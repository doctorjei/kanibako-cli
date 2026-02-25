"""XDG resolution, project hash computation, directory creation, and initialization."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from kanibako.config import (
    KanibakoConfig,
    config_file_path,
    load_config,
    migrate_config,
    read_project_meta,
    write_project_meta,
)
from kanibako.errors import ConfigError, ProjectError, WorksetError
from kanibako.utils import project_hash

if TYPE_CHECKING:
    from kanibako.workset import Workset


class ProjectMode(Enum):
    """How a project's persistent state is organized on disk."""

    account_centric = "account_centric"
    workset = "workset"
    decentralized = "decentralized"


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
    ProjectMode.account_centric: ProjectLayout.default,
    ProjectMode.workset: ProjectLayout.robust,
    ProjectMode.decentralized: ProjectLayout.simple,
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


@dataclass
class ProjectPaths:
    """Resolved paths for a specific project."""

    project_path: Path
    project_hash: str
    metadata_path: Path      # host-only: project.toml, breadcrumb, lock
    shell_path: Path         # mounted as /home/agent
    vault_ro_path: Path      # {project}/vault/share-ro (→ /home/agent/share-ro)
    vault_rw_path: Path      # {project}/vault/share-rw (→ /home/agent/share-rw)
    is_new: bool = field(default=False)
    mode: ProjectMode = field(default=ProjectMode.account_centric)
    layout: ProjectLayout = field(default=ProjectLayout.default)
    vault_enabled: bool = field(default=True)
    auth: str = field(default="shared")
    global_shared_path: Path | None = field(default=None)
    local_shared_path: Path | None = field(default=None)


def xdg(env_var: str, default_suffix: str) -> Path:
    """Resolve an XDG directory from environment or default under $HOME."""
    val = os.environ.get(env_var, "")
    if val:
        return Path(val).resolve()
    return Path.home() / default_suffix


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


def _migrate_settings_to_boxes(data_path: Path) -> None:
    """Rename ``data_path/settings`` to ``data_path/boxes`` if needed."""
    old = data_path / "settings"
    new = data_path / "boxes"
    if old.is_dir() and not new.exists():
        old.rename(new)
        import sys
        print(f"Migrated: {old} → {new}", file=sys.stderr)


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
                f"{config_file} is missing. Run 'kanibako setup' to set up."
            )
        config = load_config(config_file)

    rel = config.paths_data_path or "kanibako"
    data_path = data_home / rel
    state_path = state_home / rel
    cache_path = cache_home / rel

    # Migrate settings/ -> boxes/ if needed.
    _migrate_settings_to_boxes(data_path)

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
    )


