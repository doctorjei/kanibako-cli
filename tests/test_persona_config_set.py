"""B1: CLI config route for per-persona agent keys (``agent.<node>.<key>``).

These exercise the config-interface engine directly (``set``/``get``/``reset``
threaded with an ``agents_root``), proving:

* a ``+`` node key writes the CANONICAL ``℘`` on-disk dir + the right leaf;
* ``+`` and ``℘`` spellings hit the SAME store (canonicalization) — MUTATION-
  proven (break the ``_resolve_key`` swap and the ``+`` key lands in a
  ``<node-with-+>`` dir the resolver never reads);
* ``get`` reads exactly where ``set`` wrote; ``reset`` removes it;
* a default-only persona file stays SPARSE (only the keys the user set);
* the persona-critical trio makes the persona LOADABLE (``_preflight_persona_
  load``), end-to-end.
"""

from __future__ import annotations

import logging

import pytest

from kanibako.settings.agent_config import load_agent_config
from kanibako.settings.config_keys import ConfigLevel
from kanibako.settings.config_interface import (
    _resolve_key,
    get_config_value,
    is_known_key,
    reset_config_value,
    set_config_value,
)
from kanibako.settings.config_io import load_doc

_URL = "https://gemma.example/v1"
_TOKEN_VAR = "ANTHROPIC_AUTH_TOKEN"


@pytest.fixture
def agents_root(tmp_path):
    root = tmp_path / "agents"
    root.mkdir()
    return root


def _cfg_path(tmp_path):
    return tmp_path / "settings.yaml"


def _node_file(agents_root, node="navigator℘claude"):
    return agents_root / node / "settings.yaml"


# ---------------------------------------------------------------------------
# _resolve_key canonicalization (the seam)
# ---------------------------------------------------------------------------

class TestResolveKeyCanonicalization:
    def test_plus_node_is_canonicalized_to_script_p(self):
        assert (
            _resolve_key("agent.navigator+claude.endpoint")
            == "agent.navigator℘claude.endpoint"
        )

    def test_already_canonical_is_unchanged(self):
        assert (
            _resolve_key("agent.navigator℘claude.endpoint")
            == "agent.navigator℘claude.endpoint"
        )

    def test_secret_path_tail_preserved(self):
        assert (
            _resolve_key(f"agent.navigator+claude.secret_path.{_TOKEN_VAR}")
            == f"agent.navigator℘claude.secret_path.{_TOKEN_VAR}"
        )

    def test_non_persona_key_untouched(self):
        assert _resolve_key("box.image") == "box.image"
        assert _resolve_key("model") == "model"

    def test_malformed_node_left_raw(self):
        # A double separator is an illegal segment → parse raises → left RAW (the
        # set/reset branch surfaces the error; a bad node never silently swaps).
        assert (
            _resolve_key("agent.a+b+c.endpoint") == "agent.a+b+c.endpoint"
        )

    def test_is_known_key_recognizes_persona_key_plus_form(self):
        assert is_known_key("agent.navigator+claude.endpoint")
        assert is_known_key("agent.navigator+claude.secret_path.TOK")
        assert not is_known_key("agent.navigator+claude.bogus")


# ---------------------------------------------------------------------------
# set → canonical dir + leaf
# ---------------------------------------------------------------------------

