"""Tests for kanibako.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from kanibako.vscode_config import (
    _AGENT_MARKER_REMOVE_COMMAND,
    _AGENT_MARKER_WRITE_COMMAND,
    _CODEX_EVENT_KEY,
    _SESSION_END_MATCHER,
    _SESSION_START_COMMAND,
    _SESSION_START_MATCHER,
    AGENT_MARKERS_DIR,
    _encode_image_ref,
    attached_container_config_path,
    clear_bypass_permissions,
    clear_claude_bypass_permissions,
    codex_trusted_hash,
    deliver_claude_panel_permissions,
    deliver_directive_session_hook,
    deliver_goose_panel_permissions,
    merge_attached_container_config,
    merge_bypass_permissions,
    merge_codex_config,
    merge_marker_remove_hook,
    merge_marker_write_hook,
    merge_session_start_hook,
    seed_attached_container_config,
    seed_claude_bypass_permissions,
    seed_codex_config,
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


# --- Ph4b Vector A: in-box claude settings.json bypassPermissions -----------

def test_merge_bypass_into_empty():
    assert merge_bypass_permissions({}) == {
        "permissions": {"defaultMode": "bypassPermissions"},
    }


def test_merge_bypass_preserves_other_top_level_keys():
    """The curated template keys ($schema, includeCoAuthoredBy) are preserved."""
    existing = {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "includeCoAuthoredBy": False,
    }
    merged = merge_bypass_permissions(existing)
    assert merged["$schema"] == existing["$schema"]
    assert merged["includeCoAuthoredBy"] is False
    assert merged["permissions"]["defaultMode"] == "bypassPermissions"


def test_merge_bypass_preserves_other_permissions_subkeys():
    """Sibling permissions.* sub-keys (allow/deny) are preserved."""
    existing = {"permissions": {"allow": ["Bash(ls)"], "deny": ["Bash(rm)"]}}
    merged = merge_bypass_permissions(existing)
    assert merged["permissions"]["allow"] == ["Bash(ls)"]
    assert merged["permissions"]["deny"] == ["Bash(rm)"]
    assert merged["permissions"]["defaultMode"] == "bypassPermissions"


def test_merge_bypass_does_not_mutate_input():
    existing = {"permissions": {"allow": ["x"]}}
    merge_bypass_permissions(existing)
    assert existing == {"permissions": {"allow": ["x"]}}


def test_seed_bypass_writes_and_merges_template(tmp_path):
    """Merges into the curated seed, preserving its keys; returns True."""
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "includeCoAuthoredBy": False,
    }))
    wrote = seed_claude_bypass_permissions(path)
    assert wrote is True
    written = json.loads(path.read_text())
    assert written["includeCoAuthoredBy"] is False
    assert written["permissions"]["defaultMode"] == "bypassPermissions"


def test_seed_bypass_idempotent(tmp_path):
    path = tmp_path / "settings.json"
    assert seed_claude_bypass_permissions(path) is True
    before = path.read_text()
    assert seed_claude_bypass_permissions(path) is False
    assert path.read_text() == before


def test_seed_bypass_tolerates_corrupt(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("not json {{{")
    assert seed_claude_bypass_permissions(path) is True
    assert json.loads(path.read_text()) == {
        "permissions": {"defaultMode": "bypassPermissions"},
    }


# --- Ph4b Vector A OFF-direction: clear_bypass_permissions (pure) -----------

def test_clear_removes_only_our_managed_value():
    """Our exact ``bypassPermissions`` value is removed; permissions dropped when
    it was created only by us (empty after removal)."""
    assert clear_bypass_permissions(
        {"permissions": {"defaultMode": "bypassPermissions"}},
    ) == {}


def test_clear_preserves_user_chosen_mode():
    """A user-chosen mode (plan/default/acceptEdits) is NOT touched."""
    for mode in ("plan", "default", "acceptEdits"):
        existing = {"permissions": {"defaultMode": mode}}
        assert clear_bypass_permissions(existing) == existing


def test_clear_keeps_sibling_permissions_and_drops_only_default_mode():
    """defaultMode==bypass is cleared but allow/deny (and the permissions block) stay."""
    existing = {
        "permissions": {
            "defaultMode": "bypassPermissions",
            "allow": ["Bash(ls)"],
            "deny": ["Bash(rm)"],
        },
        "includeCoAuthoredBy": False,
    }
    cleared = clear_bypass_permissions(existing)
    assert "defaultMode" not in cleared["permissions"]
    assert cleared["permissions"]["allow"] == ["Bash(ls)"]
    assert cleared["permissions"]["deny"] == ["Bash(rm)"]
    assert cleared["includeCoAuthoredBy"] is False


def test_clear_no_permissions_is_noop():
    assert clear_bypass_permissions({"includeCoAuthoredBy": False}) == {
        "includeCoAuthoredBy": False,
    }


def test_clear_does_not_mutate_input():
    existing = {"permissions": {"defaultMode": "bypassPermissions", "allow": ["x"]}}
    clear_bypass_permissions(existing)
    assert existing == {"permissions": {"defaultMode": "bypassPermissions", "allow": ["x"]}}


def test_clear_claude_bypass_absent_file_noop(tmp_path):
    """No file → no-op, and the file is NOT created."""
    path = tmp_path / "settings.json"
    assert clear_claude_bypass_permissions(path) is False
    assert not path.exists()


def test_clear_claude_bypass_writes_when_present(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "includeCoAuthoredBy": False,
        "permissions": {"defaultMode": "bypassPermissions", "allow": ["x"]},
    }))
    assert clear_claude_bypass_permissions(path) is True
    written = json.loads(path.read_text())
    assert "defaultMode" not in written["permissions"]
    assert written["permissions"]["allow"] == ["x"]
    assert written["includeCoAuthoredBy"] is False


def test_clear_claude_bypass_idempotent(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"permissions": {"defaultMode": "plan"}}))
    # A user's own mode → no change, no write.
    assert clear_claude_bypass_permissions(path) is False


# --- Ph4b Vector A gate: deliver_claude_panel_permissions (SYMMETRIC) --------

def test_deliver_on_claude_sets(tmp_path):
    """auto_approve ON + claude → SETs bypassPermissions."""
    wrote = deliver_claude_panel_permissions(
        auto_approve=True, is_claude=True, claude_config_dir=tmp_path,
    )
    assert wrote is True
    written = json.loads((tmp_path / "settings.json").read_text())
    assert written["permissions"]["defaultMode"] == "bypassPermissions"


def test_deliver_off_clears_our_bypass(tmp_path):
    """auto_approve OFF + an existing MANAGED bypass → CLEARED (mutation-proven).

    Reverting the clear-path (making the OFF branch inert) reddens this test."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}))
    wrote = deliver_claude_panel_permissions(
        auto_approve=False, is_claude=True, claude_config_dir=tmp_path,
    )
    assert wrote is True
    assert json.loads(path.read_text()) == {}