def resolve_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    vault_enabled: bool | None = None,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths.

    When *initialize* is True (used by ``start``), missing project directories
    are created and credential templates are copied in.  When False (used by
    subcommands like ``archive``/``purge``), the paths are merely computed.

    *layout* overrides the default layout for new projects.  Existing projects
    read their layout from ``project.toml``.

    *vault_enabled* controls whether vault directories are created and mounted.
    Defaults to True for new projects; existing projects read from ``project.toml``.
    """
    raw = project_dir or os.getcwd()
    project_path = Path(raw).resolve()

    if not project_path.is_dir():
        raise ProjectError(f"Project path '{project_path}' does not exist.")

    phash = project_hash(str(project_path))
    project_dir_path = std.data_path / "boxes" / phash
    metadata_path = project_dir_path

    # Check for stored paths in project.toml (enables user overrides).
    project_toml = metadata_path / "project.toml"
    meta = read_project_meta(project_toml)
    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.account_centric]
        shell_path = Path(meta["shell"]) if meta["shell"] else metadata_path / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else project_path / "vault" / "share-ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else project_path / "vault" / "share-rw"
        actual_vault_enabled = meta.get("vault_enabled", True) if vault_enabled is None else vault_enabled
    else:
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.account_centric]
        shell_path, vault_ro_path, vault_rw_path = _compute_ac_paths(
            actual_layout, metadata_path, project_path,
        )
        actual_vault_enabled = vault_enabled if vault_enabled is not None else True

    is_new = False
    if initialize and not project_dir_path.is_dir():
        _init_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            vault_enabled=actual_vault_enabled,
        )
        _global_shared = std.data_path / config.paths_shared / "global"
        _local_shared = std.data_path / config.paths_shared
        write_project_meta(
            project_toml,
            mode="account_centric",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            vault_enabled=actual_vault_enabled,
            metadata=str(metadata_path),
            project_hash=phash,
            global_shared=str(_global_shared),
            local_shared=str(_local_shared),
        )
        is_new = True

    if initialize:
        # Recovery: ensure shell exists even if metadata_path was present.
        if not shell_path.is_dir():
            shell_path.mkdir(parents=True, exist_ok=True)
            _bootstrap_shell(shell_path)
        # Backfill project-path.txt for pre-existing projects.
        breadcrumb = metadata_path / "project-path.txt"
        if metadata_path.is_dir() and not breadcrumb.exists():
            breadcrumb.write_text(str(project_path) + "\n")
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
                        f"\nNOTE: In robust layout, the account-centric vault "
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
        mode=ProjectMode.account_centric,
        layout=actual_layout,
        vault_enabled=actual_vault_enabled,
        global_shared_path=_computed_global_shared,
        local_shared_path=_computed_local_shared,
    )


def _compute_ac_paths(
    layout: ProjectLayout, metadata_path: Path, project_path: Path,
) -> tuple[Path, Path, Path]:
    """Compute (shell, vault_ro, vault_rw) for account-centric mode."""
    if layout == ProjectLayout.simple:
        shell = project_path / ".shell"
        vault_ro = project_path / "vault" / "share-ro"
        vault_rw = project_path / "vault" / "share-rw"
    elif layout == ProjectLayout.robust:
        shell = metadata_path / "shell"
        vault_ro = metadata_path / "vault" / "share-ro"
        vault_rw = metadata_path / "vault" / "share-rw"
    else:  # default
        shell = metadata_path / "shell"
        vault_ro = project_path / "vault" / "share-ro"
        vault_rw = project_path / "vault" / "share-rw"
    return shell, vault_ro, vault_rw


def _compute_ws_paths(
    layout: ProjectLayout, metadata_path: Path, project_path: Path,
    vault_base: Path, project_name: str,
) -> tuple[Path, Path, Path]:
    """Compute (shell, vault_ro, vault_rw) for workset mode."""
    if layout == ProjectLayout.simple:
        shell = project_path / ".shell"
        vault_ro = project_path / "vault" / "share-ro"
        vault_rw = project_path / "vault" / "share-rw"
    else:  # default / tree (identical for workset)
        shell = metadata_path / "shell"
        vault_ro = vault_base / project_name / "share-ro"
        vault_rw = vault_base / project_name / "share-rw"
    return shell, vault_ro, vault_rw


def _compute_decentral_paths(
    layout: ProjectLayout, metadata_path: Path, project_path: Path,
) -> tuple[Path, Path, Path]:
    """Compute (shell, vault_ro, vault_rw) for decentralized mode."""
    if layout == ProjectLayout.robust:
        shell = project_path / "shell"
        vault_ro = project_path / "vault" / "share-ro"
        vault_rw = project_path / "vault" / "share-rw"
    else:  # simple (default for decentralized)
        shell = metadata_path / "shell"
        vault_ro = project_path / "vault" / "share-ro"
        vault_rw = project_path / "vault" / "share-rw"
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

    In AC tree and WS default/tree layouts, vault dirs are stored outside the
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
    vault_enabled: bool = True,
) -> None:
    """Shared first-time project setup: create directories, bootstrap shell.

    This helper is called by both ``_init_project`` (account-centric) and
    ``_init_decentralized_project``.  It performs every step common to both
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
    if vault_enabled:
        vault_ro_path.mkdir(parents=True, exist_ok=True)
        vault_rw_path.mkdir(parents=True, exist_ok=True)
        # .gitignore in vault/ to exclude share-rw from version control.
        vault_dir = vault_ro_path.parent
        gitignore = vault_dir / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("share-rw/\n")

    print("done.", file=sys.stderr)


def _init_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
    vault_ro_path: Path,
    vault_rw_path: Path,
    project_path: Path,
    *,
    vault_enabled: bool = True,
) -> None:
    """First-time project setup: create directories, copy credentials from host."""
    _init_common(
        std, metadata_path, shell_path,
        vault_ro_path, vault_rw_path, project_path,
        vault_enabled=vault_enabled,
    )
    # Record the original project path for reverse lookup.
    (metadata_path / "project-path.txt").write_text(str(project_path) + "\n")


