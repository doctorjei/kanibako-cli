"""Credential writeback checks what it copies (172-CW (a) [Jei:2026-10-11]).

For each harness's synced credential file, a box copy reaches the store only when
it parses as that harness's format, never through a symlink, and a replaced store
file keeps its previous content as ``<file>.bak`` (0600).
"""

from __future__ import annotations

import json
import logging
import os
import resource
import stat
import tempfile
from pathlib import Path

import pytest

import kanibako.targets.credsync as credsync
from kanibako.targets.credsync import writeback_cred_files

_CLAUDE_OK = json.dumps({"claudeAiOauth": {"accessToken": "new"}})
_CODEX_OK = json.dumps({"OPENAI_API_KEY": "sk-new"})
_GOOSE_OK = "OPENAI_API_KEY: sk-new\n"


def _claude():
    from kanibako.plugins.claude.target import ClaudeTarget
    return ClaudeTarget()


def _codex():
    from kanibako.plugins.codex.target import CodexTarget
    return CodexTarget()


def _goose():
    from kanibako.plugins.goose.target import GooseTarget
    return GooseTarget()


_FILTERED = ".config/goose/extra.yaml"
_DIR = ".config/goose/providers"


def _synthetic():
    """goose plus a filtered file spec and a directory spec.

    goose no longer declares either shape, but credsync still realizes both for a
    plugin that does, so the engine's checks are tested on this synthetic target.
    Its filter keeps the store's own ``MODE`` and takes everything else from the box.
    """
    import dataclasses

    import yaml

    from kanibako.plugins.goose.credentials import parse_yaml
    from kanibako.plugins.goose.target import GooseTarget
    from kanibako.targets.base import Cadence, CredFileSpec

    class Synthetic(GooseTarget):
        @property
        def descriptor(self):
            base = GooseTarget().descriptor
            assert base is not None
            return dataclasses.replace(base, cred_files=base.cred_files + (
                CredFileSpec(_FILTERED, _FILTERED, cadence=Cadence.SYNC,
                             mtime_gate=True, filtered=True),
                CredFileSpec(_DIR, _DIR, cadence=Cadence.SYNC, mtime_gate=True,
                             filtered=False, is_dir=True),
            ))

        def transform_cred(self, spec, src, dst, direction):
            box = parse_yaml(Path(src).read_bytes())
            store = parse_yaml(dst.read_bytes()) if dst.is_file() else {}
            box.pop("MODE", None)
            if "MODE" in store:
                box["MODE"] = store["MODE"]
            dst.write_text(yaml.safe_dump(box, default_flow_style=False))

    return Synthetic()


# (target factory, credential relpath, a valid newer file, a wrong-format file)
HARNESSES = [
    pytest.param(_claude, ".claude/.credentials.json", _CLAUDE_OK,
                 json.dumps({"oauthAccount": {}}), id="claude"),
    pytest.param(_codex, ".codex/auth.json", _CODEX_OK, "[]", id="codex"),
    pytest.param(_goose, ".config/goose/secrets.yaml", _GOOSE_OK,
                 "- just\n- a list\n", id="goose"),
]


