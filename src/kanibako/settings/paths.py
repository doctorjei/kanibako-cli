"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

from kanibako.settings.messages import (PROFILE_CONTENTS, BASHRC_CONTENTS,
                                              SHELL_D_CONTENTS,

                                              STATUS_OK, STATUS_MISSING, STATUS_NO_DATA,
                                              MSG_OTS_KB_INIT, MSG_OTS_WS_PROJ_INIT, MSG_DONE,

                                              WARN_RELATIVE_XDG, WARN_FALLBACK_RT_DIR,
                                              WARN_RUNDIR_UNUSABLE, WARN_WS_NO_ROOT,
                                              WARN_WS_BAD_LOAD, WARN_WS_BOX_BAD_NAME, WARN_SA_SHADOWED_BY_PATH,
                                              WARN_BOX_BAD_KUID, WARN_BOX_NO_VAULT,

                                              ERR_SETTINGS_BAD_PATH, ERR_SETTINGS_BAD_REF,
                                              ERR_CONFIG_NO_FILE, ERR_CONFIG_NULL_PATH_REASON,
                                              ERR_PROJECT_NO_PATH,
                                              ERR_PROJECT_BAD_DESIGNATION,
                                              ERR_PROJECT_NEW_HOME, ERR_PROJECT_REG_HOME,
                                              ERR_PROJECT_NAME_USED,
                                              ERR_PROJECT_PATH_IS_NAMED_BOX,
                                              ERR_WORKSET_NO_PROJECT, ERR_WORKSET_NO_WORKSET,
                                              ERR_WORKSET_WS_NOT_BOX, ERR_WORKSET_NOT_IN_BOX,
                                              ERR_WORKSET_NULL_WORKSPACES)

import os
import shlex
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, NamedTuple, Protocol, overload

from kanibako.identifiers import find_identifier
from kanibako.log import get_logger

from kanibako.settings.config import (WORKSET_META_FILE, BOX_META_FILE, BootstrapConfig, KanibakoConfig,
                                      _system_settings_path, _typed_box_scalar, box_scalar_defaults_floor,
                                      config_file_path, load_config, read_box_enable_vault, read_workset_kuid,
                                      read_workset_skip_kuid_check, write_box_enable_vault)

from kanibako.errors import (AmbiguousNameError, ConfigError, ProjectError,
                             ReservedWorksetNameError, WorksetError)
from kanibako.settings.agent_config import (ambiguous_path_value_error,
                                            is_unambiguous_path_value)
from kanibako.settings.settings_resolve import (LevelView, ResolveCtx, SettingsError,
                                                _Unset, expand_expr, literal_expr,
                                                literal_map, resolve_value)

from kanibako.project.names import (resolve_name, resolve_qualified_name)
from kanibako.launch.box_identity import Designation, classify_designation
from kanibako.utils import literal_path, logical_cwd, project_hash, short_hash
from kanibako.settings.bootstrap import (BASHRC_FILE, CONFIG_PATH_DEFAULTS,
                                         CREDS_WATCHER_LOG_SUFFIX, HOME_PATH,
                                         IGNORE_FILE, KANIBAKO_PATH, KIND_PROJECT, KIND_WORKSET,
                                         PROFILE_FILE, RUN_USER_UID_PATH, SHELL_D_FILE,
                                         SYSTEM_PATH_DEFAULTS,
                                         UNREGISTERED_MARKER, VAULT_PATH, XDG_CACHE_HOME,
                                         XDG_CONFIG_HOME, XDG_DATA_HOME, XDG_RUNTIME_DIR,
                                         XDG_SPEC_DEFAULTS, XDG_STATE_HOME)
from kanibako.settings import bootstrap

#: RE-EXPORT of the path-literal carrier it is defined in.  Consumer:
#: ``commands.box._lifecycle``.
STANDALONE_META_DIR = bootstrap.STANDALONE_META_DIR

if TYPE_CHECKING:
    from kanibako.settings.keystore import KeyStore
    from kanibako.settings.workset_dirkeys import EarlyScope, EarlySystem


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
    # System-level derived dirs: the Layer-1 ``config.*`` foundation + Layer-2 ``system.*``.
    data: Path
    backup: Path
    agents: Path
    channels: Path
    # ``system.template`` — the system TEMPLATE ROOT.  ⚑ The box-HOME seed is
    # ``template/box/home``, NOT the root and NOT ``box/``.
    template: Path
    # ``system.canon`` — this SCOPE'S CANON CONTRIBUTION root (spec §2g), not the assembly.
    # ⚑ ``None`` when a settings file nulls it: it is a STANDARD bind's source key, so the
    # handbook binds collapse (§0/§2a) and no canon store is rooted anywhere.
    canon: Path | None
    settings: Path
    primary_workset: Path
    registry: Path
    # Lifecycle journal — write-ahead log of in-flight box-lifecycle ops (``config.journal``).
    journal: Path
    # ⚑ ``system.cache`` and ``system.state`` are THE host cache and state roots, and the
    # only things a cache or a state store derives from.  Neither has any relationship to
    # ``config.data`` ([R166]).  Do not re-derive either from a leaf.
    cache: Path
    state: Path
    runtime: Path
    # Channels skeleton — keys/defaults only; sub-key wiring is Phase 6.
    # ⚑ ``None`` on the same terms as :attr:`canon`: each is a STANDARD bind's source key,
    # so a null leaf omits that guest mount and the seeder writes nothing there.  The two
    # null INDEPENDENTLY — ``system.channels.broadcast`` is its own key, and an explicit
    # repoint of it still resolves when ``system.channels.chat`` is null (§0).
    channels_common: Path | None
    channels_chat: Path | None
    channels_broadcast: Path | None
    channels_mailboxes: Path | None
    channels_share: Path | None
    # PRIMARY-workset box store: ``@config.primary_workset/boxes`` (per-box meta + shell).
    boxes: Path
    # PRIMARY-workset vault roots.  ⚑ ``None`` when the PRIMARY workset nulls the arm:
    # no such dir, so no vault bind and no per-box vault leaf.
    primary_vault_ro: Path | None
    primary_vault_rw: Path | None
    # ``None`` when the PRIMARY ``workset.logs`` is a present ``<None>``: no logs dir.
    primary_logs: Path | None
    # ⚑ The EARLY SYSTEM TIER as DATA: the system settings file's raw ``workset.*`` values,
    # the resolved ``system.*`` tier, and any ``system:``-table refusal that tolerance
    # dropped.  Read ONCE at this load and carried from here, so an early reader never opens
    # that file again to answer a question this load already settled.
    early_system: EarlySystem


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
    # ⚑ ``None`` under a null ``workset.workspaces`` (Q106): a standalone box, or an
    # in-tree named member.
    project_path: Path | None
    project_hash: str
    metadata_path: Path      # host-only: workset.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    # ⚑ The RESOLVED ``workset.{vault_ro,vault_rw}`` (+ a ``<box-name>`` leaf in primary
    # and named mode) — NOT ``project_path/vault/ro``.
    # ⚑ ``None`` for a nulled arm, as ``StandardPaths.primary_vault_*`` above.
    vault_ro_path: Path | None   # → /home/agent/vault/ro
    vault_rw_path: Path | None   # → /home/agent/vault/rw
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    name: str = field(default="")
    group: ProjectGroup | None = field(default=None)
    _config_path: Path | None = field(default=None, repr=False)
    _enable_vault: bool | None = field(default=None, repr=False)
    #: The scope this project's resolver held — the STANDALONE store answers through
    #: ``workset.boxes``, a repointable key, so it needs the scope that read it.
    _early: "EarlyScope | None" = field(default=None, repr=False)

    def vault_enabled(self) -> bool:
        """Resolve ``box.enable_vault`` only when a consumer needs it."""
        if self._enable_vault is None:
            assert self._config_path is not None
            box_path, workset_path = box_workset_settings_paths(self)
            self._enable_vault = resolve_box_enable_vault(
                self._config_path, box_path=box_path, workset_path=workset_path,
            )
        return self._enable_vault

    def _require_early(self) -> EarlyScope:
        """The scope this project's resolver held; every resolver sets it."""
        assert self._early is not None, "ProjectPaths carries no early scope"
        return self._early


def box_tree_materialized(proj: ProjectPaths) -> bool:
    """True when the box tree a ``create`` would materialize is ALREADY on disk."""
    return box_metadata_dir(proj.mode, proj.metadata_path,
                            early=proj._require_early()).is_dir()


def standalone_box_store(root: Path, *, early: EarlyScope) -> Path:
    """The RESOLVED ``workset.boxes`` of the standalone box rooted at *root* — ITS store.

    ⭐ THE ONE PLACE A STANDALONE STORE PATH IS ANSWERED; every reader and deleter goes
    through here or :func:`box_metadata_dir`, which calls it.  Composing ``box_data/``
    instead names a directory the box never uses.  Deferred import: the paths/workset
    cycle.
    """
    from kanibako.project.workset import load_workset_settings_doc, resolve_workset_boxes

    return resolve_workset_boxes(root, load_workset_settings_doc(root), standalone=True,
                                 early=early)


def standalone_store_teardown_plan(
    root: Path, *, early: EarlyScope,
) -> tuple[Path | None, Path | None]:
    """The standalone box store as ``(removable, retained)`` for a teardown — ONE split.

    ⚑⚑ ONLY A STORE STRICTLY BELOW *root* IS REMOVABLE; anything else is the USER'S OWN
    directory and no verb ``rm -rf``\\ s it on their behalf — the line
    ``standalone_vault_teardown`` draws for a vault arm and ``delete_workset`` for a
    workset store.  BOTH ENDS ARE RESOLVED: the key is answered as it was SPELLED, so
    ``@meta.workset.path/../store`` or a symlinked parent reads as a descendant while
    sitting outside the root.  ``None`` on either arm means nothing to act on.
    """
    from kanibako.project.workset import _path_in_tree

    store = standalone_box_store(root, early=early)
    if not store.is_dir() or store.is_symlink():
        return None, None
    resolved = store.resolve()
    if resolved != root.resolve() and _path_in_tree(resolved, root):
        return resolved, None
    return None, resolved


def report_retained_store(store: Path, root: Path) -> None:
    """Print the retained-store Note: ONE text, naming the STORE — ``report_retained_vault``
    would announce a store as a vault.
    """
    import sys

    print(f"Note: left the box store at {store} in place — not strictly inside {root}, "
          f"so it is yours to remove.", file=sys.stderr)


def _standalone_settings_files(root: Path, *, early: EarlyScope) -> tuple[Path, Path]:
    """The STANDALONE ``(box_tier, workset_tier)`` pair — BOTH always real paths."""
    return standalone_box_store(root, early=early) / BOX_META_FILE, root / WORKSET_META_FILE


def box_metadata_dir(mode: BoxMode, metadata_path: Path, *,
                     early: EarlyScope) -> Path:
    """The DIR holding a box's own metadata — home, session state, box tier.

    ⚑ *early* is read on the STANDALONE arm ONLY — the one that answers the store through
    ``workset.boxes``; the primary/named arm is ``metadata_path`` itself.  Required rather
    than optional-plus-assert, which only fails at runtime on that arm.
    """
    if mode is not BoxMode.standalone:
        return metadata_path
    return standalone_box_store(metadata_path, early=early)


def _box_settings_files(mode: BoxMode, metadata_path: Path,
                        group: "_WorksetRooted | None", *,
                        early: EarlyScope | None = None) -> tuple[Path, Path | None]:
    """THE ``(box_tier, workset_tier)`` settings-file derivation (spec §2c) — spelled ONCE.
    ⚑ The box tier is non-optional BY TYPE; do not widen the return to ``Path | None``.
    ⚑ *group* is anything rooted at ``@meta.workset.path``: a :class:`ProjectGroup` OR a
    :class:`WorksetSpec`, since the NAMED resolver builds its group only at return time."""
    if mode is BoxMode.standalone:
        assert early is not None, "the standalone store is resolved, so it needs its scope"
        return _standalone_settings_files(metadata_path, early=early)
    return metadata_path / BOX_META_FILE, workset_settings_path(group)


