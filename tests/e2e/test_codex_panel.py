"""E2e: Phase-2 codex panel enablement (D2 liveness markers) on real podman.

Two automated halves of the §P2.3 e2e boundary (``codex-vscode-BUILD.md``):

1. **Codex config delivery on a REAL box start** — the full
   ``start.py → Target seams → ~/.codex/config.toml`` pipeline against a real
   launch: BOTH managed ``[[hooks.SessionStart]]`` groups (instruction delivery
   + the D2 per-PID liveness marker), both pre-computed ``[hooks.state]`` trust
   entries, directory trust, approval parity — and byte-stability across a
   restart.  The "codex binary" is a copied ``/bin/true`` (a real ELF, so
   ``CodexTarget.detect``'s primary standalone-ELF path accepts it); the box
   exits immediately, which is fine — delivery happens host-side before exec
   and is asserted on the box-home files.

2. **Simulated-panel lifecycle through the REAL panel-watch** — a
   ``--warm-only`` box (supervisor PID-1 in panel-watch mode, NO CLI agent);
   the "panel" is simulated exactly at the two probe surfaces the supervisor
   reads: a process whose cmdline carries a ``.vscode-server`` path segment
   (``vscode_server_present``) and a per-PID marker file under
   ``/tmp/kanibako/agents`` (``scan_marker_pids``).  Asserts the E2f state
   machine on a live container: ALIVE → hands-off (box stays), marker-PID death
   with the panel attached → SELF_HEAL_CLI, all surfaces gone → TEARDOWN
   (container exits).

The third §P2.3 item — ``$PPID`` == agent PID under a REAL codex — is
creds-gated (an unauthenticated codex never reaches SessionStart) and belongs
to the manual dogfood checklist (M1) unless ``KANI_E2E_CODEX_REAL=1`` with a
real authenticated codex is provided; see the skip below.

Timings assume the supervisor defaults (poll_interval 2.0 s); every wait is a
bounded POLL, not a bare sleep, so a slow VM only slows the test, never flakes
it (within the outer bound).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from tests.e2e.conftest import (
    _podman,
    e2e_requires,
    run_kanibako,
)
from tests.e2e.test_instructions_delivery import container_name, rm, run_install

pytestmark = [pytest.mark.e2e, *e2e_requires]

GUEST_HOME = "/home/agent"
MARKERS_DIR = "/tmp/kanibako/agents"


def _is_running(name: str) -> bool:
    r = subprocess.run(
        [_podman, "inspect", "--format", "{{.State.Running}}", name],
        capture_output=True, text=True, timeout=20,
    )
    return r.returncode == 0 and r.stdout.strip() == "true"


def _poll(predicate, *, timeout: float, interval: float = 1.0) -> bool:
    """Poll *predicate* until true or *timeout* elapses; returns the last value."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def _exec(name: str, script: str, *, detach: bool = False):
    argv = [_podman, "exec"]
    if detach:
        argv.append("-d")
    argv += [name, "sh", "-c", script]
    return subprocess.run(argv, capture_output=True, text=True, timeout=30)


def _seed_codex_stub(e2e_env: dict) -> None:
    """Make the claude e2e fixture env codex-capable.

    * a REAL ELF named ``codex`` on the fixture PATH (``CodexTarget.detect``'s
      primary path reads the magic bytes — a script stub would be rejected);
    * ``OPENAI_API_KEY`` in the env so ``check_auth`` passes without an
      ``auth.json`` (the lenient env-var arm).
    """
    bin_dir = e2e_env["home"] / ".local" / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2("/bin/true", bin_dir / "codex")
    os.chmod(bin_dir / "codex", 0o755)
    e2e_env["env"]["OPENAI_API_KEY"] = "kani-e2e-fake-key"


