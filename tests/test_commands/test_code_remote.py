"""Tests for `kanibako code --remote` (FF-1 remote VS Code flow)."""

from __future__ import annotations

import argparse
import binascii
import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako import vscode_remote as vr
from kanibako.commands.code_cmd import _attach_uri, run_code

# A non-proxied `ssh -G` resolution used by the flow tests (real `ssh -G` is
# never run in unit tests).
_RESOLVED_OK = vr.ResolvedDest("realhost", "me", "22", False)


def _args(project=None, box=None, remote=None) -> argparse.Namespace:
    return argparse.Namespace(project=project, box=box, remote=remote)


# --- _attach_uri context extension -----------------------------------------

def test_attach_uri_local_payload_unchanged():
    """Without a context, the payload is byte-identical to the local URI."""
    uri = _attach_uri("kanibako-foo")
    hexpart = uri.split("attached-container+", 1)[1].split("/home", 1)[0]
    payload = binascii.unhexlify(hexpart).decode()
    assert payload == '{"containerName":"/kanibako-foo"}'


def test_attach_uri_embeds_context_token():
    uri = _attach_uri("kanibako-foo", context="kanibako-remote-h")
    hexpart = uri.split("attached-container+", 1)[1].split("/home", 1)[0]
    payload = binascii.unhexlify(hexpart).decode()
    # Remote payload: BARE name (no leading slash — remote podman rejects the
    # docker-style "/name"; e2e-confirmed) + the context token.
    assert payload == (
        '{"containerName":"kanibako-foo",'
        '"settings":{"context":"kanibako-remote-h"}}'
    )


# --- --remote requires a box -----------------------------------------------

def test_remote_without_box_errors(capsys):
    rc = run_code(_args(remote="host"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "requires a box" in err


def test_remote_missing_code_cli_errors(capsys):
    with patch("kanibako.commands.code_cmd.shutil.which", return_value=None):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    assert "code" in capsys.readouterr().err


def test_remote_missing_local_podman_errors(capsys):
    def _which(name):
        return "/usr/bin/code" if name == "code" else None

    with patch("kanibako.commands.code_cmd.shutil.which", side_effect=_which):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    assert "podman" in capsys.readouterr().err


# --- dockerPath settings prompt paths --------------------------------------

@pytest.fixture
def _both_present():
    """`code` + `podman` both on PATH."""
    with patch(
        "kanibako.commands.code_cmd.shutil.which",
        side_effect=lambda n: f"/usr/bin/{n}",
    ):
        yield


def _settings_path(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    return tmp_path / "cfg" / "Code" / "User" / "settings.json"


def test_settings_prompt_accept_updates_and_proceeds(
    tmp_path, monkeypatch, _both_present, capsys,
):
    sp = _settings_path(tmp_path, monkeypatch)
    from kanibako import vscode_remote as vr
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    wrapper = str(vr.dispatch_wrapper_path())

    # Accept the prompt; then stub the rest of the remote flow so it stops early.
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="y"),
        patch(
            "kanibako.vscode_remote.probe_remote",
            side_effect=RuntimeError("stop-after-wire"),
        ),
    ):
        with pytest.raises(RuntimeError, match="stop-after-wire"):
            run_code(_args(project="mybox", remote="host"))

    written = json.loads(sp.read_text())
    assert written["dev.containers.dockerPath"] == wrapper


def test_settings_prompt_decline_aborts(
    tmp_path, monkeypatch, _both_present, capsys,
):
    _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with (
        patch("sys.stdin.isatty", return_value=True),
        patch("builtins.input", return_value="n"),
    ):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    assert "dev.containers.dockerPath" in capsys.readouterr().err


def test_settings_non_tty_prints_snippet_and_aborts(
    tmp_path, monkeypatch, _both_present, capsys,
):
    _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    with patch("sys.stdin.isatty", return_value=False):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    assert "dev.containers.dockerPath" in capsys.readouterr().err


def test_settings_unparseable_never_clobbers(
    tmp_path, monkeypatch, _both_present, capsys,
):
    sp = _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    sp.parent.mkdir(parents=True, exist_ok=True)
    original = "{ this is not json ][ "
    sp.write_text(original)
    with patch("sys.stdin.isatty", return_value=True):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    # File is NOT clobbered.
    assert sp.read_text() == original
    assert "could not be read" in capsys.readouterr().err


def test_settings_already_wired_jsonc_proceeds(
    tmp_path, monkeypatch, _both_present,
):
    # A JSONC settings.json (comments) that ALREADY points at the wrapper must
    # proceed — the wired-check read is JSONC-tolerant (Editor MAJOR-1).
    sp = _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from kanibako import vscode_remote as vr
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(
        "{\n"
        "  // my settings\n"
        f'  "dev.containers.dockerPath": {json.dumps(str(vr.dispatch_wrapper_path()))},\n'
        "}\n"
    )
    with patch(
        "kanibako.vscode_remote.probe_remote",
        side_effect=RuntimeError("proceeded"),
    ):
        with pytest.raises(RuntimeError, match="proceeded"):
            run_code(_args(project="mybox", remote="host"))


def test_settings_jsonc_needing_write_aborts_never_rewrites(
    tmp_path, monkeypatch, _both_present, capsys,
):
    # A JSONC settings.json NOT yet wired: rewriting would drop the comments →
    # abort with the manual snippet, file untouched.
    sp = _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    sp.parent.mkdir(parents=True, exist_ok=True)
    original = '{\n  // my settings\n  "editor.fontSize": 14,\n}\n'
    sp.write_text(original)
    with patch("sys.stdin.isatty", return_value=True):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    assert sp.read_text() == original
    err = capsys.readouterr().err
    assert "refusing to rewrite" in err
    assert "dev.containers.dockerPath" in err


def test_settings_already_wired_proceeds(
    tmp_path, monkeypatch, _both_present,
):
    sp = _settings_path(tmp_path, monkeypatch)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    from kanibako import vscode_remote as vr
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(
        {"dev.containers.dockerPath": str(vr.dispatch_wrapper_path())}
    ))
    # No prompt needed → flow proceeds until probe (stubbed to stop).
    with patch(
        "kanibako.vscode_remote.probe_remote",
        side_effect=RuntimeError("proceeded"),
    ):
        with pytest.raises(RuntimeError, match="proceeded"):
            run_code(_args(project="mybox", remote="host"))


