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


@pytest.fixture(autouse=True)
def _no_magicmock_dir_leak():
    """Fail any test that leaks a ``<MagicMock ...>`` entry into the CWD.

    A MagicMock ``std`` whose unconfigured channel/path attrs reach a
    ``mkdir`` (e.g. the channels L7 guarantee-create) creates literal
    directories named after the mock's ``repr`` in the working tree.  This
    autouse fixture snapshots the CWD before each test and asserts no
    ``MagicMock``-named entry appeared after — turning the silent filesystem
    leak into a test failure rather than working-tree pollution.
    """
    from pathlib import Path

    cwd = Path.cwd()

    def _magicmock_entries() -> set[str]:
        try:
            return {p.name for p in cwd.iterdir() if "MagicMock" in p.name}
        except OSError:
            return set()

    before = _magicmock_entries()
    try:
        yield
    finally:
        leaked = _magicmock_entries() - before
        # Clean up so a single offending test doesn't cascade into the rest.
        for name in leaked:
            try:
                target = cwd / name
                if target.is_dir():
                    import shutil as _shutil

                    _shutil.rmtree(target, ignore_errors=True)
                else:
                    target.unlink(missing_ok=True)
            except OSError:
                pass
        assert not leaked, (
            f"test leaked MagicMock-named entries into {cwd}: {sorted(leaked)} "
            "(an unmocked MagicMock path likely reached mkdir)"
        )


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
    """Write a default kanibako.yaml and return its path."""
    config_home = tmp_home / "config"
    cf = config_home / "kanibako.yaml"
    write_global_config(cf)
    return cf


@pytest.fixture
def sample_config():
    """Return a default KanibakoConfig."""
    return KanibakoConfig()


