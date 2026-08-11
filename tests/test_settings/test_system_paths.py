"""Tests for the two-layer path resolution (block #3a — settings-keyspace §1).

Covers ``paths.resolve_config_paths`` (the Layer-1 ``config.*`` foundation),
``paths.resolve_system_paths`` (Layer-1 foundation + the Layer-2 ``system.*``
path settings, split by prefix), ``config.load_config`` populating
``config_paths`` from the ``[config]`` + ``[system]`` tables, and
``load_std_paths`` reproducing today's default directory layout.

Keys are the FULL dotted form: Layer-1 ``config.{data,settings,agents,
primary_workset,registry}`` (the bootstrap foundation) + Layer-2 ``system.*``
path settings (channelroot/template/canon/backup/cache/runtime + channels.*).
``config.global`` is ELIMINATED.  The OLD per-project box store resolves under
the transitional pseudo-key ``system._boxes`` (the ``StandardPaths.boxes`` alias).

EQUIVALENCE ORACLE: the RESOLVED host paths are byte-identical to the pre-reshape
build for the default config (modulo the dropped ``system.global`` field) — the 5
extracted paths simply re-key ``system.{5}`` → ``config.{5}`` with the same values.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from kanibako.settings.config import load_config
from kanibako.settings.paths import (
    CONFIG_PATH_DEFAULTS,
    SYSTEM_PATH_DEFAULTS,
    host_xdg_map,
    load_system_config,
    resolve_config_paths,
    resolve_system_paths,
    resolve_xdg,
)
from kanibako.settings.settings_resolve import SettingsError


class TestResolveSystemPathsDefaults:
    def test_defaults_match_layout(self, tmp_path):
        """Empty config → the data tree hangs off $XDG_DATA_HOME/kanibako.

        Layer-1 ``config.*`` foundation + Layer-2 ``system.*`` path settings,
        resolved to the SAME host paths the pre-reshape ``system.{5}`` build
        produced (equivalence oracle; ``system.global`` is dropped).
        """
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        base = tmp_path / "kanibako"
        # Layer-1 config-key foundation (was system.{data,agents,settings,
        # primary_workset,registry} — same resolved values, re-keyed config.*).
        assert resolved["config.data"] == base
        assert resolved["config.agents"] == base / "agents"
        assert resolved["config.settings"] == base / "global" / "settings.yaml"
        assert resolved["config.primary_workset"] == base / "primary_workset"
        assert resolved["config.registry"] == base / "global" / "registry.yaml"
        # Layer-2 system.* path settings (@-ref a config key).
        assert resolved["system.backup"] == base / "backup"
        assert resolved["system.channelroot"] == base / "channels"
        assert resolved["system.template"] == base / "global" / "template"
        assert resolved["system.canon"] == base / "global" / "canon"
        # The box guide (KANIBAKO.md) is NOT a system path key — it is delivered
        # live via the RO bundle + launch-flatten, not a host runtime install.
        assert "system.instructions" not in resolved
        # Phase 5: PRIMARY box/vault/logs roots live under the PRIMARY workset.
        assert resolved["system._boxes"] == base / "primary_workset" / "boxes"
        assert resolved["system._primary_vault_ro"] == base / "primary_workset" / "vault" / "ro"
        assert resolved["system._primary_vault_rw"] == base / "primary_workset" / "vault" / "rw"
        assert resolved["system._primary_logs"] == base / "primary_workset" / "logs"

    def test_config_foundation_resolves_standalone(self, tmp_path):
        """The Layer-1 foundation resolves the 6 config keys on its own."""
        config = resolve_config_paths({}, data_home=tmp_path, home=tmp_path)
        base = tmp_path / "kanibako"
        assert config == {
            "config.data": str(base),
            "config.settings": str(base / "global" / "settings.yaml"),
            "config.agents": str(base / "agents"),
            "config.primary_workset": str(base / "primary_workset"),
            "config.registry": str(base / "global" / "registry.yaml"),
            # J1: the lifecycle journal, beside the registry.
            "config.journal": str(base / "global" / "journal.yaml"),
        }

    def test_channels_skeleton_resolves(self, tmp_path):
        resolved = resolve_system_paths({}, data_home=tmp_path, home=tmp_path)
        channels = tmp_path / "kanibako" / "channels"
        assert resolved["system.channels.common"] == channels / "common"
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
        # Every declared Layer-1 config key + Layer-2 system key + the derived
        # PRIMARY-workset pseudo-keys.
        assert set(resolved) == (
            set(CONFIG_PATH_DEFAULTS)
            | set(SYSTEM_PATH_DEFAULTS)
            | {
                "system._boxes",
                "system._primary_vault_ro",
                "system._primary_vault_rw",
                "system._primary_logs",
            }
        )


class TestResolveSystemPathsOverrides:
    def test_data_override_tracks_dependents(self, tmp_path):
        """Overriding config.data moves dependents (which @-ref config.data)."""
        resolved = resolve_system_paths(
            {"config.data": "$XDG_DATA_HOME/custom"},
            data_home=tmp_path,
            home=tmp_path,
        )
        custom = tmp_path / "custom"
        assert resolved["config.data"] == custom
        assert resolved["config.agents"] == custom / "agents"
        assert resolved["system._boxes"] == custom / "primary_workset" / "boxes"
        # A Layer-2 system.* path @-refs config.data → tracks the override too.
        assert resolved["system.channelroot"] == custom / "channels"
        assert resolved["system.template"] == custom / "global" / "template"

    def test_absolute_leaf_override_isolated(self, tmp_path):
        """An absolute config.agents override does not perturb the other keys."""
        resolved = resolve_system_paths(
            {"config.agents": "/srv/agents"},
            data_home=tmp_path,
            home=tmp_path,
        )
        assert resolved["config.agents"] == Path("/srv/agents")
        # Others keep their defaults under $XDG_DATA_HOME/kanibako.
        base = tmp_path / "kanibako"
        assert resolved["config.data"] == base
        assert resolved["system.channelroot"] == base / "channels"

    def test_tilde_expands_to_home(self, tmp_path):
        home = tmp_path / "h"
        resolved = resolve_system_paths(
            {"config.data": "~/.kani"}, data_home=tmp_path, home=home,
        )
        assert resolved["config.data"] == home / ".kani"

    def test_unknown_system_ref_raises(self, tmp_path):
        with pytest.raises(SettingsError):
            resolve_system_paths(
                {"system.channelroot": "@system.nope/x"},
                data_home=tmp_path,
                home=tmp_path,
            )

    def test_unknown_config_ref_raises(self, tmp_path):
        with pytest.raises(SettingsError):
            resolve_system_paths(
                {"system.channelroot": "@config.nope/x"},
                data_home=tmp_path,
                home=tmp_path,
            )


class TestLoadConfigPaths:
    def test_config_table_populates(self, tmp_path):
        toml = tmp_path / "kanibako_config.yaml"
        toml.write_text('config:\n  agents: "/x"\n')
        cfg = load_config(toml)
        assert cfg.config_paths == {"config.agents": "/x"}

    def test_system_table_populates(self, tmp_path):
        toml = tmp_path / "kanibako_config.yaml"
        toml.write_text('system:\n  channelroot: "/x"\n')
        cfg = load_config(toml)
        assert cfg.config_paths == {"system.channelroot": "/x"}

    def test_both_tables_merge(self, tmp_path):
        toml = tmp_path / "kanibako_config.yaml"
        toml.write_text('config:\n  data: "/d"\nsystem:\n  channelroot: "/c"\n')
        cfg = load_config(toml)
        assert cfg.config_paths == {
            "config.data": "/d",
            "system.channelroot": "/c",
        }

    def test_nested_system_subkey_flattens(self, tmp_path):
        toml = tmp_path / "kanibako_config.yaml"
        toml.write_text('system:\n  channels:\n    common: "/c"\n')
        cfg = load_config(toml)
        assert cfg.config_paths == {"system.channels.common": "/c"}

    def test_empty_config_has_no_config_paths(self, tmp_path):
        cfg = load_config(tmp_path / "absent.yaml")
        assert cfg.config_paths == {}


class TestLoadStdPathsParity:
    def test_default_layout_matches_data_path(self, tmp_home, config_file):
        """load_std_paths yields the renamed/re-pointed dirs by default."""
        from kanibako.settings.paths import load_std_paths

        config = load_config(config_file)
        std = load_std_paths(config)
        assert std.data == std.data_path
        assert std.agents == std.data_path / "agents"
        assert std.channels == std.data_path / "channels"
        assert std.primary_workset == std.data_path / "primary_workset"
        assert std.template == std.data_path / "global" / "template"
        assert std.canon == std.data_path / "global" / "canon"
        assert std.registry == std.data_path / "global" / "registry.yaml"
        # Phase 5: PRIMARY box/vault/logs live under the PRIMARY workset.
        assert std.boxes == std.primary_workset / "boxes"
        assert std.primary_vault_ro == std.primary_workset / "vault" / "ro"
        assert std.primary_vault_rw == std.primary_workset / "vault" / "rw"
        assert std.primary_logs == std.primary_workset / "logs"
        # M-11 retired the ``templates`` alias with the field rename: keeping it
        # would have shipped THREE spellings of one directory.
        assert not hasattr(std, "templates")
        # ``share_ro``/``share_rw`` named dirs deleted in the system.* reorg; use
        # the workset vault / 'shared' category instead.
        assert not hasattr(std, "share_ro")
        assert not hasattr(std, "share_rw")


class TestBoxesOverrideConsumers:
    """A ``system.data`` override is honored consistently by both project
    creation/listing AND the names.yaml reverse-lookup helpers (the transitional
    box store hangs off the resolved data dir).
    """

    def test_boxes_override_used_by_creation_and_lookup(self, tmp_home):
        """Creating a project under a custom data dir registers its box under
        ``<custom>/boxes/<name>``, and the reverse-lookup helpers find it there.
        """
        from kanibako.settings.config import load_config
        from kanibako.settings.paths import (
            _find_local_ancestor,
            _resolve_local_dir,
            iter_projects,
            load_std_paths,
            resolve_project,
        )

        custom_data = tmp_home / "srv_data"
        custom_boxes = custom_data / "primary_workset" / "boxes"

        # Write a config that overrides config.data to the custom dir.
        cf = tmp_home / "config" / "kanibako_config.yaml"
        cf.write_text(f'config:\n  data: "{custom_data}"\n')

        config = load_config(cf)
        assert config.config_paths == {"config.data": str(custom_data)}
        std = load_std_paths(config)
        assert std.boxes == custom_boxes

        # Create a project — its metadata dir must land under the custom boxes.
        workspace = tmp_home / "ws"
        workspace.mkdir()
        proj = resolve_project(std, config, str(workspace), initialize=True)
        assert proj.metadata_path.is_dir()
        assert proj.metadata_path == custom_boxes / proj.name

        # Reverse-lookup (path -> name -> dir) resolves under the custom dir.
        name, box_dir = _resolve_local_dir(std, str(workspace.resolve()))
        assert name == proj.name
        assert box_dir == custom_boxes / proj.name

        # Deepest-ancestor lookup also keys off the custom boxes dir.
        sub = workspace / "src"
        sub.mkdir()
        ancestor = _find_local_ancestor(sub.resolve(), std)
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
        import kanibako.settings.paths as paths_mod

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
        import kanibako.settings.paths as paths_mod

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
        import kanibako.settings.paths as paths_mod

        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setattr(paths_mod, "_runtime_fallback_cache", {})
        first = resolve_xdg("XDG_RUNTIME_DIR", None)
        second = resolve_xdg("XDG_RUNTIME_DIR", None)
        assert first == second


class TestHostXdgMap:
    """``host_xdg_map`` — THE canonical host-side ``$XDG_*`` map for every
    host-side ``ResolveCtx`` (Jei ruling 2026-07-02: XDG vars must have
    fallbacks).  Every var must be PRESENT in the map regardless of the
    environment: the resolver reads only the ctx map, never the env, so a
    missing key raises at expand time (the 2026-07-02 dogfood bug)."""

    # The freedesktop Base Directory spec defaults (the oracle — pinned
    # independently of the code's own defaults table).
    _SPEC_VARS = {
        "XDG_DATA_HOME": ".local/share",
        "XDG_CONFIG_HOME": ".config",
        "XDG_STATE_HOME": ".local/state",
        "XDG_CACHE_HOME": ".cache",
    }

    def _pin_runtime_dir(self, tmp_path, monkeypatch):
        """Pin XDG_RUNTIME_DIR to an absolute dir (avoids the fallback+warn)."""
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))

    def test_returns_all_five_vars(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        self._pin_runtime_dir(tmp_path, monkeypatch)
        assert set(host_xdg_map()) == set(self._SPEC_VARS) | {"XDG_RUNTIME_DIR"}

    def test_unset_vars_fall_back_to_spec_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        self._pin_runtime_dir(tmp_path, monkeypatch)
        for var in self._SPEC_VARS:
            monkeypatch.delenv(var, raising=False)
        xdg_map = host_xdg_map()
        for var, suffix in self._SPEC_VARS.items():
            assert xdg_map[var] == str(tmp_path / suffix)

    def test_empty_vars_fall_back_to_spec_defaults(self, tmp_path, monkeypatch):
        """An EMPTY env value is as good as unset (per the spec) → default."""
        monkeypatch.setenv("HOME", str(tmp_path))
        self._pin_runtime_dir(tmp_path, monkeypatch)
        for var in self._SPEC_VARS:
            monkeypatch.setenv(var, "")
        xdg_map = host_xdg_map()
        for var, suffix in self._SPEC_VARS.items():
            assert xdg_map[var] == str(tmp_path / suffix)

    def test_set_absolute_vars_win(self, tmp_path, monkeypatch):
        """A SET (absolute) env var is honored over the spec default."""
        monkeypatch.setenv("HOME", str(tmp_path))
        for var in self._SPEC_VARS:
            monkeypatch.setenv(var, str(tmp_path / var.lower()))
        self._pin_runtime_dir(tmp_path, monkeypatch)
        xdg_map = host_xdg_map()
        for var in self._SPEC_VARS:
            assert xdg_map[var] == str((tmp_path / var.lower()).resolve())

    def test_data_home_param_overrides_env(self, tmp_path, monkeypatch):
        """An explicit *data_home* anchors XDG_DATA_HOME (the flat resolver's
        already-resolved anchor), beating even a set env var; the other vars
        still resolve from the environment."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "env_data"))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        self._pin_runtime_dir(tmp_path, monkeypatch)
        anchor = tmp_path / "anchor"
        xdg_map = host_xdg_map(anchor)
        assert xdg_map["XDG_DATA_HOME"] == str(anchor)
        assert xdg_map["XDG_CACHE_HOME"] == str(tmp_path / ".cache")


