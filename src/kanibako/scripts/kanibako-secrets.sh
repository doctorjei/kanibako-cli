# kanibako-managed — export mounted secrets for every in-box login shell.
# Sourced from /etc/profile.d by every login shell (interactive CLI, VS Code
# integrated terminal, the codex panel app-server) — the paths the PID-1
# _secret_export_shim does NOT reach (they are non-login `podman exec` children).
#
# Arm's-length: the VALUE is read IN-BOX from the ro secret mount; kanibako never
# reads it. Self-adapting: only exports what is actually mounted, so this is a
# no-op on a box with no secrets configured. The directory below MUST equal
# settings_categories.SECRET_MOUNT_DIR = /run/kanibako/secrets (drift-guarded).
for f in /run/kanibako/secrets/*; do
    [ -e "$f" ] || continue
    export "$(basename "$f")"="$(cat "$f")"
done