def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]:
    """The :class:`ProjectPaths` ADAPTER over :func:`_box_settings_files` (no logic of its own).

    ⚑ Reads the scope off *proj* rather than taking one: the standalone box tier sits in
    the RESOLVED store, so answering it is this seam's job on every mode.  ``getattr``
    because a caller may pass a STAND-IN (a ``SimpleNamespace`` naming the paths it needs)
    that carries no scope — fine, the arm that needs one is STANDALONE.
    """
    return _box_settings_files(proj.mode, proj.metadata_path, proj.group,
                               early=getattr(proj, "_early", None))


def resolve_box_enable_vault(global_path: Path, *, box_path: Path,
                             workset_path: Path | None) -> bool:
    """Resolve ``box.enable_vault`` through its base-to-box cascade."""
    from kanibako.settings.kb_store import __MISSING__
    from kanibako.settings.settings_launch import snapshot_leaf

    snapshot = _narrow_box_scalar_cascade(
        global_path, workset_path=workset_path, box_path=box_path,
    )
    defaults = KanibakoConfig()
    value = snapshot_leaf(snapshot, "box.enable_vault")
    if value is __MISSING__ or value is None:
        return defaults.box_enable_vault
    return bool(_typed_box_scalar(defaults, "box_enable_vault", value))


def _narrow_box_scalar_cascade(
    global_path: Path, *, workset_path: Path | None, box_path: Path | None,
) -> "KeyStore":
    """Build the pre-selection cascade for ``box.enable_vault``."""
    from kanibako.settings.settings_assemble import ReadPurpose, assemble_levels, cascade_files
    from kanibako.settings.settings_merge import merge

    base_levels = assemble_levels(
        agent_name="",
        files=cascade_files(
            purpose=ReadPurpose.NARROW, system_path=_system_settings_path(global_path),
            agent_path=None, workset_path=workset_path, box_path=box_path,
        ),
        floor=box_scalar_defaults_floor(),
    )
    return merge([base_levels[0], base_levels[1], base_levels[4], base_levels[5]])


class _WorksetLike(Protocol):
    """Structural type for the attributes :meth:`WorksetSpec.from_workset` reads (cycle-breaker)."""
    name: str
    root: Path
    is_default: bool

    @property
    def projects_dir(self) -> Path: ...
    @property
    def workspaces_dir(self) -> Path | None: ...
    @property
    def vault_ro_dir(self) -> Path | None: ...
    @property
    def vault_rw_dir(self) -> Path | None: ...
    @property
    def logs_dir(self) -> Path | None: ...
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
    #: The resolved ``workset.workspaces``; ``None`` where the root nulls it (no dir).
    workspaces_dir: Path | None
    #: ⚑ The RESOLVED ``workset.{vault_ro,vault_rw}`` — ONE ARM EACH, never a shared
    #: ``vault/`` parent to join ``ro``/``rw`` onto.  The two are independently
    #: repointable keys, so a single parent cannot answer both.  ``None`` for a nulled arm.
    vault_ro_dir: Path | None
    vault_rw_dir: Path | None
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

#: The temp-root name prefix shared by BOTH last-resort arms (P10 — spelled ONCE, so a
#: reader is never left deciding whether the reused dir and the ``mkdtemp`` dir are one
#: thing or two).  It ends in ``-``, so the reused name is the prefix + the uid.
_RUNTIME_TMP_PREFIX = "kanibako-runtime-"

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

    # Last resort, FIRST arm: a STABLE per-uid dir under the temp root, reused by every
    # later process.  Not silent either way — both arms warn.
    #
    # ⚑ The name buys nothing on its own — any user can create it.  Under a sticky
    # world-writable root, ours is told from theirs by the owner + mode check below.
    stable = Path(tempfile.gettempdir()) / f"{_RUNTIME_TMP_PREFIX}{uid}"
    try:
        stable.mkdir(mode=0o700)
    except FileExistsError:
        # Pre-existing, and therefore NOT ours to chmod — the trust check may adopt it,
        # but nothing here may rewrite its mode.
        pass
    except OSError:
        # Unusable temp root, or a non-directory planted in the way (ENOTDIR).  Fall
        # through: the check below will refuse it too, and the mkdtemp arm answers.
        pass
    else:
        # We just created it, so this is the ONE path we may chmod.  `mkdir`'s mode is
        # masked by the umask, and a umask that strips owner bits leaves a dir the check
        # below would refuse (or that we could not write) — so re-assert 0700 here.
        stable.chmod(0o700)
    if _runtime_base_usable(stable, follow_symlinks=False, require_private=True):
        logger.warning(WARN_RUNDIR_UNUSABLE, var_name, uid, stable, var_name)
        _runtime_fallback_cache[cache_key] = stable
        return stable

    # Last resort, SECOND arm: a fresh 0700 temp dir, exactly as before.  A stable name
    # we could not trust is worse than a name nobody else can predict.
    chosen = Path(tempfile.mkdtemp(prefix=_RUNTIME_TMP_PREFIX))
    chosen.chmod(0o700)
    logger.warning(WARN_RUNDIR_UNUSABLE, var_name, uid, chosen, var_name)
    _runtime_fallback_cache[cache_key] = chosen
    return chosen


def _runtime_base_usable(base: Path, *, follow_symlinks: bool = True,
                         require_private: bool = False) -> bool:
    """True iff *base* is a directory we own and can write to (any OS error ⇒ not usable).

    ⚑ THE OWNER TEST IS SPELLED ONCE, HERE.  Both callers — the ``/run/user`` base and
    the stable temp dir — reach it through this function, because a second copy of
    "do we own it" is how two arms drift apart on the one check that decides whether
    kanibako will write into a directory somebody else may also reach.

    ⚑ *follow_symlinks* and *require_private* are keyword-only and default to exactly
    what the ``/run/user`` call has always got: ``stat``, which follows a link, and no
    mode test.  That call site passes NEITHER, so its behavior is unchanged.  The
    stable temp dir is a different trust problem — a PREDICTABLE name in a
    world-writable root — so it passes both: ``lstat`` so a planted symlink is refused
    as a symlink instead of being read through to its target, and a mode test so a
    group- or other-accessible directory is refused.
    """
    try:
        st = base.stat() if follow_symlinks else os.lstat(base)
    except OSError:
        return False
    import stat as _stat

    if not follow_symlinks and _stat.S_ISLNK(st.st_mode):
        return False
    if not _stat.S_ISDIR(st.st_mode):
        return False
    if st.st_uid != os.getuid():
        return False
    if require_private and st.st_mode & 0o077:
        return False
    return os.access(base, os.W_OK | os.X_OK)


def xdg(env_var: str, default_suffix: str) -> Path:
    """Backward-compatible thin wrapper over :func:`resolve_xdg` for plain XDG base dirs."""
    return resolve_xdg(env_var, default_suffix)


def user_config_home() -> Path:
    """The XDG config base dir — the ONE internal resolver for directory users ([R154]).

    ⚑ NOT A KEY: [R154] refuses a companion ``config_home`` key by name — 18 sites use
    this value as a DIRECTORY, two outside kanibako's tree entirely (VS Code's
    ``settings.json``, the persona-grata root), so they route through here instead.
    """
    return xdg(XDG_CONFIG_HOME, XDG_SPEC_DEFAULTS[XDG_CONFIG_HOME])


def spec_default_xdg_map(data_home: Path | None) -> dict[str, str]:
    """The XDG vars that HAVE a spec default (data/config/state/cache) — no ``XDG_RUNTIME_DIR``.

    ⚑ Side-effect-free: unlike ``XDG_RUNTIME_DIR`` (see :func:`_fallback_runtime_dir`), none of
    these four ever mkdir a fallback dir — :func:`resolve_data_path` relies on that to stay total.
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


#: The ``system.*`` keys :func:`load_std_paths` SUBSCRIPTS, so a missing one is a ``KeyError``
#: rather than a ``None``.  Every OTHER key in the table it reads with ``.get``, so an ABSENT
#: one is a value the consumer can hold.
SUBSCRIPTED_SYSTEM_PATH_KEYS: frozenset[str] = frozenset({
    "system.backup",
    "system.channelroot",
    "system.template",
    "system.cache",
    "system.state",
    "system.runtime",
})


def _refused_null_path_value_error(key: str, default: str, *,
                                    referent: "str | None" = None) -> str:
    """THE null-path refusal for a path key that REFUSES a ``<None>`` (spec §2a).

    ⚑ ONE CARRIER, TWO ROADS IN.  A key can be nulled by a stored ``null`` or by a
    DERIVED one — an embedded reference to a present ``<None>`` (spec §0) — and the launch
    gives a null path key no meaning either way, so both raise THIS.  *referent* names the
    key the value pointed at, when the null arrived that way: it is the line the user must
    read, since their own line holds a reference and not a null.

    The reason is ``ERR_CONFIG_NULL_PATH_REASON``, shared with every other null-path door.
    """
    via = (f" Its value references {referent}, which is null, so the value is null too."
           if referent else "")
    return (
        f"{key} is set to <None>, which is not a path. {ERR_CONFIG_NULL_PATH_REASON}"
        f"{via} Delete the line to take the default ({default}), or set a path."
    )


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
    ⚑ A PRESENT ``<None>`` IS REFUSED BEFORE THE STRINGIFY, not by it.  ``str(None)``
    is the word ``"None"``, which is a bare relative, so the null used to reach the
    user as *"``config.data`` is set to ``'None'``"* — a message quoting a string
    nobody wrote.  [R177]/§2h: a present ``<None>`` is a value that does NOT fall
    back to the key's default, so the cure named here is the real one.
    """
    if raw is None:
        raise SettingsError(_refused_null_path_value_error(key, default))
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


