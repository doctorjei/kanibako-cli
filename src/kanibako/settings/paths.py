"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol, overload

from kanibako.log import get_logger

from kanibako.settings.config import (
    BOX_META_FILE,
    KanibakoConfig,
    config_file_path,
    load_config,
    read_box_enable_vault,
    read_workset_kuid,
    read_workset_skip_kuid_check,
    write_box_enable_vault,
)
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
)
from kanibako.project.names import (
    resolve_name,
    resolve_qualified_name,
)
from kanibako.utils import project_hash, short_hash


class BoxMode(Enum):
    """How a box's persistent state is organized on disk.

    Surfaced as the ``box.mode`` token.  ``primary`` is the implicit PRIMARY
    workset (formerly the synthesized default workset), ``named`` is a named
    workset, ``standalone`` keeps all state inside the project directory.
    """

    primary = "primary"
    named = "named"
    standalone = "standalone"


# The STANDALONE box-store dir name.  It is ``@meta.box.path`` for a standalone box
# (the empty leaf of ``@workset.boxes``, spec §2c) and half of the §5 detection
# marker (``box_data/`` dir + the ROOT ``settings.yaml``).  Defined here, at the top,
# because ``_box_settings_files`` below needs it and it is a LAYOUT constant, not a
# detail of the detection helper that used to own it.
_STANDALONE_META_DIR = "box_data"


class DetectionResult(NamedTuple):
    """Result of box mode detection.

    *mode* is the detected box mode.  *project_root* is the ancestor
    directory where the marker was found (may differ from the original
    *project_dir* when the user is in a subdirectory).
    """

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
    # System-level derived directories.  The path roots split into the Layer-1
    # CONFIG-key foundation (``config.*``: data/settings/agents/primary_workset/
    # registry) and the Layer-2 ``system.*`` path settings (channelroot/
    # template/canon/backup/cache/runtime).  ``global`` is ELIMINATED (children
    # inline ``@config.data/global/...``).
    data: Path
    backup: Path
    agents: Path
    channels: Path
    # ``system.template`` — the system TEMPLATE ROOT (M-11 rename of the former
    # ``system.base_template``; the default moved ``global/base_template`` →
    # ``global/template`` at the same time).  Holding the ROOT rather than the
    # box-seed dir directly is what leaves room for further template subtrees
    # without new keys.  ⚑ The box-HOME seed is ``template/box/home``, NOT the root
    # and NOT ``box/``: ``box/`` is the box TEMPLATE root, holding ``home/`` (spec
    # §2a layers 1-3, the ``seeded`` category) beside ``canon/handbook/`` (the box
    # handbook HOST template — not a seed since 2026-08-07g; see
    # ``launch.templates.install_box_handbook_template``).
    template: Path
    # ``system.canon`` — the SYSTEM-level CANON CONTRIBUTION root (spec §2g).  Its
    # ``handbook/`` subtree supplies ``SYS_CONTENTS.md`` + the ``general`` chapter,
    # bound RO into every box.  ⚑ It names what this SCOPE CONTRIBUTES to the canon,
    # NOT a copy of the assembled tree: ``~/canon`` (guest) is the assembly,
    # ``@<scope>.canon`` (host) is one scope's contribution to it.
    canon: Path
    settings: Path
    primary_workset: Path
    registry: Path
    # Lifecycle journal — write-ahead log of in-flight box-lifecycle ops, beside
    # the registry (``config.journal = @config.data/global/journal.yaml``).
    # PATH-BASED on the resolved key (mirrors ``registry``; no reconstruction).
    journal: Path
    cache: Path
    runtime: Path
    # Channels skeleton — keys/defaults only; sub-key wiring is Phase 6.
    channels_common: Path
    channels_chat: Path
    channels_broadcast: Path
    channels_mailboxes: Path
    channels_share: Path
    # PRIMARY-workset box store: ``@config.primary_workset/boxes``.  Phase 5
    # moved this here from the OLD ``@config.data/boxes`` location (the
    # transitional ``_boxes`` pseudo-key + alias property were retired with the
    # ``_migrate_settings_to_boxes`` shim).  Per-box metadata/shell live under
    # ``boxes/<name>/``; the PRIMARY vault/logs live as siblings under the
    # PRIMARY workset (see :func:`resolve_project`).
    boxes: Path
    # PRIMARY-workset vault + logs roots: ``@config.primary_workset/vault/{ro,rw}``
    # and ``@config.primary_workset/logs``.  Phase 5 moved the PRIMARY vault out
    # of the workspace into the PRIMARY workset.
    primary_vault_ro: Path
    primary_vault_rw: Path
    primary_logs: Path


@dataclass(frozen=True)
class ProjectGroup:
    """Descriptor of a project's grouping (default workset or named workset).

    Captures the default-vs-workset difference as *data* rather than control
    flow.  The implicit default group is the *default workset* (``is_default``
    is True), rooted at ``@config.primary_workset`` (spec §2c: PRIMARY
    ``meta.workset.path``); a named workset forms a non-default group rooted at
    the workset root.  Standalone projects belong to no group
    (``ProjectPaths.group`` is None).

    *local_shared_base* is the root under which the local-shared path lives
    (``base / "common"``): the standard data path for the default
    group, the workset root for a workset group.
    """

    name: str
    root: Path
    is_default: bool
    local_shared_base: Path


class _WorksetRooted(Protocol):
    """Structural type for "anything rooted at ``@meta.workset.path``".

    Satisfied by both :class:`ProjectGroup` (the launch-side view) and
    ``kanibako.project.workset.Workset`` (the workset-command view), so the workset
    file derivations below serve every caller through ONE expression.
    """

    @property
    def root(self) -> Path: ...


@overload
def workset_settings_path(group: _WorksetRooted) -> Path: ...
@overload
def workset_settings_path(group: None) -> None: ...


def workset_settings_path(group: _WorksetRooted | None) -> Path | None:
    """THE workset-tier settings-file derivation (spec §2c ALL WORKSETS:
    ``meta.workset.settings`` = ``@meta.workset.path/settings.yaml``).

    ``group.root`` carries ``@meta.workset.path`` for both modes — the PRIMARY
    workset roots at ``@config.primary_workset``, a NAMED workset at its own
    root — so the one expression serves every caller (launch cascade, config
    verbs, ``--effective`` displays).  ``None`` (no group = standalone) has no
    workset tier file.
    """
    return group.root / "settings.yaml" if group is not None else None


def _default_project_group(std: StandardPaths) -> ProjectGroup:
    """The PRIMARY (default) workset's :class:`ProjectGroup`.

    Spec §2c: the PRIMARY workset roots at ``@config.primary_workset`` — the
    workset-tier settings/env files derive from this root (F4).
    ``local_shared_base`` stays the data path (the legacy ``shared/``
    location).  Also emits the one-shot legacy-settings warning (see
    :func:`warn_legacy_primary_settings`).
    """
    warn_legacy_primary_settings(std)
    return ProjectGroup(
        name="default",
        root=std.primary_workset,
        is_default=True,
        local_shared_base=std.data_path,
    )


_legacy_primary_settings_warned = False


def warn_legacy_primary_settings(std: StandardPaths) -> None:
    """One-shot warning for a leftover legacy ``<data>/settings.yaml``.

    1.6.0's launch cascade read the primary workset's settings from
    ``@config.data/settings.yaml`` (a location no shipped code ever wrote);
    the spec §2c file is ``@config.primary_workset/settings.yaml``.  Migration
    ruling (2026-07-02, option (c) drop + document): the legacy file is NOT
    read and NOT touched — warn while it exists without the spec file so a
    hand-migrated 1.6.0 install notices, then fall silent once the spec file
    exists.
    """
    global _legacy_primary_settings_warned
    if _legacy_primary_settings_warned:
        return
    legacy = std.data_path / "settings.yaml"
    spec_file = std.primary_workset / "settings.yaml"
    if legacy.is_file() and not spec_file.is_file():
        import sys

        _legacy_primary_settings_warned = True
        print(
            f"warning: {legacy} is no longer read — 1.7.0 moved the primary "
            f"workset's settings to {spec_file}. Move wanted values there, or "
            "re-set them via 'kanibako workset set default <key>=<value>'.",
            file=sys.stderr,
        )


@dataclass
class ProjectPaths:
    """Resolved paths for a specific project."""

    project_path: Path
    project_hash: str
    metadata_path: Path      # host-only: settings.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    vault_ro_path: Path      # {project}/vault/ro (→ /home/agent/vault/ro)
    vault_rw_path: Path      # {project}/vault/rw (→ /home/agent/vault/rw)
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    enable_vault: bool = field(default=True)
    name: str = field(default="")
    group: ProjectGroup | None = field(default=None)


def box_tree_materialized(proj: ProjectPaths) -> bool:
    """True when the box tree a ``create`` would materialize is ALREADY on disk.

    The MODE-AWARE analogue of ``is_new``, computable from a NON-materialising
    probe (``initialize=False``) — which is the whole point: ``is_new`` is only
    set inside the ``initialize=True`` branch that does the mutation, so a caller
    that wants to REFUSE before mutating cannot ask ``is_new`` and has to ask
    this instead.

    * PRIMARY / NAMED — the box dir IS ``metadata_path``, and
      :func:`resolve_project` gates ``is_new`` on exactly that dir, so with
      ``initialize=True`` this is precisely ``not is_new``.
    * STANDALONE — ``metadata_path`` is the USER'S OWN project root, which always
      exists (it is their runtime dir), so it says nothing.  The marker is
      ``<root>/box_data`` — the same dir :func:`resolve_standalone_project` gates
      ``is_new`` on.

    ⚑ For a BRAND-NEW primary box ``_resolve_local_dir`` misses and
    ``metadata_path`` is the ``__unregistered__`` placeholder, which does not
    exist ⇒ False ⇒ the create proceeds.  That coupling is inherited from
    ``resolve_project``'s own ``is_new`` gate, not introduced here.

    The mode split is NOT restated here — :func:`box_metadata_dir` already owns
    it, and it lands on exactly the two dirs the two resolvers gate ``is_new``
    on.  One derivation, not a second copy that can drift.
    """
    return box_metadata_dir(proj.mode, proj.metadata_path).is_dir()


def _standalone_settings_files(root: Path) -> tuple[Path, Path]:
    """The STANDALONE ``(box_tier, workset_tier)`` pair — BOTH always real paths.

    The standalone arm of :func:`_box_settings_files`, split out because it is the one
    mode whose workset tier is unconditional (it is the project ROOT file, not a
    ``ProjectGroup`` lookup that can come back ``None``).  Callers inside the
    standalone resolver need that stronger type — they read and write both files — and
    getting it from the type system beats asserting it at each site.  Still ONE
    derivation: ``_box_settings_files`` delegates here rather than restating it.
    """
    return root / _STANDALONE_META_DIR / BOX_META_FILE, root / BOX_META_FILE


def box_metadata_dir(mode: BoxMode, metadata_path: Path) -> Path:
    """The DIR holding a box's own metadata — home, session state, box tier.

    Equals ``metadata_path`` for primary/named, but for STANDALONE
    ``metadata_path`` is the PROJECT ROOT, whose box metadata lives one level down
    in ``box_data/`` (beside ``workspace/`` and ``vault/``, which are NOT box
    metadata).

    ⚑ Lifecycle ops (convert / move / duplicate) must copy from HERE, not from
    ``metadata_path``.  Copying a standalone ROOT "as if it were box metadata"
    both drags the workspace + vault into the destination's box dir AND delivers
    the source's WORKSET-tier file to the destination's BOX tier — which after P2
    means the real box settings (one level down) are never read again.
    """
    if mode is BoxMode.standalone:
        return metadata_path / _STANDALONE_META_DIR
    return metadata_path


def _box_settings_files(
    mode: BoxMode,
    metadata_path: Path,
    group: "ProjectGroup | None",
) -> tuple[Path, Path | None]:
    """THE ``(box_tier, workset_tier)`` settings-file derivation (spec §2c).

    ONE expression, spelled ONCE.  ``meta.box.settings`` is the UNIFORM
    ``@meta.box.path/settings.yaml`` in EVERY mode (spec §2c ALL PROJECTS), so
    the box tier is ALWAYS a real path — never ``None``:

    * **primary / named** — box tier = the box's own ``<metadata_path>/settings.yaml``
      (``BOX_META_FILE``, which IS ``@meta.box.path`` for these modes); workset tier =
      ``workset_settings_path(group)`` (the workset root's ``settings.yaml``).
    * **standalone** — ``@meta.box.path`` is the ``box_data/`` marker dir (the empty
      leaf of ``@workset.boxes``), so the box tier is
      ``<root>/box_data/settings.yaml`` — **ABSENT BY DEFAULT** (spec §5): an
      absent file is an empty tier, and ``config_io.load_doc`` yields ``{}`` for it,
      so a standalone box with no box file resolves byte-identically to one with no
      box tier at all.  The workset tier is the ROOT ``<root>/settings.yaml`` — the
      file that plays the WORKSET tier for a degenerate one-box workset, and the file
      §5 DETECTION reads (``box_resolve.standalone_settings_present``).  A ``box.*``
      key stored THERE still resolves for box scope via R2 downward-defaults
      (``box`` ⊂ ``workset`` in ``SCOPE_CONTAINMENT`` — the workset-tier read KEEPS
      ``box.*``), which is exactly how a pre-P2 standalone box keeps working with no
      migration.

    ⚑ This pair is the SINGLE SOURCE for READ, WRITE **and** ANCHOR (M-8): the launch
    cascade's ``box_path``/``workset_path``, the ``meta.box.settings`` anchor, and the
    ``config set`` / ``get`` / ``show`` / ``reset`` target all derive from here.  A
    second, independent spelling of either path is the M-8 bug ("I set it and nothing
    changed") and must not be re-introduced.

    ⚑ The return type says ``Path``, not ``Path | None``, deliberately: the
    non-optional box tier is TYPE-CHECKED, so mypy rejects any re-introduction of a
    ``None`` box tier and flags any stale ``is None`` narrowing at a call site.

    Takes the three primitives rather than a :class:`ProjectPaths` because the
    standalone/primary/named resolvers must consult it WHILE that dataclass is still
    being constructed (``enable_vault`` is read before the instance exists).
    :func:`box_workset_settings_paths` is the ``ProjectPaths`` adapter.
    """
    if mode is BoxMode.standalone:
        return _standalone_settings_files(metadata_path)
    return metadata_path / BOX_META_FILE, workset_settings_path(group)


