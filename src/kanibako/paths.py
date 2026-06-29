"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from collections.abc import Mapping, Sequence
from typing import NamedTuple, Protocol

import yaml

from kanibako.log import get_logger

from kanibako.config import (
    BOX_META_FILE,
    KanibakoConfig,
    config_file_path,
    load_config,
    read_project_meta,
    write_project_meta,
)
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.settings_resolve import (
    GUEST_HOME,
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
    # PRIMARY-workset box store: ``@system.primary_workset/boxes``.  Phase 5
    # moved this here from the OLD ``@system.data/boxes`` location (the
    # transitional ``_boxes`` pseudo-key + alias property were retired with the
    # ``_migrate_settings_to_boxes`` shim).  Per-box metadata/shell live under
    # ``boxes/<name>/``; the PRIMARY vault/logs live as siblings under the
    # PRIMARY workset (see :func:`resolve_project`).
    boxes: Path
    # PRIMARY-workset vault + logs roots: ``@system.primary_workset/vault/{ro,rw}``
    # and ``@system.primary_workset/logs``.  Phase 5 moved the PRIMARY vault out
    # of the workspace into the PRIMARY workset.
    primary_vault_ro: Path
    primary_vault_rw: Path
    primary_logs: Path

    # ------------------------------------------------------------------
    # Transitional aliases (owner = Phase 7; not retired here).
    #
    # ``templates`` still aliases the renamed ``base_template`` dir for the
    # templates (Phase 7) call sites; ``share_ro``/``share_rw`` raise (the dirs
    # were deleted in the system.* reorg).  The ``comms`` alias was retired in
    # Phase 6 (the legacy comms mount is gone; all readers use ``channels``).
    # Do NOT add new uses.
    # ------------------------------------------------------------------

    @property
    def templates(self) -> Path:
        """OLD ``std.templates`` → the renamed/re-pointed ``base_template`` dir."""
        return self.base_template

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
    (``base / "shared"``): the standard data path for the default
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
    metadata_path: Path      # host-only: settings.yaml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    vault_ro_path: Path      # {project}/vault/ro (→ /home/agent/vault/ro)
    vault_rw_path: Path      # {project}/vault/rw (→ /home/agent/vault/rw)
    is_new: bool = field(default=False)
    mode: BoxMode = field(default=BoxMode.primary)
    enable_vault: bool = field(default=True)
    # Group-auth (block #2 — capability chain). ``group_auth`` is NO LONGER the
    # flat side-channel: it is the READ-COMPAT carrier of the BOX-level on-disk
    # choice (old ``[project].group_auth`` / new ``box.group_auth_on``), default
    # True. ``workset_group_auth`` carries the WORKSET-level on-disk policy (old
    # default-workset/named-workset ``group_auth`` / new ``group_auth_enabled``).
    # Both feed ``settings_launch.group_auth_chain_floor`` (JC-3); the EFFECTIVE
    # bool is resolved through the launch snapshot, NOT merged here.
    group_auth: bool = field(default=True)
    workset_group_auth: bool = field(default=True)
    name: str = field(default="")
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


# The container's home directory.  Boxes always run as ``agent`` with this
# home; used to anchor the box-side XDG default when resolving an in-container
# path from the HOST (where ``$HOME`` is the operator's, not the box's).
# Alias of the single source of truth :data:`~kanibako.settings_resolve.GUEST_HOME`.
BOX_HOME = GUEST_HOME


def box_state_home(box_env: Mapping[str, str] | None) -> PurePosixPath:
    """Resolve the BOX-side ``$XDG_STATE_HOME`` from the box's container env.

    This is the host-assembly mirror of :func:`resolve_xdg` for the
    ``XDG_STATE_HOME`` var, evaluated against the BOX's environment (*box_env*,
    the assembled ``container_env``) rather than the host process env.  Honors
    the var iff it is set AND absolute (per the XDG Base Directory spec); else
    falls back to ``<BOX_HOME>/.local/state``.

    Returning a :class:`PurePosixPath` (no filesystem touch) keeps it correct
    for an in-container path computed on the host.  The matching box-side shell
    reads ``${XDG_STATE_HOME:-$HOME/.local/state}`` (see ``helper-init.sh``) and
    the in-box CLI uses :func:`xdg`, so all three agree by construction.
    """
    val = (box_env or {}).get("XDG_STATE_HOME", "")
    if val and PurePosixPath(val).is_absolute():
        return PurePosixPath(val)
    return PurePosixPath(BOX_HOME) / ".local" / "state"


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
# ``global`` holds the global settings/registry files; ``channelroot`` carries a
# skeleton of sub-keys (their behavior/wiring is Phase 6 — here they only need
# to resolve).  The OLD per-leaf ``boxes`` location is resolved separately as a
# transitional value (see :func:`resolve_system_paths`) and is NOT a key here.
SYSTEM_PATH_DEFAULTS: dict[str, str] = {
    "system.data": "$XDG_DATA_HOME/kanibako",
    "system.backup": "@system.data/backup",
    "system.agents": "@system.data/agents",
    "system.channelroot": "@system.data/channels",
    "system.global": "@system.data/global",
    "system.base_template": "@system.global/base_template",
    "system.settings": "@system.global/settings.yaml",
    "system.primary_workset": "@system.data/primary_workset",
    "system.registry": "@system.global/registry.yaml",
    "system.cache": "$XDG_CACHE_HOME/kanibako",
    "system.runtime": "$XDG_RUNTIME_DIR/kanibako",
    # Channels skeleton (Phase 6 fills sub-key behavior).
    "system.channels.commons": "@system.channelroot/commons",
    "system.channels.chat": "@system.channelroot/chat",
    "system.channels.broadcast": "@system.channels.chat/broadcast.md",
    "system.channels.mailboxes": "@system.channelroot/mailboxes",
    "system.channels.share": "@system.channelroot/share",
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
    :data:`SYSTEM_PATH_DEFAULTS`, plus the derived PRIMARY-workset pseudo-keys
    ``system._boxes`` / ``system._primary_vault_ro`` /
    ``system._primary_vault_rw`` / ``system._primary_logs`` (boxes/vault/logs
    under ``@system.primary_workset``; Phase 5 moved them here).
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
        # system.* config paths are always scalar strings (no structured
        # category leaves at this tier); narrow the now-``object``-typed value.
        return expand_expr(
            str(rv.value), space="host", ctx=ctx, lookup=lookup, chain=chain,
        )

    resolved: dict[str, Path] = {}
    for key in SYSTEM_PATH_DEFAULTS:
        rv = resolve_value(key, levels=levels, ctx=ctx, lookup=lookup)
        if isinstance(rv, _Unset):  # Unreachable: every key has a default.
            raise SettingsError(f"Unresolvable system path: {key}")
        expanded = expand_expr(str(rv.value), space="host", ctx=ctx, lookup=lookup)
        resolved[key] = Path(expanded)

    # PRIMARY-workset box/vault/logs roots, derived from the resolved PRIMARY
    # workset dir.  Phase 5 moved boxes/vault/logs out of ``@system.data/boxes``
    # (and out of the per-project workspace, for the vault) into the PRIMARY
    # workset so the PRIMARY workset is a real on-disk dir like a named one.
    pw = resolved["system.primary_workset"]
    resolved["system._boxes"] = pw / "boxes"
    resolved["system._primary_vault_ro"] = pw / "vault" / "ro"
    resolved["system._primary_vault_rw"] = pw / "vault" / "rw"
    resolved["system._primary_logs"] = pw / "logs"
    return resolved


def load_system_config(
    user_config_path: Path, *, data_home: Path, home: Path,
) -> dict[str, Path]:
    """Resolve the ``system.*`` config tier from the CONFIG file set.

    The CONFIG (``system.*``) set is two files, read in cascade order so the
    most-authoritative present value of each ``system.<leaf>`` set-value
    wins **before** expression resolution:

    1. ``/etc/kanibako/config_base.yaml`` — site-wide overridable defaults
       (least specific).
    2. *user_config_path* — the user's global ``~/.config/kanibako.yaml``
       (overrides the base).

    Missing files are skipped (each contributes nothing).  The merged set-values
    are handed to :func:`resolve_system_paths`, which fills in
    :data:`SYSTEM_PATH_DEFAULTS` and resolves ``@``-/``$XDG_*``-references.

    Keys are the bare ``system.<leaf>`` form (the ``.path`` segment was dropped
    in the system.* reorg); the on-disk config shape is a flat ``[system]``
    table.

    Back-compat: a user with only ``~/.config/kanibako.yaml`` (no ``/etc``
    file) gets exactly the prior behavior — the base layer is empty, so the
    user file is the sole source of set-values.
    """
    # Lazy import to avoid a config <-> paths import cycle at module load.
    from kanibako.config import (
        config_base_path,
        load_config,
    )

    set_values: dict[str, str] = {}
    # base < user.  load_config(...).system_paths yields the file's
    # ``system.path.<leaf>`` set-values (full dotted keys), or {} when the file
    # is absent — so missing layers are skipped automatically.
    set_values.update(load_config(config_base_path()).system_paths)
    set_values.update(load_config(user_config_path).system_paths)

    return resolve_system_paths(set_values, data_home=data_home, home=home)


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
    # only ~/.config/kanibako.yaml gets the prior behavior (empty /etc layer).
    resolved = load_system_config(
        config_file, data_home=data_home, home=Path.home(),
    )
    data_path = resolved["system.data"]
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
        data=resolved["system.data"],
        backup=resolved["system.backup"],
        agents=resolved["system.agents"],
        channels=resolved["system.channelroot"],
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
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths (PRIMARY mode).

    When *initialize* is True (used by ``start``), missing project directories
    are created and credential templates are copied in.  When False (used by
    subcommands like ``archive``/``purge``), the paths are merely computed.

    Phase 5: PRIMARY boxes/vault/logs live under ``@system.primary_workset``
    (the real PRIMARY-workset dir); there is no layout axis.  Per-box state is
    ``boxes/<name>/`` (metadata + shell) with the vault at
    ``@system.primary_workset/vault/{ro,rw}/<name>``.

    *enable_vault* controls whether vault directories are created and mounted.
    Defaults to True for new projects; existing projects read from ``settings.yaml``.
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

    # Drop-in import: the registry reverse-lookup missed, but an on-disk PRIMARY
    # box for this workspace may exist under @system.primary_workset/boxes (a
    # dropped-in / moved tree).  On-disk metadata is authoritative — import it
    # (alert + register; name collision → refuse), then re-resolve the dir.
    if not project_name:
        from kanibako import import_reconcile

        imported = import_reconcile.import_primary_box_for_workspace(
            std.data_path, std.boxes, project_path,
        )
        if imported:
            project_name, project_dir_path = _resolve_local_dir(
                std.data_path, project_path_str, std.boxes,
            )

    metadata_path = project_dir_path

    # Check for stored paths in settings.yaml (enables user overrides).
    project_toml = metadata_path / BOX_META_FILE
    meta = read_project_meta(project_toml)
    _default_shell, _default_vro, _default_vrw = _primary_box_paths(
        std, metadata_path, project_name or metadata_path.name,
    )
    if meta:
        shell_path = Path(meta["shell"]) if meta["shell"] else _default_shell
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else _default_vro
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else _default_vrw
        actual_vault_enabled = meta.get("enable_vault", True) if enable_vault is None else enable_vault
    else:
        shell_path, vault_ro_path, vault_rw_path = (
            _default_shell, _default_vro, _default_vrw,
        )
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    # Group-auth (block #2): carry the WORKSET-level on-disk policy (default
    # workset's config.yaml ``group_auth``) and the BOX-level on-disk choice
    # (this box's meta ``group_auth``) SEPARATELY — the capability chain keys them
    # to distinct keys (workset → ``workset.group_auth_enabled``, box →
    # ``box.group_auth_on``) and the EFFECTIVE bool is resolved through the launch
    # snapshot. (Was a single merged ``actual_group_auth`` flat bool.) JC-3
    # read-compat: a False at either level maps to the chain override.
    from kanibako.workset import default_workset
    workset_group_auth = bool(default_workset(std).group_auth)
    box_group_auth = bool(meta.get("group_auth", True)) if meta else True

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
        shell_path, vault_ro_path, vault_rw_path = _primary_box_paths(
            std, metadata_path, project_name,
        )
        project_toml = metadata_path / BOX_META_FILE

        _init_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        write_project_meta(
            project_toml,
            mode="primary",
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            enable_vault=actual_vault_enabled,
            metadata=str(metadata_path),
            project_hash=phash,
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
        # Backfill settings.yaml for old-format projects (pre-v0.8).
        if metadata_path.is_dir() and read_project_meta(metadata_path / BOX_META_FILE) is None:
            # Use directory name as project name (name-based dirs).
            _bf_name = metadata_path.name if not metadata_path.name.startswith(phash[:8]) else ""
            write_project_meta(
                metadata_path / BOX_META_FILE,
                mode="primary",
                workspace=str(project_path),
                shell=str(shell_path),
                vault_ro=str(vault_ro_path),
                vault_rw=str(vault_rw_path),
                enable_vault=actual_vault_enabled,
                metadata=str(metadata_path),
                project_hash=phash,
                name=_bf_name,
            )

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
        group_auth=box_group_auth,
        workset_group_auth=workset_group_auth,
        name=project_name,
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



def _primary_box_paths(
    std: StandardPaths, metadata_path: Path, box_name: str,
) -> tuple[Path, Path, Path]:
    """Fixed PRIMARY-mode ``(shell, vault_ro, vault_rw)`` (no layout axis).

    Shell lives under the per-box metadata dir (``boxes/<name>/home``); the
    vault lives under the PRIMARY workset (``@system.primary_workset/vault/{ro,
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
    ``@system.data/logs/<id>/`` location):

    * PRIMARY    → ``@system.primary_workset/logs/<box>.jsonl`` (``std.primary_logs``)
    * NAMED      → ``@workset.logs/<box>.jsonl`` (``<workset_root>/logs/<box>``)
    * STANDALONE → ``@meta.workset.path/box_data/<box>.jsonl`` (inside ``box_data/``)

    The caller is responsible for guarantee-creating the parent dir before the
    bind (L7).  The box-side dest stays ``$XDG_STATE_HOME/kanibako/helpers.jsonl``.
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


_STANDALONE_META_DIR = "box_data"


def _is_standalone_meta_dir(root: Path) -> bool:
    """True only if *root* is a real standalone project root.

    The walk marker is a ``box_data/`` directory present under *root* PLUS a box
    ``settings.yaml`` AT THE ROOT (``<root>/settings.yaml``, NOT inside
    ``box_data/``) that declares ``box.mode = "standalone"``.  Requiring both
    keeps an unrelated ``box_data/`` directory from ever being mistaken for a
    standalone marker, and distinguishes standalone from a NAMED workset (whose
    root ``settings.yaml`` carries ``workset.meta`` and has no ``box_data/``).
    """
    meta_dir = root / _STANDALONE_META_DIR
    toml = root / BOX_META_FILE
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
    3. Walk ancestors for on-disk markers — a ``box_data/`` standalone marker,
       or an unregistered NAMED workset root (a ``settings.yaml`` carrying a
       ``workset.meta`` identity).  Both are drop-in *imported* on discovery
       (registered + an alert to stderr; a name collision REFUSES — see
       :mod:`kanibako.import_reconcile`).
    4. Default — ``primary`` mode at the original *project_dir*.
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
        return DetectionResult(BoxMode.named, resolved)

    # 2. Name-based default-mode check (one-pass scan, deepest match wins).
    ac_ancestor = _find_local_ancestor(resolved, std.data_path, std.boxes)
    if ac_ancestor is not None:
        return DetectionResult(BoxMode.primary, ac_ancestor)

    # 3. Walk ancestors for on-disk markers (standalone box_data/ or an
    # unregistered NAMED workset root).  On-disk metadata is authoritative; a
    # discovered-but-unregistered entity is IMPORTED here (alert + register;
    # collision → refuse) so a dropped-in tree is re-discovered.  The named
    # check runs first at each level: a workset root may itself contain a
    # box_data/ dir, but its settings.yaml workset.meta marker is the more
    # specific identity.
    from kanibako import import_reconcile
    from kanibako.workset import WORKSET_META_FILE, read_workset_meta

    current = resolved
    while True:
        # NAMED: an unregistered workset root (settings.yaml carrying a
        # workset.meta identity, name not in the registry).  Import it, then the
        # standard workset check resolves it.
        if read_workset_meta(current / WORKSET_META_FILE) is not None:
            import_reconcile.import_named_workset(std.data_path, current)
            ws_after = _check_workset(resolved, std)
            if ws_after is not None:
                return ws_after

        # STANDALONE: a box_data/ directory holding a real standalone metadata
        # file (box.mode = "standalone").  A bare directory is not enough (the
        # metadata file must declare standalone mode).
        if _is_standalone_meta_dir(current):
            import_reconcile.import_standalone(std.data_path, current)
            return DetectionResult(BoxMode.standalone, current)

        # Stop conditions: reached $HOME or filesystem root.
        if current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 4. Default: primary mode at the original directory.
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
    from kanibako import registry_store

    worksets_section = registry_store.load_section(
        std.data_path, "worksets"
    )
    if not worksets_section:
        return None

    for _root_str in worksets_section.values():
        ws_root = Path(_root_str).resolve()
        ws_workspaces = ws_root / "workspaces"
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

    # Check for stored paths in settings.yaml (enables user overrides).
    project_toml = metadata_path / BOX_META_FILE
    meta = read_project_meta(project_toml)
    # Honor a stored workspace override (set when the project was connected to
    # an EXTERNAL directory): the external dir is the live workspace.  Mirrors
    # the describe path (iter_projects), which already reads meta["workspace"].
    if meta and meta.get("workspace"):
        project_path = Path(meta["workspace"])
    _ws_shell, _ws_vro, _ws_vrw = _workset_box_paths(
        metadata_path, ws.vault_dir, project_name,
    )
    if meta:
        shell_path = Path(meta["shell"]) if meta["shell"] else _ws_shell
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else _ws_vro
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else _ws_vrw
        actual_vault_enabled = meta.get("enable_vault", True) if enable_vault is None else enable_vault
    else:
        shell_path, vault_ro_path, vault_rw_path = _ws_shell, _ws_vro, _ws_vrw
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    # Group-auth (block #2): carry the WORKSET-level policy (ws.group_auth) and
    # the BOX-level choice (this box's meta group_auth) SEPARATELY for the
    # capability chain (was a single merged bool); the effective bool resolves
    # through the launch snapshot. JC-3 read-compat at both levels.
    workset_group_auth = bool(ws.group_auth)
    box_group_auth = bool(meta.get("group_auth", True)) if meta else True

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        write_project_meta(
            project_toml,
            mode="named",
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            enable_vault=actual_vault_enabled,
            group_auth=box_group_auth,
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
        mode=BoxMode.named,
        enable_vault=actual_vault_enabled,
        group_auth=box_group_auth,
        workset_group_auth=workset_group_auth,
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

    *project_path* is read from ``settings.yaml`` (``workspace`` field) when
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
        # Prefer settings.yaml workspace field.
        meta = read_project_meta(entry / BOX_META_FILE)
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
        return resolve_standalone_project(std, config, root_str, initialize=initialize)
    return resolve_project(std, config, project_dir=root_str, initialize=initialize)


def resolve_box_target(
    std: StandardPaths,
    config: KanibakoConfig,
    value: str | None = None,
    *,
    initialize: bool = False,
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
    caller's job via :func:`kanibako.box_identity.is_valid_box_name` — this
    resolver does not reject on name shape.

    ``None`` / empty *value* resolves the cwd box (delegates to
    :func:`resolve_any_project`), matching the positional-``project`` default.
    """
    # Empty / None -> cwd resolution (same as a bare positional default).
    if not value:
        return _flag_nonconforming(
            resolve_any_project(std, config, value, initialize=initialize)
        )

    # NAME-first: a bare token (no separator) that names a registered STANDALONE
    # box wins over a same-named relative path.  resolve_any_project covers the
    # projects/worksets registry + paths, but NOT the standalone-name domain, so
    # check it here before falling through.
    if "/" not in value:
        from kanibako import registry_store

        standalone = registry_store.load_standalone(std.data_path)
        # Box names are lowercase (R2); fold the query for the lookup.
        root_str = standalone.get(value.lower())
        if root_str is not None:
            return _flag_nonconforming(
                resolve_standalone_project(
                    std, config, root_str, initialize=initialize,
                )
            )

    # Else: NAME (projects/worksets/qualified) or PATH, both via the existing
    # resolver (name-precedence for bare tokens is already handled there).
    return _flag_nonconforming(
        resolve_any_project(std, config, value, initialize=initialize)
    )


def _flag_nonconforming(proj: ProjectPaths) -> ProjectPaths:
    """Warn (do NOT reject) when a resolved box's name violates the blocklist.

    Pre-existing boxes created before the §Design 8 box-name constraint still
    resolve (the canonical-id/registry matchers are structural, not policy-
    gated); but a non-conforming name is FLAGGED on use so the drift is visible.
    Returns *proj* unchanged.
    """
    from kanibako.box_identity import box_name_reason

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


def establish_standalone(
    std: StandardPaths,
    root: Path,
    *,
    enable_vault: bool,
    group_auth: bool,
    name: str = "",
) -> tuple[str, Path, Path, Path]:
    """Establish a standalone box at *root*: identity + meta + registration.

    The single shared core behind all three standalone paths (``create
    --standalone``, ``convert --standalone``, ``duplicate --standalone``).  It

    1. derives the box identity via :func:`box_identity.resolve_standalone_name`
       — a fresh canonical ``<random24>_<leaf>`` (whole-name collision regen vs
       ``registry.standalone``) when *name* is empty, otherwise honoring the
       supplied (lowercased) ``--name``: a verbatim canonical id if free (else
       refuse), or a fresh prefix over the supplied string as the leaf;
    2. writes the standalone ``<root>/settings.yaml`` meta (``mode=standalone``
       + the fixed STANDALONE path table via :func:`_standalone_box_paths`); the
       box ``settings.yaml`` lives at the ROOT (the ``box_data/`` marker dir
       holds only ``home/`` + the ``<box>.jsonl`` helper log);
    3. registers the box in ``registry.standalone`` (``box_name`` → *root*).

    *root* is the standalone project dir.  The box-data dir (``root/box_data``)
    must already exist (each caller creates/copies it before calling).  Returns
    ``(box_name, shell_path, vault_ro, vault_rw)`` so callers can build their
    result state without recomputing the table.  Callers own their own
    surrounding concerns (file copies, unwind registration, old-name
    unregister) — only the identity/meta/register core lives here.
    """
    from kanibako import box_identity, registry_store

    box_data = root / _STANDALONE_META_DIR
    workspace = root / "workspace"
    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)
    # phash derives from the resolved root (a standalone tree is drop-in
    # portable); the on-disk string fields below use *root* verbatim, matching
    # each call site's prior behavior.
    phash = project_hash(str(root.resolve()))

    existing = registry_store.standalone_box_names(std.data_path)
    box_name = box_identity.resolve_standalone_name(root, name, existing)

    write_project_meta(
        root / BOX_META_FILE,
        mode="standalone",
        workspace=str(workspace),
        shell=str(shell_path),
        vault_ro=str(vault_ro_path),
        vault_rw=str(vault_rw_path),
        enable_vault=enable_vault,
        group_auth=group_auth,
        metadata=str(box_data),
        project_hash=phash,
        name=box_name,
    )
    registry_store.register_standalone(std.data_path, box_name, root)
    return box_name, shell_path, vault_ro_path, vault_rw_path


def resolve_standalone_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    enable_vault: bool | None = None,
    group_auth: bool | None = None,
    name: str = "",
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths for standalone mode.

    All project state lives inside *project_dir* itself.
    No data is written to ``$XDG_DATA_HOME``.

    Phase 5d/Part 3 (drift H+I): no layout axis.  The project *root* (the
    runtime dir) is the standalone workset root and holds, in fixed positions:
    ``settings.yaml`` (the box meta, AT THE ROOT — ``metadata_path``), a
    ``workspace/`` subdir (the live workspace → ``~/workspace`` — the
    ``project_path``), a ``box_data/`` marker dir holding ``home/`` + the
    ``<box>.jsonl`` helper log, and ``vault/{ro,rw}/``.  The box identity is
    ``<random24>_<sanitized leaf>`` (generated + registered in
    ``registry.standalone`` at create time; reused from the stored meta after).
    """
    raw = project_dir or os.getcwd()
    root = Path(raw).resolve()

    if not root.is_dir():
        raise ProjectError(f"Project path '{root}' does not exist.")

    # The hash + identity key off the ROOT (the standalone workset root), which
    # is stable; the workspace subdir is the bind source, not the identity.
    phash = project_hash(str(root))

    # Metadata at the ROOT (settings.yaml); the ``box_data/`` marker dir holds
    # home/ + the helper log.  ``project_path`` is the ``workspace/`` subdir.
    metadata_path = root
    box_data = root / _STANDALONE_META_DIR
    project_path = root / "workspace"
    project_toml = root / BOX_META_FILE

    meta = None
    if box_data.is_dir() and project_toml.is_file():
        meta = read_project_meta(project_toml)

    # STANDALONE paths are ALWAYS derived from the (current) root, never the
    # stored absolutes: a standalone tree is drop-in portable, so a moved/
    # imported tree must resolve against its new location.  The resolved.*
    # section in settings.yaml is advisory only (BUG#1 fix); home/vault always
    # live at the fixed box_data/home + <root>/vault/{ro,rw} positions.
    shell_path, vault_ro_path, vault_rw_path = _standalone_box_paths(root)
    if meta:
        actual_vault_enabled = (
            meta.get("enable_vault", True) if enable_vault is None else enable_vault
        )
    else:
        actual_vault_enabled = enable_vault if enable_vault is not None else True

    # Box identity: reuse the stored name; for a fresh box, resolve the identity
    # from the user-supplied *name* (empty → fresh canonical) at create time via
    # establish_standalone → box_identity.resolve_standalone_name.
    box_name = meta.get("name", "") if meta else ""
    # The user's explicit --name (only meaningful when establishing a new box;
    # ignored once meta exists since the stored identity is authoritative).
    requested_name = name

    # Group-auth (block #2): standalone has NO workset group, so only the
    # BOX-level choice is carried (explicit --distinct-auth param > on-disk meta >
    # default True). The chain pins the workset keys to literal False for
    # standalone (short-circuit, spec §2c L315-316), so effective group-auth is
    # ALWAYS False for a lone box; this value only governs the box.group_auth_on
    # CHOICE key (carried for persistence/read-compat symmetry).
    box_group_auth = (
        group_auth
        if group_auth is not None
        else (bool(meta.get("group_auth", True)) if meta else True)
    )

    is_new = False
    if initialize and not box_data.is_dir():
        # Not-yet-initialized iff the ``box_data/`` marker dir is absent (the
        # root itself always exists — it is the runtime dir).
        # Pre-flight the requested --name BEFORE any FS mutation so a doomed
        # create (a verbatim-canonical name already taken) refuses up front
        # rather than leaving an orphaned half-created box_data/ + vault/ tree
        # (BUG-A).  establish_standalone re-resolves the name authoritatively;
        # this only surfaces the refusable collision early.
        from kanibako import box_identity, registry_store
        box_identity.validate_standalone_name(
            requested_name,
            registry_store.standalone_box_names(std.data_path),
        )
        _init_standalone_project(
            std, box_data, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            enable_vault=actual_vault_enabled,
        )
        # Identity + meta + registration via the shared establish core.  The
        # init block is only reached when no meta exists, so the identity is
        # resolved fresh from the user-supplied --name (empty → fresh canonical).
        box_name, shell_path, vault_ro_path, vault_rw_path = establish_standalone(
            std, root,
            enable_vault=actual_vault_enabled,
            group_auth=box_group_auth,
            name=requested_name,
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
        group_auth=box_group_auth,
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
