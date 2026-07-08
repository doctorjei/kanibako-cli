"""Host-side attached-container config generation for ``kanibako code``.

VS Code's Dev Containers extension reads an "attached container configuration"
(a devcontainer.json subset) when you *attach to a running container*.
``kanibako code`` seeds this config so that, on attach, VS Code opens the box's
workspace folder and auto-installs the box agent's VS Code extension (e.g.
``anthropic.claude-code``, so claude's ``/ide`` integration works in-box).

Everything here is PURE + host-side: it only computes paths and merges/writes a
JSON file next to VS Code's global storage.  It changes NO box/launch behavior —
the ``code`` launcher works identically whether or not seeding succeeds
(Phase-1's zero-launch-delta discipline).

CONFIRMED (VS Code Phase-0 test) — the config is IMAGE-keyed, not name-level:

    <config_home>/Code/User/globalStorage/
        ms-vscode-remote.remote-containers/imageConfigs/<ENC>.json

where ``<ENC>`` is the image reference percent-encoded (``/`` → ``%2f``,
``:`` → ``%3a``) with LOWERCASE hex escapes — VS Code uses an
``encodeURIComponent``-style encoder then lowercases, so e.g.
``ghcr.io/doctorjei/kanibako-oci:latest`` → file
``ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest.json`` (see :func:`_encode_image_ref`;
Python's :func:`urllib.parse.quote` emits UPPERCASE escapes, so we lowercase them).

Because the file is IMAGE-shared (one file per image, used by every box on that
image) and VS Code OWNS it — it reads AND accumulates ``extensions`` as the user
installs more — the seed is a read-modify-write UNION-MERGE, never a clobbering
create-if-absent: we add this box's agent extension iff absent, set
``workspaceFolder`` only if absent, and preserve every other key/extension the
file already holds.  The schema VS Code itself writes is exactly ``extensions``
(a JSON array) and ``workspaceFolder`` — it does NOT write ``remoteUser`` (it
infers the container user), so we omit it to match VS Code's schema exactly.
Our ``kanibako code`` launcher passes an explicit ``--folder-uri`` anyway, so
``workspaceFolder`` is only a fallback default.
"""

from __future__ import annotations

import copy
import json
import urllib.parse
from pathlib import Path

# The VS Code Dev Containers global-storage sub-path (relative to the user
# config home, e.g. ``~/.config`` on Linux) under which per-IMAGE
# attached-container configs live.  CONFIRMED by the Phase-0 VS Code test.
_IMAGE_CONFIGS_SUBPATH = (
    "Code/User/globalStorage/ms-vscode-remote.remote-containers/imageConfigs"
)


def _encode_image_ref(ref: str) -> str:
    """Percent-encode *ref* the way VS Code keys ``imageConfigs`` files.

    VS Code encodes an image reference ``encodeURIComponent``-style and
    lowercases, so ``/`` → ``%2f`` and ``:`` → ``%3a``.  :func:`urllib.parse.quote`
    with ``safe=""`` encodes the same code points but emits UPPERCASE escapes
    (``%2F``/``%3A``), so we lowercase the whole result.

    CONFIRMED byte-exact for the canonical lowercase ref
    ``ghcr.io/doctorjei/kanibako-oci:latest`` →
    ``ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest``.  OCI image NAMES are already
    lowercase per spec, so only a tag's case can differ; for an uppercase-tag ref
    we ASSUME VS Code does ``encodeURIComponent(x).toLowerCase()`` (whole-string
    lowercase) — PENDING a Phase-0 uppercase-tag confirm.  Whole-string lowercase
    is the simpler/more-common encoder idiom and is identical to escape-only
    lowercasing for any all-lowercase ref, so it's the safer default.
    """
    return urllib.parse.quote(ref, safe="").lower()


def attached_container_config_path(image_ref: str, config_home: Path) -> Path:
    """Return the host path VS Code reads the IMAGE-keyed attached config from.

    *config_home* is the user config home (``xdg("XDG_CONFIG_HOME", ".config")``);
    *image_ref* is the box's image reference (e.g.
    ``ghcr.io/doctorjei/kanibako-oci:latest``).  The file is
    ``<config_home>/Code/User/globalStorage/
    ms-vscode-remote.remote-containers/imageConfigs/<ENC>.json`` where ``<ENC>``
    is :func:`_encode_image_ref` of the reference.
    """
    return config_home / _IMAGE_CONFIGS_SUBPATH / f"{_encode_image_ref(image_ref)}.json"


def merge_attached_container_config(
    existing: dict,
    *,
    workspace_folder: str,
    extension: str | None,
) -> dict:
    """UNION-MERGE the box's config into *existing*, returning a NEW dict.

    Pure + deterministic; *existing* is never mutated (deep-copied first):

    * ``extensions`` — when *extension* is not ``None``, add it to the array iff
      not already present (set-union: dedup, preserve existing order, NEVER
      remove).  When *extension* is ``None`` the extensions are left untouched
      (no key created if absent).
    * ``workspaceFolder`` — set to *workspace_folder* only if the key is ABSENT;
      an existing value (VS Code's or the user's) is never clobbered.
    * every OTHER key and every OTHER extension already present is preserved.

    ``remoteUser`` is intentionally NOT added — VS Code omits it and infers the
    container user; we match its schema exactly.
    """
    merged = copy.deepcopy(existing)

    if extension is not None:
        current = merged.get("extensions")
        exts = list(current) if isinstance(current, list) else []
        if extension not in exts:
            exts.append(extension)
        merged["extensions"] = exts

    if "workspaceFolder" not in merged:
        merged["workspaceFolder"] = workspace_folder

    return merged


def _read_existing_config(path: Path) -> dict:
    """Read *path* as a JSON object, tolerating absence AND corruption.

    Returns the parsed dict, or ``{}`` if the file is absent, unreadable, not
    valid JSON, or not a JSON object.  NEVER raises — VS Code owns this file and
    may have written anything into it.
    """
    try:
        raw = path.read_text()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def seed_attached_container_config(
    path: Path,
    *,
    workspace_folder: str,
    extension: str | None,
) -> bool:
    """Read-modify-write UNION-MERGE of the box's config into *path*.

    Reads the existing ``imageConfigs/<ENC>.json`` tolerantly (absent/corrupt →
    ``{}``), computes the :func:`merge_attached_container_config` result, and
    writes it back as pretty JSON (``indent=2``), creating parent dirs.  If the
    merge produces no change versus what is on disk, the write is skipped
    (idempotent).

    Returns ``True`` iff it wrote the file, ``False`` if nothing changed.  NEVER
    removes existing extensions or clobbers an existing ``workspaceFolder``.
    """
    existing = _read_existing_config(path)
    merged = merge_attached_container_config(
        existing, workspace_folder=workspace_folder, extension=extension,
    )
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Non-atomic write is acceptable here: VS Code re-reads this file on each
    # attach, and the seed is idempotent, so a torn write self-heals next run.
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


# ---------------------------------------------------------------------------
# Ph4b Vector A: in-box claude settings.json bypassPermissions delivery.
#
# kanibako's per-agent ``auto_approve`` reaches the CLI claude via the
# ``--dangerously-skip-permissions`` flag, but NOT the VS Code claude-code
# EXTENSION panel (the default `kanibako code` UX), which reads the box's
# in-box ``~/.claude/settings.json``.  This file is PER-BOX (the box home mount),
# so it is the correctly-scoped place to reflect the box's configured yolo.
#
# SYMMETRIC: the managed ``permissions.defaultMode`` is DRIVEN by the box's
# resolved claude ``auto_approve`` — SET to ``"bypassPermissions"`` when yolo is
# ON, and CLEARED when yolo is OFF (so toggling off actually takes effect in the
# panel).  Both directions merge, never clobber: the clear removes ONLY the exact
# value we manage, leaving a user-chosen mode (``plan``/``default``/
# ``acceptEdits``), sibling ``permissions.allow``/``deny``, and every other
# top-level key intact.
# ---------------------------------------------------------------------------

# The exact ``permissions.defaultMode`` value kanibako owns for the panel-yolo
# delivery.  We SET it on and CLEAR it (only when unchanged from this) off — a
# user's own mode is never touched.
_MANAGED_MODE = "bypassPermissions"


