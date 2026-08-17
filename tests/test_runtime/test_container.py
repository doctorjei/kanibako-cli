"""Tests for kanibako.runtime.container."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.runtime.container import ContainerRuntime
from kanibako.errors import ContainerError


class TestContainerRuntime:
    def test_detect_raises_when_nothing_found(self, monkeypatch):
        monkeypatch.delenv("KANIBAKO_DOCKER_CMD", raising=False)
        with patch("shutil.which", return_value=None):
            with pytest.raises(ContainerError, match="No container runtime"):
                ContainerRuntime()

    def test_uses_env_override(self, monkeypatch):
        monkeypatch.setenv("KANIBAKO_DOCKER_CMD", "/usr/bin/fake-docker")
        rt = ContainerRuntime()
        assert rt.cmd == "/usr/bin/fake-docker"

    def test_explicit_command(self):
        rt = ContainerRuntime(command="/usr/bin/podman")
        assert rt.cmd == "/usr/bin/podman"


class TestGetLocalDigest:
    def test_success_podman_format(self):
        """Podman returns a list; extract digest from RepoDigests."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{
            "RepoDigests": ["ghcr.io/x/kanibako-oci@sha256:abc123"]
        }])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            result = rt.get_local_digest("ghcr.io/x/kanibako-oci:latest")
        assert result == "sha256:abc123"

    def test_failure_returns_none(self):
        rt = ContainerRuntime(command="echo")
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            result = rt.get_local_digest("nonexistent:latest")
        assert result is None

    def test_empty_repo_digests(self):
        """Locally-built images may have no RepoDigests."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{"RepoDigests": []}])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            result = rt.get_local_digest("local:latest")
        assert result is None

    def test_exception_returns_none(self):
        """Any unexpected exception returns None."""
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
            result = rt.get_local_digest("img:latest")
        assert result is None

    def test_returns_first_of_multiple(self):
        """With multiple RepoDigests, get_local_digest returns the first."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{
            "RepoDigests": [
                "ghcr.io/x/kanibako-oci@sha256:3de8",
                "ghcr.io/x/kanibako-oci@sha256:4f49",
            ]
        }])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            assert rt.get_local_digest("ghcr.io/x/kanibako-oci:latest") == "sha256:3de8"


class TestGetLocalDigests:
    def test_returns_full_list(self):
        """All RepoDigests are returned with the repo@ prefix stripped."""
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{
            "RepoDigests": [
                "ghcr.io/x/kanibako-oci@sha256:3de8",
                "ghcr.io/x/kanibako-oci@sha256:4f49",
            ]
        }])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            result = rt.get_local_digests("ghcr.io/x/kanibako-oci:latest")
        assert result == ["sha256:3de8", "sha256:4f49"]

    def test_empty_repo_digests(self):
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{"RepoDigests": []}])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            assert rt.get_local_digests("local:latest") == []

    def test_failure_returns_empty(self):
        rt = ContainerRuntime(command="echo")
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.get_local_digests("nonexistent:latest") == []

    def test_exception_returns_empty(self):
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
            assert rt.get_local_digests("img:latest") == []


class TestGetLocalPlatform:
    def test_parses_os_arch(self):
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{"Os": "linux", "Architecture": "amd64"}])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            assert rt.get_local_platform("img:latest") == "linux/amd64"

    def test_parses_os_arch_variant(self):
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{
            "Os": "linux", "Architecture": "arm", "Variant": "v7",
        }])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            assert rt.get_local_platform("img:latest") == "linux/arm/v7"

    def test_missing_fields_returns_none(self):
        import json
        rt = ContainerRuntime(command="echo")
        inspect_output = json.dumps([{"Os": "linux"}])
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=inspect_output)
            assert rt.get_local_platform("img:latest") is None

    def test_failure_returns_none(self):
        rt = ContainerRuntime(command="echo")
        from unittest.mock import MagicMock
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.get_local_platform("img:latest") is None

    def test_exception_returns_none(self):
        rt = ContainerRuntime(command="echo")
        with patch("kanibako.runtime.container.subprocess.run", side_effect=OSError("fail")):
            assert rt.get_local_platform("img:latest") is None


