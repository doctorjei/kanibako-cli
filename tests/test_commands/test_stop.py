"""Tests for kanibako.commands.stop."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.stop import run, _stop_one, _stop_all
from kanibako.settings.settings_launch import AuthSource

_SHARED_AUTH = AuthSource(
    tier="global",
    global_enabled=True,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
)


@pytest.fixture
def mock_runtime():
    rt = MagicMock()
    rt.stop.return_value = True
    rt.list_running.return_value = []
    rt.container_exists.return_value = False
    rt.rm.return_value = True
    # Default: no running container -> the on-stop writeback (FIX 1) is skipped.
    rt.is_running.return_value = False
    return rt


class TestStopOne:
    def test_running_container_stopped(self, mock_runtime, capsys):
        # A LIVE box: the only state for which "Stopped" is true.  The fixture
        # default is NOT running (it exists for the writeback guard), so a test
        # about stopping a running container must say so.
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = None  # no stamp -> no writeback
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.stop.assert_called_once()
            out = capsys.readouterr().out
            assert "Stopped" in out

    def test_no_running_container(self, mock_runtime, capsys):
        mock_runtime.stop.return_value = False
        mock_runtime.container_exists.return_value = False
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            proj.metadata_path = MagicMock()
            lock_path = MagicMock()
            lock_path.__str__ = lambda self: "/fake/path/.kanibako.lock"
            proj.metadata_path.__truediv__ = MagicMock(return_value=lock_path)
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            out = capsys.readouterr().out
            assert "No running container" in out
            assert "rm " in out
            assert ".kanibako.lock" in out

    def test_stop_removes_persistent_container(self, mock_runtime, capsys):
        """After stopping a running container, rm is called to clean up."""
        mock_runtime.is_running.return_value = True  # a live box, per the name
        mock_runtime.inspect_env.return_value = None  # no stamp -> no writeback
        mock_runtime.container_exists.return_value = True  # exists after stop
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.rm.assert_called_once()

    def test_stop_cleans_stale_persistent_container(self, mock_runtime, capsys):
        """A stopped persistent container (not running) is removed."""
        mock_runtime.stop.return_value = False  # not running
        mock_runtime.container_exists.return_value = True  # but exists (stopped)
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.rm.assert_called_once()
            out = capsys.readouterr().out
            assert "Removed stopped container" in out

    def test_orphan_container_is_not_reported_as_stopped(
        self, mock_runtime, capsys,
    ):
        """🛑 THE RC-0 LIE: ``podman stop`` succeeds on an ALREADY-EXITED container.

        A box whose PID 1 died without the CLI surviving to clean up — crash,
        killed terminal, reboot — leaves an ``Exited`` container squatting on
        the name, and the launch guard now names ``kanibako stop`` as the cure.
        ``runtime.stop`` returns the runtime's exit status, which is 0 here
        because there was nothing to stop, so this path printed ``Stopped
        <box>`` to a user who had been told one command earlier that the box
        was not running.

        The pin is the SENTENCE, not the routing: the ``rm`` is what clears the
        orphan and it must still happen, exactly as on the live arm.
        """
        mock_runtime.is_running.return_value = False   # the box is NOT live
        mock_runtime.stop.return_value = True          # ...and podman says 0
        mock_runtime.container_exists.return_value = True  # the orphan is there
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.name = "droste"
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            assert _stop_one(mock_runtime, project_dir=None) == 0

            # The action is unchanged: the orphan is removed.
            mock_runtime.rm.assert_called_once_with("kanibako-droste")
            # The sentence is the fix, and it is true.
            out = capsys.readouterr().out
            assert out == "Removed stopped container: kanibako-droste\n"

    def test_live_box_is_reported_as_stopped(self, mock_runtime, capsys):
        """The live arm's sentence is unchanged — the same box, actually running."""
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = None  # no stamp -> no writeback
        mock_runtime.stop.return_value = True
        mock_runtime.container_exists.return_value = True
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.name = "droste"
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            assert _stop_one(mock_runtime, project_dir=None) == 0

            mock_runtime.rm.assert_called_once_with("kanibako-droste")
            out = capsys.readouterr().out
            assert out == "Stopped kanibako-droste\n"

    def test_liveness_is_read_exactly_once(self, mock_runtime):
        """ONE ``is_running`` reading serves the writeback AND the sentence.

        Two readings taken across the stop could disagree — the box written
        back from and the box named in the message would be different facts.
        The hoist is the invariant, so the call count is the pin.
        """
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = None
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.name = "droste"
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            assert _stop_one(mock_runtime, project_dir=None) == 0
            mock_runtime.is_running.assert_called_once_with("kanibako-droste")

    def test_stop_with_project_dir(self, mock_runtime):
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
        ):
            proj = MagicMock()
            proj.project_hash = "abcdef1234567890" * 4
            m_resolve.return_value = proj

            _stop_one(mock_runtime, project_dir="/some/path")
            # resolve_any_project called with the given path (positional)
            call_args = m_resolve.call_args
            assert call_args[0][2] == "/some/path"
            assert call_args[1]["initialize"] is False


