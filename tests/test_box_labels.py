"""system-design § Box and helper identity: a box's container carries
``kanibako.workset``, ``kanibako.box``, and ``kanibako.workset_kuid`` (a helper's also
``kanibako.helper``), and the box-reaching verbs find it by those labels, not by name."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kanibako.runtime.container import ContainerRuntime
from kanibako.utils import box_container, box_labels


def _proj(tmp_path: Path, *, mode: str = "primary", name: str = "myapp",
          group: str | None = None, kuid: str | None = None):
    settings = tmp_path / "workset.yaml"
    if kuid is not None:
        settings.write_text(f"workset:\n  kuid: {kuid}\n")
    proj = SimpleNamespace(
        mode=SimpleNamespace(value=mode), name=name,
        group=SimpleNamespace(name=group) if group else None,
        project_path=tmp_path, metadata_path=tmp_path, project_hash="ab" * 16,
    )
    return proj, settings


@pytest.fixture
def settings_pair(monkeypatch):
    """Point the workset-tier reader at the test's file."""
    holder: dict[str, Path | None] = {"file": None}
    monkeypatch.setattr("kanibako.settings.paths.box_workset_settings_paths",
                        lambda _proj: (Path("/unused"), holder["file"]))
    return holder


class TestBoxLabels:
    def test_a_box_carries_its_pair_and_its_kuid(self, tmp_path, settings_pair):
        proj, settings = _proj(tmp_path, mode="named", group="kento", kuid="k7m2q")
        settings_pair["file"] = settings
        assert box_labels(proj) == {
            "kanibako.workset": "kento", "kanibako.box": "myapp",
            "kanibako.workset_kuid": "k7m2q",
        }

    def test_an_unset_kuid_is_the_sentinel(self, tmp_path, settings_pair):
        proj, settings = _proj(tmp_path)
        settings_pair["file"] = settings
        assert box_labels(proj)["kanibako.workset_kuid"] == "00000"
        assert box_labels(proj)["kanibako.workset"] == "primary"

    def test_a_helper_also_carries_its_number(self, tmp_path, settings_pair):
        proj, settings = _proj(tmp_path)
        settings_pair["file"] = settings
        assert box_labels(proj, helper_num=3)["kanibako.helper"] == "3"
        assert "kanibako.helper" not in box_labels(proj)


class TestRunSetsLabels:
    def test_run_passes_every_label(self, tmp_path):
        rt = ContainerRuntime(command="/usr/bin/podman")
        with (
            patch("kanibako.runtime.container.subprocess.run") as m,
            patch("kanibako.runtime.container._precreate_mount_stubs"),
        ):
            m.return_value = MagicMock(returncode=0, stdout="cid", stderr="")
            rt.run("img", shell_path=tmp_path, project_path=tmp_path, vault_ro_path=None,
                   vault_rw_path=None, name="kb-primary-myapp", detach=True,
                   labels={"kanibako.box": "myapp", "kanibako.workset": "primary"})
            argv = m.call_args.args[0]
        pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
        assert pairs == ["kanibako.box=myapp", "kanibako.workset=primary"]
        assert argv.index("--label") < argv.index("img")


def _ps(answers: dict[tuple[str, ...], str | int]):
    """A ``subprocess.run`` stand-in for ``ps``: keyed by the label filters it was
    given; an int is a failing exit code."""
    def _run(argv, **_kw):
        filters = tuple(sorted(argv[i + 1] for i, a in enumerate(argv) if a == "--filter"))
        out = answers.get(filters, "")
        if isinstance(out, int):
            return MagicMock(returncode=out, stdout="", stderr="boom")
        return MagicMock(returncode=0, stdout=out, stderr="")
    return _run


BOX = {"kanibako.box": "myapp", "kanibako.workset": "primary"}
WANT = ("label=kanibako.box=myapp", "label=kanibako.workset=primary")
WANT_H = tuple(sorted(WANT + ("label=kanibako.helper",)))


