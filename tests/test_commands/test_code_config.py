"""Tests for kanibako.vscode.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from kanibako.settings.core_defaults import ROM_ROOT_PARTS, _canon_dest, packaged_data_dir
from kanibako.vscode.vscode_config import (
    _AGENT_MARKER_REMOVE_COMMAND,
    _AGENT_MARKER_REMOVE_RETIRED_COMMANDS,
    _AGENT_MARKER_WRITE_COMMAND,
    _AGENT_MARKER_WRITE_RETIRED_COMMANDS,
    _CHARTER_HOOK_DIR,
    _CODEX_EVENT_KEY,
    _CODEX_EVENT_KEYS,
    _CODEX_MANAGED_HOOKS,
    _MAIL_MONITOR_COMMAND,
    _SESSION_END_MATCHER,
    _SESSION_START_COMMAND,
    _SESSION_START_MATCHER,
    _SESSION_START_RETIRED_COMMANDS,
    AGENT_MARKERS_DIR,
    CodexModelProvider,
    _encode_image_ref,
    attached_container_config_path,
    codex_trusted_hash,
    merge_attached_container_config,
    merge_codex_config,
    merge_codex_model_provider,
    merge_marker_remove_hook,
    merge_marker_write_hook,
    merge_session_start_hook,
    seed_attached_container_config,
    seed_codex_config,
    seed_codex_sandbox_mode,
    seed_session_start_hook,
)

_WS = "/home/agent/workspace"
_EXT = "anthropic.claude-code"


# --- _encode_image_ref -----------------------------------------------------

def test_encode_image_ref_canonical_confirmed():
    """CONFIRMED byte-exact for the canonical lowercase ref VS Code reads."""
    assert (
        _encode_image_ref("ghcr.io/doctorjei/kanibako-oci:latest")
        == "ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest"
    )


def test_encode_image_ref_whole_string_lowercase():
    """Uppercase-tag ref is WHOLE-STRING lowercased (assumed VS Code behavior:
    ``encodeURIComponent(x).toLowerCase()`` — pending a Phase-0 uppercase confirm)."""
    assert _encode_image_ref("reg/img:PR-42") == "reg%2fimg%3apr-42"


# --- attached_container_config_path ----------------------------------------

def test_config_path_is_exact():
    """Pin the FULL image-keyed host path VS Code reads on attach."""
    path = attached_container_config_path(
        "ghcr.io/doctorjei/kanibako-oci:latest", Path("/home/u/.config"),
    )
    assert path == Path(
        "/home/u/.config/Code/User/globalStorage/"
        "ms-vscode-remote.remote-containers/imageConfigs/"
        "ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest.json"
    )


# --- merge_attached_container_config (pure) --------------------------------

def test_merge_into_empty_adds_ext_and_workspace():
    merged = merge_attached_container_config(
        {}, workspace_folder=_WS, extension=_EXT,
    )
    assert merged == {"extensions": [_EXT], "workspaceFolder": _WS}


def test_merge_adds_extension_when_missing():
    merged = merge_attached_container_config(
        {"extensions": ["github.copilot-chat"]}, workspace_folder=_WS, extension=_EXT,
    )
    assert merged["extensions"] == ["github.copilot-chat", _EXT]


def test_merge_no_dup_when_present():
    """Set-union: an already-present extension is not added twice."""
    merged = merge_attached_container_config(
        {"extensions": [_EXT]}, workspace_folder=_WS, extension=_EXT,
    )
    assert merged["extensions"] == [_EXT]


def test_merge_preserves_other_extensions_and_keys():
    existing = {
        "extensions": ["github.copilot-chat"],
        "workspaceFolder": "/somewhere/else",
        "unknownKey": {"nested": 1},
    }
    merged = merge_attached_container_config(
        existing, workspace_folder=_WS, extension=_EXT,
    )
    assert merged["extensions"] == ["github.copilot-chat", _EXT]
    assert merged["unknownKey"] == {"nested": 1}


def test_merge_does_not_clobber_existing_workspace():
    merged = merge_attached_container_config(
        {"workspaceFolder": "/somewhere/else"}, workspace_folder=_WS, extension=_EXT,
    )
    assert merged["workspaceFolder"] == "/somewhere/else"


def test_merge_sets_workspace_when_absent():
    merged = merge_attached_container_config(
        {"extensions": [_EXT]}, workspace_folder=_WS, extension=None,
    )
    assert merged["workspaceFolder"] == _WS


def test_merge_none_extension_leaves_extensions_untouched():
    """extension=None adds nothing (and creates no extensions key if absent)."""
    merged = merge_attached_container_config(
        {"extensions": ["github.copilot-chat"]}, workspace_folder=_WS, extension=None,
    )
    assert merged["extensions"] == ["github.copilot-chat"]

    merged_empty = merge_attached_container_config(
        {}, workspace_folder=_WS, extension=None,
    )
    assert "extensions" not in merged_empty
    assert merged_empty == {"workspaceFolder": _WS}


def test_merge_does_not_mutate_input():
    existing = {"extensions": ["a"]}
    merge_attached_container_config(existing, workspace_folder=_WS, extension=_EXT)
    assert existing == {"extensions": ["a"]}


# --- seed_attached_container_config (I/O) -----------------------------------

def test_seed_writes_when_absent(tmp_path):
    """A new file is written; content parses to the merged dict; returns True."""
    path = tmp_path / "imageConfigs" / "img.json"
    wrote = seed_attached_container_config(
        path, workspace_folder=_WS, extension=_EXT,
    )
    assert wrote is True
    assert json.loads(path.read_text()) == {
        "extensions": [_EXT],
        "workspaceFolder": _WS,
    }


def test_seed_union_merges_into_existing(tmp_path):
    """Adds our ext, keeps a pre-existing ext AND a pre-existing workspaceFolder."""
    path = tmp_path / "img.json"
    path.write_text(json.dumps({
        "extensions": ["github.copilot-chat"],
        "workspaceFolder": "/somewhere/else",
    }))
    wrote = seed_attached_container_config(
        path, workspace_folder=_WS, extension=_EXT,
    )
    assert wrote is True
    written = json.loads(path.read_text())
    assert written["extensions"] == ["github.copilot-chat", _EXT]
    assert written["workspaceFolder"] == "/somewhere/else"


def test_seed_is_idempotent(tmp_path):
    """A second identical seed makes no change and returns False."""
    path = tmp_path / "img.json"
    first = seed_attached_container_config(path, workspace_folder=_WS, extension=_EXT)
    assert first is True
    before = path.read_text()
    second = seed_attached_container_config(path, workspace_folder=_WS, extension=_EXT)
    assert second is False
    assert path.read_text() == before


def test_seed_tolerates_corrupt_existing(tmp_path):
    """A non-JSON existing file is treated as {} and overwritten, never raises."""
    path = tmp_path / "img.json"
    path.write_text("this is not json {{{")
    wrote = seed_attached_container_config(
        path, workspace_folder=_WS, extension=_EXT,
    )
    assert wrote is True
    assert json.loads(path.read_text()) == {
        "extensions": [_EXT],
        "workspaceFolder": _WS,
    }


# --- merge_session_start_hook (pure, increment 2b) -------------------------

def _managed_group() -> dict:
    return {
        "matcher": _SESSION_START_MATCHER,
        "hooks": [{"type": "command", "command": _SESSION_START_COMMAND}],
    }


def _marker_write_group() -> dict:
    """The per-PID marker-WRITE managed group (its own SessionStart group)."""
    return {
        "matcher": _SESSION_START_MATCHER,
        "hooks": [{"type": "command", "command": _AGENT_MARKER_WRITE_COMMAND}],
    }


def _marker_remove_group() -> dict:
    """The per-PID marker-REMOVE managed group (the SessionEnd group)."""
    return {
        "matcher": _SESSION_END_MATCHER,
        "hooks": [{"type": "command", "command": _AGENT_MARKER_REMOVE_COMMAND}],
    }


def _full_managed_hooks() -> dict:
    """The full claude managed hook set seed_session_start_hook writes: the
    directive + marker-write SessionStart groups and the marker-remove SessionEnd
    group."""
    return {
        "SessionStart": [_managed_group(), _marker_write_group()],
        "SessionEnd": [_marker_remove_group()],
    }


def test_merge_session_start_into_empty_creates_hooks_block():
    merged = merge_session_start_hook({})
    assert merged == {"hooks": {"SessionStart": [_managed_group()]}}


def test_merge_session_start_command_is_additional_context_flattener():
    """The managed command runs the flattener in --additional-context mode,
    silent-safe, referencing the SEED env var.

    ⚑ The flattener path is the SAME literal ``start._directive_flatten_shim``
    carries — the package-bind path, not a canon path (C-CANON R1, P-2). The two
    must move together: a one-sided change is SILENT, because ``|| true`` swallows
    the failure and the box just loses its directives.
    """
    assert _SESSION_START_COMMAND == (
        'python3 "/opt/kanibako/kanibako/scripts/import-directives.py" '
        '--additional-context "$KANIBAKO_DIRECTIVE_SEED" || true'
    )
    assert _SESSION_START_MATCHER == "startup|resume|clear|compact"


def test_merge_session_start_is_idempotent():
    """A second merge does NOT duplicate the managed group."""
    once = merge_session_start_hook({})
    twice = merge_session_start_hook(once)
    assert twice == once
    assert len(twice["hooks"]["SessionStart"]) == 1


def test_merge_session_start_preserves_existing_sessionstart_groups():
    """A user's own SessionStart group is preserved; ours is APPENDED."""
    user_group = {
        "matcher": "startup",
        "hooks": [{"type": "command", "command": "echo hi"}],
    }
    merged = merge_session_start_hook({"hooks": {"SessionStart": [user_group]}})
    groups = merged["hooks"]["SessionStart"]
    assert groups[0] == user_group
    assert _managed_group() in groups
    assert len(groups) == 2


