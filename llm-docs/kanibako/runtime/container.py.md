# The Podman Invocation (where a box actually becomes a process)

Every kanibako box is, in the end, one `podman run` argv. This module builds it. That makes almost
everything here **empirical platform behaviour** rather than design: the option spellings, the
argument ORDER, the escalating deletion attempts and the host-side mountpoint pre-creation are each
a bug that was paid for once, in debugging, on a real runtime.

⚑ **Most of it cannot be re-derived from a unit test.** The dev box has no working podman, and the
failures these notes encode are things podman does INSIDE the box (what a mask shows, what `:U`
chowns, what crun can mkdir) — invisible to any test that only inspects the argv. So the standing
rule for this file is inverted from the usual one: a claim that sounds odd is kept, and a note is
dropped only when it can be proven false.

The one-line `⚑` markers left in the source mark the exact lines where deleting a note would let a
future edit break something silently. Each is expanded below.

## The argv shape, and why its ORDER is load-bearing

`run()` emits, in this order: run flags (`-dt` detached, else `-it`/`-i` plus `--rm`), the userns
pin, `-w`, then **every tmpfs mask**, then **every bind**, then env, name, entrypoint, image, and
finally the agent's own cli args.

* **Binds are emitted as `-v`, never as `--mount type=bind`.** The ONLY `--mount` this module ever
  emits is the tmpfs for a mask. The two flags are not interchangeable here — `-v` carries the
  colon-delimited option suffix (`ro`, `Z`, `U`) that the whole binding model depends on.
* **Masks are emitted BEFORE binds, and that ordering is why a bind whose dest sits under a mask
  still takes at runtime.** Reordering the two loops is an invisible change to the argv builder and
  a visible one inside the box.
* Env vars are emitted `sorted()`, so the argv is deterministic for a given input — which is what
  makes byte-comparison of the emitted argv a usable oracle for changes to this file.

### `Mount.to_volume_arg`'s falsy-options guard

The `-v` argument string is built by `Mount.to_volume_arg` in `targets/base.py`, not here, but this
is its only production caller and the guard matters at this seam:

```python
base = f"{self.source}:{self.destination}"
return f"{base}:{self.options}" if self.options else base
```

⚑ **The `if self.options` is LOAD-BEARING, not tidiness.** An unconditional interpolation yields the
literal `src:dst:` form for a mount with no options, and **podman REJECTS that with exit 125**
(UNVERIFIED here — no podman on this box; recorded from the original debugging). Mounts with empty
options are ordinary and common: the box home bind and the package bind both carry none.

⚑ `to_volume_arg` does **NOT** validate. It will happily emit a nonexistent source, a relative path,
or a garbage option string; the failure surfaces from podman at launch, not from this module.

### `Z` and `U` mutate the host

⚑ Both are **RECURSIVE and MUTATE THE HOST PATH** — `Z` relabels (SELinux) and `U` chowns
everything underneath the bind SOURCE, on the host, at container creation. They are not annotations.
This is the single most dangerous property of the argv this module builds, and it is what
`KEEP_ID_USERNS` and the `post_start` hook both exist to contain.

## `KEEP_ID_USERNS` — why not plain `keep-id`

Plain `keep-id` maps the CALLING host user to the same numeric uid inside the container. A host user
whose uid is not 1000 therefore lands *beside* — not *on* — the image's `agent` user (uid/gid 1000,
the `GUEST_UID`/`GUEST_GID` image contract). The `:U` bind option then recursively chowns the box
home AND the user's project tree to an unrelated subuid, bricking both.

`keep-id:uid=…,gid=…` pins the caller onto the agent user regardless of host uid: in-box files are
caller-owned, `:U` chowns resolve to the caller's own uid (a no-op on a caller-owned tree), and the
host-uid==1000 case is unchanged.

Requires **podman >= 4.3** (the `uid=`/`gid=` options, 2022-10). `keep-id` is podman-only; Docker
support is future backlog work with its own userns handling.

## Masks are a VOID, and independent of the vault

A mask is an EMPTY read-only tmpfs over each box-dest in the `box.masks` category (resolved in
`start.py`). It is emitted here rather than arriving as a category mount because tmpfs has no host
source, so there is nothing for the caller to pre-build.

