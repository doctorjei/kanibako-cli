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

# Per-box construct-time metadata + box-tier settings cascade file (TARGET §2c box.meta.*)
BOX_META_FILE = "settings.yaml"

# Shared boolean truth tables: used by the typed `config set` writer
# (config_interface) AND the box.meta writer so both round-trip identically.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


def coerce_bool(value: object) -> bool | None:
    """Coerce a config value to a real bool using the shared truth table.

    Returns the bool, or None if *value* is not a recognized bool literal.
    Already-bool values pass through. Used by the typed `config set` writer
    (config_interface) AND the box.meta writer so both round-trip identically.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    return None


_DEFAULTS = {
    "paths_project_toml": BOX_META_FILE,
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
    """Merged configuration (hardcoded defaults < kanibako.yaml < settings.yaml < CLI)."""

    paths_project_toml: str = _DEFAULTS["paths_project_toml"]
    box_image: str = _DEFAULTS["box_image"]
    box_agent: str = _DEFAULTS["box_agent"]
    box_bootstrap_program: str = _DEFAULTS["box_bootstrap_program"]
    box_shell: str = _DEFAULTS["box_shell"]
    allow_helpers: bool = True
    box_share_images: bool = False
    # System-level config tier: raw set-values keyed by full dotted name
    # (bare "system.<leaf>"), read from the file's flat [system] table.
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

    The dict field (``system_paths``) is NOT included here; it keeps its own
    dedicated parsing/merge logic.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    # Drop the sections handled by dedicated logic so they don't leak into the
    # scalar field overlay.  The whole [system] table is the config tier
    # (handled by load_config's system_paths extraction), not flat fields.
    data.pop("system", None)
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
        # Extract the [system] table: the system-level config tier (resolver
        # expressions), keyed by bare ``system.<leaf>`` dotted names.  The table
        # is flattened so nested sub-keys (e.g. ``system.channels.commons``)
        # become dotted keys; scalar leaves (e.g. ``system.data``) stay flat.
        system_tbl = data.get("system", {})
        if isinstance(system_tbl, dict):
            cfg.system_paths = _flatten_dotted(system_tbl, "system")
        else:
            cfg.system_paths = {}
        # Scalar/bool fields: a present key sets the field; a ``None`` value
        # (YAML null/empty) resets it to the built-in default.
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(KanibakoConfig(), k))
            else:
                setattr(cfg, k, v)
    return cfg


def load_merged_config(
    global_path: Path,
    project_path: Path | None = None,
    *,
    workset_path: Path | None = None,
    cli_overrides: dict[str, str] | None = None,
) -> KanibakoConfig:
    """Load machine + global config, overlay workset, project, then CLI overrides.

    Precedence: CLI flags > settings.yaml > workset config.yaml > kanibako.yaml
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
    # system_paths: the global config wins when it supplies one (matches the
    # prior behavior where load_config(global_path) was the base); else keep
    # whatever the machine layer provided.
    if glob.system_paths:
        cfg.system_paths = glob.system_paths
    if workset_path and workset_path.exists():
        _overlay_scalars(cfg, workset_path)
    if project_path and project_path.exists():
        _overlay_scalars(cfg, project_path)
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
    # System-level config tier (settings-framework "system.*"), written at the
    # DEFAULT expressions as a flat [system] table (bare ``system.<leaf>``).
    # Kept in lock-step with paths.SYSTEM_PATH_DEFAULTS; the resolver fills
    # these in if the file omits them, so only the most commonly-tuned roots
    # are emitted (the channels skeleton + derived files resolve from these).
    data: dict = {
        "system": {
            "data": "$XDG_DATA_HOME/kanibako",
            "backup": "@system.data/backup",
            "agents": "@system.data/agents",
            "channels": "@system.data/channels",
            "global": "@system.data/global",
            "base_template": "@system.global/base_template",
            "settings": "@system.global/settings.yaml",
            "primary_workset": "@system.data/primary_workset",
            "registry": "@system.global/registry.yaml",
            "cache": "$XDG_CACHE_HOME/kanibako",
            "runtime": "$XDG_RUNTIME_DIR/kanibako",
        },
        "box": {
            "image": cfg.box_image,
            "agent": cfg.box_agent,
            "share_images": cfg.box_share_images,
        },
    }
    dump_doc(path, data)


