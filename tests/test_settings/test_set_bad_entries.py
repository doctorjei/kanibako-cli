"""Keyspec §2a: an entry that is not a key, in a file the command reads, refuses ``set``
unless ``--force`` — and an entry the edited value's own ``@``-chain reaches is refused
either way. ``--force`` and ``get`` warn and proceed; the entry is never removed."""

from __future__ import annotations

import io
import logging

import pytest
import yaml

from kanibako.settings.config_interface import get_config_value, set_config_value, show_config
from kanibako.settings.config_keys import ConfigLevel

_LOGGER = "kanibako.settings.config_interface"

#: The bad entry IS the input the set-time rule judges, so the census needs it named.
_JUDGES = "the bad entry IS the input the rule judges; the set-time snapshot reads it"


def _write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc))


def _system_set(tmp_path, key, value, **kw):
    ssp = tmp_path / "settings.yaml"
    return set_config_value(
        key, value, config_path=tmp_path / "kanibako.cfg",
        system_settings_path=ssp, cascade_system_path=ssp,
        command_scope=ConfigLevel.system, **kw,
    )


def _system_get(tmp_path, key):
    ssp = tmp_path / "settings.yaml"
    return get_config_value(
        key, global_config_path=tmp_path / "kanibako.cfg",
        system_settings_path=ssp, cascade_system_path=ssp,
        command_scope=ConfigLevel.system,
    )


class TestSetRefusesOnABadEntry:
    """The OUT-of-chain arm: an error unless ``--force``, which warns and writes."""

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_refused_names_entry_and_writes_nothing(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"box": {"shell": "bash", "bogus": 1}})
        before = ssp.read_text()
        msg = _system_set(tmp_path, "box.shell", "zsh")
        assert msg.startswith("Error: ")
        assert str(ssp) in msg and "box.bogus = 1" in msg
        assert "--force" in msg
        assert ssp.read_text() == before

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_force_warns_writes_and_keeps_the_entry(self, tmp_path, caplog):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"box": {"shell": "bash", "bogus": 1}})
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            msg = _system_set(tmp_path, "box.shell", "zsh", force=True)
        assert msg == "Set box.shell=zsh"
        assert "box.bogus = 1" in caplog.text
        assert yaml.safe_load(ssp.read_text())["box"] == {"shell": "zsh", "bogus": 1}

    def test_a_clean_cascade_sets_silently(self, tmp_path, caplog):
        _write(tmp_path / "settings.yaml", {"box": {"shell": "bash"}})
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert _system_set(tmp_path, "box.shell", "zsh") == "Set box.shell=zsh"
        assert "not keys" not in caplog.text

    @pytest.mark.writes_undeclared("box.zzz", reason=_JUDGES)
    def test_a_bad_entry_in_a_containing_file_refuses_a_workset_set(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        ws_file = tmp_path / "ws" / "workset.yaml"
        _write(ssp, {"box": {"zzz": 1}})
        _write(ws_file, {"box": {"shell": "bash"}})
        msg = set_config_value(
            "box.shell", "zsh", config_path=ws_file, cascade_system_path=ssp,
            command_scope=ConfigLevel.workset,
        )
        assert msg.startswith("Error: ") and "box.zzz = 1" in msg
        assert yaml.safe_load(ws_file.read_text()) == {"box": {"shell": "bash"}}

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_a_ref_to_another_key_is_not_the_chain_arm(self, tmp_path, caplog):
        """A file holding a bad entry is not by itself a broken chain: a value naming a
        key that IS declared takes the ``--force`` arm, never the hard one."""
        _write(tmp_path / "settings.yaml", {
            "system": {"agent": "claude"}, "box": {"bogus": 1},
        })
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            msg = _system_set(tmp_path, "system.template", "@system.canon/x", force=True)
        assert msg == "Set system.template=@system.canon/x", msg
        assert "upstream chain reaches" not in caplog.text


class TestAnInChainBadEntryIsHard:
    """The HARD arm: ``--force`` does not reach it, and the broken upstream is named.

    ⚑ The edited key is ``box.canon``, a contained-scope key, NOT a system path key: a
    system path value referencing a bad entry is refused EARLIER, by the system path
    tier's own door (``config.system_path_ref_error``), so it never reaches this arm."""

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_force_does_not_override_a_chain_that_reaches_it(self, tmp_path, caplog):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"box": {"bogus": "/tmp"}})
        before = ssp.read_text()
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            msg = _system_set(tmp_path, "box.canon", "@box.bogus/x", force=True)
        assert msg.startswith("Error: "), msg
        assert "box.bogus" in msg and "--force does not set" in msg
        assert ssp.read_text() == before
        assert "not keys" not in caplog.text  # a refusal names it; it does not warn

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_the_refusal_is_the_same_without_force(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"box": {"bogus": "/tmp"}})
        msg = _system_set(tmp_path, "box.canon", "@box.bogus/x")
        assert msg.startswith("Error: ") and "--force does not set" in msg, msg

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_the_chain_is_followed_through_a_declared_key(self, tmp_path):
        """Two hops: the value names a DECLARED key whose own value names the bad entry."""
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {
            "agent": {"default": {"canon": "@box.bogus/x"}},
            "box": {"canon": "/tmp/ok", "bogus": "/tmp"},
        })
        msg = _system_set(tmp_path, "box.canon", "@agent.default.canon/y", force=True)
        assert msg.startswith("Error: ") and "box.bogus" in msg, msg
        assert "--force does not set" in msg, msg
        assert yaml.safe_load(ssp.read_text())["box"]["canon"] == "/tmp/ok"

    @pytest.mark.writes_undeclared("box.self", reason=_JUDGES)
    def test_a_self_referential_entry_terminates_and_is_refused(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"box": {"self": "@box.self/x"}})
        msg = _system_set(tmp_path, "box.canon", "@box.self/x", force=True)
        assert msg.startswith("Error: ") and "box.self" in msg, msg

    def test_the_walk_terminates_on_a_loop_through_declared_keys(self):
        """The door's E3 probe refuses a cyclic value first, so the walk's own SEEN set is
        pinned on the function: a two-key loop that reaches no bad entry ends empty."""
        from kanibako.settings.config import chain_reaches

        stored = {"box.canon": "@box.shell/x", "box.shell": "@box.canon/y"}.get
        assert chain_reaches("@box.canon/z", key="box.shell", targets=["box.bogus"], stored=stored) == []
        assert chain_reaches("@box.canon/z", key="box.shell", targets=["box.shell"], stored=stored) == ["box.shell"]
        stored2 = {"box.canon": "@box.bogus/x"}.get
        assert chain_reaches("@box.canon/z", key="box.shell", targets=["box.bogus"], stored=stored2) == ["box.bogus"]


