"""Unified config interface engine for all management commands.

Provides a reusable config subsystem that box/workset/agent/system commands
share.  Handles get, set, show, and reset operations with a consistent
syntax:

- ``key=value``  → set
- ``key``        → get (if key is known)
- no args        → show all overrides
- ``--effective`` → show resolved values
- ``--reset key`` → remove override
- ``--reset --all`` → remove all overrides (with confirmation)
"""

from __future__ import annotations

import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import Any

from kanibako.config import (
    _DEFAULTS,
    load_merged_config,
    load_project_overrides,
    read_agent_settings,
    unset_project_config_key,
)
from kanibako.config_io import dump_doc, load_doc
from kanibako.errors import UserCancelled
from kanibako.shellenv import (
    merge_env,
    read_env_file,
    set_env_var,
    unset_env_var,
    write_env_file,
)
from kanibako.utils import confirm_prompt


# ---------------------------------------------------------------------------
# Key registry
# ---------------------------------------------------------------------------

class ConfigLevel(Enum):
    """Which scope a config operation targets."""

    box = "box"
    workset = "workset"
    agent = "agent"
    system = "system"


# Keys recognized by the unified config interface.
# This set drives the "known-key heuristic": if a positional arg matches one
# of these, it's treated as a GET request rather than a project name.
KNOWN_CONFIG_KEYS: frozenset[str] = frozenset({
    # Start mode / agent flags
    "start_mode",
    "autonomous",
    "model",
    "persistence",
    # Box
    "box.image",
    "box.agent",
    "box.share_images",
    "box.shell",
    "box.bootstrap_program",
    # Auth / project
    "group_auth",
    "layout",
    "mode",
    # Vault
    "vault.enabled",
    "vault.ro",
    "vault.rw",
    # System-level config settings (resolver-backed system.* tier)
    "system.data",
    "system.backup",
    "system.agents",
    "system.channels",
    "system.global",
    "system.base_template",
    "system.settings",
    "system.primary_workset",
    "system.registry",
    "system.cache",
    "system.runtime",
    # system.default_agent: the lone system.*-named SETTING (behavior, not a
    # config path).  Routed to the SYSTEM settings tier (the agent.default
    # table), NOT the [system] config table — handled explicitly below.
    "system.default_agent",
    # Box-level path settings (flat KanibakoConfig.paths_* fields)
    "paths.shell",
    "paths.vault",
    "paths.shared",
    # Helpers
    "allow_helpers",
})

# Prefixes for dynamic keys (env vars, resources, shared caches).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.", "resource.", "shared.")

# Map friendly short names to canonical flat config keys.
_KEY_ALIASES: dict[str, str] = {
    "image": "box.image",
    "agent": "box.agent",
}


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS or arg in _KEY_ALIASES:
        return True
    return any(arg.startswith(p) for p in DYNAMIC_PREFIXES)


# ---------------------------------------------------------------------------
# Typed writer routing table (the H1/H2 core)
# ---------------------------------------------------------------------------
#
# The single source of truth for HOW every non-dynamic, non-env config key is
# stored.  ``get``/``set``/``reset`` all consult this table so the same key set
# is recognised on every path (no "get-validated, set-unguarded" asymmetry that
# crashed H1).  A key absent from here (and not env./resource./shared./agent.*/
# system.path.*) is UNKNOWN — the writer returns an error string, never raises.
#
# Each entry maps the canonical key → the nested config location it lands in:
# ``(sections_tuple, leaf_name)``.  An empty ``sections`` tuple means a
# top-level scalar field (e.g. ``allow_helpers``).  This is the *currently
# advertised* key set; later phases (4) extend it with the new categories
# (masks/bindings/synced/caches) without touching the routing mechanism.
_KEY_ROUTES: dict[str, tuple[tuple[str, ...], str]] = {
    # Box section ([box] table).
    "box.image": (("box",), "image"),
    "box.agent": (("box",), "agent"),
    "box.shell": (("box",), "shell"),
    "box.bootstrap_program": (("box",), "bootstrap_program"),
    "box.share_images": (("box",), "share_images"),
    # Project section ([project] table) — group_auth/mode/layout/vault.* are read
    # back by read_project_meta(); vault.enabled lands in its real stored key
    # ``enable_vault`` (the H1 alias fix).
    "group_auth": (("project",), "group_auth"),
    "mode": (("project",), "mode"),
    "layout": (("project",), "layout"),
    "vault.enabled": (("project",), "enable_vault"),
    "vault.ro": (("project",), "vault_ro"),
    "vault.rw": (("project",), "vault_rw"),
    # Top-level scalar fields (flat KanibakoConfig fields).
    "allow_helpers": ((), "allow_helpers"),
    "persistence": ((), "persistence"),
    # Box-level path fields ([paths] table → flat paths_* KanibakoConfig fields).
    "paths.shell": (("paths",), "shell"),
    "paths.vault": (("paths",), "vault"),
    "paths.shared": (("paths",), "shared"),
}

