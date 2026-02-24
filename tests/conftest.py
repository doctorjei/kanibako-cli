"""Shared fixtures for kanibako tests."""

from __future__ import annotations

pytest_plugins = ["tests.conftest_integration"]

import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kanibako.config import KanibakoConfig, load_config, write_global_config


@pytest.fixture
def tmp_home(tmp_path, monkeypatch):
    """Set HOME, XDG dirs, and CWD to an isolated temp tree."""
    home = tmp_path / "home"
    home.mkdir()
    config_home = tmp_path / "config"
    data_home = tmp_path / "data"
    state_home = tmp_path / "state"
    cache_home = tmp_path / "cache"
    for d in (config_home, data_home, state_home, cache_home):
        d.mkdir()

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)

    return tmp_path


@pytest.fixture
def config_file(tmp_home):
    """Write a default kanibako.toml and return its path."""
    config_home = tmp_home / "config"
    cf = config_home / "kanibako.toml"
    write_global_config(cf)
    return cf


@pytest.fixture
def sample_config():
    """Return a default KanibakoConfig."""
    return KanibakoConfig()


@pytest.fixture
def config(config_file):
    """Load config from the default kanibako.toml."""
    return load_config(config_file)


@pytest.fixture
def std(config_file):
    """Load standard paths from the default config."""
    from kanibako.paths import load_std_paths
    config = load_config(config_file)
    return load_std_paths(config)


@pytest.fixture
def project_dir(tmp_home):
    """Return the pre-existing project directory created by tmp_home."""
    return tmp_home / "project"


@pytest.fixture
def credentials_dir(tmp_home, config_file):
    """Set up host credentials and return the data path."""
    from kanibako.config import load_config
    config = load_config(config_file)
    data_home = tmp_home / "data"
    data_path = data_home / (config.paths_data_path or "kanibako")
    data_path.mkdir(parents=True, exist_ok=True)

    # Write host credentials (used directly by init now)
    home = tmp_home / "home"
    host_claude = home / ".claude"
    host_claude.mkdir(parents=True, exist_ok=True)
    creds = {"claudeAiOauth": {"token": "test-token"}, "someOtherKey": True}
    (host_claude / ".credentials.json").write_text(json.dumps(creds))

    # Write host settings file
    cfg = {"oauthAccount": "test", "hasCompletedOnboarding": True}
    (home / ".claude.json").write_text(json.dumps(cfg))

    return data_path


@pytest.fixture
def fake_git_repo(tmp_home):
    """Create a real git repo (git init + commit) in tmp_home/project. Returns the project Path."""
    project = tmp_home / "project"
    project.mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=project, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=project, capture_output=True, check=True,
    )
    readme = project / "README.md"
    readme.write_text("# test\n")
    subprocess.run(["git", "add", "."], cwd=project, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=project, capture_output=True, check=True,
    )
    return project


@pytest.fixture
def corrupt_credentials(tmp_home):
    """Create credential files with various defects. Returns dict of scenario->Path."""
    base = tmp_home / "corrupt_creds"
    base.mkdir()

    malformed = base / "malformed.json"
    malformed.write_text("{bad json!!")

    empty = base / "empty.json"
    empty.write_text("")

    missing_oauth = base / "missing_oauth.json"
    missing_oauth.write_text(json.dumps({"someOtherKey": True}))

    permission_denied = base / "noperm.json"
    permission_denied.write_text(json.dumps({"claudeAiOauth": {"token": "x"}}))
    permission_denied.chmod(0o000)

    return {
        "malformed": malformed,
        "empty": empty,
        "missing_oauth": missing_oauth,
        "permission_denied": permission_denied,
    }


@pytest.fixture
def project_env(config_file, credentials_dir, tmp_home):
    """Combines config + credentials + resolve_project into a single namespace."""
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths, resolve_project

    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = str(tmp_home / "project")
    proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
    return SimpleNamespace(
        config=config, std=std, proj=proj, project_dir=project_dir,
        config_file=config_file, tmp_home=tmp_home,
    )


@pytest.fixture
def mock_runtime():
    """Pre-configured MagicMock of ContainerRuntime."""
    rt = MagicMock()
    rt.image_exists.return_value = False
    rt.pull.return_value = True
    rt.run.return_value = 0
    return rt


