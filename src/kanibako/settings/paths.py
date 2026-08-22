"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

from kanibako.settings.paths_defaults import (XDG_SPEC_DEFAULTS, CONFIG_PATH_DEFAULTS,
                                              SYSTEM_PATH_DEFAULTS, XDG_DATA_HOME, XDG_CONFIG_HOME,
                                              XDG_RUNTIME_DIR, XDG_STATE_HOME, XDG_CACHE_HOME,

                                              SHELL_D_FILE, PROFILE_FILE, BASHRC_FILE, IGNORE_FILE,
                                              PROFILE_CONTENTS, BASHRC_CONTENTS,
                                              SHELL_D_CONTENTS, RUN_USER_UID_PATH,

                                              BOXES_PATH, HOME_PATH, KANIBAKO_PATH, LOGS_PATH,
                                              RO_PATH, RW_PATH, VAULT_PATH, STANDALONE_META_DIR,

                                              STATUS_OK, STATUS_MISSING, STATUS_NO_DATA,
                                              MSG_OTS_KB_INIT, MSG_OTS_WS_PROJ_INIT, MSG_DONE,

                                              WARN_RELATIVE_XDG, WARN_FALLBACK_RT_DIR,
                                              WARN_RUNDIR_UNUSABLE, WARN_WS_NO_ROOT,
                                              WARN_WS_BAD_LOAD, WARN_WS_BOX_BAD_NAME,
                                              WARN_BOX_BAD_KUID, WARN_BOX_NO_VAULT,

                                              ERR_SETTINGS_BAD_PATH, ERR_SETTINGS_BAD_REF,
                                              ERR_CONFIG_NO_FILE, ERR_PROJECT_NO_PATH,
                                              ERR_PROJECT_NEW_HOME, ERR_PROJECT_REG_HOME,
                                              ERR_PROJECT_NAME_USED, ERR_PROJECT_DIR_IS_WS,
                                              ERR_WORKSET_NO_PROJECT, ERR_WORKSET_NO_WORKSET,
                                              ERR_WORKSET_WS_NOT_BOX, ERR_WORKSET_NOT_IN_BOX,

                                              UNREGISTERED_MARKER, KIND_PROJECT, KIND_WORKSET)

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol, overload

from kanibako.log import get_logger

from kanibako.settings.config import (WORKSET_META_FILE, BOX_META_FILE, KanibakoConfig, config_file_path,
                                      load_config, read_box_enable_vault, read_workset_kuid,
                                      read_workset_skip_kuid_check, write_box_enable_vault)

from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings.settings_resolve import (LevelView, ResolveCtx, SettingsError,
                                                _Unset, expand_expr, resolve_value)

from kanibako.project.names import (resolve_name, resolve_qualified_name)
from kanibako.utils import project_hash, short_hash


class BoxMode(Enum):
    """How a box's persistent state is organized on disk (the ``box.mode`` token)."""
    primary = "primary"
    named = "named"
    standalone = "standalone"


class DetectionResult(NamedTuple):
    """Result of box mode detection: the *mode* + the ancestor *project_root* it was found at."""
    mode: BoxMode
    project_root: Path


@dataclass
class StandardPaths:
    """Resolved XDG and kanibako standard directory paths."""
    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path
    config_file: Path
    data_path: Path
    state_path: Path
    cache_path: Path
    # System-level derived dirs: the Layer-1 ``config.*`` foundation + Layer-2 ``system.*``.
    data: Path
    backup: Path
    agents: Path
    channels: Path
    # ``system.template`` — the system TEMPLATE ROOT.  ⚑ The box-HOME seed is
    # ``template/box/home``, NOT the root and NOT ``box/``.
    template: Path
    # ``system.canon`` — this SCOPE'S CANON CONTRIBUTION root (spec §2g), not the assembly.
    canon: Path
    settings: Path
    primary_workset: Path
    registry: Path
    # Lifecycle journal — write-ahead log of in-flight box-lifecycle ops (``config.journal``).
    journal: Path
    cache: Path
    runtime: Path
    # Channels skeleton — keys/defaults only; sub-key wiring is Phase 6.
    channels_common: Path
    channels_chat: Path
    channels_broadcast: Path
    channels_mailboxes: Path
    channels_share: Path
    # PRIMARY-workset box store: ``@config.primary_workset/boxes`` (per-box meta + shell).
    boxes: Path
    # PRIMARY-workset vault + logs roots under ``@config.primary_workset``.
    primary_vault_ro: Path
    primary_vault_rw: Path
    primary_logs: Path


@dataclass(frozen=True)
class ProjectGroup:
    """A project's grouping (PRIMARY or named workset) as DATA rather than control flow."""
    name: str
    root: Path
    is_default: bool
    local_shared_base: Path


class _WorksetRooted(Protocol):
    """Structural type for "anything rooted at ``@meta.workset.path``"."""
    @property
    def root(self) -> Path: ...


@overload
def workset_settings_path(group: _WorksetRooted) -> Path: ...
@overload
def workset_settings_path(group: None) -> None: ...


def workset_settings_path(group: _WorksetRooted | None) -> Path | None:
    """THE workset-tier settings-file derivation: ``@meta.workset.path/workset.yaml`` (spec §2c)."""
    return group.root / WORKSET_META_FILE if group is not None else None


def _default_project_group(std: StandardPaths) -> ProjectGroup:
    """The PRIMARY (default) workset's :class:`ProjectGroup`, rooted at ``@config.primary_workset``."""
    warn_legacy_primary_settings(std)
    return ProjectGroup(name="default", root=std.primary_workset,
                        is_default=True, local_shared_base=std.data_path)


_legacy_primary_settings_warned = False

def warn_legacy_primary_settings(std: StandardPaths) -> None:
    """One-shot warning for a leftover legacy ``<data>/settings.yaml`` (never read, never touched)."""
    global _legacy_primary_settings_warned
    if _legacy_primary_settings_warned:
        return
    legacy = std.data_path / "settings.yaml"
    spec_file = std.primary_workset / WORKSET_META_FILE
    if legacy.is_file() and not spec_file.is_file():
        import sys

        _legacy_primary_settings_warned = True
        print(f"warning: {legacy} is no longer read — 1.7.0 moved primary workset's settings " +
              f"to {spec_file}. Move wanted values there or re-set them via 'kanibako workset " +
              "set default <key>=<value>'.", file=sys.stderr)


@dataclass
class ProjectPaths:
    """Resolved paths for a specific project."""
    project_path: Path
    project_hash: str
    metadata_path: Path      # host-only: workset.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    vault_ro_path: Path      # {project}/vault/ro (→ /home/agent/vault/ro)
    vault_rw_path: Path      # {project}/vault/rw (→ /home/agent/vault/rw)
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    enable_vault: bool = field(default=True)
    name: str = field(default="")
    group: ProjectGroup | None = field(default=None)


def box_tree_materialized(proj: ProjectPaths) -> bool:
    """True when the box tree a ``create`` would materialize is ALREADY on disk."""
    return box_metadata_dir(proj.mode, proj.metadata_path).is_dir()


def _standalone_settings_files(root: Path) -> tuple[Path, Path]:
    """The STANDALONE ``(box_tier, workset_tier)`` pair — BOTH always real paths."""
    return root / STANDALONE_META_DIR / BOX_META_FILE, root / WORKSET_META_FILE


def box_metadata_dir(mode: BoxMode, metadata_path: Path) -> Path:
    """The DIR holding a box's own metadata — home, session state, box tier."""
    return metadata_path / STANDALONE_META_DIR if mode is BoxMode.standalone else metadata_path


