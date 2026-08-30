"""Tests for kanibako.commands.agent_cmd."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kanibako.settings.agent_config import (
    AgentConfig,
    agent_settings_path,
    agents_dir,
)
from kanibako.settings.agent_file import save as write_agent_config
from kanibako.settings.settings_launch import AuthSource

# Auth 3-tier SHARING fixtures replacing the old ``effective_group_auth`` bool
# (2026-07-01 redesign). ``_resolve_box_auth_source`` returns an ``AuthSource``;
# ``.creds_shared`` is the single-bool gate the reauth path consults. A SHARING box
# picks a non-``box`` tier (here ``global``, ``.creds_shared`` True); a PRIVATE box is
# tier ``box`` (``.creds_shared`` False), the old distinct-auth.
_SHARED_AUTH = AuthSource(
    tier="global",
    global_enabled=True,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
)
_PRIVATE_AUTH = AuthSource(
    tier="box",
    global_enabled=False,
    workset_enabled=False,
    global_sync=False,
    workset_source=None,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_env(config_file, tmp_home):
    """Set up an agent environment with one agent defined."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(config_file)
    std = load_std_paths(config)

    adir = agents_dir(std.data_path)
    adir.mkdir(parents=True, exist_ok=True)

    # Create a sample agent
    cfg = AgentConfig(
        name="claude",
        run_args=["--no-helpers"],
        state={"model": "opus"},
        env={"EDITOR": "vim"},
    )
    write_agent_config(agent_settings_path(adir, "claude"), cfg)

    return std.data_path


