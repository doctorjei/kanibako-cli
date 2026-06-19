"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol

import yaml

from kanibako.log import get_logger

from kanibako.config import (
    KanibakoConfig,
    config_file_path,
    load_config,
    migrate_config,
    read_project_meta,
    write_project_meta,
)
from kanibako.config_io import load_doc
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings_resolve import (
    LevelView,
    ResolveCtx,
    SettingsError,
    _Unset,
    expand_expr,
    resolve_value,
)
from kanibako.names import (
    assign_name,
    read_names,
    register_name,
    resolve_name,
    resolve_qualified_name,
)
from kanibako.utils import project_hash


class ProjectMode(Enum):
    """How a project's persistent state is organized on disk."""

    default = "default"
    workset = "workset"
    standalone = "standalone"


class DetectionResult(NamedTuple):
    """Result of project mode detection.

    *mode* is the detected project mode.  *project_root* is the ancestor
    directory where the marker was found (may differ from the original
    *project_dir* when the user is in a subdirectory).
    """

    mode: ProjectMode
    project_root: Path


class ProjectLayout(Enum):
    """Directory layout variant within a project mode.

    - **simple**: shell and vault live inside the workspace (minimal footprint)
    - **default**: shell in boxes, vault in workspace (middle ground)
    - **robust**: full separation — all four folders are top-level siblings
    """

    simple = "simple"
    default = "default"
    robust = "robust"


# Default layout per mode.
_DEFAULT_LAYOUT = {
    ProjectMode.default: ProjectLayout.default,
    ProjectMode.workset: ProjectLayout.robust,
    ProjectMode.standalone: ProjectLayout.simple,
}


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
    # System-level derived directories (settings-framework "system.*" config).
    data: Path
    backup: Path
    agents: Path
    channels: Path
    global_dir: Path        # ``global`` is a Python keyword → ``global_dir``.
    base_template: Path
    settings: Path
    primary_workset: Path
    registry: Path
    cache: Path
    runtime: Path
    # Channels skeleton — keys/defaults only; sub-key wiring is Phase 6.
    channels_commons: Path
    channels_chat: Path
    channels_broadcast: Path
    channels_mailboxes: Path
    channels_share: Path
    # Transitional: the OLD ``boxes`` location, resolved unchanged (Phase 5
    # moves boxes/logs/vault under the PRIMARY workset; until then disk layout
    # is preserved).  Backs the ``boxes`` alias below.  Deleted in Phase 5.
    _boxes: Path

    # ------------------------------------------------------------------
    # Transitional aliases (DELETED in Phase 5).
    #
    # The renamed/restructured fields above replace the old flat
    # ``system.path.*``-backed fields.  Roughly 20 call sites (start.py,
    # workset.py, install, diagnose, box lifecycle, helper_listener, …) still
    # read the OLD field names.  These read-only ``@property`` aliases keep
    # those non-Phase-3 call sites compiling unchanged until Phase 5 migrates
    # them onto the new structure (boxes/logs/vault move under the PRIMARY
    # workset; channels sub-keys get wired).  Do NOT add new uses.
    # ------------------------------------------------------------------

    @property
    def boxes(self) -> Path:
        """OLD ``std.boxes`` — the per-project box store (location unchanged
        in Phase 3; Phase 5 moves it under the PRIMARY workset)."""
        return self._boxes

    @property
    def comms(self) -> Path:
        """OLD ``std.comms`` → the renamed ``channels`` dir."""
        return self.channels

    @property
    def templates(self) -> Path:
        """OLD ``std.templates`` → the renamed/re-pointed ``base_template`` dir."""
        return self.base_template

    @property
    def ws_hints(self) -> Path:
        """OLD ``std.ws_hints`` → the renamed/absorbed ``registry`` file."""
        return self.registry

    @property
    def share_ro(self) -> Path:
        """OLD ``std.share_ro`` — DELETED (subsumed by ``@workset.vault_ro`` /
        the ``shared`` category).  No replacement dir exists → raise."""
        raise NotImplementedError(
            "system.path.share_ro was deleted in the system.* reorg; use the "
            "workset vault / 'shared' category instead."
        )

    @property
    def share_rw(self) -> Path:
        """OLD ``std.share_rw`` — DELETED (subsumed by ``@workset.vault_rw`` /
        the ``shared`` category)."""
        raise NotImplementedError(
            "system.path.share_rw was deleted in the system.* reorg; use the "
            "workset vault / 'shared' category instead."
        )


@dataclass(frozen=True)
class ProjectGroup:
    """Descriptor of a project's grouping (default workset or named workset).

    Captures the default-vs-workset difference as *data* rather than control
    flow.  The implicit default group is the *default workset* (``is_default``
    is True); a named workset forms a non-default group rooted at the workset
    root.  Standalone projects belong to no group (``ProjectPaths.group`` is
    None).

    *local_shared_base* is the root under which the local-shared path lives
    (``base / config.paths_shared``): the standard data path for the default
    group, the workset root for a workset group.
    """

    name: str
    root: Path
    is_default: bool
    local_shared_base: Path


@dataclass
class ProjectPaths:
    """Resolved paths for a specific project."""

    project_path: Path
    project_hash: str
    metadata_path: Path      # host-only: project.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    vault_ro_path: Path      # {project}/vault/ro (→ /home/agent/share-ro)
    vault_rw_path: Path      # {project}/vault/rw (→ /home/agent/share-rw)
    is_new: bool = field(default=False)
    mode: ProjectMode = field(default=ProjectMode.default)
    layout: ProjectLayout = field(default=ProjectLayout.default)
    enable_vault: bool = field(default=True)
    group_auth: bool = field(default=True)
    name: str = field(default="")
    global_shared_path: Path | None = field(default=None)
    local_shared_path: Path | None = field(default=None)
    group: ProjectGroup | None = field(default=None)


