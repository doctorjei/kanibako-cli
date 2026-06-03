#!/usr/bin/env bash
# Mint release-candidate and promotion git tags for kanibako.
#
# bump2version (config at .bumpversion.cfg) only creates final v<ver> tags.
# This helper adds two ergonomic modes:
#
#   Mode A (mint rc):  scripts/release-rc.sh <part> [--rc N]
#                      Bumps the version (commit, NO tag) then tags v<ver>-rcN.
#
#   Mode B (promote):  scripts/release-rc.sh --promote <ver>
#                      Tags v<ver> on the CURRENT HEAD (the rc commit). No bump.
#
# RC discipline: NEVER push the rc tag and the release tag back-to-back. Push the
# rc, wait for GREEN CI on it, and only then promote. This script never pushes;
# the operator controls when tags reach origin.
#
# Usage:
#   scripts/release-rc.sh patch                    # bump patch, tag v<ver>-rc1
#   scripts/release-rc.sh minor --rc 2             # bump minor, tag v<ver>-rc2
#   scripts/release-rc.sh patch --version 1.3.0    # set version explicitly
#   scripts/release-rc.sh --promote 1.2.5          # tag v1.2.5 on HEAD
#   scripts/release-rc.sh --dry-run patch          # print commands, run nothing
#   scripts/release-rc.sh --help
#
# Requires: git + bump2version (or bumpversion) + coreutils.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$REPO_ROOT/.bumpversion.cfg"

usage() {
    cat <<'EOF'
release-rc.sh — mint release-candidate (v<ver>-rcN) and promotion (v<ver>) tags.

bump2version only makes final v<ver> tags; this wraps it for an rc-then-promote flow.

MODE A — mint an rc tag (default):
  scripts/release-rc.sh <part> [--rc N] [--version X.Y.Z] [--dry-run]
    <part>        patch | minor | major
    --rc N        rc number (default: 1)
    --version     set the new version explicitly (still needs a part; defaults to patch)
  Steps: verify clean tree -> bump2version --no-tag <part> (commits "Release v<new>")
         -> read new version -> git tag v<new>-rc<N>. Does NOT push.

MODE B — promote an rc to a release:
  scripts/release-rc.sh --promote <ver> [--dry-run]
    <ver>         X.Y.Z (e.g. 1.2.5)
  Steps: git tag v<ver> on the CURRENT HEAD (run with HEAD at the rc commit).
         No version bump. Does NOT push.

GLOBAL:
  --dry-run       print each git/bump command it WOULD run, execute nothing
  -h, --help      this help

RC DISCIPLINE:
  Never push the rc tag and the release tag back-to-back. Push the rc, wait for
  GREEN CI, then promote.

EXAMPLE END-TO-END FLOW:
  # 1. Mint rc on a clean tree:
  scripts/release-rc.sh patch --rc 1
  # 2. Push branch + rc tag (this script prints the exact commands):
  git push origin main && git push origin v1.2.4-rc1
  # 3. Wait for CI to go GREEN on v1.2.4-rc1.
  # 4. With HEAD still at the rc commit, promote:
  scripts/release-rc.sh --promote 1.2.4
  # 5. Push the release tag:
  git push origin v1.2.4
EOF
}

# --- arg parsing -----------------------------------------------------------