def box_workset_settings_paths(proj: ProjectPaths) -> tuple[Path, Path | None]:
    """The :class:`ProjectPaths` ADAPTER over :func:`_box_settings_files`.

    The name every caller uses.  Carries no logic of its own — the derivation lives
    in one place so the pair cannot be spelled twice.
    """
    return _box_settings_files(proj.mode, proj.metadata_path, proj.group)


class _WorksetLike(Protocol):
    """Structural type for the attributes :meth:`WorksetSpec.from_workset` reads.

    Avoids importing the concrete :class:`kanibako.project.workset.Workset` into
    ``paths.py`` (which ``workset.py`` imports from, creating a cycle).
    """

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


class _WorksetProjectLike(Protocol):
    """Structural type for the workset project attributes read here."""

    @property
    def name(self) -> str: ...

    @property
    def source_path(self) -> Path: ...


@dataclass(frozen=True)
class WorksetSpec:
    """Primitive view of a workset, decoupled from :class:`kanibako.project.workset.Workset`.

    Carries only the values the path resolver and project listings need, so
    ``paths.py`` does not import the ``workset`` module (which depends on
    ``paths.py``).  Callers holding a full ``Workset`` build one with
    :meth:`from_workset`.
    """

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
        return cls(
            name=ws.name,
            root=ws.root,
            projects_dir=ws.projects_dir,
            workspaces_dir=ws.workspaces_dir,
            vault_dir=ws.vault_dir,
            project_names=tuple(p.name for p in ws.projects),
            is_default=ws.is_default,
        )


logger = get_logger("paths")


# Spec defaults for the XDG base directories that HAVE one (freedesktop Base
# Directory spec).  ``XDG_RUNTIME_DIR`` is deliberately ABSENT — it has no spec
# default and is handled specially by :func:`resolve_xdg` (fallback + warn).
_XDG_SPEC_DEFAULTS: dict[str, str] = {
    "XDG_DATA_HOME": ".local/share",
    "XDG_CONFIG_HOME": ".config",
    "XDG_STATE_HOME": ".local/state",
    "XDG_CACHE_HOME": ".cache",
}


def resolve_xdg(var_name: str, spec_default_suffix: str | None) -> Path:
    """Resolve an XDG base directory per the freedesktop Base Directory spec.

    The environment variable *var_name* is honored **iff it is set AND
    absolute**; a relative value is INVALID per the spec and is ignored (we fall
    back to the default).  When unset/invalid:

    * For the dirs that have a spec default (*spec_default_suffix* is the suffix
      under ``$HOME``, e.g. ``".local/share"``), return ``$HOME/<suffix>``.
    * For ``XDG_RUNTIME_DIR`` (*spec_default_suffix* is ``None``) there is NO
      spec default: fall back to a replacement dir with similar capabilities and
      **warn** — prefer ``/run/user/<uid>/kanibako`` when it is usable
      (writable, owned by us), else a ``0700`` temp dir.

    An absolute env value is returned as-is (resolved); the runtime-dir fallback
    is the only case that creates a directory.
    """
    val = os.environ.get(var_name, "")
    if val:
        if os.path.isabs(val):
            return Path(val).resolve()
        # Relative value: invalid per spec → ignore and fall through to default.
        logger.warning(
            "%s=%r is relative (not absolute); ignoring per the XDG Base "
            "Directory spec and using the default.",
            var_name,
            val,
        )

    if spec_default_suffix is not None:
        return Path.home() / spec_default_suffix

    # XDG_RUNTIME_DIR has no spec default — pick a replacement and warn.
    return _fallback_runtime_dir(var_name)


# Process-lifetime cache of the chosen runtime-dir fallback, keyed by var name.
# Without this, a temp-dir fallback would leak a NEW dir (and re-warn) on every
# ``resolve_system_paths`` call within a single process.  The cache is keyed by
# (var_name, env value) so a test/process that later SETS the var is unaffected.
_runtime_fallback_cache: dict[tuple[str, str], Path] = {}


def _fallback_runtime_dir(var_name: str) -> Path:
    """Choose a replacement for an unset/invalid ``XDG_RUNTIME_DIR`` and warn.

    Prefers ``/run/user/<uid>/kanibako`` when ``/run/user/<uid>`` exists and is
    a writable directory owned by us; otherwise a ``0700`` per-user temp dir.
    Never substitutes silently — warns on first selection (cached per process so
    repeated calls don't leak temp dirs or re-warn).
    """
    cache_key = (var_name, os.environ.get(var_name, ""))
    cached = _runtime_fallback_cache.get(cache_key)
    if cached is not None and cached.is_dir():
        return cached

    uid = os.getuid()
    run_user = Path(f"/run/user/{uid}")
    if _runtime_base_usable(run_user):
        chosen = run_user / "kanibako"
        chosen.mkdir(mode=0o700, parents=True, exist_ok=True)
        logger.warning(
            "%s is not set; falling back to %s for runtime files (helper "
            "sockets). Set %s to a per-user runtime dir to silence this.",
            var_name,
            chosen,
            var_name,
        )
        _runtime_fallback_cache[cache_key] = chosen
        return chosen

    # Last resort: a 0700 temp dir under the system temp root.
    chosen = Path(tempfile.mkdtemp(prefix="kanibako-runtime-"))
    chosen.chmod(0o700)
    logger.warning(
        "%s is not set and /run/user/%d is unusable; falling back to the "
        "temp dir %s for runtime files. Set %s to a persistent per-user "
        "runtime dir to silence this.",
        var_name,
        uid,
        chosen,
        var_name,
    )
    _runtime_fallback_cache[cache_key] = chosen
    return chosen


def _runtime_base_usable(base: Path) -> bool:
    """True iff *base* is a directory we own and can write to.

    Mirrors the freedesktop requirement that the runtime dir be owned by the
    user and writable.  Best-effort: any OS error → not usable.
    """
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
    """Resolve an XDG directory from environment or default under $HOME.

    Backward-compatible thin wrapper over :func:`resolve_xdg`: honors the env
    var only when set AND absolute (a relative value is now ignored per the
    spec), else returns ``$HOME/<default_suffix>``.  Used by the many call
    sites that just need a spec-backed XDG base dir.
    """
    return resolve_xdg(env_var, default_suffix)


# ---------------------------------------------------------------------------
# Layer 1 — the CONFIG-key FOUNDATION (spec §1; config keys finalized at 5)
# ---------------------------------------------------------------------------
#
# The 5 bootstrap CONFIG keys live in ``kanibako_config.yaml`` (`.config`/`/etc`)
# and resolve via a flat foundation resolver, NOT the keyspace pipeline
# (chicken-and-egg: the pipeline needs these resolved to find its own input
# files).  ``config.global`` is ELIMINATED — its children inline
# ``@config.data/global/...`` (the ``global/`` dir is created on demand by the
# atomic writer when those files are first written).  ``@config.*`` refs resolve
# against THIS set; ``$XDG_*`` against the environment.
CONFIG_PATH_DEFAULTS: dict[str, str] = {
    "config.data": "$XDG_DATA_HOME/kanibako",
    "config.settings": "@config.data/global/settings.yaml",
    "config.agents": "@config.data/agents",
    "config.primary_workset": "@config.data/primary_workset",
    "config.registry": "@config.data/global/registry.yaml",
    # The LIFECYCLE JOURNAL (write-ahead log of in-flight box-lifecycle ops),
    # beside the registry.  The registry is the steady-state truth; the journal
    # is the transient truth (normally empty).  See ``kanibako.launch.journal``.
    "config.journal": "@config.data/global/journal.yaml",
}


# ---------------------------------------------------------------------------
# Layer 2 — system-scope SETTINGS keys that are PATHS (spec §1/§2g)
# ---------------------------------------------------------------------------
#
# These are SETTINGS keys (system tier), NOT bootstrap config: each ``@``-refs a
# Layer-1 config key (or an XDG base).  They resolve the normal way at launch
# (assemble→merge→expand) — but the flat resolver below ALSO materializes them
# into :class:`StandardPaths` (the legacy host-side path surface) by resolving
# ``@config.*`` against the Layer-1 foundation.  ``channelroot`` moved to Layer 2
# (its skeleton is created on the launch path, not at setup).  The OLD per-leaf
# ``boxes`` location is resolved separately (see :func:`resolve_system_paths`)
# and is NOT a key here.
SYSTEM_PATH_DEFAULTS: dict[str, str] = {
    "system.backup": "@config.data/backup",
    "system.channelroot": "@config.data/channels",
    # M-11 (2026-07-30): ``system.base_template`` → ``system.template``, and the
    # default moved ``global/base_template`` → ``global/template`` at the same time.
    # ⚑ The on-disk dir MOVES with it: an existing install's populated (possibly
    # user-edited) ``global/base_template/`` is ORPHANED by the rename — setup
    # re-installs packaged content at the new root and the old dir is left behind.
    # Documentation-only, deliberately: no code auto-migrates a user's store.
    "system.template": "@config.data/global/template",
    # The SYSTEM canon CONTRIBUTION root (spec §2g). Same indirection as
    # ``system.template``: holding the ROOT leaves room for further canon subtrees
    # without new keys, and its ``handbook/`` is what binds into a box.
    "system.canon": "@config.data/global/canon",
    "system.cache": "$XDG_CACHE_HOME/kanibako",
    "system.runtime": "$XDG_RUNTIME_DIR/kanibako",
    # Channels skeleton (the type-roots derive from system.channelroot).
    "system.channels.common": "@system.channelroot/common",
    "system.channels.chat": "@system.channelroot/chat",
    "system.channels.broadcast": "@system.channels.chat/broadcast.md",
    "system.channels.mailboxes": "@system.channelroot/mailboxes",
    "system.channels.share": "@system.channelroot/share",
}


def host_xdg_map(data_home: Path | None = None) -> dict[str, str]:
    """Build the canonical HOST-side ``$XDG_*`` map for a ``ResolveCtx``.

    THE single builder for the ``xdg=`` argument of every host-side
    :class:`~kanibako.settings.settings_resolve.ResolveCtx` (Jei ruling 2026-07-02: XDG
    vars must have fallbacks).  Hand-rolled partial maps caused stored values
    like ``$XDG_CACHE_HOME/kanibako`` (the setup-materialized ``system.cache``)
    to raise ``Variable $XDG_CACHE_HOME is not set in this context`` at expand
    time — the resolver reads ONLY this map, never the environment, so a
    missing key is unrecoverable at the call site.

    Every var in :data:`_XDG_SPEC_DEFAULTS` resolves via the hardened
    :func:`resolve_xdg` (env honored iff set AND absolute; unset/empty/relative
    → the XDG-spec default under ``$HOME``), plus ``XDG_RUNTIME_DIR`` (no spec
    default: fallback + warn).  *data_home* is an optional already-resolved
    ``$XDG_DATA_HOME`` anchoring the default tree (the flat foundation/system
    resolver passes it); when None it resolves from the environment like the
    rest.
    """
    xdg_map: dict[str, str] = {}
    for name, suffix in _XDG_SPEC_DEFAULTS.items():
        if name == "XDG_DATA_HOME" and data_home is not None:
            # Already resolved by the caller — don't re-read the env (a
            # relative env value would re-warn on every call).
            xdg_map[name] = str(data_home)
        else:
            xdg_map[name] = str(resolve_xdg(name, suffix))
    xdg_map["XDG_RUNTIME_DIR"] = str(resolve_xdg("XDG_RUNTIME_DIR", None))
    return xdg_map


def resolve_config_paths(
    set_values: Mapping[str, str], *, data_home: Path, home: Path,
) -> dict[str, str]:
    """Resolve the Layer-1 CONFIG-key foundation to concrete host paths.

    *set_values* holds raw user-set ``config.<leaf>`` expressions (from the
    ``kanibako_config.yaml`` set).  Returns ``{config.<key>: resolved_str}`` for
    every key in :data:`CONFIG_PATH_DEFAULTS` — the FOUNDATION mapping injected
    into :class:`~kanibako.settings.settings_resolve.ResolveCtx.config` so ``@config.*``
    refs resolve there (spec §1A / JC-2).  Flat by design (chicken-and-egg): the
    keyspace pipeline needs these resolved to find its own input files, so they
    resolve OUTSIDE it, with ``@config.*`` refs chained within this set only.
    """
    xdg_vars = host_xdg_map(data_home)
    ctx = ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(home),
        xdg=xdg_vars,
    )
    levels = [
        LevelView("config", values=dict(set_values), defaults=CONFIG_PATH_DEFAULTS)
    ]

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        return expand_expr(
            str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain,
        )

    resolved: dict[str, str] = {}
    for key in CONFIG_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(f"Unresolvable config path: {key}")
        resolved[key] = expand_expr(
            str(rv.value), space="host", ctx=ctx, lookup=lookup,
        )
    return resolved