# --- full remote happy path + failure surfacing ----------------------------

def _wire_ok(tmp_path, monkeypatch):
    """Pre-wire settings.json so _wire_docker_path is a no-op."""
    from kanibako import vscode_remote as vr
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    sp = tmp_path / "cfg" / "Code" / "User" / "settings.json"
    sp.parent.mkdir(parents=True, exist_ok=True)
    sp.write_text(json.dumps(
        {"dev.containers.dockerPath": str(vr.dispatch_wrapper_path())}
    ))


def test_remote_lifecycle_failure_surfaces_remote_stderr(
    tmp_path, monkeypatch, _both_present, capsys,
):
    _wire_ok(tmp_path, monkeypatch)
    failed = MagicMock(returncode=1, stdout="", stderr="bash: kanibako: command not found")
    with (
        patch("kanibako.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode_remote.resolve_ssh_dest", return_value=_RESOLVED_OK),
        patch("kanibako.vscode_remote.preflight_engine"),
        patch("kanibako.vscode_remote.remote_run_kanibako", return_value=failed),
    ):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    err = capsys.readouterr().err
    assert "command not found" in err
    assert "kanibako" in err  # the install hint


def test_remote_happy_path_uses_context_uri_and_seeds_remote_image(
    tmp_path, monkeypatch, _both_present, capsys,
):
    _wire_ok(tmp_path, monkeypatch)
    started = MagicMock(returncode=0, stdout="chatter\nkanibako-mybox\n", stderr="")

    engine = MagicMock()
    engine.running_with_stderr.return_value = (True, "")
    engine.version.return_value = MagicMock(returncode=0, stderr="")
    engine.container_image.return_value = "ghcr.io/doctorjei/remote-img:latest"
    engine.inspect_env.return_value = "claude"

    seen = {}

    def _seed(path, *, workspace_folder, extension):
        seen["image_keyed_path"] = str(path)
        seen["extension"] = extension
        return True

    with (
        patch("kanibako.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode_remote.resolve_ssh_dest", return_value=_RESOLVED_OK),
        patch("kanibako.vscode_remote.remote_run_kanibako", return_value=started),
        patch("kanibako.vscode_remote.RemoteEngine", return_value=engine),
        patch("kanibako.vscode_remote.ensure_docker_context_meta"),
        patch(
            "kanibako.commands.code_cmd._extension_for_agent",
            return_value="anthropic.claude-code",
        ),
        patch(
            "kanibako.commands.code_cmd.seed_attached_container_config",
            side_effect=_seed,
        ),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args(project="mybox", remote="host"))

    assert rc == 0
    # The remote box cname (last non-empty stdout line) is what we attach to.
    m_run.assert_called_once()
    launched = m_run.call_args[0][0]
    assert launched[0] == "/usr/bin/code"
    assert launched[1] == "--folder-uri"
    uri = launched[2]
    hexpart = uri.split("attached-container+", 1)[1].split("/home", 1)[0]
    payload = json.loads(binascii.unhexlify(hexpart).decode())
    # BARE name for remote (remote podman rejects the docker-style "/name" —
    # e2e-confirmed); the local URI keeps the leading slash.
    assert payload["containerName"] == "kanibako-mybox"
    from kanibako import vscode_remote as vr
    assert payload["settings"]["context"] == vr.remote_context_name("host")
    # Seed keyed by the REMOTE image, extension from the REMOTE stamp.
    assert "remote-img" in seen["image_keyed_path"]
    assert seen["extension"] == "anthropic.claude-code"
    engine.inspect_env.assert_called_with("kanibako-mybox", "KANIBAKO_AGENT")
    # Engine pre-flight ran before the lifecycle leg.
    engine.version.assert_called_once()
    # The stored engine URL is the RESOLVED form (user@host:port) the wrapper
    # dials; the original opaque dest is kept as DEST.
    entry = vr.read_context_entry(vr.remote_context_name("host"))
    assert entry["URL"] == "ssh://me@realhost:22/run/user/1000/podman/podman.sock"
    assert entry["DEST"] == "host"