def test_merge_session_start_preserves_other_hook_events_and_top_keys():
    """Sibling hook events (PreToolUse) and other top-level keys are untouched."""
    existing = {
        "$schema": "x",
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": []}]},
    }
    merged = merge_session_start_hook(existing)
    assert merged["$schema"] == "x"
    assert merged["hooks"]["PreToolUse"] == [{"matcher": "*", "hooks": []}]
    assert merged["hooks"]["SessionStart"] == [_managed_group()]


def test_merge_session_start_does_not_mutate_input():
    src = {"hooks": {"SessionStart": []}}
    merge_session_start_hook(src)
    assert src == {"hooks": {"SessionStart": []}}


def test_merge_session_start_idempotent_even_with_different_matcher():
    """Idempotence keys on the COMMAND, not the matcher: a pre-existing group
    carrying our exact command (any matcher) suppresses a duplicate append."""
    pre = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {"type": "command", "command": _SESSION_START_COMMAND},
                    ],
                }
            ]
        }
    }
    merged = merge_session_start_hook(pre)
    assert merged == pre


def test_merge_session_start_tolerates_non_dict_hooks():
    """A corrupt ``hooks`` (non-dict) is replaced, not crashed on."""
    merged = merge_session_start_hook({"hooks": "garbage"})
    assert merged["hooks"]["SessionStart"] == [_managed_group()]


# --- seed_session_start_hook (I/O; claude settings.json JSON hooks) ---------
# codex no longer uses this JSON path — it has its own config.toml manager below.