def _write(path: Path, text: str, mtime: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    os.utime(path, (mtime, mtime))
    return path


def _run(make, tmp_path: Path, rel: str, box_text: str | None):
    """Seed an older store file and (when given) a newer box file; write back."""
    host, proj = tmp_path / "host", tmp_path / "proj"
    store = _write(host / rel, "STORE-OLD", mtime=100)
    if box_text is not None:
        _write(proj / rel, box_text, mtime=200)
    target = make()
    writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
    return host, proj, store


@pytest.mark.parametrize("make, rel, ok, wrong", HARNESSES)
class TestWritebackChecks:
    def test_valid_newer_replaces_store_and_keeps_one_backup(
        self, make, rel, ok, wrong, tmp_path: Path,
    ) -> None:
        _, _, store = _run(make, tmp_path, rel, ok)
        assert store.read_text() == ok
        bak = store.with_name(store.name + ".bak")
        assert bak.read_text() == "STORE-OLD"
        assert stat.S_IMODE(bak.stat().st_mode) == 0o600

    def test_second_replace_keeps_only_the_latest_backup(
        self, make, rel, ok, wrong, tmp_path: Path,
    ) -> None:
        host, proj, store = _run(make, tmp_path, rel, ok)
        newer = ok.replace("new", "newer")
        _write(proj / rel, newer, mtime=300)
        target = make()
        writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
        assert store.read_text() == newer
        assert store.with_name(store.name + ".bak").read_text() == ok
        assert sorted(p.name for p in store.parent.glob(store.name + "*")) == [
            store.name, store.name + ".bak"]

    @pytest.mark.parametrize("bad", ["", "\x00\xff garbage {{{", "{"], ids=[
        "empty", "binary", "truncated"])
    def test_garbage_keeps_store_and_warns(
        self, make, rel, ok, wrong, bad, tmp_path: Path, caplog,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _, proj, store = _run(make, tmp_path, rel, bad)
        assert store.read_text() == "STORE-OLD"
        assert not store.with_name(store.name + ".bak").exists()
        assert str(proj / rel) in caplog.text

    def test_wrong_format_keeps_store_and_warns(
        self, make, rel, ok, wrong, tmp_path: Path, caplog,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            _, proj, store = _run(make, tmp_path, rel, wrong)
        assert store.read_text() == "STORE-OLD"
        assert str(proj / rel) in caplog.text

    def test_symlinked_box_file_is_never_followed(
        self, make, rel, ok, wrong, tmp_path: Path, caplog,
    ) -> None:
        # The link names a VALID file host-side, so only the link refusal stops it.
        planted = _write(tmp_path / "elsewhere" / "secret", ok, mtime=200)
        link = tmp_path / "proj" / rel
        link.parent.mkdir(parents=True)
        link.symlink_to(planted)
        os.utime(link, (300, 300), follow_symlinks=False)
        with caplog.at_level(logging.WARNING):
            _, _, store = _run(make, tmp_path, rel, None)
        assert store.read_text() == "STORE-OLD"
        assert "symlink" in caplog.text and str(link) in caplog.text

    def test_symlinked_parent_dir_is_never_followed(
        self, make, rel, ok, wrong, tmp_path: Path, caplog,
    ) -> None:
        real = tmp_path / "elsewhere"
        _write(real / Path(rel).name, ok, mtime=200)
        top = tmp_path / "proj" / Path(rel).parts[0]
        top.parent.mkdir(parents=True)
        if len(Path(rel).parts) == 2:
            top.symlink_to(real)
        else:  # .config/goose/secrets.yaml: link the middle directory
            top.mkdir()
            (top / Path(rel).parts[1]).symlink_to(real)
        with caplog.at_level(logging.WARNING):
            _, _, store = _run(make, tmp_path, rel, None)
        assert store.read_text() == "STORE-OLD"
        assert "symlink" in caplog.text

    def test_fifo_box_file_does_not_hang(
        self, make, rel, ok, wrong, tmp_path: Path, caplog,
    ) -> None:
        fifo = tmp_path / "proj" / rel
        fifo.parent.mkdir(parents=True)
        os.mkfifo(fifo)
        with caplog.at_level(logging.WARNING):
            _, _, store = _run(make, tmp_path, rel, None)
        assert store.read_text() == "STORE-OLD"
        assert "not a regular file" in caplog.text

    def test_new_store_file_is_0600_with_no_backup(
        self, make, rel, ok, wrong, tmp_path: Path,
    ) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        _write(proj / rel, ok, mtime=200)
        target = make()
        writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
        store = host / rel
        assert store.read_text() == ok
        assert stat.S_IMODE(store.stat().st_mode) == 0o600
        assert not store.with_name(store.name + ".bak").exists()

    def test_sparse_oversize_box_file_is_refused_unread(
        self, make, rel, ok, wrong, tmp_path: Path, caplog,
    ) -> None:
        box = tmp_path / "proj" / rel
        box.parent.mkdir(parents=True)
        with open(box, "wb") as fh:
            fh.truncate(256 << 20)  # costs the box no disk
        os.utime(box, (200, 200))
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with caplog.at_level(logging.WARNING):
            _, _, store = _run(make, tmp_path, rel, None)
        grown_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
        assert grown_kib < 64 << 10
        assert store.read_text() == "STORE-OLD"
        assert "larger than" in caplog.text and str(box) in caplog.text

    def test_box_file_grown_after_its_size_check_is_refused(
        self, make, rel, ok, wrong, tmp_path: Path, caplog, monkeypatch,
    ) -> None:
        box = _write(tmp_path / "proj" / rel, ok, mtime=200)
        real_fstat = os.fstat

        def fstat_then_grow(fd):
            st = real_fstat(fd)
            with open(box, "a") as fh:  # trailing blanks keep it parseable
                fh.write(" " * (2 << 20))
            return st

        monkeypatch.setattr(credsync.os, "fstat", fstat_then_grow)
        with caplog.at_level(logging.WARNING):
            _, _, store = _run(make, tmp_path, rel, None)
        monkeypatch.undo()
        assert store.read_text() == "STORE-OLD"
        assert "larger than" in caplog.text and str(box) in caplog.text

    def test_failed_backup_leaves_the_store_as_it_was(
        self, make, rel, ok, wrong, tmp_path: Path, monkeypatch, caplog,
    ) -> None:
        real_mkstemp = tempfile.mkstemp

        def no_space_for_bak(*a, **k):
            if ".bak" in k.get("prefix", ""):
                raise OSError(28, "No space left on device")
            return real_mkstemp(*a, **k)

        monkeypatch.setattr(credsync.tempfile, "mkstemp", no_space_for_bak)
        with caplog.at_level(logging.WARNING):
            _run(make, tmp_path, rel, ok)
        store = tmp_path / "host" / rel
        assert store.read_text() == "STORE-OLD"
        assert "No space left" in caplog.text and str(tmp_path / "proj" / rel) in caplog.text
        assert sorted(p.name for p in store.parent.iterdir()) == [store.name]

    def test_symlinked_store_file_stays_a_link(
        self, make, rel, ok, wrong, tmp_path: Path,
    ) -> None:
        host, proj = tmp_path / "host", tmp_path / "proj"
        kept = _write(tmp_path / "dotfiles" / "cred", "STORE-OLD", mtime=100)
        store = host / rel
        store.parent.mkdir(parents=True)
        store.symlink_to(kept)
        _write(proj / rel, ok, mtime=200)
        target = make()
        writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
        assert store.is_symlink() and kept.read_text() == ok
        assert store.with_name(store.name + ".bak").read_text() == "STORE-OLD"


def _providers(tmp_path: Path) -> tuple[Path, Path]:
    return tmp_path / "proj" / _DIR, tmp_path / "host" / _DIR


def _dir_writeback(tmp_path: Path) -> None:
    target = _synthetic()
    writeback_cred_files(target.descriptor, target, source_root=tmp_path / "host",
                         project_home=tmp_path / "proj")


def test_dir_spec_over_256_entries_is_refused(tmp_path: Path, caplog) -> None:
    box, out = _providers(tmp_path)
    box.mkdir(parents=True)
    for i in range(257):
        (box / f"p{i}.json").write_text("{}")
    with caplog.at_level(logging.WARNING):
        _dir_writeback(tmp_path)
    assert not out.exists()
    assert str(box) in caplog.text


def test_dir_spec_over_4_mib_is_refused(tmp_path: Path, caplog) -> None:
    box, out = _providers(tmp_path)
    box.mkdir(parents=True)
    with open(box / "big.json", "wb") as fh:
        fh.truncate((4 << 20) + 1)
    with caplog.at_level(logging.WARNING):
        _dir_writeback(tmp_path)
    assert not out.exists()
    assert str(box) in caplog.text


def test_dir_spec_at_the_caps_is_copied(tmp_path: Path) -> None:
    box, out = _providers(tmp_path)
    box.mkdir(parents=True)
    for i in range(255):
        (box / f"p{i}.json").write_text("{}")
    with open(box / "big.json", "wb") as fh:
        fh.truncate((4 << 20) - 2 * 255)
    _dir_writeback(tmp_path)
    assert len(list(out.iterdir())) == 256


def test_dir_spec_skips_planted_links(tmp_path: Path, caplog) -> None:
    """A link in a synced dir would copy the host file it names: skipped."""
    secret = _write(tmp_path / "host-secret", "HOST-SECRET", mtime=100)
    box, out = _providers(tmp_path)
    _write(box / "real.json", "{}", mtime=200)
    (box / "leak.json").symlink_to(secret)
    with caplog.at_level(logging.WARNING):
        _dir_writeback(tmp_path)
    assert (out / "real.json").read_text() == "{}"
    assert not (out / "leak.json").exists()
    assert str(box / "leak.json") in caplog.text


def test_dir_spec_dir_itself_a_link_is_refused(tmp_path: Path, caplog) -> None:
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "x.json", "HOST-SECRET", mtime=100)
    box, out = _providers(tmp_path)
    box.parent.mkdir(parents=True)
    box.symlink_to(elsewhere)
    with caplog.at_level(logging.WARNING):
        _dir_writeback(tmp_path)
    assert not out.exists()
    assert "symlink" in caplog.text


def _engine_writeback(target, tmp_path: Path, monkeypatch) -> Path:
    """Run the real session-end writeback (global tier) for *target*; the box home."""
    from types import SimpleNamespace

    from kanibako.commands.start import writeback_session_credentials
    from kanibako.settings.settings_launch import AuthSource

    monkeypatch.setenv("HOME", str(tmp_path / "host"))
    auth = AuthSource(tier="global", global_enabled=True, workset_enabled=False,
                      global_sync=False, workset_source=None)
    proj = tmp_path / "proj"
    writeback_session_credentials(target, SimpleNamespace(shell_path=proj), auth_src=auth)
    return proj


@pytest.mark.parametrize("rel, host_text, box_text", [
    pytest.param(_FILTERED, "MODE: approve\nX: 1\n",
                 "MODE: auto\nX: 1\n", id="filtered-noop"),
    pytest.param(".config/goose/secrets.yaml", _GOOSE_OK, _GOOSE_OK,
                 id="goose-unfiltered-same"),
])
def test_unchanged_result_leaves_the_store_file_alone(
    rel, host_text, box_text, tmp_path: Path, monkeypatch,
) -> None:
    store = _write(tmp_path / "host" / rel, host_text, mtime=1000)
    _write(tmp_path / "proj" / rel, box_text, mtime=2000)
    before = store.stat()
    _engine_writeback(_synthetic(), tmp_path, monkeypatch)
    after = store.stat()
    assert (after.st_ino, after.st_mtime) == (before.st_ino, 1000)
    assert store.read_text() == host_text
    assert not store.with_name(store.name + ".bak").exists()


def test_a_failed_spec_skips_only_itself_and_keeps_the_dirty_flag(
    tmp_path: Path, monkeypatch, caplog,
) -> None:
    from kanibako.launch.creds_watcher import creds_dirty_flag_path

    secrets = _write(tmp_path / "host/.config/goose/secrets.yaml", "STORE-OLD", mtime=100)
    config = _write(tmp_path / "host" / _FILTERED, "X: 1\n", mtime=100)
    _write(tmp_path / "proj/.config/goose/secrets.yaml", _GOOSE_OK, mtime=200)
    _write(tmp_path / "proj" / _FILTERED, "X: 2\n", mtime=200)
    flag = creds_dirty_flag_path(tmp_path / "proj")
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("")
    real_mkstemp = tempfile.mkstemp

    def no_space_for_secrets_bak(*a, **k):
        if k.get("prefix", "").startswith(".secrets.yaml.bak"):
            raise OSError(28, "No space left on device")
        return real_mkstemp(*a, **k)

    monkeypatch.setattr(credsync.tempfile, "mkstemp", no_space_for_secrets_bak)
    with caplog.at_level(logging.WARNING):
        proj = _engine_writeback(_synthetic(), tmp_path, monkeypatch)
    assert secrets.read_text() == "STORE-OLD"
    assert config.read_text() == "X: 2\n"
    assert str(proj / ".config/goose/secrets.yaml") in caplog.text
    assert flag.exists()

    monkeypatch.setattr(credsync.tempfile, "mkstemp", real_mkstemp)
    _engine_writeback(_synthetic(), tmp_path, monkeypatch)
    assert secrets.read_text() == _GOOSE_OK
    assert not flag.exists()