def resolve_system_paths(
    set_values: Mapping[str, str],
    *,
    data_home: Path,
    home: Path,
) -> dict[str, Path]:
    """Resolve the path tier to concrete host paths.

    *set_values* holds raw user-set expressions keyed by their full dotted name —
    the MERGED config-file set (both Layer-1 ``config.<leaf>`` and Layer-2
    ``system.<leaf>`` keys, e.g. the global config's ``config_paths``).  It is
    split here by prefix: ``config.*`` seeds the Layer-1 foundation,
    ``system.*`` the Layer-2 path settings.  *data_home* is the already-resolved
    XDG data base exposed as ``$XDG_DATA_HOME``; *home* expands a leading ``~``.

    Returns ``{full_dotted_key: Path}`` for every Layer-1 ``config.*`` key AND
    every Layer-2 ``system.*`` key, plus the derived PRIMARY-workset pseudo-keys
    ``system._boxes`` / ``system._primary_vault_ro`` /
    ``system._primary_vault_rw`` / ``system._primary_logs`` (under
    ``@config.primary_workset``).  The ``system.*`` defaults ``@``-ref a Layer-1
    config key, resolved against the foundation injected into ``ctx.config``.
    """
    xdg_vars = host_xdg_map(data_home)

    # Split the merged set-values by layer prefix.
    config_set = {k: v for k, v in set_values.items() if k.startswith("config.")}
    set_values = {k: v for k, v in set_values.items() if k.startswith("system.")}

    # Layer 1: resolve the config-key foundation first (chicken-and-egg).
    config = resolve_config_paths(
        config_set, data_home=data_home, home=home,
    )

    ctx = ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(home),
        xdg=xdg_vars,
        config=config,
    )
    levels = [
        LevelView("system", values=dict(set_values), defaults=SYSTEM_PATH_DEFAULTS)
    ]

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        # Resolver SPLIT (spec §1A / JC-2): ``@config.*`` → the Layer-1 foundation
        # (``ctx.config``); ``@system.*`` → the system path set.  Prefix-driven.
        if ref.startswith("config."):
            try:
                return config[ref]
            except KeyError:
                raise SettingsError(f"Unknown @config-reference: {ref}") from None
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        # system.* config paths are always scalar strings (no structured
        # category leaves at this tier); narrow the now-``object``-typed value.
        return expand_expr(
            str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain,
        )

    resolved: dict[str, Path] = {}
    # Layer 1 foundation paths are surfaced under their ``config.*`` keys.
    for key, val in config.items():
        resolved[key] = Path(val)
    # Layer 2 system path keys, resolving ``@config.*`` via the foundation.
    for key in SYSTEM_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(f"Unresolvable system path: {key}")
        expanded = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
        resolved[key] = Path(expanded)

    # PRIMARY-workset box/vault/logs roots, derived from the resolved PRIMARY
    # workset dir (``@config.primary_workset``).
    pw = resolved["config.primary_workset"]
    resolved["system._boxes"] = pw / "boxes"
    resolved["system._primary_vault_ro"] = pw / "vault" / "ro"
    resolved["system._primary_vault_rw"] = pw / "vault" / "rw"
    resolved["system._primary_logs"] = pw / "logs"
    return resolved


def load_system_config(
    user_config_path: Path, *, data_home: Path, home: Path,
) -> dict[str, Path]:
    """Resolve the path tier from the CONFIG file set.

    The CONFIG file set is two files, read in cascade order so the
    most-authoritative present value of each set-value wins **before**
    expression resolution:

    1. ``/etc/kanibako/config_base.yaml`` — site-wide overridable defaults
       (least specific).
    2. *user_config_path* — the user's global ``~/.config/kanibako_config.yaml``
       (overrides the base).

    Missing files are skipped (each contributes nothing).  The merged set-values
    are split by prefix into the Layer-1 ``config.*`` foundation and the Layer-2
    ``system.*`` path settings, then handed to :func:`resolve_system_paths`,
    which fills in the defaults and resolves ``@``-/``$XDG_*``-references.

    Back-compat: a user with only ``~/.config/kanibako_config.yaml`` (no ``/etc``
    file) gets the base layer empty, so the user file is the sole set-source.
    """
    # Lazy import to avoid a config <-> paths import cycle at module load.
    from kanibako.settings.config import (
        config_base_path,
        load_config,
    )

    raw: dict[str, str] = {}
    # base < user.  load_config(...).config_paths yields the file's set-values
    # keyed by their full dotted name (``config.*`` / ``system.*``), or {} when
    # the file is absent — so missing layers are skipped automatically.
    raw.update(load_config(config_base_path()).config_paths)
    raw.update(load_config(user_config_path).config_paths)

    return resolve_system_paths(raw, data_home=data_home, home=home)


def load_std_paths(config: KanibakoConfig | None = None) -> StandardPaths:
    """Compute all standard kanibako directories.

    If *config* is None, it is loaded from the config file (which must exist).
    Directories are created as needed.
    """
    config_home = xdg("XDG_CONFIG_HOME", ".config")
    data_home = xdg("XDG_DATA_HOME", ".local/share")
    state_home = xdg("XDG_STATE_HOME", ".local/state")
    cache_home = xdg("XDG_CACHE_HOME", ".cache")

    config_file = config_file_path(config_home)

    if config is None:
        if not config_file.exists():
            raise ConfigError(
                f"{config_file} is missing. Run any kanibako command to initialize."
            )
        config = load_config(config_file)

    # Resolve the system-level path tier (settings-framework "system.path.*")
    # from the CONFIG file set: /etc config_base < user-global.  A user with
    # only ~/.config/kanibako_config.yaml gets the prior behavior (empty /etc layer).
    resolved = load_system_config(
        config_file, data_home=data_home, home=Path.home(),
    )
    data_path = resolved["config.data"]
    # state/cache paths track the data dir's leaf name (unchanged behavior:
    # default leaf "kanibako" under each XDG base).
    rel = data_path.name
    state_path = state_home / rel
    cache_path = cache_home / rel

    # Ensure directories exist.
    config_file.parent.mkdir(parents=True, exist_ok=True)
    data_path.mkdir(parents=True, exist_ok=True)
    state_path.mkdir(parents=True, exist_ok=True)
    cache_path.mkdir(parents=True, exist_ok=True)

    return StandardPaths(
        config_home=config_home,
        data_home=data_home,
        state_home=state_home,
        cache_home=cache_home,
        config_file=config_file,
        data_path=data_path,
        state_path=state_path,
        cache_path=cache_path,
        data=resolved["config.data"],
        backup=resolved["system.backup"],
        agents=resolved["config.agents"],
        channels=resolved["system.channelroot"],
        template=resolved["system.template"],
        canon=resolved["system.canon"],
        settings=resolved["config.settings"],
        primary_workset=resolved["config.primary_workset"],
        registry=resolved["config.registry"],
        journal=resolved["config.journal"],
        cache=resolved["system.cache"],
        runtime=resolved["system.runtime"],
        channels_common=resolved["system.channels.common"],
        channels_chat=resolved["system.channels.chat"],
        channels_broadcast=resolved["system.channels.broadcast"],
        channels_mailboxes=resolved["system.channels.mailboxes"],
        channels_share=resolved["system.channels.share"],
        boxes=resolved["system._boxes"],
        primary_vault_ro=resolved["system._primary_vault_ro"],
        primary_vault_rw=resolved["system._primary_vault_rw"],
        primary_logs=resolved["system._primary_logs"],
    )


