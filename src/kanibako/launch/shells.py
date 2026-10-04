"""Launch-shell resolution for no-agent boxes.

``resolve_box_shell`` is the single source of truth; launch and diagnose both
call it.  ⚑ ``box.shell`` is the LOGIN SHELL only — the shell-VARIANT selector
it once doubled as was dropped.  Design notes:
``llm-docs/kanibako/launch/shells.py.md``.
"""

from __future__ import annotations

import shlex
import subprocess

from kanibako.errors import KanibakoError
from kanibako.project import registry_store

# ---------------------------------------------------------------------------
# Image-shell store: the ``image_shells`` section of system.registry
# (``{data_path}/global/registry.yaml``), mapping store key -> login shell.
# This module owns the section's shape; ``registry_store`` preserves every
# sibling section.
# ---------------------------------------------------------------------------

_STORE_SECTION = "image_shells"


def _store_path(std):
    # The resolved ``config.registry`` — a repointed one is honored.
    return std.registry


def load_image_shells(std) -> dict[str, str]:
    """Return ``{store_key: shell}``; a missing/empty/malformed store yields ``{}``.

    Defensive by contract: a corrupt store must never crash the launch/diagnose
    path — the shell falls back to ``sh``.
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

    The digest is preferred because it is stable across re-tags; the reference
    fallback (a locally-built image has no repo digest) accepts the minor
    staleness risk that a re-tag could mislead.
    """
    digest = runtime.get_local_digest(image)
    return digest or image


# ---------------------------------------------------------------------------
# getent probe (one ephemeral container)
# ---------------------------------------------------------------------------

# ⚑ The ``--entrypoint sh`` override below is essential: kanibako images set an
# ENTRYPOINT (kanibako-entrypoint) that would otherwise swallow the command
# (same lesson as ``probe_missing_executables`` in diagnose.py).
_PROBE_SCRIPT = (
    'u=$(id -un); '
    'getent passwd "$u" 2>/dev/null | cut -d: -f7 '
    '|| grep "^$u:" /etc/passwd 2>/dev/null | cut -d: -f7'
)


def probe_image_user_shell(runtime, image: str) -> str | None:
    """Return the box user's login shell recorded in *image*, or ``None`` on any failure.

    Runs ONE ephemeral container.  A non-zero exit that still printed a shell is
    ACCEPTED — the script's ``||`` fallback can succeed while the pipeline's status does not.
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

    Called after a successful pull/prep so the resolver reads a stored value
    instead of probing in the hot path.  MUST NEVER raise or meaningfully slow
    the install flow: an already-recorded key is left alone (no re-probe — the
    digest-keyed store yields a fresh key for a changed image), and every
    failure is swallowed, leaving the resolver's lazy backfill to cover the miss
    later.
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

    Precedence, first defined wins: ``box.shell`` → the image's recorded login
    shell (stored, else lazily probed and persisted when *runtime* is given) →
    ``sh``.
    """
    box_shell = getattr(config, "box_shell", None) or ""
    if box_shell:
        return box_shell, "box.shell"

    if image is not None:
        if runtime is not None:
            key = image_store_key(runtime, image)
        else:
            key = None
        stored = load_image_shells(std)
        if key is not None and key in stored:
            return stored[key], "image"
        # No stored hit.  Without a runtime there is no key and nothing more to read.
        if runtime is not None:
            # key is only None when runtime is None, which this branch excludes.
            assert key is not None
            shell = probe_image_user_shell(runtime, image)
            if shell:
                save_image_shell(std, key, shell)
                return shell, "image"

    return "sh", "sh"


# ---------------------------------------------------------------------------
# The ``-- <words>`` command string (keyspec §2d ``shell``)
# ---------------------------------------------------------------------------

# Linux MAX_ARG_STRLEN less the NUL: the most one program argument can hold.
ARG_STRLEN_LIMIT = 131071

# The most a persistent launch hands tmux as its session command.  Measured
# ceiling 16364 bytes on tmux 3.5a; the rest is a 64-byte margin.
TMUX_COMMAND_BUDGET = 16300


def shell_command_string(words: list[str]) -> str | None:
    """None for no words; one word verbatim; several joined as ``shlex.join`` does.

    Raises ``KanibakoError`` when the string exceeds ``ARG_STRLEN_LIMIT`` bytes.
    Only a join of several words can: each word arrived through ``execve``.
    """
    if not words:
        return None
    command = words[0] if len(words) == 1 else shlex.join(words)
    size = len(command.encode())
    if size > ARG_STRLEN_LIMIT:
        raise KanibakoError(
            f"The command after '--' is {size} bytes once its words are joined; "
            f"one program argument can hold at most {ARG_STRLEN_LIMIT} bytes "
            f"(Linux MAX_ARG_STRLEN). Pass it as one quoted word, or put it in a "
            f"workspace script and run that."
        )
    return command


def tmux_command_bytes(args: list[str]) -> int:
    """Bytes tmux holds for the session command *args*: each argument plus its NUL."""
    return sum(len(a.encode()) + 1 for a in args)
