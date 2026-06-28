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
    coerce_bool,
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
    # Box
    "box.image",
    "box.agent",
    "box.share_images",
    "box.shell",
    "box.bootstrap_program",
    # Auth / project
    "group_auth",
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
    # Helpers
    "allow_helpers",
})

# Prefixes for dynamic keys (env vars, resources).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.", "resource.")


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS:
        return True
    if any(arg.startswith(p) for p in DYNAMIC_PREFIXES):
        return True
    # Category keys (``<scope>.bindings.{ro,rw}.<name>`` / ``caches`` / ``seeded``
    # / ``shared`` / ``synced``) are settable via ``config set`` (the source-only
    # RAW repoint). Recognize them here too so the get/show paths + the
    # project-name heuristic treat a category key as a KEY, never a project name
    # — the same get-validated/set-unguarded symmetry the H1 fix established.
    return _is_path_category_key(arg)


# ---------------------------------------------------------------------------
# Typed writer routing table (the H1/H2 core)
# ---------------------------------------------------------------------------
#
# The single source of truth for HOW every non-dynamic, non-env config key is
# stored.  ``get``/``set``/``reset`` all consult this table so the same key set
# is recognised on every path (no "get-validated, set-unguarded" asymmetry that
# crashed H1).  A key absent from here (and not env./resource./agent.*/
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
    # Project section ([project] table) — group_auth/mode/vault.* are read back
    # by read_project_meta(); vault.enabled lands in its real stored key
    # ``enable_vault`` (the H1 alias fix).
    "group_auth": (("project",), "group_auth"),
    "mode": (("project",), "mode"),
    "vault.enabled": (("project",), "enable_vault"),
    "vault.ro": (("project",), "vault_ro"),
    "vault.rw": (("project",), "vault_rw"),
    # Top-level scalar fields (flat KanibakoConfig fields).
    "allow_helpers": ((), "allow_helpers"),
}

# Keys whose values must be coerced to a real type before writing (the H2 fix).
# Boolean keys parse true/false/1/0/yes/no (case-insensitive) to a Python bool
# so the loader reads back a real bool (``set box.share_images false`` actually
# disables it).  Build this extensibly — later phases add box.group_auth /
# vault_enabled / agent.*.{auto_approve,allow_helpers} etc.  The truth table
# itself lives in ``config`` (shared with the box.meta writer); see
# ``config.coerce_bool``.
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
        coerced = coerce_bool(value)
        if coerced is not None:
            return coerced
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
    """Return the canonical config key for a user-supplied key name.

    Config keys are already canonical (dot-notation like ``box.image`` or
    ``vault.enabled``, or a raw flat key); this is the single canonicalization
    seam every get/set/reset path routes through.
    """
    return raw


def _is_env_key(key: str) -> bool:
    return key.startswith("env.")


def _is_resource_key(key: str) -> bool:
    return key.startswith("resource.")


