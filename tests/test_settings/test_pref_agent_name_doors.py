"""The REAL COMMAND DOORS name an unknown pref agent — the shape message is not what they say.

A ``pref.agent.<bogus>.<category>: {dest: scalar}`` in a settings file has one defect, and it
is the agent name. The settings tier's own bind parse judges the name before the entry shape,
so every reader that assembles a cascade WITHOUT applying prefs reaches that verdict first.
This file drives the three doors the audit named, as the commands themselves, and asserts the
refusal NAMES THE AGENT.

⚑ WHY THESE THREE DOORS AND NOT A HELPER CALL. The three ``assemble_levels`` callers that run
without ``apply_prefs`` re-check nothing, so the non-deferred refusal is the only safe place
for it; a unit test of ``check_bind_map`` would pin that helper while saying nothing about
whether a command reaches it. Each row below drives the command's own entry point.

⚑ TWO DOORS RAISE AND ONE PRINTS, and both are read here. ``box info`` lets ``SettingsError``
escape to its caller; ``shell`` is a CLI shortcut and ``workset set`` is a subcommand, and both
catch it and print ``Error: {e}``. The assertion is on the MESSAGE either way, because the
message is what a user reads.

The other doors in the same audit (``box set``, config ``set`` at system/agent,
``workset share list``) reach the identical parse, which
:mod:`tests.test_settings.test_bind_map_checks` pins through ``_file_partial`` — one judgment,
one refusal, so pinning the parse pins those doors too. Only the rows below claim a command's
own output.
"""

from __future__ import annotations

import argparse
from typing import Any
from unittest.mock import patch

import pytest
import yaml

from kanibako.settings.config import load_config
from kanibako.project.workset import resolve_workset_name
from kanibako.settings.paths import (
    box_workset_settings_paths,
    load_std_paths,
    resolve_project,
    workset_settings_path,
)
from kanibako.settings.settings_resolve import SettingsError

#: The agent name no box runs, so §2h refuses it. Not a plausible harness, so the refusal
#: cannot be misread as a spelling of one.
BOGUS = "zippity"

#: The doors, as ``(id, tier)``. ``workset set`` resolves the WORKSET tier and the other two
#: the BOX tier; a pref is legal at both (spec §2h), and each door reads the tier it resolves.
DOORS = ("shell", "box info", "workset set")

#: The entry SHAPES Q2 names, each with the message it produces on its own at a valid agent.
#: A bogus agent must name the agent INSTEAD, so each row asserts its own needle is absent.
_SHAPES: tuple[tuple[str, Any, str], ...] = (
    ("scalar", "/host/seeded", "structured entry"),
    ("sub-table", {"old_name": {"src": "/host/seeded"}}, "sub-table"),
    ("wrong-arity", ["/host/seeded", "opts", "extra"], "1 or 2 elements"),
)


def _tier_file(std, config, project_dir: str, door: str, body: dict):
    """Write *body* into the settings tier *door* resolves, and return the project dir.

    ``box info`` and ``shell`` resolve the box tier; ``workset set`` resolves the workset
    tier. The path comes from the SAME helper the command uses, so a row cannot drift from
    the file the door actually reads.
    """
    proj = resolve_project(std, config, project_dir=project_dir, initialize=True)
    if door == "workset set":
        ws = resolve_workset_name("default", std)
        path = workset_settings_path(ws)
    else:
        path, _ = box_workset_settings_paths(proj)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")
    return path


def _pref_agent(entry: Any, agent: str = BOGUS) -> dict:
    """``pref.agent.<agent>.seeded`` carrying *entry* — the shape-bearing form."""
    return {"pref": {"agent": {agent: {"seeded": {"/opt/seeded": entry}}}}}


def _door_message(door: str, project_dir: str, capsys) -> str:
    """Run *door* as the command does and return what it told the user.

    Whatever the door does with the refusal — raise it, or catch it and print
    ``Error: {e}`` — the message is the same string, so the caller reads one value.
    """
    message = ""
    try:
        if door == "box info":
            from kanibako.commands.box._parser import run_info

            args = argparse.Namespace(path=project_dir)
            with patch(
                "kanibako.commands.box._parser._check_container_running",
                return_value=(False, "not running (kanibako-test)"),
            ):
                run_info(args)
        elif door == "workset set":
            from kanibako.commands.workset_cmd import run_set

            run_set(argparse.Namespace(
                workset="default", key_value="workset.auth.share_allowed=false", force=False,
            ))
        else:
            # ``shell`` is a CLI SHORTCUT, driven through ``cli.main`` so the shortcut
            # mapping onto ``start`` is the one under test.
            from kanibako.cli import main as cli_main

            cli_main(["shell", project_dir])
    except SettingsError as exc:
        message = str(exc)
    except SystemExit:
        # ``cli.main`` catches the refusal, prints it and EXITS non-zero, so the message is
        # on the stream rather than in an exception. That is the CLI's own contract.
        out = capsys.readouterr()
        message = out.out + out.err
    else:
        out = capsys.readouterr()
        message = out.out + out.err
    return message


@pytest.mark.parametrize(("door", "shape", "entry", "shape_needle"), [
    (door, shape, entry, needle) for door in DOORS for shape, entry, needle in _SHAPES
])
def test_a_command_door_names_the_agent_under_every_shape(
    config_file, tmp_home, credentials_dir, capsys, door: str, shape: str,
    entry: Any, shape_needle: str,
) -> None:
    """Q2 — the name check runs BEFORE any shape check, so EVERY shape names the agent.

    One parametrization over (door × shape), because the claim is a property of the ORDER
    and one hunk controls the order: a shape reaching its own refusal first is the failure,
    so each row asserts its OWN shape's needle is absent rather than asserting a list.

    INVERT: judge the shape before the name at the non-deferred parse -> these nine rows
    go red with that shape's message, and only this assertion catches it.
    """
    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = str(tmp_home / "project")
    _tier_file(std, config, project_dir, door, _pref_agent(entry))
    message = _door_message(door, project_dir, capsys)
    assert f"it names agent '{BOGUS}', which is not a valid agent" in message, message
    assert shape_needle not in message, message


@pytest.mark.parametrize("door", DOORS)
def test_a_command_door_still_refuses_a_valid_agents_entry_by_its_shape(
    config_file, tmp_home, credentials_dir, capsys, door: str,
) -> None:
    """THE COUNTERPART: a VALID agent's bad entry is still a shape refusal at these doors.

    Naming the agent must not become naming EVERY agent — a correct name would be sent to
    rename itself, which is the over-refusal Q2 forbids.
    """
    config = load_config(config_file)
    std = load_std_paths(config)
    project_dir = str(tmp_home / "project")
    _tier_file(std, config, project_dir, door, _pref_agent("/host/seeded", agent="claude"))
    message = _door_message(door, project_dir, capsys)
    assert "Dest-keyed binding entry must be a structured entry" in message, message
    assert "not a valid agent" not in message, message