class TestStopAll:
    def test_stops_multiple_containers_with_force(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-aabbccdd", "img:latest", "Up 5 minutes"),
            ("kanibako-11223344", "img:latest", "Up 10 minutes"),
        ]
        rc = _stop_all(mock_runtime, force=True)
        assert rc == 0
        assert mock_runtime.stop.call_count == 2
        out = capsys.readouterr().out
        assert "Stopped 2 container(s)" in out

    def test_nothing_running(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = []
        rc = _stop_all(mock_runtime, force=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No running kanibako containers" in out
        mock_runtime.stop.assert_not_called()

    def test_partial_failure(self, mock_runtime, capsys):
        mock_runtime.list_running.return_value = [
            ("kanibako-aabbccdd", "img:latest", "Up 5 minutes"),
            ("kanibako-11223344", "img:latest", "Up 10 minutes"),
        ]
        mock_runtime.stop.side_effect = [True, False]
        rc = _stop_all(mock_runtime, force=True)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Stopped 1 container(s)" in out
        capsys.readouterr()  # drain stderr

    def test_confirmation_prompt_accepted(self, mock_runtime, capsys, monkeypatch):
        """--all without --force shows confirmation and proceeds on 'y'."""
        mock_runtime.list_running.return_value = [
            ("kanibako-proj1", "img:latest", "Up 5 minutes"),
        ]
        monkeypatch.setattr("builtins.input", lambda _: "y")
        rc = _stop_all(mock_runtime, force=False)
        assert rc == 0
        mock_runtime.stop.assert_called_once()
        out = capsys.readouterr().out
        assert "This will stop 1 running container(s)" in out

    def test_confirmation_prompt_rejected(self, mock_runtime, capsys, monkeypatch):
        """--all without --force aborts on 'n'."""
        mock_runtime.list_running.return_value = [
            ("kanibako-proj1", "img:latest", "Up 5 minutes"),
        ]
        monkeypatch.setattr("builtins.input", lambda _: "n")
        rc = _stop_all(mock_runtime, force=False)
        assert rc == 2
        mock_runtime.stop.assert_not_called()
        out = capsys.readouterr().out
        assert "Aborted" in out

    def test_confirmation_prompt_eof(self, mock_runtime, capsys, monkeypatch):
        """EOFError during confirmation aborts."""
        mock_runtime.list_running.return_value = [
            ("kanibako-proj1", "img:latest", "Up 5 minutes"),
        ]
        def raise_eof(_):
            raise EOFError
        monkeypatch.setattr("builtins.input", raise_eof)
        rc = _stop_all(mock_runtime, force=False)
        assert rc == 2


class TestRunDispatch:
    def test_dispatches_to_stop_all_with_force(self, capsys):
        with patch("kanibako.commands.stop.ContainerRuntime") as m_cls:
            rt = MagicMock()
            rt.list_running.return_value = []
            m_cls.return_value = rt
            import argparse
            args = argparse.Namespace(all_containers=True, project=None, force=True)
            rc = run(args)
            assert rc == 0
            rt.list_running.assert_called_once()

    def test_dispatches_to_stop_one(self):
        with (
            patch("kanibako.commands.stop.ContainerRuntime") as m_cls,
            patch("kanibako.commands.stop._stop_one", return_value=0) as m_stop_one,
        ):
            rt = MagicMock()
            m_cls.return_value = rt
            import argparse
            args = argparse.Namespace(all_containers=False, project=None, force=False)
            rc = run(args)
            assert rc == 0
            m_stop_one.assert_called_once_with(rt, project_dir=None)

    def test_dispatches_to_stop_one_with_project(self):
        with (
            patch("kanibako.commands.stop.ContainerRuntime") as m_cls,
            patch("kanibako.commands.stop._stop_one", return_value=0) as m_stop_one,
        ):
            rt = MagicMock()
            m_cls.return_value = rt
            import argparse
            args = argparse.Namespace(all_containers=False, project="myproj", force=False)
            rc = run(args)
            assert rc == 0
            m_stop_one.assert_called_once_with(rt, project_dir="myproj")

    def test_runtime_not_found(self, capsys):
        from kanibako.errors import ContainerError
        with patch("kanibako.commands.stop.ContainerRuntime", side_effect=ContainerError("No runtime")):
            import argparse
            args = argparse.Namespace(all_containers=False, project=None, force=False)
            rc = run(args)
            assert rc == 1
            err = capsys.readouterr().err
            assert "No container runtime found" in err


class TestStopWriteback:
    """FIX 1: `kanibako stop` writes the box's creds back to the host first."""

    def test_writeback_runs_for_running_agent_box(self, mock_runtime, capsys):
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = "claude"
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch("kanibako.targets.resolve_target") as m_resolve_target,
            patch(
                "kanibako.commands.start._resolve_box_auth_source",
                return_value=_SHARED_AUTH,
            ),
            patch(
                "kanibako.commands.start.writeback_session_credentials"
            ) as m_wb,
        ):
            proj = MagicMock()
            m_resolve.return_value = proj
            target = MagicMock()
            m_resolve_target.return_value = target

            rc = _stop_one(mock_runtime, project_dir=None)
            assert rc == 0
            mock_runtime.inspect_env.assert_called_once()
            # Block #2: writeback gets the resolved AuthSource (the 3-tier
            # SHARING source), passed as the ``auth_src`` keyword.
            m_wb.assert_called_once_with(
                target, proj, auth_src=_SHARED_AUTH
            )

    @pytest.mark.parametrize("stamp", ["navigator+claude", "navigator℘claude"])
    def test_persona_writeback_canonicalizes_the_stamp_before_deriving(
        self, mock_runtime, stamp,
    ):
        """🛑 THE SILENT-FAILURE GUARD, and the reason canonicalize-on-read exists.

        ``KANIBAKO_AGENT`` is stamped in the OUTSIDE spelling (``+``) because an
        agent reads it inside the box.  ``harness_of`` splits on ``℘`` ALONE, so
        deriving the harness from the RAW ``+`` stamp yields the whole string
        ``navigator+claude``; ``resolve_target`` then raises ``KeyError`` — INSIDE
        the blanket ``except Exception: pass`` of ``_writeback_on_stop``.  Nothing
        is printed, nothing is logged, ``stop`` still exits 0, and the box's
        credentials simply never reach the host.  A bare agent cannot show any of
        this (node == harness ⇒ one string), so only a persona pins it.

        ⚑ BOTH SPELLINGS ARE PARAMETRIZED ON PURPOSE — that is the BACK-COMPAT
        proof: ``+`` is what this version stamps, ``℘`` is what a box already
        running under an older version carries, and both must behave identically.

        Three things are asserted, because the bug can hide in any of them: the
        plugin is looked up by the HARNESS, the two settings-side values get the
        CANONICAL node, and the writeback actually RAN.
        """
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = stamp
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch("kanibako.targets.resolve_target") as m_resolve_target,
            patch(
                "kanibako.commands.start._resolve_box_auth_source",
                return_value=_SHARED_AUTH,
            ) as m_auth,
            patch(
                "kanibako.commands.start.writeback_session_credentials"
            ) as m_wb,
        ):
            proj = MagicMock()
            m_resolve.return_value = proj
            target = MagicMock()
            m_resolve_target.return_value = target

            assert _stop_one(mock_runtime, project_dir=None) == 0

            # (1) The plugin is keyed by the HARNESS — literal, not derived.
            assert m_resolve_target.call_args.args[0] == "claude"
            # (2) The settings-side values discriminate the agent TIER, so they
            #     take the CANONICAL node — both the cascade discriminator and the
            #     §1A selection level.
            kwargs = m_auth.call_args.kwargs
            assert kwargs["agent_name"] == "navigator℘claude"
            assert kwargs["selection_level"] == {
                "system.agent": "navigator℘claude"
            }
            # (3) …and the writeback actually ran, which is the whole point: the
            #     failure this guards against is SILENT, so absence is the symptom.
            m_wb.assert_called_once_with(target, proj, auth_src=_SHARED_AUTH)

    def _stop_with_failing_auth_resolve(self, mock_runtime, exc, *, stamp="claude"):
        """Stop a LIVE box stamped *stamp* whose auth resolve raises *exc*.

        Returns ``(rc, writeback mock)``.
        """
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = stamp
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch("kanibako.targets.resolve_target"),
            patch(
                "kanibako.commands.start._resolve_box_auth_source",
                side_effect=exc,
            ),
            patch(
                "kanibako.commands.start.writeback_session_credentials"
            ) as m_wb,
        ):
            m_resolve.return_value = MagicMock()
            return _stop_one(mock_runtime, project_dir=None), m_wb

    @pytest.mark.parametrize("error", ["SettingsError", "ConfigError"])
    def test_settings_refusal_is_said_and_the_stop_still_happens(
        self, mock_runtime, capsys, error,
    ):
        """🛑 A settings file that turned invalid after the box started refuses the auth
        resolve; the stop must SAY the credential writeback was skipped, carrying the
        refusal's own text (which names the file), and still stop the box.
        Swallowing it left the credentials un-written-back with no word said.

        Both refusal types: ``SettingsError`` (an undeclared key, a stray in
        ``agent.yaml``) and ``ConfigError`` (a file that is not valid YAML)."""
        from kanibako.errors import ConfigError
        from kanibako.settings.settings_resolve import SettingsError

        refusal = {
            "SettingsError": (
                SettingsError,
                "`model` at the top level of /cfg/agents/claude/agent.yaml is not a "
                "settings key, so kanibako will not read the file.",
            ),
            "ConfigError": (
                ConfigError,
                "the config file /cfg/agents/claude/agent.yaml is not valid YAML: "
                "line 2. Fix or remove the file, then retry.",
            ),
        }
        exc_type, text = refusal[error]
        rc, m_wb = self._stop_with_failing_auth_resolve(mock_runtime, exc_type(text))
        assert rc == 0
        mock_runtime.stop.assert_called_once()
        m_wb.assert_not_called()
        err = capsys.readouterr().err
        assert "credential writeback skipped" in err
        assert text in err

    def test_other_writeback_failure_stays_silent(self, mock_runtime, capsys):
        """Every failure that is NOT a settings refusal keeps the old best-effort
        silence — the stop is unaffected and nothing is printed."""
        rc, m_wb = self._stop_with_failing_auth_resolve(
            mock_runtime, RuntimeError("not a settings refusal"),
        )
        assert rc == 0
        mock_runtime.stop.assert_called_once()
        m_wb.assert_not_called()
        assert capsys.readouterr().err == ""

    def test_malformed_stamp_is_not_reported_as_a_settings_refusal(
        self, mock_runtime, capsys,
    ):
        """A malformed ``KANIBAKO_AGENT`` stamp makes ``agent_address_node`` raise
        ``ConfigError`` BEFORE the auth resolve. That is not the box's settings, so the
        report stays scoped to the resolve call: the stop is silent, as before."""
        rc, m_wb = self._stop_with_failing_auth_resolve(
            mock_runtime, AssertionError("the auth resolve must not be reached"),
            stamp="bad+",
        )
        assert rc == 0
        mock_runtime.stop.assert_called_once()
        m_wb.assert_not_called()
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("stamp", [None, ""])
    def test_no_writeback_without_agent_stamp(self, mock_runtime, stamp):
        """A box with NO agent — a ``pref.system.agent: null`` box (D-M6) or a
        pre-stamp box — writes nothing back.

        ⚑ This is also what keeps the P7 credential-path fix safe on the writeback
        side: the auth resolve is fed ``{"system.agent": <stamp>}``, so an EMPTY
        stamp would rebuild the collapsed ``<auth>/`` source. It never gets there —
        the guard returns BEFORE the auth resolve. Both falsy shapes are pinned
        because ``inspect_env`` can yield either.
        """
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = stamp  # unstamped / no-agent box
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch(
                "kanibako.commands.start._resolve_box_auth_source"
            ) as m_auth,
            patch(
                "kanibako.commands.start.writeback_session_credentials"
            ) as m_wb,
        ):
            m_resolve.return_value = MagicMock()
            _stop_one(mock_runtime, project_dir=None)
            m_wb.assert_not_called()
            m_auth.assert_not_called()

    def test_no_writeback_when_not_running(self, mock_runtime):
        mock_runtime.is_running.return_value = False
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch(
                "kanibako.commands.start.writeback_session_credentials"
            ) as m_wb,
        ):
            m_resolve.return_value = MagicMock()
            _stop_one(mock_runtime, project_dir=None)
            m_wb.assert_not_called()


