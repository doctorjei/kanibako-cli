"""Tests for `kanibako system` config verbs — system-scope key ROUTING (F2/F3).

The old pin here ("ALL ``system.*``-prefixed keys are FILE-ONLY") was the F2
collateral and is DELIBERATELY FLIPPED: routing a settable ``system.*``
SETTINGS key to ``kanibako_config.yaml`` was a write-only no-op
(``resolve_system_paths`` drops unknown ``[system]`` entries), while the
launch reads those keys from the system SETTINGS file (``@config.settings`` =
``global/settings.yaml``).  The rule now:

* ``system.setup_completed`` stays FILE-ONLY in ``kanibako_config.yaml``'s
  ``[system]`` table — set/reset refused, get/show still read, and the refusal
  names the file that hand-editing actually honors.  ⚑ The
  ``SYSTEM_PATH_DEFAULTS`` family stood beside it until 2026-08-23; spec §2g
  declares all eleven Layer-2 SETTINGS keys, so they joined the row below.
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
from kanibako.settings.agent_config import store_dirname
from kanibako.settings.config import load_config, read_system_agent
from kanibako.settings.config_io import dump_doc, load_doc
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import load_std_paths
from kanibako.settings.settings_keyspace import (
    SCALAR_AGENT_LEAVES,
    TERMINAL_CATEGORY_TAILS,
)


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
    """``system.setup_completed`` is SETTABLE, and every verb names the ONE table
    the marker is stored in — the SYSTEM SETTINGS file's ``system:``.

    ⚑⚑ THIS CLASS ASSERTED THE OPPOSITE UNTIL 2026-08-23, and the reversal is the
    saying-so.  It pinned a refusal on all three verbs whose cure was "hand-edit the
    config file" — for a key spec §2g calls "PERSISTS, user-resettable" and the
    registry marks ``set: cli+file``.  Telling a user to hand-edit a file the CLI can
    write is not a safety property; it is the surface failing to keep a declared
    promise, and the hand-edit had exactly the same lack of validation.

    ⚑⚑ THE FILE IS ``@config.settings`` SINCE 2026-08-26, and the invariant is
    unchanged: every verb names the file ``read_setup_completed`` reads, because a
    verb aimed anywhere else is accepted, persisted and INERT.  What moved was the
    STORAGE — Jei closed the delta spec §2g had recorded all along (a Layer-2
    ``system.*`` settings key stored in the Layer-1 config file) by moving the marker
    to the global settings file, so ``setup``, the gate and all three verbs now name
    the same file as ``system.agent`` does.
    """

    def test_set_writes_the_file_the_gate_reads(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.settings.config import read_setup_completed

        rc = _set("system.setup_completed=1.7.0")
        assert rc == 0, capsys.readouterr()
        # The SHIPPED READER is the oracle: asserting a table exists would pass just
        # as well for a write nobody consumes.
        assert read_setup_completed(_std(config_file).settings) == "1.7.0"
        # ...and the Layer-1 file did not take the write.
        assert "setup_completed" not in load_doc(config_file).get("system", {})

    def test_reset_clears_it_back_to_never_run(
        self, config_file, tmp_home, capsys,
    ):
        from kanibako.settings.config import read_setup_completed

        _set("system.setup_completed=1.7.0")
        capsys.readouterr()
        rc = _reset("system.setup_completed")
        assert rc == 0, capsys.readouterr()
        assert read_setup_completed(_std(config_file).settings) is None

    def test_hand_editing_the_named_file_is_still_honored(
        self, config_file, tmp_home, capsys,
    ):
        """The file stays a hand-editable surface, and ``get`` reads it back.

        ⚑ The READ used to be REFUSED here (``rc == 1``, "structural config key"),
        which meant a key the CLI now sets could not be read by the same CLI — the
        get/set asymmetry the dest-parity module exists to prevent.
        """
        ssp = _std(config_file).settings
        write_nested_key(ssp, ("system",), "setup_completed", "1.7.0")
        capsys.readouterr()
        rc = _get("system.setup_completed")
        assert rc == 0, capsys.readouterr()
        assert "1.7.0" in capsys.readouterr().out
        from kanibako.settings.config import read_setup_completed
        assert read_setup_completed(ssp) == "1.7.0"

    def test_the_system_path_tier_is_settable_and_lands_in_the_settings_file(
        self, config_file, tmp_home, capsys,
    ):
        """⚑ THE REPLACEMENT PIN (2026-08-23), and it asserts the OPPOSITE of what
        stood here: spec §2g makes the ``SYSTEM_PATH_DEFAULTS`` family ordinary
        settings keys, so ``system set`` writes them to ``global/settings.yaml``.
        """
        custom = str(tmp_home / "custom-cache")
        rc = _set(f"system.cache={custom}")
        assert rc == 0, capsys.readouterr()
        std = _std(config_file)
        assert load_doc(std.settings)["system"]["cache"] == custom
        # ⚑ ``.get("system", {})``, not ``["system"]``: since 2026-08-26 the Layer-1
        # file is written EMPTY at create time, so it has no ``system:`` table at all
        # — which is a STRONGER statement of the same thing this line always asserted.
        assert load_doc(config_file).get("system", {}).get("cache") != custom
        capsys.readouterr()
        assert _get("system.cache") == 0
        assert custom in capsys.readouterr().out

    def test_an_undeclared_config_key_is_refused_as_UNKNOWN(
        self, config_file, tmp_home, capsys,
    ):
        """Spec §0: an undeclared name is not a key, and the refusal says so.

        ⚑ THIS CASE REPLACES ``test_get_structural_key_refuses_with_a_READ_verb``
        (2026-08-23), which pinned the VERB of a "structural config key" refusal on
        ``system.setup_completed``.  That key is settable and readable now, and the
        refusal it exercised was left with no subject but undeclared ``config.*``
        spellings — for which "is a structural config key … its value lives in the
        config file" asserts that a non-existent key exists.  The message is gone; the
        assertions below are the ones its own mutation guard implied.

        ⚑ THE WORDING MOVED ONCE MORE (2026-08-28) and the SUBJECT did not.  The
        ``is_known_key`` pre-gate that answered "unknown config key" is gone — it was a
        second vocabulary that refused seven DECLARED keys — so the §0 gate's own
        refusal is what an undeclared name gets.  Pinned on what §0 actually demands:
        rc 1, the offending key NAMED, and the declared alternatives listed.  It must
        NOT be pinned on the whole string, which is ``key_validity``'s to word.
        """
        capsys.readouterr()  # drain
        rc = _get("config.nope")
        assert rc == 1
        err = capsys.readouterr().err
        assert "config.nope" in err, err
        assert "not a declared Layer-1 config key" in err, err
        assert "config.settings" in err, err
        assert "structural config key" not in err, err

    def test_a_declared_config_foundation_key_still_reads(
        self, config_file, tmp_home, capsys,
    ):
        """The other half: dropping the branch must not make a REAL key unreadable."""
        capsys.readouterr()
        assert _get("config.data") == 0
        assert capsys.readouterr().out.strip() != ""

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

    def test_reset_all_clears_the_settings_auth_table(self, config_file, tmp_home):
        """Residuals item 3: reset --all at the SYSTEM scope clears the settings file's
        ``system.auth`` SETTINGS table.

        ⚑ CHANGED 2026-08-31. The other half — *"but NEVER the
        ``kanibako_config.yaml [system]`` STRUCTURAL path table"* — was measured by
        hand-writing ``system.cache`` into the config file, which no longer READS at
        all: that table refuses (see the case below). The surviving question, whether
        ``reset --all`` overreaches into a second file, is answered by the config file
        staying byte-identical.
        """
        std = _std(config_file)
        before = config_file.read_bytes()
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
        # The CONFIG file was not written at all (condition i).
        assert config_file.read_bytes() == before

    def test_a_structural_table_in_the_config_file_refuses(self, config_file, tmp_home):
        """🛑 A ``system:`` path table hand-written into ``kanibako_config.yaml`` is not
        "file-only, not an override" — it is not readable there at all (Jei, 2026-08-31).
        """
        from kanibako.errors import ConfigError

        write_nested_key(config_file, ("system",), "cache", "/custom/cache")
        with pytest.raises(ConfigError) as exc:
            _std(config_file)
        assert str(config_file) in str(exc.value)
        assert "system.cache" in str(exc.value)


class TestSystemPersonaAgentKeys:
    """B1: ``system set agent.<persona+harness>.<key>`` — CLI-configurable
    personas routed to the agent's OWN ``agents/<node>/agent.yaml`` (the
    global ``config.agents`` store), end-to-end through the ``system`` verbs.
    """

    def _file(self, std, node="navigator℘claude"):
        # ⚑ The dirname comes from the production helper; the spelling itself is
        # pinned with literals in ``test_set_endpoint_writes_the_plus_dir``.
        return std.agents / store_dirname(node) / "agent.yaml"

    def test_set_endpoint_writes_the_plus_dir(self, config_file, tmp_home):
        rc = _set("agent.navigator+claude.endpoint=https://ep")
        assert rc == 0
        std = _std(config_file)
        assert load_doc(std.agents / "navigator+claude" / "agent.yaml") == {
            "self": {"endpoint": "https://ep"},
        }
        # ⚑ NO ``℘`` REACHES THE DISK: it is a key-path device, and a store dir the
        # user lists and cd's into is not a key.
        assert not (std.agents / "navigator℘claude").exists()

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
    ``agents/<node>/agent.yaml``). That surface is an ACCEPTED LOSS, boarded as
    DS-BL1. The end-to-end value of the tests is unchanged: they prove the refusal
    is what the USER meets at the CLI, not just what the engine returns."""

    KEY = "agent.claude.bindings.ro.launcher"

    def _file(self, std, node="claude"):
        return std.agents / node / "agent.yaml"

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
        # ⚑ FLAT since S2/S3: ``self`` IS ``agent.claude``, so the bindings table sits
        # DIRECTLY under the root; the nested ``self: claude:`` shape is now refused.
        seeded = {"self": {"bindings": {"ro": {
            "launcher": ["/old/src", "/box/launcher", "ro"]}}}}
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
        dump_doc(node_file, {"self": {"bindings": {"ro": {
            "launcher": ["/newsrc", "/box/launcher", "ro"]}}}})
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

    def test_a_tuple_only_in_the_config_file_refuses(
        self, config_file, tmp_home, capsys,
    ):
        """The control: the same key hand-written into kanibako_config.yaml STOPS the
        command, because that file may not carry a settings key at all.

        ⚑ CHANGED 2026-08-31. It used to read back "(not set)" — true of the cascade and
        useless to the user, who had written the entry in front of them and was told
        nothing about it.
        """
        from kanibako.errors import ConfigError

        std = _std(config_file)
        self._seed(config_file)
        capsys.readouterr()
        with pytest.raises(ConfigError) as exc:
            _get(self.KEY)
        assert str(config_file) in str(exc.value)
        assert "system.synced.helper" in str(exc.value)
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


