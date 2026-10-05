"""Keyspec §2a at the AGENT door: ``agent set`` refuses a target file that carries a bad or
retired entry outside the edited value's chain — naming it, writing nothing.

The authority here is the agent file's OWN reader (:func:`~settings_assemble.agent_record`),
the same one the launch and every read verb already use, so one file keeps one verdict.

⚑ WHY THE READER AND NOT THE SHARED ``_cascade_bad_entries`` SCAN (ruled 2026-10-05, opt A):
the scan reads a settings file against the GENERIC keyspace, which does not know that ``self:``
is the alias for ``agent.<node>``.  Pointed at a legitimate agent file it reports
``self.endpoint`` / ``self.model`` as entries that are not keys — so wiring the scan to this
door would refuse every real ``agent set`` unless ``--force``.  The agent reader already judges
this file type precisely, alias included, and its refusal carries the cure.
"""

from __future__ import annotations

import argparse
import logging

import pytest

_LOGGER = "kanibako.commands.agent_cmd"


@pytest.fixture
def agent_door(config_file, tmp_home):
    """Standard paths for driving the ``agent set`` door."""
    from kanibako.settings.config import load_config
    from kanibako.settings.paths import load_std_paths

    return load_std_paths(load_config(config_file))


def _write_agent(std, doc, node: str = "claude"):
    """Write *doc* verbatim as the node's agent file, bypassing the whole-object save so the
    starting file holds ONLY the keys under test."""
    from kanibako.settings.agent_config import agent_config_path
    from kanibako.settings.config_io import dump_doc

    path = agent_config_path(std.data_path, node)
    path.parent.mkdir(parents=True, exist_ok=True)
    dump_doc(path, doc)
    return path


def _agent_set(node: str, kv: str, *, force: bool = False) -> int:
    from kanibako.commands.agent_cmd import run_set

    return run_set(argparse.Namespace(agent_id=node, key_value=kv, force=force))


class TestAgentSetRefusesABadTargetFile:
    """The door reads the file it is writing into BEFORE it writes, and the reader's refusal
    becomes an rc-1 refusal: no traceback, nothing written."""

    def test_a_stray_top_level_key_is_refused_named_and_nothing_is_written(
        self, agent_door, capsys,
    ):
        path = _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "bogus_stray": 1,
        })
        before = path.read_bytes()

        rc = _agent_set("claude", "env.FOO=bar")
        cap = capsys.readouterr()

        assert rc == 1, cap.out + cap.err
        assert "bogus_stray" in cap.err
        assert str(path) in cap.err
        assert "--force" in cap.err  # §2a: the refusal must say --force will set anyway
        assert path.read_bytes() == before  # byte-identical: the write never happened

    def test_a_retired_auto_approve_is_refused_named_and_nothing_is_written(
        self, agent_door, capsys,
    ):
        path = _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "auto_approve": True,
        })
        before = path.read_bytes()

        rc = _agent_set("claude", "access=full")
        cap = capsys.readouterr()

        assert rc == 1, cap.out + cap.err
        assert "auto_approve" in cap.err
        assert str(path) in cap.err
        assert "--force" in cap.err  # §2a: the refusal must say --force will set anyway
        assert path.read_bytes() == before

    def test_the_refusal_is_a_message_not_a_traceback(self, agent_door, capsys):
        _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "bogus_stray": 1,
        })
        _agent_set("claude", "env.FOO=bar")
        cap = capsys.readouterr()
        assert "Traceback" not in cap.err
        assert cap.err.startswith("Error: ")


class TestALegitimateAgentFileStillSets:
    """The guard does not overreach: a well-shaped file sets at rc 0."""

    def test_nested_self_with_env_sets_cleanly(self, agent_door, capsys):
        path = _write_agent(agent_door, {
            "self": {
                "endpoint": "https://x",
                "model": "opus",
                "env": {"EDITOR": "vim"},
            },
        })

        rc = _agent_set("claude", "model=sonnet")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        assert "Traceback" not in cap.err

        from kanibako.settings.config_io import load_doc

        doc = load_doc(path)
        assert doc["self"]["model"] == "sonnet"
        # Untouched neighbours: the write was sparse.
        assert doc["self"]["endpoint"] == "https://x"
        assert doc["self"]["env"] == {"EDITOR": "vim"}


