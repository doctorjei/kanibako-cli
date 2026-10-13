# Credential Writeback Watcher — the trusted host half

`launch/creds_watcher.py` is the per-box HOST daemon that carries an in-box credential refresh back
to the host credential STORE. Design authority:
`~/canon/workbook/designs/split-brain-persistence-DESIGN.md` (GAP-1, the always-on instance).

## Why writeback is a correctness need

A panel or agent login inside one box can refresh a SHARED token. Providers rotate refresh tokens,
so until the new token lands in the store, another box holding the old one can be locked out.
Prompt writeback is therefore a correctness need, not hygiene.

## The trust invariant

**The host credential store must NEVER be writable from inside a box.** A bind mount is not
process-scoped, so the untrusted in-box agent would inherit any store-write handle. The WRITE
therefore stays on the host:

* the in-box supervisor only SIGNALS a detach, by edge-triggering a flag in its OWN box home
  (`box_supervisor.BoxSupervisor._on_detach`);
* this daemon watches that flag (the box home is host-visible through the existing bind mount) and
  does the privileged box-home → store copy through the existing host writeback,
  `commands/start.writeback_session_credentials`, whose logic it does not change.

## Testability

The design mirrors `box_lifecycle` and `box_supervisor`: the PURE decision (`decide_watch`) is split
from the impure box probe, flag I/O, and writeback, and every side effect goes through an injected
callable (plus an injected `sleep`), so unit tests drive the whole loop with no real box, filesystem,
or waiting. Every probe is TOLERANT: a watcher that crashed on a transient error would silently stop
propagating credentials, so a failing writeback is logged, never raised.

## `creds_store_lock` — who takes it, and why a directory

Two writers to one SHARED store must not interleave, and they come in two kinds.

* **The WRITEBACK** copies box-home credentials into the store. A `kanibako stop`, a foreground
  reattach, and this daemon can all fire at once. `writeback_session_credentials` is the sole
  writeback site, and this daemon reaches the store THROUGH it rather than entering the lock itself.
* **The start-time REFRESH** of a global-synced workset store writes that store and reads host home
  while another box may be writing back into either, so `targets/credsync._sync_workset_dir_from_global`
  takes the lock over both: a torn source file corrupts as surely as a torn write. For the same
  reason the box-level seed and refresh READ of a store (`credsync._box_from_source`) takes it over
  the box's source root. A create or start therefore waits behind any writeback into a store it
  reads: milliseconds normally, and as long as the writeback's own wait if a holder hangs.

There is no other lock on these writes; the launch-path flock is the EPHEMERAL session lock, skipped
for persistent boxes.

**The lock IS the destination directory** (`os.open` + `flock(LOCK_EX)` on the directory itself).
It creates no file, mints no key, and its scope is the destination's scope by construction. The
`"global"` tier's destination is host home itself (`credsync.selected_source_root`), so a global
writeback takes a host-wide lock, while a workset-tier writeback takes only its own auth dir instead
of serializing against unrelated stores.

🛑 **Do not lock a FILE instead** (`[R167]`). Not a sentinel under a composed path (the bug this
replaced), and not the destination file, which a rename defeats: the claude plugin's
`writeback_extra` rewrites `~/.claude.json` by temp+rename while the credential files are copied in
place, so a file lock would hold for one destination and silently not for the other.

A global-synced workset box writes its workset dir AND host home, so it passes both as
*store_dirs*. Sorted real-path order means no single call can invert or repeat its way into a
deadlock; a nested entry over a directory already held still self-deadlocks, because each entry
opens a fresh descriptor.

**Why the lock is TOLERANT.** The guarded operation must never be BLOCKED by a lock-infrastructure
hiccup, so a directory that cannot be opened or locked is skipped and the operation proceeds without
that lock.