class TestSystemGetClosedKeyspaceReadGate:
    """``system get`` refuses a name this scope cannot serve, instead of faking it.

    THE DEFECT: ``system get`` gated on ``is_known_key`` alone — a WIDER predicate that
    answers "is this key-SHAPED", not "is this a DECLARED key readable HERE".  A name it
    admitted but the scope cannot serve fell through to an ordinary read, found nothing,
    and printed "(not set)" at rc 0: the fabricated answer spec §0 forbids in the same
    breath as the write, and the one ``box get``/``workset get`` were already fixed to
    refuse.  Jei, 2026-08-27: *"the system one's answer is wrong; it should also be an
    error."*

    THE CURE IS A DIFFERENT SURFACE, NOT A WIDER PREDICATE — the handler's own comment
    says so and ``is_known_key`` is deliberately untouched, quarantined answers and all.
    ``system get`` now calls ``scope_read_key_error``, the SAME function the sibling
    nouns call, so three nouns cannot drift into three answers for one key.

    ⚑ THE SCOPE DIFFERENCE IS REAL AND IS NOT A CARVE-OUT: at box and workset scope a
    BARE agent leaf names no key, but at system scope it IS the any-agent tier's key
    (``system set model=opus`` writes ``agent.default.model``).  The gate admits it via
    ``_is_agent_setting``, which is SCALAR-only by construction — so the single leaf it
    withholds is the TABLE-valued one, which is exactly the key that must still refuse.
    """

    def test_the_table_valued_agent_leaf_is_refused_WITH_AN_ADDRESS(
        self, config_file, tmp_home, capsys,
    ):
        """The reported defect: it printed "(not set)" at rc 0 for a key no scalar
        read can carry (spec §2d), and named nowhere to go instead."""
        capsys.readouterr()
        assert _get("transform_settings") == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap
        assert "transform_settings" in cap.err, cap.err
        assert "kanibako agent get" in cap.err, cap.err

    def test_the_refusal_IS_the_sibling_nouns_refusal_not_a_lookalike(
        self, config_file, tmp_home, capsys,
    ):
        """Byte-identical to what ``workset get`` prints, because it is the same
        construction — the guard against someone re-minting a parallel message here."""
        from kanibako.settings.config_keys import ConfigLevel, scope_read_key_error

        capsys.readouterr()
        assert _get("transform_settings") == 1
        printed = capsys.readouterr().err.strip()
        assert printed == scope_read_key_error(
            "transform_settings", ConfigLevel.workset,
        ), printed

    @pytest.mark.parametrize(
        "key",
        [
            "agent.claude.caches.foo",   # an ENTRY of a terminal dest-keyed key
            "agent.claude.synced.foo",   # ditto, a second category
            "box.agent.model",           # the RETIRED box-scoped agent mirror
            "agent.nosuchagent.model",   # a MISSPELLED node — the likeliest typo of the four
        ],
    )
    def test_the_rest_of_the_class_is_refused_too(
        self, config_file, tmp_home, capsys, key,
    ):
        """ONE DEFECT, NOT ONE KEY.  Every name ``is_known_key`` admits and this scope
        cannot serve was answered "(not set)" at rc 0; the gate names each of them."""
        capsys.readouterr()
        assert _get(key) == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap
        assert key in cap.err, cap.err

    def test_the_R9_bind_read_is_NOT_in_that_class_and_survives(
        self, config_file, tmp_home, capsys,
    ):
        """THE LINE BETWEEN THE TWO, and it is drawn by MEASUREMENT, not by shape.

        ``agent.<node>.caches.<name>`` and ``agent.<node>.bindings.ro.<name>`` look alike
        and are NOT alike: the engine serves the second and not the first.  A
        hand-authored ``caches`` entry reads "(not set)" — a fabrication, hence the
        refusal above — while a hand-authored BIND reads back its tuple, because R-9 kept
        that read alive on purpose (*"the read survived the write"*): it is the only way
        to check the hand edit the write refusal prescribes.

        ⚑ So the gate takes the NARROW ``_is_agent_node_bind_key``, not the wide
        ``_is_agent_scope_bind_key`` that spans all six categories.  Applying the wide one
        re-fabricates the answer; applying neither breaks this read — which is how the
        first draft of this fix was caught, by ``test_get_still_reads_a_hand_authored_bind``
        in ``TestSystemAgentNodeBindWriteRouteRetired``.
        """
        std = _std(config_file)
        node_file = std.agents / store_dirname("claude") / "agent.yaml"
        node_file.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(node_file, {"self": {
            "bindings": {"ro": {"launcher": ["/newsrc", "/box/launcher", "ro"]}},
            "caches": {"cachey": ["/csrc", "/box/cache", "rw"]},
        }})
        capsys.readouterr()
        # The BIND reads back its tuple ...
        assert _get("agent.claude.bindings.ro.launcher") == 0
        assert "/newsrc" in capsys.readouterr().out
        # ... while the CACHES entry beside it in the same file is refused, not faked.
        assert _get("agent.claude.caches.cachey") == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap

    @pytest.mark.parametrize(
        "tail", sorted(TERMINAL_CATEGORY_TAILS), ids=lambda t: ".".join(t),
    )
    def test_a_per_agent_category_TABLE_is_refused_here_not_faked(
        self, config_file, tmp_home, capsys, tail,
    ):
        """THE WHOLE-TABLE TWIN of the per-entry case above, and it went RED on
        2026-08-28 the moment the ``is_known_key`` pre-gate came out of ``system get``.

        That pre-gate was incidentally holding this line: it answered False for
        ``agent.<node>.caches``, so the verb refused it as "unknown". With the pre-gate
        gone, ``key_validity`` DECLARES the key, the §0 gate has no complaint, and the
        read fell through to an engine that has no route to an agent's own file —
        printing "(not set)" over the seven tables this fixture just wrote. A fabricated
        answer for a key whose value is sitting on disk is the fault §0 names, so the
        gate now refuses it and names the noun that DOES read it.

        ⚑ DERIVED FROM ``TERMINAL_CATEGORY_TAILS`` (P13), the same SoT the file-scope
        case above uses; the two differ only in scope, which is the real distinction.
        """
        std = _std(config_file)
        node_file = std.agents / store_dirname("claude") / "agent.yaml"
        node_file.parent.mkdir(parents=True, exist_ok=True)
        dump_doc(node_file, {"self": {
            "masks": ["~/.m"],
            "caches": {"~/.cache/uv": "/host/uv"},
            "seeded": {"~/.s": "/host/s"},
            "synced": {"~/.y": "/host/y"},
            "common": {"~/.c": "/host/c"},
            "bindings": {"ro": {"~/.r": "/host/r"}, "rw": {"~/.w": "/host/w"}},
        }})
        key = ".".join(("agent", "claude", *tail))
        capsys.readouterr()
        assert _get(key) == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap
        assert key in cap.err, cap.err
        # The cure names the noun that answers — and it really does answer.
        assert f"kanibako agent get claude {'.'.join(tail)}" in cap.err, cap.err

    @pytest.mark.parametrize(
        "tail", sorted(TERMINAL_CATEGORY_TAILS), ids=lambda t: ".".join(t),
    )
    def test_the_any_agent_DEFAULT_tier_is_NOT_swept_into_that_refusal(
        self, config_file, tmp_home, capsys, tail,
    ):
        """THE CARVE-OUT THAT IS NOT ONE: ``agent.default.<category>`` is a different
        STORE, not an exception to a rule.

        The any-agent tier lives in the SYSTEM settings file, which this noun does read,
        so the refusal above would break a working read — and its cure would be a lie,
        because there is no ``agents/default/agent.yaml`` and ``kanibako agent get
        default …`` exits 1 on "agent 'default' not found" (measured 2026-08-28).
        """
        std = _std(config_file)
        std.settings.parent.mkdir(parents=True, exist_ok=True)
        # ⚑ ``bindings`` is a TWO-segment tail, so the arm is a SECTION and only the last
        # segment is the leaf — spelling it any other way writes ``agent.default.ro``.
        write_nested_key(
            std.settings, ("agent", "default", *tail[:-1]), tail[-1],
            ["/x"] if tail[-1] == "masks" else {"~/.d": "/host/d"},
        )
        key = ".".join(("agent", "default", *tail))
        capsys.readouterr()
        assert _get(key) == 0, capsys.readouterr().err

    @pytest.mark.parametrize("leaf", sorted(SCALAR_AGENT_LEAVES))
    def test_every_scalar_agent_leaf_still_READS(
        self, config_file, tmp_home, capsys, leaf,
    ):
        """THE REGRESSION HALF, and the reason the gate is not applied flat.

        ⚑ DERIVED FROM THE RULE, NEVER LISTED (P13): the corpus is
        ``SCALAR_AGENT_LEAVES`` itself, so declaring a new scalar leaf extends this pin
        for free, and moving a leaf into ``TABLE_VALUED_AGENT_LEAVES`` moves it out of
        here and into the refusal above — which is the shape change, stated as a test.
        """
        capsys.readouterr()
        assert _get(leaf) == 0, capsys.readouterr().err

    def test_a_bare_leaf_set_at_this_scope_reads_BACK(
        self, config_file, tmp_home, capsys,
    ):
        """The concrete regression the flat gate would have caused: ``system set
        model=opus`` is this verb's own documented example, and its read-back must
        survive the refusal added beside it."""
        assert _set("model=opus") == 0
        capsys.readouterr()
        assert _get("model") == 0
        assert "opus" in capsys.readouterr().out

    def test_a_hand_authored_scope_bind_entry_still_reads(
        self, config_file, tmp_home, capsys,
    ):
        """§0 keeps these READABLE though it refuses the write — *"Refuse the write;
        keep the read honest"*.  The gate carves them out, so adding it must not turn
        the honest read into a refusal."""
        write_nested_key(
            _std(config_file).settings, ("system", "caches"), "helper",
            ["/src", "/dst", "rw"],
        )
        capsys.readouterr()
        assert _get("system.caches.helper") == 0
        assert "/src" in capsys.readouterr().out

    def test_is_known_key_was_NOT_widened(self):
        """THE CONSTRAINT THIS FIX WAS WRITTEN UNDER, pinned so it cannot be undone
        quietly.  ``transform_settings`` must stay key-SHAPED — that is what stops the
        ``box`` positional parser reading it as a project name.  Widening the predicate
        instead of adding the gate would have made this False.

        ⚑ It no longer carries anything to a READ gate (2026-08-28): ``system get`` is
        on ``key_validity`` now and asks this predicate nothing.  The pin survives on
        the DISAMBIGUATION consumers, which are the only ones left."""
        from kanibako.settings.config_keys import is_known_key

        assert is_known_key("transform_settings") is True

    @pytest.mark.parametrize(
        "tail", sorted(TERMINAL_CATEGORY_TAILS), ids=lambda t: ".".join(t),
    )
    def test_a_declared_terminal_category_key_is_NOT_refused_as_unknown(
        self, config_file, tmp_home, capsys, tail,
    ):
        """THE SEVEN THE ``is_known_key`` PRE-GATE ANSWERED WRONG, pinned (2026-08-28).

        ``system.masks`` and the six bind-shaped category terminals are DECLARED,
        ``get_config_value`` reads every one of them, and both sibling nouns already
        served them — yet ``system get`` refused all seven "unknown config key" because
        a SECOND vocabulary (``is_known_key``) gated the read ahead of the §0 one.  §0
        forbids answering that a declared key does not exist quite as firmly as it
        forbids fabricating a value for one that does not.

        ⚑ DERIVED FROM ``TERMINAL_CATEGORY_TAILS`` (P13), the declaration SoT for the
        set — an eighth terminal category is pinned here the day it is declared, and a
        retired one leaves without an edit.
        """
        capsys.readouterr()
        key = ".".join(("system", *tail))
        assert _get(key) == 0, capsys.readouterr().err
        assert "unknown config key" not in capsys.readouterr().err

    @pytest.mark.parametrize(
        "tail", sorted(TERMINAL_CATEGORY_TAILS), ids=lambda t: ".".join(t),
    )
    @pytest.mark.parametrize("scope", ["box", "workset"])
    def test_a_FOREIGN_scope_category_key_is_refused_at_this_noun(
        self, config_file, tmp_home, capsys, scope, tail,
    ):
        """JEI'S RULING, 2026-08-28: *"i dont see any justification for crossscope 'get'.
        it makes no sense at the cli."*

        ⚑ The refusal must NOT demote the key: it IS declared, and saying "unknown config
        key" about it is the same §0 fault the same-scope case above fixes.  It names the
        key, says whose scope it is, and points at the noun that reads it.
        """
        key = ".".join((scope, *tail))
        capsys.readouterr()
        assert _get(key) == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap
        assert "unknown config key" not in cap.err, cap.err
        assert key in cap.err, cap.err
        assert f"kanibako {scope} get" in cap.err, cap.err

    def test_a_foreign_scope_SCALAR_still_reads(
        self, config_file, tmp_home, capsys,
    ):
        """THE LINE THE RULING DOES *NOT* CROSS, and it is drawn by MEASUREMENT.

        A flat "the key's scope must equal the noun's scope" gate refuses 86 reads that
        answer today (29 here, 23 at ``workset``, 34 at ``box`` — counted over the
        manifest, 2026-08-28).  They are DOWNWARD DEFAULTS: ``kanibako system set
        box.image=…`` lands in THIS noun's file and reaches a box through the cascade.

        ⚑ WHAT SEPARATES THIS FROM ``box.caches`` IS THE VALUE'S SHAPE, NOT THE VERB.  A
        SCALAR is held whole by one tier, so this read is a complete answer; a terminal
        category is merged ENTRY BY ENTRY across tiers (spec ``:1085``), so a foreign
        noun holds only a fragment — see :func:`foreign_scope_read_error`, which records
        the two bases that were tried and rejected before that one, including why "does
        this noun's own file carry it" cannot tell these two apart.
        """
        assert _set("box.image=myimg") == 0
        capsys.readouterr()
        assert _get("box.image") == 0
        assert "myimg" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "key", ["meta.box.path", "meta.runtime.ws_root", "meta.workset.name"],
    )
    def test_a_meta_key_is_refused_not_reported_unset(
        self, config_file, tmp_home, capsys, key,
    ):
        """``meta.*`` is DERIVED per box at launch and stored in no settings file, so a
        file-scope noun has nothing to report — and "(not set)" would be a fabrication
        rather than an answer.  The cure names the surface that DOES resolve them."""
        capsys.readouterr()
        assert _get(key) == 1
        cap = capsys.readouterr()
        assert "(not set)" not in cap.out + cap.err, cap
        assert key in cap.err, cap.err
        assert "--effective" in cap.err, cap.err

    def test_a_hand_authored_terminal_category_VALUE_reads_back(
        self, config_file, tmp_home, capsys,
    ):
        """The other half: admitting the seven has to make the read HONEST, not merely
        quiet.  A refusal traded for a permanent "(not set)" would be the fabricated
        answer §0 names, wearing rc 0."""
        write_nested_key(
            _std(config_file).settings, ("system",), "masks", ["/a/b"],
        )
        capsys.readouterr()
        assert _get("system.masks") == 0
        assert "/a/b" in capsys.readouterr().out


