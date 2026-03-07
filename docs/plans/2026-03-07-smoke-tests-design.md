# Deployment Smoke Test Suite — Design

**Date:** 2026-03-07
**Task:** #31 Deploy and test kanibako host variants

## Goal

Build a portable smoke test suite that validates a deployed kanibako host
(container, LXC, or VM) is correctly configured and functional. The suite
runs on the host itself and covers environment, podman, kanibako CLI,
container lifecycle, persistent state, credentials, helpers, and networking.

## Approach

Modular shell test runner (Approach B from brainstorming):

- **Runner** (`smoke-test.sh`) discovers and runs `tests/NN-name.sh` files
- **TAP-style output** with pass/fail/skip counts and summary
- Each test file is self-contained, can run individually
- Skip conditions for tests that depend on previous infrastructure
- Colorized output (auto-detected), `--list` flag, selective execution

## Directory Structure

```
host-definitions/smoke-tests/
  smoke-test.sh              # Runner
  lib/tap.sh                 # TAP helper functions (ok, fail, skip, diag)
  tests/
    01-environment.sh        # OS, user, groups, subuid/subgid, packages
    02-podman.sh             # Rootless podman, pull, run, storage driver
    03-kanibako-cli.sh       # Version, help, image list
    04-container-launch.sh   # init + start, exec, stop, cleanup
    05-persistent-state.sh   # Files persist across stop/start, vault mounts
    06-credentials.sh        # Credential flow (skip if no agent plugin)
    07-helpers.sh            # Spawn, messaging, broadcast, cleanup
    08-networking.sh         # DNS resolution, internet connectivity
```

## Test Details

### 01-environment.sh
- `agent` user exists with UID 1000
- subuid/subgid mapped (100000:65536)
- fuse-overlayfs installed
- Required packages: ripgrep, gh, tmux, git, curl, python3

### 02-podman.sh
- `podman info` succeeds (rootless)
- Storage driver is overlay (fuse-overlayfs)
- Can pull `docker.io/library/busybox:latest`
- Can run `podman run --rm busybox echo hello`
- Clean up test image after

### 03-kanibako-cli.sh
- `kanibako --version` outputs version string
- `kanibako --help` exits 0
- `kanibako image list` exits 0
- Skip all if `kanibako` not on PATH

### 04-container-launch.sh
- Create temp project dir
- `kanibako init <tmpdir>` succeeds
- `kanibako start <name>` launches container
- `kanibako shell <name> -- echo hello` returns "hello"
- `kanibako stop <name>` stops container
- Clean up project + name registration
- Skip all if podman not functional

### 05-persistent-state.sh
- Start container, write a file via exec
- Stop container, restart, verify file exists
- Verify vault directory is mounted
- Skip if podman not functional

### 06-credentials.sh
- Check if agent plugin is installed (`kanibako image list` shows agent)
- Verify credential check path exists on host
- Skip all if no agent plugin

### 07-helpers.sh
- Init project with helpers enabled (default)
- Start container, verify helper socket exists
- Send a message, verify delivery
- Check broadcast.log written
- Cleanup
- Skip if podman not functional

### 08-networking.sh
- Container can resolve DNS (`nslookup github.com`)
- Container can reach the internet (`curl -s -o /dev/null https://github.com`)
- Skip if podman not functional

## Runner Features

- `./smoke-test.sh` — run all tests
- `./smoke-test.sh 01 03` — run only specified tests
- `./smoke-test.sh --list` — list available tests
- Exit code 0 = all pass, 1 = any fail
- Color output auto-detected (NO_COLOR respected)
- TAP helpers: `ok "description"`, `fail "description" "details"`,
  `skip "description" "reason"`, `diag "message"`

## Deployment Docs Updates

- README: step-by-step for building host container locally
- README: step-by-step for VM provisioning (Vagrant, cloud-init)
- README: how to run smoke tests after deployment
- Review and fix existing scripts for stale references

## Verification

All droste base images confirmed on GHCR (2026-03-07):
- `droste-seed` (413 MB) → kanibako-min
- `droste-fiber` (1.02 GB) → kanibako-oci
- `droste-thread` (1.16 GB) → kanibako-lxc
- `droste-hair` → kanibako-vm

Kanibako images also published: kanibako-min, kanibako-oci, kanibako-lxc, kanibako-vm.
Kento E2E validated VM boot, SSH, networking on droste-fiber-vm (2026-03-06).