def test_deliver_off_preserves_user_mode(tmp_path):
    """auto_approve OFF + a user's own ``plan`` mode → left intact (no write)."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"permissions": {"defaultMode": "plan"}}))
    wrote = deliver_claude_panel_permissions(
        auto_approve=False, is_claude=True, claude_config_dir=tmp_path,
    )
    assert wrote is False
    assert json.loads(path.read_text()) == {"permissions": {"defaultMode": "plan"}}


def test_deliver_off_no_file_is_noop(tmp_path):
    """auto_approve OFF + no file → no-op, file NOT created."""
    wrote = deliver_claude_panel_permissions(
        auto_approve=False, is_claude=True, claude_config_dir=tmp_path,
    )
    assert wrote is False
    assert not (tmp_path / "settings.json").exists()


def test_deliver_off_clears_default_mode_keeps_allow_deny(tmp_path):
    """OFF + permissions with allow/deny AND our bypass defaultMode → defaultMode
    cleared, allow/deny kept."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "permissions": {
            "defaultMode": "bypassPermissions",
            "allow": ["Bash(ls)"],
            "deny": ["Bash(rm)"],
        },
    }))
    wrote = deliver_claude_panel_permissions(
        auto_approve=False, is_claude=True, claude_config_dir=tmp_path,
    )
    assert wrote is True
    perms = json.loads(path.read_text())["permissions"]
    assert "defaultMode" not in perms
    assert perms["allow"] == ["Bash(ls)"]
    assert perms["deny"] == ["Bash(rm)"]


