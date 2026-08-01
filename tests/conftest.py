"""Shared fixtures for kanibako tests."""

from __future__ import annotations

pytest_plugins = ["tests.conftest_integration", "tests._timing"]

import json
import subprocess
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock, patch

import pytest

from kanibako.agent_select import AgentSelection as _AgentSelection
from kanibako.config import KanibakoConfig, load_config, write_global_config

# The REAL launch-snapshot orchestrator, captured at import time (before any
# ``start_mocks`` patch replaces the module attribute) so the ``start_mocks``
# snapshot stub can DELEGATE to it for the conditional image/helper resolves
# (which carry only their own table against REAL paths and must run for real).
from kanibako.commands.start import (
    _resolve_launch_snapshot as _REAL_RESOLVE_LAUNCH_SNAPSHOT,
)


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
    """Write a default kanibako_config.yaml and return its path."""
    config_home = tmp_home / "config"
    cf = config_home / "kanibako_config.yaml"
    write_global_config(cf)
    return cf


@pytest.fixture
def sample_config():
    """Return a default KanibakoConfig."""
    return KanibakoConfig()


@pytest.fixture
def config(config_file):
    """Load config from the default kanibako_config.yaml."""
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
        config.config_paths, data_home=data_home, home=tmp_home,
    )["config.data"]
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
        import tempfile
        from pathlib import Path

        from kanibako.agent_config import AgentConfig
        from kanibako.paths import ProjectGroup, BoxMode

        # ⚑ REAL host roots for the box STORE. The §2a seed layers resolve their
        # destinations to absolute HOST paths under ``@meta.box.path``, and the
        # applier refuses a dest that does not land inside the box store — so a
        # MagicMock ``primary_workset`` would make every seed dest a MagicMock-repr
        # string and silently skip the seed. These two are the minimum realism the
        # seed path needs; everything else on ``std``/``proj`` stays mocked.
        #
        # ⚑⚑ AND THE REALISM HAS A COST THAT ONLY APPEARS ON CI. This fixture patches
        # ``kanibako.commands.start.ContainerRuntime``, but ``_protect_canon_skeleton``
        # imports ``ContainerRuntime`` from ``kanibako.runtime.container`` INSIDE the function
        # — so the REAL runtime runs, and on a host with working user namespaces the
        # box home's canon skeleton is genuinely chowned to a foreign SUBUID. The
        # ``TemporaryDirectory`` finalizer then dies in ``_resetperms``
        # (``os.chmod(path, 0o700)``) with ``PermissionError``. It is invisible on the
        # dev box (broken ``newuidmap`` ⇒ nothing is ever protected) and the
        # ``KANI_TEST_SIM_UNSHARE`` sim cannot reproduce it (it chmods as the OWNER;
        # the real defeat is foreign OWNERSHIP). Hence the reap in the ``finally``
        # below — see ``tests/support/protected_trees``.
        _store_tmp = tempfile.TemporaryDirectory(prefix="kanibako-start-mocks-")

        with (
            patch("kanibako.commands.start.load_config") as m_load_config,
            patch("kanibako.commands.start.load_std_paths") as m_load_std,
            patch("kanibako.commands.start.resolve_box_target") as m_resolve_any,
            patch("kanibako.commands.start.load_merged_config") as m_merged,
            patch("kanibako.commands.start.ContainerRuntime") as m_rt_cls,
            patch("kanibako.commands.start.resolve_target") as m_resolve_target,
            # P7 unified SELECTION seam: _run_container resolves the agent via
            # agent_select.select_agent (system.agent < workset pref < box pref <
            # --agent, then the installed-count rule) BEFORE resolve_target.
            # Patch it to a fixed selection so _run_container tests don't depend
            # on the host's installed-agent set (which would otherwise trigger
            # Gate-2a with the meta package's 3 adapters). Tests exercising the
            # no-agent / ambiguous paths re-patch it. (Was
            # ``kanibako.config.resolve_agent``, whose cascade P7 moved out.)
            patch(
                "kanibako.agent_select.select_agent",
                return_value=_AgentSelection(node="claude", source="settings"),
            ) as m_resolve_agent,
            patch("kanibako.commands.start._upgrade_shell"),
            # The layered template seed now flows through the keystore-routed
            # ``_apply_init_seeds`` (Q1: the separate on-disk staging route
            # retired). Its ``stage_layers`` apply is patched to a no-op here so a
            # MagicMock std/proj cannot mkdir/copy MagicMock-named paths; the real
            # layered-seed behavior is covered by tests/test_templates.py with a
            # REAL std/proj.
            patch("kanibako.templates.stage_layers"),
            # Block 7b: the launch-time CATEGORY resolution now runs through ONE
            # snapshot + ONE reconcile (``_resolve_launch_snapshot``) for the
            # always-available families (core / kani / channel / common / seeded),
            # then the conditional image/helper resolves at their own sites.
            # Driven with the MagicMock ``std``/``proj`` here, the category sources
            # are MagicMock repr strings whose L7 guarantee-create would mkdir
            # literal ``<MagicMock ...>`` dirs in the CWD.  So stub the orchestrator
            # to a controlled result — the per-family category behavior is covered
            # by the dedicated suites with a REAL ``std`` (test_start_channels.py /
            # test_settings_categories.py / test_settings_launch.py /
            # test_container_extended.py).  ``_seed_channel_files`` (the channel
            # chat-log seed side-effect) is no-op'd for the same reason.  The stub
            # still yields the AGENT delivery binds (claude's descriptor, resolved
            # against the real ``install_mock`` paths) so the descriptor-delivery
            # integration assertions hold.
            # Several same-module ``kanibako.commands.start`` patches are folded
            # into ONE ``patch.multiple`` to keep this large ``with`` under
            # Python's statically-nested-block limit (20).
            # J1 lifecycle journal: the launch seed gate now wraps
            # ``_seed_box_home`` with the write-ahead journal flow (write-entry ->
            # seed -> register -> clear-entry).  ``_write_create_entry`` /
            # ``_clear_create_entry`` / ``_register_new_box`` read
            # ``proj.shell_path``/``proj.mode``/``proj.name`` and write the
            # journal/registry, which — driven by the MagicMock proj here — would
            # write a literal ``MagicMock`` path key into the journal/registry, so
            # stub them to no-ops (same rationale as ``_seed_channel_files``). The
            # journal/recovery semantics are covered by the dedicated suites with
            # REAL paths (test_journal.py / test_create_recovery.py).
            patch.multiple(
                "kanibako.commands.start",
                _resolve_launch_snapshot=DEFAULT,
                _seed_channel_files=DEFAULT,
                _container_logs=DEFAULT,
                registry_path=DEFAULT,
                _write_create_entry=DEFAULT,
                _clear_create_entry=DEFAULT,
                _pending_create_entry=DEFAULT,
                _register_new_box=DEFAULT,
                # The load-or-error pre-flight resolves the box-INDEPENDENT agent
                # (explicit --agent OR the stored ``system.agent``) to decide
                # whether to DEFER box materialisation.  With a MagicMock
                # ``std.settings`` the real reader would feed a MagicMock to yaml
                # (>10 GB balloon risk), so default it to "no system default"
                # (bare — today's behavior). Tests exercising a system-default
                # persona override its return. (P7: renamed from
                # ``read_default_agent``.)
                read_system_agent=DEFAULT,
                # AGENT-scope ``bootstrap`` resolution (spec §2d L579): the
                # authoritative per-launch value _run_container reads off the
                # settings snapshot.  With a MagicMock ``std``/``proj`` the real
                # resolver would feed MagicMock paths to build_launch_snapshot, so
                # stub it to the ``tmux`` default; the zellij / ``none`` tests
                # override ``start_mocks.effective_bootstrap.return_value``.
                _effective_bootstrap=DEFAULT,
                # run_start's EARLY persistence-mode heuristic resolves the box +
                # agent to read the agent-scope bootstrap; stub it to the ``tmux``
                # default so a run_start unit test does not double-resolve the box.
                # (Connect tests that exercise the heuristic patch it locally.)
                _resolve_bootstrap_program=DEFAULT,
            ) as m_launch_mount_stubs,
            patch("kanibako.commands.start.load_agent_config") as m_load_agent_cfg,
            patch("kanibako.commands.start.fcntl") as m_fcntl,
            patch("builtins.open", MagicMock()) as m_open,
            patch("kanibako.commands.start.load_registry", return_value={}) as m_load_registry,
            # Seed-at-create (B7).  The launch path seeds ONLY when
            # ``proj.is_new`` (the just-registered box), via ``_seed_box_home``;
            # the fixture default ``proj.is_new = False`` means the seed does NOT
            # run for an ordinary relaunch test.  ``_seed_box_home`` itself is left
            # REAL so the credsync-routing tests (which set ``is_new = True``) can
            # observe its inner ``seed_cred_files`` call; its FS-touching steps are
            # already neutralized by the ``stage_layers`` / ``credsync`` /
            # ``_resolve_launch_snapshot`` patches above.
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
                "kanibako.runtime.image_sharing.virtiofs_graphroot_message",
                return_value=None,
            ) as m_virtiofs_check,
        ):
            # The channel chat-log seed side-effect is a no-op here (see the
            # patch.multiple rationale above); the snapshot orchestrator stub's
            # return value is wired below, once the descriptor + install_mock the
            # AGENT delivery binds derive from are built.
            # ``selected_source_root`` is a PURE helper (AuthSource → source Path)
            # that start.py calls to route ``writeback_extra`` to the correct tier
            # destination. Keep it REAL on the otherwise-mocked credsync module so
            # tier-routing tests observe the true selected source (host home for
            # global, the workset store for workset), not a MagicMock.
            from kanibako.targets.credsync import selected_source_root as _real_ssr
            m_credsync.selected_source_root = _real_ssr
            m_launch_mount_stubs["_seed_channel_files"].return_value = None
            m_launch_mount_stubs["_container_logs"].return_value = ""
            m_launch_mount_stubs["_write_create_entry"].return_value = None
            m_launch_mount_stubs["_clear_create_entry"].return_value = None
            # Relaunch default: no pending create entry → the seed gate does not
            # fire for an ordinary launch of an existing box.  Tests exercising
            # auto-create-at-launch set proj.is_new = True (or override this).
            m_launch_mount_stubs["_pending_create_entry"].return_value = None
            m_launch_mount_stubs["_register_new_box"].return_value = None
            # Default: no system-default agent → non-explicit launches are NOT
            # deferred (byte-identical to today's single materialising resolve).
            m_launch_mount_stubs["read_system_agent"].return_value = None
            # Default agent-scope bootstrap = tmux (the persistent-session default).
            m_launch_mount_stubs["_effective_bootstrap"].return_value = "tmux"
            m_launch_mount_stubs["_resolve_bootstrap_program"].return_value = "tmux"

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
            # A REAL box home: ``<primary_workset>/boxes/<name>/home``, matching what
            # ``@meta.box.path/home`` resolves to from the std roots below, so the
            # HOST-space seed dests land inside the box store (see the note above).
            _pw = Path(_store_tmp.name) / "primary_workset"
            proj.shell_path = _pw / "boxes" / "testproject" / "home"
            proj.shell_path.mkdir(parents=True, exist_ok=True)
            proj.name = "testproject"
            m_resolve_any.return_value = proj

            merged = MagicMock()
            merged.box_image = "test:latest"
            merged.box_agent_name = ""
            merged.box_share_images = False
            # A REAL box_shell string (not an auto MagicMock): resolve_box_shell
            # reads ``config.box_shell`` and the detach/no-agent launch paths feed
            # the result into shell command assembly (E2b's supervisor PID-1 shlex-
            # joins it), so it must stringify.  "sh" mirrors the real fallback shell.
            merged.box_shell = "sh"
            m_merged.return_value = merged

            runtime = MagicMock()
            runtime.run.return_value = 0
            runtime.is_running.return_value = False
            runtime.container_exists.return_value = False
            runtime.exec.return_value = 0
            runtime.rm.return_value = True

            # The bootstrap-attach loop gates the interactive exec on a CAPTURED
            # readiness probe (``exec_ready``).  Mirror it onto ``is_running`` so
            # the fake behaves realistically: a "running" container is ready for
            # exec, a "dead" one (crash-path tests flip ``is_running`` False) is
            # not — so those tests fall through to the log-showing path without
            # the loop spuriously attaching to a dead container.
            runtime.exec_ready.side_effect = (
                lambda *a, **kw: bool(runtime.is_running.return_value)
            )

            # Simulate container start: after run(), is_running returns True.
            _original_run = runtime.run
            def _run_side_effect(*a, **kw):
                runtime.is_running.return_value = True
                return _original_run.return_value
            runtime.run.side_effect = _run_side_effect
            m_rt_cls.return_value = runtime

            # The std roots the box-store resolution reads (see the note at the
            # top of this factory): REAL paths, so @meta.box.path resolves to
            # <primary_workset>/boxes/testproject and the seed dests are contained.
            m_load_std.return_value.primary_workset = _pw
            m_load_std.return_value.data = Path(_store_tmp.name) / "data"

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
            # through the category resolver from ``default_common()``.
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

            # Wire the launch-snapshot orchestrator stub (block 7b).  It returns a
            # controlled ``(snapshot, reconciled)``: the reconcile's
            # MOUNT winners are ONLY the AGENT delivery binds (claude's descriptor,
            # resolved against the real ``install_mock`` paths via the real 7a
            # representation + the committed pipeline) so the descriptor-delivery
            # integration assertions hold, and NO MagicMock-path category mounts
            # are produced (their behavior is covered by the dedicated suites with a
            # REAL ``std``).  Late image/helper resolves (gated off by default) call
            # the same stub; their controlled result has no agent binds and the
            # default off-gates mean they don't fire anyway.
            def _snapshot_side_effect(*a, **kw):
                if not kw.get("include_base_families", True):
                    # Conditional image/helper resolve — REAL paths, run for real
                    # (the module-level captured, unpatched orchestrator).
                    return _REAL_RESOLVE_LAUNCH_SNAPSHOT(*a, **kw)

                from kanibako.agent_representation import agent_default_partial
                from kanibako.settings_categories import reconcile_categories
                from kanibako.settings_launch import (
                    build_launch_snapshot,
                    snapshot_category_entries,
                )
                from kanibako.settings_resolve import ResolveCtx

                ctx = ResolveCtx(
                    agent_name="claude", workset_name=None,
                    host_home="/home/host", xdg={"XDG_DATA_HOME": "/data"},
                )
                _desc = kw.get("desc")
                _install = kw.get("install")
                # Mirror the real orchestrator: root the 7a partial under the
                # ACTIVE node-name (fix 2a), not install.name (the harness).
                _node = kw.get("agent_name", "claude")
                partial = (
                    agent_default_partial(_desc, _install, node_name=_node)
                    if _desc is not None and _install is not None
                    else None
                )
                # Block 7b (ruling A): flow the BEHAVIOR cascade into the stub's
                # snapshot too — the target's declared-default floor (→ agent.
                # default.*) + the per-agent FILE's flat state (agent_cfg.state,
                # wrapped under agent.<active>) — so the LIVE behavior read
                # (effective_behavior) returns the real model/access/etc. The
                # category sources stay None (MagicMock-path mounts are covered by
                # the dedicated suites with a REAL std).
                _target = kw.get("target")
                _agent_cfg = kw.get("agent_cfg")
                _floor = None
                _state = None
                if _target is not None:
                    _descriptors = _target.setting_descriptors()
                    if _descriptors:
                        _floor = {d.key: d.default for d in _descriptors}
                if _agent_cfg is not None:
                    _state = dict(_agent_cfg.state)
                # SECRET category (spec §2a secret_path): fold the active agent's
                # secret_path pointers into the snapshot as agent-scope category
                # defaults (already discriminated agent.<node>.secret_path.<VAR>), so
                # the reconcile emits the secret_path winners the delivery seam +
                # export shim consume — exactly as the real cascade would.
                _default_cats = None
                if _agent_cfg is not None and getattr(_agent_cfg, "secret_path", None):
                    _default_cats = {
                        f"agent.{_node}.secret_path.{var}": path
                        for var, path in _agent_cfg.secret_path.items()
                    }
                # allow_helpers is now an AGENT-scope behavior key (spec §2d): the
                # live launch reader (effective_behavior) resolves it off the
                # snapshot and DEFAULTS True (helpers ON). Unit tests drive the
                # start path with a MagicMock ``std``/``proj``, so the helper hub
                # must stay OFF (else its socket/log mkdir hits a MagicMock path
                # and leaks). Seed the agent.default FLOOR with allow_helpers=false
                # (the robust replacement for the old ``merged.allow_helpers =
                # False`` seam) — a test exercising the hub sets
                # ``start_mocks.agent_cfg.state["allow_helpers"] = "true"`` (the
                # active-over-default pick makes the per-agent slot WIN).
                _floor = _floor or {}
                _floor.setdefault("allow_helpers", "false")
                snap = build_launch_snapshot(
                    agent_name=_node, ctx=ctx,
                    system_path=None, agent_path=None,
                    workset_path=None, box_path=None,
                    behavior_floor=_floor, default_categories=_default_cats,
                    agent_partial=partial,
                    agent_state=_state,
                    # P8: the §1A CLI LEVEL must be FORWARDED, not dropped. The
                    # per-launch flags (-M / -N/-C/-R) now reach the launch ONLY
                    # through this level, so a stub that swallows it would make
                    # every flag silently inert — and the flag tests in
                    # test_start.py would be asserting the harness, not the code.
                    cli_level=kw.get("cli_level"),
                )
                entries = snapshot_category_entries(
                    snap, active_agent=_node, box_ctx=ctx,
                )
                rec = reconcile_categories(
                    entries, deliver_creds=kw.get("deliver_creds", True),
                )
                # ⚑ DELIBERATE OMISSION: the real orchestrator also installs the
                # ``meta.derived.*`` materialisation (``derive_binding_keys``) and
                # emits the §0 row-5 collision warnings. This stub does NEITHER,
                # because mirroring them would put a second copy of that seam in
                # the test harness — the drift risk the single-route rule exists
                # to avoid, and this snapshot carries no abstract declarations to
                # derive from anyway. Tests that need either use the REAL path or
                # call the functions directly (tests/test_category_collisions.py).
                return snap, rec

            m_launch_mount_stubs[
                "_resolve_launch_snapshot"
            ].side_effect = _snapshot_side_effect

            try:
                yield SimpleNamespace(
                    store_tmp=_store_tmp,
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
                    resolve_launch_snapshot=m_launch_mount_stubs["_resolve_launch_snapshot"],
                    seed_channel_files=m_launch_mount_stubs["_seed_channel_files"],
                    # Create-journal stubs (write-ahead recovery flow) — exposed so
                    # explicit-create tests can assert the launch path never resurrects
                    # a half-created box (no seed / register / clear on launch).
                    pending_create_entry=m_launch_mount_stubs["_pending_create_entry"],
                    register_new_box=m_launch_mount_stubs["_register_new_box"],
                    write_create_entry=m_launch_mount_stubs["_write_create_entry"],
                    read_system_agent=m_launch_mount_stubs["read_system_agent"],
                    effective_bootstrap=m_launch_mount_stubs["_effective_bootstrap"],
                    resolve_bootstrap_program=m_launch_mount_stubs["_resolve_bootstrap_program"],
                    virtiofs_check=m_virtiofs_check,
                )
            finally:
                # ⚑ ORDER IS THE WHOLE FIX: the protected box stores must be gone
                # BEFORE ``TemporaryDirectory``'s finalizer touches the tree, because
                # that finalizer chmods on its way in and cannot survive a
                # foreign-owned directory. Routed through the SAME escalating deleter
                # the product's lifecycle verbs use.
                from tests.support.protected_trees import reap_box_stores

                reap_box_stores(_store_tmp.name)
                _store_tmp.cleanup()

    return _make


