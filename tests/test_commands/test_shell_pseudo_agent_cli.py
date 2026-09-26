"""The shell pseudo-agent is ADDRESSABLE on the CLI; its name stays unclaimable.

Keyspec §2b: the plain-shell box is an effective ``@system.agent`` of ``shell``, and
*"as any other agent, it can be invoked via CLI ``--agent shell``"*.  Its §2d fence
declares its own settings file (``meta.agent.shell.settings``), which is the file a
``kanibako agent <verb> shell`` edits (§2a: a config verb writes the file of the
scope it names).  §2d reserves the pseudo-agent names against CLAIMANTS — *"MUST be
refused to any agent, persona, or harness"* — so ``shell`` inside a composite ref,
and ``default`` anywhere a ref selects an agent, stay refused ([R178]).

Driven through :func:`kanibako.cli.main` on an isolated HOME/XDG tree
(``tmp_home``): the argparse tree, flag relevance and the command handler all run,
not a function in isolation.  Before ``agent_ref.parse_agent_address`` existed,
``create --agent shell`` and every ``agent <verb> shell`` refused with *"'shell' is a
RESERVED pseudo-agent name"*.  The last class pins the READERS of a live plain-shell
box's ``KANIBAKO_AGENT=shell`` stamp (``stop``, ``code``, the creds watcher) on a box
the CLI created; only the container runtime is faked there.
"""

from __future__ import annotations

import pytest

from kanibako.settings.config_io import load_doc


def _kb(*argv: str) -> int:
    """Run the real CLI entry point and return its exit code."""
    from kanibako.cli import main

    with pytest.raises(SystemExit) as ei:
        main(list(argv))
    return ei.value.code


def _std():
    from kanibako.settings.config import load_config, user_config_file
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(user_config_file()))


