"""Launch-shell resolution for no-agent boxes.

A box launched without an agent runs an interactive shell.  Which shell is the
single source of truth resolved here (both launch and diagnose call
``resolve_box_shell``):

1. ``box.shell`` (config ``box_shell``) — explicit value, no auto-fallback.
2. ``$KANIBAKO_SHELL`` — host env var at resolve time.
3. the IMAGE's recorded login shell for the box user (captured from the image
   via ``getent passwd`` and persisted, keyed by image digest).
4. ``sh`` — universal floor.

The image-shell store is the ``image_shells`` section of the consolidated
``system.registry`` file (``registry.yaml``) under ``std.data_path``, populated
at install/prep time; the resolver self-heals (lazy probe + store) for images
that predate the feature when a runtime is available.
"""

from __future__ import annotations

import os
import subprocess

from kanibako.project import registry_store

# ---------------------------------------------------------------------------
# Image-shell store: the ``image_shells`` section of system.registry
# (``{data_path}/global/registry.yaml``).
#
# Maps an image store key (digest, e.g. "sha256:abc..." — or the image
# reference string when no digest is available) to that image's captured login
# shell for the box user.  Schema (one section of registry.yaml):
#
#     image_shells:
#       sha256:abc...: /bin/bash
#
# This module owns the section's shape and reads/writes it via
# ``registry_store.load_section`` / ``save_section`` (which preserve every
# sibling section).  The public ``std``-based API is unchanged from when this was
# its own ``image-shells.yaml`` — only the on-disk *location* moved.
# ---------------------------------------------------------------------------

_STORE_SECTION = "image_shells"


def _store_path(std):
    # Single source of the registry location: the resolved ``config.registry``
    # (a repointed ``config.registry`` is honored).
    return std.registry


def load_image_shells(std) -> dict[str, str]:
    """Return ``{store_key: shell}`` from the image-shell store.

    Missing/empty/malformed store → ``{}`` (defensive: a corrupt store must never
    crash the launch/diagnose path — the shell falls back to ``sh``).
    """
    try:
        section = registry_store.load_section(_store_path(std), _STORE_SECTION)
    except Exception:
        return {}
    if not isinstance(section, dict):
        return {}
    return {str(k): str(v) for k, v in section.items()}


def save_image_shell(std, key: str, shell: str) -> None:
    """Upsert one ``key -> shell`` entry, preserving existing entries."""
    mapping = load_image_shells(std)
    mapping[key] = shell
    registry_store.save_section(_store_path(std), _STORE_SECTION, mapping)


def image_store_key(runtime, image: str) -> str:
    """Return the store key for *image*: its local digest, else its reference.

    The digest (``sha256:...`` from ``runtime.get_local_digest``) is preferred
    because it is stable across re-tags; when unavailable (e.g. a locally-built
    image with no repo digest) the image reference string is used as a fallback
    key (accepting the minor staleness risk that a re-tag could mislead).
    """
    digest = runtime.get_local_digest(image)
    return digest or image


# ---------------------------------------------------------------------------
# getent probe (one ephemeral container)
# ---------------------------------------------------------------------------

# Read the box user's login shell from the image's passwd database.  The
# ``--entrypoint sh`` override is essential: kanibako images set an ENTRYPOINT
# (kanibako-entrypoint) that would otherwise swallow the command (same lesson
# as ``probe_missing_executables`` in diagnose.py).
_PROBE_SCRIPT = (
    'u=$(id -un); '
    'getent passwd "$u" 2>/dev/null | cut -d: -f7 '
    '|| grep "^$u:" /etc/passwd 2>/dev/null | cut -d: -f7'
)


def probe_image_user_shell(runtime, image: str) -> str | None:
    """Return the box user's login shell recorded in *image*, or ``None``.

    Runs ONE ephemeral container that reads the image's own default USER's login
    shell from getent/passwd.  Any failure (runtime error, non-zero exit with no
    output, empty/whitespace result) yields ``None``.
    """
    try:
        result = subprocess.run(
            [runtime.cmd, "run", "--rm", "--entrypoint", "sh", image, "-c", _PROBE_SCRIPT],
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    shell = (result.stdout or "").strip()
    if result.returncode != 0 and not shell:
        return None
    return shell or None


# ---------------------------------------------------------------------------
# Install-time capture (probe + persist)
# ---------------------------------------------------------------------------


def capture_image_shell(runtime, image: str, std) -> None:
    """Probe *image*'s login shell and persist it, idempotently and safely.

    Called from the command layer after a successful pull/prep so the resolver
    reads a stored value instead of probing in the hot path.  This routine MUST
    NEVER raise or meaningfully slow the install flow:

    - if the image's store key already has a recorded shell, do nothing (no
      re-probe -- the digest-keyed store yields a fresh key for a changed image);
    - probe failures (no shell, runtime error) are swallowed -- nothing stored,
      leaving the resolver's lazy backfill to cover the miss later;
    - any unexpected exception is swallowed.
    """
    try:
        key = image_store_key(runtime, image)
        if key in load_image_shells(std):
            return
        shell = probe_image_user_shell(runtime, image)
        if shell:
            save_image_shell(std, key, shell)
    except Exception:
        return


# ---------------------------------------------------------------------------
# Resolver (single source of truth)
# ---------------------------------------------------------------------------


def resolve_box_shell(config, std, *, runtime=None, image=None) -> tuple[str, str]:
    """Resolve the launch shell for a no-agent box → ``(shell, source)``.

    *source* is one of ``"box.shell"``, ``"$KANIBAKO_SHELL"``, ``"image"``,
    ``"sh"``.  Precedence (first defined wins):

    1. ``config.box_shell`` (the ``box.shell`` setting), if truthy.
    2. ``$KANIBAKO_SHELL`` host env var, if truthy.
    3. the image's recorded login shell (when *image* is given): the stored
       value if present, else — when *runtime* is given — a lazy probe whose
       result is persisted (self-healing).
    4. ``sh``.
    """
    box_shell = getattr(config, "box_shell", "") or ""
    if box_shell:
        return box_shell, "box.shell"

    env_shell = os.environ.get("KANIBAKO_SHELL")
    if env_shell:
        return env_shell, "$KANIBAKO_SHELL"

    if image is not None:
        if runtime is not None:
            key = image_store_key(runtime, image)
        else:
            key = None
        stored = load_image_shells(std)
        if key is not None and key in stored:
            return stored[key], "image"
        # No digest-keyed hit; if we couldn't compute a key (no runtime) there is
        # nothing more to read.  With a runtime, lazily probe and persist.
        if runtime is not None:
            # key was computed from this same non-None runtime above (it is
            # only None when runtime is None), so it is a str here.
            assert key is not None
            shell = probe_image_user_shell(runtime, image)
            if shell:
                save_image_shell(std, key, shell)
                return shell, "image"

    return "sh", "sh"