class TestAnEndpointHasNoChain:
    """Keyspec ``agent.default.endpoint``: the endpoint is TEXT, so the ``@host`` in it is no
    ref and never reaches a bad entry — the walk scans neither the edited endpoint nor the
    stored endpoint a ref names.  Only the out-of-chain arm, which ``--force`` passes, is left."""

    _EP = "https://u:SEKRITP@host.invalid/v1"

    @pytest.mark.parametrize("key", ["endpoint", "agent.claude.endpoint"])
    def test_an_endpoint_naming_a_bad_entry_sets_with_force(self, tmp_path, key):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"host": {"invalid": 1}})
        agents = tmp_path / "agents"
        refused = _system_set(tmp_path, key, self._EP, agents_root=agents)
        assert refused.startswith("Error: ") and "upstream chain" not in refused, refused
        assert "--force" in refused
        msg = _system_set(tmp_path, key, self._EP, force=True, agents_root=agents)
        assert msg == f"Set {key}={self._EP}", msg

    def test_a_ref_to_a_stored_endpoint_does_not_read_its_text(self, tmp_path):
        ssp = tmp_path / "settings.yaml"
        _write(ssp, {"host": {"invalid": 1}, "agent": {"default": {"endpoint": self._EP}}})
        msg = _system_set(
            tmp_path, "agent.claude.model", "@agent.default.endpoint", force=True,
            agents_root=tmp_path / "agents",
        )
        assert msg == "Set agent.claude.model=@agent.default.endpoint", msg

    def test_a_box_pref_endpoint_naming_a_bad_entry_sets_with_force(self, tmp_path):
        box = tmp_path / "box.yaml"
        _write(box, {"host": {"invalid": 1}})
        msg = set_config_value(
            "pref.agent.claude.endpoint", self._EP, config_path=box,
            cascade_system_path=tmp_path / "settings.yaml", cascade_box_path=box,
            command_scope=ConfigLevel.box, force=True,
        )
        assert msg == f"Set pref.agent.claude.endpoint={self._EP}", msg

    def test_the_walk_skips_text_and_still_follows_other_refs(self):
        from kanibako.settings.config import chain_reaches

        stored = {"agent.default.endpoint": "https://u:k@box.bogus/v1"}.get
        bad = ["box.bogus"]
        assert chain_reaches(self._EP, ["host.invalid"], key="endpoint", stored=stored) == []
        assert chain_reaches("@agent.default.endpoint", bad, key="box.canon", stored=stored) == []
        assert chain_reaches("@box.bogus/x", bad, key="box.canon", stored=stored) == bad