class _WorksetLike(Protocol):
    """Structural type for the attributes :meth:`WorksetSpec.from_workset` reads.

    Avoids importing the concrete :class:`kanibako.workset.Workset` into
    ``paths.py`` (which ``workset.py`` imports from, creating a cycle).
    """

    name: str
    root: Path
    group_auth: bool
    is_default: bool

    @property
    def projects_dir(self) -> Path: ...

    @property
    def workspaces_dir(self) -> Path: ...

    @property
    def vault_dir(self) -> Path: ...

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
    """Primitive view of a workset, decoupled from :class:`kanibako.workset.Workset`.

    Carries only the values the path resolver and project listings need, so
    ``paths.py`` does not import the ``workset`` module (which depends on
    ``paths.py``).  Callers holding a full ``Workset`` build one with
    :meth:`from_workset`.
    """

    name: str
    root: Path
    group_auth: bool
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
            group_auth=ws.group_auth,
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
# System-level config tier (settings-framework "system.*")
# ---------------------------------------------------------------------------
#
# These model the system-level config directories as resolver-backed path
# expressions.  Keys are the FULL dotted names (bare ``system.*`` — the
# ``.path`` segment was dropped in the system.* reorg) so ``@``-refs (e.g.
# ``@system.data``) resolve against the same table.
#
# The data tree (``@system.data``) holds the persistent dirs; ``cache`` and
# ``runtime`` deliberately live under their own XDG bases (NOT under data).
# ``global`` holds the global settings/registry files; ``channels`` carries a
# skeleton of sub-keys (their behavior/wiring is Phase 6 — here they only need
# to resolve).  The OLD per-leaf ``boxes`` location is resolved separately as a
# transitional value (see :func:`resolve_system_paths`) and is NOT a key here.
SYSTEM_PATH_DEFAULTS: dict[str, str] = {
    "system.data": "$XDG_DATA_HOME/kanibako",
    "system.backup": "@system.data/backup",
    "system.agents": "@system.data/agents",
    "system.channels": "@system.data/channels",
    "system.global": "@system.data/global",
    "system.base_template": "@system.global/base_template",
    "system.settings": "@system.global/settings.yaml",
    "system.primary_workset": "@system.data/primary_workset",
    "system.registry": "@system.global/registry.yaml",
    "system.cache": "$XDG_CACHE_HOME/kanibako",
    "system.runtime": "$XDG_RUNTIME_DIR/kanibako",
    # Channels skeleton (Phase 6 fills sub-key behavior).
    "system.channels.commons": "@system.channels/commons",
    "system.channels.chat": "@system.channels/chat",
    "system.channels.broadcast": "@system.channels.chat/broadcast.md",
    "system.channels.mailboxes": "@system.channels/mailboxes",
    "system.channels.share": "@system.channels/share",
}


def resolve_system_paths(
    set_values: Mapping[str, str], *, data_home: Path, home: Path,
) -> dict[str, Path]:
    """Resolve the ``system.path.*`` tier to concrete host paths.

    *set_values* holds raw user-set expressions keyed by their full dotted name
    (bare ``system.<leaf>``); typically the global config's ``system_paths``.
    *data_home* is the already-resolved XDG data base (e.g. ``~/.local/share``)
    exposed to expressions as ``$XDG_DATA_HOME``; *home* expands a leading
    ``~``.  Returns ``{full_dotted_key: Path}`` for every key in
    :data:`SYSTEM_PATH_DEFAULTS`, plus the transitional pseudo-key
    ``system._boxes`` (the OLD ``@system.data/boxes`` location, kept for the
    ``StandardPaths.boxes`` alias until Phase 5 moves boxes under the PRIMARY
    workset).
    """
    # Populate the FULL XDG var set so ``$XDG_*`` references in system path
    # expressions resolve.  ``data_home`` is passed in already-resolved (it
    # anchors the default tree); the rest are resolved here via the hardened
    # resolver (honor-iff-absolute; runtime-dir fallback+warn).
    xdg_vars: dict[str, str] = {
        "XDG_DATA_HOME": str(data_home),
        "XDG_CONFIG_HOME": str(resolve_xdg("XDG_CONFIG_HOME", ".config")),
        "XDG_STATE_HOME": str(resolve_xdg("XDG_STATE_HOME", ".local/state")),
        "XDG_CACHE_HOME": str(resolve_xdg("XDG_CACHE_HOME", ".cache")),
        "XDG_RUNTIME_DIR": str(resolve_xdg("XDG_RUNTIME_DIR", None)),
    }
    ctx = ResolveCtx(
        agent_name=None,
        workset_name=None,
        host_home=str(home),
        xdg=xdg_vars,
    )
    levels = [
        LevelView("system", values=dict(set_values), defaults=SYSTEM_PATH_DEFAULTS)
    ]

    def lookup(ref: str, chain: tuple[str, ...]) -> str:
        rv = resolve_value(ref, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):
            raise SettingsError(f"Unknown @-reference: {ref}")
        return expand_expr(
            rv.value, space="host", ctx=ctx, lookup=lookup, chain=chain,
        )

    resolved: dict[str, Path] = {}
    for key in SYSTEM_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(f"Unresolvable system path: {key}")
        expanded = expand_expr(rv.value, space="host", ctx=ctx, lookup=lookup)
        resolved[key] = Path(expanded)

    # Transitional ``system._boxes``: the OLD ``@system.data/boxes`` location,
    # resolved off the (possibly overridden) data path so disk layout is
    # unchanged in Phase 3.  Backs the ``StandardPaths.boxes`` alias; removed
    # in Phase 5 when boxes move under the PRIMARY workset.
    resolved["system._boxes"] = resolved["system.data"] / "boxes"
    return resolved