def _copy_credentials_from_host(shell_path: Path) -> None:
    """Copy credentials directly from host ~/.claude/ into the shell directory.

    Copies ``~/.claude/.credentials.json`` → ``shell/.claude/.credentials.json``
    and filters ``~/.claude.json`` → ``shell/.claude.json``.
    """
    from kanibako.credentials import filter_settings

    claude_dir = shell_path / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    host_creds = Path.home() / ".claude" / ".credentials.json"
    if host_creds.is_file():
        shutil.copy2(str(host_creds), str(claude_dir / ".credentials.json"))

    host_settings = Path.home() / ".claude.json"
    if host_settings.is_file():
        filter_settings(host_settings, shell_path / ".claude.json")
    else:
        (shell_path / ".claude.json").touch()


def detect_project_mode(
    project_dir: Path,
    std: StandardPaths,
    config: KanibakoConfig,
) -> DetectionResult:
    """Infer which project mode applies to *project_dir*.

    Walks ancestor directories (up to ``$HOME`` or filesystem root) looking
    for project markers.  Returns a ``DetectionResult`` with the detected
    mode and the ancestor directory where the marker was found.

    Detection order (at each ancestor level):
    1. Workset — *project_dir* lives inside a registered workset root
       (``workspaces/`` subdirectory first, then the root itself).
    2. Account-centric — ``boxes/{hash}/`` already exists under
       *std.data_path*.
    3. Decentralized — a ``.kanibako`` or ``kanibako`` **directory** exists
       inside the ancestor.  ``.kanibako`` takes priority when both exist.
    4. Default — ``account_centric`` at the original *project_dir*.
    """
    resolved = project_dir.resolve()
    home = Path.home().resolve()

    # 1. Workset check (no walk needed — relative_to handles subdirs).
    ws_result = _check_workset(resolved, std)
    if ws_result is not None:
        return ws_result

    # 2. Walk ancestors for AC + decentralized markers.
    current = resolved
    while True:
        # AC check: hash current, check boxes/{hash}/ exists.
        phash = project_hash(str(current))
        settings_path = std.data_path / "boxes" / phash
        if settings_path.is_dir():
            return DetectionResult(ProjectMode.account_centric, current)

        # Decentralized check: .kanibako/ or kanibako/ directory.
        if (current / ".kanibako").is_dir():
            return DetectionResult(ProjectMode.decentralized, current)
        # Dotless kanibako/ requires project.toml to avoid false positives
        # on directories that happen to be named "kanibako".
        _nodot = current / "kanibako"
        if _nodot.is_dir() and (_nodot / "project.toml").is_file():
            return DetectionResult(ProjectMode.decentralized, current)

        # Stop conditions: reached $HOME or filesystem root.
        if current == home:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    # 3. Default: account_centric at the original directory.
    return DetectionResult(ProjectMode.account_centric, resolved)