def merge_bypass_permissions(settings: dict) -> dict:
    """UNION-MERGE ``permissions.defaultMode = "bypassPermissions"`` into a claude
    ``settings.json`` dict, returning a NEW dict (input never mutated).

    * ``permissions`` — created if absent; if present, its OTHER sub-keys
      (``allow``/``deny``/…) are preserved.  ``defaultMode`` is SET to
      ``"bypassPermissions"`` (the yolo delivery target for the panel).
    * every OTHER top-level key (``$schema``, ``includeCoAuthoredBy``, …) is
      preserved untouched.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    perms = dict(current) if isinstance(current, dict) else {}
    perms["defaultMode"] = _MANAGED_MODE
    merged["permissions"] = perms
    return merged


def clear_bypass_permissions(settings: dict) -> dict:
    """Return a NEW dict with kanibako's MANAGED ``permissions.defaultMode`` removed.

    The OFF-direction of the symmetric delivery.  Pure; input never mutated:

    * ``permissions.defaultMode`` is removed ONLY when it equals the value we
      manage (``"bypassPermissions"``).  A user-chosen mode (``plan``/``default``/
      ``acceptEdits``) is left intact, and an absent ``permissions``/``defaultMode``
      is a no-op.
    * sibling ``permissions`` sub-keys (``allow``/``deny``/…) and every other
      top-level key are preserved.
    * if removing ``defaultMode`` empties the ``permissions`` object, the now-stray
      ``permissions`` key is dropped (we are the only one who created it); a
      ``permissions`` block that still has other keys is KEPT.
    """
    merged = copy.deepcopy(settings)
    current = merged.get("permissions")
    if not isinstance(current, dict) or current.get("defaultMode") != _MANAGED_MODE:
        return merged
    perms = dict(current)
    del perms["defaultMode"]
    if perms:
        merged["permissions"] = perms
    else:
        del merged["permissions"]
    return merged


def _write_if_changed(path: Path, existing: dict, merged: dict) -> bool:
    """Write *merged* to *path* as pretty JSON iff it differs from disk.

    Returns ``True`` iff a write occurred (idempotent), ``False`` when the merge
    is a no-op versus what is already on disk.
    """
    if path.exists() and merged == existing:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n")
    return True


def seed_claude_bypass_permissions(settings_path: Path) -> bool:
    """Read-modify-write: SET ``permissions.defaultMode=bypassPermissions`` into the
    box's in-box claude ``~/.claude/settings.json`` (host path *settings_path*).

    Reads the existing file tolerantly (absent/corrupt → ``{}``), merges via
    :func:`merge_bypass_permissions`, and writes it back as pretty JSON (creating
    parent dirs) iff it changed (idempotent).  Returns ``True`` iff it wrote the
    file.  The ON-direction of :func:`deliver_claude_panel_permissions`.
    """
    existing = _read_existing_config(settings_path)
    return _write_if_changed(
        settings_path, existing, merge_bypass_permissions(existing),
    )


def clear_claude_bypass_permissions(settings_path: Path) -> bool:
    """Read-modify-write: CLEAR our managed ``permissions.defaultMode`` from the
    box's in-box claude ``~/.claude/settings.json`` (host path *settings_path*).

    No-ops when the file is ABSENT (nothing to clear — never creates it).  Reads
    tolerantly, applies :func:`clear_bypass_permissions`, and writes back iff it
    changed (idempotent).  Returns ``True`` iff it wrote the file.  The
    OFF-direction of :func:`deliver_claude_panel_permissions`.
    """
    if not settings_path.exists():
        return False
    existing = _read_existing_config(settings_path)
    return _write_if_changed(
        settings_path, existing, clear_bypass_permissions(existing),
    )


def deliver_claude_panel_permissions(
    *, auto_approve: bool, is_claude: bool, claude_config_dir: Path,
) -> bool:
    """GATE + deliver the SYMMETRIC Vector A yolo state for the VS Code panel.

    The single mutation-provable gate for the launch-side Vector A delivery,
    driven by the box's resolved claude ``auto_approve``:

    * non-claude box → inert, does NOTHING (returns ``False``).
    * claude + ``auto_approve`` ON → SET ``permissions.defaultMode=bypassPermissions``
      in ``<claude_config_dir>/settings.json``.
    * claude + ``auto_approve`` OFF → CLEAR that managed value (no-op if the file
      is absent) so toggling yolo off takes effect in the panel.

    Both directions merge (never clobber a user's own settings) and are
    idempotent.  Returns whether a write occurred.  Callers wrap this best-effort
    so a failure never blocks the launch.
    """
    if not is_claude:
        return False
    settings_path = claude_config_dir / "settings.json"
    if auto_approve:
        return seed_claude_bypass_permissions(settings_path)
    return clear_claude_bypass_permissions(settings_path)