class TestStopWithMalformedBoxSettings:
    """Q137a: a malformed ``box.yaml`` must not lock the user out of their own box.

    🛑 THE LOCKOUT.  ``resolve_box_target``'s advisory pass reads the box's
    ``box.enable_vault`` through the whole settings cascade, so a ``box.yaml``
    that is not a mapping raised ``ConfigError`` out of ``stop`` — a box the user
    is inside could then only be stopped with ``podman stop``.

    Nothing that NAMES the container is on that read: the name is the box's
    IDENTITY (``kanibako.utils.container_name_for`` — mode, metadata root,
    registry name, project hash).  So these pin the three behaviors together: the
    stop HAPPENS, under the name identity gives it, and the refusal is SAID with
    the refusal's own text.

    ⚑ THE FRONT DOOR IS ``run()`` — the argparse entry — with the container
    runtime stubbed, so nothing here needs podman.  Everything else is REAL: a
    real box on disk in an isolated ``HOME``, a real ``box.yaml`` (corrupted in
    place by the parametrization), and the real resolve chain beneath.
    """

    #: ``(label, file body)`` — the three shapes ``config_io.load_doc`` refuses.
    _MALFORMED = [
        ("invalid-yaml", "box: [unclosed\n  bad: :\n"),
        ("a-list", "- one\n- two\n"),
        ("a-scalar", "just a string\n"),
    ]

    @pytest.fixture
    def live_runtime(self):
        """A LIVE box whose stop succeeds, with no agent stamp (no writeback)."""
        rt = MagicMock()
        rt.stop.return_value = True
        rt.is_running.return_value = True
        rt.inspect_env.return_value = None
        rt.container_exists.return_value = False
        rt.rm.return_value = True
        return rt

    @staticmethod
    def _drive(live_runtime, target):
        """``kanibako stop <target>`` through the command's own entry point."""
        import argparse

        args = argparse.Namespace(
            all_containers=False, project=str(target), force=False,
        )
        with patch("kanibako.commands.stop.ContainerRuntime", return_value=live_runtime):
            return run(args)

    @staticmethod
    def _primary_box(std, config, tmp_home):
        """A real PRIMARY box + its ``box.yaml``; return ``(workspace, name, file)``."""
        from kanibako.settings.paths import resolve_project

        workspace = tmp_home / "work" / "myapp"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, project_dir=str(workspace), initialize=True)
        box_yaml = proj.metadata_path / "box.yaml"
        box_yaml.parent.mkdir(parents=True, exist_ok=True)
        box_yaml.write_text("box:\n  enable_vault: false\n")
        return workspace, proj.name, box_yaml

    @staticmethod
    def _standalone_box(std, tmp_home):
        """A real STANDALONE box + its ``box.yaml``; return ``(root, name, file)``."""
        from kanibako.settings.paths import establish_standalone

        root = tmp_home / "sa"
        (root / "box_data").mkdir(parents=True)
        name, *_ = establish_standalone(std, root, enable_vault=True, name="sa")
        box_yaml = root / "box_data" / "box.yaml"
        box_yaml.write_text("box:\n  enable_vault: false\n")
        return root, name, box_yaml

    @pytest.mark.parametrize("shape", _MALFORMED, ids=[s[0] for s in _MALFORMED])
    def test_primary_box_stops_with_a_malformed_box_yaml(
        self, std, config, tmp_home, live_runtime, capsys, shape,
    ):
        """A PRIMARY box: stopped, under its identity name, with the refusal said."""
        _label, text = shape
        target, name, box_yaml = self._primary_box(std, config, tmp_home)
        box_yaml.write_text(text)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        assert self._drive(live_runtime, target) == 0

        # The stop HAPPENED, on the name identity gives it — the box name, and
        # nothing the unreadable file could have said about it.
        live_runtime.stop.assert_called_once_with(f"kanibako-{name}")
        err = capsys.readouterr().err
        assert err.startswith("Warning: ")
        # …carrying the REFUSAL'S OWN TEXT, which names the file and the problem
        # (P10: one wording, not a second one written for the warning).
        assert str(box_yaml) in err
        assert "Fix or remove the file, then retry." in err

    @pytest.mark.parametrize("shape", _MALFORMED, ids=[s[0] for s in _MALFORMED])
    def test_standalone_box_stops_with_a_malformed_box_yaml(
        self, std, tmp_home, live_runtime, capsys, shape,
    ):
        """A STANDALONE box: its container is keyed by its ROOT — identity too."""
        from kanibako.utils import container_name_for_standalone_root

        _label, text = shape
        root, _name, box_yaml = self._standalone_box(std, tmp_home)
        box_yaml.write_text(text)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        assert self._drive(live_runtime, root) == 0

        live_runtime.stop.assert_called_once_with(
            container_name_for_standalone_root(root.resolve())
        )
        err = capsys.readouterr().err
        assert err.startswith("Warning: ")
        assert str(box_yaml) in err
        assert "Fix or remove the file, then retry." in err

    def test_the_refusal_is_said_once_when_the_writeback_hits_it_too(
        self, std, config, tmp_home, live_runtime, capsys,
    ):
        """🛑 ONE REFUSAL, ONE SENTENCE — even when the writeback hits the same file.

        A live, agent-stamped box runs the on-stop credential writeback, and that
        auth resolve reads the SAME malformed ``box.yaml``.  Both renders are
        legitimate (the stop degraded; the writeback was skipped), but printing
        the refusal's sentence twice is the duplicate-renderer defect this
        project has already been dinged for — so the second one keeps its
        "credential writeback skipped" fact and drops the repeated text.
        """
        live_runtime.inspect_env.return_value = "claude"  # stamped -> writeback runs
        _label, text = self._MALFORMED[1]
        target, _name, box_yaml = self._primary_box(std, config, tmp_home)
        box_yaml.write_text(text)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        assert self._drive(live_runtime, target) == 0

        err = capsys.readouterr().err
        sentence = "Fix or remove the file, then retry."
        assert err.count(sentence) == 1, err
        # Both facts survive: the refusal, and the skipped writeback.
        assert "Warning: the config file" in err
        assert "credential writeback skipped" in err

    def test_a_writeback_refusal_about_another_file_is_still_said_whole(
        self, mock_runtime, capsys,
    ):
        """The dedup is by TEXT, so a DIFFERENT file's refusal is never swallowed.

        The box's ``box.yaml`` is fine here and only ``agent.yaml`` is malformed —
        the writeback's warning must still carry that file's own refusal sentence,
        or the user would never learn which file to fix.
        """
        from kanibako.errors import ConfigError

        refusal = ("the config file /cfg/agents/claude/agent.yaml is not valid "
                   "YAML: line 2. Fix or remove the file, then retry.")
        mock_runtime.is_running.return_value = True
        mock_runtime.inspect_env.return_value = "claude"
        with (
            patch("kanibako.commands.stop.load_config"),
            patch("kanibako.commands.stop.load_std_paths"),
            patch("kanibako.commands.stop.resolve_box_target") as m_resolve,
            patch("kanibako.targets.resolve_target"),
            patch(
                "kanibako.commands.start._resolve_box_auth_source",
                side_effect=ConfigError(refusal),
            ),
        ):
            m_resolve.return_value = MagicMock()
            assert _stop_one(mock_runtime, project_dir=None) == 0

        err = capsys.readouterr().err
        assert "credential writeback skipped" in err
        assert refusal in err

    def test_the_stop_is_still_reported(self, std, config, tmp_home, live_runtime, capsys):
        """The action is complete: rc 0 AND the box is named as stopped.

        The ruling is "proceeds and WARNS", so a warning that arrives with a
        refusal — or a stop that happens without being reported — is the defect
        this pins shut.
        """
        _label, text = self._MALFORMED[0]
        target, name, box_yaml = self._primary_box(std, config, tmp_home)
        box_yaml.write_text(text)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        assert self._drive(live_runtime, target) == 0

        assert f"Stopped kanibako-{name}" in capsys.readouterr().out

    def test_a_valid_box_yaml_still_stops_without_a_warning(
        self, std, config, tmp_home, live_runtime, capsys,
    ):
        """The control: the healthy path is silent, and resolves EXACTLY once.

        The degraded arm is a SECOND resolve, so the guard against "always retry
        and always warn" is the call count: one resolve, no warning.
        """
        import kanibako.settings.paths as paths

        target, name, _box_yaml = self._primary_box(std, config, tmp_home)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        with patch(
            "kanibako.commands.stop.resolve_box_target",
            wraps=paths.resolve_box_target,
        ) as m_resolve:
            assert self._drive(live_runtime, target) == 0

        assert m_resolve.call_count == 1
        assert capsys.readouterr().err == ""
        live_runtime.stop.assert_called_once_with(f"kanibako-{name}")



