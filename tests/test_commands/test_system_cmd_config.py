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
  ``system.agent``, ``env.*``, agent settings) route to the SAME
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
from kanibako.config import load_config, read_system_agent
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


def _seed_system_agent(config_file, name):
    """Programmatically seed system.agent into the settings file.

    Mirrors what ``kanibako setup`` does; the CLI ``set`` now writes the SAME
    location (F3), so this stays only as the setup-write stand-in.
    """
    std = _std(config_file)
    std.settings.parent.mkdir(parents=True, exist_ok=True)
    _write_nested_toml_key(std.settings, ("system",), "agent", name)
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
        meta_runtime=meta_runtime_floor(
            mode=mode,
            ws_name=("__PRIMARY__" if mode == "primary" else "__STANDALONE__"),
            ws_root_literal=None,
        ),
        meta_identity=meta_identity_floor(
            box_name="b", project_path="/p", inbox="/i", share_global="/sg",
            share_workset=None,
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
        assert a.creds_shared is False

    def test_reset_removes_from_settings_file(self, config_file, tmp_home):
        _set("system.auth.share_allowed=false")
        rc = _reset("system.auth.share_allowed")
        assert rc == 0
        std = _std(config_file)
        assert "auth" not in load_doc(std.settings).get("system", {})
        # And the launch decision reverts to the default (workset tier).
        assert _launch_auth_source(std).tier == "workset"


class TestSystemDefaultAgentSetting:
    """F3: ``system.agent`` — a SETTING routed to the settings tier's
    ``agent.default`` table, EXACTLY where the shipped reader
    (``config.read_system_agent``) and ``setup`` already live."""

    def test_set_writes_where_read_system_agent_reads(
        self, config_file, tmp_home,
    ):
        rc = _set("system.agent=goose")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(std.settings)["system"]["agent"] == "goose"
        # The LAUNCH reader sees the CLI-set value (set/launch agreement).
        assert read_system_agent(std.settings) == "goose"
        # Nothing landed in the kanibako_config.yaml CONFIG file.
        assert "agent" not in load_doc(config_file)

    def test_get_reads_back_the_set_value(self, config_file, tmp_home, capsys):
        _set("system.agent=goose")
        capsys.readouterr()
        rc = _get("system.agent")
        assert rc == 0
        assert "system.agent=goose" in capsys.readouterr().out

    def test_get_reads_setup_written_value(self, config_file, tmp_home, capsys):
        """A ``setup``-written value (the programmatic path) reads back through
        ``system get`` — one storage location for both writers."""
        _seed_system_agent(config_file, "codex")
        capsys.readouterr()
        rc = _get("system.agent")
        assert rc == 0
        assert "system.agent=codex" in capsys.readouterr().out

    def test_reset_removes_the_setting(self, config_file, tmp_home):
        std = _seed_system_agent(config_file, "claude")
        rc = _reset("system.agent")
        assert rc == 0
        assert read_system_agent(std.settings) is None

    def test_reset_unset_reports_no_override(self, config_file, tmp_home, capsys):
        rc = _reset("system.agent")
        assert rc == 0
        assert "No override" in capsys.readouterr().out

    def test_show_renders_default_agent_from_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        _seed_system_agent(config_file, "goose")
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

    def test_get_structural_key_matches_set_refusal(
        self, config_file, tmp_home, capsys,
    ):
        """Residuals item 4: `system get system.setup_completed` /
        `system.channels.*` said "unknown config key" while `set` gave the
        truthful structural refusal. Make get's message MATCH set's truth."""
        for key in ("system.setup_completed", "system.channels.common"):
            capsys.readouterr()  # drain
            rc = _get(key)
            assert rc == 1, key
            err = capsys.readouterr().err
            # Mutation guard: the old lie is GONE, the structural truth PRESENT,
            # and the message names the real config file (as set's does).
            assert "unknown config key" not in err, (key, err)
            assert "structural config key" in err, (key, err)
            assert str(config_file) in err, (key, err)

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

    def test_reset_all_never_touches_structural_config_table(
        self, config_file, tmp_home,
    ):
        """Residuals item 3, Editor condition (i): reset --all at the SYSTEM
        scope clears the settings file's ``system.auth`` SETTINGS table but NEVER
        the kanibako_config.yaml ``[system]`` STRUCTURAL path table (cache etc.)
        — those are file-only, not overrides."""
        std = _std(config_file)
        # A structural path value hand-written into the config file's [system].
        _write_nested_toml_key(config_file, ("system",), "cache", "/custom/cache")
        # A settings-tier system.auth override in the SETTINGS file (ssp).
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        _write_nested_toml_key(
            std.settings, ("system", "auth"), "share_allowed", False,
        )
        rc = _reset(all_keys=True)
        assert rc == 0
        # The SETTINGS system.auth table was cleared (item 3).
        settings_doc = load_doc(std.settings)
        assert "auth" not in settings_doc.get("system", {}), settings_doc
        # The CONFIG file's structural [system] cache is INTACT (condition i).
        assert load_doc(config_file)["system"]["cache"] == "/custom/cache"


class TestSystemPersonaAgentKeys:
    """B1: ``system set agent.<persona+harness>.<key>`` — CLI-configurable
    personas routed to the agent's OWN ``agents/<node>/settings.yaml`` (the
    global ``config.agents`` store), end-to-end through the ``system`` verbs.
    """

    def _file(self, std, node="navigator℘claude"):
        return std.agents / node / "settings.yaml"

    def test_set_endpoint_writes_canonical_dir(self, config_file, tmp_home):
        rc = _set("agent.navigator+claude.endpoint=https://ep")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(self._file(std)) == {"self": {"endpoint": "https://ep"}}
        # The +form dir must NOT exist (the ℘ canonicalization really happened).
        assert not (std.agents / "navigator+claude").exists()

    def test_get_reads_back_via_plus_and_script_p(
        self, config_file, tmp_home, capsys,
    ):
        _set("agent.navigator+claude.model=gemma-4-31b-it")
        capsys.readouterr()
        # get with the ℘form hits the SAME store the +form set wrote.
        rc = _get("agent.navigator℘claude.model")
        assert rc == 0
        assert "agent.navigator℘claude.model=gemma-4-31b-it" in (
            capsys.readouterr().out
        )

    def test_reset_removes_and_prunes_sparse(self, config_file, tmp_home):
        _set("agent.navigator+claude.endpoint=https://ep")
        rc = _reset("agent.navigator+claude.endpoint")
        assert rc == 0
        std = _std(config_file)
        # The now-empty agent table is pruned → the file stays sparse (empty doc).
        assert load_doc(self._file(std)) == {}

    def test_secret_path_token_lands_in_self_section(
        self, config_file, tmp_home,
    ):
        rc = _set("agent.navigator+claude.secret_path.ANTHROPIC_AUTH_TOKEN=/t/tok")
        assert rc == 0
        std = _std(config_file)
        # DIRECTLY under self.secret_path — self IS agent.<node>, so no second
        # <node> embedding (RENAMED from rc-only env_file, clean break).
        assert load_doc(self._file(std)) == {
            "self": {
                "secret_path": {"ANTHROPIC_AUTH_TOKEN": "/t/tok"},
            },
        }

    def test_default_only_persona_file_stays_sparse(self, config_file, tmp_home):
        _set("agent.navigator+claude.endpoint=https://ep")
        std = _std(config_file)
        data = load_doc(self._file(std))
        assert data == {"self": {"endpoint": "https://ep"}}


class TestSystemAgentNodeBindRepoint:
    """item-0: ``system config set agent.<node>.bindings.{ro,rw}.<name> /new`` — a
    SOURCE-ONLY repoint of the descriptor delivery bind (claude launcher/share),
    written RAW to the node's OWN ``agents/<node>/settings.yaml`` (the SAME file the
    persona keys write to), end-to-end through the ``system`` verbs."""

    def _file(self, std, node="claude"):
        return std.agents / node / "settings.yaml"

    def test_repoint_launcher_writes_raw_tuple(self, config_file, tmp_home):
        # The descriptor floor supplies the launcher box_dest/opts; the repoint swaps
        # ONLY the host source (was refused/mis-routed before item-0).
        from kanibako.agent_representation import agent_default_bind_keys

        rc = _set("agent.claude.bindings.ro.launcher=/newsrc")
        assert rc == 0
        std = _std(config_file)
        _, dest, opts = agent_default_bind_keys("claude")[
            "agent.claude.bindings.ro.launcher"
        ]
        assert load_doc(self._file(std))["self"]["claude"]["bindings"]["ro"][
            "launcher"
        ] == ["/newsrc", dest, opts]

    def test_repoint_works_for_uninstalled_agent(self, config_file, tmp_home):
        # Fork 3: the registry is descriptor-only (no detect), so the repoint
        # validates + writes even though NO claude binary is installed in this
        # isolated tmp_home — the box_dest still comes from the descriptor.
        rc = _set("agent.claude.bindings.ro.share=/newshare")
        assert rc == 0
        std = _std(config_file)
        tup = load_doc(self._file(std))["self"]["claude"]["bindings"]["ro"]["share"]
        assert tup[0] == "/newshare"
        assert tup[1].endswith("/.local/share/claude")  # descriptor box_dest

    def test_unknown_bind_name_refused(self, config_file, tmp_home, capsys):
        rc = _set("agent.claude.bindings.ro.nonexistent=/x")
        assert rc == 1
        assert "nonexistent" in capsys.readouterr().err

    def test_bind_named_model_routes_to_repoint_not_persona(
        self, config_file, tmp_home, capsys,
    ):
        # COLLISION: a bind NAMED ``model`` is a category repoint (refused here as
        # not-in-descriptor), NOT the persona scalar ``model`` (which would write a
        # verbatim string). The refusal proves it took the category path.
        rc = _set("agent.claude.bindings.ro.model=/x")
        assert rc == 1
        err = capsys.readouterr().err
        assert "must already exist in the cascade" in err or "model" in err
        # And the persona-scalar model still writes verbatim (unchanged path).
        assert _set("agent.claude.model=opus") == 0
        std = _std(config_file)
        assert load_doc(self._file(std))["self"]["model"] == "opus"

    def test_set_then_get_reads_back_the_repoint(
        self, config_file, tmp_home, capsys,
    ):
        # Step B Phase 3: the read-back half — a repoint SET is now visible via GET
        # (was "(not set)" before). The stored tuple is echoed stored-at-noun.
        assert _set("agent.claude.bindings.ro.launcher=/newsrc") == 0
        capsys.readouterr()
        rc = _get("agent.claude.bindings.ro.launcher")
        assert rc == 0
        out = capsys.readouterr().out
        assert "agent.claude.bindings.ro.launcher=" in out
        assert "/newsrc" in out

    def test_get_unset_repoint_is_not_set(self, config_file, tmp_home, capsys):
        rc = _get("agent.claude.bindings.ro.launcher")
        assert rc == 0
        assert "(not set)" in capsys.readouterr().out

    def test_reset_repoint_removes_and_reports_floor(
        self, config_file, tmp_home, capsys,
    ):
        # Step B Phase 3: a repoint RESET removes the override from the node file
        # (was "Error: unknown config key" before) and — item 3 — names the
        # reverted-to descriptor destination without the set-time placeholder.
        from kanibako.core_defaults import FLOOR_PLACEHOLDER_SRC

        _set("agent.claude.bindings.ro.launcher=/newsrc")
        capsys.readouterr()
        rc = _reset("agent.claude.bindings.ro.launcher")
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cleared agent.claude.bindings.ro.launcher" in out
        assert "effective is now" in out
        assert FLOOR_PLACEHOLDER_SRC not in out
        std = _std(config_file)
        # The override is gone; the now-empty tables are pruned (sparse file).
        assert load_doc(self._file(std)) == {}


class TestRelativeCategorySourceRefusedEndToEnd:
    """The bare-relative category refusal, and its hint, through the REAL setter.

    Driven at SYSTEM scope because that is a scope from which an agent-scope
    category key can legitimately be written (containment: system ⊃ agent), and
    because ``system set`` routes an ``agent.<node>.*`` key through the validating
    engine (``set_config_value`` → ``validate_config_set``).

    ⚑ THE HINT MUST BE ACCEPTABLE. The refusal tells the user to spell the source as
    ``@meta.agent.<agent>.path/<category>/<name>``; if the SET-TIME validation
    snapshot did not materialize ``meta.agent.<a>.path``, that very value would come
    straight back as an unresolvable ``@``-reference. A tool that refuses its own
    suggestion is worse than one that suggests nothing — so the round trip (refuse,
    read the hint, set the hinted value, succeed) is pinned end to end here, not
    only at the pure-validator level.
    """

    _KEY = "agent.claude.common.plugins"
    _HINTED = "@meta.agent.claude.path/common/plugins"

    def test_relative_source_is_refused_with_a_per_scope_hint(
        self, config_file, tmp_home, capsys,
    ):
        rc = _set(f"{self._KEY}=plugins")
        out = capsys.readouterr()
        msg = out.out + out.err
        assert rc != 0, msg
        assert "bare relative path" in msg
        assert self._HINTED in msg

    def test_the_hinted_value_is_ACCEPTED(self, config_file, tmp_home, capsys):
        """The round trip: the exact string the refusal suggested sets cleanly.

        The key is seeded first because ``config set`` is SOURCE-ONLY — it repoints
        an existing bind and never creates one (F10 must-exist). That gate is a
        separate, documented rule; what is under test here is that the SUGGESTED
        VALUE resolves.

        (Mutation: drop the ``meta_agent_path_floor`` contribution from
        ``config_interface._category_set_lookups`` → the hinted value comes back as
        ``dangling @-reference '@meta.agent.claude.path'`` → RED. That dead end is
        what this pins.)
        """
        _write_nested_toml_key(
            config_file, ("agent", "claude", "common"), "plugins",
            ["/seed/src", "/home/agent/.claude/plugins"],
        )
        rc = _set(f"{self._KEY}={self._HINTED}")
        out = capsys.readouterr()
        msg = out.out + out.err
        assert rc == 0, msg
        assert "@-reference" not in msg
        # Stored VERBATIM (§0 files store UNRESOLVED), box_dest preserved.
        assert load_doc(config_file)["agent"]["claude"]["common"]["plugins"] == [
            self._HINTED, "/home/agent/.claude/plugins",
        ]

    def test_a_relative_source_is_still_refused_when_the_key_EXISTS(
        self, config_file, tmp_home, capsys,
    ):
        """Control: the refusal is about the VALUE, not about the must-exist gate —
        it still fires on a key that is present in the cascade."""
        _write_nested_toml_key(
            config_file, ("agent", "claude", "common"), "plugins",
            ["/seed/src", "/home/agent/.claude/plugins"],
        )
        rc = _set(f"{self._KEY}=plugins")
        out = capsys.readouterr()
        assert rc != 0
        assert "bare relative path" in out.out + out.err
        # And NOTHING was written — the stored tuple is untouched.
        assert load_doc(config_file)["agent"]["claude"]["common"]["plugins"] == [
            "/seed/src", "/home/agent/.claude/plugins",
        ]


class TestSystemSecretPathVerbSymmetry:
    """F1 — ``system set/get/reset <scope>.secret_path.<VAR>`` through the real
    verbs.  ``set`` wrote (and ``reset`` removed) the system SETTINGS file while
    ``get`` read ``project_toml`` — a path this handler never threads — so a
    secret pointer set at the system scope read back "(not set)" forever."""

    KEY = "system.secret_path.ANTHROPIC_AUTH_TOKEN"

    def test_set_then_get_then_reset(self, config_file, tmp_home, capsys):
        assert _set(f"{self.KEY}=/t/tok") == 0
        std = _std(config_file)
        assert load_doc(std.settings)["system"]["secret_path"][
            "ANTHROPIC_AUTH_TOKEN"
        ] == "/t/tok"
        capsys.readouterr()

        assert _get(self.KEY) == 0
        out = capsys.readouterr().out
        assert "(not set)" not in out
        assert "/t/tok" in out

        assert _reset(self.KEY) == 0
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "(not set)" in capsys.readouterr().out


class TestSystemCategoryFileRouting:
    """F2 — a SYSTEM-scope category key must live in ONE file: the system
    SETTINGS file, which is what the launch cascade's system tier reads.  ``set``
    and single-key ``reset`` used the kanibako_config.yaml CONFIG file while
    ``get`` (and ``reset --all``'s scope-table sweep) used the settings file."""

    KEY = "system.bindings.ro.helper"

    def _seed(self, path):
        _write_nested_toml_key(
            path, ("system", "bindings", "ro"), "helper",
            ["/old/src", "/home/agent/helper", "ro"],
        )

    def test_set_get_reset_all_name_the_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        std = _std(config_file)
        self._seed(std.settings)
        new_src = str(tmp_home)
        assert _set(f"{self.KEY}={new_src}") == 0, capsys.readouterr().err

        # SET → the settings file, box_dest + options preserved RAW.
        assert load_doc(std.settings)["system"]["bindings"]["ro"]["helper"] == [
            new_src, "/home/agent/helper", "ro",
        ]
        # ...and NOT the CONFIG file (which holds structural config only).
        assert "bindings" not in load_doc(config_file).get("system", {})

        # GET → reads back what SET wrote (was "(not set)").
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert new_src in capsys.readouterr().out

        # RESET → clears the same store.
        assert _reset(self.KEY) == 0
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "(not set)" in capsys.readouterr().out

    def test_reset_all_clears_a_category_set(self, config_file, tmp_home, capsys):
        """``reset --all`` sweeps the SETTINGS file's scope tables only, so a
        category write parked in the CONFIG file SURVIVED it — an override the
        remove-everything verb could not remove."""
        std = _std(config_file)
        self._seed(std.settings)
        assert _set(f"{self.KEY}={tmp_home}") == 0
        assert _reset(all_keys=True) == 0
        assert "system" not in load_doc(std.settings)
        assert "bindings" not in load_doc(config_file).get("system", {})
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "(not set)" in capsys.readouterr().out

    def test_a_bind_only_in_the_config_file_is_not_the_cascade(
        self, config_file, tmp_home, capsys,
    ):
        """The set-time must-exist probe must agree with the LAUNCH, whose system
        tier is the settings file — so a bind that exists ONLY in
        kanibako_config.yaml is nowhere in the cascade and cannot be repointed.
        (It was accepted before, because set and its probe both pointed at the
        config file: the CLI agreed with itself and with nothing else.)"""
        std = _std(config_file)
        self._seed(config_file)
        rc = _set(f"{self.KEY}={tmp_home}")
        assert rc == 1
        assert "must already exist" in capsys.readouterr().err
        # Neither store was written.
        assert load_doc(config_file)["system"]["bindings"]["ro"]["helper"] == [
            "/old/src", "/home/agent/helper", "ro",
        ]
        assert "bindings" not in load_doc(std.settings).get("system", {})


class TestSystemAgentNodeBindSeamRefuses:
    """F3 — the ``system set`` seam refuses a bad node instead of falling back.

    It swallowed ``canonicalize_agent_ref``'s ``ConfigError`` and left the write
    pointed at the kanibako_config.yaml CONFIG file, and it let the RESERVED
    ``default`` node through as far as ``mkdir`` — creating an ``agents/default/``
    dir for a key the launch never reads as a node."""

    def test_reserved_default_node_refused(self, config_file, tmp_home, capsys):
        rc = _set("agent.default.bindings.ro.share=/x")
        assert rc == 1
        assert "reserved" in capsys.readouterr().err
        std = _std(config_file)
        assert not (std.agents / "default").exists()
        assert "agent" not in load_doc(config_file)

    def test_malformed_node_refused(self, config_file, tmp_home, capsys):
        rc = _set("agent.a+b+c.bindings.ro.share=/x")
        assert rc == 1
        err = capsys.readouterr().err
        assert err.startswith("Error:")
        # The refusal NAMES the real defect — the ref.  Swallowing the parse error
        # and falling back to the config file left the user with the cascade's
        # "key must already exist" complaint about a key that could never exist,
        # which sends them off to seed a bind instead of fixing the node.
        assert "agent ref" in err
        # The malformed node's table did NOT land in the CONFIG file.
        assert "agent" not in load_doc(config_file)
        assert list(_std(config_file).agents.glob("*")) == []
