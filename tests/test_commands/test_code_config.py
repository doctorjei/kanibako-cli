"""Tests for kanibako.vscode_config (attached-container config generation)."""

from __future__ import annotations

import json
from pathlib import Path

from kanibako.vscode_config import (
    attached_container_config_path,
    build_attached_container_config,
    seed_attached_container_config,
)


def test_build_config_with_extensions():
    """All three keys present, extensions carried through verbatim."""
    cfg = build_attached_container_config(
        workspace_folder="/home/agent/workspace",
        remote_user="agent",
        extensions=["anthropic.claude-code"],
    )
    assert cfg == {
        "workspaceFolder": "/home/agent/workspace",
        "remoteUser": "agent",
        "extensions": ["anthropic.claude-code"],
    }


def test_build_config_without_extensions():
    """extensions is ALWAYS included (empty list) for stable, deterministic output."""
    cfg = build_attached_container_config(
        workspace_folder="/home/agent/workspace",
        remote_user="agent",
        extensions=[],
    )
    assert cfg == {
        "workspaceFolder": "/home/agent/workspace",
        "remoteUser": "agent",
        "extensions": [],
    }
    assert "extensions" in cfg


def test_build_config_copies_extensions_list():
    """The returned list is a copy — mutating the input must not leak in."""
    src = ["a"]
    cfg = build_attached_container_config(
        workspace_folder="/ws", remote_user="agent", extensions=src,
    )
    src.append("b")
    assert cfg["extensions"] == ["a"]


def test_config_path_is_exact():
    """Pin the FULL name-level host path VS Code reads on attach."""
    path = attached_container_config_path("kanibako-foo", Path("/home/u/.config"))
    assert path == Path(
        "/home/u/.config/Code/User/globalStorage/"
        "ms-vscode-remote.remote-containers/nameConfigs/kanibako-foo.json"
    )


def test_seed_writes_when_absent(tmp_path):
    """seed writes pretty JSON that round-trips back to the config, returns True."""
    path = tmp_path / "nameConfigs" / "kanibako-foo.json"
    cfg = build_attached_container_config(
        workspace_folder="/home/agent/workspace",
        remote_user="agent",
        extensions=["anthropic.claude-code"],
    )
    wrote = seed_attached_container_config(path, cfg)
    assert wrote is True
    assert path.exists()
    assert json.loads(path.read_text()) == cfg


def test_seed_does_not_clobber_when_present(tmp_path):
    """An existing (user-edited) config is left intact and seed returns False."""
    path = tmp_path / "kanibako-foo.json"
    path.write_text('{"sentinel": true}')
    cfg = build_attached_container_config(
        workspace_folder="/ws", remote_user="agent", extensions=[],
    )
    wrote = seed_attached_container_config(path, cfg)
    assert wrote is False
    assert json.loads(path.read_text()) == {"sentinel": True}
