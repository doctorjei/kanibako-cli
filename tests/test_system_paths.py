"""Tests for the system.path.* tier (settings-framework path resolution).

Covers ``paths.resolve_system_paths`` (the resolver-backed system path tier),
``config.load_config`` populating ``system_paths`` from a ``[system.path]``
table, and ``load_std_paths`` reproducing today's default directory layout.
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
    def test_defaults_match_legacy_layout(self, tmp_path):
        """Empty config → all dirs hang off $XDG_DATA_HOME/kanibako."""
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        base = tmp_path / "kanibako"
        assert resolved["system.path.data"] == base
        assert resolved["system.path.boxes"] == base / "boxes"
        assert resolved["system.path.agents"] == base / "agents"
        assert resolved["system.path.comms"] == base / "comms"
        assert resolved["system.path.templates"] == base / "templates"
        assert resolved["system.path.ws_hints"] == base / "worksets.yaml"

    def test_returns_every_declared_key(self, tmp_path):
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        assert set(resolved) == set(SYSTEM_PATH_DEFAULTS)


class TestResolveSystemPathsOverrides:
    def test_data_override_tracks_dependents(self, tmp_path):
        """Overriding data moves boxes (which @-refs system.path.data)."""
        resolved = resolve_system_paths(
            {"system.path.data": "$XDG_DATA_HOME/custom"},
            data_home=tmp_path,
            home=tmp_path,
        )
        custom = tmp_path / "custom"
        assert resolved["system.path.data"] == custom
        assert resolved["system.path.boxes"] == custom / "boxes"
        assert resolved["system.path.agents"] == custom / "agents"

    def test_absolute_leaf_override_isolated(self, tmp_path):
        """An absolute boxes override does not perturb the other keys."""
        resolved = resolve_system_paths(
            {"system.path.boxes": "/srv/boxes"},
            data_home=tmp_path,
            home=tmp_path,
        )
        assert resolved["system.path.boxes"] == Path("/srv/boxes")
        # Others keep their defaults under $XDG_DATA_HOME/kanibako.
        base = tmp_path / "kanibako"
        assert resolved["system.path.data"] == base
        assert resolved["system.path.agents"] == base / "agents"

    def test_tilde_expands_to_home(self, tmp_path):
        home = tmp_path / "h"
        resolved = resolve_system_paths(
            {"system.path.data": "~/.kani"}, data_home=tmp_path, home=home,
        )
        assert resolved["system.path.data"] == home / ".kani"

    def test_unknown_ref_raises(self, tmp_path):
        with pytest.raises(SettingsError):
            resolve_system_paths(
                {"system.path.boxes": "@system.path.nope/x"},
                data_home=tmp_path,
                home=tmp_path,
            )


class TestLoadConfigSystemPaths:
    def test_system_path_table_populates(self, tmp_path):
        toml = tmp_path / "kanibako.yaml"
        toml.write_text('system:\n  path:\n    boxes: "/x"\n')
        cfg = load_config(toml)
        assert cfg.system_paths == {"system.path.boxes": "/x"}

    def test_empty_config_has_no_system_paths(self, tmp_path):
        cfg = load_config(tmp_path / "absent.yaml")
        assert cfg.system_paths == {}


class TestLoadStdPathsParity:
    def test_default_layout_matches_data_path(self, tmp_home, config_file):
        """load_std_paths yields std.<dir> == std.data_path / <dir> by default."""
        from kanibako.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        assert std.boxes == std.data_path / "boxes"
        assert std.agents == std.data_path / "agents"
        assert std.comms == std.data_path / "comms"
        assert std.templates == std.data_path / "templates"
        assert std.ws_hints == std.data_path / "worksets.yaml"


class TestBoxesOverrideConsumers:
    """A ``system.path.boxes`` override is honored consistently by both
    project creation/listing AND the names.yaml reverse-lookup helpers.
    """

    def test_boxes_override_used_by_creation_and_lookup(self, tmp_home):
        """Creating a project under a custom boxes dir registers it there, and
        the reverse-lookup helpers find it at ``<custom>/<name>``.
        """
        from kanibako.config import load_config
        from kanibako.paths import (
            _find_local_ancestor,
            _resolve_local_dir,
            iter_projects,
            load_std_paths,
            resolve_project,
        )

        custom_boxes = tmp_home / "srv_boxes"

        # Write a config that overrides system.path.boxes to the custom dir.
        cf = tmp_home / "config" / "kanibako.yaml"
        cf.write_text(f'system:\n  path:\n    boxes: "{custom_boxes}"\n')

        config = load_config(cf)
        assert config.system_paths == {"system.path.boxes": str(custom_boxes)}
        std = load_std_paths(config)
        assert std.boxes == custom_boxes

        # Create a project — its metadata dir must land under the custom boxes.
        workspace = tmp_home / "ws"
        workspace.mkdir()
        proj = resolve_project(std, config, str(workspace), initialize=True)
        assert proj.metadata_path.is_dir()
        assert proj.metadata_path == custom_boxes / proj.name
        # Nothing was created under the default data_path/boxes.
        assert not (std.data_path / "boxes").exists()

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
        user.write_text('system:\n  path:\n    boxes: "/u/boxes"\n')

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.path.boxes"] == Path("/u/boxes")
        # Unset keys fall back to defaults under $XDG_DATA_HOME/kanibako.
        assert resolved["system.path.data"] == tmp_path / "kanibako"

    def test_all_files_absent_yields_defaults(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"  # absent
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert set(resolved) == set(SYSTEM_PATH_DEFAULTS)
        assert resolved["system.path.data"] == tmp_path / "kanibako"

    def test_user_wins_over_base(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"  # absent
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  path:\n    boxes: "/base/boxes"\n')
        user.write_text('system:\n  path:\n    boxes: "/user/boxes"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.path.boxes"] == Path("/user/boxes")

    def test_required_wins_over_user_and_base(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  path:\n    boxes: "/base/boxes"\n')
        user.write_text('system:\n  path:\n    boxes: "/user/boxes"\n')
        required.write_text('system:\n  path:\n    boxes: "/req/boxes"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        # Required is non-overridable → wins over BOTH user and base.
        assert resolved["system.path.boxes"] == Path("/req/boxes")

    def test_base_supplies_value_absent_from_user(self, tmp_path, monkeypatch):
        """A base-only key is honored when the user file omits it."""
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"  # absent
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  path:\n    agents: "/base/agents"\n')
        user.write_text('system:\n  path:\n    boxes: "/user/boxes"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.path.agents"] == Path("/base/agents")
        assert resolved["system.path.boxes"] == Path("/user/boxes")

    def test_per_key_independent_cascade(self, tmp_path, monkeypatch):
        """Each leaf cascades independently — required pins one key while the
        user still sets another."""
        base = tmp_path / "config_base.yaml"
        required = tmp_path / "config_required.yaml"
        user = tmp_path / "kanibako.yaml"
        base.write_text('system:\n  path:\n    data: "/base/data"\n')
        user.write_text(
            'system:\n  path:\n    data: "/user/data"\n    boxes: "/user/boxes"\n'
        )
        required.write_text('system:\n  path:\n    data: "/req/data"\n')
        self._redirect(monkeypatch, base, required)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.path.data"] == Path("/req/data")   # required wins
        assert resolved["system.path.boxes"] == Path("/user/boxes")  # user kept


class TestResolveSystemPathsXdgCtx:
    """``resolve_system_paths`` populates the full XDG var set into ctx."""

    def test_xdg_config_state_cache_refs_resolve(self, tmp_path, monkeypatch):
        """A system path expression referencing $XDG_CONFIG_HOME resolves."""
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_home = tmp_path / "cfg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
        resolved = resolve_system_paths(
            {"system.path.boxes": "$XDG_CONFIG_HOME/kani-boxes"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["system.path.boxes"] == cfg_home / "kani-boxes"

    def test_xdg_runtime_ref_resolves(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
        resolved = resolve_system_paths(
            {"system.path.boxes": "$XDG_RUNTIME_DIR/kani"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["system.path.boxes"] == run_dir / "kani"