def resolve_config_paths(set_values: Mapping[str, str | None], *, data_home: Path,
                         home: Path,
                         xdg_vars: Mapping[str, str] | None = None) -> dict[str, str]:
    """Resolve the Layer-1 CONFIG-key foundation to concrete host paths (flat by design).

    ⚑ *xdg_vars*, when given, REPLACES the live :func:`host_xdg_map` build — the seam
    :func:`resolve_data_path` uses to resolve ``config.data`` without touching
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


def _resolve_system_path_keys(set_values: Mapping[str, str | None], keys: Iterable[str], *,
                              data_home: Path, home: Path, xdg_vars: Mapping[str, str],
                              ) -> tuple[dict[str, str], dict[str, Path]]:
    """Resolve *keys* of the Layer-2 ``system.*`` table over the Layer-1 foundation.

    Returns the Layer-1 resolve and the requested Layer-2 paths, as ``(config, resolved)``.

    ⚑ SPLIT OUT FOR THE CALLER THAT MUST CREATE NOTHING.  :func:`resolve_system_paths` is
    the whole-table caller and builds :func:`host_xdg_map`; :func:`resolve_state_path`
    needs one key and passes :func:`spec_default_xdg_map`, which never resolves
    ``XDG_RUNTIME_DIR`` (whose fallback can mkdir and warn).  The *xdg_vars* argument is
    what lets the two share this resolve instead of keeping a copy each.
    ⚑ *keys* narrows what is RETURNED, not what is REACHABLE: the ``@``-ref lookup below
    still sees the whole table, so a stored ``@system.<other>`` resolves for a one-key
    caller exactly as it does for the full pass.
    """
    # Split the merged set-values by layer prefix.  ⚑ ``config.*`` is not narrowed to text
    # here: a ``None`` on the Layer-2 side is a bind SOURCE (the omission §2a asks for),
    # and the one place that could mistake it for a path is Layer 1, which says so.
    config_set = {k: v for k, v in set_values.items() if k.startswith("config.")}
    system_set = {k: v for k, v in set_values.items() if k.startswith("system.")}

    # Layer 1: resolve the config-key foundation first (chicken-and-egg).
    config = resolve_config_paths(config_set, data_home=data_home, home=home, xdg_vars=xdg_vars)

    ctx = ResolveCtx(agent_name=None, workset_name=None,
                     host_home=str(home), xdg=dict(xdg_vars), config=config)
    levels = [LevelView("system", values=system_set, defaults=SYSTEM_PATH_DEFAULTS)]

    # ⚑ §0's EMBEDDED-REF RULE, made EXPRESSIBLE in a ``-> str`` lookup.
    # ``expand_expr``'s lookup is typed ``Callable[..., str]``, so a present-``<None>``
    # referent cannot travel back through it AS a null — it arrives as the WORD "None".
    # This cell is how the null travels instead: ``lookup`` records the ref, and the key
    # being resolved is then omitted whole, which is what §0 says an embedded reference
    # to a present ``<None>`` means.  ⭐ MEASURED, not hypothetical: without it
    # ``system.channels.broadcast`` (``@system.channels.chat/broadcast.md``) resolved to
    # the path ``None/broadcast.md`` under a null chat, and the chat seeder created that
    # directory in the CWD.  ⚑ REBOUND PER KEY below — a null referent nulls only the
    # value that NAMES it, never a sibling that happens to resolve after it.
    nulled: set[str] = set()

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
        if rv.value is None:
            nulled.add(ref)
            return ""
        # system.* config paths are always scalar strings; narrow the ``object``-typed value.
        return expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain)

    # Layer 2 system path keys, resolving ``@config.*`` via the foundation.
    resolved: dict[str, Path] = {}
    # ⚑⚑ THE ASYMMETRY, IN ONE PLACE, BECAUSE IT IS ONE DECISION AND NOT TWO.
    # ``system.channels.broadcast: null`` WRITTEN is REFUSED — it is not a standard-bind
    # SOURCE, so ``refuses_null_path_key`` claims it.  The same key nulled DERIVED, by its
    # own value's reference to a null key, is OMITTED.  The difference is not the key but
    # WHICH ROAD the null arrived by: a written null is a claim about THIS key and the launch
    # gives it no meaning, while a derived null is a CONSEQUENCE of a claim about a different
    # one (``system.channels.chat``, whose own default broadcasts through it), and that
    # consequence is the omission §2a asks for — the broadcast log belongs to the chat it
    # derives from, so a null chat takes it along.  Refusing the consequence would make the
    # chat key's semantics unreachable one layer up, and chat is the key a user can act on.
    # ⚑ THE DERIVED ROAD'S DISCRIMINATOR IS THE CONSUMER, though — :data:`SUBSCRIPTED_SYSTEM_PATH_KEYS`
    # and the arm below — because a key ``load_std_paths`` subscripts has nowhere to put an
    # absence.  A reader who meets both is looking at these lines, not at a bug.
    from kanibako.settings.config import refuses_null_path_key

    for key in keys:
        nulled = set()
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(ERR_SETTINGS_BAD_PATH % ("system", key))
        if rv.value is None and not refuses_null_path_key(key):
            # ⚑ A NULL THE DOORS ADMIT IS OMITTED, NOT RESOLVED (spec §2a): it is a bind's
            # SOURCE key, so the omission is what collapses the bind (§0).  ⚑ AND ONLY
            # THAT ONE — a key the doors still REFUSE must reach
            # :func:`_refuse_bare_relative` below, which carries the [R177] message that
            # names a present ``<None>`` as ``<None>``.  Omitting those too would turn a
            # refusal the user is told about into a silent one, and the membership is the
            # doors' own so the two cannot disagree about which is which.
            continue
        _refuse_bare_relative(key, rv.value, SYSTEM_PATH_DEFAULTS[key], ctx=ctx, lookup=lookup)
        expanded = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
        if nulled:
            # ⚑ §0: an embedded reference to a present ``<None>`` makes the whole value
            # ``<None>`` — the DERIVED null.  A key the CONSUMER SUBSCRIPTS must be omitted
            # from nowhere (:func:`load_std_paths` reads those by key, so dropping one turns
            # a value the user wrote into a raw ``KeyError`` there); every other key is read
            # with ``.get``, so the resolved table being keyed by what RESOLVED is exactly
            # what a ``<None>`` means and the key is simply absent.
            if key not in SUBSCRIPTED_SYSTEM_PATH_KEYS:
                continue
            raise SettingsError(_refused_null_path_value_error(
                key, SYSTEM_PATH_DEFAULTS[key],
                referent=sorted(nulled)[0],
            ))
        resolved[key] = Path(expanded)
    return config, resolved


def _resolve_system_tier(set_values: Mapping[str, str | None], *, data_home: Path, home: Path,
                         system_refusal: str | None = None,
                         ) -> tuple[dict[str, Path], EarlySystem]:
    """Resolve the whole path tier AND build the early-system record from the SAME inputs.

    The single implementation; :func:`resolve_system_paths` and :func:`load_system_tier` are
    its two projections.  *system_refusal* is the text tolerance dropped upstream
    (:func:`_path_tier_set_values`); a direct caller passes none, because no tolerance ran.
    """
    config, resolved = _resolve_system_path_keys(set_values, SYSTEM_PATH_DEFAULTS,
                                                 data_home=data_home, home=home,
                                                 xdg_vars=host_xdg_map(data_home))
    # Layer 1 foundation paths are surfaced under their ``config.*`` keys.
    for key, val in config.items():
        resolved[key] = Path(val)

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
    # ⚑ KEYED BY NON-KEY NAMES (``_primary_*``, no dotted root), deliberately.  These are
    # internal bookkeeping, not settings: the old ``system._*`` spellings are recorded as
    # ``not_keys.code_residue`` in the manifest, and a key-shaped name here is a stray in
    # the CLOSED keyspace (spec §0).  A consumer filtering this table by ``config.`` /
    # ``system.`` prefix never sees them.
    # ⚑ Deferred import: the documented ``settings.paths`` <-> ``project.workset`` cycle.
    # ⚑ The record is built HERE, after ``system.*`` resolved, so it carries the RESOLVED tier
    # rather than a second read of the file, and the primary-root reads below take it.
    from kanibako.channels.channels import WS_TOKEN_PRIMARY
    from kanibako.settings.workset_dirkeys import EarlyScope, early_system

    record = early_system(set_values, resolved, system_refusal=system_refusal)
    early = EarlyScope(record, WS_TOKEN_PRIMARY)
    pw = resolved["config.primary_workset"]
    from kanibako.project.workset import (load_workset_settings_doc, resolve_workset_boxes,
                                          resolve_workset_logs, resolve_workset_vault_ro,
                                          resolve_workset_vault_rw)

    pw_settings = load_workset_settings_doc(pw)
    resolved["_primary_boxes"] = resolve_workset_boxes(pw, pw_settings, early=early)
    # ⚑ Each arm is OMITTED when nulled, as ``_primary_logs`` below.
    primary_vault_ro = resolve_workset_vault_ro(pw, pw_settings, early=early)
    if primary_vault_ro is not None:
        resolved["_primary_vault_ro"] = primary_vault_ro
    primary_vault_rw = resolve_workset_vault_rw(pw, pw_settings, early=early)
    if primary_vault_rw is not None:
        resolved["_primary_vault_rw"] = primary_vault_rw
    # ⚑ ``_primary_logs`` is OMITTED when ``workset.logs`` is a present ``<None>`` — the
    # table holds paths only; :func:`load_std_paths` reads the omission as ``None``.
    primary_logs = resolve_workset_logs(pw, pw_settings, early=early)
    if primary_logs is not None:
        resolved["_primary_logs"] = primary_logs
    return resolved, record


def resolve_system_paths(set_values: Mapping[str, str | None],
                         *, data_home: Path, home: Path) -> dict[str, Path]:
    """Resolve the path tier (Layer-1 ``config.*`` + Layer-2 ``system.*``) to concrete host paths."""
    return _resolve_system_tier(set_values, data_home=data_home, home=home)[0]


def host_config_map(std: StandardPaths) -> dict[str, str]:
    """THE single builder for the ``config=`` argument of every host-side ``ResolveCtx``.

    The Layer-1 CONFIG-key foundation projected BACK onto its own dotted key names, so
    a stored ``@config.*`` source resolves at launch; each value is the resolved path as a
    :func:`~kanibako.settings.settings_resolve.literal_expr`.  The Layer-1 twin of
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
    return literal_map(
        {key: str(getattr(std, key.split(".", 1)[1])) for key in CONFIG_PATH_DEFAULTS}
    )


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


def system_path_floor(std: StandardPaths) -> dict[str, str | None]:
    """The RESOLVED Layer-2 ``system.*`` path tier, keyed by its own dotted key names.

    Every consumer folds this into a floor so a stored ``@system.*`` source resolves.
    Each value is the corresponding ``std`` attribute as a
    :func:`~kanibako.settings.settings_resolve.literal_expr` — the same flat foundation
    resolves both — so an ``@``-ref-routed bind is byte-identical to a runtime-probed
    literal.

    ⭐ A NULL KEY IS A REAL ``None`` HERE, and that is the whole point of the map: a
    STANDARD bind's source is an ``@``-ref into it, so a present ``<None>`` makes the
    embedded reference ``<None>`` (spec §0) and the bind COLLAPSES — the omission §2a
    asks for.  A ``"None"`` string would instead name a directory called ``None``.

    ⚑⚑ ONE CARRIER, AND IT IS ONE BECAUSE TWO HAD ALREADY DRIFTED — in BOTH directions.
    ``settings_launch.resolve_inputs`` (the launch snapshot) and
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

    ⚑ RESERVED AND REACHABLE ARE ORTHOGONAL, and ``system.backup`` is the one left with
    no consumer: *reserved* is a fact about CONSUMERS, this floor is a fact about the
    KEYSTORE, and a reserved key still answers.  No discriminator is needed because the
    question was never asked.

    ⚑ CONSUMERS CHECKED IN THE SAME CHANGE — both, and both take the whole map:
    ``settings_launch.resolve_inputs`` (three more scalars in the snapshot floor,
    folded by the last-wins arm of ``_merge_default_categories`` — no category key, no
    origin claim, no refusal, and the keys are declared so ``_refuse_undeclared_snapshot``
    is silent) and ``commands/workset_cmd._print_effective_shares`` (``workset share list
    --effective``, which folds the map into a resolve floor and PRINTS bindings, never the
    floor itself).  ``tests/test_channels/test_system_channel_keys.py`` carried the
    by-name pin on the omission; it is INVERTED, not deleted.
    """
    return {
        key: (None if (value := getattr(std, _floor_field(key))) is None
              else literal_expr(str(value)))
        for key in SYSTEM_PATH_DEFAULTS
    }


def layer1_set_values(user_config_path: Path) -> dict[str, str]:
    """The Layer-1 ``config.*`` SET-VALUES: the ``/etc`` config base < *user_config_path*.

    An absent file yields ``{}``, so a missing layer is skipped.  ⚑⚑ ``config.*`` BY
    CONSTRUCTION (2026-08-31).  The CONFIG files carry the Layer-1 foundation and NOTHING
    ELSE — Jei: *"kanibako_config.yaml <-- cannot have settings. Period."*  The read
    (``bootstrap_config_paths``) REFUSES a ``system:`` table hand-written into one, naming
    the file and the keys.
    """
    # ⚑ Lazy import to avoid a config <-> paths import cycle at module load — do not hoist.
    from kanibako.settings.config import bootstrap_config_paths, config_base_path

    raw: dict[str, str] = {}
    for path in (config_base_path(), user_config_path):
        raw.update(bootstrap_config_paths(path))
    return raw


