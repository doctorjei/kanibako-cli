"""Unit tests for kanibako.vscode_remote (FF-1 remote-VS-Code plumbing).

No real ssh / network anywhere: the ssh legs are asserted at the argv level, and
the RemoteEngine subprocess calls are mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako import vscode_remote as vr
from kanibako.errors import KanibakoError


@pytest.fixture(autouse=True)
def _isolate_xdg(tmp_path, monkeypatch):
    """Point every XDG base the module reads at a per-test tmp dir."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("DOCKER_CONFIG", str(tmp_path / "docker"))
    monkeypatch.delenv("TMPDIR", raising=False)
    return tmp_path


# --- mux options + runtime-dir fallback ------------------------------------

def test_mux_options_use_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    opts = vr.mux_ssh_options()
    assert opts[:2] == ["-o", "ControlMaster=auto"]
    assert opts[2] == "-o"
    assert opts[3] == f"ControlPath={tmp_path / 'run'}/kanibako-remote-%C"
    assert opts[4:] == ["-o", "ControlPersist=60"]


def test_runtime_dir_falls_back_to_tmpdir_then_tmp(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", "/var/tmp/mine")
    assert vr._runtime_dir() == "/var/tmp/mine"
    monkeypatch.delenv("TMPDIR", raising=False)
    assert vr._runtime_dir() == "/tmp"


def test_runtime_dir_ignores_relative_xdg(monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", "relative/dir")
    monkeypatch.delenv("TMPDIR", raising=False)
    assert vr._runtime_dir() == "/tmp"


# --- ssh_command quoting ----------------------------------------------------

def test_ssh_command_quotes_each_remote_arg():
    cmd = vr.ssh_command("user@host", ["kanibako", "start", "a b", "c;d"])
    assert cmd[0] == "ssh"
    # `--` precedes dest (option-injection hardening), then verbatim dest,
    # then the quoted remote args.
    dashdash = cmd.index("--")
    assert cmd[dashdash + 1] == "user@host"
    assert cmd[dashdash + 2:] == ["kanibako", "start", "'a b'", "'c;d'"]


def test_ssh_command_dest_cannot_inject_options():
    # A dest beginning with `-` lands AFTER `--`, so ssh treats it as the
    # destination, never as an option.
    cmd = vr.ssh_command("-oProxyCommand=evil", ["kanibako", "start"])
    assert cmd[cmd.index("--") + 1] == "-oProxyCommand=evil"


# --- engine_url passthrough -------------------------------------------------

@pytest.mark.parametrize(
    "dest, uid, expected",
    [
        ("host", 1000, "ssh://host/run/user/1000/podman/podman.sock"),
        ("me@host", 1001, "ssh://me@host/run/user/1001/podman/podman.sock"),
        ("me@host:2222", 0, "ssh://me@host:2222/run/user/0/podman/podman.sock"),
    ],
)
def test_engine_url_embeds_dest_verbatim(dest, uid, expected):
    assert vr.engine_url(dest, uid) == expected


# --- slug + context name ----------------------------------------------------

def test_context_slug_and_name():
    slug = vr.context_slug("me@host:2222")
    # readable part + 6-hex digest of the verbatim dest
    assert re.fullmatch(r"me-host-2222-[0-9a-f]{6}", slug)
    assert vr.remote_context_name("me@host:2222") == f"kanibako-remote-{slug}"
    # fs-safe: only [a-z0-9-]
    slug = vr.context_slug("Weird/Host_Name.example")
    assert all(c.isalnum() or c == "-" for c in slug)
    assert slug == slug.lower()


def test_context_slug_distinct_dests_never_collide():
    # The readable normalisation is lossy; the digest keeps these apart.
    assert vr.context_slug("me@host") != vr.context_slug("me/host")
    assert vr.context_slug("me@host") == vr.context_slug("me@host")


# --- connection store round-trip + greppability ----------------------------

def test_store_round_trip_and_greppable():
    name = vr.remote_context_name("me@host")
    path = vr.write_context_entry(
        name,
        url="ssh://me@host/run/user/1000/podman/podman.sock",
        dest="me@host",
        uid=1000,
    )
    assert path == vr.contexts_dir() / name
    got = vr.read_context_entry(name)
    assert got == {
        "URL": "ssh://me@host/run/user/1000/podman/podman.sock",
        "DEST": "me@host",
        "UID": "1000",
    }
    # sh-greppable: one KEY=VALUE per line, no quoting/escaping.
    raw = path.read_text()
    assert "URL=ssh://me@host/run/user/1000/podman/podman.sock\n" in raw
    assert raw.splitlines() == [
        "URL=ssh://me@host/run/user/1000/podman/podman.sock",
        "DEST=me@host",
        "UID=1000",
    ]


def test_read_missing_context_is_empty():
    assert vr.read_context_entry("kanibako-remote-nope") == {}


# --- docker context meta ----------------------------------------------------

def test_context_meta_convention_and_content(tmp_path):
    name = "kanibako-remote-me-host"
    url = "ssh://me@host/run/user/1000/podman/podman.sock"
    meta_file = vr.ensure_docker_context_meta(name, url)
    digest = hashlib.sha256(name.encode()).hexdigest()
    assert meta_file == (
        Path(os.environ["DOCKER_CONFIG"]) / "contexts" / "meta" / digest / "meta.json"
    )
    data = json.loads(meta_file.read_text())
    assert data == {
        "Name": name,
        "Metadata": {},
        "Endpoints": {"docker": {"Host": url, "SkipTLSVerify": False}},
    }


def test_context_meta_is_idempotent():
    name = "kanibako-remote-me-host"
    url = "ssh://me@host/run/user/1000/podman/podman.sock"
    meta_file = vr.ensure_docker_context_meta(name, url)
    mtime = meta_file.stat().st_mtime_ns
    # Second identical call must NOT rewrite the file.
    again = vr.ensure_docker_context_meta(name, url)
    assert again == meta_file
    assert meta_file.stat().st_mtime_ns == mtime


def test_context_meta_refuses_non_kanibako_name():
    with pytest.raises(ValueError):
        vr.ensure_docker_context_meta("myctx", "ssh://x/sock")


# --- RemoteEngine argv prefix + env PATH ------------------------------------

def test_remote_engine_argv_prefix_and_path():
    url = "ssh://me@host/run/user/1000/podman/podman.sock"
    eng = vr.RemoteEngine(url, podman="/usr/bin/podman", shim_dir=Path("/shim"))
    assert eng.argv_prefix == [
        "/usr/bin/podman", "--remote", "--ssh", "native", "--url", url,
    ]
    assert eng._env["PATH"].split(os.pathsep)[0] == "/shim"


def test_remote_engine_is_running_parses_true():
    url = "ssh://h/sock"
    eng = vr.RemoteEngine(url, podman="/usr/bin/podman", shim_dir=Path("/shim"))
    completed = MagicMock(returncode=0, stdout="true\n")
    with patch("kanibako.vscode_remote.subprocess.run", return_value=completed) as m:
        assert eng.is_running("kanibako-foo") is True
        argv = m.call_args[0][0]
        assert argv[:6] == eng.argv_prefix
        assert argv[6:] == ["inspect", "--format", "{{.State.Running}}", "kanibako-foo"]
        assert m.call_args.kwargs["env"]["PATH"].startswith("/shim")


def test_remote_engine_inspect_env_and_image():
    eng = vr.RemoteEngine("ssh://h/sock", podman="podman", shim_dir=Path("/shim"))
    env_completed = MagicMock(
        returncode=0, stdout='["FOO=bar","KANIBAKO_AGENT=claude"]',
    )
    with patch("kanibako.vscode_remote.subprocess.run", return_value=env_completed):
        assert eng.inspect_env("box", "KANIBAKO_AGENT") == "claude"
        assert eng.inspect_env("box", "MISSING") is None
    img_completed = MagicMock(returncode=0, stdout="ghcr.io/x/y:latest\n")
    with patch("kanibako.vscode_remote.subprocess.run", return_value=img_completed):
        assert eng.container_image("box") == "ghcr.io/x/y:latest"


# --- probe_remote (mocked ssh) ---------------------------------------------

def test_probe_remote_returns_uid_when_socket_present():
    completed = MagicMock(
        returncode=0, stdout="KANIBAKO_UID=1000\nKANIBAKO_SOCK=ok\n", stderr="",
    )
    with patch("kanibako.vscode_remote.subprocess.run", return_value=completed) as m:
        assert vr.probe_remote("me@host") == 1000
        # The probe rides the mux ssh leg.
        argv = m.call_args[0][0]
        assert argv[0] == "ssh"
        assert "me@host" in argv


def test_probe_remote_missing_socket_raises_with_remediation():
    completed = MagicMock(
        returncode=0, stdout="KANIBAKO_UID=1000\nKANIBAKO_SOCK=missing\n", stderr="",
    )
    with patch("kanibako.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.probe_remote("me@host")
    msg = str(exc.value)
    assert "podman.socket" in msg
    assert "enable-linger" in msg


def test_probe_remote_ssh_failure_raises():
    completed = MagicMock(returncode=255, stdout="", stderr="ssh: connect refused")
    with patch("kanibako.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.probe_remote("me@host")
    assert "me@host" in str(exc.value)


# --- wrapper + shim idempotent regen ---------------------------------------

def test_wrapper_and_shim_generated_executable_and_idempotent():
    wpath = vr.ensure_dispatch_wrapper()
    spath = vr.ensure_ssh_shim()
    assert wpath == vr.dispatch_wrapper_path()
    assert spath == vr.ssh_shim_path()
    # 0755
    assert (wpath.stat().st_mode & 0o777) == 0o755
    assert (spath.stat().st_mode & 0o777) == 0o755
    # POSIX sh, data-driven (baked store dir; no eval/source of store files).
    body = wpath.read_text()
    assert body.startswith("#!/bin/sh")
    assert str(vr.contexts_dir()) in body
    assert "eval" not in body
    # Data-driven: store files are grepped (sed), never sourced.
    assert "source " not in body
    assert "sed -n 's/^URL=//p'" in body
    # Idempotent: a second ensure does not rewrite.
    assert vr._write_script(wpath, vr._wrapper_content()) is False
    assert vr._write_script(spath, vr._shim_content()) is False


def test_wrapper_rewritten_when_content_changes():
    wpath = vr.ensure_dispatch_wrapper()
    wpath.write_text("#!/bin/sh\n# stale\n")
    assert vr.ensure_dispatch_wrapper() == wpath
    assert "kanibako vscode-remote dispatch wrapper" in wpath.read_text()