def test_seed_session_start_writes_and_is_idempotent(tmp_path):
    """First seed writes the FULL managed set (directive + pidfile write/remove);
    a re-run is a no-op (claude settings.json)."""
    path = tmp_path / "settings.json"
    assert seed_session_start_hook(path, root=tmp_path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": _full_managed_hooks()}
    # Idempotent: second call writes nothing.
    assert seed_session_start_hook(path, root=tmp_path) is False


def test_seed_session_start_merges_into_existing_user_hooks(tmp_path):
    """A user's pre-existing settings.json is union-merged, never clobbered."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": []}]},
    }))
    assert seed_session_start_hook(path, root=tmp_path) is True
    data = json.loads(path.read_text())
    assert data["hooks"]["PreToolUse"] == [{"matcher": "*", "hooks": []}]
    assert data["hooks"]["SessionStart"] == [
        _managed_group(), _marker_write_group(),
    ]
    assert data["hooks"]["SessionEnd"] == [_marker_remove_group()]


def test_seed_session_start_tolerates_corrupt(tmp_path):
    """A corrupt file degrades to {} then merges (never raises)."""
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert seed_session_start_hook(path, root=tmp_path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": _full_managed_hooks()}


# --- per-PID markers: write (SessionStart) + remove (SessionEnd) -----------
#
# The hook commands CALL the packaged charter's PID helpers rather than inlining the
# shell they used to.  That moves two things out of this module and into shipped
# script bytes — the marker-dir fallback and the per-PID filename scheme — so the
# tests below reach into the shipped scripts to pin both ends of each.

#: The PID helpers' directory, relative to the packaged rom ROOT.  ⚑ The BOX path is
#: derived from it by ``_canon_dest`` (the same function the bind emitter uses), never
#: spelled a second time.
_PID_SCRIPTS_ROM_REL = "charter/general/scripts/util"

#: ``${KANIBAKO_AGENT_MARKERS_DIR:-<default>}`` as the shell scripts spell it.
_MARKERS_DIR_FALLBACK_RE = re.compile(r"\$\{KANIBAKO_AGENT_MARKERS_DIR:-([^}]*)\}")


def _shipped_pid_script(leaf: str) -> Path:
    """The SHIPPED source of one charter PID helper — the bytes the rom bind exposes."""
    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    return rom_root / _PID_SCRIPTS_ROM_REL / leaf


def test_marker_commands_call_the_shipped_charter_pid_scripts():
    """The hooks are CALLS into the charter's PID helpers, at the box path the rom bind
    puts them at — derived from ``_canon_dest``, so a relocation of the canon dest
    reds here instead of leaving a hook pointed at nothing."""
    box_dir = _canon_dest(_PID_SCRIPTS_ROM_REL)
    assert _AGENT_MARKER_WRITE_COMMAND == f'{box_dir}/pid-add.sh "$PPID" || true'
    assert _AGENT_MARKER_REMOVE_COMMAND == f'{box_dir}/pid-rm.sh "$PPID" || true'
    # A dest is only real if the SOURCE ships: the rom bind exposes the host file and
    # its mode, so a missing or non-executable script is a hook that does nothing.
    for leaf in ("pid-add.sh", "pid-rm.sh"):
        script = _shipped_pid_script(leaf)
        assert script.is_file(), f"{leaf} is not shipped under the rom root"
        assert os.access(script, os.X_OK), f"{leaf} ships without its executable bit"


def test_pid_scripts_fallback_dir_is_the_python_constant():
    """CROSS-LANGUAGE P10 pin.  The marker dir's fallback now has two spellings —
    :data:`AGENT_MARKERS_DIR` here (read side, and the env ``start.py`` seeds) and a
    ``:-`` default inside each shell script (write side).  A script cannot import a
    Python constant, so drift cannot be made impossible; it is made LOUD instead."""
    for leaf in ("pid-add.sh", "pid-rm.sh"):
        text = _shipped_pid_script(leaf).read_text()
        defaults = _MARKERS_DIR_FALLBACK_RE.findall(text)
        # ⚑ REDS ON ITS OWN EMPTINESS: a script that stopped spelling the fallback at
        # all would otherwise satisfy the set comparison vacuously.
        assert defaults, f"{leaf} no longer carries a KANIBAKO_AGENT_MARKERS_DIR default"
        assert set(defaults) == {AGENT_MARKERS_DIR}


def test_pid_scripts_honor_the_pid_argument_and_name_the_marker_for_it():
    """The two halves that make ``pid-add.sh "$PPID"`` mean what the hook intends:
    the script must PREFER its argument over its own ``$PPID`` (which by then is the
    hook's transient shell), and it must name the marker FOR that pid, so a CLI
    incumbent and a panel newcomer each hold their own file rather than racing for a
    single last-writer-wins path."""
    for leaf in ("pid-add.sh", "pid-rm.sh"):
        text = _shipped_pid_script(leaf).read_text()
        assert 'AGENT_PID="${1:-$PPID}"' in text, f"{leaf} ignores its pid argument"
        assert re.search(
            r"KANIBAKO_AGENT_MARKERS_DIR[^\n]*/\$AGENT_PID", text,
        ), f"{leaf} does not name the marker file for the pid"


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell available")
def test_marker_hook_commands_really_write_and_remove_a_marker(tmp_path):
    """MUTATION PROOF, not a byte comparison.  Run the two hook command strings through
    a shell the way claude and codex do, against a HOME holding the shipped scripts, and
    watch the marker appear and go.  A golden fixture cannot see a hook that silently
    does nothing — which is the whole failure mode of moving shell into a script.

    ⚑ THE DISCRIMINATING ASSERTION is the marker's NAME.  The command is compound, so
    the shell forks and the script's own ``$PPID`` is that shell — a different pid from
    this test process.  The marker is named for THIS process only if the script honors
    the argument the hook passes it.
    """
    home = tmp_path / "home"
    script_dir = home / "canon" / _PID_SCRIPTS_ROM_REL
    script_dir.mkdir(parents=True)
    for leaf in ("pid-add.sh", "pid-rm.sh"):
        shutil.copy2(_shipped_pid_script(leaf), script_dir / leaf)

    markers = tmp_path / "markers"
    pidfile = tmp_path / "run" / "agent.pid"
    env = {
        **os.environ,
        "HOME": str(home),
        "KANIBAKO_AGENT_MARKERS_DIR": str(markers),
        "KANIBAKO_AGENT_PIDFILE": str(pidfile),
    }

    def _run(command: str) -> None:
        done = subprocess.run(
            ["sh", "-c", command], env=env, capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stderr

    _run(_AGENT_MARKER_WRITE_COMMAND)
    assert [p.name for p in markers.iterdir()] == [str(os.getpid())]
    assert (markers / str(os.getpid())).read_text() == str(os.getpid())
    # ⚑ WIDENED BY THE MOVE, and asserted so it stays deliberate: the inline command
    # wrote only the marker; ``pid-add.sh`` also writes the shared pidfile.
    assert pidfile.read_text() == str(os.getpid())

    _run(_AGENT_MARKER_REMOVE_COMMAND)
    assert list(markers.iterdir()) == []
    # The pidfile still named us, so the remove was ours to make.
    assert not pidfile.exists()


@pytest.mark.skipif(shutil.which("sh") is None, reason="no POSIX shell available")
def test_marker_remove_leaves_a_pidfile_another_agent_owns(tmp_path):
    """The remove hook must not clear a pidfile that has since been overwritten by a
    second agent in the same box — the marker is per-PID, the pidfile is shared."""
    home = tmp_path / "home"
    script_dir = home / "canon" / _PID_SCRIPTS_ROM_REL
    script_dir.mkdir(parents=True)
    shutil.copy2(_shipped_pid_script("pid-rm.sh"), script_dir / "pid-rm.sh")

    markers = tmp_path / "markers"
    markers.mkdir()
    (markers / str(os.getpid())).write_text(str(os.getpid()))
    pidfile = tmp_path / "agent.pid"
    other = str(os.getpid() + 1)  # any pid that is not ours
    pidfile.write_text(other)

    done = subprocess.run(
        ["sh", "-c", _AGENT_MARKER_REMOVE_COMMAND],
        env={
            **os.environ,
            "HOME": str(home),
            "KANIBAKO_AGENT_MARKERS_DIR": str(markers),
            "KANIBAKO_AGENT_PIDFILE": str(pidfile),
        },
        capture_output=True, text=True,
    )
    assert done.returncode == 0, done.stderr
    assert list(markers.iterdir()) == []
    assert pidfile.read_text() == other


def test_markers_dir_agrees_with_supervisor_agent_markers_dir():
    """The two ends of the detection contract read ONE constant: the write side
    (vscode_config.AGENT_MARKERS_DIR) IS the value start.py passes to the supervisor's
    --agent-markers-dir / seeds as KANIBAKO_AGENT_MARKERS_DIR (read side)."""
    from kanibako.commands.start import AGENT_MARKERS_DIR as START_MARKERS_DIR

    assert START_MARKERS_DIR is AGENT_MARKERS_DIR


def test_merge_pidfile_write_into_empty_creates_own_sessionstart_group():
    merged = merge_marker_write_hook({})
    assert merged == {"hooks": {"SessionStart": [_marker_write_group()]}}


def test_merge_session_end_into_empty_creates_sessionend_group():
    merged = merge_marker_remove_hook({})
    assert merged == {"hooks": {"SessionEnd": [_marker_remove_group()]}}


def test_all_three_managed_commands_coexist():
    """Directive + pidfile-write (both SessionStart) + pidfile-remove (SessionEnd)
    all coexist when merged together — no group swallows another."""
    merged = merge_session_start_hook({})
    merged = merge_marker_write_hook(merged)
    merged = merge_marker_remove_hook(merged)
    assert merged["hooks"]["SessionStart"] == [
        _managed_group(), _marker_write_group(),
    ]
    assert merged["hooks"]["SessionEnd"] == [_marker_remove_group()]


def test_pidfile_merges_are_idempotent():
    """Merging all three twice does NOT duplicate any command."""
    once = merge_marker_remove_hook(
        merge_marker_write_hook(merge_session_start_hook({}))
    )
    twice = merge_marker_remove_hook(
        merge_marker_write_hook(merge_session_start_hook(once))
    )
    assert twice == once
    assert len(twice["hooks"]["SessionStart"]) == 2
    assert len(twice["hooks"]["SessionEnd"]) == 1


def test_pidfile_write_idempotent_keys_on_command_not_matcher():
    """A pre-existing group carrying our exact write command (any matcher)
    suppresses a duplicate append."""
    pre = {
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup",
                    "hooks": [
                        {"type": "command", "command": _AGENT_MARKER_WRITE_COMMAND},
                    ],
                }
            ]
        }
    }
    assert merge_marker_write_hook(pre) == pre


def test_pidfile_merges_preserve_user_hooks_on_every_event():
    """A user's own SessionStart, SessionEnd, and unrelated-event hooks survive."""
    user_ss = {"matcher": "startup", "hooks": [{"type": "command", "command": "u1"}]}
    user_se = {"matcher": "clear", "hooks": [{"type": "command", "command": "u2"}]}
    existing = {
        "$schema": "x",
        "hooks": {
            "SessionStart": [user_ss],
            "SessionEnd": [user_se],
            "PreToolUse": [{"matcher": "*", "hooks": []}],
        },
    }
    merged = merge_marker_remove_hook(
        merge_marker_write_hook(merge_session_start_hook(existing))
    )
    assert merged["$schema"] == "x"
    assert merged["hooks"]["PreToolUse"] == [{"matcher": "*", "hooks": []}]
    # User groups kept, ours appended after them.
    assert merged["hooks"]["SessionStart"] == [
        user_ss, _managed_group(), _marker_write_group(),
    ]
    assert merged["hooks"]["SessionEnd"] == [user_se, _marker_remove_group()]


def test_pidfile_merges_do_not_mutate_input():
    src = {"hooks": {"SessionStart": [], "SessionEnd": []}}
    merge_marker_write_hook(src)
    merge_marker_remove_hook(src)
    assert src == {"hooks": {"SessionStart": [], "SessionEnd": []}}


def test_seed_session_start_preserves_user_sessionend_and_is_idempotent(tmp_path):
    """seed writes all three managed hooks into a file with a pre-existing user
    SessionEnd hook, preserving it, and is idempotent."""
    path = tmp_path / "settings.json"
    user_se = {"matcher": "logout", "hooks": [{"type": "command", "command": "bye"}]}
    path.write_text(json.dumps({"hooks": {"SessionEnd": [user_se]}}))
    assert seed_session_start_hook(path, root=tmp_path) is True
    data = json.loads(path.read_text())
    assert data["hooks"]["SessionStart"] == [
        _managed_group(), _marker_write_group(),
    ]
    assert data["hooks"]["SessionEnd"] == [user_se, _marker_remove_group()]
    assert seed_session_start_hook(path, root=tmp_path) is False


# --- retired predecessors: a re-seed REPLACES a job's own earlier command -----
#
# A managed group is keyed on its command TEXT, so a command that CHANGED left its
# old group behind for good: the re-seed added the current command and kept the
# previous one, and both ran.  Each job declares the exact strings it shipped
# before, and its merge drops those in place of adding the current one.

def _command(groups: list, command: str) -> bool:
    """``True`` iff *command* appears as a hook entry anywhere in *groups*."""
    return any(
        isinstance(h, dict) and h.get("command") == command
        for g in groups if isinstance(g, dict) and isinstance(g.get("hooks"), list)
        for h in g["hooks"]
    )


def _groups(settings: dict, event: str) -> list:
    hooks = settings.get("hooks", {})
    current = hooks.get(event, []) if isinstance(hooks, dict) else []
    return list(current) if isinstance(current, list) else []


def _job(merge, event: str, command: str, retired: "frozenset[str]"):
    """One job's merge bound to its current command and its retired set."""
    return merge, event, command, retired


_DIRECTIVE_JOB = _job(
    merge_session_start_hook, "SessionStart",
    _SESSION_START_COMMAND, _SESSION_START_RETIRED_COMMANDS,
)
_MARKER_WRITE_JOB = _job(
    merge_marker_write_hook, "SessionStart",
    _AGENT_MARKER_WRITE_COMMAND, _AGENT_MARKER_WRITE_RETIRED_COMMANDS,
)
_MARKER_REMOVE_JOB = _job(
    merge_marker_remove_hook, "SessionEnd",
    _AGENT_MARKER_REMOVE_COMMAND, _AGENT_MARKER_REMOVE_RETIRED_COMMANDS,
)


@pytest.mark.parametrize(
    "merge,event,command,retired",
    [_DIRECTIVE_JOB, _MARKER_WRITE_JOB, _MARKER_REMOVE_JOB],
    ids=["directive", "marker-write", "marker-remove"],
)
def test_every_retired_command_of_a_job_is_replaced(merge, event, command, retired):
    """Each retired spelling of THIS job is gone and the current one is present,
    whichever retired spelling the box happens to still carry."""
    assert retired, "a job that shipped a command before must declare it retired"
    for stale in retired:
        pre = {"hooks": {event: [
            {"matcher": "startup",
             "hooks": [{"type": "command", "command": stale}]},
        ]}}
        groups = _groups(merge(pre), event)
        assert not _command(groups, stale), f"{stale!r} was stranded"
        assert _command(groups, command), f"{command!r} was not delivered"


@pytest.mark.parametrize(
    "merge,event,command,retired",
    [_DIRECTIVE_JOB, _MARKER_WRITE_JOB, _MARKER_REMOVE_JOB],
    ids=["directive", "marker-write", "marker-remove"],
)
def test_a_user_hook_and_an_edited_variant_survive_a_retirement(
    merge, event, command, retired,
):
    """Only the EXACT retired strings go: a user's own hook and a hand-edited
    variant of ours (here a superseded dir, there an extra argument) both stay."""
    user = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo mine"}]}
    edited = {"hooks": [{"type": "command", "command": f"{command} --tag mine"}]}
    pre = {"hooks": {event: [
        user,
        *({"hooks": [{"type": "command", "command": c}]} for c in sorted(retired)),
        edited,
    ]}}
    groups = _groups(merge(pre), event)
    assert groups[0] == user
    assert edited in groups
    assert not any(_command(groups, c) for c in retired)
    assert _command(groups, command)


def test_a_group_the_retirement_emptied_is_dropped():
    """A group carried SOLELY by a retired command goes with it; one that still
    holds a sibling entry keeps its ``matcher`` and loses only the retired entry."""
    merge, event, command, retired = _MARKER_WRITE_JOB
    stale = sorted(retired)[0]
    keep = {"matcher": "resume", "hooks": [{"type": "command", "command": "echo kept"}]}
    pre = {"hooks": {event: [
        {"matcher": "startup", "hooks": [{"type": "command", "command": stale}]},
        keep,
    ]}}
    groups = _groups(merge(pre), event)
    assert groups == [keep, _marker_write_group()]


def test_a_retirement_never_reaches_another_job_or_another_event():
    """Removal is scoped to the job's OWN event: the marker-remove set is not
    consulted for ``SessionStart``, and the two ``SessionStart`` jobs do not
    consume each other's retired commands (their matchers overlap, the
    commands do not)."""
    _, write_event, _, write_retired = _MARKER_WRITE_JOB
    remove_stale = sorted(_AGENT_MARKER_REMOVE_RETIRED_COMMANDS)[0]
    pre = {"hooks": {write_event: [
        {"hooks": [{"type": "command", "command": remove_stale}]},
    ]}}
    groups = _groups(merge_marker_write_hook(pre), write_event)
    assert _command(groups, remove_stale), "a sibling job's command was consumed"
    assert not set(write_retired) & set(_AGENT_MARKER_REMOVE_RETIRED_COMMANDS)


def test_reseed_replaces_stale_predecessors_and_then_writes_nothing(tmp_path):
    """End to end through the seeder: a box carrying a retired marker command per
    event, a user hook and an edited variant keeps the last two, loses the first
    two, and the re-seed that follows writes nothing at all."""
    path = tmp_path / "settings.json"
    stale_write = sorted(_AGENT_MARKER_WRITE_RETIRED_COMMANDS)[0]
    stale_remove = sorted(_AGENT_MARKER_REMOVE_RETIRED_COMMANDS)[0]
    user = {"matcher": "startup", "hooks": [{"type": "command", "command": "echo mine"}]}
    edited = {"hooks": [{"type": "command", "command":
                         f'{_AGENT_MARKER_WRITE_COMMAND} --tag mine'}]}
    path.write_text(json.dumps({"hooks": {
        "SessionStart": [
            user,
            {"matcher": _SESSION_START_MATCHER,
             "hooks": [{"type": "command", "command": stale_write}]},
            edited,
        ],
        "SessionEnd": [
            {"matcher": _SESSION_END_MATCHER,
             "hooks": [{"type": "command", "command": stale_remove}]},
        ],
    }}))
    assert seed_session_start_hook(path, root=tmp_path) is True
    data = json.loads(path.read_text())
    ss, se = data["hooks"]["SessionStart"], data["hooks"]["SessionEnd"]
    assert ss == [user, edited, _managed_group(), _marker_write_group()]
    assert se == [_marker_remove_group()]
    first = path.read_bytes()
    assert seed_session_start_hook(path, root=tmp_path) is False
    assert path.read_bytes() == first, "a re-seed rewrote a byte"


# --- codex_trusted_hash (pure; oracle-pinned) ------------------------------

def test_codex_trusted_hash_oracle():
    """Pinned to a REAL codex oracle vector (reproduced against codex-cli
    0.141.0): event ``session_start``, matcher ``startup``, a concrete command,
    default 600 s timeout → this exact digest."""
    got = codex_trusted_hash(
        "session_start",
        "startup",
        "/home/agent/.codex/kanibako-hooktest/fire.sh",
        600,
    )
    assert got == (
        "sha256:e7b8c5ff818bd5c7631530cf143edcac"
        "97161a1a76f62eae5f5325fbb1aa6b5d"
    )


def test_codex_trusted_hash_omits_matcher_key_when_none():
    """A ``None`` matcher OMITS the key from the identity entirely (≠ empty
    string), so the two hash to DIFFERENT digests."""
    none_h = codex_trusted_hash("session_start", None, "/x/fire.sh")
    empty_h = codex_trusted_hash("session_start", "", "/x/fire.sh")
    assert none_h.startswith("sha256:")
    assert none_h != empty_h


def test_codex_trusted_hash_default_timeout_600():
    """The default timeout is 600 s (codex normalizes unset → 600)."""
    assert (
        codex_trusted_hash("session_start", "startup", "/x/fire.sh")
        == codex_trusted_hash("session_start", "startup", "/x/fire.sh", 600)
    )


# --- merge_codex_config (pure; text→text) ----------------------------------

_BOX_CFG = "/home/agent/.codex/config.toml"
_CODEX_CWD = "/home/agent/workspace"
_TEMPLATE = (
    "# codex configuration (kanibako-curated, minimal).\n#\n"
    "# Add overrides below; safe to edit.\n"
)


def _merge(text):
    return merge_codex_config(
        text, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )


def test_codex_merge_produces_valid_toml_with_managed_hook_group():
    out = _merge(_TEMPLATE)
    data = tomllib.loads(out)  # parses
    group = data["hooks"]["SessionStart"][0]
    assert group["matcher"] == _SESSION_START_MATCHER
    assert group["hooks"][0] == {
        "type": "command", "command": _SESSION_START_COMMAND, "timeout": 600,
    }


def test_codex_merge_trusted_hash_matches_helper_and_state_key():
    out = _merge(_TEMPLATE)
    data = tomllib.loads(out)
    state = data["hooks"]["state"]
    key = f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:0:0"
    assert key in state
    assert state[key]["trusted_hash"] == codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _SESSION_START_COMMAND,
    )


def test_codex_merge_writes_project_trust():
    data = tomllib.loads(_merge(_TEMPLATE))
    assert data["projects"][_CODEX_CWD]["trust_level"] == "trusted"


def test_codex_merge_command_roundtrips_through_toml_unexpanded():
    """The command must survive TOML encode/decode as the RAW pre-${ENV} string
    (the trust hash is computed on the raw command), quotes and all."""
    data = tomllib.loads(_merge(_TEMPLATE))
    cmd = data["hooks"]["SessionStart"][0]["hooks"][0]["command"]
    assert cmd == _SESSION_START_COMMAND
    # The remaining ``$VAR`` must survive VERBATIM — it expands in-box at hook-run
    # time, and the trust hash is computed on the raw string.  (The flattener path
    # is now an absolute package-bind path, so ``$HOME`` no longer appears; the
    # unexpanded-env-var property is what this test is about, not that literal.)
    assert '"$KANIBAKO_DIRECTIVE_SEED"' in cmd
    assert '"/opt/kanibako/kanibako/scripts/import-directives.py"' in cmd


def test_codex_merge_never_writes_approval_keys():
    """T1 seam split: the directive merge NEVER writes ``sandbox_mode`` (its one
    writer is ``seed_codex_sandbox_mode``) and nothing writes ``approval_policy``."""
    data = tomllib.loads(_merge(_TEMPLATE))
    assert "approval_policy" not in data
    assert "sandbox_mode" not in data
    # hook + trust are UNCONDITIONAL (orthogonal to yolo).
    assert "SessionStart" in data["hooks"]


def test_codex_merge_passes_existing_approval_keys_through(tmp_path):
    """Keys already in the file (whoever wrote them — the sandbox seeder or
    the user) pass through the directive merge byte-untouched."""
    text = 'approval_policy = "never"\nsandbox_mode = "read-only"\n'
    data = tomllib.loads(_merge(text))
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "read-only"


def test_codex_merge_preserves_unrelated_user_keys_and_comments():
    # A genuinely UNRELATED root key (``model_reasoning_effort`` — NOT the
    # kanibako-owned ``model``/``model_provider``) is preserved through the bare
    # (provider=None) merge, along with the comment and the user's table.  (The
    # kanibako-owned root keys are covered by the WIPE tests below.)
    text = (
        "# my header comment\n"
        'model_reasoning_effort = "high"\n\n'
        "[mcp_servers.foo]\n"
        'command = "serve"\n'
    )
    out = _merge(text)
    assert "# my header comment" in out  # comment preserved
    data = tomllib.loads(out)
    assert data["model_reasoning_effort"] == "high"
    assert data["mcp_servers"]["foo"]["command"] == "serve"


def test_codex_merge_is_idempotent():
    once = _merge(_TEMPLATE)
    twice = _merge(once)
    assert twice == once
    # exactly the FIVE managed groups (directive + four charter starts) after a re-merge.
    assert once.count("[[hooks.SessionStart]]") == 5


def test_codex_merge_tolerates_corrupt_text():
    """A non-TOML/corrupt body never raises; it is preserved and the managed
    region is still appended (produces valid managed tables)."""
    out = _merge("this is {not valid toml\n")
    assert "this is {not valid toml" in out
    # the command is TOML-escaped in the file, but its stable substring is there.
    assert "import-directives.py" in out
    assert out.count("[[hooks.SessionStart]]") == 5


def test_codex_merge_counts_preceding_user_sessionstart_group():
    """When a user already has a SessionStart group, OUR group index (in the
    trust state key) accounts for it."""
    text = (
        "[[hooks.SessionStart]]\n"
        'matcher = "startup"\n\n'
        "[[hooks.SessionStart.hooks]]\n"
        'type = "command"\n'
        'command = "echo hi"\n'
    )
    out = _merge(text)
    data = tomllib.loads(out)
    # user group at 0, ours at 1 (directive) through 5 → EVERY state key shifts
    # with the user count.
    for index in range(1, 6):
        assert f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:{index}:0" in data["hooks"]["state"]
    assert f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:0:0" not in data["hooks"]["state"]
    groups = data["hooks"]["SessionStart"]
    assert len(groups) == 6
    assert groups[0]["hooks"][0]["command"] == "echo hi"
    assert groups[1]["hooks"][0]["command"] == _SESSION_START_COMMAND


# --- seed_codex_config (I/O; the directive-hook write) ----------------------

def test_seed_codex_config_writes_and_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is True
    tomllib.loads(path.read_text())  # valid
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is False


def test_seed_codex_config_absent_file_is_created(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is True
    assert path.exists()


# --- Codex personas INC 1: merge_codex_model_provider (pure; text→text) ------

_NAVIGATOR = dict(
    provider_id="navigator",
    name="NaviGator",
    base_url="https://api.ai.it.ufl.edu/v1",
    wire_api="chat",
    env_key="NAVIGATOR_API_KEY",
    model="gemma-4-31b-it",
)


def _provider(text, **overrides):
    kwargs = {**_NAVIGATOR, **overrides}
    return merge_codex_model_provider(text, **kwargs)


def test_provider_fresh_file_emits_valid_toml_block():
    """A fresh (comment-only) file gains the provider table + top-level keys and
    parses as valid TOML with exactly the requested values."""
    out = _provider(_TEMPLATE)
    data = tomllib.loads(out)  # parses → valid TOML
    assert data["model"] == "gemma-4-31b-it"
    assert data["model_provider"] == "navigator"
    prov = data["model_providers"]["navigator"]
    assert prov == {
        "name": "NaviGator",
        "base_url": "https://api.ai.it.ufl.edu/v1",
        "wire_api": "chat",
        "env_key": "NAVIGATOR_API_KEY",
    }


def test_provider_top_level_keys_precede_the_table():
    """TOML validity guard: the top-level model/model_provider scalars are emitted
    BEFORE the first table header (a bare key after a table would bind to it)."""
    out = _provider(_TEMPLATE)
    model_pos = out.index("\nmodel = ")
    provider_pos = out.index("model_provider = ")
    table_pos = out.index("[model_providers.")
    assert model_pos < table_pos
    assert provider_pos < table_pos
    tomllib.loads(out)  # and it parses


def test_provider_preserves_unrelated_user_keys_tables_and_comments():
    """User comments, unrelated top-level keys, and unrelated tables (including a
    DIFFERENT model_providers.<id>) are preserved byte-for-byte in the body."""
    text = (
        "# my header comment\n"
        'approval_policy = "never"\n\n'
        "[mcp_servers.foo]\n"
        'command = "serve"\n\n'
        "[model_providers.other]\n"
        'name = "Other"\n'
        'base_url = "https://other.example/v1"\n'
    )
    out = _provider(text)
    assert "# my header comment" in out
    data = tomllib.loads(out)
    assert data["approval_policy"] == "never"
    assert data["mcp_servers"]["foo"]["command"] == "serve"
    # the user's OWN provider is untouched...
    assert data["model_providers"]["other"]["name"] == "Other"
    assert data["model_providers"]["other"]["base_url"] == "https://other.example/v1"
    # ...and ours is added alongside it.
    assert data["model_providers"]["navigator"]["name"] == "NaviGator"
    assert data["model_provider"] == "navigator"


def test_provider_is_idempotent():
    """Applying the generator twice reproduces its own output exactly, with a
    single managed region + a single provider table."""
    once = _provider(_TEMPLATE)
    twice = _provider(once)
    assert twice == once
    assert once.count("[model_providers.") == 1
    assert once.count("model_provider = ") == 1
    assert once.count("model = ") == 1


def test_provider_update_model_changes_only_that_value():
    """Re-merging with a new model updates ONLY the model scalar; the provider id,
    table, and all user content are unchanged."""
    first = _provider(_TEMPLATE)
    updated = _provider(first, model="gemma-4-70b-it")
    data = tomllib.loads(updated)
    assert data["model"] == "gemma-4-70b-it"
    # everything else identical to the original merge.
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"] == tomllib.loads(first)[
        "model_providers"
    ]["navigator"]
    # exactly one model key (updated in place, not appended).
    assert updated.count("model = ") == 1


def test_provider_update_base_url_changes_only_the_table_value():
    """Changing base_url updates only the table's base_url, preserving the rest."""
    first = _provider(_TEMPLATE)
    updated = _provider(first, base_url="https://api2.ai.it.ufl.edu/v1")
    data = tomllib.loads(updated)
    prov = data["model_providers"]["navigator"]
    assert prov["base_url"] == "https://api2.ai.it.ufl.edu/v1"
    assert prov["name"] == "NaviGator"
    assert prov["wire_api"] == "chat"
    assert prov["env_key"] == "NAVIGATOR_API_KEY"
    assert updated.count("[model_providers.") == 1


def test_provider_updates_preexisting_user_top_level_model_in_place():
    """A user's own top-level ``model`` is SET to ours in place (not duplicated),
    keeping the file valid TOML with a single model key."""
    text = 'model = "gpt-5-codex"\n'
    out = _provider(text)
    data = tomllib.loads(out)
    assert data["model"] == "gemma-4-31b-it"
    assert out.count("model = ") == 1


def test_provider_wire_api_responses_variant():
    """wire_api is parameterized (chat vs responses settle at INC 3/4)."""
    out = _provider(_TEMPLATE, wire_api="responses")
    data = tomllib.loads(out)
    assert data["model_providers"]["navigator"]["wire_api"] == "responses"


def test_provider_empty_input_produces_valid_toml():
    """An empty existing config still yields a valid, parseable provider block."""
    out = _provider("")
    data = tomllib.loads(out)
    assert data["model"] == "gemma-4-31b-it"
    assert data["model_providers"]["navigator"]["name"] == "NaviGator"


def test_provider_namedtuple_fields_map_onto_toml():
    """CodexModelProvider bundles the six values the merge threads through."""
    mp = CodexModelProvider(**_NAVIGATOR)
    assert mp.provider_id == "navigator"
    assert mp.model == "gemma-4-31b-it"
    assert mp.env_key == "NAVIGATOR_API_KEY"


# --- INC 1 seam: merge_codex_config threads the optional provider -----------

def test_codex_merge_no_provider_is_byte_identical():
    """DEFAULT (model_provider=None) is BYTE-IDENTICAL to the pre-provider merge:
    no provider region, no model/model_provider keys."""
    default = merge_codex_config(
        _TEMPLATE, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    explicit_none = merge_codex_config(
        _TEMPLATE, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=None,
    )
    assert default == explicit_none
    assert "model_providers" not in default
    assert "model_provider = " not in default


def test_codex_merge_with_provider_composes_both_regions():
    """With a provider, the merged config carries BOTH the hook region and the
    provider region, all valid TOML, hook behavior intact."""
    mp = CodexModelProvider(**_NAVIGATOR)
    out = merge_codex_config(
        _TEMPLATE, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=mp,
    )
    data = tomllib.loads(out)
    # hook region intact
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        _SESSION_START_COMMAND
    )
    assert data["projects"][_CODEX_CWD]["trust_level"] == "trusted"
    # provider region present
    assert data["model"] == "gemma-4-31b-it"
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"]["base_url"] == (
        "https://api.ai.it.ufl.edu/v1"
    )