def _path_tier_set_values(user_config_path: Path, *, data_home: Path, home: Path,
                          xdg_vars: Mapping[str, str],
                          tolerate_bad_settings: bool = False,
                          ) -> tuple[dict[str, str | None], str | None]:
    """The path tier's merged SET-VALUES, and the text of a dropped ``system:`` refusal.

    Returns ``(set_values, system_refusal)``.  The set-values carry the Layer-1 ``config.*``
    base, the Layer-2 ``system.*`` path tier, AND the early ``workset.*`` tier — the early keys
    ride in the SAME mapping so :func:`resolve_system_paths` needs no extra parameter and no
    second read of the settings file.  ``system_refusal`` is the text of a ``system:``-table
    refusal that tolerance dropped, else ``None``.

    ⚑⚑ THE SETTINGS FILE IS THE TOP LAYER, AND IT IS THE WHOLE POINT OF THE THIRD
    ``update`` BELOW.  ``system.{template,canon,runtime,cache,backup,channelroot}`` and
    ``system.channels.*`` are Layer-2 SETTINGS keys (spec §2g: "set in settings files at
    the ``system`` cascade level"), and ``config set system.canon=…`` writes them to
    ``@config.settings`` — the tier that must reach them, or a repoint is accepted,
    persisted, and half-effective.  A settable key whose set does not reach the thing it
    names is worse than a refusal, because it never confesses.

    ⚑ THE LAYER-1 RESOLVE RUNS TWICE ON PURPOSE, and it is not a wasted read: locating the
    settings file IS ``@config.settings``, so the foundation must resolve before the file
    can be opened.  The second pass is the caller's own, over the values returned here;
    :func:`resolve_config_paths` is a pure dict resolve over set-values already in hand, so
    it reopens nothing.
    ⚑ *xdg_vars* is the caller's map, passed straight to that first resolve — the seam
    :func:`resolve_state_path` needs to stay free of the ``XDG_RUNTIME_DIR`` fallback.

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
    file, while ``system_table_set_values`` walks the ``system:`` table.  The one filter left
    below is the P13 path-tier selection, which is a different question.

    ⚑⚑ *tolerate_bad_settings* SPLITS IN TWO ARMS, because the one ``try`` it used to be
    covered both cases with one answer, and the two cases must NOT have the same answer.  The
    CONFIG files above are still read strictly either way.
      1. **The document does not load** — ``load_doc`` refuses it: invalid YAML, a duplicate
         key, not a mapping.  Nothing in the file is readable, so tolerance drops BOTH the
         ``system:`` table AND the early tier.  This is the only arm that empties the tier,
         and it is what E2 review finding 2 pins: ``stop`` degrades on a malformed system
         file as base did.
      2. **The document loads, but its ``system:`` table is refused** — a null ``system.*``
         path.  Tolerance drops ONLY the ``system:`` set-values, so ``system.*`` takes its
         defaults as it always has, and KEEPS the early tier, which was read from the loaded
         document BEFORE this read.  The refusal text is returned so it can ride in
        :class:`~kanibako.settings.workset_dirkeys.EarlySystem` rather than vanish.
    """
    # ⚑ Lazy imports to avoid a config <-> paths import cycle at module load — do not hoist.
    from kanibako.settings.config import system_table_set_values
    from kanibako.settings.config_io import load_doc
    from kanibako.settings.workset_dirkeys import early_tier

    layer1 = layer1_set_values(user_config_path)
    config = resolve_config_paths(layer1, data_home=data_home, home=home, xdg_vars=xdg_vars)
    settings_path = Path(config["config.settings"])
    # ⚑ The merged mapping widens to ``str | None`` because the EARLY tier carries a present
    # null as ``None``.  Layer 1 never holds one, so it stays ``str`` on its own.
    merged: dict[str, str | None] = dict(layer1)

    # ⚑ ONE OPEN feeds BOTH tiers.  The early tier is taken from the loaded document BEFORE
    # the ``system:`` table is read, because the two have different tolerance arms and the
    # early keys must survive the one the table does not.
    try:
        doc = load_doc(settings_path)
    except ConfigError:
        # ARM 1 — the document does not load.  Nothing here is readable.
        if not tolerate_bad_settings:
            raise
        return merged, None

    tier = early_tier(doc)
    try:
        stored = system_table_set_values(settings_path, doc)
    except ConfigError as exc:
        # ARM 2 — the document loaded; its ``system:`` table is refused.  Keep the early tier.
        if not tolerate_bad_settings:
            raise
        merged.update(tier)
        return merged, str(exc)

    merged.update({k: v for k, v in stored.items() if k in SYSTEM_PATH_DEFAULTS})
    merged.update(tier)
    return merged, None


def load_system_tier(user_config_path: Path, *, data_home: Path, home: Path,
                      tolerate_bad_settings: bool = False,
                      ) -> tuple[dict[str, Path], EarlySystem]:
    """Resolve the whole path tier AND return the early-system record, from the files that set it.

    THE ONE loader.  :func:`load_system_config` is this projected to the resolved tier, so the
    two can never disagree about what the files said.
    """
    raw, system_refusal = _path_tier_set_values(
        user_config_path, data_home=data_home, home=home,
        xdg_vars=host_xdg_map(data_home), tolerate_bad_settings=tolerate_bad_settings,
    )
    return _resolve_system_tier(raw, data_home=data_home, home=home,
                               system_refusal=system_refusal)


def load_system_config(user_config_path: Path, *, data_home: Path, home: Path,
                       tolerate_bad_settings: bool = False) -> dict[str, Path]:
    """Resolve the whole path tier to concrete host paths, from the files that set it.

    :func:`load_system_tier` projected to the resolved tier; use that one when the
    early-system record is wanted as well.
    """
    return load_system_tier(user_config_path, data_home=data_home, home=home,
                           tolerate_bad_settings=tolerate_bad_settings)[0]


