"""``box rm --purge`` lists every path it deletes, asks before unregistering, and keeps
a symlinked vault's target.

Each case runs the real ``run_rm`` over a box ``run_create`` made, so the list, the
question, and the deletions come from the same plan the CLI uses.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
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


def _create(path: Path, *, standalone: bool = False, register: bool = True) -> None:
    from kanibako.commands.box._parser import run_create

    path.mkdir(parents=True, exist_ok=True)
    assert run_create(argparse.Namespace(
        path=str(path), standalone=standalone, no_vault=False, name=None, image=None,
        agent=None, allow_home=False, register=standalone and register, recover=False,
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
        from kanibako.runtime import container

        _create(tmp_home / "proj")
        std = _std(config_file)
        meta = std.boxes / "proj"
        real = container.remove_box_tree
        monkeypatch.setattr(container, "remove_box_tree",
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


class TestUnregisteredStandalone:
    """An unregistered standalone box is reached by its path; ``rm`` adds no entry."""

    def test_plain_rm_changes_nothing_and_names_the_purge(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.commands.box._parser import run_rm

        root = tmp_home / "sa"
        _create(root, standalone=True, register=False)
        registry = _std(config_file).registry
        before = registry.read_bytes() if registry.exists() else None
        capsys.readouterr()

        assert run_rm(argparse.Namespace(target=str(root), purge=False, force=False)) == 1
        err = capsys.readouterr().err
        assert "is not registered; nothing to remove" in err
        assert f"kanibako box rm {root} --purge" in err
        assert (registry.read_bytes() if registry.exists() else None) == before
        assert (root / "box_data").is_dir()

    def test_purge_deletes_the_store_and_adds_no_entry(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True, register=False)
        registry = _std(config_file).registry
        before = registry.read_bytes() if registry.exists() else None
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        assert "from the registry" not in capsys.readouterr().out
        assert not (root / "box_data").exists()
        assert (registry.read_bytes() if registry.exists() else None) == before


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

    def test_symlinked_vault_parent_keeps_everything_behind_it(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """A linked ``vault/`` loses only the link; the list is exactly what goes."""
        root = tmp_home / "sa"
        _create(root, standalone=True)
        outside = tmp_home / "extv"
        (root / "vault").rename(outside)
        (root / "vault").symlink_to(outside)
        (outside / "rw" / "canary.txt").write_text("keep me\n")
        digest = hashlib.sha256((outside / "rw" / "canary.txt").read_bytes()).hexdigest()
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        out = capsys.readouterr()
        assert _plan_lines(out.out) == [
            f"box metadata: {root / 'box_data'}",
            f"workset settings: {root / 'workset.yaml'}",
            f"box canon (kanibako's handbook): {root / 'canon'}",
            f"vault parent folder: {root / 'vault'} → {outside} (link only; target kept)",
        ]
        assert f"left the vault at {outside / 'rw'} in place" in out.err
        for gone in ("box_data", "workset.yaml", "canon", "vault"):
            assert not (root / gone).exists() and not (root / gone).is_symlink()
        assert (outside / "ro").is_dir()
        assert hashlib.sha256((outside / "rw" / "canary.txt").read_bytes()).hexdigest() == digest

    def test_vault_linked_into_the_project_lists_arms_by_their_real_path(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The list names ``inner/ro|rw``, which go; the link line keeps ``inner/``."""
        root = tmp_home / "sa"
        _create(root, standalone=True)
        inner = root / "inner"
        (root / "vault").rename(inner)
        (root / "vault").symlink_to(inner)
        (inner / "keep.txt").write_text("mine\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        assert _plan_lines(capsys.readouterr().out) == [
            f"box metadata: {root / 'box_data'}",
            f"workset settings: {root / 'workset.yaml'}",
            f"box canon (kanibako's handbook): {root / 'canon'}",
            f"vault ro (your files): {inner / 'ro'}",
            f"vault rw (your files): {inner / 'rw'}",
            f"vault parent folder: {root / 'vault'} → {inner} (link only; target kept)",
        ]
        assert not (inner / "ro").exists() and not (inner / "rw").exists()
        assert not (root / "vault").is_symlink()
        assert (inner / "keep.txt").read_text() == "mine\n"

    def test_dangling_vault_link_is_listed_and_unlinked(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True)
        nowhere = tmp_home / "nowhere"
        shutil.rmtree(root / "vault")
        (root / "vault").symlink_to(nowhere)
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        assert (f"vault parent folder: {root / 'vault'} → {nowhere} (link only; target kept)"
                in _plan_lines(capsys.readouterr().out))
        assert not (root / "vault").is_symlink()

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

    def test_canon_folder_is_listed_and_removed(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """The ``canon/`` create made is the box's: listed, then gone; the user's file stays."""
        root = tmp_home / "sa"
        _create(root, standalone=True)
        assert (root / "canon" / "handbook").is_dir()
        (root / "mine.txt").write_text("mine\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        lines = _plan_lines(capsys.readouterr().out)
        assert f"box canon (kanibako's handbook): {root / 'canon'}" in lines
        listed = [Path(line.split(": ", 1)[1]) for line in lines]
        assert not any(p.exists() or p.is_symlink() for p in listed)
        assert (root / "mine.txt").read_text() == "mine\n"

    def test_symlinked_canon_loses_only_the_link(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True)
        outside = tmp_home / "extcanon"
        (root / "canon").rename(outside)
        (root / "canon").symlink_to(outside)
        (outside / "canary.txt").write_text("keep me\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        assert (f"box canon (kanibako's handbook): {root / 'canon'} → {outside} "
                "(link only; target kept)") in _plan_lines(capsys.readouterr().out)
        assert not (root / "canon").is_symlink() and not (root / "canon").exists()
        assert (outside / "canary.txt").read_text() == "keep me\n"

    def test_canon_repointed_outside_the_root_is_kept_and_named(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root, standalone=True)
        outside = tmp_home / "theircanon"
        outside.mkdir()
        (outside / "canary.txt").write_text("keep me\n")
        with (root / "workset.yaml").open("a") as fh:
            fh.write(f"  canon: '{outside}'\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        out = capsys.readouterr()
        assert not any(line.startswith("box canon") for line in _plan_lines(out.out))
        assert f"left the canon folder at {outside} in place" in out.err
        assert (outside / "canary.txt").read_text() == "keep me\n"

    @pytest.mark.parametrize("via_link", [False, True], ids=["at-root", "linked-parent"])
    def test_canon_at_the_root_or_behind_a_linked_parent_is_kept(
        self, config_file, tmp_home, credentials_dir, capsys, via_link,
    ):
        """Only a tier STRICTLY below the root goes: one AT it, or reached out through a
        linked parent, is kept and named."""
        root = tmp_home / "sa"
        _create(root, standalone=True)
        (root / "mine.txt").write_text("mine\n")
        outside = tmp_home / "ext"
        (outside / "canon").mkdir(parents=True)
        (outside / "canon" / "canary.txt").write_text("keep me\n")
        if via_link:
            (root / "lnk").symlink_to(outside)
        canon, kept = (root / "lnk" / "canon", outside / "canon") if via_link else (root, root)
        with (root / "workset.yaml").open("a") as fh:
            fh.write(f"  canon: '{canon}'\n")
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        out = capsys.readouterr()
        assert not any(line.startswith("box canon") for line in _plan_lines(out.out))
        assert f"left the canon folder at {kept} in place" in out.err
        assert (root / "mine.txt").read_text() == "mine\n"
        assert (outside / "canon" / "canary.txt").read_text() == "keep me\n"
        assert (root / "lnk").is_symlink() == via_link
