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
