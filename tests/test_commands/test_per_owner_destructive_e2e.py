"""Keyspec §0 "Per-owner resources", end to end: no deleting verb deletes anything while a
workset inherits a system-tier per-owner value that reaches no owner identity.

⚑ The real CLI (``main``) over a real tmp filesystem; no deletion is mocked.  Only the
container runtime is stubbed, as in ``test_stop.py``'s malformed-settings cases.  Every check
is a tree hash taken before and after.

The collided state is staged by hand, since the set door refuses it: two named worksets each
hold box ``a``, and the system settings file points ``workset.boxes`` at ``w1``'s store, so
both resolve ``a`` to the same tree, and a purge of ``w1`` takes ``w2``'s box with it.

- **State A:** the primary workset inherits the system value too.
- **State B:** the primary workset owns an anchored ``workset.boxes``, so ``load_std_paths``
  passes and only the verbs' own check stands between the data and the delete.
- **State C:** state A plus a valid system-tier ``workset.registry``, with a stale decoy
  ``registry.yaml`` in each named root.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from kanibako.cli import main
from kanibako.project.registry_store import load_deregistered
from kanibako.settings.config import WORKSET_META_FILE, load_config
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.paths import load_std_paths, resolve_project

_ANCHORED = "workset.boxes={meta.workset.path}/boxes"


def _tree_hash(root: Path) -> str:
    """Every path below *root* with its mode, and each file's bytes or link target."""
    if not root.exists() and not root.is_symlink():
        return "absent"
    digest = hashlib.sha256()
    if root.is_file():
        digest.update(root.read_bytes())
        return digest.hexdigest()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted([*dirnames, *filenames]):
            path = Path(dirpath) / name
            digest.update(f"{path.relative_to(root)}\0{path.lstat().st_mode:o}\0".encode())
            if path.is_symlink():
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(path.read_bytes())
    return digest.hexdigest()


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        main(argv)
    return excinfo.value.code


@dataclass
class World:
    std: object
    w1_root: Path
    w2_root: Path
    leaf: Path
    primary: Path
    primary_meta: Path
    archive: Path
    primary_archive: Path
    dup: Path
    marker: Path
    roots: list[Path]

    def hashes(self) -> dict[Path, str]:
        return {root: _tree_hash(root) for root in self.roots}


@pytest.fixture
def runtime():
    rt = MagicMock()
    rt.stop.return_value = True
    rt.is_running.return_value = True
    rt.inspect_env.return_value = None
    rt.container_exists.return_value = True
    rt.rm.return_value = True
    rt.list_running.return_value = []
    with (
        patch("kanibako.runtime.container.ContainerRuntime", return_value=rt),
        patch("kanibako.commands.stop.ContainerRuntime", return_value=rt),
        patch("kanibako.commands.box._parser.ContainerRuntime", return_value=rt),
        patch("kanibako.commands.stop._writeback_on_stop") as writeback,
    ):
        rt.writeback = writeback
        yield rt


def _stopped_or_removed(rt) -> list:
    return [call for call in rt.method_calls if call[0] in ("stop", "rm")]


@pytest.fixture
def world(config_file, tmp_home, credentials_dir, runtime, capsys) -> World:
    """Two named worksets each holding box ``a``, a primary box ``p``, an archive of ``w1``'s
    ``a`` and of ``p``, and a duplicate destination; then the system file pointing
    ``workset.boxes`` at ``w1``'s store (state A).
    """
    from kanibako.project.workset import add_project, create_workset

    config = load_config(config_file)
    std = load_std_paths(config)
    worksets = [create_workset(n, tmp_home / f"{n}root", std) for n in ("w1", "w2")]
    for ws in worksets:
        leaf = ws.workspaces_dir / "a"
        leaf.mkdir(parents=True)
        (leaf / "f.txt").write_text(ws.name)
        add_project(ws, "a", leaf, std)
        (ws.projects_dir / "a" / "marker").write_text(ws.name)
    primary = tmp_home / "work" / "p"
    primary.mkdir(parents=True)
    (primary / "f.txt").write_text("p")
    proj = resolve_project(std, config, project_dir=str(primary), initialize=True)
    (proj.metadata_path / "marker").write_text("p")

    leaf = worksets[0].workspaces_dir / "a"
    archive, primary_archive = tmp_home / "a.txz", tmp_home / "p.txz"
    assert _run(["box", "archive", str(leaf), str(archive), "--force"]) == 0
    assert _run(["box", "archive", str(primary), str(primary_archive), "--force"]) == 0
    dup = tmp_home / "dup"
    dup.mkdir()
    (dup / "f.txt").write_text("the destination's own")

    std.settings.parent.mkdir(parents=True, exist_ok=True)
    std.settings.write_text(yaml.safe_dump(
        {"workset": {"boxes": str(worksets[0].projects_dir)}},
    ))
    capsys.readouterr()
    runtime.reset_mock()
    return World(
        std=std, w1_root=worksets[0].root, w2_root=worksets[1].root, leaf=leaf,
        primary=primary, primary_meta=proj.metadata_path, archive=archive,
        primary_archive=primary_archive, dup=dup, marker=worksets[0].projects_dir / "a" / "marker",
        roots=[worksets[0].root, worksets[1].root, std.primary_workset, std.registry,
               tmp_home / "work", dup, archive, primary_archive],
    )


