"""Bootstrap config file: YAML load/write, the flat merged object, pre-cascade readers."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path

from kanibako.settings.config_io import dump_doc, load_doc


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Per-box construct-time metadata + box-tier settings cascade file (spec §2c meta.box.*)
BOX_META_FILE = "box.yaml"
# Workset-tier settings cascade file (spec §2c)
WORKSET_META_FILE = "workset.yaml"
# Agent-tier settings cascade file, INSIDE the per-agent store dir (spec §2d
# meta.agent.<agent>.settings).  ⚑ The SYSTEM tier is NOT here: it stays
# @config.settings = global/settings.yaml.
AGENT_META_FILE = "agent.yaml"

# Shared truth tables: the typed `config set` writer AND the box.meta writer.
_BOOL_TRUE = frozenset({"true", "1", "yes", "on"})
_BOOL_FALSE = frozenset({"false", "0", "no", "off"})


def coerce_bool(value: object) -> bool | None:
    """Coerce a config value to a real bool via the shared truth table (None if not a bool literal)."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
    return None


# ⚑ No default filename: the box-tier file is named for its tier (``box.yaml``), so
# there is no ONE global settings filename left to default to.  This key only ever
# holds a value when a user overrides it — hence its own carrier, kept out of
# ``_DEFAULTS`` so that dict stays ``dict[str, str]``.
_DEFAULT_PROJECT_TOML: str | None = None

_DEFAULTS: dict[str, str] = {
    "box_image": "ghcr.io/doctorjei/kanibako-oci:latest",
    "box_shell": "",
}


@dataclass
class KanibakoConfig:
    """The flat merged configuration object (defaults < config file < workset < box < CLI)."""

    paths_project_toml: str | None = _DEFAULT_PROJECT_TOML
    box_image: str = _DEFAULTS["box_image"]
    # ⚑ NO ``box_agent_name`` field (P7, spec §2b) — the selection is a KEY.
    box_shell: str = _DEFAULTS["box_shell"]
    box_share_images: bool = False
    # Bootstrap PATH set-values keyed by dotted name; config-file-only.
    config_paths: dict[str, str] = field(default_factory=dict)


def _flatten_toml(data: dict, prefix: str = "") -> dict[str, object]:
    """Flatten a nested config dict into underscore-joined keys (``None`` = the reset sentinel)."""
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
    """The bootstrap config file ``$XDG_CONFIG_HOME/kanibako_config.yaml`` (JC-1 clean break)."""
    return config_home / "kanibako_config.yaml"


def config_base_path() -> Path:
    """The machine-wide CONFIG base file — the bootstrap-PATH set's least-specific layer."""
    return Path("/etc/kanibako/config_base.yaml")


def settings_base_path() -> Path:
    """The machine-wide SETTINGS base file — the behavior cascade's bottom layer, below every scope."""
    return Path("/etc/kanibako/settings_base.yaml")


def _present_scalar_fields(path: Path) -> dict[str, object]:
    """The scalar/bool fields actually PRESENT in a config file (``None`` = the reset sentinel)."""
    if not path.exists():
        return {}
    data = load_doc(path)
    # Pop the bootstrap-PATH tier so it can't leak into the scalar overlay.
    data.pop("config", None)
    data.pop("system", None)
    flat = _flatten_toml(data)
    valid_keys = {fld.name for fld in fields(KanibakoConfig)}
    present: dict[str, object] = {}
    for k, v in flat.items():
        if k in valid_keys:
            present[k] = v
    return present


def load_config(path: Path) -> KanibakoConfig:
    """Read a single config file and return a KanibakoConfig with defaults filled in."""
    cfg = KanibakoConfig()
    if path.exists():
        data = load_doc(path)
        # ⚑ The bootstrap-PATH extraction is UNFILTERED — an unknown leaf lands
        # in ``config_paths`` too, and reaches no consumer (see the llm-doc).
        merged: dict[str, str] = {}
        config_tbl = data.get("config", {})
        if isinstance(config_tbl, dict):
            merged.update(_flatten_dotted(config_tbl, "config"))
        system_tbl = data.get("system", {})
        if isinstance(system_tbl, dict):
            merged.update(_flatten_dotted(system_tbl, "system"))
        cfg.config_paths = merged
        # A present key sets the field; a present ``None`` resets to the default.
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(KanibakoConfig(), k))
            else:
                setattr(cfg, k, v)
    return cfg