@pytest.fixture
def empty_agent_env(config_file, tmp_home):
    """Set up an agent environment with no agents defined."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    config = load_config(config_file)
    std = load_std_paths(config)
    return std.data_path


# ---------------------------------------------------------------------------
# agent list
# ---------------------------------------------------------------------------


class TestRunList:
    def test_list_with_agents(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out
        assert "opus" in out

    def test_list_quiet(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == "claude"

    def test_list_no_agents(self, empty_agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "No agents" in out

    def test_list_no_agents_quiet(self, empty_agent_env, capsys):
        from kanibako.commands.agent_cmd import run_list

        args = argparse.Namespace(quiet=True)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert out.strip() == ""

    def test_list_multiple_agents(self, agent_env, capsys):
        """List shows multiple agents sorted by name."""
        from kanibako.commands.agent_cmd import run_list

        adir = agents_dir(agent_env)
        cfg2 = AgentConfig(name="aider", state={"model": "sonnet"})
        write_agent_config(agent_settings_path(adir, "aider"), cfg2)

        args = argparse.Namespace(quiet=False)
        rc = run_list(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "aider" in out
        assert "claude" in out


# ---------------------------------------------------------------------------
# agent info
# ---------------------------------------------------------------------------


class TestRunInfo:
    def test_info_valid_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_info

        args = argparse.Namespace(agent_id="claude")
        rc = run_info(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "claude" in out
        assert "opus" in out
        assert "EDITOR" in out
        assert "--no-helpers" in out

    def test_info_missing_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_info

        args = argparse.Namespace(agent_id="nonexistent")
        rc = run_info(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# agent config
# ---------------------------------------------------------------------------


class TestRunConfig:
    def test_config_show(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_show

        args = argparse.Namespace(agent_id="claude", effective=False)
        rc = run_show(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "model = opus" in out

    def test_config_get_state_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_get

        args = argparse.Namespace(agent_id="claude", key="model")
        rc = run_get(args)
        assert rc == 0
        assert "opus" in capsys.readouterr().out

    def test_config_get_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_get

        args = argparse.Namespace(agent_id="claude", key="env.EDITOR")
        rc = run_get(args)
        assert rc == 0
        assert "vim" in capsys.readouterr().out

    def test_config_get_unset_declared_key(self, agent_env, capsys):
        # A DECLARED key the file does not carry is "(not set)" — the honest read.
        # ⚑ RENAMED from ``test_config_get_missing_key`` and re-keyed with D-5's gate:
        # the old key was ``nonexistent``, which is not a key at all and now REFUSES
        # (see ``TestAgentVerbKeyspaceGate``). "Missing" and "not a key" are two
        # different answers and the verb must not collapse them.
        from kanibako.commands.agent_cmd import run_get

        args = argparse.Namespace(agent_id="claude", key="endpoint")
        rc = run_get(args)
        assert rc == 0
        assert "not set" in capsys.readouterr().err

    def test_config_set_state_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(agent_id="claude", key_value="model=sonnet")
        rc = run_set(args)
        assert rc == 0
        assert "Set model=sonnet" in capsys.readouterr().out

        # Verify the file was updated
        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state["model"] == "sonnet"

    def test_config_set_access_accepts_every_tier(self, agent_env, capsys):
        """AUTH-CRITICAL parity: ``agent set <agent> access=<tier>`` is accepted
        and written VERBATIM to the flat agent leaf — the SAME happy path the
        ``config set`` verb takes (test_config_interface.py). All three tiers
        round-trip.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.agent_config import agent_config_path

        path = agent_config_path(agent_env, "claude")
        for tier in ("restricted", "editing", "full"):
            rc = run_set(argparse.Namespace(
                agent_id="claude", key_value=f"access={tier}",
            ))
            assert rc == 0
            assert f"Set access={tier}" in capsys.readouterr().out
            assert load_doc(path)["self"]["access"] == tier

    def test_config_set_access_off_enum_rejected(self, agent_env, capsys):
        """AUTH-CRITICAL: ``agent set <agent> access=<junk>`` is REJECTED at set
        time (rc 1, the legal tiers on stderr) and the key is NOT written —
        closing the gap that this sibling verb bypassed the ``config set`` guard
        (commit a368026). Mutation proof: dropping the ``is_access_key`` guard
        lets ``fll`` land verbatim and this reddens.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.agent_config import agent_config_path

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="access=fll",
        ))
        assert rc == 1
        assert "restricted | editing | full" in capsys.readouterr().err
        # The typo did not land: the fixture's agent doc has no access key.
        path = agent_config_path(agent_env, "claude")
        assert "access" not in load_doc(path).get("self", {})

    def test_config_set_access_rejects_the_retired_boolean(self, agent_env, capsys):
        """⚑ Muscle memory: the retired ``auto_approve`` vocabulary must not
        sneak back in through the successor key."""
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.agent_config import agent_config_path

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="access=true",
        ))
        assert rc == 1
        path = agent_config_path(agent_env, "claude")
        assert "access" not in load_doc(path).get("self", {})

    def test_config_set_model_still_succeeds_guard_not_overreaching(
        self, agent_env, capsys,
    ):
        """CONTROL: the ``access`` guard does NOT over-reach — a freeform
        ``model`` value still writes fine (only ``access`` is validated).
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="model=whatever",
        ))
        assert rc == 0
        assert "Set model=whatever" in capsys.readouterr().out
        cfg = load_agent_config(agent_config_path(agent_env, "claude"))
        assert cfg.state["model"] == "whatever"

    def test_config_set_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(agent_id="claude", key_value="env.PAGER=less")
        rc = run_set(args)
        assert rc == 0
        assert "Set env.PAGER=less" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.env["PAGER"] == "less"

    def test_config_set_secret_path_key(self, agent_env, capsys):
        # secret_path.<VAR>=<path> stores the POINTER (not a secret) DISCRIMINATED
        # under agent.<node>.secret_path (spec §2a; RENAMED from rc-only env_file).
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(
            agent_id="claude",
            key_value="secret_path.ANTHROPIC_AUTH_TOKEN=~/.config/claude/nav/token",
        )
        rc = run_set(args)
        assert rc == 0
        assert "Set secret_path.ANTHROPIC_AUTH_TOKEN=" in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.secret_path["ANTHROPIC_AUTH_TOKEN"] == "~/.config/claude/nav/token"
        # It must NOT have leaked into the plain env map.
        assert "ANTHROPIC_AUTH_TOKEN" not in cfg.env

    def test_config_get_secret_path_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set, run_get

        run_set(argparse.Namespace(
            agent_id="claude", key_value="secret_path.TOKEN=/secure/token",
        ))
        capsys.readouterr()
        rc = run_get(argparse.Namespace(agent_id="claude", key="secret_path.TOKEN"))
        assert rc == 0
        assert "/secure/token" in capsys.readouterr().out

    def test_config_reset_secret_path_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set, run_reset
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        run_set(argparse.Namespace(
            agent_id="claude", key_value="secret_path.TOKEN=/secure/token",
        ))
        capsys.readouterr()
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="secret_path.TOKEN", all_keys=False, force=False,
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cleared secret_path.TOKEN set on the agent scope" in out
        assert "falls back through the cascade" in out
        assert "Reset secret_path.TOKEN" not in out
        cfg = load_agent_config(agent_config_path(agent_env, "claude"))
        assert "TOKEN" not in cfg.secret_path

    def test_config_shell_is_no_longer_a_key_at_all(self, agent_env, capsys):
        # The template-variant ``shell`` axis was removed, so ``shell`` is neither an
        # AgentConfig identity field nor a declared §2d leaf.
        # ⚑ INVERTED BY D-5, DELIBERATELY. It used to land in generic state rc=0 — which
        # was the defect, not the contract: this verb had NO keyspace validation, so a
        # retired name (and anything else) was stored and read back as though it meant
        # something. Under §0 an undeclared key is not a key; the refusal NAMES it.
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.config_io import load_doc

        args = argparse.Namespace(agent_id="claude", key_value="shell=bash")
        rc = run_set(args)
        assert rc == 1
        err = capsys.readouterr().err
        assert "'shell'" in err and "keyspace is CLOSED" in err

        path = agent_config_path(agent_env, "claude")
        assert "shell" not in load_doc(path)["self"]

    def test_config_reset_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key="model", all_keys=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        # Honest cleared-form (F7): names the CLEAR + the agent scope + the
        # cascade fallback; the old plain "Reset <key>" must be gone.
        out = capsys.readouterr().out
        assert "Cleared model set on the agent scope" in out
        assert "falls back through the cascade" in out
        assert "Reset model" not in out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert "model" not in cfg.state

    def test_config_reset_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key="env.EDITOR", all_keys=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Cleared env.EDITOR set on the agent scope" in out
        assert "falls back through the cascade" in out
        assert "Reset env.EDITOR" not in out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert "EDITOR" not in cfg.env

    def test_config_reset_unset_declared_key(self, agent_env, capsys):
        # ⚑ RE-KEYED with D-5's gate, same reason as the get twin: ``nonexistent`` is not
        # a key and now refuses, so this pins the OTHER answer — a real key with no
        # override stored.
        from kanibako.commands.agent_cmd import run_reset

        args = argparse.Namespace(
            agent_id="claude", key="endpoint", all_keys=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        assert "No override" in capsys.readouterr().out

    def test_config_reset_all_forced(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.agent_config import agent_config_path
        from kanibako.settings.agent_file import load as load_agent_config

        args = argparse.Namespace(
            agent_id="claude", key=None, all_keys=True, force=True,
        )
        rc = run_reset(args)
        assert rc == 0
        # Count-based wording, aligned with the other scopes' reset_all: the
        # fixture has env{EDITOR} + [agent]{run_args, model} = 3 overrides.
        assert "Reset 3 override(s)." in capsys.readouterr().out

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state == {}
        assert cfg.env == {}
        assert cfg.run_args == []

    def test_config_reset_requires_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset

        args = argparse.Namespace(
            agent_id="claude", key=None, all_keys=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err

    def test_config_missing_agent(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_show

        args = argparse.Namespace(agent_id="nonexistent", effective=False)
        rc = run_show(args)
        assert rc == 1
        assert "not found" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# Sparse write engine (agent set/reset re-plumbed onto the nested-key
# primitives — a set/reset must NOT re-materialize default keys, matching the
# B1 `system set agent.*` route: absolute rule [[settings-must-map-to-keystore-key]]).
# ---------------------------------------------------------------------------


def _write_sparse(data_path: Path, agent: str, doc: dict) -> Path:
    """Write *doc* verbatim as ``agents/<agent>/agent.yaml`` and return path.

    Bypasses the whole-object ``agent_file.save`` so the starting file holds
    ONLY the keys under test (a genuinely sparse file, as B1 leaves them).
    """
    from kanibako.settings.agent_config import agent_config_path
    from kanibako.settings.config_io import dump_doc

    path = agent_config_path(data_path, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, doc)
    return path


class TestSparseWrites:
    def test_set_is_sparse_no_default_keys(self, agent_env):
        """Core sparsity guard: a single ``set`` on a sparse file adds exactly
        ONE key and re-materializes NO defaults (name/run_args/env/secret_path/
        tweakcc). Mutation check: reverting to ``agent_file.save`` makes this
        fail — it always emits those default tables.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc

        path = _write_sparse(
            agent_env, "claude", {"self": {"endpoint": "https://x"}},
        )
        rc = run_set(argparse.Namespace(agent_id="claude", key_value="model=gemma"))
        assert rc == 0

        data = load_doc(path)
        assert data == {"self": {"endpoint": "https://x", "model": "gemma"}}
        # Explicit: none of the whole-object dump's default keys appear.
        assert "env" not in data["self"]
        assert "env_file" not in data
        assert "transform_settings" not in data["self"]
        assert "name" not in data["self"]
        assert "run_args" not in data["self"]

    def test_set_routing_lands_at_nested_paths(self, agent_env):
        """Each key form lands at its correct nested (sections, leaf) path."""
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})
        for kv in (
            "model=opus",
            "env.FOO=bar",
            "secret_path.TOK=/p/token",
            "name=Custom",
        ):
            assert run_set(
                argparse.Namespace(agent_id="claude", key_value=kv)
            ) == 0

        data = load_doc(path)
        assert data["self"]["endpoint"] == "x"
        assert data["self"]["model"] == "opus"
        assert data["self"]["name"] == "Custom"
        assert data["self"]["env"] == {"FOO": "bar"}
        # secret_path lands DIRECTLY under self.secret_path (self IS agent.<node>);
        # the whole self table is what _agent_partial re-roots into the cascade.
        assert data["self"]["secret_path"] == {"TOK": "/p/token"}
        # secret_path.<VAR> must NOT leak into the plain env table.
        assert "TOK" not in data["self"]["env"]

    def test_set_run_args_stored_as_list(self, agent_env):
        """run_args is space-split into a LIST (not a bare string)."""
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})
        rc = run_set(
            argparse.Namespace(agent_id="claude", key_value="run_args=--a --b"),
        )
        assert rc == 0
        data = load_doc(path)
        assert data["self"]["run_args"] == ["--a", "--b"]

    def test_the_OTHER_write_route_stores_the_same_shape(self, agent_env, tmp_path):
        """``config set agent.<node>.run_args=…`` and ``agent set`` are ONE shape.

        ⚑ MEASURED APART, ON A REAL STORE, 2026-08-29.  ``kanibako system set
        agent.claude.run_args="--c --d"`` wrote ``run_args: --c --d`` — a STRING —
        while ``kanibako agent set claude run_args="--e --f"`` wrote a list; and
        ``agent_file.load`` read a list or nothing, so the first route's value was
        echoed back at rc 0 and then thrown away.  ``agent show`` printed no
        ``run_args`` line at all and the launch received no arguments.

        ⚑ THE ROW IS THE PAIR, not either half: the split moved into
        ``agent_file.write_leaf``, so re-adding it to ONE caller would still pass a
        single-route test.  Reds if either route stops going through the boundary.
        ⚑ Not ``run_args``-by-name: whatever the file's list-valued leaves are.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.agent_file import _LIST_VALUED_KEYS
        from kanibako.settings.agent_file import load as load_agent_config
        from kanibako.settings.config_io import load_doc
        from kanibako.settings.config_keys import ConfigLevel
        from kanibako.settings.config_interface import set_config_value

        adir = agents_dir(agent_env)
        for leaf in sorted(_LIST_VALUED_KEYS):
            path = _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})

            assert run_set(argparse.Namespace(
                agent_id="claude", key_value=f"{leaf}=--c --d",
            )) == 0
            by_verb = load_doc(path)["self"][leaf]

            _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})
            msg = set_config_value(
                f"agent.claude.{leaf}", "--c --d",
                config_path=tmp_path / "box.yaml",
                command_scope=ConfigLevel.system, agents_root=adir,
            )
            assert not msg.startswith("Error:"), msg
            by_config = load_doc(path)["self"][leaf]

            assert by_verb == by_config == ["--c", "--d"], (leaf, by_verb, by_config)
            # ...and the shape the record actually reads is that one.
            assert getattr(load_agent_config(path), leaf) == ["--c", "--d"]

    def test_reset_key_prunes_empty_table_leaves_siblings(self, agent_env):
        """reset removes the one entry, prunes the now-empty table, and leaves
        sibling tables/keys untouched."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.config_io import load_doc

        path = _write_sparse(
            agent_env, "claude",
            {"self": {"endpoint": "x", "env": {"ONLY": "v"}}},
        )
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="env.ONLY", all_keys=False, force=False,
        ))
        assert rc == 0
        data = load_doc(path)
        assert "env" not in data["self"]  # pruned (was the only env key)
        assert data["self"] == {"endpoint": "x"}  # sibling intact

    def test_reset_unset_key_is_honest_and_leaves_file(self, agent_env, capsys):
        """Resetting a key not present says ``No override`` and does not rewrite
        the file (so no de-sparsifying side effect)."""
        from kanibako.commands.agent_cmd import run_reset

        path = _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})
        before = path.read_text()
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="model", all_keys=False, force=False,
        ))
        assert rc == 0
        assert "No override for model" in capsys.readouterr().out
        assert path.read_text() == before

    def test_reset_unset_name_now_honest(self, agent_env, capsys):
        """ACCEPTED DELTA: the old reset always reported ``name`` cleared;
        sparse reset honestly reports ``No override for name`` when name is not
        in the file (consistent with the F7 honest-reset theme)."""
        from kanibako.commands.agent_cmd import run_reset

        _write_sparse(agent_env, "claude", {"self": {"endpoint": "x"}})
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="name", all_keys=False, force=False,
        ))
        assert rc == 0
        assert "No override for name" in capsys.readouterr().out

    def test_reset_all_preserves_only_name(self, agent_env, capsys):
        """reset --all drops every override — state/env/secret_path/run_args AND
        transform_settings — preserving ONLY name. transform_settings is NOT a
        reset-all exception (it is a normal override once set)."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {
            "self": {
                "name": "Custom", "endpoint": "x", "model": "opus",
                "run_args": ["--a"],
                # secret_path now lives DIRECTLY under self.secret_path.
                "secret_path": {"TOK": "/p"},
                "env": {"FOO": "bar"},
                "transform_settings": {"theme": "dark"},
            },
        })
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key=None, all_keys=True, force=True,
        ))
        assert rc == 0
        # env{FOO} + secret_path{TOK} + self{endpoint, model, run_args,
        # transform_settings} = 6. Only name is preserved.
        assert "Reset 6 override(s)." in capsys.readouterr().out

        data = load_doc(path)
        # Only name survives under self; everything else (incl. transform_settings) gone.
        assert data["self"] == {"name": "Custom"}

    def test_reset_all_confirm_gates_destructive_write(self, agent_env, capsys):
        """Without --force, a declined confirm aborts and leaves the file
        untouched (the confirm prompt gates the destructive clear)."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.errors import UserCancelled

        path = _write_sparse(
            agent_env, "claude", {"self": {"endpoint": "x", "model": "o"}},
        )
        before = path.read_text()
        with patch(
            "kanibako.utils.confirm_prompt", side_effect=UserCancelled(),
        ):
            rc = run_reset(argparse.Namespace(
                agent_id="claude", key=None, all_keys=True, force=False,
            ))
        assert rc == 0
        assert "Aborted" in capsys.readouterr().out
        assert path.read_text() == before


# ---------------------------------------------------------------------------
# agent reauth
# ---------------------------------------------------------------------------


class TestRunReauth:
    # W1: reauth resolves the agent via the unified config.resolve_agent cascade
    # before resolve_target.  We patch resolve_agent to a fixed name so these
    # tests don't depend on the host's installed-agent set, and patch
    # resolve_target for the resulting target object.

    def test_reauth_no_binary(self, config_file, tmp_home, capsys):
        """Reauth errors if no agent binary is found."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
        ):
            target = MagicMock()
            target.has_binary = False
            mock_target.return_value = target
            rc = run_reauth(args)
        assert rc == 1
        assert "No agent target" in capsys.readouterr().err

    def test_reauth_resolution_error_propagates(self, config_file, tmp_home):
        """When the cascade resolves nothing (Gate-2a/2b), the typed
        AgentResolutionError propagates to the top-level cli.py handler —
        reauth does NOT swallow it into a rc-1 with an ad-hoc message."""
        from kanibako.commands.agent_cmd import run_reauth
        from kanibako.errors import NoAgentSelectedError

        args = argparse.Namespace(project=None)
        with patch(
            "kanibako.settings.config.resolve_agent",
            side_effect=NoAgentSelectedError("no agent selected"),
        ):
            with pytest.raises(NoAgentSelectedError):
                run_reauth(args)

    def test_reauth_refreshes_credentials_legacy(self, config_file, tmp_home, capsys):
        """Legacy target (descriptor is None): refresh via the per-plugin hook.

        The gate mirrors start.py exactly — only a ``desc is None`` target uses
        ``target.refresh_credentials``; descriptor-bearing targets route through
        credsync (covered separately below)."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            # The box's SHARING decision is resolved through the launch capability
            # chain (auth 3-tier redesign), which needs a real ``proj.mode``; stub
            # it to a SHARING AuthSource so the ``auth_src.creds_shared`` branch under
            # test is reached unchanged.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            target.descriptor = None
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_called_once_with(proj.shell_path)

    def test_reauth_calls_resolver_with_conformant_kwargs(
        self, config_file, tmp_home, capsys
    ):
        """The reauth path must invoke ``_resolve_box_launch_decisions`` with the
        POST-P6c signature (no ``project_toml`` / ``workset_path`` kwargs — the
        resolver derives those from ``proj`` internally).

        The patch is ``autospec=True``, so the mock enforces the real signature:
        reintroducing the removed kwargs makes this call raise ``TypeError`` inside
        ``run_reauth`` (rc != 0 / propagated) → RED. The explicit ``call_args``
        assertion pins the exact conformant keyword set as a second guard.
        """
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None, None),
            ) as mock_resolve,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            target.descriptor = None
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        # rc == 0 only if the autospec'd resolver accepted the call (signature
        # conformant); a stray project_toml/workset_path would have raised.
        assert rc == 0
        mock_resolve.assert_called_once()
        passed = set(mock_resolve.call_args.kwargs)
        assert passed == {
            "std", "proj", "target", "agent_name", "agent_cfg",
            "system_settings_path", "agent_cfg_path",
            # ⚑ P7: REQUIRED, not optional. reauth resolves the SAME per-agent
            # credential dir the launch delivers from
            # (``@workset.auth.path/@system.agent``); omitting the level collapses
            # it to the workset auth ROOT, so reauth would refresh a different
            # directory than the box reads. The resolver takes it as a REQUIRED
            # keyword precisely so this cannot be dropped silently again.
            "selection_level",
            # The persona store's LIVE tier. reauth computes ``suppress_oauth``
            # from the endpoint this resolver returns, so the tier that carries
            # a persona's endpoint has to reach it here too — otherwise a reauth
            # on a custom-endpoint box could sync the host Anthropic token into a
            # box pointed at a third-party endpoint. ``None`` for a bare agent
            # (this test's ``claude``), so the value changes nothing here; what
            # is pinned is that the kwarg is PASSED.
            "persona_values",
        }
        assert mock_resolve.call_args.kwargs["persona_values"] is None
        # …and it carries the RESOLVED selection, not a placeholder.
        assert mock_resolve.call_args.kwargs["selection_level"] == {
            "system.agent": "claude",
        }
        # The two params P6c removed must NOT be passed.
        assert "project_toml" not in passed
        assert "workset_path" not in passed

    def test_reauth_refreshes_credentials_descriptor(self, config_file, tmp_home, capsys):
        """Descriptor-bearing target: refresh routes through the credsync engine.

        B2 fix: ``run_reauth`` previously called ``target.refresh_credentials``
        UNGATED, sending a descriptor agent (e.g. goose) down its bypassed legacy
        path.  It now mirrors the start.py site: descriptor present ->
        ``credsync.refresh_box_credentials`` (the 3-tier orchestrator) with the
        resolved ``AuthSource``."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.targets.credsync.refresh_box_credentials"
            ) as mock_refresh,
            # SHARING box resolved via the launch chain (auth 3-tier redesign);
            # stub a SHARING AuthSource so the credsync refresh branch is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            desc = MagicMock()
            target.descriptor = desc
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_not_called()
        mock_refresh.assert_called_once_with(
            desc, target, auth=_SHARED_AUTH, host_home=Path.home(),
            project_home=proj.shell_path,
            suppress_oauth=False,
        )

    def test_reauth_suppresses_oauth_when_endpoint_set(
        self, config_file, tmp_home, capsys
    ):
        """Block B (LEAK-PATH fix): reauth on a CUSTOM-endpoint box must SUPPRESS
        the OAuth cred sync — else it would push the Anthropic token into a box
        pointed at a third-party endpoint. The resolved endpoint (non-None) →
        ``suppress_oauth=True`` on the credsync refresh.

        Mutation check: the sibling test above (endpoint None) asserts
        ``suppress_oauth=False`` on the SAME call, so this True is non-vacuous.
        """
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.targets.credsync.refresh_box_credentials"
            ) as mock_refresh,
            # SHARING box, but with a resolved persona endpoint → the fork fires.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, "http://localhost:8080", None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            desc = MagicMock()
            target.descriptor = desc
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        mock_refresh.assert_called_once_with(
            desc, target, auth=_SHARED_AUTH, host_home=Path.home(),
            project_home=proj.shell_path,
            suppress_oauth=True,
        )

    def test_reauth_skips_refresh_for_distinct(self, config_file, tmp_home, capsys):
        """Distinct auth does not trigger credential refresh."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            # Distinct auth = PRIVATE box (tier ``box``, ``.creds_shared`` False); stub a
            # private AuthSource so the distinct-auth branch is taken.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_PRIVATE_AUTH, None, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                # Distinct auth with credentials present returns 0 before check_auth
                creds_path = MagicMock()
                creds_path.is_file.return_value = True
                target.credential_check_path.return_value = creds_path
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        target.refresh_credentials.assert_not_called()

    def test_reauth_runs_inbox_setup_when_auth_fails_and_setup_declared(
        self, config_file, tmp_home, capsys,
    ):
        """FIX 2: check_auth fails + target declares setup_entrypoint -> reauth
        delegates to _run_container(setup_only=True) (the in-box setup path)
        instead of just printing 'authentication failed'."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        with (
            patch("kanibako.settings.config.resolve_agent", return_value="goose"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container", return_value=0,
            ) as m_run,
            # SHARING box via the launch chain (auth 3-tier redesign); stub a
            # SHARING AuthSource so the shared-auth-fail path is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = "goose"
            target.display_name = "Goose"
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 0
        m_run.assert_called_once()
        assert m_run.call_args.kwargs.get("setup_only") is True

    def test_reauth_no_setup_when_auth_fails_and_no_setup_cmd(
        self, config_file, tmp_home, capsys,
    ):
        """check_auth fails + no setup command (claude) -> standard failure, rc 1."""
        from kanibako.commands.agent_cmd import run_reauth

        args = argparse.Namespace(project=None)
        from kanibako.settings.agent_select import AgentSelection
        with (
            patch(
                "kanibako.settings.agent_select.select_agent",
                return_value=AgentSelection(node="claude", source="settings"),
            ),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container",
            ) as m_run,
            # SHARING box via the launch chain (auth 3-tier redesign); stub a
            # SHARING AuthSource so the shared-auth-fail path is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = None
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.settings.paths.resolve_any_project") as mock_proj:
                proj = MagicMock()
                mock_proj.return_value = proj

                rc = run_reauth(args)

        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "authentication failed" in err.lower()