def resolve_data_path(*, config_home: Path | None = None,
                      data_home: Path | None = None) -> Path:
    """The resolved ``config.data`` DIRECTORY — PURE and TOTAL; creates nothing, never raises.

    Resolves ``config.data`` fresh from the host CONFIG file set (base < user, the same
    Layer-1 foundation :func:`load_system_config` reads), so a caller holding no
    :class:`StandardPaths` reaches the directory the user CONFIGURED instead of composing the
    XDG data base with a hardcoded ``kanibako`` leaf ([R155]: a kanibako subdirectory
    references ``config.data``; it is never composed from the XDG base).
    Unlike ``load_std_paths``, it needs no config file.

    ⚑ TOTAL: any failure to read or resolve config — the file is absent, unreadable, or
    malformed YAML, or a stored expression fails to resolve — degrades to
    ``data_home / KANIBAKO_PATH``, matching ``CONFIG_PATH_DEFAULTS["config.data"]``'s own
    default. An absent/unreadable config is exactly what every caller computed when the leaf
    was hardcoded, so this can never be worse; a readable config makes it strictly better.
    ⚑ Builds its own xdg map (:func:`spec_default_xdg_map` — data/config/state/cache,
    deliberately NOT ``host_xdg_map``) rather than the full Layer-1 resolve's usual map: resolving
    ``XDG_RUNTIME_DIR`` can mkdir a fallback dir when unset, and this function must create
    nothing. The one case that misses: a hand-edited config expression referencing
    ``$XDG_RUNTIME_DIR`` (no shipped default does) degrades to the default rather than
    resolving it — an acceptable trade for staying total and side-effect-free.
    """
    ch = config_home if config_home is not None else user_config_home()
    dh = data_home if data_home is not None else xdg(XDG_DATA_HOME,
                                                      XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    try:
        # ⚑ Lazy import to avoid a config <-> paths import cycle at module load — do not hoist
        # (mirrors load_system_config's own deferral).
        from kanibako.settings.config import bootstrap_config_paths, config_base_path

        raw: dict[str, str] = {}
        # ⚑ ``bootstrap_config_paths`` is the one filter, shared with ``load_system_config``.
        raw.update(bootstrap_config_paths(config_base_path()))
        raw.update(bootstrap_config_paths(config_file_path(ch)))
        resolved = resolve_config_paths(raw, data_home=dh, home=Path.home(),
                                        xdg_vars=spec_default_xdg_map(dh))
        return Path(resolved["config.data"])
    except Exception:
        return dh / KANIBAKO_PATH


def total_standalone_early() -> "EarlyScope":
    """The STANDALONE :class:`EarlyScope` for a reader that holds no ``StandardPaths``.

    :func:`resolve_data_path`'s contract, for the tier rather than the path: PURE and
    TOTAL — creates nothing, never raises.  ``load_std_paths`` REQUIRES a config file and
    the plugin scan runs where it is unavailable, so this degrades to the same tier an
    unreadable config yields everywhere else: never worse than the composed default.
    """
    from kanibako.channels.channels import WS_TOKEN_STANDALONE
    from kanibako.settings.workset_dirkeys import EarlyScope, EarlySystem

    dh = xdg(XDG_DATA_HOME, XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    layer1 = config_file_path(user_config_home())
    try:
        _, record = load_system_tier(layer1, data_home=dh, home=Path.home())
        return EarlyScope(record, WS_TOKEN_STANDALONE)
    except Exception:
        # *layer1* is the file whose load failed; the refusal names it.
        return EarlyScope(
            EarlySystem(tier={}, file=layer1, system_paths={}),
            WS_TOKEN_STANDALONE,
        )


def resolve_state_path(*, config_home: Path | None = None,
                       data_home: Path | None = None) -> Path:
    """The resolved ``system.state`` DIRECTORY — PURE and TOTAL; creates nothing, never raises.

    The state-base sibling of :func:`resolve_data_path`, for a caller holding no
    :class:`StandardPaths` that must still land in the state root the user configured
    ([R166]: every state store derives from ``system.state``, and from nothing else).
    :func:`kanibako.vscode.vscode_remote._vscode_remote_state_dir` and
    :func:`kanibako.browser_state.state_path` are those callers.

    ⚑ IT READS ONE FILE MORE THAN :func:`resolve_data_path` DOES, and that is the key's
    layer talking: ``system.state`` is Layer 2, so ``system set system.state=…`` writes to
    the SETTINGS file, and a resolve that stopped at the CONFIG files would miss every
    value a user ever set.
    ⚑ TOTAL: any failure to read or resolve — either file absent, unreadable or malformed,
    or a stored expression that fails to resolve — degrades to ``$XDG_STATE_HOME`` joined to
    ``KANIBAKO_PATH``, matching ``SYSTEM_PATH_DEFAULTS["system.state"]``'s own default.
    ⚑ Side-effect-free by :func:`resolve_data_path`'s route, for the same reason: the
    ``xdg`` map is :func:`spec_default_xdg_map`, which never resolves ``XDG_RUNTIME_DIR``
    (whose fallback can mkdir a directory and warn).  ONE case misses because of it — a
    ``system.state`` a user stored as an expression over ``$XDG_RUNTIME_DIR`` degrades to
    the default instead of resolving — the same trade, made the same way.
    """
    ch = config_home if config_home is not None else user_config_home()
    dh = data_home if data_home is not None else xdg(XDG_DATA_HOME,
                                                      XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    xdg_vars = spec_default_xdg_map(dh)
    try:
        raw, _system_refusal = _path_tier_set_values(config_file_path(ch), data_home=dh,
                                                     home=Path.home(), xdg_vars=xdg_vars)
        _, resolved = _resolve_system_path_keys(raw, ("system.state",), data_home=dh,
                                                home=Path.home(), xdg_vars=xdg_vars)
        return resolved["system.state"]
    except Exception:
        return Path(xdg_vars[XDG_STATE_HOME]) / KANIBAKO_PATH


def resolve_cache_path(*, config_home: Path | None = None,
                       data_home: Path | None = None) -> Path:
    """The resolved ``system.cache`` DIRECTORY — PURE and TOTAL; creates nothing, never raises.

    The cache-base sibling of :func:`resolve_state_path`, for a caller holding no
    :class:`StandardPaths` that must still land in the cache root the user configured
    (``system.cache`` and ``system.state`` are THE host cache and state roots, and the
    only things a cache or a state store derives from — neither has any relationship
    to ``config.data``).
    :func:`kanibako.vscode.vscode_remote.vscode_remote_bin_dir` is that caller: the
    generated dispatch wrapper is regenerable output, so it lives under the cache
    root rather than in the user-meaningful data store.

    ⚑ Same layer as :func:`resolve_state_path`: ``system.cache`` is Layer 2, so
    ``system set system.cache=…`` writes to the SETTINGS file, and a resolve that
    stopped at the CONFIG files would miss every value a user ever set.
    ⚑ TOTAL: any failure to read or resolve degrades to ``$XDG_CACHE_HOME`` joined
    to ``KANIBAKO_PATH``, matching ``SYSTEM_PATH_DEFAULTS["system.cache"]``'s own
    default. Side-effect-free by :func:`resolve_data_path`'s route, for the same
    reason (the ``xdg`` map is :func:`spec_default_xdg_map`, which never resolves
    ``XDG_RUNTIME_DIR``).
    """
    ch = config_home if config_home is not None else user_config_home()
    dh = data_home if data_home is not None else xdg(XDG_DATA_HOME,
                                                      XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    xdg_vars = spec_default_xdg_map(dh)
    try:
        raw, _system_refusal = _path_tier_set_values(config_file_path(ch), data_home=dh,
                                                     home=Path.home(), xdg_vars=xdg_vars)
        _, resolved = _resolve_system_path_keys(raw, ("system.cache",), data_home=dh,
                                                home=Path.home(), xdg_vars=xdg_vars)
        return resolved["system.cache"]
    except Exception:
        return Path(xdg_vars[XDG_CACHE_HOME]) / KANIBAKO_PATH


def load_std_paths(config: BootstrapConfig | None = None, *,
                   tolerate_bad_settings: bool = False) -> StandardPaths:
    """Compute all standard kanibako directories, resolving them ONLY.

    ⚑ RESOLVE-ONLY: this function creates NOTHING.  An ``Ensure directories
    exist`` block stood here ``mkdir``-ing ``config_file.parent``,
    ``config.data``, ``system.state`` and ``system.cache`` on the common path,
    so one stored-but-unusable value (an unwritable path, a file where a
    directory belongs) bricked EVERY command behind a raw ``OSError`` —
    including the ``reset`` that would have un-stored it.  Every one of those
    stores already materializes at its own point of use (the atomic writer,
    first-run init, the cache and state writers), so the eager creation bought
    nothing and the recovery cost everything.
    """
    config_home = user_config_home()
    data_home = xdg(XDG_DATA_HOME, XDG_SPEC_DEFAULTS[XDG_DATA_HOME])
    state_home = xdg(XDG_STATE_HOME, XDG_SPEC_DEFAULTS[XDG_STATE_HOME])
    cache_home = xdg(XDG_CACHE_HOME, XDG_SPEC_DEFAULTS[XDG_CACHE_HOME])

    config_file = config_file_path(config_home)

    if config is None:
        if not config_file.exists():
            raise ConfigError(ERR_CONFIG_NO_FILE % config_file)
        config = load_config(config_file)

    # Resolve the system-level path tier from the CONFIG file set: /etc base < user-global.
    # ⚑ ``load_system_tier``, not ``load_system_config``: this load is the ONE place the
    # settings file is read, and the early tier it carries rides out on the record below.
    resolved, early = load_system_tier(config_file, data_home=data_home, home=Path.home(),
                                       tolerate_bad_settings=tolerate_bad_settings)
    data_path = resolved["config.data"]

    return StandardPaths(config_home=config_home, data_home=data_home, state_home=state_home,
                     cache_home=cache_home, config_file=config_file, data_path=data_path,
                     data=resolved["config.data"],
                     backup=resolved["system.backup"], agents=resolved["config.agents"],
                     channels=resolved["system.channelroot"], template=resolved["system.template"],
                     # ⚑ ``.get`` on the five a ``<None>`` OMITS, and a subscript on the
                     # rest: the resolved table is keyed by what RESOLVED, so a null key is
                     # ABSENT and a subscript would raise KeyError on a legal settings file.
                     canon=resolved.get("system.canon"), settings=resolved["config.settings"],
                     primary_workset=resolved["config.primary_workset"],
                     registry=resolved["config.registry"], journal=resolved["config.journal"],
                     cache=resolved["system.cache"], state=resolved["system.state"],
                     runtime=resolved["system.runtime"],
                     channels_common=resolved.get("system.channels.common"),
                     channels_chat=resolved.get("system.channels.chat"),
                     channels_broadcast=resolved.get("system.channels.broadcast"),
                     channels_mailboxes=resolved.get("system.channels.mailboxes"),
                     channels_share=resolved.get("system.channels.share"),
                     boxes=resolved["_primary_boxes"],
                     primary_vault_ro=resolved.get("_primary_vault_ro"),
                     primary_vault_rw=resolved.get("_primary_vault_rw"),
                     primary_logs=resolved.get("_primary_logs"),
                     early_system=early)


def resolve_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None, *,
                    initialize: bool = False, enable_vault: bool | None = None,
                    name_override: str | None = None, register: bool = True) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths (PRIMARY mode)."""
    raw = resolve_designation(std, project_dir, unknown_name_is_path=True)
    project_path = Path(literal_path(raw))

    if not project_path.is_dir():
        raise ProjectError(ERR_PROJECT_NO_PATH % project_path)

    phash = project_hash(str(project_path.resolve()))
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
            _member = _workset_box_name_for_workspace(
                std.primary_workset, project_path_str, early=_early_scope(std, BoxMode.primary))

        except (OSError, RuntimeError):
            _member = None
        if _member:
            project_name = _member
            project_dir_path = std.boxes / project_name

    metadata_path = project_dir_path

    # B2b: the per-box custom home/vault path OVERRIDE is DROPPED.
    primary_group = _default_project_group(std)
    project_toml, workset_toml = _box_settings_files(BoxMode.primary, metadata_path,
                                                     primary_group)
    shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(std, metadata_path,
                                                               project_name or metadata_path.name)
    resolved_vault = enable_vault
    if initialize and resolved_vault is None:
        resolved_vault = resolve_box_enable_vault(
            std.config_file, box_path=project_toml, workset_path=workset_toml,
        )

    is_new = False
    if initialize and not project_dir_path.is_dir():
        # Guard: refuse to implicitly create a project rooted at $HOME.
        if project_path == Path.home().resolve():
            raise ProjectError(ERR_PROJECT_NEW_HOME)

        # New project: SELECT a name here only (no store write); the membership write happens
        # below — eager for register=True, deferred to the caller for register=False.
        if name_override:
            if register:
                check_primary_box_name_free(std.primary_workset,
                                              name_override, project_path_str,
                                              early=_early_scope(std, BoxMode.primary))
            project_name = name_override
        elif project_name:
            # Bug A: the workspace is ALREADY registered; reuse the name (re-register is a no-op).
            pass
        else:
            project_name = pick_primary_box_name(std.primary_workset,
                                                 project_path_str, boxes_dir=std.boxes,
                                                 early=_early_scope(std, BoxMode.primary))

        project_dir_path = std.boxes / project_name
        metadata_path = project_dir_path
        # Recompute paths with the name-based directory.
        shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(std, metadata_path,
                                                                      project_name)
        project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, primary_group)

        # ⚑ Creation ownership for the unwind below — must be captured BEFORE ``_init_project``
        # merges into the dir, so the unwind never deletes a pre-existing box's ``home/``.
        _dir_existed = project_dir_path.is_dir()

        assert resolved_vault is not None
        authored_vault = read_box_enable_vault(project_toml)
        persist_vault = (enable_vault if enable_vault is not None else authored_vault)
        _init_project(std, metadata_path, shell_path, vault_ro_path,
                      vault_rw_path, project_path, enable_vault=resolved_vault)
        write_box_enable_vault(project_toml, persist_vault)
        # Register the PRIMARY membership (name → workspace) — the SOLE store, idempotent.
        # The except-arm is the belt-and-suspenders unwind for a Guard-1 refusal.
        if register:
            try:
                _register_workset_box_membership(std.primary_workset, project_name, project_path,
                                                 early=_early_scope(std, BoxMode.primary))

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
                        is_new=is_new, mode=BoxMode.primary, name=project_name,
                        group=_default_project_group(std), _config_path=std.config_file,
                        _enable_vault=resolved_vault,
                        _early=_early_scope(std, BoxMode.primary))


def _resolve_local_dir(std: StandardPaths, project_path_str: str) -> tuple[str, Path]:
    """Find the boxes directory for a default-mode project; ``("", empty_path)`` when unregistered."""
    try:
        name = primary_box_name_for_workspace(std.primary_workset, project_path_str,
                                              early=_early_scope(std, BoxMode.primary))
    except (OSError, RuntimeError):
        name = None
    if name is not None:
        return name, std.boxes / name

    return "", std.boxes / UNREGISTERED_MARKER


def _primary_box_paths(std: StandardPaths,
                       metadata_path: Path, box_name: str) -> tuple[Path, Path | None, Path | None]:
    """Fixed PRIMARY-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    ⚑ A NULL ARM YIELDS ``None`` FOR THE PER-BOX LEAF TOO — none is invented.
    """
    shell = metadata_path / HOME_PATH
    vault_ro = None if std.primary_vault_ro is None else std.primary_vault_ro / box_name
    vault_rw = None if std.primary_vault_rw is None else std.primary_vault_rw / box_name
    return shell, vault_ro, vault_rw


def _workset_box_paths(metadata_path: Path, vault_ro_base: Path | None,
                       vault_rw_base: Path | None, box_name: str,
                       ) -> tuple[Path, Path | None, Path | None]:
    """Fixed NAMED-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    ⚑ The two bases are the RESOLVED ``workset.{vault_ro,vault_rw}`` — one arm each,
    because either may be repointed independently of the other, and either may be a
    present ``<None>``.  Only the per-box
    ``@meta.box.name`` LEAF is composed here; that leaf is the whole per-mode variation.
    """
    shell = metadata_path / HOME_PATH
    return (shell,
            None if vault_ro_base is None else vault_ro_base / box_name,
            None if vault_rw_base is None else vault_rw_base / box_name)


def _early_scope(std: StandardPaths, mode: BoxMode, workset_name: str | None = None) -> EarlyScope:
    """*std*'s early-system record, scoped to the partition of a *mode* box in *workset_name*."""
    from kanibako.channels.channels import workset_token
    from kanibako.settings.workset_dirkeys import EarlyScope

    return EarlyScope(std.early_system, workset_token(mode, workset_name))


def _standalone_box_paths(
    root: Path, *, early: EarlyScope,
) -> tuple[Path, Path | None, Path | None]:
    """Fixed STANDALONE-mode ``(home, vault_ro, vault_rw)`` (no layout axis).

    ⚑ STANDALONE roots a degenerate workset at *root*, so *root*'s own ``workset.yaml``
    is the workset tier and its ``workset.{vault_ro,vault_rw}`` are RESOLVED here — the
    keys are UNIFORM in every mode (§2c ALL PROJECTS, R-29), with no standalone
    carve-out.  Only the BIND differs: a lone box takes the arm itself, no name leaf.
    ⚑ A null arm answers ``None``, as in the other two modes.
    """
    from kanibako.project.workset import resolve_workset_vault_pair

    home = standalone_box_store(root, early=early) / HOME_PATH
    vault_ro, vault_rw = resolve_workset_vault_pair(root, early=early)
    return home, vault_ro, vault_rw


def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None:
    """Per-box, per-mode HOST path for the helper message log (the ``helpers.jsonl`` bind source).

    ``None`` when the box's ``workset.logs`` is a present ``<None>``: the hub keeps no log
    and the helper-log bind is omitted (companion, "The helper-log bind").

    ⚑⚑ THIS IS THE HUB'S WRITER, and the MOUNT it must agree with is the spec's own
    spelling ``@workset.logs/@{meta.box.name}.jsonl`` (``data/rom/settings/core-defaults.yaml``,
    ``helpers``).  ⚑ ALL THREE arms RESOLVE the key, so there is one answer in every mode.
    STANDALONE resolves it against the degenerate workset rooted at the project dir.
    """
    logs_dir, box = box_logs_location(std, proj)
    return None if logs_dir is None else box_log_files(logs_dir, box).helper


def creds_watcher_log_path(std: StandardPaths, proj: ProjectPaths) -> Path | None:
    """Per-box HOST log of the detached creds watcher — its stderr, beside the helper log.

    The watcher runs detached with no terminal, so this file is the only place its
    WARNING and ERROR records reach (:func:`kanibako.commands.start._spawn_creds_watcher`).
    ``None`` when the box's ``workset.logs`` is a present ``<None>``.
    """
    logs_dir, box = box_logs_location(std, proj)
    return None if logs_dir is None else box_log_files(logs_dir, box).creds_watcher


class BoxLogFiles(NamedTuple):
    """Every per-box file kanibako writes into a resolved ``workset.logs`` dir."""

    helper: Path
    creds_watcher: Path


def box_log_files(logs_dir: Path, box: str) -> BoxLogFiles:
    """The per-box log files of box *box* in *logs_dir* — THE one place they are named.

    ⚑ A new per-box file under ``workset.logs`` is added HERE, so every removal path
    (:func:`remove_box_logs`) deletes it without being edited.
    """
    return BoxLogFiles(
        helper=logs_dir / f"{box}.jsonl",
        creds_watcher=logs_dir / f"{box}{CREDS_WATCHER_LOG_SUFFIX}",
    )


def remove_box_logs(logs_dir: Path | None, box: str, *, keep: Iterable[Path] = ()) -> list[Path]:
    """Delete box *box*'s log files from *logs_dir*; returns the ones that existed.

    A ``None`` *logs_dir* (``workset.logs`` is ``<None>``) holds no logs: nothing to delete.
    *keep* spares files *box* also names elsewhere — see ``purge_box_logs``.
    """
    removed: list[Path] = []
    if logs_dir is None:
        return removed
    skip = set(keep)
    for log_file in box_log_files(logs_dir, box):
        if log_file in skip:
            continue
        if log_file.is_file():
            log_file.unlink()
            removed.append(log_file)
    return removed


def standalone_logs_dir(root: Path, *, early: EarlyScope) -> Path | None:
    """The resolved ``workset.logs`` of the standalone box rooted at *root*.

    *root* is the workset root of the degenerate workset, so the key is read from the
    root ``workset.yaml``; its default is ``@workset.boxes`` = ``box_data/``.  ``None``
    when the key is a present ``<None>``.
    """
    # ⚑ Deferred import: the documented ``settings.paths`` <-> ``project.workset`` cycle.
    from kanibako.project.workset import load_workset_settings_doc, resolve_workset_logs

    return resolve_workset_logs(root, load_workset_settings_doc(root), standalone=True, early=early)


def box_logs_dir_for(
    std: StandardPaths, mode: BoxMode, metadata_path: Path, ws_root: Path | None,
    *, workset_name: str | None = None,
) -> Path | None:
    """The resolved ``workset.logs`` dir for a box in *mode*; ``None`` under a present ``<None>``.

    ⚑ Takes *mode*'s operands, not a :class:`ProjectPaths`; *ws_root* is read in ``named`` mode only.
    """
    # ⚑ Deferred import: the documented ``settings.paths`` <-> ``project.workset`` cycle.
    from kanibako.project.workset import load_workset_settings_doc, resolve_workset_logs

    if mode is BoxMode.standalone:
        # ``metadata_path`` IS the standalone root (drift I).
        return standalone_logs_dir(metadata_path, early=_early_scope(std, mode))

    if mode is BoxMode.named:
        if workset_name is None:
            raise ValueError("a named box's logs dir needs its workset name")
        # ⚑ The fallback still assumes the DEFAULT box layout; it is unreachable from
        # ``resolve_workset_project``, which always supplies the group.
        root = ws_root if ws_root is not None else metadata_path.parent.parent
        return resolve_workset_logs(
            root, load_workset_settings_doc(root), early=_early_scope(std, mode, workset_name),
        )
    # PRIMARY: the PRIMARY workset's logs dir — ``std.primary_logs`` is already the
    # RESOLVED ``workset.logs`` of the primary root (:func:`resolve_system_paths`).
    return std.primary_logs


def box_logs_location(std: StandardPaths, proj: ProjectPaths) -> tuple[Path | None, str]:
    """``(resolved workset.logs dir, box name)`` for *proj*'s mode; the dir is ``None`` under ``<None>``."""
    box = proj.name if proj.name else short_hash(proj.project_hash)
    ws_root = proj.group.root if proj.group else proj.metadata_path.parent.parent
    return box_logs_dir_for(std, proj.mode, proj.metadata_path, ws_root,
                            workset_name=proj.group.name if proj.group else None), box


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


def _init_common(std: StandardPaths, metadata_path: Path, shell_path: Path,
                 vault_ro_path: Path | None, vault_rw_path: Path | None, project_path: Path,
                 *, enable_vault: bool = True,
                 vault_root: Path) -> None:
    """Shared first-time project setup: create directories, bootstrap shell.

    ⚑⚑ *vault_root* is the workset root that owns the ``vault/`` SKELETON dir, and it is
    REQUIRED because the skeleton is composed off it — see :func:`write_vault_gitignore`,
    which answers the whole question.  Without a root there is no skeleton, so there is
    nothing to answer.
    ⚑ A NULL ARM IS NOT CREATED, and has no ``.gitignore`` beside it.
    """
    import sys

    print(MSG_OTS_KB_INIT % project_path, end="", flush=True, file=sys.stderr)
    metadata_path.mkdir(parents=True, exist_ok=True)

    # Create persistent agent shell (mounted as /home/agent).
    shell_path.mkdir(parents=True, exist_ok=True)
    _bootstrap_shell(shell_path)

    # Vault directories (skip when vault is disabled).
    if enable_vault:
        if vault_ro_path is not None:
            vault_ro_path.mkdir(parents=True, exist_ok=True)
        if vault_rw_path is not None:
            vault_rw_path.mkdir(parents=True, exist_ok=True)
            # ⚑ ORDER IS LOAD-BEARING: the mkdir above is what puts the skeleton on disk
            # whenever the gate would pass, satisfying this call's precondition silently.
            write_vault_gitignore(vault_root, vault_rw_path)

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


def write_vault_gitignore(vault_root: Path, vault_rw_path: Path) -> None:
    """Write the vault skeleton's ``.gitignore`` while ``workset.vault_rw`` makes its claim true.

    ⚑⚑ THE SKELETON IS COMPOSED OFF *vault_root*, NEVER POSITIONED OFF A RESOLVED ARM.  It
    is ``<vault_root>/vault`` — the one non-key leaf a workset root carries
    (``project/workset.py::_VAULT_LEAF``), and the same file ``standalone_vault_teardown``
    clears.  ``vault_ro_path.parent`` was that position, and ``workset.vault_ro`` is a
    repointable key, so the parent stopped being the skeleton the moment it moved:
    ``vault_ro: @meta.workset.path/store/ro`` named ``<root>/store`` — a directory no key
    gave us — and in PRIMARY the arm carries a ``@meta.box.name`` leaf, so the parent was
    the ``ro`` arm ITSELF, where an ``rw/`` pattern matches nothing.

    ⚑ The file's entire content is a CLAIM that ``rw/`` is a child of the skeleton, so it is
    written only while the RESOLVED *vault_rw_path* really is under it — a repoint out makes
    the claim false and the file a stray beside a directory the USER named.
    ⚑ STRICT: an arm pointed AT the skeleton is the user's rw store, not its parent.

    ⚑⚑ ONE CARRIER FOR THREE WRITE SITES — this, ``_lifecycle._to_standalone`` and
    ``_duplicate._duplicate_to_standalone``.  The gate used to travel as prose, and both
    convert sites kept writing on ``vault_dir.is_dir()`` alone: the skeleton is the ``ro``
    arm's DEFAULT parent, so it can sit on disk while ``vault_rw`` points elsewhere and the
    ``rw/`` claim is already false.  A position cannot answer a key (P10).

    ⚑ The skeleton must already be on disk — an absent one means no vault was laid here (a
    duplicate never carries one), and inventing an empty ``vault/`` to hold the file would
    make this function a creator of the thing it only annotates.
    """
    vault_dir = vault_root / VAULT_PATH
    if not vault_dir.is_dir():
        return
    if vault_rw_path == vault_dir or not _host_path_within(vault_rw_path, vault_dir):
        return
    gitignore = vault_dir / IGNORE_FILE
    if not gitignore.exists():
        gitignore.write_text("rw/\n")


def _init_project(std: StandardPaths, metadata_path: Path, shell_path: Path,
                  vault_ro_path: Path | None, vault_rw_path: Path | None,
                  project_path: Path, *, enable_vault: bool = True) -> None:
    """First-time project setup: create directories, copy credentials from host."""
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault, vault_root=std.primary_workset)


def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None:
    """Find the deepest registered default-mode project that is an ancestor of *target*."""
    boxes_dir = std.boxes
    best: Path | None = None
    best_depth = -1
    for name, path_str in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary)).items():
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
    """True only if *root*'s own ``workset.yaml`` stores the standalone ``workset.registry`` null."""
    from kanibako.launch import box_resolve
    return box_resolve.stores_standalone_registry_null(root)