def test_codex_merge_with_provider_is_idempotent():
    """Re-merging the provider-bearing output reproduces it exactly (both regions
    reconciled, not duplicated)."""
    mp = CodexModelProvider(**_NAVIGATOR)
    kw = dict(
        box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=mp,
    )
    once = merge_codex_config(_TEMPLATE, **kw)
    twice = merge_codex_config(once, **kw)
    assert twice == once
    assert once.count("[model_providers.") == 1
    assert once.count("[[hooks.SessionStart]]") == 5


def test_codex_merge_provider_preserves_user_content():
    """A provider merge preserves the user's unrelated keys/tables/comments."""
    text = (
        "# keep me\n"
        'model = "gpt-5-codex"\n\n'
        "[mcp_servers.foo]\n"
        'command = "serve"\n'
    )
    mp = CodexModelProvider(**_NAVIGATOR)
    out = merge_codex_config(
        text, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=mp,
    )
    assert "# keep me" in out
    data = tomllib.loads(out)
    assert data["mcp_servers"]["foo"]["command"] == "serve"
    # user's model is overridden to ours (in place, single key)
    assert data["model"] == "gemma-4-31b-it"
    assert out.count("model = ") == 1


# --- D1: bare/non-persona reconciled WIPE (OFF direction) -------------------
#
# kanibako OWNS model/model_provider + the managed provider region and
# reconciles them every merge: REPLACE with a persona, WIPE to stock when bare.