#: The three box-scope SCALAR keys resolved through the KEYSPACE (B6, R-11a(a)):
#: dotted key → the flat ``KanibakoConfig`` field it lands on.
_BOX_SCALAR_FIELDS: dict[str, str] = {
    "box.image": "box_image",
    "box.share_images": "box_share_images",
    "box.shell": "box_shell",
}


def _resolve_box_scalars(
    global_path: Path,
    floor_values: "dict[str, object]",
    *,
    workset_path: Path | None,
    box_path: Path | None,
    cli_overrides: "dict[str, object] | None",
) -> dict[str, object]:
    """Resolve the three box scalars through the KEYSPACE — the ONE resolve behind ``load_merged_config``.

    ⚑ Lazy imports throughout: ``paths`` and ``settings_assemble`` both import
    this module at module load, so hoisting any of these closes the cycle.
    """
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.paths import load_system_config, host_xdg_map, xdg
    from kanibako.settings.settings_cli_level import build_cli_level
    from kanibako.settings.settings_launch import build_launch_snapshot
    from kanibako.settings.settings_resolve import ResolveCtx

    # Path resolution only, deliberately NOT load_std_paths (which materializes
    # the store). ⚑ Not mkdir-free: an unset XDG_RUNTIME_DIR makes one dir here.
    system_path = load_system_config(
        global_path, data_home=xdg("XDG_DATA_HOME", ".local/share"),
        home=Path.home(),
    )["config.settings"]

    # The kanibako_config [box] tier as the FLOOR (risk 1); ``""`` drops out.
    floor = dict(floor_values)

    overrides = cli_overrides or {}
    image_val = overrides.get("box_image")
    cli_level = build_cli_level(
        image=str(image_val) if image_val else None,
        share_images=bool(overrides.get("box_share_images", False)),
    )

    snapshot = build_launch_snapshot(
        agent_name="general",
        ctx=ResolveCtx(
            agent_name="general", workset_name=None,
            host_home=str(Path.home()), xdg=host_xdg_map(),
        ),
        system_path=system_path if system_path.exists() else None,
        agent_path=None,
        workset_path=workset_path,
        box_path=box_path,
        default_categories=floor,
        cli_level=cli_level,
        # ⚑ NO PERSONA TIER, deliberately — this resolve is AGENT-LESS.
    )

    resolved: dict[str, object] = {}
    for dotted in _BOX_SCALAR_FIELDS:
        node: object = snapshot
        for seg in dotted.split("."):
            if not isinstance(node, KeyStore):
                node = None
                break
            node = dict.get(node, seg)
        if node is not None:
            resolved[dotted] = node
    return resolved


def load_merged_config(
    global_path: Path,
    project_path: Path | None = None,
    *,
    workset_path: Path | None = None,
    cli_overrides: "dict[str, object] | None" = None,
) -> KanibakoConfig:
    """Load global config, overlay workset then project then CLI, then run the B6 box-scalar resolve."""
    defaults = KanibakoConfig()

    def _overlay_scalars(cfg: KanibakoConfig, path: Path) -> None:
        """Overlay one file layer's PRESENT scalar/bool fields onto *cfg*."""
        for k, v in _present_scalar_fields(path).items():
            if v is None:
                setattr(cfg, k, getattr(defaults, k))
            else:
                setattr(cfg, k, v)

    # The user global config is the least-specific FILE source.
    cfg = load_config(global_path)
    # ⚑ The FLOOR is captured BEFORE the overlays, so a workset/box value cannot
    # masquerade as the system-stored default.
    floor_values: dict[str, object] = {
        dotted: getattr(cfg, field_name)
        for dotted, field_name in _BOX_SCALAR_FIELDS.items()
    }
    if workset_path and workset_path.exists():
        _overlay_scalars(cfg, workset_path)
    if project_path and project_path.exists():
        _overlay_scalars(cfg, project_path)
    if cli_overrides:
        valid_keys = {fld.name for fld in fields(cfg)}
        for k, v in cli_overrides.items():
            if k in valid_keys:
                setattr(cfg, k, v)

    # KEYSPACE resolve (B6): a resolved value wins; ABSENT keeps the flat value.
    resolved = _resolve_box_scalars(
        global_path, floor_values,
        workset_path=workset_path, box_path=project_path,
        cli_overrides=cli_overrides,
    )
    for dotted, field_name in _BOX_SCALAR_FIELDS.items():
        if dotted not in resolved:
            continue
        value = resolved[dotted]
        if field_name == "box_share_images":
            coerced = coerce_bool(value)
            setattr(cfg, field_name, coerced if coerced is not None else bool(value))
        else:
            setattr(cfg, field_name, str(value))
    return cfg


