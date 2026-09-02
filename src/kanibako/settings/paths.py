"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

from kanibako.settings.messages import (PROFILE_CONTENTS, BASHRC_CONTENTS,
                                              SHELL_D_CONTENTS,

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
                                              ERR_WORKSET_WS_NOT_BOX, ERR_WORKSET_NOT_IN_BOX)

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import NamedTuple, Protocol, overload

from kanibako.log import get_logger

from kanibako.settings.config import (WORKSET_META_FILE, BOX_META_FILE, BootstrapConfig, config_file_path,
                                      load_config, read_box_enable_vault, read_workset_kuid,
                                      read_workset_skip_kuid_check, resolve_box_enable_vault,
                                      write_box_enable_vault)

from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings.agent_config import (ambiguous_path_value_error,
                                            is_unambiguous_path_value)
from kanibako.settings.settings_resolve import (LevelView, ResolveCtx, SettingsError,
                                                _Unset, expand_expr, resolve_value)

from kanibako.project.names import (resolve_name, resolve_qualified_name)
from kanibako.utils import project_hash, short_hash
from kanibako.settings.bootstrap import (BASHRC_FILE, CONFIG_PATH_DEFAULTS, HOME_PATH,
                                         IGNORE_FILE, KANIBAKO_PATH, KIND_PROJECT, KIND_WORKSET,
                                         PROFILE_FILE, RUN_USER_UID_PATH, SHELL_D_FILE,
                                         STANDALONE_META_DIR, SYSTEM_PATH_DEFAULTS,
                                         UNREGISTERED_MARKER, VAULT_PATH, XDG_CACHE_HOME,
                                         XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_RUNTIME_DIR,
                                         XDG_SPEC_DEFAULTS, XDG_STATE_HOME)


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
    return ProjectGroup(name="default", root=std.primary_workset,
                        is_default=True, local_shared_base=std.data_path)


