"""task-purgeguard STEP 1 — the scenario matrix for ``--purge`` and repointed role keys.

The rule under test, from the dispatch: a purge may delete ONLY the folder kanibako
created *for that role*.  A role key (``workset.canon`` / ``workset.vault_ro`` /
``workset.vault_rw`` / ``workset.boxes``) pointed at any other folder — the workspace,
or a folder inside it that kanibako never stamped — is NEVER deleted; it is named as
left in place.

Every cell runs the real ``run_rm`` over a standalone box ``run_create`` made, with ONE
role key repointed, and holds the dispatch's invariant: **user files hash-identical
before and after**, while what genuinely IS kanibako's (the default role folder) still
goes.  ``pointed_at`` is the axis; ``unset`` is the control that pins the default.
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

import pytest

ROLE_DEFAULTS = {
    "canon": "canon",
    "vault_ro": "vault/ro",
    "vault_rw": "vault/rw",
    "boxes": "box_data",
}


@pytest.fixture(autouse=True)
def _no_seed(monkeypatch):
    monkeypatch.setattr("kanibako.commands.start.seed_new_box",
                       lambda std, config, proj, **kw: None)


def _create(root: Path) -> None:
    from kanibako.commands.box._parser import run_create

    root.mkdir(parents=True, exist_ok=True)
    assert run_create(argparse.Namespace(
        path=str(root), standalone=True, no_vault=False, name=None, image=None,
        agent=None, allow_home=False, register=True, recover=False,
    )) == 0


def _rm(target: str, *, force: bool) -> int:
    from kanibako.commands.box._parser import run_rm

    return run_rm(argparse.Namespace(target=target, purge=True, force=force))


def _answer_yes(monkeypatch):
    """Answer the ``--purge`` confirmation, so the ``rm --purge`` arm really deletes."""
    monkeypatch.setattr("builtins.input", lambda *_a: "yes")


def _repoint(root: Path, key: str, value: str) -> None:
    with (root / "workset.yaml").open("a") as fh:
        fh.write(f"  {key}: '{value}'\n")


def _canary(folder: Path) -> str:
    """A user file inside *folder*, returned as its hash (the thing that must survive)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ufile.txt").write_text("mine\n")
    return hashlib.sha256((folder / "ufile.txt").read_bytes()).hexdigest()


def _plan_lines(out: str) -> list[str]:
    head, _, rest = out.partition("--purge deletes these ")
    return [line.strip() for line in rest.splitlines()[1:] if line.startswith("  ")] if rest else []


NAMED_LEFT = r"left the [a-z ]*at {target} in place"


class TestRepointedRoleKeyIsNeverDeleted:
    """Row 1: an in-root repoint names a folder kanibako did NOT create for the role."""

    @pytest.mark.parametrize("key", sorted(ROLE_DEFAULTS))
    @pytest.mark.parametrize("force", [False, True], ids=["rm-purge", "force"])
    def test_in_root_repoint_survives_hash_identical_and_is_named(
        self, key, force, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        root = tmp_home / "sa"
        _create(root)
        target = root / f"user_{key}"
        digest = _canary(target)
        _repoint(root, key, str(target))
        capsys.readouterr()
        _answer_yes(monkeypatch)

        _rm(str(root), force=force)
        out = capsys.readouterr()

        assert target.is_dir(), f"{key} repointed inside the root was DELETED:\n{out.out}{out.err}"
        assert hashlib.sha256((target / "ufile.txt").read_bytes()).hexdigest() == digest
        assert re.search(NAMED_LEFT.format(target=re.escape(str(target))), out.err), (
            f"{key} was kept but never named as left in place:\n{out.err}")
        assert not any(str(target) in line for line in _plan_lines(out.out)), (
            f"{key} was listed for deletion:\n{out.out}")


class TestDefaultRoleFolderIsStillRemoved:
    """Control: the folder ``create`` stamped IS kanibako's and still goes."""

    @pytest.mark.parametrize("key", sorted(ROLE_DEFAULTS))
    @pytest.mark.parametrize("force", [False, True], ids=["rm-purge", "force"])
    def test_unset_key_removes_the_default_folder(
        self, key, force, config_file, tmp_home, credentials_dir, capsys, monkeypatch,
    ):
        root = tmp_home / "sa"
        _create(root)
        default = root / ROLE_DEFAULTS[key]
        assert default.is_dir(), f"create did not stamp {default}"
        capsys.readouterr()
        _answer_yes(monkeypatch)

        assert _rm(str(root), force=force) == 0
        out = capsys.readouterr()

        assert not default.exists(), f"the default {key} folder survived a purge:\n{out.out}"

    @pytest.mark.parametrize("key", sorted(ROLE_DEFAULTS))
    def test_explicit_repoint_to_the_default_is_still_kanibakos(
        self, key, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Spelling the default out loud does not make the folder the user's."""
        root = tmp_home / "sa"
        _create(root)
        default = root / ROLE_DEFAULTS[key]
        _repoint(root, key, str(default))
        capsys.readouterr()

        assert _rm(str(root), force=True) == 0
        out = capsys.readouterr()

        assert not default.exists(), (
            f"an explicit repoint that RESOLVES to the default was not removed:\n{out.out}")


class TestRepointOutsideIsKept:
    """Control: an out-of-root repoint is already kept — the guard must not regress it."""

    @pytest.mark.parametrize("key", sorted(ROLE_DEFAULTS))
    def test_outside_repoint_survives(
        self, key, config_file, tmp_home, credentials_dir, capsys,
    ):
        root = tmp_home / "sa"
        _create(root)
        target = tmp_home / f"outside_{key}"
        digest = _canary(target)
        _repoint(root, key, str(target))
        capsys.readouterr()

        _rm(str(root), force=True)
        out = capsys.readouterr()

        assert target.is_dir(), f"out-of-root {key} was DELETED:\n{out.out}{out.err}"
        assert hashlib.sha256((target / "ufile.txt").read_bytes()).hexdigest() == digest