def _merge_prov(text, **overrides):
    """merge_codex_config with the navigator provider (REPLACE direction)."""
    mp = CodexModelProvider(**{**_NAVIGATOR, **overrides})
    return merge_codex_config(
        text, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=mp,
    )


def test_codex_merge_bare_wipes_stale_provider_region_and_root_keys():
    """persona → bare: re-merging a provider-bearing config with provider=None
    STRIPS the managed provider region AND removes the model/model_provider root
    keys (the box no longer runs the stale custom provider)."""
    on = _merge_prov(_TEMPLATE)
    assert "# >>> kanibako-managed (model provider)" in on
    off = _merge(on)  # provider=None
    assert "# >>> kanibako-managed (model provider)" not in off
    data = tomllib.loads(off)
    assert "model" not in data
    assert "model_provider" not in data
    assert "model_providers" not in data
    # the instruction-delivery hook region survives (only the provider projection
    # is wiped, not the whole managed surface).
    assert "SessionStart" in data["hooks"]


def test_codex_merge_bare_wipes_hand_edited_root_keys_unconditionally():
    """The wipe is UNCONDITIONAL over the owned keys: a hand-set model /
    model_provider (NOT our managed value) is still removed on the bare merge."""
    text = 'model = "gpt-5-codex"\nmodel_provider = "someone-else"\n'
    off = _merge(text)
    data = tomllib.loads(off)
    assert "model" not in data
    assert "model_provider" not in data


