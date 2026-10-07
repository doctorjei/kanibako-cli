"""The stored ``workset.registry: <None>`` marker DEFINES a standalone root.

``system-design-1.8.0.md`` § Detection & import and the keyspec's standalone
``workset.registry`` row: create writes the marker beside ``workset.kuid`` in the
root ``workset.yaml``, detection reads THAT file's own key (never the cascade), and
``workset set workset.registry=<None>`` on a NAMED workset is refused unless ``--force``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from kanibako.errors import ConfigError
from kanibako.launch import box_resolve
from kanibako.settings.config import WORKSET_META_FILE
from kanibako.settings.paths import STANDALONE_META_DIR

MARKER_LINE = "  registry: null  # REMOVING THIS WILL BREAK A STANDALONE BOX!"


def _cli(argv: list[str]) -> int:
    from kanibako.cli import main

    try:
        main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


def _create_standalone(tmp_home: Path) -> Path:
    root = tmp_home / "home" / "sa"
    root.mkdir()
    assert _cli(["create", str(root), "--standalone", "--no-vault"]) == 0
    return root


def _info(root: Path, capsys) -> str:
    capsys.readouterr()
    _cli(["box", "info", str(root)])
    out = capsys.readouterr()
    return out.out + out.err


class TestCreateWritesMarker:
    def test_root_file_carries_the_commented_null_line(
        self, config_file, tmp_home, credentials_dir,
    ):
        root = _create_standalone(tmp_home)
        text = (root / WORKSET_META_FILE).read_text()
        assert MARKER_LINE in text.splitlines()
        doc = yaml.safe_load(text)
        assert doc["workset"]["registry"] is None
        assert doc["workset"]["kuid"]


class TestDumpDocCommented:
    def test_comment_lands_on_the_key_line_and_round_trips(self, tmp_path):
        from kanibako.settings.config_io import dump_doc_commented

        path = tmp_path / "w.yaml"
        data = {"box": {"registry": None}, "workset": {"kuid": "abcde", "registry": None}}
        dump_doc_commented(path, data, ("workset",), "registry", "KEEP ME")
        text = path.read_text()
        assert "  registry: null  # KEEP ME" in text.splitlines()
        assert text.count("# KEEP ME") == 1
        assert yaml.safe_load(text) == data

    def test_absent_anchor_raises_and_writes_nothing(self, tmp_path):
        from kanibako.settings.config_io import dump_doc_commented

        path = tmp_path / "w.yaml"
        with pytest.raises(ConfigError):
            dump_doc_commented(path, {"workset": {"kuid": "abcde"}}, ("workset",),
                               "registry", "KEEP ME")
        assert not path.exists()


class TestDetection:
    def test_created_root_is_standalone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = _create_standalone(tmp_home)
        assert box_resolve.stores_standalone_registry_null(root) is True
        assert "Mode:         standalone" in _info(root, capsys)

    def test_removing_the_line_reads_not_standalone(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = _create_standalone(tmp_home)
        settings = root / WORKSET_META_FILE
        kept = [ln for ln in settings.read_text().splitlines() if "registry" not in ln]
        settings.write_text("\n".join(kept) + "\n")
        assert (root / STANDALONE_META_DIR).is_dir()
        assert box_resolve.stores_standalone_registry_null(root) is False
        assert "Mode:         standalone" not in _info(root, capsys)

    def test_box_data_alone_is_not_the_marker(self, tmp_path):
        (tmp_path / STANDALONE_META_DIR).mkdir()
        (tmp_path / WORKSET_META_FILE).write_text("workset:\n  kuid: abcde\n")
        assert box_resolve.stores_standalone_registry_null(tmp_path) is False

    def test_stored_null_is_the_marker_without_box_data(self, tmp_path):
        (tmp_path / WORKSET_META_FILE).write_text("workset:\n  registry: null\n")
        assert box_resolve.stores_standalone_registry_null(tmp_path) is True

    def test_a_system_file_null_is_not_the_roots_own_marker(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        root = tmp_home / "home" / "ws1"
        assert _cli(["workset", "create", str(root), "--name", "ws1"]) == 0
        std = load_std_paths(load_config(config_file))
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        std.settings.write_text("workset:\n  registry: null\n")
        assert box_resolve.stores_standalone_registry_null(root) is False
        out = _info(root / "workspaces", capsys)
        assert "Mode:         standalone" not in out
        assert "workset.registry" in out


class TestNamedSetRefusal:
    def _named(self, tmp_home) -> Path:
        root = tmp_home / "home" / "ws2"
        assert _cli(["workset", "create", str(root), "--name", "ws2"]) == 0
        return root

    def test_refused_without_force(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = self._named(tmp_home)
        before = (root / WORKSET_META_FILE).read_text() if (
            root / WORKSET_META_FILE).exists() else None
        capsys.readouterr()
        assert _cli(["workset", "set", "ws2", "workset.registry", "--null"]) == 1
        err = capsys.readouterr().err
        assert "standalone" in err
        assert "--force" in err
        after = (root / WORKSET_META_FILE).read_text() if (
            root / WORKSET_META_FILE).exists() else None
        assert after == before
        assert box_resolve.stores_standalone_registry_null(root) is False

    def test_accepted_with_force(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = self._named(tmp_home)
        capsys.readouterr()
        rc = _cli(["workset", "set", "ws2", "workset.registry", "--null", "--force"])
        out = capsys.readouterr()
        assert rc == 0, out.err
        assert "standalone" in out.err
        doc = yaml.safe_load((root / WORKSET_META_FILE).read_text())
        assert doc["workset"]["registry"] is None
        assert box_resolve.stores_standalone_registry_null(root) is True


class TestForcedNullReadsStandalone:
    """After a forced null, the registered workset reads STANDALONE from anywhere in its tree."""

    def _forced(self, tmp_home, capsys) -> Path:
        root = tmp_home / "home" / "ws3"
        assert _cli(["workset", "create", str(root), "--name", "ws3"]) == 0
        assert _cli(["workset", "set", "ws3", "workset.registry", "--null", "--force"]) == 0
        capsys.readouterr()
        return root

    def test_box_info_in_tree_at_root_and_unrelated(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = self._forced(tmp_home, capsys)
        (root / "workspaces" / "foo").mkdir(parents=True, exist_ok=True)
        for target in (root, root / "workspaces" / "foo"):
            out = _info(target, capsys)
            assert "Mode:         standalone" in out, out
            assert f"Metadata:     {root}" in out, out
        other = tmp_home / "home" / "other"
        other.mkdir()
        out = _info(other, capsys)
        assert "workset.registry" not in out, out

    def test_box_list_does_not_warn(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        self._forced(tmp_home, capsys)
        assert _cli(["box", "list"]) == 0
        assert "workset.registry" not in capsys.readouterr().err

    def test_workset_reset_is_the_way_back(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = self._forced(tmp_home, capsys)
        assert _cli(["workset", "reset", "ws3", "workset.registry"]) == 0, capsys.readouterr().err
        assert box_resolve.stores_standalone_registry_null(root) is False
        assert "Mode:         standalone" not in _info(root / "workspaces", capsys)
        assert _cli(["workset", "info", "ws3"]) == 0


def _tree(root: Path) -> dict[str, bytes | str | None]:
    out: dict[str, bytes | str | None] = {}
    for dirpath, dirs, files in os.walk(root):
        for leaf in dirs + files:
            p = Path(dirpath) / leaf
            out[str(p.relative_to(root))] = (
                "-> " + os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
    return out


class TestForcedNullKeepsTheMemberGuard:
    """A forced null hides a populated workset's members from detection, never from ``workset rm``."""

    def _populated(self, tmp_home, *, force_null: bool) -> Path:
        root = tmp_home / "home" / "ws4"
        assert _cli(["workset", "create", str(root), "--name", "ws4"]) == 0
        src = tmp_home / "home" / "m1"
        src.mkdir()
        assert _cli(["workset", "connect", "ws4", str(src)]) == 0
        (root / "boxes" / "m1").mkdir(parents=True, exist_ok=True)
        (root / "boxes" / "m1" / "keep.txt").write_text("keep")
        if force_null:
            assert _cli(["workset", "set", "ws4", "workset.registry", "--null", "--force"]) == 0
        return root

    @pytest.mark.parametrize("force_null", [True, False])
    @pytest.mark.parametrize("purge", [[], ["--purge"]])
    def test_rm_without_force_is_refused_and_the_tree_survives(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch, force_null, purge,
    ):
        root = self._populated(tmp_home, force_null=force_null)
        before = _tree(root)
        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        capsys.readouterr()
        assert _cli(["workset", "rm", "ws4", *purge]) == 1
        assert "1 project(s)" in capsys.readouterr().err
        assert _tree(root) == before

    @pytest.mark.parametrize("remove_files", [[], ["--remove-files"]])
    def test_disconnect_is_refused_before_any_change(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch, remove_files,
    ):
        root = self._populated(tmp_home, force_null=True)
        assert (root / "workspaces" / "m1").is_symlink()
        before = _tree(tmp_home)
        monkeypatch.setattr("builtins.input", lambda *_a: "yes")
        capsys.readouterr()
        assert _cli(["workset", "disconnect", "ws4", "m1", *remove_files]) == 1
        assert "workset.registry" in capsys.readouterr().err
        assert _tree(tmp_home) == before

    def test_connect_is_refused_before_the_journal_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        self._populated(tmp_home, force_null=True)
        (tmp_home / "home" / "m2").mkdir()
        before = _tree(tmp_home)
        capsys.readouterr()
        assert _cli(["workset", "connect", "ws4", str(tmp_home / "home" / "m2")]) == 1
        assert "workset.registry" in capsys.readouterr().err
        assert _tree(tmp_home) == before
