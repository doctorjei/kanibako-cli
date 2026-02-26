#!/usr/bin/env bash
# Create a Proxmox VM for kanibako using an Ubuntu cloud image + cloud-init.
#
# Prerequisites: Proxmox VE host with qm and pvesh available.
#
# Usage:
#   create-proxmox-vm.sh --ssh-key ~/.ssh/id_ed25519.pub
#   create-proxmox-vm.sh --ssh-key ~/.ssh/id.pub --claude --start
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLOUD_INIT_TEMPLATE="${SCRIPT_DIR}/cloud-init/user-data.yml"

# ── Defaults ────────────────────────────────────────────────────────
VMID=""
VM_NAME="kanibako"
MEMORY=4096
CORES=2
DISK_SIZE="32G"
STORAGE="local-lvm"
BRIDGE="vmbr0"
SSH_KEY_FILE=""
INSTALL_CLAUDE="false"
KANIBAKO_REPO="https://github.com/doctorjei/kanibako.git"
KANIBAKO_BRANCH="main"
START_VM=false

UBUNTU_IMAGE_URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
IMAGE_CACHE_DIR="/var/lib/vz/template/iso"
IMAGE_FILENAME="noble-server-cloudimg-amd64.img"

# ── Usage ───────────────────────────────────────────────────────────
usage() {
    cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Create a Proxmox VM provisioned with kanibako via cloud-init + Ansible.

Options:
  --vmid ID          VM ID (default: auto-detect next available)
  --name NAME        VM name (default: kanibako)
  --memory MB        Memory in MB (default: 4096)
  --cores N          CPU cores (default: 2)
  --disk-size SIZE   Disk size, e.g. 32G (default: 32G)
  --storage STORE    Proxmox storage target (default: local-lvm)
  --bridge BRIDGE    Network bridge (default: vmbr0)
  --ssh-key FILE     Path to SSH public key file (required)
  --claude           Also install kanibako-plugin-claude
  --repo URL         Git repository URL (default: upstream GitHub)
  --branch REF       Git branch or tag (default: main)
  --start            Start the VM after creation
  -h, --help         Show this help message
EOF
}

# ── Parse arguments ─────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --vmid)    VMID="$2"; shift 2 ;;
        --name)    VM_NAME="$2"; shift 2 ;;
        --memory)  MEMORY="$2"; shift 2 ;;
        --cores)   CORES="$2"; shift 2 ;;
        --disk-size) DISK_SIZE="$2"; shift 2 ;;
        --storage) STORAGE="$2"; shift 2 ;;
        --bridge)  BRIDGE="$2"; shift 2 ;;
        --ssh-key) SSH_KEY_FILE="$2"; shift 2 ;;
        --claude)  INSTALL_CLAUDE="true"; shift ;;
        --repo)    KANIBAKO_REPO="$2"; shift 2 ;;
        --branch)  KANIBAKO_BRANCH="$2"; shift 2 ;;
        --start)   START_VM=true; shift ;;
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

# ── Auto-detect VMID ───────────────────────────────────────────────
if [[ -z "$VMID" ]]; then
    VMID=$(pvesh get /cluster/nextid)
    echo "Auto-detected VMID: $VMID"
fi

# ── Download cloud image (cached) ──────────────────────────────────
IMAGE_PATH="${IMAGE_CACHE_DIR}/${IMAGE_FILENAME}"
if [[ ! -f "$IMAGE_PATH" ]]; then
    echo "Downloading Ubuntu Noble cloud image..."
    mkdir -p "$IMAGE_CACHE_DIR"
    curl -fSL -o "$IMAGE_PATH" "$UBUNTU_IMAGE_URL"
else
    echo "Using cached cloud image: $IMAGE_PATH"
fi

# ── Generate cloud-init user-data ──────────────────────────────────
SSH_PUBLIC_KEY=$(cat "$SSH_KEY_FILE")
export SSH_PUBLIC_KEY KANIBAKO_REPO KANIBAKO_BRANCH INSTALL_CLAUDE

SNIPPETS_DIR="/var/lib/vz/snippets"
mkdir -p "$SNIPPETS_DIR"
USERDATA_PATH="${SNIPPETS_DIR}/${VM_NAME}-user-data.yml"

envsubst < "$CLOUD_INIT_TEMPLATE" > "$USERDATA_PATH"
echo "Generated cloud-init user-data: $USERDATA_PATH"

# ── Create VM ──────────────────────────────────────────────────────
echo "Creating VM ${VMID} (${VM_NAME})..."

qm create "$VMID" \
    --name "$VM_NAME" \
    --memory "$MEMORY" \
    --cores "$CORES" \
    --net0 "virtio,bridge=${BRIDGE}" \
    --ostype l26 \
    --agent enabled=1 \
    --serial0 socket \
    --vga serial0

# Import cloud image as disk
qm importdisk "$VMID" "$IMAGE_PATH" "$STORAGE"

# Attach disk, add cloud-init drive, set boot order
qm set "$VMID" \
    --scsihw virtio-scsi-pci \
    --scsi0 "${STORAGE}:vm-${VMID}-disk-0" \
    --ide2 "${STORAGE}:cloudinit" \
    --boot order=scsi0

# Resize disk
qm resize "$VMID" scsi0 "$DISK_SIZE"

# Attach cloud-init user-data
qm set "$VMID" --cicustom "user=local:snippets/${VM_NAME}-user-data.yml"

echo "VM ${VMID} created successfully."

# ── Optionally start ───────────────────────────────────────────────
if $START_VM; then
    echo "Starting VM ${VMID}..."
    qm start "$VMID"
    echo "VM started. Cloud-init provisioning will run on first boot."
fi

echo ""
echo "Summary:"
echo "  VMID:    $VMID"
echo "  Name:    $VM_NAME"
echo "  Memory:  ${MEMORY} MB"
echo "  Cores:   $CORES"
echo "  Disk:    $DISK_SIZE"
echo "  SSH key: $SSH_KEY_FILE"
echo "  Claude:  $INSTALL_CLAUDE"