def _is_agent_setting(key: str) -> bool:
    """Keys that belong in the agent section of settings.yaml."""
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

    A ``system``-scope CATEGORY key (``system.caches.x`` / ``system.bindings.*`` /
    ``system.seeded.*`` / …) is ALSO excluded: categories exist at every scope
    INCLUDING system (spec §2a — e.g. global ``system.caches``), so a system-scope
    category repoint must reach the source-only ``config set`` path, NOT the
    structural ``system.*`` file-only refusal. (Their dotted shape only LOOKS like
    a ``system.*`` config key.)
    """
    if not key.startswith("system.") or _is_default_agent_key(key):
        return False
    return not _is_path_category_key(key)


def _system_key_refusal(key: str, config_path: Path) -> str:
    """Error string refusing a CLI write to a FILE-ONLY ``system.*`` config key.

    ``system.*`` keys are STRUCTURAL config (layout), not behavior settings, so
    they are file-only: editable in the config file (or via ``kanibako setup``)
    but never via ``config set``/``--reset``.  Points the user at the file.
    """
    return (
        f"Error: '{key}' is a structural config key and is not settable from "
        f"the CLI. Edit the config file directly:\n  {config_path}\n"
        f"(or re-run 'kanibako setup')."
    )


def _is_path_category_key(key: str) -> bool:
    """True iff *key* is a PATH-TUPLE category key settable via ``config set``.

    The source-only RAW repoint (spec §2a / design §6d / S24) applies to the
    bind-shaped categories ONLY — ``bindings.{ro,rw}`` / ``caches`` / ``seeded`` /
    ``shared`` / ``synced`` (a 2-/3-element ``[host_src, box_dest[, options]]``
    tuple). ``env`` (scalar) is routed by the earlier ``_is_env_key`` branch;
    ``masks`` (a keyed list) is YAML-only (spec §2a L216) and is NOT matched here.
    """
    from kanibako.settings_categories import BIND_KEY_RE

    return BIND_KEY_RE.match(key) is not None


def _category_ref_lookup(config_path: Path):
    """Build the ``ref_exists`` predicate for a category ``config set`` at
    *config_path* (the COMMAND-scope file).

    A category ``host_src`` value may carry an ``@``-ref; ``validate_config_set``
    rejects a DANGLING ``@``-ref (spec §2a L212). With no launch snapshot at this
    seam, the visible keyspace is: (1) the resolved ``system.*`` config tier
    (``load_std_paths`` — the overwhelmingly common ``@``-ref target for a host
    source, e.g. ``@system.data``) and (2) the command-scope file's OWN dotted
    keys (a sibling repoint can reference a key set in the same file). Built into
    a raw :class:`KeyStore` consumed by the committed ``make_ref_lookup`` (S3
    unbound probe; present-None counts as existing).

    LIMITATION (Jei-noted, see block-7c summary): an ``@``-ref to a HIGHER
    non-``system`` scope key not present in the command file (e.g. a ``box config``
    value referencing ``@workset.shared.x``) is NOT visible here and would be
    reported dangling. No shipped host_src default uses such a ref; the realistic
    target set is ``system.*``. A genuinely cross-scope ref class is an escalation,
    NOT a silent pass.
    """
    from kanibako.config import config_file_path
    from kanibako.paths import load_system_config, xdg
    from kanibako.settings_configset import make_ref_lookup
    from kanibako.settings_store import KeyStore

    keyspace = KeyStore()

    # (1) Resolved system.* tier — the standard host_src @-ref target set.
    # ``load_system_config`` returns ``{system.<leaf>: Path}`` (full dotted keys),
    # the SAME resolved tier ``get_config_value``'s ``system.*`` read consults.
    try:
        config_home = xdg("XDG_CONFIG_HOME", ".config")
        user_config = config_file_path(config_home)
        data_home = xdg("XDG_DATA_HOME", ".local/share")
        for dotted, path in load_system_config(
            user_config, data_home=data_home, home=Path.home(),
        ).items():
            _ref_insert(keyspace, dotted, str(path))
    except Exception:
        # A system-paths resolution failure must not crash a config set; the
        # command-scope keys below still validate sibling refs.
        pass

    # (2) The command-scope file's own dotted keys (sibling references).
    if config_path.exists():
        try:
            _ref_overlay_doc(keyspace, load_doc(config_path))
        except Exception:
            pass

    return make_ref_lookup(keyspace)


def _ref_insert(store: Any, dotted: str, value: object) -> None:
    """Insert *value* at *dotted* into the ref-lookup *store*, exploding nested
    nodes. Defensive: a reserved/awkward segment name is skipped (a config key
    can never legitimately be a reserved dict-method name, and an existence probe
    over it would be moot)."""
    from kanibako.settings_store import KeyStore, ReservedKeyError

    parts = dotted.split(".")
    node = store
    try:
        for seg in parts[:-1]:
            existing = dict.get(node, seg, None)
            if not isinstance(existing, KeyStore):
                existing = KeyStore()
                node[seg] = existing
            node = existing
        node[parts[-1]] = value
    except ReservedKeyError:
        return


def _ref_overlay_doc(store: Any, doc: object, prefix: str = "") -> None:
    """Overlay a loaded YAML *doc*'s leaf keys into the ref-lookup *store* as
    dotted paths (existence only — leaf VALUES are irrelevant to ``ref_exists``)."""
    if not isinstance(doc, dict):
        return
    for key, val in doc.items():
        dotted = f"{prefix}{key}"
        if isinstance(val, dict):
            _ref_overlay_doc(store, val, prefix=f"{dotted}.")
        else:
            _ref_insert(store, dotted, val if not isinstance(val, (list, tuple)) else "")


def _category_var_known(name: str) -> bool:
    """``var_known`` for a category ``config set``: a ``$VAR`` is known iff it is a
    recognized XDG base-dir var or present in the host environment (spec §2a L212 —
    a well-formed but UNKNOWN ``$VAR`` is still a hard Error). Box-side ``$XDG`` is
    deferred (S17), but the four XDG base names are always recognized so a
    legitimate ``$XDG_DATA_HOME`` host source never false-errors."""
    import os

    _XDG_NAMES = frozenset({
        "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME",
        "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    })
    return name in _XDG_NAMES or name in os.environ


def _set_category_value(
    canonical: str, value: str, *, config_path: Path,
) -> str:
    """Validate + RAW-repoint a path-tuple category key (S24/S25, spec §2a).

    Runs ``validate_config_set`` (Error refuses, Warn proceeds-with-message, OK
    silent) BEFORE the write, then ``repoint_host_src`` (swaps host_src, preserves
    box_dest+opts RAW, key-MUST-exist). The WARN message is surfaced to the user
    AND the set proceeds. A ``ConfigSetError`` (key absent / non-tuple value) is
    returned as an ``Error:`` string (the CLI prints it to stderr + exit 1).
    """
    from kanibako.settings_configset import (
        ConfigSetError,
        Error,
        Warn,
        repoint_host_src,
        validate_config_set,
    )

    def _host_exists(raw: str) -> bool:
        # A plain literal host path; ``~`` is home-relative. (A token-bearing
        # value is not path-checked — validate_config_set only calls this for a
        # literal host_src.)
        from pathlib import Path as _Path
        return _Path(raw).expanduser().exists()

    verdict = validate_config_set(
        canonical,
        value,
        is_category=True,
        ref_exists=_category_ref_lookup(config_path),
        var_known=_category_var_known,
        host_exists=_host_exists,
    )
    if isinstance(verdict, Error):
        return f"Error: {verdict.message}"

    try:
        repoint_host_src(config_path, canonical, value)
    except ConfigSetError as exc:
        return f"Error: {exc}"

    confirm = f"Set {canonical} host source to {value}"
    if isinstance(verdict, Warn):
        return f"{confirm}\nWarning: {verdict.message}"
    return confirm


def _dot_to_flat(key: str) -> str:
    """Convert ``vault.enabled`` to ``enable_vault``, etc."""
    return key.replace(".", "_")


# Reverse of _dot_to_flat for the routing table: the CLI surface (and prior
# code) also accepts the flat underscore form of a key (``box_image``).
# Normalise it to the canonical routing key so get/set/reset all hit the SAME
# _KEY_ROUTES entry regardless of which spelling was given.
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
    system_settings_path: Path | None = None,
) -> str | None:
    """Read a single config value from the appropriate store.

    Returns the resolved (merged) value as a string, or None if the key
    is not set.

    *system_settings_path*, when supplied (the SYSTEM scope), is the file used
    for SETTINGS reads (``system.default_agent`` + agent settings) — i.e.
    ``@system.settings`` = ``global/settings.yaml``.  When None (box/workset
    scope) the existing ``project_toml``/``global_config_path`` paths are used,
    so those scopes keep their own ``settings.yaml`` behavior.  CONFIG
    (``system.*`` layout) reads always use ``global_config_path``.
    """
    canonical = _resolve_key(key)

    # env.* keys — read from env files
    if _is_env_key(canonical):
        env_name = canonical[4:]  # strip "env."
        merged = merge_env(env_global, env_project)
        return merged.get(env_name)

    # resource.* keys — read from resource_overrides in settings.yaml
    if _is_resource_key(canonical):
        resource_name = canonical[9:]  # strip "resource."
        if project_toml and project_toml.exists():
            data = load_doc(project_toml)
            overrides = data.get("resource_overrides", {})
            return str(overrides.get(resource_name, "")) or None
        return None

    # target settings (model, start_mode, autonomous)
    if _is_agent_setting(canonical):
        # The agent-agnostic ``config`` CLI reads/writes the reserved any-agent
        # ``agent.default`` tier; per-agent overrides live under ``agent.<name>``
        # and are resolved by the launch-time effective-state cascade.  For the
        # SYSTEM scope these are SETTINGS that live in the system settings file
        # (system_settings_path), not the kanibako.yaml CONFIG file.
        setting_src = (
            system_settings_path if system_settings_path is not None else project_toml
        )
        if setting_src and setting_src.exists():
            settings = read_agent_settings(setting_src, "default")
            if canonical in settings:
                return settings[canonical]
        return None

    # system.default_agent — the SETTING (not a config path).  Read it from the
    # system settings tier: ``@system.settings`` = ``global/settings.yaml``
    # (system_settings_path) for the SYSTEM scope, else the project/global paths.
    if _is_default_agent_key(canonical):
        sources = (
            (project_toml, system_settings_path)
            if system_settings_path is not None
            else (project_toml, global_config_path)
        )
        for src in sources:
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
    # defaults + inheritance apply (box.*, allow_helpers, box.share_images).
    flat = _dot_to_flat(routed)
    cfg = load_merged_config(global_config_path, project_toml)
    valid = {fld.name for fld in fields(cfg)}
    if flat in valid:
        val = getattr(cfg, flat)
        if isinstance(val, bool):
            return str(val).lower()
        return str(val) if val else None

    # Keys with no flat field (group_auth, mode, vault.*) land in [project]/root
    # — read the raw set-value from the routed location.
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
    system_settings_path: Path | None = None,
) -> str:
    """Write a config value to the appropriate store.

    *config_path* is the settings.yaml (for box/workset) or kanibako.yaml
    (for system).  *system_settings_path*, when supplied (the SYSTEM scope), is
    the file SETTINGS (``system.default_agent`` + agent settings) are written to
    — ``@system.settings`` = ``global/settings.yaml`` — keeping them out of the
    kanibako.yaml CONFIG file.  When None (box/workset) writes go to
    ``config_path`` as before.  Returns a human-readable confirmation message.
    """
    canonical = _resolve_key(key)
    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

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

    # target settings — the agent-agnostic CLI writes the any-agent
    # ``agent.default`` tier (per-agent overrides live under ``agent.<name>``).
    # SYSTEM scope routes to the system settings file (settings_dest).
    if _is_agent_setting(canonical):
        _write_nested_toml_key(settings_dest, ("agent", "default"), canonical, value)
        return f"Set {canonical}={value}"

    # Path-TUPLE category keys (``bindings.{ro,rw}`` / ``caches`` / ``seeded`` /
    # ``shared`` / ``synced``) — the source-only RAW repoint (S24/S25, spec §2a,
    # design §6d). Checked BEFORE the ``system.*`` file-only refusal because a
    # SYSTEM-scope category key (``system.caches.x`` / ``system.bindings.*``) only
    # LOOKS like a ``system.*`` config key — categories are settable at every
    # scope (spec §2a). ``config set <key> <value>`` validates the RAW value at
    # set time (``validate_config_set``) then swaps ONLY ``host_src`` in the
    # existing tuple at the COMMAND-scope file (``repoint_host_src``), preserving
    # ``box_dest`` + options RAW. Source-only: it REPOINTS an existing bind, never
    # creates one. ``env`` (scalar) was handled above; ``masks`` is YAML-only
    # (spec §2a L216) — not a tuple, so a repoint is refused as non-category.
    if _is_path_category_key(canonical):
        return _set_category_value(canonical, value, config_path=config_path)

    # system.* keys (INCLUDING system.default_agent) — FILE-ONLY host-global
    # config (W1, option (a) narrow scope).  The CLI reads/shows them but
    # refuses to SET them: edit the config file directly, or run
    # ``kanibako setup`` (which writes ``default_agent`` programmatically via
    # write_system_value, bypassing this guard).  ``default_agent`` joins this
    # rule per §Design 1 — it is the host-global default and stays in system.*.
    # (A system-scope CATEGORY key was already routed above — ``_is_system_path_
    # key`` excludes it — so this refusal applies only to true structural keys.)
    if _is_default_agent_key(canonical) or _is_system_path_key(canonical):
        return _system_key_refusal(canonical, config_path)

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
    system_settings_path: Path | None = None,
) -> str:
    """Remove an override for a single key.  Returns confirmation message.

    *system_settings_path*, when supplied (SYSTEM scope), is where SETTINGS
    (``system.default_agent`` + agent settings) are removed from
    (``@system.settings`` = ``global/settings.yaml``); when None (box/workset)
    they are removed from ``config_path`` as before.
    """
    canonical = _resolve_key(key)
    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )

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

    # target settings — reset the any-agent ``agent.default`` tier (SYSTEM scope
    # routes to the system settings file).
    if _is_agent_setting(canonical):
        if _remove_nested_toml_key(settings_dest, ("agent", "default"), canonical):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # system.* keys (INCLUDING system.default_agent) — FILE-ONLY host-global
    # config (see set_config_value).  The CLI refuses to RESET them too (for
    # symmetry); edit the config file directly or re-run ``kanibako setup``.
    if _is_default_agent_key(canonical) or _is_system_path_key(canonical):
        return _system_key_refusal(canonical, config_path)

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


def write_system_value(config_path: Path, leaf: str, value: object) -> None:
    """Programmatically write a ``[system] <leaf>`` key to the CONFIG file.

    This is the PROGRAM editing the config file on the user's behalf — it
    bypasses the file-only CLI guard in :func:`set_config_value` (which refuses
    ``system.*`` keys).  Used by ``kanibako setup`` to record host-global values
    (e.g. ``system.setup_completed`` → ``[system] setup_completed``) that the CLI
    deliberately will not let a user SET directly.

    *leaf* is the bare key name under the ``[system]`` table (NOT prefixed with
    ``system.``).  Writes preserve all other config content (read-modify-write
    via :func:`_write_nested_toml_key`).
    """
    _write_nested_toml_key(config_path, ("system",), leaf, value)


def reset_all(
    *,
    config_path: Path,
    env_path: Path | None = None,
    force: bool = False,
    system_settings_path: Path | None = None,
) -> str:
    """Remove all overrides at this config level.  Confirms unless *force*.

    *system_settings_path*, when supplied (SYSTEM scope), is where the SETTINGS
    (the ``agent`` table + ``resource_overrides``) are cleared from
    (``@system.settings`` = ``global/settings.yaml``), while CONFIG overrides are
    cleared from ``config_path``.  When None (box/workset) everything is cleared
    from ``config_path`` as before.
    """
    if not force:
        try:
            confirm_prompt("Remove all config overrides? Type 'yes' to proceed: ")
        except UserCancelled:
            return "Aborted."

    count = 0

    # Clear project-level config overrides (always from config_path).
    overrides = load_project_overrides(config_path)
    for key in overrides:
        unset_project_config_key(config_path, key)
        count += 1

    # Clear target settings + resource overrides.  SYSTEM scope keeps these in
    # the system settings file (settings_dest); box/workset use config_path.
    settings_dest = (
        system_settings_path if system_settings_path is not None else config_path
    )
    if settings_dest.exists():
        data = load_doc(settings_dest)
        agent_tbl = data.get("agent")
        if isinstance(agent_tbl, dict):
            # agent table is agent-keyed: {<agent>: {key: val}}; clear every
            # agent's subsection (the reserved "default" tier included).
            for agent, sec in list(agent_tbl.items()):
                if isinstance(sec, dict):
                    for k in list(sec):
                        _remove_nested_toml_key(settings_dest, ("agent", agent), k)
                        count += 1
        if data.get("resource_overrides"):
            for k in list(data["resource_overrides"]):
                _remove_toml_key(settings_dest, "resource_overrides", k)
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
    system_settings_path: Path | None = None,
) -> int:
    """Display config values.  Returns exit code.

    - *effective=False*: show only overrides at this level.
    - *effective=True*: show all resolved values including inherited defaults.

    *system_settings_path*, when supplied (SYSTEM scope), is the file the agent
    SETTINGS + ``system.default_agent`` are DISPLAYED from (``@system.settings``
    = ``global/settings.yaml``); the ``system.*`` CONFIG display always uses
    ``global_config_path``.  When None (box/workset) settings display reads
    ``config_path`` as before.
    """
    out = file or sys.stdout
    # The file agent SETTINGS are read from for display: system settings file for
    # the SYSTEM scope, else the level's own config_path (box/workset).
    settings_src = (
        system_settings_path if system_settings_path is not None else config_path
    )

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
                read_agent_settings(settings_src, "default")
                if settings_src and settings_src.exists()
                else {}
            )
            if agent_state:
                print("", file=out)
                for k, v in sorted(agent_state.items()):
                    marker = " (override)" if k in proj_agent else ""
                    print(f"  {k} = {v}{marker}", file=out)
        elif settings_src and settings_src.exists():
            settings = read_agent_settings(settings_src, "default")
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

        if settings_src and settings_src.exists():
            settings = read_agent_settings(settings_src, "default")
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
