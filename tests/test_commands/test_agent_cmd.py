"""Tests for kanibako.commands.agent_cmd."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from kanibako.agent_config import (
    AgentConfig,
    agent_settings_path,
    agents_dir,
    write_agent_config,
)
from kanibako.settings_launch import AuthSource

# Auth 3-tier SHARING fixtures replacing the old ``effective_group_auth`` bool
# (2026-07-01 redesign). ``_resolve_box_auth_source`` returns an ``AuthSource``;
# ``.shares`` is the single-bool gate the reauth path consults. A SHARING box
# picks a non-``box`` tier (here ``global``, ``.shares`` True); a PRIVATE box is
# tier ``box`` (``.shares`` False), the old distinct-auth.
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
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths

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
    from kanibako.config import load_config
    from kanibako.paths import load_std_paths

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

    def test_config_get_missing_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_get

        args = argparse.Namespace(agent_id="claude", key="nonexistent")
        rc = run_get(args)
        assert rc == 0
        assert "not set" in capsys.readouterr().err

    def test_config_set_state_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(agent_id="claude", key_value="model=sonnet")
        rc = run_set(args)
        assert rc == 0
        assert "Set model=sonnet" in capsys.readouterr().out

        # Verify the file was updated
        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert cfg.state["model"] == "sonnet"

    def test_config_set_env_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_set
        from kanibako.agent_config import agent_config_path, load_agent_config

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
        from kanibako.agent_config import agent_config_path, load_agent_config

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
        from kanibako.agent_config import agent_config_path, load_agent_config

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

    def test_config_shell_is_no_longer_an_identity_key(self, agent_env, capsys):
        # The template-variant ``shell`` axis was removed; ``shell`` is no longer
        # an AgentConfig identity field, so a ``shell=`` set now lands in generic
        # state (not as a dedicated identity knob).
        from kanibako.commands.agent_cmd import run_set
        from kanibako.agent_config import agent_config_path, load_agent_config

        args = argparse.Namespace(agent_id="claude", key_value="shell=bash")
        rc = run_set(args)
        assert rc == 0

        path = agent_config_path(agent_env, "claude")
        cfg = load_agent_config(path)
        assert not hasattr(cfg, "shell")
        assert cfg.state["shell"] == "bash"

    def test_config_reset_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.agent_config import agent_config_path, load_agent_config

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
        from kanibako.agent_config import agent_config_path, load_agent_config

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

    def test_config_reset_missing_key(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset

        args = argparse.Namespace(
            agent_id="claude", key="nonexistent", all_keys=False, force=False,
        )
        rc = run_reset(args)
        assert rc == 0
        assert "No override" in capsys.readouterr().out

    def test_config_reset_all_forced(self, agent_env, capsys):
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.agent_config import agent_config_path, load_agent_config

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
    """Write *doc* verbatim as ``agents/<agent>/settings.yaml`` and return path.

    Bypasses the whole-object ``write_agent_config`` so the starting file holds
    ONLY the keys under test (a genuinely sparse file, as B1 leaves them).
    """
    from kanibako.agent_config import agent_config_path
    from kanibako.config_io import dump_doc

    path = agent_config_path(data_path, agent)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, doc)
    return path


class TestSparseWrites:
    def test_set_is_sparse_no_default_keys(self, agent_env):
        """Core sparsity guard: a single ``set`` on a sparse file adds exactly
        ONE key and re-materializes NO defaults (name/run_args/env/secret_path/
        tweakcc). Mutation check: reverting to ``write_agent_config`` makes this
        fail — it always emits those default tables.
        """
        from kanibako.commands.agent_cmd import run_set
        from kanibako.config_io import load_doc

        path = _write_sparse(
            agent_env, "claude", {"agent": {"endpoint": "https://x"}},
        )
        rc = run_set(argparse.Namespace(agent_id="claude", key_value="model=gemma"))
        assert rc == 0

        data = load_doc(path)
        assert data == {"agent": {"endpoint": "https://x", "model": "gemma"}}
        # Explicit: none of the whole-object dump's default keys appear.
        assert "env" not in data
        assert "env_file" not in data
        assert "tweakcc" not in data
        assert "name" not in data["agent"]
        assert "run_args" not in data["agent"]

    def test_set_routing_lands_at_nested_paths(self, agent_env):
        """Each key form lands at its correct nested (sections, leaf) path."""
        from kanibako.commands.agent_cmd import run_set
        from kanibako.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {"agent": {"endpoint": "x"}})
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
        assert data["agent"]["endpoint"] == "x"
        assert data["agent"]["model"] == "opus"
        assert data["agent"]["name"] == "Custom"
        assert data["env"] == {"FOO": "bar"}
        # secret_path lands DISCRIMINATED under agent.<node>.secret_path (the shape
        # _agent_partial reads into the cascade), NOT a flat top-level section.
        assert data["agent"]["claude"] == {"secret_path": {"TOK": "/p/token"}}
        # secret_path.<VAR> must NOT leak into the plain env table.
        assert "TOK" not in data["env"]

    def test_set_run_args_stored_as_list(self, agent_env):
        """run_args is space-split into a LIST (not a bare string)."""
        from kanibako.commands.agent_cmd import run_set
        from kanibako.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {"agent": {"endpoint": "x"}})
        rc = run_set(
            argparse.Namespace(agent_id="claude", key_value="run_args=--a --b"),
        )
        assert rc == 0
        data = load_doc(path)
        assert data["agent"]["run_args"] == ["--a", "--b"]

    def test_reset_key_prunes_empty_table_leaves_siblings(self, agent_env):
        """reset removes the one entry, prunes the now-empty table, and leaves
        sibling tables/keys untouched."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.config_io import load_doc

        path = _write_sparse(
            agent_env, "claude",
            {"agent": {"endpoint": "x"}, "env": {"ONLY": "v"}},
        )
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="env.ONLY", all_keys=False, force=False,
        ))
        assert rc == 0
        data = load_doc(path)
        assert "env" not in data          # pruned (was the only env key)
        assert data["agent"] == {"endpoint": "x"}  # sibling intact

    def test_reset_unset_key_is_honest_and_leaves_file(self, agent_env, capsys):
        """Resetting a key not present says ``No override`` and does not rewrite
        the file (so no de-sparsifying side effect)."""
        from kanibako.commands.agent_cmd import run_reset

        path = _write_sparse(agent_env, "claude", {"agent": {"endpoint": "x"}})
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

        _write_sparse(agent_env, "claude", {"agent": {"endpoint": "x"}})
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key="name", all_keys=False, force=False,
        ))
        assert rc == 0
        assert "No override for name" in capsys.readouterr().out

    def test_reset_all_preserves_name_and_tweakcc(self, agent_env, capsys):
        """reset --all drops state/env/secret_path/run_args but PRESERVES name and
        tweakcc (behavior-parity with the old de-sparse reset)."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.config_io import load_doc

        path = _write_sparse(agent_env, "claude", {
            "agent": {
                "name": "Custom", "endpoint": "x", "model": "opus",
                "run_args": ["--a"],
                # secret_path now lives DISCRIMINATED under agent.<node>.secret_path.
                "claude": {"secret_path": {"TOK": "/p"}},
            },
            "env": {"FOO": "bar"},
            "tweakcc": {"theme": "dark"},
        })
        rc = run_reset(argparse.Namespace(
            agent_id="claude", key=None, all_keys=True, force=True,
        ))
        assert rc == 0
        # env{FOO} + secret_path{TOK} + [agent]{endpoint, model, run_args} = 5.
        assert "Reset 5 override(s)." in capsys.readouterr().out

        data = load_doc(path)
        assert data["agent"] == {"name": "Custom"}  # state/run_args/node-sub gone
        assert "env" not in data
        assert data["tweakcc"] == {"theme": "dark"}  # preserved

    def test_reset_all_confirm_gates_destructive_write(self, agent_env, capsys):
        """Without --force, a declined confirm aborts and leaves the file
        untouched (the confirm prompt gates the destructive clear)."""
        from kanibako.commands.agent_cmd import run_reset
        from kanibako.errors import UserCancelled

        path = _write_sparse(
            agent_env, "claude", {"agent": {"endpoint": "x", "model": "o"}},
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
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
            "kanibako.config.resolve_agent",
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            # The box's SHARING decision is resolved through the launch capability
            # chain (auth 3-tier redesign), which needs a real ``proj.mode``; stub
            # it to a SHARING AuthSource so the ``auth_src.shares`` branch under
            # test is reached unchanged.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            target.descriptor = None
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None),
            ) as mock_resolve,
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            target.descriptor = None
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.targets.credsync.refresh_box_credentials"
            ) as mock_refresh,
            # SHARING box resolved via the launch chain (auth 3-tier redesign);
            # stub a SHARING AuthSource so the credsync refresh branch is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            desc = MagicMock()
            target.descriptor = desc
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.targets.credsync.refresh_box_credentials"
            ) as mock_refresh,
            # SHARING box, but with a resolved persona endpoint → the fork fires.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, "http://localhost:8080"),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = True
            target.display_name = "Claude Code"
            desc = MagicMock()
            target.descriptor = desc
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            # Distinct auth = PRIVATE box (tier ``box``, ``.shares`` False); stub a
            # private AuthSource so the distinct-auth branch is taken.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_PRIVATE_AUTH, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
            patch("kanibako.config.resolve_agent", return_value="goose"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container", return_value=0,
            ) as m_run,
            # SHARING box via the launch chain (auth 3-tier redesign); stub a
            # SHARING AuthSource so the shared-auth-fail path is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = "goose"
            target.display_name = "Goose"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
        with (
            patch("kanibako.config.resolve_agent", return_value="claude"),
            patch("kanibako.targets.resolve_target") as mock_target,
            patch(
                "kanibako.commands.start._run_container",
            ) as m_run,
            # SHARING box via the launch chain (auth 3-tier redesign); stub a
            # SHARING AuthSource so the shared-auth-fail path is reached.
            patch(
                "kanibako.commands.start._resolve_box_launch_decisions",
                autospec=True,
                return_value=(_SHARED_AUTH, None),
            ),
        ):
            target = MagicMock()
            target.has_binary = True
            target.check_auth.return_value = False
            target.setup_entrypoint = None
            target.display_name = "Claude Code"
            mock_target.return_value = target

            with patch("kanibako.paths.resolve_any_project") as mock_proj:
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