# Keys whose values must be coerced to a real type before writing (the H2 fix).
# Boolean keys parse true/false/1/0/yes/no (case-insensitive) to a Python bool
# so the loader reads back a real bool (``set box.share_images false`` actually
# disables it).  Build this extensibly — later phases add box.group_auth /
# vault_enabled / agent.*.{auto_approve,allow_helpers} etc.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})

KEY_TYPES: dict[str, str] = {
    "box.share_images": "bool",
    "allow_helpers": "bool",
    "group_auth": "bool",
    "vault.enabled": "bool",
}


def _coerce_value(canonical: str, value: str) -> object | str:
    """Coerce *value* to the typed form declared for *canonical* in KEY_TYPES.

    Returns the typed Python value (e.g. a real ``bool``) on success, or an
    ``"Error: ..."`` string when a bool key is given an unparseable value.
    Scalars (no KEY_TYPES entry) pass through unchanged as the raw string.
    """
    kind = KEY_TYPES.get(canonical)
    if kind == "bool":
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
        return (
            f"Error: {canonical} expects a boolean "
            f"(true/false/1/0/yes/no), got {value!r}"
        )
    return value


# ---------------------------------------------------------------------------
# Config action parsing
# ---------------------------------------------------------------------------

class ConfigAction(Enum):
    """What the user wants to do with config."""

    get = "get"
    set = "set"
    show = "show"
    reset = "reset"


def parse_config_arg(arg: str | None) -> tuple[ConfigAction, str, str]:
    """Parse a single positional config argument.

    Returns ``(action, key, value)``.

    - ``"key=value"`` → ``(set, key, value)``
    - ``"key"``       → ``(get, key, "")``
    - ``None``        → ``(show, "", "")``
    """
    if arg is None:
        return (ConfigAction.show, "", "")
    if "=" in arg:
        key, _, value = arg.partition("=")
        return (ConfigAction.set, key.strip(), value.strip())
    return (ConfigAction.get, arg.strip(), "")


# ---------------------------------------------------------------------------
# Canonical key resolution
# ---------------------------------------------------------------------------

def _resolve_key(raw: str) -> str:
    """Map a user-supplied key name to the canonical form.

    Accepts aliases (``image`` → ``box.image``), dot-notation
    (``vault.enabled``), or the raw flat key.  Returns the key unchanged
    if no alias exists.
    """
    if raw in _KEY_ALIASES:
        return _KEY_ALIASES[raw]
    return raw


def _is_env_key(key: str) -> bool:
    return key.startswith("env.")


def _is_resource_key(key: str) -> bool:
    return key.startswith("resource.")


def _is_shared_key(key: str) -> bool:
    return key.startswith("shared.")


def _is_agent_setting(key: str) -> bool:
    """Keys that belong in the agent section of project.yaml."""
    return key in {"model", "start_mode", "autonomous"}


# ``system.default_agent`` is the lone ``system.*``-named SETTING (behavior, not
# a config path).  It does NOT land in the ``[system]`` config table; it lands in
# the SYSTEM settings tier — the reserved any-agent ``agent.default`` table, key
# ``default_agent`` — where ``config.read_default_agent`` reads it back.  Phase 5
# re-points the system settings tier to ``@system.settings``.
_DEFAULT_AGENT_KEY = "system.default_agent"
_DEFAULT_AGENT_SECTIONS: tuple[str, ...] = ("agent", "default")
_DEFAULT_AGENT_LEAF = "default_agent"


def _is_default_agent_key(key: str) -> bool:
    """The ``system.default_agent`` SETTING (routed to the settings tier)."""
    return key == _DEFAULT_AGENT_KEY