def detect_project_mode(project_dir: Path, std: StandardPaths,
                        config: BootstrapConfig) -> DetectionResult:
    """Infer which project mode applies to *project_dir*, walking ancestors for markers."""
    resolved = Path(literal_path(project_dir))
    homes = {Path.home().resolve(), Path(literal_path(Path.home()))}

    # 1. Connected-external check.  ⚑ MUST run BEFORE the step-2 marker check: otherwise
    # import_standalone re-creates the very dual registration that --force removed.
    from kanibako.launch import box_resolve
    if box_resolve.find_connected_external_box(resolved, std) is not None:
        return DetectionResult(BoxMode.named, resolved)

    # 2. In-place standalone marker AT the resolved dir (D3-mode #1, marker-first): it
    # OVERRIDES workset TREE membership.  Only this dir; ancestors are the step-5 walk.
    if _is_standalone_meta_dir(resolved):
        from kanibako.project import import_reconcile
        import_reconcile.import_standalone(
            std.registry, resolved, journal=std.journal,
            early=_early_scope(std, BoxMode.standalone))
        return DetectionResult(BoxMode.standalone, resolved)

    # 3. Workset check (no walk needed — relative_to handles subdirs).
    ws_result = _check_workset(resolved, std)
    if ws_result is not None:
        return ws_result

    # 4. Name-based default-mode check (one-pass scan, deepest match wins).
    ac_ancestor = _find_local_ancestor(resolved, std)
    if ac_ancestor is not None:
        return DetectionResult(BoxMode.primary, ac_ancestor)

    # 5. Walk ancestors for on-disk markers, IMPORTING what is unregistered.  STANDALONE is
    # checked first at each level: the root file's own null defines it, skeleton or not.
    from kanibako.project import import_reconcile
    from kanibako.project.workset import (
        is_workset_skeleton, refuse_retired_workset_identity,
    )
    from kanibako.settings.workset_dirkeys import EarlyScope

    current = resolved
    while True:
        # ⚑⚑ THE LEGACY REFUSAL RUNS FIRST, and unconditionally.  A v1.6/v1.7 root HAS
        # the four-dir skeleton, so the NAMED arm below would import it happily under
        # its leaf name — leaving the retired identity table, and the `projects:` list
        # beside it, unread and unmentioned.  Diagnosing the legacy shape has to come
        # before acting on the directory, or the diagnosis never happens.
        refuse_retired_workset_identity(current)

        # STANDALONE: the root file's own stored ``workset.registry`` null; box_data/ is not the marker.
        if _is_standalone_meta_dir(current):
            import_reconcile.import_standalone(
                std.registry, current, journal=std.journal,
                early=_early_scope(std, BoxMode.standalone))
            return DetectionResult(BoxMode.standalone, current)

        # NAMED: an unregistered workset root; import it, then the standard check resolves it.
        if is_workset_skeleton(current, early=EarlyScope(std.early_system, current.name)):
            import_reconcile.import_named_workset(
                std.registry, current, journal=std.journal,
            )
            ws_after = _check_workset(resolved, std)
            if ws_after is not None:
                return ws_after

        # Stop conditions: reached $HOME or filesystem root.
        if current in homes:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 6. Default: primary mode at the original directory.
    return DetectionResult(BoxMode.primary, resolved)


