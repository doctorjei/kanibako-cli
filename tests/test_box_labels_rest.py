"""system-design § Box and helper identity: "All box-reaching verbs find it via the labels
(not by container name)" -- the rest of the verbs: ``stop --all``, ``box list``, the
primary rm/purge pre-flights, ``box purge --all``, a workset member that does not
resolve, and the helper hub.  An unlabeled container is still found by its name, and a
label query the runtime fails is never "not running" for a destructive verb."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from unittest.mock import MagicMock

import pytest

from kanibako.project.workset import add_project, create_workset
from kanibako.runtime.container import ContainerRuntime
from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    WorksetSpec, load_std_paths, resolve_project, resolve_workset_project)

_FAKE = '''
import json, os, sys
cfg = json.load(open(os.environ["FAKE_RT_CFG"]))
args = sys.argv[1:]
with open(cfg["log"], "a") as f:
    f.write(" ".join(args) + "\\n")
filters = [args[i + 1] for i, a in enumerate(args) if a == "--filter"]
labels = sorted(f[len("label="):] for f in filters if f.startswith("label="))
if args[0] == "ps":
    if labels:
        if cfg.get("label_fail"):
            sys.stderr.write("label query broke\\n")
            sys.exit(125)
        for name in cfg["labeled"].get(",".join(labels), []):
            print(name)
        sys.exit(0)
    prefix = next((f[len("name="):] for f in filters if f.startswith("name=")), "")
    for name in cfg["running"]:
        if name.startswith(prefix):
            print(name + "\\timg:tag\\tUp 1 minute")
    sys.exit(0)
if args[0] == "inspect":
    name = args[-1]
    if "--format" in args:
        print("true" if name in cfg["running"] else "false")
    sys.exit(0 if name in cfg["running"] else 1)
sys.exit(0)
'''


def _runtime(monkeypatch, tmp_path, *, running=(), labeled=None, label_fail=False):
    """A stand-in container CLI: ``ps`` with label filters answers *labeled* (keyed by
    the sorted filters, comma-joined) or fails (*label_fail*); without, it lists
    *running*.  Returns its call log."""
    log = tmp_path / "rt.log"
    log.write_text("")
    cfg = tmp_path / "rt.json"
    cfg.write_text(json.dumps({"running": list(running), "labeled": labeled or {},
                               "label_fail": label_fail, "log": str(log)}))
    script = tmp_path / "fake-rt.py"
    script.write_text(_FAKE)
    stub = tmp_path / "fake-rt"
    stub.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -I "
                    f"{shlex.quote(str(script))} \"$@\"\n")
    stub.chmod(0o755)
    monkeypatch.setenv("KANIBAKO_DOCKER_CMD", str(stub))
    monkeypatch.setenv("FAKE_RT_CFG", str(cfg))
    return log


def box_container_named(*a, **kw):
    from kanibako.utils import box_container_named as real
    return real(*a, **kw)


def _label_query_error(msg):
    from kanibako.errors import LabelQueryError
    return LabelQueryError(msg)


def _key(box, workset):
    return f"kanibako.box={box},kanibako.workset={workset}"


@pytest.fixture
def world(config_file, tmp_home):
    """Two primaries (``pk``, ``pq``) and a named member ``paws/bar``, each with data."""
    config = load_config(config_file)
    std = load_std_paths(config)
    boxes = {}
    for name in ("pk", "pq"):
        d = tmp_home / name
        d.mkdir()
        boxes[name] = resolve_project(std, config, project_dir=str(d), initialize=True)
    ws = create_workset("paws", tmp_home / "worksets" / "paws", std)
    src = tmp_home / "bar_src"
    src.mkdir()
    add_project(ws, "bar", src)
    boxes["bar"] = resolve_workset_project(WorksetSpec.from_workset(ws), "bar", std, config,
                                           initialize=True)
    for proj in boxes.values():
        (proj.metadata_path / "data.txt").write_text("keep me")
    return std, boxes


def _no_question(monkeypatch):
    def _asked(*_a, **_k):
        raise AssertionError("the purge asked its question despite a live box")
    monkeypatch.setattr("kanibako.utils.confirm_prompt", _asked)


def _purge_all(force=False):
    from kanibako.commands.clean import run
    return run(argparse.Namespace(all_projects=True, force=force, path=None))


def _intact(boxes):
    for name, proj in boxes.items():
        assert (proj.metadata_path / "data.txt").is_file(), f"{name}: box tree touched"


class TestBoxContainerNamed:
    def test_found_by_its_pair_of_labels(self):
        rt = MagicMock()
        rt.find_labeled.return_value = ["kb-primary-old"]
        assert box_container_named(rt, "pk", "primary") == "kb-primary-old"
        assert rt.find_labeled.call_args.args[0] == {
            "kanibako.box": "pk", "kanibako.workset": "primary"}
        assert rt.find_labeled.call_args.kwargs == {"without": "kanibako.helper",
                                                    "check": False}

    def test_an_unlabeled_container_is_found_by_its_name(self):
        rt = MagicMock()
        rt.find_labeled.return_value = []
        assert box_container_named(rt, "pk", "primary") == "kb-primary-pk"

    def test_a_box_that_renders_no_name_has_none(self):
        rt = MagicMock()
        assert box_container_named(rt, "Bad Name!", "primary") is None
        rt.find_labeled.assert_not_called()


class TestFindLabeledStrict:
    def _rt(self, monkeypatch, tmp_path, **kw):
        _runtime(monkeypatch, tmp_path, **kw)
        return ContainerRuntime()

    def test_a_failed_query_raises_with_check(self, monkeypatch, tmp_path):
        rt = self._rt(monkeypatch, tmp_path, label_fail=True)
        assert rt.find_labeled({"kanibako.box": "pk"}) == []
        from kanibako.errors import LabelQueryError

        with pytest.raises(LabelQueryError, match="label query broke"):
            rt.find_labeled({"kanibako.box": "pk"}, check=True)

    def test_a_none_value_filters_by_the_key_alone(self, monkeypatch, tmp_path):
        rt = self._rt(monkeypatch, tmp_path, labeled={"kanibako.box": ["kb-x"]})
        assert rt.find_labeled({"kanibako.box": None}) == ["kb-x"]

    def test_no_labels_answers_none_and_asks_nothing(self, monkeypatch, tmp_path):
        log = _runtime(monkeypatch, tmp_path, running=["kb-x"])
        assert ContainerRuntime().find_labeled({}) == []
        assert log.read_text() == ""


class TestPurgeAll:
    def test_a_primary_running_under_another_name_refuses(
            self, world, monkeypatch, tmp_path, capsys):
        _std, boxes = world
        _runtime(monkeypatch, tmp_path, running=["kb-primary-moved"],
                 labeled={_key("pk", "primary"): ["kb-primary-moved"]})
        _no_question(monkeypatch)
        assert _purge_all() == 1
        err = capsys.readouterr().err
        assert "kb-primary-moved" in err, err
        _intact(boxes)

    def test_a_member_running_under_another_name_refuses(
            self, world, monkeypatch, tmp_path, capsys):
        _std, boxes = world
        _runtime(monkeypatch, tmp_path, running=["kb-paws-moved"],
                 labeled={_key("bar", "paws"): ["kb-paws-moved"]})
        _no_question(monkeypatch)
        assert _purge_all() == 1
        assert "kb-paws-moved" in capsys.readouterr().err
        _intact(boxes)

    def test_a_failed_label_query_refuses_with_no_lock(
            self, world, monkeypatch, tmp_path, capsys):
        _std, boxes = world
        _runtime(monkeypatch, tmp_path, label_fail=True)
        _no_question(monkeypatch)
        assert _purge_all() == 1
        err = capsys.readouterr().err
        assert "could not tell which container is the box's" in err, err
        _intact(boxes)

    def test_an_unlabeled_container_is_still_found_by_its_name(
            self, world, monkeypatch, tmp_path, capsys):
        _std, boxes = world
        _runtime(monkeypatch, tmp_path, running=["kb-primary-pk"])
        _no_question(monkeypatch)
        assert _purge_all() == 1
        assert "kb-primary-pk" in capsys.readouterr().err
        _intact(boxes)


class TestPrimaryPreflights:
    def test_box_rm_refuses_a_primary_running_under_another_name(
            self, world, monkeypatch, tmp_path, capsys):
        from kanibako.commands.box._parser import run_rm

        std, _boxes = world
        _runtime(monkeypatch, tmp_path, running=["kb-primary-moved"],
                 labeled={_key("pk", "primary"): ["kb-primary-moved"]})
        rc = run_rm(argparse.Namespace(target="pk", box=None, purge=False, force=False))
        assert rc == 1
        assert "kb-primary-moved" in capsys.readouterr().err

    def test_a_parked_primary_purge_refuses_one_running_under_another_name(
            self, config_file, tmp_home, monkeypatch, tmp_path, capsys):
        from kanibako.commands.box._parser import run_rm
        from kanibako.project import registry_store

        std = load_std_paths(load_config(config_file))
        md = std.boxes / "bP1"
        md.mkdir(parents=True)
        (md / "keep.txt").write_text("must survive")
        ws = tmp_home / "bP1_ws"
        ws.mkdir()
        registry_store.register_deregistered(std.registry, "bP1", kind="primary",
                                             workspace=str(ws), metadata=str(md))
        _runtime(monkeypatch, tmp_path, running=["kb-primary-moved"],
                 labeled={_key("bP1", "primary"): ["kb-primary-moved"]})
        rc = run_rm(argparse.Namespace(target="bP1", box=None, purge=True, force=False))
        assert rc == 1
        assert "kb-primary-moved" in capsys.readouterr().err
        assert (md / "keep.txt").is_file()


class TestUnresolvedMember:
    def test_an_unresolvable_member_is_asked_by_its_labels(
            self, tmp_path, monkeypatch):
        from kanibako.commands import workset_cmd

        _runtime(monkeypatch, tmp_path, running=["kb-paws-moved"],
                 labeled={_key("bar", "paws"): ["kb-paws-moved"]})
        boxes_dir = tmp_path / "boxes"
        (boxes_dir / "bar").mkdir(parents=True)
        assert workset_cmd._member_running(None, "paws", boxes_dir, "bar", "It would.",
                                           ws=None)

    def test_a_failed_label_query_is_never_not_running(self, tmp_path, monkeypatch):
        from kanibako.commands.box._lifecycle import box_running_reason

        _runtime(monkeypatch, tmp_path, label_fail=True)
        reason = box_running_reason(None, tmp_path / "absent.lock", box_ref=("bar", "paws"))
        assert reason is not None and "label query broke" in reason


class TestBoxList:
    def test_a_box_running_under_another_name_lists_active(
            self, world, monkeypatch, tmp_path, capsys):
        from kanibako.commands.box._parser import run_list

        _runtime(monkeypatch, tmp_path, running=["kb-primary-moved", "kb-paws-moved"],
                 labeled={_key("pk", "primary"): ["kb-primary-moved"],
                          _key("bar", "paws"): ["kb-paws-moved"]})
        assert run_list(argparse.Namespace(active=True)) == 0
        out = capsys.readouterr().out
        assert "pk " in out and "bar " in out, out
        assert "pq " not in out, out

    @pytest.mark.parametrize("label_fail", [False, True])
    def test_idle_asks_once_not_per_box(self, world, monkeypatch, tmp_path, capsys,
                                        label_fail):
        """Nothing runs: the ``kb-`` listing plus ONE any-box label query, however many
        boxes; a failed query reads every box stopped, as the per-box query would."""
        from kanibako.commands.box._parser import run_list

        log = _runtime(monkeypatch, tmp_path, label_fail=label_fail)
        assert run_list(argparse.Namespace()) == 0
        out = capsys.readouterr().out
        for name in ("pk", "pq", "bar"):
            assert name in out, out
        assert "active" not in out, out
        calls = log.read_text().splitlines()
        assert len(calls) == 2, calls
        assert "label=kanibako.box" in calls[1].split(), calls


class TestStopAll:
    @pytest.fixture(autouse=True)
    def _no_registry(self, monkeypatch):
        monkeypatch.setattr("kanibako.commands.stop._boxes_rendering_no_name", lambda: [])

    def _rt(self):
        rt = MagicMock()
        rt.list_running.return_value = [("kb-primary-a", "img", "Up")]
        rt.is_running.return_value = True
        rt.stop.return_value = True
        rt.container_exists.return_value = False
        return rt

    def test_a_labeled_container_under_another_name_is_stopped(self):
        from kanibako.commands.stop import _stop_all

        rt = self._rt()
        rt.find_labeled.return_value = ["zz-moved", "kb-primary-a"]
        assert _stop_all(rt, force=True) == 0
        assert [c.args[0] for c in rt.stop.call_args_list] == ["kb-primary-a", "zz-moved"]
        assert rt.find_labeled.call_args.args[0] == {"kanibako.box": None}
        assert rt.find_labeled.call_args.kwargs == {"check": True}

    def test_a_failed_label_query_fails_the_run_after_the_named_ones(self, capsys):
        from kanibako.commands.stop import _stop_all

        rt = self._rt()
        rt.find_labeled.side_effect = _label_query_error("ps broke")
        assert _stop_all(rt, force=True) == 1
        assert [c.args[0] for c in rt.stop.call_args_list] == ["kb-primary-a"]
        assert "ps broke" in capsys.readouterr().err


class TestHub:
    LABELS = {"kanibako.workset": "primary", "kanibako.box": "myapp",
              "kanibako.workset_kuid": "00000"}

    def _hub(self, tmp_path, runtime, labels=None):
        from kanibako.channels.helper_listener import HelperContext, HelperHub
        from kanibako.settings.paths import BoxMode
        from tests.support.early import early_record

        helpers_dir = tmp_path / "shell" / "helpers"
        (helpers_dir / "4" / "workspace").mkdir(parents=True)
        hub = HelperHub()
        hub._ctx = HelperContext(
            runtime=runtime, image="img", container_name_segments=("primary", "myapp"),
            shell_path=tmp_path / "shell", helpers_dir=helpers_dir,
            socket_path=tmp_path / "s", early=early_record(tmp_path, mode=BoxMode.primary),
            labels=self.LABELS if labels is None else labels,
        )
        return hub

    def test_a_non_held_stop_sees_the_helper_running_under_another_name(self, tmp_path):
        rt = MagicMock()
        rt.find_labeled.return_value = ["kb-primary-myapp-helper-4-old"]
        rt.list_running.side_effect = lambda prefix, check: (
            [(prefix, "img", "Up")] if prefix.endswith("-old") else [])
        resp = self._hub(tmp_path, rt)._handle_stop(
            {"container_name": "kb-primary-myapp-helper-4", "helper_num": 4})
        assert resp.get("running") is True, resp
        assert rt.find_labeled.call_args.args[0] == {**self.LABELS, "kanibako.helper": "4"}
        rt.stop.assert_not_called()
        rt.rm.assert_not_called()

    def test_a_failed_label_query_is_unverified(self, tmp_path):
        rt = MagicMock()
        rt.find_labeled.side_effect = _label_query_error("ps broke")
        rt.list_running.return_value = []
        resp = self._hub(tmp_path, rt)._handle_stop(
            {"container_name": "kb-primary-myapp-helper-4", "helper_num": 4})
        assert resp.get("unverified") is True, resp
        rt.stop.assert_not_called()

    def test_the_shutdown_sweep_stops_a_labeled_helper_it_did_not_launch(self, tmp_path):
        rt = MagicMock()
        rt.find_labeled.return_value = ["kb-primary-myapp-helper-9"]
        hub = self._hub(tmp_path, rt)
        hub._containers = ["kb-primary-myapp-helper-4"]
        hub.stop()
        assert {c.args[0] for c in rt.stop.call_args_list} == {
            "kb-primary-myapp-helper-4", "kb-primary-myapp-helper-9"}
        assert rt.find_labeled.call_args.args[0] == {**self.LABELS, "kanibako.helper": None}

    def test_no_labels_sweeps_only_what_it_holds(self, tmp_path):
        """A helper label alone would match every box's helpers."""
        rt = MagicMock()
        rt.find_labeled.return_value = ["kb-other-helper-1"]
        hub = self._hub(tmp_path, rt, labels={})
        hub._containers = ["kb-primary-myapp-helper-4"]
        hub.stop()
        assert [c.args[0] for c in rt.stop.call_args_list] == ["kb-primary-myapp-helper-4"]
        rt.find_labeled.assert_not_called()

    def test_spawn_removes_a_stopped_labeled_helper_under_another_name(self, tmp_path):
        rt = MagicMock()
        rt.run.return_value = 0
        rt.container_exists.return_value = False
        rt.is_running.side_effect = lambda name: name == "kb-live"
        rt.find_labeled.return_value = ["kb-primary-myapp-helper-4-old", "kb-live"]
        assert self._hub(tmp_path, rt)._handle_spawn({"helper_num": 4})["status"] == "ok"
        assert [c.args[0] for c in rt.rm.call_args_list] == ["kb-primary-myapp-helper-4-old"]
        assert rt.find_labeled.call_args.args[0] == {**self.LABELS, "kanibako.helper": "4"}