def test_codex_merge_bare_wipes_over_preexisting_stale_managed_region():
    """A file already carrying a stale managed provider region (not from an ON
    merge in this call) is wiped clean by a bare merge."""
    stale = (
        'model = "gemma-4-31b-it"\n'
        'model_provider = "navigator"\n\n'
        "# >>> kanibako-managed (model provider) — do not edit >>>\n"
        '[model_providers."navigator"]\n'
        'name = "NaviGator"\n'
        'base_url = "https://api.ai.it.ufl.edu/v1"\n'
        'wire_api = "chat"\n'
        'env_key = "NAVIGATOR_API_KEY"\n'
        "# <<< kanibako-managed (model provider) <<<\n"
    )
    off = _merge(stale)
    assert "# >>> kanibako-managed (model provider)" not in off
    data = tomllib.loads(off)
    assert "model" not in data
    assert "model_provider" not in data
    assert "model_providers" not in data


def test_codex_merge_bare_preserves_independent_provider_table():
    """CONTRACT INVERSION guard: a user's INDEPENDENT (non-managed)
    ``[model_providers.<other>]`` table, a ``[profiles.x].model`` sub-table key,
    and unrelated root keys are PRESERVED through a bare wipe — only the two
    owned root keys + the delimited managed region are touched."""
    text = (
        "# header\n"
        'model = "gpt-5-codex"\n'
        'model_provider = "navigator"\n'
        'model_reasoning_effort = "high"\n\n'
        "[profiles.fast]\n"
        'model = "gpt-5-codex"\n\n'
        "[model_providers.other]\n"
        'name = "Other"\n'
        'base_url = "https://other.example/v1"\n\n'
        "# >>> kanibako-managed (model provider) — do not edit >>>\n"
        '[model_providers."navigator"]\n'
        'name = "NaviGator"\n'
        'base_url = "https://api.ai.it.ufl.edu/v1"\n'
        'wire_api = "chat"\n'
        'env_key = "NAVIGATOR_API_KEY"\n'
        "# <<< kanibako-managed (model provider) <<<\n"
    )
    off = _merge(text)
    assert "# header" in off
    data = tomllib.loads(off)
    # owned root keys wiped
    assert "model" not in data
    assert "model_provider" not in data
    # everything the box does NOT own is intact
    assert data["model_reasoning_effort"] == "high"
    assert data["profiles"]["fast"]["model"] == "gpt-5-codex"
    assert data["model_providers"]["other"]["name"] == "Other"
    assert data["model_providers"]["other"]["base_url"] == "https://other.example/v1"
    # the MANAGED navigator provider region (and its table) is gone
    assert "navigator" not in data["model_providers"]


def test_codex_merge_persona_to_persona_replaces_no_dup():
    """persona → different persona: the region + root keys are RECONCILED, not
    duplicated — single region, single root key pair, updated values."""
    first = _merge_prov(_TEMPLATE)
    second = _merge_prov(first, provider_id="scholar", model="gemma-4-70b-it")
    data = tomllib.loads(second)
    assert data["model"] == "gemma-4-70b-it"
    assert data["model_provider"] == "scholar"
    assert list(data["model_providers"]) == ["scholar"]
    assert second.count("# >>> kanibako-managed (model provider)") == 1
    assert second.count("model_provider = ") == 1
    assert second.count("model = ") == 1


def test_codex_merge_bare_is_idempotent():
    """The WIPE reproduces its own output: a second bare merge finds nothing left
    to strip/remove (idempotence in the OFF direction)."""
    once = _merge(_merge_prov(_TEMPLATE))
    twice = _merge(once)
    assert twice == once


def test_codex_merge_replace_wipe_replace_round_trips():
    """ON → OFF → ON reproduces the ON bytes exactly (both-direction stability —
    the wipe leaves a clean body the replace rebuilds identically)."""
    on = _merge_prov(_TEMPLATE)
    back = _merge_prov(_merge(on))
    assert back == on


def test_seed_codex_config_bare_wipes_prior_provider(tmp_path):
    """I/O seam: seed a provider-bearing config, then a bare seed WIPES it (writes
    once, then is a no-op)."""
    path = tmp_path / "config.toml"
    mp = CodexModelProvider(**_NAVIGATOR)
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=mp,
    ) is True
    # bare re-seed reconciles the file back to stock
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=None,
    ) is True
    text = path.read_text()
    assert "# >>> kanibako-managed (model provider)" not in text
    data = tomllib.loads(text)
    assert "model" not in data
    assert "model_provider" not in data
    # already reconciled → second bare seed writes nothing
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=None,
    ) is False


def test_seed_codex_config_no_provider_byte_identical(tmp_path):
    """seed_codex_config default write is byte-identical to an explicit
    model_provider=None write (the INC-3 I/O seam is inert by default)."""
    p1 = tmp_path / "a.toml"
    p2 = tmp_path / "b.toml"
    seed_codex_config(
        p1, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    seed_codex_config(
        p2, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=None,
    )
    assert p1.read_text() == p2.read_text()


def test_seed_codex_config_with_provider_writes_region(tmp_path):
    """seed_codex_config threads the provider through to the written file."""
    path = tmp_path / "config.toml"
    mp = CodexModelProvider(**_NAVIGATOR)
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=mp,
    ) is True
    data = tomllib.loads(path.read_text())
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"]["env_key"] == "NAVIGATOR_API_KEY"


