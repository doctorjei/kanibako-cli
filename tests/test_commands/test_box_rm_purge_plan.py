"""``box rm --purge`` lists every path it deletes, asks before unregistering, and keeps
a symlinked vault's target.

Each case runs the real ``run_rm`` over a box ``run_create`` made, so the list, the
question, and the deletions come from the same plan the CLI uses.
"""

from __future__ import annotations

import argparse
import hashlib
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


def _rm(target: str, *, force: bool) -> int:
    from kanibako.commands.box._parser import run_rm

    return run_rm(argparse.Namespace(target=target, purge=True, force=force))


def _closed_stdin(monkeypatch):
    def _eof(*_a):
        raise EOFError
    monkeypatch.setattr("builtins.input", _eof)


def _symlink_to_canary(arm: Path, outside: Path) -> str:
    """Replace the vault dir *arm* with a link to *outside*, which holds a canary."""
    outside.mkdir()
    (outside / "canary.txt").write_text("keep me\n")
    arm.rmdir()
    arm.symlink_to(outside)
    return hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest()


def _plan_lines(out: str) -> list[str]:
    head, _, rest = out.partition("--purge deletes these ")
    assert rest, out
    return [line.strip() for line in rest.splitlines()[1:] if line.startswith("  ")]


class TestPrimaryPurge:
    def test_declined_question_changes_nothing(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """Closed stdin: the box stays registered and every listed path stays on disk."""
        from kanibako.settings.paths import load_primary_boxes, _early_scope, BoxMode

        _create(tmp_home / "proj")
        std = _std(config_file)
        meta = std.boxes / "proj"
        capsys.readouterr()
        _closed_stdin(monkeypatch)

        assert _rm("proj", force=False) == 2
        out = capsys.readouterr().out
        assert "Delete these 3 paths? This cannot be undone." in out
        assert "proj" in load_primary_boxes(
            std.primary_workset, early=_early_scope(std, BoxMode.primary))
        assert meta.is_dir()
        assert (std.primary_vault_rw / "proj").is_dir()

    def test_list_names_every_deleted_path_and_force_still_prints_it(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        _create(tmp_home / "proj")
        std = _std(config_file)
        std.primary_logs.mkdir(parents=True, exist_ok=True)
        log = std.primary_logs / "proj.jsonl"
        log.write_text("{}\n")
        listed = [std.boxes / "proj", std.primary_vault_ro / "proj",
                  std.primary_vault_rw / "proj", log]
        capsys.readouterr()

        assert _rm("proj", force=True) == 0
        out = capsys.readouterr().out
        lines = _plan_lines(out)
        assert [line.split(": ", 1)[1] for line in lines] == [str(p) for p in listed]
        assert f"vault rw (your files): {listed[2]}" in lines
        assert "Delete these" not in out
        assert not any(p.exists() for p in listed)

    def test_symlinked_vault_loses_only_the_link(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        _create(tmp_home / "proj")
        std = _std(config_file)
        arm = std.primary_vault_rw / "proj"
        outside = tmp_home / "elsewhere"
        digest = _symlink_to_canary(arm, outside)
        capsys.readouterr()

        assert _rm("proj", force=True) == 0
        out = capsys.readouterr().out
        assert f"{arm} → {outside} (link only; target kept)" in out
        assert not arm.is_symlink() and not arm.exists()
        assert hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest() == digest


    def test_unremovable_metadata_reports_kept_vaults_and_fails(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        """The metadata dir cannot go: each gated vault is named as KEPT, and rm exits 1."""
        from kanibako.commands.box import _parser

        _create(tmp_home / "proj")
        std = _std(config_file)
        meta = std.boxes / "proj"
        real = _parser._purge_dir
        monkeypatch.setattr(_parser, "_purge_dir",
                            lambda target: False if target == meta else real(target))
        capsys.readouterr()

        assert _rm("proj", force=True) == 1
        err = capsys.readouterr().err
        for arm in (std.primary_vault_ro / "proj", std.primary_vault_rw / "proj"):
            assert arm.is_dir()
            assert (f"Kept vault: {arm} — the box metadata folder could not be removed."
                    in err)

    def test_empty_plan_says_nothing_is_deleted(self, capsys):
        from kanibako.commands.box._parser import _confirm_purge

        assert _confirm_purge([], force=False) is True
        out = capsys.readouterr().out
        assert "--purge deletes nothing" in out
        assert "Delete these" not in out


class TestStandalonePurge:
    def test_declined_question_keeps_the_registration(
        self, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        from kanibako.project import registry_store

        root = tmp_home / "sa"
        _create(root, standalone=True)
        std = _std(config_file)
        before = registry_store.load_standalone(std.registry)
        assert before
        capsys.readouterr()
        _closed_stdin(monkeypatch)

        assert _rm(str(root), force=False) == 2
        assert registry_store.load_standalone(std.registry) == before
        assert (root / "box_data").is_dir()

    def test_symlinked_vault_loses_only_the_link(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True)
        arm = root / "vault" / "rw"
        outside = tmp_home / "elsewhere"
        digest = _symlink_to_canary(arm, outside)
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        out = capsys.readouterr().out
        assert f"vault rw (your files): {arm} → {outside} (link only; target kept)" in out
        assert not arm.is_symlink() and not arm.exists()
        assert hashlib.sha256((outside / "canary.txt").read_bytes()).hexdigest() == digest

    def test_declared_in_root_arm_is_listed_as_your_files(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True)
        with (root / "workset.yaml").open("a") as fh:
            fh.write("  vault_rw: '@meta.workset.path/data'\n")
        (root / "data").mkdir()
        (root / "data" / "ufile.txt").write_text("mine\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        lines = _plan_lines(capsys.readouterr().out)
        assert f"vault rw (your files): {root / 'data'}" in lines
        assert f"workset settings: {root / 'workset.yaml'}" in lines
        assert not (root / "data").exists()