def _check_workset(resolved_dir: Path, std: StandardPaths) -> DetectionResult | None:
    """Check whether *resolved_dir* is inside a registered workset (``workspaces/`` first)."""
    from kanibako.project.workset import (list_worksets, load_workset_settings_doc,
                                          resolve_workspaces_locator)

    for ws_name, ws_root in list_worksets(std).items():
        ws_root = ws_root.resolve()
        if _is_standalone_meta_dir(ws_root):
            continue
        # The RESOLVED ``workset.workspaces`` — a repoint is honored (§3.3).
        ws_workspaces = resolve_workspaces_locator(
            ws_root, load_workset_settings_doc(ws_root),
            early=_early_scope(std, BoxMode.named, ws_name),
        )
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


def _workset_box_name_for_workspace(ws_root: Path, workspace: str,
                                    *, early: EarlyScope) -> str | None:
    """Reverse-look-up *workspace* in *ws_root*'s per-workset ``boxes:`` membership (Guard 2)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE), early=early)
    return workset_registry.reverse_lookup_workset_box(registry_path, workspace)


def _workset_box_workspace_for_name(ws_root: Path, box_name: str,
                                    *, early: EarlyScope) -> str | None:
    """Forward-look-up *box_name* in *ws_root*'s per-workset ``boxes:`` membership."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE), early=early)
    return workset_registry.workset_box_path(registry_path, box_name)


def _register_workset_box_membership(ws_root: Path, box_name: str, workspace: Path,
                                     *, early: EarlyScope) -> None:
    """Register *box_name* → *workspace* in *ws_root*'s per-workset registry (idempotent)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE), early=early)
    workset_registry.register_workset_box(registry_path, box_name, workspace)


def _unregister_workset_box_membership(ws_root: Path, box_name: str,
                                       *, early: EarlyScope) -> None:
    """Drop *box_name* from *ws_root*'s per-workset registry (compensating action, idempotent)."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / WORKSET_META_FILE), early=early)
    workset_registry.unregister_workset_box(registry_path, box_name)


# ---------------------------------------------------------------------------
# PRIMARY-box name registry (the primary per-workset ``boxes:`` membership).
# ---------------------------------------------------------------------------
# The SOLE store of default-mode box names; mirrors the retired ``names.py`` API.

def load_primary_boxes(primary_workset: Path, *, early: EarlyScope) -> dict[str, str]:
    """Return the PRIMARY box membership as ``{box_name: workspace_path_str}``."""
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        primary_workset, load_doc(primary_workset / WORKSET_META_FILE), early=early)
    return workset_registry.load_workset_boxes(registry_path)


def primary_box_name_for_workspace(primary_workset: Path, workspace: str,
                                   *, early: EarlyScope) -> str | None:
    """Return the PRIMARY box name registered for *workspace*, or ``None`` (resolved-path aware)."""
    return _workset_box_name_for_workspace(primary_workset, workspace, early=early)


def check_primary_box_name_free(primary_workset: Path, name: str, workspace: str,
                                *, early: EarlyScope) -> None:
    """Raise ``ProjectError`` if *name* is already a PRIMARY box's; worksets are not consulted."""
    if Path(workspace).resolve() == Path.home().resolve():
        from kanibako.errors import ProjectError
        raise ProjectError(ERR_PROJECT_REG_HOME)

    # ⚑ Case-blind (spec §0, ⚑ NAMING RULES).
    if find_identifier(name, load_primary_boxes(primary_workset, early=early)) is not None:
        from kanibako.errors import ProjectError
        raise ProjectError(ERR_PROJECT_NAME_USED % name)


def check_workspace_not_named_box(std: StandardPaths, workspace: str) -> None:
    """Raise ``ProjectError`` when a NAMED box already holds *workspace* (no write).

    Read through the ONE resolver that consults the per-workset ``boxes:`` membership —
    :func:`box_resolve.find_connected_external_box` — which states what it matches.
    """
    # ⚑ Lazy import avoids a paths <-> box_resolve import cycle — do not hoist.
    from kanibako.errors import ProjectError
    from kanibako.launch import box_resolve

    owned = box_resolve.find_connected_external_box(Path(workspace), std)
    if owned is not None:
        raise ProjectError(ERR_PROJECT_PATH_IS_NAMED_BOX % (
            workspace, owned.box_name, owned.workset_name,
            *map(shlex.quote, (owned.workset_name, owned.box_name,
                               owned.workset_name, owned.box_name)),
        ))


def pick_primary_box_name(primary_workset: Path, workspace: str,
                          boxes_dir: Path | None = None, *, early: EarlyScope) -> str:
    """Pick a PRIMARY box name from *workspace*'s basename, free among primary boxes."""
    base = Path(workspace).name or "project"
    taken_names = load_primary_boxes(primary_workset, early=early)

    def taken(cand: str) -> bool:
        # ⚑ TWO rules, deliberately: the NAME domain compares case-blind (§0), the
        # DIRECTORY probe is a PATH and is never folded.
        return (
            find_identifier(cand, taken_names) is not None
            or (boxes_dir is not None and (boxes_dir / cand).exists())
        )

    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}{n}"
        n += 1
    return candidate


def register_primary_box_name(primary_workset: Path, name: str,
                              workspace: Path | str, *, early: EarlyScope) -> None:
    """Register *name* → *workspace* in the PRIMARY membership (with guards)."""
    check_primary_box_name_free(primary_workset, name, str(workspace), early=early)
    _register_workset_box_membership(primary_workset, name, Path(workspace), early=early)


def register_primary_box_name_if_absent(primary_workset: Path, name: str,
                                        workspace: Path | str, *, early: EarlyScope) -> None:
    """Idempotent :func:`register_primary_box_name` for deferred-create recovery."""
    from kanibako.project.workset_registry import _same_workspace

    boxes = load_primary_boxes(primary_workset, early=early)
    stored = find_identifier(name, boxes)
    existing = None if stored is None else boxes[stored]
    if existing is not None and _same_workspace(existing, str(workspace)):
        return
    register_primary_box_name(primary_workset, name, workspace, early=early)


def assign_primary_box_name(primary_workset: Path, workspace: Path | str,
                            boxes_dir: Path | None = None, *, early: EarlyScope) -> str:
    """Auto-assign + register a PRIMARY box name from *workspace*'s basename."""
    candidate = pick_primary_box_name(primary_workset, str(workspace),
                                      boxes_dir=boxes_dir, early=early)
    register_primary_box_name(primary_workset, candidate, workspace, early=early)
    return candidate


def unregister_primary_box_name(primary_workset: Path, name: str,
                                *, early: EarlyScope) -> None:
    """Drop *name* from the PRIMARY membership (the membership ``unregister_name``)."""
    _unregister_workset_box_membership(primary_workset, name, early=early)


def resolve_workset_project(ws: WorksetSpec, project_name: str, std: StandardPaths,
                            config: BootstrapConfig, *, initialize: bool = False,
                            enable_vault: bool | None = None) -> ProjectPaths:
    """Resolve per-project paths for a project inside a NAMED workset."""
    # Look up project in workset.
    if project_name not in ws.project_names:
        raise WorksetError(ERR_WORKSET_NO_PROJECT % (project_name, ws.name))

    # Name-based paths (not hash-based).
    project_dir = ws.projects_dir / project_name
    metadata_path = project_dir

    # Workspace override (P7/D10): the REGISTERED path IS the workspace; unregistered
    # members fall back to the composed default.  ⚑ Never re-derive a registered member.
    project_toml, workset_toml = _box_settings_files(BoxMode.named, metadata_path, ws)
    ws_early = _early_scope(std, BoxMode.primary if ws.is_default else BoxMode.named, ws.name)
    registered_workspace = _workset_box_workspace_for_name(ws.root, project_name, early=ws_early)
    if registered_workspace is not None:
        workspace = Path(registered_workspace)
    else:
        if ws.workspaces_dir is None:
            # ⚑ No recorded workspace and no workspaces dir to compose one in.
            raise WorksetError(ERR_WORKSET_NULL_WORKSPACES % (
                ws.root / WORKSET_META_FILE, f"a workspace for '{project_name}'"))
        workspace = ws.workspaces_dir / project_name
        from kanibako.launch import box_resolve
        identity = box_resolve.resolve_box_identity(workspace, std, config)
        if identity is not None:
            workspace = Path(identity["workspace"])
    # B2b: the per-box custom home/vault path OVERRIDE is DROPPED;
    # the workspace override above is a SEPARATE concern and STAYS.
    shell_path, vault_ro_path, vault_rw_path = _workset_box_paths(
        metadata_path, ws.vault_ro_dir, ws.vault_rw_dir, project_name)
    resolved_vault = enable_vault

    # Hash the workspace (identity): a null must not rename the box.
    phash = project_hash(str(workspace.resolve()))
    # None for an in-tree member under a null ``workset.workspaces`` (Q106).
    project_path: Path | None = workspace
    from kanibako.project.workset import refuse_null_box_workspace
    try:
        refuse_null_box_workspace(ws.root, workspace, project_name, standalone=False,
                                  early=ws_early)
    except WorksetError:
        project_path = None

    is_new = False
    if initialize and not shell_path.is_dir():
        authored_vault = read_box_enable_vault(project_toml)
        persist_vault = (enable_vault if enable_vault is not None else authored_vault)
        _init_workset_project(std, metadata_path, shell_path)
        write_box_enable_vault(project_toml, persist_vault)
        # P5a dual-register (idempotent), the SOLE identity record; *workspace* seeds external.
        _register_workset_box_membership(ws.root, project_name, workspace, early=ws_early)
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
                        name=project_name, group=ProjectGroup(name=ws.name, root=ws.root,
                                                              is_default=False,
                                                              local_shared_base=ws.root),
                        _config_path=std.config_file, _enable_vault=resolved_vault,
                        _early=_early_scope(std, BoxMode.named, ws.name))


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
        std.primary_workset, load_doc(std.primary_workset / WORKSET_META_FILE),
        early=_early_scope(std, BoxMode.primary))
    registered = workset_registry.load_workset_boxes(primary_registry)
    results: list[tuple[Path, Path | None]] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        # ⚑ The box dir's LEAF is a path segment; matching it to a registered box name is
        # an identifier question, so it compares case-blind (spec §0).  The directory name
        # itself is never folded — only the comparison is.
        stored = find_identifier(entry.name, registered)
        registered_ws = None if stored is None else registered[stored]
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
            ws = load_workset(root, ws_name, early_system=std.early_system)
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
                                          load_workset_settings_doc, resolve_workspaces_locator)

    registry = list_worksets(std)
    resolved = Path(literal_path(project_dir))
    for ws_name, root in registry.items():
        ws_root = root.resolve()
        # The RESOLVED ``workset.workspaces`` — a repoint is honored (§3.3).
        ws_workspaces = resolve_workspaces_locator(
            ws_root, load_workset_settings_doc(ws_root),
            early=_early_scope(std, BoxMode.named, ws_name),
        )
        # Check workspaces/ first (specific project).
        try:
            rel = resolved.relative_to(ws_workspaces)
            project_name = rel.parts[0] if rel.parts else None
            ws = load_workset(root, ws_name, early_system=std.early_system)
            return ws, project_name
        except ValueError:
            pass
        # Then check workset root itself.
        try:
            resolved.relative_to(ws_root)
            ws = load_workset(root, ws_name, early_system=std.early_system)
            return ws, None
        except ValueError:
            continue
    raise WorksetError(ERR_WORKSET_NO_WORKSET % project_dir)


