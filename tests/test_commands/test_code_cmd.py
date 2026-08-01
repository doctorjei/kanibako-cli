"""Tests for kanibako.commands.code_cmd."""

from __future__ import annotations

import argparse
import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.commands.code_cmd import (
    _CodeShimError,
    _attach_uri,
    _resolve_code_cli,
    run_code,
)

# Pinned expectation for container name ``kanibako-foo``:
#   json  = {"containerName":"kanibako-foo"}   (BARE name — podman rejects the
#           docker-convention "/<name>" on local CLI and remote API alike;
#           live-confirmed on a Raiju local attach 2026-07-09)
#   hex   = binascii.hexlify(json.encode()).decode()
#   uri   = vscode-remote://attached-container+<hex>/home/agent/workspace
_EXPECTED_HEX = (
    "7b22636f6e7461696e65724e616d65223a226b616e6962616b6f2d666f6f227d"
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
    # The RUNNING container's image keys the (image-shared) attached config.
    rt.container_image.return_value = "ghcr.io/doctorjei/kanibako-oci:latest"
    return rt


@pytest.fixture(autouse=True)
def _isolate_seed_path(tmp_path):
    """Never write the attached-container config into the real user config home.

    Redirects the seed path to a per-test tmp dir so exercising run_code (which
    best-effort seeds a config) can't pollute ``~/.config`` on the test machine.
    The fake ignores the (image-ref, config_home) args and returns a fixed path.
    """
    def _fake_path(image_ref, config_home):
        return tmp_path / "imageConfigs" / "box.json"

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


# --- in-container remote-shim detection (_resolve_code_cli) -----------------

def _make_exec(path):
    """Create an executable file at *path* (parents made)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return path


def test_resolve_code_cli_refuses_shim_on_path(tmp_path, monkeypatch):
    """(t1 core) A remote-cli shim resolved on PATH raises _CodeShimError."""
    shim = _make_exec(
        tmp_path / ".vscode-server" / "bin" / "abc" / "bin" / "remote-cli" / "code"
    )
    monkeypatch.delenv("VSCODE_IPC_HOOK_CLI", raising=False)
    monkeypatch.setenv("PATH", str(shim.parent))
    with pytest.raises(_CodeShimError):
        _resolve_code_cli()


def test_resolve_code_cli_refuses_symlink_to_shim(tmp_path, monkeypatch):
    """(t4) A PATH symlink pointing INTO .vscode-server is detected via resolve()."""
    real_shim = _make_exec(
        tmp_path / ".vscode-server" / "bin" / "abc" / "bin" / "remote-cli" / "code"
    )
    bindir = tmp_path / "bin"
    bindir.mkdir()
    link = bindir / "code"
    link.symlink_to(real_shim)
    monkeypatch.delenv("VSCODE_IPC_HOOK_CLI", raising=False)
    monkeypatch.setenv("PATH", str(bindir))
    with pytest.raises(_CodeShimError):
        _resolve_code_cli()


def test_resolve_code_cli_real_binary_proceeds(tmp_path, monkeypatch):
    """(t3) A real code at a normal location resolves fine (returns its path)."""
    real = _make_exec(tmp_path / "usr" / "bin" / "code")
    monkeypatch.delenv("VSCODE_IPC_HOOK_CLI", raising=False)
    monkeypatch.setenv("PATH", str(real.parent))
    assert _resolve_code_cli() == str(real)


def test_resolve_code_cli_ipc_set_real_binary_does_not_refuse(tmp_path, monkeypatch):
    """(t5) VSCODE_IPC_HOOK_CLI set but a REAL code on PATH must NOT refuse.

    The env var leaking into a box shell must never, on its own, block a
    legitimate host-style code binary that resolves outside a shim tree.
    """
    real = _make_exec(tmp_path / ".local" / "share" / "code" / "bin" / "code")
    monkeypatch.setenv("VSCODE_IPC_HOOK_CLI", "/tmp/vscode-ipc.sock")
    monkeypatch.setenv("PATH", str(real.parent))
    assert _resolve_code_cli() == str(real)


def test_resolve_code_cli_ipc_plus_remote_cli_refuses(tmp_path, monkeypatch):
    """Prong (b) POSITIVE: IPC var set + a remote-cli dir OUTSIDE any known
    server tree still refuses (the belt for unknown server-dir layouts)."""
    shim = _make_exec(
        tmp_path / ".some-server" / "bin" / "abc" / "bin" / "remote-cli" / "code"
    )
    monkeypatch.setenv("VSCODE_IPC_HOOK_CLI", "/tmp/vscode-ipc.sock")
    monkeypatch.setenv("PATH", str(shim.parent))
    with pytest.raises(_CodeShimError):
        _resolve_code_cli()


@pytest.mark.parametrize("server_dir", [
    ".vscode-server-insiders", ".vscode-server-oss", ".cursor-server",
])
def test_resolve_code_cli_refuses_variant_server_trees(
    tmp_path, monkeypatch, server_dir,
):
    """Prong (a) covers the known server-dir VARIANTS even with no IPC var
    (the stale-shell case for Insiders/OSS/Cursor)."""
    shim = _make_exec(
        tmp_path / server_dir / "bin" / "abc" / "bin" / "remote-cli" / "code"
    )
    monkeypatch.delenv("VSCODE_IPC_HOOK_CLI", raising=False)
    monkeypatch.setenv("PATH", str(shim.parent))
    with pytest.raises(_CodeShimError):
        _resolve_code_cli()


def test_resolve_code_cli_missing_returns_none(tmp_path, monkeypatch):
    """No code on PATH → None (callers print their own 'missing' guidance)."""
    monkeypatch.delenv("VSCODE_IPC_HOOK_CLI", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    assert _resolve_code_cli() is None


def test_local_run_code_refuses_shim(mock_runtime, capsys):
    """(t1) The LOCAL run_code path refuses rc=1 with the shim message and never
    launches (or auto-starts) when the resolved code is the remote shim."""
    stack, _proj = _patched(mock_runtime)
    shim = "/home/u/.vscode-server/bin/abc/bin/remote-cli/code"
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.code_cmd.shutil.which", return_value=shim),
        patch("kanibako.commands.start.start_detached") as m_start,
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
    assert rc == 1
    m_run.assert_not_called()
    m_start.assert_not_called()
    err = capsys.readouterr().err
    assert "remote shim" in err
    assert "from the host instead" in err


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


def test_box_not_running_auto_starts_then_attaches(mock_runtime, capsys):
    """Phase 4: a stopped box is AUTO-STARTED (detached keep-alive) then attached."""
    # First is_running check (before auto-start) is False; after start_detached
    # flips the box up, the re-check returns True and the attach proceeds.
    mock_runtime.is_running.side_effect = [False, True]
    stack, _proj = _patched(mock_runtime)

    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.start.start_detached", return_value=0,
        ) as m_start,
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 0
        # Auto-start ran, then VS Code launched (attach).
        m_start.assert_called_once()
        m_run.assert_called_once_with(
            ["/usr/bin/code", "--folder-uri", _EXPECTED_URI]
        )
        err = capsys.readouterr().err
        assert "starting it in the background" in err


def test_box_running_does_not_auto_start(mock_runtime):
    """An already-running box is attached directly — start_detached is NOT called."""
    # mock_runtime.is_running defaults to True (fixture).
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.start.start_detached") as m_start,
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 0
        m_start.assert_not_called()
        m_run.assert_called_once_with(
            ["/usr/bin/code", "--folder-uri", _EXPECTED_URI]
        )


def test_auto_start_failure_aborts(mock_runtime, capsys):
    """If the detached auto-start fails, `code` aborts and does NOT launch VS Code."""
    mock_runtime.is_running.return_value = False
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.start.start_detached", return_value=1,
        ) as m_start,
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())
        assert rc == 1
        m_start.assert_called_once()
        m_run.assert_not_called()
        err = capsys.readouterr().err
        assert "could not auto-start" in err


def test_code_not_on_path_before_auto_start(mock_runtime, capsys):
    """A missing `code` CLI fails fast — BEFORE any auto-start leaves a box behind."""
    mock_runtime.is_running.return_value = False
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch("kanibako.commands.start.start_detached") as m_start,
        patch("kanibako.commands.code_cmd.shutil.which", return_value=None),
    ):
        rc = run_code(_args())
        assert rc == 1
        m_start.assert_not_called()
        err = capsys.readouterr().err
        assert "PATH" in err


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
    seed_path = tmp_path / "imageConfigs" / "box.json"
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
    # IMAGE-keyed content: no remoteUser (VS Code infers the user).
    written = json.loads(seed_path.read_text())
    assert written == {
        "extensions": ["anthropic.claude-code"],
        "workspaceFolder": "/home/agent/workspace",
    }


def test_uses_running_image_to_key_path(mock_runtime, _isolate_seed_path):
    """The IMAGE-keyed path is resolved from the RUNNING container's image."""
    stack, _proj = _patched(mock_runtime)
    captured: dict[str, str] = {}

    def _capture_path(image_ref, config_home):
        captured["image_ref"] = image_ref
        return _isolate_seed_path / "imageConfigs" / "box.json"

    mock_runtime.container_image.return_value = "ghcr.io/doctorjei/custom:v9"
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.code_cmd._resolve_box_vscode_extension",
            return_value="anthropic.claude-code",
        ),
        patch(
            "kanibako.commands.code_cmd.attached_container_config_path",
            _capture_path,
        ),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once()
    mock_runtime.container_image.assert_called_once_with("kanibako-foo")
    assert captured["image_ref"] == "ghcr.io/doctorjei/custom:v9"