def load_system_config(
    user_config_path: Path, *, data_home: Path, home: Path,
) -> dict[str, Path]:
    """Resolve the ``system.*`` config tier from the CONFIG file set.

    The CONFIG (``system.*``) set is three files, read in cascade order so the
    most-authoritative present value of each ``system.<leaf>`` set-value
    wins **before** expression resolution:

    1. ``/etc/kanibako/config_base.yaml`` — site-wide overridable defaults
       (least specific).
    2. *user_config_path* — the user's global ``~/.config/kanibako.yaml``
       (overrides the base).
    3. ``/etc/kanibako/config_required.yaml`` — site-wide **non-overridable**
       values, applied LAST so they win over both the base and the user file
       (decision D: ``*_required`` sits above everything else in the set).

    Missing files are skipped (each contributes nothing).  The merged set-values
    are handed to :func:`resolve_system_paths`, which fills in
    :data:`SYSTEM_PATH_DEFAULTS` and resolves ``@``-/``$XDG_*``-references.

    Keys are the bare ``system.<leaf>`` form (the ``.path`` segment was dropped
    in the system.* reorg); the on-disk config shape is a flat ``[system]``
    table.

    Back-compat: a user with only ``~/.config/kanibako.yaml`` (no ``/etc``
    files) gets exactly the prior behavior — the base and required layers are
    empty, so the user file is the sole source of set-values.
    """
    # Lazy import to avoid a config <-> paths import cycle at module load.
    from kanibako.config import (
        config_base_path,
        config_required_path,
        load_config,
    )

    set_values: dict[str, str] = {}
    # base < user < required.  load_config(...).system_paths yields the file's
    # ``system.path.<leaf>`` set-values (full dotted keys), or {} when the file
    # is absent — so missing layers are skipped automatically.
    set_values.update(load_config(config_base_path()).system_paths)
    set_values.update(load_config(user_config_path).system_paths)
    set_values.update(load_config(config_required_path()).system_paths)

    return resolve_system_paths(set_values, data_home=data_home, home=home)


def _migrate_global_env(config_home: Path, data_path: Path) -> None:
    """Move global env file from old config_home/kanibako/env to data_path/env."""
    old = config_home / "kanibako" / "env"
    new = data_path / "env"
    if old.is_file() and not new.exists():
        import shutil
        data_path.mkdir(parents=True, exist_ok=True)
        shutil.move(str(old), str(new))
        import sys
        print(f"Migrated: {old} → {new}", file=sys.stderr)


def _migrate_settings_to_boxes(data_path: Path, boxes_path: Path) -> None:
    """Rename the legacy ``data_path/settings`` dir to the resolved boxes dir."""
    old = data_path / "settings"
    if old.is_dir() and not boxes_path.exists():
        old.rename(boxes_path)
        import sys
        print(f"Migrated: {old} → {boxes_path}", file=sys.stderr)


def load_std_paths(config: KanibakoConfig | None = None) -> StandardPaths:
    """Compute all standard kanibako directories.

    If *config* is None, it is loaded from the config file (which must exist).
    Directories are created as needed.
    """
    config_home = xdg("XDG_CONFIG_HOME", ".config")
    data_home = xdg("XDG_DATA_HOME", ".local/share")
    state_home = xdg("XDG_STATE_HOME", ".local/state")
    cache_home = xdg("XDG_CACHE_HOME", ".cache")

    # Migrate config file from old subdir location if needed.
    migrate_config(config_home)
    config_file = config_file_path(config_home)

    if config is None:
        if not config_file.exists():
            raise ConfigError(
                f"{config_file} is missing. Run any kanibako command to initialize."
            )
        config = load_config(config_file)

    # Resolve the system-level path tier (settings-framework "system.path.*")
    # from the CONFIG file set: /etc config_base < user-global < /etc
    # config_required (required is non-overridable, applied last).  A user with
    # only ~/.config/kanibako.yaml gets the prior behavior (empty /etc layers).
    resolved = load_system_config(
        config_file, data_home=data_home, home=Path.home(),
    )
    data_path = resolved["system.data"]
    # state/cache paths track the data dir's leaf name (unchanged behavior:
    # default leaf "kanibako" under each XDG base).
    rel = data_path.name
    state_path = state_home / rel
    cache_path = cache_home / rel

    # Migrate settings/ -> boxes/ if needed (transitional box location).
    _migrate_settings_to_boxes(data_path, resolved["system._boxes"])

    # Migrate global env file from config_home/kanibako/env to data_path/env.
    _migrate_global_env(config_home, data_path)

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
        data=resolved["system.data"],
        backup=resolved["system.backup"],
        agents=resolved["system.agents"],
        channels=resolved["system.channels"],
        global_dir=resolved["system.global"],
        base_template=resolved["system.base_template"],
        settings=resolved["system.settings"],
        primary_workset=resolved["system.primary_workset"],
        registry=resolved["system.registry"],
        cache=resolved["system.cache"],
        runtime=resolved["system.runtime"],
        channels_commons=resolved["system.channels.commons"],
        channels_chat=resolved["system.channels.chat"],
        channels_broadcast=resolved["system.channels.broadcast"],
        channels_mailboxes=resolved["system.channels.mailboxes"],
        channels_share=resolved["system.channels.share"],
        _boxes=resolved["system._boxes"],
    )


