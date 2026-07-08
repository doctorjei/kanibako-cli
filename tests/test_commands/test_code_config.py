"""Tests for kanibako.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
from pathlib import Path

from kanibako.vscode_config import (
    _encode_image_ref,
    attached_container_config_path,
    clear_bypass_permissions,
    clear_claude_bypass_permissions,
    deliver_claude_panel_permissions,
    merge_attached_container_config,
    merge_bypass_permissions,
    seed_attached_container_config,
    seed_claude_bypass_permissions,
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