def test_seed_failure_still_launches(mock_runtime):
    """A seeding failure NEVER blocks the ``code`` launch (zero-launch-delta)."""
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.commands.code_cmd.seed_attached_container_config",
            side_effect=RuntimeError("boom"),
        ),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once_with(["/usr/bin/code", "--folder-uri", _EXPECTED_URI])


def test_image_resolution_failure_still_launches(mock_runtime):
    """If the box image can't be resolved, seeding is skipped but ``code`` launches."""
    # No running-container image AND the merged-config fallback blows up.
    mock_runtime.container_image.return_value = None
    stack, _proj = _patched(mock_runtime)
    with (
        stack[0], stack[1], stack[2], stack[3], stack[4],
        patch(
            "kanibako.settings.config.load_merged_config",
            side_effect=RuntimeError("no config"),
        ),
        patch(
            "kanibako.commands.code_cmd.seed_attached_container_config",
        ) as m_seed,
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args())

    assert rc == 0
    m_run.assert_called_once_with(["/usr/bin/code", "--folder-uri", _EXPECTED_URI])
    # Image unresolved → we never attempt the (image-keyed) seed.
    m_seed.assert_not_called()


def test_stamp_first_wins_over_cascade(mock_runtime, _isolate_seed_path):
    """The RUNNING box's KANIBAKO_AGENT stamp resolves the extension — the
    create-time resolve_agent cascade is NOT consulted (and would raise here)."""
    tmp_path = _isolate_seed_path
    seed_path = tmp_path / "imageConfigs" / "box.json"

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
            "kanibako.settings.config.resolve_agent",
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