def resolve_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    enable_vault: bool | None = None,
    name_override: str | None = None,
    register: bool = True,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths (PRIMARY mode).

    When *initialize* is True (used by ``start``), missing project directories
    are created and credential templates are copied in.  When False (used by
    subcommands like ``archive``/``purge``), the paths are merely computed.

    Phase 5: PRIMARY boxes/vault/logs live under ``@config.primary_workset``
    (the real PRIMARY-workset dir); there is no layout axis.  Per-box state is
    ``boxes/<name>/`` (metadata + shell) with the vault at
    ``@config.primary_workset/vault/{ro,rw}/<name>``.

    *enable_vault* controls whether vault directories are created and mounted.
    Defaults to True for new projects; existing projects read from ``settings.yaml``.

    *register* (B3 interrupted-create journal): when False AND this call
    materializes a NEW box, the box dir + meta are created and ``is_new`` is set,
    but the PRIMARY membership is NOT written — the caller defers registration
    until AFTER the home seed (journal entry → seed → register → clear-entry) so the
    invariant "registered ⟹ fully seeded" holds on the sole store.  The picked
    name is still reserved against a concurrent create via the directory-aware
    :func:`pick_primary_box_name`.  Defaults True (every other caller registers
    inline, unchanged).
    """
    raw = project_dir or os.getcwd()
    # If the user passed a bare token (no path separator) and no file/dir of
    # that name exists in cwd, try resolving it as a registered project name.
    # Falls through to path resolution on miss so the eventual error stays
    # informative.
    if raw and "/" not in raw and not Path(raw).exists():
        try:
            resolved, kind = resolve_name(
                std.registry, raw, cwd=Path.cwd(),
                primary_workset=std.primary_workset,
            )
            if kind == "project":
                raw = resolved
        except ProjectError:
            pass
    project_path = Path(raw).resolve()

    if not project_path.is_dir():
        raise ProjectError(f"Project path '{project_path}' does not exist.")

    phash = project_hash(str(project_path))
    project_path_str = str(project_path)

    # Determine the project directory: name-based (boxes/{name}/).
    project_name, project_dir_path = _resolve_local_dir(std, project_path_str)

    # Registry reverse-lookup miss.
    #
    # register=True (a normal resolve): P8b/Option A — an unregistered on-disk
    # PRIMARY box is NOT auto-rediscovered.  The registry is the SOLE identity
    # authority; a primary box no longer self-describes on disk (sparse create),
    # so there is nothing on disk to re-import from.  ``project_name`` stays empty
    # and the code falls through to the not-found/create path below (a fresh name
    # is assigned when ``initialize``; otherwise the miss surfaces downstream).
    # The future ``system recover`` is the remedy for a lost registry entry.
    #
    # register=False (a deferred-registration create/recovery resolve): the box's
    # name is read from the pending CREATE JOURNAL entry whose recorded workspace
    # == this workspace (P8b — identity no longer self-describes in on-disk meta).
    # Re-discovering a half-built box during a re-create must NOT prematurely
    # register it (the caller completes seed -> register -> clear-entry), so the
    # resolved name re-associates the on-disk dir directly (the registry is still
    # empty, so _resolve_local_dir would miss again).
    if not project_name and not register:
        from kanibako.launch import journal as journal_mod

        entry = journal_mod.pending_create_for_workspace(
            std.journal, project_path,
        )
        recovered = (entry.get("name") or "").strip() if entry else ""
        if recovered:
            project_name = recovered
            project_dir_path = std.boxes / recovered

    # Registration-layer reverse-lookup (Bug A durable fix — defense in depth).
    #
    # ``_resolve_local_dir`` now reverse-looks-up the PRIMARY membership itself
    # (resolved-path aware), so this arm is a belt-and-suspenders repeat: if the
    # name is still unresolved, consult the PRIMARY-workset ``boxes:`` membership
    # — the SAME registry ``register_workset_box``'s uniqueness guard (Guard 1)
    # writes and ``list``/``box_resolve`` read.  Reusing the existing name/dir
    # here keeps the create branch below from minting a duplicate entry + box dir
    # for a workspace that is ALREADY a member (which would otherwise let Guard 1
    # raise mid-create, after ``_init_project`` already committed the dir, with no
    # unwind → stranded half-box).  The lookup is exception-guarded (a symlink-
    # cycle/permission path must not crash resolve_project) — matching
    # ``_same_workspace``'s own guarding.  (register=False deferred-create is
    # handled by the journal block above and left untouched here.)
    if not project_name and register:
        try:
            _member = _workset_box_name_for_workspace(
                std.primary_workset, project_path_str,
            )
        except (OSError, RuntimeError):
            _member = None
        if _member:
            project_name = _member
            project_dir_path = std.boxes / project_name

    metadata_path = project_dir_path

    # B2b (Option A, Jei-ruled): the per-box meta["shell"]/["vault_ro"]/["vault_rw"]
    # custom-path OVERRIDE is DROPPED.  home/vault are now SOLELY the spec-derived
    # default location (@meta.box.path/home + @workset.vault_{ro,rw}/@meta.box.name;
    # the launch routes the home/vault binds through those @-refs).
    # A user customizing home/vault now sets the box.bindings.{rw,ro}.{home,vault}
    # CASCADE override (which wins naturally), NOT a stored shell path.  The
    # ``shell``/``vault_*`` fields are no longer written to disk at all under sparse
    # create (P8b/Option A) — they are always the spec-derived default location.
    project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)
    shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(
        std, metadata_path, project_name or metadata_path.name,
    )
    # enable_vault (P5a): an explicit param wins; otherwise the stored box-scope
    # ``box.enable_vault`` (absent ⇒ True).  Decoupled from box identity — which
    # now derives from the registries (``box_resolve``), not ``read_project_meta``.
    # NO ``default_from``: PRIMARY reads the box tier ONLY, exactly as before P2.
    # Extending the R2 workset fallback here would make a workset-tier
    # ``box.enable_vault`` — today an inert silent no-op — go live for every box in
    # the workset; a real defect, tracked separately, deliberately NOT this phase's.
    actual_vault_enabled = (
        enable_vault if enable_vault is not None
        else read_box_enable_vault(project_toml)
    )

    is_new = False
    if initialize and not project_dir_path.is_dir():
        # Guard: refuse to implicitly create a project rooted at $HOME.
        if project_path == Path.home().resolve():
            raise ProjectError(
                "Refusing to create a project rooted at $HOME — this would "
                "mount your entire home directory as the workspace.\n"
                "If you really want a project here, use:\n"
                "  kanibako create --standalone ~ --allow-home"
            )
        # New project: SELECT a name first (no store write here), then create
        # boxes/{name}/ and register the PRIMARY membership below.  The former
        # global ``projects:`` name registry retired (2026-07-08); the primary
        # per-workset ``boxes:`` membership is the SOLE store now.
        # An explicit override (e.g. `kanibako create --name X`) registers
        # strictly; collisions error rather than auto-suffix — so for a normal
        # (register=True) create the override is validated against the PRIMARY-box
        # name domain (membership ∪ workset names) UP FRONT, before the dir is
        # materialized.  A deferred (register=False) create leaves that domain
        # check to the post-seed commit (``_register_new_box``).
        # B3: whichever branch, the name is only SELECTED here (directory-aware,
        # so a half-built box's dir keeps its name reserved); the membership write
        # happens once, below — eager for register=True, deferred to the caller
        # for register=False (invariant "registered ⟹ fully seeded").
        if name_override:
            if register:
                check_primary_box_name_free(
                    std.primary_workset, std.registry,
                    name_override, project_path_str,
                )
            project_name = name_override
        elif project_name:
            # The reverse-lookup above (Bug A) matched this workspace to an
            # ALREADY-registered box name — its dir is just missing here, so
            # reuse the name and (re)register the membership below (idempotent —
            # same name → same path is a no-op overwrite).
            pass
        else:
            project_name = pick_primary_box_name(
                std.primary_workset, std.registry, project_path_str,
                boxes_dir=std.boxes,
            )
        project_dir_path = std.boxes / project_name
        metadata_path = project_dir_path
        # Recompute paths with the name-based directory.
        shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(
            std, metadata_path, project_name,
        )
        project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)

        # Creation ownership for the unwind below: capture whether the box dir
        # ALREADY existed at its FINAL (post-``name_override`` reassignment) path,
        # BEFORE ``_init_project`` merges into it (``mkdir(exist_ok=True)``).  A
        # ``--name X`` pointing at a pre-existing orphan ``std.boxes/X`` must NOT
        # be ``rmtree``d on a Guard-1 raise — that would delete a pre-existing
        # box's ``home/`` (credentials, session state).  The unwind only removes a
        # dir THIS call created.
        _dir_existed = project_dir_path.is_dir()

        _init_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        # Sparse create (P8b/Option A): NO ``project:``/``resolved:`` identity is
        # written — the box's identity + workspace live in the PRIMARY per-workset
        # ``boxes:`` membership (``box_resolve`` reads it), registered just below.
        # Only a NON-default ``box.enable_vault`` is persisted, sparsely.
        write_box_enable_vault(project_toml, actual_vault_enabled)
        # Register the PRIMARY membership (name → external workspace) — the SOLE
        # store since the global ``projects:`` section retired.  The PRIMARY
        # workset is NON-EXCEPTIONAL (D0/D1): its registry is anchored by
        # ``std.primary_workset``.  Idempotent — ``register_workset_box``
        # overwrites a moved box's path.
        #
        # register=False DEFERS this write to the caller's post-seed commit
        # (``_register_new_box`` → ``register_primary_box_name_if_absent``) so the
        # invariant "registered ⟹ fully seeded" holds on the sole store; the
        # membership is written eagerly only for a normal (register=True) create.
        #
        # Belt-and-suspenders unwind: Guard 2 above normally pre-empts Guard 1
        # (the workspace-path uniqueness check in ``register_workset_box``) by
        # reusing the existing name.  If Guard 1 STILL refuses here — e.g. an
        # explicit ``--name`` claims a workspace already a member under a
        # different name — ``resolve_project`` has no outer unwind, so roll back
        # the box dir (ONLY if THIS call created it — never delete a pre-existing
        # orphan's ``home/``) so a genuine invariant violation fails CLEAN (no
        # stranded half-box), then re-raise.  The membership write is the sole
        # store, so a Guard-1 raise leaves nothing else to unwind.
        if register:
            try:
                _register_workset_box_membership(
                    std.primary_workset, project_name, project_path,
                )
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
        # P8b/Option A: NO settings.yaml backfill.  A primary box's identity,
        # workspace and ``box.enable_vault`` all live in the registries now (not a
        # self-describing ``project:``/``resolved:`` on disk), so a box dir lacking
        # a settings.yaml is not a defect to repair here — nothing needs the file
        # to exist for a default-vault primary box.  (The former pre-v0.8 backfill
        # materialized an identity that no longer exists on disk.)

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=BoxMode.primary,
        enable_vault=actual_vault_enabled,
        name=project_name,
        group=_default_project_group(std),
    )


def _resolve_local_dir(
    std: StandardPaths,
    project_path_str: str,
) -> tuple[str, Path]:
    """Find the boxes directory for a default-mode project.

    Reverse-looks-up the project name in the PRIMARY per-workset ``boxes:``
    membership (the sole store since the global ``projects:`` section retired) and
    returns ``(project_name, std.boxes/{name}/)``.  The membership reverse-lookup
    is resolved-path aware (via :func:`primary_box_name_for_workspace`), so a
    symlink/normalization alias of the stored workspace still matches.

    Returns ``("", empty_path)`` when no name is registered — the caller
    (``resolve_project``) will assign a name during initialization.  The lookup
    is exception-guarded (an unresolvable registry/path must not crash
    ``resolve_project``) — matching the registration-layer Guard 2.
    """
    try:
        name = primary_box_name_for_workspace(std.primary_workset, project_path_str)
    except (OSError, RuntimeError):
        name = None
    if name is not None:
        return name, std.boxes / name

    return "", std.boxes / "__unregistered__"



def _primary_box_paths(
    std: StandardPaths, metadata_path: Path, box_name: str,
) -> tuple[Path, Path, Path]:
    """Fixed PRIMARY-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    Shell lives under the per-box metadata dir (``boxes/<name>/home``); the
    vault lives under the PRIMARY workset (``@config.primary_workset/vault/{ro,
    rw}/<name>``), NOT inside the user's workspace.  Phase 5 moved the PRIMARY
    vault out of the workspace so the PRIMARY workset owns boxes/vault/logs
    just like a named workset.
    """
    shell = metadata_path / "home"
    vault_ro = std.primary_vault_ro / box_name
    vault_rw = std.primary_vault_rw / box_name
    return shell, vault_ro, vault_rw


def _workset_box_paths(
    metadata_path: Path, vault_base: Path, box_name: str,
) -> tuple[Path, Path, Path]:
    """Fixed NAMED-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    Shell under the per-project box dir; vault under the workset's vault dir
    (``<vault_base>/{ro,rw}/<box_name>``).  The ro/rw split nests ABOVE the box
    name to match PRIMARY (``vault/{ro,rw}/<box>``) and STANDALONE.
    """
    shell = metadata_path / "home"
    vault_ro = vault_base / "ro" / box_name
    vault_rw = vault_base / "rw" / box_name
    return shell, vault_ro, vault_rw


def _standalone_box_paths(
    root: Path,
) -> tuple[Path, Path, Path]:
    """Fixed STANDALONE-mode ``(home, vault_ro, vault_rw)`` (no layout axis).

    All host state lives inside the project *root*: the agent home is
    ``<root>/box_data/home`` (the ``box_data/`` marker dir also holds the
    ``<box>.jsonl`` helper log), and the vault lives at ``<root>/vault/{ro,rw}``
    (per the §2c STANDALONE table).  The box ``settings.yaml`` lives at
    ``<root>/settings.yaml`` (the root, NOT ``box_data/``) and the workspace is
    the ``<root>/workspace`` subdir — both handled by the callers, not here.
    """
    home = root / _STANDALONE_META_DIR / "home"
    vault_ro = root / "vault" / "ro"
    vault_rw = root / "vault" / "rw"
    return home, vault_ro, vault_rw


def helper_log_path(std: StandardPaths, proj: ProjectPaths) -> Path:
    """Per-box, per-mode HOST path for the helper message log.

    The log is the host source of the read-only ``helpers.jsonl`` bind into the
    box; it lives inside the box's own workset/box tree (never the old shared
    ``@config.data/logs/<id>/`` location):

    * PRIMARY    → ``@config.primary_workset/logs/<box>.jsonl`` (``std.primary_logs``)
    * NAMED      → ``@workset.logs/<box>.jsonl`` (``<workset_root>/logs/<box>``)
    * STANDALONE → ``@meta.workset.path/box_data/<box>.jsonl`` (inside ``box_data/``)

    The caller is responsible for guarantee-creating the parent dir before the
    bind (L7).  The box-side dest is the PINNED ``~/.kanibako/state/helpers.jsonl``
    (declared in ``core-defaults.yaml``), NOT a ``$XDG_STATE_HOME`` expression; see
    :data:`~kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH` for why, and
    ``box_supervisor.project_pinned_xdg`` for the post-boot XDG projection.
    """
    box = proj.name if proj.name else short_hash(proj.project_hash)
    if proj.mode is BoxMode.standalone:
        # The standalone log stays inside the ``box_data/`` marker dir (settings
        # itself now lives at the root, so the log is anchored explicitly under
        # ``metadata_path/box_data`` rather than ``metadata_path``) so the whole
        # standalone tree is drop-in portable.
        return proj.metadata_path / _STANDALONE_META_DIR / f"{box}.jsonl"
    if proj.mode is BoxMode.named:
        # The workset root is carried on the project group (root=ws.root).
        ws_root = proj.group.root if proj.group else proj.metadata_path.parent.parent
        return ws_root / "logs" / f"{box}.jsonl"
    # PRIMARY: the PRIMARY workset's logs dir.
    return std.primary_logs / f"{box}.jsonl"


# `~/.shell.d/*.sh` is a user/template extension point for customizing a box's
# INTERACTIVE shell.  A box user (or a seed/template) drops `*.sh` scripts into
# `~/.shell.d/` and kanibako guarantees `.bashrc` sources them on every interactive
# shell startup — see README "Init scripts".  This is an intentional seam with no
# first-party producers by design: kanibako seeds the source line but never writes
# the scripts themselves.  Scope note: the source line runs ONLY for an interactive
# `.bashrc` (a human attaching to the box); it does NOT reach the agent (exec'd
# directly), the agent's `bash -c` tool calls (non-interactive), or the launch env.
# To deliver env to the AGENT, use `env.<VAR>` / `secret_path` (§2a/§2d) instead.
_SHELL_D_SOURCE_LINE = 'for _f in ~/.shell.d/*.sh; do [ -r "$_f" ] && . "$_f"; done\nunset _f'


def _bootstrap_shell(shell_path: Path) -> None:
    """Write minimal shell skeleton files into a new shell directory."""
    bashrc = shell_path / ".bashrc"
    if not bashrc.exists():
        bashrc.write_text(
            "# kanibako shell environment\n"
            "[ -f /etc/bashrc ] && . /etc/bashrc\n"
            'export PS1="${KANIBAKO_PS1:-(kanibako) \\u@\\h:\\w\\$ }"\n'
            "# Source user init scripts\n"
            f"{_SHELL_D_SOURCE_LINE}\n"
        )
    profile = shell_path / ".profile"
    if not profile.exists():
        profile.write_text(
            "# kanibako login profile\n"
            "[ -f ~/.bashrc ] && . ~/.bashrc\n"
        )
    # Create shell.d drop-in directory.
    shell_d = shell_path / ".shell.d"
    shell_d.mkdir(exist_ok=True)


def _upgrade_shell(shell_path: Path) -> None:
    """Keep the ``.shell.d`` sourcing seam current on an existing shell directory.

    Ensures the user/template extension point stays wired on every launch,
    including shells created before this seam existed.  Idempotent — safe to
    call every launch.  Creates ``.shell.d/`` if missing and appends the source
    line to ``.bashrc`` if absent.  No-op if *shell_path* does not exist yet.
    """
    if not shell_path.is_dir():
        return
    shell_d = shell_path / ".shell.d"
    shell_d.mkdir(exist_ok=True)

    bashrc = shell_path / ".bashrc"
    if not bashrc.is_file():
        return
    content = bashrc.read_text()
    if ".shell.d/" in content:
        return
    # Append source line.
    if content and not content.endswith("\n"):
        content += "\n"
    content += "# Source user init scripts\n"
    content += f"{_SHELL_D_SOURCE_LINE}\n"
    bashrc.write_text(content)


def _init_common(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
    vault_ro_path: Path,
    vault_rw_path: Path,
    project_path: Path,
    *,
    enable_vault: bool = True,
) -> None:
    """Shared first-time project setup: create directories, bootstrap shell.

    This helper is called by both ``_init_project`` (default) and
    ``_init_standalone_project``.  It performs every step common to both
    modes: print message, create metadata and shell dirs, bootstrap the
    shell, and set up vault directories when enabled.

    Credential copy is handled separately by ``target.init_home()`` in
    ``start.py``, after template application.
    """
    import sys

    print(
        f"[One Time Setup] Initializing kanibako in {project_path}... ",
        end="",
        flush=True,
        file=sys.stderr,
    )
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
        gitignore = vault_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("rw/\n")

    print("done.", file=sys.stderr)


def _init_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
    vault_ro_path: Path,
    vault_rw_path: Path,
    project_path: Path,
    *,
    enable_vault: bool = True,
) -> None:
    """First-time project setup: create directories, copy credentials from host."""
    _init_common(
        std, metadata_path, shell_path,
        vault_ro_path, vault_rw_path, project_path,
        enable_vault=enable_vault,
    )



def _find_local_ancestor(target: Path, std: StandardPaths) -> Path | None:
    """Find the deepest registered default-mode project that is an ancestor of *target*.

    Reads the PRIMARY per-workset ``boxes:`` membership (the sole store since the
    global ``projects:`` section retired) and, for each entry whose registered
    workspace path is a prefix of *target*, checks that ``std.boxes/{name}/``
    actually exists on disk.  Among all valid matches, the deepest (most path
    components) wins.  Returns the matched path or ``None``.
    """
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
    """True only if *root* carries the standalone box MARKER (presence-based).

    P5a: the marker is a ``box_data/`` directory under *root* PLUS a box
    ``settings.yaml`` AT THE ROOT (``<root>/settings.yaml``, NOT inside
    ``box_data/``), both PRESENT.  The FILE's existence is the standalone
    self-declaration (design D4) — the former ``box.mode == "standalone"`` field
    read is DROPPED (that field is gone).  A box's own in-place settings
    file is the highest-precedence, authoritative standalone signal and
    OVERRIDES any workset determination (D3-mode #1); requiring both parts keeps
    an unrelated ``box_data/`` directory from being mistaken for a marker.
    Delegates to :func:`box_resolve.standalone_settings_present` (the single
    definition of the presence check).
    """
    from kanibako.launch import box_resolve
    return box_resolve.standalone_settings_present(root)


def detect_project_mode(
    project_dir: Path,
    std: StandardPaths,
    config: KanibakoConfig,
) -> DetectionResult:
    """Infer which project mode applies to *project_dir*.

    Walks ancestor directories (up to ``$HOME`` or filesystem root) looking
    for project markers.  Returns a ``DetectionResult`` with the detected
    mode and the ancestor directory where the marker was found.

    Detection order:
    1. Connected-external — *project_dir* (or an ancestor) is an external
       directory bound to a workset by a live ``boxes:`` connection record.
       Runs FIRST so a force-connected box (which keeps its on-disk marker)
       resolves as its workset box, never re-imported as standalone.
    2. In-place standalone marker — a ``box_data/`` + root ``settings.yaml``
       marker AT *project_dir* declares it standalone (D3-mode #1); OVERRIDES
       workset tree membership.  Imported (registered) on discovery.
    3. Workset — *project_dir* lives inside a registered workset root
       (``workspaces/`` subdirectory first, then the root itself).
    4. Default (name-based) — one-pass scan of ``names.yaml``;
       deepest registered path that is an ancestor of *project_dir* wins.
       Requires ``boxes/{name}/`` to exist on disk.
    5. Walk ancestors for on-disk markers — a ``box_data/`` standalone marker,
       or an unregistered NAMED workset root (a ``settings.yaml`` carrying a
       ``workset.meta`` identity).  Both are drop-in *imported* on discovery
       (registered + an alert to stderr; a name collision REFUSES — see
       :mod:`kanibako.project.import_reconcile`).
    6. Default — ``primary`` mode at the original *project_dir*.
    """
    resolved = project_dir.resolve()
    home = Path.home().resolve()

    # 1. Connected-external check: the path (or an ancestor) is an external
    # directory connected to a workset by a live boxes: record.  This MUST run
    # BEFORE the standalone-marker check (step 2) to preserve the single-registry
    # invariant.  A FORCE-CONNECTED standalone box (absorbed via
    # workset.add_project(force=True)) deliberately KEEPS its on-disk box_data/ +
    # root settings.yaml marker while its global standalone: registry entry is
    # DROPPED and a per-workset boxes: connection entry is written — the box lives
    # in EXACTLY ONE registry (no dual registration).  Such a box is EXTERNAL
    # (outside the workset tree), so the workset-tree check (step 3) does NOT match
    # it; the live connection is what claims it as its named workset box.  Were the
    # marker check to run first, its import_standalone side-effect would re-register
    # the box in standalone:, re-creating the very dual registration that --force
    # removed.  D10: the per-workset registries collectively form the reverse
    # index, scanned by box_resolve (replaces the deleted global connected: index).
    from kanibako.launch import box_resolve
    if box_resolve.find_connected_external_box(resolved, std) is not None:
        return DetectionResult(BoxMode.named, resolved)

    # 2. In-place standalone marker AT the resolved dir (D3-mode #1, marker-first).
    # A box's own in-place settings file is the highest-precedence, authoritative
    # standalone self-declaration and OVERRIDES any workset TREE determination — a
    # workset (even one whose tree physically CONTAINS this box) must NOT be able
    # to "steal" a box that declares itself standalone.  (A LIVE connection is the
    # one exception, resolved by step 1 above: a force-connected box is its workset
    # box, never re-registered as standalone.)  This mirrors the marker-first
    # precedence of box_resolve.detect_box_mode (step 1) and keys on the SAME
    # standalone-marker signal (box_data/ + root settings.yaml, via
    # _is_standalone_meta_dir → box_resolve.standalone_settings_present), so a
    # workset/primary box (which never carries box_data/) is unaffected.  Only the
    # resolved dir itself is inspected here (an ancestor marker is still handled by
    # the step-5 walk below); this matches detect_box_mode, which likewise honors
    # the in-place marker only at project_dir before the workset scan.  A GENUINE
    # nested standalone (with NO connection record) is authoritative on disk →
    # import (register) on discovery, exactly as the step-5 walk does.
    if _is_standalone_meta_dir(resolved):
        from kanibako.project import import_reconcile
        import_reconcile.import_standalone(
            std.registry, resolved, journal=std.journal,
        )
        return DetectionResult(BoxMode.standalone, resolved)

    # 3. Workset check (no walk needed — relative_to handles subdirs).
    ws_result = _check_workset(resolved, std)
    if ws_result is not None:
        return ws_result

    # 4. Name-based default-mode check (one-pass scan, deepest match wins).
    ac_ancestor = _find_local_ancestor(resolved, std)
    if ac_ancestor is not None:
        return DetectionResult(BoxMode.primary, ac_ancestor)

    # 5. Walk ancestors for on-disk markers (standalone box_data/ or an
    # unregistered NAMED workset root).  On-disk metadata is authoritative; a
    # discovered-but-unregistered entity is IMPORTED here (alert + register;
    # collision → refuse) so a dropped-in tree is re-discovered.  The named
    # check runs first at each level: a workset root may itself contain a
    # box_data/ dir, but its settings.yaml workset.meta marker is the more
    # specific identity.
    from kanibako.project import import_reconcile
    from kanibako.project.workset import WORKSET_META_FILE, read_workset_meta

    current = resolved
    while True:
        # NAMED: an unregistered workset root (settings.yaml carrying a
        # workset.meta identity, name not in the registry).  Import it, then the
        # standard workset check resolves it.
        if read_workset_meta(current / WORKSET_META_FILE) is not None:
            import_reconcile.import_named_workset(
                std.registry, current, journal=std.journal,
            )
            ws_after = _check_workset(resolved, std)
            if ws_after is not None:
                return ws_after

        # STANDALONE: the in-place marker — a box_data/ directory alongside a
        # root settings.yaml (presence-only since D4; the former box.mode ==
        # "standalone" field read is DROPPED).  A bare box_data/ directory is not
        # enough — the root settings.yaml must be present too (see
        # _is_standalone_meta_dir → box_resolve.standalone_settings_present).
        if _is_standalone_meta_dir(current):
            import_reconcile.import_standalone(
                std.registry, current, journal=std.journal,
            )
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


def _check_workset(
    resolved_dir: Path,
    std: StandardPaths,
) -> DetectionResult | None:
    """Check whether *resolved_dir* is inside a registered workset.

    Returns a ``DetectionResult`` if found, ``None`` otherwise.
    Checks ``workspaces/`` first (specific project), then the workset root
    itself (inside workset but not necessarily a project workspace).
    """
    from kanibako.project import registry_store
    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workset_workspaces,
    )

    worksets_section = registry_store.load_section(
        std.registry, "worksets"
    )
    if not worksets_section:
        return None

    for _root_str in worksets_section.values():
        ws_root = Path(_root_str).resolve()
        # The resolved ``workset.workspaces`` (repoint honored; default
        # ``@meta.workset.path/workspaces`` — §3.3: real and USED).
        ws_workspaces = resolve_workset_workspaces(
            ws_root, load_workset_settings_doc(ws_root)
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


def _workset_box_name_for_workspace(ws_root: Path, workspace: str) -> str | None:
    """Reverse-look-up *workspace* in *ws_root*'s per-workset ``boxes:`` membership.

    Returns the registered box name (resolved-path aware) or ``None``.  Mirrors
    :func:`_register_workset_box_membership`'s registry-path resolution so both the
    write (Guard 1) and this reverse-lookup (Guard 2) consult the SAME per-workset
    ``boxes:`` registry — the one ``list``/``box_resolve`` read — closing the drift
    where a global-name miss let Guard 1 fire mid-create.
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / "settings.yaml"),
    )
    return workset_registry.reverse_lookup_workset_box(registry_path, workspace)