def _resolve_workset_or_connected(project_dir: Path,
                                  std: StandardPaths) -> tuple[_WorksetLike, str | None]:
    """Resolve *project_dir* to its owning workset, honoring external connects."""
    try:
        ws, proj_name = _find_workset_for_path(project_dir, std)
    except ReservedWorksetNameError:
        raise
    except WorksetError:
        ws, proj_name = None, None
    if ws is None or proj_name is None:
        # Tree lookup missed: try the connected-external boxes (D10 enumerate-and-scan).
        # ⚑ Lazy import avoids a paths <-> box_resolve import cycle — do not hoist.
        from kanibako.launch import box_resolve
        from kanibako.project.workset import load_workset
        owned = box_resolve.find_connected_external_box(project_dir, std)
        if owned is not None:
            ws, proj_name = (load_workset(owned.workset_root, owned.workset_name,
                                          early_system=std.early_system),
                             owned.box_name)
    if ws is None:
        raise WorksetError(ERR_WORKSET_NO_WORKSET % project_dir)
    return ws, proj_name


class DesignationRoute(Enum):
    """The route a box designation is resolved by."""

    CWD = "cwd"
    PATH = "path"
    NAME = "name"
    QUALIFIED = "qualified"
    INVALID = "invalid"


def designation_route(value: str | None, *, name_first: bool = False) -> DesignationRoute:
    """Decide how a box designation is resolved; the one place that decides it.

    An IDENTIFIER is ambiguous between a box name and a relative path: it takes the
    NAME route when *name_first* or when no such path exists, else the PATH route.  A
    PATH of the form ``<workset>/<box>``, both segments IDENTIFIERs, that does not exist
    takes the QUALIFIED route.  Reads the filesystem (``exists``), nothing else.
    """
    kind = classify_designation(value)
    if kind is Designation.ABSENT:
        return DesignationRoute.CWD
    if kind is Designation.INVALID:
        return DesignationRoute.INVALID
    assert value is not None
    on_disk = Path(value).exists()
    if kind is Designation.IDENTIFIER:
        return DesignationRoute.NAME if name_first or not on_disk else DesignationRoute.PATH
    workset, sep, box = value.partition("/")
    if (sep and not on_disk and classify_designation(workset) is Designation.IDENTIFIER
            and classify_designation(box) is Designation.IDENTIFIER):
        return DesignationRoute.QUALIFIED
    return DesignationRoute.PATH


def resolve_designation(std: StandardPaths, value: str | None, *, unknown_name_is_path: bool,
                        name_first: bool = False) -> str:
    """Turn a box designation into the path string the path route resolves.

    A name lookup that hits a project returns its workspace; a miss on a name whose
    path exists returns the path.  Raises :class:`ProjectError` for an INVALID
    designation, :class:`AmbiguousNameError` for a name matching several boxes,
    :class:`WorksetError` for a bare workset name with no such path, and
    :class:`ProjectError` for an unknown name with no such path unless
    *unknown_name_is_path*.  A QUALIFIED miss returns the designation unchanged.
    """
    route = designation_route(value, name_first=name_first)
    if route is DesignationRoute.CWD:
        return logical_cwd()
    assert value is not None
    if route is DesignationRoute.INVALID:
        raise ProjectError(ERR_PROJECT_BAD_DESIGNATION % value)
    if route is DesignationRoute.PATH:
        if classify_designation(value) is Designation.IDENTIFIER:
            _warn_standalone_shadowed(std, value)
        return value
    if route is DesignationRoute.QUALIFIED:
        try:
            return resolve_qualified_name(std.registry, value, early_system=std.early_system)[0]
        except ProjectError:
            return value
    on_disk = Path(value).exists()
    try:
        # A registered standalone name ranks after a same-named path (spec § Box designation).
        resolved, kind = resolve_name(std.registry, value, cwd=Path.cwd(),
                                      primary_workset=std.primary_workset, standalone=not on_disk,
                                      early_system=std.early_system)
    except AmbiguousNameError:
        raise
    except ProjectError:
        if on_disk:
            _warn_standalone_shadowed(std, value)
            return value
        if unknown_name_is_path:
            return value
        raise
    if kind == KIND_PROJECT:
        return resolved
    if kind == KIND_WORKSET and not on_disk:
        raise WorksetError(ERR_WORKSET_WS_NOT_BOX % (value, value))
    _warn_standalone_shadowed(std, value)
    return value


def _warn_standalone_shadowed(std: StandardPaths, value: str) -> None:
    """Warn when the path *value* outranks a registered standalone box of that name."""
    from kanibako.project import registry_store

    root = registry_store.standalone_root(std.registry, value)
    if root is not None and Path(root).resolve() != Path(value).resolve():
        logger.warning(WARN_SA_SHADOWED_BY_PATH, value, Path(value).resolve(), root)


def resolve_any_project(std: StandardPaths, config: BootstrapConfig, project_dir: str | None = None,
                        *, initialize: bool = False, register: bool = True,
                        name_override: str | None = None) -> ProjectPaths:
    """Auto-detect project mode and resolve paths accordingly."""
    # An unknown name on the READ path is refused rather than path-ified into a phantom
    # hash-named box; the CREATE path (*initialize*) still path-ifies it.
    raw = resolve_designation(std, project_dir, unknown_name_is_path=initialize)
    return _resolve_designated_path(std, config, raw, initialize=initialize,
                                    register=register, name_override=name_override)


def _resolve_designated_path(std: StandardPaths, config: BootstrapConfig, raw: str, *,
                             initialize: bool, register: bool,
                             name_override: str | None = None) -> ProjectPaths:
    """Resolve the path a designation resolved to, by the mode detected there."""
    raw_dir = Path(literal_path(raw))
    detection = detect_project_mode(raw_dir, std, config)
    root_str = str(detection.project_root)

    if detection.mode == BoxMode.named:
        ws, proj_name = _resolve_workset_or_connected(raw_dir, std)
        if proj_name is None:
            raise WorksetError(ERR_WORKSET_NOT_IN_BOX % (ws.name, ws.workspaces_dir or "<None>"))

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

    # A registered box name wins over a same-named folder, hence *name_first* — except a
    # registered standalone name, which the spec ranks after primary workset boxes AND
    # paths (system-design § Box designation & workset path space), so the folder wins.
    raw = resolve_designation(std, value, unknown_name_is_path=initialize, name_first=True)
    return _flag(_resolve_designated_path(std, config, raw, initialize=initialize,
                                          register=register))


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
    try:
        # ⚑ A NULL ARM IS NO SUCH DIR, not a missing one to warn about.
        if (proj.vault_enabled() and proj.vault_rw_path is not None
                and not proj.vault_rw_path.is_dir()):
            get_logger(__name__).warning(WARN_BOX_NO_VAULT, proj.name or str(proj.project_path or "<None>"),
                                         proj.vault_rw_path)
    except SettingsError:
        pass

    return proj


STANDALONE_REGISTRY_COMMENT = "REMOVING THIS WILL BREAK A STANDALONE BOX!"


def establish_standalone(std: StandardPaths, root: Path, *, enable_vault: bool | None,
                         name: str = "",
                         register: bool = True) -> tuple[str, Path, Path | None, Path | None]:
    """Establish a standalone box at *root*: identity + meta + registration (the shared core)."""
    from kanibako.project import registry_store
    from kanibako.launch import box_identity

    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(
        root, early=_early_scope(std, BoxMode.standalone))

    existing = registry_store.standalone_box_names(std.registry)
    box_name = box_identity.resolve_standalone_name(root, name, existing)

    box_settings, settings_file = _standalone_settings_files(
        root, early=_early_scope(std, BoxMode.standalone))
    # ⚑ Sparse create, EACH KEY AT ITS OWN SCOPE'S TIER (M-8): an AUTHORED ``box.enable_vault``
    # to the BOX tier — the same file ``config set box.*`` writes; ``None`` authors nothing.
    if enable_vault is not None:
        write_box_enable_vault(box_settings, enable_vault)
    # ⚑ The workset-scope kuid goes to the ROOT file, beside the stored ``workset.registry``
    # null that DEFINES standalone (``system-design-1.8.0.md`` § "Detection & import").
    from kanibako.settings.config_io import dump_doc_commented, load_doc, refuse_scalar_sections

    data = load_doc(settings_file)
    refuse_scalar_sections(settings_file, ("workset",), data=data)
    data.setdefault("workset", {}).update(kuid=box_identity.standalone_kuid(box_name), registry=None)
    dump_doc_commented(settings_file, data, ("workset",), "registry", STANDALONE_REGISTRY_COMMENT)
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

    # Metadata at the ROOT; ``project_path`` is the RESOLVED ``workset.workspaces`` (ruled 10),
    # ``None`` when the root nulls it.
    metadata_path = root
    standalone_early = _early_scope(std, BoxMode.standalone)
    box_data = standalone_box_store(root, early=standalone_early)
    project_path = resolve_workset_workspaces(root, load_workset_settings_doc(root),
                                              standalone=True, early=standalone_early)
    # The mode-aware tier pair from the ONE derivation (M-8).
    box_settings, project_toml = _standalone_settings_files(root, early=standalone_early)

    # ⚑ STANDALONE paths derive from the CURRENT root, never stored absolutes — that is
    # what makes a default-shaped tree drop-in portable BY CONSTRUCTION.
    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root, early=standalone_early)
    resolved_vault = enable_vault
    if initialize and resolved_vault is None:
        resolved_vault = resolve_box_enable_vault(
            std.config_file, box_path=box_settings, workset_path=project_toml,
        )

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
        # ⚑ A null ``workset.workspaces`` in a pre-existing root file: no workspace dir to
        # create (Q96), refused here, before the first write, not at the later mkdir.
        from kanibako.project.workset import refuse_null_workspaces
        refuse_null_workspaces(root, f"a workspace for '{root.name}'", standalone=True,
                               early=standalone_early)
        assert project_path is not None  # a null root refused on the line above
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
        from kanibako.channels.channels import WS_TOKEN_STANDALONE
        from kanibako.launch.templates import check_workset_template, install_workset_template

        check_workset_template(std, root, workset_name=WS_TOKEN_STANDALONE, canon_only=True)
        install_workset_template(std, root, workset_name=WS_TOKEN_STANDALONE, canon_only=True)
        assert resolved_vault is not None
        _init_standalone_project(std, box_data, shell_path, vault_ro_path, vault_rw_path,
                                 project_path, enable_vault=resolved_vault,
                                 workset_root=root)
        # Identity + meta + registration via the shared establish core (fresh identity here).
        box_name, shell_path, vault_ro_path, vault_rw_path = establish_standalone(
            std, root, enable_vault=enable_vault, name=requested_name, register=register)
        is_new = True

    if initialize:
        # Recovery: ensure home + workspace exist (a null names no workspace to make).
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        if project_path is not None:
            project_path.mkdir(parents=True, exist_ok=True)

    return ProjectPaths(project_path=project_path, project_hash=phash, metadata_path=metadata_path,
                        shell_path=shell_path, vault_ro_path=vault_ro_path,
                        vault_rw_path=vault_rw_path, is_new=is_new, mode=BoxMode.standalone,
                        name=box_name, _config_path=std.config_file,
                        _enable_vault=resolved_vault, _early=standalone_early)


def _init_standalone_project(std: StandardPaths, metadata_path: Path, shell_path: Path,
                             vault_ro_path: Path | None, vault_rw_path: Path | None,
                             project_path: Path, *, enable_vault: bool = True,
                             workset_root: Path) -> None:
    """First-time standalone project setup: all state inside the project dir (vault included).

    ⚑ *workset_root* owns the ``vault/`` skeleton and is passed APART from *metadata_path*:
    a repointed store's parent is not the root, so deriving it would stamp
    ``write_vault_gitignore`` on the store.
    """
    _init_common(std, metadata_path, shell_path, vault_ro_path, vault_rw_path, project_path,
                 enable_vault=enable_vault, vault_root=workset_root)
    # The workspace is a SUBDIR of the root (drift H); create the bind source.
    project_path.mkdir(parents=True, exist_ok=True)