⚑ **`notmpcopyup` IS LOAD-BEARING, NOT A TUNING KNOB.** podman's tmpfs default is `tmpcopyup`: it
copies whatever already sits at the destination UP into the new tmpfs, so a mask left the
pre-existing content plainly visible (read-only) and hid nothing — it downgraded the path to
read-only instead. A mask is a VOID: there is nothing inside it (collapse DESIGN §8.1a). Deleting
this option restores the old behaviour **silently, with every test still green**, because what
changes is what podman shows INSIDE the box.

⚑ **A MASK IS INDEPENDENT OF THE VAULT.** The emission loop used to sit inside an `if enable_vault:`
block, which was residue: back then the ONLY mask was the hardcoded `~/workspace/vault` tmpfs, so
the block held the vault binds AND their mask. `4e96daa` routed the vault binds out through the
category resolver and left the wrapper behind, and `242bfde` then dropped the default mask — so a
vault-disabled box silently got NO masks at all, while `<scope>.masks` is an ordinary user-writable
key that has nothing to do with the vault. A declared mask is emitted regardless. The same rule
governs the mask STUB loop in `_precreate_mount_stubs`, which is outside the vault arm for exactly
this reason.

There is **no default mask**: the vault moved out of `~/workspace` in 1.6.0, so there is nothing in
the workspace to hide. No masks emits no tmpfs mounts. The `.gitignore` overlay that used to ride on
the vault tmpfs is DROPPED — no special-case overlay survives.

## Nothing is hardwired into a box

The home + workspace + vault binds are **not** built here. They flow in via *extra_mounts* — the
core box mounts the caller resolves through the category keyspace and folds through the assembly
COLLAPSE — so nothing is bound into a box except through the keyspace. Only `-w` (a flag, not a
mount) stays in this module's own argv.

