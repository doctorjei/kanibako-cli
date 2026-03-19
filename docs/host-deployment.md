# Host Deployment

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

For always-on deployments, use [Kento](https://github.com/doctorjei/kento) to
stand up `kanibako-lxc` or `kanibako-vm` as the host.  Kento reads OCI images
directly from Podman's layer store -- no export or conversion step.

## LXC host (Proxmox or standalone)

```bash
# Pull the image
podman pull ghcr.io/doctorjei/kanibako-lxc:latest

# Create and start the LXC (auto-detects Proxmox)
sudo kento container create kanibako-lxc --name kanibako-host
sudo kento container start kanibako-host
```

## VM host (QEMU)

```bash
podman pull ghcr.io/doctorjei/kanibako-vm:latest
sudo kento container create kanibako-vm --name kanibako-host --vm
sudo kento container start kanibako-host
```

## OCI nested host (alternative)

`kanibako-oci` also serves as a host container -- it includes rootless
Podman, so you can run Kanibako itself inside it and spawn nested agent
containers.  This is useful when Kento is not available.

```bash
podman pull ghcr.io/doctorjei/kanibako-oci:latest

# Run with nested podman support
podman run --privileged -it \
    -v kanibako-data:/home/agent/.local/share/kanibako \
    -v kanibako-config:/home/agent/.config \
    ghcr.io/doctorjei/kanibako-oci:latest
```

The `--privileged` flag is required for rootless Podman to work inside the
container.  Alternatively, use `--cap-add=SYS_ADMIN --security-opt seccomp=unconfined`
for a narrower permission set.

Install Kanibako and plugins inside the host container:

```bash
pip install kanibako    # installs kanibako-base + kanibako-agent-claude
```

## Persistent state

Mount named volumes or host directories to preserve state across restarts:

| Mount target | Purpose |
|------|---------|
| `/home/agent/.local/share/kanibako` | Project state, agent configs, names |
| `/home/agent/.config` | kanibako.toml, Podman storage config |
| `/home/agent/workspace` | Optional: bind a host project directory |

## Building locally

```bash
# OCI container (default)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-fiber:latest \
    -t kanibako-oci src/kanibako/containers/

# LXC system container (requires VARIANT=lxc for systemd/networking fixes)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-thread:latest \
    --build-arg VARIANT=lxc \
    -t kanibako-lxc src/kanibako/containers/

# VM host (requires VARIANT=vm)
podman build -f src/kanibako/containers/Containerfile.kanibako \
    --build-arg BASE_IMAGE=ghcr.io/doctorjei/droste-hair:latest \
    --build-arg VARIANT=vm \
    -t kanibako-vm src/kanibako/containers/
```

The `VARIANT` build arg enables variant-specific configuration in the shared
Containerfile.  OCI builds don't need it (defaults to `oci`).  LXC builds
add systemd unit masking, DHCP networking, and cgroupfs for rootless Podman.
VM builds get the same LXC fixes plus a systemd entrypoint.

## VM Variant

For bare-metal or VM deployments (Proxmox, KVM/libvirt, VirtualBox), Kanibako
ships an Ansible playbook and per-provider creation scripts.  The playbook
mirrors the base + host Containerfiles -- same packages, same user setup, same
rootless Podman configuration.

### Ansible playbook (standalone)

Run directly against any Ubuntu host:

```bash
ansible-playbook host-definitions/ansible/playbook.yml \
    -i 'myhost,' -u root

# With Claude plugin
ansible-playbook host-definitions/ansible/playbook.yml \
    -i 'myhost,' -u root -e install_claude_plugin=true
```

### Proxmox

```bash
host-definitions/vm/create-proxmox-vm.sh \
    --ssh-key ~/.ssh/id_ed25519.pub --start

# With Claude plugin, custom resources
host-definitions/vm/create-proxmox-vm.sh \
    --ssh-key ~/.ssh/id.pub --claude \
    --memory 8192 --cores 4 --disk-size 64G --start
```

Key flags: `--vmid`, `--name`, `--memory`, `--cores`, `--disk-size`,
`--storage`, `--bridge`, `--ssh-key`, `--claude`, `--repo`, `--branch`,
`--start`.

### KVM / libvirt

Prerequisites: `virt-install`, `qemu-img`, `cloud-localds`
(`sudo apt install virtinst qemu-utils cloud-image-utils`).

```bash
host-definitions/vm/create-libvirt-vm.sh \
    --ssh-key ~/.ssh/id_ed25519.pub

# With Claude plugin
host-definitions/vm/create-libvirt-vm.sh \
    --ssh-key ~/.ssh/id.pub --claude --name my-kanibako
```

Key flags: `--name`, `--memory`, `--vcpus`, `--disk-size`, `--network`,
`--ssh-key`, `--claude`, `--repo`, `--branch`.

### Vagrant (VirtualBox)

No host-side Ansible needed -- uses `ansible_local` provisioner inside the VM.

```bash
cd host-definitions/vm
vagrant up

# With Claude plugin
KANIBAKO_CLAUDE=true vagrant up
```

### After provisioning

All methods produce the same result: an Ubuntu VM with an `agent` user
(UID 1000), rootless Podman, and Kanibako installed.  SSH in and use
`kanibako` normally:

```bash
ssh agent@<vm-ip>
cd ~/my-project && kanibako
```

## Smoke Tests

A portable smoke test suite validates that a deployed Kanibako host
(container, LXC, or VM) is correctly configured.  The tests run on the
host itself -- no external dependencies required.

```bash
# Run all tests
host-definitions/smoke-tests/smoke-test.sh

# Run specific tests
host-definitions/smoke-tests/smoke-test.sh 01 02

# List available tests
host-definitions/smoke-tests/smoke-test.sh --list
```

| Test | What it checks |
|------|---------------|
| `01-environment` | agent user, subuid/subgid, required packages (tmux, git, curl, python3), optional packages (rg, gh) |
| `02-podman` | rootless Podman, storage driver, pull/run |
| `03-kanibako-cli` | Kanibako installed, --version, --help, rig list |
| `04-container-launch` | init, one-shot shell exec, stop/cleanup |
| `05-persistent-state` | files persist across container runs |
| `06-credentials` | agent plugin detection, credential path |
| `07-helpers` | comms directory mounted inside container |
| `08-networking` | DNS resolution, internet access from container |

Tests use TAP-style output with color (respects `NO_COLOR`).  Exit code
is 0 if all tests pass, 1 if any fail.