class TestUnshareRm:
    """Test ContainerRuntime.unshare_rm()."""

    def test_invokes_podman_unshare_rm(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            ok = rt.unshare_rm(Path("/data/boxes/proj"))
        assert ok is True
        cmd = m.call_args[0][0]
        assert cmd == ["/usr/bin/podman", "unshare", "rm", "-rf",
                       "/data/boxes/proj"]

    def test_returns_false_on_nonzero(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.unshare_rm(Path("/data/boxes/proj")) is False

    def test_docker_has_no_unshare(self):
        rt = ContainerRuntime(command="/usr/bin/docker")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            assert rt.unshare_rm(Path("/data/boxes/proj")) is False
            m.assert_not_called()


@pytest.mark.no_unshare_sim
class TestUnshareChownChmod:
    """``unshare_chown`` / ``unshare_chmod`` — the WRITE-side counterparts of
    ``unshare_rm``, and the mechanism behind J-7's root-owned canon skeleton.

    ⚑ THE ARGV IS THE CONTRACT. Two things are load-bearing and both are silent when
    wrong: the uid (0 inside ``podman unshare`` is the REAL HOST USER, whom
    ``keep-id:uid=1000`` maps to the in-box AGENT — so ``chown 0:0`` would leave the
    books writable by the very agent they are meant to be protected from), and the
    absence of ``-R`` (a recursive sweep of ``~/canon`` would take the SEEDED,
    agent-owned ``notebook/`` and ``workbook/`` with it).
    """

    _PATHS = [Path("/boxes/p/home/canon"), Path("/boxes/p/home/canon/bible")]

    def test_chown_invokes_podman_unshare_with_every_path(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.unshare_chown(self._PATHS, 1, 1) is True
        assert m.call_args[0][0] == [
            "/usr/bin/podman", "unshare", "chown", "1:1",
            "/boxes/p/home/canon", "/boxes/p/home/canon/bible",
        ]

    def test_chmod_invokes_podman_unshare_with_every_path(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.unshare_chmod(self._PATHS, "555") is True
        assert m.call_args[0][0] == [
            "/usr/bin/podman", "unshare", "chmod", "555",
            "/boxes/p/home/canon", "/boxes/p/home/canon/bible",
        ]

    def test_never_emits_a_recursive_flag(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.unshare_chown(self._PATHS, 1, 1)
            rt.unshare_chmod(self._PATHS, "555")
            for call in m.call_args_list:
                assert "-R" not in call[0][0]
                assert "--recursive" not in call[0][0]

    def test_returns_false_on_nonzero(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.unshare_chown(self._PATHS, 1, 1) is False
            assert rt.unshare_chmod(self._PATHS, "555") is False

    def test_docker_has_no_unshare(self):
        rt = ContainerRuntime(command="/usr/bin/docker")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            assert rt.unshare_chown(self._PATHS, 1, 1) is False
            assert rt.unshare_chmod(self._PATHS, "555") is False
            m.assert_not_called()

    def test_empty_path_list_is_a_no_op(self):
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            assert rt.unshare_chown([], 1, 1) is False
            assert rt.unshare_chmod([], "555") is False
            m.assert_not_called()


class TestPostStartHook:
    """``ContainerRuntime.run(post_start=...)`` — the seam that repairs the canon
    ownership podman's ``:U`` resets at container creation.

    ⚑ WHY THE HOOK LIVES IN ``run()`` AND NOT AT THE CALL SITES. ``run()`` is the
    ONE container-creation seam in the codebase (stopped containers are ``rm``'d and
    recreated, never ``podman start``ed), so wiring it here covers every present and
    future caller by construction. A re-protect a new call site could forget is a
    re-protect that will eventually be forgotten.
    """

    _KW = dict(
        shell_path=Path("/s"), project_path=Path("/p"),
        vault_ro_path=Path("/vro"), vault_rw_path=Path("/vrw"),
    )

    def test_detached_run_invokes_the_hook_after_the_container_starts(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        calls: list[str] = []
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="deadbeef", stderr="")
            rt.run("img", name="b", detach=True,
                   post_start=lambda: calls.append("protect"), **self._KW)
        assert calls == ["protect"]

    def test_a_failed_detached_launch_does_not_invoke_the_hook(self):
        """Nothing was created, so there is nothing to re-protect — and running the
        hook would log a spurious warning about a box that does not exist."""
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        calls: list[str] = []
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=125, stdout="", stderr="boom")
            rt.run("img", name="b", detach=True,
                   post_start=lambda: calls.append("protect"), **self._KW)
        assert calls == []

    def test_a_raising_hook_never_breaks_the_launch(self):
        """The hook is a REPAIR step, not a precondition: a box whose re-protect
        failed is litter-able but perfectly usable, and taking the launch down over
        it would trade a cosmetic problem for a total one."""
        from unittest.mock import MagicMock

        def _boom() -> None:
            raise RuntimeError("no podman")

        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="id", stderr="")
            assert rt.run("img", name="b", detach=True, post_start=_boom,
                          **self._KW) == 0

    def test_foreground_run_fires_the_hook_from_a_watcher(self):
        """⚑ THE EPHEMERAL/SHELL PATH. A foreground ``podman run`` BLOCKS for the
        whole session, so there is no "after start" moment in this thread. Without
        the watcher the detached path would be protected and the foreground path
        silently not — the worst kind of split, because the mode that skips the
        protection is invisible from the outside.
        """
        import threading

        rt = ContainerRuntime(command="/usr/bin/podman")
        fired = threading.Event()
        running = threading.Event()

        def _fg(cmd, *a, **kw):
            from unittest.mock import MagicMock
            running.set()               # the container is now "up"
            assert fired.wait(timeout=5), "watcher never fired the hook"
            return MagicMock(returncode=0)

        with patch("kanibako.runtime.container.subprocess.run", side_effect=_fg), \
                patch.object(ContainerRuntime, "is_running",
                             side_effect=lambda n: running.is_set()):
            rc = rt.run("img", name="b", detach=False,
                        post_start=fired.set, **self._KW)
        assert rc == 0
        assert fired.is_set()

    def test_short_lived_container_still_gets_the_hook_from_the_finally(self):
        """⚑ THE WATCHER IS NOT THE GUARANTEE. A container that lives less than one
        poll interval is NEVER observed running (measured: up at 20ms, gone at 50ms —
        the first probe fires before the subprocess starts and the second lands after
        the cancel), so a watcher-only design leaves short ephemeral boxes silently
        unrepaired — and no e2e can see it, because e2e stubs are long-running.

        The ``finally`` fire is what makes the on-disk state ALWAYS end protected.
        RED if it is removed: ``is_running`` never returns True here.
        """
        from unittest.mock import MagicMock

        rt = ContainerRuntime(command="/usr/bin/podman")
        calls: list[str] = []
        with patch("kanibako.runtime.container.subprocess.run",
                   return_value=MagicMock(returncode=0)), \
                patch.object(ContainerRuntime, "is_running", return_value=False):
            rc = rt.run("img", name="b", detach=False,
                        post_start=lambda: calls.append("protect"), **self._KW)
        assert rc == 0
        assert calls == ["protect"], "the finally must re-assert even if the watcher never saw the container"

    def test_hook_is_not_fired_twice_when_the_watcher_also_saw_it(self):
        """The watcher and the ``finally`` can both fire; the pass is idempotent, so
        this pins the COUNT only to keep the cost bounded and the logs honest."""
        import threading
        from unittest.mock import MagicMock

        rt = ContainerRuntime(command="/usr/bin/podman")
        calls: list[str] = []
        seen = threading.Event()

        def _fg(cmd, *a, **kw):
            assert seen.wait(timeout=5)
            return MagicMock(returncode=0)

        def _hook() -> None:
            calls.append("protect")
            seen.set()

        with patch("kanibako.runtime.container.subprocess.run", side_effect=_fg), \
                patch.object(ContainerRuntime, "is_running", return_value=True):
            rt.run("img", name="b", detach=False, post_start=_hook, **self._KW)
        assert len(calls) <= 2, calls
        assert calls, "the hook must fire at least once"

    def test_no_watcher_thread_leaks_when_the_container_never_starts(self):
        """The watcher is a daemon on a bounded poll AND is cancelled in a
        ``finally``, so a container that never comes up cannot leave it spinning."""
        import threading
        from unittest.mock import MagicMock

        before = threading.active_count()
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run",
                   return_value=MagicMock(returncode=1)), \
                patch.object(ContainerRuntime, "is_running", return_value=False):
            rt.run("img", name="b", detach=False, post_start=lambda: None, **self._KW)
        for t in threading.enumerate():
            if t.name.startswith("kanibako-poststart-"):
                t.join(timeout=5)
        assert threading.active_count() <= before


class TestPostStartCallSites:
    """⚑ THE OBLIGATION THAT THE SIGNATURE CANNOT ENFORCE.

    ``post_start`` is OPT-IN, so ``ContainerRuntime.run`` covers NOBODY by itself —
    a call site that omits it silently launches a box whose canon podman's ``:U``
    just re-chowned to the agent. What IS true by construction is only that ``run()``
    is the sole container-CREATION seam, so there is no other place the chown can
    happen. Whether each seam actually passes the hook is checked here.

    EXEMPT (verified individually — none binds a box home, so none has a canon
    skeleton to re-protect): the image probe/pull paths, ``run_interactive``, and the
    throwaway inspect/diff containers. Only the seams below mount a real box home:

      * ``commands/start.py`` main launch — the box itself;
      * ``commands/start.py`` ``_run_setup_command`` — a one-time setup container
        over the SAME box home, so its ``:U`` resets the same skeleton;
      * ``helper_listener.py`` — a helper box (no skeleton today, so its hook is a
        guarded no-op, but the seam must still pass one or it becomes the single
        unprotected launch path the day helper homes gain canon).
    """

    def test_every_box_home_run_site_passes_post_start(self):
        """Source-level sweep: a ``runtime.run(``/``ctx.runtime.run(`` call in the
        box-home modules must carry ``post_start=``. Adding a fourth box-launching
        seam without the hook fails HERE rather than in a user's box."""
        import inspect
        import re

        from kanibako.channels import helper_listener
        from kanibako.commands import start as start_mod

        offenders: list[str] = []
        for mod in (start_mod, helper_listener):
            src = inspect.getsource(mod)
            for m in re.finditer(r"\w*runtime\.run\(", src):
                line_start = src.rfind("\n", 0, m.start()) + 1
                before = src[line_start:m.start()]
                # PROSE, not a call. Both modules DISCUSS ``runtime.run(...)`` in
                # comments and docstrings, and a sweep that flags prose is a sweep
                # people learn to ignore.
                if "#" in before or "``" in before:
                    continue
                # Slice the call's argument text: from the open paren to the
                # matching close, cheaply bounded by the next ``)`` at call indent.
                tail = src[m.end():m.end() + 1200]
                call = tail.split("\n        )")[0].split("\n    )")[0]
                if "post_start" not in call:
                    lineno = src[:m.start()].count("\n") + 1
                    offenders.append(f"{mod.__name__}:{lineno}")
        assert not offenders, (
            "these runtime.run() call sites bind a box home but pass no post_start "
            f"hook, so podman's :U leaves their canon agent-owned: {offenders}"
        )

    def test_helper_seam_reasserts_but_never_creates_a_skeleton(self, tmp_path):
        """A helper home is not a box home. Re-asserting what is there is always
        right; CREATING canon mountpoints from a launch would be a silent layout
        change made by the wrong seam."""
        from unittest.mock import patch as _p

        from kanibako.settings.core_defaults import materialize_canon_skeleton_if_present

        bare = tmp_path / "helper-home"
        bare.mkdir()
        with _p("kanibako.settings.core_defaults.materialize_canon_skeleton") as m:
            materialize_canon_skeleton_if_present(bare)
        m.assert_not_called()
        assert not (bare / "canon").exists()

        with_canon = tmp_path / "box-home"
        (with_canon / "canon").mkdir(parents=True)
        with _p("kanibako.settings.core_defaults.materialize_canon_skeleton") as m:
            materialize_canon_skeleton_if_present(with_canon)
        m.assert_called_once()


class TestRemoveBoxTree:
    """``container.remove_box_tree`` — THE box-tree deleter (the body formerly inline
    in ``commands.box._parser._purge_dir``, moved so every verb can reuse it).

    Since J-7 every box home carries the root-owned canon skeleton, so this is no
    longer a rare has-a-root-owned-file case: a bare ``rmtree`` of ANY box home fails.
    """

    def test_removes_a_normal_tree(self, tmp_path):
        from kanibako.runtime.container import remove_box_tree
        d = tmp_path / "box"
        (d / "home").mkdir(parents=True)
        (d / "settings.yaml").write_text("x")
        assert remove_box_tree(d) is True
        assert not d.exists()

    def test_falls_back_to_unshare_on_permission_error(self, tmp_path):
        from unittest.mock import patch as _p
        from kanibako.runtime.container import remove_box_tree
        d = tmp_path / "box"
        d.mkdir()
        with _p("shutil.rmtree", side_effect=PermissionError("denied")), \
                _p("kanibako.runtime.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = True
            assert remove_box_tree(d) is True
            mock_rt.return_value.unshare_rm.assert_called_once_with(d)

    def test_false_when_unshare_fails_and_tree_remains(self, tmp_path):
        from unittest.mock import patch as _p
        from kanibako.runtime.container import remove_box_tree
        d = tmp_path / "box"
        d.mkdir()
        with _p("shutil.rmtree", side_effect=PermissionError("denied")), \
                _p("kanibako.runtime.container.ContainerRuntime") as mock_rt:
            mock_rt.return_value.unshare_rm.return_value = False
            assert remove_box_tree(d) is False
            assert d.exists()

    def test_purge_dir_still_delegates_here(self, tmp_path):
        """``_purge_dir`` is kept as a name (rm's call sites + tests read against it);
        the behaviour must be the moved body, not a second implementation."""
        from unittest.mock import patch as _p

        from kanibako.commands.box._parser import _purge_dir
        with _p("kanibako.runtime.container.remove_box_tree", return_value=True) as m:
            assert _purge_dir(tmp_path / "box") is True
            m.assert_called_once_with(tmp_path / "box")


class TestRunEnvFlags:
    """Test that run() emits -e flags from the env parameter."""

    def test_env_flags_emitted(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                env={"EDITOR": "vim", "NODE_ENV": "development"},
            )
            cmd = m.call_args[0][0]
            # env flags should appear as -e KEY=VALUE pairs
            assert "-e" in cmd
            idx_editor = cmd.index("EDITOR=vim")
            assert cmd[idx_editor - 1] == "-e"
            idx_node = cmd.index("NODE_ENV=development")
            assert cmd[idx_node - 1] == "-e"

    def test_env_none_no_flags(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                env=None,
            )
            cmd = m.call_args[0][0]
            assert "-e" not in cmd

    def test_env_empty_dict_no_flags(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                env={},
            )
            cmd = m.call_args[0][0]
            assert "-e" not in cmd


class TestDetachMode:
    """Test detach=True uses -dt (TTY for tmux) and omits --rm."""

    def test_detach_uses_dash_dt(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                detach=True,
            )
            cmd = m.call_args[0][0]
            assert "-dt" in cmd
            assert "-it" not in cmd
            assert "--rm" not in cmd
            # The persistent (detached) path maps the caller onto the image
            # agent user too — same contract as the ephemeral path; literal
            # oracle on purpose (see test_container_extended.test_base_flags).
            assert "--userns=keep-id:uid=1000,gid=1000" in cmd
            assert "--userns=keep-id" not in cmd  # plain form must be gone

    def test_interactive_uses_it_and_rm(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                detach=False,
            )
            cmd = m.call_args[0][0]
            # -it when TTY available, -i when not (e.g. CI)
            assert "-it" in cmd or "-i" in cmd
            assert "--rm" in cmd
            assert "-d" not in cmd

    def test_detach_captures_stdout_so_id_not_leaked(self):
        """detach=True captures stdout (container id stays off the terminal)."""
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(
                returncode=0, stdout="57c49acad8fb" * 4, stderr=""
            )
            rc = rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                detach=True,
            )
            assert rc == 0
            assert m.call_args.kwargs.get("capture_output") is True

    def test_interactive_does_not_capture_stdout(self):
        """detach=False stays foreground (no capture) so stdio is inherited."""
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rc = rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                detach=False,
            )
            assert rc == 0
            assert m.call_args.kwargs.get("capture_output") is not True

    def test_detach_propagates_failure_returncode(self):
        """A failed detached launch still propagates its non-zero return code."""
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(
                returncode=125, stdout="", stderr="Error: boom\n"
            )
            rc = rt.run(
                "test:latest",
                shell_path=Path("/tmp/shell"),
                project_path=Path("/tmp/project"),
                vault_ro_path=Path("/tmp/vault-ro"),
                vault_rw_path=Path("/tmp/vault-rw"),
                enable_vault=False,
                detach=True,
            )
            assert rc == 125


class TestRmAndIsRunning:
    """Test rm() and is_running() methods."""

    def test_rm_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.rm("mycontainer") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "rm", "mycontainer"]

    def test_rm_failure(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.rm("nonexistent") is False

    def test_is_running_true(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="true\n")
            assert rt.is_running("mycontainer") is True

    def test_is_running_false_stopped(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="false\n")
            assert rt.is_running("mycontainer") is False

    def test_is_running_false_not_found(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.is_running("nonexistent") is False


class TestInspectEnv:
    """Test inspect_env() — reads a container's recorded .Config.Env."""

    def test_returns_value_for_present_key(self):
        import json
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        env = json.dumps(["PATH=/usr/bin", "KANIBAKO_AGENT=claude", "TERM=xterm"])
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=env)
            assert rt.inspect_env("box", "KANIBAKO_AGENT") == "claude"
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "inspect",
                "--format", "{{json .Config.Env}}", "box",
            ]

    def test_returns_none_for_absent_key(self):
        import json
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        env = json.dumps(["PATH=/usr/bin", "TERM=xterm"])
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=env)
            assert rt.inspect_env("box", "KANIBAKO_AGENT") is None

    def test_returns_none_when_inspect_fails(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.inspect_env("nope", "KANIBAKO_AGENT") is None

    def test_returns_none_on_bad_json(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="not json")
            assert rt.inspect_env("box", "KANIBAKO_AGENT") is None

    def test_value_with_equals_sign_preserved(self):
        import json
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        env = json.dumps(["FOO=a=b=c"])
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout=env)
            assert rt.inspect_env("box", "FOO") == "a=b=c"


