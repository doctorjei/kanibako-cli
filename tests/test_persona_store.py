"""Tests for the persona-grata store discovery + resolve (pure, no box).

Covers ``kanibako.persona_store``: the discovery root builder, entry location
(store PRESENCE decides persona-vs-plain), and the ``.secret_path`` token
pointer resolution (expansion + the relative→persona-dir anchor).  Everything
here is filesystem-only under ``tmp_home`` — no container, no settings writes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.persona_store import (
    PersonaEntry,
    locate_entry,
    persona_store_root,
    resolve_secret_path,
)


def _make_store_entry(tmp_home: Path, persona: str = "navigator", harness: str = "codex") -> Path:
    """Lay down ``$XDG_CONFIG_HOME/personas/<persona>/<harness>/``; return persona dir."""
    persona_dir = tmp_home / "config" / "personas" / persona
    (persona_dir / harness).mkdir(parents=True)
    return persona_dir


class TestPersonaStoreRoot:
    def test_honors_absolute_xdg_config_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        assert persona_store_root() == (tmp_path / "cfg" / "personas").resolve()

    def test_unset_falls_back_to_home_dot_config(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        assert persona_store_root() == tmp_path / ".config" / "personas"

    def test_relative_xdg_value_ignored_per_spec(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", "relative/cfg")
        assert persona_store_root() == tmp_path / ".config" / "personas"


class TestLocateEntry:
    def test_hit_with_plus_ref(self, tmp_home):
        persona_dir = _make_store_entry(tmp_home)
        entry = locate_entry("navigator+codex")
        assert entry is not None
        assert entry.node == "navigator℘codex"
        assert entry.persona == "navigator"
        assert entry.harness == "codex"
        assert entry.persona_dir == persona_dir
        assert entry.config_dir == persona_dir / "codex"

    def test_hit_with_canonical_ref(self, tmp_home):
        _make_store_entry(tmp_home)
        entry = locate_entry("navigator℘codex")
        assert entry is not None
        assert entry.node == "navigator℘codex"

    def test_absent_store_dir_is_none(self, tmp_home):
        assert locate_entry("navigator+codex") is None

    def test_persona_dir_without_harness_dir_is_none(self, tmp_home):
        _make_store_entry(tmp_home, harness="codex")
        assert locate_entry("navigator+claude") is None

    def test_bare_ref_is_never_a_persona(self, tmp_home):
        # Even a store dir shaped like a bare name must not turn a bare agent
        # into a persona: a bare ref has no persona segment at all.
        _make_store_entry(tmp_home, persona="claude", harness="claude")
        assert locate_entry("claude") is None

    def test_config_dir_is_a_file_not_dir(self, tmp_home):
        persona_dir = tmp_home / "config" / "personas" / "navigator"
        persona_dir.mkdir(parents=True)
        (persona_dir / "codex").write_text("not a dir\n")
        assert locate_entry("navigator+codex") is None

    def test_dot_dot_persona_never_escapes_store_root(self, tmp_home):
        # "..+claude" would resolve <root>/../claude == $XDG_CONFIG_HOME/claude
        # — an ordinary harness config dir, NOT a store entry.  A dot segment
        # is a legal ref but must never traverse out of the store root.
        (tmp_home / "config" / "claude").mkdir(parents=True)
        (tmp_home / "config" / "personas").mkdir()
        assert locate_entry("..+claude") is None
        assert locate_entry(".+claude") is None

    def test_dot_dot_harness_never_escapes_persona_dir(self, tmp_home):
        # "navigator+.." would resolve <root>/navigator/.. == the store root.
        _make_store_entry(tmp_home)
        assert locate_entry("navigator+..") is None

    def test_malformed_ref_raises_config_error(self, tmp_home):
        with pytest.raises(ConfigError):
            locate_entry("navi/gator+codex")
        with pytest.raises(ConfigError):
            locate_entry("")


class TestResolveSecretPath:
    def _entry(self, tmp_home, pointer_line: str | None = None) -> PersonaEntry:
        persona_dir = _make_store_entry(tmp_home)
        if pointer_line is not None:
            (persona_dir / ".secret_path").write_text(pointer_line)
        entry = locate_entry("navigator+codex")
        assert entry is not None
        return entry

    # --- the resolution rule table, one case per row ------------------------

    def test_absolute_path_used_as_is(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == Path("/data/tok")

    def test_tilde_expands_to_home(self, tmp_home):
        entry = self._entry(tmp_home, "~/creds/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == tmp_home / "home" / "creds" / "tok"

    def test_env_var_expands(self, tmp_home):
        # tmp_home sets XDG_DATA_HOME to <tmp>/data
        entry = self._entry(tmp_home, "$XDG_DATA_HOME/p/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == tmp_home / "data" / "p" / "tok"

    def test_dot_relative_anchors_to_persona_dir(self, tmp_home):
        entry = self._entry(tmp_home, "./token\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == entry.persona_dir / "token"

    def test_bare_relative_anchors_to_persona_dir(self, tmp_home):
        entry = self._entry(tmp_home, "token\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == entry.persona_dir / "token"

    # --- anchor + pointer semantics -----------------------------------------

    def test_relative_anchor_is_persona_dir_not_cwd(self, tmp_home, monkeypatch):
        entry = self._entry(tmp_home, "./token\n")
        elsewhere = tmp_home / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        path, _ = resolve_secret_path(entry)
        assert path == entry.persona_dir / "token"
        assert not str(path).startswith(str(elsewhere))

    def test_result_is_always_absolute(self, tmp_home):
        entry = self._entry(tmp_home, "./token\n")
        path, _ = resolve_secret_path(entry)
        assert path is not None and path.is_absolute()

    def test_pointer_resolves_even_when_token_file_absent(self, tmp_home):
        # A pointer, not a read: the token file need not exist yet.
        entry = self._entry(tmp_home, "/nonexistent/place/tok\n")
        path, error = resolve_secret_path(entry)
        assert error is None
        assert path == Path("/nonexistent/place/tok")
        assert not path.exists()

    def test_never_reads_the_token_file(self, tmp_home):
        # The token exists but resolve must not open it; unreadable perms would
        # raise on any read attempt.
        entry = self._entry(tmp_home, "./token\n")
        token = entry.persona_dir / "token"
        token.write_text("SECRET")
        token.chmod(0o000)
        try:
            path, error = resolve_secret_path(entry)
        finally:
            token.chmod(0o600)
        assert error is None
        assert path == token

    # --- malformed pointer cases (tolerant: (None, reason), never raises) ---

    def test_missing_pointer_file(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and ".secret_path" in error

    def test_empty_file(self, tmp_home):
        entry = self._entry(tmp_home, "")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "empty" in error

    def test_whitespace_only_file(self, tmp_home):
        entry = self._entry(tmp_home, "   \n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "empty" in error

    def test_multiline_file_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n/other/tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "one line" in error

    def test_blank_second_line_is_still_multiline(self, tmp_home):
        # Two trailing newlines = a (blank) second line -> malformed.
        entry = self._entry(tmp_home, "/data/tok\n\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "one line" in error

    def test_single_trailing_newline_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\n")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_no_trailing_newline_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_crlf_single_line_is_fine(self, tmp_home):
        entry = self._entry(tmp_home, "/data/tok\r\n")
        assert resolve_secret_path(entry).path == Path("/data/tok")

    def test_not_utf8_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        (entry.persona_dir / ".secret_path").write_bytes(b"\xff\xfe\x00bad")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_pointer_is_a_directory_is_an_error(self, tmp_home):
        entry = self._entry(tmp_home, pointer_line=None)
        (entry.persona_dir / ".secret_path").mkdir()
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_oversized_pointer_file_is_an_error(self, tmp_home):
        # Tolerance without a slurp: a runaway "pointer" is rejected, not read.
        entry = self._entry(tmp_home, "/data/" + "x" * (17 * 1024))
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None and "too large" in error

    def test_unresolvable_tilde_user_is_an_error(self, tmp_home):
        # Path.expanduser raises RuntimeError for an unknown ~user; the
        # never-raises-through contract turns it into a warnable reason.
        entry = self._entry(tmp_home, "~kanibako_no_such_user/tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None

    def test_embedded_nul_is_an_error(self, tmp_home):
        # An embedded NUL raises ValueError inside resolve(); warnable, no raise.
        entry = self._entry(tmp_home, "/data/\x00tok\n")
        path, error = resolve_secret_path(entry)
        assert path is None
        assert error is not None