# ---------------------------------------------------------------------------
# The PROTECTED-CANON simulation fixture (ordered after the R1b CI-red postmortem).
# ---------------------------------------------------------------------------


@pytest.fixture
def protected_canon(monkeypatch):
    """Reproduce the PROTECTED canon-skeleton state on a host that cannot make one.

    ⚑⚑ WHY THIS EXISTS.  ``core_defaults._protect_canon_skeleton`` chowns the
    skeleton to a SUBUID via ``podman unshare`` and only then chmods it.  Where
    ``podman unshare`` fails — a host with a broken ``newuidmap``, which includes
    this project's dev box — the chown returns False and the guard RETURNS EARLY, so
    the skeleton is left agent-owned at plain 0755.  Every local test run therefore
    exercises ONLY the DEGRADED path, while CI runners and bifrost (working rootless
    userns) genuinely protect the box.  A test can be green locally and red in CI
    purely because of that split — which is exactly what happened once, when a
    test-body ``rmtree`` met 555 directories on CI.

    This fixture makes the protected state reproducible LOCALLY and
    DETERMINISTICALLY: ``unshare_chown`` reports success (ownership cannot be
    simulated without the namespace, and the test host owns the tree anyway) and
    ``unshare_chmod`` applies a REAL ``os.chmod``.  What that buys is the MODE half —
    the 555 dirs and 444 file mountpoints — which is what most of this class of bug
    trips over.

    ⚑ IT IS NOT AN ORACLE.  Ownership is NOT simulated, so nothing here can certify
    "the agent cannot write the books"; only CI and bifrost can.  Use it to REPRODUCE
    and REGRESS-TEST mode-related failures, never to claim protected-state proof.

    ⚑ AUTOMATIC WHERE REQUESTED, opt-in globally: set ``KANI_TEST_SIM_UNSHARE=1`` to
    make the simulation the default for the whole run (useful for sweeping the suite
    the way the R1b postmortem did); otherwise it applies only to tests that request
    the fixture by name.
    """
    import os as _os

    from kanibako.runtime.container import ContainerRuntime

    def _chown(self, paths, uid, gid):
        return bool(paths)

    def _chmod(self, paths, mode):
        if not paths:
            return False
        for p in paths:
            try:
                _os.chmod(p, int(mode, 8))
            except OSError:
                return False
        return True

    monkeypatch.setattr(ContainerRuntime, "unshare_chown", _chown, raising=True)
    monkeypatch.setattr(ContainerRuntime, "unshare_chmod", _chmod, raising=True)
    return True