def _box_settings_files(mode: BoxMode, metadata_path: Path,
                        group: "ProjectGroup | None") -> tuple[Path, Path | None]:
    """THE ``(box_tier, workset_tier)`` settings-file derivation (spec §2c) — spelled ONCE.
    ⚑ The box tier is non-optional BY TYPE; do not widen the return to ``Path | None``."""
    if mode is BoxMode.standalone:
        return _standalone_settings_files(metadata_path)
    return metadata_path / BOX_META_FILE, workset_settings_path(group)


def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]:
    """The :class:`ProjectPaths` ADAPTER over :func:`_box_settings_files` (no logic of its own)."""
    return _box_settings_files(proj.mode, proj.metadata_path, proj.group)


class _WorksetLike(Protocol):
    """Structural type for the attributes :meth:`WorksetSpec.from_workset` reads (cycle-breaker)."""
    name: str
    root: Path
    is_default: bool

    @property
    def projects_dir(self) -> Path: ...
    @property
    def workspaces_dir(self) -> Path: ...
    @property
    def vault_dir(self) -> Path: ...
    @property
    def logs_dir(self) -> Path: ...
    @property
    def projects(self) -> Sequence[_WorksetProjectLike]: ...


#: One :func:`iter_workset_projects` row: ``(workset_name, workset, [(project, status), ...])``.
#: ⚑ Named because the bare type is 127 characters — too long for any signature wrap that keeps it
#: whole, so spelling it inline forced a break INSIDE the generic in one place and left it unbroken
#: in another. Two spellings of one concept is the thing that gets copied wrong (convention 0).
_WorksetProjectRows = list[tuple[str, _WorksetLike, list[tuple[str, str]]]]


class _WorksetProjectLike(Protocol):
    """Structural type for the workset project attributes read here."""
    @property
    def name(self) -> str: ...
    @property
    def source_path(self) -> Path: ...


@dataclass(frozen=True)
class WorksetSpec:
    """Primitive view of a workset, decoupled from :class:`kanibako.project.workset.Workset`."""
    name: str
    root: Path
    projects_dir: Path
    workspaces_dir: Path
    vault_dir: Path
    project_names: tuple[str, ...]
    is_default: bool = False

    @classmethod
    def from_workset(cls, ws: _WorksetLike) -> WorksetSpec:
        """Build a :class:`WorksetSpec` from a ``Workset``-like object."""
        return cls(name=ws.name, root=ws.root, projects_dir=ws.projects_dir,
                   workspaces_dir=ws.workspaces_dir, vault_dir=ws.vault_dir,
                   project_names=tuple(p.name for p in ws.projects), is_default=ws.is_default)


logger = get_logger("paths")

def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path:
    """Resolve an XDG base dir per the freedesktop spec — env honored iff set AND absolute."""
    val = os.environ.get(var_name, "")
    if val:
        if os.path.isabs(val):
            return Path(val).resolve()

        # Relative value: invalid per spec → ignore and fall through to default.
        logger.warning(WARN_RELATIVE_XDG, var_name, val)

    if spec_default_suffix is not None:
        return Path.home() / spec_default_suffix

    # XDG_RUNTIME_DIR has no spec default — pick a replacement and warn.
    return _fallback_runtime_dir(var_name)


# Process-lifetime cache of the chosen runtime-dir fallback, keyed by (var_name, env value).
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}

def _fallback_runtime_dir(var_name: str) -> Path:
    """Choose a replacement for an unset/invalid ``XDG_RUNTIME_DIR`` and warn (never silent)."""
    cache_key = (var_name, os.environ.get(var_name, ""))
    cached = _runtime_fallback_cache.get(cache_key)
    if cached is not None and cached.is_dir():
        return cached

    uid = os.getuid()
    run_user = Path(RUN_USER_UID_PATH % uid)
    if _runtime_base_usable(run_user):
        chosen = run_user / KANIBAKO_PATH
        chosen.mkdir(mode=0o700, parents=True, exist_ok=True)
        logger.warning(WARN_FALLBACK_RT_DIR, var_name, chosen, var_name)
        _runtime_fallback_cache[cache_key] = chosen
        return chosen

    # Last resort: a 0700 temp dir under the system temp root.
    chosen = Path(tempfile.mkdtemp(prefix="kanibako-runtime-"))
    chosen.chmod(0o700)
    logger.warning(WARN_RUNDIR_UNUSABLE, var_name, uid, chosen, var_name)
    _runtime_fallback_cache[cache_key] = chosen
    return chosen


def _runtime_base_usable(base: Path) -> bool:
    """True iff *base* is a directory we own and can write to (any OS error ⇒ not usable)."""
    try:
        st = base.stat()
    except OSError:
        return False
    import stat as _stat

    if not _stat.S_ISDIR(st.st_mode):
        return False
    if st.st_uid != os.getuid():
        return False
    return os.access(base, os.W_OK | os.X_OK)


def xdg(env_var: str, default_suffix: str) -> Path:
    """Backward-compatible thin wrapper over :func:`resolve_xdg` for plain XDG base dirs."""
    return resolve_xdg(env_var, default_suffix)


def _spec_default_xdg_map(data_home: Path | None) -> dict[str, str]:
    """The XDG vars that HAVE a spec default (data/config/state/cache) — no ``XDG_RUNTIME_DIR``.

    ⚑ Side-effect-free: unlike ``XDG_RUNTIME_DIR`` (see :func:`_fallback_runtime_dir`), none of
    these four ever mkdir a fallback dir — :func:`resolve_data_leaf` relies on that to stay total.
    """
    xdg_map: dict[str, str] = {}
    for name, suffix in XDG_SPEC_DEFAULTS.items():
        if name == XDG_DATA_HOME and data_home is not None:
            # Already resolved by the caller — don't re-read the env (it would re-warn).
            xdg_map[name] = str(data_home)
        else:
            xdg_map[name] = str(resolve_xdg(name, suffix))
    return xdg_map


def host_xdg_map(data_home: Path | None = None) -> dict[str, str]:
    """THE single builder for the ``xdg=`` argument of every host-side ``ResolveCtx``."""
    xdg_map = _spec_default_xdg_map(data_home)
    xdg_map[XDG_RUNTIME_DIR] = str(resolve_xdg(XDG_RUNTIME_DIR, None))
    return xdg_map


