"""System/core category defaults — thin reader of the shipped declarative file.

The STATIC, non-agent-specific launch-path defaults (the ``box.masks`` default,
now empty, and the per-mode channel bind table) live as declarative data in
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
    """Return the default ``box.masks`` list — now EMPTY (no default mask).

    Per spec §2a ``masks`` is a real ``list[box_dest]`` (NOT a comma-string), so
    the default is a LIST the resolver iterates as real entries.  The old
    vestigial ``~/workspace/vault`` default was DROPPED: the vault moved OUT of
    ``~/workspace`` in 1.6.0, so there is nothing in the workspace to hide behind
    a tmpfs.  The seam is kept (so a box may still declare masks via
    ``box.masks`` / ``<scope>.masks``) but the default reads as an empty list
    from the shipped file (decision B).
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
        # B2: an entry with a ``meta_ref`` is ROUTED through that @meta.* reference
        # (spec §2c) — the host_src is the @-ref STRING, which ``expand`` resolves
        # to the SAME materialized identity literal as the runtime-probed source
        # (byte-identical, JC-B2-4).  The ``source`` gate above still applies (so a
        # workset-scoped meta_ref entry on a standalone box is still omitted).
        host_src = entry.get("meta_ref", sources[source])
        binds[f"box.bindings.rw.{entry['key']}"] = (
            host_src,
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
    (``scope: vault`` in the file) are UNIVERSAL UNLESS DISABLED: emitted whenever
    *enable_vault* is true, with the probed source dir CREATED IF MISSING here so
    the bind is ALWAYS emitted (rather than silently dropped when the source
    happens to be absent).  Only an explicitly DISABLED vault (``enable_vault`` is
    false) omits the vault binds.
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
        # Vault binds are UNIVERSAL unless explicitly disabled: when vault is
        # enabled, ensure the probed source dir exists (create-if-missing) so the
        # bind is ALWAYS emitted, rather than silently dropped when the source
        # happens to be absent.  Only skip vault when it is disabled.
        if entry.get("scope") == "vault":
            if not enable_vault:
                continue
            src_path = vault_dir.get(entry["source"])
            if src_path is None:
                continue  # unknown source name (defensive)
            # Vault is UNIVERSAL unless disabled: ensure the source dir exists
            # (create-if-missing) so the bind is always emitted when enabled,
            # rather than silently dropped when the source happens to be absent.
            src_path.mkdir(parents=True, exist_ok=True)
        category = entry["category"]
        # B2: an entry with a ``meta_ref`` is ROUTED through that @meta.* reference
        # (spec §2c) — the host_src is the @-ref STRING, which ``expand`` resolves
        # to the SAME runtime-probed literal (byte-identical, JC-B2-4).  Falls back
        # to the probed source for an un-routed entry.
        host_src = entry.get("meta_ref", sources[entry["source"]])
        binds[f"box.{category}.{entry['key']}"] = (
            host_src,
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds


def kani_default_categories() -> dict[str, tuple[str, str, str]]:
    """Build the kanibako CLI binds as ``default_categories`` (Phase B).

    Maps ``box.bindings.ro.<key>`` → a STRUCTURED 3-TUPLE
    ``(host_src, box_dest, options)`` for the in-box kanibako package + entry
    script — TODAY's hardwired ``_kanibako_mounts`` ``-v`` list routed through the
    category resolver so nothing is bound into a box except through the keyspace.
    The box-side destinations + options come from the declarative file (``kani:``
    list); the host SOURCES are import-resolved here and injected into each keyed
    entry (the file names them SYMBOLICALLY).  Both binds are UNCONDITIONAL
    (every box mode).
    """
    import importlib.resources

    import kanibako

    pkg_dir = Path(kanibako.__file__).parent
    entry_ref = importlib.resources.files("kanibako.scripts").joinpath(
        "kanibako-entry"
    )
    entry_path = Path(str(entry_ref))

    sources: dict[str, str] = {
        "kani_pkg": str(pkg_dir),
        "kani_bin": str(entry_path),
    }

    binds: dict[str, tuple[str, str, str]] = {}
    for entry in _load_doc().get("kani", []):
        category = entry["category"]
        binds[f"box.{category}.{entry['key']}"] = (
            sources[entry["source"]],
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds


def helper_default_categories(
    *,
    box_state_kanibako: str,
    socket_path: Path,
    log_path: Path,
) -> dict[str, tuple[str, str, str]]:
    """Build the helper hub binds as ``default_categories`` (Phase B).

    Maps ``box.bindings.{rw,ro}.<key>`` → a STRUCTURED 3-TUPLE
    ``(host_src, box_dest, options)`` for the live helper unix SOCKET + the
    per-box helper message LOG — TODAY's hardwired ``_HMount`` appends inside the
    ``helpers_enabled`` block routed through the category resolver.

    Both box-side destinations are DYNAMIC: derived from the box's
    ``box_state_home(container_env)`` (passed in as *box_state_kanibako*, an
    absolute box path) — so the loader injects BOTH the probed host source AND the
    runtime-derived box destination at the seam (the file carries only the keys +
    options).  The host SOURCES (*socket_path* / *log_path*) are runtime-probed and
    GATED on ``.exists()`` here, reproducing the old skip-if-missing appends: a
    missing socket/log simply omits its key.

    ⚠ helper_sock options MUST be ``""`` (empty): it is a LIVE unix socket the hub
    listens on; a ``Z``/``U`` relabel/chown would break the shared socket topology.
    The per-entry empty-options 3rd slot carries that through ``unpack_bind``.
    """
    base = box_state_kanibako.rstrip("/")
    sources: dict[str, tuple[Path, str]] = {
        # symbolic source name -> (probed host source, dynamic box dest)
        "helper_sock": (socket_path, f"{base}/helper.sock"),
        "helper_log": (log_path, f"{base}/helpers.jsonl"),
    }

    binds: dict[str, tuple[str, str, str]] = {}
    for entry in _load_doc().get("helpers", []):
        src_path, box_dest = sources[entry["source"]]
        # Skip-if-missing gate (parity with the old `.exists()`-guarded appends).
        if not src_path.exists():
            continue
        category = entry["category"]
        binds[f"box.{category}.{entry['key']}"] = (
            str(src_path),
            box_dest,
            str(entry["options"]),
        )
    return binds


def image_default_categories(
    *,
    graph_root: Path,
    storage_conf_path: Path,
) -> dict[str, tuple[str, str, str]]:
    """Build the image-sharing binds as ``default_categories`` (Phase B, D-M8).

    Maps ``box.bindings.ro.<key>`` → a STRUCTURED 3-TUPLE
    ``(host_src, box_dest, options)`` for the host image graph root + the GENERATED
    ``storage.conf`` — TODAY's hardwired Mounts from
    :func:`kanibako.image_sharing.build_image_sharing_mounts` routed through the
    category resolver.  The box-side destinations + options come from the
    declarative file (``images:`` list); the host SOURCES (the runtime-probed
    *graph_root* and the already-GENERATED *storage_conf_path*) are injected here.

    The caller applies the CONDITIONAL gate (only when image-sharing is requested
    AND the host graph root is detectable) before invoking this, so every entry is
    emitted unconditionally once called.
    """
    sources: dict[str, str] = {
        "images_store": str(graph_root),
        "images_conf": str(storage_conf_path),
    }

    binds: dict[str, tuple[str, str, str]] = {}
    for entry in _load_doc().get("images", []):
        category = entry["category"]
        binds[f"box.{category}.{entry['key']}"] = (
            sources[entry["source"]],
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds
