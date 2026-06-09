"""Tests for the system.path.* tier (settings-framework path resolution).

Covers ``paths.resolve_system_paths`` (the resolver-backed system path tier),
``config.load_config`` populating ``system_paths`` from a ``[system.path]``
table, and ``load_std_paths`` reproducing today's default directory layout.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.config import load_config
from kanibako.paths import SYSTEM_PATH_DEFAULTS, resolve_system_paths
from kanibako.settings_resolve import SettingsError


class TestResolveSystemPathsDefaults:
    def test_defaults_match_legacy_layout(self, tmp_path):
        """Empty config → all dirs hang off $XDG_DATA_HOME/kanibako."""
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        base = tmp_path / "kanibako"
        assert resolved["system.path.data"] == base
        assert resolved["system.path.boxes"] == base / "boxes"
        assert resolved["system.path.crabs"] == base / "crabs"
        assert resolved["system.path.comms"] == base / "comms"
        assert resolved["system.path.templates"] == base / "templates"
        assert resolved["system.path.ws_hints"] == base / "worksets.toml"

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
        assert resolved["system.path.crabs"] == custom / "crabs"

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
        assert resolved["system.path.crabs"] == base / "crabs"

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
        toml = tmp_path / "kanibako.toml"
        toml.write_text('[system.path]\nboxes = "/x"\n')
        cfg = load_config(toml)
        assert cfg.system_paths == {"system.path.boxes": "/x"}

    def test_empty_config_has_no_system_paths(self, tmp_path):
        cfg = load_config(tmp_path / "absent.toml")
        assert cfg.system_paths == {}


class TestLoadStdPathsParity:
    def test_default_layout_matches_data_path(self, tmp_home, config_file):
        """load_std_paths yields std.<dir> == std.data_path / <dir> by default."""
        from kanibako.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        assert std.boxes == std.data_path / "boxes"
        assert std.crabs == std.data_path / "crabs"
        assert std.comms == std.data_path / "comms"
        assert std.templates == std.data_path / "templates"
        assert std.ws_hints == std.data_path / "worksets.toml"