⚑ Prior text here named `start._build_core_mounts` as the producer, then the retired
`reconcile_categories` pipeline. **Neither symbol exists** — the first went in `f56211f` when the
launch read-path moved onto the KeyStore, the second at cutover 6-R3. The core mounts arrive from
`_resolve_launch_snapshot` → `_install_assembly_collapse` → `meta.assembly.bindings` →
`_emit_category_mounts`. Dead names are not carried forward. (It survives in two comments in
`tests/test_runtime/test_container_extended.py`, which is outside this pass's seam.)

## `post_start` — the `:U` re-chown repair seam

*post_start* runs after podman has set the box's mounts up. It is the seam for work that must happen
AFTER that moment — specifically the **canon re-protect**, because the home bind's `:U` option makes
podman RECURSIVELY RE-CHOWN the home bind SOURCE to the container user's mapping at container
creation, which resets the box-create canon skeleton from container-root back to the agent
(measured on bifrost, 2026-07-31: host `165536 165536 555` post-create → `1000 1000 555`
post-start).

The hook is wired inside `run()` rather than as a separate step after each `runtime.run(...)` call,
so that the ORDERING — and the detach/foreground split below — exists in ONE place instead of being
re-derived at every call site.

⚑ **IT IS NOT "COVERED BY CONSTRUCTION."** The parameter is OPT-IN, so a call site that does not
pass *post_start* gets nothing. What IS true by construction is narrower, and worth stating exactly:
`run()` is the only container-CREATION seam (stopped containers are `rm`'d and recreated, never
`podman start`ed), so there is no OTHER place a `:U` chown can happen. Every call site that binds a
box home must pass the hook itself; that obligation is enforced by a test
(`test_container.TestPostStartCallSites`), not by the signature.

The hook is a **repair step, not a precondition** — which is why `_run_post_start` swallows
everything it raises. A box whose canon re-protect failed is degraded (litter-able) but perfectly
usable, and taking the launch down over it would trade a cosmetic problem for a total one.

### Two paths, two firing rules

* **DETACHED** — the container is up the instant `podman run -d` returns, so the hook runs INLINE,
  once, right there. No polling, no thread.
* **FOREGROUND** — `subprocess.run(cmd)` BLOCKS for the whole session, so there is no "after start"
  moment in this thread. The hook therefore runs from `_watch_for_start` alongside it, AND
  unconditionally in the `finally`.

⚑ **THE GUARANTEE LIVES IN THE `finally`, NOT IN THE WATCHER.** A container that lives less than one
poll interval is never observed running (measured: up at 20 ms, gone at 50 ms — the first probe
fires before the subprocess even starts and the second lands after the cancel), so the watcher
*alone* would leave short-lived ephemeral boxes NEVER repaired, silently, and invisibly to any e2e
using a long-running stub. Re-asserting once the container is gone costs one idempotent pass and
makes the on-disk state ALWAYS end protected. The watcher is only an OPTIMISATION: it shortens the
window DURING a long session. Without the foreground arm entirely, the ephemeral/shell modes would
run their whole session with an agent-owned canon while the detached path was protected — a split
nobody would notice.

`_watch_for_start` is a short-lived DAEMON thread: it cannot keep the process alive, it polls a
bounded number of times (`_POST_START_TIMEOUT_S` / `_POST_START_POLL_S`), and every exception is
swallowed and debug-logged. A launch must never fail because a post-start hook did.

## The detached-launch output

`podman run -d` prints the new container's full SHA id to stdout. The caller reattaches by NAME
(`runtime.exec`), so the id is not needed; it is captured to keep it off the user's terminal and
surfaced only at DEBUG (`-v`). A genuine launch failure must still be reported, so captured stderr
is echoed on a non-zero return.

## `exec` — the attach/non-attach split

*attach* marks an exec as a BOOTSTRAP ATTACH (a `tmux attach` handoff) rather than a command whose
output is the user's payload.

A pty is allocated only when stdin is a real terminal. In scripted / subprocess contexts (CI, e2e
tests) `-t` causes interactive commands like `tmux attach` to render but never return.

* **NON-attach** (the default; the one-off `kanibako shell <box> -- <cmd>` path) always inherits all
  stdio, tty or not, so its output reaches the user.
* **Attach** captures stderr in BOTH arms, so the runtime's raw race error — `container state
  improper`, or `can only create exec sessions on running containers`, when a supervised box tore
  down between the readiness probe and this attach — does NOT leak to the caller's stderr as if it
  were agent output. The caller re-checks liveness and surfaces the agent's real logs and exit via
  `podman logs`, so that noise is debug-only.
  * *interactive* — stdin/stdout stay on the TTY so the tmux session renders and the user drives it
    (tmux draws to stdout, errors to stderr). stdout is a terminal, so it drains itself; no
    deadlock.
  * *non-tty* — there is no session to render, so BOTH streams are captured. ⚑ **Capturing stdout is
    REQUIRED, not just tidy:** inheriting it to the caller's (undrained) pipe lets a live `tmux
    attach` fill the pipe buffer and DEADLOCK until the caller's timeout. Draining it here both
    prevents that wedge and keeps the raw error off the caller's stdio.

`exec_ready` runs the same operation as the interactive bootstrap-attach exec, captured, so a fresh
success is a tight predictor that the attach will start cleanly. It is what gates the
TTY-inheriting interactive exec, and because its output is captured, podman's raw "container state
improper" race error is swallowed rather than leaking to the user's TTY.

## The `unshare` family — writing as a subuid

Files a `--userns=keep-id` container creates as root map to subuids the host user cannot touch
directly. `podman unshare` runs a command inside the user namespace where those subuids appear as
root, so the operation succeeds.

* `unshare_rm` — the read side of the problem: a plain `rmtree` of a box's shell dir can fail with
  EACCES.
* `unshare_chown` — the write-side counterpart, and the mechanism behind the canon skeleton's
  root-owned book roots (J-7): a host user cannot hand a file to one of their own subuids directly,
  but inside `podman unshare` those subuids are ordinary namespace uids.
* `unshare_chmod` — runs inside the namespace because the paths are, by then, owned by a subuid the
  host user cannot chmod directly.

⚑ **NO `-R`, on either mutating call.** Callers pass an EXPLICIT, enumerated path list — a recursive
sweep of `~/canon` would take the seeded, agent-owned `notebook/` and `workbook/` books with it.

All three are podman-only and return False for docker, for an empty *paths*, or on any failure.

## `remove_box_tree` — THE box-tree deleter

A box's home can contain files owned by mapped subuids — files a `--userns=keep-id` container wrote
as root, and (since J-7) the root-owned canon skeleton kanibako itself creates at box create — which
the host user cannot unlink. A plain `shutil.rmtree` then fails with EACCES.

⚑ **EVERY verb that deletes a box home or box metadata tree must come through here** — `rm --purge`,
`extract`, `move`/`convert`, `duplicate`'s `--force`/rollback arms, and `purge`. A bare `rmtree` on
any of those paths is a bug: it fails on the canon skeleton and leaves a half-deleted box behind.

**THREE ESCALATING ATTEMPTS**, because there are three distinct failure shapes:

1. **plain `rmtree`** — ordinary content.
2. **`rmtree` after re-opening the directory modes we OWN.** ⚑ `rmtree` raises `PermissionError` on
   a tree containing 555 directories **even when the caller owns them** (unlinking a child needs
   write on its parent), and an owned-but-555 canon skeleton is genuinely reachable:
   `shutil.copytree` reproduces the skeleton's modes without its ownership, so every box-home copy
   passes through that state, as does a create whose `chown` failed after the `chmod`. This step
   needs no container runtime at all — which matters, because the machines that most often lack
   podman are exactly the ones running these verbs.
3. **`podman unshare rm -rf`** — the SUBUID case, where the files are not ours and no amount of
   chmod helps.

The mode-reopening walk is bounded to *target*'s own subtree and is best-effort per entry; a dir
owned by someone else simply fails the chmod and is skipped, which is attempt 3's job.

⚑ **NEVER chmod THROUGH a symlink.** `os.chmod` follows links, so a symlinked dir inside the box
home would have its TARGET re-opened — possibly somewhere outside the tree being deleted. On Linux
this is safe today only *by accident* (a symlink's own lstat mode is 0777, so the `not S_IWUSR` test
skips it); the explicit `islink` check makes the safety DELIBERATE rather than a property of one
platform's mode bits. `os.walk` does not follow directory symlinks either, so skipping here loses
nothing: rmtree unlinks the link itself, which needs only the parent's write bit.

⚑ **DEGRADED END-STATE**, stated because it is not obvious: if step 2 re-opened some modes and steps
2 AND 3 then both failed, the tree is left BEHIND with those dir modes re-opened — i.e. LESS
protected than before the attempt. That is the right trade (the caller is trying to delete the tree,
and a half-deletable box is worse than a briefly-writable one) but a caller that reports the False
return must not imply the tree is untouched.

Returns True if *target* is gone afterwards, False otherwise; callers warn rather than crash.

## Mount stubs — why the host pre-creates mountpoints

In some environments — notably **LXC nested containers** — the OCI runtime cannot create mount-point
directories inside bind-mounted overlay filesystems. Pre-creating the stubs on the host side avoids
the problem entirely, so `_precreate_mount_stubs` runs before the argv is even assembled.

The dest→host mapping is: destinations under `/home/agent/workspace/` are created relative to
*project_path* (the workspace bind); other destinations under `/home/agent/` are created relative to
*shell_path* (the box HOME bind).

### `_guest_dest_to_host` is THE single translator

It is shared by the mount-stub precreate and shadow scans here AND by the seed/synced COPY appliers
in `kanibako.commands.start`.

*map_home_root* controls the box-home ROOT itself (`/home/agent`, with any trailing slash):

* `False` (default — the mount-stub callers): the bare home root returns `None`. The base home bind
  is not a stub to pre-create, and the shadow scan skips the base roots explicitly.
* `True` (the seed/synced COPY callers): the bare home root maps to *shell_path*, so a `~`-targeted
  copy (the `seeded[~/]` trio) stages straight into the box HOME.

Both COPY callers gain the `/workspace` split for free by routing here: a `~/workspace/...` copy
dest lands under *project_path* (the workspace bind), not the shadowed `shell_path/workspace` stub —
closing the former inline translators' latent drift bug (audit P3).

### `_clear_symlink`

A baked/dirty image may ship `~/.local/bin/claude` (or `~/.local/share/claude`) as a symlink into
the install-dir subtree. If the destination is a symlink, the OCI runtime follows it and the bind
lands somewhere it gets shadowed — the "the bind isn't taking" symptom. Clearing the symlink first
guarantees the bind takes on a real, non-symlink mountpoint that we own.

### `_loosen_parents` — the minimum that makes a path resolve

**WHY.** crun's openat2 destination resolution must TRAVERSE every parent of a bind dest. A
pre-existing box home commonly ships XDG dirs like `~/.config` at 0700 (gh/podman/XDG tools create
them private); a file bind placed under such a dir — the agent kickoff SEED at
`~/.config/kanibako/kickoff.md`, new in rc12 — then dies at launch with `crun: creating ... openat2
home/agent/.config: Permission denied` (exit 126). Fresh boxes never hit it, because our own mkdir
makes `.config` traversable; pre-existing homes do.

**WHY only `0o011`** (g+x, o+x — the SEARCH bits, no read): +x alone makes a directory TRAVERSABLE
without exposing a listing, so directory contents stay unlistable and private. It is the MINIMUM
that makes the path resolve.

⚑ **Containment is load-bearing** — this mutates user-visible modes in the box home, so it is
deliberately narrow:

* *root* is *shell_path* ONLY; callers never pass a project_path (workspace) dest — that is the
  user's real tree, and loosening its modes is a policy call not being made this increment.
* Never chmod AT or ABOVE *root*: the walk stops when it reaches root, so root itself and its
  ancestors are untouched.
* A SYMLINKED parent stops the walk — we must not follow a symlink OUT of the box home and chmod a
  target elsewhere. Belt-and-suspenders: the `resolve().relative_to` test also catches an escaping
  ancestor, since `resolve()` collapses any symlinked ancestor so an escape lands OUTSIDE root and
  raises `ValueError`.
* Only parents of ACTUAL bind dests are visited, so a private 0700 dir with no bind under it (e.g.
  `~/.ssh`) is never touched.
* The walk starts at the stub's PARENT: the stub's own mode is owned by the bind. It then
  ascends LEXICAL parents (`.parent`), testing each RESOLVED against *root* — lexical ascent with
  a resolved containment test, not a resolved walk.

Best-effort like the sibling stub helpers: any `OSError` is debug-logged and swallowed — a
pre-create hiccup must never abort a launch (crun may still succeed, or the real error surfaces
there). On a probe failure the walk STOPS, because we can no longer safely reason about ancestors we
cannot probe.

### What gets stubbed

* `shell_path/workspace` — always. All the built-in dir mounts are shell_path-side, and the
  workspace stub's only parent IS shell_path, so its loosen walk is a no-op.
* the vault ro/rw dests when *enable_vault* — vault is UNIVERSAL unless disabled: the host source
  dirs are created if missing by the core-defaults resolver, so the box-side dest stubs are always
  made whenever vault is enabled.
* one per box-dest in *tmpfs_masks*, mapped the same way extra mounts are. An empty list (the
  default — no masks) yields no stubs. ⚑ This loop is OUTSIDE the vault arm on purpose and must STAY
  outside; see the mask section above.
* one per *extra_mounts* dest, a dir or a file according to whether the SOURCE is a dir.

Only home-side (shell_path) parents are loosened for traversal; project_path dests pass
`traverse_root=None`, which is what `_home_root` decides.

### The canon-skeleton skip is EXISTENCE-aware, not PATH-aware

⚑ **THE CANON SKELETON OWNS ITS OWN MOUNTPOINTS (J-7) — BUT ONLY WHERE IT EXISTS.** On a box created
by R1b the mountpoint is already there, root-owned and unwritable, so stubbing it is pointless and
wrong-headed.

⚑ **That the skip tests EXISTENCE rather than just the path is load-bearing.** Boxes with NO
skeleton exist and will keep arriving: every R1-era and pre-canon box, plus any box whose create hit
the degraded path. An unconditional path-based skip leaves their five-or-six canon binds with no
mountpoints at all, and in LXC crun cannot mkdir inside a bind-mounted overlay — that is a **launch
failure (exit 126)**, not a degradation. Falling through to the tolerant stub helpers instead
pre-creates the mountpoints for exactly those boxes, which also makes this a free self-healing
migration: an old box gains its canon mountpoints on its next launch.

Where the skeleton DOES exist the fall-through would be a no-op anyway (`_ensure_dir`/`_ensure_file`
are create-if-absent, and `_loosen_parents` finds 555 already carrying the `0o011` search bits), so
the skip buys clarity rather than behaviour — it says out loud that these mountpoints are not the
launch path's to manage.

`_is_managed_canon_dest` is ⚑ **PATH-shaped, not key-shaped, and deliberately so**: EVERY bind under
`~/canon` is by construction one of the canon book binds, whose mountpoint
`kanibako.settings.core_defaults.materialize_canon_skeleton` pre-created at box create. One uniform
rule beats six per-key special cases. The seeded `canon/notebook` + `canon/workbook` are never
binds, so they never reach here.

## Shadow detection

A bind mount whose DEST already holds content silently hides that content inside the box: the files
remain on disk under the OUTER home/workspace bind, but the INNER mount shadows them so they are
invisible — and untouched — in the box. `detect_shadowed_mounts` inspects each candidate dest's
mapped host stub (the OUTER view) and returns the box-dests that already contain content.

Candidates are the vault ro/rw dests (when *enable_vault*) plus each `mount.destination` in
*extra_mounts*. The base roots `/home/agent` and `/home/agent/workspace` are EXCLUDED — their
content IS the box, not something shadowed (since 1.6.0 the home/workspace base binds flow through
*extra_mounts* too). Tmpfs masks are not candidates: masking is intentional hiding, and they are not
in *extra_mounts*.

⚑ **This function is PURE.** It performs no filesystem mutation — no mkdir, touch, unlink or
clear-symlink. All probes are best-effort and any `OSError` skips that dest rather than raising. A
symlink stub is not user content (`_precreate` clears it); a missing path, socket or fifo is not
shadowed. Do not add mutation here: the mutating twin is `_precreate_mount_stubs`, and keeping the
detector side-effect-free is what lets a caller run it to WARN without changing the box.

## Image reading

`load` reads the loaded image ref back from the runtime's output rather than from the archive's
filename, because the filename is not a reliable source for the loaded tag. ⚑ Three output shapes
are observed in the wild — `Loaded image: <ref>`, `Loaded image(s): <ref>`, and `Loaded image ID:
sha256:...` — which is what the regex's optional group covers. An archive with no RepoTags yields an
empty string; a failed load command yields `None`. The two are distinct answers.

`get_local_digests` returns ALL repo digests, not just the first: a pulled multi-arch image
typically records BOTH the per-platform manifest digest and the index digest, and callers deciding
freshness want the full set. `get_local_digest` is the single-value form, kept for callers that need
one stable image key (e.g. `launch/shells.image_store_key`). An image with no repo digests — a
locally-built one — yields an empty list.

`get_local_platform` reports the platform we actually run, as `os/arch[/variant]`; freshness matches
it against the per-arch child of a remote image index. `get_local_created` returns `.Created`, an
RFC3339 string. `get_local_label` reads `Config.Labels`, falling back to a top-level `Labels`.

`ensure_image` is inspect-then-pull. Base images are **pull-only** — the cli no longer bundles or
builds a base Containerfile — so a pull failure raises an actionable `ContainerError` directing the
user to build a custom base themselves. ⚑ *containers_dir* is accepted for call-site compatibility
and is **unused**.

`diff` returns each changed path verbatim, possibly prefixed by a change-type letter (`C`/`A`/`D`).

`inspect_env` reads the container's recorded `.Config.Env` — the env baked in at `run` time — and
returns the first `KEY=VALUE` whose KEY matches. `None` means the container does not exist, the var
is unset, or the inspect failed; callers fall back to normal resolution.

### `container_image` and what a `None` means

Reads the running container's `.ImageName`, the fully-qualified image reference recorded at `run`
time.

⚑ **What a `None` MEANS is the caller's call, not this function's**, and the two callers answer it
differently — neither is a sanctioned default:

* `code_cmd._resolve_box_image` falls back to the configured `box_image`; it only needs a stable key
  for image-shared config.
* the box-shell resolve on the reattach fast path (`commands/start.py`) DROPS the image tier
  instead, because there the configured image was never run through `resolve_rig` and can name a
  different rig than the live box.

⚑ `.ImageName` is **podman-specific**; `docker inspect` has no such field, so on Docker this errors
and returns None. (Docker is backlog, not yet supported.)

## Functions

```python
_run_post_start(hook: Callable[[], None]) -> None
```
Invoke a post-start *hook*, swallowing anything it raises. A hook is a repair step, never a
precondition — see the `post_start` section.

```python
class ContainerRuntime
```
Wrapper around the podman/docker CLI. `__init__` takes an explicit *command* or falls back to
`_detect`.

```python
ContainerRuntime._detect() -> str
```
Resolve the runtime binary: `KANIBAKO_DOCKER_CMD` if set, else the first of `podman`/`docker` on
PATH. Raises `ContainerError` naming both install routes when neither is present.

```python
ContainerRuntime.image_exists(image: str) -> bool
```
True iff `image inspect` succeeds locally.

```python
ContainerRuntime.image_inspect(image: str) -> dict | None
```
Return image metadata as a dict, or None if not found. podman returns a LIST and docker an object,
so a list is unwrapped to its first element.

```python
ContainerRuntime.pull(image: str, *, quiet: bool = True) -> bool
```
Pull *image* from its registry; True on success. *quiet* captures the runtime's progress output.

```python
ContainerRuntime.remove_image(image: str) -> None
```
Remove a local image; raises `ContainerError` carrying the runtime's stderr on failure.

```python
ContainerRuntime.unshare_rm(path: Path) -> bool
```
Remove *path* from within the rootless user namespace. podman-only; False for docker or any failure.

```python
ContainerRuntime.unshare_chown(paths: list[Path], uid: int, gid: int) -> bool
```
`chown uid:gid` an EXPLICIT list of *paths* inside the namespace. ⚑ Never recursive.

```python
ContainerRuntime.unshare_chmod(paths: list[Path], mode: str) -> bool
```
`chmod mode` an EXPLICIT list of *paths* inside the namespace. ⚑ Never recursive.

```python
ContainerRuntime._unshare_apply(argv: list[str], paths: list[Path]) -> bool
```
The shared body of the two mutating `unshare` calls: refuses an empty *paths* and refuses docker
before spawning anything.

```python
ContainerRuntime.build(image: str, containerfile: Path, context: Path) -> None
```
Build *image*, capturing output; raises `ContainerError` with stderr on failure.

```python
ContainerRuntime.rebuild(image, containerfile, context, build_args=None) -> int
```
Rebuild with `--no-cache`, STREAMING output to the user's terminal (unlike `build`), and return the
exit code rather than raising.

```python
ContainerRuntime.run_interactive(image: str, *, container_name: str | None = None) -> int
```
Run a bare interactive container (`run -it`) with no binds at all; returns the exit code. Not the
box launch path — that is `run`.

```python
ContainerRuntime.commit(container: str, image: str) -> None
```
Commit a container to a new image; raises `ContainerError` on failure.

```python
ContainerRuntime.cp(src: Path, dest: str) -> bool
```
Copy *src* into a container at *dest* (`<container>:<path>`); True on success.

```python
ContainerRuntime.save(image: str, out: Path) -> bool
```
Save *image* to a tar archive at *out*; True on success.

```python
ContainerRuntime.load(archive: Path) -> str | None
```
Load an image from the tar *archive*. Returns the ref parsed from the runtime's output, `""` for an
archive with no RepoTags, or `None` if the load command itself failed.

```python
ContainerRuntime.diff(image: str) -> list[str]
```
Return the changed paths for *image* as verbatim lines; empty list on failure.

```python
ContainerRuntime.ensure_image(image: str, containers_dir: Path | None = None) -> None
```
Inspect, then pull. Base images are PULL-ONLY; raises an actionable `ContainerError` on pull
failure. *containers_dir* is unused.

```python
ContainerRuntime.run(image, *, shell_path, project_path, vault_ro_path, vault_rw_path,
                     extra_mounts=None, tmpfs_masks=None, enable_vault=True, env=None,
                     name=None, entrypoint=None, cli_args=None, detach=False,
                     post_start=None) -> int
```
THE box launcher, and the only container-CREATION seam in the tree. Pre-creates stubs, assembles the
argv described above, and returns the exit code. *detach* backgrounds it (`-dt`, no `--rm`); the
foreground arm inherits the terminal and blocks. See the argv, mask and `post_start` sections — every
parameter here has a platform note attached to it.

```python
ContainerRuntime._watch_for_start(name: str, post_start: Callable[[], None]) -> threading.Event
```
Fire *post_start* once *name* is running; returns a cancel Event. A bounded, exception-swallowing
daemon thread — an optimisation, never the guarantee.

```python
ContainerRuntime.exec(name, command, *, env=None, attach=False) -> int
```
Run a command inside a running container and return its exit code. *attach* marks a `tmux attach`
handoff and changes only stream handling, never the argv.

```python
ContainerRuntime.exec_ready(name: str) -> bool
```
Probe whether the container can accept an exec session right now, via a cheap CAPTURED `exec <name>
true`. Gates the TTY-inheriting interactive exec.

```python
ContainerRuntime.container_exists(name: str) -> bool
```
True if a container exists, running or stopped.

```python
ContainerRuntime.stop(name: str) -> bool
```
Stop a running container by name; True if stopped.

```python
ContainerRuntime.rm(name: str) -> bool
```
Remove a stopped container by name; True if removed.

```python
ContainerRuntime.is_running(name: str) -> bool
```
True iff `.State.Running` inspects as exactly `true`.

```python
ContainerRuntime.inspect_env(name: str, key: str) -> str | None
```
Return the value of env var *key* recorded on container *name*, or None.

```python
ContainerRuntime.container_image(name: str) -> str | None
```
Return the image reference container *name* was created from, or None. ⚑ podman-only field; the
meaning of None belongs to the caller.

```python
ContainerRuntime.list_running(prefix: str = 'kanibako-') -> list[tuple[str, str, str]]
```
Return running containers matching *prefix* as (name, image, status) tuples. Lines that do not split
into exactly three tab-separated fields are dropped.

```python
ContainerRuntime.get_local_digests(image: str) -> list[str]
```
ALL repo digests (`sha256:...`) for a local image, `repo@` prefix stripped; empty on failure.

```python
ContainerRuntime.get_local_digest(image: str) -> str | None
```
The FIRST repo digest, for callers needing one stable image key.

```python
ContainerRuntime.get_local_created(image: str) -> str | None
```
The local image build timestamp (`.Created`, RFC3339), or None.

```python
ContainerRuntime.get_local_tags(image: str) -> list[str]
```
The local image's `RepoTags`; empty for an image referenced only by digest.

```python
ContainerRuntime.get_local_label(image: str, label: str) -> str | None
```
The value of *label* from `Config.Labels`, falling back to top-level `Labels`; None if absent/empty.

```python
ContainerRuntime.get_local_platform(image: str) -> str | None
```
The local image platform as `os/arch[/variant]`, or None.

```python
ContainerRuntime.list_local_images() -> list[tuple[str, str]]
```
Local kanibako images as (repo:tag, size) tuples — filtered by a case-insensitive `kanibako`
substring on the whole line.

```python
remove_box_tree(target: Path) -> bool
```
THE box-tree deleter; three escalating attempts. True iff *target* is gone afterwards. ⚑ Every
box-deleting verb must route through it, and a False return does NOT mean the tree is untouched.

```python
_is_managed_canon_dest(dest: str) -> bool
```
True for a bind dest the CANON SKELETON owns, which must not be stubbed. Path-shaped by design.

```python
_guest_dest_to_host(dest, shell_path, project_path, *, map_home_root=False) -> Path | None
```
THE single guest-dest→host-path translator, shared with the seed/synced COPY appliers.

```python
detect_shadowed_mounts(shell_path, project_path, extra_mounts, enable_vault) -> list[str]
```
Report box-dests whose pre-existing host content a bind will SHADOW. ⚑ PURE — probes only.

```python
_precreate_mount_stubs(shell_path, project_path, extra_mounts, enable_vault,
                       vault_ro_path, vault_rw_path, tmpfs_masks) -> None
```
Pre-create mount destination stubs to avoid crun permission errors in LXC. The mutating twin of
`detect_shadowed_mounts`. Hosts the nested helpers `_clear_symlink`, `_loosen_parents`,
`_ensure_dir`, `_ensure_file` and `_home_root`, all documented above.

## UNVERIFIED on this box

There is no working podman here, so the following are recorded from prior debugging and could not be
re-measured in this pass. **Do not delete them for lack of a test; confirm at a bifrost e2e.**

1. That podman REJECTS the `src:dst:` volume form with **exit 125** (the `to_volume_arg` falsy-options
   guard).
2. That `tmpcopyup` is podman's tmpfs DEFAULT, and that it copies destination content up into the
   new tmpfs.
3. That emitting the tmpfs `--mount` args BEFORE the `-v` args is what lets a bind under a mask
   survive at runtime. The ORDER itself is measured from the emitted argv; its runtime CONSEQUENCE
   is not.
4. That `Z` and `U` are recursive and mutate the host path.
5. That crun cannot mkdir a mount destination inside a bind-mounted overlay under LXC (exit 126),
   and that `openat2` traversal needs the `0o011` search bits.
6. The `keep-id` uid-mapping consequences, and the podman >= 4.3 floor for `uid=`/`gid=`.
7. The bifrost 2026-07-31 `:U` re-chown measurement (`165536 165536 555` → `1000 1000 555`).
8. The 20 ms/50 ms short-lived-container timing behind the foreground `finally` guarantee.