class TestSystemSetAnchorsTheNodeItWrites:
    """``system set agent.<node>.canon=@meta.agent.<node>.path/canon`` was refused as a
    dangling ``@``-reference, though ``meta.agent.<node>.path`` is a declared key.

    The set-time snapshot floors the agent STORE-ROOT anchor only for the agent the
    COMMAND names (``_set_time_anchor``'s ``meta_agent_path_floor`` fold).  ``agent set``
    names it because the verb is handed the node; this verb was handing over nothing, so
    the one anchor a per-node key is spelled against was absent and its own store root
    read as a dangling dependency.  ``agent set claude canon=…`` accepted the value and
    ``system set agent.claude.canon=…`` — the same key, the other spelling — refused it.
    """

    KEY = "agent.claude.canon"
    VALUE = "@meta.agent.claude.path/canon"

    def _stored(self, config_file):
        std = _std(config_file)
        return load_doc(std.agents / store_dirname("claude") / "agent.yaml")

    def test_the_node_s_own_store_root_resolves(self, config_file, tmp_home, capsys):
        """MUTATION PROOF: drop ``cascade_agent_name`` from ``system_cmd``'s
        ``set_config_value`` call and this reddens with
        ``dangling @-reference '@meta.agent.claude.path'`` — while the two refusals below
        stay green, so the fix cannot be a blanket accept wearing a pass."""
        capsys.readouterr()
        rc = _set(f"{self.KEY}={self.VALUE}")
        assert rc == 0, capsys.readouterr().err
        assert self._stored(config_file)["self"]["canon"] == self.VALUE

    def test_a_bogus_ref_is_still_refused_by_name(self, config_file, tmp_home, capsys):
        """The floor anchors ONE node, so it cannot become a blanket accept."""
        capsys.readouterr()
        assert _set(f"{self.KEY}=@bogus.ref") == 1
        assert "@bogus.ref" in capsys.readouterr().err

    def test_the_secret_family_is_anchored_too(self, config_file, tmp_home, capsys):
        """THE SECOND ARM, and it is the reason ``agent_node_of`` reads TWO parsers.

        ``agent.<node>.secret_path.<VAR>`` is a per-node PATH key anchored at the same
        store root (``path_key_anchor``'s first arm), but it is parsed by
        ``_parse_agent_node_secret_key`` — ``_parse_persona_agent_key`` does NOT match it.
        A one-parser ``agent_node_of`` would leave this half refused while every other
        test in this class stayed green, so this is the row that pins the choice.

        MUTATION PROOF: drop ``_parse_agent_node_secret_key`` from ``agent_node_of`` and
        THIS test reds alone — the three ``canon`` rows above do not move.
        """
        capsys.readouterr()
        value = "@meta.agent.claude.path/tok"
        rc = _set(f"agent.claude.secret_path.ANTHROPIC_AUTH_TOKEN={value}")
        assert rc == 0, capsys.readouterr().err
        assert self._stored(config_file)["self"]["secret_path"][
            "ANTHROPIC_AUTH_TOKEN"
        ] == value

    def test_another_node_s_root_still_dangles(self, config_file, tmp_home, capsys):
        """WHAT "THE NODE EXISTS" MEANS HERE: the node this write ADDRESSES.  A ref
        spelled against a DIFFERENT node's store root is not anchored by this write and
        is refused, so the shape ``@meta.agent.*.path`` is never accepted on sight."""
        capsys.readouterr()
        assert _set(f"{self.KEY}=@meta.agent.goose.path/canon") == 1
        assert "meta.agent.goose.path" in capsys.readouterr().err