def test_creds_watcher_finds_the_box_by_its_labels(monkeypatch):
    from unittest.mock import patch

    import kanibako.launch.creds_watcher as cw

    runtime = MagicMock()
    runtime.inspect_env.return_value = None
    seen = []
    with (
        patch("kanibako.runtime.container.ContainerRuntime", return_value=runtime),
        patch("kanibako.settings.config.load_config"),
        patch("kanibako.settings.paths.load_std_paths"),
        patch("kanibako.settings.paths.resolve_box_target", return_value=MagicMock()),
        patch("kanibako.utils.box_container",
              side_effect=lambda rt, proj: seen.append(rt) or "kb-primary-moved"),
        patch.object(cw, "running_box_agent",
                     side_effect=lambda rt, name: seen.append(name) and None),
    ):
        assert cw._resolve_watch_context("x") is None
    assert seen == [runtime, "kb-primary-moved"]



class TestRunningUnderANonKbName:
    """A labeled container need not carry the ``kb-`` prefix: once found, its liveness is
    asked by THAT exact name, never read off a ``kb-``-filtered listing."""

    def test_box_rm_refuses(self, world, monkeypatch, tmp_path, capsys):
        from kanibako.commands.box._parser import run_rm

        _std, boxes = world
        _runtime(monkeypatch, tmp_path, running=["zz-moved"],
                 labeled={_key("pk", "primary"): ["zz-moved"]})
        rc = run_rm(argparse.Namespace(target="pk", box=None, purge=False, force=False))
        assert rc == 1
        assert "zz-moved" in capsys.readouterr().err
        _intact(boxes)

    def test_purge_all_refuses(self, world, monkeypatch, tmp_path, capsys):
        _std, boxes = world
        _runtime(monkeypatch, tmp_path, running=["zz-moved"],
                 labeled={_key("pk", "primary"): ["zz-moved"]})
        _no_question(monkeypatch)
        assert _purge_all() == 1
        assert "zz-moved" in capsys.readouterr().err
        _intact(boxes)

    def test_a_box_ref_member_refuses(self, tmp_path, monkeypatch):
        from kanibako.commands.box._lifecycle import box_running_reason

        _runtime(monkeypatch, tmp_path, running=["zz-paws-moved"],
                 labeled={_key("bar", "paws"): ["zz-paws-moved"]})
        reason = box_running_reason(None, tmp_path / "absent.lock", box_ref=("bar", "paws"))
        assert reason is not None and "zz-paws-moved" in reason, reason

    def test_an_exact_name_only(self, tmp_path, monkeypatch):
        from kanibako.commands.box._lifecycle import box_running_reason

        # The name filter is a prefix match: a longer running name is not the box's.
        _runtime(monkeypatch, tmp_path, running=["zz-paws-moved-2"],
                 labeled={_key("bar", "paws"): ["zz-paws-moved"]})
        assert box_running_reason(None, tmp_path / "absent.lock",
                                  box_ref=("bar", "paws")) is None

    def test_box_list_shows_it_active(self, world, monkeypatch, tmp_path, capsys):
        from kanibako.commands.box._parser import run_list

        # Nothing ``kb-`` runs, so the one any-box label query must name it.
        _runtime(monkeypatch, tmp_path, running=["zz-moved"],
                 labeled={"kanibako.box": ["zz-moved"],
                          _key("pk", "primary"): ["zz-moved"]})
        assert run_list(argparse.Namespace(active=True)) == 0
        out = capsys.readouterr().out
        assert "pk " in out, out
        assert "pq " not in out and "bar " not in out, out