def resolve_config_paths(set_values: Mapping[str, str], *, data_home: Path, home: Path,
                         xdg_vars: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve the Layer-1 CONFIG-key foundation to concrete host paths (flat by design).

    ⚑ *xdg_vars*, when given, REPLACES the live :func:`host_xdg_map` build — the seam
    :func:`resolve_data_leaf` uses to resolve ``config.data`` without touching
    ``XDG_RUNTIME_DIR`` (whose fallback can mkdir; see :func:`_spec_default_xdg_map`).
    Every other caller leaves it unset and gets today's exact ``host_xdg_map(data_home)``.
    """
    xdg_vars = dict(xdg_vars) if xdg_vars is not None else host_xdg_map(data_home)
    ctx = ResolveCtx(agent_name=None, workset_name=None, host_home=str(home), xdg=xdg_vars)
    levels = [LevelView("config", values=dict(set_values), defaults=CONFIG_PATH_DEFAULTS)]

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(ERR_SETTINGS_BAD_REF % ("", ref))
        return expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain)

    resolved: dict[str, str] = {}
    for key in CONFIG_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(ERR_SETTINGS_BAD_PATH % ("config", key))
        resolved[key] = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
    return resolved


def resolve_system_paths(set_values: Mapping[str, str],
                         *, data_home: Path, home: Path) -> dict[str, Path]:
    """Resolve the path tier (Layer-1 ``config.*`` + Layer-2 ``system.*``) to concrete host paths."""
    xdg_vars = host_xdg_map(data_home)

    # Split the merged set-values by layer prefix.
    config_set = {k: v for k, v in set_values.items() if k.startswith("config.")}
    set_values = {k: v for k, v in set_values.items() if k.startswith("system.")}

    # Layer 1: resolve the config-key foundation first (chicken-and-egg).
    config = resolve_config_paths(config_set, data_home=data_home, home=home)

    ctx = ResolveCtx(agent_name=None, workset_name=None,
                     host_home=str(home), xdg=xdg_vars, config=config)
    levels = [LevelView("system", values=dict(set_values), defaults=SYSTEM_PATH_DEFAULTS)]

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        # Resolver SPLIT (spec §1A / JC-2), prefix-driven: ``@config.*`` vs ``@system.*``.
        if ref.startswith("config."):
            try:
                return config[ref]
            except KeyError:
                raise SettingsError(ERR_SETTINGS_BAD_REF % ("config", ref)) from None
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(ERR_SETTINGS_BAD_REF % ("", ref))
        # system.* config paths are always scalar strings; narrow the ``object``-typed value.
        return expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain)

    resolved: dict[str, Path] = {}
    # Layer 1 foundation paths are surfaced under their ``config.*`` keys.
    for key, val in config.items():
        resolved[key] = Path(val)
    # Layer 2 system path keys, resolving ``@config.*`` via the foundation.
    for key in SYSTEM_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(ERR_SETTINGS_BAD_PATH % ("system", key))
        expanded = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
        resolved[key] = Path(expanded)

    # PRIMARY-workset box/vault/logs roots, derived from ``@config.primary_workset``.
    pw = resolved["config.primary_workset"]
    resolved["system._boxes"] = pw / BOXES_PATH
    resolved["system._primary_vault_ro"] = pw / VAULT_PATH / RO_PATH
    resolved["system._primary_vault_rw"] = pw / VAULT_PATH / RW_PATH
    resolved["system._primary_logs"] = pw / LOGS_PATH
    return resolved


def load_system_config(user_config_path: Path, *, data_home: Path, home: Path) -> dict[str, Path]:
    """Resolve the path tier from the CONFIG file set: ``/etc`` base < user global."""
    # ⚑ Lazy import to avoid a config <-> paths import cycle at module load — do not hoist.
    from kanibako.settings.config import (config_base_path, load_config)
    raw: dict[str, str] = {}

    # base < user; an absent file yields {}, so missing layers are skipped automatically.
    raw.update(load_config(config_base_path()).config_paths)
    raw.update(load_config(user_config_path).config_paths)

    return resolve_system_paths(raw, data_home=data_home, home=home)


def resolve_data_leaf(data_path: Path | None = None, *, config_home: Path | None = None,
                      data_home: Path | None = None) -> str:
    """The leaf (basename) of ``config.data`` — PURE and TOTAL; creates nothing, never raises.

    Given an ALREADY-RESOLVED *data_path* (e.g. a caller's own
    ``load_system_config(...)["config.data"]``), this is just ``data_path.name`` — no re-read.
    Without one, resolves ``config.data`` fresh from the host CONFIG file set (base < user,
    the same Layer-1 foundation :func:`load_system_config` reads) and returns ITS leaf — so a
    caller with no path in hand yet (:func:`kanibako.vscode.vscode_remote._vscode_remote_state_dir`)
    still tracks a non-default ``config.data`` instead of hardcoding the default leaf.

    ⚑ TOTAL: any failure to read or resolve config — the file is absent, unreadable, or
    malformed YAML, or a stored expression fails to resolve — degrades to the DEFAULT leaf
    (:data:`KANIBAKO_PATH`, matching ``CONFIG_PATH_DEFAULTS["config.data"]``'s own default).
    An absent/unreadable config is exactly TODAY's status quo (nothing has ever read it for
    this purpose either), so this can never be worse; a readable config makes it strictly
    better. ⚑ Builds its own xdg map (:func:`_spec_default_xdg_map` — data/config/state/cache,
    deliberately NOT ``host_xdg_map``) rather than the full Layer-1 resolve's usual map: resolving
    ``XDG_RUNTIME_DIR`` can mkdir a fallback dir when unset, and this function must create
    nothing. The one case that misses: a hand-edited config expression referencing
    ``$XDG_RUNTIME_DIR`` (no shipped default does) degrades to the default leaf rather than
    resolving it — an acceptable trade for staying total and side-effect-free.
    """
    if data_path is not None:
        return data_path.name
    ch = config_home if config_home is not None else xdg(XDG_CONFIG_HOME,
                                                          XDG_SPEC_DEFAULTS[XDG_CONFIG_HOME])
    dh = data_home if data_home is not None else xdg(XDG_DATA_HOME,
                                                      XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    try:
        # ⚑ Lazy import to avoid a config <-> paths import cycle at module load — do not hoist
        # (mirrors load_system_config's own deferral).
        from kanibako.settings.config import config_base_path

        raw: dict[str, str] = {}
        raw.update(load_config(config_base_path()).config_paths)
        raw.update(load_config(config_file_path(ch)).config_paths)
        config_set = {k: v for k, v in raw.items() if k.startswith("config.")}
        resolved = resolve_config_paths(config_set, data_home=dh, home=Path.home(),
                                        xdg_vars=_spec_default_xdg_map(dh))
        return Path(resolved["config.data"]).name
    except Exception:
        return KANIBAKO_PATH


def load_std_paths(config: KanibakoConfig | None = None) -> StandardPaths:
    """Compute all standard kanibako directories, creating them as needed."""
    config_home = xdg(XDG_CONFIG_HOME, XDG_SPEC_DEFAULTS[XDG_CONFIG_HOME])
    data_home = xdg(XDG_DATA_HOME, XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    state_home = xdg(XDG_STATE_HOME, XDG_SPEC_DEFAULTS[XDG_STATE_HOME])
    cache_home = xdg(XDG_CACHE_HOME, XDG_SPEC_DEFAULTS[XDG_CACHE_HOME])

    config_file = config_file_path(config_home)

    if config is None:
        if not config_file.exists():
            raise ConfigError(ERR_CONFIG_NO_FILE % config_file)
        config = load_config(config_file)

    # Resolve the system-level path tier from the CONFIG file set: /etc base < user-global.
    resolved = load_system_config(config_file, data_home=data_home, home=Path.home())
    data_path = resolved["config.data"]
    # state/cache paths track the data dir's leaf name (default leaf "kanibako").
    rel = resolve_data_leaf(data_path)
    state_path = state_home / rel
    cache_path = cache_home / rel

    # Ensure directories exist.
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data_path.mkdir(parents=True, exist_ok=True)
    state_path.mkdir(parents=True, exist_ok=True)
    cache_path.mkdir(parents=True, exist_ok=True)

    return StandardPaths(config_home=config_home, data_home=data_home, state_home=state_home,
                     cache_home=cache_home, config_file=config_file, data_path=data_path,
                     state_path=state_path, cache_path=cache_path, data=resolved["config.data"],
                     backup=resolved["system.backup"], agents=resolved["config.agents"],
                     channels=resolved["system.channelroot"], template=resolved["system.template"],
                     canon=resolved["system.canon"], settings=resolved["config.settings"],
                     primary_workset=resolved["config.primary_workset"],
                     registry=resolved["config.registry"], journal=resolved["config.journal"],
                     cache=resolved["system.cache"], runtime=resolved["system.runtime"],
                     channels_common=resolved["system.channels.common"],
                     channels_chat=resolved["system.channels.chat"],
                     channels_broadcast=resolved["system.channels.broadcast"],
                     channels_mailboxes=resolved["system.channels.mailboxes"],
                     channels_share=resolved["system.channels.share"],
                     boxes=resolved["system._boxes"],
                     primary_vault_ro=resolved["system._primary_vault_ro"],
                     primary_vault_rw=resolved["system._primary_vault_rw"],
                     primary_logs=resolved["system._primary_logs"])


def resolve_project(std: StandardPaths, config: KanibakoConfig, project_dir: str | None = None, *,
                    initialize: bool = False, enable_vault: bool | None = None,
                    name_override: str | None = None, register: bool = True) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths (PRIMARY mode)."""
    raw = project_dir or os.getcwd()
    # A bare token that names no path in cwd may be a registered project name; miss falls through.
    if raw and "/" not in raw and not Path(raw).exists():
        try:
            resolved, kind = resolve_name(std.registry, raw, cwd=Path.cwd(),
                                          primary_workset=std.primary_workset)
            if kind == KIND_PROJECT:
                raw = resolved
        except ProjectError:
            pass
    project_path = Path(raw).resolve()

    if not project_path.is_dir():
        raise ProjectError(ERR_PROJECT_NO_PATH % project_path)

    phash = project_hash(str(project_path))
    project_path_str = str(project_path)

    # Determine the project directory: name-based (boxes/{name}/).
    project_name, project_dir_path = _resolve_local_dir(std, project_path_str)

    # Registry reverse-lookup miss, register=False arm: recover the name from the pending
    # CREATE JOURNAL entry, WITHOUT registering (the caller owns seed -> register -> clear).
    if not project_name and not register:
        from kanibako.launch import journal as journal_mod

        entry = journal_mod.pending_create_for_workspace(std.journal, project_path)
        recovered = (entry.get("name") or "").strip() if entry else ""

        if recovered:
            project_name = recovered
            project_dir_path = std.boxes / recovered

    # Registration-layer reverse-lookup (Bug A durable fix — Guard 2, defense in depth).
    if not project_name and register:
        try:
            _member = _workset_box_name_for_workspace(std.primary_workset, project_path_str)

        except (OSError, RuntimeError):
            _member = None
        if _member:
            project_name = _member
            project_dir_path = std.boxes / project_name

    metadata_path = project_dir_path

    # B2b (Option A, Jei-ruled): the per-box custom home/vault path OVERRIDE is DROPPED.
    project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)
    shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(std, metadata_path,
                                                               project_name or metadata_path.name)
    # enable_vault (P5a): explicit param wins, else stored ``box.enable_vault`` (absent ⇒ True).
    # ⚑ NO ``default_from``: PRIMARY reads the box tier ONLY — adding it would go live workset-wide.
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else read_box_enable_vault(project_toml))

    is_new = False
    if initialize and not project_dir_path.is_dir():
        # Guard: refuse to implicitly create a project rooted at $HOME.
        if project_path == Path.home().resolve():
            raise ProjectError(ERR_PROJECT_NEW_HOME)

        # New project: SELECT a name here only (no store write); the membership write happens
        # below — eager for register=True, deferred to the caller for register=False.
        if name_override:
            if register:
                check_primary_box_name_free(std.primary_workset, std.registry,
                                              name_override, project_path_str)
            project_name = name_override
        elif project_name:
            # Bug A: the workspace is ALREADY registered; reuse the name (re-register is a no-op).
            pass
        else:
            project_name = pick_primary_box_name(std.primary_workset, std.registry,
                                                 project_path_str, boxes_dir=std.boxes)

        project_dir_path = std.boxes / project_name
        metadata_path = project_dir_path
        # Recompute paths with the name-based directory.
        shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(std, metadata_path,
                                                                      project_name)
        project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)

        # ⚑ Creation ownership for the unwind below — must be captured BEFORE ``_init_project``
        # merges into the dir, so the unwind never deletes a pre-existing box's ``home/``.
        _dir_existed = project_dir_path.is_dir()

        _init_project(std, metadata_path, shell_path, vault_ro_path,
                      vault_rw_path, project_path, enable_vault=actual_vault_enabled)

        # Sparse create (P8b/Option A): only a NON-default ``box.enable_vault`` is persisted.
        write_box_enable_vault(project_toml, actual_vault_enabled)
        # Register the PRIMARY membership (name → workspace) — the SOLE store, idempotent.
        # The except-arm is the belt-and-suspenders unwind for a Guard-1 refusal.
        if register:
            try:
                _register_workset_box_membership(std.primary_workset, project_name, project_path)

            except Exception:
                if not _dir_existed:
                    import shutil

                    shutil.rmtree(project_dir_path, ignore_errors=True)
                raise
        is_new = True

    if initialize:
        # Recovery: ensure shell exists even if metadata_path was present.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        # P8b/Option A: NO workset.yaml backfill — identity lives in the registries now.

    return ProjectPaths(project_path=project_path, project_hash=phash, metadata_path=metadata_path,
                        shell_path=shell_path, vault_ro_path=vault_ro_path,
                        vault_rw_path=vault_rw_path,
                        is_new=is_new, mode=BoxMode.primary, enable_vault=actual_vault_enabled,
                        name=project_name, group=_default_project_group(std))


