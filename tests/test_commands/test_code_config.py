"""Tests for kanibako.vscode.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import yaml

from kanibako.vscode.vscode_config import (
    _AGENT_MARKER_REMOVE_COMMAND,
    _AGENT_MARKER_WRITE_COMMAND,
    _CODEX_EVENT_KEY,
    _SESSION_END_MATCHER,
    _SESSION_START_COMMAND,
    _SESSION_START_MATCHER,
    AGENT_MARKERS_DIR,
    CodexModelProvider,
    _encode_image_ref,
    attached_container_config_path,
    clear_bypass_permissions,
    clear_claude_bypass_permissions,
    codex_trusted_hash,
    merge_attached_container_config,
    merge_bypass_permissions,
    merge_codex_config,
    merge_codex_model_provider,
    merge_marker_remove_hook,
    merge_marker_write_hook,
    merge_session_start_hook,
    seed_attached_container_config,
    seed_claude_bypass_permissions,
    seed_codex_approval,
    seed_codex_config,
    seed_goose_mode,
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
    # The remaining ``$VAR`` must survive VERBATIM — it expands in-box at hook-run
    # time, and the trust hash is computed on the raw string.  (The flattener path
    # is now an absolute package-bind path, so ``$HOME`` no longer appears; the
    # unexpanded-env-var property is what this test is about, not that literal.)
    assert '"$KANIBAKO_DIRECTIVE_SEED"' in cmd
    assert '"/opt/kanibako/kanibako/scripts/import-directives.py"' in cmd


def test_codex_merge_never_writes_approval_keys():
    """T1 seam split: the directive merge NEVER writes the approval/sandbox
    parity keys — those have exactly one writer, ``seed_codex_approval``."""
    data = tomllib.loads(_merge(_TEMPLATE))
    assert "approval_policy" not in data
    assert "sandbox_mode" not in data
    # hook + trust are UNCONDITIONAL (orthogonal to yolo).
    assert "SessionStart" in data["hooks"]


def test_codex_merge_passes_existing_approval_keys_through(tmp_path):
    """Keys already in the file (whoever wrote them — the approval seeder or
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
    # exactly the TWO managed groups (directive + D2 marker) after a re-merge.
    assert once.count("[[hooks.SessionStart]]") == 2


def test_codex_merge_tolerates_corrupt_text():
    """A non-TOML/corrupt body never raises; it is preserved and the managed
    region is still appended (produces valid managed tables)."""
    out = _merge("this is {not valid toml\n")
    assert "this is {not valid toml" in out
    # the command is TOML-escaped in the file, but its stable substring is there.
    assert "import-directives.py" in out
    assert out.count("[[hooks.SessionStart]]") == 2


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
    # user group at 0, ours at 1 (directive) and 2 (marker) → BOTH state keys
    # shift with the user count.
    assert f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:1:0" in data["hooks"]["state"]
    assert f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:2:0" in data["hooks"]["state"]
    groups = data["hooks"]["SessionStart"]
    assert len(groups) == 3
    assert groups[0]["hooks"][0]["command"] == "echo hi"
    assert groups[1]["hooks"][0]["command"] == _SESSION_START_COMMAND


# --- seed_codex_config (I/O; the directive-hook write) ----------------------

def test_seed_codex_config_writes_and_is_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is True
    tomllib.loads(path.read_text())  # valid
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is False


def test_seed_codex_config_absent_file_is_created(tmp_path):
    path = tmp_path / "nested" / "config.toml"
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
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
    provider region, all valid TOML, hook behaviour intact."""
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
    assert once.count("[[hooks.SessionStart]]") == 2


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
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=mp,
    ) is True
    # bare re-seed reconciles the file back to stock
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=None,
    ) is True
    text = path.read_text()
    assert "# >>> kanibako-managed (model provider)" not in text
    data = tomllib.loads(text)
    assert "model" not in data
    assert "model_provider" not in data
    # already reconciled → second bare seed writes nothing
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD, model_provider=None,
    ) is False


def test_seed_codex_config_no_provider_byte_identical(tmp_path):
    """seed_codex_config default write is byte-identical to an explicit
    model_provider=None write (the INC-3 I/O seam is inert by default)."""
    p1 = tmp_path / "a.toml"
    p2 = tmp_path / "b.toml"
    seed_codex_config(
        p1, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    seed_codex_config(
        p2, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=None,
    )
    assert p1.read_text() == p2.read_text()


def test_seed_codex_config_with_provider_writes_region(tmp_path):
    """seed_codex_config threads the provider through to the written file."""
    path = tmp_path / "config.toml"
    mp = CodexModelProvider(**_NAVIGATOR)
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=mp,
    ) is True
    data = tomllib.loads(path.read_text())
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"]["env_key"] == "NAVIGATOR_API_KEY"


# --- T1 seam Step 2: seed_codex_approval (standalone approval writer) --------
#
# The codex panel-permissions emitter behind ``Target.deliver_panel_permissions``.
# The SOLE writer of approval_policy/sandbox_mode; the directive write
# (seed_codex_config) never touches them.


def test_seed_codex_approval_on_absent_creates_approval_only(tmp_path):
    path = tmp_path / ".codex" / "config.toml"
    assert seed_codex_approval(path, auto_approve=True) is True
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "danger-full-access"
    assert "hooks" not in data  # approval ONLY — no region, no trust


def test_seed_codex_approval_off_absent_creates_sandbox_invariant(tmp_path):
    """sandbox_mode is a BOX INVARIANT: even an OFF launch on an absent file
    CREATES the config to establish ``danger-full-access`` (the panel
    app-server needs it regardless of yolo).  approval_policy stays absent
    (yolo-gated, nothing to set when OFF)."""
    path = tmp_path / ".codex" / "config.toml"
    assert seed_codex_approval(path, auto_approve=False) is True
    data = tomllib.loads(path.read_text())
    assert data["sandbox_mode"] == "danger-full-access"
    assert "approval_policy" not in data
    assert "hooks" not in data


def test_seed_codex_approval_idempotent(tmp_path):
    path = tmp_path / "config.toml"
    assert seed_codex_approval(path, auto_approve=True) is True
    before = path.read_bytes()
    assert seed_codex_approval(path, auto_approve=True) is False
    assert path.read_bytes() == before


def test_seed_codex_approval_off_removes_managed_policy_forces_sandbox(tmp_path):
    """OFF removes a MANAGED approval_policy but sandbox_mode is FORCED to the
    invariant (kanibako owns it) — a prior user ``read-only`` is migrated, not
    preserved."""
    path = tmp_path / "config.toml"
    path.write_text('approval_policy = "never"\nsandbox_mode = "read-only"\n')
    assert seed_codex_approval(path, auto_approve=False) is True
    data = tomllib.loads(path.read_text())
    assert "approval_policy" not in data  # was our managed value → removed
    assert data["sandbox_mode"] == "danger-full-access"  # kanibako-owned → forced


def test_seed_codex_approval_off_preserves_user_chosen_approval_policy(tmp_path):
    """A user-chosen approval_policy (NOT the managed ``never``) survives OFF;
    sandbox_mode is still forced to the invariant alongside it."""
    path = tmp_path / "config.toml"
    path.write_text('approval_policy = "on-request"\n')
    assert seed_codex_approval(path, auto_approve=False) is True
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "on-request"  # user value → preserved
    assert data["sandbox_mode"] == "danger-full-access"


def test_seed_codex_approval_migrates_old_workspace_write(tmp_path):
    """MIGRATION: a config carrying the OLD kanibako-managed
    ``sandbox_mode = "workspace-write"`` reconciles to ``danger-full-access``
    on BOTH the ON and OFF paths (the invariant is independent of yolo)."""
    for auto in (True, False):
        path = tmp_path / f"config-{auto}.toml"
        path.write_text('sandbox_mode = "workspace-write"\n')
        assert seed_codex_approval(path, auto_approve=auto) is True
        assert tomllib.loads(path.read_text())["sandbox_mode"] == (
            "danger-full-access"
        )


def test_seed_codex_approval_no_op_never_normalizes_user_bytes(tmp_path):
    """Short-circuit contract: when the reconciled state already matches
    (approval_policy correct AND sandbox_mode already the invariant) the seeder
    must NOT rewrite (no trailing-newline normalization, no region relocation)."""
    path = tmp_path / "config.toml"
    # already-correct OFF state: no approval_policy, sandbox at the invariant,
    # plus extra trailing newlines that must NOT be normalized away.
    odd = '# mine\nsandbox_mode = "danger-full-access"\nfoo = "bar"\n\n\n'
    path.write_text(odd)
    assert seed_codex_approval(path, auto_approve=False) is False
    assert path.read_text() == odd


def test_seed_codex_approval_keys_land_outside_managed_region(tmp_path):
    """The insert-inside-region hazard: a region-bearing file with NO user
    tables — a naive root-key insert would land after the region's BEGIN marker
    (the first ``[`` line is the region's own table) and be swallowed by the
    next regeneration.  The seeder must extract the region, edit the clean
    body, and carry the region through VERBATIM."""
    path = tmp_path / "config.toml"
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    region_only = path.read_text()
    assert region_only.lstrip().startswith("# >>> kanibako-managed")
    assert seed_codex_approval(path, auto_approve=True) is True
    out = path.read_text()
    # keys present, and BEFORE the region begin marker (root section):
    assert out.index("approval_policy") < out.index("# >>> kanibako-managed")
    # region carried through verbatim:
    begin = out.index("# >>> kanibako-managed")
    assert out[begin:] == region_only.lstrip("\n").rstrip("\n") + "\n"
    # ...and a subsequent directive re-merge does NOT swallow the keys:
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "danger-full-access"


def test_seed_codex_approval_keys_never_land_inside_provider_region(tmp_path):
    """``seed_codex_approval`` extracts BOTH managed regions before the root-key
    surgery, so its approval keys land in the TRUE root section — never INSIDE
    the provider region (the legacy insert lost them there; the hazard was
    deleted at plan Step 5).  Under D1 the provider region only exists on a
    PERSONA launch (a bare launch WIPES it), so the region-present cell is a
    persona launch: with approval keys absent, toggle yolo ON and confirm the
    keys sit OUTSIDE (before) the provider region."""
    path = tmp_path / "config.toml"
    # Persona launch with approval keys absent (auto_approve=False history):
    seed_codex_approval(path, auto_approve=False)
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    # Toggle yolo ON on the provider-region-bearing file — the keys must NOT land
    # inside the provider region:
    assert seed_codex_approval(path, auto_approve=True) is True
    out = path.read_text()
    assert out.index("approval_policy") < out.index(
        "# >>> kanibako-managed (model provider)"
    )
    # …and a subsequent PERSONA launch (provider region regenerated) must NOT
    # swallow them — the exact loss the legacy insert position caused:
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "danger-full-access"
    # D1: a BARE relaunch WIPES the provider region but the approval/sandbox keys
    # (owned by the separate approval seam, not the provider projection) SURVIVE.
    seed_codex_config(path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD)
    wiped = tomllib.loads(path.read_text())
    assert "model_providers" not in wiped
    assert wiped["approval_policy"] == "never"
    assert wiped["sandbox_mode"] == "danger-full-access"


def test_seed_codex_approval_carries_provider_region_and_root_keys(tmp_path):
    """Both managed regions (hook + model provider) survive an approval toggle
    verbatim, provider root keys included."""
    path = tmp_path / "config.toml"
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    assert seed_codex_approval(path, auto_approve=True) is True
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["model_provider"] == "navigator"
    assert data["model_providers"]["navigator"]["base_url"] == (
        _NAVIGATOR["base_url"]
    )
    key = f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:0:0"
    assert key in data["hooks"]["state"]


# --- T1 seam: the composed codex writes are stable -------------------------
#
# The legacy-vs-seam byte-equivalence matrix retired WITH the legacy write
# (plan Step 5); the golden fixtures (test_panel_delivery_golden.py) carry the
# byte-identity guarantee permanently.  What stays unit-pinned here is the
# composition's stability: a relaunch over the two writers' own output is a
# byte-level no-op (no writer fights the other).


def test_codex_seam_composition_relaunch_is_no_op(tmp_path):
    """Second seam pass over its own output: neither call writes."""
    path = tmp_path / "config.toml"
    seed_codex_approval(path, auto_approve=True)
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    )
    before = path.read_bytes()
    assert seed_codex_approval(path, auto_approve=True) is False
    assert seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
    ) is False
    assert path.read_bytes() == before


# --- T1 seam Step 2: seed_goose_mode (extracted GOOSE_MODE emitter) ----------


def test_seed_goose_mode_on_writes_auto(tmp_path):
    path = tmp_path / "config.yaml"
    assert seed_goose_mode(path, auto_approve=True) is True
    assert yaml.safe_load(path.read_text())["GOOSE_MODE"] == "auto"


def test_seed_goose_mode_off_writes_explicit_approve(tmp_path):
    path = tmp_path / "config.yaml"
    assert seed_goose_mode(path, auto_approve=False) is True
    assert yaml.safe_load(path.read_text())["GOOSE_MODE"] == "approve"


def test_seed_goose_mode_idempotent_and_merge_preserving(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("GOOSE_PROVIDER: openai\nextensions:\n  developer:\n    enabled: true\n")
    assert seed_goose_mode(path, auto_approve=True) is True
    doc = yaml.safe_load(path.read_text())
    assert doc["GOOSE_MODE"] == "auto"
    assert doc["GOOSE_PROVIDER"] == "openai"
    assert doc["extensions"]["developer"]["enabled"] is True
    before = path.read_bytes()
    assert seed_goose_mode(path, auto_approve=True) is False
    assert path.read_bytes() == before


def test_seed_goose_mode_overwrites_conflicting_existing_value(tmp_path):
    """A pre-existing GOOSE_MODE with a DIFFERENT value is OVERWRITTEN to the
    desired one (guards against a setdefault-style mutant that would leave a
    stale permissive ``auto`` in place for an OFF box)."""
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "GOOSE_MODE": "auto",
        "GOOSE_PROVIDER": "anthropic",
    }))
    assert seed_goose_mode(path, auto_approve=False) is True
    written = yaml.safe_load(path.read_text())
    assert written["GOOSE_MODE"] == "approve"
    assert written["GOOSE_PROVIDER"] == "anthropic"


def test_seed_codex_approval_on_overrides_user_value(tmp_path):
    """ON SETS the managed value even over a user-chosen one (mirrors claude
    merge_bypass_permissions ON-direction)."""
    path = tmp_path / "config.toml"
    path.write_text('approval_policy = "untrusted"\n')
    assert seed_codex_approval(path, auto_approve=True) is True
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["sandbox_mode"] == "danger-full-access"


def test_seed_codex_approval_toggle_on_off_on_round_trips(tmp_path):
    """ON → OFF → ON round-trips back to the ON bytes (the toggle discipline
    that lived in the retired coupled merge, now the seeder's contract)."""
    path = tmp_path / "config.toml"
    seed_codex_approval(path, auto_approve=True)
    on1 = path.read_bytes()
    assert seed_codex_approval(path, auto_approve=False) is True
    assert seed_codex_approval(path, auto_approve=True) is True
    assert path.read_bytes() == on1


# --- Phase 2 D2: codex liveness-marker SessionStart group ---------------------
#
# codex has NO SessionEnd hook event (HookEventName verified identical at
# rust-v0.141.0 and 0.144.x), so the managed region carries ONLY the marker
# WRITE as its second [[hooks.SessionStart]] group; the remove side is the
# box_supervisor's kill-0 dead-PID classification.  The command is claude's
# _AGENT_MARKER_WRITE_COMMAND VERBATIM (one SoT for both agents' marker bytes).


def test_codex_merge_marker_group_is_second_managed_group():
    data = tomllib.loads(_merge(_TEMPLATE))
    groups = data["hooks"]["SessionStart"]
    assert len(groups) == 2
    # directive first (byte-order the goldens freeze), marker second.
    assert groups[0]["hooks"][0]["command"] == _SESSION_START_COMMAND
    assert groups[1]["hooks"][0]["command"] == _AGENT_MARKER_WRITE_COMMAND
    assert groups[1]["matcher"] == _SESSION_START_MATCHER
    assert groups[1]["hooks"][0]["type"] == "command"


def test_codex_merge_marker_group_has_own_trust_entry():
    """Both managed groups carry their own pre-computed [hooks.state] hash so
    the FIRST launch fires them with no /hooks prompt — the marker entry is
    keyed on group index 1 (no user groups) and hashes the MARKER command."""
    data = tomllib.loads(_merge(_TEMPLATE))
    state = data["hooks"]["state"]
    directive_key = f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:0:0"
    marker_key = f"{_BOX_CFG}:{_CODEX_EVENT_KEY}:1:0"
    assert set(state) == {directive_key, marker_key}
    assert state[marker_key]["trusted_hash"] == codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _AGENT_MARKER_WRITE_COMMAND,
    )
    assert state[directive_key]["trusted_hash"] == codex_trusted_hash(
        _CODEX_EVENT_KEY, _SESSION_START_MATCHER, _SESSION_START_COMMAND,
    )
    # distinct commands ⇒ distinct hashes (a copy-paste of one hash would
    # trip codex's trust prompt on the other group).
    assert state[marker_key]["trusted_hash"] != state[directive_key]["trusted_hash"]


def test_codex_merge_no_marker_remove_hook_anywhere():
    """No SessionEnd exists in codex — the region must not emit one (an
    unknown hooks table would be at best ignored, at worst rejected)."""
    out = _merge(_TEMPLATE)
    assert "SessionEnd" not in out
    assert _AGENT_MARKER_REMOVE_COMMAND not in out


def test_codex_marker_group_composes_with_approval_and_provider(tmp_path):
    """Full composed file (panel approval + directive region with marker +
    provider) parses and carries everything — the Phase-2 super-set cell."""
    path = tmp_path / "config.toml"
    seed_codex_approval(path, auto_approve=True)
    seed_codex_config(
        path, box_config_path=_BOX_CFG, codex_cwd=_CODEX_CWD,
        model_provider=CodexModelProvider(**_NAVIGATOR),
    )
    data = tomllib.loads(path.read_text())
    assert data["approval_policy"] == "never"
    assert data["model_provider"] == "navigator"
    assert [g["hooks"][0]["command"] for g in data["hooks"]["SessionStart"]] == [
        _SESSION_START_COMMAND, _AGENT_MARKER_WRITE_COMMAND,
    ]
    assert len(data["hooks"]["state"]) == 2