class TestCreateSelectsTheShellPseudoAgent:

    def test_create_agent_shell_creates_a_shell_box(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The box is created, and its saved selection IS the pseudo-agent.

        CONTROL: the same create with ``--agent claude`` seeds claude's layer-2
        template into the home, so the absence of ``.claude`` below is the shell
        tier installing no template (§2b), not a seed that never runs.
        """
        shell_dir = tmp_home / "shellbox"
        claude_dir = tmp_home / "claudebox"
        shell_dir.mkdir()
        claude_dir.mkdir()

        assert _kb("create", str(shell_dir), "--agent", "shell") == 0, (
            capsys.readouterr().err
        )
        assert _kb("create", str(claude_dir), "--agent", "claude") == 0, (
            capsys.readouterr().err
        )

        std = _std()
        shell_box = std.boxes / "shellbox"
        claude_box = std.boxes / "claudebox"
        assert load_doc(shell_box / "box.yaml")["pref"]["system"]["agent"] == "shell"
        assert (claude_box / "home" / ".claude").exists()
        assert not (shell_box / "home" / ".claude").exists()

    def test_the_selection_resolves_to_the_shell_node_for_any_case(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """``Shell`` is the same identifier ([R172]); the NODE it resolves to is lowercase."""
        from kanibako.settings.agent_select import select_agent
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import resolve_project

        path = tmp_home / "capbox"
        path.mkdir()
        assert _kb("create", str(path), "--agent", "Shell") == 0, (
            capsys.readouterr().err
        )
        std = _std()
        proj = resolve_project(
            std, load_config(user_config_file()), project_dir=str(path),
            initialize=False,
        )
        assert select_agent(std=std, proj=proj).node == "shell"

    @pytest.mark.parametrize(
        ("ref", "segment"),
        [
            ("shell+claude", "persona segment 'shell'"),
            ("nav+shell", "harness segment 'shell'"),
            ("default", "'default'"),
        ],
    )
    def test_a_CLAIM_on_a_pseudo_agent_name_is_still_refused(
        self, ref, segment, config_file, tmp_home, credentials_dir, capsys,
    ):
        """§2d's reservation is untouched: a persona cannot ride a pseudo-agent
        ([R178]), and ``default`` is the any-agent tier, not an agent to select."""
        path = tmp_home / "refused"
        path.mkdir()
        assert _kb("create", str(path), "--agent", ref) == 1
        err = capsys.readouterr().err
        assert "RESERVED pseudo-agent name" in err
        assert segment in err
        assert not (_std().boxes / "refused").exists()


class TestAgentVerbsAddressTheShellTier:
    """Every ``agent`` verb needs the agent's file to exist, for ``shell`` as for any
    agent; the first ``create`` is what materializes it, so each test runs one."""

    @pytest.fixture(autouse=True)
    def _a_shell_box(self, config_file, tmp_home, credentials_dir, capsys):
        path = tmp_home / "shellbox"
        path.mkdir()
        assert _kb("create", str(path), "--agent", "shell") == 0, (
            capsys.readouterr().err
        )
        capsys.readouterr()

    def test_set_then_get_round_trips_through_the_shell_agent_file(self, capsys):
        """``agent set shell`` writes ``agents/shell/agent.yaml`` — the file
        ``meta.agent.shell.settings`` declares — and ``agent get`` reads it back."""
        from kanibako.settings.agent_config import agent_settings_path

        assert _kb("agent", "set", "shell", "label=My Shell") == 0, (
            capsys.readouterr().err
        )
        capsys.readouterr()
        assert _kb("agent", "get", "shell", "label") == 0
        assert capsys.readouterr().out.strip() == "My Shell"

        path = agent_settings_path(_std().agents, "shell")
        assert path.parent.name == "shell"
        assert load_doc(path)["self"]["label"] == "My Shell"

    @pytest.mark.parametrize(("leaf", "given", "stored"), [
        ("model", "x", "x"),
        ("endpoint", "http://localhost:1", "http://localhost:1"),
        ("continue_mode", "false", "false"),
    ])
    def test_a_universal_leaf_the_shell_fence_lists_is_set(self, leaf, given, stored, capsys):
        """§2d's shell fence gives every universal key a value (``<None>`` for these
        three), so each is a key at ``agent.shell.*``: set, stored and read back."""
        from kanibako.settings.agent_config import agent_settings_path

        assert _kb("agent", "set", "shell", f"{leaf}={given}") == 0, (
            capsys.readouterr().err
        )
        capsys.readouterr()
        assert load_doc(agent_settings_path(_std().agents, "shell"))["self"][leaf] == stored
        assert _kb("agent", "get", "shell", leaf) == 0
        assert capsys.readouterr().out.strip().lower() == given

    def test_a_leaf_the_shell_fence_does_not_list_is_refused(self, capsys):
        """§2d's shell fence is COMPLETE: a leaf it does not list is not a key there."""
        from kanibako.settings.agent_config import agent_settings_path

        before = load_doc(agent_settings_path(_std().agents, "shell"))
        assert _kb("agent", "set", "shell", "frobnicate=x") == 1
        assert "frobnicate" in capsys.readouterr().err
        assert load_doc(agent_settings_path(_std().agents, "shell")) == before

    def test_show_effective_reads_the_shell_tier_floor(self, capsys):
        """Nothing set: the display verb reports the fence's own ``label``."""
        assert _kb("agent", "show", "shell", "--effective") == 0
        assert "label = Box Shell" in capsys.readouterr().out



class TestAgentVerbsStillRefuseAClaim:
    """CONTROLS, outside the class above so they need no shell box: they held before
    the fix and must hold after it — a composite naming ``shell`` is a claim ([R178])."""

    @pytest.mark.parametrize("ref", ["nav+shell", "shell+claude"])
    def test_a_composite_ref_naming_shell_is_still_refused(
        self, ref, config_file, tmp_home, capsys,
    ):
        assert _kb("agent", "set", ref, "label=x") == 1
        assert "RESERVED pseudo-agent name" in capsys.readouterr().err


class _FakeRuntime:
    """The one thing faked: a container runtime reporting a LIVE box stamped ``shell``.

    Everything else is real — the box on disk, the config, the target registry, the
    auth-source resolve and the writeback.  ``shell`` is what a launch stamps for a
    plain-shell box (``start._core_env_default_categories``: the box has a target,
    ``ShellTarget``, so it gets ``KANIBAKO_AGENT``).
    """

    def inspect_env(self, container_name, var):
        return "shell" if var == "KANIBAKO_AGENT" else None

    def is_running(self, container_name):
        return True

    def container_image(self, container_name):
        return "example/img:tag"


