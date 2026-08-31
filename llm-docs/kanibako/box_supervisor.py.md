# Box Supervisor — the PID-1 keep-alive that outlives sessions

⚠️ **DROP RULE INVERTED FOR THIS FILE.** `box_supervisor.py` is half of the PID-1 pair (with
`box_lifecycle.py`), pinned flat on purpose, stdlib-only, and invoked in-box by a dotted literal.
Its notes are overwhelmingly **platform facts about process / signal / PID-1 / tmux / container
teardown behaviour, paid for once in painful debugging and NOT reproducible from the dev box**
(there is no working podman here). Nothing in that class was dropped. Each such fact is carried here
verbatim in substance and tagged **`[UNVERIFIED-PLATFORM]`**; the index at the bottom lists every
one. *"Sounds odd"* is not evidence of falsehood — only a proof retires one of these.

The source keeps one-line descriptors and `⚑` markers only. The reasons live here.

## Why this module exists

A box used to die with its agent session. The always-on-instance design
(`split-brain-persistence-DESIGN.md`, "E2 BUILD DESIGN") makes a box's keep-alive PID-1 a
**SUPERVISOR**: it runs the agent in a DETACHED tmux session, watches client attach/detach via
`kanibako.box_lifecycle`, and SELF-HEALS the agent (restart with `--continue` plus a continue-marker)
when it dies — so the box persists independent of any one agent session. Design principle **B**: only
a genuine exit-of-everything, or an explicit `kanibako stop`, tears the box down.

⚑ **Read the module's scope as TWO jobs, not one.** Besides the in-box supervisor it is also the
**single source of truth for three host-side launch literals** — `CONTINUE_MARKER`,
`KANIBAKO_PKG_MOUNT_ROOT` and `PINNED_ROOT_RELPATH` / `XDG_PROJECTIONS`. `commands/start.py` imports
`CONTINUE_MARKER` and `KANIBAKO_PKG_MOUNT_ROOT` from here at MODULE scope (`start.py:31`), on the
host, on every launch. The module docstring described only the supervisor job; a reader who took that
framing literally would move a constant out of a "box-only" module and break the host launcher. The
constants live at the LOWEST module that needs them, and that module happens to be this one.

## The PID-1 contract — stdlib-only, pinned flat, dotted-literal invocation

`box_supervisor` and `box_lifecycle` are **pinned flat** (ratified 2026-08-01) and invoked in-box by
a dotted literal (`python3 -m kanibako.box_supervisor`). They must import **stdlib only**, plus each
other and `kanibako.log`.

**VERIFIED (this pass, on the dev box):** `import kanibako.box_supervisor` pulls in exactly
`kanibako`, `kanibako.box_lifecycle`, `kanibako.log` and nothing else from the package;
`box_lifecycle` imports only `__future__ · collections.abc · dataclasses · enum · glob · pathlib ·
subprocess`, and `log` only `__future__ · logging · sys`. The contract holds today.

Why it is load-bearing: every launch runs a forward-compat probe — `import kanibako.box_supervisor`
— whose FAILURE silently degrades the launch to the bare-shell keep-alive. Widening the import
surface (e.g. reaching into the settings package) would put every launch at the mercy of that
package importing cleanly. This is the whole reason `PINNED_ROOT_RELPATH` is a QUARANTINED DUPLICATE
rather than an import; see its entry below.

## Tolerance is the house rule

Like `kanibako.box_lifecycle`, **every** tmux/subprocess call in this module is TOLERANT: a missing
tmux binary or a non-zero exit resolves to a safe falsy value and is logged, never raised. The
supervisor IS PID-1 — a probe or action that crashed the loop would take the whole box down with it.
Both watch loops additionally wrap each tick's body, so a raising probe is logged and the loop
CONTINUES.

## Design for testability

The PURE decision logic (`decide`, `decide_panel`) is split from the impure tmux actions, and every
impure op funnels through an injectable seam on the constructor, so tests drive the whole thing with
no real tmux, no real agent, no real waiting and **no real process**. The seams, all keyword-only:

| Seam | Default | Covers |
|------|---------|--------|
| `run` / `sleep` | `subprocess.run` / `time.sleep` | every tmux subprocess call; the loop + backoff waits |
| `pid_alive` / `list_marker_pids` | `_default_pid_alive` / `_default_list_marker_pids` | the marker probes (E2f liveness, 4a detection) |
| `cmdline_of` / `remove_marker` | `_proc_cmdline` / `_default_remove_marker` | the marker IDENTITY probe and the marker REAP |
| `kill` / `killpg` | `os.kill` / `os.killpg` | the 4b SIGSTOP/SIGCONT and the pane process-GROUP evict |
| `getpgid` / `getpgrp` | `os.getpgid` / `os.getpgrp` | the group resolution the evict's safety guard rules on |
| `reap` | `reap_zombie_children` | the PID-1 child-reap duty at the top of every tick |

⚑ **Nothing in `BoxSupervisor` may reach `os` (or the module reaper) directly** — a unit test that
tripped over such a call would signal a REAL process. The pin is
`test_process_ops_reach_the_os_only_through_the_injected_seam`: it booby-traps the module-level ops
and fails on any direct call, so the rule holds BY CONSTRUCTION, not by convention. The `_default_*`
functions and
`reap_zombie_children` are the REAL implementations that sit BEHIND their seams (exactly as
`subprocess.run` sits behind `run`); their own tests drive them directly.

## The increment map

| Increment | What it added here |
|-----------|--------------------|
| 2 / E2a | the supervisor MODULE itself |
| E2b–E2h | the launch-model wiring in `commands/start.py`; panel-watch (E2f); markers dir (E2g) |
| 4a | single-state DETECTION — `scan_marker_pids` enumerates the per-agent markers dir; `newcomer_pids` flags any live marker PID that is NOT the supervisor's own agent (the split-brain hazard: a second agent has bound the session) |
| 4b | single-writer ENFORCEMENT — `BoxSupervisor._takeover` |
| D | the credential-writeback SIGNAL flag (`_on_detach` / `--creds-flag`) |
| — | the pinned-root XDG projection and the bootstrap-PYTHONPATH scrub (not numbered in the original provenance list, but owned here) |

## Increment 4b — the single-writer takeover

4b covers the **CRITICAL direction** only: a VS Code panel newcomer taking over a live CLI incumbent,
in `run_forever`. PAUSE the newcomer (`SIGSTOP`), a best-effort GRACE plus heads-up to the incumbent,
then EVICT the incumbent as a PROCESS GROUP (`kill_agent_session`, child-kill-with-parent so no
orphaned subagent survives), then RESUME the newcomer (`SIGCONT`) as the sole writer and hand off to
the agentless panel-watch keep-alive.

⚑ **HARD SAFETY GATE.** The destructive evict is gated behind `config.session_takeover` (threaded
from the experimental env `KANIBAKO_SESSION_TAKEOVER`), DEFAULT-OFF, so 4b lands DORMANT. With the
flag OFF both loops handle a newcomer exactly as 4a: LOG-ONLY — no `SIGSTOP`, no send-keys, no
eviction of a newcomer.