def test_deliver_non_claude_is_inert(tmp_path):
    """Non-claude agent → NOTHING written in EITHER direction."""
    assert deliver_claude_panel_permissions(
        auto_approve=True, is_claude=False, claude_config_dir=tmp_path,
    ) is False
    assert deliver_claude_panel_permissions(
        auto_approve=False, is_claude=False, claude_config_dir=tmp_path,
    ) is False
    assert not (tmp_path / "settings.json").exists()


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
    silent-safe, referencing the SEED env var."""
    assert _SESSION_START_COMMAND == (
        'python3 "$HOME/playbook/kanibako/scripts/import-directives.py" '
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
    assert seed_session_start_hook(path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": _full_managed_hooks()}
    # Idempotent: second call writes nothing.
    assert seed_session_start_hook(path) is False


def test_seed_session_start_merges_into_existing_user_hooks(tmp_path):
    """A user's pre-existing settings.json is union-merged, never clobbered."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "hooks": {"PreToolUse": [{"matcher": "*", "hooks": []}]},
    }))
    assert seed_session_start_hook(path) is True
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
    assert seed_session_start_hook(path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": _full_managed_hooks()}


# --- per-PID markers: write (SessionStart) + remove (SessionEnd) -----------

def test_marker_commands_default_dir_is_the_single_source_constant():
    """SINGLE SOURCE OF TRUTH guard: the hook commands' default dir is built FROM
    AGENT_MARKERS_DIR, so it cannot drift from the supervisor's --agent-markers-dir."""
    default = "${KANIBAKO_AGENT_MARKERS_DIR:-" + AGENT_MARKERS_DIR + "}"
    assert default in _AGENT_MARKER_WRITE_COMMAND
    assert default in _AGENT_MARKER_REMOVE_COMMAND
    # The write command writes a per-PID marker <dir>/$PPID after mkdir -p; silent-safe.
    assert _AGENT_MARKER_WRITE_COMMAND == (
        f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
        'mkdir -p "$d" && printf %s "$PPID" > "$d/$PPID" || true'
    )
    assert _AGENT_MARKER_REMOVE_COMMAND == (
        f'd="${{KANIBAKO_AGENT_MARKERS_DIR:-{AGENT_MARKERS_DIR}}}"; '
        'rm -f "$d/$PPID" || true'
    )


def test_marker_write_filename_is_the_pid_not_a_fixed_file():
    """The per-PID scheme keys the marker on $PPID as the FILENAME (``$d/$PPID``),
    so a CLI incumbent and a panel newcomer each hold their OWN marker — no single
    last-writer-wins path."""
    assert '"$d/$PPID"' in _AGENT_MARKER_WRITE_COMMAND
    assert '"$d/$PPID"' in _AGENT_MARKER_REMOVE_COMMAND


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
    assert seed_session_start_hook(path) is True
    data = json.loads(path.read_text())
    assert data["hooks"]["SessionStart"] == [
        _managed_group(), _marker_write_group(),
    ]
    assert data["hooks"]["SessionEnd"] == [user_se, _marker_remove_group()]
    assert seed_session_start_hook(path) is False


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
    """The default timeout is 600 s (codex normalises unset → 600)."""
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


def _merge(text, *, auto_approve=True):
    return merge_codex_config(
        text, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        auto_approve=auto_approve,
    )


def test_codex_merge_produces_valid_toml_with_managed_hook_group():
    out = _merge(_TEMPLATE)
    data = tomllib.loads(out)  # parses
    group = data["hooks"]["SessionStart"][0]
    assert group["matcher"] == _SESSION_START_MATCHER
    assert group["hooks"][0] == {
        "type": "command", "command": _SESSION_START_COMMAND,
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
    assert '"$HOME' in cmd and '"$KANIBAKO_DIRECTIVE_SEED"' in cmd


def test_codex_merge_approval_parity_on_sets_both():
    data = tomllib.loads(_merge(_TEMPLATE, auto_approve=True))
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "workspace-write"


def test_codex_merge_approval_parity_off_omits_both():
    data = tomllib.loads(_merge(_TEMPLATE, auto_approve=False))
    assert "approval_policy" not in data
    assert "sandbox_mode" not in data
    # hook + trust are UNCONDITIONAL (orthogonal to yolo).
    assert "SessionStart" in data["hooks"]


def test_codex_merge_off_clears_only_managed_value():
    """OFF removes our managed approval value but LEAVES a user-chosen one."""
    text = 'approval_policy = "never"\nsandbox_mode = "read-only"\n'
    data = tomllib.loads(_merge(text, auto_approve=False))
    assert "approval_policy" not in data  # was our managed value → removed
    assert data["sandbox_mode"] == "read-only"  # user value → preserved


def test_codex_merge_on_overrides_user_approval():
    """ON SETS the managed value even over a user-chosen one (mirrors claude
    merge_bypass_permissions ON-direction)."""
    text = 'approval_policy = "untrusted"\n'
    data = tomllib.loads(_merge(text, auto_approve=True))
    assert data["approval_policy"] == "never"


def test_codex_merge_preserves_unrelated_user_keys_and_comments():
    text = (
        "# my header comment\n"
        'model = "gpt-5-codex"\n\n'
        "[mcp_servers.foo]\n"
        'command = "serve"\n'
    )
    out = _merge(text)
    assert "# my header comment" in out  # comment preserved
    data = tomllib.loads(out)
    assert data["model"] == "gpt-5-codex"
    assert data["mcp_servers"]["foo"]["command"] == "serve"
    # managed root keys land BEFORE the first table (legal top-level position).
    assert data["approval_policy"] == "never"


def test_codex_merge_is_idempotent():
    once = _merge(_TEMPLATE)
    twice = _merge(once)
    assert twice == once
    # exactly ONE managed group after a re-merge.
    assert once.count("[[hooks.SessionStart]]") == 1


def test_codex_merge_idempotent_off_direction():
    off_once = _merge(_TEMPLATE, auto_approve=False)
    off_twice = _merge(off_once, auto_approve=False)
    assert off_twice == off_once


def test_codex_merge_toggle_on_then_off_then_on():
    on1 = _merge(_TEMPLATE, auto_approve=True)
    off = _merge(on1, auto_approve=False)
    on2 = _merge(off, auto_approve=True)
    assert on2 == on1  # round-trips back to the ON state


def test_codex_merge_tolerates_corrupt_text():
    """A non-TOML/corrupt body never raises; it is preserved and the managed
    region is still appended (produces valid managed tables)."""
    out = _merge("this is {not valid toml\n")
    assert "this is {not valid toml" in out
    # the command is TOML-escaped in the file, but its stable substring is there.
    assert "import-directives.py" in out
    assert out.count("[[hooks.SessionStart]]") == 1


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
    # user group at 0, ours at 1 → state key uses group index 1.
    assert f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:1:0" in data["hooks"]["state"]
    groups = data["hooks"]["SessionStart"]
    assert len(groups) == 2
    assert groups[0]["hooks"][0]["command"] == "echo hi"
    assert groups[1]["hooks"][0]["command"] == _SESSION_START_COMMAND


# --- seed_codex_config (I/O) + deliver_directive_session_hook dispatch -------

def test_seed_codex_config_writes_and_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, auto_approve=True,
    ) is True
    tomllib.loads(path.read_text())  # valid
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, auto_approve=True,
    ) is False