class TestForceSetsPastABadEntry:
    """§2a's second arm: with ``--force``, WARN and write — and ``set`` never removes the entry."""

    def test_force_writes_and_keeps_a_stray_entry(self, agent_door, caplog):
        path = _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "bogus_stray": 1,
        })
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            rc = _agent_set("claude", "env.FOO=bar", force=True)

        assert rc == 0

        from kanibako.settings.config_io import load_doc

        doc = load_doc(path)
        assert doc["self"]["env"]["FOO"] == "bar"   # the value landed
        assert doc["bogus_stray"] == 1              # …and the bad entry was NOT removed
        assert "bogus_stray" in caplog.text         # warned, not silent

    def test_force_writes_and_keeps_a_retired_entry(self, agent_door, caplog):
        path = _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "auto_approve": True,
        })
        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            rc = _agent_set("claude", "access=full", force=True)

        assert rc == 0

        from kanibako.settings.config_io import load_doc

        doc = load_doc(path)
        assert doc["self"]["access"] == "full"
        assert doc["auto_approve"] is True
        assert "auto_approve" in caplog.text

    def test_force_still_enforces_the_other_checks(self, agent_door, capsys):
        """``--force`` opens the bad-entry gate and NOTHING else: a value that fails a typed or
        enum check is still refused, so the arm cannot be used to smuggle a bad value in."""
        _write_agent(agent_door, {
            "self": {"endpoint": "https://x"},
            "bogus_stray": 1,
        })
        rc = _agent_set("claude", "access=not_a_tier", force=True)
        cap = capsys.readouterr()
        assert rc == 1, cap.out + cap.err

    def test_following_the_printed_cure_with_force_works(self, agent_door, capsys):
        """The reader's cure line says ``Fix: kanibako agent set claude env.FOO=bar``.  A plain
        ``set`` following it now refuses; the SAME command with ``--force`` succeeds.  The cure
        text itself is NOT edited in this task — the mismatch is reported instead."""
        path = _write_agent(agent_door, {
            "self": {"claude": {"env": {"FOO": "bar"}}},
        })

        plain = _agent_set("claude", "model=opus")
        cap = capsys.readouterr()
        assert plain == 1
        assert "Fix:" in cap.err          # the cure the reader prints
        assert "--force" in cap.err       # …and the arm that makes it followable

        forced = _agent_set("claude", "model=opus", force=True)
        assert forced == 0

        from kanibako.settings.config_io import load_doc

        assert load_doc(path)["self"]["model"] == "opus"


class TestTheEditedKeyItselfIsNeverBlocked:
    """§2a's open door: "setting the bad key itself to a valid value is not blocked."

    The exemption the system/workset/box doors get from ``_overwritten_by``; the agent door
    reaches it by judging the file as it would stand after the edit, because the agent reader
    stops at the first bad entry and cannot skip one itself.
    """

    def test_setting_the_bad_key_itself_lands_and_warns_nothing(self, agent_door, caplog, capsys):
        """``self.model`` stored as a TABLE is the bad entry; ``model=opus`` replaces it."""
        path = _write_agent(agent_door, {"self": {"model": {"x": 1}}})

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            rc = _agent_set("claude", "model=opus")
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err
        assert "Warning" not in caplog.text  # the exempt entry is not even mentioned

        from kanibako.settings.config_io import load_doc

        assert load_doc(path)["self"]["model"] == "opus"

    def test_a_bad_entry_elsewhere_still_blocks_while_another_key_is_edited(
        self, agent_door, capsys,
    ):
        """The exemption is narrow: it covers the edited entry only, not its neighbours."""
        path = _write_agent(agent_door, {
            "self": {"model": {"x": 1}, "claude": {"env": {"FOO": "bar"}}},
        })
        before = path.read_bytes()

        rc = _agent_set("claude", "model=opus")
        cap = capsys.readouterr()

        # The `self.claude:` sub-table is still bad after the edit, so the write is refused.
        assert rc == 1, cap.out + cap.err
        assert "self.claude" in cap.err
        assert path.read_bytes() == before

    def test_the_exemption_survives_force_too(self, agent_door, capsys):
        """With ``--force`` the same edit lands as well — the exemption is not arm-dependent."""
        path = _write_agent(agent_door, {"self": {"model": {"x": 1}}})

        rc = _agent_set("claude", "model=opus", force=True)
        cap = capsys.readouterr()

        assert rc == 0, cap.out + cap.err

        from kanibako.settings.config_io import load_doc

        assert load_doc(path)["self"]["model"] == "opus"