# ---------------------------------------------------------------------------
# Parser / alias tests
# ---------------------------------------------------------------------------


class TestAgentParser:
    def test_agent_is_subcommand(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "agent" in _SUBCOMMANDS

    def test_fork_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "fork" not in _SUBCOMMANDS

    def test_helper_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "helper" not in _SUBCOMMANDS

    def test_reauth_no_longer_top_level(self):
        from kanibako.cli import _SUBCOMMANDS
        assert "reauth" not in _SUBCOMMANDS

    def test_agent_default_is_list(self):
        """Running 'agent' with no subcommand defaults to list."""
        from kanibako.cli import build_parser
        from kanibako.commands.agent_cmd import run_list

        parser = build_parser()
        args = parser.parse_args(["agent"])
        assert args.func == run_list

    def test_helper_list_alias_ls(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "ls"])
        assert args.command == "box"
        assert hasattr(args, "func")

    def test_helper_send(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "send", "3", "hello"])
        assert args.number == 3
        assert args.message == "hello"

    def test_helper_broadcast(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "broadcast", "all hands"])
        assert args.message == "all hands"

    def test_helper_log(self):
        from kanibako.cli import build_parser

        parser = build_parser()
        args = parser.parse_args(["box", "helper", "log", "-f", "--from", "1", "--last", "5"])
        assert args.follow is True
        assert args.from_helper == 1
        assert args.last == 5


