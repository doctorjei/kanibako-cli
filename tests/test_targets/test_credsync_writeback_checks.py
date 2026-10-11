"""Credential writeback checks what it copies (his 172-CW answer (a)).

For each harness's synced credential file, a box copy reaches the store only when
it parses as that harness's format, never through a symlink, and a replaced store
file keeps its previous content as ``<file>.bak`` (0600).
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path

import pytest

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


def test_goose_custom_providers_skips_planted_links(tmp_path: Path, caplog) -> None:
    """A link in the synced goose dir would copy the host file it names: skipped."""
    host, proj = tmp_path / "host", tmp_path / "proj"
    secret = _write(tmp_path / "host-secret", "HOST-SECRET", mtime=100)
    providers = proj / ".config/goose/custom_providers"
    _write(providers / "real.json", "{}", mtime=200)
    (providers / "leak.json").symlink_to(secret)
    target = _goose()
    with caplog.at_level(logging.WARNING):
        writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
    out = host / ".config/goose/custom_providers"
    assert (out / "real.json").read_text() == "{}"
    assert not (out / "leak.json").exists()
    assert str(providers / "leak.json") in caplog.text


def test_goose_custom_providers_dir_itself_a_link_is_refused(
    tmp_path: Path, caplog,
) -> None:
    host, proj = tmp_path / "host", tmp_path / "proj"
    elsewhere = tmp_path / "elsewhere"
    _write(elsewhere / "x.json", "HOST-SECRET", mtime=100)
    (proj / ".config/goose").mkdir(parents=True)
    (proj / ".config/goose/custom_providers").symlink_to(elsewhere)
    target = _goose()
    with caplog.at_level(logging.WARNING):
        writeback_cred_files(target.descriptor, target, source_root=host, project_home=proj)
    assert not (host / ".config/goose/custom_providers").exists()
    assert "symlink" in caplog.text
