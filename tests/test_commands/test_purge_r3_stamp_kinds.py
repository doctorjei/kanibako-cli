"""rootcanon r3 — what a stamp PATH may BE, and what the Note may claim.

Every case here was RED on the r2 tip (`d1de4058` = kanibako's `3ec43aca`): a FIFO at a
stamp path hung the purge forever, an unreadable stamp file raised a bare ``PermissionError``,
a mold-deleted file and an absent mold both produced a Note that accused the user of content
kanibako never shipped, and a mold carrying ``__pycache__`` installed files the stamp denied.
"""
from __future__ import annotations

import contextlib
import os
import signal
import shutil

import pytest

NOT_IN_TEMPLATE = ("it is the default canon folder `create` made, and it holds files the "
                  "current template does not")
NO_TEMPLATE = ("it is the default canon folder `create` made, and kanibako cannot check it "
               "against a template because none is installed")
UNREADABLE = ("it is the default canon folder `create` made, and kanibako cannot check it "
              "against a template because a file in it cannot be read")
DRIFT = ("it is the default canon folder `create` made, and it differs from the current "
         "template")

STAMPED = "canon/handbook/SYS_WORKSET.md"


def _cli(*argv: str) -> int:
    from kanibako.cli import main
    with pytest.raises(SystemExit) as info:
        main(list(argv))
    return int(info.value.code or 0)


@contextlib.contextmanager
def _no_hang(seconds: int = 15):
    """Fail loudly instead of hanging: a FIFO opened for reading blocks with no reader.

    ⚑ A plain assertion cannot catch a block, and a hung test is a hung gate.
    """
    def _boom(signum, frame):
        raise AssertionError(f"the purge BLOCKED for {seconds}s — it opened a non-regular "
                            "file instead of keeping the folder")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _seed(config_file):
    from kanibako.launch.templates import install_packaged_templates
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths
    std = load_std_paths(load_config(config_file))
    install_packaged_templates(std, [])
    return std


def _repointed(std, tmp_home, name):
    root = tmp_home / name
    root.mkdir(parents=True)
    assert _cli("create", "--standalone", "--register", str(root)) == 0
    (tmp_home / f"{name}_out").mkdir()
    from kanibako.settings.config_io import write_nested_key
    write_nested_key(root / "workset.yaml", ("workset",), "canon",
                     str(tmp_home / f"{name}_out"))
    return root


def _note(err: str, path) -> str:
    lines = [ln for ln in err.splitlines() if f"left the canon folder at {path} in place" in ln]
    assert len(lines) == 1, f"expected exactly one Note for {path}, got {lines}"
    return lines[0]


# --------------------------------------------------------------------------- #
# Item 1 — a FIFO at a stamp path must never be opened.
# --------------------------------------------------------------------------- #
class TestANonRegularFileAtAStampPathIsNeverOpened:
    """``_sha256_file`` opening a FIFO blocks forever. The kind is read by ``lstat``
    BEFORE any open, so the folder is kept and named instead of hanging the door."""

    @pytest.mark.parametrize("door", ["rm_purge", "box_purge"])
    def test_a_fifo_at_a_stamp_path_is_kept_and_named(
        self, door, config_file, tmp_home, credentials_dir, capsys,
    ):
        std = _seed(config_file)
        root = _repointed(std, tmp_home, "fifo")
        victim = root / STAMPED
        victim.unlink()
        os.mkfifo(victim)
        capsys.readouterr()

        with _no_hang():
            if door == "rm_purge":
                rc = _cli("box", "rm", "--purge", "--force", str(root))
            else:
                rc = _cli("box", "purge", "--force", str(root))
        out = capsys.readouterr()

        assert rc == 0, f"{door}: rc {out.out}{out.err}"
        assert victim.is_fifo(), "the FIFO is the user's; it must survive untouched"
        assert (root / "canon").is_dir(), "the folder is KEPT, not emptied"
        assert NOT_IN_TEMPLATE in _note(out.err, root / "canon"), out.err
        assert "did not put there" not in out.err, out.err

    def test_the_predicate_refuses_a_non_regular_file_without_opening_it(
        self, config_file, tmp_path,
    ):
        """Unit pin on the audit itself: a FIFO answers NOT-shipped, and ``checkable``
        stays True because nothing was ever opened — the difference between "cannot read"
        and "never tried"."""
        from kanibako.project.workset import _create_canon_stamp, _stamp_audit

        std = _seed(config_file)
        stamp = _create_canon_stamp(std)
        rel = next(r for r, (is_dir, _d) in stamp.items() if not is_dir)
        leaf = tmp_path / "canon"
        victim = leaf / rel
        victim.parent.mkdir(parents=True)
        os.mkfifo(victim)

        with _no_hang(10):
            shipped, _matching, checkable = _stamp_audit(leaf, stamp)
        assert shipped is False, "a FIFO is not what the copier copied"
        assert checkable is True, "nothing was unreadable — nothing was opened"