class TestExec:
    """Test exec() method."""

    def test_exec_basic_command(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m, \
             patch("kanibako.runtime.container.sys.stdin.isatty", return_value=True):
            m.return_value = MagicMock(returncode=0)
            rc = rt.exec("mycontainer", ["tmux", "attach", "-t", "kanibako"])
            assert rc == 0
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "exec", "-it",
                "mycontainer", "tmux", "attach", "-t", "kanibako",
            ]

    def test_exec_no_tty_when_not_a_terminal(self):
        """Without a stdin TTY (CI, scripts), use -i instead of -it.

        `-t` would cause interactive commands like tmux attach to render
        but never return.
        """
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m, \
             patch("kanibako.runtime.container.sys.stdin.isatty", return_value=False):
            m.return_value = MagicMock(returncode=0)
            rt.exec("mycontainer", ["echo", "hi"])
            cmd = m.call_args[0][0]
            assert cmd[2] == "-i"

    def test_exec_returns_exit_code(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=42)
            rc = rt.exec("mycontainer", ["false"])
            assert rc == 42

    def test_exec_with_env(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m, \
             patch("kanibako.runtime.container.sys.stdin.isatty", return_value=True):
            m.return_value = MagicMock(returncode=0)
            rt.exec("mycontainer", ["bash"], env={"FOO": "bar"})
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "exec", "-it",
                "-e", "FOO=bar",
                "mycontainer", "bash",
            ]