# --- T1 seam Step 2: seed_codex_sandbox_mode (standalone invariant writer) ----
#
# The codex panel emitter behind ``Target.deliver_panel_permissions``: the SOLE
# writer of ``sandbox_mode``; the directive write (seed_codex_config) never touches
# it, and nothing writes ``approval_policy`` (the tier rides ``-c`` on the argv).


def test_seed_codex_sandbox_mode_on_absent_creates_the_invariant_only(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    assert tomllib.loads(path.read_text()) == {"sandbox_mode": "danger-full-access"}


def test_seed_codex_sandbox_mode_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    before = path.read_bytes()
    assert seed_codex_sandbox_mode(path, root=tmp_path) is False
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "policy", ["untrusted", "on-request", "never", "on-failure"],
)
def test_seed_codex_sandbox_mode_keeps_any_approval_policy(tmp_path, policy):
    """``approval_policy`` is the user's, whatever its value — including the
    values kanibako used to write.  Only ``sandbox_mode`` is forced."""
    path = tmp_path / "config.toml"
    path.write_text(f'approval_policy = "{policy}"\nsandbox_mode = "read-only"\n')
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    assert path.read_text() == (
        f'approval_policy = "{policy}"\nsandbox_mode = "danger-full-access"\n'
    )


def test_seed_codex_sandbox_mode_migrates_old_workspace_write(tmp_path):
    """MIGRATION: the OLD kanibako-managed ``sandbox_mode = "workspace-write"``
    reconciles to ``danger-full-access``."""
    path = tmp_path / "config.toml"
    path.write_text('sandbox_mode = "workspace-write"\n')
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    assert tomllib.loads(path.read_text())["sandbox_mode"] == "danger-full-access"


def test_seed_codex_sandbox_mode_no_op_never_normalizes_user_bytes(tmp_path):
    """Short-circuit contract: when ``sandbox_mode`` already holds the invariant
    the seeder must NOT rewrite (no trailing-newline normalization)."""
    path = tmp_path / "config.toml"
    odd = (
        '# mine\nsandbox_mode = "danger-full-access"\n'
        'approval_policy = "untrusted"\nfoo = "bar"\n\n\n'
    )
    path.write_text(odd)
    assert seed_codex_sandbox_mode(path, root=tmp_path) is False
    assert path.read_text() == odd


def test_seed_codex_sandbox_mode_key_lands_outside_managed_region(tmp_path):
    """The insert-inside-region hazard: a region-bearing file with NO user
    tables — a naive root-key insert would land after the region's BEGIN marker
    and be swallowed by the next regeneration.  The seeder extracts the region,
    edits the clean body, and carries the region through VERBATIM."""
    path = tmp_path / "config.toml"
    seed_codex_config(path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD)
    region_only = path.read_text()
    assert region_only.lstrip().startswith("# >>> kanibako-managed")
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    out = path.read_text()
    assert out.index("sandbox_mode") < out.index("# >>> kanibako-managed")
    begin = out.index("# >>> kanibako-managed")
    assert out[begin:] == region_only.lstrip("\n").rstrip("\n") + "\n"
    seed_codex_config(path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD)
    assert tomllib.loads(path.read_text())["sandbox_mode"] == "danger-full-access"


def test_seed_codex_sandbox_mode_key_never_lands_inside_provider_region(tmp_path):
    """Both managed regions are extracted before the root-key surgery, so the
    key lands in the TRUE root section — never INSIDE the provider region, and
    a regenerated provider region (persona relaunch) or a wiped one (bare
    relaunch, D1) does not take it along."""
    path = tmp_path / "config.toml"
    seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    out = path.read_text()
    assert out.index("sandbox_mode") < out.index(
        "# >>> kanibako-managed (model provider)"
    )
    seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    assert tomllib.loads(path.read_text())["sandbox_mode"] == "danger-full-access"
    seed_codex_config(path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD)
    wiped = tomllib.loads(path.read_text())
    assert "model_providers" not in wiped
    assert wiped["sandbox_mode"] == "danger-full-access"


def test_seed_codex_sandbox_mode_carries_provider_region_and_root_keys(tmp_path):
    """Both managed regions (hook + model provider) survive the sandbox write
    verbatim, provider root keys included."""
    path = tmp_path / "config.toml"
    seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    assert seed_codex_sandbox_mode(path, root=tmp_path) is True
    data = tomllib.loads(path.read_text())
    assert data["sandbox_mode"] == "danger-full-access"
    assert "approval_policy" not in data
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"]["base_url"] == (
        _NAVIGATOR["base_url"]
    )
    key = f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:0:0"
    assert key in data["hooks"]["state"]


# --- T1 seam: the composed codex writes are stable -------------------------
#
# The golden fixtures (test_panel_delivery_golden.py) carry the byte-identity
# guarantee; what stays unit-pinned here is the composition's stability: a
# relaunch over the two writers' own output is a byte-level no-op.


def test_codex_seam_composition_relaunch_is_no_op(tmp_path):
    """Second seam pass over its own output: neither call writes."""
    path = tmp_path / "config.toml"
    seed_codex_sandbox_mode(path, root=tmp_path)
    seed_codex_config(path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD)
    before = path.read_bytes()
    assert seed_codex_sandbox_mode(path, root=tmp_path) is False
    assert seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is False
    assert path.read_bytes() == before


# --- codex canon-hook parity: the charter cascade, mail monitor, end + stop -------
#
# The managed region wires codex to the SAME canon hooks the claude seed template
# wires: the four charter start scripts (each cascades to ``pid-add.sh``), the mail
# monitor and ``edited.sh`` after tool use, ``end.sh`` at SessionEnd (codex 0.145.0+;
# older codex ignores the unknown table) and ``stop.sh`` at Stop.  Every handler
# carries an EXPLICIT timeout hashed with that same value: codex hashes its per-event
# default when the key is absent (SessionEnd's is 1), so an implicit timeout is a trust
# entry that silently fails to match and a hook that never fires.

#: The managed region for an EMPTY user file, byte for byte.  ⚑ Every hash below was
#: reported ``trustStatus: trusted`` by codex-cli 0.158.0's own ``hooks/list``
#: (app-server) for exactly this file; a hash that moves without a command, matcher, or
#: timeout change is a bug in ``codex_trusted_hash``, not a fixture to regenerate.
_EXPECTED_CODEX_REGION = '''\
# >>> kanibako-managed (instruction-delivery hook + trust) — do not edit >>>
[[hooks.SessionStart]]
matcher = "startup|resume|clear|compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = "python3 \\"/opt/kanibako/kanibako/scripts/import-directives.py\\" --additional-context \\"$KANIBAKO_DIRECTIVE_SEED\\" || true"
timeout = 600

[[hooks.SessionStart]]
matcher = "startup"

[[hooks.SessionStart.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/startup.sh \\"$PPID\\""
timeout = 600

[[hooks.SessionStart]]
matcher = "resume"

[[hooks.SessionStart.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/resume.sh \\"$PPID\\""
timeout = 600

[[hooks.SessionStart]]
matcher = "clear"

[[hooks.SessionStart.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/clear-start.sh \\"$PPID\\""
timeout = 600

[[hooks.SessionStart]]
matcher = "compact"

[[hooks.SessionStart.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/compact.sh \\"$PPID\\""
timeout = 600

[[hooks.PostToolUse]]
matcher = "Bash|Read|Write|Edit|Glob|Grep|Agent"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "~/canon/handbook/general/scripts/behavior/check-comms.sh"
timeout = 5

[[hooks.PostToolUse]]
matcher = "Write|Edit"

[[hooks.PostToolUse.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/edited.sh"
timeout = 5

[[hooks.SessionEnd]]

[[hooks.SessionEnd.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/end.sh \\"$PPID\\""
timeout = 3

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "~/canon/charter/general/scripts/hooks/stop.sh"
timeout = 5

[hooks.state."/home/agent/.codex/config.toml:session_start:0:0"]
trusted_hash = "sha256:fcb5a2573b270adbe492e97a1b21a4cbc868744df607884eed6e2ada3023d905"

[hooks.state."/home/agent/.codex/config.toml:session_start:1:0"]
trusted_hash = "sha256:c19be2decc19aa830ee79460cc1ea4107b55b155e9a181b8904730314089221f"

[hooks.state."/home/agent/.codex/config.toml:session_start:2:0"]
trusted_hash = "sha256:6a5c2d915360b91389a8892883cb80a583b38d4ea4ca041364caa7ea7ea5ec6b"

[hooks.state."/home/agent/.codex/config.toml:session_start:3:0"]
trusted_hash = "sha256:3a6fdb542c872a168f4f27e4376dc393dcde4b451f57184aafdfb64138f88d8a"

[hooks.state."/home/agent/.codex/config.toml:session_start:4:0"]
trusted_hash = "sha256:e865c48f6621aec253dc3ec692c7a7cc772b68f3c1ecc91e501616d30cd42fe7"

[hooks.state."/home/agent/.codex/config.toml:post_tool_use:0:0"]
trusted_hash = "sha256:b3cd5e1cd24d46429f40ef5d0b3012d88464eed04035d83914399919dc14d0af"

[hooks.state."/home/agent/.codex/config.toml:post_tool_use:1:0"]
trusted_hash = "sha256:f46241dfdd6b6f73738aeec9d3ef42b8730cc86967b3398a21b98874940c0e67"

[hooks.state."/home/agent/.codex/config.toml:session_end:0:0"]
trusted_hash = "sha256:d10b85ab20b9801ac3610b8812a919a9cea105218361935a6215ca02d3bfc42a"

[hooks.state."/home/agent/.codex/config.toml:stop:0:0"]
trusted_hash = "sha256:62fc3978288f96bed4defb105b775a3a350a3d6104d4f048f201714cc5c965d0"

[projects."/home/agent/workspace"]
trust_level = "trusted"
# <<< kanibako-managed (instruction-delivery hook + trust) <<<
'''


