"""``workset connect`` onto another box's tree (keyspace § 0 "Per-owner resources").

A standalone root, by its path, a link, or a linked parent, is another box's: connecting
it is refused unless --force, and --force absorbs that box.  An absorb finds the box's
registry row under any spelling and moves it (one registration), and records the member at
the box's root: the row's path, else the path given, with a link leaf resolved.  Measured
before the fix: through a link, the member was recorded at the link, and a row spelled
another way was kept beside the member.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._parser import run_create
from kanibako.commands.workset_cmd import run_connect
from kanibako.project import registry_store
from kanibako.project.workset import add_project, create_workset, list_worksets, load_workset


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    return load_std_paths(load_config(config_file))


def _connect(ws: str, source: Path, name: str, *, force: bool) -> int:
    return run_connect(argparse.Namespace(
        workset=ws, source=str(source), project_name=name, force=force))


def _members(std, ws: str) -> dict:
    from kanibako.project import workset_registry
    from kanibako.settings.config_io import load_doc
    loaded = load_workset(list_worksets(std)[ws], ws, early_system=std.early_system)
    return workset_registry.load_workset_boxes(workset_registry.resolve_workset_registry_path(
        loaded.root, load_doc(loaded.root / "workset.yaml"), early=loaded.early_scope))


@pytest.fixture
def env(config_file, credentials_dir, tmp_home, monkeypatch):
    monkeypatch.chdir(tmp_home)
    monkeypatch.setenv("PWD", str(tmp_home))
    std = _std(config_file)
    area = (tmp_home / "nest").resolve()
    area.mkdir()
    create_workset("wn", area / "wn", std)
    return std, area


def _standalone(std, area: Path, capsys, root: Path | None = None) -> tuple[Path, str]:
    root = root or area / "XS"
    assert run_create(build_parser().parse_args(
        ["box", "create", "--standalone", "--register", str(root)])) == 0
    capsys.readouterr()
    name = registry_store.standalone_name_for_root(std.registry, root)
    assert name is not None
    return root, name


def _tree_digest(tree: Path) -> dict[str, str]:
    """Every file under *tree* (relative path → sha256): proof no file was overwritten."""
    import hashlib
    return {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(tree.rglob("*")) if p.is_file() and not p.is_symlink()}


def _owner(std, path: Path) -> str | None:
    from kanibako.launch import box_resolve
    owned = box_resolve.find_connected_external_box(path, std)
    return None if owned is None else f"{owned.workset_name}/{owned.box_name}"


def _assert_absorbed(std, name: str | None, root: Path) -> None:
    """One registration: the row moved (no row at any spelling of *root*), and the member
    records *root*, which its connected-box lookup finds."""
    if name is not None:
        assert name not in registry_store.load_standalone(std.registry)
    assert registry_store.standalone_name_for_same_dir(std.registry, root) is None
    assert _members(std, "wn").get("m") == str(root)
    assert _owner(std, root) == "wn/m"


class TestConnectAStandaloneRoot:
    """Its own path, a link to it, or a path through a linked parent: one rule."""

    @staticmethod
    def _reach(area: Path, root: Path, how: str) -> Path:
        if how == "path":
            return root
        if how == "link":
            (area / "rlnk").symlink_to(root)
            return area / "rlnk"
        (area / "lnk").symlink_to(root.parent)
        return area / "lnk" / root.name

    @pytest.mark.parametrize("how", ["path", "link", "parent"])
    def test_refused_without_force_naming_it(self, env, capsys, how):
        std, area = env
        root, name = _standalone(std, area, capsys)
        rows = registry_store.load_standalone(std.registry)
        source = self._reach(area, root, how)

        assert _connect("wn", source, "m", force=False) == 1
        err = capsys.readouterr().err
        assert f"Cannot connect '{source}'" in err, err
        assert "it is a standalone box" in err and "Re-run with --force" in err, err
        assert registry_store.load_standalone(std.registry) == rows
        assert "m" not in _members(std, "wn")

    @pytest.mark.parametrize("how", ["path", "link", "parent"])
    def test_force_absorbs_it_once(self, env, capsys, how):
        std, area = env
        root, name = _standalone(std, area, capsys)
        source = self._reach(area, root, how)

        assert _connect("wn", source, "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, root)


class TestConnectInsideAMember:
    def _member(self, std, area: Path) -> Path:
        other = create_workset("other", area / "other", std)
        ext = area / "XN"
        (ext / "sub").mkdir(parents=True)
        add_project(other, "XN", ext, std)
        return ext

    def test_inside_a_member_names_the_members_path(self, env, capsys):
        std, area = env
        ext = self._member(std, area)
        assert _connect("wn", ext / "sub", "m", force=False) == 1
        err = capsys.readouterr().err
        assert (f"Cannot connect '{ext / 'sub'}': it is inside the workspace of member "
                f"'XN' of workset 'other' ({ext}).") in err, err
        assert "re-run with --force" in err, err
        assert "already connected as project" not in err
        assert "m" not in _members(std, "wn")

    def test_force_connects_inside_a_member(self, env, capsys):
        """His Q4Jei 176-4B (C) answer, "yes", to "Newly allowed with --force: `workset
        connect` / `convert --workset --move` inside a member": no file is overwritten,
        so --force shares the member's tree."""
        std, area = env
        ext = self._member(std, area)
        (ext / "sub" / "f.txt").write_text("theirs")
        before = _tree_digest(ext)
        assert _connect("wn", ext / "sub", "m", force=True) == 0, capsys.readouterr().err
        assert _members(std, "wn").get("m") == str(ext / "sub")
        after = _tree_digest(ext)
        assert before and all(after.get(path) == digest for path, digest in before.items())
        assert _members(std, "other").get("XN") == str(ext)

    def test_the_members_own_path_keeps_its_message(self, env, capsys):
        std, area = env
        ext = self._member(std, area)
        assert _connect("wn", ext, "m", force=True) == 1
        assert ("already connected as project 'XN' in workset 'other'"
                in capsys.readouterr().err)


class TestConnectRootRegisteredThroughALink:
    """[R188]: a root is its path as given.  A root registered through a link is that box:
    a forced connect at that spelling absorbs it once, its row moved, never kept beside."""

    def test_root_registered_at_a_link_absorbs_once(self, env, capsys):
        std, area = env
        (area / "real").mkdir()
        link = area / "rlnk"
        link.symlink_to(area / "real")
        _root, name = _standalone(std, area, capsys, root=link)

        assert _connect("wn", link, "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, area / "real")

    def test_root_registered_under_a_linked_parent_absorbs_once(self, env, capsys):
        std, area = env
        (area / "real_parent").mkdir()
        (area / "r").symlink_to(area / "real_parent")
        _root, name = _standalone(std, area, capsys, root=area / "r" / "XS")

        assert _connect("wn", area / "r" / "XS", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, area / "r" / "XS")


class TestConvertOntoAStandaloneRootLink:
    """``convert --workset`` reaches the same absorb (``_to_workset`` → ``add_project``,
    ``force=True``): a standalone whose workspace is a link to ANOTHER standalone's root
    absorbs that box, so it is refused unless --force."""

    @staticmethod
    def _source_on_link(std, area: Path, capsys) -> tuple[Path, Path, str]:
        from kanibako.settings.config_io import dump_doc, load_doc
        xs_root, xs_name = _standalone(std, area, capsys)
        wlnk = area / "wlnk"
        wlnk.symlink_to(xs_root)
        src = area / "S"
        assert run_create(build_parser().parse_args(
            ["box", "create", "--standalone", "--register", str(src)])) == 0
        capsys.readouterr()
        meta = src / "workset.yaml"
        doc = load_doc(meta) or {}
        doc.setdefault("workset", {})["workspaces"] = str(wlnk)
        dump_doc(meta, doc)
        return src, xs_root, xs_name

    @staticmethod
    def _convert(src: Path, *, force: bool) -> int:
        from kanibako.commands.box._lifecycle import run_convert
        return run_convert(argparse.Namespace(
            old=str(src), force=force, to_default=False, to_standalone=False,
            to_workset="wn", move=None, name="m"))

    def test_refused_without_force(self, env, capsys):
        std, area = env
        src, xs_root, _xs_name = self._source_on_link(std, area, capsys)
        rows = registry_store.load_standalone(std.registry)

        assert self._convert(src, force=False) == 1
        err = capsys.readouterr().err
        assert (f"its workspace {area / 'wlnk'} is the root of another standalone box "
                f"({xs_root})") in err, err
        assert "Re-run with --force" in err
        assert registry_store.load_standalone(std.registry) == rows
        assert "m" not in _members(std, "wn")

    def test_force_absorbs_once(self, env, capsys):
        std, area = env
        src, xs_root, xs_name = self._source_on_link(std, area, capsys)

        assert self._convert(src, force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, xs_name, xs_root)

    def test_force_absorbs_a_root_registered_at_another_link(self, env, capsys):
        """The row names a link to XS; the workspace is a second link: one box, absorbed once."""
        from kanibako.settings.config_io import dump_doc, load_doc
        std, area = env
        (area / "real").mkdir()
        (area / "rlnk1").symlink_to(area / "real")
        (area / "rlnk2").symlink_to(area / "real")
        _root, xs_name = _standalone(std, area, capsys, root=area / "rlnk1")
        src = area / "S"
        assert run_create(build_parser().parse_args(
            ["box", "create", "--standalone", "--register", str(src)])) == 0
        capsys.readouterr()
        meta = src / "workset.yaml"
        doc = load_doc(meta) or {}
        doc.setdefault("workset", {})["workspaces"] = str(area / "rlnk2")
        dump_doc(meta, doc)

        assert self._convert(src, force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, xs_name, area / "real")


class TestAbsorbAcrossSpellings:
    """A forced connect finds the box's row under any spelling of its directory (the
    registry's ``standalone_name_for_same_dir``), so the row moves and is never kept
    beside the member, and a link leaf is resolved, so the member records the real root."""

    @staticmethod
    def _linked_real(area: Path, *links: str) -> Path:
        (area / "real").mkdir()
        for link in links:
            (area / link).symlink_to(area / "real")
        return area / "real"

    def test_row_at_one_link_connected_through_another(self, env, capsys):
        std, area = env
        real = self._linked_real(area, "rlnk1", "rlnk2")
        _root, name = _standalone(std, area, capsys, root=area / "rlnk1")

        assert _connect("wn", area / "rlnk2", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, real)

    def test_row_at_a_link_connected_at_the_real_path(self, env, capsys):
        std, area = env
        real = self._linked_real(area, "rlnk1")
        _root, name = _standalone(std, area, capsys, root=area / "rlnk1")

        assert _connect("wn", real, "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, real)

    def test_row_under_one_linked_parent_connected_under_another(self, env, capsys):
        """A linked ancestor spells both alike ([R188]): the member records the row's path."""
        std, area = env
        (area / "real_parent").mkdir()
        (area / "r").symlink_to(area / "real_parent")
        (area / "r2").symlink_to(area / "real_parent")
        _root, name = _standalone(std, area, capsys, root=area / "r" / "XS")

        assert _connect("wn", area / "r2" / "XS", "m", force=True) == 0, \
            capsys.readouterr().err
        _assert_absorbed(std, name, area / "r" / "XS")

    def test_unregistered_box_through_a_link_is_recorded_at_its_root(self, env, capsys):
        std, area = env
        root = area / "XS"
        assert run_create(build_parser().parse_args(
            ["box", "create", "--standalone", str(root)])) == 0
        capsys.readouterr()
        assert registry_store.standalone_name_for_same_dir(std.registry, root) is None
        (area / "rlnk").symlink_to(root)

        assert _connect("wn", area / "rlnk", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, None, root)


    @staticmethod
    def _linked_area(area: Path) -> tuple[Path, Path]:
        """``L -> realarea``: a linked ancestor; returns ``(L, realarea/XS)``."""
        (area / "realarea" / "XS").mkdir(parents=True)
        (area / "L").symlink_to(area / "realarea")
        return area / "L", area / "realarea" / "XS"

    def test_leaf_link_under_a_linked_ancestor_keeps_the_ancestor(self, env, capsys):
        """Unregistered XS reached as ``L/lnk``: only the leaf resolves, so ``L/XS``."""
        std, area = env
        linked, real = self._linked_area(area)
        assert run_create(build_parser().parse_args(
            ["box", "create", "--standalone", str(real)])) == 0
        capsys.readouterr()
        (real.parent / "lnk").symlink_to(real)

        assert _connect("wn", linked / "lnk", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, None, linked / "XS")

    def test_row_at_a_leaf_link_under_a_linked_ancestor_keeps_the_ancestor(self, env,
                                                                         capsys):
        """The row names ``L/rlnk1 -> XS``; a connect at ``L/XS`` records ``L/XS``."""
        std, area = env
        linked, real = self._linked_area(area)
        (real.parent / "rlnk1").symlink_to(real)
        _root, name = _standalone(std, area, capsys, root=linked / "rlnk1")

        assert _connect("wn", linked / "XS", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, name, linked / "XS")

    def test_leaf_link_into_another_directory_records_the_full_resolve(self, env, capsys):
        """``L/lnk -> elsewhere/XS`` leaves ``L``'s directory: the real root is recorded."""
        std, area = env
        linked, _real = self._linked_area(area)
        other = area / "elsewhere" / "XS"
        assert run_create(build_parser().parse_args(
            ["box", "create", "--standalone", str(other)])) == 0
        capsys.readouterr()
        (linked / "lnk").symlink_to(other)

        assert _connect("wn", linked / "lnk", "m", force=True) == 0, capsys.readouterr().err
        _assert_absorbed(std, None, other)
