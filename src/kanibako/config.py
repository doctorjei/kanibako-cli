"""YAML config loading, writing, defaults, and merge logic."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc


# ---------------------------------------------------------------------------
# Defaults (match the old kanibako.rc values)
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "paths_project_toml": "project.yaml",
    "paths_shared": "shared",
    "paths_shell": "shell",
    "paths_vault": "vault",
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_crab": "",
}

# Backward-compat aliases: old field name -> new field name.
# Applied during load_config() so old config files still work.
_FIELD_ALIASES: dict[str, str] = {}


@dataclass
class KanibakoConfig:
    """Merged configuration (hardcoded defaults < kanibako.yaml < project.yaml < CLI)."""

    paths_project_toml: str = _DEFAULTS["paths_project_toml"]
    paths_shared: str = _DEFAULTS["paths_shared"]
    paths_shell: str = _DEFAULTS["paths_shell"]
    paths_vault: str = _DEFAULTS["paths_vault"]
    box_image: str = _DEFAULTS["box_image"]
    box_crab: str = _DEFAULTS["box_crab"]
    allow_helpers: bool = True
    box_share_images: bool = False
    shared_caches: dict[str, str] = field(default_factory=dict)
    # System-level path tier: raw set-values keyed by full dotted name
    # ("system.path.<leaf>"), read from the file's [system][path] table.
    # System-only (never supplied by project/workset configs).
    system_paths: dict[str, str] = field(default_factory=dict)


def _flatten_toml(data: dict, prefix: str = "") -> dict[str, object]:
    """Flatten nested config dict into underscore-joined keys.

    ``{"paths": {"boxes": "x"}}`` → ``{"paths_boxes": "x"}``
    Booleans are preserved; other scalars are stringified.
    """
    out: dict[str, object] = {}
    for k, v in data.items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_toml(v, key))
        elif isinstance(v, bool):
            out[key] = v
        else:
            out[key] = str(v)
    return out


def config_file_path(config_home: Path) -> Path:
    """Return the path to kanibako.yaml, checking new then old location.

    New: ``$XDG_CONFIG_HOME/kanibako.yaml``
    Old: ``$XDG_CONFIG_HOME/kanibako/kanibako.yaml``

    Returns the new path if neither exists (for first-time setup).
    """
    new_path = config_home / "kanibako.yaml"
    if new_path.exists():
        return new_path
    old_path = config_home / "kanibako" / "kanibako.yaml"
    if old_path.exists():
        return old_path
    return new_path


def migrate_config(config_home: Path) -> Path:
    """Migrate config file from old location to new, if needed.

    Returns the final config file path (new location).
    Prints a notice to stderr when migration occurs.
    """
    new_path = config_home / "kanibako.yaml"
    old_path = config_home / "kanibako" / "kanibako.yaml"
    if old_path.exists() and not new_path.exists():
        import shutil
        shutil.move(str(old_path), str(new_path))
        print(
            f"Migrated config: {old_path} → {new_path}",
            file=sys.stderr,
        )
        # Remove empty old config dir if it's now empty.
        old_dir = old_path.parent
        try:
            if old_dir.is_dir() and not any(old_dir.iterdir()):
                old_dir.rmdir()
        except OSError:
            pass
    return new_path


def load_config(path: Path) -> KanibakoConfig:
    """Read a single config file and return a KanibakoConfig with defaults filled in."""
    cfg = KanibakoConfig()
    if path.exists():
        data = load_doc(path)
        # Extract [shared] section before flattening (it's a key-value dict,
        # not nested config fields).
        shared = data.pop("shared", {})
        # Extract the [system][path] table before flattening: these are the
        # system-level path tier (resolver expressions), not flat fields.
        system_path = data.get("system", {}).pop("path", {})
        if "system" in data and not data["system"]:
            data.pop("system")
        cfg.system_paths = {
            f"system.path.{k}": str(v) for k, v in system_path.items()
        }
        flat = _flatten_toml(data)
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in flat.items():
            # Apply backward-compat aliases.
            k = _FIELD_ALIASES.get(k, k)
            if k in valid_keys:
                setattr(cfg, k, v)
        cfg.shared_caches = {k: str(v) for k, v in shared.items()}
    return cfg


def load_merged_config(
    global_path: Path,
    project_path: Path | None = None,
    *,
    workset_path: Path | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> KanibakoConfig:
    """Load global config, overlay workset config, project config, then CLI overrides.

    Precedence: CLI flags > project.yaml > workset config.yaml > kanibako.yaml > hardcoded defaults.
    """
    cfg = load_config(global_path)
    defaults = KanibakoConfig()
    # system_paths is SYSTEM-ONLY: only the global config supplies it.  Skip it
    # in the project/workset overlay so a non-global file never clobbers the
    # global's resolved system path tier (its default {} would otherwise be a
    # no-op, but skipping makes the system-only invariant explicit).
    if workset_path and workset_path.exists():
        ws = load_config(workset_path)
        # Only override non-default values from workset config.
        for fld in fields(ws):
            if fld.name == "system_paths":
                continue
            val = getattr(ws, fld.name)
            if val != getattr(defaults, fld.name):
                setattr(cfg, fld.name, val)
    if project_path and project_path.exists():
        proj = load_config(project_path)
        # Only override non-default values from project config.
        for fld in fields(proj):
            if fld.name == "system_paths":
                continue
            val = getattr(proj, fld.name)
            if val != getattr(defaults, fld.name):
                setattr(cfg, fld.name, val)
    if cli_overrides:
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in cli_overrides.items():
            if k in valid_keys:
                setattr(cfg, k, v)
    return cfg


def write_global_config(path: Path, cfg: KanibakoConfig | None = None) -> None:
    """Write a YAML config file with the structured layout.

    If *cfg* is None, writes defaults.
    """
    if cfg is None:
        cfg = KanibakoConfig()
    # System-level path tier (settings-framework "system.path.*"), written at
    # the DEFAULT expressions.  Kept in lock-step with
    # paths.SYSTEM_PATH_DEFAULTS (imported lazily there to avoid an import
    # cycle); the resolver fills these in if the file omits them.
    data: dict = {
        "system": {
            "path": {
                "data": "$XDG_DATA_HOME/kanibako",
                "boxes": "@system.path.data/boxes",
                "crabs": "@system.path.data/crabs",
                "comms": "@system.path.data/comms",
                "templates": "@system.path.data/templates",
                "ws_hints": "@system.path.data/worksets.yaml",
            }
        },
        "box": {
            "image": cfg.box_image,
            "crab": cfg.box_crab,
            "share_images": cfg.box_share_images,
        },
        # Global shared caches (lazy: only mounted if the dir exists on host).
        "shared": {},
    }
    dump_doc(path, data)


def write_project_config(path: Path, image: str) -> None:
    """Write or update a project.yaml with the given image."""
    write_project_config_key(path, "box_image", image)


def write_project_meta(
    path: Path,
    *,
    mode: str,
    layout: str,
    workspace: str,
    shell: str,
    vault_ro: str,
    vault_rw: str,
    enable_vault: bool = True,
    group_auth: bool = True,
    metadata: str = "",
    project_hash: str = "",
    global_shared: str = "",
    local_shared: str = "",
    name: str = "",
) -> None:
    """Write resolved project metadata to project.yaml, preserving other sections."""
    existing = load_doc(path)

    project_sec: dict = {
        "mode": mode, "layout": layout,
        "enable_vault": enable_vault, "group_auth": group_auth,
    }
    if name:
        project_sec["name"] = name
    existing["project"] = project_sec
    existing.setdefault("resolved", {})
    existing["resolved"]["workspace"] = workspace
    existing["resolved"]["shell"] = shell
    existing["resolved"]["vault_ro"] = vault_ro
    existing["resolved"]["vault_rw"] = vault_rw
    existing["resolved"]["metadata"] = metadata
    existing["resolved"]["project_hash"] = project_hash
    existing["resolved"]["global_shared"] = global_shared
    existing["resolved"]["local_shared"] = local_shared

    dump_doc(path, existing)


def read_project_meta(path: Path) -> dict | None:
    """Read stored project metadata from project.yaml.

    Returns a dict with 'mode', 'workspace', 'shell', 'vault_ro', 'vault_rw'
    or None if no project metadata is stored.
    """
    if not path.exists():
        return None
    data = load_doc(path)

    project_sec = data.get("project", {})
    # Support both old ("paths") and new ("resolved") section names.
    resolved_sec = data.get("resolved", data.get("paths", {}))

    if not project_sec.get("mode"):
        return None

    # Backward compat: terminology renamed in v1.0.
    _MODE_COMPAT = {"account_centric": "local", "decentralized": "standalone"}
    raw_mode = project_sec["mode"]
    mode = _MODE_COMPAT.get(raw_mode, raw_mode)

    return {
        "mode": mode,
        # Backward compat: "tree" was renamed to "robust" in v0.6.0.
        "layout": "robust" if project_sec.get("layout") == "tree" else project_sec.get("layout", ""),
        "enable_vault": project_sec.get("enable_vault", True),
        "group_auth": project_sec.get("group_auth", True),
        "name": project_sec.get("name", ""),
        "workspace": resolved_sec.get("workspace", ""),
        "shell": resolved_sec.get("shell", ""),
        "vault_ro": resolved_sec.get("vault_ro", ""),
        "vault_rw": resolved_sec.get("vault_rw", ""),
        "metadata": resolved_sec.get("metadata", ""),
        "project_hash": resolved_sec.get("project_hash", ""),
        "global_shared": resolved_sec.get("global_shared", ""),
        "local_shared": resolved_sec.get("local_shared", ""),
    }


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into (section, key).

    ``"box_image"``       → ``("box", "image")``
    ``"paths_dot_path"``  → ``("paths", "dot_path")``
    """
    for prefix in ("paths_", "box_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            key = flat_key[len(prefix):]
            return section, key
    raise ValueError(f"Cannot determine config section for key: {flat_key}")


def config_keys() -> list[str]:
    """Return all valid flat config key names."""
    return [fld.name for fld in fields(KanibakoConfig)]


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a project.yaml.

    *flat_key* is the underscore-joined config name (e.g. ``"box_image"``).
    """
    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    sec = data.get(section)
    if not isinstance(sec, dict):
        sec = {}
        data[section] = sec
    sec[key] = value
    dump_doc(path, data)


def unset_project_config_key(path: Path, flat_key: str) -> bool:
    """Remove a single key from a project.yaml.

    Returns True if the key was found and removed, False if it was not present.
    """
    if not path.exists():
        return False

    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    sec = data.get(section)
    if not isinstance(sec, dict) or key not in sec:
        return False
    del sec[key]
    # Clean up an empty section.
    if not sec:
        data.pop(section, None)
    dump_doc(path, data)
    return True


def load_project_overrides(path: Path) -> dict[str, str]:
    """Load only the project-level overrides from a project.yaml.

    Returns a dict of flat_key → value for keys that differ from defaults.
    """
    if not path.exists():
        return {}
    proj_cfg = load_config(path)
    defaults = KanibakoConfig()
    overrides: dict[str, str] = {}
    for fld in fields(proj_cfg):
        val = getattr(proj_cfg, fld.name)
        if val != getattr(defaults, fld.name):
            overrides[fld.name] = val
    return overrides


# ---------------------------------------------------------------------------
# Target settings overrides (per-project)
# ---------------------------------------------------------------------------

def read_crab_settings(path: Path) -> dict[str, str]:
    """Read crab-state overrides from a project.yaml ``crab`` section.

    project.yaml's ``crab`` holds box-level crab-state overrides (e.g.
    ``{"model": "sonnet"}``); identity keys live in ``box.crab``, not here.
    Returns an empty dict when the file or section is absent.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    return {k: str(v) for k, v in data.get("crab", {}).items()}


