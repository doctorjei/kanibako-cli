"""Tests for `kanibako system` config verbs — system-scope key ROUTING (F2/F3).

The old pin here ("ALL ``system.*``-prefixed keys are FILE-ONLY") was the F2
collateral and is DELIBERATELY FLIPPED: routing a settable ``system.*``
SETTINGS key to ``kanibako_config.yaml`` was a write-only no-op
(``resolve_system_paths`` drops unknown ``[system]`` entries), while the
launch reads those keys from the system SETTINGS file (``@config.settings`` =
``global/settings.yaml``).  The rule now:

* STRUCTURAL path-tier keys (the ``SYSTEM_PATH_DEFAULTS`` family +
  ``system.setup_completed``) stay FILE-ONLY in ``kanibako_config.yaml``'s
  ``[system]`` table — set/reset refused, get/show still read, and the refusal
  names the file that hand-editing actually honors.
* system-scope SETTINGS (``system.auth.share_allowed``,
  ``system.default_agent``, ``env.*``, agent settings) route to the SAME
  storage the launch cascade reads: the system settings file for keyed
  settings, ``@config.data/env`` for the env tier.  set → get → show
  --effective → launch agree on ONE location per key.

These tests exercise the integrated ``system_cmd`` set/reset/get/show paths
end-to-end through the discrete verbs (the B2 config.*-forbid guard still
fires).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from kanibako.commands.system_cmd import run_get, run_reset, run_set, run_show
from kanibako.config import load_config, read_default_agent
from kanibako.config_io import load_doc
from kanibako.config_interface import _write_nested_toml_key
from kanibako.paths import load_std_paths
from kanibako.shellenv import read_env_file


def _set(key_value, *, force=True):
    return run_set(argparse.Namespace(key_value=key_value, force=force))


def _reset(key=None, *, all_keys=False, force=True):
    return run_reset(argparse.Namespace(key=key, all_keys=all_keys, force=force))


def _get(key):
    return run_get(argparse.Namespace(key=key))


def _show(*, effective=False):
    return run_show(argparse.Namespace(effective=effective))


def _std(config_file):
    return load_std_paths(load_config(config_file))


def _seed_default_agent(config_file, name):
    """Programmatically seed system.default_agent into the settings file.

    Mirrors what ``kanibako setup`` does; the CLI ``set`` now writes the SAME
    location (F3), so this stays only as the setup-write stand-in.
    """
    std = _std(config_file)
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    _write_nested_toml_key(std.settings, ("agent", "default"), "default_agent", name)
    return std


def _launch_auth_source(std, *, agent_name="claude", mode="primary"):
    """The LAUNCH's credential-sharing decision, off the REAL system settings
    file — the same ``build_launch_snapshot`` → ``resolve_auth_source`` read
    ``start._resolve_box_auth_source`` performs (system_path=std.settings)."""
    from kanibako.paths import host_xdg_map
    from kanibako.settings_launch import (
        auth_chain_floor,
        build_launch_snapshot,
        meta_identity_floor,
        meta_runtime_floor,
        resolve_auth_source,
    )
    from kanibako.settings_resolve import ResolveCtx

    ctx = ResolveCtx(
        agent_name=agent_name, workset_name=None,
        host_home=str(Path.home()), xdg=host_xdg_map(),
    )
    snapshot = build_launch_snapshot(
        agent_name=agent_name, ctx=ctx,
        system_path=std.settings, agent_path=None,
        workset_path=None, box_path=None,
        auth_chain=auth_chain_floor(mode=mode, agent_name=agent_name),
        meta_runtime=meta_runtime_floor(mode=mode, ws_root_literal=None),
        meta_identity=meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None, workset_name="__PRIMARY__",
            agent_name=agent_name, agent_real_name=agent_name,
            agent_auth_share_support=True,
        ),
    )
    return resolve_auth_source(snapshot, mode=mode)


class TestSystemAuthShareAllowed:
    """F2: ``system.auth.share_allowed`` — a declared settable SETTINGS key.

    set → get → show --effective → launch must agree on ONE storage location:
    the ``system.auth.share_allowed`` entry of the system SETTINGS file.
    """

    def test_set_lands_in_settings_file_not_config_file(
        self, config_file, tmp_home,
    ):
        rc = _set("system.auth.share_allowed=false")
        assert rc == 0
        std = _std(config_file)
        # Stored as a REAL bool in the system SETTINGS file (the launch input).
        assert load_doc(std.settings)["system"]["auth"]["share_allowed"] is False
        # NOT in the kanibako_config.yaml [system] table (the dead location:
        # resolve_system_paths drops unknown [system] entries).
        assert "auth" not in load_doc(config_file).get("system", {})

    def test_get_reads_back_the_set_value(self, config_file, tmp_home, capsys):
        _set("system.auth.share_allowed=false")
        capsys.readouterr()
        rc = _get("system.auth.share_allowed")
        assert rc == 0
        assert "system.auth.share_allowed=false" in capsys.readouterr().out

    def test_show_effective_renders_the_set_value(
        self, config_file, tmp_home, capsys,
    ):
        _set("system.auth.share_allowed=false")
        capsys.readouterr()
        rc = _show(effective=True)
        assert rc == 0
        assert "system.auth.share_allowed = false" in capsys.readouterr().out

    def test_show_overrides_renders_the_set_value(
        self, config_file, tmp_home, capsys,
    ):
        _set("system.auth.share_allowed=false")
        capsys.readouterr()
        rc = _show()
        assert rc == 0
        assert "system.auth.share_allowed = false" in capsys.readouterr().out

    def test_launch_honors_the_set_value(self, config_file, tmp_home):
        """The LAUNCH auth resolve reads the SAME file ``set`` wrote: with the
        host-wide gate off, sharing is private everywhere (tier ``box``)."""
        std = _std(config_file)
        # Baseline (unset): primary + capable defaults to the workset tier.
        assert _launch_auth_source(std).tier == "workset"
        _set("system.auth.share_allowed=false")
        a = _launch_auth_source(std)
        assert a.tier == "box"
        assert a.shares is False

    def test_reset_removes_from_settings_file(self, config_file, tmp_home):
        _set("system.auth.share_allowed=false")
        rc = _reset("system.auth.share_allowed")
        assert rc == 0
        std = _std(config_file)
        assert "auth" not in load_doc(std.settings).get("system", {})
        # And the launch decision reverts to the default (workset tier).
        assert _launch_auth_source(std).tier == "workset"


class TestSystemDefaultAgentSetting:
    """F3: ``system.default_agent`` — a SETTING routed to the settings tier's
    ``agent.default`` table, EXACTLY where the shipped reader
    (``config.read_default_agent``) and ``setup`` already live."""

    def test_set_writes_where_read_default_agent_reads(
        self, config_file, tmp_home,
    ):
        rc = _set("system.default_agent=goose")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(std.settings)["agent"]["default"]["default_agent"] == "goose"
        # The LAUNCH reader sees the CLI-set value (set/launch agreement).
        assert read_default_agent(std.settings) == "goose"
        # Nothing landed in the kanibako_config.yaml CONFIG file.
        assert "agent" not in load_doc(config_file)

    def test_get_reads_back_the_set_value(self, config_file, tmp_home, capsys):
        _set("system.default_agent=goose")
        capsys.readouterr()
        rc = _get("system.default_agent")
        assert rc == 0
        assert "system.default_agent=goose" in capsys.readouterr().out

    def test_get_reads_setup_written_value(self, config_file, tmp_home, capsys):
        """A ``setup``-written value (the programmatic path) reads back through
        ``system get`` — one storage location for both writers."""
        _seed_default_agent(config_file, "codex")
        capsys.readouterr()
        rc = _get("system.default_agent")
        assert rc == 0
        assert "system.default_agent=codex" in capsys.readouterr().out

    def test_reset_removes_the_setting(self, config_file, tmp_home):
        std = _seed_default_agent(config_file, "claude")
        rc = _reset("system.default_agent")
        assert rc == 0
        assert read_default_agent(std.settings) is None

    def test_reset_unset_reports_no_override(self, config_file, tmp_home, capsys):
        rc = _reset("system.default_agent")
        assert rc == 0
        assert "No override" in capsys.readouterr().out

    def test_show_renders_default_agent_from_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        _seed_default_agent(config_file, "goose")
        capsys.readouterr()
        rc = _show()
        assert rc == 0
        assert "goose" in capsys.readouterr().out


class TestSystemEnvTier:
    """``system set env.<VAR>`` — the system env tier (F4/F9 sibling).

    The storage is ``@config.data/env`` — the EXACT file the launch env
    layering reads as its system tier (``start._build_config_env``; precedence
    system < agent < workset < box).
    """

    def test_set_env_writes_the_launch_system_tier_file(
        self, config_file, tmp_home,
    ):
        rc = _set("env.EDITOR=nano")
        assert rc == 0
        std = _std(config_file)
        assert read_env_file(std.data_path / "env")["EDITOR"] == "nano"

    def test_get_env_reads_back(self, config_file, tmp_home, capsys):
        _set("env.EDITOR=nano")
        capsys.readouterr()
        rc = _get("env.EDITOR")
        assert rc == 0
        assert "env.EDITOR=nano" in capsys.readouterr().out

    def test_show_renders_env(self, config_file, tmp_home, capsys):
        _set("env.EDITOR=nano")
        capsys.readouterr()
        rc = _show()
        assert rc == 0
        assert "env.EDITOR = nano" in capsys.readouterr().out

    def test_launch_env_includes_it_at_system_precedence(
        self, config_file, tmp_home, tmp_path,
    ):
        """The launch layering picks the value up from the system tier, and
        every higher tier (agent < workset < box) overrides it."""
        from kanibako.commands.start import _build_config_env

        _set("env.EDITOR=nano")
        std = _std(config_file)
        sys_env = std.data_path / "env"
        absent = tmp_path / "absent-box-env"
        # System tier alone → the set value is live at launch.
        env = _build_config_env(sys_env, {}, None, absent)
        assert env["EDITOR"] == "nano"
        # EVERY higher tier overrides in order (system is the LOWEST):
        # agent beats system …
        assert _build_config_env(
            sys_env, {"EDITOR": "agent-e"}, None, absent,
        )["EDITOR"] == "agent-e"
        # … workset beats agent …
        ws_env = tmp_path / "ws-env"
        ws_env.write_text("EDITOR=ws-e\n")
        assert _build_config_env(
            sys_env, {"EDITOR": "agent-e"}, ws_env, absent,
        )["EDITOR"] == "ws-e"
        # … box beats workset (the full system < agent < workset < box chain).
        box_env = tmp_path / "box-env"
        box_env.write_text("EDITOR=box-e\n")
        assert _build_config_env(
            sys_env, {"EDITOR": "agent-e"}, ws_env, box_env,
        )["EDITOR"] == "box-e"

    def test_reset_env_removes_it(self, config_file, tmp_home, capsys):
        _set("env.EDITOR=nano")
        rc = _reset("env.EDITOR")
        assert rc == 0
        std = _std(config_file)
        assert "EDITOR" not in read_env_file(std.data_path / "env")


class TestSystemStructuralFileOnly:
    """The STRUCTURAL path-tier family stays FILE-ONLY — and the refusal's
    advice is TRUE: hand-editing the named file is honored by the resolver."""

    def test_set_structural_key_refused_names_the_real_file(
        self, config_file, tmp_home, capsys,
    ):
        rc = _set("system.cache=/custom/cache")
        assert rc == 1
        err = capsys.readouterr().err
        assert "structural config key" in err
        # The advice names the file resolve_system_paths actually reads.
        assert str(config_file) in err
        # Nothing was written anywhere.
        assert load_doc(config_file)["system"].get("cache") != "/custom/cache"

    def test_reset_structural_key_refused(self, config_file, tmp_home, capsys):
        rc = _reset("system.cache")
        assert rc == 1
        assert "structural config key" in capsys.readouterr().err

    def test_hand_editing_the_named_file_actually_works(
        self, config_file, tmp_home, capsys,
    ):
        """The refusal advice must not lie: a hand-edit of the config file's
        [system] table is honored by the path resolver AND readable via get."""
        custom = str(tmp_home / "custom-cache")
        _write_nested_toml_key(config_file, ("system",), "cache", custom)
        std = _std(config_file)
        assert std.cache == Path(custom)
        capsys.readouterr()
        rc = _get("system.cache")
        assert rc == 0
        assert custom in capsys.readouterr().out

    def test_setup_completed_stays_file_only(self, config_file, tmp_home, capsys):
        """system.setup_completed's shipped reader (read_setup_completed) reads
        kanibako_config.yaml's [system] table — it keeps the file-only refusal
        (spec-vs-code divergence flagged; reader relocation out of scope)."""
        rc = _set("system.setup_completed=1.7.0")
        assert rc == 1
        assert "structural config key" in capsys.readouterr().err

    def test_get_config_path_key_reads_kanibako_config_yaml(
        self, config_file, tmp_home, capsys,
    ):
        """config.data (Layer-1 CONFIG) is read from kanibako_config.yaml — get
        still works (the key moved system.data -> config.data in block #3a)."""
        custom = str(tmp_home / "custom-data")
        _write_nested_toml_key(config_file, ("config",), "data", custom)
        capsys.readouterr()
        rc = _get("config.data")
        assert rc == 0
        assert custom in capsys.readouterr().out

    def test_non_system_key_still_settable(self, config_file, tmp_home):
        """A regular (non-system.*) key still sets fine at the global tier."""
        rc = _set("model=gpt-5")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(std.settings)["agent"]["default"]["model"] == "gpt-5"
