"""A plain ``box rm`` never deregisters a running box (his 171-RM Q1 = (d)).

The refusal is the one pre-flight every destructive verb uses (``abort_if_box_running``
via ``_refuse_purge_while_live``); it fires before anything is printed or changed, names
``kanibako stop <box>``, and ``--force`` does NOT override it (his 172-RF: "forcing breaks
things"); it still overrides the same refusal for ``--purge``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    monkeypatch.setattr("kanibako.commands.start.seed_new_box",
                        lambda std, config, proj, **kw: None)


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(config_file))


def _create(path: Path, *, standalone: bool = False) -> None:
    from kanibako.commands.box._parser import run_create

    path.mkdir(parents=True, exist_ok=True)
    assert run_create(argparse.Namespace(
        path=str(path), standalone=standalone, no_vault=False, name=None, image=None,
        agent=None, allow_home=False, register=standalone, recover=False,
    )) == 0


def _rm(target: str, *, force: bool = False, purge: bool = False) -> int:
    from kanibako.commands.box._parser import run_rm

    return run_rm(argparse.Namespace(target=target, purge=purge, force=force))


def _runtime_says(monkeypatch, running: bool, asked: list | None = None):
    from kanibako.commands.box import _parser as P

    def _probe(proj, fail_closed=False, container_name=None):
        if asked is not None:
            asked.append(container_name)
        return (True, "Up 3 minutes") if running else (False, "exited")
    monkeypatch.setattr(P, "_check_container_running", _probe)


def _primary_state(std):
    from kanibako.project import registry_store
    from kanibako.settings.paths import BoxMode, _early_scope, load_primary_boxes

    return (load_primary_boxes(std.primary_workset, early=_early_scope(std, BoxMode.primary)),
            registry_store.load_deregistered(std.registry))


class TestPrimary:
    def test_a_running_primary_is_refused_and_stays_registered(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "proj")
        std = _std(config_file)
        asked: list = []
        _runtime_says(monkeypatch, True, asked)
        capsys.readouterr()

        rc = _rm("proj")
        out, err = capsys.readouterr()
        boxes, parked = _primary_state(std)
        assert rc == 1, err
        assert asked == ["kb-primary-proj"], asked
        assert "may be running" in err and "cannot be deregistered" in err, err
        assert "kanibako stop proj" in err, err
        assert "Removing project" not in out, out
        assert "proj" in boxes and "proj" not in parked

    def test_force_does_not_deregister_a_running_primary(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "proj")
        std = _std(config_file)
        _runtime_says(monkeypatch, True)
        capsys.readouterr()

        rc = _rm("proj", force=True)
        out, err = capsys.readouterr()
        boxes, parked = _primary_state(std)
        assert rc == 1, err
        assert "Error: this box may be running" in err, err
        assert "kanibako stop proj" in err and "--force" not in err, err
        assert "Removing project" not in out, out
        assert "proj" in boxes and "proj" not in parked

    def test_force_still_overrides_the_refusal_for_a_purge(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """Out of scope of 172-RF: ``--purge --force`` keeps its override, as before."""
        _create(tmp_home / "proj")
        std = _std(config_file)
        _runtime_says(monkeypatch, True)

        assert _rm("proj", force=True, purge=True) == 0
        assert "or pass --force" in capsys.readouterr().err
        boxes, _parked = _primary_state(std)
        assert "proj" not in boxes

    def test_a_stopped_primary_is_deregistered(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "proj")
        std = _std(config_file)
        _runtime_says(monkeypatch, False)

        assert _rm("proj") == 0
        boxes, parked = _primary_state(std)
        assert "proj" not in boxes and "proj" in parked

    def test_a_held_lock_with_no_container_is_refused(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        import fcntl

        _create(tmp_home / "proj")
        std = _std(config_file)
        _runtime_says(monkeypatch, False)
        lock = std.boxes / "proj" / ".kanibako.lock"
        with open(lock, "w") as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            rc = _rm("proj")
        err = capsys.readouterr().err
        boxes, _parked = _primary_state(std)
        assert rc == 1 and "holds its lock file" in err, err
        assert "stale lock" not in err, err
        assert "proj" in boxes

    def test_a_lock_the_runtime_cannot_answer_for_names_the_stale_lock_cure(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """``kanibako stop`` finds no container here, so the refusal names stop's own lock cure."""
        from kanibako.commands.box import _parser as P
        from kanibako.errors import ContainerError

        _create(tmp_home / "proj")
        std = _std(config_file)

        def _unanswered(proj, fail_closed=False, container_name=None):
            raise ContainerError("runtime not found")
        monkeypatch.setattr(P, "_check_container_running", _unanswered)
        lock = std.boxes / "proj" / ".kanibako.lock"
        lock.touch()
        rc = _rm("proj", force=True)
        err = capsys.readouterr().err
        assert rc == 1 and "a lock file exists and the container runtime" in err, err
        # Stop's own lines, verbatim (``stop.stale_lock_cure``).
        assert ("If a stale lock file is blocking a new session, remove it manually:\n"
                f"  rm {lock}") in err, err
        assert "proj" in _primary_state(std)[0]