def write_global_config(path: Path, cfg: KanibakoConfig | None = None) -> None:
    """Write a YAML config file with the structured layout; ``None`` writes defaults."""
    if cfg is None:
        cfg = KanibakoConfig()
    # Bootstrap PATH tier at the DEFAULT expressions, in the ``[config]``
    # (Layer-1, spec §1) and ``[system]`` (Layer-2, spec §2g) tables.
    # ⚑ These literals DUPLICATE paths.CONFIG_PATH_DEFAULTS /
    # SYSTEM_PATH_DEFAULTS — every edit here needs the matching edit there.
    data: dict = {
        "config": {
            "data": "$XDG_DATA_HOME/kanibako",
            "settings": "@config.data/global/settings.yaml",
            "agents": "@config.data/agents",
            "primary_workset": "@config.data/primary_workset",
            "registry": "@config.data/global/registry.yaml",
            "journal": "@config.data/global/journal.yaml",
        },
        "system": {
            "backup": "@config.data/backup",
            "channelroot": "@config.data/channels",
            # M-11: ``base_template`` → ``template``, plus the ``canon`` root.
            "template": "@config.data/global/template",
            "canon": "@config.data/global/canon",
            "cache": "$XDG_CACHE_HOME/kanibako",
            "runtime": "$XDG_RUNTIME_DIR/kanibako",
        },
        # ⚑ NO ``agent_name`` row (P7): ``box.agent_name`` is RETIRED (§2b).
        "box": {
            "image": cfg.box_image,
            "share_images": cfg.box_share_images,
        },
    }
    dump_doc(path, data)


def write_project_config(path: Path, image: str) -> None:
    """Write or update a box.yaml with the given image."""
    write_project_config_key(path, "box_image", image)


def persist_creation_flags(
    box_settings_path: Path,
    *,
    materializing: bool,
    image: str | None = None,
    share_images: bool | None = None,
) -> None:
    """The §1A **CREATE EXCEPTION** — the ONE gate through which a shadowing CLI flag ever PERSISTS."""
    if not materializing:
        return
    updates: dict[str, object] = {}
    if image:
        updates["image"] = image
    if share_images is not None:
        updates["share_images"] = bool(share_images)
    if not updates:
        return
    data = load_doc(box_settings_path)
    sec = data.get("box")
    if not isinstance(sec, dict):
        sec = {}
        data["box"] = sec
    sec.update(updates)
    dump_doc(box_settings_path, data)


def write_box_enable_vault(path: Path, enable_vault: bool = True) -> None:
    """Sparsely persist the box-scope ``box.enable_vault`` key at *path* (reader: :func:`read_box_enable_vault`)."""
    existing = load_doc(path)
    ev = coerce_bool(enable_vault)
    if ev is False:
        existing.setdefault("box", {})["enable_vault"] = False
        dump_doc(path, existing)
        return
    # Default (True): rewrite ONLY to drop a stale override; no empty file.
    box_sec = existing.get("box")
    if isinstance(box_sec, dict) and "enable_vault" in box_sec:
        box_sec.pop("enable_vault", None)
        dump_doc(path, existing)


def read_box_enable_vault(path: Path, *, default_from: Path | None = None) -> bool:
    """The box-scope ``box.enable_vault`` value stored at *path*, defaulting to ``True``.

    ⚑ *default_from* is the WORKSET tier, and it is the R2 downward-default
    (spec §0 "Directional view/set across CONTAINMENT levels"): the STANDALONE
    and NAMED resolvers both pass it.  It is load-bearing in each for a
    DIFFERENT reason — standalone's is the pre-P2 read-compat path, named's is
    ``workset create --no-vault``.  PRIMARY still passes nothing (see the
    resolver).
    """
    for candidate in (path, default_from):
        if candidate is None or not candidate.exists():
            continue
        box_tbl = load_doc(candidate).get("box") or {}
        if "enable_vault" in box_tbl:
            return box_tbl["enable_vault"]
    return True


