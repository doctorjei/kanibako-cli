#!/usr/bin/env bash
# Build all kanibako packages (sdist + wheel) in dependency order.
#
# Usage:
#   scripts/build-all.sh              # build only
#   scripts/build-all.sh --upload     # build + upload to PyPI via twine
#   scripts/build-all.sh --clean      # remove dist/ dirs without building
#
# Requires: python3 -m build (pip install build)
# Optional: twine (pip install twine) for --upload

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

PACKAGES=(
    "."                               # kanibako-base
    "packages/agent-claude"           # kanibako-agent-claude
    "packages/plugin-claude-wrapper"  # kanibako-plugin-claude (transitional)
    "packages/meta"                   # kanibako (meta)
)

UPLOAD=false
CLEAN_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --upload)  UPLOAD=true ;;
        --clean)   CLEAN_ONLY=true ;;
        -h|--help)
            echo "Usage: $(basename "$0") [--upload] [--clean]"
            echo ""
            echo "Build all kanibako packages (sdist + wheel)."
            echo ""
            echo "Options:"
            echo "  --upload   Upload to PyPI via twine after building"
            echo "  --clean    Remove dist/ dirs without building"
            echo "  -h,--help  Show this help"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

clean() {
    for pkg in "${PACKAGES[@]}"; do
        local dir="$REPO_ROOT/$pkg"
        rm -rf "$dir/dist" "$dir/build" "$dir/*.egg-info"
        # setuptools may leave egg-info under src/
        find "$dir" -maxdepth 3 -name '*.egg-info' -type d -exec rm -rf {} + 2>/dev/null || true
    done
    echo "Cleaned build artifacts."
}

if $CLEAN_ONLY; then
    clean
    exit 0
fi

# Verify build module is available
if ! python3 -m build --version >/dev/null 2>&1; then
    echo "Error: 'python3 -m build' not found. Install it: pip install build" >&2
    exit 1
fi

clean

echo "=== Building all packages ==="
for pkg in "${PACKAGES[@]}"; do
    dir="$REPO_ROOT/$pkg"
    name=$(python3 -c "
import tomllib, pathlib
d = tomllib.loads(pathlib.Path('$dir/pyproject.toml').read_text())
print(d['project']['name'])
")
    echo ""
    echo "--- $name ($pkg) ---"
    python3 -m build "$dir" --outdir "$dir/dist"
done

echo ""
echo "=== Build complete ==="
for pkg in "${PACKAGES[@]}"; do
    echo "  $pkg/dist/"
    ls "$REPO_ROOT/$pkg/dist/"
done

if $UPLOAD; then
    if ! command -v twine >/dev/null 2>&1; then
        echo ""
        echo "Error: twine not found. Install it: pip install twine" >&2
        exit 1
    fi
    echo ""
    echo "=== Uploading to PyPI ==="
    for pkg in "${PACKAGES[@]}"; do
        twine upload "$REPO_ROOT/$pkg/dist/"*
    done
    echo ""
    echo "=== Upload complete ==="
fi