def _find_box_codex_config(e2e_env: dict) -> Path:
    """The BOX-HOME host-side ~/.codex/config.toml the launch delivery wrote.

    Filtered to the box-home tree (``boxes/*/home/``): the data home ALSO holds
    an agent-template copy of ``.codex/config.toml`` (the packaged template the
    seed stages), which a bare rglob would pick up and trip the ambiguity
    assert (bifrost finding).
    """
    candidates = [
        p
        for p in e2e_env["data_home"].rglob(".codex/config.toml")
        if "boxes" in p.parts and "home" in p.parts
    ]
    assert candidates, (
        ".codex/config.toml not delivered under any box home "
        f"(data home: {e2e_env['data_home']})"
    )
    assert len(candidates) == 1, f"ambiguous box homes: {candidates}"
    return candidates[0]


def test_codex_delivery_real_box(e2e_env):
    """Real `start --agent codex` delivers the FULL managed config.toml:
    directive + D2 marker SessionStart groups, both trust entries, project
    trust, approval parity — and a restart is byte-identical (idempotent on the
    real path)."""
    env, project, box = e2e_env["env"], e2e_env["project"], "codexpanel-deliv"
    _seed_codex_stub(e2e_env)
    run_install(env)

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    try:
        run_kanibako(["start", box, "--agent", "codex"], env=env, timeout=90)
        cfg_path = _find_box_codex_config(e2e_env)
        first = cfg_path.read_bytes()
        data = tomllib.loads(first.decode())

        groups = data["hooks"]["SessionStart"]
        commands = [g["hooks"][0]["command"] for g in groups]
        assert len(groups) == 2, f"expected directive+marker groups, got {commands}"
        assert "import-directives.py" in commands[0]
        assert MARKERS_DIR in commands[1] and "$PPID" in commands[1]
        box_cfg = f"{GUEST_HOME}/.codex/config.toml"
        assert set(data["hooks"]["state"]) == {
            f"{box_cfg}:session_start:0:0",
            f"{box_cfg}:session_start:1:0",
        }
        for entry in data["hooks"]["state"].values():
            assert entry["trusted_hash"].startswith("sha256:")
        assert data["projects"][f"{GUEST_HOME}/workspace"]["trust_level"] == "trusted"
        # default auto_approve=True → the panel-permission seam delivered parity.
        assert data["approval_policy"] == "never"
        assert data["sandbox_mode"] == "workspace-write"
        assert "SessionEnd" not in first.decode()  # codex has no such event

        # restart → byte-identical (both seams idempotent on the real path).
        run_kanibako(["start", box, "--agent", "codex"], env=env, timeout=90)
        assert cfg_path.read_bytes() == first, "restart changed delivered bytes"
    finally:
        rm(container_name(box))