def write_project_config(path: Path, image: str) -> None:
    """Write or update a settings.yaml with the given image."""
    write_project_config_key(path, "box_image", image)


def write_project_meta(
    path: Path,
    *,
    mode: str,
    workspace: str,
    shell: str,
    vault_ro: str,
    vault_rw: str,
    enable_vault: bool = True,
    group_auth: bool = True,
    metadata: str = "",
    project_hash: str = "",
    name: str = "",
) -> None:
    """Write resolved project metadata to settings.yaml, preserving other sections.

    Phase 5 removed the layout axis: ``mode`` (``box.mode``) is the sole
    on-disk shape descriptor now.  No ``layout`` field is written.

    The bool meta keys (``enable_vault``/``group_auth``) are routed through the
    shared :func:`coerce_bool` so they round-trip identically to the typed
    ``config set`` writer (H1/H2 fix); a non-bool literal falls back to raw.
    """
    existing = load_doc(path)

    ev = coerce_bool(enable_vault)
    ga = coerce_bool(group_auth)
    project_sec: dict = {
        "mode": mode,
        "enable_vault": ev if ev is not None else enable_vault,
        "group_auth": ga if ga is not None else group_auth,
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

    dump_doc(path, existing)


def read_project_meta(path: Path) -> dict | None:
    """Read stored project metadata from settings.yaml.

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

    # No back-compat token translation: 1.6.0 is a hard break (fresh trees
    # only).  The on-disk ``box.mode`` token is read verbatim — pre-1.6.0 dev
    # boxes (``default``/``workset``/``account_centric``/…) are unsupported.
    mode = project_sec["mode"]

    return {
        "mode": mode,
        "enable_vault": project_sec.get("enable_vault", True),
        "group_auth": project_sec.get("group_auth", True),
        "name": project_sec.get("name", ""),
        "workspace": resolved_sec.get("workspace", ""),
        "shell": resolved_sec.get("shell", ""),
        "vault_ro": resolved_sec.get("vault_ro", ""),
        "vault_rw": resolved_sec.get("vault_rw", ""),
        "metadata": resolved_sec.get("metadata", ""),
        "project_hash": resolved_sec.get("project_hash", ""),
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
    """Write or update a single key in a settings.yaml.

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
    """Remove a single key from a settings.yaml.

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
    """Load only the project-level overrides from a settings.yaml.

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


def read_default_agent(system_path: Path | None) -> str | None:
    """Read the ``system.default_agent`` SETTING from the system settings tier.

    ``system.default_agent`` is the lone ``system.*``-named key that lives in the
    SETTINGS file set (it is behavior, not a config path).  Transitionally its
    system tier physically lives in the user-global ``~/.config/kanibako.yaml``,
    in the reserved any-agent ``agent.default`` table (the same place the system
    settings tier of :func:`load_settings` reads from), under the key
    ``default_agent``.  Phase 5 re-points this to ``@system.settings``.

    Returns the configured agent name, or ``None`` when unset/empty (meaning
    "no system default" — callers fall through to today's auto-detect).
    """
    if system_path is None or not system_path.exists():
        return None
    settings = read_agent_settings(system_path, "default")
    value = settings.get("default_agent", "").strip()
    return value or None


def resolve_box_agent(box_agent: str | None, system_path: Path | None) -> str | None:
    """Resolve the effective agent name for a box launch.

    Resolution order (D-M2):

        1. explicit ``box.agent`` (per-box identity) — wins if set;
        2. ``system.default_agent`` (the user-global default SETTING);
        3. ``None`` — fall through to today's behavior (auto-detect, then the
           no-agent box).

    Returns the agent name to hand to :func:`~kanibako.targets.resolve_target`,
    or ``None`` to let it auto-detect.  When neither ``box.agent`` nor
    ``system.default_agent`` is set this returns ``None`` — exactly today's
    behavior (no regression).
    """
    explicit = (box_agent or "").strip()
    if explicit:
        return explicit
    return read_default_agent(system_path)


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
      ``agent.<name>``-keyed, loaded from ``agents/<name>/settings.yaml``)
      overlaid by
      *agent_path*'s ``[agent]`` table if supplied.
    * ``workset`` tier → *workset_path* = today's workset ``config.yaml``.
    * ``box`` tier → *box_path* = today's box/project ``settings.yaml`` ``[agent]``
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
# Scope categories (settings-framework {scope}.<category>.* — the unified
# masks/bindings/caches/seeded/shared/synced/env primitive)
# ---------------------------------------------------------------------------

