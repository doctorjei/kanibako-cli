"""Tests for browser state persistence."""

from __future__ import annotations

import json
import time
from pathlib import Path

from kanibako.browser_state import (
    BrowserState,
    clear_state,
    from_playwright_context,
    load_state,
    save_state,
    state_path,
    to_playwright_context,
)


class TestBrowserState:
    def test_defaults(self):
        s = BrowserState()
        assert s.cookies == []
        assert s.origins == []
        assert s.updated_at == 0.0

    def test_is_fresh_empty(self):
        s = BrowserState()
        assert s.is_fresh() is False

    def test_is_fresh_recent(self):
        s = BrowserState(cookies=[{"name": "a"}], updated_at=time.time())
        assert s.is_fresh() is True

    def test_is_fresh_stale(self):
        old = time.time() - 31 * 86400  # 31 days ago
        s = BrowserState(cookies=[{"name": "a"}], updated_at=old)
        assert s.is_fresh() is False

    def test_is_fresh_custom_max_age(self):
        old = time.time() - 3 * 86400  # 3 days ago
        s = BrowserState(cookies=[{"name": "a"}], updated_at=old)
        assert s.is_fresh(max_age_days=2.0) is False
        assert s.is_fresh(max_age_days=5.0) is True


class TestStatePath:
    def test_path(self, tmp_path):
        p = state_path(tmp_path)
        assert p == tmp_path / "browser-state" / "context.json"


class TestLoadState:
    def test_missing_file(self, tmp_path):
        s = load_state(tmp_path)
        assert s.cookies == []
        assert s.updated_at == 0.0

    def test_valid_file(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True)
        data = {
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [{"origin": "https://example.com"}],
            "updated_at": 1234567890.0,
        }
        path.write_text(json.dumps(data))

        s = load_state(tmp_path)
        assert len(s.cookies) == 1
        assert s.cookies[0]["name"] == "session"
        assert len(s.origins) == 1
        assert s.updated_at == 1234567890.0

    def test_corrupt_json(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("{bad json!")
        s = load_state(tmp_path)
        assert s.cookies == []

    def test_non_dict_json(self, tmp_path):
        path = state_path(tmp_path)
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2, 3]")
        s = load_state(tmp_path)
        assert s.cookies == []


class TestSaveState:
    def test_roundtrip(self, tmp_path):
        state = BrowserState(
            cookies=[{"name": "a", "value": "1"}],
            origins=[{"origin": "https://example.com"}],
        )
        save_state(tmp_path, state)

        loaded = load_state(tmp_path)
        assert loaded.cookies == state.cookies
        assert loaded.origins == state.origins
        assert loaded.updated_at > 0

    def test_creates_parent_dirs(self, tmp_path):
        data_path = tmp_path / "deep" / "nested"
        save_state(data_path, BrowserState(cookies=[{"x": 1}]))
        assert state_path(data_path).is_file()

    def test_updates_timestamp(self, tmp_path):
        state = BrowserState()
        assert state.updated_at == 0.0
        save_state(tmp_path, state)
        assert state.updated_at > 0


class TestClearState:
    def test_clears_existing(self, tmp_path):
        save_state(tmp_path, BrowserState(cookies=[{"a": 1}]))
        assert state_path(tmp_path).is_file()

        clear_state(tmp_path)
        assert not state_path(tmp_path).is_file()

    def test_clears_missing(self, tmp_path):
        # Should not raise
        clear_state(tmp_path)


class TestPlaywrightConversion:
    def test_to_playwright(self):
        state = BrowserState(
            cookies=[{"name": "a"}],
            origins=[{"origin": "https://x.com"}],
        )
        ctx = to_playwright_context(state)
        assert ctx["cookies"] == [{"name": "a"}]
        assert ctx["origins"] == [{"origin": "https://x.com"}]

    def test_from_playwright(self):
        ctx = {
            "cookies": [{"name": "b", "value": "2"}],
            "origins": [{"origin": "https://y.com", "localStorage": []}],
        }
        state = from_playwright_context(ctx)
        assert state.cookies == ctx["cookies"]
        assert state.origins == ctx["origins"]
        assert state.updated_at > 0

    def test_roundtrip(self):
        original = BrowserState(
            cookies=[{"name": "c"}],
            origins=[{"origin": "https://z.com"}],
        )
        ctx = to_playwright_context(original)
        restored = from_playwright_context(ctx)
        assert restored.cookies == original.cookies
        assert restored.origins == original.origins