def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]:
    """Find the boxes directory for a default-mode project; ``("", empty_path)`` when unregistered."""
    try:
        name = primary_box_name_for_workspace(std.primary_workset, project_path_str)
    except (OSError, RuntimeError):
        name = None
    if name is not None:
        return name, std.boxes / name

    return "", std.boxes / UNREGISTERED_MARKER


def _primary_box_paths(std: StandardPaths,
                       metadata_path: Path, box_name: str) -> tuple[Path, Path, Path]:
    """Fixed PRIMARY-mode ``(shell, vault_ro, vault_rw)`` (no layout axis)."""
    shell = metadata_path / HOME_PATH
    vault_ro = std.primary_vault_ro / box_name
    vault_rw = std.primary_vault_rw / box_name
    return shell, vault_ro, vault_rw


def _workset_box_paths(metadata_path: Path,
                       vault_base: Path, box_name: str) -> tuple[Path, Path, Path]:
    """Fixed NAMED-mode ``(shell, vault_ro, vault_rw)`` (no layout axis)."""
    shell = metadata_path / HOME_PATH
    vault_ro = vault_base / RO_PATH / box_name
    vault_rw = vault_base / RW_PATH / box_name
    return shell, vault_ro, vault_rw


def _standalone_box_paths(root: Path) -> tuple[Path, Path, Path]:
    """Fixed STANDALONE-mode ``(home, vault_ro, vault_rw)`` (no layout axis)."""
    home = root / STANDALONE_META_DIR / HOME_PATH
    vault_ro = root / VAULT_PATH / RO_PATH
    vault_rw = root / VAULT_PATH / RW_PATH
    return home, vault_ro, vault_rw


def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path:
    """Per-box, per-mode HOST path for the helper message log (the ``helpers.jsonl`` bind source)."""
    box = proj.name if proj.name else short_hash(proj.project_hash)
    if proj.mode is BoxMode.standalone:
        # Anchored under ``box_data/`` (not the root) so the standalone tree stays portable.
        return proj.metadata_path / STANDALONE_META_DIR / f"{box}.jsonl"
    if proj.mode is BoxMode.named:
        # The workset root is carried on the project group (root=ws.root).
        ws_root = proj.group.root if proj.group else proj.metadata_path.parent.parent
        return ws_root / LOGS_PATH / f"{box}.jsonl"
    # PRIMARY: the PRIMARY workset's logs dir.
    return std.primary_logs / f"{box}.jsonl"


def _bootstrap_shell(shell_path: Path) -> None:
    """Write minimal shell skeleton files into a new shell directory."""
    bashrc = shell_path / BASHRC_FILE
    if not bashrc.exists():
        bashrc.write_text(BASHRC_CONTENTS)
    profile = shell_path / PROFILE_FILE
    if not profile.exists():
        profile.write_text(PROFILE_CONTENTS)

    # Create shell.d drop-in directory.
    shell_d = shell_path / SHELL_D_FILE
    shell_d.mkdir(exist_ok=True)