def _is_system_path_key(key: str) -> bool:
    """Keys that belong in the ``[system]`` config table (system-only).

    ``system.default_agent`` is EXCLUDED — it is a SETTING, not a config path,
    and is handled by :func:`_is_default_agent_key` before this check.
    """
    return key.startswith("system.") and not _is_default_agent_key(key)


def _system_key_sections(key: str) -> tuple[tuple[str, ...], str]:
    """Split a ``system.<a>.<b>...`` key into (nested sections, leaf).

    ``system.data`` → ``(("system",), "data")``;
    ``system.channels.commons`` → ``(("system", "channels"), "commons")``.
    """
    parts = key.split(".")  # ["system", "<a>", ...]
    *sections, leaf = parts
    return tuple(sections), leaf


def _dot_to_flat(key: str) -> str:
    """Convert ``vault.enabled`` to ``enable_vault``, etc."""
    # For paths.* keys, convert to the flat KanibakoConfig field name.
    if key.startswith("paths."):
        return "paths_" + key[6:]
    return key.replace(".", "_")


# Reverse of _dot_to_flat for the routing table: the CLI surface (and prior
# code) also accepts the flat underscore form of a key (``box_image``,
# ``paths_shell``).  Normalise it to the canonical routing key so get/set/reset
# all hit the SAME _KEY_ROUTES entry regardless of which spelling was given.
_FLAT_TO_CANONICAL: dict[str, str] = {
    _dot_to_flat(canonical): canonical
    for canonical in _KEY_ROUTES
    if _dot_to_flat(canonical) != canonical
}


def _route_key(canonical: str) -> str:
    """Map a flat-underscore key spelling to its canonical routing key."""
    return _FLAT_TO_CANONICAL.get(canonical, canonical)


# ---------------------------------------------------------------------------
# Get / set / reset operations
# ---------------------------------------------------------------------------

def get_config_value(
    key: str,
    *,
    global_config_path: Path,
    project_toml: Path | None = None,
    env_global: Path | None = None,
    env_project: Path | None = None,
) -> str | None:
    """Read a single config value from the appropriate store.

    Returns the resolved (merged) value as a string, or None if the key
    is not set.
    """
    canonical = _resolve_key(key)

    # env.* keys — read from env files
    if _is_env_key(canonical):
        env_name = canonical[4:]  # strip "env."
        merged = merge_env(env_global, env_project)
        return merged.get(env_name)

    # resource.* keys — read from resource_overrides in project.yaml
    if _is_resource_key(canonical):
        resource_name = canonical[9:]  # strip "resource."
        if project_toml and project_toml.exists():
            data = load_doc(project_toml)
            overrides = data.get("resource_overrides", {})
            return str(overrides.get(resource_name, "")) or None
        return None

    # shared.* keys — read from [shared] in global config or project
    if _is_shared_key(canonical):
        cache_name = canonical[7:]  # strip "shared."
        cfg = load_merged_config(global_config_path, project_toml)
        return cfg.shared_caches.get(cache_name)

    # target settings (model, start_mode, autonomous)
    if _is_agent_setting(canonical):
        # The agent-agnostic ``config`` CLI reads/writes the reserved any-agent
        # ``agent.default`` tier; per-agent overrides live under ``agent.<name>``
        # and are resolved by the launch-time effective-state cascade.
        if project_toml and project_toml.exists():
            settings = read_agent_settings(project_toml, "default")
            if canonical in settings:
                return settings[canonical]
        return None

    # system.default_agent — the SETTING (not a config path).  Read it from the
    # system settings tier (the global config's agent.default table).
    if _is_default_agent_key(canonical):
        for src in (project_toml, global_config_path):
            if src is None or not src.exists():
                continue
            settings = read_agent_settings(src, "default")
            if _DEFAULT_AGENT_LEAF in settings:
                return settings[_DEFAULT_AGENT_LEAF] or None
        return None

    # system.* keys — read the raw set-value from the global config's [system]
    # table (system-only tier; not a merged-config field).
    if _is_system_path_key(canonical):
        cfg = load_merged_config(global_config_path, project_toml)
        return cfg.system_paths.get(canonical)

    # Regular config keys — route via the SAME known-key table that set/reset
    # use (no get-validated/set-unguarded asymmetry).  An unknown key returns
    # None (rendered "not set").
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return None

    # Keys backed by a flat KanibakoConfig field use the merged config so
    # defaults + inheritance apply (box.*, paths.*, allow_helpers,
    # box.share_images).
    flat = _dot_to_flat(routed)
    cfg = load_merged_config(global_config_path, project_toml)
    valid = {fld.name for fld in fields(cfg)}
    if flat in valid:
        val = getattr(cfg, flat)
        if isinstance(val, bool):
            return str(val).lower()
        return str(val) if val else None

    # Keys with no flat field (group_auth, mode, layout, vault.*, persistence)
    # land in [project]/root — read the raw set-value from the routed location.
    sections, leaf = route
    for src in (project_toml, global_config_path):
        if src is None or not src.exists():
            continue
        node: object = load_doc(src)
        for sec in sections:
            if not isinstance(node, dict):
                node = None
                break
            node = node.get(sec)
        if isinstance(node, dict) and leaf in node:
            v = node[leaf]
            if isinstance(v, bool):
                return str(v).lower()
            return str(v) if v != "" else None
    return None


