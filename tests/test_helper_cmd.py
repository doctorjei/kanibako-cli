"""Tests for kanibako helper CLI commands."""

from __future__ import annotations

import shutil
import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.helper_cmd import (
    _format_log_entry,
    _get_existing_helpers,
    run_broadcast,
    run_cleanup,
    run_list,
    run_log,
    run_register,
    run_respawn,
    run_send,
    run_spawn,
    run_stop,
)
import kanibako.commands.helper_cmd as helper_cmd
from kanibako.channels.helper_client import send_request as _REAL_SEND_REQUEST
from kanibako.channels.helpers import (
    SpawnBudget,
    read_helper_state,
    write_helper_number,
    write_helper_state,
    write_spawn_budget,
)
from tests.support.filenames import CONFIG_FILENAME


@pytest.fixture
def helpers_env(tmp_path, monkeypatch):
    """Set up a temporary helpers environment."""
    home = tmp_path / "home"
    home.mkdir()
    (home / "canon" / "notebook" / "scripts").mkdir(parents=True)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))

    # ⚑ THE TWO XDG REDIRECTS ARE THE LOAD-BEARING HALF, not the file — and the
    # ``Path.home`` patch above does NOT subsume them.  ``run_spawn`` reads its own
    # ``system.helpers.*`` tier from ``config.system_settings_path()``, which resolves
    # ``{config.settings}`` off ``$XDG_CONFIG_HOME`` (the Layer-1 file) and
    # ``$XDG_DATA_HOME`` (the store the settings file sits in), and
    # ``settings.paths.resolve_xdg`` honors the ENV VAR OVER ``Path.home()``: it falls
    # back to ``Path.home() / …`` only when the var is UNSET or set to a RELATIVE value
    # (which it warns about and ignores, per the XDG spec).  Unset — this box, and CI —
    # those fallbacks do land in the fake home, so dropping either line still passes here.
    # The developer who EXPORTS them is the one who leaks a REAL host budget into what
    # these tests assert on.  Test it with the vars exported at a store carrying NO
    # ``system.helpers.*`` — exporting alone proves nothing, since an absent file reads
    # back the same ``None`` — or the test cannot see what the lines are for.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / CONFIG_FILENAME
    # EMPTY, exactly as ``settings.config.write_global_config`` writes it
    # (``atomic_write_text(path, "")``, called by ``cli._ensure_initialized`` on first run
    # when the file is absent) — an initialized host that overrides nothing.
    config_file.write_text("")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    # A LIVE HUB by default: a spawn with no hub refuses (see ``TestNoHub``), so the
    # box these tests model has a LISTENING socket at the pinned path, and the hub
    # answers every launch "ok".  The socket is bound in a short ``/tmp`` dir and
    # linked in, because an AF_UNIX path is capped near 108 bytes and ``tmp_path`` is
    # not.  Nobody accepts on it: a connect still succeeds into the backlog, and that
    # is all the CLI's liveness probe asks.
    sock = home / ".kanibako" / "state" / "hub" / "helper.sock"
    sock.parent.mkdir(parents=True)
    listener, real = _listening_socket()
    sock.symlink_to(real)

    def _hub(_path, request):
        if request.get("action") == "spawn":
            return {"status": "ok", "container_name": f"kb-t-helper-{request['helper_num']}"}
        return {"status": "ok"}

    monkeypatch.setattr("kanibako.channels.helper_client.send_request", _numbering(_hub))
    yield home
    listener.close()
    shutil.rmtree(real.parent, ignore_errors=True)


def _numbering(reply):
    """A hub stub that hands out helper numbers from 1, as the hub's counter does, and
    answers any other request with *reply*: a callable is called with the request, an
    exception is raised, and anything else is returned."""
    numbers = iter(range(1, 1_000_000))

    def _send(path, request):
        if request.get("action") == "number":
            return {"status": "ok", "helper_num": next(numbers)}
        if isinstance(reply, BaseException):
            raise reply
        if callable(reply):
            return reply(path, request)
        return reply

    return _send


def _launches(m) -> list[dict]:
    """The launch requests a patched ``send_request`` saw (its number requests left out)."""
    return [c[0][1] for c in m.call_args_list if c[0][1].get("action") != "number"]


def _listening_socket() -> tuple[socket.socket, Path]:
    """A listening AF_UNIX socket in a fresh short ``/tmp`` dir, and its path."""
    real = Path(tempfile.mkdtemp(prefix="kbh-", dir="/tmp")) / "s"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(real))
    listener.listen(8)
    return listener, real


def _no_hub(home: Path) -> None:
    """Take the hub away: the box a persistent start leaves (no socket)."""
    (home / ".kanibako" / "state" / "hub" / "helper.sock").unlink()