class TestAgentSetNull:
    """B-5: ``agent set`` ADVERTISES ``--null`` but never read it.

    The flag parsed (it is on the parser) and the bare key then fell through to
    the GET fallback, so ``kanibako agent set claude --null model`` PRINTED the
    current model and exited 0 — an accepted, silently-ignored write.

    It is REFUSED rather than wired, because this file's reader coerces what it
    loads (``agent_file.load`` builds ``cfg.state``/``cfg.env`` with
    ``str(v)``): a YAML null here would read back as the TEXT ``"None"``, and
    for ``access`` that is not a legal tier at all — a suppression flag that
    would leave the box refusing to launch (and, before R-41 made the resolver
    exact, would have launched it PERMISSIVE).  Agent-file null semantics need
    the reader to change with them.
    """

    def _stored(self, agent_env):
        from kanibako.settings.config_io import load_doc

        return load_doc(agent_settings_path(agents_dir(agent_env), "claude"))

    def test_null_is_refused_and_names_both_cures(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="model", null=True,
        ))
        assert rc == 1
        cap = capsys.readouterr()
        # INVERT: with the refusal removed this is the GET fallback — rc 0 and
        # the stored value on stdout, which is the silent-read bug itself.
        assert "opus" not in cap.out
        assert "not supported at agent scope" in cap.err
        assert "'agent reset claude model'" in cap.err
        assert "'--null pref.agent.claude.model'" in cap.err

    def test_refusal_writes_nothing(self, agent_env, capsys):
        """The whole point of refusing: the file is untouched, so nothing reads
        back as the string 'None' later."""
        from kanibako.commands.agent_cmd import run_set

        before = self._stored(agent_env)
        assert run_set(argparse.Namespace(
            agent_id="claude", key_value="model", null=True,
        )) == 1
        assert run_set(argparse.Namespace(
            agent_id="claude", key_value="env.EDITOR", null=True,
        )) == 1
        assert run_set(argparse.Namespace(
            agent_id="claude", key_value="access", null=True,
        )) == 1
        assert self._stored(agent_env) == before

    def test_null_with_a_value_names_the_key_alone_in_the_cure(
        self, agent_env, capsys,
    ):
        """``--null key=value`` supplies two values; the refusal still has to
        name the KEY, not echo the whole token into an untypeable command."""
        from kanibako.commands.agent_cmd import run_set

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="model=sonnet", null=True,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "'agent reset claude model'" in err
        assert "model=sonnet" not in err
        assert self._stored(agent_env)["self"]["model"] == "opus"  # untouched

    def test_null_without_a_key_is_refused(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value=None, null=True,
        ))
        assert rc == 1
        assert "requires a key" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# The ``agent`` noun's own closed-keyspace gate (D-5 · D-6 · D-7 · D-4's write half)
