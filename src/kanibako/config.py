"""TOML config loading, writing, defaults, and merge logic."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, fields
from pathlib import Path

# Python 3.11+ stdlib
import tomllib


# ---------------------------------------------------------------------------
# Defaults (match the old kanibako.rc values)
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "paths_data_path": "",
    "paths_agents": "agents",
    "paths_boxes": "boxes",
    "paths_project_toml": "project.toml",
    "paths_shared": "shared",
    "paths_shell": "shell",
    "paths_templates": "templates",
    "paths_vault": "vault",
    "paths_workspaces": "workspaces",
    "paths_ws_hints": "working_sets.toml",
    "container_image": "ghcr.io/doctorjei/kanibako-base:latest",
    "target_name": "",
}

# Backward-compat aliases: old field name -> new field name.
# Applied during load_config() so old TOML files still work.
_FIELD_ALIASES: dict[str, str] = {
    "paths_relative_std_path": "paths_data_path",
    "paths_settings_path": "paths_boxes",
}


@dataclass
class KanibakoConfig:
    """Merged configuration (hardcoded defaults < kanibako.toml < project.toml < CLI)."""

    paths_data_path: str = _DEFAULTS["paths_data_path"]
    paths_agents: str = _DEFAULTS["paths_agents"]
    paths_boxes: str = _DEFAULTS["paths_boxes"]
    paths_project_toml: str = _DEFAULTS["paths_project_toml"]
    paths_shared: str = _DEFAULTS["paths_shared"]
    paths_shell: str = _DEFAULTS["paths_shell"]
    paths_templates: str = _DEFAULTS["paths_templates"]
    paths_vault: str = _DEFAULTS["paths_vault"]
    paths_workspaces: str = _DEFAULTS["paths_workspaces"]
    paths_ws_hints: str = _DEFAULTS["paths_ws_hints"]
    container_image: str = _DEFAULTS["container_image"]
    target_name: str = _DEFAULTS["target_name"]
    shared_caches: dict[str, str] = field(default_factory=dict)


def _flatten_toml(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested TOML dict into underscore-joined keys.

    ``{"paths": {"boxes": "x"}}`` → ``{"paths_boxes": "x"}``
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_toml(v, key))
        else:
            out[key] = str(v)
    return out


def config_file_path(config_home: Path) -> Path:
    """Return the path to kanibako.toml, checking new then old location.

    New: ``$XDG_CONFIG_HOME/kanibako.toml``
    Old: ``$XDG_CONFIG_HOME/kanibako/kanibako.toml``

    Returns the new path if neither exists (for first-time setup).
    """
    new_path = config_home / "kanibako.toml"
    if new_path.exists():
        return new_path
    old_path = config_home / "kanibako" / "kanibako.toml"
    if old_path.exists():
        return old_path
    return new_path


def migrate_config(config_home: Path) -> Path:
    """Migrate config file from old location to new, if needed.

    Returns the final config file path (new location).
    Prints a notice to stderr when migration occurs.
    """
    new_path = config_home / "kanibako.toml"
    old_path = config_home / "kanibako" / "kanibako.toml"
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
    """Read a single TOML file and return a KanibakoConfig with defaults filled in."""
    cfg = KanibakoConfig()
    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        # Extract [shared] section before flattening (it's a key-value dict,
        # not nested config fields).
        shared = data.pop("shared", {})
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
    cli_overrides: dict[str, str] | None = None,
) -> KanibakoConfig:
    """Load global config, overlay project config, then CLI overrides.

    Precedence: CLI flags > project.toml > kanibako.toml > hardcoded defaults.
    """
    cfg = load_config(global_path)
    if project_path and project_path.exists():
        proj = load_config(project_path)
        # Only override non-default values from project config.
        defaults = KanibakoConfig()
        for fld in fields(proj):
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
    """Write a TOML config file with the structured layout.

    If *cfg* is None, writes defaults.
    """
    if cfg is None:
        cfg = KanibakoConfig()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "[paths]",
        f'data_path = "{cfg.paths_data_path}"',
        f'boxes = "{cfg.paths_boxes}"',
        f'shell = "{cfg.paths_shell}"',
        f'vault = "{cfg.paths_vault}"',
        "",
        "[container]",
        f'image = "{cfg.container_image}"',
        "",
        "[shared]",
        '# Global shared caches (lazy: only mounted if dir exists on host)',
        '# cargo-git = ".cargo/git"',
        '# cargo-reg = ".cargo/registry"',
        '# npm = ".cache/npm"',
        '# pip = ".cache/pip"',
        '# uv = ".cache/uv"',
        "",
    ]
    path.write_text("\n".join(lines))


def write_project_config(path: Path, image: str) -> None:
    """Write or update a project.toml with the given image."""
    write_project_config_key(path, "container_image", image)


def write_project_meta(
    path: Path,
    *,
    mode: str,
    layout: str,
    workspace: str,
    shell: str,
    vault_ro: str,
    vault_rw: str,
    vault_enabled: bool = True,
    auth: str = "shared",
    metadata: str = "",
    project_hash: str = "",
    global_shared: str = "",
    local_shared: str = "",
) -> None:
    """Write resolved project metadata to project.toml, preserving other sections."""
    existing: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            existing = tomllib.load(f)

    existing["project"] = {
        "mode": mode, "layout": layout,
        "vault_enabled": vault_enabled, "auth": auth,
    }
    existing.setdefault("resolved", {})
    existing["resolved"]["workspace"] = workspace
    existing["resolved"]["shell"] = shell
    existing["resolved"]["vault_ro"] = vault_ro
    existing["resolved"]["vault_rw"] = vault_rw
    existing["resolved"]["metadata"] = metadata
    existing["resolved"]["project_hash"] = project_hash
    existing["resolved"]["global_shared"] = global_shared
    existing["resolved"]["local_shared"] = local_shared

    _write_toml(path, existing)


def read_project_meta(path: Path) -> dict | None:
    """Read stored project metadata from project.toml.

    Returns a dict with 'mode', 'workspace', 'shell', 'vault_ro', 'vault_rw'
    or None if no project metadata is stored.
    """
    if not path.exists():
        return None
    with open(path, "rb") as f:
        data = tomllib.load(f)

    project_sec = data.get("project", {})
    # Support both old ("paths") and new ("resolved") section names.
    resolved_sec = data.get("resolved", data.get("paths", {}))

    if not project_sec.get("mode"):
        return None

    return {
        "mode": project_sec["mode"],
        # Backward compat: "tree" was renamed to "robust" in v0.6.0.
        "layout": "robust" if project_sec.get("layout") == "tree" else project_sec.get("layout", ""),
        "vault_enabled": project_sec.get("vault_enabled", True),
        "auth": project_sec.get("auth", "shared"),
        "workspace": resolved_sec.get("workspace", ""),
        "shell": resolved_sec.get("shell", ""),
        "vault_ro": resolved_sec.get("vault_ro", ""),
        "vault_rw": resolved_sec.get("vault_rw", ""),
        "metadata": resolved_sec.get("metadata", ""),
        "project_hash": resolved_sec.get("project_hash", ""),
        "global_shared": resolved_sec.get("global_shared", ""),
        "local_shared": resolved_sec.get("local_shared", ""),
    }


def _toml_key(k: str) -> str:
    """Quote a TOML key if it contains characters outside [A-Za-z0-9_-]."""
    if re.fullmatch(r"[A-Za-z0-9_-]+", k):
        return k
    return f'"{k}"'


def _write_toml(path: Path, data: dict) -> None:
    """Write a dict as TOML. Handles one level of nesting (sections with scalar values)."""
    lines: list[str] = []
    for section_name, section_data in data.items():
        if not isinstance(section_data, dict):
            continue
        if lines:
            lines.append("")
        lines.append(f"[{section_name}]")
        for k, v in section_data.items():
            key = _toml_key(k)
            if isinstance(v, bool):
                lines.append(f"{key} = {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{key} = {v}")
            else:
                lines.append(f'{key} = "{v}"')
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines))


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into (section, toml_key).

    ``"container_image"`` → ``("container", "image")``
    ``"paths_dot_path"``  → ``("paths", "dot_path")``
    """
    for prefix in ("paths_", "container_", "target_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            toml_key = flat_key[len(prefix):]
            return section, toml_key
    raise ValueError(f"Cannot determine TOML section for key: {flat_key}")


def config_keys() -> list[str]:
    """Return all valid flat config key names."""
    return [fld.name for fld in fields(KanibakoConfig)]


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a project.toml.

    *flat_key* is the underscore-joined config name (e.g. ``"container_image"``).
    """
    section, toml_key = _split_config_key(flat_key)
    section_header = f"[{section}]"

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text()
        # NOTE: This regex approach only handles quoted-string values and
        # assumes key names are unique across TOML sections.
        # Replace existing key line
        if re.search(rf'^{re.escape(toml_key)}\s*=', text, re.MULTILINE):
            text = re.sub(
                rf'^{re.escape(toml_key)}\s*=\s*"[^"]*"',
                f'{toml_key} = "{value}"',
                text,
                flags=re.MULTILINE,
            )
            path.write_text(text)
            return
        # Has the right section but no matching key
        if section_header in text:
            text = text.replace(
                section_header, f'{section_header}\n{toml_key} = "{value}"', 1
            )
            path.write_text(text)
            return
        # File exists but different section — append
        if not text.endswith("\n"):
            text += "\n"
        text += f"\n{section_header}\n{toml_key} = \"{value}\"\n"
        path.write_text(text)
        return
    # New file
    lines = [
        section_header,
        f'{toml_key} = "{value}"',
        "",
    ]
    path.write_text("\n".join(lines))


def unset_project_config_key(path: Path, flat_key: str) -> bool:
    """Remove a single key from a project.toml.

    Returns True if the key was found and removed, False if it was not present.
    """
    if not path.exists():
        return False

    section, toml_key = _split_config_key(flat_key)
    text = path.read_text()

    # Remove the key line (including trailing newline)
    new_text, count = re.subn(
        rf'^{re.escape(toml_key)}\s*=\s*"[^"]*"\n?',
        "",
        text,
        flags=re.MULTILINE,
    )
    if count == 0:
        return False

    # Clean up empty sections: if the section header is followed by
    # nothing (or only blank lines) before the next section or EOF, remove it.
    new_text = re.sub(
        r'^\[[\w]+\]\n(?=\s*(?:\[|$))',
        "",
        new_text,
        flags=re.MULTILINE,
    )
    # Strip trailing whitespace
    new_text = new_text.rstrip() + "\n" if new_text.strip() else ""

    path.write_text(new_text)
    return True


def load_project_overrides(path: Path) -> dict[str, str]:
    """Load only the project-level overrides from a project.toml.

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
# Resource scope overrides (per-project)
# ---------------------------------------------------------------------------

def read_resource_overrides(path: Path) -> dict[str, str]:
    """Read ``[resource_overrides]`` from a project.toml.

    Returns a dict of resource_path → scope_string (e.g. ``"shared"``).
    Returns an empty dict when the file or section is absent.
    """
    if not path.exists():
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return {k: str(v) for k, v in data.get("resource_overrides", {}).items()}


def write_resource_override(path: Path, resource_path: str, scope: str) -> None:
    """Write a single resource scope override to ``[resource_overrides]`` in project.toml.

    Preserves all other sections.
    """
    existing: dict = {}
    if path.exists():
        with open(path, "rb") as f:
            existing = tomllib.load(f)
    existing.setdefault("resource_overrides", {})
    existing["resource_overrides"][resource_path] = scope
    _write_toml(path, existing)


def remove_resource_override(path: Path, resource_path: str) -> bool:
    """Remove a single resource scope override from ``[resource_overrides]``.

    Returns True if the override was found and removed, False otherwise.
    """
    if not path.exists():
        return False
    with open(path, "rb") as f:
        existing = tomllib.load(f)
    overrides = existing.get("resource_overrides", {})
    if resource_path not in overrides:
        return False
    del overrides[resource_path]
    if not overrides:
        # Remove the empty section entirely.
        existing.pop("resource_overrides", None)
    _write_toml(path, existing)
    return True
