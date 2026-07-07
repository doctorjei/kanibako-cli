"""Tests for kanibako.commands.code_cmd."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.code_cmd import _attach_uri, run_code

# Pinned expectation for container name ``kanibako-foo``:
#   json  = {"containerName":"/kanibako-foo"}
#   hex   = binascii.hexlify(json.encode()).decode()
#   uri   = vscode-remote://attached-container+<hex>/home/agent/workspace
_EXPECTED_HEX = (
    "7b22636f6e7461696e65724e616d65223a222f6b616e6962616b6f2d666f6f227d"
)
_EXPECTED_URI = (
    f"vscode-remote://attached-container+{_EXPECTED_HEX}/home/agent/workspace"
)


def _args(project=None, box=None) -> argparse.Namespace:
    return argparse.Namespace(project=project, box=box)


@pytest.fixture
def mock_runtime():
    rt = MagicMock()
    rt.is_running.return_value = True
    return rt


@pytest.fixture(autouse=True)
def _isolate_seed_path(tmp_path):
    """Never write the attached-container config into the real user config home.

    Redirects the seed path to a per-test tmp dir so exercising run_code (which
    best-effort seeds a config) can't pollute ``~/.config`` on the test machine.
    """
    def _fake_path(container_name, config_home):
        return tmp_path / "nameConfigs" / f"{container_name}.json"

    with patch(
        "kanibako.commands.code_cmd.attached_container_config_path", _fake_path,
    ):
        yield tmp_path


def _patched(runtime, cname="kanibako-foo", name="foo"):
    """Patch the resolve/runtime chain used by run_code."""
    proj = MagicMock()
    proj.name = name
    stack = [
        patch("kanibako.commands.code_cmd.ContainerRuntime", return_value=runtime),
        patch("kanibako.commands.code_cmd.load_config"),
        patch("kanibako.commands.code_cmd.load_std_paths"),
        patch("kanibako.commands.code_cmd.resolve_box_target", return_value=proj),
        patch("kanibako.commands.code_cmd.container_name_for", return_value=cname),
    ]
    return stack, proj


def test_attach_uri_exact():
    """URI construction is exact and deterministic for a known container name."""
    assert _attach_uri("kanibako-foo") == _EXPECTED_URI


def test_happy_path(mock_runtime, capsys):
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 0
        m_run.assert_called_once_with(
            ["/usr/bin/code", "--folder-uri", _EXPECTED_URI]
        )
        out = capsys.readouterr().out
        assert "Opening VS Code attached to box 'foo'" in out


def test_box_not_running(mock_runtime, capsys):
    mock_runtime.is_running.return_value = False
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "is not running" in err
        assert "kanibako start foo --persistent" in err


def test_code_not_on_path(mock_runtime, capsys):
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.code_cmd.shutil.which", return_value=None),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 1
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "code" in err
        assert "PATH" in err
        assert "Dev Containers" in err


def test_seeds_config_before_launch(mock_runtime, _isolate_seed_path):
    """run_code seeds the attached-container config BEFORE invoking ``code``."""
    tmp_path = _isolate_seed_path
    seed_path = tmp_path / "nameConfigs" / "kanibako-foo.json"
    seen: dict[str, bool] = {}

    def _run_side_effect(argv):
        seen["existed_at_launch"] = seed_path.exists()

    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.code_cmd._resolve_box_vscode_extension",
            return_value="anthropic.claude-code",
        ),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch(
            "kanibako.commands.code_cmd.subprocess.run",
            side_effect=_run_side_effect,
        ) as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once()
    # The config file existed at the moment ``code`` was launched.
    assert seen["existed_at_launch"] is True
    written = json.loads(seed_path.read_text())
    assert written == {
        "workspaceFolder": "/home/agent/workspace",
        "remoteUser": "agent",
        "extensions": ["anthropic.claude-code"],
    }


def test_seed_failure_still_launches(mock_runtime):
    """A seeding failure NEVER blocks the ``code`` launch (zero-launch-delta)."""
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.code_cmd.build_attached_container_config",
            side_effect=RuntimeError("boom"),
        ),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once_with(["/usr/bin/code", "--folder-uri", _EXPECTED_URI])


def test_stamp_first_wins_over_cascade(mock_runtime, _isolate_seed_path):
    """The RUNNING box's KANIBAKO_AGENT stamp resolves the extension — the
    create-time resolve_agent cascade is NOT consulted (and would raise here)."""
    tmp_path = _isolate_seed_path
    seed_path = tmp_path / "nameConfigs" / "kanibako-foo.json"

    # The box is stamped as running "claude".
    mock_runtime.inspect_env.return_value = "claude"

    # A fake claude target whose descriptor advertises the VS Code extension
    # (the real descriptor is read from the MAIN-tree yaml under the editable
    # finder, so we inject the plugin resolution to keep the test hermetic).
    fake_desc = MagicMock()
    fake_desc.vscode_extension = "anthropic.claude-code"
    fake_target = MagicMock()
    fake_target.descriptor = fake_desc

    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        # resolve_target (imported inside _extension_for_agent) returns our fake.
        patch("kanibako.targets.resolve_target", return_value=fake_target),
        # If the code ever fell through to the cascade, this would blow up →
        # extension None → extensions=[].  Asserting the ext proves stamp-first.
        patch(
            "kanibako.config.resolve_agent",
            side_effect=AssertionError("cascade must not run when a stamp exists"),
        ),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once()
    mock_runtime.inspect_env.assert_called_once_with("kanibako-foo", "KANIBAKO_AGENT")
    written = json.loads(seed_path.read_text())
    assert written["extensions"] == ["anthropic.claude-code"]