def resolve_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    enable_vault: bool | None = None,
    name_override: str | None = None,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths.

    When *initialize* is True (used by ``start``), missing project directories
    are created and credential templates are copied in.  When False (used by
    subcommands like ``archive``/``purge``), the paths are merely computed.

    *layout* overrides the default layout for new projects.  Existing projects
    read their layout from ``project.yaml``.

    *enable_vault* controls whether vault directories are created and mounted.
    Defaults to True for new projects; existing projects read from ``project.yaml``.
    """
    raw = project_dir or os.getcwd()
    # If the user passed a bare token (no path separator) and no file/dir of
    # that name exists in cwd, try resolving it as a registered project name.
    # Falls through to path resolution on miss so the eventual error stays
    # informative.
    if raw and "/" not in raw and not Path(raw).exists():
        try:
            resolved, kind = resolve_name(std.data_path, raw, cwd=Path.cwd())
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
    project_name, project_dir_path = _resolve_local_dir(
        std.data_path, project_path_str, std.boxes,
    )

    metadata_path = project_dir_path

    # Check for stored paths in project.yaml (enables user overrides).
    project_toml = metadata_path / "project.yaml"
    meta = read_project_meta(project_toml)
    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.default]
        shell_path = Path(meta["shell"]) if meta["shell"] else metadata_path / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else project_path / "vault" / "ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else project_path / "vault" / "rw"
        actual_vault_enabled = meta.get("enable_vault", True) if enable_vault is None else enable_vault
    else:
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.default]
        shell_path, vault_ro_path, vault_rw_path = _compute_project_paths(
            actual_layout, metadata_path, project_path,
            vault_root=_local_vault_root(actual_layout, metadata_path, project_path),
        )
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    # Auth mode for the default group: the default workset's
    # group_auth (from {data_path}/config.yaml) is the base; a project may
    # narrow shared→distinct via its own meta — mirroring the named-workset
    # logic in resolve_workset_project.  No-op on upgrade: default_workset's
    # group_auth is True until a user runs `workset config default group_auth`,
    # and existing project meta froze group_auth=True at init.
    from kanibako.workset import default_workset
    actual_group_auth = default_workset(std).group_auth
    if actual_group_auth and meta:
        actual_group_auth = bool(meta.get("group_auth", True))

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
        # New project: assign a name first, then create boxes/{name}/.
        # An explicit override (e.g. `kanibako create --name X`) registers
        # strictly; collisions error rather than auto-suffix.
        if name_override:
            register_name(std.data_path, name_override, project_path_str)
            project_name = name_override
        else:
            project_name = assign_name(std.data_path, project_path_str)
        project_dir_path = std.boxes / project_name
        metadata_path = project_dir_path
        # Recompute paths with the name-based directory.
        shell_path, vault_ro_path, vault_rw_path = _compute_project_paths(
            actual_layout, metadata_path, project_path,
            vault_root=_local_vault_root(actual_layout, metadata_path, project_path),
        )
        project_toml = metadata_path / "project.yaml"

        _init_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        _global_shared = std.data_path / config.paths_shared / "global"
        _local_shared = std.data_path / config.paths_shared
        write_project_meta(
            project_toml,
            mode="default",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            enable_vault=actual_vault_enabled,
            metadata=str(metadata_path),
            project_hash=phash,
            global_shared=str(_global_shared),
            local_shared=str(_local_shared),
            name=project_name,
        )
        import sys
        print(f"Project name: {project_name}", file=sys.stderr)
        is_new = True

    if initialize:
        # Recovery: ensure shell exists even if metadata_path was present.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        # Backfill project.yaml for old-format projects (pre-v0.8).
        if metadata_path.is_dir() and read_project_meta(metadata_path / "project.yaml") is None:
            _global_shared_bf = std.data_path / config.paths_shared / "global"
            _local_shared_bf = std.data_path / config.paths_shared
            # Use directory name as project name (name-based dirs).
            _bf_name = metadata_path.name if not metadata_path.name.startswith(phash[:8]) else ""
            write_project_meta(
                metadata_path / "project.yaml",
                mode="default",
                layout=actual_layout.value,
                workspace=str(project_path),
                shell=str(shell_path),
                vault_ro=str(vault_ro_path),
                vault_rw=str(vault_rw_path),
                enable_vault=actual_vault_enabled,
                metadata=str(metadata_path),
                project_hash=phash,
                global_shared=str(_global_shared_bf),
                local_shared=str(_local_shared_bf),
                name=_bf_name,
            )
        # Convenience symlink when vault lives outside the workspace.
        if actual_vault_enabled:
            _ensure_vault_symlink(project_path, vault_ro_path)
            # Human-friendly symlink for robust layout.
            if actual_layout == ProjectLayout.robust:
                human_vault_dir = std.data_path / config.paths_vault
                _ensure_human_vault_symlink(
                    human_vault_dir, project_path, vault_ro_path.parent,
                )
                if is_new:
                    import sys
                    print(
                        f"\nNOTE: In robust layout, the default-workset vault "
                        f"is linked from\n{human_vault_dir}. You can create a "
                        f"symlink from your home directory with:\n"
                        f"  ln -s {human_vault_dir} $HOME/kanibako_vault",
                        file=sys.stderr,
                    )

    # Resolve shared paths: prefer stored values (enables user overrides).
    _computed_global_shared = std.data_path / config.paths_shared / "global"
    _computed_local_shared = std.data_path / config.paths_shared
    if meta and meta.get("global_shared"):
        _computed_global_shared = Path(meta["global_shared"])
    if meta and meta.get("local_shared"):
        _computed_local_shared = Path(meta["local_shared"])

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=ProjectMode.default,
        layout=actual_layout,
        enable_vault=actual_vault_enabled,
        group_auth=actual_group_auth,
        name=project_name,
        global_shared_path=_computed_global_shared,
        local_shared_path=_computed_local_shared,
        group=ProjectGroup(
            name="default",
            root=std.data_path,
            is_default=True,
            local_shared_base=std.data_path,
        ),
    )


def _resolve_local_dir(
    data_path: Path,
    project_path_str: str,
    boxes_dir: Path,
) -> tuple[str, Path]:
    """Find the boxes directory for a default-mode project.

    Looks up the project name via names.yaml reverse lookup and returns
    ``(project_name, boxes_dir/{name}/)`` path.  *boxes_dir* is the resolved
    transitional ``std.boxes`` box-store directory; *data_path* is still
    needed to read ``names.yaml``.

    Returns ``("", empty_path)`` when no name is registered — the caller
    (``resolve_project``) will assign a name during initialization.
    """
    names = read_names(data_path)
    # Reverse lookup: path → name.
    for name, path in names["projects"].items():
        if path == project_path_str:
            return name, boxes_dir / name

    return "", boxes_dir / "__unregistered__"



def _compute_project_paths(
    layout: ProjectLayout, metadata_path: Path, project_path: Path,
    *, vault_root: Path,
) -> tuple[Path, Path, Path]:
    """Compute ``(shell, vault_ro, vault_rw)`` for default and workset modes.

    The only structural difference between default and workset is *where* the
    vault lives in the non-``simple`` layouts; the caller expresses that by
    passing ``vault_root`` — the parent directory under which ``ro`` and
    ``rw`` are placed.  The ``simple`` layout always keeps shell and
    vault inside the workspace and ignores *vault_root*.

    Caller-supplied *vault_root* must reproduce the existing per-mode policy:

    - **default**: ``default`` → ``project_path/"vault"``; ``robust`` →
      ``metadata_path/"vault"``.
    - **workset**: ``default``/``robust`` → ``vault_base/project_name``.
    """
    if layout == ProjectLayout.simple:
        shell = project_path / ".shell"
        vault_ro = project_path / "vault" / "ro"
        vault_rw = project_path / "vault" / "rw"
    else:  # default / robust
        shell = metadata_path / "shell"
        vault_ro = vault_root / "ro"
        vault_rw = vault_root / "rw"
    return shell, vault_ro, vault_rw


def _local_vault_root(layout: ProjectLayout, metadata_path: Path, project_path: Path) -> Path:
    """Vault parent dir for default mode in the non-``simple`` layouts."""
    if layout == ProjectLayout.robust:
        return metadata_path / "vault"
    return project_path / "vault"  # default


def _compute_standalone_paths(
    layout: ProjectLayout, metadata_path: Path, project_path: Path,
) -> tuple[Path, Path, Path]:
    """Compute (shell, vault_ro, vault_rw) for standalone mode."""
    if layout == ProjectLayout.robust:
        shell = project_path / "shell"
        vault_ro = project_path / "vault" / "ro"
        vault_rw = project_path / "vault" / "rw"
    else:  # simple (default for standalone)
        shell = metadata_path / "shell"
        vault_ro = project_path / "vault" / "ro"
        vault_rw = project_path / "vault" / "rw"
    return shell, vault_ro, vault_rw


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
    """Patch an existing shell directory to add shell.d support.

    Idempotent — safe to call every launch.  Creates ``.shell.d/`` if missing
    and appends the source line to ``.bashrc`` if absent.  No-op if
    *shell_path* does not exist yet.
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