class TestSetPersona:
    def test_set_endpoint_writes_canonical_node_dir(self, tmp_path, agents_root):
        msg = set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system,
            agents_root=agents_root,
        )
        # The DISPLAY (+form) confirmation, and the value at the ℘ dir + agent leaf.
        assert msg == f"Set agent.navigator+claude.endpoint={_URL}"
        f = _node_file(agents_root)
        assert f.exists()
        assert load_doc(f) == {"self": {"endpoint": _URL}}
        # The +form dir must NOT exist (mutation guard: the swap really happened).
        assert not (agents_root / "navigator+claude").exists()

    def test_set_model_and_endpoint_flat_agent_section(self, tmp_path, agents_root):
        cp = _cfg_path(tmp_path)
        set_config_value(
            "agent.navigator+claude.endpoint", _URL, config_path=cp,
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        set_config_value(
            "agent.navigator+claude.model", "gemma-4-31b-it", config_path=cp,
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert load_doc(_node_file(agents_root)) == {
            "self": {"endpoint": _URL, "model": "gemma-4-31b-it"},
        }

    def test_set_secret_path_token_lands_in_self_section(
        self, tmp_path, agents_root,
    ):
        set_config_value(
            f"agent.navigator+claude.secret_path.{_TOKEN_VAR}", "/host/token",
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        # Stored DIRECTLY under self.secret_path — self IS agent.<node>, so there is
        # no second <node> embedding. The whole self table is what _agent_partial
        # re-roots into the cascade.
        assert load_doc(_node_file(agents_root)) == {
            "self": {"secret_path": {_TOKEN_VAR: "/host/token"}},
        }

    def test_set_env_var_lands_in_env_section(self, tmp_path, agents_root):
        set_config_value(
            "agent.navigator+claude.env.ANTHROPIC_MODEL", "gemma",
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert load_doc(_node_file(agents_root)) == {
            "self": {"env": {"ANTHROPIC_MODEL": "gemma"}},
        }

    def test_set_persona_auto_approve_accepts_bool(self, tmp_path, agents_root):
        """A per-persona ``agent.<node>.auto_approve`` bool literal is accepted +
        written VERBATIM to the node's flat agent leaf (happy path unchanged)."""
        msg = set_config_value(
            "agent.navigator+claude.auto_approve", "false",
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg.startswith("Set")
        assert load_doc(_node_file(agents_root)) == {
            "self": {"auto_approve": "false"},
        }

    def test_set_persona_auto_approve_typo_rejected(self, tmp_path, agents_root):
        """AUTH-CRITICAL: a per-persona ``auto_approve`` typo is REJECTED at set
        time (never written), same write-guard as the bare key (Editor finding B).
        Mutation proof: dropping the guard lets ``garbage`` through and this
        reddens."""
        msg = set_config_value(
            "agent.navigator+claude.auto_approve", "garbage",
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg.startswith("Error:")
        assert "auto_approve must be a boolean" in msg
        # The node file was never written (the typo did not land).
        assert not _node_file(agents_root).exists()

    def test_default_only_persona_file_stays_sparse(self, tmp_path, agents_root):
        # Setting ONLY endpoint must not materialise name/run_args/env/secret_path/
        # tweakcc — the sparse-store rule. The file has EXACTLY the one key.
        set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        data = load_doc(_node_file(agents_root))
        assert data == {"self": {"endpoint": _URL}}
        assert "name" not in data.get("self", {})
        assert "env" not in data.get("self", {}) and "env_file" not in data
        assert "navigator℘claude" not in data.get("self", {})


# ---------------------------------------------------------------------------
# canonicalization: + and ℘ hit the SAME store (mutation-proven)
# ---------------------------------------------------------------------------

class TestCanonicalizationSameStore:
    def test_plus_write_script_p_read_same_value(self, tmp_path, agents_root):
        # Write with the +form, read with the ℘form → SAME store.
        set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        got = get_config_value(
            "agent.navigator℘claude.endpoint",
            global_config_path=tmp_path / "kanibako_config.yaml",
            agents_root=agents_root,
        )
        assert got == _URL

    def test_script_p_write_plus_read_same_value(self, tmp_path, agents_root):
        set_config_value(
            "agent.navigator℘claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        got = get_config_value(
            "agent.navigator+claude.endpoint",
            global_config_path=tmp_path / "kanibako_config.yaml",
            agents_root=agents_root,
        )
        assert got == _URL

    def test_only_one_canonical_dir_exists(self, tmp_path, agents_root):
        set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        dirs = sorted(p.name for p in agents_root.iterdir())
        assert dirs == ["navigator℘claude"]


# ---------------------------------------------------------------------------
# get / reset symmetry
# ---------------------------------------------------------------------------

class TestGetResetPersona:
    def _gc(self, tmp_path):
        return tmp_path / "kanibako_config.yaml"

    def test_get_returns_stored_value(self, tmp_path, agents_root):
        set_config_value(
            "agent.navigator+claude.model", "gemma-4-31b-it",
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        got = get_config_value(
            "agent.navigator+claude.model",
            global_config_path=self._gc(tmp_path), agents_root=agents_root,
        )
        assert got == "gemma-4-31b-it"

    def test_get_unset_returns_none(self, tmp_path, agents_root):
        got = get_config_value(
            "agent.navigator+claude.endpoint",
            global_config_path=self._gc(tmp_path), agents_root=agents_root,
        )
        assert got is None

    def test_reset_removes_override(self, tmp_path, agents_root):
        cp = _cfg_path(tmp_path)
        set_config_value(
            "agent.navigator+claude.endpoint", _URL, config_path=cp,
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        msg = reset_config_value(
            "agent.navigator+claude.endpoint", config_path=cp,
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg == (
            "Cleared agent.navigator+claude.endpoint set on the system scope; "
            "it now falls back through the cascade."
        )
        # The now-empty agent table is pruned → file stays sparse (empty doc).
        assert load_doc(_node_file(agents_root)) == {}
        assert get_config_value(
            "agent.navigator+claude.endpoint",
            global_config_path=self._gc(tmp_path), agents_root=agents_root,
        ) is None

    def test_reset_missing_reports_no_override(self, tmp_path, agents_root):
        msg = reset_config_value(
            "agent.navigator+claude.endpoint", config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg == "No override for agent.navigator+claude.endpoint"


# ---------------------------------------------------------------------------
# guard rails: scope, agents_root, malformed node
# ---------------------------------------------------------------------------

class TestPersonaGuards:
    def test_box_scope_refuses_upward_agent_write(self, tmp_path, agents_root):
        # agent.* is an UPWARD write from box — the directional guard refuses it
        # BEFORE any file is created (box cannot configure a global persona).
        msg = set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.box, agents_root=agents_root,
        )
        assert msg.startswith("Error:")
        assert not _node_file(agents_root).exists()

    def test_missing_agents_root_refuses(self, tmp_path):
        # system scope allows agent.*, but with no agents_root there is nowhere to
        # write — refuse rather than silently drop.
        msg = set_config_value(
            "agent.navigator+claude.endpoint", _URL,
            config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=None,
        )
        assert msg.startswith("Error:")
        assert "system scope" in msg

    def test_malformed_node_errors_and_writes_nothing(self, tmp_path, agents_root):
        msg = set_config_value(
            "agent.a+b+c.endpoint", _URL, config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg.startswith("Error:")
        assert list(agents_root.iterdir()) == []

    def test_reserved_default_node_refused(self, tmp_path, agents_root):
        # ``default`` is the reserved any-agent tier, NOT a persona node — the
        # launch never reads agents/default/ as a node. Refuse + write nothing.
        msg = set_config_value(
            "agent.default.model", "opus", config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg.startswith("Error:")
        assert "reserved" in msg
        assert not (agents_root / "default").exists()

    def test_reserved_default_node_reset_refused(self, tmp_path, agents_root):
        msg = reset_config_value(
            "agent.default.endpoint", config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        assert msg.startswith("Error:")
        assert "reserved" in msg


# ---------------------------------------------------------------------------
# end-to-end: after set, the persona resolves LOADABLE
# ---------------------------------------------------------------------------

class TestPersonaLoadableEndToEnd:
    def test_set_trio_makes_persona_loadable(self, tmp_path, agents_root):
        from kanibako.commands.start import _preflight_persona_load

        token = tmp_path / "token"
        token.write_text("sk-persona-secret\n")
        cp = _cfg_path(tmp_path)
        for key, val in (
            ("agent.navigator+claude.endpoint", _URL),
            ("agent.navigator+claude.model", "gemma-4-31b-it"),
            (f"agent.navigator+claude.secret_path.{_TOKEN_VAR}", str(token)),
        ):
            set_config_value(
                key, val, config_path=cp,
                command_scope=ConfigLevel.system, agents_root=agents_root,
            )

        # Load the just-written persona file the way the launch does, then run the
        # SAME pre-flight the launch runs. The stored endpoint is the keyspace
        # endpoint; the secret_path token resolves → loadable (error is None).
        agent_cfg = load_agent_config(_node_file(agents_root))
        assert agent_cfg.state["endpoint"] == _URL
        assert agent_cfg.state["model"] == "gemma-4-31b-it"
        assert agent_cfg.secret_path[_TOKEN_VAR] == str(token)

        endpoint, error, _adopted, _provider = _preflight_persona_load(
            "navigator℘claude", agent_cfg, _URL, logging.getLogger("test"),
        )
        assert error is None
        assert endpoint == _URL

    def test_endpoint_without_token_is_unloadable(
        self, tmp_path, agents_root, monkeypatch,
    ):
        from kanibako.commands.start import _preflight_persona_load

        # Isolate the host persona dir ($XDG_CONFIG_HOME/claude/<persona>) to an
        # empty tree so the no-token check does not adopt a real host token.
        empty_cfg = tmp_path / "xdgcfg"
        empty_cfg.mkdir()
        monkeypatch.setenv("XDG_CONFIG_HOME", str(empty_cfg))
        set_config_value(
            "agent.navigator+claude.endpoint", _URL, config_path=_cfg_path(tmp_path),
            command_scope=ConfigLevel.system, agents_root=agents_root,
        )
        agent_cfg = load_agent_config(_node_file(agents_root))
        _ep, error, _adopted, _provider = _preflight_persona_load(
            "navigator℘claude", agent_cfg, _URL, logging.getLogger("test"),
        )
        assert error is not None
        assert "no auth token" in error