class TestStopWithMalformedGlobalSettings:
    """The GLOBAL settings tier degrades the same way the box tier does.

    ⚑ TWO TIERS, ONE CONTRACT.  ``box.yaml`` and ``<data>/global/settings.yaml``
    both reach ``stop`` through :func:`load_std_paths` and
    :func:`resolve_box_target`, and a file that is not a mapping of keys raises
    ``ConfigError`` out of the settings read.  Neither file names the container:
    the name is the box's identity (mode, metadata root, registry name, project
    hash), and identity resolves without a settings file.

    ⚑ THE FRONT DOOR IS ``run()`` — the argparse entry — with the container
    runtime stubbed, so nothing here needs podman.  The box, the settings file
    and the whole resolve chain are real.
    """

    _MALFORMED = [
        ("invalid-yaml", "system: [unclosed\n  bad: :\n"),
        ("a-list", "- one\n- two\n"),
    ]

    @pytest.fixture
    def live_runtime(self):
        rt = MagicMock()
        rt.stop.return_value = True
        rt.is_running.return_value = True
        rt.inspect_env.return_value = None
        rt.container_exists.return_value = False
        rt.rm.return_value = True
        return rt

    @staticmethod
    def _primary_box(std, config, tmp_home):
        """A real PRIMARY box; return ``(workspace, name)``."""
        from kanibako.settings.paths import resolve_project

        workspace = tmp_home / "work" / "myapp"
        workspace.mkdir(parents=True)
        proj = resolve_project(std, config, project_dir=str(workspace), initialize=True)
        return workspace, proj.name

    @staticmethod
    def _drive(live_runtime, target):
        import argparse

        args = argparse.Namespace(
            all_containers=False, project=str(target), force=False,
        )
        with patch("kanibako.commands.stop.ContainerRuntime", return_value=live_runtime):
            return run(args)

    @pytest.mark.parametrize("shape", _MALFORMED, ids=[s[0] for s in _MALFORMED])
    def test_stops_with_a_malformed_global_settings_file(
        self, std, config, tmp_home, live_runtime, capsys, shape,
    ):
        """Stopped under the identity name, with the refusal said as a ``Warning:``."""
        _label, text = shape
        workspace, name = self._primary_box(std, config, tmp_home)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text(text)
        capsys.readouterr()  # drain the box creation's one-time-setup notice

        assert self._drive(live_runtime, workspace) == 0

        # The stop HAPPENED, on the name identity gives it.
        live_runtime.stop.assert_called_once_with(f"kanibako-{name}")
        err = capsys.readouterr().err
        assert err.startswith("Warning: "), err
        # …carrying the REFUSAL'S OWN TEXT (P10: one wording, not a second one).
        assert str(std.settings) in err
        assert "Fix or remove the file, then retry." in err

    def test_the_refusal_is_said_exactly_once(
        self, std, config, tmp_home, live_runtime, capsys,
    ):
        """One refusal, one sentence, one place."""
        _label, text = self._MALFORMED[1]
        workspace, _name = self._primary_box(std, config, tmp_home)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text(text)
        capsys.readouterr()

        assert self._drive(live_runtime, workspace) == 0
        err = capsys.readouterr().err
        assert err.count("Fix or remove the file, then retry.") == 1, err
        assert "Error: " not in err, err