class TestExecReady:
    """Test exec_ready() — CAPTURED readiness probe for the interactive exec."""

    def test_exec_ready_true_when_rc_zero(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.exec_ready("mycontainer") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "exec", "mycontainer", "true"]

    def test_exec_ready_false_when_rc_nonzero(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.exec_ready("mycontainer") is False

    def test_exec_ready_captures_output_so_race_error_cannot_leak(self):
        """The probe MUST capture output, or podman's raw "container state
        improper" race line would leak to the user's TTY/stderr — the exact
        bug this method closes."""
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.exec_ready("mycontainer")
            assert m.call_args.kwargs.get("capture_output") is True


class TestContainerExists:
    """Test container_exists() method."""

    def test_exists_running(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.container_exists("mycontainer") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "inspect", "mycontainer"]

    def test_not_exists(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.container_exists("nonexistent") is False


class TestRunInteractive:
    """Test run_interactive() command construction."""

    def test_basic_command(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rc = rt.run_interactive("img:latest")
            assert rc == 0
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "run", "-it", "img:latest"]

    def test_with_container_name(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.run_interactive("img:latest", container_name="test-build")
            cmd = m.call_args[0][0]
            assert cmd == [
                "/usr/bin/podman", "run", "-it",
                "--name", "test-build", "img:latest",
            ]

    def test_returns_exit_code(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=42)
            assert rt.run_interactive("img:latest") == 42


class TestCommit:
    """Test commit() command construction."""

    def test_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stderr="")
            rt.commit("mycontainer", "myimage:latest")
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "commit", "mycontainer", "myimage:latest"]

    def test_failure_raises(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stderr="no such container")
            with pytest.raises(ContainerError, match="Failed to commit"):
                rt.commit("bad", "img")


class TestCpSaveLoadDiff:
    """Test cp(), save(), load(), diff() thin wrappers."""

    def test_cp_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.cp(Path("/x/y"), "ctr:/etc/") is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "cp", "/x/y", "ctr:/etc/"]

    def test_cp_failure(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.cp(Path("/x/y"), "ctr:/etc/") is False

    def test_save_success(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            assert rt.save("img", Path("/o.tar")) is True
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "save", "-o", "/o.tar", "img"]

    def test_save_failure(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1)
            assert rt.save("img", Path("/o.tar")) is False

    def test_load_success_returns_ref(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(
                returncode=0, stdout="Loaded image: repo/app:1.0\n",
            )
            assert rt.load(Path("/a.tar")) == "repo/app:1.0"
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "load", "-i", "/a.tar"]

    def test_load_success_image_id_form(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(
                returncode=0, stdout="Loaded image(s): ghcr.io/x/y:tag\n",
            )
            assert rt.load(Path("/a.tar")) == "ghcr.io/x/y:tag"

    def test_load_success_untagged_returns_empty(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="")
            assert rt.load(Path("/a.tar")) == ""

    def test_load_failure_returns_none(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.load(Path("/a.tar")) is None

    def test_diff_returns_lines(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="C /etc\nA /etc/foo\n")
            result = rt.diff("img")
            assert result == ["C /etc", "A /etc/foo"]
            cmd = m.call_args[0][0]
            assert cmd == ["/usr/bin/podman", "diff", "img"]

    def test_diff_empty_stdout(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0, stdout="")
            assert rt.diff("img") == []

    def test_diff_failure_returns_empty(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=1, stdout="")
            assert rt.diff("img") == []


class TestRebuildBuildArgs:
    """Test rebuild() passes --build-arg flags."""

    def test_with_build_args(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.rebuild(
                "kanibako-oci:latest",
                Path("/tmp/Containerfile"),
                Path("/tmp/context"),
                build_args={"BASE_IMAGE": "droste-fiber:latest"},
            )
            cmd = m.call_args[0][0]
            assert "--build-arg" in cmd
            idx = cmd.index("--build-arg")
            assert cmd[idx + 1] == "BASE_IMAGE=droste-fiber:latest"

    def test_without_build_args(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run") as m:
            m.return_value = MagicMock(returncode=0)
            rt.rebuild(
                "custom:latest",
                Path("/tmp/Containerfile"),
                Path("/tmp/context"),
            )
            cmd = m.call_args[0][0]
            assert "--build-arg" not in cmd


class TestPrecreateMountStubs:
    """Test _precreate_mount_stubs creates directory/file stubs for mounts."""

    def test_workspace_dir_always_created(self, tmp_path):
        from kanibako.runtime.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            enable_vault=False,
            vault_ro_path=tmp_path / "no-ro",
            vault_rw_path=tmp_path / "no-rw",
            tmpfs_masks=[],
        )
        assert (shell / "workspace").is_dir()
        # Vault disabled: no vault dest stubs are created.
        assert not (shell / "vault" / "ro").exists()
        assert not (shell / "vault" / "rw").exists()

    def test_vault_dirs_created_when_enabled(self, tmp_path):
        from kanibako.runtime.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        vault_ro = tmp_path / "vault-ro"
        vault_ro.mkdir()
        vault_rw = tmp_path / "vault-rw"
        vault_rw.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            enable_vault=True,
            vault_ro_path=vault_ro,
            vault_rw_path=vault_rw,
            tmpfs_masks=["/home/agent/workspace/vault"],
        )
        assert (shell / "vault" / "ro").is_dir()
        assert (shell / "vault" / "rw").is_dir()
        # The default vault mask box-dest maps to project_path / "vault"
        # (byte-identical to the old single-vault stub).
        assert (project / "vault").is_dir()

    def test_mask_stub_under_home(self, tmp_path):
        """A mask box-dest under ~/ (not workspace) maps under shell_path."""
        from kanibako.runtime.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        vault_ro = tmp_path / "vault-ro"
        vault_ro.mkdir()
        vault_rw = tmp_path / "vault-rw"
        vault_rw.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            enable_vault=True,
            vault_ro_path=vault_ro,
            vault_rw_path=vault_rw,
            tmpfs_masks=["/home/agent/.secret"],
        )
        assert (shell / ".secret").is_dir()

    def test_mask_stub_made_with_the_vault_disabled(self, tmp_path):
        """Mask stubs are made regardless of ``enable_vault`` — they pair with
        the emit, which no longer gates on it.

        Without the stub the tmpfs mount FAILS in LXC (the OCI runtime cannot
        mkdir the mountpoint inside a bind-mounted overlay), so re-nesting this
        loop under the vault arm would turn the repaired mask into a launch
        error on exactly the platform the stubs exist for.
        """
        from kanibako.runtime.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            enable_vault=False,
            vault_ro_path=tmp_path / "no-ro",
            vault_rw_path=tmp_path / "no-rw",
            tmpfs_masks=["/home/agent/.secret", "/home/agent/workspace/build"],
        )
        assert (shell / ".secret").is_dir()
        assert (project / "build").is_dir()
        # Still no vault stubs: the vault arm itself is untouched.
        assert not (shell / "vault" / "ro").exists()
        assert not (shell / "vault" / "rw").exists()

    def test_vault_dirs_created_even_when_source_missing(self, tmp_path):
        """Vault is UNIVERSAL unless disabled: the box-side dest stubs are made
        whenever vault is enabled, regardless of whether the host source exists
        (the resolver creates the source if missing)."""
        from kanibako.runtime.container import _precreate_mount_stubs
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        _precreate_mount_stubs(
            shell, project, None,
            enable_vault=True,
            vault_ro_path=tmp_path / "missing-ro",
            vault_rw_path=tmp_path / "missing-rw",
            tmpfs_masks=[],
        )
        assert (shell / "vault" / "ro").is_dir()
        assert (shell / "vault" / "rw").is_dir()

    @staticmethod
    def _canon_mounts(tmp_path):
        from dataclasses import dataclass

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        src_dir = tmp_path / "chapter"
        src_dir.mkdir(exist_ok=True)
        src_file = tmp_path / "index.md"
        src_file.touch()
        other = tmp_path / "other"
        other.mkdir(exist_ok=True)
        return [
            FakeMount(source=src_dir, destination="/home/agent/canon/bible/general"),
            FakeMount(source=src_file, destination="/home/agent/canon/COLLECTION.md"),
            FakeMount(source=src_dir, destination="/home/agent/canon/handbook/box"),
            # A NON-canon dest in the same call must still be stubbed — the skip is
            # narrow, not a general opt-out of stub pre-creation.
            FakeMount(source=other, destination="/home/agent/comms"),
        ]

    def test_existing_canon_mountpoints_are_left_alone(self, tmp_path):
        """⚑ J-7 MACHINERY EXCLUSION. Where the box-create skeleton HAS run, the canon
        mountpoints are root-owned and unwritable, so the launch path cannot manage
        them and must not try. Nothing under ``~/canon`` may be created or modified.
        """
        from kanibako.runtime.container import _precreate_mount_stubs

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()

        # Pre-create the skeleton mountpoints the four mounts land on, as create does.
        for rel in ("canon/bible/general", "canon/handbook/box"):
            (shell / rel).mkdir(parents=True)
        (shell / "canon" / "COLLECTION.md").touch()
        before = sorted(p.relative_to(shell) for p in (shell / "canon").rglob("*"))

        _precreate_mount_stubs(
            shell, project, self._canon_mounts(tmp_path),
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        after = sorted(p.relative_to(shell) for p in (shell / "canon").rglob("*"))
        assert after == before, "the launch path must not touch an existing skeleton"
        assert (shell / "comms").is_dir(), "non-canon stubs are unaffected"

    def test_absent_canon_mountpoints_ARE_stubbed(self, tmp_path):
        """⚑⚑ THE SKIP IS EXISTENCE-AWARE, NOT PATH-AWARE — and that is load-bearing.

        Skeleton-less boxes exist and keep arriving: every R1-era and pre-canon box,
        plus any box whose create hit the degraded path. An UNCONDITIONAL skip leaves
        their five-or-six canon binds with no mountpoints at all, and in LXC crun
        cannot mkdir inside a bind-mounted overlay — a launch failure (exit 126), not a
        degradation. Falling through to the tolerant stub helpers pre-creates exactly
        those mountpoints, which makes this a free self-healing migration.

        RED if the skip is made unconditional again.
        """
        from kanibako.runtime.container import _precreate_mount_stubs

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        assert not (shell / "canon").exists(), "a pre-R1b box home has no skeleton"

        _precreate_mount_stubs(
            shell, project, self._canon_mounts(tmp_path),
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        assert (shell / "canon" / "bible" / "general").is_dir()
        assert (shell / "canon" / "handbook" / "box").is_dir()
        assert (shell / "canon" / "COLLECTION.md").is_file()
        assert (shell / "comms").is_dir()

    def test_canon_dest_predicate_is_prefix_aware(self):
        """``~/canon-of-mine`` is NOT under ``~/canon``: a naive ``startswith``
        without the separator would wrongly skip a real bind's stub."""
        from kanibako.runtime.container import _is_managed_canon_dest

        assert _is_managed_canon_dest("/home/agent/canon")
        assert _is_managed_canon_dest("/home/agent/canon/bible/general")
        assert _is_managed_canon_dest("/home/agent/canon/COLLECTION.md")
        assert not _is_managed_canon_dest("/home/agent/canon-of-mine")
        assert not _is_managed_canon_dest("/home/agent/canonical")
        assert not _is_managed_canon_dest("/home/agent/workspace")

    def test_extra_dir_mount_under_home(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "comms-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/home/agent/comms")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        assert (shell / "comms").is_dir()

    def test_extra_file_mount_under_home(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_file = tmp_path / "claude-binary"
        src_file.touch()
        mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/claude")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        assert (shell / ".local" / "bin").is_dir()
        assert (shell / ".local" / "bin" / "claude").is_file()

    def test_extra_mount_under_workspace(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "vault-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/home/agent/workspace/vault")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        assert (project / "vault").is_dir()

    def test_mount_outside_home_skipped(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_dir = tmp_path / "opt-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/opt/kanibako/kanibako")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        # No dirs created under shell or project for /opt/ mounts
        assert list(shell.iterdir()) == [shell / "workspace"]

    def test_existing_file_not_overwritten(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Pre-existing file with content
        (shell / ".local" / "bin").mkdir(parents=True)
        existing = shell / ".local" / "bin" / "kanibako"
        existing.write_text("existing content")
        src_file = tmp_path / "entry"
        src_file.touch()
        mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/kanibako")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        # File stub should NOT overwrite existing content
        assert existing.read_text() == "existing content"

    def test_oserror_is_swallowed(self, tmp_path):
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src_file = tmp_path / "f"
        src_file.touch()
        # Make shell read-only so mkdir fails
        shell.chmod(0o444)
        try:
            mounts = [FakeMount(source=src_file, destination="/home/agent/deep/nested/file")]
            # Should not raise
            _precreate_mount_stubs(
                shell, project, mounts,
                enable_vault=False,
                vault_ro_path=tmp_path / "x",
                vault_rw_path=tmp_path / "y",
                tmpfs_masks=[],
            )
        finally:
            shell.chmod(0o755)

    def test_symlink_file_dest_cleared_to_real_mountpoint(self, tmp_path):
        """A baked symlink at a file dest is cleared so the bind lands clean.

        Mirrors a dirty image where ~/.local/bin/claude is a symlink into the
        install-dir subtree; the destination must become a real, non-symlink
        mountpoint before the bind.
        """
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Pre-existing dest symlink (as a baked image would ship).
        bin_dir = shell / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        link = bin_dir / "claude"
        link.symlink_to("/home/agent/.local/share/claude/versions/2.1.177")
        assert link.is_symlink()

        src_file = tmp_path / "claude-launcher"
        src_file.touch()
        mounts = [FakeMount(source=src_file, destination="/home/agent/.local/bin/claude")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        # Symlink cleared; dest is now a real, non-symlink file mountpoint.
        assert not link.is_symlink()
        assert link.is_file()

    def test_symlink_dir_dest_cleared_to_real_mountpoint(self, tmp_path):
        """A baked symlink at a dir dest is cleared to a real directory."""
        from dataclasses import dataclass
        from kanibako.runtime.container import _precreate_mount_stubs

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        share_parent = shell / ".local" / "share"
        share_parent.mkdir(parents=True)
        link = share_parent / "claude"
        target_dir = tmp_path / "real-elsewhere"
        target_dir.mkdir()
        link.symlink_to(target_dir)
        assert link.is_symlink()

        src_dir = tmp_path / "share-src"
        src_dir.mkdir()
        mounts = [FakeMount(source=src_dir, destination="/home/agent/.local/share/claude")]
        _precreate_mount_stubs(
            shell, project, mounts,
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        assert not link.is_symlink()
        assert link.is_dir()

    # --- Parent-dir traversal loosening (crun openat2 needs +x on every parent
    # of a bind dest; pre-existing box homes ship XDG dirs like ~/.config at
    # 0700, which crun cannot traverse -> exit-126 launch death). ---

    @staticmethod
    def _mount(destination, source):
        from dataclasses import dataclass

        @dataclass
        class FakeMount:
            source: Path
            destination: str
            options: str = ""

        return FakeMount(source=source, destination=destination)

    def test_private_parent_loosened_for_file_bind(self, tmp_path):
        """A file bind under a pre-existing 0700 parent -> parent becomes 0711."""
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Pre-existing private XDG dir (as gh/podman/XDG tools leave it).
        cfg = shell / ".config"
        cfg.mkdir()
        cfg.chmod(0o700)
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        _precreate_mount_stubs(
            shell, project,
            [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        import stat as _stat
        assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
        assert (shell / ".config" / "kanibako" / "kickoff.md").is_file()

    def test_multi_level_private_parents_all_loosened(self, tmp_path):
        """Both a 0700 grandparent and 0700 parent on the bind path loosen."""
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        cfg = shell / ".config"
        cfg.mkdir()
        sub = cfg / "kanibako"
        sub.mkdir()
        cfg.chmod(0o700)
        sub.chmod(0o700)
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        _precreate_mount_stubs(
            shell, project,
            [self._mount("/home/agent/.config/kanibako/seed/kickoff.md", src_file)],
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        import stat as _stat
        assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
        assert _stat.S_IMODE(sub.stat().st_mode) == 0o711
        assert (shell / ".config" / "kanibako" / "seed" / "kickoff.md").is_file()

    def test_private_dir_off_bind_path_untouched(self, tmp_path):
        """A 0700 dir with NO bind under it (e.g. ~/.ssh) stays 0700."""
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        ssh = shell / ".ssh"
        ssh.mkdir()
        ssh.chmod(0o700)
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        _precreate_mount_stubs(
            shell, project,
            [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        import stat as _stat
        # Off the bind path -> never visited -> mode preserved.
        assert _stat.S_IMODE(ssh.stat().st_mode) == 0o700

    def test_shell_path_root_never_chmodded(self, tmp_path):
        """shell_path itself is off-limits: it stays 0700 even as a bind root."""
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        cfg = shell / ".config"
        cfg.mkdir()
        cfg.chmod(0o700)
        shell.chmod(0o700)
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        try:
            _precreate_mount_stubs(
                shell, project,
                [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
                enable_vault=False,
                vault_ro_path=tmp_path / "x",
                vault_rw_path=tmp_path / "y",
                tmpfs_masks=[],
            )
            import stat as _stat
            # The walk stops AT the root; root's own mode is untouched.
            assert _stat.S_IMODE(shell.stat().st_mode) == 0o700
            # ...while a private parent BELOW the root is still loosened.
            assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
        finally:
            shell.chmod(0o755)

    def test_symlinked_parent_stops_walk_target_untouched(self, tmp_path):
        """A symlinked parent halts the walk; the symlink target is not chmodded."""
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # A private dir the .config symlink points at, INSIDE the box home.
        target = shell / "realtarget"
        target.mkdir()
        target.chmod(0o700)
        link = shell / ".config"
        link.symlink_to("realtarget")
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        _precreate_mount_stubs(
            shell, project,
            [self._mount("/home/agent/.config/kickoff.md", src_file)],
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        import stat as _stat
        # Walk stops at the symlinked parent -> the target dir keeps its 0700 mode.
        assert link.is_symlink()
        assert _stat.S_IMODE(target.stat().st_mode) == 0o700

    def test_loosening_logged_at_info(self, tmp_path, caplog):
        """The permission change is discoverable in the logs at INFO."""
        import logging
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        cfg = shell / ".config"
        cfg.mkdir()
        cfg.chmod(0o700)
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        with caplog.at_level(logging.INFO, logger="kanibako.container"):
            _precreate_mount_stubs(
                shell, project,
                [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
                enable_vault=False,
                vault_ro_path=tmp_path / "x",
                vault_rw_path=tmp_path / "y",
                tmpfs_masks=[],
            )
        assert any(
            "loosened box-home dir for bind traversal" in r.getMessage()
            for r in caplog.records
        )

    def test_already_traversable_parent_not_rechmodded(self, tmp_path, caplog):
        """A parent that already has search bits is left alone: no chmod, no log.

        Guards the ``perm & 0o011 != 0o011`` idempotence gate — a fresh-box
        0755 XDG dir must not be spuriously re-chmodded (and mode stays 0755, not
        widened) nor emit the loosen INFO line.
        """
        import logging
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        cfg = shell / ".config"
        cfg.mkdir()
        cfg.chmod(0o755)  # already traversable (fresh-box mkdir default).
        src_file = tmp_path / "kickoff-src"
        src_file.touch()
        from kanibako.runtime.container import _precreate_mount_stubs
        with caplog.at_level(logging.INFO, logger="kanibako.container"):
            _precreate_mount_stubs(
                shell, project,
                [self._mount("/home/agent/.config/kanibako/kickoff.md", src_file)],
                enable_vault=False,
                vault_ro_path=tmp_path / "x",
                vault_rw_path=tmp_path / "y",
                tmpfs_masks=[],
            )
        import stat as _stat
        # Mode is untouched (not widened past its existing search bits)...
        assert _stat.S_IMODE(cfg.stat().st_mode) == 0o755
        # ...and no loosen was logged, since nothing was chmodded.
        assert not any(
            "loosened box-home dir for bind traversal" in r.getMessage()
            for r in caplog.records
        )

    def test_dir_bind_under_private_parent_loosened(self, tmp_path):
        """A DIR bind (not just a file bind) under a 0700 parent loosens it too.

        ``_ensure_dir`` and ``_ensure_file`` share the loosen call, but the dir
        branch is otherwise untested; a dir-source extra mount must also make its
        private parent traversable.
        """
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        cfg = shell / ".config"
        cfg.mkdir()
        cfg.chmod(0o700)
        src_dir = tmp_path / "seed-dir"
        src_dir.mkdir()
        from kanibako.runtime.container import _precreate_mount_stubs
        _precreate_mount_stubs(
            shell, project,
            [self._mount("/home/agent/.config/kanibako/seed", src_dir)],
            enable_vault=False,
            vault_ro_path=tmp_path / "x",
            vault_rw_path=tmp_path / "y",
            tmpfs_masks=[],
        )
        import stat as _stat
        assert _stat.S_IMODE(cfg.stat().st_mode) == 0o711
        assert (shell / ".config" / "kanibako" / "seed").is_dir()


class TestDetectShadowedMounts:
    """Test detect_shadowed_mounts: pure detection of binds shadowing content."""

    @staticmethod
    def _mount(source, destination, options=""):
        from kanibako.targets.base import Mount
        return Mount(source=source, destination=destination, options=options)

    def test_vault_dir_shadow(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # rw vault stub has content; ro is empty (not stubbed at all).
        (shell / "vault" / "rw").mkdir(parents=True)
        (shell / "vault" / "rw" / "somefile").write_text("data")
        result = detect_shadowed_mounts(shell, project, None, enable_vault=True)
        assert "/home/agent/vault/rw" in result
        assert "/home/agent/vault/ro" not in result

    def test_empty_first_launch(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        result = detect_shadowed_mounts(shell, project, None, enable_vault=True)
        assert result == []

    def test_file_dest_shadow_nonempty_reported(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        host_stub = shell / ".local" / "bin" / "foo"
        host_stub.parent.mkdir(parents=True)
        host_stub.write_text("x")  # >0 bytes
        src = tmp_path / "src-foo"
        src.touch()
        mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert "/home/agent/.local/bin/foo" in result

    def test_file_dest_zero_byte_not_reported(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        host_stub = shell / ".local" / "bin" / "foo"
        host_stub.parent.mkdir(parents=True)
        host_stub.touch()  # 0 bytes
        src = tmp_path / "src-foo"
        src.touch()
        mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_base_roots_excluded(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Give the host home + workspace bases content; they must NOT be warned.
        (shell / "existing").write_text("home content")
        (project / "existing").write_text("workspace content")
        src = tmp_path / "src"
        src.mkdir()
        mounts = [
            self._mount(src, "/home/agent"),
            self._mount(src, "/home/agent/workspace"),
        ]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_symlink_skipped(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        # Host stub is a symlink (a precreate-cleared stub, not user content).
        bin_dir = shell / ".local" / "bin"
        bin_dir.mkdir(parents=True)
        link = bin_dir / "foo"
        target = tmp_path / "elsewhere"
        target.write_text("data")
        link.symlink_to(target)
        src = tmp_path / "src-foo"
        src.touch()
        mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_pure_no_paths_created(self, tmp_path):
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        src = tmp_path / "src-foo"
        src.touch()
        mounts = [self._mount(src, "/home/agent/.local/bin/foo")]
        before = set(shell.rglob("*")) | set(project.rglob("*"))
        detect_shadowed_mounts(shell, project, mounts, enable_vault=True)
        after = set(shell.rglob("*")) | set(project.rglob("*"))
        # No mkdir/touch: the candidate stub stays non-existent.
        assert not (shell / ".local" / "bin" / "foo").exists()
        assert not (shell / "vault").exists()
        assert before == after

    def test_managed_canon_bible_excluded(self, tmp_path):
        """Finding #7: the box-create skeleton owns canon/bible — never report it shadowed."""
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        chapter = shell / "canon" / "bible" / "general"
        chapter.mkdir(parents=True)
        (chapter / "existing.md").write_text("pre-existing skeleton content")
        src = tmp_path / "src-general"
        src.mkdir()
        mounts = [self._mount(src, "/home/agent/canon/bible/general")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_managed_canon_handbook_excluded(self, tmp_path):
        """Same guard, handbook side (canon/handbook is skeleton-owned, not seed-owned)."""
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        chapter = shell / "canon" / "handbook" / "box"
        chapter.mkdir(parents=True)
        (chapter / "existing.md").write_text("pre-existing skeleton content")
        src = tmp_path / "src-box"
        src.mkdir()
        mounts = [self._mount(src, "/home/agent/canon/handbook/box")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_managed_canon_collection_file_excluded(self, tmp_path):
        """The single-file managed dest (canon/COLLECTION.md) is excluded too."""
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        (shell / "canon").mkdir()
        host_stub = shell / "canon" / "COLLECTION.md"
        host_stub.write_text("pre-existing skeleton content")
        src = tmp_path / "src-collection"
        src.touch()
        mounts = [self._mount(src, "/home/agent/canon/COLLECTION.md")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert result == []

    def test_canon_notebook_still_reported(self, tmp_path):
        """MUTATION PROOF (other direction): canon/notebook stays SEEDABLE (spec §2c), so a
        genuine shadow there is real user content and must still be reported — this is what
        would break if the exclusion were widened to all of ``~/canon`` instead of the
        seed-deny prefixes.
        """
        from kanibako.runtime.container import detect_shadowed_mounts
        shell = tmp_path / "shell"
        shell.mkdir()
        project = tmp_path / "project"
        project.mkdir()
        notebook = shell / "canon" / "notebook"
        notebook.mkdir(parents=True)
        (notebook / "existing.md").write_text("real user content")
        src = tmp_path / "src-notebook"
        src.mkdir()
        mounts = [self._mount(src, "/home/agent/canon/notebook")]
        result = detect_shadowed_mounts(shell, project, mounts, enable_vault=False)
        assert "/home/agent/canon/notebook" in result


class TestLocalImageMetadata:
    """get_local_created / get_local_tags / get_local_label (via image_inspect)."""

    @staticmethod
    def _inspect(data):
        import json
        from unittest.mock import MagicMock
        m = MagicMock(returncode=0, stdout=json.dumps([data]))
        return patch("kanibako.runtime.container.subprocess.run", return_value=m)

    def test_get_local_created(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"Created": "2026-06-01T00:00:00Z"}):
            assert rt.get_local_created("img:latest") == "2026-06-01T00:00:00Z"

    def test_get_local_created_missing(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({}):
            assert rt.get_local_created("img:latest") is None

    def test_get_local_created_empty_string(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"Created": ""}):
            assert rt.get_local_created("img:latest") is None

    def test_get_local_created_inspect_fail(self):
        from unittest.mock import MagicMock
        rt = ContainerRuntime(command="echo")
        with patch(
            "kanibako.runtime.container.subprocess.run",
            return_value=MagicMock(returncode=1, stdout=""),
        ):
            assert rt.get_local_created("img:latest") is None

    def test_get_local_tags(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"RepoTags": ["img:latest", "img:1.6.0"]}):
            assert rt.get_local_tags("img:latest") == ["img:latest", "img:1.6.0"]

    def test_get_local_tags_none(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"RepoTags": None}):
            assert rt.get_local_tags("img:latest") == []

    def test_get_local_label_from_config(self):
        rt = ContainerRuntime(command="echo")
        data = {"Config": {"Labels": {"org.opencontainers.image.version": "1.6.0"}}}
        with self._inspect(data):
            assert rt.get_local_label(
                "img:latest", "org.opencontainers.image.version"
            ) == "1.6.0"

    def test_get_local_label_top_level_fallback(self):
        rt = ContainerRuntime(command="echo")
        data = {"Config": {}, "Labels": {"x": "y"}}
        with self._inspect(data):
            assert rt.get_local_label("img:latest", "x") == "y"

    def test_get_local_label_absent(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"Config": {"Labels": {}}}):
            assert rt.get_local_label("img:latest", "nope") is None

    def test_get_local_label_no_labels(self):
        rt = ContainerRuntime(command="echo")
        with self._inspect({"Config": {}}):
            assert rt.get_local_label("img:latest", "x") is None


class TestGuestDestToHost:
    """The single guest_dest -> host-path translator (audit P3 unification).

    Equivalence: the shared ``_guest_dest_to_host`` returns the SAME host path as
    each of the three former sites for the four cases, plus the ``map_home_root``
    fork (mount-stub callers vs seed/synced COPY callers) and the ``/workspace``
    split the copy callers now inherit.
    """

    def _paths(self):
        shell = Path("/host/shell")
        project = Path("/host/project")
        return shell, project

    def test_home_root_mount_callers_return_none(self):
        """map_home_root=False (mount stub/shadow callers): bare home -> None.

        Byte-identical to the former ``_mount_dest_to_host``: the base home bind
        is not a stub to pre-create.
        """
        from kanibako.runtime.container import _guest_dest_to_host
        shell, project = self._paths()
        assert _guest_dest_to_host("/home/agent", shell, project) is None
        assert _guest_dest_to_host("/home/agent/", shell, project) == shell

    def test_home_root_copy_callers_map_to_shell(self):
        """map_home_root=True (seed/synced COPY callers): bare home -> shell root.

        Byte-identical to the former ``_host_dest`` / inline synced branch.
        """
        from kanibako.runtime.container import _guest_dest_to_host
        shell, project = self._paths()
        assert _guest_dest_to_host(
            "/home/agent", shell, project, map_home_root=True
        ) == shell
        assert _guest_dest_to_host(
            "/home/agent/", shell, project, map_home_root=True
        ) == shell

    def test_home_subpath_maps_under_shell(self):
        """A ~/x dest maps under shell_path in every mode (all three former sites
        agreed)."""
        from kanibako.runtime.container import _guest_dest_to_host
        shell, project = self._paths()
        for kw in ({}, {"map_home_root": True}):
            assert _guest_dest_to_host(
                "/home/agent/.claude", shell, project, **kw
            ) == shell / ".claude"

    def test_workspace_subpath_maps_under_project(self):
        """A ~/workspace/x dest maps under project_path (the workspace bind).

        The canonical mount behavior the COPY callers now inherit (P3 fix): both
        modes route ~/workspace/x to project_path/x, NOT the shadowed
        shell_path/workspace/x.
        """
        from kanibako.runtime.container import _guest_dest_to_host
        shell, project = self._paths()
        for kw in ({}, {"map_home_root": True}):
            assert _guest_dest_to_host(
                "/home/agent/workspace/proj/f", shell, project, **kw
            ) == project / "proj" / "f"

    def test_outside_home_returns_none(self):
        """A dest outside the box home returns None (the skip case)."""
        from kanibako.runtime.container import _guest_dest_to_host
        shell, project = self._paths()
        assert _guest_dest_to_host("/etc/passwd", shell, project) is None
        assert _guest_dest_to_host(
            "/etc/passwd", shell, project, map_home_root=True
        ) is None
