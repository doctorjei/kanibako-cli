"""YAML config loading, writing, defaults, and merge logic."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path

from kanibako.config_io import dump_doc, load_doc
from kanibako.settings_resolve import LevelView, ResolveCtx, SettingsResolver


# ---------------------------------------------------------------------------
# Defaults (match the old kanibako.rc values)
# ---------------------------------------------------------------------------

_DEFAULTS = {
    "paths_project_toml": "project.yaml",
    "paths_shared": "shared",
    "paths_shell": "shell",
    "paths_vault": "vault",
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_agent": "",
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
    box_agent: str = _DEFAULTS["box_agent"]
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


def config_base_path() -> Path:
    """Return the machine-wide CONFIG base file (``/etc/kanibako/config_base.yaml``).

    The least-specific layer of the CONFIG (``system.*``) file set: a site admin
    supplies overridable defaults that the user's ``~/.config/kanibako.yaml``
    (and, above it, the non-overridable ``config_required.yaml``) can still beat.
    Missing file → treated as an empty level.
    """
    return Path("/etc/kanibako/config_base.yaml")


def config_required_path() -> Path:
    """Return the machine-wide CONFIG required file (``/etc/kanibako/config_required.yaml``).

    The MOST-specific layer of the CONFIG (``system.*``) file set — it is
    **non-overridable**: applied LAST so a site admin can pin values that neither
    the base layer nor the user's ``~/.config/kanibako.yaml`` can override.
    Missing file → treated as an empty level.
    """
    return Path("/etc/kanibako/config_required.yaml")


def settings_base_path() -> Path:
    """Return the machine-wide SETTINGS base file (``/etc/kanibako/settings_base.yaml``).

    The LEAST-specific (bottom) layer of the SETTINGS (behavior) cascade — below
    every scope (``system``/``agent``/``workset``/``box``): a site admin supplies
    overridable behavior defaults that any scope can still beat.  Missing file →
    treated as an empty level (so its absence preserves current behavior).
    """
    return Path("/etc/kanibako/settings_base.yaml")


def settings_required_path() -> Path:
    """Return the machine-wide SETTINGS required file (``/etc/kanibako/settings_required.yaml``).

    The MOST-specific (top) layer of the SETTINGS (behavior) cascade — it is
    **non-overridable** and sits ABOVE ``box`` (decision D): applied LAST so a
    site admin can pin behavior that no scope (not even ``box``) can override.
    Missing file → treated as an empty level (so its absence preserves current
    behavior).
    """
    return Path("/etc/kanibako/settings_required.yaml")


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
                "agents": "@system.path.data/agents",
                "comms": "@system.path.data/comms",
                "templates": "@system.path.data/templates",
                "ws_hints": "@system.path.data/worksets.yaml",
            }
        },
        "box": {
            "image": cfg.box_image,
            "agent": cfg.box_agent,
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
    ``"allow_helpers"``   → ``("", "allow_helpers")`` (top-level scalar field)

    A flat key with no recognised section prefix is a TOP-LEVEL scalar field
    (e.g. ``allow_helpers``); it returns an empty section rather than raising
    (the typed writer in ``config_interface`` is the routed set/get/reset path —
    this helper only serves the few remaining flat-key callers and must never
    crash on an advertised key).
    """
    for prefix in ("paths_", "box_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            key = flat_key[len(prefix):]
            return section, key
    return "", flat_key


def config_keys() -> list[str]:
    """Return all valid flat config key names."""
    return [fld.name for fld in fields(KanibakoConfig)]


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a project.yaml.

    *flat_key* is the underscore-joined config name (e.g. ``"box_image"``).
    """
    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    if not section:
        # Top-level scalar field (e.g. allow_helpers).
        data[key] = value
        dump_doc(path, data)
        return
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
    if not section:
        # Top-level scalar field (e.g. allow_helpers).
        if key not in data:
            return False
        del data[key]
        dump_doc(path, data)
        return True
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

def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]:
    """Read agent-keyed agent-state overrides from a config file's ``agent`` table.

    Override sections are keyed per agent under ``agent.<agent_name>``, layered
    over the reserved any-agent ``agent.default`` tier (the agent-specific value
    wins within a single file). This stops an override set while a box is on one
    agent (e.g. ``model`` under ``agent.claude``) from bleeding onto another
    agent after the box is switched (e.g. to ``goose``); identity keys live in
    ``box.agent``, not here.

    ``agent.default`` is RESERVED as the any-agent default tier; no real agent
    may be named ``default``.

    **No pass-1 migration.** A legacy FLAT ``[agent]`` table (scalar values
    written directly under ``agent``, e.g. ``agent.model``) is treated as UNSET —
    only nested per-agent dicts (``agent.default`` / ``agent.<agent_name>``) are
    honored. Configs are hand-edited to the new shape. The common no-config case
    (absent file, or absent/empty ``agent`` table) still returns ``{}`` unchanged.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return {}
    out: dict[str, str] = {}
    default_sec = agent.get("default")
    if isinstance(default_sec, dict):
        out.update({k: str(v) for k, v in default_sec.items()})
    agent_sec = agent.get(agent_name)
    if isinstance(agent_sec, dict):
        out.update({k: str(v) for k, v in agent_sec.items()})
    return out


def load_settings(
    agent_name: str,
    *,
    system_path: Path | None,
    agent_state: Mapping[str, str] | None = None,
    workset_path: Path | None = None,
    box_path: Path | None = None,
    agent_path: Path | None = None,
    floor: Mapping[str, str] | None = None,
    host_home: str | None = None,
    workset_name: str | None = None,
    xdg: dict[str, str] | None = None,
) -> SettingsResolver:
    """Assemble the behavior-settings cascade into a :class:`SettingsResolver`.

    This is the unifying READ-path for behavior settings.  It builds the ordered
    ``list[LevelView]`` (MOST-SPECIFIC-FIRST, as :func:`resolve_value` expects)
    for the 5-tier cascade plus the non-overridable ``required`` cap (decision
    D):

        settings_base < system < agent.<agent> < workset < box < settings_required

    * ``settings_base``     — ``/etc/kanibako/settings_base.yaml`` (defaults; lowest).
    * ``system``            — the system-level settings file.
    * ``agent.<agent>``     — the per-agent settings tier.
    * ``workset``           — the workset settings file.
    * ``box``               — the box/project settings file.
    * ``settings_required`` — ``/etc/kanibako/settings_required.yaml``;
      **non-overridable**, applied ABOVE box so it wins over everything.

    ⚑ TRANSITIONAL FILE MAPPING (the new per-scope ``settings.yaml`` LAYOUT is
    Phase 5 — files are NOT moved here; only re-pointed there).  Each tier pulls
    its set-values from the CURRENT on-disk locations via the existing
    :func:`read_agent_settings` reader (which already layers
    ``agent.default`` < ``agent.<agent>`` within a single file).  Phase 5 only
    re-points these paths:

    * ``settings_base`` tier  → ``/etc/kanibako/settings_base.yaml``  (NEW;
      additive — empty by default, so its absence preserves current behavior).
    * ``system`` tier  → *system_path* = today's user-global
      ``~/.config/kanibako.yaml`` ``[agent]`` table.  (TARGET ``system.settings``
      = ``@system.global/settings.yaml`` — re-pointed in Phase 5.)
    * ``agent.<agent>`` tier → *agent_state* (the per-agent state dict, already
      ``agent.<name>``-keyed, loaded from ``agents/<name>.yaml``) overlaid by
      *agent_path*'s ``[agent]`` table if supplied.
    * ``workset`` tier → *workset_path* = today's workset ``config.yaml``.
    * ``box`` tier → *box_path* = today's box/project ``project.yaml`` ``[agent]``
      table.
    * ``settings_required`` tier → ``/etc/kanibako/settings_required.yaml``  (NEW;
      additive — empty by default).

    *floor* (the target's declared-default ``{key: default}``) is attached as the
    ``settings_base`` level's declared defaults — set-values at any tier beat it,
    and it is the ultimate fallback.  *agent_state* and *floor* are optional so
    callers that only have files can omit them.  Absent optional file layers
    contribute an empty :class:`LevelView` (skipped during resolution).
    """
    def _read(path: Path | None) -> dict[str, str]:
        if path is None:
            return {}
        try:
            if not path.exists():
                return {}
            return read_agent_settings(path, agent_name)
        except Exception:
            return {}

    # agent tier: the per-agent state dict, overlaid by an explicit agent file.
    agent_vals: dict[str, str] = dict(agent_state or {})
    agent_vals.update(_read(agent_path))

    base_vals = _read(settings_base_path())
    required_vals = _read(settings_required_path())

    # MOST-SPECIFIC-FIRST: required (cap) > box > workset > agent > system > base.
    levels = [
        LevelView("settings_required", required_vals),
        LevelView("box", _read(box_path)),
        LevelView("workset", _read(workset_path)),
        LevelView("agent", agent_vals),
        LevelView("system", _read(system_path)),
        LevelView("settings_base", base_vals, defaults=dict(floor or {})),
    ]

    ctx = ResolveCtx(
        agent_name=agent_name,
        workset_name=workset_name,
        host_home=host_home if host_home is not None else str(Path.home()),
        xdg=xdg or {},
    )
    return SettingsResolver(levels, ctx)


def write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None:
    """Write a single agent-state override under ``agent.<agent_name>``.

    Preserves all other sections and other agents' agent subsections. Pass the
    reserved ``"default"`` agent name to target the any-agent default tier.
    """
    existing = load_doc(path)
    agent = existing.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        existing["agent"] = agent
    agent_sec = agent.get(agent_name)
    if not isinstance(agent_sec, dict):
        agent_sec = {}
        agent[agent_name] = agent_sec
    agent_sec[key] = value
    dump_doc(path, existing)


def remove_agent_setting(path: Path, key: str, agent_name: str) -> bool:
    """Remove a single agent-state override from ``agent.<agent_name>``.

    Returns True if the setting was found and removed, False otherwise. Prunes a
    now-empty agent subsection and a now-empty ``agent`` table.
    """
    if not path.exists():
        return False
    existing = load_doc(path)
    agent = existing.get("agent")
    if not isinstance(agent, dict):
        return False
    agent_sec = agent.get(agent_name)
    if not isinstance(agent_sec, dict) or key not in agent_sec:
        return False
    del agent_sec[key]
    if not agent_sec:
        del agent[agent_name]
    if not agent:
        existing.pop("agent", None)
    dump_doc(path, existing)
    return True


def read_binding_overrides(path: Path | None, agent_name: str) -> dict[str, str]:
    """Read agent-keyed binding host-source overrides from a config ``agent`` table.

    Reads the ``binding`` sub-table under ``agent.<agent_name>`` layered over the
    reserved any-agent ``agent.default.binding`` tier (the agent-specific value
    wins within a single file) — the SAME agent-keying as
    :func:`read_agent_settings`. These redirect the HOST SOURCE of a descriptor
    :class:`~kanibako.targets.base.Binding` (e.g. ``agent.claude.binding.plugins``
    points the claude ``plugins`` share at a custom host directory).

    Returns ``{binding_key: host_src}``. Each binding VALUE may be either:

    * a bare string ``host_src`` (``agent.claude.binding.plugins = "/path"``), or
    * a sub-table carrying a ``host_src`` key
      (``agent.claude.binding.plugins.host_src = "/path"``).

    A sub-table without a string ``host_src`` (and any other non-string value)
    is skipped. As with :func:`read_agent_settings`, a legacy FLAT ``[agent]``
    table is treated as UNSET (no pass-1 migration); the common no-config case
    (absent/None/unreadable path, or absent ``agent``/``binding`` table) returns
    ``{}``.
    """
    if path is None or not path.exists():
        return {}
    try:
        data = load_doc(path)
    except Exception:
        return {}
    agent = data.get("agent", {})
    if not isinstance(agent, dict):
        return {}
    out: dict[str, str] = {}
    # Least-specific (default tier) first so the agent-specific tier wins.
    for tier in ("default", agent_name):
        section = agent.get(tier)
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
