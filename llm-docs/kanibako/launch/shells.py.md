# Launch Shells — which shell a no-agent box runs, and where that answer is stored

`launch/shells.py` answers one question: when a box is launched with no agent, what program does
it run? `resolve_box_shell` is the single source of that answer, and both callers that need it —
`commands/start.py` at launch and `commands/diagnose.py` for its "Shell" detail line — go through
it, so what a user is told matches what they will get.

Around the resolver sits the machinery that makes step 3 of its precedence possible: a probe that
reads an image's own login shell, a per-image store that remembers the result, and an install-time
capture hook so the resolver reads instead of probing in the hot path.

⚑ **`box.shell` means the LOGIN SHELL and nothing else.** It once doubled as a shell-*variant*
selector picking a template subdirectory; that axis was DROPPED — the template is chosen by
`@agent.<agent>.template` with no variant subdir, which is precisely what freed `box.shell` to
mean only this. Do not read a variant dimension back into this module.

## The precedence — four steps, first defined wins

`resolve_box_shell` returns `(shell, source)`; *source* names the step that won, and
`commands/diagnose.py` maps those tokens to friendly labels.

1. **`config.box_shell`** — the `box.shell` setting, an explicit user choice. There is no
   auto-fallback behind it: if the user named a shell, that is the shell.
2. **`$KANIBAKO_SHELL`** — a host environment variable, read at resolve time rather than captured
   earlier, so it reflects the environment the command was actually run in.
3. **The image's recorded login shell** — only consulted when an *image* is supplied. The stored
   value is used if present; otherwise, when a *runtime* is also supplied, the image is probed
   lazily and the result persisted. That lazy branch is what self-heals images pulled before this
   feature existed.
4. **`sh`** — the universal floor. Every POSIX image has it, so the resolver always returns
   something.

Step 3 is the only step with two arguments' worth of preconditions. With no *image* it is skipped
entirely; with an *image* but no *runtime* there is no way to compute a store key, so only the
"nothing more to read" path remains and the resolver drops to `sh`.

## The image-shell store

The store maps an image store key to that image's login shell for the box user:

```yaml
image_shells:
  sha256:abc...: /bin/bash
```

It is the `image_shells` section of the consolidated `system.registry` file — by default
`{data_path}/global/registry.yaml`, reached through `std.registry` so a repointed
`config.registry` is honored. This module owns the section's shape and reads and writes it through
`registry_store.load_section` / `save_section`, which preserve every sibling section.

The store used to be its own `image-shells.yaml` file. Only the on-disk *location* moved when it
was consolidated; the `std`-based public API here is unchanged from that era.

### The store key

`image_store_key` prefers the image's local digest (`sha256:...`, from
`runtime.get_local_digest`) because a digest is stable across re-tags. When no digest is available
— a locally-built image with no repo digest, typically — the image reference string is used
instead. That fallback accepts a minor staleness risk: a re-tag of the same reference could point
at a different image and the stored shell would be the old one.

Digest keying is also why `capture_image_shell` never needs to invalidate anything. A changed
image yields a fresh key, so the old entry is simply never read again.

## The getent probe

`probe_image_user_shell` runs ONE ephemeral container (`run --rm`) and reads the image's own
default USER's login shell out of the passwd database — `getent passwd` first, with a
`grep '^user:' /etc/passwd` fallback for images with no getent.

⚑ The `--entrypoint sh` override is essential. Kanibako images set an ENTRYPOINT
(`kanibako-entrypoint`) that would otherwise swallow the command and the probe would read nothing.
This is the same platform lesson as `probe_missing_executables` in `commands/diagnose.py`.

The probe treats every failure the same way — it returns `None`. That covers a runtime error, a
non-zero exit with no output, and an empty or whitespace-only result. Note the ordering: a
non-zero exit that nevertheless printed a shell is ACCEPTED, because the fallback `||` branch of
the probe script can succeed while the pipeline's exit status stays non-zero.

## Capture at install time, backfill at resolve time

`capture_image_shell` is the install-time half. `commands/image.py` calls it after a successful
pull or prep, and `commands/start.py` calls it before launching, so by the time the resolver runs
the answer is usually already on disk.

It must never raise and never meaningfully slow the install flow, which shapes all three of its
branches:

- If the image's store key already has a recorded shell, do nothing. No re-probe is needed,
  because the digest-keyed store yields a fresh key for a changed image.
- A probe failure (no shell, or a runtime error) is swallowed: nothing is stored, leaving the
  resolver's lazy backfill to cover the miss later, the next time that image is launched.
- Any unexpected exception is swallowed too.

## Why the read path is defensive

`load_image_shells` returns `{}` for a missing, empty or malformed store, and coerces every key
and value to `str` on the way out. A corrupt store must never crash the launch or diagnose path;
degrading to `{}` costs a user the image step of the precedence and drops them to `sh`, which is a
working shell. Raising would cost them the box.