# ---------------------------------------------------------------------------


def _stored_doc(agent_env):
    from kanibako.settings.config_io import load_doc

    return load_doc(agent_settings_path(agents_dir(agent_env), "claude"))


class TestAgentSetRefusesABareRelativePath:
    """[R147] reaches ``agent set`` BECAUSE the verb routes through ``set_config_value``.

    ⚑ THIS IS THE PAYOFF OF THAT ROUTING, pinned at the CLI surface rather than at the
    engine: while this verb had its own writer straight to ``write_leaf``, no set-time
    rule saw the value at all — the same class of hole that let a dangling ``@``-ref
    store at rc 0.  ``canon`` and ``template`` are the two path-valued agent leaves.
    ⚑ MUTATION: restore a direct ``write_leaf`` for these two leaves and both rows go
    green-with-a-stored-value; the engine-level sweep in
    ``tests/test_settings/test_path_key_set_refusal.py`` stays green throughout, which is
    exactly why this row is here and not only there.
    """

    @pytest.mark.parametrize("leaf", ("canon", "template"))
    def test_a_bare_relative_is_refused_and_the_file_is_unchanged(
        self, leaf, agent_env, capsys,
    ):
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(agent_id="claude", key_value=f"{leaf}=mydir"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "BARE RELATIVE" in err
        # Both readings named — the refusal removes the guess rather than relaying it.
        assert str(Path.cwd() / "mydir") in err
        assert "@meta.agent.claude.path" in err or "/mydir" in err
        assert _stored_doc(agent_env) == before

    @pytest.mark.parametrize("value", ("/srv/x", "~/x", "$XDG_DATA_HOME/x",
                                       "@meta.agent.claude.path/x"))
    def test_the_legal_spellings_still_write(self, value, agent_env, capsys):
        """⚑ THE OVER-FIRE GUARD. A refusal one notch too wide here bans the ``@``-ref
        the message itself offers as the cure."""
        from kanibako.commands.agent_cmd import run_set

        rc = run_set(argparse.Namespace(agent_id="claude", key_value=f"canon={value}"))
        assert rc == 0, capsys.readouterr().err
        assert _stored_doc(agent_env)["self"]["canon"] == value


class TestAgentVerbKeyspaceGate:
    """D-5: ``agent set`` had NO keyspace validation and stored whatever it was handed.

    Every other noun routes its writes through ``set_config_value``, which owns the §0 check;
    this verb has its own writer and had none, so it was the ONE place a user could type
    ``self.`` and have it land on disk (ruling 55).  The gate is ``key_validity`` on the
    canonical key built from the KNOWN node, NEVER ``is_known_key``: measured,
    ``is_known_key("agent.claude.self.model")`` is True (the persona parser reads the node as
    ``claude.self``), so a literal ``is_known_key`` gate would leave exactly this hole open while
    refusing ``run_args`` and ``name``.
    """

    _REFUSED = ("self.model", "self.claude.env.FOO", "anything.at.all", "shell")

    @pytest.mark.parametrize("key", _REFUSED)
    def test_set_refuses_and_writes_nothing(self, key, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(agent_id="claude", key_value=f"{key}=x"))
        assert rc == 1
        assert "keyspace is CLOSED" in capsys.readouterr().err
        assert _stored_doc(agent_env) == before

    def test_the_self_alias_is_refused_on_the_command_line(self, agent_env, capsys):
        """RULING 55, at the one surface that could still accept it.

        ``self`` is a FILE-SURFACE alias substituted at the parse boundary; nothing past that
        boundary recognises it, and the way it stays out of the code is that no parser admits
        it.  Every other noun already refused ``self.`` as unknown — this verb did not.
        """
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="self.model=sonnet",
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "self.model" in err
        # Nothing landed — in particular no ``self.model`` leaf under the root.
        assert _stored_doc(agent_env) == before
        assert "self.model" not in _stored_doc(agent_env)["self"]

    @pytest.mark.parametrize("verb", ("get", "reset"))
    def test_read_and_reset_take_the_same_vocabulary(self, verb, agent_env, capsys):
        """SYMMETRY (spec §0): reading or resetting an undeclared key is equally an error.

        "(not set)" and "No override for …" are both LIES about a spelling that is not a key —
        the same reason ``reset_config_value`` refuses the retired bind routes symmetrically.
        """
        from kanibako.commands.agent_cmd import run_get, run_reset

        if verb == "get":
            rc = run_get(argparse.Namespace(agent_id="claude", key="self.model"))
        else:
            rc = run_reset(argparse.Namespace(
                agent_id="claude", key="self.model", all_keys=False, force=False,
            ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "keyspace is CLOSED" in err

    @pytest.mark.parametrize(
        "kv", ("model=opus", "env.FOO=bar", "secret_path.TOK=/p", "name=Nav",
               "run_args=--a --b"),
    )
    def test_the_live_keys_still_write(self, kv, agent_env, capsys):
        """THE GREEN HALF. ``name`` is NOT a declared §2d leaf — it is a FILE-identity field
        of ``AgentConfig`` — so it rides an explicit ``IDENTITY_KEYS`` allowlist. Drop that
        allowlist and this case dies; that is the mutation proof for the residue."""
        from kanibako.commands.agent_cmd import run_set

        assert run_set(
            argparse.Namespace(agent_id="claude", key_value=kv)
        ) == 0

    def test_a_plugin_declared_leaf_is_not_refused(self, agent_env, capsys):
        """The PLUGIN union (§0 *"Agent specifics are PLUGIN-declared"*).

        ``provider`` is declared by the goose target through ``setting_descriptors()``, not by
        core's §2d table. MUTATION PROOF: drop ``agent_leaves=`` from the gate's
        ``key_validity`` call and this reddens while every core key stays green.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.settings_prefs import default_valid_agents

        if "provider" not in (getattr(default_valid_agents(), "leaves", None) or ()):
            pytest.skip("no installed plugin declares 'provider' in this environment")
        rc = run_set(argparse.Namespace(agent_id="claude", key_value="provider=ollama"))
        assert rc == 0


class TestAgentSetRoutesThroughTheOneSetter:
    """``agent set`` reached the file through its OWN writer, so no set-time validation ran.

    The verb called ``agent_file.write_leaf`` directly and never entered
    ``config_interface.set_config_value``, so the E3 RESOLUTION probe every other noun's ``set``
    runs did not run here.  MEASURED on an isolated store: ``kanibako agent set claude
    canon=@bogus.ref`` printed ``Set canon=@bogus.ref`` at rc 0 and stored the dangling
    reference, while the SAME value through ``kanibako system set`` was refused by name.

    Two writers of one keyspace slot is the defect, so the fix is ONE writer — not a second copy
    of the checks in the second writer.  ⚑ ``name`` still writes through the file boundary and
    that is NOT a carve-out: it is a FILE-identity field of ``AgentConfig``, absent from the
    keyspace, so the shared setter has no key to route (pinned by
    ``TestAgentVerbKeyspaceGate.test_the_live_keys_still_write``).
    """

    def test_a_dangling_ref_is_refused_and_nothing_lands(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="canon=@bogus.ref",
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "@bogus.ref" in err
        assert _stored_doc(agent_env) == before

    def test_the_refusal_is_the_one_the_shared_setter_produces(
        self, agent_env, capsys, tmp_path,
    ):
        """ONE wording, not two: the text printed is the setter's own, reused verbatim.

        A refusal drafted here would drift from the one ``system set`` prints for the same
        value at the same key — which is how a user comes to believe two doors mean two rules.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.settings.config_interface import set_config_value
        from kanibako.settings.config_keys import ConfigLevel

        assert run_set(argparse.Namespace(
            agent_id="claude", key_value="canon=@bogus.ref",
        )) == 1
        by_verb = capsys.readouterr().err.strip()

        by_config = set_config_value(
            "agent.claude.canon", "@bogus.ref",
            config_path=tmp_path / "box.yaml",
            command_scope=ConfigLevel.system,
            agents_root=agents_dir(agent_env),
        )
        assert by_verb == by_config.strip()

    def test_a_ref_that_resolves_still_writes(self, agent_env, capsys):
        """THE GREEN HALF, and it is what keeps the probe from refusing legal values.

        ``@meta.agent.<node>.path`` is the agent STORE-ROOT anchor; the set-time snapshot
        carries it only for a node the caller NAMES.  MUTATION PROOF: drop
        ``cascade_agent_name`` from the verb's ``set_config_value`` call and this reddens —
        the ref dangles — while the ``@bogus.ref`` case above stays green.
        """
        from kanibako.commands.agent_cmd import run_set

        rc = run_set(argparse.Namespace(
            agent_id="claude", key_value="canon=@meta.agent.claude.path/canon",
        ))
        assert rc == 0
        assert (
            _stored_doc(agent_env)["self"]["canon"]
            == "@meta.agent.claude.path/canon"
        )

    def test_a_poisoned_file_is_still_repairable(self, agent_env, capsys):
        """The set-time snapshot must NOT read the node's OWN file, and this is why.

        A nested ``self.<sub>:`` sub-table is refused by ``agent_file``'s cascade reader, and the
        repair verbs deliberately never go through it — a poisoned file still lists, still
        displays, and can still be fixed from the command line.  MEASURED: threading the agent
        tier into the set-time cascade raises ``SettingsError`` out of ``assemble_levels``, which
        would both break ``set_config_value``'s never-raises contract and take the repair path
        away on the one file that needs it.  MUTATION PROOF: add ``cascade_agent_path=path`` to
        the verb's ``set_config_value`` call and this reddens with that traceback.
        """
        from kanibako.commands.agent_cmd import run_set

        _write_sparse(
            agent_env, "claude", {"self": {"claude": {"env": {"FOO": "bar"}}}},
        )
        rc = run_set(argparse.Namespace(agent_id="claude", key_value="model=opus"))
        assert rc == 0
        assert _stored_doc(agent_env)["self"]["model"] == "opus"


class TestAgentResetRoutesThroughTheOneSetter:
    """``agent reset`` removed the leaf with its OWN hand while ``set`` already routed.

    There was no VALIDATION hole — the verb runs the same ``_agent_key_gate`` — so the defect is
    structural: two writers of one keyspace slot, and a removal is a write.  ``reset`` now calls
    ``config_interface.reset_config_value``, the resetter ``workset`` / ``system`` / ``box``
    already share, with the threading its ``set`` twin uses.

    ⚑ THE GATE STAYS IN FRONT OF IT, and that is not a duplicate of the engine: the gate judges
    the tail against the KNOWN-GOOD node (the on-disk store dir), which the shared engine
    structurally cannot do — it reads the node OUT of the key, so ``self.model`` parses as a node
    ``claude.self``.  Pinned by ``test_the_self_alias_is_still_refused_by_the_gate``.
    ⚑ ``name`` is still removed at the file boundary and that is NOT a carve-out: it is a
    FILE-identity field of ``AgentConfig``, absent from the keyspace, so the shared resetter has
    no key to route.
    """

    _ROUTED = ("model", "run_args", "env.EDITOR", "secret_path.TOKEN")

    @staticmethod
    def _spy(monkeypatch):
        """Record every ``reset_config_value`` call and still run the real one."""
        from kanibako.settings import config_interface

        calls = []
        real = config_interface.reset_config_value

        def recorder(key, **kwargs):
            calls.append((key, kwargs))
            return real(key, **kwargs)

        monkeypatch.setattr(config_interface, "reset_config_value", recorder)
        return calls

    @pytest.mark.parametrize("key", _ROUTED)
    def test_every_declared_shape_enters_the_shared_resetter(
        self, key, agent_env, monkeypatch, capsys,
    ):
        """MUTATION PROOF: put ``remove_leaf(slot_for(...))`` back and no call is recorded."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.config_keys import ConfigLevel

        calls = self._spy(monkeypatch)
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key=key, all_keys=False, force=False,
        ))
        assert rc == 0, capsys.readouterr().err
        assert len(calls) == 1
        routed_key, kwargs = calls[0]
        # The CANONICAL key the engine's per-node routes read, built from the KNOWN node.
        assert routed_key == f"agent.claude.{key}"
        # The threading that makes those routes reachable at all — the per-node agent store is
        # global, so the command scope is SYSTEM and the node is named.
        assert kwargs["command_scope"] is ConfigLevel.system
        assert kwargs["cascade_agent_name"] == "claude"
        assert kwargs["agents_root"] == agents_dir(agent_env)

    def test_name_is_the_one_tail_the_verb_still_removes_itself(
        self, agent_env, monkeypatch, capsys,
    ):
        """The IDENTITY residue: no key, so nothing for the shared resetter to route."""
        from kanibako.commands.agent_cmd import run_reset

        calls = self._spy(monkeypatch)
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="name", all_keys=False, force=False,
        ))
        assert rc == 0, capsys.readouterr().err
        assert calls == []
        assert "Cleared name set on the agent scope" in capsys.readouterr().out

    def test_the_reserved_any_agent_tier_is_refused(self, agent_env, capsys):
        """The guard the routing BUYS, and the one ``set`` has had since it routed.

        ``default`` is the reserved any-agent tier, not a persona node; the engine refuses a
        per-node write against it and prescribes the bare spelling.  The verb's own gate cannot
        see this — ``agent_write_key_error('default', 'model')`` is ``None``, measured — so
        while ``reset`` wrote by hand it silently removed from a store dir named ``default``.
        MUTATION PROOF: restore ``remove_leaf(slot_for(...))`` and this returns 0 with
        "No override for model".
        """
        from kanibako.commands.agent_cmd import run_reset

        _write_sparse(agent_env, "default", {"self": {"model": "opus"}})
        rc = run_reset(argparse.Namespace(
            agent_id="default", key="model", all_keys=False, force=False,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "reserved any-agent tier" in err

    def test_the_refusal_is_the_one_the_shared_resetter_produces(
        self, agent_env, capsys, tmp_path,
    ):
        """ONE wording, not two — the same rule ``set``'s refusal-parity test pins."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.settings.config_interface import reset_config_value
        from kanibako.settings.config_keys import ConfigLevel

        _write_sparse(agent_env, "default", {"self": {"model": "opus"}})
        assert run_reset(argparse.Namespace(
            agent_id="default", key="model", all_keys=False, force=False,
        )) == 1
        by_verb = capsys.readouterr().err.strip()

        by_config = reset_config_value(
            "agent.default.model",
            config_path=tmp_path / "box.yaml",
            command_scope=ConfigLevel.system,
            agents_root=agents_dir(agent_env),
        )
        assert by_verb == by_config.strip()

    def test_the_no_op_prefix_is_the_engines(self, agent_env, tmp_path):
        """The verb reads "did anything change" OFF the engine's answer, so pin the wording.

        MUTATION PROOF for the coupling: change either half of ``"No override for "`` and the
        verb starts reporting a cleared key it never cleared.
        """
        from kanibako.settings.config_interface import reset_config_value
        from kanibako.settings.config_keys import ConfigLevel

        msg = reset_config_value(
            "agent.claude.canon",
            config_path=tmp_path / "box.yaml",
            command_scope=ConfigLevel.system,
            agents_root=agents_dir(agent_env),
        )
        assert msg.startswith("No override for ")

    def test_the_self_alias_is_still_refused_by_the_gate(self, agent_env, capsys):
        """RULING 55 survives the routing, and it must be the GATE that says so.

        Routing without the gate does not merely change the wording — it blames the AGENT NAME
        for a bad KEY: measured, the engine answers ``agent.claude.self.model`` with "invalid
        agent name 'claude.self'".  MUTATION PROOF: drop the gate and this reddens on both
        assertions while the routed cases above stay green.
        """
        from kanibako.commands.agent_cmd import run_reset

        before = _stored_doc(agent_env)
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="self.model", all_keys=False, force=False,
        ))
        assert rc == 1
        err = capsys.readouterr().err
        assert "keyspace is CLOSED" in err
        assert "invalid agent name" not in err
        assert _stored_doc(agent_env) == before


class TestRetiredBindRoutesRefuseByName:
    """D-4's write half: the bind-shaped categories are refused BY NAME, never degraded.

    A retired spelling gets its own message and cure (§0) rather than "not a declared key", and
    the refusal comes from the SAME derived recogniser the other verbs use — so it covers all
    five bind-shaped categories rather than being a bindings-only rule someone widens later.
    """

    _RETIRED = (
        "bindings.ro./box/share", "bindings.rw./box/w", "caches.~/.cache/uv",
        "seeded.~", "common.~/.claude/plugins", "synced.~/.config/x",
    )

    @pytest.mark.parametrize("key", _RETIRED)
    def test_set_refuses_and_the_file_is_unchanged(self, key, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(agent_id="claude", key_value=f"{key}=/h/x"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "agent.yaml" in err             # the cure names the node's own file
        assert _stored_doc(agent_env) == before

    @pytest.mark.parametrize("key", _RETIRED)
    def test_reset_refuses_symmetrically(self, key, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset

        rc = run_reset(argparse.Namespace(
            agent_id="claude", key=key, all_keys=False, force=False,
        ))
        assert rc == 1
        assert "No override" not in capsys.readouterr().out

    @pytest.mark.parametrize(
        "key", ("bindings.ro", "caches", "masks", "transform_settings"),
    )
    def test_a_whole_table_takes_no_scalar(self, key, agent_env, capsys):
        """D-7: the TERMINAL keys are declared and pass the keyspace gate — what refuses
        them is the VALUE SHAPE. Their entries are DATA inside the table."""
        from kanibako.commands.agent_cmd import run_set

        before = _stored_doc(agent_env)
        rc = run_set(argparse.Namespace(agent_id="claude", key_value=f"{key}=x"))
        assert rc == 1
        err = capsys.readouterr().err
        assert "holds a TABLE" in err
        assert _stored_doc(agent_env) == before


class TestAgentGetReadsWhatTheFileCarries:
    """D-6: ``agent get`` printed "(not set)" over values the file actually holds.

    The requirement is agreement: ``agent get <node> <tail>`` and ``config get
    agent.<node>.<tail>`` read ONE file through ONE boundary slot, so they must render ONE
    string — the category tables have no ``AgentConfig`` field, which is why the record-only
    read could not answer for them at all.
    """

    _DOTTED = "~/.cache/uv"

    def _hand_author(self, agent_env):
        from kanibako.settings.config_io import dump_doc

        path = agent_settings_path(agents_dir(agent_env), "claude")
        dump_doc(path, {"self": {
            "name": "claude",
            "bindings": {"ro": {
                "/box/share": ["/host/share"],
                self._DOTTED: ["/store/uv"],
            }},
            "caches": {self._DOTTED: ["/store/uv"]},
            "masks": {"~/.ssh": True},
        }})
        return path

    def test_agent_get_reads_the_hand_authored_bind_entry(self, agent_env, capsys):
        """The one per-entry spelling whose READ survived R-9 — and the refusal that
        prescribes hand-editing it promises the read-back works, so it must."""
        from kanibako.commands.agent_cmd import run_get

        self._hand_author(agent_env)
        rc = run_get(argparse.Namespace(
            agent_id="claude", key="bindings.ro./box/share",
        ))
        assert rc == 0
        assert "/host/share" in capsys.readouterr().out

    @pytest.mark.parametrize("tail", ("bindings.ro", "caches", "masks"))
    def test_agent_get_reads_the_whole_terminal_table(self, tail, agent_env, capsys):
        """The TERMINAL category key is what §2a declares, and it reads back the whole
        dest-keyed map — which is what makes every retired-route cure checkable."""
        from kanibako.commands.agent_cmd import run_get

        self._hand_author(agent_env)
        rc = run_get(argparse.Namespace(agent_id="claude", key=tail))
        assert rc == 0
        assert capsys.readouterr().out.strip() not in ("", "(not set)")

    @pytest.mark.parametrize("tail", ("caches.~/.cache/uv", "masks.~/.ssh"))
    def test_a_per_entry_spelling_refuses_and_names_the_terminal_key(
        self, tail, agent_env, capsys,
    ):
        """⚑ NOT A GAP IN THE READ — §0. Outside the ``bindings`` arms a per-entry
        spelling is not a key at ANY scope (2026-08-08c), so the honest answer is the
        refusal naming the terminal key, never "(not set)" over a value that is there."""
        from kanibako.commands.agent_cmd import run_get

        self._hand_author(agent_env)
        rc = run_get(argparse.Namespace(agent_id="claude", key=tail))
        assert rc == 1
        err = capsys.readouterr().err
        assert "box destinations" in err
        assert tail.partition(".")[0] in err

    def test_the_two_verbs_render_one_string(self, agent_env, capsys):
        """The AGREEMENT, pinned by comparison rather than by two expected literals.

        ⚑ THE DESTINATION IS DOTTED ON PURPOSE. Both verbs now resolve through ONE address
        rule, so a mutation to that rule moves them TOGETHER and an equality alone would stay
        green — which is the very shape of a test that passes while pinning nothing. A dotted
        dest is what the split MUTATION actually breaks: restore ``tail.split(".")`` and both
        sides go empty/``None``, which is not equality, and this dies.
        """
        from kanibako.commands.agent_cmd import run_get
        from kanibako.settings.agent_file import read_leaf, slot_for
        from kanibako.settings.config_interface import get_config_value

        self._hand_author(agent_env)
        tail = f"bindings.ro.{self._DOTTED}"
        run_get(argparse.Namespace(agent_id="claude", key=tail))
        via_agent = capsys.readouterr().out.strip()
        via_config = get_config_value(
            f"agent.claude.{tail}",
            global_config_path=agent_env / "config.yaml",
            agents_root=agents_dir(agent_env),
        )
        assert via_agent == via_config
        assert via_agent == read_leaf(
            slot_for(agents_dir(agent_env), "claude", tail),
        )

    def test_agent_info_does_not_raise_on_a_malformed_table(self, agent_env, capsys):
        """D-7's repair-reachability half: a scalar where a table belongs used to raise out
        of ``agent_file.load``, so the very verbs that SHOW a user their broken file died."""
        from kanibako.commands.agent_cmd import run_info
        from kanibako.settings.config_io import dump_doc

        dump_doc(
            agent_settings_path(agents_dir(agent_env), "claude"),
            {"self": {"name": "claude", "transform_settings": "oops"}},
        )
        assert run_info(argparse.Namespace(agent_id="claude")) == 0