def test_codex_region_is_exact_for_an_empty_file():
    """The whole managed region, bytes and hashes, for a fresh box."""
    assert _BOX_CFG == "/home/agent/.codex/config.toml"
    assert _CODEX_CWD == "/home/agent/workspace"
    assert _merge("") == _EXPECTED_CODEX_REGION


def _managed_groups(data: dict, event: str, user_count: int = 0) -> list[dict]:
    return data["hooks"][event][user_count:]


def test_every_managed_handler_writes_and_hashes_one_explicit_timeout():
    """The timeout codex READS is the timeout we HASHED — recomputed from the parsed
    file, per group, so an omitted or mismatched timeout reds here."""
    data = tomllib.loads(_merge(_TEMPLATE))
    state = data["hooks"]["state"]
    seen = 0
    for event, event_key in _CODEX_EVENT_KEYS.items():
        for index, group in enumerate(data["hooks"].get(event, [])):
            (handler,) = group["hooks"]
            assert "timeout" in handler, f"{event}:{index} has an implicit timeout"
            key = f"{_BOX_CFG}:{event_key}:{index}:0"
            assert state[key]["trusted_hash"] == codex_trusted_hash(
                event_key, group.get("matcher"), handler["command"],
                handler["timeout"],
            )
            seen += 1
    assert seen == len(_CODEX_MANAGED_HOOKS) == len(state) == 9


def test_session_end_trust_is_not_the_implicit_default_hash():
    """The measured trap: codex hashes an implicit SessionEnd timeout at 1, and our
    explicit 3 must hash as 3 — neither 1 nor the 600 SessionStart default."""
    data = tomllib.loads(_merge(""))
    (group,) = data["hooks"]["SessionEnd"]
    command = group["hooks"][0]["command"]
    assert group["hooks"][0]["timeout"] == 3
    got = data["hooks"]["state"][f"{_BOX_CFG}:session_end:0:0"]["trusted_hash"]
    assert got == codex_trusted_hash("session_end", None, command, 3)
    assert got != codex_trusted_hash("session_end", None, command, 1)
    assert got != codex_trusted_hash("session_end", None, command)


def test_user_groups_shift_only_their_own_event_index():
    """codex indexes groups PER EVENT, so a user PostToolUse and Stop group move our
    post_tool_use and stop trust keys and nothing else (verified trusted by codex
    0.158.0's ``hooks/list`` with this exact user content)."""
    user = (
        "[[hooks.PostToolUse]]\n"
        'matcher = "Bash"\n\n'
        "[[hooks.PostToolUse.hooks]]\n"
        'type = "command"\n'
        'command = "echo user-ptu"\n\n'
        "[[hooks.Stop]]\n\n"
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        'command = "echo user-stop"\n'
    )
    data = tomllib.loads(_merge(user))
    expected = {
        *(f"{_BOX_CFG}:session_start:{i}:0" for i in range(5)),
        f"{_BOX_CFG}:post_tool_use:1:0",
        f"{_BOX_CFG}:post_tool_use:2:0",
        f"{_BOX_CFG}:session_end:0:0",
        f"{_BOX_CFG}:stop:1:0",
    }
    assert set(data["hooks"]["state"]) == expected
    assert data["hooks"]["PostToolUse"][0]["hooks"][0]["command"] == "echo user-ptu"
    assert data["hooks"]["Stop"][0]["hooks"][0]["command"] == "echo user-stop"
    assert data["hooks"]["Stop"][1]["hooks"][0]["command"] == (
        f"{_CHARTER_HOOK_DIR}/stop.sh"
    )


def test_codex_start_groups_are_the_charter_cascade_not_a_bare_marker():
    """The four charter start scripts each cascade to ``pid-add.sh``, so the bare
    marker-write group is gone; ``end.sh`` (which cascades to ``pid-rm.sh``) takes
    every SessionEnd — codex reports only ``other`` — so it has no matcher."""
    out = _merge(_TEMPLATE)
    data = tomllib.loads(out)
    starts = _managed_groups(data, "SessionStart")
    assert [(g["matcher"], g["hooks"][0]["command"]) for g in starts[1:]] == [
        ("startup", f'{_CHARTER_HOOK_DIR}/startup.sh "$PPID"'),
        ("resume", f'{_CHARTER_HOOK_DIR}/resume.sh "$PPID"'),
        ("clear", f'{_CHARTER_HOOK_DIR}/clear-start.sh "$PPID"'),
        ("compact", f'{_CHARTER_HOOK_DIR}/compact.sh "$PPID"'),
    ]
    (end,) = data["hooks"]["SessionEnd"]
    assert "matcher" not in end
    assert end["hooks"][0]["command"] == f'{_CHARTER_HOOK_DIR}/end.sh "$PPID"'
    assert _AGENT_MARKER_WRITE_COMMAND not in out
    assert _AGENT_MARKER_REMOVE_COMMAND not in out


def test_codex_hooks_are_the_claude_seed_templates_commands():
    """PARITY: every non-directive codex command is a command the claude seed template
    runs on the SAME event — one spelling per job across both agents."""
    from tests.test_claude_seed_hooks import shipped_settings

    claude = shipped_settings()["hooks"]
    for hook in _CODEX_MANAGED_HOOKS:
        if hook.command == _SESSION_START_COMMAND:
            continue
        claude_groups = {
            entry["command"]: group.get("matcher")
            for group in claude[hook.event]
            for entry in group["hooks"]
        }
        assert hook.command in claude_groups, f"{hook.event}: {hook.command}"
        if hook.event in ("SessionStart", "PostToolUse"):
            assert claude_groups[hook.command] == hook.matcher


def test_codex_hook_commands_point_at_shipped_scripts():
    """The box paths are the binds' own dests (``_canon_dest``), and the scripts there
    ship with their executable bit — a missing one is a hook that does nothing."""
    assert _CHARTER_HOOK_DIR == _canon_dest("charter/general/scripts/hooks")
    behavior = _canon_dest("handbook/general/scripts/behavior")
    assert _MAIL_MONITOR_COMMAND == f"{behavior}/check-comms.sh"
    rom_hooks = Path(str(packaged_data_dir(*ROM_ROOT_PARTS))) / (
        "charter/general/scripts/hooks"
    )
    for leaf in ("startup", "resume", "clear-start", "compact", "edited", "end", "stop"):
        script = rom_hooks / f"{leaf}.sh"
        assert script.is_file() and os.access(script, os.X_OK), leaf
    monitor = Path(str(packaged_data_dir(
        "templates", "system", "canon", "handbook", "general", "scripts",
        "behavior", "check-comms.sh",
    )))
    assert monitor.is_file() and os.access(monitor, os.X_OK)


def test_codex_full_composition_with_sandbox_and_provider(tmp_path):
    """Full composed file (panel sandbox invariant + hook region + provider) parses and
    carries every managed hook — the super-set cell."""
    path = tmp_path / "config.toml"
    seed_codex_sandbox_mode(path, root=tmp_path)
    seed_codex_config(
        path, root=tmp_path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    data = tomllib.loads(path.read_text())
    assert data["sandbox_mode"] == "danger-full-access"
    assert data["model_provider"] == "navigator"
    commands = [
        group["hooks"][0]["command"]
        for event in _CODEX_EVENT_KEYS
        for group in data["hooks"][event]
    ]
    assert commands == [hook.command for hook in _CODEX_MANAGED_HOOKS]
    assert len(data["hooks"]["state"]) == len(_CODEX_MANAGED_HOOKS)
