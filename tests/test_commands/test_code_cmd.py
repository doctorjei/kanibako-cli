"""Tests for kanibako.commands.code_cmd."""

from __future__ import annotations

import argparse
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
