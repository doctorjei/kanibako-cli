"""No-follow at the launch-time host sites that touch a box-writable tree (174 lane 2).

Plan ``174-nofollow-patch`` sites H2, H3, H6, H7, H8: a symlink the box planted, at the
LEAF or at a PARENT, is never followed -- the host file it points at keeps its hash, the
site warns naming the path, and the call returns normally.  A link pointing back INSIDE
the tree is skipped the same way, and a tree with no link behaves as before.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from kanibako.commands import start
from kanibako.log import rotate_if_over
from kanibako.settings import core_defaults
from kanibako.settings.paths import _bootstrap_shell, _upgrade_shell
from kanibako.vscode.vscode_config import seed_codex_config, seed_session_start_hook

_BOXFS = "kanibako.boxfs"


def _digest(path: Path) -> tuple[str, int]:
    st = path.stat()
    return hashlib.sha256(path.read_bytes()).hexdigest(), st.st_mode


@pytest.fixture
def outside(tmp_path):
    """A host directory outside every box tree, holding a sentinel file."""
    d = tmp_path / "host"
    d.mkdir()
    (d / "sentinel").write_text("host secret\n")
    os.chmod(d / "sentinel", 0o640)
    return d


@pytest.fixture
def home(tmp_path):
    d = tmp_path / "box" / "home"
    d.mkdir(parents=True)
    return d


def _sentinel_untouched(outside: Path, before: dict[str, tuple[str, int]]) -> None:
    after = {p.name: _digest(p) for p in outside.iterdir()}
    assert after == before


def _snapshot(outside: Path) -> dict[str, tuple[str, int]]:
    return {p.name: _digest(p) for p in outside.iterdir()}


def _warned(caplog, path: Path) -> bool:
    return any(
        r.levelno == logging.WARNING and str(path) in r.getMessage()
        for r in caplog.records
    )


# --- H3: the shell bootstrap / upgrade ---------------------------------------------


class TestShellSeam:
    def test_leaf_link_bashrc_outside_is_not_written(self, home, outside, caplog):
        (home / ".bashrc").symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            _upgrade_shell(home)
            _bootstrap_shell(home)
        _sentinel_untouched(outside, before)
        assert (home / ".bashrc").is_symlink()
        assert _warned(caplog, home / ".bashrc")
        # The rest of the skeleton still lands.
        assert (home / ".profile").is_file()
        assert (home / ".shell.d").is_dir()

    def test_leaf_link_dangling_profile_is_not_created(self, home, outside, caplog):
        (home / ".profile").symlink_to(outside / "planted")
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            _bootstrap_shell(home)
        assert not (outside / "planted").exists()
        assert _warned(caplog, home / ".profile")

    def test_shell_d_link_outside_is_not_followed(self, home, outside, caplog):
        (home / ".shell.d").symlink_to(outside)
        (home / ".bashrc").write_text("# user\n")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            _upgrade_shell(home)
        _sentinel_untouched(outside, before)
        assert _warned(caplog, home / ".shell.d")
        # The bashrc is a plain file, so its seam line is still appended.
        assert ".shell.d/" in (home / ".bashrc").read_text()

    def test_link_inside_the_tree_is_skipped_too(self, home, caplog):
        real = home / "real-bashrc"
        real.write_text("# mine\n")
        (home / ".bashrc").symlink_to(real)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            _upgrade_shell(home)
        assert real.read_text() == "# mine\n"
        assert _warned(caplog, home / ".bashrc")

    def test_no_link_control(self, home, caplog):
        _bootstrap_shell(home)
        (home / ".bashrc").write_text("# user\n")
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            _upgrade_shell(home)
        assert ".shell.d/" in (home / ".bashrc").read_text()
        assert (home / ".profile").is_file()
        assert not caplog.records


# --- H6: the launch-time ``synced`` copy -------------------------------------------


class TestSyncedCopy:
    @pytest.fixture
    def src_file(self, tmp_path):
        p = tmp_path / "hostsrc" / "token"
        p.parent.mkdir()
        p.write_text("new token\n")
        return p

    @pytest.fixture
    def src_dir(self, tmp_path):
        d = tmp_path / "hostsrc-dir"
        (d / "sub").mkdir(parents=True)
        (d / "a").write_text("A\n")
        (d / "sub" / "b").write_text("B\n")
        return d

    def _copy(self, src, dest, root):
        start._apply_shell_copy(
            src, dest, label="synced", name=str(dest), host_src=str(src),
            logger=logging.getLogger("kanibako.test"), if_absent=False,
            skip_if=None, dest_root=root,
        )

    def test_leaf_link_outside(self, home, outside, src_file, caplog):
        dest = home / ".tok"
        dest.symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            self._copy(src_file, dest, home)
        _sentinel_untouched(outside, before)
        assert dest.is_symlink()
        assert _warned(caplog, dest)

    def test_parent_link_outside(self, home, outside, src_file, caplog):
        (home / ".config").symlink_to(outside)
        dest = home / ".config" / "sentinel"
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            self._copy(src_file, dest, home)
        _sentinel_untouched(outside, before)
        assert _warned(caplog, dest)

    def test_dir_source_with_a_link_inside_the_dest_tree(
        self, home, outside, src_dir, caplog,
    ):
        dest = home / ".cfg"
        dest.mkdir()
        (dest / "sub").symlink_to(outside)
        (dest / "a").symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            self._copy(src_dir, dest, home)
        _sentinel_untouched(outside, before)
        assert not (outside / "b").exists()
        assert _warned(caplog, dest / "sub")
        assert _warned(caplog, dest / "a")

    def test_link_inside_the_tree(self, home, src_file, caplog):
        target = home / "elsewhere"
        target.write_text("box file\n")
        dest = home / ".tok"
        dest.symlink_to(target)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            self._copy(src_file, dest, home)
        assert target.read_text() == "box file\n"
        assert _warned(caplog, dest)

    def test_no_link_control_file_and_dir(self, home, src_file, src_dir, caplog):
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            self._copy(src_file, home / ".x" / "tok", home)
            self._copy(src_dir, home / ".cfg", home)
            # An overwrite: the sync owns its destination.
            src_file.write_text("newer\n")
            self._copy(src_file, home / ".x" / "tok", home)
        assert (home / ".x" / "tok").read_text() == "newer\n"
        assert (home / ".cfg" / "a").read_text() == "A\n"
        assert (home / ".cfg" / "sub" / "b").read_text() == "B\n"
        assert not caplog.records

    def test_host_side_source_links_are_followed_as_before(self, home, tmp_path):
        """The HOST source is the user's own: a stow-style link there still copies
        its content, as ``copytree`` did; only the box side is no-follow."""
        real = tmp_path / "dotfiles" / "tok"
        real.parent.mkdir()
        real.write_text("from stow\n")
        src = tmp_path / "hostsrc-stow"
        src.mkdir()
        (src / "tok").symlink_to(real)
        self._copy(src, home / ".cfg", home)
        out = home / ".cfg" / "tok"
        assert not out.is_symlink()
        assert out.read_text() == "from stow\n"


# --- H7: the chat logs -------------------------------------------------------------


class TestChannelLogs:
    @pytest.fixture
    def chat(self, tmp_path, monkeypatch):
        from kanibako.channels import channels as ch

        monkeypatch.setattr(ch, "workset_channel_paths", lambda proj, std: None)
        d = tmp_path / "channels" / "chat"
        d.mkdir(parents=True)
        return d

    def _std(self, chat):
        return SimpleNamespace(channels_chat=chat, channels_broadcast=chat / "broadcast.md")

    def test_dangling_leaf_link_is_not_created(self, chat, outside, caplog):
        (chat / "general.md").symlink_to(outside / "planted")
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            start._seed_channel_files(self._std(chat), None)
        assert not (outside / "planted").exists()
        assert _warned(caplog, chat / "general.md")
        assert (chat / "broadcast.md").is_file()

    def test_broadcast_link_is_not_rotated(self, chat, outside, caplog, monkeypatch):
        monkeypatch.setattr(start, "ROTATE_MAX_BYTES", 1)
        (chat / "broadcast.md").symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            start._seed_channel_files(self._std(chat), None)
        _sentinel_untouched(outside, before)
        assert (chat / "broadcast.md").is_symlink()
        assert not (chat / "broadcast.md.1").exists()
        assert _warned(caplog, chat / "broadcast.md")

    def test_no_link_control_rotates(self, chat, monkeypatch, caplog):
        monkeypatch.setattr(start, "ROTATE_MAX_BYTES", 4)
        (chat / "broadcast.md").write_text("over the limit\n")
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            start._seed_channel_files(self._std(chat), None)
        assert (chat / "general.md").is_file()
        assert (chat / "broadcast.md.1").read_text() == "over the limit\n"
        assert (chat / "broadcast.md").read_text() == ""
        assert not caplog.records


class TestRotateIfOverRoot:
    def test_leaf_link_is_not_rotated(self, tmp_path, outside):
        log = tmp_path / "b.md"
        log.symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        assert rotate_if_over(log, 1, root=tmp_path) is False
        _sentinel_untouched(outside, before)
        assert log.is_symlink()

    def test_parent_link_is_not_rotated(self, tmp_path, outside):
        (tmp_path / "chat").symlink_to(outside)
        before = _snapshot(outside)
        assert rotate_if_over(tmp_path / "chat" / "sentinel", 1, root=tmp_path) is False
        _sentinel_untouched(outside, before)

    def test_control(self, tmp_path):
        log = tmp_path / "b.md"
        log.write_text("12345")
        assert rotate_if_over(log, 3, root=tmp_path) is True
        assert (tmp_path / "b.md.1").read_text() == "12345"
        assert log.read_text() == ""


# --- the supervisor fallback log (box home read) -----------------------------------


class TestSupervisorFallback:
    def test_leaf_link_is_not_read(self, home, outside, capsys, caplog):
        (outside / "sentinel").write_text(start.SUPERVISOR_FALLBACK_WARNING + "\nSECRET\n")
        log = home / start.SUPERVISOR_FALLBACK_RELPATH
        log.parent.mkdir(parents=True)
        log.symlink_to(outside / "sentinel")
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            start._print_supervisor_fallback(home)
        assert "SECRET" not in capsys.readouterr().err
        assert _warned(caplog, log)

    def test_parent_link_is_not_read(self, home, outside, capsys, caplog):
        (outside / Path(start.SUPERVISOR_FALLBACK_RELPATH).name).write_text(
            start.SUPERVISOR_FALLBACK_WARNING + "\nSECRET\n",
        )
        (home / Path(start.SUPERVISOR_FALLBACK_RELPATH).parent).symlink_to(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            start._print_supervisor_fallback(home)
        assert "SECRET" not in capsys.readouterr().err
        assert _warned(caplog, home / start.SUPERVISOR_FALLBACK_RELPATH)

    def test_control(self, home, capsys):
        log = home / start.SUPERVISOR_FALLBACK_RELPATH
        log.parent.mkdir(parents=True)
        log.write_text(start.SUPERVISOR_FALLBACK_WARNING + "\ntrace line\n")
        start._print_supervisor_fallback(home)
        assert "trace line" in capsys.readouterr().err


# --- H8: the canon skeleton --------------------------------------------------------


class TestCanonSkeleton:
    @pytest.fixture
    def runtime(self, monkeypatch):
        """Record what reaches ``podman unshare``; chmod applied for real (as the
        ``protected_canon`` simulation does), so a followed link would show."""
        from kanibako.runtime import container

        calls: dict[str, list[Path]] = {"chown": [], "chmod": []}

        class _Fake:
            def unshare_chown(self, paths, uid, gid):
                calls["chown"] += paths
                return True

            def unshare_chmod(self, paths, mode):
                calls["chmod"] += paths
                for p in paths:
                    os.chmod(p, int(mode, 8))
                return True

        monkeypatch.setattr(container, "ContainerRuntime", _Fake)
        return calls

    def test_parent_link_outside(self, home, outside, runtime, caplog):
        (home / "canon").symlink_to(outside)
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            core_defaults.materialize_canon_skeleton(home)
        _sentinel_untouched(outside, before)
        assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]
        assert runtime["chown"] == [] and runtime["chmod"] == []
        warned = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warned) == 1 and str(home / "canon") in warned[0].getMessage()

    def test_leaf_link_outside(self, home, outside, runtime, caplog):
        rels = core_defaults.canon_skeleton_rels()
        file_rel = next(rel for rel, is_dir in rels if not is_dir)
        leaf = home / file_rel
        leaf.parent.mkdir(parents=True)
        leaf.symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            core_defaults.materialize_canon_skeleton(home)
        _sentinel_untouched(outside, before)
        assert leaf not in runtime["chown"] and leaf not in runtime["chmod"]
        assert _warned(caplog, leaf)
        # Everything else is still protected.
        assert home / "canon" in runtime["chown"]

    def test_link_inside_the_tree(self, home, runtime, caplog):
        inside = home / "mine"
        inside.mkdir()
        os.chmod(inside, 0o750)
        (home / "canon").symlink_to(inside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            core_defaults.materialize_canon_skeleton(home)
        assert list(inside.iterdir()) == []
        assert (inside.stat().st_mode & 0o777) == 0o750
        assert _warned(caplog, home / "canon")

    def test_if_present_does_not_follow_a_linked_canon(self, home, outside, runtime, caplog):
        (home / "canon").symlink_to(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            core_defaults.materialize_canon_skeleton_if_present(home)
        assert sorted(p.name for p in outside.iterdir()) == ["sentinel"]
        assert runtime["chown"] == []
        assert _warned(caplog, home / "canon")

    def test_no_link_control(self, home, runtime, caplog):
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            core_defaults.materialize_canon_skeleton(home)
        expected = [home / rel for rel, _ in core_defaults.canon_skeleton_rels()]
        assert sorted(runtime["chown"]) == sorted(expected)
        assert all(p.exists() for p in expected)
        assert not caplog.records


# --- H2: the in-box agent config seeds ---------------------------------------------


class TestAgentConfigSeeds:
    def _codex(self, path, root):
        return seed_codex_config(
            path, root=root, box_config_path="/home/agent/.codex/config.toml",
            codex_cwd="/home/agent/workspace",
        )

    def test_claude_settings_leaf_link_outside(self, home, outside, caplog):
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
        _sentinel_untouched(outside, before)
        assert _warned(caplog, home / ".claude" / "settings.json")

    def test_claude_settings_parent_link_outside(self, home, outside, caplog):
        (home / ".claude").symlink_to(outside)
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
        _sentinel_untouched(outside, before)
        assert not (outside / "settings.json").exists()

    def test_codex_config_leaf_and_parent_links(self, home, outside, caplog):
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").symlink_to(outside / "sentinel")
        before = _snapshot(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert self._codex(home / ".codex" / "config.toml", home) is False
        _sentinel_untouched(outside, before)
        (home / ".codex" / "config.toml").unlink()
        (home / ".codex").rmdir()
        (home / ".codex").symlink_to(outside)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert self._codex(home / ".codex" / "config.toml", home) is False
        _sentinel_untouched(outside, before)
        assert not (outside / "config.toml").exists()

    def test_link_inside_the_tree(self, home, caplog):
        real = home / "real.json"
        real.write_text("{}\n")
        (home / ".claude").mkdir()
        (home / ".claude" / "settings.json").symlink_to(real)
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
        assert real.read_text() == "{}\n"

    def test_no_link_control(self, home, caplog):
        with caplog.at_level(logging.WARNING, logger=_BOXFS):
            assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is True
            assert self._codex(home / ".codex" / "config.toml", home) is True
            assert seed_session_start_hook(home / ".claude" / "settings.json", root=home) is False
        assert "SessionStart" in (home / ".claude" / "settings.json").read_text()
        assert not caplog.records
