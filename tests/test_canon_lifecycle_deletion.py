"""Box-tree DELETION against a protected canon skeleton (C-CANON R1b / J-7).

⚑ WHY THIS MODULE EXISTS. Before J-7 a box home only *sometimes* contained files the
host user could not unlink — when an agent happened to write as root inside a
``--userns=keep-id`` container — so exactly one verb (``box rm --purge``) carried the
``podman unshare`` escalation and everything else used a bare ``shutil.rmtree``.

Under J-7 that assumption is dead: **every** box home carries the canon skeleton, and
the skeleton is root-owned and mode-``555`` by construction. Two consequences drive
every test here:

1. ``shutil.rmtree`` raises ``PermissionError`` on a tree containing 555 DIRECTORIES
   **even when the caller owns them** — unlinking a child needs write on its parent.
   So the breakage is not confined to the fully-protected state; it also hits the
   owned-but-555 state that ``shutil.copytree`` produces (it reproduces modes without
   ownership) and that a partially-degraded create can leave behind.
2. Because (1) needs no subuid at all, it is reproducible in a plain unit test: a
   tree of 555 dirs the test itself owns. That is what ``protected_box_home`` builds.
   The genuinely-subuid case still needs podman and belongs to the bifrost e2e; what
   is pinned here is that every verb ROUTES through the escalating deleter.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path

import pytest

from kanibako.runtime.container import remove_box_tree


def protected_box_home(box_dir: Path) -> Path:
    """Build a box tree whose canon skeleton is 555, as box create leaves it.

    Ownership is the test process's own (a unit test cannot mint subuids), which is
    precisely the case the Editor's finding makes interesting: the modes ALONE are
    enough to break ``shutil.rmtree``.
    """
    home = box_dir / "home"
    (home / "canon" / "bible" / "general").mkdir(parents=True)
    (home / "canon" / "bible" / "agent").mkdir()
    (home / "canon" / "handbook" / "box").mkdir(parents=True)
    (home / "canon" / "COLLECTION.md").touch()
    (home / "canon" / "bible" / "ROM_CONTENTS.md").touch()
    # The seeded books stay agent-owned + writable (undeletable only via the parent).
    (home / "canon" / "notebook").mkdir()
    (home / "canon" / "notebook" / "MY_CONTENTS.md").write_text("mine\n")
    (box_dir / "box.yaml").write_text("box: {}\n")

    for p in sorted((home / "canon").rglob("*"), reverse=True):
        if p.is_dir() and p.name not in ("notebook", "workbook"):
            p.chmod(0o555)
        elif p.is_file() and p.parent.name != "notebook":
            p.chmod(0o444)
    (home / "canon").chmod(0o555)
    return home


@pytest.fixture
def protected_box(tmp_path):
    box = tmp_path / "boxes" / "mybox"
    box.mkdir(parents=True)
    protected_box_home(box)
    return box


class TestThePremise:
    """Guard the guard: if a bare rmtree ever stops failing on these trees, every
    test below would pass vacuously and the whole module would stop meaning anything.
    """

    def test_bare_rmtree_fails_on_an_owned_555_tree(self, protected_box):
        with pytest.raises(PermissionError):
            shutil.rmtree(protected_box)
        assert protected_box.exists()

    def test_the_skeleton_really_is_unwritable(self, protected_box):
        canon = protected_box / "home" / "canon"
        assert not stat.S_IMODE(canon.stat().st_mode) & stat.S_IWUSR
        with pytest.raises(OSError):
            (canon / "scratch").mkdir()


class TestRemoveBoxTree:
    """The shared primitive every verb routes through."""

    def test_removes_a_protected_tree_without_any_runtime(self, protected_box):
        """⚑ NO PODMAN REQUIRED for the owned case. ``remove_box_tree`` re-opens the
        directory modes it owns and retries before ever reaching ``podman unshare`` —
        which matters because the machines most likely to lack podman are exactly the
        ones running archive/extract/move.
        """
        assert remove_box_tree(protected_box) is True
        assert not protected_box.exists()

    def test_takes_the_seeded_books_with_it(self, protected_box):
        """A box deletion is a box deletion: notebook/workbook go too."""
        assert remove_box_tree(protected_box) is True
        assert not (protected_box / "home" / "canon" / "notebook").exists()


class TestVerbsRouteThroughIt:
    """⚑ THE ROUTING CONTRACT — the part that actually regressed.

    Each verb below owned a bare ``shutil.rmtree`` of a BOX tree. These assert the
    call reaches ``remove_box_tree``; the primitive's own behaviour is covered above,
    so a verb that quietly reverts to ``shutil.rmtree`` fails here by name.
    """

    def test_workset_remove_project_routes_the_box_tree_only(
        self, tmp_home, std, monkeypatch,
    ):
        """⚑ SCOPED. Only ``projects_dir/<name>`` is a box tree; the workspace and
        vault dirs are ordinary user content and must stay on the plain path — the
        escalation is not a blanket upgrade.

        Driven through the REAL ``create_workset``/``add_project``/``remove_project``
        chain, so a change to what ``remove_project`` deletes shows up here.
        """
        import kanibako.runtime.container as container_mod
        from kanibako.project.workset import add_project, create_workset, remove_project

        seen: list[Path] = []
        real = remove_box_tree
        monkeypatch.setattr(
            container_mod, "remove_box_tree", lambda p: (seen.append(p), real(p))[1],
        )

        ws = create_workset("delset", tmp_home / "worksets" / "delset", std)
        source = tmp_home / "src"
        source.mkdir()
        add_project(ws, "b1", source, std)

        box_tree = ws.projects_dir / "b1"
        protected_box_home(box_tree)
        (ws.workspaces_dir / "b1").mkdir(parents=True, exist_ok=True)
        for leaf in ("ro", "rw"):
            (ws.vault_dir / leaf / "b1").mkdir(parents=True, exist_ok=True)

        remove_project(ws, "b1", remove_files=True, std=std)

        assert seen == [box_tree], f"only the BOX tree may escalate, got {seen}"
        assert not box_tree.exists()
        assert not (ws.workspaces_dir / "b1").exists()
        assert not (ws.vault_dir / "ro" / "b1").exists()

    def test_delete_workset_clears_box_trees_before_the_root(
        self, tmp_home, std, monkeypatch,
    ):
        """The workset ROOT contains ``boxes/<name>/home/canon`` per member, so a
        whole-root rmtree dies on the first skeleton — and it dies AFTER the registry
        entries are already gone, leaving a half-deleted workset that no longer
        resolves. Driven through the real ``delete_workset``.
        """
        import kanibako.runtime.container as container_mod
        from kanibako.project.workset import add_project, create_workset, delete_workset

        seen: list[Path] = []
        real = remove_box_tree
        monkeypatch.setattr(
            container_mod, "remove_box_tree", lambda p: (seen.append(p), real(p))[1],
        )

        ws = create_workset("wipeset", tmp_home / "worksets" / "wipeset", std)
        for name in ("b1", "b2"):
            source = tmp_home / f"src-{name}"
            source.mkdir()
            add_project(ws, name, source, std)
            protected_box_home(ws.projects_dir / name)

        root = delete_workset("wipeset", std, remove_files=True)

        assert seen == [ws.projects_dir / "b1", ws.projects_dir / "b2"], seen
        assert not root.exists(), "the workset root must be fully gone"

    @pytest.mark.parametrize(
        "module,func",
        [
            ("kanibako.commands.clean", "remove_box_tree"),
            ("kanibako.commands.restore", "remove_box_tree"),
            ("kanibako.commands.box._duplicate", "remove_box_tree"),
            ("kanibako.commands.box._lifecycle", "remove_box_tree"),
        ],
    )
    def test_every_box_verb_module_imports_the_shared_deleter(self, module, func):
        """A cheap structural pin: if someone re-introduces a bare ``shutil.rmtree``
        for a box tree in these modules the import usually goes with it."""
        import importlib

        mod = importlib.import_module(module)
        assert hasattr(mod, func), f"{module} lost its {func} import"

    def test_a_created_box_is_not_removable_by_a_bare_rmtree(self, tmp_path):
        """⚑ THE REGRESSION CI CAUGHT, pinned as a unit.

        `box create` leaves a canon skeleton. Where ``podman unshare`` WORKS that
        skeleton ends up subuid-owned and 555, and a bare ``shutil.rmtree`` of the
        box home then raises ``PermissionError`` partway through — which is what
        reddened ``test_box_register.py`` in CI while every local run stayed green
        (on a host whose ``podman unshare`` fails, the protect pass short-circuits
        before the chmod and leaves a plain 755 tree).

        ⚑ THE ASYMMETRY IS THE POINT: a bare rmtree of a created box home is
        CONDITIONALLY broken — it works on exactly the machines where the protection
        does not. So "it passed locally" proves nothing here, and the only safe rule
        is that NO caller (test bodies included) bare-rmtrees a box home.
        """
        box = tmp_path / "created-box"
        box.mkdir()
        protected_box_home(box)

        with pytest.raises(PermissionError):
            shutil.rmtree(box)
        assert box.exists(), "the tree survives, half-deleted"

        # The sanctioned deleter handles it — this is what test bodies must use.
        assert remove_box_tree(box) is True
        assert not box.exists()

    def test_no_bare_rmtree_on_a_box_home_remains(self):
        """⚑ THE SWEEP. Source-level guard over the verbs that own box trees: a
        ``shutil.rmtree`` whose argument names a box home / box metadata path is the
        exact bug this round fixed, and it is easy to reintroduce by copy-paste.

        ⚑ ``workset`` is in the list because it is the module that ACTUALLY had the
        bug — ``remove_project``/``delete_workset`` both bare-rmtree'd member box
        trees — and it was the one module the first version of this sweep did not
        cover. A guard that omits the site it was written for is theatre.
        """
        import inspect

        from kanibako.project import workset
        from kanibako.commands import clean, restore
        from kanibako.commands.box import _duplicate, _lifecycle

        offenders: list[str] = []
        for mod in (clean, restore, _duplicate, _lifecycle, workset):
            for lineno, line in enumerate(inspect.getsource(mod).splitlines(), 1):
                if "shutil.rmtree" not in line:
                    continue
                arg = line.split("shutil.rmtree", 1)[1]
                if any(
                    tok in arg
                    for tok in ("metadata", "shell", "dst_project", "new_project_dir",
                                "box_data", "dst_home", "new_path",
                                # ⚑ NOT ``proj_dir``: in ``workset.remove_project``
                                # that is the loop variable over box tree AND
                                # workspace AND both vault dirs, and only the box
                                # tree escalates. Flagging it would mark the
                                # deliberately-scoped branch as a defect — a guard
                                # that cries wolf about correct code gets silenced.
                                "box_tree", "projects_dir")
                ):
                    offenders.append(f"{mod.__name__}:{lineno}: {line.strip()}")
        assert not offenders, (
            "box trees must be removed via container.remove_box_tree:\n"
            + "\n".join(offenders)
        )
