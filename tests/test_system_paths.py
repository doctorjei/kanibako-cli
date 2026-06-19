"""Tests for the system.* config tier (settings-framework path resolution).

Covers ``paths.resolve_system_paths`` (the resolver-backed system config tier),
``config.load_config`` populating ``system_paths`` from a flat ``[system]``
table, and ``load_std_paths`` reproducing today's default directory layout.

Keys are the bare ``system.<leaf>`` form (the ``.path`` segment was dropped in
the system.* reorg).  The OLD per-project box store resolves under the
transitional pseudo-key ``system._boxes`` (the ``StandardPaths.boxes`` alias).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from kanibako.config import load_config
from kanibako.paths import (
    SYSTEM_PATH_DEFAULTS,
    load_system_config,
    resolve_system_paths,
    resolve_xdg,
)
from kanibako.settings_resolve import SettingsError


class TestResolveSystemPathsDefaults:
    def test_defaults_match_layout(self, tmp_path):
        """Empty config → the data tree hangs off $XDG_DATA_HOME/kanibako."""
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        base = tmp_path / "kanibako"
        assert resolved["system.data"] == base
        assert resolved["system.backup"] == base / "backup"
        assert resolved["system.agents"] == base / "agents"
        assert resolved["system.channels"] == base / "channels"
        assert resolved["system.global"] == base / "global"
        assert resolved["system.base_template"] == base / "global" / "base_template"
        assert resolved["system.settings"] == base / "global" / "settings.yaml"
        assert resolved["system.primary_workset"] == base / "primary_workset"
        assert resolved["system.registry"] == base / "global" / "registry.yaml"
        # Transitional box store (OLD std.boxes location, unchanged in Phase 3).
        assert resolved["system._boxes"] == base / "boxes"

    def test_channels_skeleton_resolves(self, tmp_path):
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        channels = tmp_path / "kanibako" / "channels"
        assert resolved["system.channels.commons"] == channels / "commons"
        assert resolved["system.channels.chat"] == channels / "chat"
        assert resolved["system.channels.broadcast"] == channels / "chat" / "broadcast.md"
        assert resolved["system.channels.mailboxes"] == channels / "mailboxes"
        assert resolved["system.channels.share"] == channels / "share"

    def test_cache_and_runtime_not_under_data(self, tmp_path, monkeypatch):
        """cache/runtime live under their OWN XDG bases, NOT under data."""
        cache_home = tmp_path / "cache"
        run_dir = tmp_path / "run"
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
        resolved = resolve_system_paths(
            {}, data_home=tmp_path / "data", home=tmp_path,
        )
        assert resolved["system.cache"] == cache_home / "kanibako"
        assert resolved["system.runtime"] == run_dir / "kanibako"

    def test_returns_every_declared_key(self, tmp_path):
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        # Every declared default key plus the transitional system._boxes.
        assert set(resolved) == set(SYSTEM_PATH_DEFAULTS) | {"system._boxes"}


class TestResolveSystemPathsOverrides:
    def test_data_override_tracks_dependents(self, tmp_path):
        """Overriding data moves dependents (which @-ref system.data)."""
        resolved = resolve_system_paths(
            {"system.data": "$XDG_DATA_HOME/custom"},
            data_home=tmp_path,
            home=tmp_path,
        )
        custom = tmp_path / "custom"
        assert resolved["system.data"] == custom
        assert resolved["system.agents"] == custom / "agents"
        assert resolved["system._boxes"] == custom / "boxes"
        assert resolved["system.global"] == custom / "global"

    def test_absolute_leaf_override_isolated(self, tmp_path):
        """An absolute agents override does not perturb the other keys."""
        resolved = resolve_system_paths(
            {"system.agents": "/srv/agents"},
            data_home=tmp_path,
            home=tmp_path,
        )
        assert resolved["system.agents"] == Path("/srv/agents")
        # Others keep their defaults under $XDG_DATA_HOME/kanibako.
        base = tmp_path / "kanibako"
        assert resolved["system.data"] == base
        assert resolved["system.channels"] == base / "channels"

    def test_tilde_expands_to_home(self, tmp_path):
        home = tmp_path / "h"
        resolved = resolve_system_paths(
            {"system.data": "~/.kani"}, data_home=tmp_path, home=home,
        )
        assert resolved["system.data"] == home / ".kani"

    def test_unknown_ref_raises(self, tmp_path):
        with pytest.raises(SettingsError):
            resolve_system_paths(
                {"system.agents": "@system.nope/x"},
                data_home=tmp_path,
                home=tmp_path,
            )


class TestLoadConfigSystemPaths:
    def test_system_table_populates(self, tmp_path):
        toml = tmp_path / "kanibako.yaml"
        toml.write_text('system:\n  agents: "/x"\n')
        cfg = load_config(toml)
        assert cfg.system_paths == {"system.agents": "/x"}

    def test_nested_system_subkey_flattens(self, tmp_path):
        toml = tmp_path / "kanibako.yaml"
        toml.write_text('system:\n  channels:\n    commons: "/c"\n')
        cfg = load_config(toml)
        assert cfg.system_paths == {"system.channels.commons": "/c"}

    def test_empty_config_has_no_system_paths(self, tmp_path):
        cfg = load_config(tmp_path / "absent.yaml")
        assert cfg.system_paths == {}


class TestLoadStdPathsParity:
    def test_default_layout_matches_data_path(self, tmp_home, config_file):
        """load_std_paths yields the renamed/re-pointed dirs by default."""
        from kanibako.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        assert std.data == std.data_path
        assert std.agents == std.data_path / "agents"
        assert std.channels == std.data_path / "channels"
        assert std.primary_workset == std.data_path / "primary_workset"
        assert std.global_dir == std.data_path / "global"
        assert std.base_template == std.data_path / "global" / "base_template"
        assert std.registry == std.data_path / "global" / "registry.yaml"
        # Transitional aliases (deleted in Phase 5).
        assert std.boxes == std.data_path / "boxes"
        assert std.comms == std.channels
        assert std.templates == std.base_template

    def test_deleted_share_aliases_raise(self, tmp_home, config_file):
        from kanibako.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        with pytest.raises(NotImplementedError):
            _ = std.share_ro
        with pytest.raises(NotImplementedError):
            _ = std.share_rw


class TestBoxesOverrideConsumers:
    """A ``system.data`` override is honored consistently by both project
    creation/listing AND the names.yaml reverse-lookup helpers (the transitional
    box store hangs off the resolved data dir).
    """

    def test_boxes_override_used_by_creation_and_lookup(self, tmp_home):
        """Creating a project under a custom data dir registers its box under
        ``<custom>/boxes/<name>``, and the reverse-lookup helpers find it there.
        """
        from kanibako.config import load_config
        from kanibako.paths import (
            _find_local_ancestor,
            _resolve_local_dir,
            iter_projects,
            load_std_paths,
            resolve_project,
        )

        custom_data = tmp_home / "srv_data"
        custom_boxes = custom_data / "boxes"

        # Write a config that overrides system.data to the custom dir.
        cf = tmp_home / "config" / "kanibako.yaml"
        cf.write_text(f'system:\n  data: "{custom_data}"\n')

        config = load_config(cf)
        assert config.system_paths == {"system.data": str(custom_data)}
        std = load_std_paths(config)
        assert std.boxes == custom_boxes

        # Create a project — its metadata dir must land under the custom boxes.
        workspace = tmp_home / "ws"
        workspace.mkdir()
        proj = resolve_project(std, config, str(workspace), initialize=True)
        assert proj.metadata_path.is_dir()
        assert proj.metadata_path == custom_boxes / proj.name

        # Reverse-lookup (path -> name -> dir) resolves under the custom dir.
        name, box_dir = _resolve_local_dir(
            std.data_path, str(workspace.resolve()), std.boxes,
        )
        assert name == proj.name
        assert box_dir == custom_boxes / proj.name

        # Deepest-ancestor lookup also keys off the custom boxes dir.
        sub = workspace / "src"
        sub.mkdir()
        ancestor = _find_local_ancestor(sub.resolve(), std.data_path, std.boxes)
        assert ancestor == workspace.resolve()

        # Listing enumerates the custom boxes dir.
        listed = {p.name for p, _ in iter_projects(std, config)}
        assert proj.name in listed


class TestResolveXdg:
    """The hardened XDG base-directory resolver (freedesktop spec)."""

    def test_absolute_env_value_honored(self, tmp_path, monkeypatch):
        abs_dir = tmp_path / "custom_data"
        monkeypatch.setenv("XDG_DATA_HOME", str(abs_dir))
        assert resolve_xdg("XDG_DATA_HOME", ".local/share") == abs_dir.resolve()

    def test_unset_uses_spec_default_under_home(self, tmp_path, monkeypatch):
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert resolve_xdg("XDG_CONFIG_HOME", ".config") == tmp_path / ".config"

    def test_relative_env_value_ignored_for_spec_default(
        self, tmp_path, monkeypatch, caplog
    ):
        """A relative XDG value is invalid → ignored → spec default used."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_STATE_HOME", "relative/state")
        with caplog.at_level(logging.WARNING, logger="kanibako.paths"):
            result = resolve_xdg("XDG_STATE_HOME", ".local/state")
        assert result == tmp_path / ".local/state"
        assert any("relative" in r.message.lower() for r in caplog.records)

    def test_empty_env_value_uses_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CACHE_HOME", "")
        assert resolve_xdg("XDG_CACHE_HOME", ".cache") == tmp_path / ".cache"

    def test_runtime_dir_absolute_honored(self, tmp_path, monkeypatch):
        abs_dir = tmp_path / "run"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(abs_dir))
        assert resolve_xdg("XDG_RUNTIME_DIR", None) == abs_dir.resolve()

    def test_runtime_dir_unset_falls_back_and_warns(self, monkeypatch, caplog):
        """No spec default → must fall back to a usable 0700 dir AND warn."""
        import kanibako.paths as paths_mod

        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        # Clear the process cache so this test sees a fresh selection+warn.
        monkeypatch.setattr(paths_mod, "_runtime_fallback_cache", {})
        with caplog.at_level(logging.WARNING, logger="kanibako.paths"):
            result = resolve_xdg("XDG_RUNTIME_DIR", None)
        # Fallback dir is real, owned, and 0700.
        assert result.is_dir()
        st = result.stat()
        assert st.st_uid == os.getuid()
        assert (st.st_mode & 0o777) == 0o700
        # Never silent.
        assert any(
            "XDG_RUNTIME_DIR" in r.message and "falling back" in r.message
            for r in caplog.records
        )

    def test_runtime_dir_relative_falls_back_and_warns(self, monkeypatch, caplog):
        import kanibako.paths as paths_mod

        monkeypatch.setenv("XDG_RUNTIME_DIR", "relative/run")
        monkeypatch.setattr(paths_mod, "_runtime_fallback_cache", {})
        with caplog.at_level(logging.WARNING, logger="kanibako.paths"):
            result = resolve_xdg("XDG_RUNTIME_DIR", None)
        assert result.is_dir()
        # Both the relative-ignored warning and the fallback warning fire.
        msgs = " ".join(r.message.lower() for r in caplog.records)
        assert "relative" in msgs
        assert "falling back" in msgs

    def test_runtime_dir_fallback_cached_within_process(self, monkeypatch):
        """Repeated resolution returns the SAME fallback dir (no temp leak)."""
        import kanibako.paths as paths_mod

        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(paths_mod, "_runtime_fallback_cache", {})
        first = resolve_xdg("XDG_RUNTIME_DIR", None)
        second = resolve_xdg("XDG_RUNTIME_DIR", None)
        assert first == second


