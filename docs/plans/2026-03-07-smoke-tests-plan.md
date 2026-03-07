# Smoke Test Suite Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a portable smoke test suite that validates deployed kanibako hosts.

**Architecture:** Modular shell test runner with TAP-style output. A runner script
discovers and executes numbered test files. Each test file sources a shared TAP library
for assertions. Tests cover environment, podman, kanibako CLI, container lifecycle,
persistent state, credentials, helpers, and networking.

**Tech Stack:** Bash (POSIX-compatible where possible), TAP output format

**Design doc:** `docs/plans/2026-03-07-smoke-tests-design.md`

---

### Task 1: Create directory structure and TAP library

**Files:**
- Create: `host-definitions/smoke-tests/lib/tap.sh`

**Step 1: Create the TAP helper library**

```bash
mkdir -p host-definitions/smoke-tests/lib host-definitions/smoke-tests/tests
```

Write `host-definitions/smoke-tests/lib/tap.sh`:

```bash
#!/usr/bin/env bash
# TAP (Test Anything Protocol) helper library for smoke tests.
# Source this file; do not execute directly.

_TAP_PASS=0
_TAP_FAIL=0
_TAP_SKIP=0
_TAP_NUM=0

# Detect color support
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    _C_GREEN=$'\033[32m'
    _C_RED=$'\033[31m'
    _C_YELLOW=$'\033[33m'
    _C_CYAN=$'\033[36m'
    _C_RESET=$'\033[0m'
    _C_BOLD=$'\033[1m'
else
    _C_GREEN="" _C_RED="" _C_YELLOW="" _C_CYAN="" _C_RESET="" _C_BOLD=""
fi

ok() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_PASS=$((_TAP_PASS + 1))
    printf '%sok %d - %s%s\n' "$_C_GREEN" "$_TAP_NUM" "$1" "$_C_RESET"
}

fail() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_FAIL=$((_TAP_FAIL + 1))
    printf '%snot ok %d - %s%s\n' "$_C_RED" "$_TAP_NUM" "$1" "$_C_RESET"
    if [[ -n "${2:-}" ]]; then
        diag "$2"
    fi
}

skip() {
    _TAP_NUM=$((_TAP_NUM + 1))
    _TAP_SKIP=$((_TAP_SKIP + 1))
    printf '%sok %d - %s # SKIP %s%s\n' "$_C_YELLOW" "$_TAP_NUM" "$1" "${2:-}" "$_C_RESET"
}

diag() {
    printf '%s# %s%s\n' "$_C_CYAN" "$1" "$_C_RESET"
}

# Run a command and call ok/fail based on exit code.
# Usage: check "description" command arg1 arg2 ...
check() {
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        ok "$desc"
    else
        fail "$desc" "command failed: $*"
    fi
}

# Run a command and check that stdout contains a substring.
# Usage: check_output "description" "expected_substring" command arg1 ...
check_output() {
    local desc="$1" expected="$2"; shift 2
    local output
    if output=$("$@" 2>&1); then
        if echo "$output" | grep -qF "$expected"; then
            ok "$desc"
        else
            fail "$desc" "expected '$expected' in output, got: $output"
        fi
    else
        fail "$desc" "command failed (rc=$?): $*"
    fi
}

tap_summary() {
    local total=$((_TAP_PASS + _TAP_FAIL + _TAP_SKIP))
    echo ""
    printf '%s1..%d%s\n' "$_C_BOLD" "$total" "$_C_RESET"
    printf '# pass: %s%d%s  fail: %s%d%s  skip: %s%d%s\n' \
        "$_C_GREEN" "$_TAP_PASS" "$_C_RESET" \
        "$_C_RED" "$_TAP_FAIL" "$_C_RESET" \
        "$_C_YELLOW" "$_TAP_SKIP" "$_C_RESET"
    if [[ $_TAP_FAIL -gt 0 ]]; then
        printf '%sFAILED%s\n' "$_C_RED" "$_C_RESET"
        return 1
    else
        printf '%sALL PASSED%s\n' "$_C_GREEN" "$_C_RESET"
        return 0
    fi
}
```

**Step 2: Verify it sources cleanly**

```bash
bash -n host-definitions/smoke-tests/lib/tap.sh && echo OK
```