@dataclass
class ProjectPaths:
    """Resolved paths for a specific project."""
    project_path: Path
    project_hash: str
    metadata_path: Path      # host-only: workset.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    # ⚑ The RESOLVED ``workset.{vault_ro,vault_rw}`` (+ a ``<box-name>`` leaf in primary
    # and named mode) — NOT ``project_path/vault/ro``.  🛑 That stale spelling is what the
    # comment here used to say, and ``commands/archive.py`` was written against it.
    vault_ro_path: Path      # → /home/agent/vault/ro
    vault_rw_path: Path      # → /home/agent/vault/rw
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
                        group: "_WorksetRooted | None") -> tuple[Path, Path | None]:
    """THE ``(box_tier, workset_tier)`` settings-file derivation (spec §2c) — spelled ONCE.
    ⚑ The box tier is non-optional BY TYPE; do not widen the return to ``Path | None``.
    ⚑ *group* is anything rooted at ``@meta.workset.path``: a :class:`ProjectGroup` OR a
    :class:`WorksetSpec`, since the NAMED resolver builds its group only at return time."""
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
    def vault_ro_dir(self) -> Path: ...
    @property
    def vault_rw_dir(self) -> Path: ...
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
    #: ⚑ The RESOLVED ``workset.{vault_ro,vault_rw}`` — ONE ARM EACH, never a shared
    #: ``vault/`` parent to join ``ro``/``rw`` onto.  The two are independently
    #: repointable keys, so a single parent cannot answer both.
    vault_ro_dir: Path
    vault_rw_dir: Path
    project_names: tuple[str, ...]
    is_default: bool = False

    @classmethod
    def from_workset(cls, ws: _WorksetLike) -> WorksetSpec:
        """Build a :class:`WorksetSpec` from a ``Workset``-like object."""
        return cls(name=ws.name, root=ws.root, projects_dir=ws.projects_dir,
                   workspaces_dir=ws.workspaces_dir, vault_ro_dir=ws.vault_ro_dir,
                   vault_rw_dir=ws.vault_rw_dir,
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


def spec_default_xdg_map(data_home: Path | None) -> dict[str, str]:
    """The XDG vars that HAVE a spec default (data/config/state/cache) — no ``XDG_RUNTIME_DIR``.

    ⚑ Side-effect-free: unlike ``XDG_RUNTIME_DIR`` (see :func:`_fallback_runtime_dir`), none of
    these four ever mkdir a fallback dir — :func:`resolve_data_leaf` relies on that to stay total.
    ⚑ PUBLIC for the second caller that needs exactly that guarantee:
    ``settings/workset_dirkeys.py`` resolves ``$XDG_*`` inside the ancestor WALK, where a
    mkdir-and-warn on a directory that turns out not to be a workset is a real side effect.
    It is the SAME builder, not a copy — :func:`host_xdg_map` still wraps it.
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
    xdg_map = spec_default_xdg_map(data_home)
    xdg_map[XDG_RUNTIME_DIR] = str(resolve_xdg(XDG_RUNTIME_DIR, None))
    return xdg_map


def _refuse_bare_relative(key: str, raw: object, default: str, *,
                          ctx: ResolveCtx,
                          lookup: Callable[[str, tuple[str, ...]], str]) -> None:
    """Refuse a Layer-1/Layer-2 path key whose STORED value is a bare relative ([R147]).

    ⚑⚑ THE TEST IS ON THE STORED SPELLING, NOT ON WHAT IT RESOLVED TO, and the
    difference is load-bearing.  [R147] rules on the value a user WROTE: ``$XDG_DATA_HOME
    /kanibako`` is a legal stored value even in an environment where that variable
    answers something odd, and refusing it there would report a KEY defect for an
    ENVIRONMENT one — with a "did you mean" line that pastes the token back into itself.
    A source that resolves to a relative path is a different rule at a different layer
    (``settings_expand._refuse_relative_host_src``), with its own message.
    ⚑ The other candidate root is DERIVED from this key's own declared *default* (P13),
    never listed: it is the default's leading token, so a key added to either table
    carries its own anchor into this message.
    """
    value = str(raw)
    if not value or is_unambiguous_path_value(value):
        return
    anchor_ref = default.split("/", 1)[0]
    try:
        anchor = expand_expr(anchor_ref, space="host", ctx=ctx, lookup=lookup)
    except SettingsError:
        anchor = anchor_ref  # An unresolvable anchor still names the reading.
    raise SettingsError(ambiguous_path_value_error(
        key, value, anchor=anchor, anchor_ref=anchor_ref,
    ))


def resolve_config_paths(set_values: Mapping[str, str], *, data_home: Path, home: Path,
                         xdg_vars: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve the Layer-1 CONFIG-key foundation to concrete host paths (flat by design).

    ⚑ *xdg_vars*, when given, REPLACES the live :func:`host_xdg_map` build — the seam
    :func:`resolve_data_leaf` uses to resolve ``config.data`` without touching
    ``XDG_RUNTIME_DIR`` (whose fallback can mkdir; see :func:`spec_default_xdg_map`).
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
    for key, default in CONFIG_PATH_DEFAULTS.items():
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(ERR_SETTINGS_BAD_PATH % ("config", key))
        _refuse_bare_relative(key, rv.value, default, ctx=ctx, lookup=lookup)
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
    for key, default in SYSTEM_PATH_DEFAULTS.items():
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(ERR_SETTINGS_BAD_PATH % ("system", key))
        _refuse_bare_relative(key, rv.value, default, ctx=ctx, lookup=lookup)
        expanded = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
        resolved[key] = Path(expanded)

    # PRIMARY-workset box/vault/logs roots, derived from ``@config.primary_workset``.
    # ⚑⚑ ALL FOUR ARE RESOLVED, NOT COMPOSED.  There are no ``system.{boxes,logs,vault_*}``
    # keys at all (spec ``:335``) — these are SURROGATES for the PRIMARY workset's
    # ``@workset.{boxes,logs,vault_ro,vault_rw}``, which are declared, CLI-settable and
    # repointable in EVERY mode (§2c ALL PROJECTS, R-29).  Composing them here answered
    # the key a second way: the settings file accepted a repoint and the filesystem
    # ignored it.  ⚑ The PRIMARY workset root is an ordinary workset root and carries an
    # ordinary ``workset.yaml``, so it repoints exactly as a named one does.
    # ⚑ Resolving HERE and not at ``_primary_box_paths`` is deliberate — every consumer
    # of ``std.boxes`` / ``std.primary_logs`` / ``std.primary_vault_*`` (create, rm,
    # clean, purge, the helper hub) then sees the ONE answer.
    # ⚑ Deferred import: the documented ``settings.paths`` <-> ``project.workset`` cycle.
    pw = resolved["config.primary_workset"]
    from kanibako.project.workset import (load_workset_settings_doc, resolve_workset_boxes,
                                          resolve_workset_logs, resolve_workset_vault_ro,
                                          resolve_workset_vault_rw)

    pw_settings = load_workset_settings_doc(pw)
    resolved["system._boxes"] = resolve_workset_boxes(pw, pw_settings)
    resolved["system._primary_vault_ro"] = resolve_workset_vault_ro(pw, pw_settings)
    resolved["system._primary_vault_rw"] = resolve_workset_vault_rw(pw, pw_settings)
    resolved["system._primary_logs"] = resolve_workset_logs(pw, pw_settings)
    return resolved


def host_config_map(std: StandardPaths) -> dict[str, str]:
    """THE single builder for the ``config=`` argument of every host-side ``ResolveCtx``.

    The Layer-1 CONFIG-key foundation projected BACK onto its own dotted key names, so
    a stored ``@config.*`` source resolves at launch.  The Layer-1 twin of
    :func:`system_path_floor`, and the ``config=`` twin of :func:`host_xdg_map` — a host
    ctx is built from those two and nothing else.

    ⚑⚑ DERIVED FROM :data:`CONFIG_PATH_DEFAULTS`, WHICH IS WHY THIS EXISTS.  The map was
    written out inline in ``settings/agent_select.launch_resolve_ctx`` and again in
    ``commands/workset_cmd._print_effective_shares``, five string literals each — and the
    table has held SIX keys since ``config.journal`` was declared the day after those
    literals were written.  So ``config set config.journal=…`` was accepted, and a binding
    sourced at ``@config.journal`` resolved at SET time and reached ``_ABSENT`` at launch:
    the key was dropped with no message and rc 0.  That is the ``system.channels.broadcast``
    shape exactly, one layer down — two carriers of one shape, with nothing comparing them.
    Deriving the key set means a Layer-1 key declared tomorrow reaches every host ctx
    without an edit here.

    ⚑ The ``StandardPaths`` attribute is ``key.split(".", 1)[1]`` for all six, and that is
    a rule rather than a coincidence: Layer 1 names its fields after its keys, which is
    why this needs no alias at all where ``system_path_floor`` needs exactly one
    (``system.channelroot`` → ``std.channels``; see :data:`_FLOOR_FIELD_ALIASES`).
    A Layer-1 key added WITHOUT the matching field raises ``AttributeError`` on the next
    ctx build — loud, immediate, and at every launch.  Silent omission is the failure this
    replaces; a crash is strictly the better one.
    """
    return {key: str(getattr(std, key.split(".", 1)[1])) for key in CONFIG_PATH_DEFAULTS}


#: The :class:`StandardPaths` FIELD a Layer-2 ``system.*`` key's resolved value lands in,
#: for the keys where that field is not ``key.split(".", 1)[1].replace(".", "_")``.
#: ⚑⚑ A SPELLING TABLE, NOT A MEMBERSHIP LIST, and the difference is the whole of the
#: 2026-08-28 widening: FLOOR MEMBERSHIP is :data:`SYSTEM_PATH_DEFAULTS` entire, so a key
#: declared tomorrow reaches the floor with no edit here, and a key whose field is missing
#: raises ``AttributeError`` at the next floor build instead of dangling silently.  A
#: membership list fails by SILENCE; this one fails by CRASH, which is the trade
#: :func:`host_config_map` makes one layer down for the same reason.
#: ⚑ ``system.channelroot`` is the only irregular pair — the field is older than the key
#: name and is read as ``std.channels`` throughout.  ``system.canon`` and
#: ``system.template`` follow the rule: the SYSTEM-level CANON CONTRIBUTION root (spec
#: §2g) is the source of the handbook's SYS_CONTENTS.md + general chapter binds and the
#: install dest of the packaged handbook — a per-scope CONTRIBUTION root, NOT a copy of
#: the assembled canon (``~/canon`` in-guest is the assembly, ``@<scope>.canon`` on the
#: host is what that scope contributes to it).
_FLOOR_FIELD_ALIASES: dict[str, str] = {"system.channelroot": "channels"}


def _floor_field(key: str) -> str:
    """The :class:`StandardPaths` field holding *key*'s resolved value."""
    return _FLOOR_FIELD_ALIASES.get(key, key.split(".", 1)[1].replace(".", "_"))


def system_path_floor(std: StandardPaths) -> dict[str, str]:
    """The RESOLVED Layer-2 ``system.*`` path tier, keyed by its own dotted key names.

    Every consumer folds this into a floor so a stored ``@system.*`` source resolves.
    Each value equals the corresponding ``std`` attribute — the same flat foundation
    resolves both — so an ``@``-ref-routed bind is byte-identical to a runtime-probed
    literal.

    ⚑⚑ ONE CARRIER, AND IT IS ONE BECAUSE TWO HAD ALREADY DRIFTED — in BOTH directions.
    ``commands/start._launch_snapshot_inputs`` (the launch snapshot) and
    ``commands/workset_cmd._print_effective_shares`` (``workset share list
    --effective``) each built this map by hand, under paired comments telling each other
    they must agree.  They did not: the launch map omitted
    ``system.channels.broadcast``, so a binding sourcing that declared key collapsed to
    ``None`` and was DROPPED with no message and rc 0; the display map omitted all five
    ``system.channels.*`` leaves, so a workset binding sourcing
    ``@system.channels.chat`` mounted at launch and did not appear in ``--effective``.
    A display that lies about what a launch does is precisely what those comments
    existed to prevent, and a hand list on each side is how they failed to.

    ⚑⚑ DERIVED FROM :data:`SYSTEM_PATH_DEFAULTS` ENTIRE SINCE 2026-08-28, which WIDENED
    it from 8 keys to 11.  The hand-named ``_FLOOR_ROOT_KEYS`` tuple it replaced carried
    three roots and derived only the channel leaves, so ``system.backup``,
    ``system.cache`` and ``system.runtime`` — declared, manifest-defaulted, CLI-settable,
    and resolved by the SET-time tier (``config_interface._path_tier_split``) — answered
    ``__MISSING__`` in every launch snapshot.  ``config set`` therefore ACCEPTED a
    binding sourced at ``@system.cache`` and the launch dropped it with no message and
    rc 0: the ``system.channels.broadcast`` shape again, one omission over.  [R143]
    settles that it is a defect rather than a report — *"if it has a default value, yes,
    thay value should be placed in the keystore"* — universally, with no exemption list.

    ⚑ RESERVED AND REACHABLE ARE ORTHOGONAL.  Nothing in kanibako READS those three yet,
    and that stays true: *reserved* is a fact about CONSUMERS, this floor is a fact about
    the KEYSTORE, and a reserved key still answers.  No discriminator is needed because
    the question was never asked.

    ⚑ CONSUMERS CHECKED IN THE SAME CHANGE — both, and both take the whole map:
    ``commands/start._launch_snapshot_inputs`` (three more scalars in the snapshot floor,
    folded by the last-wins arm of ``_merge_default_categories`` — no category key, no
    origin claim, no refusal, and the keys are declared so ``_refuse_undeclared_snapshot``
    is silent) and ``commands/workset_cmd._print_effective_shares`` (``workset share list
    --effective``, which folds the map into a resolve floor and PRINTS bindings, never the
    floor itself).  ``tests/test_channels/test_system_channel_keys.py`` carried the
    by-name pin on the omission; it is INVERTED, not deleted.
    """
    return {key: str(getattr(std, _floor_field(key))) for key in SYSTEM_PATH_DEFAULTS}


def load_system_config(user_config_path: Path, *, data_home: Path, home: Path) -> dict[str, Path]:
    """Resolve the path tier: ``/etc`` config base < user config < the SYSTEM SETTINGS file.

    ⚑⚑ THE SETTINGS FILE IS THE TOP LAYER, AND IT IS THE WHOLE POINT OF THE THIRD
    ``update`` BELOW.  ``system.{template,canon,runtime,cache,backup,channelroot}`` and
    ``system.channels.*`` are Layer-2 SETTINGS keys (spec §2g: "set in settings files at
    the ``system`` cascade level"), and ``config set system.canon=…`` writes them to
    ``@config.settings``.  Until 2026-08-23 this function read the CONFIG files ONLY, so
    that write reached the launch cascade and NOT :class:`StandardPaths` — a repoint that
    was accepted, persisted, and half-effective.  A settable key whose set does not reach
    the thing it names is worse than a refusal, because it never confesses.

    ⚑ THE LAYER-1 RESOLVE RUNS TWICE ON PURPOSE, and it is not a wasted read: locating the
    settings file IS ``@config.settings``, so the foundation must resolve before the file
    can be opened.  :func:`resolve_config_paths` is a pure dict resolve over set-values
    already in hand — the second pass reopens nothing.

    ⚑ FILTERED TO :data:`SYSTEM_PATH_DEFAULTS` (P13 — derived from the table, never a list
    here).  The settings file's ``system:`` table also holds ``system.agent``, the
    ``auth``/``env``/``secret_path`` families and the bind-shaped categories, none of which
    belong to the path tier; and a ``config:`` table hand-written into a SETTINGS file must
    never reach Layer 1, which lives in ``kanibako.cfg`` alone (spec §1).

    ⚑⚑ AND THE MIRROR OF THAT: a ``system:`` table hand-written into a CONFIG file must
    never reach Layer 2.  The two files each hold exactly one layer —
    *"kanibako_config.yaml <-- cannot have settings. Period."* (Jei, on what is now
    ``kanibako.cfg``) — and since 2026-08-31
    that is a property of the READS rather than of filters applied after them:
    ``bootstrap_config_paths`` walks the ``config:`` table and REFUSES anything else in the
    file, while ``system_path_set_values`` walks the ``system:`` table.  The one filter left
    below is the P13 path-tier selection, which is a different question.
    """
    # ⚑ Lazy import to avoid a config <-> paths import cycle at module load — do not hoist.
    from kanibako.settings.config import (bootstrap_config_paths, config_base_path,
                                          system_path_set_values)
    raw: dict[str, str] = {}

    # base < user; an absent file yields {}, so missing layers are skipped automatically.
    # ⚑⚑ ``config.*`` BY CONSTRUCTION (2026-08-31).  The CONFIG files carry the Layer-1
    # foundation and NOTHING ELSE — Jei: *"kanibako_config.yaml <-- cannot have settings.
    # Period."*  A ``system:`` table hand-written into one used to enter ``raw`` here as a
    # real (if lowest) layer of the Layer-2 path tier, which made the bootstrap file a
    # settings source; then it was dropped in silence; now the read REFUSES it, naming the
    # file and the keys.
    for path in (config_base_path(), user_config_path):
        raw.update(bootstrap_config_paths(path))

    config = resolve_config_paths(raw, data_home=data_home, home=home)
    stored = system_path_set_values(Path(config["config.settings"]))
    raw.update({k: v for k, v in stored.items() if k in SYSTEM_PATH_DEFAULTS})

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
    better. ⚑ Builds its own xdg map (:func:`spec_default_xdg_map` — data/config/state/cache,
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
        from kanibako.settings.config import bootstrap_config_paths, config_base_path

        raw: dict[str, str] = {}
        # ⚑ ``bootstrap_config_paths`` IS the filter this function used to spell inline —
        # it is now the one carrier, shared with ``load_system_config`` (2026-08-26).
        raw.update(bootstrap_config_paths(config_base_path()))
        raw.update(bootstrap_config_paths(config_file_path(ch)))
        resolved = resolve_config_paths(raw, data_home=dh, home=Path.home(),
                                        xdg_vars=spec_default_xdg_map(dh))
        return Path(resolved["config.data"]).name
    except Exception:
        return KANIBAKO_PATH


def load_std_paths(config: BootstrapConfig | None = None) -> StandardPaths:
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


def resolve_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None, *,
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
    primary_group = _default_project_group(std)
    project_toml, workset_toml = _box_settings_files(BoxMode.primary, metadata_path,
                                                     primary_group)
    shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(std, metadata_path,
                                                               project_name or metadata_path.name)
    # enable_vault (P5a): explicit param wins, else THE CASCADE — base < system < workset
    # < box (absent everywhere ⇒ the declared ``True``).
    # ⚑ The workset tier applies HERE TOO.  The primary workset is a workset — spec §2c
    # gives PRIMARY and NAMED the same ``meta.workset.settings`` — so spec §0 "Directional
    # view/set across CONTAINMENT levels" makes a ``box.*`` key stored there an OVERRIDABLE
    # DEFAULT for the boxes it contains.  That it goes live for EVERY default-mode box is
    # what a workset-tier default MEANS, not a reason to drop the tier: this module already
    # honours that same file for ``workset.registry`` (see ``load_primary_boxes``).
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else resolve_box_enable_vault(std.config_file,
                                                          box_path=project_toml,
                                                          workset_path=workset_toml))
    # ⚑ What the create branch PERSISTS is the BOX-AUTHORED value, NOT the resolved one —
    # ``box.enable_vault`` is "sparse — absent from the settings file unless THE USER sets
    # it" (spec ``:868``).  Mirrors the NAMED resolver; see it for the full reasoning.
    box_authored_vault = (enable_vault if enable_vault is not None
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
        project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, primary_group)

        # ⚑ Creation ownership for the unwind below — must be captured BEFORE ``_init_project``
        # merges into the dir, so the unwind never deletes a pre-existing box's ``home/``.
        _dir_existed = project_dir_path.is_dir()

        _init_project(std, metadata_path, shell_path, vault_ro_path,
                      vault_rw_path, project_path, enable_vault=actual_vault_enabled)

        # Sparse create (P8b/Option A): only a NON-default ``box.enable_vault`` is persisted,
        # and only when the BOX authored it (see ``box_authored_vault`` above).
        write_box_enable_vault(project_toml, box_authored_vault)
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
        # P8b/Option A: NO box.yaml backfill — identity lives in the registries now.

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


def _workset_box_paths(metadata_path: Path, vault_ro_base: Path, vault_rw_base: Path,
                       box_name: str) -> tuple[Path, Path, Path]:
    """Fixed NAMED-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    ⚑ The two bases are the RESOLVED ``workset.{vault_ro,vault_rw}`` — one arm each,
    because either may be repointed independently of the other.  Only the per-box
    ``@meta.box.name`` LEAF is composed here; that leaf is the whole per-mode variation.
    """
    shell = metadata_path / HOME_PATH
    return shell, vault_ro_base / box_name, vault_rw_base / box_name


def _standalone_box_paths(root: Path) -> tuple[Path, Path, Path]:
    """Fixed STANDALONE-mode ``(home, vault_ro, vault_rw)`` (no layout axis).

    ⚑ STANDALONE roots a degenerate workset at *root*, so *root*'s own ``workset.yaml``
    is the workset tier and its ``workset.{vault_ro,vault_rw}`` are RESOLVED here — the
    keys are UNIFORM in every mode (§2c ALL PROJECTS, R-29), with no standalone
    carve-out.  Only the BIND differs: a lone box takes the arm itself, no name leaf.
    """
    from kanibako.project.workset import resolve_workset_vault_pair

    home = root / STANDALONE_META_DIR / HOME_PATH
    vault_ro, vault_rw = resolve_workset_vault_pair(root)
    return home, vault_ro, vault_rw


def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path:
    """Per-box, per-mode HOST path for the helper message log (the ``helpers.jsonl`` bind source).

    ⚑⚑ THIS IS THE HUB'S WRITER, and the MOUNT it must agree with is the spec's own
    spelling ``@workset.logs/@{meta.box.name}.jsonl`` (``data/core-defaults.yaml``,
    ``helpers``).  While an arm COMPOSED its directory the two disagreed the moment a
    user repointed ``workset.logs`` — the mount moved and the writer did not (migration
    M-14).  ⚑ ALL THREE arms now RESOLVE the key, so there is one answer in every mode.
    STANDALONE resolves it against the degenerate workset rooted at the project dir,
    whose declared default is ``@meta.box.path`` = ``box_data/`` — the same directory
    the composed form named, now reached through the key that may move it.
    """
    box = proj.name if proj.name else short_hash(proj.project_hash)
    # ⚑ Deferred import: the documented ``settings.paths`` <-> ``project.workset`` cycle.
    from kanibako.project.workset import load_workset_settings_doc, resolve_workset_logs

    if proj.mode is BoxMode.standalone:
        # ``metadata_path`` IS the standalone root (drift I), i.e. the workset root of
        # the degenerate workset — so the key is read from the root ``workset.yaml``.
        root = proj.metadata_path
        return resolve_workset_logs(
            root, load_workset_settings_doc(root), standalone=True) / f"{box}.jsonl"

    if proj.mode is BoxMode.named:
        # The workset root is carried on the project group (root=ws.root).  ⚑ The
        # fallback still assumes the DEFAULT box layout; it is unreachable from
        # ``resolve_workset_project``, which always supplies the group.
        ws_root = proj.group.root if proj.group else proj.metadata_path.parent.parent
        return resolve_workset_logs(
            ws_root, load_workset_settings_doc(ws_root)) / f"{box}.jsonl"
    # PRIMARY: the PRIMARY workset's logs dir — ``std.primary_logs`` is already the
    # RESOLVED ``workset.logs`` of the primary root (:func:`resolve_system_paths`).
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
                 vault_rw_path: Path, project_path: Path, *, enable_vault: bool = True,
                 vault_root: Path) -> None:
    """Shared first-time project setup: create directories, bootstrap shell.

    ⚑⚑ *vault_root* is the workset root that owns the ``vault/`` SKELETON dir, and it is
    REQUIRED because the skeleton is composed off it — ``<vault_root>/vault``, the one
    non-key leaf a workset root carries (``project/workset.py::_VAULT_LEAF``, and the
    same literal ``_lifecycle._to_standalone`` and ``_duplicate`` write into).  Without a
    root there is no skeleton, so there is nothing to answer.
    """
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
        # ⚑⚑ THE SKELETON IS COMPOSED OFF THE ROOT, NEVER POSITIONED OFF A RESOLVED ARM.
        # ``vault_ro_path.parent`` was that position, and ``workset.vault_ro`` is a
        # repointable key, so the parent stopped being the skeleton the moment it moved:
        # ``vault_ro: @meta.workset.path/store/ro`` named ``<root>/store`` — a directory
        # no key gave us — and in PRIMARY the arm carries a ``@meta.box.name`` leaf, so
        # the parent was the ``ro`` arm ITSELF, where an ``rw/`` pattern matches nothing.
        vault_dir = vault_root / VAULT_PATH
        # ⚑ The file CLAIMS ``rw/`` is a child of the skeleton, so it is written only
        # while the RESOLVED ``workset.vault_rw`` really is under it — a repoint out
        # makes the claim false and the file a stray beside a directory the USER named.
        # ⚑ STRICT: an arm pointed AT the skeleton is the user's rw store, not its parent.
        if vault_rw_path != vault_dir and _host_path_within(vault_rw_path, vault_dir):
            gitignore = vault_dir / IGNORE_FILE
            if not gitignore.exists():
                gitignore.write_text("rw/\n")

    print(MSG_DONE, file=sys.stderr)


