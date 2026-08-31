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
real authenticated codex is provided.  That gate PROBES the host rather than
trusting the variable, and forwards the credential it found into the fixture's
isolated home; see ``_real_codex_gate`` and ``_forward_real_codex_creds``.

Timings assume the supervisor defaults (poll_interval 2.0 s); every wait is a
bounded POLL, not a bare sleep, so a slow VM only slows the test, never flakes
it (within the outer bound).
"""

from __future__ import annotations

import json
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
# ⚑ Box-side spellings, deliberately INDEPENDENT of the module constants: this file is a
# black box that observes a real container, so its expectations are written out rather
# than imported from the code under test.
PID_ADD_SCRIPT = "~/canon/bible/general/scripts/util/pid-add.sh"
# The e2e fixture box's DEFAULT agent program.  ⚑ Load-bearing for the panel
# simulation below: PID 1 judges a marker's pid against the launch grammar it was
# given, so a simulated panel agent must carry the box's own agent name.
AGENT_PROGRAM = "claude"
PANEL_AGENT_DIR = "/tmp/kani-e2e-panel-agent"


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


def _require_executable(name: str, path: str) -> None:
    """Assert *path* is an executable file inside the LIVE box *name*.

    Names WHICH fact failed: a `podman exec` can fail because the container is
    gone (rc 125) as easily as because the file is absent, and a message that
    blames the file for a missing container sends the next reader into the
    wrong tree.
    """
    probe = _exec(name, f"test -x {path}")
    assert _is_running(name), (
        f"cannot probe {path}: box {name} is NOT running, so this says nothing "
        f"about the file (podman exec: rc={probe.returncode} "
        f"stderr={probe.stderr.strip()!r})"
    )
    assert probe.returncode == 0, (
        f"{path} is not an executable file in the running box {name} — the "
        f"marker hook would silently do nothing (rc={probe.returncode} "
        f"stderr={probe.stderr.strip()!r})"
    )


def _diag(name: str) -> str:
    """Box-side state for a failure message: sessions, processes, markers, PID-1 log.

    An assertion about a remote container is unreadable without the state that
    produced it; every panel-watch assert below carries this.
    """
    parts = []
    for label, script in (
        ("tmux list-sessions", "tmux list-sessions 2>&1"),
        ("ps", "ps -eo pid,ppid,args 2>&1"),
        ("markers", f"ls -l {MARKERS_DIR} 2>&1"),
    ):
        r = _exec(name, script)
        parts.append(f"--- {label} (rc={r.returncode}) ---\n{r.stdout}{r.stderr}")
    logs = subprocess.run(
        [_podman, "logs", "--tail", "80", name],
        capture_output=True, text=True, timeout=30,
    )
    parts.append(f"--- podman logs (PID 1) ---\n{logs.stdout}{logs.stderr}")
    return "\n".join(parts)


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


def _find_box_codex_config(
    e2e_env: dict, start: subprocess.CompletedProcess[str],
) -> Path:
    """The BOX-HOME host-side ~/.codex/config.toml the launch delivery wrote.

    Filtered to the box-home tree (``boxes/*/home/``): the data home ALSO holds
    an agent-template copy of ``.codex/config.toml`` (the packaged template the
    seed stages), which a bare rglob would pick up and trip the ambiguity
    assert (bifrost finding).

    *start* is the launch whose delivery this reads, carried for the failure
    message alone: a start that never ran leaves nothing under any box home, and
    a bare "not delivered" then blames the delivery seam for a launch failure.
    ⚑ Its rc is deliberately NOT asserted.  A foreground box whose agent exits
    at once is this test's DESIGN (the ``/bin/true`` codex), and ``start``
    reports that as NON-ZERO — ``_container_exit_code(...) or 1``, since tmux
    masks the inner program's code — so an ``rc == 0`` assert here would red on
    the passing case.
    """
    candidates = [
        p
        for p in e2e_env["data_home"].rglob(".codex/config.toml")
        if "boxes" in p.parts and "home" in p.parts
    ]
    assert candidates, (
        ".codex/config.toml not delivered under any box home "
        f"(data home: {e2e_env['data_home']}; start: rc={start.returncode} "
        f"stderr={start.stderr[-300:]!r})"
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
        r = run_kanibako(["start", box, "--agent", "codex"], env=env, timeout=90)
        cfg_path = _find_box_codex_config(e2e_env, r)
        first = cfg_path.read_bytes()
        data = tomllib.loads(first.decode())

        groups = data["hooks"]["SessionStart"]
        commands = [g["hooks"][0]["command"] for g in groups]
        assert len(groups) == 2, f"expected directive+marker groups, got {commands}"
        assert "/opt/kanibako/kanibako/scripts/import-directives.py" in commands[0]
        # ⚑ MARKERS_DIR is deliberately NOT expected in the command any more: the
        # marker hook is a CALL into the bible's PID helper, which is where the
        # ``${KANIBAKO_AGENT_MARKERS_DIR:-…}`` fallback now lives.
        assert PID_ADD_SCRIPT in commands[1] and '"$PPID"' in commands[1]
        # ⚑ That the CALLED script really exists in a codex box is a claim about a
        # LIVE container, which this test does not have — its box exits the moment
        # the ``/bin/true`` codex does.  It is asserted by
        # ``test_codex_box_has_bible_pid_helper`` below, on a detached box.
        box_cfg = f"{GUEST_HOME}/.codex/config.toml"
        assert set(data["hooks"]["state"]) == {
            f"{box_cfg}:session_start:0:0",
            f"{box_cfg}:session_start:1:0",
        }
        for entry in data["hooks"]["state"].values():
            assert entry["trusted_hash"].startswith("sha256:")
        assert data["projects"][f"{GUEST_HOME}/workspace"]["trust_level"] == "trusted"
        # default access=full → the panel-permission seam delivered parity.
        assert data["approval_policy"] == "never"
        # sandbox_mode is a box invariant (danger-full-access), not yolo-gated.
        assert data["sandbox_mode"] == "danger-full-access"
        assert "SessionEnd" not in first.decode()  # codex has no such event

        # restart → byte-identical (both seams idempotent on the real path).
        # ⚑ VACUOUSLY SATISFIABLE: a restart that never ran leaves the bytes trivially
        # identical. No discriminator is testable without a real box; boarded.
        r = run_kanibako(["start", box, "--agent", "codex"], env=env, timeout=90)
        assert cfg_path.read_bytes() == first, (
            f"restart changed delivered bytes (restart: rc={r.returncode} "
            f"stderr={r.stderr[-300:]!r})"
        )
    finally:
        rm(container_name(box))


def test_codex_box_has_bible_pid_helper(e2e_env):
    """A CODEX box really carries the bible PID helper the D2 marker hook calls.

    The delivered ``config.toml`` only proves the hook COMMAND names the script;
    an agent-gated ``bible/general`` bind would leave that command calling
    nothing, and no config-bytes check can see it.  Needs a LIVE box, hence
    ``--detach``: a foreground codex box exits with its ``/bin/true`` codex.
    """
    env, project, box = e2e_env["env"], e2e_env["project"], "codexpanel-rom"
    _seed_codex_stub(e2e_env)
    run_install(env)
    name = container_name(box)

    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    try:
        r = run_kanibako(["start", box, "--detach", "--agent", "codex"],
                         env=env, timeout=120)
        assert _poll(lambda: _is_running(name), timeout=30), (
            f"codex box never came up: rc={r.returncode} stderr={r.stderr[-300:]!r}"
        )
        _require_executable(name, PID_ADD_SCRIPT)
    finally:
        rm(name)


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
        # ⚑ TWO constraints the obvious `sleep 600` fails, both measured on a real
        # box.  (1) It must READ AS THE AGENT, not merely be alive:
        # ``scan_marker_pids`` reaps a marker whose pid is alive but whose argv is
        # not the box's launch grammar (``agent_session_verdict``), so a bare
        # ``sleep`` marker is STALE on the very first tick and the box self-heals.
        # A copy named for the agent whose FIRST argument is an OPTION gives the
        # ``(claude, None)`` head a bare ``claude <flags>`` launch has — what a
        # panel-launched agent looks like to PID 1.  (2) It must SURVIVE the exec
        # session that starts it: `tail -f` exits when its stdout pipe closes
        # (coreutils' output-alive check) and `sleep` does not — hence an
        # option-first `sleep -- 600` rather than a `tail -f /dev/null`.
        # NB: plain `;` before the backgrounded agent — an `&&` list would be
        # backgrounded WHOLE and `$!` would name the subshell, not the agent.
        r = _exec(
            name,
            f"mkdir -p {MARKERS_DIR} {PANEL_AGENT_DIR} || exit 1; "
            f"cp /bin/sleep {PANEL_AGENT_DIR}/{AGENT_PROGRAM} || exit 1; "
            f"{PANEL_AGENT_DIR}/{AGENT_PROGRAM} -- 600 & pid=$!; "
            f'printf %s "$pid" > {MARKERS_DIR}/$pid || exit 1; '
            f'printf %s "$pid"',
            detach=False,
        )
        assert r.returncode == 0, f"panel-agent sim failed: {r.stderr}"
        agent_pid = r.stdout.strip()
        # ⚑ The simulation must RED ON ITS OWN EMPTINESS.  A panel agent that never
        # started, or died with its exec session, leaves a marker naming a dead pid
        # — which IS the DEAD state, so every phase below would pass through the
        # wrong transitions and report a state machine it never exercised.
        assert _exec(name, f"kill -0 {agent_pid}").returncode == 0, (
            f"simulated panel agent (pid {agent_pid!r}) is not alive — the "
            f"simulation, not the supervisor, is what failed\n{_diag(name)}"
        )

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
            f"(tmux sessions: {probe.stdout.strip()!r})\n{_diag(name)}"
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
            f"with the panel surface still attached\n{_diag(name)}"
        )
        assert _is_running(name), "box died during self-heal"

        # ALL surfaces + agents gone → TEARDOWN: kill the healed agent's tmux
        # and the fake panel-server process; the ref-count closes the box.
        _exec(name, "pkill -f kani-e2e-panel; true")
        _exec(name, "tmux kill-server 2>/dev/null; true")
        assert _poll(lambda: not _is_running(name), timeout=60), (
            "box never tore down after all surfaces and agents were gone"
            f"\n{_diag(name)}"
        )
    finally:
        rm(name)


# --------------------------------------------------------------------------- #
# The REAL-codex credential gate                                              #
# --------------------------------------------------------------------------- #
# ⚑ Everything below reads the HOST's home — the one place in this module that
# does.  Every other path here is the fixture's ISOLATED home, and the gap
# between the two is precisely the defect this section closes.


def _host_codex_auth() -> Path | None:
    """The host user's real ``~/.codex/auth.json``, or ``None`` if absent/empty.

    ⚑ Runs at COLLECTION time (the skip marker below is built at module scope),
    so like ``conftest``'s probes it must never raise: an unresolvable home or an
    unreadable file is simply "this host has no codex credential".

    ⚑ The predicate is ``CodexTarget.check_auth``'s, ``is_file()`` included.  A
    DIRECTORY at that path stats non-empty, so dropping the ``is_file()`` would
    let it open the gate below and then raise ``IsADirectoryError`` out of
    :func:`_forward_real_codex_creds`'s ``copy2`` — an ERROR where the honest
    answer is a skip.
    """
    try:
        auth = Path.home() / ".codex" / "auth.json"
        return auth if auth.is_file() and auth.stat().st_size > 0 else None
    except (OSError, RuntimeError):
        return None


_HOST_CODEX_AUTH = _host_codex_auth()


def _real_codex_gate() -> str | None:
    """Why this host cannot run the REAL-codex check, or ``None`` if it can.

    ⚑ Every clause PROBES.  The gate used to be the opt-in variable alone, which
    asserted a precondition — "a real authenticated codex" — that nothing checked
    and the isolated-``HOME`` fixture then made unreachable: codex found no
    credential, printed an OAuth URL, and blocked on interactive login until the
    subprocess budget SIGKILLed the start.  That is a failure no host could
    avoid, however well authenticated (measured 2026-08-30), so the skip
    condition has to name the real preconditions and
    :func:`_forward_real_codex_creds` has to satisfy the one the fixture broke.
    """
    if os.environ.get("KANI_E2E_CODEX_REAL") != "1":
        return (
            "opt-in: needs a REAL authenticated codex (an unauthenticated codex "
            "never reaches SessionStart, so the $PPID==agent-PID validation "
            "cannot run); set KANI_E2E_CODEX_REAL=1 on a host with codex + "
            "creds — otherwise this check lives on the manual dogfood checklist "
            "(M1, codex-vscode-BUILD.md §P2.3)"
        )
    if shutil.which("codex") is None:
        return (
            "KANI_E2E_CODEX_REAL=1 but no `codex` on PATH — CodexTarget.detect "
            "would find no binary to bind"
        )
    if _HOST_CODEX_AUTH is None and not os.environ.get("OPENAI_API_KEY"):
        return (
            "KANI_E2E_CODEX_REAL=1 but this host has no codex credential to "
            "forward: neither a non-empty ~/.codex/auth.json nor OPENAI_API_KEY"
        )
    return None


_REAL_CODEX_SKIP = _real_codex_gate()


def _forward_real_codex_creds(e2e_env: dict) -> None:
    """Put the HOST's codex credential where the ISOLATED fixture home can see it.

    ``e2e_env`` hands every test a private ``HOME`` under ``/tmp``; the user's
    codex login lives in the real ``~/.codex``.  The credential therefore has to
    make one hop, and the destination is ``<isolated home>/.codex/auth.json`` —
    the exact host source the codex descriptor's declared ``cred_files`` entry
    already syncs into the box (``host_rel`` == ``home_rel`` ==
    ``.codex/auth.json``).  NO new delivery route: this only moves the credential
    to where the existing one starts.

    Two sources, in the order ``CodexTarget.check_auth`` itself accepts them: the
    host ``auth.json``, else ``OPENAI_API_KEY`` written into that file's own
    top-level API-key field (codex's spelling for an API-key login).  The gate
    above guarantees one of them exists.

    ⚑ SECRET HYGIENE — a real credential lands in a temp dir here.  The value is
    never bound to a name, never asserted on and never formatted into a message:
    the file arm copies wholesale, and the env arm streams straight from
    ``os.environ`` into a file opened 0600, inside a 0700 directory.
    ⚑ ...and it is deleted at teardown, but only BEST-EFFORT.  ``dest`` sits under
    ``tmp_path``, which this directory's autouse ``_reap_subuid_owned_tmp`` fixture
    reaps for every e2e test, so pytest's tree retention never gets the chance to
    hold the token.  That reap is best-effort by contract — ``reap_tree`` returns
    False rather than raising, and the conftest then prints a diagnostic — so the
    credential outlives the run only when the reap FAILS, and that case announces
    itself.  The modes keep it unreadable by other users, so this is a RISK to
    weigh before opting in with ``KANI_E2E_CODEX_REAL=1``, not an exposure — if
    the diagnostic fires on a shared host, delete the named tree.
    ⚑ NOT :func:`_seed_codex_stub`, which plants a FAKE key and a ``/bin/true``
    codex — the exact opposite of what this test needs.

    ⚑ ONE-WAY, by construction rather than by care: the credsync WRITEBACK also
    resolves off the subprocess ``HOME``, so a token the box refreshes lands back
    in the ISOLATED copy and the run cannot rewrite the user's real
    ``~/.codex/auth.json``.  The flip side is the reader's to know — an OAuth
    refresh inside the box rotates a token the host copy still holds the old
    half of, so re-running ``codex login`` on the host is the recovery.
    """
    dest = e2e_env["home"] / ".codex" / "auth.json"
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode= is a silent no-op if .codex already exists, so state the 0700
    # outright — the same reason dest.chmod follows the 0600 os.open below.
    dest.parent.chmod(0o700)
    if _HOST_CODEX_AUTH is not None:
        shutil.copy2(_HOST_CODEX_AUTH, dest)
    else:
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"OPENAI_API_KEY": os.environ["OPENAI_API_KEY"]}, fh)
    dest.chmod(0o600)


@pytest.mark.skipif(_REAL_CODEX_SKIP is not None, reason=_REAL_CODEX_SKIP or "")
def test_codex_ppid_marker_real_cli(e2e_env):
    """CREDS-GATED: with a real codex, the SessionStart marker hook writes a
    file named for the codex process's PID (validates the $PPID assumption for
    the CLI half; the panel/app-server half stays manual M1)."""
    env, project, box = e2e_env["env"], e2e_env["project"], "codexpanel-ppid"
    _forward_real_codex_creds(e2e_env)
    run_install(env)
    name = container_name(box)
    r = run_kanibako(["create", str(project), "--name", box], env=env)
    assert r.returncode == 0, f"create failed: {r.stderr}"
    try:
        r = run_kanibako(["start", box, "--detach", "--agent", "codex"], env=env,
                         timeout=120)
        assert _poll(lambda: _is_running(name), timeout=30), (
            f"real-codex box never came up: rc={r.returncode} "
            f"stderr={r.stderr[-300:]!r}"
        )

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