def test_seed_codex_config_absent_file_is_created(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, auto_approve=True,
    ) is True
    assert path.exists()


def test_deliver_directive_codex_writes_config_toml_not_hooks_json(tmp_path):
    """The codex branch writes ~/.codex/config.toml (TOML) — NOT a hooks.json."""
    wrote = deliver_directive_session_hook(
        agent_name="codex",
        config_root=tmp_path,
        box_codex_config_path=_BOX_CFG,
        codex_cwd=_CODEX_CWD,
        auto_approve=True,
    )
    assert wrote is True
    cfg = tmp_path / ".codex" / "config.toml"
    assert cfg.exists()
    assert not (tmp_path / ".codex" / "hooks.json").exists()
    data = tomllib.loads(cfg.read_text())
    assert data["hooks"]["SessionStart"][0]["hooks"][0]["command"] == (
        _SESSION_START_COMMAND
    )
    assert data["projects"][_CODEX_CWD]["trust_level"] == "trusted"


def test_deliver_directive_claude_writes_settings_json(tmp_path):
    """The claude branch still writes ~/.claude/settings.json (JSON hooks)."""
    wrote = deliver_directive_session_hook(
        agent_name="claude",
        config_root=tmp_path,
        box_codex_config_path=_BOX_CFG,
        codex_cwd=_CODEX_CWD,
        auto_approve=True,
    )
    assert wrote is True
    settings = tmp_path / ".claude" / "settings.json"
    assert settings.exists()
    assert not (tmp_path / ".codex" / "config.toml").exists()
    data = json.loads(settings.read_text())
    assert data["hooks"] == _full_managed_hooks()