def carried_box_settings(box_tier: Path, workset_tier: Path | None) -> dict:
    """The box-scope settings doc a LIFECYCLE op carries to a new box's box tier.

    ⚑ The legacy underlay below is what keeps a pre-P2 standalone box from
    losing ``box.image`` on convert/move/duplicate — do not drop it.
    """
    doc = dict(load_doc(box_tier))
    legacy = (load_doc(workset_tier).get("box") or {}) if workset_tier else {}
    if isinstance(legacy, dict) and legacy:
        box_sec = dict(legacy)
        box_sec.update(doc.get("box") or {})   # box tier wins (R2)
        doc["box"] = box_sec
    doc.pop("workset", None)
    return doc


def read_workset_kuid(path: Path) -> str:
    """The stored ``workset.kuid`` at *path*, defaulting to :data:`kanibako.kuid.SENTINEL`."""
    from kanibako import kuid

    if not path.exists():
        return kuid.SENTINEL
    data = load_doc(path)
    value = (data.get("workset") or {}).get("kuid", kuid.SENTINEL)
    return str(value)


def read_workset_skip_kuid_check(path: Path) -> bool:
    """The stored ``workset.skip_kuid_check`` bool at *path*, defaulting to ``True`` (checking OFF)."""
    if not path.exists():
        return True
    data = load_doc(path)
    return bool((data.get("workset") or {}).get("skip_kuid_check", True))


def _split_config_key(flat_key: str) -> tuple[str, str]:
    """Split a flat config key into ``(section, key)``; no recognised prefix → an EMPTY section."""
    for prefix in ("paths_", "box_"):
        if flat_key.startswith(prefix):
            section = prefix.rstrip("_")
            key = flat_key[len(prefix):]
            return section, key
    return "", flat_key


def write_project_config_key(path: Path, flat_key: str, value: str) -> None:
    """Write or update a single key in a box.yaml (*flat_key* is underscore-joined)."""
    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    if not section:
        # Top-level scalar field (no recognised section prefix).
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
    """Remove a single key from a box.yaml; True iff it was found and removed."""
    if not path.exists():
        return False

    section, key = _split_config_key(flat_key)
    data = load_doc(path)
    if not section:
        # Top-level scalar field (no recognised section prefix).
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
    """The project-level overrides in a box.yaml — flat_key → value for keys differing from defaults."""
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
# Agent settings, agent selection, and the setup-version gate
# ---------------------------------------------------------------------------