@pytest.fixture
def start_mocks():
    """Context-manager fixture that patches all external deps of _run_container.

    Yields a SimpleNamespace of all mocks for fine-grained control.
    """
    @contextmanager
    def _make():
        from kanibako.agents import AgentConfig
        from kanibako.paths import ProjectMode

        with (
            patch("kanibako.commands.start.load_config") as m_load_config,
            patch("kanibako.commands.start.load_std_paths") as m_load_std,
            patch("kanibako.commands.start.resolve_any_project") as m_resolve_any,
            patch("kanibako.commands.start.load_merged_config") as m_merged,
            patch("kanibako.commands.start.ContainerRuntime") as m_rt_cls,
            patch("kanibako.commands.start.resolve_target") as m_resolve_target,
            patch("kanibako.commands.start._upgrade_shell"),
            patch("kanibako.templates.apply_shell_template"),
            patch("kanibako.commands.start.load_agent_config") as m_load_agent_cfg,
            patch("kanibako.commands.start.agent_toml_path") as m_agent_toml_path,
            patch("kanibako.commands.start.fcntl") as m_fcntl,
            patch("builtins.open", MagicMock()) as m_open,
        ):
            proj = MagicMock()
            proj.is_new = False
            proj.mode = ProjectMode.account_centric
            proj.metadata_path = MagicMock()
            proj.metadata_path.__truediv__ = MagicMock(return_value=MagicMock())
            proj.shell_path = MagicMock()
            proj.shell_path.__truediv__ = MagicMock(return_value=MagicMock())
            proj.global_shared_path = None
            proj.local_shared_path = None
            m_resolve_any.return_value = proj

            merged = MagicMock()
            merged.container_image = "test:latest"
            merged.target_name = ""
            merged.paths_agents = "agents"
            m_merged.return_value = merged

            runtime = MagicMock()
            runtime.run.return_value = 0
            m_rt_cls.return_value = runtime

            # Agent config mock: empty defaults (no default_args, no state, no env)
            agent_cfg = AgentConfig()
            m_load_agent_cfg.return_value = agent_cfg
            # agent_toml_path returns a mock Path that reports exists()=True
            mock_agent_path = MagicMock()
            mock_agent_path.exists.return_value = True
            m_agent_toml_path.return_value = mock_agent_path

            # Target mock: resolve_target returns a mock target with detect/build_cli_args/etc.
            target = MagicMock()
            target.display_name = "Claude Code"
            target.name = "claude"
            target.detect.return_value = MagicMock()  # install object
            target.binary_mounts.return_value = []
            target.build_cli_args.side_effect = lambda *, safe_mode, resume_mode, new_session, is_new_project, extra_args: (
                _build_default_cli_args(safe_mode, resume_mode, new_session, is_new_project, extra_args)
            )
            target.apply_state.return_value = ([], {})
            m_resolve_target.return_value = target

            yield SimpleNamespace(
                load_config=m_load_config,
                load_std_paths=m_load_std,
                resolve_any_project=m_resolve_any,
                load_merged_config=m_merged,
                runtime_cls=m_rt_cls,
                runtime=runtime,
                proj=proj,
                merged=merged,
                resolve_target=m_resolve_target,
                target=target,
                agent_cfg=agent_cfg,
                load_agent_config=m_load_agent_cfg,
                agent_toml_path=m_agent_toml_path,
                fcntl=m_fcntl,
                open=m_open,
            )

    return _make


def _build_default_cli_args(
    safe_mode: bool, resume_mode: bool, new_session: bool,
    is_new_project: bool, extra_args: list[str],
) -> list[str]:
    """Reproduce ClaudeTarget.build_cli_args logic for test mocks."""
    cli_args: list[str] = []
    if not safe_mode:
        cli_args.append("--dangerously-skip-permissions")
    if resume_mode:
        cli_args.append("--resume")
    else:
        skip_continue = new_session or is_new_project
        if any(a in ("--resume", "-r") for a in extra_args):
            skip_continue = True
        if not skip_continue:
            cli_args.append("--continue")
    cli_args.extend(extra_args)
    return cli_args