def _own_primary_boxes(world: World) -> None:
    """State B: the primary workset owns an anchored ``workset.boxes``."""
    meta = world.std.primary_workset / WORKSET_META_FILE
    doc = load_doc(meta) or {}
    doc.setdefault("workset", {})["boxes"] = "{meta.workset.path}/boxes"
    dump_doc(meta, doc)


# ``box rm`` is not here: it addresses primary and standalone boxes only, so it has no
# named-workset box to refuse; :class:`TestStateBPartitionKey` exercises it.
_VERBS = {
    "workset-delete-purge": lambda w: ["workset", "rm", "w1", "--purge", "--force"],
    "clean": lambda w: ["box", "purge", str(w.leaf), "--force"],
    "clean-all": lambda w: ["box", "purge", "--all", "--force"],
    "box-move": lambda w: ["box", "move", str(w.leaf), str(w.dup.parent / "moved" / "a"),
                           "--force"],
    "box-convert": lambda w: ["box", "convert", str(w.leaf), "--standalone", "--force"],
    "restore": lambda w: ["box", "extract", str(w.archive), str(w.leaf), "--force"],
    "box-duplicate-force": lambda w: ["box", "duplicate", str(w.leaf), str(w.dup), "--force"],
}


class TestStateB:
    """The primary workset passes; each verb's own check refuses before its first change."""

    @pytest.mark.parametrize("verb", list(_VERBS))
    def test_the_verb_refuses_and_deletes_nothing(self, world, runtime, capsys, verb):
        _own_primary_boxes(world)
        before = world.hashes()

        rc = _run(_VERBS[verb](world))

        err = capsys.readouterr().err
        assert rc != 0, err
        assert str(world.std.settings) in err, err
        assert world.hashes() == before
        assert _stopped_or_removed(runtime) == []

    def test_stop_refuses_naming_the_file(self, world, runtime, capsys):
        _own_primary_boxes(world)
        before = world.hashes()

        rc = _run(["stop", str(world.leaf)])

        err = capsys.readouterr().err
        assert rc == 1, err
        assert str(world.std.settings) in err, err
        assert _stopped_or_removed(runtime) == []
        runtime.writeback.assert_not_called()
        assert world.hashes() == before


def _collide_mailboxes(world: World) -> None:
    """The partition-key state: the system file holds a collided ``workset.channels.mailboxes``."""
    world.std.settings.write_text(yaml.safe_dump(
        {"workset": {"channels": {"mailboxes": str(world.dup.parent / "mb")}}},
    ))


_PRIMARY_VERBS = {
    "box-rm-purge": lambda w: ["box", "rm", "p", "--purge", "--force"],
    "workset-delete-purge": lambda w: ["workset", "rm", "w1", "--purge", "--force"],
    "clean": lambda w: ["box", "purge", str(w.primary), "--force"],
    "clean-all": lambda w: ["box", "purge", "--all", "--force"],
    "box-move": lambda w: ["box", "move", str(w.primary), str(w.dup.parent / "moved" / "p"),
                           "--force"],
    "box-convert": lambda w: ["box", "convert", str(w.primary), "--standalone", "--force"],
    "restore": lambda w: ["box", "extract", str(w.primary_archive), str(w.primary), "--force"],
    "box-duplicate-force": lambda w: ["box", "duplicate", str(w.primary), str(w.dup),
                                      "--force"],
}


class TestStateBPartitionKey:
    """State B's twin for a key no ``std`` read touches: the system file holds a collided
    ``workset.channels.mailboxes`` only, so every read the verbs make before deleting
    passes, and the verb's own check is all that refuses.

    ⚑ ``box rm`` addresses primary and standalone boxes only, so it cannot be run against a
    named workset's box in state B; this is where its check is exercised.
    """

    @pytest.mark.parametrize("verb", list(_PRIMARY_VERBS))
    def test_the_verb_refuses_and_deletes_nothing(self, world, runtime, capsys, verb):
        _collide_mailboxes(world)
        before = world.hashes()

        rc = _run(_PRIMARY_VERBS[verb](world))

        err = capsys.readouterr().err
        assert rc != 0, err
        assert "workset.channels.mailboxes is set to" in err, err
        assert str(world.std.settings) in err, err
        assert world.hashes() == before
        assert _stopped_or_removed(runtime) == []