# --------------------------------------------------------------------------- #
# Item 2 — an unreadable stamp file must not raise out of the purge.
# --------------------------------------------------------------------------- #
class TestAnUnreadableStampFileFailsClosed:
    """``chmod 000`` used to escape as a bare ``PermissionError`` at rc 1. The audit
    catches ``OSError`` and answers UNREADABLE — which is NOT "differs"."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    @pytest.mark.parametrize("door", ["rm_purge", "box_purge"])
    def test_an_unreadable_stamp_file_is_kept_and_named(
        self, door, config_file, tmp_home, credentials_dir, capsys,
    ):
        std = _seed(config_file)
        root = _repointed(std, tmp_home, "unread")
        victim = root / STAMPED
        victim.chmod(0o000)
        capsys.readouterr()
        try:
            if door == "rm_purge":
                rc = _cli("box", "rm", "--purge", "--force", str(root))
            else:
                rc = _cli("box", "purge", "--force", str(root))
        finally:
            victim.chmod(0o644)
        out = capsys.readouterr()

        assert "Traceback" not in out.err, f"{door}: {out.err}"
        assert rc == 0, f"{door}: rc {out.err}"
        assert victim.exists(), "the unreadable file must survive"
        assert (root / "canon").is_dir(), "the folder is KEPT"
        assert UNREADABLE in _note(out.err, root / "canon"), out.err
        # ⚑ Unreadable is not "differs" and not "does not hold" — kanibako has not
        # seen the bytes, so it may not claim either.
        assert DRIFT not in out.err, out.err
        assert NOT_IN_TEMPLATE not in out.err, out.err


class TestTheMoldSideIsReadByKindAndFailsClosed:
    """The stamp reads the LIVE mold, so the mold side gets the folder side's rules: an
    unreadable mold file answers UNREADABLE, and a FIFO in the mold is never opened."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-000 file")
    @pytest.mark.parametrize("door", ["rm_purge", "box_purge"])
    def test_an_unreadable_mold_file_is_kept_and_named(
        self, door, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.templates import workset_mold_canon_source

        std = _seed(config_file)
        root = _repointed(std, tmp_home, "mread")
        mold_file = workset_mold_canon_source(std) / "handbook" / "SYS_WORKSET.md"
        mold_file.chmod(0o000)
        capsys.readouterr()
        try:
            with _no_hang():
                if door == "rm_purge":
                    rc = _cli("box", "rm", "--purge", "--force", str(root))
                else:
                    rc = _cli("box", "purge", "--force", str(root))
        finally:
            mold_file.chmod(0o644)
        out = capsys.readouterr()

        assert "Traceback" not in out.err, f"{door}: {out.err}"
        assert rc == 0, f"{door}: rc {out.err}"
        assert (root / STAMPED).is_file(), "the box's copy must survive"
        assert (root / "canon").is_dir(), "the folder is KEPT"
        assert UNREADABLE in _note(out.err, root / "canon"), out.err
        assert DRIFT not in out.err, out.err
        assert NOT_IN_TEMPLATE not in out.err, out.err

    @pytest.mark.parametrize("door", ["rm_purge", "box_purge"])
    def test_a_fifo_in_the_mold_is_never_opened(
        self, door, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.templates import workset_mold_canon_source

        std = _seed(config_file)
        root = _repointed(std, tmp_home, "mfifo")
        mold_file = workset_mold_canon_source(std) / "handbook" / "SYS_WORKSET.md"
        mold_file.unlink()
        os.mkfifo(mold_file)
        capsys.readouterr()

        with _no_hang():
            if door == "rm_purge":
                rc = _cli("box", "rm", "--purge", "--force", str(root))
            else:
                rc = _cli("box", "purge", "--force", str(root))
        out = capsys.readouterr()

        assert "Traceback" not in out.err, f"{door}: {out.err}"
        assert rc == 0, f"{door}: rc {out.err}"
        assert mold_file.is_fifo(), "the mold is not kanibako's to rewrite"
        assert (root / STAMPED).is_file(), "the box's copy must survive"
        # ⚑ The mold no longer ships that file as anything ``create`` would copy.
        assert NOT_IN_TEMPLATE in _note(out.err, root / "canon"), out.err


# --------------------------------------------------------------------------- #
# Item 3 — the Note never claims an author it cannot see.
# --------------------------------------------------------------------------- #
class TestTheNoteNeverClaimsAnAuthorItCannotSee:
    def test_a_file_the_mold_no_longer_ships_is_not_blamed_on_the_user(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.templates import workset_mold_canon_source

        std = _seed(config_file)
        root = _repointed(std, tmp_home, "mdelf")
        (workset_mold_canon_source(std) / "handbook" / "SYS_WORKSET.md").unlink()
        capsys.readouterr()

        assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
        err = capsys.readouterr().err
        assert (root / "canon").is_dir()
        assert NOT_IN_TEMPLATE in _note(err, root / "canon"), err
        assert "did not put there" not in err, err

    def test_a_mold_moved_away_says_there_is_no_template(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.templates import workset_mold_canon_source

        std = _seed(config_file)
        root = _repointed(std, tmp_home, "mgone")
        shutil.rmtree(workset_mold_canon_source(std))
        capsys.readouterr()

        assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
        err = capsys.readouterr().err
        assert (root / "canon").is_dir()
        assert NO_TEMPLATE in _note(err, root / "canon"), err
        assert "did not put there" not in err, err

    def test_a_never_installed_mold_says_there_is_no_template(self, config_file, tmp_path):
        """No mold at all: the reason must not read the folder as user content."""
        from kanibako.project.workset import default_canon_leftover_why
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import load_std_paths

        std = load_std_paths(load_config(config_file))
        leaf = tmp_path / "canon" / "handbook"
        leaf.mkdir(parents=True)
        (leaf / "SYS_WORKSET.md").write_text("anything\n")

        assert default_canon_leftover_why(tmp_path / "canon", std) == NO_TEMPLATE

    def test_a_user_file_uses_the_same_no_authorship_words(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        """Accepted reword: a real user file reads the same as a mold-deleted file,
        because from here it is the same fact — the template does not have it."""
        std = _seed(config_file)
        root = _repointed(std, tmp_home, "ufile")
        (root / "canon" / "mine.txt").write_text("mine\n")
        capsys.readouterr()

        assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
        err = capsys.readouterr().err
        assert (root / "canon" / "mine.txt").read_text() == "mine\n"
        assert NOT_IN_TEMPLATE in _note(err, root / "canon"), err
        assert "did not put there" not in err, err


# --------------------------------------------------------------------------- #
# Item 4 — the copier and the stamp count the same files.
# --------------------------------------------------------------------------- #
class TestTheCopierAndTheStampCountTheSameFiles:
    """``copy_tree`` copies every REGULAR file; the stamp used ``walk_shipped_files``,
    which drops ``__pycache__`` / ``*.pyc`` / ``.DS_Store``. A pristine ``create``
    from a mold carrying one was therefore kept with a false Note."""

    def test_files_the_copier_takes_are_files_the_stamp_names(
        self, config_file, tmp_home, credentials_dir, capsys,
    ):
        from kanibako.launch.templates import workset_mold_canon_source

        std = _seed(config_file)
        mold = workset_mold_canon_source(std)
        (mold / "handbook" / "__pycache__").mkdir()
        (mold / "handbook" / "__pycache__" / "x.pyc").write_bytes(b"\x00stale")
        (mold / "handbook" / ".DS_Store").write_bytes(b"finder junk")
        root = _repointed(std, tmp_home, "pyc")

        assert (root / "canon" / "handbook" / "__pycache__" / "x.pyc").is_file(), \
            "setup: create copied the file the old stamp refused to name"
        assert (root / "canon" / "handbook" / ".DS_Store").is_file()
        capsys.readouterr()

        assert _cli("box", "rm", "--purge", "--force", str(root)) == 0
        out = capsys.readouterr()
        assert not (root / "canon").exists(), (
            "a pristine create from this mold must still come away — the stamp now names "
            f"what the copier copied: {out.err}")
        assert "Removed the default canon folder `create` made" in out.out, out.out

    def test_the_shared_filter_is_the_copiers_own_predicate(self):
        """One predicate, both sides. ``walk_shipped_files`` is NOT it."""
        from kanibako.launch.templates import is_copyable_file

        assert is_copyable_file.__doc__ is not None
        # A regular file passes; the things copy_tree skips, fail.
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            reg = os.path.join(d, "a.md")
            with open(reg, "w") as fh:
                fh.write("x")
            from pathlib import Path
            assert is_copyable_file(Path(reg)) is True
            assert is_copyable_file(Path(d)) is False
            fifo = os.path.join(d, "f")
            os.mkfifo(fifo)
            assert is_copyable_file(Path(fifo)) is False, \
                "a FIFO must not read as copyable — that is the hang"
