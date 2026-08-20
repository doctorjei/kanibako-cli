# Box Lifecycle — client-attachment detection, the shared PID-1 primitive

`box_lifecycle` answers one question for the box's keep-alive PID-1: **which clients are attached
right now, and did that change since the last tick?** It is the detection half of the always-on
design (`split-brain-persistence-DESIGN.md`, "Mechanism — PID-1 lifecycle watcher"); the acting half
is `box_supervisor`, which imports from here and never re-implements detection.

Two client surfaces are watched today — the in-box VS Code server (a panel / attach) and a tmux
TERMINAL client (an interactive shell) — and the module emits ATTACH / DETACH transitions between
snapshots of them. Those transitions drive two later increments: the always-on agent self-heals on
DETACH (E2), and the credential writeback fires on DETACH (D / GAP-1).

## What this module is, and what it deliberately is not

This is INCREMENT 1: the PURE detection + transition logic plus a thin, INJECTABLE system-probe
layer. It makes NO launch-model changes and runs no supervisor loop. E2 imports
`snapshot_attach_state` / `canonical_tmux_session_pid` and drives the loop itself; D subscribes to
the `LifecycleEvent.DETACH` that `classify_transition` emits.

Everything here is deterministic and side-effect free EXCEPT the two probe functions at the bottom.
Their only impurity — reading `/proc` cmdlines and shelling `tmux` — is funnelled through injectable
parameters, so tests never touch real processes or a real tmux server.

## The PID-1 contract

`box_lifecycle` and `box_supervisor` are the PID-1 pair: **pinned flat** at the top of the package
(ratified 2026-08-01), **stdlib-only**, and invoked in-box by a dotted literal
(`python3 -m kanibako.box_supervisor`). This module must never be packaged, moved, or given a
non-stdlib import. The full reasoning — why a widened import surface silently degrades every launch
to the bare-shell keep-alive — is in `llm-docs/kanibako/box_supervisor.py.md`, section "The PID-1
contract".

## Tolerance is the house rule

Every probe in this module is TOLERANT: an absent tmux, an empty or garbled output, or a malformed
`/proc` entry resolves to a safe falsy value or `None` — **never an exception**. This is not
defensive habit, it is the PID-1 requirement: code that raises here takes the whole box down with
it.

Concretely:

* `_collect_proc_cmdlines` SKIPS a vanished PID, a permission error, and a kernel thread's empty
  cmdline rather than raising, so it is safe to call from PID-1 on any box. A system with no
  readable `/proc` yields `[]`.
* `_tmux_clients_output` normalises EVERY not-attached / no-tmux condition to the empty string:
  a non-zero exit (no such session, no server) → `""`; a missing tmux binary (`FileNotFoundError`)
  → `""`; any other `OSError` → `""`.
* `canonical_tmux_session_pid` returns `None` when the session or server is absent, tmux is not
  installed, the command fails, or the output is unparseable.

## The VS Code remote-server markers

A running in-box VS Code (or Cursor) server materialises a remote-server tree under `$HOME`. The
directory that tree gets is what the detector keys on: `.vscode-server` covers Stable, the `-*`
suffixes cover the Insiders and OSS channels, and `.cursor-server` is Cursor's fork.

⚑ **`VSCODE_SERVER_DIR_MARKERS` is ILLUSTRATIVE — it enumerates the KNOWN channels for readers and
is NOT the matching rule.** The authoritative test is `is_vscode_server_path_part`, which matches a
`.vscode-server` PREFIX and is deliberately broader so a future `.vscode-server-*` channel still
matches. Editing the tuple does not change matching. Keep the two in loose sync as documentation,
but the function's prefix rule is what runs.

`kanibako.commands.code_cmd` imports that function rather than re-spelling the rule inline — the
function captures the exact test `code_cmd` used to carry itself. It operates on ONE path component
(e.g. an element of `pathlib.Path.parts`), so a caller tests each segment of a resolved or cmdline
path independently.

