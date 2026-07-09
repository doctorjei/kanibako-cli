"""Dispatch tests that run the REAL generated wrapper via /bin/sh.

Fake ``podman`` + fake ``ssh`` scripts on a temp PATH log their argv; NO real
ssh / network anywhere.  These verify the wrapper's routing, token-strip, and
audit-log behavior end-to-end against the actual generated POSIX-sh file.
"""

from __future__ import annotations

import socket
import subprocess
from pathlib import Path

import pytest

from kanibako import vscode_remote as vr

# The stored context name for the fixture's dest (slug carries a digest of the
# verbatim dest, so derive it from the API rather than hardcoding).
_CTX = vr.remote_context_name("me@host")


def _make_socket_file(path: Path) -> None:
    """Leave a real AF_UNIX socket FILE at *path* (so the wrapper's `[ -S ]`
    tunnel check sees a live socket and skips re-establishing)."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(path))
    s.close()


@pytest.fixture
def wrapper_env(tmp_path, monkeypatch):
    """Generate the real wrapper + a stored context + tunnel socket, fake PATH.

    Returns ``(wrapper_path, fake_bin, run, url)`` where ``run(*argv, env=...)``
    executes the wrapper with the fake podman/ssh first on PATH and returns the
    CompletedProcess.  ``fake_bin`` holds ``podman.log`` / ``ssh.log`` sinks.
    The forwarded local socket is pre-created so the tunnel fast-path is taken
    (missing-socket re-establish is covered by its own test).
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "run").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))

    wrapper = vr.ensure_dispatch_wrapper()

    sock = vr.tunnel_socket_path(_CTX)
    remote_sock = vr.remote_socket_path(1000)
    url = f"unix://{sock}"
    vr.write_context_entry(
        _CTX, url=url, dest="me@host", uid=1000,
        sock=str(sock), remote_sock=remote_sock,
    )
    _make_socket_file(sock)

    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    podman_log = fake_bin / "podman.log"
    ssh_log = fake_bin / "ssh.log"
    (fake_bin / "podman").write_text(
        f'#!/bin/sh\necho "$*" >> "{podman_log}"\n'
    )
    (fake_bin / "ssh").write_text(
        f'#!/bin/sh\necho "$*" >> "{ssh_log}"\n'
    )
    (fake_bin / "podman").chmod(0o755)
    (fake_bin / "ssh").chmod(0o755)

    def run(*argv, env_extra=None):
        import os
        env = dict(os.environ)
        env["PATH"] = f"{fake_bin}:{env['PATH']}"
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            [str(wrapper), *argv], capture_output=True, text=True, env=env,
        )

    return wrapper, fake_bin, run, url


def _podman_lines(fake_bin: Path) -> list[str]:
    log = fake_bin / "podman.log"
    return log.read_text().splitlines() if log.exists() else []


def _ssh_lines(fake_bin: Path) -> list[str]:
    log = fake_bin / "ssh.log"
    return log.read_text().splitlines() if log.exists() else []


def test_no_token_execs_local_podman_verbatim(wrapper_env):
    _wrapper, fake_bin, run, _url = wrapper_env
    result = run("inspect", "foo", "bar")
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == ["inspect foo bar"]


def test_token_via_argv_context_routes_remote_and_strips(wrapper_env):
    _wrapper, fake_bin, run, url = wrapper_env
    result = run("--context", _CTX, "inspect", "foo")
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == [
        f"--remote --url {url} inspect foo"
    ]


def test_token_via_context_equals_form(wrapper_env):
    _wrapper, fake_bin, run, url = wrapper_env
    result = run("inspect", "--context=" + _CTX, "zap")
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == [
        f"--remote --url {url} inspect zap"
    ]


def test_token_via_docker_context_env(wrapper_env):
    _wrapper, fake_bin, run, url = wrapper_env
    result = run(
        "inspect", "aa", env_extra={"DOCKER_CONTEXT": _CTX},
    )
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == [
        f"--remote --url {url} inspect aa"
    ]


def test_token_via_matching_docker_host_env(wrapper_env):
    _wrapper, fake_bin, run, url = wrapper_env
    result = run("inspect", "bb", env_extra={"DOCKER_HOST": url})
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == [
        f"--remote --url {url} inspect bb"
    ]


def test_non_matching_docker_host_is_local(wrapper_env):
    _wrapper, fake_bin, run, _url = wrapper_env
    result = run("inspect", "cc", env_extra={"DOCKER_HOST": "ssh://other/sock"})
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == ["inspect cc"]


def test_unknown_context_name_is_local_passthrough(wrapper_env):
    _wrapper, fake_bin, run, _url = wrapper_env
    result = run("--context", "some-other-ctx", "inspect", "qux")
    assert result.returncode == 0
    # Context stripped, routed local (unknown/non-kanibako context).
    assert _podman_lines(fake_bin) == ["inspect qux"]


def test_missing_socket_reestablishes_tunnel_then_execs_podman(wrapper_env):
    _wrapper, fake_bin, run, url = wrapper_env
    # Remove the pre-created socket so the wrapper must re-establish the tunnel
    # via the (fake) ssh on PATH before dialing podman.
    sock = vr.tunnel_socket_path(_CTX)
    sock.unlink()
    result = run("--context", _CTX, "inspect", "foo")
    assert result.returncode == 0
    # Fake ssh logged the -L forward (with the ControlPersist=600 override) to
    # the remote podman socket, dest last.
    ssh = _ssh_lines(fake_bin)
    assert len(ssh) == 1
    line = ssh[0]
    # HEADLESS hardening: never interactive under the extension host.
    assert "BatchMode=yes" in line
    assert "ControlPersist=600" in line
    assert "ExitOnForwardFailure=yes" in line
    assert "StreamLocalBindUnlink=yes" in line
    assert f"-L {sock}:{vr.remote_socket_path(1000)}" in line
    assert "-f -N" in line
    assert line.strip().endswith("-- me@host")
    # podman still exec'd against the local unix socket afterwards.
    assert _podman_lines(fake_bin) == [f"--remote --url {url} inspect foo"]


def test_tunnel_establish_failure_logs_and_still_execs_podman(
    wrapper_env, tmp_path,
):
    # The "never swallow" guarantee (spec item 4): ssh re-establish FAILS →
    # a failure line lands in dispatch.log and podman is STILL exec'd (its own
    # connection error then surfaces to the extension). Editor #3 NIT-2.
    _wrapper, fake_bin, run, url = wrapper_env
    vr.tunnel_socket_path(_CTX).unlink()
    (fake_bin / "ssh").write_text("#!/bin/sh\nexit 1\n")
    (fake_bin / "ssh").chmod(0o755)
    result = run("--context", _CTX, "inspect", "foo")
    assert result.returncode == 0
    assert _podman_lines(fake_bin) == [f"--remote --url {url} inspect foo"]
    log_text = vr.dispatch_log_path().read_text()
    assert "tunnel-establish-failed" in log_text
    assert "context=" + _CTX in log_text


def test_dispatch_log_line_written(wrapper_env, tmp_path):
    _wrapper, _fake_bin, run, _url = wrapper_env
    run("--context", _CTX, "inspect", "foo")
    log = vr.dispatch_log_path()
    assert log.is_file()
    line = log.read_text().splitlines()[-1]
    assert "channel=argv" in line
    assert "context=" + _CTX in line
    assert "route=remote" in line
    assert "argv=inspect foo" in line