def set_config_value(
    key: str,
    value: str,
    *,
    config_path: Path,
    env_path: Path | None = None,
    is_system: bool = False,
) -> str:
    """Write a config value to the appropriate store.

    *config_path* is the project.yaml (for box/workset) or kanibako.yaml
    (for system).  Returns a human-readable confirmation message.
    """
    canonical = _resolve_key(key)

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path is None:
            return f"Error: no env file path for key {canonical}"
        try:
            set_env_var(env_path, env_name, value)
        except ValueError as e:
            return f"Error: {e}"
        return f"Set {env_name}={value}"

    # resource.* keys — write to [resource_overrides]
    if _is_resource_key(canonical):
        resource_name = canonical[9:]
        _write_toml_key(config_path, "resource_overrides", resource_name, value)
        return f"Set resource.{resource_name}={value}"

    # shared.* keys — write to [shared]
    if _is_shared_key(canonical):
        cache_name = canonical[7:]
        _write_toml_key(config_path, "shared", cache_name, value)
        return f"Set shared.{cache_name}={value}"

    # target settings — the agent-agnostic CLI writes the any-agent
    # ``agent.default`` tier (per-agent overrides live under ``agent.<name>``).
    if _is_agent_setting(canonical):
        _write_nested_toml_key(config_path, ("agent", "default"), canonical, value)
        return f"Set {canonical}={value}"

    # system.default_agent — the SETTING.  Write it into the SYSTEM settings
    # tier (the agent.default table), NOT the [system] config table.
    if _is_default_agent_key(canonical):
        _write_nested_toml_key(
            config_path, _DEFAULT_AGENT_SECTIONS, _DEFAULT_AGENT_LEAF, value,
        )
        return f"Set {canonical}={value}"

    # system.* keys — write into the [system] config table.
    if _is_system_path_key(canonical):
        sections, leaf = _system_key_sections(canonical)
        _write_nested_toml_key(config_path, sections, leaf, value)
        return f"Set {canonical}={value}"

    # Regular config keys — route via the single known-key table (the H1 fix:
    # an unknown key returns an error string and NEVER raises).  Accept either
    # the canonical dotted spelling or the flat underscore form.
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    sections, leaf = route
    typed = _coerce_value(routed, value)  # the H2 fix (real bool/etc.)
    if isinstance(typed, str) and KEY_TYPES.get(routed):
        # _coerce_value signalled a parse error (it only returns a str for a
        # typed key when coercion failed).
        return typed
    if sections:
        _write_nested_toml_key(config_path, sections, leaf, typed)
    else:
        _write_toml_key_root(config_path, leaf, typed)
    return f"Set {_dot_to_flat(routed)}={value}"


