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
    "box_bootstrap_program": "tmux",
    "box_shell": "",
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
    box_bootstrap_program: str = _DEFAULTS["box_bootstrap_program"]
    box_shell: str = _DEFAULTS["box_shell"]
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
    Booleans are preserved; ``None`` (YAML ``null``/empty) is preserved as the
    "reset to built-in default" sentinel; other scalars are stringified.
    """
    out: dict[str, object] = {}
    for k, v in data.items():
        key = f"{prefix}_{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_toml(v, key))
        elif isinstance(v, bool):
            out[key] = v
        elif v is None:
            out[key] = None
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


def machine_config_path() -> Path:
    """Return the machine-wide config path (``/etc/kanibako/kanibako.yaml``).

    This sits BELOW the user's ``~/.config`` config and ABOVE the built-in
    ``_DEFAULTS``: a site admin can set defaults for all users that an individual
    user can still override.  Missing file → treated as an empty level.
    """
    return Path("/etc/kanibako/kanibako.yaml")


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


def _present_scalar_fields(path: Path) -> dict[str, object]:
    """Parse a config file and return ONLY the scalar/bool fields actually
    present in it, as a field-name → value mapping.

    A value of ``None`` (YAML ``null``/``~``/empty ``foo:``) is preserved as the
    "reset to built-in default" sentinel; callers must distinguish it from an
    absent key (which simply won't appear in the returned dict).

    The dict fields (``shared_caches``, ``system_paths``) are NOT included here;
    they keep their own dedicated parsing/merge logic.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    # Drop the sections handled by dedicated logic so they don't leak into the
    # scalar field overlay.
    data.pop("shared", None)
    if isinstance(data.get("system"), dict):
        data["system"].pop("path", None)
        if not data["system"]:
            data.pop("system")
    flat = _flatten_toml(data)
    valid_keys = {fld.name for fld in fields(KanibakoConfig)}
    present: dict[str, object] = {}
    for k, v in flat.items():
        # Apply backward-compat aliases.
        k = _FIELD_ALIASES.get(k, k)
        if k in valid_keys:
            present[k] = v
    return present


def load_config(path: Path) -> KanibakoConfig:
    """Read a single config file and return a KanibakoConfig with defaults filled in."""
    cfg = KanibakoConfig()
    if path.exists():
        data = load_doc(path)
        # Extract [shared] section before flattening (it's a key-value dict,
        # not nested config fields).
        shared = data.get("shared", {})
        # Extract the [system][path] table: these are the system-level path
        # tier (resolver expressions), not flat fields.
        system_path = data.get("system", {}).get("path", {})
        cfg.system_paths = {
            f"system.path.{k}": str(v) for k, v in system_path.items()
        }
        # Scalar/bool fields: a present key sets the field; a ``None`` value
        # (YAML null/empty) resets it to the built-in default.
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(KanibakoConfig(), k))
            else:
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
    """Load machine + global config, overlay workset, project, then CLI overrides.

    Precedence: CLI flags > project.yaml > workset config.yaml > kanibako.yaml
    (user) > /etc/kanibako/kanibako.yaml (machine) > hardcoded defaults.

    The machine layer (``/etc/kanibako/kanibako.yaml``) is the least-specific
    file source: it beats the built-in defaults but the user's ``~/.config``
    global config beats it.  A missing machine file is an empty level.
    """
    defaults = KanibakoConfig()

    def _overlay_scalars(cfg: KanibakoConfig, path: Path) -> None:
        """Overlay one file layer's PRESENT scalar/bool fields onto *cfg*.

        Presence-based: a key absent from this layer leaves the underlying value
        untouched; a present key with a ``None`` value (YAML null/empty) resets
        the field to its built-in default; any other present value (including
        ``""``) sets the field.  ``system_paths`` is SYSTEM-ONLY and handled
        separately, so it never appears here.
        """
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(defaults, k))
            else:
                setattr(cfg, k, v)

    # Start from the machine doc (least-specific file source), then overlay the
    # user global, workset, and project layers in order so the most-specific
    # present value wins (with null/empty resetting to the built-in default).
    cfg = load_config(machine_config_path())
    glob = load_config(global_path)
    _overlay_scalars(cfg, global_path)
    # shared_caches (DICT field): keep the existing merge — a layer that supplies
    # a non-empty mapping wins over the underlying one (last non-empty wins).
    if glob.shared_caches != defaults.shared_caches:
        cfg.shared_caches = glob.shared_caches
    # system_paths: the global config wins when it supplies one (matches the
    # prior behavior where load_config(global_path) was the base); else keep
    # whatever the machine layer provided.
    if glob.system_paths:
        cfg.system_paths = glob.system_paths
    if workset_path and workset_path.exists():
        ws = load_config(workset_path)
        _overlay_scalars(cfg, workset_path)
        if ws.shared_caches != defaults.shared_caches:
            cfg.shared_caches = ws.shared_caches
    if project_path and project_path.exists():
        proj = load_config(project_path)
        _overlay_scalars(cfg, project_path)
        if proj.shared_caches != defaults.shared_caches:
            cfg.shared_caches = proj.shared_caches
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

    # Backward compat: terminology renamed over time. "account_centric"
    # (v1.0) and "local" (v1.5.0 mode rename) both map to "default"; old
    # "decentralized" maps to "standalone".
    _MODE_COMPAT = {"account_centric": "default", "decentralized": "standalone", "local": "default"}
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