class TestLoadSystemConfig:
    """The 2-file CONFIG loader: config_base < user-global.

    ``config_base_path`` points at ``/etc/kanibako`` in production; tests redirect
    it at a tmp file via monkeypatch so the cascade can be exercised hermetically.
    (The former ``config_required`` non-overridable tier was CUT, 2026-06-29f.)
    """

    def _redirect(self, monkeypatch, base: Path) -> None:
        """Point the /etc base CONFIG path at a tmp file.

        ``load_system_config`` imports this lazily from ``kanibako.settings.config``, so
        patching the source module catches every call.
        """
        import kanibako.settings.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "config_base_path", lambda: base)

    def test_only_user_global_still_works(self, tmp_path, monkeypatch):
        """Back-compat: a user with only ~/.config/kanibako_config.yaml (absent
        /etc base) gets exactly the prior behavior."""
        base = tmp_path / "config_base.yaml"       # absent
        self._redirect(monkeypatch, base)

        user = tmp_path / "kanibako_config.yaml"
        user.write_text('config:\n  agents: "/u/agents"\n')

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["config.agents"] == Path("/u/agents")
        # Unset keys fall back to defaults under $XDG_DATA_HOME/kanibako.
        assert resolved["config.data"] == tmp_path / "kanibako"

    def test_all_files_absent_yields_defaults(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        user = tmp_path / "kanibako_config.yaml"  # absent
        self._redirect(monkeypatch, base)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert set(resolved) == (
            set(CONFIG_PATH_DEFAULTS)
            | set(SYSTEM_PATH_DEFAULTS)
            | {
                "system._boxes",
                "system._primary_vault_ro",
                "system._primary_vault_rw",
                "system._primary_logs",
            }
        )
        assert resolved["config.data"] == tmp_path / "kanibako"

    def test_user_wins_over_base(self, tmp_path, monkeypatch):
        base = tmp_path / "config_base.yaml"
        user = tmp_path / "kanibako_config.yaml"
        base.write_text('config:\n  agents: "/base/agents"\n')
        user.write_text('config:\n  agents: "/user/agents"\n')
        self._redirect(monkeypatch, base)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["config.agents"] == Path("/user/agents")

    def test_base_supplies_value_absent_from_user(self, tmp_path, monkeypatch):
        """A base-only key is honored when the user file omits it."""
        base = tmp_path / "config_base.yaml"
        user = tmp_path / "kanibako_config.yaml"
        base.write_text('system:\n  channelroot: "/base/channels"\n')
        user.write_text('config:\n  agents: "/user/agents"\n')
        self._redirect(monkeypatch, base)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["system.channelroot"] == Path("/base/channels")
        assert resolved["config.agents"] == Path("/user/agents")

    def test_per_key_independent_cascade(self, tmp_path, monkeypatch):
        """Each leaf cascades independently — base pins one key while the
        user sets (and overrides) another (spanning BOTH layers)."""
        base = tmp_path / "config_base.yaml"
        user = tmp_path / "kanibako_config.yaml"
        base.write_text(
            'config:\n  data: "/base/data"\nsystem:\n  channelroot: "/base/channels"\n'
        )
        user.write_text(
            'config:\n  data: "/user/data"\n  agents: "/user/agents"\n'
        )
        self._redirect(monkeypatch, base)

        resolved = load_system_config(user, data_home=tmp_path, home=tmp_path)
        assert resolved["config.data"] == Path("/user/data")    # user overrides base
        assert resolved["config.agents"] == Path("/user/agents")  # user-only
        assert resolved["system.channelroot"] == Path("/base/channels")  # base-only


class TestResolveSystemPathsXdgCtx:
    """``resolve_system_paths`` populates the full XDG var set into ctx."""

    def test_xdg_config_state_cache_refs_resolve(self, tmp_path, monkeypatch):
        """A config path expression referencing $XDG_CONFIG_HOME resolves."""
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg_home = tmp_path / "cfg"
        monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
        resolved = resolve_system_paths(
            {"config.agents": "$XDG_CONFIG_HOME/kani-agents"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["config.agents"] == cfg_home / "kani-agents"

    def test_xdg_runtime_ref_resolves(self, tmp_path, monkeypatch):
        run_dir = tmp_path / "run"
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(run_dir))
        resolved = resolve_system_paths(
            {"config.agents": "$XDG_RUNTIME_DIR/kani"},
            data_home=tmp_path / "data",
            home=tmp_path,
        )
        assert resolved["config.agents"] == run_dir / "kani"


class TestResolverSplitRouting:
    """Spec §1A / JC-2: ``@config.*`` routes to the Layer-1 FOUNDATION
    (``ctx.config``); ``@system.*`` to the merged settings snapshot.

    These exercise the launch-path expand engine directly (the same router both
    the launch snapshot and set-time validation use), proving the prefix-driven
    split: a ``@config.*`` ref reads ``ctx.config`` even when the key is ABSENT
    from the snapshot, while a ``@system.*`` ref reads the snapshot.
    """

    def _ctx(self, config):
        from kanibako.settings.settings_resolve import ResolveCtx

        return ResolveCtx(
            agent_name=None,
            workset_name=None,
            host_home="/home/u",
            xdg={"XDG_DATA_HOME": "/data"},
            config=config,
        )

    def test_config_ref_resolves_via_foundation_not_snapshot(self):
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_expand import expand

        # The snapshot has NO config.* node — only ctx.config does.
        snap = KeyStore()
        snap["x"] = "@config.data/sub"
        ctx = self._ctx({"config.data": "/foundation/data"})
        out = expand(snap, ctx)
        assert out["x"] == "/foundation/data/sub"

    def test_dangling_config_ref_drops_whole_value(self):
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_expand import expand

        # A whole-value @config.* ref to a key absent from the foundation is a
        # dangling ref → the holder key is DROPPED (§6b propagation).
        snap = KeyStore()
        snap["x"] = "@config.missing"
        ctx = self._ctx({"config.data": "/foundation/data"})
        out = expand(snap, ctx)
        assert "x" not in out

    def test_system_ref_resolves_via_snapshot(self):
        from kanibako.settings.keystore import KeyStore
        from kanibako.settings.settings_expand import expand

        # @system.* reads the snapshot (NOT ctx.config).
        snap = KeyStore()
        sysnode = KeyStore()
        sysnode["channelroot"] = "/snap/channels"
        snap["system"] = sysnode
        snap["x"] = "@system.channelroot/chat"
        ctx = self._ctx({"config.data": "/foundation/data"})
        out = expand(snap, ctx)
        assert out["x"] == "/snap/channels/chat"


class TestConfigDataCascade:
    """A user ``kanibako_config.yaml`` override of ``config.data`` cascades to
    every dependent (all 5 config keys + the Layer-2 system paths that @-ref it).
    """

    def test_config_data_override_cascades_to_all(self, tmp_path):
        resolved = resolve_system_paths(
            {"config.data": "/srv/kani"}, data_home=tmp_path, home=tmp_path,
        )
        root = Path("/srv/kani")
        assert resolved["config.data"] == root
        assert resolved["config.settings"] == root / "global" / "settings.yaml"
        assert resolved["config.agents"] == root / "agents"
        assert resolved["config.primary_workset"] == root / "primary_workset"
        assert resolved["config.registry"] == root / "global" / "registry.yaml"
        # Layer-2 system paths @-ref config.data → cascade too.
        assert resolved["system.channelroot"] == root / "channels"
        assert resolved["system.backup"] == root / "backup"
        assert resolved["system.template"] == root / "global" / "template"