def reset_config_value(
    key: str,
    *,
    config_path: Path,
    env_path: Path | None = None,
) -> str:
    """Remove an override for a single key.  Returns confirmation message."""
    canonical = _resolve_key(key)

    # env.* keys
    if _is_env_key(canonical):
        env_name = canonical[4:]
        if env_path and unset_env_var(env_path, env_name):
            return f"Unset env.{env_name}"
        return f"No override for env.{env_name}"

    # resource.* keys
    if _is_resource_key(canonical):
        resource_name = canonical[9:]
        if _remove_toml_key(config_path, "resource_overrides", resource_name):
            return f"Reset resource.{resource_name}"
        return f"No override for resource.{resource_name}"

    # shared.* keys
    if _is_shared_key(canonical):
        cache_name = canonical[7:]
        if _remove_toml_key(config_path, "shared", cache_name):
            return f"Reset shared.{cache_name}"
        return f"No override for shared.{cache_name}"

    # target settings — reset the any-agent ``agent.default`` tier.
    if _is_agent_setting(canonical):
        if _remove_nested_toml_key(config_path, ("agent", "default"), canonical):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # system.default_agent — the SETTING.  Remove it from the SYSTEM settings
    # tier (the agent.default table).
    if _is_default_agent_key(canonical):
        if _remove_nested_toml_key(
            config_path, _DEFAULT_AGENT_SECTIONS, _DEFAULT_AGENT_LEAF,
        ):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # system.* keys — remove from the [system] config table.
    if _is_system_path_key(canonical):
        sections, leaf = _system_key_sections(canonical)
        if _remove_nested_toml_key(config_path, sections, leaf):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # Regular config keys — route via the same known-key table as set/get
    # (no get-validated/set-unguarded asymmetry).
    routed = _route_key(canonical)
    route = _KEY_ROUTES.get(routed)
    if route is None:
        return f"Error: unknown config key: {key}"
    sections, leaf = route
    removed = (
        _remove_nested_toml_key(config_path, sections, leaf)
        if sections
        else _remove_toml_key_root(config_path, leaf)
    )
    flat = _dot_to_flat(routed)
    if removed:
        default_val = _DEFAULTS.get(flat, "(none)")
        return f"Reset {flat} (reverts to default: {default_val})"
    return f"No override for {flat}"


def reset_all(
    *,
    config_path: Path,
    env_path: Path | None = None,
    force: bool = False,
) -> str:
    """Remove all overrides at this config level.  Confirms unless *force*."""
    if not force:
        try:
            confirm_prompt("Remove all config overrides? Type 'yes' to proceed: ")
        except UserCancelled:
            return "Aborted."

    count = 0

    # Clear project-level config overrides
    overrides = load_project_overrides(config_path)
    for key in overrides:
        unset_project_config_key(config_path, key)
        count += 1

    # Clear target settings
    if config_path.exists():
        data = load_doc(config_path)
        agent_tbl = data.get("agent")
        if isinstance(agent_tbl, dict):
            # agent table is agent-keyed: {<agent>: {key: val}}; clear every
            # agent's subsection (the reserved "default" tier included).
            for agent, sec in list(agent_tbl.items()):
                if isinstance(sec, dict):
                    for k in list(sec):
                        _remove_nested_toml_key(config_path, ("agent", agent), k)
                        count += 1
        if data.get("resource_overrides"):
            for k in list(data["resource_overrides"]):
                _remove_toml_key(config_path, "resource_overrides", k)
                count += 1

    # Clear env file
    if env_path and env_path.is_file():
        env = read_env_file(env_path)
        if env:
            count += len(env)
            write_env_file(env_path, {})

    return f"Reset {count} override(s)." if count else "No overrides to reset."