def read_crab_settings(path: Path, agent_name: str) -> dict[str, str]:
    """Read agent-keyed crab-state overrides from a config file's ``crab`` table.

    Override sections are keyed per agent under ``crab.<agent_name>``, layered
    over the reserved any-agent ``crab.default`` tier (the agent-specific value
    wins within a single file). This stops an override set while a box is on one
    agent (e.g. ``model`` under ``crab.claude``) from bleeding onto another
    agent after the box is switched (e.g. to ``goose``); identity keys live in
    ``box.crab``, not here.

    ``crab.default`` is RESERVED as the any-agent default tier; no real agent
    may be named ``default``.

    **No pass-1 migration.** A legacy FLAT ``[crab]`` table (scalar values
    written directly under ``crab``, e.g. ``crab.model``) is treated as UNSET —
    only nested per-agent dicts (``crab.default`` / ``crab.<agent_name>``) are
    honored. Configs are hand-edited to the new shape. The common no-config case
    (absent file, or absent/empty ``crab`` table) still returns ``{}`` unchanged.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    crab = data.get("crab", {})
    if not isinstance(crab, dict):
        return {}
    out: dict[str, str] = {}
    default_sec = crab.get("default")
    if isinstance(default_sec, dict):
        out.update({k: str(v) for k, v in default_sec.items()})
    agent_sec = crab.get(agent_name)
    if isinstance(agent_sec, dict):
        out.update({k: str(v) for k, v in agent_sec.items()})
    return out


def write_crab_setting(path: Path, key: str, value: str, agent_name: str) -> None:
    """Write a single crab-state override under ``crab.<agent_name>``.

    Preserves all other sections and other agents' crab subsections. Pass the
    reserved ``"default"`` agent name to target the any-agent default tier.
    """
    existing = load_doc(path)
    crab = existing.get("crab")
    if not isinstance(crab, dict):
        crab = {}
        existing["crab"] = crab
    agent_sec = crab.get(agent_name)
    if not isinstance(agent_sec, dict):
        agent_sec = {}
        crab[agent_name] = agent_sec
    agent_sec[key] = value
    dump_doc(path, existing)


def remove_crab_setting(path: Path, key: str, agent_name: str) -> bool:
    """Remove a single crab-state override from ``crab.<agent_name>``.

    Returns True if the setting was found and removed, False otherwise. Prunes a
    now-empty agent subsection and a now-empty ``crab`` table.
    """
    if not path.exists():
        return False
    existing = load_doc(path)
    crab = existing.get("crab")
    if not isinstance(crab, dict):
        return False
    agent_sec = crab.get(agent_name)
    if not isinstance(agent_sec, dict) or key not in agent_sec:
        return False
    del agent_sec[key]
    if not agent_sec:
        del crab[agent_name]
    if not crab:
        existing.pop("crab", None)
    dump_doc(path, existing)
    return True


def read_binding_overrides(path: Path | None, agent_name: str) -> dict[str, str]:
    """Read agent-keyed binding host-source overrides from a config ``crab`` table.

    Reads the ``binding`` sub-table under ``crab.<agent_name>`` layered over the
    reserved any-agent ``crab.default.binding`` tier (the agent-specific value
    wins within a single file) — the SAME agent-keying as
    :func:`read_crab_settings`. These redirect the HOST SOURCE of a descriptor
    :class:`~kanibako.targets.base.Binding` (e.g. ``crab.claude.binding.plugins``
    points the claude ``plugins`` share at a custom host directory).

    Returns ``{binding_key: host_src}``. Each binding VALUE may be either:

    * a bare string ``host_src`` (``crab.claude.binding.plugins = "/path"``), or
    * a sub-table carrying a ``host_src`` key
      (``crab.claude.binding.plugins.host_src = "/path"``).

    A sub-table without a string ``host_src`` (and any other non-string value)
    is skipped. As with :func:`read_crab_settings`, a legacy FLAT ``[crab]``
    table is treated as UNSET (no pass-1 migration); the common no-config case
    (absent/None/unreadable path, or absent ``crab``/``binding`` table) returns
    ``{}``.
    """
    if path is None or not path.exists():
        return {}
    try:
        data = load_doc(path)
    except Exception:
        return {}
    crab = data.get("crab", {})
    if not isinstance(crab, dict):
        return {}
    out: dict[str, str] = {}
    # Least-specific (default tier) first so the agent-specific tier wins.
    for tier in ("default", agent_name):
        section = crab.get(tier)
        if not isinstance(section, dict):
            continue
        binding = section.get("binding")
        if not isinstance(binding, dict):
            continue
        for key, val in binding.items():
            if isinstance(val, str):
                out[key] = val
            elif isinstance(val, dict):
                host_src = val.get("host_src")
                if isinstance(host_src, str):
                    out[key] = host_src
    return out


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