def _ensure_vault_symlink(project_path: Path, vault_ro_path: Path) -> None:
    """Create a convenience symlink from project_path/vault when vault lives elsewhere.

    In local tree and WS default/tree layouts, vault dirs are stored outside the
    project workspace.  The symlink lets the user discover vault via their
    project directory.  No-op when vault is already under project_path or the
    symlink target already matches.
    """
    vault_parent = vault_ro_path.parent  # e.g. metadata_path/vault or vault_base/name
    link = project_path / "vault"

    # Vault already lives under project_path — no symlink needed.
    try:
        if vault_parent.resolve() == link.resolve():
            return
    except OSError:
        pass

    if link.is_symlink():
        # Symlink exists — update only if target differs.
        if link.resolve() == vault_parent.resolve():
            return
        link.unlink()
    elif link.exists():
        # A real directory or file exists — don't overwrite.
        return

    try:
        link.symlink_to(vault_parent)
    except OSError:
        pass  # Best-effort; non-fatal if we can't create the symlink.


def _ensure_human_vault_symlink(
    vault_dir: Path, project_path: Path, vault_parent: Path,
) -> Path | None:
    """Create a human-friendly symlink ``{vault_dir}/{basename}`` → *vault_parent*.

    *vault_dir* is e.g. ``{data_path}/vault``.  *project_path* is the user's
    workspace directory whose basename is used as the symlink name.
    *vault_parent* is the hash-based vault directory (``…/boxes/{hash}/vault``).

    Collision handling: if *basename* already points elsewhere, tries
    ``{name}1``, ``{name}2``, … up to ``{name}99``.

    Returns the created/existing symlink ``Path`` on success, ``None`` on
    failure or if *vault_parent* does not exist.
    """
    if not vault_parent.is_dir():
        return None

    vault_dir.mkdir(parents=True, exist_ok=True)
    basename = project_path.name

    # Try the plain name first, then name1..name99.
    candidates = [basename] + [f"{basename}{i}" for i in range(1, 100)]
    for name in candidates:
        link = vault_dir / name
        if link.is_symlink():
            try:
                if link.resolve() == vault_parent.resolve():
                    return link  # Already correct — idempotent.
            except OSError:
                pass
            continue  # Points elsewhere — try next candidate.
        if link.exists():
            continue  # Real file/dir — skip.
        # Slot is free.
        try:
            link.symlink_to(vault_parent)
            return link
        except OSError:
            return None  # Best-effort.
    return None  # All 100 candidates exhausted.