def _upgrade_shell(shell_path: Path) -> None:
    """Keep the ``.shell.d`` sourcing seam current on an existing shell dir (idempotent)."""
    if not shell_path.is_dir():
        return
    shell_d = shell_path / SHELL_D_FILE
    shell_d.mkdir(exist_ok=True)

    bashrc = shell_path / BASHRC_FILE
    if not bashrc.is_file():
        return
    content = bashrc.read_text()
    # ⚑ The trailing slash is load-bearing: the seam is detected by the SOURCE LINE
    # (``~/.shell.d/*.sh``), not by a bare mention of the directory name.
    if SHELL_D_FILE + "/" in content:
        return
    # Append source line.
    if content and not content.endswith("\n"):
        content += "\n"
    content += SHELL_D_CONTENTS
    bashrc.write_text(content)


def _init_common(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path,
                 vault_rw_path: Path, project_path: Path, *, enable_vault: bool = True) -> None:
    """Shared first-time project setup: create directories, bootstrap shell."""
    import sys

    print(MSG_OTS_KB_INIT % project_path, end="", flush=True, file=sys.stderr)
    metadata_path.mkdir(parents=True, exist_ok=True)

    # Create persistent agent shell (mounted as /home/agent).
    shell_path.mkdir(parents=True, exist_ok=True)
    _bootstrap_shell(shell_path)

    # Vault directories (skip when vault is disabled).
    if enable_vault:
        vault_ro_path.mkdir(parents=True, exist_ok=True)
        vault_rw_path.mkdir(parents=True, exist_ok=True)
        # .gitignore in vault/ to exclude rw from version control.
        vault_dir = vault_ro_path.parent
        gitignore = vault_dir / IGNORE_FILE
        if not gitignore.exists():
            gitignore.write_text("rw/\n")

    print(MSG_DONE, file=sys.stderr)


def _init_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path,
                  vault_rw_path: Path, project_path: Path, *, enable_vault: bool = True) -> None:
    """First-time project setup: create directories, copy credentials from host."""
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault)


def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None:
    """Find the deepest registered default-mode project that is an ancestor of *target*."""
    boxes_dir = std.boxes
    best: Path | None = None
    best_depth = -1
    for name, path_str in load_primary_boxes(std.primary_workset).items():
        registered = Path(path_str)
        try:
            target.relative_to(registered)
        except ValueError:
            continue
        # Only accept if boxes_dir/{name}/ exists on disk.
        if not (boxes_dir / name).is_dir():
            continue
        depth = len(registered.parts)
        if depth > best_depth:
            best = registered
            best_depth = depth
    return best


def _is_standalone_meta_dir(root: Path) -> bool:
    """True only if *root* carries the standalone box MARKER: ``box_data/`` AND a root settings file."""
    from kanibako.launch import box_resolve
    return box_resolve.standalone_settings_present(root)


def detect_project_mode(project_dir: Path, std: StandardPaths,
                        config: KanibakoConfig) -> DetectionResult:
    """Infer which project mode applies to *project_dir*, walking ancestors for markers."""
    resolved = project_dir.resolve()
    home = Path.home().resolve()

    # 1. Connected-external check.  ⚑ MUST run BEFORE the step-2 marker check: otherwise
    # import_standalone re-creates the very dual registration that --force removed.
    from kanibako.launch import box_resolve
    if box_resolve.find_connected_external_box(resolved, std) is not None:
        return DetectionResult(BoxMode.named, resolved)

    # 2. In-place standalone marker AT the resolved dir (D3-mode #1, marker-first): it
    # OVERRIDES workset TREE membership.  Only this dir; ancestors are the step-5 walk.
    if _is_standalone_meta_dir(resolved):
        from kanibako.project import import_reconcile
        import_reconcile.import_standalone(std.registry, resolved, journal=std.journal)
        return DetectionResult(BoxMode.standalone, resolved)

    # 3. Workset check (no walk needed — relative_to handles subdirs).
    ws_result = _check_workset(resolved, std)
    if ws_result is not None:
        return ws_result

    # 4. Name-based default-mode check (one-pass scan, deepest match wins).
    ac_ancestor = _find_local_ancestor(resolved, std)
    if ac_ancestor is not None:
        return DetectionResult(BoxMode.primary, ac_ancestor)

    # 5. Walk ancestors for on-disk markers, IMPORTING what is unregistered.  NAMED is
    # checked first at each level: a workset root is the more specific shape.
    from kanibako.project import import_reconcile
    from kanibako.project.workset import (
        is_workset_skeleton, refuse_retired_workset_identity,
    )

    current = resolved
    while True:
        # ⚑⚑ THE LEGACY REFUSAL RUNS FIRST, and unconditionally.  A v1.6/v1.7 root HAS
        # the four-dir skeleton, so the NAMED arm below would import it happily under
        # its leaf name — leaving the retired identity table, and the `projects:` list
        # beside it, unread and unmentioned.  Diagnosing the legacy shape has to come
        # before acting on the directory, or the diagnosis never happens.
        refuse_retired_workset_identity(current)

        # NAMED: an unregistered workset root; import it, then the standard check resolves it.
        if is_workset_skeleton(current):
            import_reconcile.import_named_workset(
                std.registry, current,
                primary_workset=std.primary_workset, journal=std.journal,
            )
            ws_after = _check_workset(resolved, std)
            if ws_after is not None:
                return ws_after

        # STANDALONE: the in-place marker (presence-only since D4); a bare box_data/ is NOT enough.
        if _is_standalone_meta_dir(current):
            import_reconcile.import_standalone(std.registry, current, journal=std.journal)
            return DetectionResult(BoxMode.standalone, current)

        # Stop conditions: reached $HOME or filesystem root.
        if current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 6. Default: primary mode at the original directory.
    return DetectionResult(BoxMode.primary, resolved)


def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None:
    """Check whether *resolved_dir* is inside a registered workset (``workspaces/`` first)."""
    from kanibako.project import registry_store
    from kanibako.project.workset import (load_workset_settings_doc, resolve_workset_workspaces)

    worksets_section = registry_store.load_section(std.registry, "worksets")
    if not worksets_section:
        return None

    for _root_str in worksets_section.values():
        ws_root = Path(_root_str).resolve()
        # The RESOLVED ``workset.workspaces`` — a repoint is honored (§3.3).
        ws_workspaces = resolve_workset_workspaces(ws_root, load_workset_settings_doc(ws_root))
        # Check workspaces/ first (more specific).
        try:
            resolved_dir.relative_to(ws_workspaces)
            return DetectionResult(BoxMode.named, resolved_dir)
        except ValueError:
            pass
        # Then check workset root itself.
        try:
            resolved_dir.relative_to(ws_root)
            return DetectionResult(BoxMode.named, resolved_dir)
        except ValueError:
            continue

    return None


def _workset_box_name_for_workspace(ws_root: Path, workspace: str) -> str | None:
    """Reverse-look-up *workspace* in *ws_root*'s per-workset ``boxes:`` membership (Guard 2)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE))
    return workset_registry.reverse_lookup_workset_box(registry_path, workspace)


def _workset_box_workspace_for_name(ws_root: Path, box_name: str) -> str | None:
    """Forward-look-up *box_name* in *ws_root*'s per-workset ``boxes:`` membership."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE))
    return workset_registry.workset_box_path(registry_path, box_name)


def _register_workset_box_membership(ws_root: Path, box_name: str, workspace: Path) -> None:
    """Register *box_name* → *workspace* in *ws_root*'s per-workset registry (idempotent)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE))
    workset_registry.register_workset_box(registry_path, box_name, workspace)


def _unregister_workset_box_membership(ws_root: Path, box_name: str) -> None:
    """Drop *box_name* from *ws_root*'s per-workset registry (compensating action, idempotent)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE))
    workset_registry.unregister_workset_box(registry_path, box_name)