def _tree(root):
    return {
        p: p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


class TestAShellBoxStampIsReadBackAsTheShellAgent:
    """Every reader of a LIVE box's ``KANIBAKO_AGENT`` stamp reads ``shell`` as the
    shell pseudo-agent, through ``agent_ref.parse_agent_address``.

    Through the claimant grammar each of them raised the §2d reservation error on a
    plain-shell box: ``stop`` and ``code`` swallowed it, and the creds watcher
    logged it at ERROR and exited.  The box is made with ``system.agent: shell`` and
    a plain ``create``, a route that worked before the fix, so these pins isolate
    the READERS from ``create --agent shell``.
    """

    @pytest.fixture
    def shell_box(self, config_file, tmp_home, credentials_dir, capsys):
        from kanibako.settings.config import load_config, user_config_file
        from kanibako.settings.paths import resolve_box_target
        from kanibako.utils import container_name_for

        path = tmp_home / "shellbox"
        path.mkdir()
        assert _kb("system", "set", "system.agent=shell") == 0
        assert _kb("create", str(path)) == 0, capsys.readouterr().err
        capsys.readouterr()
        config = load_config(user_config_file())
        std = _std()
        proj = resolve_box_target(std, config, str(path), initialize=False)
        return std, config, proj, container_name_for(proj), path

    def test_stop_writeback_is_a_clean_no_op(self, shell_box, tmp_home):
        """The writeback RUNS for the ``ShellTarget`` and does nothing: a shell box
        resolves a BOX-tier (private) auth source, so no credential leaves it.

        INVERT: route the stamp through ``canonicalize_agent_ref`` and the spy is
        never reached — the reservation error dies in the blanket catch.
        """
        from unittest.mock import patch

        import kanibako.commands.start as start_mod
        from kanibako.commands.stop import _writeback_on_stop
        from kanibako.targets.shell import ShellTarget

        std, config, proj, cname, _ = shell_box
        real = start_mod.writeback_session_credentials
        seen = []

        def spy(target, proj, *, auth_src):
            seen.append((target, auth_src))
            return real(target, proj, auth_src=auth_src)

        before = _tree(tmp_home)
        with patch.object(start_mod, "writeback_session_credentials", spy):
            _writeback_on_stop(
                _FakeRuntime(), proj, cname, std=std, config=config, box_is_live=True,
            )
        assert len(seen) == 1
        target, auth_src = seen[0]
        assert isinstance(target, ShellTarget)
        assert auth_src.creds_shared is False
        assert _tree(tmp_home) == before

    def test_code_resolves_the_live_box_s_agent_as_shell(self, shell_box):
        """The node is the answer — not ``None`` from a swallowed error — and the
        shell target seeds no VS Code extension."""
        from kanibako.commands.code_cmd import (
            _resolve_box_agent_node, _resolve_box_vscode_extension,
        )

        std, _config, proj, cname, _ = shell_box
        node = _resolve_box_agent_node(_FakeRuntime(), std, proj, cname)
        assert node == "shell"
        assert _resolve_box_vscode_extension(node, proj) is None

    def test_code_remote_seed_asks_the_shell_target_for_its_extension(self, shell_box):
        """The remote leg reaches the extension lookup with the ``shell`` node, and
        seeds the workspace folder with no extension."""
        import json
        from unittest.mock import patch

        import kanibako.commands.code_cmd as code_cmd
        from kanibako.settings.paths import user_config_home
        from kanibako.vscode.vscode_config import attached_container_config_path

        _std_, _config, _proj, cname, _ = shell_box
        real = code_cmd._extension_for_agent
        asked = []

        def spy(agent_name, project_path):
            asked.append(agent_name)
            return real(agent_name, project_path)

        with patch.object(code_cmd, "_extension_for_agent", spy):
            code_cmd._seed_remote_attached_config(_FakeRuntime(), cname)
        assert asked == ["shell"]
        written = json.loads(attached_container_config_path(
            "example/img:tag", user_config_home(),
        ).read_text())
        assert "extensions" not in written

    def test_the_creds_watcher_resolves_the_box_and_exits_as_private(
        self, shell_box, caplog,
    ):
        """A launch never SPAWNS a watcher for a shell box (it spawns only for a
        shared-creds box, and a shell box's auth source is the box tier), but one run
        by hand must resolve the box and leave at its private-box arm, not log the
        reservation error."""
        import logging
        from unittest.mock import patch

        import kanibako.launch.creds_watcher as cw
        from kanibako.targets.shell import ShellTarget

        *_, path = shell_box
        with patch("kanibako.runtime.container.ContainerRuntime", _FakeRuntime):
            ctx = cw._resolve_watch_context(str(path))
            assert ctx is not None
            assert isinstance(ctx[3], ShellTarget)
            assert ctx[4].creds_shared is False
            with caplog.at_level(logging.INFO, logger="kanibako.creds_watcher"):
                assert cw.main(["--box", str(path)]) == 0
        assert "is private" in caplog.text
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
