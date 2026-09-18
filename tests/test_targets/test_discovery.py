"""Tests for target discovery and resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.agent_ref import PSEUDO_AGENT_NAMES
from kanibako.targets import discover_targets, get_target, resolve_target
from kanibako.targets.base import AgentInstall, Target
from kanibako.targets.no_agent import NoAgentTarget

from tests.support.filenames import CONFIG_FILENAME


class _FakeTarget(Target):
    """Minimal concrete Target for testing."""

    _detect_result: AgentInstall | None = None

    @property
    def name(self) -> str:
        return "fake"

    @property
    def display_name(self) -> str:
        return "Fake Agent"

    def detect(self):
        return self._detect_result

    def binary_mounts(self, install):
        return []

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []


class _DetectableTarget(_FakeTarget):
    """Target whose detect() returns a valid install."""

    @property
    def name(self) -> str:
        return "detectable"

    @property
    def display_name(self) -> str:
        return "Detectable Agent"

    def detect(self):
        return AgentInstall(name="detectable", binary=Path("/bin/x"), install_dir=Path("/opt/x"))


class _NoNameTarget(_FakeTarget):
    """Target with an empty meta.agent.<agent>.name (invalid — has no store dir)."""

    @property
    def name(self) -> str:
        return ""

    def detect(self):
        return None


class _ReservedNameTarget(_FakeTarget):
    """Target claiming a RESERVED pseudo-agent name (keyspec §2d)."""

    @property
    def name(self) -> str:
        return "shell"

    def detect(self):
        return None


def _mock_entry_point(name: str, cls: type) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


class TestDiscoverTargets:
    def test_discovers_registered_targets(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert "fake" in targets
        assert targets["fake"] is _FakeTarget

    def test_empty_when_no_targets(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert targets == {}

    def test_multiple_targets(self):
        ep1 = _mock_entry_point("a", _FakeTarget)
        ep2 = _mock_entry_point("b", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            targets = discover_targets()
        assert len(targets) == 2
        assert "a" in targets
        assert "b" in targets


class TestBrokenEntryPointIsSkipped:
    """A plugin that cannot IMPORT must not take the whole CLI down with it.

    Regression, measured 2026-08-17: a stale ``kanibako-agent-goose`` wheel raised
    ``ImportError: cannot import name 'BindDefault'`` from its own module body.
    ``ep.load()`` was unguarded, so that escaped ``discover_targets`` as a raw
    traceback and killed every command that resolves an agent — including
    ``kanibako setup``, which calls discovery too, so the documented cure was
    unreachable and hand-editing site-packages was the only way back in.
    """

    @staticmethod
    def _broken_entry_point(name: str, exc: Exception) -> MagicMock:
        ep = MagicMock()
        ep.name = name
        ep.load.side_effect = exc
        return ep

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        # The warning is once-per-process, so reset the memo or test order decides
        # whether a later test sees the message.
        from kanibako.targets import _EP_LOAD_FAILED
        _EP_LOAD_FAILED.clear()
        yield
        _EP_LOAD_FAILED.clear()

    def test_broken_plugin_does_not_abort_discovery(self):
        broken = self._broken_entry_point(
            "goose",
            ImportError("cannot import name 'BindDefault' from 'kanibako.targets.base'"),
        )
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[broken, good]):
            targets = discover_targets()
        # The healthy agent survives; the broken one is simply absent.
        assert targets["fake"] is _FakeTarget
        assert "goose" not in targets

    def test_broken_plugin_is_reported_on_stderr_with_the_cure(self, capsys):
        broken = self._broken_entry_point("goose", ImportError("cannot import name 'BindDefault'"))
        with patch("kanibako.targets.entry_points", return_value=[broken]):
            discover_targets()
        err = capsys.readouterr().err
        # Named, not swallowed: a pip-installed adapter that cannot load is a
        # broken install the user has to know about.
        assert "goose" in err
        assert "SKIPPED" in err
        assert "ImportError" in err
        assert "BindDefault" in err
        # And it says what still works, so the user does not conclude the CLI is dead.
        assert "kanibako setup" in err

    def test_warning_is_emitted_once_per_process(self, capsys):
        broken = self._broken_entry_point("goose", ImportError("boom"))
        with patch("kanibako.targets.entry_points", return_value=[broken]):
            discover_targets()
            discover_targets()
            discover_targets()
        # discover_targets runs several times per command; the paragraph must not
        # be repeated each time or it buries the real output.
        assert capsys.readouterr().err.count("failed to load") == 1

    def test_a_plugin_raising_a_non_import_error_is_also_survived(self):
        """The guard is deliberately broad: any exception from third-party code."""
        broken = self._broken_entry_point("exploding", RuntimeError("bad metaclass"))
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[broken, good]):
            targets = discover_targets()
        assert "fake" in targets
        assert "exploding" not in targets


class TestReservedPseudoAgentNameIsRefused:
    """A plugin may not register a PSEUDO-AGENT name (keyspec §2d).

    *"Their names are RESERVED and MUST be refused to any agent, persona, or harness."*
    ``default`` and ``shell`` already own an ``agent.<name>.*`` cascade slot and a store
    dir in the keyspace itself, so a harness answering to one would own them too.
    Before 2026-09-18 ``discover_targets`` keyed straight off ``ep.name`` with no name
    validation at all.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        # Once-per-process, like the load-failure warning — reset it or test order
        # decides whether a later test sees the message.
        from kanibako.targets import _RESERVED_NAME_WARNED
        _RESERVED_NAME_WARNED.clear()
        yield
        _RESERVED_NAME_WARNED.clear()

    def test_the_reserved_set_is_not_empty(self):
        # The sweep below is parametrized over the set; an empty one would pass
        # vacuously.  The oracle is the spec's own two subheadings.
        assert PSEUDO_AGENT_NAMES == {"default", "shell"}

    @pytest.mark.parametrize("name", sorted(PSEUDO_AGENT_NAMES))
    def test_entry_point_with_a_reserved_name_is_skipped(self, name):
        bad = _mock_entry_point(name, _FakeTarget)
        good = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad, good]):
            targets = discover_targets()
        assert name not in targets
        # SKIPPED, not fatal: discovery runs on every command, so one plugin's bad
        # name must not take the CLI down with it.
        assert targets["fake"] is _FakeTarget

    def test_the_refusal_names_the_name_and_says_it_was_skipped(self, capsys):
        bad = _mock_entry_point("shell", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
        err = capsys.readouterr().err
        assert "'shell'" in err
        assert "RESERVED" in err
        assert "SKIPPED" in err

    def test_the_warning_is_emitted_once_per_process(self, capsys):
        bad = _mock_entry_point("shell", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[bad]):
            discover_targets()
            discover_targets()
            discover_targets()
        assert capsys.readouterr().err.count("RESERVED") == 1

    def test_an_ordinary_name_still_registers(self):
        # Non-vacuity: prove the gate above refuses on the NAME, not on everything.
        ep = _mock_entry_point("shellfish", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            targets = discover_targets()
        assert targets["shellfish"] is _FakeTarget

    def test_require_meta_name_refuses_a_reserved_harness_name(self):
        # The floor under a Target that reaches a caller without going through
        # discovery.  It RAISES rather than skipping: by here it is the target
        # being launched, and there is nothing left to fall back to.
        from kanibako.targets import _require_meta_name

        with pytest.raises(ValueError, match="RESERVED pseudo-agent name"):
            _require_meta_name(_ReservedNameTarget())


class TestGetTarget:
    def test_found(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            cls = get_target("fake")
        assert cls is _FakeTarget

    def test_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError, match="Unknown target 'nope'"):
                get_target("nope")


class TestResolveTarget:
    def test_resolve_by_name(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target("fake")
        assert isinstance(t, _FakeTarget)

    def test_resolve_by_name_not_found(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            with pytest.raises(KeyError):
                resolve_target("missing")

    def test_auto_detect(self):
        ep = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_skips_undetectable(self):
        ep1 = _mock_entry_point("fake", _FakeTarget)
        ep2 = _mock_entry_point("detectable", _DetectableTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep1, ep2]):
            t = resolve_target()
        assert isinstance(t, _DetectableTarget)

    def test_auto_detect_none_found_returns_no_agent(self):
        ep = _mock_entry_point("fake", _FakeTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            t = resolve_target()
        assert isinstance(t, NoAgentTarget)

    def test_auto_detect_empty_returns_no_agent(self):
        with patch("kanibako.targets.entry_points", return_value=[]):
            t = resolve_target()
        assert isinstance(t, NoAgentTarget)

    def test_resolve_by_name_requires_meta_name(self):
        # meta.agent.<agent>.name (the plugin's `name`) is REQUIRED; an empty
        # name has no resolvable store dir / cascade key -> fail loudly.
        ep = _mock_entry_point("blank", _NoNameTarget)
        with patch("kanibako.targets.entry_points", return_value=[ep]):
            with pytest.raises(ValueError, match=r"meta\.agent\.<agent>\.name"):
                resolve_target("blank")


# ── Helpers for file-drop plugin tests ──────────────────────────────

_PLUGIN_SOURCE = '''\
from kanibako.targets.base import Target


class MyFilePlugin(Target):
    @property
    def name(self):
        return "{name}"

    @property
    def display_name(self):
        return "File Plugin {name}"

    def detect(self):
        return None

    def binary_mounts(self, install):
        return []

    def refresh_credentials(self, home):
        pass

    def writeback_credentials(self, home):
        pass

    def build_cli_args(self, **kwargs):
        return []
'''


def _write_plugin(directory: Path, filename: str, name: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(_PLUGIN_SOURCE.format(name=name))


class TestDirectoryPluginDiscovery:
    """Tests for file-drop plugin directories."""

    @pytest.fixture(autouse=True)
    def _isolate_config(self, tmp_path, monkeypatch):
        """Pin the CONFIG side of the user plugin dir, which is ``config.data``/plugins.

        ⚑ ``XDG_DATA_HOME`` alone no longer determines where the scan looks ([R155]), so an
        unisolated ``XDG_CONFIG_HOME`` — or the site base under ``/etc`` — would let whatever
        config happens to be on the box running the suite move the directory away from the one
        these tests write into.
        """
        import kanibako.settings.config as cfg_mod

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
        monkeypatch.setattr(cfg_mod, "config_base_path", lambda: tmp_path / "etc_absent.cfg")

    def test_user_dir_follows_repointed_config_data(self, tmp_path, monkeypatch):
        """[R155]: the user scan follows ``config.data``, never the XDG base plus a leaf.

        On the hardcoded reading the configured store was not scanned at all, so a plugin
        dropped there silently did not exist — and the stale default location won instead.
        """
        store = tmp_path / "srv" / "custom_store"
        _write_plugin(store / "plugins", "repointed.py", "repointed")
        config_home = tmp_path / "cfg"
        config_home.mkdir(parents=True, exist_ok=True)
        (config_home / CONFIG_FILENAME).write_text(f'config:\n  data: "{store}"\n')
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
        # The abandoned default location holds a DIFFERENT plugin, so the old reading does
        # not merely come up empty — it discovers the wrong store's plugin and says nothing.
        _write_plugin(tmp_path / "data" / "kanibako" / "plugins", "stale.py", "stale")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()

        assert "repointed" in targets
        assert "stale" not in targets

    def test_discover_user_dir_plugins(self, tmp_path, monkeypatch):
        """Plugins in user data dir are discovered."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "myplugin.py", "myplugin")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert "myplugin" in targets

    def test_reserved_name_file_drop_plugin_is_skipped(self, tmp_path, monkeypatch):
        """The reservation holds at the file-drop tier too (keyspec §2d).

        All three discovery tiers assign through ``targets._register``, so this is the
        same gate the entry-point tier uses — pinned separately because a per-tier copy
        is exactly how a rule comes to hold in one place and not another.
        """
        from kanibako.targets import _RESERVED_NAME_WARNED

        _RESERVED_NAME_WARNED.clear()
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "shellplugin.py", "shell")
        _write_plugin(user_plugins, "okplugin.py", "okplugin")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        _RESERVED_NAME_WARNED.clear()

        assert "shell" not in targets
        assert "okplugin" in targets  # the healthy neighbour still lands

    def test_discover_project_dir_plugins(self, tmp_path):
        """Plugins in project box_data/plugins/ are discovered."""
        proj = tmp_path / "myproject"
        proj_plugins = proj / "box_data" / "plugins"
        _write_plugin(proj_plugins, "projplugin.py", "projplugin")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets(project_path=proj)
        assert "projplugin" in targets

    def test_project_plugin_overrides_user_plugin(self, tmp_path, monkeypatch):
        """Project-level plugin overrides user-level with same name."""
        # User plugin named "common" from user_shared.py
        user_plugins = tmp_path / "data" / "kanibako" / "plugins"
        _write_plugin(user_plugins, "user_shared.py", "common")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

        # Project plugin also named "common" from proj_shared.py
        proj = tmp_path / "project"
        proj_plugins = proj / "box_data" / "plugins"
        _write_plugin(proj_plugins, "proj_shared.py", "common")

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets(project_path=proj)

        assert "common" in targets
        # The class should come from the project dir, not user dir.
        # Different filenames produce different module names.
        cls = targets["common"]
        assert cls.__module__ == "kanibako_plugin_proj_shared"

    def test_underscore_files_skipped(self, tmp_path, monkeypatch):
        """Files starting with _ are not loaded as plugins."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        _write_plugin(user_plugins, "_private.py", "private")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert "private" not in targets

    def test_invalid_plugin_gracefully_handled(self, tmp_path, monkeypatch):
        """Invalid Python files don't crash discovery."""
        user_plugins = tmp_path / "kanibako" / "plugins"
        user_plugins.mkdir(parents=True)
        (user_plugins / "broken.py").write_text("raise RuntimeError('boom')")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

        with patch("kanibako.targets.entry_points", return_value=[]):
            # Should not raise
            targets = discover_targets()
        assert "broken" not in targets

    def test_discover_targets_default_no_project(self):
        """discover_targets() without project_path works (backward compat)."""
        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        # Should not raise; may be empty or contain module-scanned targets
        assert isinstance(targets, dict)

    def test_nonexistent_directory_is_ignored(self, tmp_path, monkeypatch):
        """Nonexistent plugin directories are silently skipped."""
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "nonexistent"))

        with patch("kanibako.targets.entry_points", return_value=[]):
            targets = discover_targets()
        assert isinstance(targets, dict)

    def test_get_target_with_project_path(self, tmp_path):
        """get_target accepts project_path parameter."""
        proj = tmp_path / "proj"
        proj_plugins = proj / "box_data" / "plugins"
        _write_plugin(proj_plugins, "custom.py", "custom")

        with patch("kanibako.targets.entry_points", return_value=[]):
            cls = get_target("custom", project_path=proj)
        assert cls is not None

    def test_resolve_target_with_project_path(self, tmp_path):
        """resolve_target passes project_path through."""
        proj = tmp_path / "proj"
        proj_plugins = proj / "box_data" / "plugins"
        _write_plugin(proj_plugins, "myplugin.py", "myplugin")

        with patch("kanibako.targets.entry_points", return_value=[]):
            # resolve by name
            t = resolve_target("myplugin", project_path=proj)
        assert t.name == "myplugin"