class TestGetWarnsOnTheSameEntries:
    """``get`` warns and reads on — and says NOTHING ELSE about the file it read."""

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_warns_on_a_bad_entry_and_still_answers(self, tmp_path, caplog):
        _write(tmp_path / "settings.yaml", {
            "system": {"agent": "claude"}, "box": {"bogus": 1},
        })
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert _system_get(tmp_path, "system.agent") == "claude"
        assert "box.bogus = 1" in caplog.text

    @pytest.mark.writes_undeclared("box.bogus", reason=_JUDGES)
    def test_says_nothing_about_a_table_the_cascade_drops(self, tmp_path, caplog):
        """The bad-entry scan judges the file through the reader that ANNOUNCES every
        dropped table.  A ``get`` is not a ``show``, so those lines are the drop's to
        speak and this scan must not speak them."""
        _write(tmp_path / "settings.yaml", {
            "system": {"agent": "claude"},
            "meta": {"x": 1},
            "box": {"bogus": 1},
        })
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert _system_get(tmp_path, "system.agent") == "claude"
        assert "box.bogus = 1" in caplog.text
        assert "Dropping" not in caplog.text

    def test_a_clean_file_is_silent(self, tmp_path, caplog):
        _write(tmp_path / "settings.yaml", {"system": {"agent": "claude"}})
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            assert _system_get(tmp_path, "system.agent") == "claude"
        assert caplog.text == ""


class TestShowFlagsADroppedTableGetReads:
    """His Q5 ("3 - agreed"): plain ``show`` flags, in one line, a table the cascade
    drops at the noun that ``get`` still reads."""

    def test_box_show_flags_the_dropped_tables(self, tmp_path):
        box_file = tmp_path / "box.yaml"
        _write(box_file, {
            "box": {"shell": "zsh"},
            "agent": {"default": {"model": "opus"}},
            "meta": {"x": 1},
        })
        out = io.StringIO()
        show_config(
            global_config_path=tmp_path / "kanibako.cfg", command_scope=ConfigLevel.box,
            config_path=box_file, file=out,
        )
        flagged = [ln for ln in out.getvalue().splitlines() if ln.startswith("  (dropped")]
        assert len(flagged) == 1
        assert "stores agent:" in flagged[0] and "'get' still reads them" in flagged[0]
        assert "meta" not in flagged[0]

    def test_no_dropped_table_no_line(self, tmp_path):
        box_file = tmp_path / "box.yaml"
        _write(box_file, {"box": {"shell": "zsh"}})
        out = io.StringIO()
        show_config(
            global_config_path=tmp_path / "kanibako.cfg", command_scope=ConfigLevel.box,
            config_path=box_file, file=out,
        )
        assert "(dropped" not in out.getvalue()


class TestEverySetParserTakesForce:
    @pytest.mark.parametrize(
        "argv",
        [
            ["system", "set", "--force", "system.agent=claude"],
            ["workset", "set", "--force", "ws1", "box.shell=zsh"],
            ["box", "set", "--force", "box.shell=zsh"],
            ["agent", "set", "--force", "claude", "model=opus"],
        ],
    )
    def test_the_flag_parses(self, argv):
        from kanibako.cli import build_parser

        parser = build_parser()
        assert parser.parse_args(argv).force is True, argv