def _own_mailboxes(root: Path) -> None:
    """*root*'s workset owns ``workset.channels.mailboxes``, so it inherits nothing to refuse."""
    meta = root / WORKSET_META_FILE
    doc = load_doc(meta) or {}
    doc.setdefault("workset", {}).setdefault("channels", {})["mailboxes"] = (
        "@meta.workset.path/channels/mailboxes")
    dump_doc(meta, doc)


def _settled(*argvs: list[str]):
    """A setup that runs *argvs* while the system file collides nothing."""
    def setup(world: World) -> None:
        world.std.settings.write_text("{}\n")
        for argv in argvs:
            assert _run(argv) == 0, argv
    return setup


def _write(path_of, text: str = "kept"):
    def setup(world: World) -> None:
        path = path_of(world)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return setup


def _stray_q(w: World) -> Path:
    return w.leaf.parent / "q" / "f.txt"


_TO_STANDALONE = lambda w: ["box", "convert", str(w.primary), "--standalone", "--force"]  # noqa: E731

# Each case: a setup run before the collision, the verb, and a path the verb deletes or
# overwrites if nothing refuses.  Every case reaches a check the primary-box cases above do not.
_GUARD_CASES = {
    "named-clean": (None, lambda w: ["box", "purge", str(w.leaf), "--force"],
                    lambda w: w.marker),
    "named-box-move": (None, lambda w: ["box", "move", str(w.leaf),
                                        str(w.dup.parent / "moved" / "a"), "--force"],
                       lambda w: w.leaf / "f.txt"),
    "named-box-convert": (None, lambda w: ["box", "convert", str(w.leaf), "--standalone",
                                           "--force"],
                          lambda w: w.marker),
    "named-restore": (_write(lambda w: w.leaf / "new.txt"),
                      lambda w: ["box", "extract", str(w.archive), str(w.leaf), "--force"],
                      lambda w: w.leaf / "new.txt"),
    "named-duplicate-force": (None,
                              lambda w: ["box", "duplicate", str(w.leaf), str(w.dup), "--force"],
                              lambda w: w.dup / "f.txt"),
    "duplicate-into-workset": (_write(_stray_q),
                               lambda w: ["box", "duplicate", str(w.primary),
                                          str(w.dup.parent / "unused"), "--to", "named",
                                          "--workset", "w1", "--name", "q", "--force"],
                               _stray_q),
    "duplicate-cross-mode": (_write(lambda w: w.dup / "workspace" / "f.txt"),
                             lambda w: ["box", "duplicate", str(w.primary), str(w.dup),
                                        "--to", "standalone", "--force"],
                             lambda w: w.dup / "workspace" / "f.txt"),
    "convert-to-workset": (lambda w: _own_mailboxes(w.std.primary_workset),
                           lambda w: ["box", "convert", str(w.primary), "--workset", "w1",
                                      "--force"],
                           lambda w: w.primary_meta),
    "convert-to-primary": (lambda w: _own_mailboxes(w.w1_root),
                           lambda w: ["box", "convert", str(w.leaf), "--default", "--move",
                                      str(w.dup.parent / "moved" / "a"), "--force"],
                           lambda w: w.marker),
    "convert-to-standalone": (lambda w: _own_mailboxes(w.w1_root),
                              lambda w: ["box", "convert", str(w.leaf), "--standalone",
                                         "--move", str(w.dup.parent / "moved" / "a"), "--force"],
                              lambda w: w.marker),
    "convert-source-only": (lambda w: _own_mailboxes(w.w1_root),
                            lambda w: ["box", "convert", str(w.primary), "--workset", "w1",
                                       "--force"],
                            lambda w: w.primary_meta),
    "duplicate-target-only": (lambda w: _own_mailboxes(w.w1_root),
                              lambda w: ["box", "duplicate", str(w.leaf), str(w.dup), "--force"],
                              lambda w: w.dup / "f.txt"),
    "duplicate-source-only": (lambda w: (_own_mailboxes(w.w1_root), _write(_stray_q)(w)),
                              lambda w: ["box", "duplicate", str(w.primary),
                                         str(w.dup.parent / "unused"), "--to", "named",
                                         "--workset", "w1", "--name", "q", "--force"],
                              _stray_q),
    "clean-all-primary-only": (lambda w: [_own_mailboxes(r) for r in (w.w1_root, w.w2_root)],
                               lambda w: ["box", "purge", "--all", "--force"],
                               lambda w: w.primary_meta),
    "rm-standalone": (lambda w: _settled(_TO_STANDALONE(w))(w),
                      lambda w: ["box", "rm", str(w.primary), "--purge", "--force"],
                      lambda w: w.primary / "box_data"),
    "rm-deregistered-primary": (_settled(["box", "rm", "p"]),
                                lambda w: ["box", "rm", "p", "--purge", "--force"],
                                lambda w: w.primary_meta),
    "rm-deregistered-standalone": (
        lambda w: _settled(_TO_STANDALONE(w), ["box", "rm", str(w.primary)])(w),
        lambda w: ["box", "rm", *load_deregistered(w.std.registry), "--purge", "--force"],
        lambda w: w.primary / "box_data"),
    "clean-all-named-only": (_settled(["box", "rm", "p", "--purge", "--force"]),
                             lambda w: ["box", "purge", "--all", "--force"],
                             lambda w: w.marker),
    "disconnect-remove-files": (None, lambda w: ["workset", "disconnect", "w1", "a",
                                                 "--remove-files", "--force"],
                                lambda w: w.marker),
}