Expected: `OK`

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/lib/tap.sh
git commit -m "Add TAP helper library for smoke tests (#31)"
```

---

### Task 2: Create the test runner

**Files:**
- Create: `host-definitions/smoke-tests/smoke-test.sh`

**Step 1: Write the runner**

Write `host-definitions/smoke-tests/smoke-test.sh`:

```bash
#!/usr/bin/env bash
# Smoke test runner for kanibako host deployments.
# Usage:
#   ./smoke-test.sh          # run all tests
#   ./smoke-test.sh 01 03    # run specific tests
#   ./smoke-test.sh --list   # list available tests
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/tap.sh
source "$SCRIPT_DIR/lib/tap.sh"

TEST_DIR="$SCRIPT_DIR/tests"

# List mode
if [[ "${1:-}" == "--list" ]]; then
    for f in "$TEST_DIR"/[0-9]*.sh; do
        [[ -f "$f" ]] || continue
        basename "$f"
    done
    exit 0
fi

# Collect test files (all or filtered)
tests=()
if [[ $# -eq 0 ]]; then
    for f in "$TEST_DIR"/[0-9]*.sh; do
        [[ -f "$f" ]] && tests+=("$f")
    done
else
    for pattern in "$@"; do
        for f in "$TEST_DIR"/${pattern}*.sh; do
            [[ -f "$f" ]] && tests+=("$f")
        done
    done
fi

if [[ ${#tests[@]} -eq 0 ]]; then
    echo "No test files found."
    exit 1
fi

# Run each test file
for test_file in "${tests[@]}"; do
    name="$(basename "$test_file")"
    printf '\n%s=== %s ===%s\n' "$_C_BOLD" "$name" "$_C_RESET"
    source "$test_file"
done

# Print summary and exit
tap_summary
```

**Step 2: Make executable and syntax-check**

```bash
chmod +x host-definitions/smoke-tests/smoke-test.sh
bash -n host-definitions/smoke-tests/smoke-test.sh && echo OK
```

Expected: `OK`

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/smoke-test.sh
git commit -m "Add smoke test runner (#31)"
```

---

### Task 3: Write test 01-environment.sh

**Files:**
- Create: `host-definitions/smoke-tests/tests/01-environment.sh`

**Step 1: Write the test**

```bash
#!/usr/bin/env bash
# Test: host environment is correctly configured.

# agent user
if id agent &>/dev/null; then
    check "agent user has UID 1000" test "$(id -u agent)" = "1000"
else
    skip "agent user has UID 1000" "agent user does not exist"
fi

# subuid/subgid (only if agent user exists)
if id agent &>/dev/null; then
    if grep -q '^agent:' /etc/subuid 2>/dev/null; then
        ok "subuid mapping exists for agent"
    else
        fail "subuid mapping exists for agent"
    fi
    if grep -q '^agent:' /etc/subgid 2>/dev/null; then
        ok "subgid mapping exists for agent"
    else
        fail "subgid mapping exists for agent"
    fi
else
    skip "subuid mapping exists for agent" "no agent user"
    skip "subgid mapping exists for agent" "no agent user"
fi

# Required packages
for cmd in rg gh tmux git curl python3; do
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd is installed"
    else
        fail "$cmd is installed"
    fi
done

# fuse-overlayfs
if command -v fuse-overlayfs &>/dev/null; then
    ok "fuse-overlayfs is installed"
else
    skip "fuse-overlayfs is installed" "not required on all variants"
fi
```

**Step 2: Syntax-check**

```bash
bash -n host-definitions/smoke-tests/tests/01-environment.sh && echo OK
```

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/tests/01-environment.sh
git commit -m "Add environment smoke test (#31)"
```

---

### Task 4: Write test 02-podman.sh

**Files:**
- Create: `host-definitions/smoke-tests/tests/02-podman.sh`

**Step 1: Write the test**

```bash
#!/usr/bin/env bash
# Test: rootless podman is functional.

if ! command -v podman &>/dev/null; then
    skip "podman is installed" "podman not found"
    skip "podman info succeeds" "podman not found"
    skip "storage driver is overlay" "podman not found"
    skip "podman can pull busybox" "podman not found"
    skip "podman can run a container" "podman not found"
    return 0  # sourced, not executed
fi

ok "podman is installed"
check "podman info succeeds" podman info

# Storage driver
_driver=$(podman info --format '{{.Store.GraphDriverName}}' 2>/dev/null || echo "unknown")
if [[ "$_driver" == "overlay" ]]; then
    ok "storage driver is overlay"
else
    diag "storage driver: $_driver"
    skip "storage driver is overlay" "driver is $_driver (may be fine)"
fi

# Pull and run
if podman pull --quiet docker.io/library/busybox:latest >/dev/null 2>&1; then
    ok "podman can pull busybox"
else
    fail "podman can pull busybox"
fi

_output=$(podman run --rm busybox echo smoke-test-ok 2>&1)
if [[ "$_output" == *"smoke-test-ok"* ]]; then
    ok "podman can run a container"
else
    fail "podman can run a container" "output: $_output"
fi

# Clean up
podman rmi -f busybox >/dev/null 2>&1 || true
```

**Step 2: Syntax-check**

```bash
bash -n host-definitions/smoke-tests/tests/02-podman.sh && echo OK
```

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/tests/02-podman.sh
git commit -m "Add podman smoke test (#31)"
```

---

### Task 5: Write test 03-kanibako-cli.sh

**Files:**
- Create: `host-definitions/smoke-tests/tests/03-kanibako-cli.sh`

**Step 1: Write the test**

```bash
#!/usr/bin/env bash
# Test: kanibako CLI is installed and functional.

if ! command -v kanibako &>/dev/null; then
    skip "kanibako is installed" "kanibako not on PATH"
    skip "kanibako --version outputs version" "kanibako not on PATH"
    skip "kanibako --help exits 0" "kanibako not on PATH"
    skip "kanibako image list exits 0" "kanibako not on PATH"
    return 0
fi

ok "kanibako is installed"
check_output "kanibako --version outputs version" "kanibako" kanibako --version
check "kanibako --help exits 0" kanibako --help
check "kanibako image list exits 0" kanibako image list
```

**Step 2: Syntax-check**

```bash
bash -n host-definitions/smoke-tests/tests/03-kanibako-cli.sh && echo OK
```

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/tests/03-kanibako-cli.sh
git commit -m "Add kanibako CLI smoke test (#31)"
```

---

### Task 6: Write test 04-container-launch.sh

**Files:**
- Create: `host-definitions/smoke-tests/tests/04-container-launch.sh`

**Step 1: Write the test**

```bash
#!/usr/bin/env bash
# Test: kanibako can init, start, exec into, and stop a container.

if ! command -v kanibako &>/dev/null || ! podman info &>/dev/null 2>&1; then
    skip "kanibako init" "kanibako or podman not available"
    skip "kanibako start" "kanibako or podman not available"
    skip "kanibako shell exec" "kanibako or podman not available"
    skip "kanibako stop" "kanibako or podman not available"
    return 0
fi

_SMOKE_DIR=$(mktemp -d /tmp/kanibako-smoke-XXXXXX)
_SMOKE_NAME="smoke-test-$$"

_cleanup_launch() {
    kanibako stop "$_SMOKE_NAME" 2>/dev/null || true
    rm -rf "$_SMOKE_DIR"
    kanibako box forget "$_SMOKE_NAME" --force 2>/dev/null || true
}
trap _cleanup_launch EXIT

# Init
if kanibako init "$_SMOKE_DIR" 2>&1; then
    ok "kanibako init"
else
    fail "kanibako init" "failed to init at $_SMOKE_DIR"
    return 0
fi

# Start (no-agent, non-interactive, detached)
if kanibako start "$_SMOKE_NAME" --no-helpers 2>&1; then
    ok "kanibako start"
else
    fail "kanibako start" "failed to start $_SMOKE_NAME"
    return 0
fi

# Exec
_exec_out=$(kanibako shell "$_SMOKE_NAME" -- echo smoke-ok 2>&1)
if [[ "$_exec_out" == *"smoke-ok"* ]]; then
    ok "kanibako shell exec"
else
    fail "kanibako shell exec" "output: $_exec_out"
fi

# Stop
if kanibako stop "$_SMOKE_NAME" 2>&1; then
    ok "kanibako stop"
else
    fail "kanibako stop"
fi

trap - EXIT
_cleanup_launch
```

**Step 2: Syntax-check**

```bash
bash -n host-definitions/smoke-tests/tests/04-container-launch.sh && echo OK
```

**Step 3: Commit**

```bash
git add host-definitions/smoke-tests/tests/04-container-launch.sh
git commit -m "Add container launch smoke test (#31)"
```

---

### Task 7: Write tests 05 through 08

**Files:**
- Create: `host-definitions/smoke-tests/tests/05-persistent-state.sh`
- Create: `host-definitions/smoke-tests/tests/06-credentials.sh`
- Create: `host-definitions/smoke-tests/tests/07-helpers.sh`
- Create: `host-definitions/smoke-tests/tests/08-networking.sh`

These follow the same pattern as tasks 3-6. Each test file:
- Sources the TAP library (via the runner)
- Checks preconditions and skips if unmet
- Cleans up after itself

**05-persistent-state.sh:**
- Start container, write a marker file via `kanibako shell -- touch /home/agent/workspace/.smoke-marker`
- Stop, restart, verify marker file exists via `kanibako shell -- test -f ...`
- Check vault directory exists inside container

**06-credentials.sh:**
- Check if kanibako has an agent plugin installed (look for plugin in `kanibako image list` output)
- If yes, verify credential check path exists on host
- Skip all if no plugin

**07-helpers.sh:**
- Init + start a project (helpers enabled by default)
- Verify broadcast.log gets created in the comms directory
- Stop + cleanup

**08-networking.sh:**
- Start container, exec `nslookup github.com` (or `getent hosts github.com`)
- Exec `curl -s -o /dev/null -w '%{http_code}' https://github.com` → expect 200 or 301
- Skip if podman/kanibako not available

**Step: Write all four files, syntax-check each, commit**

```bash
git add host-definitions/smoke-tests/tests/0{5,6,7,8}-*.sh
git commit -m "Add persistent-state, credentials, helpers, and networking smoke tests (#31)"
```

---

### Task 8: Review and fix deployment scripts

**Files:**
- Modify: `host-definitions/vm/create-proxmox-vm.sh`
- Modify: `host-definitions/vm/create-libvirt-vm.sh`
- Modify: `host-definitions/vm/Vagrantfile`
- Modify: `host-definitions/vm/cloud-init/user-data.yml`

**Step 1: Read each file, check for stale image refs or broken paths**

Look for:
- References to old Containerfile names (pre-droste-rebase)
- Hardcoded image names that should use the new droste tier names
- Ansible playbook path correctness
- Cloud-init variable names matching current playbook vars

**Step 2: Fix any issues found**

**Step 3: Commit if changes made**

```bash
git commit -m "Fix stale references in deployment scripts (#31)"
```

---

### Task 9: Update README deployment docs

**Files:**
- Modify: `README.md`

**Step 1: Add/update deployment and smoke test sections**

Add a section covering:
- How to run the smoke tests: `./host-definitions/smoke-tests/smoke-test.sh`
- What each test validates (brief table)
- How to run specific tests: `./smoke-test.sh 01 02`

Review existing host/VM sections for accuracy.

**Step 2: Commit**

```bash
git add README.md
git commit -m "Update README with smoke test documentation (#31)"
```

---

### Task 10: Run smoke tests on live host

**Prerequisite:** User provides SSH credentials and confirms packages are pushed.

**Step 1: SSH into the vanilla host**

**Step 2: Clone the repo (or copy smoke-tests/ directory)**

**Step 3: Run the full suite**

```bash
cd host-definitions/smoke-tests
./smoke-test.sh
```

**Step 4: Fix any failures, iterate**

**Step 5: Test individual variants**
- OCI container: pull kanibako-oci image, run smoke tests inside
- LXC container: if available, test kanibako-lxc
- VM variant: if available, test kanibako-vm

**Step 6: Coordinate with kento for Proxmox-specific testing**

Send a message to kento's mailbox with results and any Proxmox-specific items.

---

## Testing Strategy

- Tasks 1-2: Syntax check only (no runtime)
- Tasks 3-7: Syntax check during development; real validation in Task 10
- Task 8: Manual review of script correctness
- Task 9: Visual review of README
- Task 10: Live execution on deployed host — the real test

## Commit Strategy

One commit per task (tasks 1-9). Task 10 may produce fix-up commits as issues
are discovered during live testing.
