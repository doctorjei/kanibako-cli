#!/usr/bin/env bash
# Create a KVM/libvirt VM for kanibako using an Ubuntu cloud image + cloud-init.
#
# Prerequisites: virt-install, qemu-img, cloud-image-utils (for cloud-localds).
#
# Usage:
#   create-libvirt-vm.sh --ssh-key ~/.ssh/id_ed25519.pub
#   create-libvirt-vm.sh --ssh-key ~/.ssh/id.pub --claude --name my-kanibako
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD_INIT_TEMPLATE="${SCRIPT_DIR}/cloud-init/user-data.yml"

# ── Defaults ────────────────────────────────────────────────────────
VM_NAME="kanibako"
MEMORY=4096
VCPUS=2
DISK_SIZE="32G"
NETWORK="default"
SSH_KEY_FILE=""
INSTALL_CLAUDE="false"
KANIBAKO_REPO="https://github.com/doctorjei/kanibako.git"
KANIBAKO_BRANCH="main"

UBUNTU_IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/kanibako-vm"
IMAGE_FILENAME="noble-server-cloudimg-amd64.img"

# ── Usage ───────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a libvirt/KVM VM provisioned with kanibako via cloud-init + Ansible.

Prerequisites:
  - virt-install (from virtinst package)
  - qemu-img (from qemu-utils package)
  - cloud-localds (from cloud-image-utils package)

Options:
  --name NAME        VM name (default: kanibako)
  --memory MB        Memory in MB (default: 4096)
  --vcpus N          Virtual CPUs (default: 2)
  --disk-size SIZE   Disk size, e.g. 32G (default: 32G)
  --network NET      Libvirt network name (default: default)
  --ssh-key FILE     Path to SSH public key file (required)
  --claude           Also install kanibako-plugin-claude
  --repo URL         Git repository URL (default: upstream GitHub)
  --branch REF       Git branch or tag (default: main)
  -h, --help         Show this help message
EOF
}

# ── Parse arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)    VM_NAME="$2"; shift 2 ;;
        --memory)  MEMORY="$2"; shift 2 ;;
        --vcpus)   VCPUS="$2"; shift 2 ;;
        --disk-size) DISK_SIZE="$2"; shift 2 ;;
        --network) NETWORK="$2"; shift 2 ;;
        --ssh-key) SSH_KEY_FILE="$2"; shift 2 ;;
        --claude)  INSTALL_CLAUDE="true"; shift ;;
        --repo)    KANIBAKO_REPO="$2"; shift 2 ;;
        --branch)  KANIBAKO_BRANCH="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *)         echo "Unknown option: $1" >&2; usage >&2; exit 1 ;;
    esac
done

if [[ -z "$SSH_KEY_FILE" ]]; then
    echo "Error: --ssh-key is required" >&2
    usage >&2
    exit 1
fi

if [[ ! -f "$SSH_KEY_FILE" ]]; then
    echo "Error: SSH key file not found: $SSH_KEY_FILE" >&2
    exit 1
fi

# ── Check prerequisites ────────────────────────────────────────────
for cmd in virt-install qemu-img cloud-localds; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "Error: $cmd not found. Install the required packages:" >&2
        echo "  sudo apt install virtinst qemu-utils cloud-image-utils" >&2
        exit 1
    fi
done

# ── Download cloud image (cached) ──────────────────────────────────
mkdir -p "$CACHE_DIR"
IMAGE_PATH="${CACHE_DIR}/${IMAGE_FILENAME}"
if [[ ! -f "$IMAGE_PATH" ]]; then
    echo "Downloading Ubuntu Noble cloud image..."
    curl -fSL -o "$IMAGE_PATH" "$UBUNTU_IMAGE_URL"
else
    echo "Using cached cloud image: $IMAGE_PATH"
fi

# ── Create backing-file disk ───────────────────────────────────────
DISK_DIR="${CACHE_DIR}/disks"
mkdir -p "$DISK_DIR"
DISK_PATH="${DISK_DIR}/${VM_NAME}.qcow2"

echo "Creating disk: $DISK_PATH ($DISK_SIZE)"
qemu-img create -f qcow2 -b "$IMAGE_PATH" -F qcow2 "$DISK_PATH" "$DISK_SIZE"

# ── Generate cloud-init user-data and ISO ──────────────────────────
SSH_PUBLIC_KEY=$(cat "$SSH_KEY_FILE")
export SSH_PUBLIC_KEY KANIBAKO_REPO KANIBAKO_BRANCH INSTALL_CLAUDE

USERDATA_PATH="${CACHE_DIR}/${VM_NAME}-user-data.yml"
envsubst < "$CLOUD_INIT_TEMPLATE" > "$USERDATA_PATH"

CIDATA_ISO="${CACHE_DIR}/${VM_NAME}-cidata.iso"
echo "Generating cloud-init ISO: $CIDATA_ISO"
cloud-localds "$CIDATA_ISO" "$USERDATA_PATH"

# ── Create VM ──────────────────────────────────────────────────────
echo "Creating VM: $VM_NAME"
virt-install \
    --name "$VM_NAME" \
    --memory "$MEMORY" \
    --vcpus "$VCPUS" \
    --disk "path=${DISK_PATH},format=qcow2" \
    --disk "path=${CIDATA_ISO},device=cdrom" \
    --os-variant ubuntu24.04 \
    --network "network=${NETWORK}" \
    --graphics none \
    --console pty,target_type=serial \
    --import \
    --noautoconsole

echo ""
echo "VM '$VM_NAME' created and starting."
echo "Cloud-init provisioning will run on first boot."
echo ""
echo "Connect to console:  virsh console $VM_NAME"
echo "SSH after boot:      ssh agent@<vm-ip>"
echo "Find VM IP:          virsh domifaddr $VM_NAME"
echo ""
echo "Summary:"
echo "  Name:    $VM_NAME"
echo "  Memory:  ${MEMORY} MB"
echo "  vCPUs:   $VCPUS"
echo "  Disk:    $DISK_SIZE"
echo "  SSH key: $SSH_KEY_FILE"
echo "  Claude:  $INSTALL_CLAUDE"
