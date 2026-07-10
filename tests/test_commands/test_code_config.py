"""Tests for kanibako.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from kanibako.vscode_config import (
    _CODEX_EVENT_KEY,
    _SESSION_START_COMMAND,
    _SESSION_START_MATCHER,
    _encode_image_ref,
    attached_container_config_path,
    clear_bypass_permissions,
    clear_claude_bypass_permissions,
    codex_trusted_hash,
    deliver_claude_panel_permissions,
    deliver_directive_session_hook,
    merge_attached_container_config,
    merge_bypass_permissions,
    merge_codex_config,
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
    """First seed writes; a re-run is a no-op (claude settings.json)."""
    path = tmp_path / "settings.json"
    assert seed_session_start_hook(path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": {"SessionStart": [_managed_group()]}}
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
    assert data["hooks"]["SessionStart"] == [_managed_group()]


def test_seed_session_start_tolerates_corrupt(tmp_path):
    """A corrupt file degrades to {} then merges (never raises)."""
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert seed_session_start_hook(path) is True
    data = json.loads(path.read_text())
    assert data == {"hooks": {"SessionStart": [_managed_group()]}}


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
    assert data["hooks"]["SessionStart"] == [_managed_group()]


def test_deliver_directive_other_agent_is_inert(tmp_path):
    assert deliver_directive_session_hook(
        agent_name="goose",
        config_root=tmp_path,
        box_codex_config_path=_BOX_CFG,
        codex_cwd=_CODEX_CWD,
        auto_approve=True,
    ) is False
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / ".claude").exists()