def test_simulated_panel_marker_lifecycle(e2e_env):
    """warm-only box + simulated panel: ALIVE marker holds the box up; killing
    the marker PID with the panel surface attached triggers SELF_HEAL_CLI; all
    surfaces gone → TEARDOWN (container exits)."""
    env, project, box = e2e_env["env"], e2e_env["project"], "codexpanel-watch"
    run_install(env)
    name = container_name(box)

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    try:
        r = run_kanibako(
            ["start", box, "--warm-only", "--detach",
             "-e", "CLAUDE_STUB_MODE=long-running"],
            env=env, timeout=120,
        )
        assert _poll(lambda: _is_running(name), timeout=30), (
            f"warm box never came up: rc={r.returncode} stderr={r.stderr[-300:]!r}"
        )

        # Simulated PANEL surface: a live process whose cmdline carries a
        # `.vscode-server` path segment (what vscode_server_present matches).
        r = _exec(
            name,
            "mkdir -p ~/.vscode-server/bin"
            " && cp /bin/sleep ~/.vscode-server/bin/kani-e2e-panel"
            " && ~/.vscode-server/bin/kani-e2e-panel 600",
            detach=True,
        )
        assert r.returncode == 0, f"panel-surface sim failed: {r.stderr}"

        # Simulated PANEL AGENT: a live process + its per-PID marker file —
        # exactly what the D2 SessionStart hook writes.
        # NB: plain `;` before the backgrounded sleep — an `&&` list would be
        # backgrounded WHOLE and `$!` would name the subshell, not the sleep.
        r = _exec(
            name,
            f"mkdir -p {MARKERS_DIR}; "
            f"sleep 600 & pid=$!; "
            f'printf %s "$pid" > {MARKERS_DIR}/$pid',
            detach=False,
        )
        assert r.returncode == 0, f"panel-agent sim failed: {r.stderr}"

        # ALIVE → hands-off: the box stays up well past several poll ticks.
        time.sleep(8)  # > 3 ticks at the default 2.0 s poll
        assert _is_running(name), "panel-watch tore down an ALIVE panel agent"
        # ...and hands-off means NO self-heal fired: an ALIVE marker must not
        # spawn a CLI agent.  This also guards the simulation itself — if the
        # backgrounded sleep died with its exec session, the marker PID would
        # be dead and the state machine would take the SELF_HEAL path here,
        # silently passing the later phases through the wrong transitions.
        probe = _exec(name, "tmux list-sessions 2>/dev/null | wc -l")
        assert probe.stdout.strip() in ("", "0"), (
            "premature SELF_HEAL_CLI while the panel agent is ALIVE "
            f"(tmux sessions: {probe.stdout.strip()!r})"
        )

        # Kill the marker PID (panel agent dies; surface still attached) →
        # DEAD + vscode_server → SELF_HEAL_CLI: a CLI agent appears in tmux.
        r = _exec(name, f"kill $(cat {MARKERS_DIR}/*) 2>/dev/null; true")
        assert r.returncode == 0, f"marker kill failed: {r.stderr}"

        def _healed() -> bool:
            probe = _exec(name, "tmux list-sessions 2>/dev/null | wc -l")
            return probe.returncode == 0 and probe.stdout.strip() not in ("", "0")

        assert _poll(_healed, timeout=30), (
            "no self-healed CLI agent (tmux session) after panel-agent death "
            "with the panel surface still attached"
        )
        assert _is_running(name), "box died during self-heal"

        # ALL surfaces + agents gone → TEARDOWN: kill the healed agent's tmux
        # and the fake panel-server process; the ref-count closes the box.
        _exec(name, "pkill -f kani-e2e-panel; true")
        _exec(name, "tmux kill-server 2>/dev/null; true")
        assert _poll(lambda: not _is_running(name), timeout=60), (
            "box never tore down after all surfaces and agents were gone"
        )
    finally:
        rm(name)


@pytest.mark.skipif(
    os.environ.get("KANI_E2E_CODEX_REAL") != "1",
    reason=(
        "needs a REAL authenticated codex (an unauthenticated codex never "
        "reaches SessionStart, so the $PPID==agent-PID validation cannot run); "
        "set KANI_E2E_CODEX_REAL=1 on a host with codex + creds — otherwise "
        "this check lives on the manual dogfood checklist (M1, "
        "codex-vscode-BUILD.md §P2.3)"
    ),
)
def test_codex_ppid_marker_real_cli(e2e_env):
    """CREDS-GATED: with a real codex, the SessionStart marker hook writes a
    file named for the codex process's PID (validates the $PPID assumption for
    the CLI half; the panel/app-server half stays manual M1)."""
    env, project, box = e2e_env["env"], e2e_env["project"], "codexpanel-ppid"
    run_install(env)
    name = container_name(box)
    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    try:
        run_kanibako(["start", box, "--detach", "--agent", "codex"], env=env,
                     timeout=120)
        assert _poll(lambda: _is_running(name), timeout=30)

        def _marker_matches_codex_pid() -> bool:
            markers = _exec(name, f"ls {MARKERS_DIR} 2>/dev/null")
            pids = _exec(name, "pgrep -x codex | head -5")
            if markers.returncode != 0 or pids.returncode != 0:
                return False
            marker_set = set(markers.stdout.split())
            pid_set = set(pids.stdout.split())
            return bool(marker_set & pid_set)

        assert _poll(_marker_matches_codex_pid, timeout=60), (
            "no marker file named for a live codex PID — the $PPID assumption "
            "does not hold for the codex CLI"
        )
    finally:
        rm(name)