def write_crab_setting(path: Path, key: str, value: str) -> None:
    """Write a single crab-state override to ``crab`` in project.yaml.

    Preserves all other sections.
    """
    existing = load_doc(path)
    existing.setdefault("crab", {})
    existing["crab"][key] = value
    dump_doc(path, existing)


def remove_crab_setting(path: Path, key: str) -> bool:
    """Remove a single crab-state override from ``crab`` in project.yaml.

    Returns True if the setting was found and removed, False otherwise.
    """
    if not path.exists():
        return False
    existing = load_doc(path)
    settings = existing.get("crab", {})
    if key not in settings:
        return False
    del settings[key]
    if not settings:
        existing.pop("crab", None)
    dump_doc(path, existing)
    return True


# ---------------------------------------------------------------------------
# Scoped shares (settings-framework {scope}.path.share_{ro,rw}.*)
# ---------------------------------------------------------------------------

def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict into DOTTED-key form, stringifying scalar leaves.

    ``{"system": {"path": {"share_rw": {"foo": "h:g"}}}}`` →
    ``{"system.path.share_rw.foo": "h:g"}``.
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dotted(v, key))
        else:
            out[key] = str(v)
    return out


def read_shares(path: Path | None) -> dict[str, str]:
    """Read scoped-share keys ({scope}.path.share_{ro,rw}.{name}) from a config
    file as a flat dotted-key dict. Missing/None/unreadable path → {}."""
    from kanibako.settings_shares import is_share_key

    if path is None:
        return {}
    try:
        if not path.exists():
            return {}
        data = load_doc(path)
    except Exception:
        return {}
    flat = _flatten_dotted(data)
    return {k: v for k, v in flat.items() if is_share_key(k)}