def test_remote_box_not_running_after_start_errors(
    tmp_path, monkeypatch, _both_present, capsys,
):
    _wire_ok(tmp_path, monkeypatch)
    started = MagicMock(returncode=0, stdout="kanibako-mybox\n", stderr="")
    engine = MagicMock()
    engine.version.return_value = MagicMock(returncode=0, stderr="")
    # is_running False AND the underlying podman inspect stderr (item 6): the
    # "did not come up" message must carry it, not swallow it.
    engine.running_with_stderr.return_value = (
        False, 'Error: no such container "kanibako-mybox"',
    )
    with (
        patch("kanibako.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode_remote.resolve_ssh_dest", return_value=_RESOLVED_OK),
        patch("kanibako.vscode_remote.remote_run_kanibako", return_value=started),
        patch("kanibako.vscode_remote.RemoteEngine", return_value=engine),
        patch("kanibako.vscode_remote.ensure_docker_context_meta"),
        patch("kanibako.commands.code_cmd.subprocess.run") as m_run,
    ):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    m_run.assert_not_called()
    err = capsys.readouterr().err
    assert "did not come up" in err
    assert "no such container" in err  # podman's own inspect stderr surfaced


# --- rc7: proxied dest refusal + engine pre-flight -------------------------

def test_remote_proxied_dest_refused_before_lifecycle(
    tmp_path, monkeypatch, _both_present, capsys,
):
    # A dest resolving through a ProxyJump/ProxyCommand cannot be dialed by
    # podman's built-in ssh → refuse cleanly, before the lifecycle leg (item 2).
    _wire_ok(tmp_path, monkeypatch)
    proxied = vr.ResolvedDest("realhost", "me", "22", True)
    with (
        patch("kanibako.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode_remote.resolve_ssh_dest", return_value=proxied),
        patch("kanibako.vscode_remote.remote_run_kanibako") as m_life,
    ):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    m_life.assert_not_called()
    err = capsys.readouterr().err
    assert "ProxyJump" in err
    assert "Remote-SSH" in err  # Flavor A fallback suggested


def test_remote_preflight_failure_surfaces_podman_stderr(
    tmp_path, monkeypatch, _both_present, capsys,
):
    # A dead engine leg (undialable host) is caught by the pre-flight, which
    # surfaces podman's own stderr — before the lifecycle leg runs (item 5).
    from kanibako.errors import KanibakoError

    _wire_ok(tmp_path, monkeypatch)
    with (
        patch("kanibako.vscode_remote.probe_remote", return_value=1000),
        patch("kanibako.vscode_remote.resolve_ssh_dest", return_value=_RESOLVED_OK),
        patch("kanibako.vscode_remote.RemoteEngine"),
        patch(
            "kanibako.vscode_remote.preflight_engine",
            side_effect=KanibakoError(
                "Could not reach the remote podman engine.\n  ... no such host"
            ),
        ),
        patch("kanibako.vscode_remote.remote_run_kanibako") as m_life,
    ):
        rc = run_code(_args(project="mybox", remote="host"))
    assert rc == 1
    m_life.assert_not_called()  # pre-flight aborts before the lifecycle leg
    assert "no such host" in capsys.readouterr().err