def _workset_box_workspace_for_name(ws_root: Path, box_name: str) -> str | None:
    """Forward-look-up *box_name* in *ws_root*'s per-workset ``boxes:`` membership.

    Returns the REGISTERED workspace path (the ``boxes:`` entry VALUE) or
    ``None``.  The forward twin of :func:`_workset_box_name_for_workspace`, with
    the same registry-path resolution.  The registered path is authoritative
    (D1b/D3-auth) *wherever a composition epoch put it* — a member registered
    before a ``workset.workspaces`` repoint keeps resolving to its recorded
    workspace, never re-derived from the CURRENT composition (bifrost A0).
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / "settings.yaml"),
    )
    return workset_registry.workset_box_path(registry_path, box_name)


def _register_workset_box_membership(
    ws_root: Path, box_name: str, workspace: Path,
) -> None:
    """Register *box_name* → *workspace* in *ws_root*'s per-workset registry.

    The P5a dual-register helper (D1/D3-auth): resolves the workset's
    ``workset.registry`` path (honoring a repoint via its ``settings.yaml``) and
    records the box's membership.  Idempotent — ``register_workset_box``
    overwrites a moved box's stored path.  Used for both NAMED worksets
    (``ws_root`` = the workset root) and the PRIMARY workset (``ws_root`` =
    ``std.primary_workset`` — NON-EXCEPTIONAL per D0/D1).
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / "settings.yaml"),
    )
    workset_registry.register_workset_box(registry_path, box_name, workspace)


