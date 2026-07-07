"""Host-side attached-container config generation for ``kanibako code``.

VS Code's Dev Containers extension reads a per-container "attached container
configuration" (a devcontainer.json subset) when you *attach to a running
container*.  ``kanibako code`` seeds this config for a box so that, on attach,
VS Code opens the box's workspace folder and auto-installs the box agent's VS
Code extension (e.g. ``anthropic.claude-code``, so claude's ``/ide`` integration
works in-box).

Everything here is PURE + host-side: it only computes paths and writes a JSON
file next to VS Code's global storage.  It changes NO box/launch behavior — the
``code`` launcher works identically whether or not seeding succeeds (Phase-1's
zero-launch-delta discipline).

⚑ PENDING Phase-0 confirm: the EXACT host path VS Code reads a NAME-level
attached-container config from — the ``Code/User/globalStorage/...`` prefix on
Linux and ``nameConfigs`` vs ``imageConfigs`` — is a documented best-guess until
validated against a real VS Code install (see the VS Code integration DESIGN's
Phase-0 gate).  It is a single constant (:func:`attached_container_config_path`)
and trivially swappable.  We target the NAME level (per-box) deliberately: the
agent extension is per-AGENT-in-box, and one image (kanibako-oci) can run
different agents across boxes, so an IMAGE-level config would be wrong.
"""

from __future__ import annotations

import json
from pathlib import Path

# The VS Code Dev Containers global-storage sub-path (relative to the user
# config home, e.g. ``~/.config`` on Linux) under which per-container
# ("name-level") attached-container configs live.  See the module docstring for
# the Phase-0-confirm caveat.
_NAME_CONFIGS_SUBPATH = (
    "Code/User/globalStorage/ms-vscode-remote.remote-containers/nameConfigs"
)


def build_attached_container_config(
    *,
    workspace_folder: str,
    remote_user: str,
    extensions: list[str],
) -> dict:
    """Build the devcontainer.json-subset dict VS Code reads on attach.

    Returns a MINIMAL, DETERMINISTIC mapping with a stable key order:

    * ``workspaceFolder`` — the in-box folder VS Code opens on attach;
    * ``remoteUser`` — the in-box user the VS Code Server runs as;
    * ``extensions`` — Marketplace extension ids auto-installed into the box.

    All three keys are ALWAYS included (even when *extensions* is empty) so the
    generated file is stable and self-documenting regardless of the box agent —
    an empty ``extensions`` list is a valid, meaningful "no editor extension".
    """
    return {
        "workspaceFolder": workspace_folder,
        "remoteUser": remote_user,
        "extensions": list(extensions),
    }


def attached_container_config_path(container_name: str, config_home: Path) -> Path:
    """Return the host path VS Code reads a NAME-level attached config from.

    *config_home* is the user config home (``xdg("XDG_CONFIG_HOME", ".config")``);
    *container_name* is the box's container name (``kanibako-<hash|name>``).  The
    file is ``<config_home>/Code/User/globalStorage/
    ms-vscode-remote.remote-containers/nameConfigs/<container_name>.json``.

    NAME-level (per-box) is deliberate — see the module docstring.  The exact
    prefix is a Phase-0-confirm best-guess.
    """
    return config_home / _NAME_CONFIGS_SUBPATH / f"{container_name}.json"


def seed_attached_container_config(path: Path, config: dict) -> bool:
    """Create-if-absent write of *config* (pretty JSON) to *path*.

    Mirrors the KANIBAKO.md install discipline: write ONCE and never clobber a
    file the user may have hand-edited.  Creates parent directories as needed.

    Returns ``True`` iff it wrote the file (it was absent); ``False`` when the
    file already exists (left untouched).
    """
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True