def _host_path_within(candidate: Path, root: Path) -> bool:
    """True when the HOST path *candidate* is *root* or lies beneath it (no I/O).

    ⚑ DELIBERATELY NOT ``settings.store_collapse.is_within``, and not to be merged with
    it.  That one is a separator-guarded STRING prefix test over GUEST DESTINATION
    spellings, public because the collapse and the delivery half must answer "which
    mount covers this dest" identically.  This is a host ``Path`` containment test, and
    ``settings/paths.py`` is the foundation ``store_collapse`` sits above — importing
    upward for it would invert the layering to reuse a predicate from another domain.
    ⚑ The ``relative_to``/``ValueError`` idiom it wraps is spelled inline four more times
    in this module; those are loop-and-``continue`` shapes and stay as they are.
    """
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _init_project(std: StandardPaths, metadata_path: Path, shell_path: Path, vault_ro_path: Path,
                  vault_rw_path: Path, project_path: Path, *, enable_vault: bool = True) -> None:
    """First-time project setup: create directories, copy credentials from host."""
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault, vault_root=std.primary_workset)


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
                        config: BootstrapConfig) -> DetectionResult:
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
                            config: BootstrapConfig, *, initialize: bool = False,
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
    project_toml, workset_toml = _box_settings_files(BoxMode.named, metadata_path, ws)
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
    shell_path, vault_ro_path, vault_rw_path = _workset_box_paths(
        metadata_path, ws.vault_ro_dir, ws.vault_rw_dir, project_name)
    # enable_vault (P5a): explicit param wins, else THE CASCADE — base < system < workset
    # < box (absent everywhere ⇒ the declared ``True``).
    # ⚑ The workset tier is REQUIRED, not optional: ``workset create --no-vault`` writes
    # ``box.enable_vault`` at the workset tier, and spec §0 "Directional view/set across
    # CONTAINMENT levels" makes a ``box.*`` key stored there an OVERRIDABLE DEFAULT for the
    # boxes the workset contains — the contained scope still wins (spec §2 cascade bracket
    # ``… < workset < box``).  Without it the flag is a silent no-op for every named box.
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else resolve_box_enable_vault(std.config_file,
                                                          box_path=project_toml,
                                                          workset_path=workset_toml))
    # ⚑ What the create branch PERSISTS is the BOX-AUTHORED value, NOT the resolved one.
    # ``box.enable_vault`` is "sparse — absent from the settings file unless THE USER sets
    # it" (spec ``:868``), and setting it at the workset tier is not setting it here.
    # Persisting the inherited default would PIN it, silently converting an overridable
    # workset default into a box-scope override that later workset edits cannot reach.
    box_authored_vault = (enable_vault if enable_vault is not None
                          else read_box_enable_vault(project_toml))

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        # Sparse create (P8b/Option A): only a NON-default ``box.enable_vault`` is persisted,
        # and only when the BOX authored it (see ``box_authored_vault`` above).
        write_box_enable_vault(project_toml, box_authored_vault)
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