# ---------------------------------------------------------------------------
# PRIMARY-box name registry (the primary per-workset ``boxes:`` membership).
# ---------------------------------------------------------------------------
# The SOLE store of default-mode box names; mirrors the retired ``names.py`` API.

def load_primary_boxes(primary_workset: Path) -> dict[str, str]:
    """Return the PRIMARY box membership as ``{box_name: workspace_path_str}``."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        primary_workset, load_doc(primary_workset / WORKSET_META_FILE))
    return workset_registry.load_workset_boxes(registry_path)


def primary_box_name_for_workspace(primary_workset: Path, workspace: str) -> str | None:
    """Return the PRIMARY box name registered for *workspace*, or ``None`` (resolved-path aware)."""
    return _workset_box_name_for_workspace(primary_workset, workspace)


def _primary_name_domain(primary_workset: Path, registry: Path) -> set[str]:
    """The PRIMARY-box name collision domain: primary membership ∪ global worksets."""
    from kanibako.project import registry_store

    primary = set(load_primary_boxes(primary_workset))
    worksets = set(registry_store.load_section(registry, "worksets"))
    return primary | worksets


def check_primary_box_name_free(primary_workset: Path, registry: Path, name: str, workspace: str,
                                *, force: bool = False) -> None:
    """Raise ``ProjectError`` if *name* collides in the PRIMARY-box domain (no write)."""
    from kanibako.project import registry_store

    if Path(workspace).resolve() == Path.home().resolve():
        from kanibako.errors import ProjectError
        raise ProjectError(ERR_PROJECT_REG_HOME)

    if name in load_primary_boxes(primary_workset):
        from kanibako.errors import ProjectError
        raise ProjectError(ERR_PROJECT_NAME_USED % name)

    if not force and name in set(registry_store.load_section(registry, "worksets")):
        from kanibako.errors import ProjectError
        raise ProjectError(ERR_PROJECT_DIR_IS_WS % name)


def pick_primary_box_name(primary_workset: Path, registry: Path, workspace: str,
                          boxes_dir: Path | None = None) -> str:
    """Pick a collision-free PRIMARY box name from *workspace*'s basename (no write)."""
    base = Path(workspace).name or "project"
    taken_names = _primary_name_domain(primary_workset, registry)

    def taken(cand: str) -> bool:
        return cand in taken_names or (boxes_dir is not None and (boxes_dir / cand).exists())

    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}{n}"
        n += 1
    return candidate


def register_primary_box_name(primary_workset: Path, registry: Path, name: str,
                              workspace: Path | str, *, force: bool = False) -> None:
    """Register *name* → *workspace* in the PRIMARY membership (with guards)."""
    check_primary_box_name_free(primary_workset, registry, name, str(workspace), force=force)
    _register_workset_box_membership(primary_workset, name, Path(workspace))


def register_primary_box_name_if_absent(primary_workset: Path, registry: Path, name: str,
                                        workspace: Path | str, *, force: bool = False) -> None:
    """Idempotent :func:`register_primary_box_name` for deferred-create recovery."""
    from kanibako.project.workset_registry import _same_workspace

    existing = load_primary_boxes(primary_workset).get(name)
    if existing is not None and _same_workspace(existing, str(workspace)):
        return
    register_primary_box_name(primary_workset, registry, name, workspace, force=force)


def assign_primary_box_name(primary_workset: Path, registry: Path, workspace: Path | str,
                            boxes_dir: Path | None = None) -> str:
    """Auto-assign + register a PRIMARY box name from *workspace*'s basename."""
    candidate = pick_primary_box_name(primary_workset, registry, str(workspace),
                                      boxes_dir=boxes_dir)
    register_primary_box_name(primary_workset, registry, candidate, workspace)
    return candidate


def unregister_primary_box_name(primary_workset: Path, name: str) -> None:
    """Drop *name* from the PRIMARY membership (the membership ``unregister_name``)."""
    _unregister_workset_box_membership(primary_workset, name)


def resolve_workset_project(ws: WorksetSpec, project_name: str, std: StandardPaths,
                            config: KanibakoConfig, *, initialize: bool = False,
                            enable_vault: bool | None = None) -> ProjectPaths:
    """Resolve per-project paths for a project inside a NAMED workset."""
    # Look up project in workset.
    if project_name not in ws.project_names:
        raise WorksetError(ERR_WORKSET_NO_PROJECT % (project_name, ws.name))

    # Name-based paths (not hash-based).
    project_path = ws.workspaces_dir / project_name
    project_dir = ws.projects_dir / project_name
    metadata_path = project_dir

    # Workspace override (P7/D10): the REGISTERED path IS the workspace; unregistered
    # members fall back to the composed default.  ⚑ Never re-derive a registered member.
    project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)
    registered_workspace = _workset_box_workspace_for_name(ws.root, project_name)
    if registered_workspace is not None:
        project_path = Path(registered_workspace)
    else:
        from kanibako.launch import box_resolve
        identity = box_resolve.resolve_box_identity(project_path, std, config)
        if identity is not None:
            project_path = Path(identity["workspace"])
    # B2b (Option A, Jei-ruled): the per-box custom home/vault path OVERRIDE is DROPPED;
    # the workspace override above is a SEPARATE concern and STAYS.
    shell_path, vault_ro_path, vault_rw_path = _workset_box_paths(metadata_path, ws.vault_dir,
                                                                  project_name)
    # enable_vault (P5a): explicit param wins, else stored ``box.enable_vault`` (absent ⇒ True).
    # ⚑ NO ``default_from``: NAMED reads the box tier ONLY, exactly as before P2.
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else read_box_enable_vault(project_toml))

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        # Sparse create (P8b/Option A): only a NON-default ``box.enable_vault`` is persisted.
        write_box_enable_vault(project_toml, actual_vault_enabled)
        # P5a dual-register (idempotent): the SOLE on-disk identity record.  Sourced from the
        # RESOLVED *project_path* so an external-connect override seeds the external dir.
        _register_workset_box_membership(ws.root, project_name, project_path)
        is_new = True

    if initialize:
        # Recovery: ensure shell exists.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)

    # J2 connect self-heal: the box is already registered, so recovery == CLEAR the stale
    # entry (NO re-register, NO seed).  ⚑ The key is the host-side box dir, not the workspace.
    journal_path = getattr(std, "journal", None)
    if journal_path is not None:
        from kanibako.launch import journal as _journal
        box_key = Path(shell_path).parent
        if _journal.pending_import(journal_path, box_key) is not None:
            _journal.clear_entry(journal_path, box_key)

    return ProjectPaths(project_path=project_path, project_hash=phash, metadata_path=metadata_path,
                        shell_path=shell_path, vault_ro_path=vault_ro_path,
                        vault_rw_path=vault_rw_path, is_new=is_new, mode=BoxMode.named,
                        enable_vault=actual_vault_enabled, name=project_name,
                        group=ProjectGroup(name=ws.name, root=ws.root, is_default=False,
                                           local_shared_base=ws.root))


def _init_workset_project(std: StandardPaths, metadata_path: Path, shell_path: Path) -> None:
    """First-time workset project setup: bootstrap shell directory (no vault ``.gitignore``)."""
    import sys
    print(MSG_OTS_WS_PROJ_INIT % metadata_path, end="", flush=True, file=sys.stderr)
    metadata_path.mkdir(parents=True, exist_ok=True)

    # Create persistent agent shell (mounted as /home/agent).
    shell_path.mkdir(parents=True, exist_ok=True)
    _bootstrap_shell(shell_path)
    print(MSG_DONE, file=sys.stderr)


