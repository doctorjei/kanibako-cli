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

import pytest
import argparse
from pathlib import Path

from kanibako.commands.system_cmd import run_get, run_reset, run_set, run_show
from kanibako.settings.config import load_config, read_system_agent
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import load_std_paths


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
    write_nested_key(std.settings, ("system",), "agent", name)
    return std


def _launch_auth_source(std, *, agent_name="claude", mode="primary"):
    """The LAUNCH's credential-sharing decision, off the REAL system settings
    file — the same ``build_launch_snapshot`` → ``resolve_auth_source`` read
    ``start._resolve_box_auth_source`` performs (system_path=std.settings)."""
    from kanibako.settings.paths import host_xdg_map
    from kanibako.settings.settings_launch import (
        auth_chain_floor,
        build_launch_snapshot,
        meta_identity_floor,
        meta_runtime_floor,
        resolve_auth_source,
    )
    from kanibako.settings.settings_resolve import ResolveCtx

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
    """``system set system.env.<VAR>`` — the system env tier.

    ⚑ FLIPPED by B9. The bare ``env.<VAR>`` spelling wrote ``@config.data/env``,
    a docker ``.env`` FILE the launch layered as its system tier. R-39 retired
    the spelling and Jei's RQ-1 re-ruling retired the launch READ, so the file is
    gone from both ends. The system env tier is now the settings key
    ``system.env.<VAR>``, stored in the system SETTINGS file and delivered by
    ``settings_launch._emit_scope_node`` as a system-scope ``env`` entry.
    """

    def test_bare_env_set_is_refused_with_the_system_cure(
        self, config_file, tmp_home, capsys,
    ):
        rc = _set("env.EDITOR=nano")
        assert rc == 1
        err = capsys.readouterr().err
        assert "system.env.EDITOR" in err
        # The retired FILE is not created, and not suggested.
        assert not (_std(config_file).data_path / "env").exists()
        assert "no longer read at all" in err

    def test_bare_env_get_is_refused_at_the_handler(
        self, config_file, tmp_home, capsys,
    ):
        """``env.`` stays KEY-SHAPED (``is_known_key``) precisely so the read
        reaches this refusal instead of being read as a project name."""
        rc = _get("env.EDITOR")
        assert rc == 1
        assert "system.env.EDITOR" in capsys.readouterr().err

    def test_set_env_writes_the_system_settings_file(self, config_file, tmp_home):
        rc = _set("system.env.EDITOR=nano")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(std.settings)["system"]["env"]["EDITOR"] == "nano"
        # SETTINGS, never the Layer-1 CONFIG file.
        assert "env" not in load_doc(config_file).get("system", {})

    def test_get_env_reads_back(self, config_file, tmp_home, capsys):
        _set("system.env.EDITOR=nano")
        capsys.readouterr()
        rc = _get("system.env.EDITOR")
        assert rc == 0
        assert "system.env.EDITOR=nano" in capsys.readouterr().out

    def test_show_renders_env(self, config_file, tmp_home, capsys):
        _set("system.env.EDITOR=nano")
        capsys.readouterr()
        rc = _show()
        assert rc == 0
        assert "system.env.EDITOR = nano" in capsys.readouterr().out

    def test_launch_env_is_the_collapsed_slots_and_only_them(self):
        """``_build_config_env`` projects the slots — it layers nothing under them.

        🛑 RECOMPOSED TWICE, and the second time the SUBJECT went. It first read an
        un-arbitrated ``LaunchDeliveries.envs`` entry list and claimed the pick was
        "the most-specific scope per VAR" — the CASCADE's direction, which is not
        the direction VARIABLES realize in. It then pinned the agent tier as an
        UNDER-layer, which MBR-1 P3 retired: that dict was the per-agent file's
        ``self.env``, off the cascade entirely and therefore below ``system``, and
        the file's table is an ordinary ``agent.<node>.env.<VAR>`` key now. One
        input, no layering, nothing to order.
        """
        from kanibako.commands.start import _build_config_env
        from kanibako.settings.store_collapse import CollapsedEnv

        def _slot(var, value, scope):
            return {var: CollapsedEnv(value, scope, f"{scope}.env.{var}")}

        assert _build_config_env({}) == {}
        assert _build_config_env(_slot("EDITOR", "box-e", "box"))["EDITOR"] == "box-e"
        # The scope rides as PROVENANCE and this projection never consults it.
        assert _build_config_env(
            _slot("PAGER", "less", "system"),
        ) == {"PAGER": "less"}

    def test_reset_env_removes_it(self, config_file, tmp_home, capsys):
        _set("system.env.EDITOR=nano")
        rc = _reset("system.env.EDITOR")
        assert rc == 0
        std = _std(config_file)
        assert "env" not in load_doc(std.settings).get("system", {})


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
        # The VERB is the op the user ran.
        assert "cannot be set from the CLI" in err
        # The advice names the file resolve_system_paths actually reads.
        assert str(config_file) in err
        # Nothing was written anywhere.
        assert load_doc(config_file)["system"].get("cache") != "/custom/cache"

    def test_reset_structural_key_refused(self, config_file, tmp_home, capsys):
        rc = _reset("system.cache")
        assert rc == 1
        err = capsys.readouterr().err
        assert "structural config key" in err
        # ⚑ "reset", not "set": a reset is its own op and used to borrow set's verb.
        assert "cannot be reset from the CLI" in err

    def test_hand_editing_the_named_file_actually_works(
        self, config_file, tmp_home, capsys,
    ):
        """The refusal advice must not lie: a hand-edit of the config file's
        [system] table is honored by the path resolver AND readable via get."""
        custom = str(tmp_home / "custom-cache")
        write_nested_key(config_file, ("system",), "cache", custom)
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

    def test_get_structural_key_refuses_with_a_READ_verb(
        self, config_file, tmp_home, capsys,
    ):
        """Residuals item 4 gave `get` the structural TRUTH; this pins its VERB.

        `get` shares ``system_key_refusal`` with `set`/`reset`, and the message was
        a fixed "is not settable from the CLI" — a WRITE verb printed on a READ,
        mis-describing the op that failed. It now takes *verb*, and the read tail
        drops "(or re-run 'kanibako setup')" too: that is a WRITE cure, useless to
        someone who ran `get`.

        ⚑ MUTATION: pass ``verb="set"`` at ``system_cmd``'s call -> the read-verb
        assertion dies.
        """
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
            # THE VERB matches the op the user ran — and no write verb survives.
            assert "cannot be read from the CLI" in err, (key, err)
            assert "settable" not in err, (key, err)
            assert "cannot be set" not in err, (key, err)
            # A write cure has no business on a read.
            assert "kanibako setup" not in err, (key, err)

    def test_get_config_path_key_reads_kanibako_config_yaml(
        self, config_file, tmp_home, capsys,
    ):
        """config.data (Layer-1 CONFIG) is read from kanibako_config.yaml — get
        still works (the key moved system.data -> config.data in block #3a)."""
        custom = str(tmp_home / "custom-data")
        write_nested_key(config_file, ("config",), "data", custom)
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
        write_nested_key(config_file, ("system",), "cache", "/custom/cache")
        # A settings-tier system.auth override in the SETTINGS file (ssp).
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        write_nested_key(
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


class TestSystemAgentNodeBindWriteRouteRetired:
    """R-9 — through the REAL ``system config`` CLI, not the engine: the per-node
    descriptor bind write route is refused, and the refusal reaches the user's
    terminal naming the key they typed.

    This class REPLACES ``TestSystemAgentNodeBindRepoint``, which pinned the
    opposite end-to-end behaviour (``system config set
    agent.<node>.bindings.{ro,rw}.<name> /new`` writing a RAW tuple into
    ``agents/<node>/settings.yaml``). That surface is an ACCEPTED LOSS, boarded as
    DS-BL1. The end-to-end value of the tests is unchanged: they prove the refusal
    is what the USER meets at the CLI, not just what the engine returns."""

    KEY = "agent.claude.bindings.ro.launcher"

    def _file(self, std, node="claude"):
        return std.agents / node / "settings.yaml"

    def test_set_exits_nonzero_and_names_the_key_on_stderr(
        self, config_file, tmp_home, capsys,
    ):
        rc = _set(f"{self.KEY}=/newsrc")
        assert rc == 1
        err = capsys.readouterr().err
        assert self.KEY in err, err
        assert "RETIRED" in err, err

    def test_no_node_directory_is_created_for_a_refused_write(
        self, config_file, tmp_home, capsys,
    ):
        """⚑ The handler used to ``mkdir`` the node dir BEFORE handing the key to
        the engine. With the write refused, that would leave an
        ``agents/<node>/`` directory behind for an operation that did nothing —
        which is why the routing block was removed rather than left inert."""
        rc = _set(f"{self.KEY}=/newsrc")
        assert rc == 1
        std = _std(config_file)
        assert not (std.agents / "claude").exists()
        # ...and nothing leaked into the CONFIG file either.
        assert "agent" not in load_doc(config_file)

    def test_reset_exits_nonzero_and_keeps_the_tuple(
        self, config_file, tmp_home, capsys,
    ):
        std = _std(config_file)
        node_file = self._file(std)
        node_file.parent.mkdir(parents=True, exist_ok=True)
        seeded = {"self": {"claude": {"bindings": {"ro": {
            "launcher": ["/old/src", "/box/launcher", "ro"]}}}}}
        dump_doc(node_file, seeded)
        rc = _reset(self.KEY)
        assert rc == 1
        err = capsys.readouterr().err
        assert self.KEY in err and "RETIRED" in err, err
        assert load_doc(node_file) == seeded

    def test_bind_named_model_is_still_claimed_as_a_bind(
        self, config_file, tmp_home, capsys,
    ):
        """COLLISION: a bind NAMED ``model`` must meet the bind RETIREMENT, not the
        persona scalar branch — which would have written the verbatim string
        ``/x`` into ``self.model``. The preamble refusal runs before every branch,
        so this is the ordering guarantee it inherits."""
        rc = _set("agent.claude.bindings.ro.model=/x")
        assert rc == 1
        err = capsys.readouterr().err
        assert "RETIRED" in err, err
        # And the persona-scalar model still writes verbatim (unchanged path).
        assert _set("agent.claude.model=opus") == 0
        std = _std(config_file)
        assert load_doc(self._file(std))["self"]["model"] == "opus"

    def test_get_still_reads_a_hand_authored_bind(
        self, config_file, tmp_home, capsys,
    ):
        """The read survives — the refusal tells the user to edit the node settings
        file, and this is how they check that the edit took."""
        std = _std(config_file)
        node_file = self._file(std)
        node_file.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(node_file, {"self": {"claude": {"bindings": {"ro": {
            "launcher": ["/newsrc", "/box/launcher", "ro"]}}}}})
        capsys.readouterr()
        rc = _get(self.KEY)
        assert rc == 0
        out = capsys.readouterr().out
        assert "(not set)" not in out, out
        assert "/newsrc" in out, out

    def test_get_unset_bind_is_not_set(self, config_file, tmp_home, capsys):
        rc = _get(self.KEY)
        assert rc == 0
        assert "(not set)" in capsys.readouterr().out



# ---------------------------------------------------------------------------
# ⚑⚑ ``TestRelativeCategorySourceRefusedEndToEnd`` LIVED HERE AND IS GONE
# (DS-BL1 = (a), 2026-08-07g).
#
# It drove the bare-relative CATEGORY source refusal and its per-scope rooted hint
# through the REAL setter at system scope (``agent.claude.common.plugins``), plus the
# round trip that proved the SUGGESTED value is itself acceptable — a tool that
# refuses its own suggestion being worse than one that suggests nothing.
#
# **There is no CLI category set any more**: the key is refused BY NAME in the verb
# preamble, so ``validate_config_set``'s ``is_category=True`` arm — where the
# relative-source rule and ``_rooted_form_hint`` lived — was never reached from any
# CLI door. Re-pointing these tests at a scalar would NOT preserve them: the rule
# they pin is category-only by construction.
#
# ⚑⚑ AND THE RULE ITSELF IS NOW GONE, NOT JUST ITS END-TO-END DOOR. This block used
# to say the unit coverage SURVIVED in ``test_settings_configset.py``; QA′
# (2026-08-08, on Jei's word) deleted the whole ``is_category`` arm — the
# relative-source refusal, ``_rooted_form_hint``'s per-scope cure, the ``:``
# notation refusal and the ``Warn`` severity — as an orphan of the retired route.
# Those unit rows went with it; the graveyard block at the foot of
# ``test_settings_configset.py`` names them.
#
# ⚑ SO NOTHING CHECKS A BARE-RELATIVE CATEGORY SOURCE ANY MORE, AT ANY LAYER. That
# is not a regression this pass caused: a source authored directly in YAML was never
# checked by that validator either, before or after, because ``config set`` was the
# only door it guarded. Closing the gap needs a DECLARATION-time check, not this
# arm restored.
#
# ⚑ The set-time ``meta_agent_path_floor`` anchor those tests mutation-proved went
# with the arm's caller: ``_category_set_lookups`` now anchors only the agent the
# COMMAND names.
# ---------------------------------------------------------------------------


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
    """F2 — a SYSTEM-scope category key must live in ONE file: the system SETTINGS
    file, which is what the launch cascade's system tier reads.

    ⚑⚑ THE SET HALF IS GONE (DS-BL1 = (a)), so the get/set disagreement this class
    was built around cannot recur — one of its two sides no longer exists. What is
    pinned now, through the REAL ``system config`` CLI: the surviving READ names the
    settings file, a tuple parked in the CONFIG file is NOT read, and both write
    verbs refuse by name without touching either store.

    ⚑ The VEHICLE stays ``synced`` (CONCRETE — no declaration root), unchanged.
    """

    KEY = "system.synced.helper"
    SEEDED = ["/old/src", "/home/agent/helper", "ro"]

    def _seed(self, path):
        write_nested_key(path, ("system", "synced"), "helper", list(self.SEEDED))

    def test_get_reads_the_settings_file_and_the_verbs_refuse(
        self, config_file, tmp_home, capsys,
    ):
        std = _std(config_file)
        self._seed(std.settings)
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "/old/src" in capsys.readouterr().out

        # SET and RESET both refuse BY NAME, and neither store changes.
        assert _set(f"{self.KEY}={tmp_home}") != 0
        assert "RETIRED" in capsys.readouterr().err
        assert _reset(self.KEY) != 0
        assert "RETIRED" in capsys.readouterr().err
        assert load_doc(std.settings)["system"]["synced"]["helper"] == self.SEEDED
        assert "synced" not in load_doc(config_file).get("system", {})

    def test_a_tuple_only_in_the_config_file_is_not_read(
        self, config_file, tmp_home, capsys,
    ):
        """The control: the same key hand-written into kanibako_config.yaml reads
        back "(not set)", because that file is in NO cascade level."""
        std = _std(config_file)
        self._seed(config_file)
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "(not set)" in capsys.readouterr().out
        assert not load_doc(std.settings).get("system", {}).get("synced")

    def test_reset_all_still_sweeps_the_scope_table(
        self, config_file, tmp_home, capsys,
    ):
        """``reset --all`` clears the SETTINGS file's scope tables wholesale, so a
        hand-authored category tuple goes with them — a DELIBERATE asymmetry with
        the per-key reset above (a table sweep is not a per-key write, and it does
        not consult the per-key retirement doors). Pre-existing; pinned so the
        difference is recorded rather than surprising."""
        std = _std(config_file)
        self._seed(std.settings)
        assert _reset(all_keys=True) == 0
        assert "system" not in load_doc(std.settings)
        capsys.readouterr()
        assert _get(self.KEY) == 0
        assert "(not set)" in capsys.readouterr().out


class TestRetiredScopeBindRoute:
    """R-9 — through the REAL ``system config`` CLI, not the engine: the
    scope-level bind route is refused, and the refusal reaches the user's
    terminal naming the key they typed."""

    KEY = "system.bindings.ro.helper"

    def _seed(self, path):
        write_nested_key(
            path, ("system", "bindings", "ro"), "helper",
            ["/old/src", "/home/agent/helper", "ro"],
        )

    def test_set_exits_nonzero_and_names_the_key_on_stderr(
        self, config_file, tmp_home, capsys,
    ):
        std = _std(config_file)
        self._seed(std.settings)
        rc = _set(f"{self.KEY}={tmp_home}")
        assert rc == 1
        err = capsys.readouterr().err
        assert self.KEY in err, err
        assert "RETIRED" in err, err
        # The hand-authored tuple is untouched by the refusal.
        assert load_doc(std.settings)["system"]["bindings"]["ro"]["helper"] == [
            "/old/src", "/home/agent/helper", "ro",
        ]

    def test_reset_exits_nonzero_and_keeps_the_tuple(
        self, config_file, tmp_home, capsys,
    ):
        std = _std(config_file)
        self._seed(std.settings)
        rc = _reset(self.KEY)
        assert rc == 1
        err = capsys.readouterr().err
        assert self.KEY in err and "RETIRED" in err, err
        assert load_doc(std.settings)["system"]["bindings"]["ro"]["helper"] == [
            "/old/src", "/home/agent/helper", "ro",
        ]

    def test_get_still_prints_the_stored_tuple(
        self, config_file, tmp_home, capsys,
    ):
        """The read survives — the refusal tells the user to edit the settings
        file, and this is how they check that the edit took."""
        std = _std(config_file)
        self._seed(std.settings)
        capsys.readouterr()
        assert _get(self.KEY) == 0
        out = capsys.readouterr().out
        assert "(not set)" not in out, out
        assert "/old/src" in out, out


class TestSystemAgentNodeBindSeamSuperseded:
    """F3's ``system set`` node-routing seam is GONE, and what replaced it refuses
    EARLIER and just as loudly.

    The seam swallowed ``canonicalize_agent_ref``'s ``ConfigError`` and left the
    write pointed at the kanibako_config.yaml CONFIG file, and it let the RESERVED
    ``default`` node through as far as ``mkdir`` — creating an ``agents/default/``
    dir for a key the launch never reads as a node. R-9 retired the whole write
    route, so the handler no longer parses a node at all and the engine refuses in
    its preamble. The PROPERTIES those tests protected are unchanged and are what
    is pinned here: nonzero exit, nothing written to either store, no stray node
    directory. Only the message changed — it names the retirement, not the node.
    """

    @pytest.mark.parametrize(
        "key",
        [
            "agent.default.bindings.ro.share",   # the RESERVED any-agent tier
            "agent.a+b+c.bindings.ro.share",     # a MALFORMED node ref
            "agent.claude.bindings.ro.share",    # a GOOD node
        ],
    )
    def test_every_node_shape_is_refused_and_writes_nothing(
        self, config_file, tmp_home, capsys, key,
    ):
        rc = _set(f"{key}=/x")
        assert rc == 1
        err = capsys.readouterr().err
        assert err.startswith("Error:"), err
        assert "RETIRED" in err, err
        # Neither store was written, and no node directory was created.
        assert "agent" not in load_doc(config_file)
        assert list(_std(config_file).agents.glob("*")) == []