def _remove_human_vault_symlink(vault_dir: Path, vault_parent: Path) -> bool:
    """Remove the human-friendly symlink that points to *vault_parent*.

    Scans *vault_dir* for the first symlink whose target resolves to
    *vault_parent* and removes it.  Removes *vault_dir* itself if empty
    afterwards.

    Returns True if a symlink was removed, False otherwise.
    """
    if not vault_dir.is_dir():
        return False
    try:
        for entry in vault_dir.iterdir():
            if entry.is_symlink():
                try:
                    if entry.resolve() == vault_parent.resolve():
                        entry.unlink()
                        # Clean up empty vault_dir.
                        if not any(vault_dir.iterdir()):
                            vault_dir.rmdir()
                        return True
                except OSError:
                    continue
    except OSError:
        pass
    return False


def _remove_project_vault_symlink(project_path: Path) -> bool:
    """Remove ``{project_path}/vault`` if it is a symlink (not a real dir).

    Returns True if a symlink was removed, False otherwise.
    """
    link = project_path / "vault"
    if link.is_symlink():
        try:
            link.unlink()
            return True
        except OSError:
            pass
    return False


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



def _find_local_ancestor(target: Path, data_path: Path, boxes_dir: Path) -> Path | None:
    """Find the deepest registered default-mode project that is an ancestor of *target*.

    Reads ``names.yaml`` and, for each entry whose registered path is a
    prefix of *target*, checks that ``boxes_dir/{name}/`` actually exists on
    disk.  Among all valid matches, the deepest (most path components)
    wins.  Returns the matched path or ``None``.  *boxes_dir* is the resolved
    transitional ``std.boxes`` box-store directory.
    """
    names = read_names(data_path)
    best: Path | None = None
    best_depth = -1
    for name, path_str in names["projects"].items():
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