## The two pure detectors

**`vscode_server_present`** operates over ALREADY-collected process command lines; the impure
collection lives in `_collect_proc_cmdlines`. A cmdline counts as a server when any `/`-delimited
SEGMENT of it names a remote-server dir — so `/home/agent/.vscode-server/bin/<hash>/node` matches on
its `.vscode-server` segment while `/usr/bin/node` and `/home/agent/.local/bin/claude` do not. An
empty iterable is `False`.

**`tmux_terminal_attached`** is a pure string test, and its contract is the output shape of
`tmux list-clients -t <session>`: tmux prints ONE LINE per attached client, and NOTHING when no
client is attached. So a non-empty, non-whitespace output means at least one terminal is attached;
`""` or all-whitespace means not attached. The caller (`snapshot_attach_state`, via
`_tmux_clients_output`) is responsible for passing `""` on any tmux error, which is what keeps this
function pure.

⚑ `-F ""` is NOT used when running `list-clients`. The default one-line-per-client output *is*
exactly the presence signal being tested.

## The attach-state model

`AttachState` is an immutable snapshot of which client surfaces are attached. The two surfaces are
kept as separate named bools rather than a set or a count, so adding a third surface is a single
added field. It is frozen so a prev/cur pair is safe to hold across a watcher tick.

`AttachState._surfaces` is the per-surface flags as a positionally stable tuple.
`classify_transition` compares two states surface-by-surface through it, so extending the model with
a new surface field — added to that tuple — needs no change to the classifier at all.

`LifecycleEvent` is the transition a watcher tick produces, consumed by E2 self-heal and by D.

## `classify_transition` — and why it is DETACH-biased

The rule, in order:

* **`DETACH`** — ANY surface present in `prev` is gone in `cur`. This wins even when a DIFFERENT
  surface simultaneously appeared: a mixed tick, e.g. a terminal detaching while a panel attaches.
* **`ATTACH`** — no surface was lost AND at least one new surface appeared.
* **`NONE`** — no surface changed. This includes the idempotent same-state tick.

⚑ The "any surface LOST ⇒ DETACH" bias is deliberate and safety-critical. D fires the credential
writeback on DETACH, so MISSING a detach — and thereby dropping a just-refreshed token — is strictly
worse than an EXTRA detach, which costs only a redundant, idempotent writeback. When a tick both
loses and gains a surface we therefore report the loss. The case this protects is a watcher polling
fast enough that a real detach-then-attach collapses into a single tick.

## The probe layer

`_Runner` is the subprocess-runner signature the probes call. `subprocess.run` matches it; tests
inject a fake to avoid touching a real tmux. The same alias exists in `box_supervisor` and is
defined separately there on purpose — neither module may depend on the other's privates.

**`_collect_proc_cmdlines`** is the impure companion to `vscode_server_present`. It reads each
`/proc/<pid>/cmdline` (NUL-delimited argv), joins the argv with spaces, and returns the non-empty
lines.

**`snapshot_attach_state`** is the composed impure snapshot: it runs the two pure detectors over
freshly probed system state and returns an `AttachState`, never raising. VS Code server presence
comes from process command lines, by default collected via `_collect_proc_cmdlines`, though a caller
or a test may inject an explicit `proc_cmdlines` iterable to skip the `/proc` read entirely. tmux
terminal attachment comes from `tmux list-clients -t <session>` run through the injectable `run`,
tolerating an absent tmux or a dead session as "not attached". All probing in this module is
confined to this function and the two `_*` helpers.

**`canonical_tmux_session_pid`** is the best-effort PID of the tmux server hosting a session — the
"live marker". It runs `tmux display-message -p -t <session> '#{pid}'` and parses the first integer
printed. ⚑ `#{pid}` is the tmux SERVER pid, not a pane or client pid. E2 uses this value to identify
the canonical always-on instance.