def _check_workset(
    resolved_dir: Path,
    std: StandardPaths,
) -> DetectionResult | None:
    """Check whether *resolved_dir* is inside a registered workset.

    Returns a ``DetectionResult`` if found, ``None`` otherwise.
    Checks ``workspaces/`` first (specific project), then the workset root
    itself (inside workset but not necessarily a project workspace).
    """
    worksets_toml = std.data_path / "worksets.toml"
    if not worksets_toml.is_file():
        return None

    import tomllib as _tomllib

    with open(worksets_toml, "rb") as _f:
        _data = _tomllib.load(_f)

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
    ws: Workset,
    project_name: str,
    std: StandardPaths,
    config: KanibakoConfig,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    vault_enabled: bool | None = None,
) -> ProjectPaths:
    """Resolve per-project paths for a project inside a workset.

    Raises ``WorksetError`` if *project_name* is not registered in *ws*.
    """
    # Look up project in workset.
    found = None
    for p in ws.projects:
        if p.name == project_name:
            found = p
            break
    if found is None:
        raise WorksetError(
            f"Project '{project_name}' not found in workset '{ws.name}'."
        )

    # Name-based paths (not hash-based).
    project_path = ws.workspaces_dir / project_name
    project_dir = ws.projects_dir / project_name
    metadata_path = project_dir

    # Check for stored paths in project.toml (enables user overrides).
    project_toml = metadata_path / "project.toml"
    meta = read_project_meta(project_toml)
    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.workset]
        shell_path = Path(meta["shell"]) if meta["shell"] else project_dir / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else ws.vault_dir / project_name / "share-ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else ws.vault_dir / project_name / "share-rw"
        actual_vault_enabled = meta.get("vault_enabled", True) if vault_enabled is None else vault_enabled
    else:
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.workset]
        shell_path, vault_ro_path, vault_rw_path = _compute_ws_paths(
            actual_layout, metadata_path, project_path, ws.vault_dir, project_name,
        )
        actual_vault_enabled = vault_enabled if vault_enabled is not None else True

    # Auth mode: workset-level overrides project-level.
    actual_auth = getattr(ws, "auth", "shared")
    if actual_auth == "shared" and meta:
        actual_auth = meta.get("auth", "shared")

    # Hash the resolved workspace path for container naming.
    phash = project_hash(str(project_path.resolve()))

    is_new = False
    if initialize and not shell_path.is_dir():
        _init_workset_project(std, metadata_path, shell_path)
        _ws_global_shared = std.data_path / config.paths_shared / "global"
        _ws_local_shared = Path(ws.root) / config.paths_shared
        write_project_meta(
            project_toml,
            mode="workset",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            vault_enabled=actual_vault_enabled,
            auth=actual_auth,
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
    _ws_computed_local = Path(ws.root) / config.paths_shared
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
        vault_enabled=actual_vault_enabled,
        auth=actual_auth,
        global_shared_path=_ws_computed_global,
        local_shared_path=_ws_computed_local,
    )


def _init_workset_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
) -> None:
    """First-time workset project setup: bootstrap shell directory.

    Unlike ``_init_project``, this does not write a ``project-path.txt``
    breadcrumb (workset.toml already records source_path) and does not create
    vault ``.gitignore`` files (vault lives under the workset root, not inside
    a user git repo).

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

    *project_path* is read from the ``project-path.txt`` breadcrumb when
    available; otherwise it is ``None``.
    """
    projects_dir = std.data_path / "boxes"
    if not projects_dir.is_dir():
        return []
    results: list[tuple[Path, Path | None]] = []
    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        breadcrumb = entry / "project-path.txt"
        project_path = None
        if breadcrumb.is_file():
            text = breadcrumb.read_text().strip()
            if text:
                project_path = Path(text)
        results.append((entry, project_path))
    return results


def iter_workset_projects(
    std: StandardPaths,
    config: KanibakoConfig,
) -> list[tuple[str, "Workset", list[tuple[str, str]]]]:
    """Return workset project info for all registered worksets.

    Each entry is ``(workset_name, workset, [(project_name, status), ...])``.
    Status is ``"ok"``, ``"missing"`` (no workspace), or ``"no-data"``
    (no project dir).
    """
    import sys

    from kanibako.workset import list_worksets, load_workset

    registry = list_worksets(std)
    results: list[tuple[str, Workset, list[tuple[str, str]]]] = []

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