@pytest.fixture(autouse=True)
def _reap_protected_box_stores(tmp_path):
    """Reap any PROTECTED box store a test left under its ``tmp_path``.

    ⚑ THE SWEEP FINDING. Fifteen unit files drive the real create/skeleton path
    (``run_create`` / ``seed_new_box`` / ``_run_container`` / the lifecycle verbs)
    into ``tmp_path`` WITHOUT patching ``kanibako.runtime.container.ContainerRuntime`` — the
    import site ``_protect_canon_skeleton`` actually uses — so on a host with working
    user namespaces every one of them leaves foreign-owned directories behind.

    They are absent from the CI failure list, and the reason matters: pytest's
    ``tmp_path`` cleanup is DEFERRED (it keeps the last three numbered dirs and
    removes older ones at the START of a LATER session), and CI runners are fresh
    every run, so the sweep that would fail never runs there. On a PERSISTENT runner
    it does — that is the ``/tmp/pytest-of-*`` creep the e2e gate measured. Same
    class, later fuse.

    ⚑⚑ OFF BY DEFAULT — set ``KANI_TEST_REAP_TMP=1``, and set it on a persistent
    runner. Gated because the cost is real and the benefit here is not: finding a
    store means walking each test's whole ``tmp_path``, and those trees are not small
    (``test_commands/test_box.py`` alone: **7.9 s -> 94 s**, measured — a 12x
    regression on one file). Paying that on every gate to pre-empt a failure that
    provably does not occur where we gate is the wrong trade. The ONE site that
    genuinely fails (``start_mocks``'s ``TemporaryDirectory``, whose finalizer chmods
    and cannot be deferred) is reaped surgically and UNCONDITIONALLY at its known
    root, for free.
    """
    yield
    import os

    if os.environ.get("KANI_TEST_REAP_TMP") != "1":
        return
    from tests.support.protected_trees import reap_box_stores

    reap_box_stores(tmp_path)


@pytest.fixture(autouse=True)
def _sim_unshare_globally(request):
    """Apply :func:`protected_canon` to EVERY test when ``KANI_TEST_SIM_UNSHARE=1``.

    Zero-impact unless the env var is set: the fixture body returns immediately, so a
    normal run, CI and the rework gates all pay nothing.
    """
    import os

    if os.environ.get("KANI_TEST_SIM_UNSHARE") != "1":
        return
    if "protected_canon" in request.fixturenames:
        return  # already requested explicitly
    # ⚑ EXEMPT the tests OF the two methods being simulated. Patching
    # ``unshare_chown``/``unshare_chmod`` inside ``test_container.py``'s own suite for
    # them replaces the subject with the stub and turns four real assertions into
    # false reds — in precisely the tool that exists to remove false greens.
    if request.node.get_closest_marker("no_unshare_sim") is not None:
        return
    module = getattr(request.node, "module", None)
    if getattr(module, "__name__", "").endswith("test_container"):
        cls = getattr(request.node, "cls", None)
        if cls is not None and cls.__name__ == "TestUnshareChownChmod":
            return
    request.getfixturevalue("protected_canon")