class TestFindLabeled:
    def _find(self, answers, **kw):
        rt = ContainerRuntime(command="/usr/bin/podman")
        with patch("kanibako.runtime.container.subprocess.run", side_effect=_ps(answers)) as m:
            return rt.find_labeled(BOX, **kw), m

    def test_all_containers_every_label(self):
        found, m = self._find({WANT: "kb-a\nkb-b\n"})
        assert found == ["kb-a", "kb-b"]
        argv = m.call_args.args[0]
        assert argv[:3] == ["/usr/bin/podman", "ps", "-a"]

    def test_without_drops_the_helpers(self):
        found, _ = self._find({WANT: "kb-a\nkb-a-helper-1\n", WANT_H: "kb-a-helper-1\n"},
                              without="kanibako.helper")
        assert found == ["kb-a"]

    def test_a_failed_exclusion_answers_none(self):
        """Fail closed: a helper is never answered as the box."""
        found, _ = self._find({WANT: "kb-a\nkb-a-helper-1\n", WANT_H: 125},
                              without="kanibako.helper")
        assert found == []

    def test_a_failed_runtime_answers_none(self):
        assert self._find({WANT: 125})[0] == []

    def test_a_line_that_is_not_a_name_is_skipped(self):
        assert self._find({WANT: "kb-a\timg\tUp 3m\nkb-b\n"})[0] == ["kb-b"]


class TestBoxContainer:
    def _runtime(self, names):
        rt = MagicMock()
        rt.find_labeled.return_value = names
        return rt

    def test_found_by_its_labels_not_its_name(self, tmp_path, settings_pair):
        proj, settings = _proj(tmp_path)
        settings_pair["file"] = settings
        rt = self._runtime(["kb-parked-old"])
        assert box_container(rt, proj) == "kb-parked-old"
        labels = rt.find_labeled.call_args.args[0]
        assert labels["kanibako.box"] == "myapp"
        assert rt.find_labeled.call_args.kwargs == {"without": "kanibako.helper"}

    def test_the_rendered_name_wins_a_tie(self, tmp_path, settings_pair):
        proj, settings = _proj(tmp_path)
        settings_pair["file"] = settings
        rt = self._runtime(["kb-a-old", "kb-primary-myapp"])
        assert box_container(rt, proj) == "kb-primary-myapp"

    def test_an_unlabeled_container_is_found_by_its_name(self, tmp_path, settings_pair):
        """One created before containers were labeled carries none: the rendered name
        still reaches it."""
        proj, settings = _proj(tmp_path)
        settings_pair["file"] = settings
        assert box_container(self._runtime([]), proj) == "kb-primary-myapp"

    def test_a_box_that_renders_no_name_has_none(self, tmp_path, settings_pair):
        proj, _ = _proj(tmp_path, name="Bad Name!")
        rt = self._runtime(["kb-x"])
        assert box_container(rt, proj) is None
        rt.find_labeled.assert_not_called()


class TestHelperContainersCarryLabels:
    def test_the_hub_adds_the_helper_label(self, tmp_path):
        from kanibako.channels.helper_listener import HelperContext, HelperHub
        from kanibako.settings.paths import BoxMode
        from tests.support.early import early_record

        helpers_dir = tmp_path / "shell" / "helpers"
        (helpers_dir / "4" / "workspace").mkdir(parents=True)
        runtime = MagicMock()
        runtime.run.return_value = 0
        runtime.container_exists.return_value = False
        ctx = HelperContext(
            runtime=runtime, image="img", container_name_segments=("primary", "myapp"),
            shell_path=tmp_path / "shell", helpers_dir=helpers_dir,
            socket_path=tmp_path / "s", early=early_record(tmp_path, mode=BoxMode.primary),
            labels={"kanibako.workset": "primary", "kanibako.box": "myapp",
                    "kanibako.workset_kuid": "00000"},
        )
        hub = HelperHub()
        hub._ctx = ctx
        assert hub._handle_spawn({"helper_num": 4})["status"] == "ok"
        assert runtime.run.call_args.kwargs["labels"] == {
            "kanibako.workset": "primary", "kanibako.box": "myapp",
            "kanibako.workset_kuid": "00000", "kanibako.helper": "4",
        }
