#!/usr/bin/env python3
"""Report RESIDENT CONTEXT for each subagent of the current Claude Code session.

WHY THIS EXISTS
---------------
Per-subagent context occupancy is NOT a supported interface: hook payloads carry
`agent_id`/`agent_type` but no token data, and the status line (the source of
`~/.claude/context-status.json`) runs for the TOP-LEVEL SESSION ONLY. A subagent
asked to read that file gets the DIRECTOR's numbers.

But the per-agent transcript records a `usage` block per response, and

    input_tokens + cache_creation_input_tokens + cache_read_input_tokens

of the LAST such record is the full prompt of the most recent request — i.e. the
agent's RESIDENT CONTEXT. Verified 2026-08-06c against the director's own
`context-status.json`, where that same sum equals `total_input_tokens` exactly.

⚑ DO NOT substitute either of these — both were tried and both are WRONG:
  * `subagent_tokens` from a task-completion notification is CUMULATIVE BILLED
    tokens across every turn (re-sent context inflates it). P0 billed 213,236
    but ENDED at 208,926.
  * transcript BYTE SIZE is logged traffic plus JSON escaping. For that same
    agent it implied ~270,000 — high by ~30%.

⚑ FRAGILITY: the transcript JSONL format is internal to Claude Code and is
documented as changing between releases. If this script goes quiet or reports
zeros, suspect a format change first, not an idle agent.

USAGE
    subagent-context.py                 # all agents in the newest session dir
    subagent-context.py --ceiling 240000
    subagent-context.py --session-dir /tmp/claude-1000/<proj>/<session>/tasks
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

DEFAULT_ROOT = "/tmp/claude-1000"
DEFAULT_CEILING = 240_000
DEFAULT_STALE_MIN = 5.0


def _find_usage(obj):
    """Depth-first search for a ``usage`` dict anywhere in a transcript record."""
    if isinstance(obj, dict):
        u = obj.get("usage")
        if isinstance(u, dict):
            return u
        for v in obj.values():
            found = _find_usage(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_usage(v)
            if found:
                return found
    return None


def _blocks(rec):
    """Yield (kind, name) for each content block of a transcript record.

    Defensive: transcript lines are not all dicts, and ``message`` is not always
    a dict either. A crash here would take out the whole report over one odd
    line, so anything unexpected simply yields nothing.
    """
    if not isinstance(rec, dict):
        return
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return
    content = msg.get("content")
    if isinstance(content, list):
        for b in content:
            if isinstance(b, dict):
                yield b.get("type"), b.get("name")


def resident_context(path: str) -> tuple[int, int, str]:
    """Return (resident_tokens, usage_record_count, pending_tool).

    ``pending_tool`` is the name of a tool call that was ISSUED AND NEVER
    ANSWERED — i.e. the agent is blocked inside it. Empty string means the
    transcript ends cleanly (the agent finished, however long ago).

    ⚑ THIS IS THE DISCRIMINATOR THAT MAKES STALL DETECTION USEFUL. Idle time
    ALONE cannot tell a hung agent from one that completed hours ago — every
    finished agent looks "idle" forever. Only an unanswered tool_use means
    stuck.
    """
    last, count = None, 0
    open_tool = ""          # tool issued, result not yet seen
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except (ValueError, TypeError):
                continue
            u = _find_usage(rec)
            if u:
                last, count = u, count + 1
            for kind, name in _blocks(rec):
                if kind == "tool_use":
                    open_tool = name or "?"
                elif kind == "tool_result":
                    open_tool = ""
    if not last:
        return 0, 0, ""
    return (
        last.get("input_tokens", 0)
        + last.get("cache_creation_input_tokens", 0)
        + last.get("cache_read_input_tokens", 0)
    ), count, open_tool


def newest_tasks_dir(root: str) -> str | None:
    dirs = glob.glob(os.path.join(root, "*", "*", "tasks"))
    return max(dirs, key=os.path.getmtime) if dirs else None


GATE_LOCK = os.environ.get("KANI_GATE_LOCK", "/tmp/kanibako-gate.lock")
GATE_PROGRESS = os.environ.get("KANI_GATE_PROGRESS", "/tmp/kanibako-gate-progress")


def render_gate(tail: int = 5) -> int:
    """Render the gate runner's lock + heartbeat (DS-BL5).

    The point is that a PARENT has no visibility into a child blocked inside a
    long ``Bash`` call, so "is the gate alive?" cannot be answered by asking the
    child — it is answered here, from what the TOOL wrote.  ⚑ Judge liveness by
    the heartbeat's TIMESTAMP, never by a transcript's byte size (a live child
    read 127 bytes for ~19 min once, and that false 'stillborn' reading caused
    work already in flight to be re-issued).
    """
    owner = os.path.join(GATE_LOCK, "owner")
    running = False
    if os.path.isdir(GATE_LOCK):
        try:
            with open(owner) as fh:
                pid, started = (fh.read().split("\n") + ["", ""])[:2]
            alive = False
            if pid.strip().isdigit():
                try:
                    os.kill(int(pid), 0)
                    alive = True
                except OSError:
                    alive = False
            running = alive
            state = "RUNNING" if alive else "STALE (owner is gone — next run reclaims it)"
            print(f"gate lock: {state}  pid={pid.strip() or '?'}  since={started.strip() or '?'}")
        except OSError:
            print(f"gate lock: present but unreadable ({GATE_LOCK})")
    else:
        print("gate lock: free — no gate is running")

    if not os.path.exists(GATE_PROGRESS):
        print(f"heartbeat: none at {GATE_PROGRESS}")
        return 0
    with open(GATE_PROGRESS) as fh:
        lines = [ln.rstrip("\n") for ln in fh if ln.strip()]
    if not lines:
        print("heartbeat: file present but empty (run has not finished a file yet)")
        return 0
    last = lines[-1].split()
    age = time.time() - os.path.getmtime(GATE_PROGRESS)
    print(f"progress : {last[1] if len(last) > 1 else '?'} files done "
          f"· last write {age/60:.1f} min ago")
    if running and age > 300:
        print("⚑ heartbeat is >5 min stale while the lock is held — a single test file "
              "should not take that long; suspect a hang, not slowness.")
    fails = [ln for ln in lines if not ln.endswith("rc=0")]
    if fails:
        print(f"non-zero so far ({len(fails)}) — ⚑ rc=5 is integration DESELECTION, not failure:")
        for ln in fails:
            print("   " + ln)
    for ln in lines[-tail:]:
        print("   " + ln)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--gate", action="store_true",
                    help="render the gate runner's lock + heartbeat instead of agent contexts")
    ap.add_argument("--session-dir", help="a .../tasks directory; default = newest")
    ap.add_argument("--ceiling", type=int, default=DEFAULT_CEILING)
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--all", action="store_true",
                    help="include agents that already finished")
    ap.add_argument("--stale-min", type=float, default=DEFAULT_STALE_MIN,
                    help="minutes with no transcript write => flag as possibly stalled")
    args = ap.parse_args()

    if args.gate:
        return render_gate()

    tasks = args.session_dir or newest_tasks_dir(args.root)
    if not tasks or not os.path.isdir(tasks):
        print(f"no tasks dir found under {args.root}", file=sys.stderr)
        return 1

    rows = []
    for path in glob.glob(os.path.join(tasks, "*.output")):
        tokens, count, pending = resident_context(path)
        if count:
            rows.append((tokens, count, os.path.basename(path)[:17],
                         os.path.getmtime(path), pending))
    if not rows:
        print("no usage records found — suspect a transcript FORMAT CHANGE", file=sys.stderr)
        return 1

    rows.sort(reverse=True)
    now = time.time()
    print(f"{tasks}\nceiling {args.ceiling:,}  ·  stall threshold {args.stale_min} min\n")
    print(f"{'agent':<19}{'resident':>10}  {'turns':>6}  {'idle':>8}  status")
    stalled = []
    for tokens, count, name, mtime, pending in rows:
        idle_min = (now - mtime) / 60
        pct = 100 * tokens / args.ceiling
        flags = []
        if tokens >= args.ceiling:
            flags.append("OVER-CEILING")
        elif pct >= 85:
            flags.append("near-ceiling")
        # STALLED = idle AND blocked in an unanswered tool call. Idle alone
        # just means "finished a while ago".
        if pending and idle_min >= args.stale_min:
            flags.append(f"STALLED in {pending}")
            stalled.append((name, idle_min, pending))
        elif pending:
            flags.append(f"busy in {pending}")
        if not args.all and not pending and idle_min >= args.stale_min:
            continue        # completed agent; hide unless --all
        print(f"{name:<19}{tokens:>10,}  {count:>6}  {idle_min:7.1f}m  "
              f"{pct:4.0f}%  {' '.join(flags)}")

    if stalled:
        # ⚑ A STALLED agent cannot report its own stall — it is blocked INSIDE a
        # tool call and has no turn to speak in. That is why this check is
        # EXTERNAL. Observed 2026-08-06c: a Writer delegated its gate run to a
        # child agent; the child was STILLBORN (transcript = the prompt record
        # only, 127 bytes, zero assistant turns) and the parent waited ~19 min
        # with no timeout and no way to notice.
        print("\n⚑ POSSIBLE STALLS — check each before assuming it is broken:")
        for name, idle_min, pending in stalled:
            print(f"   {name}  idle {idle_min:.0f} min, blocked in {pending}")
        print("   If blocked in `Agent`, it is waiting on a CHILD.")
        print("   ⚑ RUN THIS TOOL AGAIN AND LOOK FOR THE CHILD IN THIS TABLE.")
        print("      A child `busy in Bash` with a rising turn count is ALIVE and")
        print("      probably running the (slow) gate — the parent is fine, just")
        print("      waiting. Do NOT tell it to redo the work.")
        print("   ⚑ DO NOT judge a child by its .output FILE SIZE. Transcript")
        print("      writes are BUFFERED: a live child read 127 bytes for ~19 min")
        print("      here, and that false 'stillborn' reading caused BOTH the")
        print("      parent AND the director to re-issue work already in flight")
        print("      (2026-08-06c). Turn count is the signal; size is not.")
        print("   Only if the child has NO usage records at all is it truly dead.")
        print("   ⚑ Prevention (Jei's rule, 2026-08-06c — NOT a blanket ban):")
        print("      DELEGATE the gate only if you have work to do WHILE it runs;")
        print("      if the gate is your LAST step, run it yourself. Blocking-wait")
        print("      delegation buys nothing and adds a failure mode. Delegation")
        print("      that preserves parallelism is a GOOD trade.")
        print("      The hard limit is on CONCURRENCY, not on who launches it:")
        print("      the gate is a SERIALIZED resource (~3 GiB RSS per invocation).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