def iter_projects(std: StandardPaths, config: KanibakoConfig) -> list[tuple[Path, Path | None]]:
    """Return ``(metadata_path, project_path | None)`` for every known project."""
    projects_dir = std.boxes
    if not projects_dir.is_dir():
        return []
    # P8a: box → workspace comes SOLELY from the PRIMARY per-workset registry, read directly
    # (it is keyed by workspace PATH, so ``resolve_box_identity`` cannot answer from a box dir).
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    primary_registry = workset_registry.resolve_workset_registry_path(
        std.primary_workset, load_doc(std.primary_workset / WORKSET_META_FILE))
    registered = workset_registry.load_workset_boxes(primary_registry)
    results: list[tuple[Path, Path | None]] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        registered_ws = registered.get(entry.name)
        project_path: Path | None = Path(registered_ws) if registered_ws else None
        results.append((entry, project_path))
    return results


def iter_workset_projects(std: StandardPaths, config: KanibakoConfig) -> _WorksetProjectRows:
    """Return ``(workset_name, workset, [(project_name, status), ...])`` for every workset."""
    import sys

    from kanibako.project.workset import list_worksets, load_workset

    registry = list_worksets(std)
    results: _WorksetProjectRows = []

    for ws_name in sorted(registry):
        root = registry[ws_name]
        if not root.is_dir():
            print(WARN_WS_NO_ROOT % (ws_name, root), file=sys.stderr)
            continue
        try:
            ws = load_workset(root, ws_name)
        except Exception as exc:
            print(WARN_WS_BAD_LOAD % (ws_name, exc), file=sys.stderr)
            continue

        project_list: list[tuple[str, str]] = []
        for proj in ws.projects:
            has_project_dir = (ws.projects_dir / proj.name).is_dir()
            # ⚑ Presence is checked at the REGISTERED path — ``source_path`` IS the
            # ``boxes:`` value, so there is no second read and nothing to re-derive.
            has_workspace = proj.source_path.is_dir()
            if has_project_dir and has_workspace:
                status = STATUS_OK
            elif has_project_dir and not has_workspace:
                status = STATUS_MISSING
            else:
                status = STATUS_NO_DATA
            project_list.append((proj.name, status))

        results.append((ws_name, ws, project_list))

    return results


def _find_workset_for_path(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]:
    """Return ``(workset, project_name)`` for a path inside a workset (name ``None`` at the root)."""
    from kanibako.project.workset import (list_worksets, load_workset,
                                          load_workset_settings_doc, resolve_workset_workspaces)

    registry = list_worksets(std)
    resolved = project_dir.resolve()
    for ws_name, root in registry.items():
        ws_root = root.resolve()
        # The RESOLVED ``workset.workspaces`` — a repoint is honored (§3.3).
        ws_workspaces = resolve_workset_workspaces(ws_root, load_workset_settings_doc(ws_root))
        # Check workspaces/ first (specific project).
        try:
            rel = resolved.relative_to(ws_workspaces)
            project_name = rel.parts[0] if rel.parts else None
            ws = load_workset(root, ws_name)
            return ws, project_name
        except ValueError:
            pass
        # Then check workset root itself.
        try:
            resolved.relative_to(ws_root)
            ws = load_workset(root, ws_name)
            return ws, None
        except ValueError:
            continue
    raise WorksetError(ERR_WORKSET_NO_WORKSET % project_dir)


def _resolve_workset_or_connected(project_dir: Path,
                                  std: StandardPaths) -> tuple[_WorksetLike, str | None]:
    """Resolve *project_dir* to its owning workset, honoring external connects."""
    try:
        ws, proj_name = _find_workset_for_path(project_dir, std)
    except WorksetError:
        ws, proj_name = None, None
    if ws is None or proj_name is None:
        # Tree lookup missed: try the connected-external boxes (D10 enumerate-and-scan).
        # ⚑ Lazy import avoids a paths <-> box_resolve import cycle — do not hoist.
        from kanibako.launch import box_resolve
        from kanibako.project.workset import load_workset
        owned = box_resolve.find_connected_external_box(project_dir.resolve(), std)
        if owned is not None:
            ws, proj_name = (load_workset(owned.workset_root, owned.workset_name),
                             owned.box_name)
    if ws is None:
        raise WorksetError(ERR_WORKSET_NO_WORKSET % project_dir)
    return ws, proj_name


def resolve_any_project(std: StandardPaths, config: KanibakoConfig, project_dir: str | None = None,
                        *, initialize: bool = False, register: bool = True,
                        name_override: str | None = None) -> ProjectPaths:
    """Auto-detect project mode and resolve paths accordingly."""
    raw = project_dir or os.getcwd()
    # ⚑ CLI front-door name lookup, which must run BEFORE Path(raw).resolve() path-ifies
    # the token — otherwise detect_project_mode never sees the registered name.
    named_workset = False
    raw_name = raw
    if raw and "/" not in raw and not Path(raw).exists():
        try:
            resolved, kind = resolve_name(std.registry, raw, cwd=Path.cwd(),
                                          primary_workset=std.primary_workset)
        except ProjectError:
            # An unknown bare token: on the READ path, refuse rather than path-ify it into
            # a phantom ``kanibako-<hash>`` box.  The CREATE path still path-ifies.
            if not initialize:
                raise
        else:
            if kind in (KIND_PROJECT, KIND_WORKSET):
                # Update `raw` for BOTH kinds; a bare WORKSET is still rejected below.
                raw = resolved
                named_workset = kind == KIND_WORKSET

    if named_workset:
        # A workset is not a single box; fail with an actionable message, not the generic one.
        raise WorksetError(ERR_WORKSET_WS_NOT_BOX % (raw_name, raw_name))

    # Qualified ``workset/project`` addressing; a real relative path is left untouched.
    if "/" in raw and not Path(raw).exists():
        try:
            project_workspace, _ws_name = resolve_qualified_name(std.registry, raw)
            raw = project_workspace
        except ProjectError:
            pass
    raw_dir = Path(raw).resolve()
    detection = detect_project_mode(raw_dir, std, config)
    root_str = str(detection.project_root)

    if detection.mode == BoxMode.named:
        ws, proj_name = _resolve_workset_or_connected(raw_dir, std)
        if proj_name is None:
            raise WorksetError(ERR_WORKSET_NOT_IN_BOX % (ws.name, ws.workspaces_dir))

        return resolve_workset_project(WorksetSpec.from_workset(ws), proj_name, std, config,
                                       initialize=initialize)
    if detection.mode == BoxMode.standalone:
        return resolve_standalone_project(std, config, root_str, initialize=initialize,
                                          register=register)
    return resolve_project(std, config, project_dir=root_str, initialize=initialize,
                           register=register, name_override=name_override)


def resolve_box_target(std: StandardPaths, config: KanibakoConfig, value: str | None = None,
                       *, initialize: bool = False, register: bool = True,
                       warn: bool = True) -> ProjectPaths:
    """Resolve a ``--box`` value (a box NAME or a path) to its :class:`ProjectPaths`, NAME first."""
    def _flag(proj: ProjectPaths) -> ProjectPaths:
        if warn:
            _flag_nonconforming(proj)
            _flag_invalid_kuid(proj)
            _flag_missing_vault(proj)
        return proj

    # Empty / None -> cwd resolution (same as a bare positional default).
    if not value:
        return _flag(resolve_any_project(std, config, value, initialize=initialize,
                                         register=register))

    # NAME-first: the standalone-name domain, which resolve_any_project does NOT cover.
    if "/" not in value:
        from kanibako.project import registry_store

        standalone = registry_store.load_standalone(std.registry)
        # Box names are lowercase (R2); fold the query for the lookup.
        root_str = standalone.get(value.lower())
        if root_str is not None:
            return _flag(resolve_standalone_project(std, config, root_str, initialize=initialize,
                                                    register=register))

    # Else: NAME (projects/worksets/qualified) or PATH, both via the existing resolver.
    return _flag(resolve_any_project(std, config, value, initialize=initialize, register=register))


