"""System/core category defaults — thin reader of the shipped declarative file.

The STATIC, non-agent-specific launch-path defaults (the ``box.masks`` vault
default and the per-mode channel bind table) live as declarative data in
:mod:`kanibako.data` (``core-defaults.yaml``), mirroring how the image baseline
ships (:mod:`kanibako.baseline`) and how containerfiles/templates ship via
:mod:`importlib.resources`.  This module reads that file and emits the entries
through the existing category seam so the box-launch path injects them as the
AGENT-level ``default_categories`` exactly as the old in-code emitters did.

Split (documented in the YAML header too):

* STATIC — box-side destinations + the structural shape (which keys exist, their
  per-mode scope).  These are read straight from the file.
* DYNAMIC — host SOURCES that are runtime-PROBED (the channel host roots come
  from :class:`~kanibako.paths.StandardPaths` /
  :func:`kanibako.channels.box_channel_addresses`).  The loader injects each
  probed source into its keyed entry at the seam; the file names the source
  SYMBOLICALLY so the structure stays declarative.
* CONDITIONAL — the workset-local channel binds are emitted only for
  PRIMARY/NAMED boxes; the loader applies that gate (standalone has no workset
  channel paths) at the injection site.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths

# Filename of the shipped system/core defaults (in kanibako.data).
CORE_DEFAULTS_FILENAME = "core-defaults.yaml"


def _load_doc() -> dict[str, Any]:
    """Read and parse the bundled system/core defaults file."""
    ref = importlib.resources.files("kanibako.data").joinpath(CORE_DEFAULTS_FILENAME)
    raw = yaml.safe_load(Path(str(ref)).read_text()) or {}
    if not isinstance(raw, dict):
        return {}
    return raw


def vault_mask_default() -> list[str]:
    """Return the default ``box.masks`` list (the unconditional vault tmpfs mask).

    Per spec §2a ``masks`` is a real ``list[box_dest]`` (NOT a comma-string), so
    the default is a LIST the resolver iterates as real entries.  The default
    (no extra masks) yields ``["~/workspace/vault"]`` → ``@``-expands to
    ``/home/agent/workspace/vault`` so the local vault is hidden behind a
    read-only tmpfs in every box mode (decision B).
    """
    masks = _load_doc().get("masks", [])
    return [str(m) for m in masks]


def channel_default_categories(
    std: StandardPaths, proj: ProjectPaths
) -> dict[str, tuple[str, str]]:
    """Build the per-mode channel bind table as ``default_categories`` (§2c/§2f).

    Maps ``box.bindings.rw.<key>`` → a STRUCTURED ``(host_src, box_dest)`` pair
    for every channel surfaced into THIS box.  The box-side destinations and the
    structure come from the declarative file; the host SOURCES are runtime-probed
    here and injected into each keyed entry (the file names them symbolically).

    Per spec §2a a binding value is a STRUCTURED PAIR (a YAML list / Python
    tuple), NOT a colon-joined string — so no escaping of a literal ``:`` in the
    host path is needed; :func:`~kanibako.settings_resolve.unpack_bind` consumes
    the pair directly.

    ALL MODES (system scope): the five system channel type roots
    (commons/chat/share/mailboxes) plus this box's own inbox double-bind (the SAME
    host source bound at both ``~/channels/inbox`` and
    ``~/channels/mailboxes/<ws>/<self>`` — A2).  PRIMARY + NAMED additionally get
    the three workset-local type roots under ``~/channels/workset/``; STANDALONE
    OMITS them (A10 — gated by the absence of workset channel paths).
    """
    from kanibako import channels as _ch

    addr = _ch.box_channel_addresses(proj, std)
    wch = _ch.workset_channel_paths(proj, std)

    # Resolve each SYMBOLIC source name from the declarative file to its
    # runtime-probed host path.  Workset sources are present only when this box
    # has workset-local channels (PRIMARY/NAMED) — the entries that reference
    # them are dropped for standalone boxes.
    sources: dict[str, str] = {
        "channels_commons": str(std.channels_commons),
        "channels_chat": str(std.channels_chat),
        "channels_share": str(std.channels_share),
        "channels_mailboxes": str(std.channels_mailboxes),
        "inbox": str(addr.inbox),
    }
    if wch is not None:
        sources["workset_commons"] = str(wch.commons)
        sources["workset_chat"] = str(wch.chat)
        sources["workset_share"] = str(wch.share)

    binds: dict[str, tuple[str, str]] = {}
    for entry in _load_doc().get("channels", []):
        source = entry["source"]
        if source not in sources:
            # Workset-scoped entry on a standalone box: no host source → omit.
            continue
        binds[f"box.bindings.rw.{entry['key']}"] = (
            sources[source],
            str(entry["box_dest"]),
        )
    return binds


def core_default_categories(
    std: StandardPaths, proj: ProjectPaths, *, enable_vault: bool
) -> dict[str, tuple[str, str, str]]:
    """Build the core box mounts as ``default_categories`` (step 3).

    Maps ``box.bindings.{ro,rw}.<key>`` → a STRUCTURED 3-TUPLE
    ``(host_src, box_dest, options)`` for every CORE box mount (home + workspace +
    vault).  These are the box's own home/workspace/vault binds — TODAY's hardwired
    podman ``-v`` routed through the category resolver so nothing is bound into a
    box except through the keyspace.  The box-side destinations, per-entry mount
    options, and category come from the declarative file (``core:`` list); the host
    SOURCES are runtime-probed from *proj* here and injected into each keyed entry.

    Per spec §2a a binding value is a STRUCTURED TUPLE (a YAML list / Python
    tuple), NOT a colon-joined string — the per-entry mount OPTIONS are its
    OPTIONAL 3rd slot, consumed by :func:`~kanibako.settings_resolve.unpack_bind`
    (a 3-element value OVERRIDES the category default for that entry, so e.g. the
    ``ro`` vault bind keeps ``ro`` and the ``Z,U`` binds keep ``Z,U`` regardless of
    the category's own default).

    home + workspace are UNCONDITIONAL (every box mode).  The vault binds
    (``scope: vault`` in the file) are CONDITIONAL — emitted only when *enable_vault*
    AND the probed source dir exists, reproducing TODAY's
    ``if enable_vault and path.is_dir()`` skip-if-missing behavior EXACTLY (the
    resolver's L7 mkdir's rw SOURCES unconditionally, so blindly injecting a vault
    key would start CREATING the source dir — the gate is applied HERE so a missing
    vault source is simply omitted).
    """
    # Resolve each SYMBOLIC source name from the declarative file to its
    # runtime-probed host path off ``ProjectPaths``.
    sources: dict[str, str] = {
        "shell_path": str(proj.shell_path),
        "project_path": str(proj.project_path),
        "vault_ro_path": str(proj.vault_ro_path),
        "vault_rw_path": str(proj.vault_rw_path),
    }
    vault_dir = {
        "vault_ro_path": proj.vault_ro_path,
        "vault_rw_path": proj.vault_rw_path,
    }

    binds: dict[str, tuple[str, str, str]] = {}
    for entry in _load_doc().get("core", []):
        # Vault binds are gated: emit only when vault is enabled AND the probed
        # source dir exists (today's skip-if-missing behavior).
        if entry.get("scope") == "vault":
            src_path = vault_dir.get(entry["source"])
            if not enable_vault or src_path is None or not src_path.is_dir():
                continue
        category = entry["category"]
        binds[f"box.{category}.{entry['key']}"] = (
            sources[entry["source"]],
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds
