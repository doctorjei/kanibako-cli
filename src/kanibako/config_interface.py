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
    read_target_settings,
    unset_project_config_key,
    write_project_config_key,
)
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
    # Container
    "image",
    "container_image",
    # Auth / project
    "auth",
    "layout",
    "mode",
    # Vault
    "vault.enabled",
    "vault.ro",
    "vault.rw",
    # Target settings (Claude-specific)
    "target_name",
    # System-level path settings
    "paths.data_path",
    "paths.boxes",
    "paths.shell",
    "paths.vault",
    "paths.shared",
    "paths.comms",
    "paths.templates",
    # Helpers
    "helpers_disabled",
})

# Prefixes for dynamic keys (env vars, resources, shared caches).
DYNAMIC_PREFIXES: tuple[str, ...] = ("env.", "resource.", "shared.")

# Map friendly short names to canonical flat config keys.
_KEY_ALIASES: dict[str, str] = {
    "image": "container_image",
}


def is_known_key(arg: str) -> bool:
    """Return True if *arg* looks like a config key (not a project name)."""
    if arg in KNOWN_CONFIG_KEYS:
        return True
    return any(arg.startswith(p) for p in DYNAMIC_PREFIXES)


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

    Accepts aliases (``image`` → ``container_image``), dot-notation
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


def _is_target_setting(key: str) -> bool:
    """Keys that belong in [target_settings] of project.toml."""
    return key in {"model", "start_mode", "autonomous"}


def _dot_to_flat(key: str) -> str:
    """Convert ``vault.enabled`` to ``vault_enabled``, etc."""
    # For paths.* keys, convert to the flat KanibakoConfig field name.
    if key.startswith("paths."):
        return "paths_" + key[6:]
    return key.replace(".", "_")


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

    # resource.* keys — read from [resource_overrides] in project.toml
    if _is_resource_key(canonical):
        resource_name = canonical[9:]  # strip "resource."
        if project_toml and project_toml.exists():
            import tomllib
            with open(project_toml, "rb") as f:
                data = tomllib.load(f)
            overrides = data.get("resource_overrides", {})
            return str(overrides.get(resource_name, "")) or None
        return None

    # shared.* keys — read from [shared] in global config or project
    if _is_shared_key(canonical):
        cache_name = canonical[7:]  # strip "shared."
        cfg = load_merged_config(global_config_path, project_toml)
        return cfg.shared_caches.get(cache_name)

    # target settings (model, start_mode, autonomous)
    if _is_target_setting(canonical):
        if project_toml and project_toml.exists():
            settings = read_target_settings(project_toml)
            if canonical in settings:
                return settings[canonical]
        return None

    # Regular config keys — use merged config
    flat = _dot_to_flat(canonical)
    cfg = load_merged_config(global_config_path, project_toml)
    valid = {fld.name for fld in fields(cfg)}
    if flat in valid:
        val = getattr(cfg, flat)
        if isinstance(val, bool):
            return str(val).lower()
        return str(val) if val else None
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

    *config_path* is the project.toml (for box/workset) or kanibako.toml
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

    # target settings
    if _is_target_setting(canonical):
        _write_toml_key(config_path, "target_settings", canonical, value)
        return f"Set {canonical}={value}"

    # Regular config keys
    flat = _dot_to_flat(canonical)
    write_project_config_key(config_path, flat, value)
    return f"Set {flat}={value}"


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

    # target settings
    if _is_target_setting(canonical):
        if _remove_toml_key(config_path, "target_settings", canonical):
            return f"Reset {canonical}"
        return f"No override for {canonical}"

    # Regular config keys
    flat = _dot_to_flat(canonical)
    if unset_project_config_key(config_path, flat):
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
        import tomllib
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        if data.get("target_settings"):
            for k in list(data["target_settings"]):
                _remove_toml_key(config_path, "target_settings", k)
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
) -> int:
    """Display config values.  Returns exit code.

    - *effective=False*: show only overrides at this level.
    - *effective=True*: show all resolved values including inherited defaults.
    """
    out = file or sys.stdout

    if effective:
        # Show all resolved values
        cfg = load_merged_config(global_config_path, config_path)
        overrides = load_project_overrides(config_path) if config_path else {}
        for fld in fields(cfg):
            val = getattr(cfg, fld.name)
            marker = " (override)" if fld.name in overrides else ""
            print(f"  {fld.name} = {val}{marker}", file=out)

        # Target settings
        if config_path and config_path.exists():
            settings = read_target_settings(config_path)
            if settings:
                print("", file=out)
                for k, v in sorted(settings.items()):
                    print(f"  {k} = {v} (override)", file=out)

        # Env vars
        merged = merge_env(env_global, env_project)
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
            settings = read_target_settings(config_path)
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
# TOML section helpers
# ---------------------------------------------------------------------------

def _write_toml_key(path: Path, section: str, key: str, value: str) -> None:
    """Write a key to a specific TOML section, preserving other content."""
    import tomllib
    import tomli_w

    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)

    data.setdefault(section, {})[key] = value
    with open(path, "wb") as f:
        tomli_w.dump(data, f)


def _remove_toml_key(path: Path, section: str, key: str) -> bool:
    """Remove a key from a specific TOML section.  Returns True if found."""
    import tomllib
    import tomli_w

    if not path.exists():
        return False

    with open(path, "rb") as f:
        data = tomllib.load(f)

    sec = data.get(section, {})
    if key not in sec:
        return False

    del sec[key]
    if not sec:
        del data[section]
    with open(path, "wb") as f:
        tomli_w.dump(data, f)
    return True
