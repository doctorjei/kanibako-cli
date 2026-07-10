"""Tests for kanibako.box_lifecycle — the PID-1 attach/detach detection primitive.

The point of Increment 1 is PURE logic, so the transition table and the two
surface detectors are exercised exhaustively; the thin probe layer is driven
through injected fakes (no real ``/proc`` walk, no real tmux).
"""

from __future__ import annotations

import subprocess

import pytest

from kanibako.box_lifecycle import (
    VSCODE_SERVER_DIR_MARKERS,
    AttachState,
    LifecycleEvent,
    canonical_tmux_session_pid,
    classify_transition,
    is_vscode_server_path_part,
    snapshot_attach_state,
    tmux_terminal_attached,
    vscode_server_present,
)


# --- marker constant + helper ----------------------------------------------

def test_markers_are_the_canonical_four():
    assert VSCODE_SERVER_DIR_MARKERS == (
        ".vscode-server",
        ".vscode-server-insiders",
        ".vscode-server-oss",
        ".cursor-server",
    )


@pytest.mark.parametrize("part", list(VSCODE_SERVER_DIR_MARKERS))
def test_is_vscode_server_path_part_matches_each_marker(part):
    assert is_vscode_server_path_part(part) is True


@pytest.mark.parametrize("part", ["bin", "node", "home", "agent", ".config", ""])
def test_is_vscode_server_path_part_rejects_ordinary_segments(part):
    assert is_vscode_server_path_part(part) is False


# --- vscode_server_present -------------------------------------------------

@pytest.mark.parametrize("marker", list(VSCODE_SERVER_DIR_MARKERS))
def test_vscode_server_present_matches_each_marker_in_realistic_cmdline(marker):
    cmdline = f"/home/agent/{marker}/bin/1a2b3c/node /home/agent/{marker}/bin/1a2b3c/server-main.js"
    assert vscode_server_present([cmdline]) is True


def test_vscode_server_present_true_when_any_of_several():
    lines = [
        "/usr/bin/tmux new-session",
        "/home/agent/.local/bin/claude",
        "/home/agent/.vscode-server/bin/deadbeef/node",
    ]
    assert vscode_server_present(lines) is True


def test_vscode_server_present_no_false_positive_on_normal_binaries():
    lines = [
        "/usr/bin/node /srv/app/index.js",
        "/home/agent/.local/bin/claude --continue",
        "/bin/bash -l",
        "tmux: server",
    ]
    assert vscode_server_present(lines) is False


def test_vscode_server_present_empty_iterable_is_false():
    assert vscode_server_present([]) is False


# --- tmux_terminal_attached ------------------------------------------------

def test_tmux_terminal_attached_nonempty_true():
    output = "/dev/pts/3: 0 [80x24 xterm-256color] (utf8)\n"
    assert tmux_terminal_attached(output) is True


def test_tmux_terminal_attached_two_clients_true():
    assert tmux_terminal_attached("/dev/pts/3: ...\n/dev/pts/5: ...\n") is True


@pytest.mark.parametrize("output", ["", "   ", "\n", "\t\n  \n"])
def test_tmux_terminal_attached_empty_or_whitespace_false(output):
    assert tmux_terminal_attached(output) is False


# --- AttachState -----------------------------------------------------------

def test_attach_state_defaults_none_attached():
    st = AttachState()
    assert st.vscode_server is False
    assert st.tmux_terminal is False
    assert st.any_attached is False


@pytest.mark.parametrize("vs,tm,expect", [
    (False, False, False),
    (True, False, True),
    (False, True, True),
    (True, True, True),
])
def test_attach_state_any_attached(vs, tm, expect):
    assert AttachState(vscode_server=vs, tmux_terminal=tm).any_attached is expect


def test_attach_state_is_frozen():
    st = AttachState()
    with pytest.raises(Exception):
        st.vscode_server = True  # type: ignore[misc]


# --- classify_transition (full truth table) --------------------------------

_NONE = AttachState(False, False)
_VS = AttachState(True, False)
_TM = AttachState(False, True)
_BOTH = AttachState(True, True)


def test_classify_first_attach_is_attach():
    assert classify_transition(_NONE, _VS) is LifecycleEvent.ATTACH
    assert classify_transition(_NONE, _TM) is LifecycleEvent.ATTACH
    assert classify_transition(_NONE, _BOTH) is LifecycleEvent.ATTACH


def test_classify_full_detach_is_detach():
    assert classify_transition(_VS, _NONE) is LifecycleEvent.DETACH
    assert classify_transition(_TM, _NONE) is LifecycleEvent.DETACH
    assert classify_transition(_BOTH, _NONE) is LifecycleEvent.DETACH


def test_classify_partial_loss_is_detach():
    # One surface remains, the other went away → still a DETACH.
    assert classify_transition(_BOTH, _VS) is LifecycleEvent.DETACH
    assert classify_transition(_BOTH, _TM) is LifecycleEvent.DETACH


def test_classify_partial_gain_is_attach():
    assert classify_transition(_VS, _BOTH) is LifecycleEvent.ATTACH
    assert classify_transition(_TM, _BOTH) is LifecycleEvent.ATTACH