def iter_projects(std: StandardPaths, config: BootstrapConfig) -> list[tuple[Path, Path | None]]:
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


def iter_workset_projects(std: StandardPaths, config: BootstrapConfig) -> _WorksetProjectRows:
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
        # ⚑ Hoisted: ``projects_dir`` RESOLVES ``workset.boxes`` off the root
        # workset.yaml, so reading it per member would re-read that file per box and
        # open a window for two members to disagree about the same document.
        boxes_dir = ws.projects_dir
        for proj in ws.projects:
            has_project_dir = (boxes_dir / proj.name).is_dir()
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


def resolve_any_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None,
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


def resolve_box_target(std: StandardPaths, config: BootstrapConfig, value: str | None = None,
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
    # ⚑ The workset-scope kuid goes to the ROOT file, whose write MATERIALIZES the detection
    # marker (``system-design-1.8.0.md`` § "Detection & import").
    from kanibako.settings.config_io import write_nested_key

    write_nested_key(settings_file, ("workset",), "kuid", box_identity.standalone_kuid(box_name))
    if register:
        registry_store.register_standalone(std.registry, box_name, root)
    return box_name, shell_path, vault_ro_path, vault_rw_path


def resolve_standalone_project(std: StandardPaths, config: BootstrapConfig,
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
    # enable_vault (P5a): explicit param wins, else THE CASCADE — base < system < workset
    # < box.  ⚑ The workset tier is LIVE DESIGN, not migration: spec §2c's STANDALONE
    # block declares it — "Box values (box.enable_vault, workset.kuid, …) still resolve
    # from the workset tier @meta.workset.settings as downward defaults when no box file
    # exists."  All three resolvers pass it, for that one reason.
    actual_vault_enabled = (enable_vault if enable_vault is not None
                            else resolve_box_enable_vault(std.config_file,
                                                          box_path=box_settings,
                                                          workset_path=project_toml))

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
    """First-time standalone project setup: all state inside the project dir (vault included).

    ⚑ *metadata_path* is the ``box_data/`` dir; the WORKSET root is its parent, and that
    is what owns the ``vault/`` skeleton the ``.gitignore`` belongs to.
    """
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault, vault_root=metadata_path.parent)
    # The workspace is a SUBDIR of the root (drift H); create the bind source.
    project_path.mkdir(parents=True, exist_ok=True)