class TestTheTempCopyPathNeverReachesTheUser:
    """D1: the verdict is judged on a throwaway COPY, so every error raised in there — the WRITE
    step included — names the copy.  The user must only ever see the real path.

    ``write_leaf`` raises ``ConfigError``, which is a SIBLING of ``SettingsError`` under
    ``KanibakoError``, not a subclass; catching only the reader's class let the writer's
    ``/tmp/kanibako-agentset-*`` path print straight through.
    """

    _TEMP_PREFIX = "kanibako-agentset-"

    def _write_raw(self, std, raw: bytes, node: str = "claude"):
        from kanibako.settings.agent_config import agent_config_path

        path = agent_config_path(std.data_path, node)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_unparseable_yaml_names_the_real_file_and_not_the_temp_copy(
        self, agent_door, capsys,
    ):
        path = self._write_raw(agent_door, b"self:\n  model: [unclosed\n")

        rc = _agent_set("claude", "model=opus")
        cap = capsys.readouterr()
        err = cap.out + cap.err

        assert rc == 1, err
        assert str(path) in err, f"real path missing from: {err}"
        assert self._TEMP_PREFIX not in err, f"temp path leaked: {err}"

    def test_unparseable_yaml_under_force_leaks_no_temp_path(
        self, agent_door, caplog,
    ):
        """Under ``--force`` no text reaching the user — refusal or exception — may name the copy.

        The exit code is pinned by ``test_a_copy_the_write_cannot_land_is_a_plain_refusal``.
        """
        from kanibako.errors import KanibakoError

        self._write_raw(agent_door, b"self:\n  model: [unclosed\n")

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            try:
                _agent_set("claude", "model=opus", force=True)
                raised = ""
            except KanibakoError as exc:
                raised = str(exc)

        seen = caplog.text + raised
        assert self._TEMP_PREFIX not in seen, f"temp path leaked under --force: {seen}"

    def test_a_write_step_refusal_names_the_real_file(self, agent_door, capsys):
        """A value the WRITE step rejects (not the reader) must also name the real file."""
        path = self._write_raw(agent_door, b"self:\n  env: str\n")

        rc = _agent_set("claude", "env.FOO=bar")
        cap = capsys.readouterr()
        err = cap.out + cap.err

        assert rc == 1, err
        assert str(path) in err, f"real path missing from: {err}"
        assert self._TEMP_PREFIX not in err, f"temp path leaked: {err}"

    @pytest.mark.parametrize("force", [False, True], ids=["plain", "force"])
    @pytest.mark.parametrize(("raw", "kv"), [
        (b"self:\n  model: [unclosed\n", "model=opus"),
        (b"self:\n  env: str\n", "env.FOO=bar"),
    ], ids=["malformed-yaml", "non-table-parent"])
    def test_a_copy_the_write_cannot_land_is_a_plain_refusal(
        self, agent_door, capsys, caplog, raw, kv, force,
    ):
        """The copy's WRITE failing is not a bad entry: no ``--force`` cure, no downgrade to a
        warning, the real file untouched — under ``--force`` too."""
        path = self._write_raw(agent_door, raw)

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            rc = _agent_set("claude", kv, force=force)
        cap = capsys.readouterr()
        err = cap.out + cap.err + caplog.text

        assert rc == 1, err
        assert str(path) in err, f"real path missing from: {err}"
        assert self._TEMP_PREFIX not in err, f"temp path leaked: {err}"
        assert "--force" not in err, f"a cure that does not work: {err}"
        assert "Warning" not in err, f"refusal downgraded to a warning: {err}"
        assert path.read_bytes() == raw