def test_classify_mixed_transition_biases_detach():
    # vscode gained WHILE tmux lost (and vice-versa): any loss ⇒ DETACH.
    assert classify_transition(_TM, _VS) is LifecycleEvent.DETACH
    assert classify_transition(_VS, _TM) is LifecycleEvent.DETACH


@pytest.mark.parametrize("state", [_NONE, _VS, _TM, _BOTH])
def test_classify_same_state_is_none(state):
    assert classify_transition(state, state) is LifecycleEvent.NONE


# --- snapshot_attach_state (injected probes) -------------------------------

def _fake_run(stdout="", returncode=0):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")
    return _run


def test_snapshot_composes_both_detectors_all_attached():
    st = snapshot_attach_state(
        "kanibako-foo",
        run=_fake_run(stdout="/dev/pts/1: ...\n"),
        proc_cmdlines=["/home/agent/.vscode-server/bin/x/node"],
    )
    assert st == AttachState(vscode_server=True, tmux_terminal=True)


def test_snapshot_none_attached():
    st = snapshot_attach_state(
        "kanibako-foo",
        run=_fake_run(stdout=""),
        proc_cmdlines=["/usr/bin/node", "/home/agent/.local/bin/claude"],
    )
    assert st == AttachState(vscode_server=False, tmux_terminal=False)


def test_snapshot_vscode_only():
    st = snapshot_attach_state(
        "s",
        run=_fake_run(stdout="   \n"),  # whitespace-only ⇒ no terminal
        proc_cmdlines=["/home/agent/.cursor-server/bin/y/node"],
    )
    assert st == AttachState(vscode_server=True, tmux_terminal=False)


def test_snapshot_tmux_only():
    st = snapshot_attach_state(
        "s",
        run=_fake_run(stdout="/dev/pts/2: ...\n"),
        proc_cmdlines=["/bin/bash"],
    )
    assert st == AttachState(vscode_server=False, tmux_terminal=True)


def test_snapshot_tmux_nonzero_rc_is_not_attached():
    # No such session / no server → rc!=0 ⇒ tmux_terminal False (tolerant).
    st = snapshot_attach_state(
        "s",
        run=_fake_run(stdout="no server running\n", returncode=1),
        proc_cmdlines=[],
    )
    assert st.tmux_terminal is False


def test_snapshot_tmux_absent_binary_is_not_attached():
    def _boom(cmd, **kwargs):
        raise FileNotFoundError("tmux")

    st = snapshot_attach_state("s", run=_boom, proc_cmdlines=[])
    assert st.tmux_terminal is False
    assert st.vscode_server is False


def test_snapshot_tmux_oserror_is_not_attached():
    def _boom(cmd, **kwargs):
        raise OSError("kaboom")

    st = snapshot_attach_state("s", run=_boom, proc_cmdlines=[])
    assert st.tmux_terminal is False


def test_snapshot_tmux_stdout_none_is_not_attached():
    # A CompletedProcess with stdout=None (defensive) must not raise; the
    # ``proc.stdout or ""`` guard normalises it to "not attached".
    st = snapshot_attach_state(
        "s",
        run=_fake_run(stdout=None),
        proc_cmdlines=[],
    )
    assert st.tmux_terminal is False


def test_snapshot_passes_session_to_list_clients():
    seen = {}

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    snapshot_attach_state("my-box", run=_run, proc_cmdlines=[])
    assert seen["cmd"] == ["tmux", "list-clients", "-t", "my-box"]


# --- canonical_tmux_session_pid --------------------------------------------

def test_canonical_pid_parses_integer():
    assert canonical_tmux_session_pid("s", run=_fake_run(stdout="4321\n")) == 4321


def test_canonical_pid_passes_display_message_command():
    seen = {}

    def _run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="7\n", stderr="")

    canonical_tmux_session_pid("my-box", run=_run)
    assert seen["cmd"] == [
        "tmux", "display-message", "-p", "-t", "my-box", "#{pid}",
    ]


def test_canonical_pid_nonzero_rc_is_none():
    assert canonical_tmux_session_pid(
        "s", run=_fake_run(stdout="", returncode=1),
    ) is None


def test_canonical_pid_garbage_is_none():
    assert canonical_tmux_session_pid("s", run=_fake_run(stdout="not-a-pid")) is None


def test_canonical_pid_empty_is_none():
    assert canonical_tmux_session_pid("s", run=_fake_run(stdout="\n")) is None


def test_canonical_pid_stdout_none_is_none():
    # stdout=None (defensive) must reach the ``or ""`` guard, not an AttributeError.
    assert canonical_tmux_session_pid("s", run=_fake_run(stdout=None)) is None


def test_canonical_pid_absent_tmux_is_none():
    def _boom(cmd, **kwargs):
        raise FileNotFoundError("tmux")

    assert canonical_tmux_session_pid("s", run=_boom) is None


def test_canonical_pid_oserror_is_none():
    def _boom(cmd, **kwargs):
        raise OSError("nope")

    assert canonical_tmux_session_pid("s", run=_boom) is None
