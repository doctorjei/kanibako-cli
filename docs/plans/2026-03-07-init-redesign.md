# Init Command Redesign

## Summary

Merge `kanibako init` and `kanibako new` into a single `init` command that
supports all project modes and accepts `--image` for setting the container
image at project creation time.

## Current State

- `kanibako init --local` — creates a decentralized project in cwd
- `kanibako new --local <path>` — creates a directory and initializes a
  decentralized project in it
- Neither command accepts `--image`
- AC/workset projects are implicitly created on first `kanibako start`
- `kanibako start -i/--image` sets the image and persists it for new projects

## Design

### Unified `init` command

```
kanibako init [path] [--local] [--image IMAGE] [--no-vault] [--distinct-auth]
```

**Arguments:**
- `path` (optional) — target directory. Defaults to cwd. Created if it
  doesn't exist.
- `--local` — use decentralized mode (`.kanibako/` inside project dir).
  Without this flag, mode is auto-detected: AC by default, workset if
  inside an existing workset.
- `--image IMAGE` — container image for this project. Persisted to
  `project.toml`. Defaults to global `container_image` setting.
- `--no-vault` — disable vault mounts.
- `--distinct-auth` — use distinct credentials (no sync from host).

**Behavior:**
1. Resolve the target directory (cwd or `path`). Create it if needed.
2. Check if a project already exists at that path. If so, print a
   warning and exit non-zero.
3. If `--local`: call `resolve_decentralized_project()` with
   `initialize=True`.
4. Otherwise: call `resolve_project()` with `initialize=True` (uses
   the same AC/workset detection as `start`).
5. Persist `--image` (or the default) to `project.toml` via
   `write_project_config()`.
6. For decentralized projects, write `.kanibako/` to `.gitignore`.

### Remove `new` command

The `new` subcommand is removed. Its behavior (create directory + init)
is absorbed into `init [path]`.

Migration: `kanibako new --local myproject` becomes
`kanibako init --local myproject`.

### `--image` on `start` and `connect`

These already have `-i/--image`. No changes needed — they continue to
work as before (override image for the run, persist on first use for
new projects).

## Changes Required

1. **`commands/init.py`**: Merge `run_init` and `run_new` into a single
   `run_init`. Add `--image` argument. Support AC/workset modes (call
   `resolve_project()` when `--local` is not set). Add already-exists
   check.
2. **`cli.py`**: Remove `new` from `_SUBCOMMANDS` and parser
   registration. Remove `add_new_parser` import.
3. **Tests**: Update init tests, remove new-specific tests, add tests
   for `--image` persistence and already-exists behavior.
4. **`config.py`**: No changes — `write_project_config()` already
   exists.