def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten nested dict into DOTTED-key form, stringifying scalar leaves.

    ``{"system": {"bindings": {"rw": {"foo": "h:g"}}}}`` →
    ``{"system.bindings.rw.foo": "h:g"}``.
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dotted(v, key))
        else:
            out[key] = str(v)
    return out


def read_categories(path: Path | None) -> dict[str, str]:
    """Read scope-category keys (the unified primitive) from a config file as a
    flat dotted-key dict. Missing/None/unreadable path → {}.

    Recognizes all eight category shapes: ``{scope}.masks``,
    ``{scope}.bindings.{ro,rw}.{name}``, ``{scope}.caches.{name}``,
    ``{scope}.seeded.{name}``, ``{scope}.shared.{name}``,
    ``{scope}.synced.{name}``, ``{scope}.env.{VAR}``.
    """
    from kanibako.settings_categories import is_category_key

    if path is None:
        return {}
    try:
        if not path.exists():
            return {}
        data = load_doc(path)
    except Exception:
        return {}
    flat = _flatten_dotted(data)
    return {k: v for k, v in flat.items() if is_category_key(k)}


def read_shares(path: Path | None) -> dict[str, str]:
    """Read scoped-binding keys ({scope}.bindings.{ro,rw}.{name}) from a config
    file as a flat dotted-key dict. Missing/None/unreadable path → {}.

    Compatibility filter over :func:`read_categories` for the launch path's
    share-mount wrapper (:func:`kanibako.settings_shares.resolve_shares`).
    """
    from kanibako.settings_shares import is_share_key

    return {k: v for k, v in read_categories(path).items() if is_share_key(k)}


def read_seeds(path: Path | None) -> dict[str, str]:
    """Read seed keys ({scope}.seeded.{name}) from a config file as a flat
    dotted-key dict. Missing/None/unreadable path → {}.

    Compatibility filter over :func:`read_categories` for the launch path's
    init-seed wrapper (:func:`kanibako.settings_seeds.resolve_seeds`).
    """
    from kanibako.settings_seeds import is_seed_key

    return {k: v for k, v in read_categories(path).items() if is_seed_key(k)}


# ---------------------------------------------------------------------------
# Resource scope overrides (per-project)
# ---------------------------------------------------------------------------

def read_resource_overrides(path: Path) -> dict[str, str]:
    """Read ``resource_overrides`` from a settings.yaml.

    Returns a dict of resource_path → scope_string (e.g. ``"shared"``).
    Returns an empty dict when the file or section is absent.
    """
    if not path.exists():
        return {}
    data = load_doc(path)
    return {k: str(v) for k, v in data.get("resource_overrides", {}).items()}


def write_resource_override(path: Path, resource_path: str, scope: str) -> None:
    """Write a single resource scope override to ``resource_overrides`` in settings.yaml.

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