def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths:
    """Warn (do NOT reject) when a resolved box's name violates the blocklist."""
    from kanibako.launch.box_identity import box_name_reason

    if proj.name:
        reason = box_name_reason(proj.name)
        if reason is not None:
            get_logger(__name__).warning(WARN_WS_BOX_BAD_NAME, proj.name, reason)

    return proj


def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths:
    """Advisory (never fatal): flag a standalone box whose stored ``workset.kuid`` is invalid."""
    if proj.mode is not BoxMode.standalone:
        return proj
    from kanibako import kuid

    # ⚑ WORKSET-scope keys, so read from the WORKSET tier of the ONE pair — never re-spelled.
    _, settings_file = box_workset_settings_paths(proj)
    if settings_file is None:
        # Unreachable for standalone; the guard exists so the reads below are TYPED.
        return proj
    value = read_workset_kuid(settings_file)
    if (value != kuid.SENTINEL and not read_workset_skip_kuid_check(settings_file)
            and not kuid.is_valid(value)):
        get_logger(__name__).warning(WARN_BOX_BAD_KUID, value, proj.name)

    return proj


def _flag_missing_vault(proj: ProjectPaths) -> ProjectPaths:
    """Advisory (never fatal): warn when a box that EXPECTS a vault has none on disk (spec D5)."""
    if proj.enable_vault and not proj.vault_rw_path.is_dir():
        get_logger(__name__).warning(WARN_BOX_NO_VAULT, proj.name or str(proj.project_path),
                                     proj.vault_rw_path)

    return proj


def establish_standalone(std: StandardPaths, root: Path, *, enable_vault: bool,
                         name: str = "", register: bool = True) -> tuple[str, Path, Path, Path]:
    """Establish a standalone box at *root*: identity + meta + registration (the shared core)."""
    from kanibako.project import registry_store
    from kanibako.launch import box_identity

    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)

    existing = registry_store.standalone_box_names(std.registry)
    box_name = box_identity.resolve_standalone_name(root, name, existing)

    box_settings, settings_file = _standalone_settings_files(root)
    # ⚑ Sparse create, EACH KEY AT ITS OWN SCOPE'S TIER (M-8): box-scope ``box.enable_vault``
    # to the BOX tier — the same file ``config set box.*`` writes.
    write_box_enable_vault(box_settings, enable_vault)
    # ⚑ The workset-scope kuid goes to the ROOT file, whose write MATERIALIZES the §5 marker.
    from kanibako.settings.config_io import write_nested_key

    write_nested_key(settings_file, ("workset",), "kuid", box_identity.standalone_kuid(box_name))
    if register:
        registry_store.register_standalone(std.registry, box_name, root)
    return box_name, shell_path, vault_ro_path, vault_rw_path


def resolve_standalone_project(std: StandardPaths, config: KanibakoConfig,
                               project_dir: str | None = None, *, initialize: bool = False,
                               enable_vault: bool | None = None, name: str = "",
                               register: bool = True) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths for standalone mode."""
    raw = project_dir or os.getcwd()
    root = Path(raw).resolve()

    if not root.is_dir():
        raise ProjectError(ERR_PROJECT_NO_PATH % root)

    # The hash + identity key off the stable ROOT; the workspace subdir is not the identity.
    phash = project_hash(str(root))

    from kanibako.project.workset import (load_workset_settings_doc, resolve_workset_workspaces)

    # Metadata at the ROOT; ``project_path`` is the RESOLVED ``workset.workspaces`` (ruled 10).
    metadata_path = root
    box_data = root / STANDALONE_META_DIR
    project_path = resolve_workset_workspaces(root, load_workset_settings_doc(root),
                                              standalone=True)
    # The mode-aware tier pair from the ONE derivation (M-8).
    box_settings, project_toml = _standalone_settings_files(root)

    # ⚑ STANDALONE paths derive from the CURRENT root, never stored absolutes — that is
    # what makes a default-shaped tree drop-in portable BY CONSTRUCTION.
    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)
    # enable_vault (P5a): explicit param wins, else the BOX tier with an R2 downward-default
    # to the WORKSET tier.  ⚑ That fallback is standalone-only; it is the pre-P2 migration path.
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else read_box_enable_vault(box_settings, default_from=project_toml))

    # Box identity name (P8a): composed LIVE by ``box_resolve`` for a MATERIALIZED standalone;
    # a not-yet-materialized root yields "" and the create block below assigns it.
    box_name = ""
    if box_data.is_dir() and project_toml.is_file():
        from kanibako.launch import box_resolve
        identity = box_resolve.resolve_box_identity(root, std, config)
        box_name = identity["name"] if identity is not None else ""
    # The user's explicit --name; ignored once the box exists (stored identity is authoritative).
    requested_name = name

    is_new = False
    if initialize and not box_data.is_dir():
        # ⚑ Pre-flight the requested --name BEFORE any FS mutation, so a doomed create
        # refuses up front rather than orphaning a half-created tree (BUG-A).
        from kanibako.project import registry_store
        from kanibako.launch import box_identity
        box_identity.validate_standalone_name(requested_name,
                                              registry_store.standalone_box_names(std.registry))
        # ⚑ The WORKSET CANON tier, stamped CANON-ONLY.  ``workset.canon`` is UNIFORM
        # IN EVERY MODE (spec ``:962``) so a lone box has one; ``workset.template`` is
        # <None> in standalone (spec ``:936``), so the template half is NOT stamped —
        # it would be structure for a key this mode does not have.
        #
        # ⚑⚑ PRE-FLIGHT, THEN STAMP AS THE CREATE'S FIRST WRITE.  Refusing here leaves
        # NOTHING behind: ``box_data/`` does not exist yet, so the guard above is still
        # true and a corrected re-run does the whole create.  The copy is
        # create-if-absent, so a re-run or a recovery pass adds only what is missing
        # and clobbers no file already under ``canon/``.
        #
        # ⚑⚑ AND THERE IS DELIBERATELY NO UNWIND — DO NOT ADD ONE, and do not "unify
        # the creators" by routing standalone through ``create_workset``.  That
        # function's failure path is ``shutil.rmtree(root)`` (``project/workset.py``),
        # which is safe only because IT made the root.  A standalone root is a
        # directory the USER already had — ``root.is_dir()`` is required above — so the
        # same unwind would delete their project.  That is the trap in the refactor.
        from kanibako.launch.templates import check_workset_template, install_workset_template

        check_workset_template(std, root, canon_only=True)
        install_workset_template(std, root, canon_only=True)
        _init_standalone_project(std, box_data, shell_path, vault_ro_path, vault_rw_path,
                                 project_path, enable_vault=actual_vault_enabled)
        # Identity + meta + registration via the shared establish core (fresh identity here).
        box_name, shell_path, vault_ro_path, vault_rw_path = establish_standalone(
            std, root, enable_vault=actual_vault_enabled, name=requested_name, register=register)
        is_new = True

    if initialize:
        # Recovery: ensure home + workspace exist.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        project_path.mkdir(parents=True, exist_ok=True)

    return ProjectPaths(project_path=project_path, project_hash=phash, metadata_path=metadata_path,
                        shell_path=shell_path, vault_ro_path=vault_ro_path,
                        vault_rw_path=vault_rw_path, is_new=is_new, mode=BoxMode.standalone,
                        enable_vault=actual_vault_enabled, name=box_name)


def _init_standalone_project(std: StandardPaths, metadata_path: Path, shell_path: Path,
                             vault_ro_path: Path, vault_rw_path: Path, project_path: Path,
                             *, enable_vault: bool = True) -> None:
    """First-time standalone project setup: all state inside the project dir (vault included)."""
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault)
    # The workspace is a SUBDIR of the root (drift H); create the bind source.
    project_path.mkdir(parents=True, exist_ok=True)
