#!/usr/bin/env bash
# Build the store `kinemata claims` resolves runtime path claims against.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/build-claims-store.sh [-h|--help]

Builds a kanibako store, linked from .claims-store at the repository root, for
`kinemata claims` to resolve runtime path claims against (`[claims]
resolve_in` in kinemata.toml). The docs name paths kanibako creates for a
user -- box.yaml, vault/, kanibako.cfg -- and this store is where they
are checked against what kanibako really creates.

The store is built only with kanibako's own commands, under an isolated
HOME and XDG base set inside it:
  kanibako setup --agent claude --refresh-templates
  kanibako create <primary box>
  kanibako workset create --name ws1 <workset> + kanibako create (named box)
  kanibako create --standalone <standalone box>
Nothing in it is written by hand. No container is started; `create` only
lays out the store.

The store is built in a fresh temp directory and .claims-store links to it,
because a store built under a box's workspace path (/home/agent/workspace)
collides with that box's own workspace binding. Idempotent: the previous store and link are removed and
rebuilt on every run. A .claims-store link that resolves anywhere but a
kanibako-claims-store.* directory under the temp root is refused (exit 2) and
left alone. The link is gitignored, and every kinemata scan skips
it.

Environment:
  PYTHON  interpreter with kanibako importable (default: python3).
          Locally, point it at a venv and set PYTHONPATH=src to build with
          this tree's code.
EOF
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  "") ;;
  *) usage >&2; exit 2 ;;
esac

repo=$(cd "$(dirname "$0")/.." && pwd)
store="$repo/.claims-store"
python=${PYTHON:-python3}

# The store itself lives in a fresh temp directory and .claims-store is a link
# to it. In a box, a repository sits under /home/agent/workspace, where every
# box binds its workspace, and `create` refuses a store there: its cache
# binding would sit inside that workspace binding. A temp directory builds the
# same store in a box and in CI.
# The old link's target is deleted only if it resolves to a store this script
# made: a kanibako-claims-store.* directory directly under the temp root. A
# link anywhere else was not made here, so the run refuses and touches nothing.
tmproot=$(readlink -f "${TMPDIR:-/tmp}")
if [ -L "$store" ]; then
  old=$(readlink -f "$store" || true)
  case "$old" in
    "$tmproot"/kanibako-claims-store.*)
      if [ "$(dirname "$old")" != "$tmproot" ]; then
        echo "build-claims-store: refusing: $store links to $old, not a store this script built" >&2
        exit 2
      fi
      rm -rf "$old" ;;
    *)
      echo "build-claims-store: refusing: $store links to ${old:-$(readlink "$store")}, not a store this script built" >&2
      exit 2 ;;
  esac
  rm -f "$store"
elif [ -e "$store" ]; then
  rm -rf "$store"
fi
target=$(mktemp -d "${TMPDIR:-/tmp}/kanibako-claims-store.XXXXXX")
ln -s "$target" "$store"
store=$target
mkdir -p "$store/home" "$store/projects" \
  "$store/xdg/data" "$store/xdg/config" "$store/xdg/state" \
  "$store/xdg/cache" "$store/xdg/run"
chmod 700 "$store/xdg/run"

export HOME="$store/home"
export XDG_DATA_HOME="$store/xdg/data"
export XDG_CONFIG_HOME="$store/xdg/config"
export XDG_STATE_HOME="$store/xdg/state"
export XDG_CACHE_HOME="$store/xdg/cache"
export XDG_RUNTIME_DIR="$store/xdg/run"
unset KANIBAKO_NAME KANIBAKO_AGENT

kanibako() {
  echo "+ kanibako $*"
  "$python" -m kanibako "$@" </dev/null
}

cd "$store/projects"
kanibako setup --agent claude --refresh-templates
kanibako create "$store/projects/primarybox"
kanibako workset create --name ws1 "$store/projects/ws1"
(cd "$store/projects/ws1" && kanibako create wsbox)
kanibako create --standalone "$store/projects/solo"