def _is_standalone_meta_dir(meta_dir: Path) -> bool:
    """True only if *meta_dir* is a real standalone project metadata directory.

    A bare directory named ``.kanibako``/``kanibako`` is NOT sufficient: the
    kanibako container image bakes an empty ``~/.kanibako`` runtime/IPC dir into
    every container home (helper socket + log), which must never be mistaken for
    a standalone project marker.  Require a parseable ``project.yaml`` that
    declares ``mode = "standalone"``.
    """
    toml = meta_dir / "project.yaml"
    if not meta_dir.is_dir() or not toml.is_file():
        return False
    try:
        meta = read_project_meta(toml)
    except (OSError, ValueError, yaml.YAMLError):  # malformed/unreadable file
        return False
    return bool(meta and meta.get("mode") == "standalone")


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
    1. Workset — *project_dir* lives inside a registered workset root
       (``workspaces/`` subdirectory first, then the root itself).
    2. Default (name-based) — one-pass scan of ``names.yaml``;
       deepest registered path that is an ancestor of *project_dir* wins.
       Requires ``boxes/{name}/`` to exist on disk.
    3. Walk ancestors for standalone markers — a ``.kanibako`` or
       ``kanibako`` **directory** exists inside the ancestor.
       ``.kanibako`` takes priority.
    4. Default — ``default`` mode at the original *project_dir*.
    """
    resolved = project_dir.resolve()
    home = Path.home().resolve()

    # 1. Workset check (no walk needed — relative_to handles subdirs).
    ws_result = _check_workset(resolved, std)
    if ws_result is not None:
        return ws_result

    # 1b. Connected-external check: the path (or an ancestor) is an external
    # directory connected to a workset.  Resolves before the default scan.
    from kanibako.workset import _find_connected_project
    if _find_connected_project(resolved, std) is not None:
        return DetectionResult(ProjectMode.workset, resolved)

    # 2. Name-based default-mode check (one-pass scan, deepest match wins).
    ac_ancestor = _find_local_ancestor(resolved, std.data_path, std.boxes)
    if ac_ancestor is not None:
        return DetectionResult(ProjectMode.default, ac_ancestor)

    # 3. Walk ancestors for standalone markers.
    current = resolved
    while True:
        # Standalone check: .kanibako/ or kanibako/ directory with a real
        # standalone project.yaml.  A bare directory is not enough (the
        # container image bakes an empty ~/.kanibako runtime/IPC dir).
        if _is_standalone_meta_dir(current / ".kanibako"):
            return DetectionResult(ProjectMode.standalone, current)
        if _is_standalone_meta_dir(current / "kanibako"):
            return DetectionResult(ProjectMode.standalone, current)

        # Stop conditions: reached $HOME or filesystem root.
        if current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 4. Default: default mode at the original directory.
    return DetectionResult(ProjectMode.default, resolved)


def _check_workset(
    resolved_dir: Path,
    std: StandardPaths,
) -> DetectionResult | None:
    """Check whether *resolved_dir* is inside a registered workset.

    Returns a ``DetectionResult`` if found, ``None`` otherwise.
    Checks ``workspaces/`` first (specific project), then the workset root
    itself (inside workset but not necessarily a project workspace).
    """
    worksets_toml = std.ws_hints
    if not worksets_toml.is_file():
        return None

    _data = load_doc(worksets_toml)

    for _root_str in _data.get("worksets", {}).values():
        ws_root = Path(_root_str).resolve()
        ws_workspaces = ws_root / "workspaces"
        # Check workspaces/ first (more specific).
        try:
            resolved_dir.relative_to(ws_workspaces)
            return DetectionResult(ProjectMode.workset, resolved_dir)
        except ValueError:
            pass
        # Then check workset root itself.
        try:
            resolved_dir.relative_to(ws_root)
            return DetectionResult(ProjectMode.workset, resolved_dir)
        except ValueError:
            continue

    return None


def resolve_workset_project(
    ws: WorksetSpec,
    project_name: str,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    enable_vault: bool | None = None,
) -> ProjectPaths:
    """Resolve per-project paths for a project inside a workset.

    *ws* is a lightweight :class:`WorksetSpec` describing the workset's name,
    root, directory layout, auth mode, and registered project names.  Callers
    holding a full ``Workset`` object pass ``WorksetSpec.from_workset(ws)``.

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

    # Check for stored paths in project.yaml (enables user overrides).
    project_toml = metadata_path / "project.yaml"
    meta = read_project_meta(project_toml)
    # Honor a stored workspace override (set when the project was connected to
    # an EXTERNAL directory): the external dir is the live workspace.  Mirrors
    # the describe path (iter_projects), which already reads meta["workspace"].
    if meta and meta.get("workspace"):
        project_path = Path(meta["workspace"])
    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.workset]
        shell_path = Path(meta["shell"]) if meta["shell"] else project_dir / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else ws.vault_dir / project_name / "ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else ws.vault_dir / project_name / "rw"
        actual_vault_enabled = meta.get("enable_vault", True) if enable_vault is None else enable_vault
    else:
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.workset]
        shell_path, vault_ro_path, vault_rw_path = _compute_project_paths(
            actual_layout, metadata_path, project_path,
            vault_root=ws.vault_dir / project_name,
        )
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    # Auth mode: workset-level overrides project-level.
    actual_group_auth = ws.group_auth
    if actual_group_auth and meta:
        actual_group_auth = bool(meta.get("group_auth", True))

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        _ws_global_shared = std.data_path / config.paths_shared / "global"
        _ws_local_shared = ws.root / config.paths_shared
        write_project_meta(
            project_toml,
            mode="workset",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            enable_vault=actual_vault_enabled,
            group_auth=actual_group_auth,
            metadata=str(metadata_path),
            project_hash=phash,
            global_shared=str(_ws_global_shared),
            local_shared=str(_ws_local_shared),
        )
        is_new = True

    if initialize:
        # Recovery: ensure shell exists.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        # Convenience symlink when vault lives outside the workspace.
        if actual_vault_enabled:
            _ensure_vault_symlink(project_path, vault_ro_path)

    # Resolve shared paths: prefer stored values (enables user overrides).
    _ws_computed_global = std.data_path / config.paths_shared / "global"
    _ws_computed_local = ws.root / config.paths_shared
    if meta and meta.get("global_shared"):
        _ws_computed_global = Path(meta["global_shared"])
    if meta and meta.get("local_shared"):
        _ws_computed_local = Path(meta["local_shared"])

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=ProjectMode.workset,
        layout=actual_layout,
        enable_vault=actual_vault_enabled,
        group_auth=actual_group_auth,
        name=project_name,
        global_shared_path=_ws_computed_global,
        local_shared_path=_ws_computed_local,
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

    *project_path* is read from ``project.yaml`` (``workspace`` field) when
    available, falling back to ``project-path.txt`` for backward compat.
    """
    projects_dir = std.boxes
    if not projects_dir.is_dir():
        return []
    results: list[tuple[Path, Path | None]] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        project_path: Path | None = None
        # Prefer project.yaml workspace field.
        meta = read_project_meta(entry / "project.yaml")
        if meta and meta.get("workspace"):
            project_path = Path(meta["workspace"])
        else:
            # Backward compat: fall back to breadcrumb file.
            breadcrumb = entry / "project-path.txt"
            if breadcrumb.is_file():
                text = breadcrumb.read_text().strip()
                if text:
                    project_path = Path(text)
        results.append((entry, project_path))
    return results


def iter_workset_projects(
    std: StandardPaths,
    config: KanibakoConfig,
) -> list[tuple[str, _WorksetLike, list[tuple[str, str]]]]:
    """Return workset project info for all registered worksets.

    Each entry is ``(workset_name, workset, [(project_name, status), ...])``.
    The workset object is a concrete ``kanibako.workset.Workset`` typed
    structurally as :class:`_WorksetLike` (so ``paths.py`` need not import
    ``workset``).  Status is ``"ok"``, ``"missing"`` (no workspace), or
    ``"no-data"`` (no project dir).
    """
    import sys

    from kanibako.workset import list_worksets, load_workset

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

        project_list: list[tuple[str, str]] = []
        for proj in ws.projects:
            has_project_dir = (ws.projects_dir / proj.name).is_dir()
            has_workspace = (ws.workspaces_dir / proj.name).is_dir()
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

    The returned object is a concrete ``kanibako.workset.Workset`` (typed
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
    from kanibako.workset import list_worksets, load_workset

    registry = list_worksets(std)
    resolved = project_dir.resolve()
    for _name, root in registry.items():
        ws_root = root.resolve()
        ws_workspaces = ws_root / "workspaces"
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
        # try the connected-external redirect index.  Lazy import avoids a
        # paths <-> workset import cycle (mirrors resolve_any_project).
        from kanibako.workset import _find_connected_project
        hit = _find_connected_project(project_dir.resolve(), std)
        if hit is not None:
            ws, proj_name = hit
    if ws is None:
        raise WorksetError(f"No workset found for path: {project_dir}")
    return ws, proj_name


def resolve_any_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
) -> ProjectPaths:
    """Auto-detect project mode and resolve paths accordingly.

    Uses ``detect_project_mode`` to walk ancestor directories and find the
    project root.  The resolved *project_root* (not the raw CWD) is passed
    to the appropriate resolver.
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
            resolved, kind = resolve_name(std.data_path, raw, cwd=Path.cwd())
            if kind in ("project", "workset"):
                # Update `raw` for BOTH kinds: a bare workset name resolves to
                # the workset ROOT, which detect_project_mode must see (without
                # this, the name path-ifies to cwd/<name> and resolution fails
                # with a misleading "does not exist").  A workset is not a single
                # box, so we still reject it below -- but with a clear message.
                raw = resolved
                named_workset = kind == "workset"
        except ProjectError:
            pass
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
            project_workspace, _ws_name = resolve_qualified_name(std.data_path, raw)
            raw = project_workspace
        except ProjectError:
            pass
    raw_dir = Path(raw).resolve()
    detection = detect_project_mode(raw_dir, std, config)
    root_str = str(detection.project_root)

    if detection.mode == ProjectMode.workset:
        ws, proj_name = _resolve_workset_or_connected(raw_dir, std)
        if proj_name is None:
            raise WorksetError(
                f"Inside workset '{ws.name}' but not in a specific project workspace. "
                f"Change to a project directory under {ws.workspaces_dir}/."
            )
        return resolve_workset_project(
            WorksetSpec.from_workset(ws), proj_name, std, config, initialize=initialize,
        )
    if detection.mode == ProjectMode.standalone:
        return resolve_standalone_project(std, config, root_str, initialize=initialize)
    return resolve_project(std, config, project_dir=root_str, initialize=initialize)