def _find_workset_for_path(project_dir: Path, std: StandardPaths) -> tuple[Workset, str | None]:
    """Return ``(Workset, project_name)`` for a path inside a workset.

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
    raw_dir = Path(raw).resolve()
    detection = detect_project_mode(raw_dir, std, config)
    root_str = str(detection.project_root)

    if detection.mode == ProjectMode.workset:
        ws, proj_name = _find_workset_for_path(raw_dir, std)
        if proj_name is None:
            raise WorksetError(
                f"Inside workset '{ws.name}' but not in a specific project workspace. "
                f"Change to a project directory under {ws.workspaces_dir}/."
            )
        return resolve_workset_project(ws, proj_name, std, config, initialize=initialize)
    if detection.mode == ProjectMode.decentralized:
        return resolve_decentralized_project(std, config, root_str, initialize=initialize)
    return resolve_project(std, config, project_dir=root_str, initialize=initialize)


def resolve_decentralized_project(
    std: StandardPaths,
    config: KanibakoConfig,
    project_dir: str | None = None,
    *,
    initialize: bool = False,
    layout: ProjectLayout | None = None,
    vault_enabled: bool | None = None,
    auth: str | None = None,
) -> ProjectPaths:
    """Resolve (and optionally initialize) per-project paths for decentralized mode.

    All project state lives inside *project_dir* itself.
    No data is written to ``$XDG_DATA_HOME``.
    """
    raw = project_dir or os.getcwd()
    project_path = Path(raw).resolve()

    if not project_path.is_dir():
        raise ProjectError(f"Project path '{project_path}' does not exist.")

    phash = project_hash(str(project_path))

    # Determine metadata_path (depends on layout for decentralized).
    # For tree layout: {project}/kanibako (no dot)
    # For simple (default): {project}/.kanibako (dot prefix)
    # Check both locations for existing projects.
    dot_meta = project_path / ".kanibako"
    nodot_meta = project_path / "kanibako"

    # Check for stored paths in existing metadata.
    meta = None
    actual_layout = None
    if dot_meta.is_dir():
        meta = read_project_meta(dot_meta / "project.toml")
        metadata_path = dot_meta
    elif nodot_meta.is_dir():
        meta = read_project_meta(nodot_meta / "project.toml")
        metadata_path = nodot_meta
    else:
        # New project — determine layout and metadata_path.
        actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.decentralized]
        if actual_layout == ProjectLayout.robust:
            metadata_path = nodot_meta
        else:
            metadata_path = dot_meta

    if meta:
        actual_layout = ProjectLayout(meta["layout"]) if meta.get("layout") else _DEFAULT_LAYOUT[ProjectMode.decentralized]
        shell_path = Path(meta["shell"]) if meta["shell"] else metadata_path / "shell"
        vault_ro_path = Path(meta["vault_ro"]) if meta["vault_ro"] else project_path / "vault" / "share-ro"
        vault_rw_path = Path(meta["vault_rw"]) if meta["vault_rw"] else project_path / "vault" / "share-rw"
        actual_vault_enabled = meta.get("vault_enabled", True) if vault_enabled is None else vault_enabled
    else:
        if actual_layout is None:
            actual_layout = layout or _DEFAULT_LAYOUT[ProjectMode.decentralized]
        shell_path, vault_ro_path, vault_rw_path = _compute_decentral_paths(
            actual_layout, metadata_path, project_path,
        )
        actual_vault_enabled = vault_enabled if vault_enabled is not None else True

    project_toml = metadata_path / "project.toml"

    # Auth mode for decentralized: explicit param > meta > default.
    actual_auth = auth or (meta.get("auth", "shared") if meta else "shared")

    is_new = False
    if initialize and not metadata_path.is_dir():
        _init_decentralized_project(
            std, metadata_path, shell_path,
            vault_ro_path, vault_rw_path, project_path,
            vault_enabled=actual_vault_enabled,
        )
        write_project_meta(
            project_toml,
            mode="decentralized",
            layout=actual_layout.value,
            workspace=str(project_path),
            shell=str(shell_path),
            vault_ro=str(vault_ro_path),
            vault_rw=str(vault_rw_path),
            vault_enabled=actual_vault_enabled,
            auth=actual_auth,
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
        mode=ProjectMode.decentralized,
        layout=actual_layout,
        vault_enabled=actual_vault_enabled,
        auth=actual_auth,
        global_shared_path=None,
    )


def _init_decentralized_project(
    std: StandardPaths,
    metadata_path: Path,
    shell_path: Path,
    vault_ro_path: Path,
    vault_rw_path: Path,
    project_path: Path,
    *,
    vault_enabled: bool = True,
) -> None:
    """First-time decentralized project setup: all state inside project dir.

    Unlike ``_init_project``, this does not write a ``project-path.txt``
    breadcrumb (the project is self-contained).  Unlike workset init, this
    *does* create vault directories and a ``.gitignore`` (vault lives inside
    the user's project, likely a git repo).

    Credential copy is handled separately by ``target.init_home()`` in
    ``start.py``, after template application.
    """
    _init_common(
        std, metadata_path, shell_path,
        vault_ro_path, vault_rw_path, project_path,
        vault_enabled=vault_enabled,
    )