def show_config(
    *,
    global_config_path: Path,
    config_path: Path | None = None,
    env_global: Path | None = None,
    env_project: Path | None = None,
    effective: bool = False,
    file: Any = None,
    workset_path: Path | None = None,
    agent_state: dict[str, str] | None = None,
    env_resolved: dict[str, str] | None = None,
) -> int:
    """Display config values.  Returns exit code.

    - *effective=False*: show only overrides at this level.
    - *effective=True*: show all resolved values including inherited defaults.
    """
    out = file or sys.stdout

    if effective:
        # Show all resolved values
        cfg = load_merged_config(
            global_config_path, config_path, workset_path=workset_path,
        )
        overrides = load_project_overrides(config_path) if config_path else {}
        for fld in fields(cfg):
            val = getattr(cfg, fld.name)
            marker = " (override)" if fld.name in overrides else ""
            print(f"  {fld.name} = {val}{marker}", file=out)

        # Agent settings.  When a fully-resolved agent_state is supplied (box
        # view), render it; mark only the keys actually set at the box level.
        # Otherwise fall back to the project-level overrides (today's behavior).
        if agent_state is not None:
            proj_agent = (
                read_agent_settings(config_path, "default")
                if config_path and config_path.exists()
                else {}
            )
            if agent_state:
                print("", file=out)
                for k, v in sorted(agent_state.items()):
                    marker = " (override)" if k in proj_agent else ""
                    print(f"  {k} = {v}{marker}", file=out)
        elif config_path and config_path.exists():
            settings = read_agent_settings(config_path, "default")
            if settings:
                print("", file=out)
                for k, v in sorted(settings.items()):
                    print(f"  {k} = {v} (override)", file=out)

        # Env vars.  Prefer the fully-resolved env (box view) when supplied.
        merged = (
            env_resolved
            if env_resolved is not None
            else merge_env(env_global, env_project)
        )
        if merged:
            print("", file=out)
            for k in sorted(merged):
                print(f"  env.{k} = {merged[k]}", file=out)

    else:
        # Show only overrides
        has_output = False

        overrides = load_project_overrides(config_path) if config_path else {}
        for k, v in sorted(overrides.items()):
            print(f"  {k} = {v}", file=out)
            has_output = True

        if config_path and config_path.exists():
            settings = read_agent_settings(config_path, "default")
            for k, v in sorted(settings.items()):
                print(f"  {k} = {v}", file=out)
                has_output = True

        # Env vars (project-level only)
        if env_project:
            env = read_env_file(env_project)
            for k in sorted(env):
                print(f"  env.{k} = {env[k]}", file=out)
                has_output = True

        if not has_output:
            print("  (no overrides)", file=out)

    return 0


# ---------------------------------------------------------------------------
# Config section helpers (load → mutate → dump as YAML)
# ---------------------------------------------------------------------------

def _write_toml_key(path: Path, section: str, key: str, value: object) -> None:
    """Write a key to a specific config section, preserving other content."""
    data = load_doc(path)
    sec = data.get(section)
    if not isinstance(sec, dict):
        sec = {}
        data[section] = sec
    sec[key] = value
    dump_doc(path, data)


def _write_toml_key_root(path: Path, key: str, value: object) -> None:
    """Write a TOP-LEVEL scalar key, preserving other content.

    Used for flat KanibakoConfig fields (e.g. ``allow_helpers``) that live at
    the document root, not under a section.
    """
    data = load_doc(path)
    data[key] = value
    dump_doc(path, data)


def _remove_toml_key_root(path: Path, key: str) -> bool:
    """Remove a TOP-LEVEL scalar key.  Returns True if it was present."""
    if not path.exists():
        return False
    data = load_doc(path)
    if key not in data:
        return False
    del data[key]
    dump_doc(path, data)
    return True


def _remove_toml_key(path: Path, section: str, key: str) -> bool:
    """Remove a key from a specific config section.  Returns True if found."""
    if not path.exists():
        return False

    data = load_doc(path)
    sec = data.get(section, {})
    if not isinstance(sec, dict) or key not in sec:
        return False

    del sec[key]
    if not sec:
        del data[section]
    dump_doc(path, data)
    return True


def _write_nested_toml_key(
    path: Path, sections: tuple[str, ...], key: str, value: object,
) -> None:
    """Write *key* into a nested table (e.g. ``("system", "path")``).

    Preserves other content; creates intermediate tables as needed.
    """
    data = load_doc(path)
    node = data
    for sec in sections:
        child = node.get(sec)
        if not isinstance(child, dict):
            child = {}
            node[sec] = child
        node = child
    node[key] = value
    dump_doc(path, data)


def _remove_nested_toml_key(
    path: Path, sections: tuple[str, ...], key: str,
) -> bool:
    """Remove *key* from a nested table.  Returns True if found.

    Prunes now-empty intermediate tables.
    """
    if not path.exists():
        return False

    data = load_doc(path)

    # Walk to the innermost table, recording the chain for pruning.
    chain: list[dict] = [data]
    node = data
    for sec in sections:
        if sec not in node or not isinstance(node[sec], dict):
            return False
        node = node[sec]
        chain.append(node)

    if key not in node:
        return False
    del node[key]

    # Prune empty tables bottom-up.
    for i in range(len(sections) - 1, -1, -1):
        if not chain[i + 1]:
            del chain[i][sections[i]]
        else:
            break
    dump_doc(path, data)
    return True