⚑ **NOTE the one flag-INDEPENDENT part.** The process-GROUP kill in `kill_agent_session` runs on the
normal TEARDOWN and self-heal-restart paths **regardless** of the flag. That is a strict improvement
— it reaps orphaned subagents a bare `tmux kill-session` would leave — and it is guarded against a
pathological `pgid` (never signals pgid `<= 1` or the supervisor's own group).

The evict TARGET is always the pane incumbent (`_own_agent_pids`, CLI-side validated), never a bare
marker PID. The **REVERSE** direction (a CLI newcomer over a live-panel incumbent, in panel-watch)
has no panel injection vector, so it stays LOG-ONLY (deferred). `kill_agent_session` is BOTH the
total-teardown kill and the 4b eviction primitive.

Design refs: `split-brain-persistence-DESIGN.md` §85-96, §108, §189/§217, §267/§348, §338-359, §346,
§89-96, §86-88. ⚑ Those `§` numbers are LINE numbers into that design doc, and the doc has been
edited since; spot-checked this pass, §85-96 · §267 · §346 land in the right region, §108 lands
adjacent. Treat them as approximate pointers, not addresses.

## `remain-on-exit` — and the two launch policies

`remain-on-exit on` makes a pane whose process exits stay in the session as a **DEAD pane**, holding
its real exit code in `#{pane_dead_status}` and its final output in scrollback. That is what lets an
INSTANT-crashing agent still be diagnosed.

**Which launches arm it:**

* **`on_agent_exit == "self-heal"`** (detached / panel launches) — ARM it. A dead agent leaves a
  capturable dead pane for the restart plus output capture.
* **`on_agent_exit == "teardown"`** (a FOREGROUND CLI launch, human present) — do **NOT** arm it at
  all. Start a plain detached `new-session` so the agent pane CLOSES on ANY exit, clean or crash.
  With no lingering dead pane an attached foreground client returns straight to the shell instead of
  stranding the user on tmux's *"Pane is dead"* overlay. A teardown launch never self-heals, so the
  dead-pane capture / exit-code machinery is not needed (the human saw the agent's output live).

⚠️ **CORRECTED THIS PASS.** `start_agent_session`'s docstring said *"Start the agent DETACHED with
`remain-on-exit` armed globally first"* and `restart_agent_session`'s said *"Arms `remain-on-exit`
globally and starts … in one invocation"* — both **unconditional**, both **false on the teardown
path**, which is the ordinary foreground `kanibako start` launch. Proven by driving the production
path with an injected runner: with `on_agent_exit="teardown"` the emitted argv is exactly
`tmux new-session -d -s kanibako -- <agent>` — no `set-option` at all. The false clauses were dropped,
not relocated; the accurate statement is the bullet list above.

### The four-command arm, and why it is ONE invocation

`_start_session_argv` emits four tmux commands in one invocation, `;`-separated (each `;` a
standalone argument — tmux's command separator):

1. `set-option -g remain-on-exit on` — the server-global default, set BEFORE the pane exists so it is
   effective the instant the agent pane is born.
2. `new-session -d -s <session> -- <agent>` — `-d` detached (agent executing, no client); `--` ends
   new-session's option parsing so the agent grammar is verbatim.
3. `set-option -t <session> remain-on-exit on` — pin the option SESSION-LOCAL so it outlives step 4.
4. `set-option -g remain-on-exit off` — REVERT the global default.

So the global is `on` only across the pane's birth (steps 1→3); any OTHER window sharing this box's
tmux server (a user's own `tmux` windows, a VS Code integrated terminal running tmux) keeps the
normal default and does NOT accumulate lingering dead panes. At every instant the AGENT pane is
covered — by the global (steps 2→3), then by its session-local value (after step 3) — so there is no
gap.

**[UNVERIFIED-PLATFORM]** Arming AFTER a bare `new-session -- <agent>` (the pre-fix approach) loses
the race essentially always — empirically **0/20** instant crashes left a dead pane — while
arm-before-birth won **20/20** on real tmux. Verified likewise that a sibling session's pane is still
destroyed normally.

**[UNVERIFIED-PLATFORM]** All four MUST share one invocation: a separate `set-option -g` on a
not-yet-running server starts a server that immediately exits (no session), so the global would be
gone before `new-session` runs.

### The `;`-in-argv guard

A standalone `;` token inside the agent argv would be read by tmux as a top-level command separator in
the combined form — `--` ends new-session's OWN option parsing, not tmux's command splitting — so it
would break the launch. That essentially never occurs in a real agent grammar, but to avoid making a
rare input WORSE than the pre-fix behavior, `_arm_and_start_session` falls back to a plain
`new-session` followed by a best-effort per-session arm. LAUNCH stays correct; only the instant-crash
dead-pane capture is not guaranteed for that edge.

## The pinned root and the XDG projection

`PINNED_ROOT_RELPATH` (`.kanibako`) is the FIXED box-side root for state that CANNOT resolve without
a live box but MUST be placed BEFORE one exists — the mount and copy destinations the host writes
into the container runtime's arguments (a copy runs at `create`, with no container at all).
`~/.kanibako/<facet>` is the one answer for that whole class.

Once the box IS live, XDG compliance is restored by **projection**: for each row of `XDG_PROJECTIONS`
point `$XDG_STATE_HOME/kanibako` at `~/.kanibako/state`, so a consumer that resolves the XDG way
finds the same files.

⚑ **A SYMLINK, not a bind mount.** **[UNVERIFIED-PLATFORM]** A box runs with an EMPTY effective
capability set, so `mount --bind` in-box fails outright (*"must be superuser to use mount"*); a
symlink needs no privilege and every consumer resolves through it identically.

⚑ **ORDERING IS LOAD-BEARING.** **[UNVERIFIED-PLATFORM]** The projection must run AFTER every podman
mount exists — and PID-1 does by construction. All three podman symlink traps (a SOURCE symlink
dereferenced to its target; a DESTINATION symlink followed to where it points; a destination symlink
into another bind mount getting shadowed by the directory mount) are podman resolving a symlink AT
MOUNT TIME. Created here, after the mounts, this one is invisible to all of them. Created earlier —
baked into the image, or host-side into the box home — it would hit every one. **Do not move this
call earlier, and do not create the link host-side.**

### The two halves, and why there are two

Not every box has a Python PID-1. A bare keep-alive / shell box fronts `tmux new-session --
<box.shell>`; the forward-compat fallback (`commands/start.py` → `_build_supervisor_pid1`) fronts
that SAME shell precisely BECAUSE `import kanibako.box_supervisor` failed; and a helper box enters
through `scripts/helper-init.sh`, which is bash. None of the three can call `project_pinned_xdg`, so
the projection is also emitted as SHELL — the one language all three already speak.

⚑ **The shell half is GENERATED, not a second hand-written rule.** Every literal in
`xdg_projection_sh` comes from `XDG_PROJECTIONS`, `PINNED_ROOT_RELPATH` and `XDG_LINK_NAME`, so a new
facet ROW extends the shell exactly as it extends the Python. `scripts/helper-init.sh` carries the
generated text VERBATIM (bash can import nothing); `tests/test_box_supervisor.py` pins the two
(`assert bs.xdg_projection_sh() in script`).

The two are interchangeable and idempotent against each other: whichever runs first creates the link,
the other takes the "already a symlink" skip.

### Three refusals, each deliberate

* **Never clobber.** If the XDG location already exists as a real directory or file (a box upgraded
  from a release that mounted there), it is LEFT ALONE and logged. Removing it is a user's decision,
  not PID-1's.
* **Never re-point.** An existing symlink aimed somewhere else is left alone too.
* **Never raise.** This is PID-1: a failure logs and the box comes up normally, because the PINNED
  path is the REAL location and everything kanibako reads in-box spells it directly. The projection
  is a convenience for everything else.

Rows whose XDG location already resolves to the pinned dir are skipped — there is nothing to project
when the two are the same place.

### Where the shell twin deliberately DIVERGES (both narrowing)

* It does not LOG the two refusals — PID-1's stderr is `podman logs`, and a shell twin cannot reach
  the module `log` anyway. The ACTIONS are identical.
* The same-place guard compares the two paths as STRINGS where the Python normalises them first.
  Reaching that difference takes BOTH a non-normal spelling of the XDG var (a trailing slash, a
  `/./`) AND a future facet row for which the guard can fire at all — today's `state` row cannot
  reach it from either half, since `$XDG_STATE_HOME/kanibako` can never equal `~/.kanibako/state`
  (`TestProjectPinnedXdg.test_same_path_is_skipped` records the shape that would). Even then the
  shell merely falls through to the create attempt, where `[ ! -e ]` sees the pinned dir and declines
  the link: it makes a directory the Python's skip would have left alone, and clobbers nothing.

### Shape rules inside the generated shell

* The **NESTING mirrors the Python's statement order exactly**, and each rung is observable: the
  same-place guard runs BEFORE anything is created (so that row makes no directory at all), the
  pinned dir is then made whether or not the link is, and only the LINK is withheld by the two
  refusals. Flattening this into one condition would quietly stop creating the pinned dir on a box
  that declines the link.
* ⚑ **`-L` before `-e`:** `-e` is FALSE for a dangling symlink, so an `-e`-only guard would read an
  occupied slot as empty.
* **Guaranteed rc 0** (it ends on `unset`), so a caller may compose it ahead of a `&&`/`||` chain —
  the forward-compat probe in `_build_supervisor_pid1` is exactly such a chain, and a projection that
  changed its exit status would decide which PID-1 the box gets. It is also safe under `set -e`: the
  only fallible list ends in `|| true`.
* The XDG Base Directory spec is honoured identically on both sides: use the var **iff set AND
  absolute**; anything else falls back to the spec default under `$HOME`.

## The host-package mount and the PYTHONPATH scrub

`KANIBAKO_PKG_MOUNT_ROOT` (`/opt/kanibako`) is the in-box read-only bind-mount ROOT of the HOST
kanibako package. `_kanibako_mounts` (`commands/start.py:8014`) lands the host package dir at
`f"{KANIBAKO_PKG_MOUNT_ROOT}/kanibako"`; the in-box `kanibako` CLI puts this dir on `sys.path`
(`scripts/kanibako-entry:13`); and the host launcher `_build_supervisor_pid1` (`start.py:1375`) also
injects it as `PYTHONPATH`.

**Why:** so PID-1 imports THIS supervisor from the fresh host package (version == host CLI) rather
than the image's baked copy. **[UNVERIFIED-PLATFORM]** Published images ship an OLD kanibako WITHOUT
the supervisor module, so importing the baked copy would silently degrade every real launch to the
bare-shell fallback.

Single-sourced HERE — the lowest module that needs it: `start.py` imports it and
`scrub_bootstrap_pythonpath` strips it back out — so the mount dest and the injected/scrubbed
PYTHONPATH can never drift. ⚑ `scripts/kanibako-entry` and `data/core-defaults.yaml` also carry this
literal (the entry script runs BEFORE kanibako is importable, and a YAML data file cannot reference a
constant); keep those literals in sync. *(Both confirmed present this pass:
`data/core-defaults.yaml:227`, `scripts/kanibako-entry:13`.)*

### Why the children must NOT inherit it

The supervisor's CHILDREN — the tmux server and, under it, the AGENT — must not inherit the injected
entry: the agent runs with its own environment and has no business importing kanibako from our host
mount, and an inherited `/opt/kanibako` on the agent's path could also shadow an unrelated package in
the agent's own tree.

**[UNVERIFIED-PLATFORM but stdlib-documented]** `PYTHONPATH` is read by CPython only at interpreter
STARTUP, so mutating the env now cannot disturb THIS already-running supervisor's `sys.path` — it
affects only processes spawned AFTER the call. Call it once, before the watch loop spawns anything.
The scrub removes EXACTLY the mount-root path element, preserving every other entry the image set,
and drops `PYTHONPATH` entirely when nothing else remains.

## `PINNED_ROOT_RELPATH` is a QUARANTINED DUPLICATE

The single source of truth is `kanibako.settings.settings_resolve.BOX_PINNED_ROOT_RELPATH`
(`src/kanibako/settings/settings_resolve.py:80`), which this module deliberately does **NOT** import
— see the stdlib-only contract above. `tests/test_channels/test_helpers.py:485` pins the two literals
together (`PINNED_ROOT_RELPATH == BOX_PINNED_ROOT_RELPATH == ".kanibako"`), and
`scripts/helper-init.sh:42` carries the third (bash can import neither). All three confirmed present
this pass.

⚑ `XDG_PROJECTIONS` has **ONE ROW TODAY and is deliberately a TABLE**: the pinned root is the fixed
answer for the whole resolve-before-liveness class, so a second facet (cache, runtime, …) is a ROW
here — never a second mechanism.

## Zombies and the PID-1 reaping duty

**[UNVERIFIED-PLATFORM]** The supervisor is the box's PID-1, so every ORPHANED in-box process (e.g. a
panel-spawned agent whose own parent already exited) reparents to it — and on exit sits as a ZOMBIE
until PID-1 `wait()`s it. Without reaping, the zombie both leaks a process-table slot and (before the
`_default_pid_alive` zombie check) blinded the liveness probe.

**[UNVERIFIED-PLATFORM]** `kill -0` answers **True for a ZOMBIE**. Since orphans reparent to the
supervisor itself, an unreaped panel agent would read ALIVE **forever** and wedge both
`SELF_HEAL_CLI` and `TEARDOWN`. Hence the second layer: an existing PID is additionally checked
against `/proc/<pid>/stat`, state `Z` ⇒ dead.

Reaping our OWN children is always safe here regardless of PID-1-ness: the supervisor collects no
child exit statuses anywhere else (its only child mechanism is inline `subprocess.run`, which waits
its child synchronously on the same single thread — there is no concurrent waiter to rob), so both
supervise loops call the injected `reap` (default `reap_zombie_children`) unconditionally at the top
of every tick, **FIRST**, before the marker probes.

### Parsing `/proc/<pid>/stat` safely

**[UNVERIFIED-PLATFORM]** The stat line is `<pid> (<comm>) <state> <ppid> …` — and `comm` is
attacker/filename-controlled text that may itself contain spaces AND parentheses (e.g. a process
named `a) (b`), so a naive `split()` is wrong. The kernel emits `comm` as the ONLY parenthesised
field, so the state is the first token AFTER the **last** `)`.

## Constants

```CONTINUE_MARKER = "[Agent handoff - Continue prior task(s)]"```
The continue-marker string a self-heal restart delivers.

Sent via `tmux send-keys` as a real acting turn so a resurrected agent autonomously resumes the prior
task(s) (design §108). Defined ONCE here so the host-side launch wiring (`commands/start.py` →
`--marker`) and the in-box supervisor share a single source of truth — never a duplicated literal.

```TAKEOVER_HEADS_UP```
The heads-up string 4b ENFORCEMENT delivers to a CLI incumbent about to be evicted.

Design §346, *"GRACE + heads-up to the incumbent"*. It lands as a real acting turn if the incumbent
is idle, or queues mid-loop — a best-effort nudge to wind down/checkpoint before the bounded grace
window elapses and the incumbent's process group is evicted. **[UNVERIFIED-PLATFORM]** Injection is
feasible ONLY toward the tmux (CLI) agent; a panel incumbent has no injection vector (the DIRECTIONAL
limit), so this is used only in the CRITICAL direction.

```KANIBAKO_PKG_MOUNT_ROOT = "/opt/kanibako"```
In-box read-only bind-mount ROOT of the HOST kanibako package. — see the section above.

```PINNED_ROOT_RELPATH = ".kanibako"```
FIXED box-side root for state that must be placed before a box exists. — see the section above.

```XDG_PROJECTIONS: tuple[tuple[str, str, str], ...]```
The XDG facets served from the pinned root once the box is LIVE.

Each row is `(env var, XDG spec default suffix, facet dir under the pinned root)`. One row today
(`XDG_STATE_HOME`, `.local/state`, `state`), deliberately a table.

```XDG_LINK_NAME = "kanibako"```
The link's own basename under each projected XDG base — `$XDG_STATE_HOME/kanibako`.

Single-sourced because BOTH halves of the projection spell it: `project_pinned_xdg` (Python) and
`xdg_projection_sh` (shell).

## Injectable-probe type aliases

```_Runner = Callable[..., "subprocess.CompletedProcess[str]"]```
The subprocess-runner signature the tmux actions call.

`subprocess.run` matches it; tests inject a fake so nothing touches a real tmux server. Shared in
spirit with `box_lifecycle`'s `_Runner` (`box_lifecycle.py:177` — same alias, defined separately
because neither module may depend on the other's privates).

```_Sleeper = Callable[[float], None]```
The `time.sleep` signature the loop / backoff use; injectable so tests never actually wait.

```_PidAlive = Callable[[int], bool]```
"Is this PID a live process?" — E2f liveness + 4a detection.

```_MarkersLister = Callable[[str], "list[int]"]```
Returns the PIDs named by the marker FILES in the markers dir (`[]` when the dir is absent/empty).

Both marker probes are injectable so unit tests never touch the real FS / os; the defaults are the
real PID-1 implementations.

```_Signaller = Callable[[int, int], None]```
The `(pid, sig)` shape of BOTH process-signal primitives — `os.kill` and `os.killpg` match it.

```_GroupOf = Callable[[int], int]```
`os.getpgid`: the process GROUP of a pid, resolved before the group-kill safety guard rules on it.

```_OwnGroup = Callable[[], int]```
`os.getpgrp`: the SUPERVISOR's own group — the value the guard refuses to signal.

```_Reaper = Callable[[], int]```
`reap_zombie_children` at its default arity: the PID-1 child-reap duty, returning the count reaped.

The four process-control primitives are injectable for the same reason the runner is, and one degree
more sharply: a unit test that reached the real ops would signal a REAL process. See "Design for
testability" for the full seam table and the test that pins it.

## Functions

```_parse_stat_state(stat_text: str) -> str | None```
PURE: the process STATE character from a `/proc/<pid>/stat` line.

See "Parsing `/proc/<pid>/stat` safely" above for the `comm` hazard. Returns `None` when the text has
no `)` or nothing after it (unparseable — the caller falls back to its kill-0 verdict).

```_proc_stat_state(pid: int) -> str | None```
The kernel state char for *pid* from `/proc/<pid>/stat`, or `None`.

`None` on ANY read/parse problem (proc entry raced away, no `/proc`, …) — tolerant; the caller falls
back to its kill-0 verdict, never raises.

```_default_pid_alive(pid: int) -> bool```
Real `_PidAlive`: is *pid* a live (non-zombie) process?

**[UNVERIFIED-PLATFORM]** Shared PID namespace — the supervisor is PID-1, so it sees the panel agent.
Two layers:

* **EXISTENCE** — `os.kill(pid, 0)` sends no signal but raises when the PID is not a signalable
  process. `ProcessLookupError` ⇒ dead; `PermissionError` ⇒ exists (we merely may not signal it); any
  other `OSError` ⇒ treated as not-live (tolerant — the caller degrades to "not a live agent" rather
  than crashing PID-1).
* ⚑ **ZOMBIE** — see the zombie section above. State `Z` ⇒ dead. An unreadable/unparseable stat
  (`None`) falls back to the kill-0 verdict (True here) — never raises. See also
  `reap_zombie_children`, the PID-1 duty that clears the zombie itself.

```reap_zombie_children(max_reaps: int = 32) -> int```
Bounded non-blocking reap of this process's exited children (PID-1 duty).

Rationale in the zombie section above. BOUNDED to *max_reaps* per call so a burst of zombies cannot
stall a tick (the next tick continues the drain). `ChildProcessError` (no children at all) ends the
sweep; any other `OSError` is swallowed — PID-1's tick must never die here. Returns the number
reaped. `waitpid` returning pid `0` means children exist but none exited: done this tick.

```_default_list_marker_pids(markers_dir: str) -> list[int]```
Real `_MarkersLister`: the PIDs named by the marker FILES in *markers_dir*.

Each live agent session's start hook writes a per-PID marker FILE named for its `$PPID`
(`<dir>/<pid>`; default dir `kanibako.vscode.vscode_config.AGENT_MARKERS_DIR` = `/tmp/kanibako/agents`),
so the FILENAMES enumerate the agent PIDs — no file READ is needed. Parses each entry name as an
`int`, skipping any non-integer name. Tolerant (PID-1 must never die on a missing/racing dir): an
absent dir or any `OSError` resolves to `[]` — read by the caller as "no agents yet" — never an
exception.

```_proc_cmdline(pid: int) -> list[str] | None```
Real `_CmdlineOf`: *pid*'s ARGV, or `None` if it cannot be read.

The per-PID companion to `box_lifecycle._collect_proc_cmdlines`' whole-table read, with the same
lenient decode — but returned **split, not joined**. Which token is `argv[0]` and which is `argv[1]`
is what separates an agent's session from its helpers, and joining throws that away. ⚑ A kernel
thread's EMPTY cmdline returns `None`, not `[]`: the caller's three-valued contract needs
"unreadable" and "not the session" to stay distinguishable, and an empty cmdline is the former.

```_default_remove_marker(markers_dir: str, pid: int) -> None```
Real `_MarkerRemover`: REAP the marker file naming *pid*; race-tolerant.

`FileNotFoundError` is named and passed, not swallowed generically: the agent's own `pid-rm.sh` runs
on its `SessionEnd` hook and a second supervisor scan may be mid-flight, so **two removers agreeing
on one file is the normal case**. Any other `OSError` is logged at debug and dropped — PID-1 must not
die because a marker dir went read-only.

### Identifying the agent SESSION — the marker-identity rule

⚑ **The unit of identity is the SESSION, not the program.** An agent runs helper processes under its
own binary — claude runs a `daemon`, a `bg-pty-host` and a `bg-spare` — so "the basename is `claude`"
calls every one of them the agent. A helper's marker then reads as a *second agent holding the
session*, which is precisely the signal 4b acts on: measured on a live box, a `claude bg-spare`
marker sat alongside the running agent's, and with takeover armed the supervisor would have evicted
the real agent to make room for a background pty host. **Do not loosen this back to a name match.**

The rule is DERIVED from the launch grammar, never a list of helper subcommands to exclude — a list
is the same defect wearing a different shape, and it goes stale the moment a harness adds a helper.

```_argv_head(argv: Iterable[str]) -> tuple[str, str | None] | None```
PURE: an argv's `(PROGRAM basename, SUBCOMMAND)` head, or `None` if it has none.

⚑ `argv[0]` is SPLIT ON WHITESPACE first. A harness that rewrites its process TITLE packs the
subcommand into `argv[0]` rather than `argv[1]` — measured, `claude bg-spare` and
`claude bg-pty-host` each arrive as a *single* argv entry — so reading `argv[1]` alone would miss the
very distinction the head exists to draw. The SUBCOMMAND slot is the first following token that is
not an option; an argv that goes straight to flags has `None`, which is itself the head shape a bare
agent launch has.

Only the head is read because everything after it legitimately DIVERGES between the launch and the
process it became.

```agent_launch_heads(*argvs: Iterable[str]) -> set[tuple[str, str | None]]```
PURE: the `(PROGRAM, SUBCOMMAND)` heads the supervisor was launched to run.

The launch grammars in `SupervisorConfig` (`start_argv`, `continue_argv`) are the ONLY thing PID 1
knows about WHICH agent this box runs. `commands/start.py` wraps an agent launch in one
`sh -c <script> sh <program> <args...>` shim per concern (`_secret_export_shim`,
`_directive_flatten_shim`), nesting outward; each layer sets `$0=sh` and `exec "$@"`s the next, so
peeling `["sh", "-c", <script>, "sh"]` off the front while it matches leaves the agent.

BOTH grammars contribute: start and continue modes may differ in the subcommand slot (`codex` starts
bare and continues as `codex resume`), and the running process may be either.

⚑ MEASURED against a live claude box's `/proc/1/cmdline`: the real `--continue-cmd`
`sh -c '<flatten script>' sh claude --continue --dangerously-skip-permissions --model opus` peels to
exactly `{("claude", None)}`.

⚑ The shim shape is QUARANTINED KNOWLEDGE of a sibling module, deliberately NOT imported — PID 1 is
stdlib-only, and importing `commands.start` would put every marker scan at the mercy of the whole
command package importing cleanly. It is SAFE UNDER SKEW: a shim shape this does not recognise leaves
the wrapper's own `sh` as the head, which matches no real agent, so `agent_session_verdict` falls
through to `None` and KEEPS the marker rather than reaping on a misread.

```agent_session_verdict(argv, heads) -> bool | None```
PURE: is *argv* the agent SESSION this supervisor supervises? `None` ⇒ unjudgeable.

Four answers, in order:

| condition | verdict | why |
|---|---|---|
| head is in *heads* | `True` | it begins the way the launch grammar begins |
| same program, different subcommand | `False` | the agent's binary, but not the session — a helper |
| different program, but the argv NAMES an agent (`_names_an_agent`) | `None` | probably an interpreter launching it |
| nothing matches | `False` | unrelated process |

**Only the head is compared, and that is the load-bearing limit.** Measured: a box launched
`claude --continue --dangerously-skip-permissions --model opus` was running as
`claude --resume <uuid> --allow-dangerously-skip-permissions --model opus --permission-mode
bypassPermissions`. A user's own flags, a resumed session and a model override all move the tail;
only the program and its subcommand slot survive. Matching further would reject real sessions, and
**rejecting a real session deletes its marker** — the one outcome this whole mechanism exists to
avoid.

⚑ The residual risk of the tightening, stated plainly: an agent whose SESSION process runs under a
subcommand the supervisor's grammar does not name would be judged `False`. None of the three shipped
targets does — claude launches with flags only, `goose session`, `codex resume` — and the union of
both grammars widens the accepted set. If a target ever grows one, this is where it shows up.

```_names_an_agent(argv, heads) -> bool```
PURE: does *argv* NAME an agent program anywhere, without being one?

The loose fallback, mirroring `box_lifecycle.vscode_server_present`: PREFIX-test every `/`-delimited
segment. It catches the shapes a head cannot judge — an interpreted install
(`node …/claude-code/cli.js`), a shell whose argv carries an agent path — and answers only
INCONCLUSIVE, never "agent". ⚑ A segment must START with the name, so `/home/agent/.claude/…` is NOT
a mention (`.claude` does not begin with `claude`) while `/tmp/claude-<id>-cwd` is.

Measured against a live box's whole process table: the session → `True`; all five `claude` helpers →
`False`; `tmux`, `tmux attach`, `python3`, `head`, the supervisor itself, the VS Code server →
`False`; one bash command whose argv mentions `/tmp/claude-…-cwd` → `None`, keeping its marker.

```scan_marker_pids(markers_dir: str, *, list_pids: _MarkersLister, pid_alive: _PidAlive, is_agent: _AgentCheck | None = None, remove: _MarkerRemover | None = None) -> tuple[set[int], set[int]]```
Partition the markers dir into (LIVE pids, STALE pids), REAPING the stale ones.

Enumerates the marker PIDs via *list_pids*, drops any non-positive PID, then prunes-dead via
*pid_alive*: a marker whose process is live lands in the LIVE set, one whose process is gone (a crash
left the marker behind) lands in the STALE set. When *is_agent* is given, a LIVE pid that is
positively **not an agent** is STALE too — that is how a leaked marker reads once the kernel has
REISSUED its PID to something else.

**Both stale kinds are handed to *remove*, and this is the fix for the stale-marker hazards.** A
leaked marker used to be a LEVEL the supervisor read forever: `panel_agent_state` returned `DEAD` on
every tick for the life of the box, and with `session_takeover` armed a reissued PID read as a live
NEWCOMER and evicted the real agent. Reaping makes it an EDGE — the call still RETURNS the stale PID,
so the panel agent's death still reaches `decide_panel` on the tick that noticed it, and no later
tick re-decides on the same corpse.

⚑ **NEVER REAP WHAT YOU CANNOT JUDGE.** A *pid_alive* probe that RAISES skips that PID entirely
(tolerant — one bad probe must not crash PID-1), and an *is_agent* that answers `None` (nothing to
match against, or `/proc` unreadable) leaves the marker LIVE and on disk. A PID that is genuinely
gone reads dead on a later scan; a wrongly deleted marker is a live agent the supervisor has stopped
seeing, which is the worst outcome available here. *remove* is OPTIONAL — omitted, this is the
pure-ish partitioner it has always been. Deterministic over its injected probes, so tests exhaust it
with no real FS / os.

⚑ **Every stale verdict says WHICH of the two reasons it was**, at `info`, naming the markers dir and
the PID: *"the pid it names is not alive"* versus *"the pid it names is LIVE but is not the agent
session"*. The two take the same branch but are not the same event — the first is hygiene, the second
is the one that can unsee a running agent — and a reap read back off `podman logs` is useless if it
cannot tell them apart. `_is_agent_pid` supplies the other half of the WHY: on a `False` verdict it
logs the argv head that was tested, the launch heads it was tested against, and the full argv. See
"Identifying the agent SESSION" for what those heads mean.

```newcomer_pids(live_pids: set[int], own_pids: set[int]) -> set[int]```
PURE: the LIVE marker PIDs that are NOT the supervisor's OWN agent.

A newcomer is a second agent that has bound the session — a live marker whose PID the supervisor did
not launch/front (the split-brain hazard increment 4 catches). *own_pids* is the mode-specific set of
legitimate agent PIDs: in `run_forever` the tmux pane PID(s); in panel-watch the tmux pane PID(s) of a
self-healed CLI PLUS the fronted panel incumbent. 4a LOGS these; 4b acts on exactly this signal when
`session_takeover` is on.

```project_pinned_xdg(home: Path | None = None, environ: Mapping[str, str] | None = None) -> list[str]```
Serve the box's real XDG locations from the pinned root — the POST-BOOT half.

Returns the link paths created (`[]` when every row was already satisfied or skipped). Full rationale
— symlink-not-mount, ordering, the three refusals — in "The pinned root and the XDG projection"
above.

⚑ This is the PYTHON half only, and it reaches a box ONLY when the supervisor is PID-1 (a persistent
AGENT box). A bare keep-alive box, the forward-compat fallback and a helper box run NO kanibako
Python at PID-1 at all, so they are served by `xdg_projection_sh`.

```xdg_projection_sh() -> str```
The POSIX-`sh` twin of `project_pinned_xdg`, GENERATED from the table.

Semantics are the Python function's, clause for clause. See "The two halves", "Where the shell twin
deliberately DIVERGES" and "Shape rules inside the generated shell" above.

```scrub_bootstrap_pythonpath(environ: MutableMapping[str, str] | None = None) -> None```
Strip the injected `KANIBAKO_PKG_MOUNT_ROOT` entry from `PYTHONPATH`.

Rationale in "The host-package mount and the PYTHONPATH scrub" above. Defaults to `os.environ` (the
real child-inheritance source); a caller may pass an explicit mapping (tests).

```decide(prev_state: AttachState, cur_state: AttachState, agent_alive: bool) -> SupervisorAction```
PURE: decide a tick's action from the attach-state transition + agent liveness.

Two ORTHOGONAL signals combine:

* **agent liveness** — a DEAD agent session (`agent_alive` False) ⇒ `ActionKind.SELF_HEAL` regardless
  of any client transition (the always-on guarantee: whenever no instance is live, restart one).
* **the client-attach transition** — `classify_transition` over `prev_state` → `cur_state`; a
  `LifecycleEvent.DETACH` sets *fire_detach_hook* (ATTACH / NONE do not). This is independent of
  liveness, so "agent died AND a surface detached in the same tick" yields BOTH.

Deterministic and side-effect free over its inputs — the whole loop's logic lives here so tests can
exhaust it without any tmux.

```decide_panel(tmux_alive: bool, panel: PanelAgentState, vscode_server: bool, any_attached: bool, seen_surface: bool) -> PanelAction```
PURE: decide a panel-watch tick's action (E2f state machine, design 3a/3b).

**Two DISTINCT surface signals combine** (design principle B / the E2e FF-8 fix) — conflating them is
the bug this signature exists to prevent:

* *vscode_server* — the **PANEL specifically**. It gates `SELF_HEAL_CLI`, which is the panel-specific
  §89-96 fallback ("the PANEL died while the panel is connected → launch a CLI agent"). A tmux
  terminal is NOT a panel, so it cannot trigger a panel-death self-heal.
* *any_attached* — **ANY** client surface (panel OR tmux terminal). It gates the ref-count
  KEEP-ALIVE / TEARDOWN: a box must persist while ANY surface is attached, so tearing down keys on
  "no surface AT ALL is attached" — never on the panel alone (else a box could close out from under
  an attached terminal, the exact FF-8-class bug E2e fixed for the CLI path).

The state machine, in evaluation order:

* `tmux_alive` OR `panel == ALIVE` → `NONE` — an agent IS running (a self-healed CLI agent in tmux, or
  the live panel agent); hands-off.
* No live agent:
  * `panel == DEAD` AND `vscode_server` → `SELF_HEAL_CLI` — the panel agent died but the PANEL is
    STILL connected, so launch a CLI agent in tmux. Thereafter a tmux agent exists and the first
    branch keeps it hands-off / self-healed.
  * Else (`panel` is `NONE`, or `DEAD` with no panel):
    * `any_attached` → `NONE` — keep-alive (principle B: a live surface keeps the box up; a panel
      will (re)bring an agent, a terminal is a human).
    * No surface AND `seen_surface` (a surface was present earlier, now gone) → `TEARDOWN` —
      ref-count / principle B: ALL surfaces and agents are gone, close the box.
    * No surface AND NOT `seen_surface` (never attached — a freshly warmed box) → `NONE` — keep-alive
      through the STARTUP GRACE so we do not tear down before VS Code first attaches. ⚑ **Known
      limitation:** a `code` box that is NEVER attached lingers until `kanibako stop` — accepted.

Deterministic and side-effect free, so the whole panel-watch loop's logic is exhaustively
unit-testable without any tmux / FS / os.

## `SupervisorConfig` — immutable configuration for a `BoxSupervisor`

| Field | Meaning |
|-------|---------|
| `session` | the tmux session name the agent lives in (E2b keeps `"kanibako"` for attach/reattach compat; `start.py:4138` passes it) |
| `start_argv` | the agent launch grammar for the INITIAL start (entrypoint + args), run as `tmux new-session -d -s <session> -- <start_argv...>` |
| `continue_argv` | the launch grammar for a self-heal RESTART (the `--continue` form, which re-reads the box's `~/.claude` history). Defaults to *start_argv* when a caller does not distinguish the two |
| `marker` | the continue-marker sent via `tmux send-keys` as a real acting turn so a restarted agent autonomously resumes |
| `poll_interval` | seconds between watch-loop ticks |
| `max_restart_retries` | bounded self-heal attempts before giving up (principle B: on exhaustion PID-1 exits so the box can stop) |
| `backoff_base` | base seconds for the exponential self-heal backoff |
| `send_keys_retries` / `send_keys_delay` | bounded retry so `send-keys` lands after the freshly created pane is ready |
| `on_agent_exit` | the LAUNCH-INTENT-AWARE exit policy — see below |
| `session_takeover` | 4b ENFORCEMENT master switch — see below |
| `takeover_grace` | seconds the takeover waits before the process-group evict — see below |
| `panel_watch` | PANEL-WATCH mode — see below |
| `agent_markers_dir` | box-local per-agent liveness MARKERS directory — see below |
| `creds_flag` | box-local ABSOLUTE path to the credential-writeback SIGNAL flag — see below |
| `capture_history` | bounded scrollback (lines) captured from the dead agent pane on a foreground teardown and echoed to PID-1's stdout so `podman logs` surfaces the agent's final output to the host (`capture_agent_output`) |

**`on_agent_exit`** (design §85-96, E2c) — what happens when the agent exits. `"self-heal"` (the
default; detached and panel launches) keeps the always-on bounded-retry restart. `"teardown"` (a
FOREGROUND CLI launch, where a human is the driver) treats an agent EXIT as a NORMAL termination and
lets PID-1 return so the box closes — no self-heal loop while a CLI is the surface. **Any value other
than `"teardown"` is treated as `"self-heal"`** (safe default). ⚑ It also decides whether
`remain-on-exit` is armed at all — see that section.

**`session_takeover`** (design §338-359) — DEFAULT-OFF so 4b lands DORMANT. `False` (unset) ⇒ the
`run_forever` loop's newcomer handling is 4a LOG-ONLY: no `SIGSTOP`, no send-keys, no newcomer
eviction; the newcomer path is 4a-identical. ⚑ Note the teardown/self-heal process-group kill is
flag-INDEPENDENT. `True` ⇒ full single-writer TAKEOVER (pause the panel newcomer, grace + evict the
CLI incumbent, resume the newcomer). Gated behind an internal/experimental env
(`KANIBAKO_SESSION_TAKEOVER`, threaded by `commands/start.py:4170`), **NOT a spec settings key** —
flipping the default to `True` is a follow-up gated on desktop validation of the `$PPID` ==
agent-PID / panel invariant, and promoting it to a proper `agent.default.*` key is a later
spec-delta.

**`takeover_grace`** — seconds the takeover waits (after pausing the newcomer plus a best-effort
heads-up) for the CLI incumbent to wind down before the process-group evict. **[UNVERIFIED-PLATFORM]**
Kept SHORT by default: the paused panel's stdio blocks while stopped, so a long pause risks the
extension's watchdog respawning it — a desktop-gated unknown.

**`panel_watch`** (E2f, design cases 3a/3b) — when `True` the supervisor starts NO CLI agent (the VS
Code panel is the agent), enumerates the *agent_markers_dir* liveness MARKERS plus the vscode_server
surface, and self-heals a CLI agent ONLY when the panel agent DIES with the panel still connected
(the §89-96 fallback). `False` (default) is the E2b–E2e tmux-agent path, byte-unchanged EXCEPT the 4a
newcomer detection (LOG-ONLY, or the 4b takeover when `session_takeover` is on). This is the
`kanibako code` AGENT-INDEPENDENT warm-up. ⚑ In panel-watch the `on_agent_exit` policy is INERT — the
panel-watch loop never reads it — but `start.py` passes it anyway for a uniform, forward-compatible
argv.

**`agent_markers_dir`** (E2g / increment 4a) — each agent session's start hook writes a per-PID marker
`<dir>/$PPID`. Enumerated tolerantly by `BoxSupervisor.panel_agent_state` (panel liveness) and by the
newcomer detection wired into BOTH loops. `None` (default) disables both (an old launcher that
threads no dir).

**`creds_flag`** (increment D) — on EVERY detach transition, in ALL modes, `_on_detach` writes this
flag into the supervisor's OWN box-home (already host-visible via the box-home bind mount), so a
TRUSTED HOST watcher (`kanibako.launch.creds_watcher`) can do the privileged box-home → store
credential writeback. ⚑ **The box NEVER touches the host credential store itself** — the load-bearing
trust invariant. `None` (the default) leaves `_on_detach` a no-op: an old host launcher that threads
no flag simply signals nothing.

## The action model

```class ActionKind(Enum)```
What the supervisor must DO for a tick (besides the detach hook). — `NONE`, `SELF_HEAL`.

```class SupervisorAction```
The decision a single watch-loop tick produces.

* *kind* — `ActionKind.SELF_HEAL` when the agent session has died (restart it with the continue
  grammar + marker); otherwise `ActionKind.NONE`.
* *fire_detach_hook* — `True` when this tick is a DETACH transition (a client surface that was
  attached is gone), so the loop calls the best-effort `BoxSupervisor._on_detach` hook. ⚑ That hook
  is the GAP-1 cred-writeback point and increment **D FILLED IT** — the original note read *"fills it
  in D"*, future tense, which is now stale.

## The panel-watch model (E2f) — the agent-independent `code` warm-up path

```class PanelAgentState(Enum)```
Liveness of the PANEL-launched agent, from the per-PID markers dir.

Computed from the NON-OWN markers (excluding a self-healed CLI's own tmux pane):

* `NONE` — no non-own marker (dir absent, or every panel agent exited cleanly and removed its
  `<dir>/$PPID`): no panel agent is present.
* `ALIVE` — a non-own marker names a LIVE process. ⚠️ **CORRECTED THIS PASS:** the original said
  *"`os.kill(pid, 0)` ok"*, which is the pre-zombie-check definition and is now false — kill-0
  succeeds for a zombie. Liveness is kill-0 **AND** `/proc/<pid>/stat` state `!= "Z"`. Proven by
  driving `_default_pid_alive` with kill-0 succeeding and stat state `Z` ⇒ `False`, state `S` ⇒
  `True`.
* `DEAD` — no live non-own marker but a STALE one remains (a crash left the per-PID file behind): the
  panel agent exited. ⚑ **DEAD is an EDGE, not a level.** The scan that produces this verdict also
  REAPS the stale marker (`scan_marker_pids`), so the verdict fires on the tick that noticed the
  death and not on every tick thereafter. Before that, a single leaked marker pinned `DEAD` for the
  life of the box, and `decide_panel` turned `DEAD` + `vscode_server` into `SELF_HEAL_CLI` — a CLI
  agent nobody asked for, re-spawned every time the box had no live tmux agent.

```class PanelActionKind(Enum)```
What a PANEL-WATCH tick must DO (besides the detach hook). — `NONE`, `SELF_HEAL_CLI`, `TEARDOWN`.

```class PanelAction```
The decision a single panel-watch tick produces.

*kind* — `SELF_HEAL_CLI` when the panel agent died with the panel still connected (launch a CLI agent
in tmux, the §89-96 fallback); `TEARDOWN` when every surface + agent is gone (ref-count / principle
B); otherwise `NONE` (keep-alive).

## `class BoxSupervisor` — PID-1 keep-alive supervising an agent in a detached tmux session

Impure by nature (it shells `tmux`, sleeps and signals processes), but every side effect is funnelled
through an injected primitive — see the seam table under "Design for testability" — so tests drive it
deterministically, instantly, and without touching a real process. Detection is **DELEGATED** to
`kanibako.box_lifecycle` (`snapshot_attach_state` + `classify_transition` via `decide`); this class
never re-implements attach detection.

Lifecycle: `run_forever` starts the agent if absent, then loops — snapshot → decide → act (fire the
detach hook, self-heal a dead agent) — until an explicit teardown (SIGTERM / `teardown`) or a
self-heal that exhausts its bounded retries (principle B: then PID-1 returns so the box can stop).

### Constructor state

```__init__(self, config, *, run=subprocess.run, sleep=time.sleep, proc_cmdlines=None, pid_alive=_default_pid_alive, list_marker_pids=_default_list_marker_pids, cmdline_of=_proc_cmdline, remove_marker=_default_remove_marker, kill=os.kill, killpg=os.killpg, getpgid=os.getpgid, getpgrp=os.getpgrp, reap=reap_zombie_children)```

* `_proc_cmdlines` — when provided, a fixed process-cmdline listing handed to `snapshot_attach_state`
  (tests inject it to skip the real `/proc` walk); `None` ⇒ each snapshot collects fresh from `/proc`
  (the real PID-1 path).
* `_pid_alive` / `_list_marker_pids` — the agent-marker probes (E2f liveness + 4a detection),
  injectable so unit tests never touch the real FS / os; defaults are the real PID-1
  implementations.
* `_cmdline_of` / `_remove_marker` — the marker IDENTITY probe and the marker REAP, injectable on
  the same rule. ⚑ Tests inject `_cmdline_of` for a reason worth keeping: the default reads the REAL
  `/proc`, so a fixture PID that happens to exist in the runner would be judged a non-agent and
  reclassified. `None` (cannot judge) reproduces the pre-reap behaviour exactly.
* `_agent_heads` — the agent `(PROGRAM, SUBCOMMAND)` heads recovered from the launch grammars by
  `agent_launch_heads`, computed ONCE (config-derived and immutable). ⚑ An EMPTY set DISARMS the
  identity half of the scan entirely: `_is_agent_pid` then answers `None` for every PID rather than
  judging every process a non-session, which would reap every marker in the dir.
* `_kill` / `_killpg` / `_getpgid` / `_getpgrp` / `_reap` — the process-control primitives (4b
  signals, the pane-group evict, the PID-1 reap duty); defaults are the stdlib ops and the module
  reaper. ⚑ Every process-touching call in this class goes through one of these — never `os`
  directly, or a unit test would signal a REAL process.
* `_reported_newcomers` — 4a: PIDs already logged as newcomers, so the LOG-ONLY detection announces a
  given newcomer ONCE, not every poll tick. Pruned to the still-present newcomer set each tick, so a
  departed-then-returning PID re-logs.

### tmux action helpers (impure; tolerant; injectable `run`)

```_run_tmux(self, args: list[str]) -> int | None```
Run `tmux <args>` via the injected runner; return its rc, or `None`.

Centralises tolerance: a missing tmux binary (`FileNotFoundError`) or any other `OSError` resolves to
`None` (logged at debug), never an exception, so a tmux hiccup can never crash the loop.

```_tmux_output(self, args: list[str]) -> str | None```
Run `tmux <args>` via the injected runner; return its STDOUT, or `None`.

The stdout sibling of `_run_tmux`, for probes that read a tmux FORMAT string (`display-message`)
rather than only its rc. Same tolerance, plus: a **non-zero rc also resolves to `None`**.

```_start_session_argv(self, session_argv: list[str]) -> list[str]```
tmux argv that arms `remain-on-exit`, starts the detached session, and SCOPES the arm.

The four commands and their ordering proof are in "The four-command arm" above.

```_arm_and_start_session(self, session_argv: list[str]) -> int | None```
Arm remain-on-exit (unless the policy is `teardown`) + start the detached session; return the tmux rc.

The policy split and the `;`-in-argv guard are in the `remain-on-exit` section above.

```start_agent_session(self) -> bool```
Start the agent detached; returns `True` on rc 0.

⚠️ Whether `remain-on-exit` is armed depends on `on_agent_exit` — see the corrected claim above.

```restart_agent_session(self) -> bool```
Restart the agent for self-heal, with the CONTINUE grammar + marker.

Starts `new-session -d -s <session> -- <continue_argv>` via `_arm_and_start_session` (the
`--continue` form re-reads the box's `~/.claude` history), then delivers the continue-marker via
`_send_marker` so the successor gets it as a REAL acting turn (autonomous resume, no human needed).
Returns `True` when the new-session started (rc 0); marker delivery is best-effort and logged.

⚑ **Kill the (dead) session FIRST.** With `remain-on-exit` a dead agent leaves its session PRESENT
(a dead pane) still holding the canonical name, so a fresh `new-session` with the same name would
COLLIDE. The kill is a tolerant no-op when nothing is there.

```_send_keys_text(self, text: str) -> bool```
Send *text* to the session as a real user turn via `tmux send-keys` (bounded retry).

**[UNVERIFIED-PLATFORM]** A freshly created pane may not be ready the instant `new-session` returns,
so retry up to `send_keys_retries` times with a small `send_keys_delay` between attempts. Emits
`send-keys -t <session> '<text>' Enter` — the trailing `Enter` submits it as a real user turn.
Returns `True` once a send lands. Shared by the self-heal continue-marker and the 4b takeover
heads-up — **one** send-keys path.

```_send_marker(self) -> bool```
Send the continue-marker to the session (self-heal restart).

```_send_takeover_heads_up(self) -> bool```
Send the 4b takeover heads-up (`TAKEOVER_HEADS_UP`) to the CLI incumbent.

Best-effort GRACE nudge (design §346): lands as a real acting turn if the incumbent is idle, or
queues mid-loop — either way the incumbent is warned before the bounded grace elapses. Feasible ONLY
toward the tmux (CLI) agent (the DIRECTIONAL limit).

```agent_pane_dead_status(self) -> int | None```
Return the agent pane's DEAD exit status, or `None` when it is not dead.

**[UNVERIFIED-PLATFORM]** With `remain-on-exit on` a pane whose agent process exits stays in the
session as a DEAD pane, and tmux exposes its real exit code via `#{pane_dead_status}`. Read with
`tmux display-message -p -t <session> '#{pane_dead_status}'`: tmux prints the integer exit code for a
DEAD pane and an EMPTY string for a live one.

Tolerant like every probe: a missing tmux / dead server / no session (non-zero rc → `None` output) OR
empty / unparseable output all resolve to `None` — treated by callers as "not dead / unknown".
Returns a parsed `int` only for a genuinely dead pane.

```capture_agent_output(self) -> str | None```
Return the agent pane's captured MAIN-screen text (scrollback), or `None`.

`tmux capture-pane -p -S -<n> -E - -t <session>` prints the pane's content through the END of
history. With `remain-on-exit on` the pane PERSISTS after the agent exits, so this recovers the
exited agent's main-screen output — which the self-heal host relies on (via `podman logs`) to show
the user WHY the agent died. A `teardown` launch does NOT arm `remain-on-exit`, so this capture is
only meaningful for the self-heal / panel modes.

**[UNVERIFIED-PLATFORM]** Under the supervisor PID-1 that output lives in the tmux pane, NOT PID-1's
own stdout, so without echoing it here `podman logs` would be empty and both surfaces would silently
break (the E2c/E2d observability residual).

⚑ **SCOPE.** This recovers MAIN-screen output — an early cold-start error the agent prints before any
TUI (e.g. claude's *"No conversation found"*, a config error, an immediate crash) — which is the
class the host actually acts on. **[UNVERIFIED-PLATFORM]** An agent that dies while in the tmux
ALTERNATE screen (a running full-screen TUI mid-session) leaves only tmux's *"Pane is dead"* overlay
behind, which capture cannot see past; that output is not recoverable here. Best-effort improvement
over the pre-fix state (nothing surfaced at all), not a total transcript.

⚑ **`-E -` = capture through the END OF HISTORY.** **[UNVERIFIED-PLATFORM]** Without it the end
defaults to the VISIBLE screen, which for a dead pane is tmux's *"Pane is dead (status N)"* overlay —
so the agent's actual output is NOT returned. `-E -` reaches past that overlay into the pane's
scrollback where the exited agent's final output lives (verified on real tmux: **20/20** instant
crashes captured with `-E -`, **0/20** without).

The history is BOUNDED (`-S -<capture_history>`) so a chatty agent can't make PID-1 dump an unbounded
log; the host tails it anyway. Trailing blank lines tmux pads the region with are stripped, but the
meaningful body is kept verbatim (the host greps it for target-specific sentinels).

```agent_session_alive(self) -> bool```
True iff the agent session EXISTS and its pane is NOT dead.

⚑ **`tmux has-session` alone is NOT sufficient:** with `remain-on-exit on` the session PERSISTS after
the agent process exits (a dead pane), so `has-session` stays rc 0. Liveness is therefore has-session
(rc 0) **AND** `agent_pane_dead_status()` is `None`. Tolerant throughout — a missing tmux / dead
server / no such session all resolve to `False`.

```_kill_process_group(self, pid: int, sig: int) -> bool```
Send *sig* to *pid*'s whole PROCESS GROUP (child-kill-with-parent); tolerant.

**[UNVERIFIED-PLATFORM]** The agent is launched as `tmux new-session -- <agent>` (no shell wrap), so
tmux starts it as a session/process-group LEADER — its PID is the group id — and every subagent /
worker it spawns inherits that group. Signalling the GROUP (`os.killpg`) therefore reaps the agent
AND all its descendants, so no orphaned worker survives a takeover/teardown (the child-kill-with-
parent ruling, design §267 / §348). Resolves the real pgid via the injected `getpgid` (falling back
to the pid itself when it cannot — a group leader's pgid == its pid), then signals it through the
injected `killpg`. Tolerant like every PID-1 op: `ProcessLookupError` or any `OSError` resolves to
`False` and is logged at debug.

⚑ **SAFETY GUARD — a wrong pgid here could kill the whole box.** **[UNVERIFIED-PLATFORM]** A real
pane agent is its OWN `setsid` session/group leader, so its pgid is never 0/1 nor the SUPERVISOR's
(PID-1) own group. Refuse those pathological targets — **most importantly `pid <= 0`, because
`os.getpgid(0)` returns the CALLER's group**, so a stray `0` would make `os.killpg` signal PID-1's own
group → box death. The guard can never block a legitimate eviction (a pane group is none of these).

```kill_agent_session(self) -> None```
Evict the supervised agent: process-group kill each pane agent, then the session.

The eviction primitive — used on explicit teardown (SIGTERM / `teardown`), on a self-heal restart (to
free the dead-pane name), AND as the 4b single-writer EVICT. First reaps each of the supervisor's OWN
pane agents (`_own_agent_pids`) as a PROCESS GROUP, so no orphaned subagent / worker survives; then
`tmux kill-session` removes the (now-dead) session so its name is reusable. Tolerant throughout — an
already-absent session / dead pane / missing tmux all resolve to a logged no-op.

*(The original docstring said the process-group handling was "RESERVED for increment 4" by this
method's own docstring — a self-reference to text that no longer exists. Dropped as dangling.)*

```_snapshot(self) -> AttachState```
Probe the current client-attach state (delegates to `box_lifecycle`).

```_other_surface_attached(self, state: AttachState) -> bool```
True when a surface OTHER than the foreground CLI's own terminal is attached.

The **CLI↔panel REF-COUNT SLICE** (E2e, design principle B): a FOREGROUND launch's OWN surface is the
tmux TERMINAL it attached, so the "other" surface whose presence must keep the box alive AFTER that
CLI agent exits is the VS Code PANEL — `AttachState.vscode_server`. While the panel is attached, an
agent exit stays an agentless keep-alive instead of tearing the box down; the box closes only once
this last other surface ALSO detaches — a poll-based ref-count where the box stops when the LAST
surface goes.

⚑ Deliberately the CLI↔panel slice, **not** a full N-terminal ref-count (multiple independent CLI
terminals) — that generalization is a noted extension (E2e brief, "Out of scope"; not verifiable from
the repo). This slice covers the stated FF-8 bug: a CLI agent exit must not demolish a box a panel is
concurrently using. Reads only the already-probed `AttachState`, so it is as tolerant as the
snapshot.

### agent markers — liveness (E2f) + newcomer detection (4a)

```_scan_markers(self) -> tuple[set[int], set[int]]```
Enumerate the markers dir → (LIVE pids, STALE pids); tolerant.

Delegates to `scan_marker_pids` with the injected lister + liveness probe. No dir configured, or any
unexpected raise, resolves to two EMPTY sets — PID-1 must never die on a marker enumeration.

```_own_agent_pids(self) -> set[int]```
The PIDs of the agent(s) the supervisor itself launched in its tmux session.

**[UNVERIFIED-PLATFORM]** `tmux list-panes -s -t <session> -F '#{pane_pid}'` lists the ROOT process of
every pane in the session — and because the agent is launched as `new-session -- <agent argv>` (no
shell wrap), a pane's root process IS the agent. So the pane PIDs are the supervisor's OWN agents
(E2g: the marker they write via `$PPID` equals the pane PID), which the 4a newcomer detection
excludes.

⚑ In panel-watch steady state there is no tmux agent, so this is EMPTY — the fronted panel agent is
not a pane the supervisor launched, so the panel-watch loop adds that fronted incumbent to 'own'
separately. Tolerant: a missing tmux / dead server / no session (`None` output) → empty set.

```_log_newcomers(self, live_pids: set[int], own_pids: set[int]) -> None```
LOG-ONLY (increment 4a): warn once per newcomer; take NO action.

A newcomer is a LIVE marker PID that is not one of *own_pids* (the supervisor's legitimate agent(s)
for this mode) — a second agent has bound the session. This emits ONE warning per newly-seen newcomer
(deduped via `_reported_newcomers`, re-pinned to the current newcomer set so a departed-then-returning
PID re-logs) and does NOTHING else — NO SIGSTOP, kill, evict, or send-keys.

### 4b ENFORCEMENT — single-writer takeover (grace + pause + evict)

```_signal_pid(self, pid: int, sig: int) -> bool```
A tolerant SINGLE-process signal (SIGSTOP/SIGCONT) via the injected `kill`.

Distinct from `_kill_process_group` (which signals a whole group): the newcomer PAUSE/RESUME targets
ONE process (the panel agent), never its group. Tolerant like every PID-1 op.

```_resume(self, pids: list[int]) -> None```
`SIGCONT` every PID in *pids* — the reversible undo of the newcomer PAUSE.

Called on ANY takeover error before the evict (**never leave a newcomer frozen**) AND after a
successful evict (single-writer is now guaranteed). Best-effort per PID (a newcomer that died while
paused is simply skipped).

```_takeover(self, own_pids: set[int], newcomers: set[int]) -> bool```
4b ENFORCEMENT: pause the newcomer, grace the incumbent, evict it, resume (NEW-wins).

The single-writer TAKEOVER for the CRITICAL direction (a VS Code panel newcomer over a live CLI
incumbent). Steps, IN ORDER — **reversible ops (1–3) kept clearly separate from the ONE destructive
op (4)**:

1. **PAUSE** (reversible) — `SIGSTOP` every newcomer PID so it cannot take a divergent WRITE turn
   while the session is handed over.
2. **HEADS-UP** (reversible, best-effort) — `tmux send-keys` a heads-up to the incumbent (feasible
   only because it is the tmux agent).
3. **GRACE** (reversible) — wait `config.takeover_grace` seconds for the incumbent to wind down /
   checkpoint.
4. **EVICT** (the ONE destructive op) — process-group kill the incumbent (`kill_agent_session`,
   child-kill-with-parent). The TARGET is the pane incumbent (`own_pids` / re-read `_own_agent_pids`),
   **NEVER a bare marker PID**.
5. **RESUME** — `SIGCONT` the paused newcomer(s); single-writer is now guaranteed.

**SAFETY.** On ANY error BEFORE the evict, the newcomer is `SIGCONT`'d (never left frozen) and the
incumbent is NOT killed → returns `False` (no takeover). Also returns `False` (no eviction) when NO
newcomer could be paused (every one vanished first) — there is then nothing to hand the session to.
Returns `True` ONLY once the incumbent has been evicted, so the caller hands off to the agentless
keep-alive. The caller pre-checks `own_pids` is non-empty (there IS a pane incumbent to evict), so
this never fires on a bare marker.

```panel_agent_state(self) -> PanelAgentState```
Liveness of the PANEL-launched agent from the per-PID markers dir (E2f).

Enumerates the markers dir (`_scan_markers`) and EXCLUDES the supervisor's OWN tmux-pane agents
(`_own_agent_pids`) — a self-healed CLI agent writes a marker too and it is NOT the panel. TOLERANT
throughout:

* no markers dir configured, or NO non-own markers at all (never started, or a clean SessionEnd
  removed each) → `NONE`;
* ≥1 LIVE non-own marker → `ALIVE`;
* no live non-own marker but ≥1 STALE non-own marker (a crash left a per-PID file behind) → `DEAD`.

This preserves the E2f contract the single-pidfile scheme drove (a clean exit → NONE, a crash → DEAD
→ SELF_HEAL_CLI), now over the per-PID dir.

⚑ **AGENT ASYMMETRY (Phase 2 D2).** **[UNVERIFIED-PLATFORM]** Only claude has a marker-REMOVE hook —
codex's hook surface has NO SessionEnd/exit event (verified against codex-rs at rust-v0.141.0 and
0.144.x), so a cleanly-exited codex agent leaves a stale marker and reads DEAD here, never NONE.
Intended: the codex panel's sessions are threads inside one long-lived `codex app-server` process, so
its marker-PID dying ≈ the panel process itself is gone — DEAD (→ SELF_HEAL_CLI when the VS Code
server is still attached) is the useful verdict, and at worst a clean exit costs one benign
self-heal. **Do NOT "fix" this by janitor-unlinking stale markers at scan: DEAD-vs-NONE is exactly the
information `decide_panel` consumes.**

### self-heal

```_self_heal(self) -> bool```
Restart a dead agent with bounded retry + exponential backoff.

Up to `max_restart_retries` attempts: each `restart_agent_session`, then check
`agent_session_alive`; a live session ⇒ success (stop retrying). Between failed attempts,
`sleep(backoff_base * 2**(n-1))`. On exhaustion returns `False` so `run_forever` exits (principle B:
no agent + no one watching → let the box stop, don't spin).

### detach hook (increment D)

```_on_detach(self) -> None```
Best-effort HOOK fired on a DETACH tick — write the creds-dirty SIGNAL flag.

A client detached, so an in-box panel/agent may have refreshed a shared credential. ⚑ **The
load-bearing TRUST invariant is that the box NEVER writes the host credential store** — a mount is
not process-scoped, so the untrusted agent would inherit any store-write handle. The supervisor only
SIGNALS: it edge-triggers a flag in its OWN box-home (`config.creds_flag`, already host-visible via
the box-home bind mount), and a TRUSTED HOST watcher (`kanibako.launch.creds_watcher`) does the
privileged box-home → store copy via the existing host writeback.

Universal across supervisor modes (foreground teardown, detached self-heal, panel-watch) — the flag
means only "a client detached, creds may have refreshed", which the host resolves (writeback is a
no-op for a private box). Best-effort and idempotent: `None` flag ⇒ no-op (an old launcher threading
no `--creds-flag`); a missing parent dir is created; ANY `OSError` is swallowed here.

⚑ The flag is a **tiny edge-trigger MARKER** — the host watcher only checks EXISTENCE, so the contents
are immaterial; a single byte keeps it a non-empty file.

```_safe_on_detach(self) -> None```
Call `_on_detach`, swallowing ANY exception (the loop must not die).

Belt and braces: `_on_detach` already swallows `OSError`, and the loops go through this wrapper so
even an unexpected raise can never break the supervisor.

### teardown / signals

```teardown(self) -> None```
Total teardown: signal the loop to exit and kill the agent session.

Design principle B (teardown = TOTAL): an explicit `kanibako stop` (podman stop → SIGTERM to PID-1)
kills everything. Sets the loop-exit flag then kills the supervised session; `run_forever` returns on
its next check.

```_handle_sigterm(self, signum: int, frame: FrameType | None) -> None```
SIGTERM handler → `teardown` (factored out so tests call it directly).

```install_signal_handlers(self) -> None```
Install the SIGTERM handler (best-effort; a no-op off the main thread).

Registering a signal handler outside the main thread raises `ValueError`; that (and any `OSError`) is
tolerated so the supervisor still runs — teardown then only comes via container kill, which is
acceptable.

### the watch loops

```run_forever(self) -> int```
Run the supervise loop until teardown, agent-exit, or self-heal exhaustion; returns a process exit code.

Installs the SIGTERM handler, starts the agent if absent (the warm-box 1b case leaves a live agent
alone), then loops: snapshot → `decide` → act (fire the detach hook, then respond to a dead agent per
the launch-intent policy). Each tick's body is guarded so a raising probe/action is logged and the
loop CONTINUES.

⚑ **The response to a DEAD agent is LAUNCH-INTENT AWARE** (`config.on_agent_exit`, E2c). The pure
`decide` still just reports the SELF_HEAL signal (a transition/liveness fact); **this loop** decides
what to DO with it — keeping `decide` byte-identical and the policy in exactly one place.

* **`"self-heal"`** (default; detached / panel) → bounded-retry restart; the one clean exit is a
  self-heal that EXHAUSTS its retries (returns 0 so the box can stop).
* **`"teardown"`** (foreground CLI, human present) → an agent exit is a NORMAL termination, but
  **SURFACE-AWARE** (E2e, principle B / ref-count): PID-1 closes the box ONLY when no OTHER client
  surface is still attached.
  * **With another surface (a VS Code panel) attached** — do NOT tear down. Stay an AGENTLESS
    keep-alive (the box persists for the panel), and do NOT self-heal a CLI agent while the panel is
    the live surface (**the one-agent invariant**). Keep polling; close on a LATER tick once that last
    surface also detaches. The entry is logged ONCE, guarded against per-tick spam.
  * **With no other surface** — close the box with a TRUTHFUL-as-possible exit code. A teardown
    launch does NOT arm `remain-on-exit`, so on exit the pane simply CLOSES and there is normally no
    `#{pane_dead_status}` to read. The code is derived as:
    * a dead pane status IS present (stale/armed) ⇒ **honor it verbatim**;
    * else the agent CAME UP and then exited (`started`) ⇒ **0** — a clean quit must NOT masquerade
      as a failure (the whole point of dropping the dead pane). ⚑ A sole-agent CRASH is
      indistinguishable from a clean exit here so it ALSO returns 0, until a pipe-pane follow-up
      restores truthful crash codes;
    * else it NEVER came up (the initial start failed) ⇒ **1**, a real failure the host must surface.

  Before returning, any captured agent output is echoed to stdout. **[UNVERIFIED-PLATFORM]** The
  host's foreground path reads `podman logs` (PID-1's stdout) to show WHY the agent died — but the
  agent ran in a tmux pane, so its output never reached PID-1's stdout. With `remain-on-exit` OFF in
  teardown the pane has closed and this normally captures nothing; it still recovers output from a
  stale/armed pane if one is present.

**Startup, and the `started` flag.** Startup runs BEFORE the per-tick guard, so it is guarded too: a
probe raising there (e.g. an unexpected snapshot failure) must not kill PID-1 before the loop even
begins. On any startup hiccup, degrade to "no attach" and enter the loop — it self-heals (or, under
teardown, closes on) a missing agent on its first tick.

`started` answers *did the agent ever COME UP?* A warm box already has a live session; a cold box is
up iff the initial start returns rc 0. It distinguishes, in the teardown branch, a NEVER-STARTED
failure (rc 1) from a normal ran-then-exited quit (rc 0) once the pane has closed and there is no
dead-pane status to read. ⚑ **Defaults `True`** so a startup-probe hiccup (the except path) prefers 0
— never fabricate a failure from a clean quit.

⚑ **A failed INITIAL start does NOT return early.** It is handled UNIFORMLY by the loop's first tick
(the E2e factoring): under `self-heal` the loop self-heals a missing agent; under `teardown` the
loop's SURFACE-AWARE branch closes the box (rc 1) UNLESS a panel is attached, in which case it stays
up as an agentless keep-alive. One start attempt, then the loop's ref-count policy decides — which
keeps the teardown decision in exactly ONE place (design §86-88, cold-start-error-human-direct).

**Per-tick order, and why:**

1. **PID-1 duty FIRST** — `self._reap()`, so a dead panel process cannot sit as a zombie and
   read ALIVE to the marker probes below.
2. snapshot, then `agent_session_alive`.
3. **Newcomer detection**, gated on a configured markers dir so an old launcher (or a test) that
   threads none is byte-unchanged. A live marker PID that is NOT this supervisor's own tmux agent =
   a second agent bound the session (e.g. the VS Code panel auto-`--resume` over a live CLI agent).
   * `session_takeover` ON → 4b ENFORCEMENT. Act ONLY when there IS a pane incumbent to evict (`own`
     non-empty) — **never evict on a bare marker PID**. On success the incumbent is gone and the
     newcomer is the sole writer, so hand off to the agentless panel-watch keep-alive (E2f
     self-heal-to-CLI on the newcomer's death) by tail-calling `_run_panel_watch()`.
   * OFF (default) → 4a LOG-ONLY: no signals, no kills.
4. `decide`, then act.

⚑ Panel-watch short-circuits at the top: when `config.panel_watch` is set the E2f `code`
AGENT-INDEPENDENT warm-up runs a distinct loop that starts NO CLI agent and watches the panel-agent
marker + surface. The E2b–E2e path is untouched (only reached when NOT `panel_watch`).

```_run_panel_watch(self) -> int```
The PANEL-WATCH loop (E2f): agent-independent `code` warm-up.

Unlike `run_forever`'s tmux-agent path, startup starts NO CLI agent — the VS Code panel is the agent.
Each tick snapshots the surfaces, tracks a `seen_surface` LATCH, and drives the pure `decide_panel`
over (tmux liveness, panel-agent marker, vscode_server, seen_surface):

* `SELF_HEAL_CLI` → the panel agent DIED with the panel still connected: run `_self_heal` (continue
  grammar + marker) to launch a CLI agent in tmux. Thereafter that tmux agent is live, so
  `decide_panel` returns NONE and the loop leaves it be (self-healing it again if IT later dies while
  the panel is up). A self-heal that EXHAUSTS its retries returns 0 (principle B: let the box stop).
* `TEARDOWN` → every surface + agent is gone: return 0.
* `NONE` → keep-alive; keep polling.

The DETACH hook (`_safe_on_detach`, D's cred-writeback point) still fires on a DETACH transition,
computed exactly as the E2b loop does via `classify_transition` over the prev→cur snapshot. Every
tick's body is guarded so a raising probe/action is logged and the loop CONTINUES.

**Two pieces of loop state:**

* `seen_surface` **LATCHES** True once any surface has ever been attached, so a box that IS attached
  and later fully detaches tears down (ref-count), while a never-yet-attached box stays up through
  the startup grace. The pre-loop snapshot is guarded like `run_forever`'s startup.
* `panel_incumbent` — 4a: the fronted panel incumbent PID (first live non-own marker seen). A
  panel-watch box's LEGITIMATE agent is the fronted panel (a non-own marker) or a self-healed CLI (an
  own tmux pane); a SECOND live non-own marker beyond the latched incumbent is a newcomer (a
  concurrent panel resume). Latched so the fronted panel is not itself flagged. Cleared when it
  departs the live set.

The per-tick order matches `run_forever`: **PID-1 reaping duty FIRST**, so a dead panel agent cannot
sit as a zombie and hold `panel_agent_state` at ALIVE forever, wedging `SELF_HEAL_CLI` and `TEARDOWN`
alike. Newcomer detection here uses `own` = the self-healed CLI's tmux pane(s) PLUS the latched
fronted panel incumbent; a live marker outside that set is a newcomer. **NO action in this loop** —
the reverse direction has no injection vector (see 4b above).

## CLI entry point

```_build_parser() -> argparse.ArgumentParser```
Build the `python3 -m kanibako.box_supervisor` argument parser (options only).

The trailing `-- <agent argv>` is split off BEFORE argparse (see `config_from_argv`), so the parser
only sees the named options.

```config_from_argv(argv: list[str]) -> SupervisorConfig```
Parse *argv* (without the program name) into a `SupervisorConfig`.

Splits on the FIRST standalone `--`: everything before it is parsed as options, everything after is
the agent `start_argv`. `--continue-cmd` is shlex-split into `continue_argv` (defaulting to a copy of
`start_argv` when absent). A missing `--` / empty trailing argv is an error (there is no agent to
run) — **EXCEPT under `--panel-watch`** (E2f), which starts NO agent at launch, so it takes an empty
`start_argv` and relies on `--continue-cmd` for its self-heal grammar (the host always threads one
through).

```main(argv: list[str] | None = None) -> int```
CLI: parse args, build the supervisor, run the watch loop forever.

```
python3 -m kanibako.box_supervisor --session NAME --marker 'STR' [--poll SEC]
  [--max-retries N] [--continue-cmd 'ARGV'] [--on-agent-exit self-heal|teardown]
  [--session-takeover] [--takeover-grace SEC] [--panel-watch]
  [--agent-markers-dir DIR] [--creds-flag PATH] -- <agent entrypoint + argv...>
```

In `--panel-watch` mode (E2f) the trailing `-- <agent argv>` is OMITTED (no agent starts at launch);
`--continue-cmd` carries the self-heal grammar.

**Three ordered calls before the loop, all load-bearing:**

1. `setup_logging(verbose=True)` — see below. It is FIRST because `config_from_argv` itself logs.
2. `project_pinned_xdg()` — serve the box's XDG locations from the pinned root now that the box is
   LIVE. ⚑ This is the earliest point at which it is SAFE and the only one at which it is correct:
   every podman mount is already in place, so the link is invisible to mount-time symlink resolution.
3. `scrub_bootstrap_pythonpath()` — strip the host-package mount from `PYTHONPATH` before the loop
   spawns tmux / the agent, so those children do not inherit our import path. Safe here: this
   process's own imports are already resolved, and `PYTHONPATH` only affects newly spawned processes.

### Why PID 1 configures logging, and why at DEBUG

Every other module in the package logs through a logger that `cli.main` configured. PID 1 has no
`cli.main` above it — it is exec'd directly as `python3 -m kanibako.box_supervisor` — so until this
call landed the `kanibako` logger had no handler at all, and Python dropped everything below WARNING.
(WARNING and above still escaped, via `logging.lastResort`. That is why the symptom was *thin* logs
rather than obviously broken ones.) Measured consequence: `podman logs <box>` was EMPTY on a box that
had just forked a second agent, because self-heal, panel-watch entry, teardown and every marker reap
are `info`/`debug` decisions.

**Placement.** In `main`, ahead of `config_from_argv` — `_directive_watch` warns there about a
half-armed directive watch, and that warning is itself a decision worth reading. **Not** at module
scope: `commands/start.py` imports this module on the HOST at module scope, so an import-time
`setup_logging` would silently reconfigure the CLI's own logging as a side effect of importing three
constants.

**Level.** `setup_logging` offers two rungs, WARNING and DEBUG; only DEBUG emits what this module
decides at, so DEBUG it is. The standing cost is near zero because **no tick logs unconditionally**
at either level: on a healthy box `_reap`, `_snapshot`, `agent_session_alive`, `_scan_markers` and
`_own_agent_pids` log only on an error path or on a real event (a reaped child, a stale marker), so a
quiet box stays quiet and a noisy log is itself the signal.

**It is not settable.** Nothing threads a verbosity into PID 1 — `kanibako -v` configures the HOST
process only, and the supervisor's argv carries no level. Making it settable would mean a new
supervisor flag threaded from `commands/start.py`, or a new settings key; the keyspace is CLOSED, so
the second is not available without a spec edit. Left alone deliberately.

**The stream.** Detached boxes run with `-dt` (`runtime/container.py`, `run_container`), so podman
allocates a pty and merges the container's stdout and stderr onto it; `podman logs` shows that merged
stream, and `--timestamps` supplies per-line times from the log driver, so the handler does not need a
timestamp of its own. `setup_logging` writes to `sys.stderr`, which Python line-buffers, so nothing
sits in a buffer waiting for a flush. ⚑ `[UNVERIFIED-PLATFORM]` — the dev box has no working podman;
what is verified here is the argv (`-dt`) and the handler stream, not a `podman logs` capture.

## `[UNVERIFIED-PLATFORM]` index

Every claim below is a platform fact that CANNOT be reproduced on the dev box (no working podman, no
real tmux server under PID-1). None was dropped; none was independently confirmed this pass.

1. `remain-on-exit` arm-before-birth wins the race 20/20 on real tmux; arming after a bare
   `new-session` loses it 0/20.
2. A separate `set-option -g` on a not-yet-running tmux server starts a server that immediately exits.
3. A standalone `;` in the agent argv would be split by tmux as a top-level command separator.
4. `capture-pane` without `-E -` returns the *"Pane is dead"* overlay, not the agent's output (20/20
   vs 0/20).
5. Output from an agent that died in the tmux ALTERNATE screen is not recoverable at all.
6. Under supervisor PID-1, agent output lives in the tmux pane and never reaches PID-1's stdout, so
   `podman logs` is empty without the explicit echo.
7. A box runs with an EMPTY effective capability set, so in-box `mount --bind` fails outright.
8. All three podman symlink traps are podman resolving a symlink AT MOUNT TIME; a link created after
   the mounts is invisible to them.
9. Published images ship an OLD kanibako without the supervisor module, so importing the baked copy
   would degrade every launch to the bare-shell fallback.
10. `kill -0` answers True for a ZOMBIE, and orphans reparent to PID-1, so an unreaped agent would
    read ALIVE forever.
11. `/proc/<pid>/stat`'s `comm` field may contain spaces AND parentheses; the state is the first token
    after the LAST `)`.
12. Shared PID namespace: the supervisor, as PID-1, sees the panel agent.
13. `tmux new-session -- <agent>` (no shell wrap) makes the agent a session/process-group LEADER, so
    every subagent inherits its group.
14. A real pane agent is its own `setsid` session/group leader, so its pgid is never 0/1 nor PID-1's.
    `os.getpgid(0)` returns the CALLER's group — hence the `pid <= 0` refusal.
15. `tmux list-panes -F '#{pane_pid}'` gives the pane ROOT process, which IS the agent, and equals the
    `$PPID` the marker hook writes.
16. A freshly created pane may not accept `send-keys` the instant `new-session` returns.
17. tmux exposes a dead pane's real exit code via `#{pane_dead_status}` (integer for dead, empty for
    live).
18. A panel incumbent has no send-keys injection vector; only the tmux CLI agent can be nudged.
19. A paused (SIGSTOP'd) panel's stdio blocks, so a long grace risks the VS Code extension's watchdog
    respawning it.
20. codex's hook surface has NO SessionEnd/exit event (verified against codex-rs rust-v0.141.0 and
    0.144.x), so a cleanly-exited codex agent leaves a stale marker.
21. Under `podman run -dt` the pty merges the container's stdout and stderr into the single stream
    `podman logs` replays, so PID-1's stderr handler reaches the host. The `-dt` argv and the
    handler's stream are verified on the dev box; the capture itself is not.

## Corrections made in this pass

| Site | The claim | What the code does |
|------|-----------|--------------------|
| `start_agent_session` docstring | *"Start the agent DETACHED with `remain-on-exit` armed globally first."* | Only when `on_agent_exit != "teardown"`. The foreground CLI path emits a bare `new-session` with no `set-option` at all. |
| `restart_agent_session` docstring | *"Arms `remain-on-exit` globally and starts … in one invocation (`_start_session_argv`)."* | Delegates to `_arm_and_start_session`, which skips the arm under `teardown` and splits into two invocations when `;` is in the argv. |
| `PanelAgentState.ALIVE` docstring | *"a non-own marker names a LIVE process (`os.kill(pid, 0)` ok)"* | Liveness is kill-0 **AND** `/proc` state `!= "Z"`; the zombie layer was added after this line. |
| `SupervisorAction.fire_detach_hook` | *"GAP-1 cred-writeback fills it in D"* — future tense | Increment D is SHIPPED: `_on_detach` writes the flag and `start.py` threads `--creds-flag`. |
| `newcomer_pids` / `_log_newcomers` | *"4b will act"* — future tense | 4b is SHIPPED as `_takeover`, gated OFF by `session_takeover`. |
| `kill_agent_session` | *"the process-group handling this method's docstring RESERVED for increment 4"* | Self-reference to docstring text that no longer exists. Dangling; dropped. |
| module docstring | *"EVERY subprocess call funnels through an injectable runner"* — true but incomplete WHEN WRITTEN | ⚑ **RESOLVED SINCE** — the signal ops and the reap duty now go through injected primitives too, so the promise covers the whole surface again (see "Design for testability"). |
| module docstring | scope framed as in-box supervisor only | It is ALSO the single source of three host-side launch literals that `start.py` imports at module scope. |