# --- `code --remote` on an ABSENT remote box: surface the REMOTE "no box" ------
def test_remote_no_box_surfaces_remote_create_hint(capsys):
    """Explicit-create (Jei 2026-07-11g): when the REMOTE `kanibako start` errors
    with "no box" (the remote box does not exist), `code --remote` surfaces it as a
    REMOTE-box problem — create must be run ON THE REMOTE host — not a generic
    failure."""
    from types import SimpleNamespace
    from pathlib import Path

    from kanibako.commands.code_cmd import _run_code_remote

    dest = "myhost"
    args = argparse.Namespace(project="webapp", box=None, remote=dest)

    failed = SimpleNamespace(
        returncode=1,
        stderr="Error: no box at /home/u/webapp. To create a new box, run 'kanibako create webapp'",
        stdout="",
    )

    with (
        patch("kanibako.commands.code_cmd._resolve_code_cli", return_value="/usr/bin/code"),
        patch("kanibako.commands.code_cmd.shutil.which", return_value="/usr/bin/podman"),
        patch("kanibako.commands.code_cmd._wire_docker_path", return_value=None),
        patch("kanibako.vscode.vscode_remote.dispatch_wrapper_path", return_value=Path("/w")),
        patch("kanibako.vscode.vscode_remote.ensure_dispatch_wrapper"),
        patch("kanibako.vscode.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode.vscode_remote.remote_context_name", return_value="ctx"),
        patch("kanibako.vscode.vscode_remote.tunnel_socket_path", return_value=Path("/tmp/s.sock")),
        patch("kanibako.vscode.vscode_remote.engine_url", return_value="unix:///tmp/s.sock"),
        patch("kanibako.vscode.vscode_remote.remote_socket_path", return_value="/run/x.sock"),
        patch("kanibako.vscode.vscode_remote.ensure_docker_context_meta"),
        patch("kanibako.vscode.vscode_remote.write_context_entry"),
        patch("kanibako.vscode.vscode_remote.ensure_tunnel"),
        patch("kanibako.vscode.vscode_remote.RemoteEngine", return_value=MagicMock()),
        patch("kanibako.vscode.vscode_remote.preflight_engine"),
        patch("kanibako.vscode.vscode_remote.remote_run_kanibako", return_value=failed),
    ):
        rc = _run_code_remote(args, dest)

    assert rc == 1
    err = capsys.readouterr().err
    # The verbatim remote stderr is shown AND a remote-oriented hint is added.
    assert "no box at /home/u/webapp" in err
    assert "does not exist on the remote host" in err
    assert f"ssh {dest} kanibako create webapp" in err