@pytest.fixture
def config(config_file):
    """Load config from the default kanibako.yaml."""
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
    from kanibako.paths import resolve_system_paths
    config = load_config(config_file)
    data_home = tmp_home / "data"
    data_path = resolve_system_paths(
        config.system_paths, data_home=data_home, home=tmp_home,
    )["system.data"]
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
        from pathlib import Path

        from kanibako.agent_config import AgentConfig
        from kanibako.paths import ProjectGroup, BoxMode

        with (
            patch("kanibako.commands.start.load_config") as m_load_config,
            patch("kanibako.commands.start.load_std_paths") as m_load_std,
            patch("kanibako.commands.start.resolve_box_target") as m_resolve_any,
            patch("kanibako.commands.start.load_merged_config") as m_merged,
            patch("kanibako.commands.start.ContainerRuntime") as m_rt_cls,
            patch("kanibako.commands.start.resolve_target") as m_resolve_target,
            # W1 unified resolver: _run_container resolves the agent name via
            # config.resolve_agent (cascade + installed-count rule) BEFORE
            # resolve_target.  Patch it to a fixed name so _run_container tests
            # don't depend on the host's installed-agent set (which would
            # otherwise trigger Gate-2a with the meta package's 3 adapters).
            # Tests exercising the no-agent / ambiguous paths re-patch it.
            patch(
                "kanibako.config.resolve_agent", return_value="claude",
            ) as m_resolve_agent,
            patch("kanibako.commands.start._upgrade_shell"),
            patch("kanibako.templates.apply_template_layers"),
            # Channel mounts run through the real category resolver + L7
            # guarantee-create (mkdir of every rw source).  Driven with the
            # MagicMock ``std`` here, the channel sources are MagicMock repr
            # strings, which the guarantee-create would mkdir as literal
            # ``<MagicMock ...>`` directories in the test's CWD.  Stub it to an
            # empty mount set — channel-mount behavior is covered by
            # tests/test_commands/test_start_channels.py with a real ``std``.
            patch(
                "kanibako.commands.start._build_channel_mounts",
                return_value=[],
            ) as m_build_channel_mounts,
            patch("kanibako.commands.start.load_agent_config") as m_load_agent_cfg,
            patch("kanibako.commands.start.fcntl") as m_fcntl,
            patch("kanibako.commands.start._container_logs", return_value=""),
            patch("builtins.open", MagicMock()) as m_open,
            patch("kanibako.commands.start.load_registry", return_value={}) as m_load_registry,
            patch("kanibako.commands.start.registry_path"),
            # Credential-sync engine (descriptor path).  Default to a no-op mock
            # so descriptor-bearing targets driven through _run_container don't
            # perform real filesystem credential ops against MagicMock project
            # paths.  Tests asserting credsync routing re-patch it locally.
            patch("kanibako.commands.start.credsync") as m_credsync,
            # Two-tier launch baseline check: default to "all present" so the
            # probe never spins a real container in unit tests. Individual tests
            # override m_launch_check to exercise tier-1/tier-2 behavior.
            patch(
                "kanibako.commands.start._check_launch_baseline",
                return_value=[],
            ) as m_launch_check,
            # Launch-path agent-binary validation runs on install.binary
            # (a MagicMock here); default it to "valid" so the fail-fast
            # guard never trips. Tests exercising the guard override
            # m_validate_binary.return_value with a reason string.
            patch(
                "kanibako.targets.base._validate_agent_binary",
                return_value=None,
            ) as m_validate_binary,
            # Virtiofs-graphroot preflight: default to "not applicable" so the
            # MagicMock runtime never triggers a real podman info / procfs read.
            # Tests exercising the diagnostic override its return value.
            patch(
                "kanibako.image_sharing.virtiofs_graphroot_message",
                return_value=None,
            ) as m_virtiofs_check,
        ):
            proj = MagicMock()
            proj.is_new = False
            proj.mode = BoxMode.primary
            proj.group = ProjectGroup(
                name="default",
                root=Path("/data"),
                is_default=True,
                local_shared_base=Path("/data"),
            )
            proj.metadata_path = MagicMock()
            proj.metadata_path.__truediv__ = MagicMock(return_value=MagicMock())
            proj.shell_path = MagicMock()
            proj.shell_path.__truediv__ = MagicMock(return_value=MagicMock())
            proj.name = "testproject"
            m_resolve_any.return_value = proj

            merged = MagicMock()
            merged.box_image = "test:latest"
            merged.box_agent = ""
            merged.box_bootstrap_program = "tmux"
            merged.box_share_images = False
            # Helpers off by default in the mock (MagicMock attrs are truthy);
            # individual tests opt in by setting merged.allow_helpers = True.
            merged.allow_helpers = False
            m_merged.return_value = merged

            runtime = MagicMock()
            runtime.run.return_value = 0
            runtime.is_running.return_value = False
            runtime.container_exists.return_value = False
            runtime.exec.return_value = 0
            runtime.rm.return_value = True

            # Simulate container start: after run(), is_running returns True.
            _original_run = runtime.run
            def _run_side_effect(*a, **kw):
                runtime.is_running.return_value = True
                return _original_run.return_value
            runtime.run.side_effect = _run_side_effect
            m_rt_cls.return_value = runtime

            # Agent config mock: empty defaults (no run_args, no state, no env)
            agent_cfg = AgentConfig()
            m_load_agent_cfg.return_value = agent_cfg
            # start.py derives the agent config path as
            # agent_settings_path(std.agents, "<id>") == std.agents / "<id>" /
            # "settings.yaml" (two __truediv__ hops).  std is a MagicMock, so the
            # derived path's .exists() is truthy by default — which keeps the
            # "config already present" branch.
            mock_agent_path = (
                m_load_std.return_value.agents
                .__truediv__.return_value      # std.agents / "<id>"
                .__truediv__.return_value      # ... / "settings.yaml"
            )
            mock_agent_path.exists.return_value = True

            # Target mock: resolve_target returns a mock target with detect/build_cli_args/etc.
            target = MagicMock()
            target.display_name = "Claude Code"
            target.name = "claude"
            target.default_entrypoint = "claude"
            target.config_dir_name = ".claude"
            # In-box setup is opt-in (base default: no setup). Mirror the real
            # claude default (setup_entrypoint=None) so the auth-probe setup
            # branch never fires for the default mock target; check_auth passes
            # by default. Tests exercising the in-box-setup path re-set these.
            target.setup_entrypoint = None
            target.setup_args = []
            target.check_auth.return_value = True
            target.writeback_extra.return_value = None
            # Post-launch config-validation matcher (refined FIX 2) is opt-in:
            # default False so the launch-validation branch only fires for tests
            # that explicitly drive it (otherwise a truthy MagicMock would trip
            # it whenever container logs are non-empty).
            target.should_run_setup.return_value = False
            target.should_retry_new_session.return_value = False
            # Default the mock target to the DESCRIPTOR path using claude's REAL
            # descriptor: the descriptor-only plugin system means a target with a
            # host `install` ALWAYS has a descriptor (the legacy
            # build_cli_args / binary_mounts / apply_state launch hooks were
            # removed from start.py).  start.py therefore drives argv / env /
            # delivery mounts through ``kanibako.targets.assembly`` for this mock.
            # A test that wants a descriptor-less target (only NoAgentTarget in
            # production) sets ``target.descriptor = None`` explicitly.
            from kanibako.plugins.claude.target import ClaudeTarget
            target.descriptor = ClaudeTarget().descriptor

            # Make the detected install resolvable by ``descriptor_mounts``: its
            # AGENT_CRITICAL bindings (share -> install_dir, launcher -> launcher)
            # require a host source whose ``.exists()`` is True and that survives
            # ``Mount(src, dest, opts)`` construction, so use REAL paths under a
            # temp dir (created here so they actually exist on disk).  Plugins +
            # cache are no longer descriptor bindings (Part 3a) — they flow
            # through the category resolver from ``default_shares()``.
            import tempfile
            _install_root = Path(tempfile.mkdtemp(prefix="kanibako-test-install-"))
            _install_dir = _install_root / "share" / "claude"
            _install_dir.mkdir(parents=True, exist_ok=True)
            _launcher = _install_root / "bin" / "claude"
            _launcher.parent.mkdir(parents=True, exist_ok=True)
            _launcher.write_bytes(b"\x7fELF" + b"\x00" * 50)

            from kanibako.targets.base import AgentInstall
            install_mock = AgentInstall(
                name="claude",
                binary=_launcher,
                install_dir=_install_dir,
                launcher=_launcher,
            )
            target.detect.return_value = install_mock
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
                resolve_agent=m_resolve_agent,
                target=target,
                agent_cfg=agent_cfg,
                load_agent_config=m_load_agent_cfg,
                agent_config_path=mock_agent_path,
                fcntl=m_fcntl,
                open=m_open,
                load_registry=m_load_registry,
                launch_check=m_launch_check,
                validate_binary=m_validate_binary,
                credsync=m_credsync,
                build_channel_mounts=m_build_channel_mounts,
                virtiofs_check=m_virtiofs_check,
            )

    return _make