class TestStandalone:
    def _registered(self, std) -> dict:
        from kanibako.project import registry_store

        return registry_store.load_standalone(std.registry)

    def test_a_running_standalone_is_refused_and_stays_registered(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "sa", standalone=True)
        std = _std(config_file)
        (name,) = self._registered(std)
        _runtime_says(monkeypatch, True)
        capsys.readouterr()

        rc = _rm(name)
        out, err = capsys.readouterr()
        assert rc == 1, err
        assert "cannot be deregistered" in err and "kanibako stop" in err, err
        assert "Removing standalone box" not in out, out
        assert name in self._registered(std)

    def test_force_does_not_deregister_a_running_standalone(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "sa", standalone=True)
        std = _std(config_file)
        (name,) = self._registered(std)
        _runtime_says(monkeypatch, True)
        capsys.readouterr()

        rc = _rm(name, force=True)
        out, err = capsys.readouterr()
        assert rc == 1, err
        assert "cannot be deregistered" in err and "--force" not in err, err
        assert "Removing standalone box" not in out, out
        assert name in self._registered(std)

    def test_force_does_not_wave_on_a_failed_lookup(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.commands.box import _parser as P

        _create(tmp_home / "sa", standalone=True)
        std = _std(config_file)
        (name,) = self._registered(std)
        _runtime_says(monkeypatch, False)

        def _boom(*_a, **_k):
            raise RuntimeError("unresolvable")
        monkeypatch.setattr(P, "_resolve_purge_probe_target", _boom)
        capsys.readouterr()

        rc = _rm(name, force=True)
        err = capsys.readouterr().err
        assert rc == 1 and "does not resolve" in err and "--force" not in err, err
        assert name in self._registered(std)

    def test_a_failed_lookup_is_never_not_running(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """The probe target does not resolve and no lock is on disk: refused, not waved on."""
        from kanibako.commands.box import _parser as P

        _create(tmp_home / "sa", standalone=True)
        std = _std(config_file)
        (name,) = self._registered(std)
        _runtime_says(monkeypatch, False)

        def _boom(*_a, **_k):
            raise RuntimeError("unresolvable")
        monkeypatch.setattr(P, "_resolve_purge_probe_target", _boom)
        capsys.readouterr()

        rc = _rm(name)
        err = capsys.readouterr().err
        assert rc == 1 and "does not resolve" in err, err
        assert name in self._registered(std)

    def test_a_stopped_standalone_is_deregistered(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        _create(tmp_home / "sa", standalone=True)
        std = _std(config_file)
        (name,) = self._registered(std)
        _runtime_says(monkeypatch, False)

        assert _rm(name) == 0
        assert name not in self._registered(std)