class TestStopRefusalIsNotPrecededByADeceptiveWarning:
    """A refusal the degraded resolve cannot route around is reported once.

    The degraded resolve drops the ADVISORY settings reads only.  A file the
    identity resolve itself needs — the registry carrying the registered name —
    still refuses, and then the command must say exactly what ``main`` says: the
    ``Error:`` line, rc 1, and no ``Warning:`` announcing a stop that never
    happened.
    """

    @pytest.fixture
    def live_runtime(self):
        rt = MagicMock()
        rt.stop.return_value = True
        rt.is_running.return_value = True
        rt.inspect_env.return_value = None
        rt.container_exists.return_value = False
        rt.rm.return_value = True
        return rt

    def test_a_malformed_registry_is_one_error_line_and_rc_1(
        self, std, config, tmp_home, live_runtime, capsys,
    ):
        from kanibako.cli import main
        from kanibako.settings.paths import resolve_project

        workspace = tmp_home / "work" / "myapp"
        workspace.mkdir(parents=True)
        resolve_project(std, config, project_dir=str(workspace), initialize=True)
        std.registry.parent.mkdir(parents=True, exist_ok=True)
        std.registry.write_text("- one\n- two\n")
        capsys.readouterr()

        with (
            patch("kanibako.commands.stop.ContainerRuntime", return_value=live_runtime),
            pytest.raises(SystemExit) as excinfo,
        ):
            main(["stop", workspace.name])

        assert excinfo.value.code == 1
        err = capsys.readouterr().err
        # The warning the stop did NOT earn must be absent.
        assert "Warning: " not in err, err
        # …and the refusal is said exactly once, on the Error: line.
        assert err.startswith("Error: "), err
        assert err.count("Fix or remove the file, then retry.") == 1, err
        live_runtime.stop.assert_not_called()