def resolve_standalone_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    enable_vault: bool | None = None,
    group_auth: bool | None = None,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths for standalone mode.

    All project state lives inside *project_dir* itself.
    No data is written to ``$XDG_DATA_HOME``.
    """
    raw = project_dir or os.getcwd()
    project_path = Path(raw).resolve()

    if not project_path.is_dir():
        raise ProjectError(f"Project path '{project_path}' does not exist.")

    phash = project_hash(str(project_path))

    # Determine metadata_path (depends on layout for standalone).
    # For tree layout: {project}/kanibako (no dot)
    # For simple (default): {project}/.kanibako (dot prefix)
    # Check both locations for existing projects.
    dot_meta = project_path / ".kanibako"
    nodot_meta = project_path / "kanibako"

    # Check for stored paths in existing metadata.
    meta = None
    actual_layout = None
    if dot_meta.is_dir():
        meta = read_project_meta(dot_meta / "project.yaml")
        metadata_path = dot_meta
    elif nodot_meta.is_dir():
        meta = read_project_meta(nodot_meta / "project.yaml")
        metadata_path = nodot_meta
    else:
        # New project — determine layout and metadata_path.
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.standalone]
        if actual_layout == ProjectLayout.robust:
            metadata_path = nodot_meta
        else:
            metadata_path = dot_meta

    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.standalone]
        shell_path = Path(meta["shell"]) if meta["shell"] else metadata_path / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else project_path / "vault" / "ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else project_path / "vault" / "rw"
        actual_vault_enabled = meta.get("enable_vault", True) if enable_vault is None else enable_vault
    else:
        if actual_layout is None:
            actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.standalone]
        shell_path, vault_ro_path, vault_rw_path = _compute_standalone_paths(
            actual_layout, metadata_path, project_path,
        )
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    project_toml = metadata_path / "project.yaml"

    # Auth mode for standalone: explicit param > meta > default.
    # Standalone projects are NOT in the default group, so they do
    # not consult the default workset config.yaml.
    actual_group_auth = (
        group_auth
        if group_auth is not None
        else (bool(meta.get("group_auth", True)) if meta else True)
    )

    is_new = False
    if initialize and not metadata_path.is_dir():
        _init_standalone_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        write_project_meta(
            project_toml,
            mode="standalone",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            enable_vault=actual_vault_enabled,
            group_auth=actual_group_auth,
            metadata=str(metadata_path),
            project_hash=phash,
        )
        is_new = True

    if initialize:
        # Recovery: ensure shell exists.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)

    return ProjectPaths(
        project_path=project_path,
        project_hash=phash,
        metadata_path=metadata_path,
        shell_path=shell_path,
        vault_ro_path=vault_ro_path,
        vault_rw_path=vault_rw_path,
        is_new=is_new,
        mode=ProjectMode.standalone,
        layout=actual_layout,
        enable_vault=actual_vault_enabled,
        group_auth=actual_group_auth,
        global_shared_path=None,
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
    """
    _init_common(
        std, metadata_path, shell_path,
        vault_ro_path, vault_rw_path, project_path,
        enable_vault=enable_vault,
    )
