"""The SEEDED claude hook commands — the bible hooks a real box runs at session edges.

WHY THIS MODULE EXISTS
----------------------
``test_delivery_manifest`` asserts the claude template's ``settings.json`` is
DELIVERED; nothing asserted what was IN it. That hole hid a live defect for a
release: every bible hook was invoked as a COMPOUND command
(``…/startup.sh || true``), and a compound command costs two things at once.

1. THE PID. A compound command forces the hook shell to survive and evaluate the
   right-hand side, so it cannot ``exec`` the script. ``$PPID`` inside the script
   was then that transient shell, not the agent — so ``pid-add.sh`` wrote a marker
   naming an already-dead process, and ``SessionEnd`` ran under a DIFFERENT wrapper
   whose remove targeted a filename that never existed. One leaked marker per
   session start.
2. THE EXIT STATUS. ``|| true`` swallowed a handbook / notebook layer that EXISTS
   and fails — exactly the loudness the cascade scripts' own comments promise.

Both are cured together: the command passes ``"$PPID"`` (expanded by the hook
shell, where it IS the agent) and drops ``|| true``.

⚑ BELT AND BRACES, DELIBERATELY. With ``|| true`` gone the shell MAY ``exec`` the
script and its own ``$PPID`` would be right anyway — but the explicit argument makes
the pid correct BY CONSTRUCTION rather than by a shell optimisation that would break
silently the day the command became compound again. The pin below is on the argument,
not on the exec.

⚑ MUTATION PROOF, NOT A BYTE COMPARISON. The string assertions cannot see a cascade
that silently marks the wrong process, so the tests below RUN the shipped command
strings through a shell whose parent is this pytest process — standing in for the
agent — and watch which pid the marker names.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kanibako.settings.core_defaults import ROM_ROOT_PARTS, _canon_dest, packaged_data_dir
from kanibako.launch.templates import _packaged_agent_store
from kanibako.vscode.vscode_config import _AGENT_MARKER_WRITE_COMMAND

#: Rom-root-relative home of the bible's hook cascade and its PID helpers. The BOX
#: paths are derived by ``_canon_dest`` — the same function the bind emitter uses —
#: so relocating the canon dest reds here instead of leaving a hook pointed at nothing.
_SCRIPTS_ROM_REL = "bible/general/scripts"
_HOOKS_ROM_REL = f"{_SCRIPTS_ROM_REL}/hooks"

#: Seed-source-relative path of the claude harness config the box actually reads.
_SETTINGS_REL = "template/box/home/.claude/settings.json"

#: The session-edge hooks that own a liveness marker, and which helper each cascades to.
#: ⚑ ``stop.sh`` and ``edited.sh`` are deliberately absent: ``Stop`` is a TURN boundary,
#: not a session end, so neither touches a pid.
_PID_HOOKS = {
    "startup": "pid-add",
    "resume": "pid-add",
    "clear-start": "pid-add",
    "compact": "pid-add",
    "end": "pid-rm",
    "clear-end": "pid-rm",
}

#: Bible hooks seeded without a pid argument — they must still be BARE calls.
_PIDLESS_HOOKS = ("stop",)

_HAS_BASH = shutil.which("bash") is not None


def _shipped_settings() -> dict:
    """Parse the SHIPPED claude seed template — the bytes a box gets at create."""
    store = _packaged_agent_store("claude")
    assert store is not None, "the claude plugin ships no agent-store payload"
    path = Path(store) / _SETTINGS_REL
    assert path.is_file(), f"the claude seed template is missing {_SETTINGS_REL}"
    return json.loads(path.read_text())


def _hook_commands(settings: dict) -> list[str]:
    """Every ``command`` string in the template, across all events and matchers."""
    return [
        entry["command"]
        for groups in settings.get("hooks", {}).values()
        for group in groups
        for entry in group.get("hooks", [])
        if entry.get("type") == "command"
    ]


def _shipped_script(rom_rel: str, leaf: str) -> Path:
    """The SHIPPED source of one bible script — the bytes the rom bind exposes."""
    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    return rom_root / rom_rel / leaf


# --- The seeded command strings --------------------------------------------

def test_seeded_pid_hooks_pass_the_agent_pid_explicitly():
    """Each marker-owning hook is invoked with ``"$PPID"`` — expanded by the HOOK
    SHELL, where it names the agent, and handed down the cascade as ``$1``."""
    commands = _hook_commands(_shipped_settings())
    box_dir = _canon_dest(_HOOKS_ROM_REL)
    for leaf in _PID_HOOKS:
        expected = f'{box_dir}/{leaf}.sh "$PPID"'
        assert commands.count(expected) == 1, (
            f"{leaf}.sh is not seeded exactly once as a bare call passing $PPID; "
            f"commands naming it: {[c for c in commands if f'/{leaf}.sh' in c]}"
        )


def test_seeded_pidless_hooks_are_bare_calls():
    """``Stop`` is a turn boundary: its hook takes no pid, but must still be bare."""
    commands = _hook_commands(_shipped_settings())
    box_dir = _canon_dest(_HOOKS_ROM_REL)
    for leaf in _PIDLESS_HOOKS:
        assert commands.count(f"{box_dir}/{leaf}.sh") == 1, (
            f"{leaf}.sh is not seeded exactly once as a bare call; "
            f"commands naming it: {[c for c in commands if f'/{leaf}.sh' in c]}"
        )


def test_no_seeded_bible_hook_swallows_its_exit_status():
    """LOUDNESS. ``|| true`` cannot tell "the user created no layer" (silent, and the
    cascade scripts already handle it with an existence test) from "a layer that
    EXISTS raised" (a bug in the user's own hook, which has to stay visible). It also
    forces the compound form that costs the pid. No bible hook may carry one."""
    box_dir = _canon_dest(_SCRIPTS_ROM_REL)
    offenders = [
        command
        for command in _hook_commands(_shipped_settings())
        if command.startswith(box_dir) and "||" in command
    ]
    assert not offenders, f"bible hook commands swallow their exit status: {offenders}"


# --- The shipped cascade scripts -------------------------------------------

def test_shipped_hook_cascade_forwards_its_pid_argument():
    """The other half of ``startup.sh "$PPID"`` meaning what the hook intends: a
    CASCADED caller must prefer its own ``$1`` over its ``$PPID`` (which by then is
    the hook shell) and hand that pid to the helper. The bare ``$PPID`` fallback stays
    for a caller that wires the script AS the hook command."""
    for leaf, helper in _PID_HOOKS.items():
        text = _shipped_script(_HOOKS_ROM_REL, f"{leaf}.sh").read_text()
        expected = f'{helper}.sh "${{1:-$PPID}}"'
        assert expected in text, f"{leaf}.sh does not forward its pid argument to {helper}.sh"


def test_shipped_pidless_hooks_touch_no_pid_helper():
    """``stop.sh`` / ``edited.sh`` must not acquire pid bookkeeping by drift: a turn
    boundary that wrote a marker would leak one per assistant turn."""
    for leaf in (*_PIDLESS_HOOKS, "edited"):
        text = _shipped_script(_HOOKS_ROM_REL, f"{leaf}.sh").read_text()
        assert "pid-add.sh" not in text and "pid-rm.sh" not in text, (
            f"{leaf}.sh performs pid bookkeeping; Stop is a turn boundary, not a session end"
        )


# --- MUTATION PROOF: run the shipped commands and watch the marker ---------

@pytest.fixture
def cascade_box(tmp_path):
    """A HOME holding the shipped bible cascade, plus the env the helpers read.

    Returns ``(env, markers_dir, pidfile, home)``. The handbook layer is deliberately
    ABSENT, which is the ordinary case and must stay silent.
    """
    home = tmp_path / "home"
    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    for rel in (_HOOKS_ROM_REL, f"{_SCRIPTS_ROM_REL}/util"):
        source = rom_root / rel
        assert source.is_dir(), f"the wheel ships no {rel}"
        dest = home / "canon" / rel
        dest.mkdir(parents=True, exist_ok=True)
        for script in source.iterdir():
            if script.is_file():
                shutil.copy2(script, dest / script.name)

    markers = tmp_path / "markers"
    pidfile = tmp_path / "run" / "agent.pid"
    env = {
        **os.environ,
        "HOME": str(home),
        "KANIBAKO_AGENT_MARKERS_DIR": str(markers),
        "KANIBAKO_AGENT_PIDFILE": str(pidfile),
    }
    return env, markers, pidfile, home


def _run_hook(command: str, env: dict) -> subprocess.CompletedProcess:
    """Run one seeded command the way the harness does — a shell child of THIS process,
    so this pytest process stands in for the agent and its pid is the correct marker."""
    return subprocess.run(["sh", "-c", command], env=env, capture_output=True, text=True)


def _seeded(leaf: str) -> str:
    """The command string the shipped template seeds for one bible hook."""
    box_dir = _canon_dest(_HOOKS_ROM_REL)
    commands = _hook_commands(_shipped_settings())
    matches = [c for c in commands if c.startswith(f"{box_dir}/{leaf}.sh")]
    assert len(matches) == 1, f"expected exactly one seeded command for {leaf}.sh, got {matches}"
    return matches[0]


@pytest.mark.skipif(not _HAS_BASH, reason="the bible cascade is bash")
@pytest.mark.parametrize(("start_leaf", "end_leaf"), [
    ("startup", "end"),
    ("resume", "end"),
    ("clear-start", "clear-end"),
    ("compact", "end"),
])
def test_seeded_cascade_marks_the_agent_pid_and_clears_it(
    start_leaf, end_leaf, cascade_box,
):
    """THE DISCRIMINATING ASSERTION is the marker's NAME. Before the fix the marker was
    named for the hook's transient wrapper shell — a pid already dead when the marker
    was written, and a different pid at ``SessionEnd``, so the remove missed and the
    marker LEAKED once per session start."""
    env, markers, pidfile, _ = cascade_box
    agent_pid = str(os.getpid())

    done = _run_hook(_seeded(start_leaf), env)
    assert done.returncode == 0, done.stderr
    assert [p.name for p in markers.iterdir()] == [agent_pid], (
        f"{start_leaf}.sh marked a pid that is not the agent's"
    )
    assert (markers / agent_pid).read_text() == agent_pid
    assert pidfile.read_text() == agent_pid

    done = _run_hook(_seeded(end_leaf), env)
    assert done.returncode == 0, done.stderr
    assert list(markers.iterdir()) == [], f"{end_leaf}.sh left a leaked marker"
    # The pidfile still named us, so the remove was ours to make.
    assert not pidfile.exists()


@pytest.mark.skipif(not _HAS_BASH, reason="the bible cascade is bash")
def test_seeded_end_cascade_leaves_a_pidfile_another_agent_owns(cascade_box):
    """The pidfile is a SINGLE SHARED PATH while markers are per-pid, so the cascade
    must drop it only while it still names the leaver — and must still exit 0 when it
    does not, or a second agent in the box turns a clean end into a red one."""
    env, markers, pidfile, _ = cascade_box
    agent_pid = str(os.getpid())
    markers.mkdir(parents=True)
    (markers / agent_pid).write_text(agent_pid)
    pidfile.parent.mkdir(parents=True)
    other_agent = str(os.getpid() + 1)  # any pid that is not ours
    pidfile.write_text(other_agent)

    done = _run_hook(_seeded("end"), env)
    assert done.returncode == 0, done.stderr
    assert list(markers.iterdir()) == []
    assert pidfile.read_text() == other_agent, "the cascade cleared another agent's pidfile"


@pytest.mark.skipif(not _HAS_BASH, reason="the bible cascade is bash")
def test_seeded_cascade_surfaces_a_failing_handbook_layer(cascade_box):
    """LOUDNESS, proved by mutation rather than by reading the string. A handbook hook
    that EXISTS and exits non-zero is a bug in the user's own hook; the seeded command
    must carry that status out. ⚑ An ABSENT layer stays silent — the other tests in
    this module run with no handbook at all and expect rc 0."""
    env, _, _, home = cascade_box
    handbook_hook = home / "canon/handbook/general/scripts/hooks/startup.sh"
    handbook_hook.parent.mkdir(parents=True)
    handbook_hook.write_text('#!/usr/bin/env bash\necho "handbook startup failed" >&2\nexit 3\n')
    handbook_hook.chmod(0o755)

    done = _run_hook(_seeded("startup"), env)
    assert done.returncode != 0, (
        "the seeded startup hook swallowed a failing handbook layer; "
        f"stderr was {done.stderr!r}"
    )


@pytest.mark.skipif(not _HAS_BASH, reason="the bible cascade is bash")
def test_seed_and_panel_carriers_name_the_same_pid(cascade_box):
    """TWO CARRIERS, ONE AGENT. The seeded template and ``vscode_config``'s panel hook
    both write a liveness marker, and ``box_supervisor`` reads one dir — so the two must
    agree on WHICH pid they name. They reach the helper by different routes (the seed
    goes through the bible CASCADE and passes ``$1``; the panel calls ``pid-add.sh``
    directly), which is exactly how they could drift apart unnoticed."""
    env, markers, _, home = cascade_box
    agent_pid = str(os.getpid())

    assert _run_hook(_seeded("startup"), env).returncode == 0
    seeded_marker = [p.name for p in markers.iterdir()]
    shutil.rmtree(markers)

    # The panel command names an absolute-ish ``~`` path into the same bound scripts.
    assert _run_hook(_AGENT_MARKER_WRITE_COMMAND, env).returncode == 0
    panel_marker = [p.name for p in markers.iterdir()]

    assert seeded_marker == panel_marker == [agent_pid], (
        f"carriers disagree: seed wrote {seeded_marker}, panel wrote {panel_marker}, "
        f"agent is {agent_pid}"
    )
