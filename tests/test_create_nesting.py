"""``create`` refuses a workspace inside, or holding, another box's (keyspace § 0).

"Per-owner resources": "one naming a workspace inside another's workspace" is refused
unless --force; identical workspaces and a standalone root are refused whatever the flag.
The check is the remap one (``_remap_dest_holder``), run with ``verb="create"``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.cli import build_parser
from kanibako.commands.box._parser import run_create

SHARE = "Both boxes would claim it: pass --force to share it, or choose another path."


def _create(*argv: str) -> int:
    return run_create(build_parser().parse_args(["box", "create", *argv]))


def _std(config_file):
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    return load_std_paths(load_config(config_file))


@pytest.fixture
def area(config_file, credentials_dir, tmp_home):
    return tmp_home / "nest"


def _refused(capsys, dest: Path, what: str) -> None:
    err = capsys.readouterr().err
    assert f"Cannot create a box at '{dest}': it is {what}" in err, err


class TestCreateNested:
    @pytest.mark.parametrize("force", [False, True])
    def test_primary_inside_primary(self, area, capsys, force):
        assert _create("--name", "outer", str(area / "A")) == 0
        dest = area / "A" / "inner"
        rc = _create(*(["--force"] if force else []), "--name", "inner", str(dest))
        if force:
            assert rc == 0
            return
        assert rc == 1
        _refused(capsys, dest, f"inside the workspace of primary box 'outer' ({area / 'A'}). "
                 + SHARE)
        assert not dest.exists()  # the dir create made is removed

    @pytest.mark.parametrize("force", [False, True])
    def test_primary_holding_primary(self, area, capsys, force):
        inner = area / "B" / "x" / "inner"
        assert _create("--name", "inner", str(inner)) == 0
        rc = _create(*(["--force"] if force else []), "--name", "outer", str(area / "B"))
        if force:
            assert rc == 0
            return
        assert rc == 1
        _refused(capsys, area / "B",
                 f"a directory holding the workspace of box 'inner' ({inner}). " + SHARE)

    @pytest.mark.parametrize("force", [False, True])
    def test_standalone_inside_primary(self, area, capsys, force):
        assert _create("--name", "outer", str(area / "A")) == 0
        dest = area / "A" / "sa"
        rc = _create(*(["--force"] if force else []), "--standalone", str(dest))
        if force:
            assert rc == 0
            return
        assert rc == 1
        _refused(capsys, dest, f"inside the workspace of primary box 'outer' ({area / 'A'}). "
                 + SHARE)

    def test_primary_inside_standalone_root(self, area, capsys):
        root = area / "S"
        assert _create("--standalone", "--register", str(root)) == 0
        capsys.readouterr()
        dest = root / "workspace" / "in"
        assert _create("--name", "inner", str(dest)) == 1
        err = capsys.readouterr().err
        assert f"Cannot create a box at '{dest}': it is inside the root of standalone box" in err
        assert SHARE in err
        assert _create("--force", "--name", "inner", str(dest)) == 0

    def test_standalone_inside_standalone_root(self, area, capsys):
        root = area / "S"
        assert _create("--standalone", "--register", str(root)) == 0
        capsys.readouterr()
        assert _create("--standalone", str(root / "sub")) == 1
        assert "it is inside the root of standalone box" in capsys.readouterr().err
        assert _create("--standalone", "--force", str(root / "sub")) == 0

    @pytest.mark.parametrize("force", [False, True])
    def test_standalone_holding_primary(self, area, capsys, force):
        inner = area / "E" / "w" / "in"
        assert _create("--name", "inner", str(inner)) == 0
        rc = _create(*(["--force"] if force else []), "--standalone", str(area / "E"))
        if force:
            assert rc == 0
            return
        assert rc == 1
        _refused(capsys, area / "E",
                 f"a directory holding the workspace of box 'inner' ({inner}). " + SHARE)

    @pytest.mark.parametrize("force", [False, True])
    def test_named_member_inside_primary(self, area, config_file, capsys, monkeypatch, force):
        from kanibako.project.workset import create_workset
        assert _create("--name", "outer", str(area / "A")) == 0
        ws_root = area / "A" / "wsroot"
        create_workset("W", ws_root, _std(config_file))
        monkeypatch.chdir(ws_root)
        monkeypatch.setenv("PWD", str(ws_root))
        rc = _create(*(["--force"] if force else []), "mem")
        if force:
            assert rc == 0
            return
        assert rc == 1
        _refused(capsys, ws_root / "workspaces" / "mem",
                 f"inside the workspace of primary box 'outer' ({area / 'A'}). " + SHARE)

    def test_siblings_are_not_nested(self, area):
        assert _create("--name", "one", str(area / "one")) == 0
        assert _create("--name", "two", str(area / "two")) == 0


class TestCreateUnforceable:
    """What --force cannot pass: a standalone root and identical workspaces."""

    @pytest.mark.parametrize("force", [False, True])
    def test_primary_at_a_standalone_root(self, area, capsys, force):
        root = area / "S2"
        assert _create("--standalone", str(root)) == 0
        capsys.readouterr()
        assert _create(*(["--force"] if force else []), "--name", "x", str(root)) == 1
        err = capsys.readouterr().err
        assert (f"Cannot create a box at '{root}': it is the root of an unregistered "
                "standalone box. A create is not a takeover, and no flag overrides this.") in err
        assert "then run the create again." in err
        assert "remap" not in err

    @pytest.mark.parametrize("force", [False, True])
    def test_standalone_whose_workspace_is_a_primarys(self, area, capsys, force):
        held = area / "F" / "workspace"
        assert _create("--name", "pf", str(held)) == 0
        capsys.readouterr()
        rc = _create(*(["--force"] if force else []), "--standalone", str(area / "F"))
        assert rc == 1
        err = capsys.readouterr().err
        assert (f"Cannot create a box at '{area / 'F'}': it is a standalone root whose "
                f"workspace ({held}) is the workspace of primary box 'pf'. A create is not a "
                "takeover, and no flag overrides this.") in err
        assert "remap" not in err
        assert not (area / "F" / "box_data").exists()

    @pytest.mark.parametrize("force", [False, True])
    def test_primary_at_a_standalones_workspace(self, area, capsys, force):
        root = area / "S"
        assert _create("--standalone", "--register", str(root)) == 0
        capsys.readouterr()
        dest = root / "workspace"
        assert _create(*(["--force"] if force else []), "--name", "x", str(dest)) == 1
        err = capsys.readouterr().err
        assert f"Cannot create a box at '{dest}': it is the workspace of standalone box" in err
        assert "Identical workspaces are refused, and no flag overrides this." in err


class TestStandaloneWithARefusedPrimaryRegistry:
    """An inherited system ``workset.registry`` refuses the primary registry; a standalone
    records no primary row, so the check reads no primary box and runs every other arm."""

    @staticmethod
    def _inherit_registry(config_file):
        from kanibako.settings.config_io import dump_doc
        std = _std(config_file)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(std.settings, {"workset": {"registry": "/srv/x"}})

    def test_standalone_nesting_still_refused(self, area, config_file, capsys):
        root = area / "S"
        assert _create("--standalone", "--register", str(root)) == 0
        self._inherit_registry(config_file)
        capsys.readouterr()
        assert _create("--standalone", str(root / "sub")) == 1
        err = capsys.readouterr().err
        assert "it is inside the root of standalone box" in err, err
        assert _create("--standalone", "--force", str(root / "sub")) == 0

    def test_a_primary_create_still_refuses_the_registry(self, area, config_file, capsys):
        self._inherit_registry(config_file)
        from kanibako.errors import ConfigError
        with pytest.raises(ConfigError, match=r"workset\.registry is set to '/srv/x'"):
            _create("--name", "p", str(area / "P"))

    def test_a_named_member_with_its_own_registry_is_still_found(
            self, area, config_file, capsys):
        """The primary registry alone is skipped: a named workset that sets its own
        ``workset.registry`` is still read, so nesting over its member is refused."""
        from kanibako.project.workset import add_project, create_workset
        from kanibako.settings.config import WORKSET_META_FILE
        from kanibako.settings.config_io import dump_doc, load_doc
        std = _std(config_file)
        ws = create_workset("W", area / "wroot", std)
        member = area / "H" / "m"
        member.mkdir(parents=True)
        add_project(ws, "m", member, std)
        meta = ws.root / WORKSET_META_FILE
        doc = load_doc(meta) or {}
        doc.setdefault("workset", {})["registry"] = str(ws.root / "registry.yaml")
        dump_doc(meta, doc)
        self._inherit_registry(config_file)
        capsys.readouterr()
        assert _create("--standalone", str(area / "H")) == 1
        err = capsys.readouterr().err
        assert f"a directory holding the workspace of box 'W/m' ({member})" in err, err
        assert _create("--standalone", "--force", str(area / "H")) == 0


class TestCreateRunsTheOneJudge:
    """Create asks ``_dest_nesting_refusal``, the verb-neutral check other verbs share."""

    def test_create_routes_through_dest_nesting_refusal(
            self, area, capsys, monkeypatch, tmp_home):
        from kanibako.commands.box import _lifecycle
        monkeypatch.chdir(tmp_home)
        monkeypatch.setenv("PWD", str(tmp_home))
        seen: list[dict] = []
        real = _lifecycle._dest_nesting_refusal

        def spy(std, **kw):
            seen.append(kw)
            return real(std, **kw)

        monkeypatch.setattr(_lifecycle, "_dest_nesting_refusal", spy)
        assert _create("--name", "outer", str(area / "A")) == 0
        dest = area / "A" / "inner"
        assert _create("--name", "inner", str(dest)) == 1
        assert [(k["verb"], k["dest"], k["force"]) for k in seen] == [
            ("create", area / "A", False), ("create", dest, False)]
        _refused(capsys, dest, f"inside the workspace of primary box 'outer' ({area / 'A'}). "
                 + SHARE)