def read_agent_settings(path: Path, agent_name: str) -> dict[str, str]:
    """Agent-state overrides from a config file's ``agent`` table: ``agent.default`` under ``agent.<name>``.

    ⚑ A legacy FLAT ``[agent]`` table is treated as UNSET — only nested
    per-agent dicts are honored, and that is deliberate (no pass-1 migration).
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


def read_system_agent(system_path: Path | None) -> str | None:
    """The stored ``system.agent`` SETTING from the system settings tier; ``None`` when unset.

    ⚑ *system_path* is the SETTINGS file (``@config.settings``), NOT
    ``kanibako_config.yaml``. ⚑ PRE-CASCADE reader — the LAUNCH does not use it.
    """
    if system_path is None or not system_path.exists():
        return None
    data = load_doc(system_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("agent") or "").strip()
    return value or None


def read_setup_completed(config_path: Path | None) -> str | None:
    """The ``system.setup_completed`` marker from the CONFIG file; ``None`` means "setup never run".

    ⚑ A RAW reader is required: ``load_config`` DOES capture the leaf into
    ``config_paths``, but nothing iterates that set, so it reaches no consumer.
    """
    if config_path is None or not config_path.exists():
        return None
    data = load_doc(config_path)
    system = data.get("system")
    if not isinstance(system, dict):
        return None
    value = str(system.get("setup_completed", "")).strip()
    return value or None


# ⚑ ``read_templates_stamp`` + ``template_staleness_gate`` lived here and are
# RETIRED (R-38, M-23); the protection folds into ``setup_compat_gate`` below.


def setup_compat_gate(config_path: Path | None) -> str | None:
    """Run the 5-band setup/config compatibility gate; a returned string is a NON-BLOCKING advisory.

    ⚑ Every comparison is by BASE version, so a dev/rc build of the same base
    as the released marker reads as ``==``, not "from the future".
    """
    from kanibako import SETUP_BCV, SETUP_FCV, __version__
    from kanibako.errors import ConfigError

    marker = read_setup_completed(config_path)
    if marker is None:
        return "kanibako isn't set up yet. Run 'kanibako setup' to get started."

    from packaging.version import InvalidVersion, Version

    try:
        config_ver = Version(Version(marker).base_version)
    except InvalidVersion:
        # Hand-edited marker: don't nag and don't block.
        return None

    current_ver = Version(Version(__version__).base_version)
    bcv = Version(Version(SETUP_BCV).base_version)
    fcv = Version(Version(SETUP_FCV).base_version)

    if config_ver > current_ver:
        raise ConfigError(
            "This kanibako config was written by a newer kanibako "
            f"({marker}) than the one running ({__version__}). "
            "Upgrade kanibako, or re-run 'kanibako setup' to rebuild it."
        )
    if config_ver == current_ver:
        return None
    if config_ver >= fcv:
        # Forward-compatible: advance the marker so later runs hit the ``==``
        # no-op. ⚑ The bump must NEVER block, so a failed write is swallowed.
        try:
            from kanibako.settings.config_interface import write_system_value

            if config_path is not None:
                write_system_value(config_path, "setup_completed", __version__)
        except Exception:  # pragma: no cover - defensive; bump is best-effort
            pass
        return None
    if config_ver >= bcv:
        return "kanibako setup is out of date — re-run 'kanibako setup'."
    raise ConfigError(
        f"This kanibako config ({marker}) is too old to auto-update. "
        "Re-run 'kanibako setup' before agent commands."
    )


# Pseudo-agents are DISCOUNTED from the implicit installed-count rule; ``no_agent``
# stays explicitly selectable. ⚑ ``general`` is a SLOT name, not a shipped target.
_PSEUDO_AGENTS = frozenset({"no_agent", "general"})


def resolve_agent(
    *,
    explicit_agent: str | None,
    requested: str | None = None,
    project_path: Path | None = None,
) -> str:
    """Validate/arbitrate the effective agent name against the installed set, plus the count rule.

    ⮕ **P7: the CASCADE moved out** — what stays here is what is NOT a key.
    """
    # ⚑ Lazy: kanibako.targets imports paths/config indirectly (cycle risk).
    from kanibako.agent_ref import canonicalize_agent_ref, harness_of
    from kanibako.errors import (
        AgentNotInstalledError,
        NoAgentInstalledError,
        NoAgentSelectedError,
    )
    from kanibako.install_method import install_command
    from kanibako.targets import discover_targets

    def _clean(value: str | None) -> str:
        return (value or "").strip()

    installed = set(discover_targets(project_path).keys())
    # The count rule considers only REAL launchable agents; an explicitly-named
    # harness still validates against the FULL `installed` set below.
    real_installed = installed - _PSEUDO_AGENTS

    # First non-empty tier resolves a name.
    raw_resolved = _clean(explicit_agent) or _clean(requested)

    if raw_resolved:
        # ⚑ Canonicalise + validate the ref shape; the HARNESS is what must be
        # installed — NOT the composite node-name (a persona segment is free-form).
        node = canonicalize_agent_ref(raw_resolved)
        harness = harness_of(node)
        if harness in installed:
            return node
        raise AgentNotInstalledError(
            f"Agent '{harness}' is not installed. Install it with:\n"
            f"  {install_command(f'kanibako-agent-{harness}')}\n"
            f"Or run 'kanibako agent list' to see installed agents."
        )

    # Nothing resolved -> installed-count rule (REAL agents only).
    if len(real_installed) == 1:
        return next(iter(real_installed))
    if len(real_installed) == 0:
        raise NoAgentInstalledError(
            "No agent plugins are installed. Install one, e.g.:\n"
            f"  {install_command('kanibako-agent-claude')}\n"
            "Access via shell: kanibako shell"
        )
    raise NoAgentSelectedError(
        "No agent selected; run 'kanibako setup' to select one or "
        "'kanibako shell' to access the container via command shell."
    )


def write_agent_setting(path: Path, key: str, value: str, agent_name: str) -> None:
    """Write a single agent-state override under ``agent.<agent_name>``, preserving every other section."""
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


def _flatten_dotted(data: dict, prefix: str = "") -> dict[str, str]:
    """Flatten a nested dict into DOTTED-key form, stringifying scalar leaves.

    ⚑ NOT a scope-category helper — its only callers are ``load_config``'s
    bootstrap ``[config]`` / ``[system]`` extraction.
    """
    out: dict[str, str] = {}
    for k, v in data.items():
        key = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out.update(_flatten_dotted(v, key))
        else:
            out[key] = str(v)
    return out