class TestGetExistingHelpers:
    def test_empty_dir(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        assert _get_existing_helpers(helpers) == []

    def test_numeric_dirs(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "1").mkdir()
        (helpers / "3").mkdir()
        (helpers / "2").mkdir()
        assert _get_existing_helpers(helpers) == [1, 2, 3]

    def test_ignores_non_numeric(self, tmp_path):
        helpers = tmp_path / "helpers"
        helpers.mkdir()
        (helpers / "1").mkdir()
        (helpers / "all").mkdir()
        (helpers / "channels").mkdir()
        assert _get_existing_helpers(helpers) == [1]

    def test_nonexistent_dir(self, tmp_path):
        assert _get_existing_helpers(tmp_path / "nope") == []


class TestRunSpawn:
    def test_basic_spawn(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0

        out = capsys.readouterr().out
        assert "Spawned helper 1" in out

        # Directory structure created
        helpers = helpers_env / "helpers"
        assert (helpers / "1" / "vault" / "ro").is_dir()
        assert (helpers / "1" / "vault" / "rw").is_dir()
        assert (helpers / "1" / "workspace").is_dir()
        # No peer links: helpers talk only through the hub (``box helper send``)
        assert not (helpers / "1" / "peers").exists()

        # RO spawn config written
        ro_config = helpers / "1" / "spawn.yaml"
        assert ro_config.is_file()

        # Broadcast dirs created
        assert (helpers / "all" / "rw").is_dir()
        assert (helpers / "all" / "ro").is_dir()

    def test_spawn_multiple(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        run_spawn(args)

        helpers = helpers_env / "helpers"
        assert (helpers / "1").is_dir()
        assert (helpers / "2").is_dir()

        # No peer channels between siblings, in either helper or under helpers/
        assert not (helpers / "1" / "peers").exists()
        assert not (helpers / "2" / "peers").exists()
        assert not (helpers / "channels").exists()

    def test_depth_zero_refused(self, helpers_env, capsys):
        # A parent handed this box depth=0
        own_ro = helpers_env / "spawn.yaml"
        write_spawn_budget(own_ro, SpawnBudget(depth=0, breadth=4))

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 1
        assert "depth" in capsys.readouterr().err

    def test_breadth_exhausted(self, helpers_env, capsys):
        # A parent handed this box breadth=1
        own_ro = helpers_env / "spawn.yaml"
        write_spawn_budget(own_ro, SpawnBudget(depth=4, breadth=1))

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0  # first spawn ok

        rc = run_spawn(args)
        assert rc == 1  # second spawn refused
        assert "breadth" in capsys.readouterr().err

    def test_own_budget_is_read_from_the_system_settings_file(self, helpers_env, capsys):
        """This box's own tier IS ``{config.settings}``, the declared system tier.

        ⚑ This pins WHERE the caller looks: ``resolve_spawn_budget`` takes budgets,
        so its own tests cannot catch the caller reading a retired place.
        """
        from kanibako.settings.config import system_settings_path

        settings_file = system_settings_path()
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        write_spawn_budget(settings_file, SpawnBudget(depth=4, breadth=1))

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0  # first spawn ok

        rc = run_spawn(args)
        assert rc == 1  # second spawn refused
        assert "breadth" in capsys.readouterr().err

    def test_the_retired_bespoke_file_is_not_a_budget(self, helpers_env):
        """The breach, stated as a negative: ``<config home>/kanibako/spawn.yaml``.

        That dedicated file held an UNDECLARED ``spawn:`` table — a §0 violation — and
        it is gone. Without this case the declaration is indistinguishable from an ADD
        that left the old read in place: the declared tier would work AND the bespoke
        file would keep working, which is the breach silently not closing.
        """
        from kanibako.settings.bootstrap import KANIBAKO_PATH
        from kanibako.settings.paths import xdg

        retired = xdg("XDG_CONFIG_HOME", ".config") / KANIBAKO_PATH / "spawn.yaml"
        retired.parent.mkdir(parents=True, exist_ok=True)
        retired.write_text("spawn:\n  depth: 0\n  breadth: 0\n")

        args = _make_args(depth=None, breadth=None, model=None)
        rc = run_spawn(args)
        assert rc == 0  # the depth-0 budget is ignored; built-in defaults apply

    def test_spawn_section_in_layer1_file_is_REFUSED(self, helpers_env):
        """A ``spawn:`` table in ``kanibako.cfg`` is refused BY NAME ([R158]).

        ⚑ STRENGTHENED 2026-09-19 with the declaration.  It used to assert rc=0 — the
        table bought nothing because ``run_spawn`` never opened the Layer-1 file at all.
        Reading the budget from ``{config.settings}`` means the spawn verb now loads that
        file like every other verb, so [R158]'s refusal reaches it: *"a user running a
        different image than their file says should learn it"*.  Silently ignoring was
        the weaker half of that rule, kept only by an accident of which files were read.
        """
        from kanibako.errors import ConfigError
        from kanibako.settings.paths import xdg

        config_home = xdg("XDG_CONFIG_HOME", ".config")
        (config_home / CONFIG_FILENAME).write_text("spawn:\n  depth: 0\n")

        args = _make_args(depth=None, breadth=None, model=None)
        with pytest.raises(ConfigError, match="carries settings"):
            run_spawn(args)

    def test_model_shown_in_output(self, helpers_env, capsys):
        args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(args)
        assert "model: sonnet" in capsys.readouterr().out

    def test_child_gets_decremented_depth(self, helpers_env):
        args = _make_args(depth=3, breadth=4, model=None)
        run_spawn(args)

        from kanibako.channels.helpers import read_spawn_budget
        child_config = read_spawn_budget(
            helpers_env / "helpers" / "1" / "spawn.yaml"
        )
        assert child_config is not None
        assert child_config.depth == 2
        assert child_config.breadth == 4

    def test_init_script_copied(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        init = helpers_env / "helpers" / "1" / "scripts" / "helper-init.sh"
        assert init.is_file()
        assert "#!/usr/bin/env bash" in init.read_text()
        # The vestigial ``playbook/`` wrapper is gone from the helper root entirely.
        assert not (helpers_env / "helpers" / "1" / "playbook").exists()

    def test_parent_override_is_read_from_the_canon_notebook(self, helpers_env):
        """A parent's own helper-init.sh wins, and it lives at the canon address.

        ⚑ This pins WHERE the parent is asked.  ``resolve_init_script`` takes the
        directory as an argument, so its own tests cannot catch the caller looking in
        a retired place — only this one can.
        """
        custom = Path.home() / "canon" / "notebook" / "scripts" / "helper-init.sh"
        custom.write_text("#!/usr/bin/env bash\n# parent's own\n")
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        init = helpers_env / "helpers" / "1" / "scripts" / "helper-init.sh"
        assert "# parent's own" in init.read_text()


class TestHelperState:
    def test_spawn_writes_state(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(args)
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"
        assert state["model"] == "sonnet"
        assert state["depth"] == 3  # default 4, decremented to 3

    def test_spawn_no_model(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["model"] is None

    def test_state_records_no_peers(self, helpers_env):
        args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(args)
        run_spawn(args)
        state2 = read_helper_state(helpers_env / "helpers", 2)
        assert "peers" not in state2


class TestRunList:
    def test_no_helpers(self, helpers_env, capsys):
        args = _make_args()
        rc = run_list(args)
        assert rc == 0
        assert "No helpers" in capsys.readouterr().out

    def test_lists_spawned_helpers(self, helpers_env, capsys):
        spawn_args = _make_args(depth=None, breadth=None, model=None)
        run_spawn(spawn_args)
        run_spawn(spawn_args)

        capsys.readouterr()  # clear spawn output
        args = _make_args()
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "1" in out
        assert "2" in out
        assert "running" in out

    def test_list_shows_model(self, helpers_env, capsys):
        spawn_args = _make_args(depth=None, breadth=None, model="sonnet")
        run_spawn(spawn_args)

        capsys.readouterr()
        args = _make_args()
        run_list(args)
        out = capsys.readouterr().out
        assert "sonnet" in out

    def test_list_shows_depth(self, helpers_env, capsys):
        spawn_args = _make_args(depth=2, breadth=4, model=None)
        run_spawn(spawn_args)

        capsys.readouterr()
        args = _make_args()
        run_list(args)
        out = capsys.readouterr().out
        # Child depth is parent depth - 1 = 1
        assert "1" in out


class TestRunStop:
    def test_stop_running_helper(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        rc = run_stop(_make_args(number=1))
        assert rc == 0
        assert "Stopped helper 1" in capsys.readouterr().out

        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "stopped"

    def test_stop_already_stopped(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        rc = run_stop(_make_args(number=1))
        assert rc == 0
        assert "already stopped" in capsys.readouterr().out

    def test_stop_nonexistent(self, helpers_env, capsys):
        rc = run_stop(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err


def _hub_replies_to_stop(monkeypatch, reply: dict | None = None) -> list[dict]:
    """A hub that launches, and answers every stop with *reply*: by default the refusal
    of one restarted after a SIGKILL (it holds no helpers) whose runtime reports the
    container running.  Returns the stop requests seen."""
    if reply is None:
        reply = {"status": "error", "running": True,
                 "message": "refusing to stop 'kb-t-helper-1'"}
    stops: list[dict] = []

    def _hub(_path, request):
        if request.get("action") == "spawn":
            return {"status": "ok", "container_name": f"kb-t-helper-{request['helper_num']}"}
        stops.append(request)
        return reply

    monkeypatch.setattr("kanibako.channels.helper_client.send_request", _numbering(_hub))
    return stops


class TestRefusedStop:
    """The hub refused the stop, so the container may still run: the CLI says so,
    exits non-zero, and keeps the helper's state and directories."""

    def test_stop_reports_the_refusal_and_keeps_state(self, helpers_env, capsys, monkeypatch):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch)
        capsys.readouterr()

        rc = run_stop(_make_args(number=1))
        out, err = capsys.readouterr()
        assert rc == 1
        assert len(stops) == 1
        assert "Stopped" not in out
        assert "Cannot stop helper 1: refusing to stop 'kb-t-helper-1'" in err
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "running"

    @pytest.mark.parametrize("cascade", [False, True])
    def test_cleanup_keeps_the_dirs(self, helpers_env, capsys, monkeypatch, cascade):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch)
        capsys.readouterr()

        rc = run_cleanup(_make_args(number=1, cascade=cascade))
        out, err = capsys.readouterr()
        assert rc == 1
        assert len(stops) == 1
        assert "Cleaned up" not in out
        assert "Cannot clean up helper 1: refusing to stop 'kb-t-helper-1'" in err
        assert (helpers_env / "helpers" / "1" / "workspace").is_dir()
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "running"


class TestNotRunningStop:
    """The hub lost the helper (a restart) and its runtime reports the container not
    running: nothing to stop, so stop and cleanup proceed."""

    def test_stop_marks_the_helper_stopped(self, helpers_env, capsys, monkeypatch):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch, {"status": "not_running"})
        capsys.readouterr()

        assert run_stop(_make_args(number=1)) == 0
        assert len(stops) == 1
        assert "Stopped helper 1." in capsys.readouterr().out
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"

    @pytest.mark.parametrize("cascade", [False, True])
    def test_cleanup_removes_the_dirs(self, helpers_env, capsys, monkeypatch, cascade):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch, {"status": "not_running"})

        assert run_cleanup(_make_args(number=1, cascade=cascade)) == 0
        assert len(stops) == 1
        assert not (helpers_env / "helpers" / "1").exists()


def _subtree(helpers: Path) -> tuple[Path, Path]:
    """Helper 1 (spawned) with child 2 under it and grandchild 3 under that, both
    running.  Returns the child's and the grandchild's helpers dirs."""
    level2 = helpers / "1" / "helpers"
    level3 = level2 / "2" / "helpers"
    (level3 / "3").mkdir(parents=True)
    write_helper_state(level2, 2, {"status": "running", "container_name": "kb-t-helper-2"})
    write_helper_state(level3, 3, {"status": "running", "container_name": "kb-t-helper-3"})
    return level2, level3


class TestStopCascades:
    """His 171-HC ruling (a): stopping a helper stops its whole subtree, deepest first,
    each through the hub and each marked stopped."""

    def test_stop_stops_every_descendant(self, helpers_env, capsys, monkeypatch):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch, {"status": "ok"})
        level2, level3 = _subtree(helpers_env / "helpers")
        capsys.readouterr()

        assert run_stop(_make_args(number=1)) == 0
        assert [s["helper_num"] for s in stops] == [3, 2, 1]
        assert [s["container_name"] for s in stops] == [
            "kb-t-helper-3", "kb-t-helper-2", "kb-t-helper-1"]
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"
        assert read_helper_state(level2, 2)["status"] == "stopped"
        assert read_helper_state(level3, 3)["status"] == "stopped"
        out = capsys.readouterr().out
        assert "Stopped helper 3." in out
        assert "Stopped helper 2." in out
        assert "Stopped helper 1." in out

    def test_a_refused_descendant_keeps_its_state(self, helpers_env, capsys, monkeypatch):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops: list[dict] = []

        def _hub(_path, request):
            if request.get("action") == "spawn":
                return {"status": "ok", "container_name": "kb-t-helper-1"}
            stops.append(request)
            if request["helper_num"] == 3:
                return {"status": "error", "running": True,
                        "message": "refusing to stop 'kb-t-helper-3'"}
            return {"status": "ok"}

        monkeypatch.setattr("kanibako.channels.helper_client.send_request", _numbering(_hub))
        level2, level3 = _subtree(helpers_env / "helpers")
        capsys.readouterr()

        assert run_stop(_make_args(number=1)) == 1
        assert [s["helper_num"] for s in stops] == [3, 2, 1]
        out, err = capsys.readouterr()
        assert "Cannot stop helper 3: refusing to stop 'kb-t-helper-3'" in err
        assert read_helper_state(level3, 3)["status"] == "running"
        assert read_helper_state(level2, 2)["status"] == "stopped"
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"

    def test_an_already_stopped_helper_still_stops_its_subtree(
            self, helpers_env, capsys, monkeypatch):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        state = read_helper_state(helpers_env / "helpers", 1)
        state["status"] = "stopped"
        write_helper_state(helpers_env / "helpers", 1, state)
        stops = _hub_replies_to_stop(monkeypatch, {"status": "ok"})
        level2, level3 = _subtree(helpers_env / "helpers")
        capsys.readouterr()

        assert run_stop(_make_args(number=1)) == 0
        assert [s["helper_num"] for s in stops] == [3, 2]
        assert "Helper 1 is already stopped." in capsys.readouterr().out
        assert read_helper_state(level2, 2)["status"] == "stopped"
        assert read_helper_state(level3, 3)["status"] == "stopped"


class TestCascadeCleanupStopsTheSubtree:
    """``cleanup``, with or without ``--cascade``, stops every descendant through the
    hub, deepest first, before it deletes any directory: a descendant's container never
    outlives its dirs."""

    @pytest.mark.parametrize("cascade", [False, True])
    def test_every_descendant_is_stopped_before_its_dirs_go(
            self, helpers_env, capsys, monkeypatch, cascade):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch, {"status": "ok"})
        _subtree(helpers_env / "helpers")
        capsys.readouterr()

        assert run_cleanup(_make_args(number=1, cascade=cascade)) == 0
        assert [s["helper_num"] for s in stops] == [3, 2, 1]
        assert [s["container_name"] for s in stops] == [
            "kb-t-helper-3", "kb-t-helper-2", "kb-t-helper-1"]
        assert "Cleaned up helper 1 and 2 descendant(s)." in capsys.readouterr().out
        assert not (helpers_env / "helpers" / "1").exists()

    @pytest.mark.parametrize("cascade", [False, True])
    def test_a_refused_descendant_keeps_every_dir(
            self, helpers_env, capsys, monkeypatch, cascade):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops: list[dict] = []

        def _hub(_path, request):
            if request.get("action") == "spawn":
                return {"status": "ok", "container_name": "kb-t-helper-1"}
            stops.append(request)
            if request["helper_num"] == 3:
                return {"status": "error", "running": True,
                        "message": "refusing to stop 'kb-t-helper-3'"}
            return {"status": "ok"}

        monkeypatch.setattr("kanibako.channels.helper_client.send_request", _numbering(_hub))
        level2, level3 = _subtree(helpers_env / "helpers")
        capsys.readouterr()

        assert run_cleanup(_make_args(number=1, cascade=cascade)) == 1
        assert [s["helper_num"] for s in stops] == [3, 2, 1]
        out, err = capsys.readouterr()
        assert "Cannot clean up helper 3: refusing to stop 'kb-t-helper-3'" in err
        assert "Cleaned up" not in out
        assert (level3 / "3").is_dir()
        assert read_helper_state(level3, 3)["status"] == "running"
        assert read_helper_state(level2, 2)["status"] == "stopped"
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"


class TestCleanupOfAStoppedHelper:
    """``stopped`` can follow an unreachable hub, so cleanup still asks; a refusal
    stops it only when the runtime reports the container running."""

    def _stopped(self, helpers_env):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        state = read_helper_state(helpers_env / "helpers", 1)
        state["status"] = "stopped"
        state["container_name"] = "kb-t-helper-1"
        write_helper_state(helpers_env / "helpers", 1, state)

    def test_a_running_container_keeps_the_dirs(self, helpers_env, capsys, monkeypatch):
        self._stopped(helpers_env)
        stops = _hub_replies_to_stop(monkeypatch)

        assert run_cleanup(_make_args(number=1)) == 1
        assert [r["container_name"] for r in stops] == ["kb-t-helper-1"]
        assert "Cannot clean up helper 1" in capsys.readouterr().err
        assert (helpers_env / "helpers" / "1" / "workspace").is_dir()

    def test_a_refusal_without_a_running_container_removes_the_dirs(
        self, helpers_env, capsys, monkeypatch,
    ):
        self._stopped(helpers_env)
        stops = _hub_replies_to_stop(monkeypatch, {"status": "error", "message": "no context"})

        assert run_cleanup(_make_args(number=1)) == 0
        assert len(stops) == 1
        assert not (helpers_env / "helpers" / "1").exists()


class TestRunCleanup:
    def test_cleanup_removes_dirs(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        helpers = helpers_env / "helpers"
        assert (helpers / "1").is_dir()

        rc = run_cleanup(_make_args(number=1))
        assert rc == 0
        assert "Cleaned up helper 1" in capsys.readouterr().out
        assert not (helpers / "1").exists()

    def test_cleanup_leaves_siblings_alone(self, helpers_env, capsys):
        """Cleaning up one helper removes only its own tree."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        helpers = helpers_env / "helpers"
        rc = run_cleanup(_make_args(number=2))
        assert rc == 0
        assert not (helpers / "2").exists()
        assert (helpers / "1" / "workspace").is_dir()
        assert (helpers / "all" / "rw").is_dir()

    def test_cleanup_nonexistent(self, helpers_env, capsys):
        rc = run_cleanup(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err


class TestRunRespawn:
    def test_respawn_stopped_helper(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        rc = run_respawn(_make_args(number=1))
        assert rc == 0
        assert "Respawned helper 1" in capsys.readouterr().out

        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"

    def test_respawn_running_refused(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        capsys.readouterr()

        rc = run_respawn(_make_args(number=1))
        assert rc == 1
        assert "not stopped" in capsys.readouterr().err

    def test_respawn_nonexistent(self, helpers_env, capsys):
        rc = run_respawn(_make_args(number=99))
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err


class TestCascadeCleanup:
    def test_cascade_removes_children(self, helpers_env, capsys):
        """Cascade cleanup removes a helper and its nested children."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))

        helpers = helpers_env / "helpers"
        # Simulate nested children: helpers/1/helpers/1 and helpers/1/helpers/2
        child_helpers = helpers / "1" / "helpers"
        (child_helpers / "1").mkdir(parents=True)
        write_helper_state(child_helpers, 1, {"status": "spawned"})
        (child_helpers / "2").mkdir(parents=True)
        write_helper_state(child_helpers, 2, {"status": "spawned"})

        capsys.readouterr()
        rc = run_cleanup(_make_args(number=1, cascade=True))
        assert rc == 0
        assert "descendant" in capsys.readouterr().out
        assert not (helpers / "1").exists()

    def test_cascade_multi_level(self, helpers_env, capsys):
        """Cascade handles multiple nesting levels."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))

        helpers = helpers_env / "helpers"
        # Create a 3-level deep tree: helpers/1/helpers/1/helpers/1
        level2 = helpers / "1" / "helpers"
        (level2 / "1").mkdir(parents=True)
        level3 = level2 / "1" / "helpers"
        (level3 / "1").mkdir(parents=True)

        capsys.readouterr()
        assert run_cleanup(_make_args(number=1, cascade=True)) == 0
        assert "Cleaned up helper 1 and 2 descendant(s)." in capsys.readouterr().out
        assert not (helpers / "1").exists()

    def test_no_cascade_stops_and_removes_children(self, helpers_env, capsys, monkeypatch):
        """Without --cascade the child's dir goes with its parent's, so the child is
        stopped through the hub first, exactly as with --cascade."""
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        stops = _hub_replies_to_stop(monkeypatch, {"status": "ok"})

        helpers = helpers_env / "helpers"
        child_helpers = helpers / "1" / "helpers"
        (child_helpers / "2").mkdir(parents=True)
        write_helper_state(child_helpers, 2, {"status": "running",
                                              "container_name": "kb-t-helper-2"})

        capsys.readouterr()
        assert run_cleanup(_make_args(number=1, cascade=False)) == 0
        assert [s["helper_num"] for s in stops] == [2, 1]
        assert "Cleaned up helper 1 and 1 descendant(s)." in capsys.readouterr().out
        assert not (helpers / "1").exists()


class TestRunSend:
    def test_send_no_socket(self, helpers_env, capsys):
        """Send fails gracefully when helpers not enabled."""
        _no_hub(helpers_env)
        rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 1
        assert "Cannot send: this box runs no helper hub" in capsys.readouterr().err

    @pytest.mark.parametrize("verb", ["send", "broadcast"])
    def test_a_stub_or_dead_socket_gets_the_no_hub_wording(self, helpers_env, capsys, verb):
        """The socket FILE exists (a stub, or a dead hub's socket): "no socket found"
        would be false; the cause is the same as with no file, so is the sentence."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.unlink()
        sock.touch()
        run = run_send if verb == "send" else run_broadcast
        assert run(_make_args(number=1, message="hello")) == 1
        err = capsys.readouterr().err
        assert f"Cannot {verb}: this box runs no helper hub" in err
        assert "no socket" not in err

    def test_send_with_socket(self, helpers_env, capsys):
        """Send succeeds when socket exists and mock returns ok."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 0
        assert "sent" in capsys.readouterr().out

    def test_send_failure(self, helpers_env, capsys):
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "no route"}
            rc = run_send(_make_args(number=1, message="hello"))
        assert rc == 1
        assert "failed" in capsys.readouterr().err.lower()


class TestRunBroadcast:
    def test_broadcast_no_socket(self, helpers_env, capsys):
        _no_hub(helpers_env)
        rc = run_broadcast(_make_args(message="all hands"))
        assert rc == 1
        assert "Cannot broadcast: this box runs no helper hub" in capsys.readouterr().err

    def test_broadcast_success(self, helpers_env, capsys):
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_broadcast(_make_args(message="all hands"))
        assert rc == 0
        assert "broadcast" in capsys.readouterr().out.lower()


class TestRunRegister:
    def test_register_no_socket(self, helpers_env, capsys):
        _no_hub(helpers_env)
        rc = run_register(_make_args(number=1))
        assert rc == 1
        assert "Cannot register helper 1: no helper hub listens" in capsys.readouterr().err

    @staticmethod
    def _real_hub(home: Path, monkeypatch):
        """A real hub at the pinned socket path (bound in a short /tmp dir, linked in),
        and the real one-shot ``send_request`` back in place of the fixture's stub."""
        from unittest.mock import MagicMock

        from kanibako.channels import helper_client
        from kanibako.channels.helper_listener import HelperHub

        monkeypatch.setattr(helper_client, "send_request", _REAL_SEND_REQUEST)
        real = Path(tempfile.mkdtemp(prefix="kbr-", dir="/tmp")) / "s"
        hub = HelperHub()
        hub.start(real, MagicMock(helpers_dir=home / "helpers"))
        link = home / ".kanibako" / "state" / "hub" / "helper.sock"
        link.unlink()
        link.symlink_to(real)
        return hub, real

    def test_the_registration_is_held_until_the_hub_goes(self, helpers_env, monkeypatch):
        """The hub drops a registration when its connection closes, so a register that
        returned would leave the helper unreachable at once.  It must still hold after
        a message to it, and end (rc 0) only when the hub goes."""
        import threading
        import time

        hub, real = self._real_hub(helpers_env, monkeypatch)
        result: list[int] = []
        holder = threading.Thread(
            target=lambda: result.append(run_register(_make_args(number=1))), daemon=True,
        )
        try:
            holder.start()
            deadline = time.monotonic() + 10
            while 1 not in hub._connections and holder.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            time.sleep(0.5)
            assert 1 in hub._connections and holder.is_alive(), result
            resp = _REAL_SEND_REQUEST(real, {"action": "send", "to": 1, "payload": {"text": "hi"}})
            assert resp == {"status": "ok"}
            time.sleep(0.2)
            assert 1 in hub._connections and holder.is_alive()
        finally:
            hub.stop()
            holder.join(timeout=10)
            shutil.rmtree(real.parent, ignore_errors=True)
        assert result == [0]
        assert 1 not in hub._connections

    def test_a_refused_registration_says_why(self, helpers_env, monkeypatch, capsys):
        hub, real = self._real_hub(helpers_env, monkeypatch)
        try:
            rc = run_register(_make_args(number=-1))
        finally:
            hub.stop()
            shutil.rmtree(real.parent, ignore_errors=True)
        assert rc == 1
        assert "Cannot register helper -1: Registration failed: invalid helper_num" in (
            capsys.readouterr().err
        )


class TestRunLog:
    def test_log_no_file(self, helpers_env, capsys):
        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 1
        assert "No helper message log" in capsys.readouterr().err

    def test_log_displays_entries(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "state" / "helpers.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        entries = [
            {"ts": "2026-02-25T12:35:10Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "Analyze auth."}},
            {"ts": "2026-02-25T12:36:45Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "Found 3 issues."}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "[0 → 1]" in out
        assert "Analyze auth." in out
        assert "[1 → 0]" in out
        assert "Found 3 issues." in out

    def test_log_filter_by_helper(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "state" / "helpers.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "a"}},
            {"ts": "T12:01:00Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "b"}},
            {"ts": "T12:02:00Z", "type": "message", "from": 2, "to": 0, "payload": {"text": "c"}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=1, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "b" in out
        assert "c" not in out

    def test_log_last_n(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "state" / "helpers.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "first"}},
            {"ts": "T12:01:00Z", "type": "message", "from": 1, "to": 0, "payload": {"text": "second"}},
            {"ts": "T12:02:00Z", "type": "message", "from": 2, "to": 0, "payload": {"text": "third"}},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=1))
        assert rc == 0
        out = capsys.readouterr().out
        assert "third" in out
        assert "first" not in out

    def test_log_control_entries(self, helpers_env, capsys):
        log_file = helpers_env / ".kanibako" / "state" / "helpers.jsonl"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        import json
        entries = [
            {"ts": "T12:00:00Z", "type": "control", "event": "spawn", "helper": 1, "model": "sonnet"},
            {"ts": "T12:01:00Z", "type": "control", "event": "register", "helper": 1},
        ]
        log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")

        rc = run_log(_make_args(follow=False, from_helper=None, last=None))
        assert rc == 0
        out = capsys.readouterr().out
        assert "[spawn]" in out
        assert "model=sonnet" in out
        assert "[register]" in out


class TestFormatLogEntry:
    def test_message_entry(self):
        entry = {"ts": "2026-02-25T12:35:10Z", "type": "message", "from": 0, "to": 1, "payload": {"text": "hello"}}
        result = _format_log_entry(entry)
        assert "12:35:10" in result
        assert "[0 → 1]" in result
        assert "hello" in result

    def test_broadcast_entry(self):
        entry = {"ts": "2026-02-25T12:37:00Z", "type": "message", "from": 0, "to": "all", "payload": {"text": "start"}}
        result = _format_log_entry(entry)
        assert "[0 → *]" in result

    def test_control_entry(self):
        entry = {"ts": "2026-02-25T12:34:56Z", "type": "control", "event": "spawn", "helper": 1, "model": "sonnet"}
        result = _format_log_entry(entry)
        assert "[spawn]" in result
        assert "helper 1" in result


class TestSocketWiring:
    def test_spawn_with_socket(self, helpers_env, capsys):
        """Spawn calls socket client when socket exists."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering({"status": "ok", "container_name": "kanibako-helper-1-abc"})
            args = _make_args(depth=None, breadth=None, model=None)
            rc = run_spawn(args)

        assert rc == 0
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"
        assert state["container_name"] == "kanibako-helper-1-abc"

    def test_spawn_socket_failure(self, helpers_env, capsys):
        """Spawn records 'failed' when socket returns error."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering({"status": "error", "message": "image not found"})
            args = _make_args(depth=None, breadth=None, model=None)
            rc = run_spawn(args)

        # A helper that did not launch is not "Spawned": no success line, rc 1.  The
        # directory stays, marked failed with the hub's reason, until ``cleanup``.
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Spawned" not in out
        assert "Cannot spawn: helper 1 did not launch: image not found" in err
        assert "cleanup 1" in err
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "failed"
        assert state["error"] == "image not found"

    def test_spawn_connection_failure(self, helpers_env, capsys):
        """A hub that drops the connection mid-request is a failed launch, too."""
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering(ConnectionError("Connection closed before response"))
            rc = run_spawn(_make_args(depth=None, breadth=None, model=None))
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Spawned" not in out
        assert "did not launch: Connection closed before response" in err
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "failed"

    def test_respawn_failure(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "image not found"}
            rc = run_respawn(_make_args(number=1))
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Respawned" not in out
        assert "Cannot respawn: helper 1 did not launch: image not found" in err
        # A helper that worked before keeps its directory AND stays respawnable: the
        # message names the retry, never ``cleanup`` (which deletes the directory).
        assert "cleanup" not in err
        assert "kanibako box helper respawn 1" in err
        state = read_helper_state(helpers_env / "helpers", 1)
        assert (state["status"], state["error"]) == ("stopped", "image not found")

    def test_a_failed_respawn_can_be_retried(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "error", "message": "image not found"}
            assert run_respawn(_make_args(number=1)) == 1
            m.return_value = {"status": "ok", "container_name": "c"}
            assert run_respawn(_make_args(number=1)) == 0
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"
        assert "error" not in state

    def test_stop_with_socket(self, helpers_env, capsys):
        """Stop calls socket client for container stop."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        # Spawn first (without socket active for dir creation)
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        # Manually set container_name in state
        state = read_helper_state(helpers_env / "helpers", 1)
        state["container_name"] = "kanibako-helper-1-abc"
        state["status"] = "running"
        write_helper_state(helpers_env / "helpers", 1, state)
        capsys.readouterr()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "ok"}
            rc = run_stop(_make_args(number=1))

        assert rc == 0
        m.assert_called_once()
        call_req = m.call_args[0][1]
        assert call_req["action"] == "stop"
        assert call_req["container_name"] == "kanibako-helper-1-abc"
        assert call_req["helper_num"] == 1

    def test_respawn_never_reaches_the_create_point(self, helpers_env, capsys):
        """``create_helper_dirs`` seeds the home; a respawn must not reach it, or a
        deleted ``.profile`` would come back (seed once, never re-seed)."""
        from kanibako.settings.bootstrap import PROFILE_FILE

        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        profile = helpers_env / "helpers" / "1" / PROFILE_FILE
        profile.unlink(missing_ok=True)
        with patch("kanibako.commands.helper_cmd.create_helper_dirs") as create:
            assert run_respawn(_make_args(number=1)) == 0
        create.assert_not_called()
        assert not profile.exists()

    def test_respawn_with_socket(self, helpers_env, capsys):
        """Respawn calls socket client for container relaunch."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.touch()

        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        capsys.readouterr()

        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "ok", "container_name": "kanibako-helper-1-new"}
            rc = run_respawn(_make_args(number=1))

        assert rc == 0
        state = read_helper_state(helpers_env / "helpers", 1)
        assert state["status"] == "running"


class TestInitScriptNameCopySite:
    """Pins the copy site to INIT_SCRIPT_NAME."""

    def test_copy_site_follows_init_script_name(self, helpers_env):
        """The copy site in helper_cmd uses INIT_SCRIPT_NAME for the destination filename."""
        with patch.object(helper_cmd, "INIT_SCRIPT_NAME", "init-check.sh"):
            run_spawn(_make_args(depth=None, breadth=None, model=None))
        init = helpers_env / "helpers" / "1" / "scripts" / "init-check.sh"
        assert init.is_file()


class TestNoHub:
    """A box with no helper hub (a persistent start runs none) cannot launch a helper,
    so spawn and respawn REFUSE, naming why, before they write anything (bifrost hb5:
    ``Spawned helper 1`` rc 0 with no container)."""

    def test_spawn_refuses_and_writes_nothing(self, helpers_env, capsys):
        _no_hub(helpers_env)
        rc = run_spawn(_make_args(depth=None, breadth=None, model=None))
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Spawned" not in out
        assert "Cannot spawn: this box runs no helper hub" in err
        assert "--ephemeral" in err
        assert not (helpers_env / "helpers").exists()

    def test_respawn_refuses_and_keeps_the_stopped_state(self, helpers_env, capsys):
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        _no_hub(helpers_env)
        capsys.readouterr()

        rc = run_respawn(_make_args(number=1))
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Respawned" not in out
        assert "Cannot respawn: this box runs no helper hub" in err
        assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"

    def test_the_leftover_socket_stub_is_not_a_hub(self, helpers_env, capsys):
        """An ephemeral start pre-creates the socket's mount stub (a plain FILE) in the
        persistent home; the next persistent start runs no hub, but the file stays."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.unlink()
        sock.touch()
        rc = run_spawn(_make_args(depth=None, breadth=None, model=None))
        assert rc == 1
        out, err = capsys.readouterr()
        assert "Spawned" not in out
        assert "Cannot spawn: this box runs no helper hub" in err
        assert not (helpers_env / "helpers").exists()

    def test_a_dead_hubs_socket_is_not_a_hub(self, helpers_env, capsys):
        """A real socket file that nothing listens on (the hub died) refuses the same way."""
        sock = helpers_env / ".kanibako" / "state" / "hub" / "helper.sock"
        sock.unlink()
        listener, real = _listening_socket()
        listener.close()  # the file stays; a connect now gets ECONNREFUSED
        try:
            sock.symlink_to(real)
            rc = run_spawn(_make_args(depth=None, breadth=None, model=None))
        finally:
            shutil.rmtree(real.parent, ignore_errors=True)
        assert rc == 1
        assert "Cannot spawn: this box runs no helper hub" in capsys.readouterr().err
        assert not (helpers_env / "helpers").exists()

    def test_a_spent_budget_still_names_the_budget(self, helpers_env, capsys):
        """The budget refusal comes first: it holds with or without a hub."""
        _no_hub(helpers_env)
        write_spawn_budget(helpers_env / "spawn.yaml", SpawnBudget(depth=0, breadth=4))
        assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 1
        assert "spawn depth exhausted" in capsys.readouterr().err


class TestTreeWideNumbering:
    """keyspec ``meta.box.helper_num``: "helper index, unique across the director's
    helper tree".  His 171-HN ruling (b): the HUB numbers every helper from one counter
    for the whole tree (no blocks, so a number no longer says where its helper sits),
    and the spawn names the requester, which a box learns from its bound spawn
    document."""

    def test_the_director_takes_the_hubs_number_and_names_no_requester(self, helpers_env):
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering({"status": "ok", "container_name": "c"})
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
        assert m.call_args_list[0][0][1] == {"action": "number"}
        req = _launches(m)[0]
        assert req["helper_num"] == 1
        assert req.get("requester") is None

    def test_the_number_is_the_hubs_not_the_box(self, helpers_env, capsys):
        """A number the box would never pick itself (none of its own are on disk)."""
        def _send(_path, request):
            if request["action"] == "number":
                return {"status": "ok", "helper_num": 42}
            return {"status": "ok", "container_name": "c"}

        with patch("kanibako.channels.helper_client.send_request", side_effect=_send):
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
        assert "Spawned helper 42" in capsys.readouterr().out
        assert _get_existing_helpers(helpers_env / "helpers") == [42]

    def test_a_planted_number_does_not_survive_the_directors_start(self, helpers_env):
        """A ``meta.box.helper_num`` hand-planted in the director's document is gone
        once a start delivers the budget (``start._deliver_spawn_budget`` writes it
        through ``write_spawn_budget``), so the director names no requester."""
        doc = helpers_env / "spawn.yaml"
        write_spawn_budget(doc, SpawnBudget(depth=2, breadth=3))
        write_helper_number(doc, 5)
        write_spawn_budget(doc, SpawnBudget(depth=2, breadth=3))  # the start's delivery
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering({"status": "ok", "container_name": "c"})
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
        assert [r.get("requester") for r in _launches(m)] == [None]

    def test_a_helpers_children_take_the_counter_not_a_block(self, helpers_env, capsys):
        write_spawn_budget(helpers_env / "spawn.yaml", SpawnBudget(depth=2, breadth=3))
        write_helper_number(helpers_env / "spawn.yaml", 2)
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.side_effect = _numbering({"status": "ok", "container_name": "c"})
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
        assert [r["helper_num"] for r in _launches(m)] == [1, 2]
        assert [r["requester"] for r in _launches(m)] == [2, 2]
        assert (helpers_env / "helpers" / "2").is_dir()

    @pytest.mark.parametrize("reply", [
        {"status": "error", "message": "hub says no"},
        {"status": "ok"},
        {"status": "ok", "helper_num": 0},
        {"status": "ok", "helper_num": True},
        ConnectionError("hub went away"),
    ])
    def test_no_number_from_the_hub_writes_nothing(self, helpers_env, capsys, reply):
        with patch("kanibako.channels.helper_client.send_request") as m:
            if isinstance(reply, BaseException):
                m.side_effect = reply
            else:
                m.return_value = reply
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 1
        assert "Cannot spawn: the hub gave no helper number" in capsys.readouterr().err
        assert not (helpers_env / "helpers").exists()
        assert _launches(m) == []

    def test_the_breadth_limit_still_counts_this_boxs_helpers(self, helpers_env):
        write_spawn_budget(helpers_env / "spawn.yaml", SpawnBudget(depth=2, breadth=2))
        for _ in range(2):
            assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 0
        assert run_spawn(_make_args(depth=None, breadth=None, model=None)) == 1

    def test_respawn_names_the_requester(self, helpers_env):
        write_helper_number(helpers_env / "spawn.yaml", 1)
        run_spawn(_make_args(depth=None, breadth=None, model=None))
        run_stop(_make_args(number=1))
        with patch("kanibako.channels.helper_client.send_request") as m:
            m.return_value = {"status": "ok", "container_name": "c"}
            assert run_respawn(_make_args(number=1)) == 0
        req = m.call_args[0][1]
        assert (req["helper_num"], req["requester"]) == (1, 1)

    def test_a_linked_helper_dir_is_not_a_helper(self, helpers_env, tmp_path):
        helpers = helpers_env / "helpers"
        helpers.mkdir()
        (helpers / "1").mkdir()
        (tmp_path / "elsewhere").mkdir()
        (helpers / "2").symlink_to(tmp_path / "elsewhere")
        assert _get_existing_helpers(helpers) == [1]

    def test_helper_init_exports_nothing(self):
        """The number is in the spawn document, never a variable (system-design
        § Delivery at launch: nothing writes a variable after the channel), so the
        bundled entrypoint exports none."""
        from kanibako.channels.helpers import bundled_init_script

        assert "KANIBAKO_HELPER_NUM" not in bundled_init_script().read_text()

    @pytest.mark.parametrize("verb", ["spawn", "respawn"])
    @pytest.mark.parametrize("value, shown", [("x1", "'x1'"), (0, "to 0,"), (True, "True")])
    def test_a_malformed_number_is_a_clean_refusal(self, helpers_env, verb, value, shown):
        """``meta.box.helper_num: x1`` in the spawn document: a KanibakoError naming the
        value and the file (the CLI's ``Error:`` line, rc 1), never a traceback; nothing
        is written for a spawn."""
        import yaml

        from kanibako.errors import KanibakoError

        if verb == "respawn":
            run_spawn(_make_args(depth=None, breadth=None, model=None))
            run_stop(_make_args(number=1))
        doc = helpers_env / "spawn.yaml"
        doc.write_text(yaml.safe_dump({"meta": {"box": {"helper_num": value}}}))
        with pytest.raises(KanibakoError, match=shown) as exc:
            if verb == "spawn":
                run_spawn(_make_args(depth=None, breadth=None, model=None))
            else:
                run_respawn(_make_args(number=1))
        assert str(doc) in str(exc.value)
        if verb == "spawn":
            assert not (helpers_env / "helpers").exists()
        else:
            assert read_helper_state(helpers_env / "helpers", 1)["status"] == "stopped"


def _make_args(**kwargs):
    """Create a minimal argparse.Namespace for testing."""
    return type("Args", (), kwargs)()