DRY_RUN=false
PROMOTE_VER=""
PART=""
RC=1
NEW_VERSION=""
MODE="rc"   # rc | promote

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --promote)
            MODE="promote"
            if [[ $# -lt 2 ]]; then
                echo "Error: --promote requires a version argument (X.Y.Z)." >&2
                exit 1
            fi
            PROMOTE_VER="$2"
            shift 2
            ;;
        --rc)
            if [[ $# -lt 2 ]]; then
                echo "Error: --rc requires a number argument." >&2
                exit 1
            fi
            RC="$2"
            shift 2
            ;;
        --version)
            if [[ $# -lt 2 ]]; then
                echo "Error: --version requires an argument (X.Y.Z)." >&2
                exit 1
            fi
            NEW_VERSION="$2"
            shift 2
            ;;
        patch|minor|major)
            PART="$1"
            shift
            ;;
        *)
            echo "Unknown option: $1" >&2
            echo "Try '$(basename "$0") --help'." >&2
            exit 1
            ;;
    esac
done

# run / show a command, honouring --dry-run
run() {
    if $DRY_RUN; then
        echo "[dry-run] $*"
    else
        "$@"
    fi
}

# --- common: ensure we're in a git repo ------------------------------------

if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Error: not inside a git repository ($REPO_ROOT)." >&2
    exit 1
fi

# ===========================================================================
# MODE B — promote
# ===========================================================================
if [[ "$MODE" == "promote" ]]; then
    if ! [[ "$PROMOTE_VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        echo "Error: --promote version must look like X.Y.Z (got '$PROMOTE_VER')." >&2
        exit 1
    fi
    TAG="v${PROMOTE_VER}"
    echo "=== Promote: tagging ${TAG} on current HEAD ==="
    run git -C "$REPO_ROOT" tag "$TAG"
    echo ""
    echo "Created release tag ${TAG} (no bump performed)."
    echo "Push it when ready:"
    echo "    git push origin ${TAG}"
    exit 0
fi

# ===========================================================================
# MODE A — mint rc
# ===========================================================================

# Validate rc number.
if ! [[ "$RC" =~ ^[0-9]+$ ]]; then
    echo "Error: --rc must be a non-negative integer (got '$RC')." >&2
    exit 1
fi

# Default the part to patch when an explicit version is requested.
if [[ -z "$PART" ]]; then
    if [[ -n "$NEW_VERSION" ]]; then
        PART="patch"
    else
        echo "Error: a part (patch|minor|major) is required in rc mode." >&2
        echo "Try '$(basename "$0") --help'." >&2
        exit 1
    fi
fi

# Validate explicit version shape if given.
if [[ -n "$NEW_VERSION" ]] && ! [[ "$NEW_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Error: --version must look like X.Y.Z (got '$NEW_VERSION')." >&2
    exit 1
fi

# Resolve the bump2version executable robustly.
BUMPER=""
if command -v bump2version >/dev/null 2>&1; then
    BUMPER="$(command -v bump2version)"
elif command -v bumpversion >/dev/null 2>&1; then
    BUMPER="$(command -v bumpversion)"
elif [[ -x "$HOME/.venv/bin/bump2version" ]]; then
    BUMPER="$HOME/.venv/bin/bump2version"
fi

if [[ -z "$BUMPER" ]]; then
    echo "Error: could not find bump2version or bumpversion." >&2
    echo "Tried: 'command -v bump2version', 'command -v bumpversion', '\$HOME/.venv/bin/bump2version'." >&2
    echo "Install it (pip install bump2version) or ensure ~/.venv/bin is populated." >&2
    exit 1
fi

# Verify the working tree has no MODIFIED tracked files (untracked are fine —
# mirrors bump2version's own behaviour). Under --dry-run this is reported but
# not enforced, so the dry-run stays safely verifiable on a dirty tree.
if ! ( git -C "$REPO_ROOT" diff --quiet && git -C "$REPO_ROOT" diff --cached --quiet ); then
    if $DRY_RUN; then
        echo "[dry-run] NOTE: working tree has modified tracked files; a real run would refuse here."
    else
        echo "Error: working tree has modified tracked files. Commit or stash first." >&2
        echo "       (untracked files are fine; bump2version ignores them.)" >&2
        exit 1
    fi
fi

echo "=== Mint rc: bumping '${PART}' (no auto-tag) ==="
echo "Using bumper: $BUMPER"

# Bump WITHOUT auto-tag but WITH commit (commit=True in cfg). bump2version still
# needs a part even with --new-version.
if [[ -n "$NEW_VERSION" ]]; then
    run "$BUMPER" --no-tag --new-version "$NEW_VERSION" "$PART"
else
    run "$BUMPER" --no-tag "$PART"
fi

# Read the new current_version from the cfg.
if $DRY_RUN; then
    if [[ -n "$NEW_VERSION" ]]; then
        VER="$NEW_VERSION"
    else
        VER="<new-version>"
    fi
    echo "[dry-run] (would read new current_version from $CFG)"
else
    VER="$(sed -n 's/^current_version[[:space:]]*=[[:space:]]*//p' "$CFG" | head -n1)"
    if [[ -z "$VER" ]]; then
        echo "Error: could not read current_version from $CFG after bump." >&2
        exit 1
    fi
fi

RC_TAG="v${VER}-rc${RC}"

echo ""
echo "=== Tagging release candidate ${RC_TAG} ==="
run git -C "$REPO_ROOT" tag "$RC_TAG"

echo ""
echo "Created rc tag ${RC_TAG} on the 'Release v${VER}' commit (NOT pushed)."
echo ""
echo "Push the branch and the rc tag:"
echo "    git push origin main && git push origin ${RC_TAG}"
echo ""
echo "Then WAIT for GREEN CI on ${RC_TAG} before promoting. Once green:"
echo "    scripts/release-rc.sh --promote ${VER}"
echo ""
echo "RC discipline: never push the rc tag and the release tag back-to-back."