def test_deliver_directive_other_agent_is_inert(tmp_path):
    """A non-claude agent (goose) gets NO claude hooks — no pidfile write/remove."""
    assert deliver_directive_session_hook(
        agent_name="goose",
        config_root=tmp_path,
        box_codex_config_path=_BOX_CFG,
        codex_cwd=_CODEX_CWD,
        auto_approve=True,
    ) is False
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / ".claude").exists()


# --- FF-5 permission parity: deliver_goose_panel_permissions ----------------

def _goose_cfg(tmp_path) -> Path:
    return tmp_path / "config.yaml"


def test_deliver_goose_on_writes_auto(tmp_path):
    """auto_approve ON + goose → GOOSE_MODE: auto."""
    wrote = deliver_goose_panel_permissions(
        auto_approve=True, is_goose=True, goose_config_dir=tmp_path,
    )
    assert wrote is True
    assert yaml.safe_load(_goose_cfg(tmp_path).read_text()) == {
        "GOOSE_MODE": "auto",
    }


def test_deliver_goose_off_writes_approve(tmp_path):
    """auto_approve OFF + goose → GOOSE_MODE: approve (EXPLICIT secure value, not
    cleared — an unset GOOSE_MODE defaults to permissive ``auto``)."""
    wrote = deliver_goose_panel_permissions(
        auto_approve=False, is_goose=True, goose_config_dir=tmp_path,
    )
    assert wrote is True
    assert yaml.safe_load(_goose_cfg(tmp_path).read_text()) == {
        "GOOSE_MODE": "approve",
    }


def test_deliver_goose_non_goose_is_inert(tmp_path):
    """Non-goose agent → NOTHING written in EITHER direction."""
    assert deliver_goose_panel_permissions(
        auto_approve=True, is_goose=False, goose_config_dir=tmp_path,
    ) is False
    assert deliver_goose_panel_permissions(
        auto_approve=False, is_goose=False, goose_config_dir=tmp_path,
    ) is False
    assert not _goose_cfg(tmp_path).exists()


def test_deliver_goose_absent_file_created_with_just_goose_mode(tmp_path):
    """An absent config.yaml is created with ONLY the GOOSE_MODE key."""
    path = _goose_cfg(tmp_path)
    assert not path.exists()
    assert deliver_goose_panel_permissions(
        auto_approve=True, is_goose=True, goose_config_dir=tmp_path,
    ) is True
    assert yaml.safe_load(path.read_text()) == {"GOOSE_MODE": "auto"}


def test_deliver_goose_preserves_unrelated_keys(tmp_path):
    """Pre-existing unrelated keys are preserved across the write."""
    path = _goose_cfg(tmp_path)
    path.write_text(yaml.safe_dump({
        "GOOSE_PROVIDER": "anthropic",
        "extensions": {"foo": {"enabled": True}},
    }))
    assert deliver_goose_panel_permissions(
        auto_approve=False, is_goose=True, goose_config_dir=tmp_path,
    ) is True
    written = yaml.safe_load(path.read_text())
    assert written["GOOSE_PROVIDER"] == "anthropic"
    assert written["extensions"] == {"foo": {"enabled": True}}
    assert written["GOOSE_MODE"] == "approve"


def test_deliver_goose_is_idempotent(tmp_path):
    """A second call with the same state returns False and does not rewrite."""
    assert deliver_goose_panel_permissions(
        auto_approve=True, is_goose=True, goose_config_dir=tmp_path,
    ) is True
    before = _goose_cfg(tmp_path).read_text()
    assert deliver_goose_panel_permissions(
        auto_approve=True, is_goose=True, goose_config_dir=tmp_path,
    ) is False
    assert _goose_cfg(tmp_path).read_text() == before


def test_deliver_goose_overwrites_conflicting_existing_value(tmp_path):
    """A pre-existing GOOSE_MODE with a DIFFERENT value is OVERWRITTEN to the
    desired one (guards against a setdefault-style mutant that would leave a
    stale permissive ``auto`` in place for an OFF box)."""
    path = _goose_cfg(tmp_path)
    path.write_text(yaml.safe_dump({
        "GOOSE_MODE": "auto",
        "GOOSE_PROVIDER": "anthropic",
    }))
    assert deliver_goose_panel_permissions(
        auto_approve=False, is_goose=True, goose_config_dir=tmp_path,
    ) is True
    written = yaml.safe_load(path.read_text())
    assert written["GOOSE_MODE"] == "approve"
    assert written["GOOSE_PROVIDER"] == "anthropic"
