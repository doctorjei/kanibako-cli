"""Tests for browser state persistence."""

from __future__ import annotations

import json

import pytest

from kanibako.browser_state import (
    BrowserState,
    from_playwright_context,
    load_state,
    save_state,
    state_path,
    to_playwright_context,
)
from tests.support.filenames import CONFIG_FILENAME


@pytest.fixture
def state_home(tmp_home, monkeypatch):
    """Pin the ``system.state`` resolve to the isolated tmp tree; return its kanibako root.

    ``state_path`` resolves the KEY on every call, reading the host config and settings
    files, so a test that writes must pin them or it writes the developer's real state
    tree.  ``tmp_home`` supplies the XDG bases; the ``/etc`` config base is pointed at an
    absent file so the resolve is hermetic on any host.
    """
    import kanibako.settings.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "config_base_path", lambda: tmp_home / "etc_absent.cfg")
    return tmp_home / "state" / "kanibako"


class TestBrowserState:
    def test_defaults(self):
        s = BrowserState()
        assert s.cookies == []
        assert s.origins == []
        assert s.updated_at == 0.0


class TestStatePath:
    """The jar's location — ``system.state``, and nothing else ([R166], [R168])."""

    def test_path(self, state_home):
        assert state_path() == state_home / "browser-state" / "context.json"

    def test_follows_a_repointed_system_state(self, state_home, tmp_home):
        """MUTATION PROOF that the location reads the KEY rather than the XDG base: a
        ``system.state`` set in the settings file moves the jar with it."""
        store = tmp_home / "srv" / "custom_store"
        (tmp_home / "config" / CONFIG_FILENAME).write_text(f'config:\n  data: "{store}"\n')
        settings = store / "global" / "settings.yaml"
        settings.parent.mkdir(parents=True)
        elsewhere = tmp_home / "elsewhere" / "state"
        settings.write_text(f'system:\n  state: "{elsewhere}"\n')

        assert state_path() == elsewhere / "browser-state" / "context.json"

    def test_never_lands_in_the_data_tree(self, state_home, tmp_home):
        """[R168]: the jar is STATE — regenerable and machine-local — so it derives from
        ``system.state``.  The retired spelling put it under ``config.data``; nothing may
        reach that spelling again, and a REPOINTED ``config.data`` moves no state ([R166]),
        so the store below is the reading that must stay unreachable."""
        store = tmp_home / "srv" / "custom_store"
        (tmp_home / "config" / CONFIG_FILENAME).write_text(f'config:\n  data: "{store}"\n')

        path = state_path()
        assert path == state_home / "browser-state" / "context.json"
        assert store not in path.parents
        assert tmp_home / "data" not in path.parents

    def test_creates_nothing(self, state_home):
        """Resolving a location materializes no directory — only ``save_state`` writes."""
        assert not state_home.exists()
        state_path()
        assert not state_home.exists()


class TestLoadState:
    def test_missing_file(self, state_home):
        s = load_state()
        assert s.cookies == []
        assert s.updated_at == 0.0

    def test_valid_file(self, state_home):
        path = state_path()
        path.parent.mkdir(parents=True)
        data = {
            "cookies": [{"name": "session", "value": "abc"}],
            "origins": [{"origin": "https://example.com"}],
            "updated_at": 1234567890.0,
        }
        path.write_text(json.dumps(data))

        s = load_state()
        assert len(s.cookies) == 1
        assert s.cookies[0]["name"] == "session"
        assert len(s.origins) == 1
        assert s.updated_at == 1234567890.0

    def test_corrupt_json(self, state_home):
        path = state_path()
        path.parent.mkdir(parents=True)
        path.write_text("{bad json!")
        s = load_state()
        assert s.cookies == []

    def test_non_dict_json(self, state_home):
        path = state_path()
        path.parent.mkdir(parents=True)
        path.write_text("[1, 2, 3]")
        s = load_state()
        assert s.cookies == []


class TestSaveState:
    def test_roundtrip(self, state_home):
        state = BrowserState(
            cookies=[{"name": "a", "value": "1"}],
            origins=[{"origin": "https://example.com"}],
        )
        save_state(state)

        loaded = load_state()
        assert loaded.cookies == state.cookies
        assert loaded.origins == state.origins
        assert loaded.updated_at > 0

    def test_creates_parent_dirs(self, state_home):
        """The state root does not exist yet; the write makes the whole chain."""
        assert not state_home.exists()
        save_state(BrowserState(cookies=[{"x": 1}]))
        assert state_path().is_file()

    def test_writes_under_the_state_root(self, state_home, tmp_home):
        """[R168], on the WRITE side: the bytes land in the state tree, never the data one."""
        save_state(BrowserState(cookies=[{"name": "session"}]))
        assert (state_home / "browser-state" / "context.json").is_file()
        assert list((tmp_home / "data").rglob("browser-state")) == []

    def test_updates_timestamp(self, state_home):
        state = BrowserState()
        assert state.updated_at == 0.0
        save_state(state)
        assert state.updated_at > 0


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