def read_seeds(path: Path | None) -> dict[str, str]:
    """Read seed keys ({scope}.path.seeded.{name}) from a config file as a flat
    dotted-key dict. Missing/None/unreadable path → {}."""
    from kanibako.settings_seeds import is_seed_key

    if path is None:
        return {}
    try:
        if not path.exists():
            return {}
        data = load_doc(path)
    except Exception:
        return {}
    flat = _flatten_dotted(data)
    return {k: v for k, v in flat.items() if is_seed_key(k)}


# ---------------------------------------------------------------------------
# Resource scope overrides (per-project)
# ---------------------------------------------------------------------------

def read_resource_overrides(path: Path) -> dict[str, str]:
    """Read ``resource_overrides`` from a project.yaml.

    Returns a dict of resource_path → scope_string (e.g. ``"shared"``).
    Returns an empty dict when the file or section is absent.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    return {k: str(v) for k, v in data.get("resource_overrides", {}).items()}


def write_resource_override(path: Path, resource_path: str, scope: str) -> None:
    """Write a single resource scope override to ``resource_overrides`` in project.yaml.

    Preserves all other sections.
    """
    existing = load_doc(path)
    existing.setdefault("resource_overrides", {})
    existing["resource_overrides"][resource_path] = scope
    dump_doc(path, existing)


def remove_resource_override(path: Path, resource_path: str) -> bool:
    """Remove a single resource scope override from ``resource_overrides``.

    Returns True if the override was found and removed, False otherwise.
    """
    if not path.exists():
        return False
    existing = load_doc(path)
    overrides = existing.get("resource_overrides", {})
    if resource_path not in overrides:
        return False
    del overrides[resource_path]
    if not overrides:
        # Remove the empty section entirely.
        existing.pop("resource_overrides", None)
    dump_doc(path, existing)
    return True