class TestLoadSystemConfig:
    """The 3-file CONFIG loader: config_base < user-global < config_required.

    ``config_base_path``/``config_required_path`` point at ``/etc/kanibako`` in
    production; tests redirect them at tmp files via monkeypatch so the cascade
    can be exercised hermetically.
    """

    def _redirect(self, monkeypatch, base: Path, required: Path) -> None:
        """Point the /etc base+required CONFIG paths at tmp files.

        ``load_system_config`` imports these lazily from ``kanibako.config``, so
        patching the source module catches every call.
        """
        import kanibako.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "config_base_path", lambda: base)
        monkeypatch.setattr(cfg_mod, "config_required_path", lambda: required)

    def test_only_user_global_still_works(self, tmp_path, monkeypatch):
        """Back-compat: a user with only ~/.config/kanibako.yaml (absent /etc
        base+required) gets exactly the prior behavior."""
        base = tmp_path / "config_base.yaml"       # absent
        required = tmp_path / "config_required.yaml"  # absent
        self._redirect(monkeypatch, base, required)

        user = tmp_path / "kanibako.yaml"
        user.write_text('system:\n  agents: "/u/agents"\n')

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.agents"] == Path("/u/agents")
        # Unset keys fall back to defaults under $XDG_DATA_HOME/kanibako.
        assert resolved["system.data"] == tmp_path / "kanibako"

    def test_all_files_absent_yields_defaults(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"  # absent
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert set(resolved) == set(SYSTEM_PATH_DEFAULTS) | {"system._boxes"}
        assert resolved["system.data"] == tmp_path / "kanibako"

    def test_user_wins_over_base(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"  # absent
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  agents: "/base/agents"\n')
        user.write_text('system:\n  agents: "/user/agents"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.agents"] == Path("/user/agents")

    def test_required_wins_over_user_and_base(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  agents: "/base/agents"\n')
        user.write_text('system:\n  agents: "/user/agents"\n')
        required.write_text('system:\n  agents: "/req/agents"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        # Required is non-overridable → wins over BOTH user and base.
        assert resolved["system.agents"] == Path("/req/agents")

    def test_base_supplies_value_absent_from_user(self, tmp_path, monkeypatch):
        """A base-only key is honored when the user file omits it."""
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"  # absent
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  channels: "/base/channels"\n')
        user.write_text('system:\n  agents: "/user/agents"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.channels"] == Path("/base/channels")
        assert resolved["system.agents"] == Path("/user/agents")

    def test_per_key_independent_cascade(self, tmp_path, monkeypatch):
        """Each leaf cascades independently — required pins one key while the
        user still sets another."""
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  data: "/base/data"\n')
        user.write_text(
            'system:\n  data: "/user/data"\n  agents: "/user/agents"\n'
        )
        required.write_text('system:\n  data: "/req/data"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.data"] == Path("/req/data")    # required wins
        assert resolved["system.agents"] == Path("/user/agents")  # user kept


class TestResolveSystemPathsXdgCtx:
    """``resolve_system_paths`` populates the full XDG var set into ctx."""

    def test_xdg_config_state_cache_refs_resolve(self, tmp_path, monkeypatch):
        """A system path expression referencing $XDG_CONFIG_HOME resolves."""
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_home = tmp_path / "cfg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
        resolved = resolve_system_paths(
            {"system.agents": "$XDG_CONFIG_HOME/kani-agents"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["system.agents"] == cfg_home / "kani-agents"

    def test_xdg_runtime_ref_resolves(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
        resolved = resolve_system_paths(
            {"system.agents": "$XDG_RUNTIME_DIR/kani"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["system.agents"] == run_dir / "kani"
