"""Unit tests for kanibako.vscode.vscode_remote (FF-1 remote-VS-Code plumbing).

No real ssh / network anywhere: the ssh legs are asserted at the argv level, and
the RemoteEngine subprocess calls are mocked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kanibako.vscode import vscode_remote as vr
from kanibako.errors import KanibakoError

from tests.support.filenames import CONFIG_FILENAME


@pytest.fixture(autouse=True)
def _isolate_xdg(tmp_path, monkeypatch):
    """Point every XDG base the module reads at a per-test tmp dir.

    ⚑ ``XDG_CONFIG_HOME`` is isolated too: ``_vscode_remote_state_dir`` resolves
    ``system.state`` via :func:`kanibako.settings.paths.resolve_state_path`, and
    ``vscode_remote_bin_dir`` resolves ``system.cache`` via
    :func:`kanibako.settings.paths.resolve_cache_path`; both read
    ``$XDG_CONFIG_HOME/kanibako.cfg`` — an unisolated env would read the REAL host config
    and make those paths (hence much of this file) depend on whatever happens to be on the
    box running the suite.
    ⚑ The site base under ``/etc`` is pinned for the same reason and needs its own patch,
    since both resolvers read :func:`kanibako.settings.config.config_base_path`
    unconditionally, BELOW the user layer — env isolation alone does not reach it (same
    pin, same reason, as ``TestDirectoryPluginDiscovery._isolate_config`` in
    ``tests/test_targets/test_discovery.py``).
    """
    import kanibako.settings.config as cfg_mod

    monkeypatch.setattr(cfg_mod, "config_base_path", lambda: tmp_path / "etc_absent.cfg")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
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


# --- remote_run_kanibako PATH preamble (~/.local/bin fallback) --------------

def test_remote_run_kanibako_injects_path_preamble():
    # The lifecycle leg rides `sh -c` with a CONSTANT PATH preamble so a
    # per-user pipx/uv install (~/.local/bin) is found even though a
    # non-interactive ssh command does not source ~/.profile.
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        vr.remote_run_kanibako("me@host", ["start", "--detach", "mybox"])
    argv = m.call_args[0][0]
    assert argv[0] == "ssh"
    dashdash = argv.index("--")
    assert argv[dashdash + 1] == "me@host"
    # ssh joins the trailing args with spaces and hands them to the remote login
    # shell; recover what that shell re-parses (ssh_command shlex-quotes each).
    remote_words = shlex.split(" ".join(argv[dashdash + 2:]))
    assert remote_words[:2] == ["sh", "-c"]
    remote_cmd = remote_words[2]
    # The preamble is present EXACTLY once, unquoted (so $HOME expands on the
    # remote shell), immediately before the kanibako argv.
    assert remote_cmd.count('PATH="$HOME/.local/bin:$PATH"') == 1
    assert remote_cmd == (
        'PATH="$HOME/.local/bin:$PATH" kanibako start --detach mybox'
    )


def test_remote_run_kanibako_hostile_box_name_cannot_escape_quoting():
    # A box name laden with shell metacharacters must stay fully quoted: it can
    # neither expand nor break out into a new command.
    completed = MagicMock(returncode=0, stdout="", stderr="")
    hostile = "mybox; rm -rf ~ $(touch /pwned)"
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        vr.remote_run_kanibako("me@host", ["start", hostile])
    argv = m.call_args[0][0]
    dashdash = argv.index("--")
    # Layer 1: the remote LOGIN shell re-parses ssh's trailing args → sh -c CMD.
    remote_words = shlex.split(" ".join(argv[dashdash + 2:]))
    assert remote_words[:2] == ["sh", "-c"]
    remote_cmd = remote_words[2]
    assert remote_cmd.startswith('PATH="$HOME/.local/bin:$PATH" kanibako start ')
    # The hostile arg survives only inside a single shlex-quoted token.
    assert remote_cmd.endswith("kanibako start " + shlex.quote(hostile))
    # Layer 2: `sh -c` re-parses CMD. No injection: the preamble is a var
    # assignment and the metachars are one inert token, never new words.
    parts = shlex.split(remote_cmd)
    assert parts[0] == "PATH=$HOME/.local/bin:$PATH"  # assignment, not a command
    assert parts[1:] == ["kanibako", "start", hostile]


# --- unix-socket tunnel engine (FF-1b) -------------------------------------

def test_remote_socket_path():
    assert vr.remote_socket_path(1000) == "/run/user/1000/podman/podman.sock"
    assert vr.remote_socket_path(0) == "/run/user/0/podman/podman.sock"


def test_tunnel_socket_path_under_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    name = "kanibako-remote-me-host-abc123"
    assert vr.tunnel_socket_path(name) == tmp_path / "run" / f"{name}.sock"


def test_engine_url_is_unix_local_socket():
    assert vr.engine_url(Path("/run/user/1000/x.sock")) == (
        "unix:///run/user/1000/x.sock"
    )


def test_ensure_tunnel_fast_path_when_socket_accepts(tmp_path):
    # A LIVE AF_UNIX listener → ensure_tunnel returns without any ssh call.
    sock_path = tmp_path / "live.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(sock_path))
    listener.listen(1)
    try:
        with patch("kanibako.vscode.vscode_remote.subprocess.run") as m:
            vr.ensure_tunnel("me@host", 1000, sock_path)
        m.assert_not_called()
    finally:
        listener.close()


def test_ensure_tunnel_missing_socket_builds_ssh_forward(tmp_path):
    sock_path = tmp_path / "gone.sock"  # nonexistent → establish
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        vr.ensure_tunnel("me@host", 1000, sock_path)
    argv = m.call_args[0][0]
    assert argv[0] == "ssh"
    # The tunnel's ControlPersist=600 lease must come BEFORE the base mux
    # opts' 60: OpenSSH takes the FIRST obtained value for a repeated option
    # (ssh_config semantics, `ssh -G`-confirmed) — Editor #3 MAJOR-1.
    assert "ControlMaster=auto" in argv
    assert argv.index("ControlPersist=600") < argv.index("ControlPersist=60")
    assert "ExitOnForwardFailure=yes" in argv
    assert "StreamLocalBindUnlink=yes" in argv
    assert "-f" in argv and "-N" in argv
    assert argv[argv.index("-L") + 1] == (
        f"{sock_path}:/run/user/1000/podman/podman.sock"
    )
    # `--` guards a `-`-leading dest; dest is last.
    assert argv[-2] == "--"
    assert argv[-1] == "me@host"


def test_ensure_tunnel_stale_socket_file_reestablishes(tmp_path):
    # A socket FILE that exists but does NOT accept a connection (stale master):
    # ensure_tunnel must re-establish rather than take the fast path.
    sock_path = tmp_path / "stale.sock"
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(str(sock_path))
    s.close()  # bound file remains, but nothing is listening
    completed = MagicMock(returncode=0, stdout="", stderr="")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        vr.ensure_tunnel("me@host", 1000, sock_path)
    m.assert_called_once()


def test_ensure_tunnel_failure_carries_stderr(tmp_path):
    sock_path = tmp_path / "gone.sock"
    completed = MagicMock(returncode=255, stdout="", stderr="ssh: connect refused")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.ensure_tunnel("me@host", 1000, sock_path)
    msg = str(exc.value)
    assert "me@host" in msg
    assert "ssh: connect refused" in msg


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


# --- _vscode_remote_state_dir sits under system.state (resolve_state_path) ---

def _write_store_config(
    store: Path, *, state: Path | None = None, cache: Path | None = None,
) -> None:
    """Point ``config.data`` at *store* and, when given, set ``system.state`` /
    ``system.cache`` in the settings file that store carries."""
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / CONFIG_FILENAME).write_text(f'config:\n  data: "{store}"\n')
    rows = ""
    if state is not None:
        rows += f'  state: "{state}"\n'
    if cache is not None:
        rows += f'  cache: "{cache}"\n'
    if rows:
        settings = store / "global" / "settings.yaml"
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(f"system:\n{rows}")


def test_state_dir_defaults_under_the_state_key_when_no_config():
    """No config under the isolated ``XDG_CONFIG_HOME`` → ``system.state``'s own default,
    exactly the prior hardcoded behaviour (TOTAL: absent config never raises)."""
    assert vr._vscode_remote_state_dir() == (
        Path(os.environ["XDG_STATE_HOME"]) / "kanibako" / "vscode-remote"
    )


def test_state_dir_follows_a_repointed_system_state(tmp_path):
    """[R166]: the connection store derives from ``system.state``, so setting that key
    moves it."""
    _write_store_config(tmp_path / "custom_store", state=tmp_path / "elsewhere" / "state")
    assert vr._vscode_remote_state_dir() == (
        tmp_path / "elsewhere" / "state" / "vscode-remote"
    )


def test_state_dir_ignores_a_repointed_config_data(tmp_path):
    """[R166] MUTATION PROOF: state has no relationship to ``config.data``.  The retired
    behaviour put this under ``$XDG_STATE_HOME/custom_store``."""
    _write_store_config(tmp_path / "custom_store")
    assert vr._vscode_remote_state_dir() == (
        Path(os.environ["XDG_STATE_HOME"]) / "kanibako" / "vscode-remote"
    )


def test_state_dir_creates_nothing(tmp_path):
    """Calling it is a pure path computation — no directory materializes."""
    _write_store_config(tmp_path / "custom_store", state=tmp_path / "elsewhere" / "state")
    before = set(tmp_path.rglob("*"))
    vr._vscode_remote_state_dir()
    after = set(tmp_path.rglob("*"))
    assert after == before
    assert not (tmp_path / "elsewhere").exists()


def test_state_dir_malformed_config_degrades_without_raising():
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / CONFIG_FILENAME).write_text("not: [valid: yaml: at all")
    d = vr._vscode_remote_state_dir()  # must not raise
    assert d == Path(os.environ["XDG_STATE_HOME"]) / "kanibako" / "vscode-remote"


# --- vscode_remote_bin_dir sits under system.cache (resolve_cache_path) ---

def test_bin_dir_defaults_under_xdg_cache_home_when_no_config():
    """No config under the isolated ``XDG_CONFIG_HOME`` → ``system.cache``'s own default
    (TOTAL: absent config never raises)."""
    assert vr.vscode_remote_bin_dir() == (
        Path(os.environ["XDG_CACHE_HOME"]) / "kanibako" / "vscode-remote" / "bin"
    )


def test_bin_dir_follows_a_repointed_system_cache(tmp_path):
    """The generated wrapper derives from ``system.cache``, so setting that key
    moves it."""
    _write_store_config(tmp_path / "custom_store", cache=tmp_path / "elsewhere" / "cache")
    assert vr.vscode_remote_bin_dir() == (
        tmp_path / "elsewhere" / "cache" / "vscode-remote" / "bin"
    )


def test_bin_dir_ignores_a_repointed_config_data(tmp_path):
    """MUTATION PROOF: the wrapper has no relationship to ``config.data``. A store
    outside ``$XDG_CACHE_HOME`` rules out a leaf-only reading: a leaf-only reading
    would rejoin "custom_store" to the XDG cache base and move the wrapper where it
    must not go."""
    _write_store_config(tmp_path / "custom_store")
    assert vr.vscode_remote_bin_dir() == (
        Path(os.environ["XDG_CACHE_HOME"]) / "kanibako" / "vscode-remote" / "bin"
    )


def test_bin_dir_creates_nothing(tmp_path):
    """Calling it is a pure path computation — no directory materializes."""
    _write_store_config(tmp_path / "custom_store", cache=tmp_path / "elsewhere" / "cache")
    before = set(tmp_path.rglob("*"))
    vr.vscode_remote_bin_dir()
    after = set(tmp_path.rglob("*"))
    assert after == before
    assert not (tmp_path / "elsewhere").exists()


def test_bin_dir_malformed_config_degrades_without_raising():
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    config_home.mkdir(parents=True, exist_ok=True)
    (config_home / CONFIG_FILENAME).write_text("not: [valid: yaml: at all")
    d = vr.vscode_remote_bin_dir()  # must not raise
    assert d == Path(os.environ["XDG_CACHE_HOME"]) / "kanibako" / "vscode-remote" / "bin"


# --- connection store round-trip + greppability ----------------------------

def test_store_round_trip_and_greppable():
    name = vr.remote_context_name("me@host")
    sock = "/run/user/1000/kanibako-remote-me-host.sock"
    remote_sock = "/run/user/1000/podman/podman.sock"
    path = vr.write_context_entry(
        name,
        url=f"unix://{sock}",
        dest="me@host",
        uid=1000,
        sock=sock,
        remote_sock=remote_sock,
    )
    assert path == vr.contexts_dir() / name
    got = vr.read_context_entry(name)
    assert got == {
        "URL": f"unix://{sock}",
        "DEST": "me@host",
        "UID": "1000",
        "SOCK": sock,
        "REMOTE_SOCK": remote_sock,
    }
    # sh-greppable: one KEY=VALUE per line, no quoting/escaping.
    raw = path.read_text()
    assert f"URL=unix://{sock}\n" in raw
    assert raw.splitlines() == [
        f"URL=unix://{sock}",
        "DEST=me@host",
        "UID=1000",
        f"SOCK={sock}",
        f"REMOTE_SOCK={remote_sock}",
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

def test_remote_engine_argv_prefix_is_unix_url():
    url = "unix:///run/user/1000/kanibako-remote-h.sock"
    eng = vr.RemoteEngine(url, podman="/usr/bin/podman")
    # No `--ssh native`, no shim PATH: podman dials the LOCAL unix socket.
    assert eng.argv_prefix == ["/usr/bin/podman", "--remote", "--url", url]


def test_remote_engine_is_running_parses_true():
    url = "unix:///run/user/1000/x.sock"
    eng = vr.RemoteEngine(url, podman="/usr/bin/podman")
    completed = MagicMock(returncode=0, stdout="true\n")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        assert eng.is_running("kanibako-foo") is True
        argv = m.call_args[0][0]
        assert argv[:4] == eng.argv_prefix
        assert argv[4:] == ["inspect", "--format", "{{.State.Running}}", "kanibako-foo"]


def test_remote_engine_inspect_env_and_image():
    eng = vr.RemoteEngine("unix:///run/x.sock", podman="podman")
    env_completed = MagicMock(
        returncode=0, stdout='["FOO=bar","KANIBAKO_AGENT=claude"]',
    )
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=env_completed):
        assert eng.inspect_env("box", "KANIBAKO_AGENT") == "claude"
        assert eng.inspect_env("box", "MISSING") is None
    img_completed = MagicMock(returncode=0, stdout="ghcr.io/x/y:latest\n")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=img_completed):
        assert eng.container_image("box") == "ghcr.io/x/y:latest"


def test_remote_engine_container_image_shares_the_local_guard():
    """ONE rule for both legs — asserted by IDENTITY, not by a duplicated table.

    Both ``container_image`` implementations send the same ``{{.ImageName}}`` format
    string, so a second copy of "what counts as a reference" could drift silently.
    """
    from kanibako.runtime import container as container_mod

    assert vr.image_ref_or_none is container_mod.image_ref_or_none


def test_remote_engine_container_image_refuses_a_non_reference_at_exit_zero():
    """Go's ``<no value>`` at rc 0 must not key the config the remote seed writes.

    Same rule as the local leg — see ``TestContainerImage`` in
    ``tests/test_runtime/test_container.py`` for which engines actually do this.
    """
    eng = vr.RemoteEngine("unix:///run/x.sock", podman="podman")
    completed = MagicMock(returncode=0, stdout="<no value>\n")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        assert eng.container_image("box") is None


def test_remote_engine_container_image_none_when_inspect_fails():
    eng = vr.RemoteEngine("unix:///run/x.sock", podman="podman")
    completed = MagicMock(returncode=125, stdout="", stderr="Error: no such container")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        assert eng.container_image("box") is None


def test_remote_engine_running_with_stderr_surfaces_inspect_error():
    eng = vr.RemoteEngine("unix:///run/x.sock", podman="podman")
    completed = MagicMock(
        returncode=125, stdout="", stderr='Error: no such container "kanibako-x"',
    )
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        running, err = eng.running_with_stderr("kanibako-x")
    assert running is False
    assert "no such container" in err


# --- preflight_engine (mocked podman version) ------------------------------

def _preflight_engine():
    return vr.RemoteEngine("unix:///run/x.sock", podman="podman")


def test_preflight_engine_ok():
    completed = MagicMock(returncode=0, stdout="Client: ...", stderr="")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        assert vr.preflight_engine(_preflight_engine()) is None
        # It pre-flights with `version` on the podman remote argv prefix.
        assert m.call_args[0][0][-1] == "version"


def test_preflight_engine_failure_carries_stderr_and_tunnel_hint():
    completed = MagicMock(
        returncode=125, stdout="",
        stderr="Cannot connect to Podman: dial unix ...: connection refused",
    )
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.preflight_engine(_preflight_engine())
    msg = str(exc.value)
    assert "connection refused" in msg  # podman's stderr verbatim
    assert "tunnel" in msg.lower()  # generic tunnel remediation


def test_preflight_engine_no_output_still_raises():
    completed = MagicMock(returncode=1, stdout="", stderr="")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.preflight_engine(_preflight_engine())
    assert "podman produced no error output" in str(exc.value)


# --- probe_remote (mocked ssh) ---------------------------------------------

def test_probe_remote_returns_uid_when_socket_present():
    completed = MagicMock(
        returncode=0, stdout="KANIBAKO_UID=1000\nKANIBAKO_SOCK=ok\n", stderr="",
    )
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed) as m:
        assert vr.probe_remote("me@host") == 1000
        # The probe rides the mux ssh leg.
        argv = m.call_args[0][0]
        assert argv[0] == "ssh"
        assert "me@host" in argv


def test_probe_remote_missing_socket_raises_with_remediation():
    completed = MagicMock(
        returncode=0, stdout="KANIBAKO_UID=1000\nKANIBAKO_SOCK=missing\n", stderr="",
    )
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.probe_remote("me@host")
    msg = str(exc.value)
    assert "podman.socket" in msg
    assert "enable-linger" in msg


def test_probe_remote_ssh_failure_raises():
    completed = MagicMock(returncode=255, stdout="", stderr="ssh: connect refused")
    with patch("kanibako.vscode.vscode_remote.subprocess.run", return_value=completed):
        with pytest.raises(KanibakoError) as exc:
            vr.probe_remote("me@host")
    assert "me@host" in str(exc.value)


# --- wrapper + shim idempotent regen ---------------------------------------

def test_wrapper_generated_executable_and_idempotent():
    wpath = vr.ensure_dispatch_wrapper()
    assert wpath == vr.dispatch_wrapper_path()
    # 0755
    assert (wpath.stat().st_mode & 0o777) == 0o755
    # POSIX sh, data-driven (baked store dir; no eval/source of store files).
    body = wpath.read_text()
    assert body.startswith("#!/bin/sh")
    assert str(vr.contexts_dir()) in body
    assert "eval" not in body
    # Data-driven: store files are grepped (sed), never sourced.
    assert "source " not in body
    assert "sed -n 's/^URL=//p'" in body
    assert "sed -n 's/^SOCK=//p'" in body
    # The wrapper dials the LOCAL unix socket (no golang ssh: engine URL).
    assert '--url "unix://$_sock"' in body
    assert "--ssh native" not in body
    # Idempotent: a second ensure does not rewrite.
    assert vr._write_script(wpath, vr._wrapper_content()) is False


def test_wrapper_rewritten_when_content_changes():
    wpath = vr.ensure_dispatch_wrapper()
    wpath.write_text("#!/bin/sh\n# stale\n")
    assert vr.ensure_dispatch_wrapper() == wpath
    assert "kanibako vscode-remote dispatch wrapper" in wpath.read_text()


# --- format_remote_failure: attributed remote-error surfacing --------------

def test_format_remote_failure_attributes_and_indents_remote_output():
    """A relayed remote error is wrapped under a "Response from remote host:"
    header, each remote line indented — so a remote message that itself begins
    with "Error:" cannot read as a second, unexplained LOCAL error (Jei rc10)."""
    msg = vr.format_remote_failure(
        "kanibako start --detach --warm-only",
        "kanibako",
        "Error: kanibako's bundled templates changed since setup was last run\n"
        "  Run 'kanibako setup' to refresh.",
    )
    assert msg.startswith(
        "remote 'kanibako start --detach --warm-only' failed on 'kanibako'. "
        "Response from remote host:\n"
    )
    # Every remote line is indented under the header (clear boundary).
    assert "\n    Error: kanibako's bundled templates changed" in msg
    assert "\n      Run 'kanibako setup' to refresh." in msg
    # No leading "Error:" — the caller prepends it.
    assert not msg.startswith("Error:")


def test_format_remote_failure_no_output_is_called_out():
    """An empty remote stderr is stated explicitly rather than left as a bare
    double error."""
    msg = vr.format_remote_failure("kanibako start", "host", "   ")
    assert "Response from remote host:\n    (no output from remote host)" in msg