class TestPartitionKeyReachesEveryCheck:
    """The partition-key state against named box ``a``, a duplicate into a workset, a
    standalone and a deregistered ``box rm --purge``, ``box purge --all`` with data in named
    worksets only, and ``workset disconnect --remove-files``.  The ``*-only`` cases and the
    ``convert-to-*`` cases give one side its own key, so the other side's check alone refuses.
    """

    @pytest.mark.parametrize("case", list(_GUARD_CASES))
    def test_the_verb_refuses_and_deletes_nothing(self, world, runtime, capsys, case):
        setup, argv, victim_of = _GUARD_CASES[case]
        if setup is not None:
            setup(world)
        _collide_mailboxes(world)
        victim = victim_of(world)
        kept = _tree_hash(victim)
        assert kept != "absent", victim
        capsys.readouterr()
        runtime.reset_mock()
        before = world.hashes()

        rc = _run(argv(world))

        err = capsys.readouterr().err
        assert _tree_hash(victim) == kept, f"DELETED OR OVERWRITTEN {victim}; rc={rc}; {err}"
        assert world.hashes() == before
        assert rc != 0, err
        assert "workset.channels.mailboxes is set to" in err, err
        assert str(world.std.settings) in err, err
        assert _stopped_or_removed(runtime) == []


class TestStateA:
    def test_box_list_refuses_naming_the_file(self, world, capsys):
        before = world.hashes()

        assert _run(["box", "list"]) == 1
        assert str(world.std.settings) in capsys.readouterr().err
        assert world.hashes() == before

    def test_stop_refuses_and_stop_all_still_stops(self, world, runtime, capsys):
        before = world.hashes()

        assert _run(["stop", "a"]) == 1
        err = capsys.readouterr().err
        assert str(world.std.settings) in err, err
        assert "kanibako stop --all reads no settings" in err, err
        assert _stopped_or_removed(runtime) == []

        runtime.list_running.return_value = [("kanibako-a", "img", "Up")]
        assert _run(["stop", "--all", "--force"]) == 0
        runtime.stop.assert_called_once_with("kanibako-a")
        assert world.hashes() == before

    def test_system_set_cures_it(self, world, capsys):
        before = world.hashes()

        assert _run(["system", "set", _ANCHORED]) == 0, capsys.readouterr().err
        assert world.hashes() == before

    def test_system_reset_cures_it(self, world, capsys):
        before = world.hashes()

        assert _run(["system", "reset", "workset.boxes"]) == 0, capsys.readouterr().err
        assert world.hashes() == before


class TestStateC:
    def test_stop_reads_the_system_registry(self, world, runtime, capsys):
        doc = yaml.safe_load(world.std.settings.read_text())
        doc["workset"]["registry"] = "{meta.workset.path}/members.yaml"
        world.std.settings.write_text(yaml.safe_dump(doc))
        for root in (world.w1_root, world.w2_root):
            text = (root / "registry.yaml").read_text()
            assert "a:" in text, text
            (root / "members.yaml").write_text(text)
            (root / "registry.yaml").write_text(text.replace("a:", "decoy:"))
        before = world.hashes()

        assert _run(["stop", str(world.leaf)]) == 1
        assert str(world.std.settings) in capsys.readouterr().err
        assert runtime.method_calls == []

        assert _run(["system", "set", _ANCHORED]) == 0, capsys.readouterr().err
        assert _run(["stop", str(world.leaf)]) == 0, capsys.readouterr().err
        runtime.stop.assert_called_once_with("kanibako-a")
        assert not [c for c in runtime.method_calls if "kanibako-decoy" in c[1]]
        assert world.hashes() == before
