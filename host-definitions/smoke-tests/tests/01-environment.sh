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

# Required packages (hard requirements)
for cmd in tmux git curl python3; do
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd is installed"
    else
        fail "$cmd is installed"
    fi
done

# Optional packages (provided by Ansible playbook / Containerfile)
for cmd in rg gh; do
    if command -v "$cmd" &>/dev/null; then
        ok "$cmd is installed"
    else
        skip "$cmd is installed" "not provisioned (install via Ansible playbook)"
    fi
done

# fuse-overlayfs
if command -v fuse-overlayfs &>/dev/null; then
    ok "fuse-overlayfs is installed"
else
    skip "fuse-overlayfs is installed" "not required on all variants"
fi