def _unregister_workset_box_membership(ws_root: Path, box_name: str) -> None:
    """Drop *box_name* from *ws_root*'s per-workset registry (compensating action).

    The inverse of :func:`_register_workset_box_membership`: resolves the
    workset's ``workset.registry`` path (honoring a repoint via its
    ``settings.yaml``) and removes the box's ``boxes:`` membership.  Idempotent —
    ``unregister_workset_box`` is a no-op when the file/entry is absent.  Used to
    unwind a connect register and to drop a disconnected external box's D10
    connection record.
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        ws_root, load_doc(ws_root / "settings.yaml"),
    )
    workset_registry.unregister_workset_box(registry_path, box_name)


# ---------------------------------------------------------------------------
# PRIMARY-box name registry (the primary per-workset ``boxes:`` membership).
#
# The SOLE store of default-mode (PRIMARY) box names since the global
# ``projects:`` section retired (clean split, 2026-07-08).  Membership is name →
# EXTERNAL-workspace path in ``@config.primary_workset/registry.yaml`` (spec
# L514, via :mod:`kanibako.project.workset_registry`).  These helpers mirror the retired
# ``names.py`` project-name API (``pick``/``assign``/``register``/``unregister``/
# reverse-lookup) but on the primary membership, so callers re-route store-for-
# store.  The name-collision DOMAIN is primary membership names ∪ global workset
# names (semantics preserved from the old ``projects ∪ worksets`` domain); the
# ``$HOME`` guard and auto-suffix numbering are carried verbatim.  Every function
# takes the primary workset root + the global registry file explicitly (the same
# no-hidden-state convention as ``_register_workset_box_membership``).
# ---------------------------------------------------------------------------

def load_primary_boxes(primary_workset: Path) -> dict[str, str]:
    """Return the PRIMARY box membership as ``{box_name: workspace_path_str}``.

    Reverse of the old ``read_names(...)['projects']`` read: the primary
    per-workset ``boxes:`` membership is the sole store now.  *primary_workset*
    is ``std.primary_workset``.
    """
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    registry_path = workset_registry.resolve_workset_registry_path(
        primary_workset, load_doc(primary_workset / "settings.yaml"),
    )
    return workset_registry.load_workset_boxes(registry_path)


def primary_box_name_for_workspace(
    primary_workset: Path, workspace: str,
) -> str | None:
    """Return the PRIMARY box name registered for *workspace*, or ``None``.

    Resolved-path aware (via :func:`_workset_box_name_for_workspace`), the
    membership replacement for the old ``lookup_by_path`` projects-arm.
    """
    return _workset_box_name_for_workspace(primary_workset, workspace)


def _primary_name_domain(primary_workset: Path, registry: Path) -> set[str]:
    """The PRIMARY-box name collision domain: primary membership ∪ global worksets.

    Preserves the retired ``names.py`` auto-name pair's cross-section domain
    (``projects ∪ worksets``) with ``projects`` replaced by the primary
    membership.  *registry* is ``std.registry`` (for the global ``worksets:``
    names); *primary_workset* is ``std.primary_workset``.
    """
    from kanibako.project import registry_store

    primary = set(load_primary_boxes(primary_workset))
    worksets = set(registry_store.load_section(registry, "worksets"))
    return primary | worksets


def check_primary_box_name_free(
    primary_workset: Path, registry: Path, name: str, workspace: str,
    *, force: bool = False,
) -> None:
    """Raise ``ProjectError`` if *name* collides in the PRIMARY-box domain.

    Mirrors :func:`names.register_name`'s pre-write guards without writing: the
    ``$HOME`` guard on *workspace* and the name-collision check across the
    PRIMARY-box domain (primary membership ∪ global worksets).  Used at the
    ``--name`` registration edge so a collision fails BEFORE the box dir is
    materialized.

    Per-kind name policy (Jei 2026-07-08): box and workset names are SEPARATE
    namespaces.  The collision splits into two arms:

    * SAME-KIND — *name* already names another PRIMARY box (primary membership).
      Unconditional: two primary boxes can NEVER share a name; *force* never
      bypasses it.
    * CROSS-KIND — *name* is a global WORKSET name.  A bare name that is both a
      box and a workset resolves deterministically to the box (shadowing the
      workset in bare-name lookups), so this refuses UNLESS *force*.
    """
    from kanibako.project import registry_store

    if Path(workspace).resolve() == Path.home().resolve():
        from kanibako.errors import ProjectError

        raise ProjectError(
            "Refusing to register $HOME as a project path — this would "
            "mount your entire home directory as the workspace."
        )
    if name in load_primary_boxes(primary_workset):
        from kanibako.errors import ProjectError

        raise ProjectError(f"Name '{name}' is already registered")
    if not force and name in set(registry_store.load_section(registry, "worksets")):
        from kanibako.errors import ProjectError

        raise ProjectError(
            f"Name '{name}' is already in use by a workset. Box and workset "
            f"names are separate namespaces, but this bare name would then "
            f"resolve to the box, shadowing the workset in bare-name lookups. "
            f"Re-run with --force to create the box under this name anyway."
        )


def pick_primary_box_name(
    primary_workset: Path,
    registry: Path,
    workspace: str,
    boxes_dir: Path | None = None,
) -> str:
    """Pick a collision-free PRIMARY box name from *workspace*'s basename (no write).

    The membership-domain counterpart of the retired ``names.py`` picker: collisions
    append a number (``name``, ``name2``, ...); a candidate is rejected when it
    is in the PRIMARY-box domain (primary membership ∪ global worksets) OR —
    when *boxes_dir* is supplied — when ``boxes_dir/<candidate>`` already exists
    (the interrupted-create reservation guard).  Performs no mutation.
    """
    base = Path(workspace).name or "project"
    taken_names = _primary_name_domain(primary_workset, registry)

    def taken(cand: str) -> bool:
        if cand in taken_names:
            return True
        if boxes_dir is not None and (boxes_dir / cand).exists():
            return True
        return False

    candidate = base
    n = 2
    while taken(candidate):
        candidate = f"{base}{n}"
        n += 1
    return candidate


def register_primary_box_name(
    primary_workset: Path, registry: Path, name: str, workspace: Path | str,
    *, force: bool = False,
) -> None:
    """Register *name* → *workspace* in the PRIMARY membership (with guards).

    The membership counterpart of :func:`names.register_name`: the ``$HOME``
    guard + the PRIMARY-box-domain name-collision check, then the actual write
    (which also enforces ``register_workset_box``'s one-box-per-workspace-path
    invariant).  *force* is forwarded to :func:`check_primary_box_name_free`: it
    bypasses the CROSS-KIND (workset-name) refusal only — the SAME-KIND
    (another primary box) arm stays unconditional.
    """
    check_primary_box_name_free(
        primary_workset, registry, name, str(workspace), force=force,
    )
    _register_workset_box_membership(primary_workset, name, Path(workspace))


def register_primary_box_name_if_absent(
    primary_workset: Path, registry: Path, name: str, workspace: Path | str,
    *, force: bool = False,
) -> None:
    """Idempotent :func:`register_primary_box_name` for deferred-create recovery.

    A no-op when *name* already maps to the SAME *workspace* in the primary
    membership (the register→clear-entry recovery re-entry); any other state
    goes through :func:`register_primary_box_name` (which raises on a genuine
    collision).  Mirrors :func:`names.register_name_if_absent`.  *force* is
    forwarded (bypasses only the CROSS-KIND workset-name refusal).
    """
    from kanibako.project.workset_registry import _same_workspace

    existing = load_primary_boxes(primary_workset).get(name)
    if existing is not None and _same_workspace(existing, str(workspace)):
        return
    register_primary_box_name(primary_workset, registry, name, workspace, force=force)


def assign_primary_box_name(
    primary_workset: Path,
    registry: Path,
    workspace: Path | str,
    boxes_dir: Path | None = None,
) -> str:
    """Auto-assign + register a PRIMARY box name from *workspace*'s basename.

    Equivalent to :func:`pick_primary_box_name` followed by
    :func:`register_primary_box_name` — the membership counterpart of the
    retired ``names.py`` auto-namer.
    """
    candidate = pick_primary_box_name(
        primary_workset, registry, str(workspace), boxes_dir=boxes_dir,
    )
    register_primary_box_name(primary_workset, registry, candidate, workspace)
    return candidate


def unregister_primary_box_name(primary_workset: Path, name: str) -> None:
    """Drop *name* from the PRIMARY membership (the membership ``unregister_name``)."""
    _unregister_workset_box_membership(primary_workset, name)


def resolve_workset_project(
    ws: WorksetSpec,
    project_name: str,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    initialize: bool = False,
    enable_vault: bool | None = None,
) -> ProjectPaths:
    """Resolve per-project paths for a project inside a NAMED workset.

    *ws* is a lightweight :class:`WorksetSpec` describing the workset's name,
    root, auth mode, and registered project names.  Callers holding a full
    ``Workset`` object pass ``WorksetSpec.from_workset(ws)``.

    Phase 5: no layout axis — shell under the per-project box dir, vault under
    the workset vault dir (``<ws.vault_dir>/{ro,rw}/<name>``).

    Raises ``WorksetError`` if *project_name* is not registered in *ws*.
    """
    # Look up project in workset.
    if project_name not in ws.project_names:
        raise WorksetError(
            f"Project '{project_name}' not found in workset '{ws.name}'."
        )

    # Name-based paths (not hash-based).
    project_path = ws.workspaces_dir / project_name
    project_dir = ws.projects_dir / project_name
    metadata_path = project_dir

    # Workspace override (P7/D10).  The per-workset registry's ``boxes:``
    # membership is the SOLE authoritative name → workspace store (D1b/D3-auth):
    # when *project_name* is registered, its REGISTERED path IS the workspace —
    # the EXTERNAL dir for a connected box, ``workspaces/<name>`` for an in-tree
    # box, and the OLD-composition path for a member registered before a
    # ``workset.workspaces`` repoint (bifrost A0: re-deriving that member from
    # the CURRENT composition strands it).  An UNREGISTERED member (an in-tree
    # connect before its first start, or a fresh ``initialize`` create) falls
    # back to the composed default, with the box_resolve identity derivation
    # preserved for any residual override.
    project_toml, _ = _box_settings_files(BoxMode.primary, metadata_path, None)
    registered_workspace = _workset_box_workspace_for_name(ws.root, project_name)
    if registered_workspace is not None:
        project_path = Path(registered_workspace)
    else:
        from kanibako.launch import box_resolve
        identity = box_resolve.resolve_box_identity(project_path, std, config)
        if identity is not None:
            project_path = Path(identity["workspace"])
    # B2b (Option A, Jei-ruled): the per-box meta["shell"]/["vault_*"] custom-path
    # OVERRIDE is DROPPED (mirrors the PRIMARY path) — home/vault are SOLELY the
    # spec-derived default location, customized via the box.bindings cascade. The
    # workspace override above (an EXTERNAL-connected live dir) is a SEPARATE concern
    # and STAYS. The shell/vault_* fields are still written for the on-disk record.
    shell_path, vault_ro_path, vault_rw_path = _workset_box_paths(
        metadata_path, ws.vault_dir, project_name,
    )
    # enable_vault (P5a): explicit param wins; else the stored box-scope
    # ``box.enable_vault`` (absent ⇒ True), read via the box-settings path.
    # NO ``default_from``: NAMED reads the box tier ONLY, exactly as before P2 (see
    # the PRIMARY resolver for why the R2 workset fallback is standalone-only).
    actual_vault_enabled = (
        enable_vault if enable_vault is not None
        else read_box_enable_vault(project_toml)
    )

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        # Sparse create (P8b/Option A): NO ``project:``/``resolved:`` identity —
        # the box's name lives in the workset's per-workset registry (the
        # ``boxes:`` entry written just below, which box_resolve reads) and its
        # workspace override in that same registry.  Only a NON-default
        # ``box.enable_vault`` is persisted, sparsely.
        write_box_enable_vault(project_toml, actual_vault_enabled)
        # P5a dual-register: record membership in the workset's per-workset
        # registry (name → workspace) — the SOLE on-disk identity record now that
        # sparse create writes no ``project:`` entry.  Sourced from the resolved
        # *project_path* so an external-connect override seeds the registry with
        # the external dir (the D10/P7 home for that record).  Idempotent —
        # overwrites a moved box's path.
        _register_workset_box_membership(ws.root, project_name, project_path)
        is_new = True

    if initialize:
        # Recovery: ensure shell exists.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)

    # J2 connect self-heal (symmetry with the import path): reaching here means
    # *project_name* IS a registered member of *ws* (the membership guard above
    # raised otherwise), so a lingering ``connect`` entry for this box is a
    # register->clear-window stale entry — the box is already registered, so
    # recovery == clear the entry (NO re-register, NO seed).  This restores
    # ``registered ==> no pending entry`` eventually-on-resolve for connect,
    # matching ``import_reconcile._clear_stale_import``.  The key is the host-side
    # box dir (``Path(shell_path).parent`` == ``ws.projects_dir/project_name`` ==
    # the connect-entry key).  Guarded + minimal: only fires when ``std.journal``
    # exists AND a register-only (import/connect) entry is actually pending for
    # this exact key — so the normal workset-resolve hot path and J1's create
    # entry are untouched.
    journal_path = getattr(std, "journal", None)
    if journal_path is not None:
        from kanibako.launch import journal as _journal
        box_key = Path(shell_path).parent
        if _journal.pending_import(journal_path, box_key) is not None:
            _journal.clear_entry(journal_path, box_key)

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=BoxMode.named,
        enable_vault=actual_vault_enabled,
        name=project_name,
        group=ProjectGroup(
            name=ws.name,
            root=ws.root,
            is_default=False,
            local_shared_base=ws.root,
        ),
    )


def _init_workset_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
) -> None:
    """First-time workset project setup: bootstrap shell directory.

    Does not create vault ``.gitignore`` files (vault lives under the workset
    root, not inside a user git repo).

    Credential copy is handled separately by ``target.init_home()`` in
    ``start.py``, after template application.
    """
    import sys

    print(
        f"[One Time Setup] Initializing workset project in {metadata_path}... ",
        end="",
        flush=True,
        file=sys.stderr,
    )
    metadata_path.mkdir(parents=True, exist_ok=True)

    # Create persistent agent shell (mounted as /home/agent).
    shell_path.mkdir(parents=True, exist_ok=True)
    _bootstrap_shell(shell_path)

    print("done.", file=sys.stderr)


def iter_projects(std: StandardPaths, config: KanibakoConfig) -> list[tuple[Path, Path | None]]:
    """Return ``(metadata_path, project_path | None)`` for every known project.

    *project_path* is the box's workspace, sourced from the PRIMARY per-workset
    registry (``name → workspace``).  A box with no registry entry yields
    ``None`` (an un-registered / half-created box has no resolvable workspace).
    """
    projects_dir = std.boxes
    if not projects_dir.is_dir():
        return []
    # P8a: box → workspace comes SOLELY from the PRIMARY per-workset registry
    # (name → workspace), the new-model source seeded at create
    # (``_register_new_box``) — the SAME source ``box_resolve`` reads.  A box
    # cannot be resolved from its box DIR via ``box_resolve.resolve_box_identity``
    # (the registry is keyed by workspace PATH, not the box dir), so we read the
    # PRIMARY registry here directly (identical data).  The transitional
    # ``read_project_meta`` (settings.yaml ``resolved.workspace``) + legacy
    # ``project-path.txt`` breadcrumb fallbacks are DROPPED (P8a): a box absent
    # from the registry has no workspace → ``None``.  (These are PRIMARY boxes:
    # ``std.boxes`` == ``@config.primary_workset/boxes``, so the PRIMARY registry
    # is the home.)
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc

    primary_registry = workset_registry.resolve_workset_registry_path(
        std.primary_workset, load_doc(std.primary_workset / "settings.yaml"),
    )
    registered = workset_registry.load_workset_boxes(primary_registry)
    results: list[tuple[Path, Path | None]] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        registered_ws = registered.get(entry.name)
        project_path: Path | None = Path(registered_ws) if registered_ws else None
        results.append((entry, project_path))
    return results


def iter_workset_projects(
    std: StandardPaths,
    config: KanibakoConfig,
) -> list[tuple[str, _WorksetLike, list[tuple[str, str]]]]:
    """Return workset project info for all registered worksets.

    Each entry is ``(workset_name, workset, [(project_name, status), ...])``.
    The workset object is a concrete ``kanibako.project.workset.Workset`` typed
    structurally as :class:`_WorksetLike` (so ``paths.py`` need not import
    ``workset``).  Status is ``"ok"``, ``"missing"`` (no workspace), or
    ``"no-data"`` (no project dir).
    """
    import sys

    from kanibako.project import workset_registry
    from kanibako.project.workset import list_worksets, load_workset
    from kanibako.settings.config_io import load_doc

    registry = list_worksets(std)
    results: list[tuple[str, _WorksetLike, list[tuple[str, str]]]] = []

    for ws_name in sorted(registry):
        root = registry[ws_name]
        if not root.is_dir():
            print(
                f"Warning: workset '{ws_name}' root missing: {root}",
                file=sys.stderr,
            )
            continue
        try:
            ws = load_workset(root)
        except Exception as exc:
            print(
                f"Warning: failed to load workset '{ws_name}': {exc}",
                file=sys.stderr,
            )
            continue

        # The per-workset ``boxes:`` membership (loaded ONCE per workset) — the
        # authoritative name → workspace store.  Workspace presence is checked
        # at the REGISTERED path when the member is registered: a member
        # registered under an OLD composition (before a ``workset.workspaces``
        # repoint) otherwise reads as "missing" and vanishes from ``list``
        # (bifrost A0).  An unregistered member falls back to the composed
        # location.
        registry_path = workset_registry.resolve_workset_registry_path(
            ws.root, load_doc(ws.root / "settings.yaml"),
        )
        registered_boxes = workset_registry.load_workset_boxes(registry_path)

        project_list: list[tuple[str, str]] = []
        for proj in ws.projects:
            has_project_dir = (ws.projects_dir / proj.name).is_dir()
            registered_ws_path = registered_boxes.get(proj.name)
            workspace_path = (
                Path(registered_ws_path) if registered_ws_path is not None
                else ws.workspaces_dir / proj.name
            )
            has_workspace = workspace_path.is_dir()
            if has_project_dir and has_workspace:
                status = "ok"
            elif has_project_dir and not has_workspace:
                status = "missing"
            else:
                status = "no-data"
            project_list.append((proj.name, status))

        results.append((ws_name, ws, project_list))

    return results


def _find_workset_for_path(project_dir: Path, std: StandardPaths) -> tuple[_WorksetLike, str | None]:
    """Return ``(workset, project_name)`` for a path inside a workset.

    The returned object is a concrete ``kanibako.project.workset.Workset`` (typed
    structurally as :class:`_WorksetLike` to avoid importing ``workset`` into
    ``paths.py``); callers that need a :class:`WorksetSpec` for
    :func:`resolve_workset_project` wrap it via ``WorksetSpec.from_workset``.

    *project_dir* may be the workspace root, a subdirectory within it,
    or anywhere inside the workset root.  When *project_dir* is inside
    ``workspaces/{name}/``, the project name is returned.  When inside
    the workset root but not in a specific workspace, ``None`` is returned
    as the project name.

    Raises ``WorksetError`` if *project_dir* does not belong to any
    registered workset.
    """
    from kanibako.project.workset import (
        list_worksets,
        load_workset,
        load_workset_settings_doc,
        resolve_workset_workspaces,
    )

    registry = list_worksets(std)
    resolved = project_dir.resolve()
    for _name, root in registry.items():
        ws_root = root.resolve()
        # The resolved ``workset.workspaces`` (repoint honored; default
        # ``@meta.workset.path/workspaces`` — §3.3: real and USED).
        ws_workspaces = resolve_workset_workspaces(
            ws_root, load_workset_settings_doc(ws_root)
        )
        # Check workspaces/ first (specific project).
        try:
            rel = resolved.relative_to(ws_workspaces)
            project_name = rel.parts[0] if rel.parts else None
            ws = load_workset(root)
            return ws, project_name
        except ValueError:
            pass
        # Then check workset root itself.
        try:
            resolved.relative_to(ws_root)
            ws = load_workset(root)
            return ws, None
        except ValueError:
            continue
    raise WorksetError(f"No workset found for path: {project_dir}")


def _resolve_workset_or_connected(
    project_dir: Path, std: StandardPaths,
) -> tuple[_WorksetLike, str | None]:
    """Resolve *project_dir* to its owning workset, honoring external connects.

    Tries the in-tree lookup first (:func:`_find_workset_for_path`).  When that
    misses (raises ``WorksetError``) or lands on the workset root without a
    specific project (``proj_name is None``), falls back to the
    connected-external redirect index so that a path living *outside* any
    workset tree -- e.g. an externally connected workspace -- still resolves to
    its ``(workset, project_name)``.

    Raises ``WorksetError`` if neither lookup resolves a workset at all.  When a
    workset is found but no specific project is (``proj_name is None``), that is
    returned to the caller rather than raised -- callers preserve their own
    distinct "inside workset but not in a project" error.
    """
    try:
        ws, proj_name = _find_workset_for_path(project_dir, std)
    except WorksetError:
        ws, proj_name = None, None
    if ws is None or proj_name is None:
        # Tree-based lookup missed (or hit the workset root without a project):
        # try the connected-external boxes (D10 enumerate-and-scan over the
        # per-workset registries).  Lazy import avoids a paths <-> box_resolve
        # import cycle.
        from kanibako.launch import box_resolve
        from kanibako.project.workset import load_workset
        owned = box_resolve.find_connected_external_box(
            project_dir.resolve(), std,
        )
        if owned is not None:
            ws, proj_name = load_workset(owned.workset_root), owned.box_name
    if ws is None:
        raise WorksetError(f"No workset found for path: {project_dir}")
    return ws, proj_name


def resolve_any_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    register: bool = True,
    name_override: str | None = None,
) -> ProjectPaths:
    """Auto-detect project mode and resolve paths accordingly.

    Uses ``detect_project_mode`` to walk ancestor directories and find the
    project root.  The resolved *project_root* (not the raw CWD) is passed
    to the appropriate resolver.

    *register* (B3) is forwarded to the PRIMARY/STANDALONE resolvers so the
    ``start`` auto-create path can defer registration until after the home seed
    (the NAMED branch never writes the name registry on create, so the flag is a
    no-op there).  Defaults True.

    *name_override* is forwarded to the PRIMARY resolver only, which is the sole
    mode whose box name is not derivable from the tree itself: a STANDALONE box
    carries its identity in its own root ``settings.yaml``, and a NAMED box takes
    its name from its workspace directory.  ``box extract --name`` is the caller
    that needs it (re-materializing an archived box under a chosen name).
    """
    raw = project_dir or os.getcwd()
    # CLI front-door: a bare token (no path separator) that doesn't exist in
    # cwd may be a registered project/workset name.  resolve_project also does
    # this lookup, but resolve_any_project must do it FIRST -- otherwise
    # Path(raw).resolve() below path-ifies the name before detect_project_mode
    # sees it.
    named_workset = False
    raw_name = raw
    if raw and "/" not in raw and not Path(raw).exists():
        try:
            resolved, kind = resolve_name(
                std.registry, raw, cwd=Path.cwd(),
                primary_workset=std.primary_workset,
            )
        except ProjectError:
            # A bare token that names NO known project/workset/workset-member box
            # AND has no path of that name on disk.  Refuse to path-ify it to a
            # nonexistent cwd-relative path — doing so would resolve to an
            # UNREGISTERED box with an empty name, minting a phantom
            # ``kanibako-<hash>`` container that no `list`/`ps` row corresponds
            # to.  Surface an honest error on the READ path (``initialize`` is
            # False for stop/box/diagnose/…).  The CREATE path
            # (``initialize=True``) still path-ifies so a new box can be
            # materialized at the resolved location; and an existing-path or
            # qualified (``ws/proj``) spec never reaches here (guarded by the
            # ``"/" not in raw and not exists`` condition).
            if not initialize:
                raise
        else:
            if kind in ("project", "workset"):
                # Update `raw` for BOTH kinds: a bare workset name resolves to
                # the workset ROOT, which detect_project_mode must see (without
                # this, the name path-ifies to cwd/<name> and resolution fails
                # with a misleading "does not exist").  A workset is not a single
                # box, so we still reject it below -- but with a clear message.
                raw = resolved
                named_workset = kind == "workset"
    if named_workset:
        # The token named a workset, not a project.  `box`/diagnose operate on a
        # single project box, and a workset may contain zero or many; there is no
        # unambiguous representative.  Fail with an actionable message instead of
        # the generic "inside a workset, cd to a project" error.
        raise WorksetError(
            f"'{raw_name}' is a workset, not a single project box. "
            f"Name a project inside it (e.g. '{raw_name}/<project>') or run the "
            f"command from a project workspace under that workset."
        )
    # Qualified ``workset/project`` addressing: a token containing a separator
    # that is NOT an existing path may be a qualified name (the form the bare-
    # workset rejection above suggests).  Resolve it to the project's workspace
    # so detect_project_mode sees a single project box.  A real relative path
    # like ``src/foo`` that happens not to exist is left untouched -- it falls
    # through to the path-ify behavior below and fails exactly as before.
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
            raise WorksetError(
                f"Inside workset '{ws.name}' but not in a specific project workspace. "
                f"Change to a project directory under {ws.workspaces_dir}/."
            )
        return resolve_workset_project(
            WorksetSpec.from_workset(ws), proj_name, std, config, initialize=initialize,
        )
    if detection.mode == BoxMode.standalone:
        return resolve_standalone_project(
            std, config, root_str, initialize=initialize, register=register,
        )
    return resolve_project(
        std, config, project_dir=root_str, initialize=initialize, register=register,
        name_override=name_override,
    )


def resolve_box_target(
    std: StandardPaths,
    config: KanibakoConfig,
    value: str | None = None,
    *,
    initialize: bool = False,
    register: bool = True,
    warn: bool = True,
) -> ProjectPaths:
    """Resolve a ``--box`` value (a box NAME or a path) to its :class:`ProjectPaths`.

    The single path-or-name resolver behind the ``--box`` selector and the
    ``start``/``shell``/``refresh``/``workset disconnect`` targeting (§Design 8).
    Returns the SAME :class:`ProjectPaths` the positional-``project`` path
    returns, so callers swap cleanly.

    *value* is EITHER a box NAME or a filesystem path.  **Box NAME takes
    precedence in ambiguous cases** — names cannot contain ``/`` so true
    ambiguity is rare (a bare token that is both a registered name and a
    relative directory in cwd resolves to the NAME).  Resolution order:

    1. **NAME first.**  A bare token (no path separator) is tried as a name:

       * a **standalone box name** in ``registry.standalone`` (the canonical-id
         domain — closes the gap that :func:`resolve_any_project` does NOT cover,
         since :func:`resolve_name` only indexes the projects/worksets sections);
       * else the registry projects/worksets names + qualified ``ws/project``
         names, which :func:`resolve_any_project` already resolves.

    2. **PATH otherwise.**  Anything that is not a name (contains ``/``, or no
       name matched) is resolved as a filesystem path via
       :func:`resolve_any_project` — reusing the existing path-resolution +
       ancestor-walk discovery (``detect_project_mode``).  No detection is
       reimplemented here.

    A pre-existing box whose name does not satisfy the §Design 8 blocklist still
    resolves (the matcher is structural, not policy-gated); FLAGGING that is the
    caller's job via :func:`kanibako.launch.box_identity.is_valid_box_name` — this
    resolver does not reject on name shape.

    ``None`` / empty *value* resolves the cwd box (delegates to
    :func:`resolve_any_project`), matching the positional-``project`` default.

    *register* (B3) is forwarded to the PRIMARY/STANDALONE resolvers; ``start``
    passes ``register=False`` so an auto-created box defers registration until
    after its home seed (journal entry → seed → register → clear-entry).  Defaults True.

    *warn* gates the non-conforming-name FLAG (:func:`_flag_nonconforming`).  A
    NON-materialising PROBE (``initialize=False``) run purely to read a box's paths
    ahead of a second materialising resolve passes ``warn=False`` so the name flag
    fires exactly ONCE (on the real resolve), never doubled.  Defaults True.
    """
    def _flag(proj: ProjectPaths) -> ProjectPaths:
        if warn:
            _flag_nonconforming(proj)
            _flag_invalid_kuid(proj)
            _flag_missing_vault(proj)
        return proj

    # Empty / None -> cwd resolution (same as a bare positional default).
    if not value:
        return _flag(
            resolve_any_project(
                std, config, value, initialize=initialize, register=register,
            )
        )

    # NAME-first: a bare token (no separator) that names a registered STANDALONE
    # box wins over a same-named relative path.  resolve_any_project covers the
    # projects/worksets registry + paths, but NOT the standalone-name domain, so
    # check it here before falling through.
    if "/" not in value:
        from kanibako.project import registry_store

        standalone = registry_store.load_standalone(std.registry)
        # Box names are lowercase (R2); fold the query for the lookup.
        root_str = standalone.get(value.lower())
        if root_str is not None:
            return _flag(
                resolve_standalone_project(
                    std, config, root_str, initialize=initialize,
                    register=register,
                )
            )

    # Else: NAME (projects/worksets/qualified) or PATH, both via the existing
    # resolver (name-precedence for bare tokens is already handled there).
    return _flag(
        resolve_any_project(
            std, config, value, initialize=initialize, register=register,
        )
    )


def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths:
    """Warn (do NOT reject) when a resolved box's name violates the blocklist.

    Pre-existing boxes created before the §Design 8 box-name constraint still
    resolve (the canonical-id/registry matchers are structural, not policy-
    gated); but a non-conforming name is FLAGGED on use so the drift is visible.
    Returns *proj* unchanged.
    """
    from kanibako.launch.box_identity import box_name_reason

    if proj.name:
        reason = box_name_reason(proj.name)
        if reason is not None:
            get_logger(__name__).warning(
                "box name '%s' does not meet the naming rules (%s); it still "
                "resolves, but rename it when convenient.",
                proj.name,
                reason,
            )
    return proj


def _flag_invalid_kuid(proj: ProjectPaths) -> ProjectPaths:
    """Advisory (never fatal): flag a standalone box whose stored ``workset.kuid``
    is a NON-sentinel value that fails the kuid parity/charset check.

    Fires ONLY when ALL hold (spec D9, INVERTED 2026-07-04):
      * the box is STANDALONE (only standalone stores a real kuid);
      * ``workset.kuid`` is NON-sentinel (``!= kuid.SENTINEL``) — the sentinel
        ``"00000"`` is the DEFAULT, EXEMPT even though ``kuid.is_valid("00000")``
        is False by parity (the exemption is EXPLICIT, never inside is_valid);
      * ``workset.skip_kuid_check`` is OFF (its DEFAULT is TRUE, so the warning is
        OPT-IN strictness);
      * ``kuid.is_valid`` rejects the stored value.

    The kuid is USER-EDITABLE, so a bad value is FLAGGED (``Warning: invalid KUID``),
    NEVER rejected. Returns *proj* unchanged.
    """
    if proj.mode is not BoxMode.standalone:
        return proj
    from kanibako import kuid

    # ``workset.kuid`` / ``workset.skip_kuid_check`` are WORKSET-scope keys, so they
    # are read from the WORKSET tier of the ONE pair — for standalone that is the ROOT
    # ``settings.yaml``, exactly where ``establish_standalone`` writes them.  Sourced
    # from the pair rather than re-spelled, so this cannot drift from the writer.
    _, settings_file = box_workset_settings_paths(proj)
    if settings_file is None:
        # Unreachable for standalone (its workset tier is the ROOT file, always a
        # path).  The guard exists so the reads below are TYPED — it is not handling
        # a real case.
        return proj
    value = read_workset_kuid(settings_file)
    if (
        value != kuid.SENTINEL
        and not read_workset_skip_kuid_check(settings_file)
        and not kuid.is_valid(value)
    ):
        get_logger(__name__).warning(
            "Warning: invalid KUID '%s' for standalone box '%s' (not a valid "
            "kuid); it still resolves — fix workset.kuid or set "
            "workset.skip_kuid_check=true to silence this.",
            value,
            proj.name,
        )
    return proj


def _flag_missing_vault(proj: ProjectPaths) -> ProjectPaths:
    """Advisory (never fatal): warn when a box that EXPECTS a vault has none on
    disk (spec D5, the NON-CRITICAL integrity tier).

    A vault is OPTIONAL storage, not a launch prerequisite — so a box whose
    ``enable_vault`` is on but whose vault directory is absent still resolves and
    launches; the missing vault is merely FLAGGED (``warning: cannot find
    vault``).  A box with ``enable_vault`` OFF expects no vault, so nothing is
    warned (the ``enable_vault`` guard is load-bearing: without it every
    vault-disabled box would warn).  Fires at resolve time alongside the other
    ``_flag`` advisories.  Returns *proj* unchanged.
    """
    if proj.enable_vault and not proj.vault_rw_path.is_dir():
        get_logger(__name__).warning(
            "warning: cannot find vault for box '%s' (expected at %s); it still "
            "launches without a vault — recreate the directory or set "
            "box.enable_vault=false to silence this.",
            proj.name or str(proj.project_path),
            proj.vault_rw_path.parent,
        )
    return proj


def establish_standalone(
    std: StandardPaths,
    root: Path,
    *,
    enable_vault: bool,
    name: str = "",
    register: bool = True,
) -> tuple[str, Path, Path, Path]:
    """Establish a standalone box at *root*: identity + meta + registration.

    The single shared core behind all three standalone paths (``create
    --standalone``, ``convert --standalone``, ``duplicate --standalone``).  It

    1. derives the box identity via :func:`box_identity.resolve_standalone_name`
       — a fresh canonical ``<kuid>_<leaf>`` (whole-name collision regen vs
       ``registry.standalone``) when *name* is empty, otherwise honoring the
       supplied (lowercased) ``--name``: a verbatim canonical id if free (else
       refuse), or a fresh prefix over the supplied string as the leaf;
    2. writes the SPARSE settings, each key AT ITS OWN SCOPE'S TIER (M-8): the
       workset-scope ``workset.kuid`` into the ROOT ``<root>/settings.yaml`` (which
       MATERIALIZES that file — half the §5 standalone detection marker), and a
       NON-default box-scope ``box.enable_vault`` into the BOX tier
       ``<root>/box_data/settings.yaml`` — the SAME file ``config set box.*`` writes,
       so create and set can never disagree.  A default-vault box therefore writes NO
       box-tier file at all, which is the spec's "ABSENT BY DEFAULT" (§5).  NO
       ``project:``/``resolved:`` identity is written — the name/mode/workspace derive
       from ``registry.standalone`` + the live kuid;
    3. registers the box in ``registry.standalone`` (``box_name`` → *root*).

    *root* is the standalone project dir.  The box-data dir (``root/box_data``)
    must already exist (each caller creates/copies it before calling).  Returns
    ``(box_name, shell_path, vault_ro, vault_rw)`` so callers can build their
    result state without recomputing the table.  Callers own their own
    surrounding concerns (file copies, unwind registration, old-name
    unregister) — only the identity/meta/register core lives here.

    *register* (B3 interrupted-create journal): when False the identity is still
    resolved and the meta file written, but the box is NOT registered in
    ``registry.standalone`` — the caller defers registration until AFTER the home
    seed (journal entry → seed → register → clear-entry), so a crash mid-seed leaves
    an UNregistered box that recovery resolves by its on-disk root.  Defaults True
    (the convert/duplicate lifecycle callers register inline, unchanged).
    """
    from kanibako.project import registry_store
    from kanibako.launch import box_identity

    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)

    existing = registry_store.standalone_box_names(std.registry)
    box_name = box_identity.resolve_standalone_name(root, name, existing)

    box_settings, settings_file = _standalone_settings_files(root)
    # Sparse create (P8b/Option A): NO ``project:``/``resolved:`` identity — the
    # standalone box's name is registered in ``registry.standalone`` (below) and
    # re-composed LIVE from the sparse ``workset.kuid`` (written just below) +
    # the dir leaf.  Only a NON-default ``box.enable_vault`` is persisted, and it goes
    # to the BOX tier (``box_data/settings.yaml``) because it is a box-scope key —
    # the same file ``config set box.enable_vault`` writes (M-8: ONE write target).
    # The ROOT settings.yaml (the standalone marker, alongside ``box_data/``) is
    # MATERIALIZED unconditionally by the ``workset.kuid`` write below, so moving the
    # box key out does NOT cost the file detection needs.
    write_box_enable_vault(box_settings, enable_vault)
    # Persist the GENERATED kuid as the settable ``workset.kuid`` key (P6d) into the
    # WORKSET tier (the ROOT file — kuid is a workset-scope key), sparsely, via the
    # SAME keystore sparse-write engine ``config set`` uses — [[settings-must-map-to-keystore-key]].
    # The kuid IS the name's prefix (``box_identity.standalone_kuid``); storing it
    # makes it the STABLE cross-move handle (the launch re-composes the name as
    # ``<stored kuid>_<live leaf>`` so a moved box keeps its identity).
    from kanibako.settings.config_io import write_nested_key

    write_nested_key(
        settings_file, ("workset",), "kuid",
        box_identity.standalone_kuid(box_name),
    )
    if register:
        registry_store.register_standalone(std.registry, box_name, root)
    return box_name, shell_path, vault_ro_path, vault_rw_path


def resolve_standalone_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    enable_vault: bool | None = None,
    name: str = "",
    register: bool = True,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths for standalone mode.

    All project state lives inside *project_dir* itself.
    No data is written to ``$XDG_DATA_HOME``.

    Phase 5d/Part 3 (drift H+I): no layout axis.  The project *root* (the
    runtime dir) is the standalone workset root and holds, in fixed positions:
    ``settings.yaml`` (the box meta, AT THE ROOT — ``metadata_path``), a
    ``workspace/`` subdir (the live workspace → ``~/workspace`` — the
    ``project_path``; the resolved ``workset.workspaces``, default
    ``@meta.workset.path/workspace``), a ``box_data/`` marker dir holding ``home/`` + the
    ``<box>.jsonl`` helper log, and ``vault/{ro,rw}/``.  The box identity is
    ``<kuid>_<sanitized leaf>`` (generated + registered in
    ``registry.standalone`` at create time; reused from the stored meta after).

    *register* (B3 interrupted-create marker): forwarded to
    :func:`establish_standalone`; when False on a NEW box the meta is written and
    ``is_new`` set but the box is NOT registered, so the caller can register after
    the home seed.  Defaults True (existing callers unchanged).
    """
    raw = project_dir or os.getcwd()
    root = Path(raw).resolve()

    if not root.is_dir():
        raise ProjectError(f"Project path '{root}' does not exist.")

    # The hash + identity key off the ROOT (the standalone workset root), which
    # is stable; the workspace subdir is the bind source, not the identity.
    phash = project_hash(str(root))

    from kanibako.project.workset import (
        load_workset_settings_doc,
        resolve_workset_workspaces,
    )

    # Metadata at the ROOT (settings.yaml); the ``box_data/`` marker dir holds
    # home/ + the helper log.  ``project_path`` is the resolved
    # ``workset.workspaces`` (ruled 10, 2026-08-02): the STANDALONE default is
    # ``@meta.workset.path/workspace`` == the ``workspace/`` subdir, and a set
    # ``workset: {workspaces: …}`` in the ROOT settings.yaml repoints it
    # ("changeable from workset level", spec §2e).
    metadata_path = root
    box_data = root / _STANDALONE_META_DIR
    project_path = resolve_workset_workspaces(
        root, load_workset_settings_doc(root), standalone=True,
    )
    # The mode-aware tier pair from the ONE derivation (M-8): the BOX tier is
    # ``box_data/settings.yaml`` (absent by default) and the WORKSET tier is the ROOT
    # ``settings.yaml`` — the file §5 detection reads and where ``workset.kuid`` lives.
    box_settings, project_toml = _standalone_settings_files(root)

    # STANDALONE paths are derived from the (current) root, never the stored
    # absolutes: the DEFAULT formulas are all ``@``-anchored to the root
    # (``@meta.workset.path/…``), so a default-shaped tree is drop-in portable
    # BY CONSTRUCTION — a moved/imported tree resolves against its new
    # location.  A stored ABSOLUTE repoint (e.g. ``workset.workspaces``) is the
    # user's own choice and travels as written.  The resolved.* section in
    # settings.yaml is advisory only (BUG#1 fix); home/vault always live at the
    # fixed box_data/home + <root>/vault/{ro,rw} positions.
    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)
    # enable_vault (P5a): explicit param wins; else the stored box-scope
    # ``box.enable_vault`` — read from the BOX tier, falling back to the WORKSET tier
    # (the ROOT file) as an R2 downward-default.  That fallback is what keeps a
    # pre-P2 standalone box, whose value was written to the root file, working with
    # no migration (M-8).
    actual_vault_enabled = (
        enable_vault if enable_vault is not None
        else read_box_enable_vault(box_settings, default_from=project_toml)
    )

    # Box identity name (P8a): sourced from ``box_resolve`` for a MATERIALIZED
    # standalone (``box_data/`` + ``settings.yaml`` present — the same gate
    # ``standalone_settings_present`` uses).  box_resolve composes the name LIVE
    # (P6d) as ``<stored workset.kuid>_<live leaf>`` — the kuid is the STABLE
    # stored prefix (from the box's OWN ``settings.yaml``, design D6) and the leaf
    # is re-derived from the CURRENT root basename, so a moved standalone tree
    # keeps its kuid identity while the leaf tracks the new dir (spec 2026-07-04);
    # a pre-kuid box (no ``workset.kuid`` ⇒ SENTINEL) falls back to the registered
    # ``standalone:`` key, else the dir leaf.  A not-yet-materialized root (no
    # ``box_data/``) yields "" (the create block below assigns it authoritatively
    # via establish_standalone).  Replaces the transitional ``read_project_meta``
    # ``project.name`` read.
    box_name = ""
    if box_data.is_dir() and project_toml.is_file():
        from kanibako.launch import box_resolve
        identity = box_resolve.resolve_box_identity(root, std, config)
        box_name = identity["name"] if identity is not None else ""
    # The user's explicit --name (only meaningful when establishing a new box;
    # ignored once the box exists since the stored identity is authoritative).
    requested_name = name

    is_new = False
    if initialize and not box_data.is_dir():
        # Not-yet-initialized iff the ``box_data/`` marker dir is absent (the
        # root itself always exists — it is the runtime dir).
        # Pre-flight the requested --name BEFORE any FS mutation so a doomed
        # create (a verbatim-canonical name already taken) refuses up front
        # rather than leaving an orphaned half-created box_data/ + vault/ tree
        # (BUG-A).  establish_standalone re-resolves the name authoritatively;
        # this only surfaces the refusable collision early.
        from kanibako.project import registry_store
        from kanibako.launch import box_identity
        box_identity.validate_standalone_name(
            requested_name,
            registry_store.standalone_box_names(std.registry),
        )
        _init_standalone_project(
            std, box_data, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        # Identity + meta + registration via the shared establish core.  The
        # init block is only reached when no meta exists, so the identity is
        # resolved fresh from the user-supplied --name (empty → fresh canonical).
        # B3: defer registration to the caller (journal entry → seed → register →
        # clear-entry) when register=False; the identity + meta are still written so
        # recovery can resolve the box by its on-disk root.
        box_name, shell_path, vault_ro_path, vault_rw_path = establish_standalone(
            std, root,
            enable_vault=actual_vault_enabled,
            name=requested_name,
            register=register,
        )
        is_new = True

    if initialize:
        # Recovery: ensure home + workspace exist.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        project_path.mkdir(parents=True, exist_ok=True)

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=BoxMode.standalone,
        enable_vault=actual_vault_enabled,
        name=box_name,
    )


def _init_standalone_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
    vault_ro_path: Path,
    vault_rw_path: Path,
    project_path: Path,
    *,
    enable_vault: bool = True,
) -> None:
    """First-time standalone project setup: all state inside project dir.

    Unlike workset init, this *does* create vault directories and a
    ``.gitignore`` (vault lives inside the user's project, likely a git repo).

    Credential copy is handled separately by ``target.init_home()`` in
    ``start.py``, after template application.

    *metadata_path* is the ``box_data/`` marker dir (home + helper log);
    *project_path* is the ``workspace/`` subdir (the live workspace), which is
    created here so the bind source exists.
    """
    _init_common(
        std, metadata_path, shell_path,
        vault_ro_path, vault_rw_path, project_path,
        enable_vault=enable_vault,
    )
    # The workspace is a SUBDIR of the root (drift H); create the bind source.
    project_path.mkdir(parents=True, exist_ok=True)
