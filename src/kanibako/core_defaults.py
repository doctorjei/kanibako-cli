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

import hashlib
import importlib.resources
import re
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths


def packaged_data_dir(*parts: str) -> Traversable:
    """Resolve a path inside the packaged ``kanibako.data`` tree.

    Single source of truth for ``importlib.resources.files("kanibako.data")``
    joined with ``*parts`` — returns the same ``Traversable`` the inline
    ``files("kanibako.data").joinpath(*parts)`` expression produced (callers wrap
    it in ``Path(str(...))`` as before).  Notably it centralizes the rom-bundle
    subpath literal ``("global", "rom", "playbook", "kanibako")`` that was
    resolved verbatim in both this module and :mod:`kanibako.templates`.
    """
    return importlib.resources.files("kanibako.data").joinpath(*parts)

# Filename of the shipped system/core defaults (in kanibako.data).
CORE_DEFAULTS_FILENAME = "core-defaults.yaml"

# The set-time FLOOR-registry placeholder host_src (F10). ``core_default_bind_keys``
# / ``agent_representation.agent_default_bind_keys`` emit their bind tuples with THIS
# sentinel in element 0 because the set-time repoint DISCARDS the old host_src
# (``settings_configset.repoint_host_src`` uses only ``base[1:]`` = box_dest+options).
# It is NEVER a launch value — the registry is folded ONLY into the set-time
# ``_category_set_lookups`` floor, never into the launch ``build_launch_snapshot``
# (which uses the real, host-probed ``core_default_categories`` /
# ``agent_default_partial``). A plain literal (not an ``@``-ref) so a stray lenient
# expand of a non-edited key never records a spurious dangling-ref defect.
FLOOR_PLACEHOLDER_SRC = "__floor_placeholder__"


def _load_doc() -> dict[str, Any]:
    """Read and parse the bundled system/core defaults file."""
    ref = packaged_data_dir(CORE_DEFAULTS_FILENAME)
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
    std: StandardPaths, proj: ProjectPaths, *, enable_vault: bool, mode: str
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
        # An entry routed through an @-ref carries either a single ``meta_ref``
        # (MODE-INDEPENDENT — home and workspace) OR a ``mode_meta_ref`` PER-MODE
        # map.  The per-mode form now serves the VAULT binds ONLY, and for a real
        # reason: primary/named take the per-box ``/@meta.box.name`` subdir that a
        # lone standalone box does not have (spec §2c).  Both vault arms root at the
        # SAME ``@workset.vault_*`` anchor.  home no longer needs an arm at all — it
        # roots at ``@meta.box.path``, which is where the per-mode variation lives.
        # The host_src is the @-ref STRING, which ``expand`` resolves to the SAME
        # runtime-probed literal (byte-identical) because the workset.* /
        # meta.workset.path anchors resolve to the launch's own roots.  Falls back to
        # the probed source for an un-routed entry.
        mode_ref = entry.get("mode_meta_ref")
        if mode_ref is not None:
            host_src = mode_ref[mode]
        else:
            host_src = entry.get("meta_ref", sources[entry["source"]])
        binds[f"box.{category}.{entry['key']}"] = (
            host_src,
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds


def core_default_bind_keys() -> dict[str, tuple[str, str, str]]:
    """The CORE box bind KEYS as a context-light set-time floor registry (F10).

    Mirrors :func:`core_default_categories`'s KEY set — ``box.bindings.{ro,rw}.
    <key>`` for home + workspace + vault (ro and rw) — with the STATIC ``box_dest``
    + per-entry ``options`` read straight from the same declarative ``core:`` doc,
    but with a PLACEHOLDER host_src (:data:`FLOOR_PLACEHOLDER_SRC`) in element 0.

    HOST-FREE (the F10 de-risk): it takes NO :class:`~kanibako.paths.ProjectPaths`
    / :class:`~kanibako.paths.StandardPaths` and does NO runtime probe or
    create-if-missing — the ONLY thing the set-time must-exist gate needs from the
    floor is ``base[1:]`` (box_dest + options), which are pure declarative literals
    (``settings_configset.repoint_host_src`` discards element 0). So this exposes
    EXACTLY the launch core-floor keys to ``config set`` so a source-only repoint of
    a core bind (``box config set box.bindings.rw.home /new``) is no longer refused
    as "nowhere in the cascade".

    Vault keys are ALWAYS emitted (both ``ro`` and ``rw``), regardless of whether
    vault would be ENABLED at launch: the gate is about the KEY existing in the
    set-time cascade, not the runtime host value. box_dest/options are byte-identical
    to the launch builder (same file, same fields); host_src is the discarded
    placeholder.
    """
    binds: dict[str, tuple[str, str, str]] = {}
    for entry in _load_doc().get("core", []):
        key = f"box.{entry['category']}.{entry['key']}"
        binds[key] = (
            FLOOR_PLACEHOLDER_SRC,
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
    secrets_ref = importlib.resources.files("kanibako.scripts").joinpath(
        "kanibako-secrets.sh"
    )
    secrets_path = Path(str(secrets_ref))

    sources: dict[str, str] = {
        "kani_pkg": str(pkg_dir),
        "kani_bin": str(entry_path),
        "secret_export": str(secrets_path),
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


# The packaged rom root — the READ-ONLY built-in tree whose every shipped file is
# bind-mounted live (ro) at its mirrored ``~`` path.  A module constant (symmetric
# with :func:`templates._packaged_base_template`'s hardcoded ``("global","template")``
# writable-seed root): rom is the RO-bind DUAL of that writable template seed.
ROM_ROOT_PARTS = ("global", "rom")

# The load-bearing box guide, as its rom-ROOT-relative posix path.  The enumeration
# MUST include this file whenever the rom root is populated (fail-closed guard) —
# a box launched without the guide is a silent degradation of EVERY box.
ROM_GUIDE_REL = "playbook/kanibako/directives/KANIBAKO.md"


def rom_default_categories() -> dict[str, tuple[str, str, str]]:
    """Build the per-FILE read-only rom binds as ``default_categories``.

    Enumerates every shipped file under the packaged rom root
    (:data:`ROM_ROOT_PARTS`) and emits ONE ``box.bindings.ro.<key>`` bind per file
    that mounts it READ-ONLY at its mirrored guest ``~`` path.  This GENERALIZES the
    retired single-special-case ``playbook_kanibako`` whole-dir RO bind: instead of a
    hand-maintained ``core-defaults.yaml`` entry per rom subtree, ANY file dropped
    anywhere under ``rom/`` is bound at ``~/<rom-relative path>`` by this loader —
    the RO-bind dual of the per-file ``template/`` writable seed (both co-populate
    ``~`` with zero directory collision because only the exact mirror FILES are
    bound, leaving containing dirs — ``~/playbook``, ``~/playbook/kanibako`` — as
    ordinary WRITABLE mountpoints).

    Granularity is per LEAF FILE, never per directory: a ``rom/playbook ->
    ~/playbook`` RO bind would make the template-seeded writable ``~/playbook``
    read-only (a fatal shadow), so every emitted dest is a FILE.

    Key = a deterministic ``rom_<slug>_<hash>`` where ``<slug>`` slugifies the
    rom-relative posix path (every non-alphanumeric char → ``_``) and ``<hash>`` is
    the first 6 hex of the sha256 of that rel path (slug-collision safety, e.g.
    ``a/b.md`` vs ``a/b_md``).  The enumeration is SORTED so the key set is identical
    across machines.

    FAIL-CLOSED guide guard: if the rom root exists and is non-empty but the
    enumeration does NOT include the load-bearing guide (:data:`ROM_GUIDE_REL`), this
    RAISES rather than launch a box missing the guide (guards the empty-glob /
    wrong-root / over-broad-filter failure class).  An empty/absent rom root yields
    an empty dict (a no-rom install) — that is fine.

    DISJOINTNESS guard: the set of rom ``~``-relative file paths must NOT intersect
    the ``template/`` writable-seed ``~``-relative file paths (computed via the SAME
    walk).  An overlap is a content-design bug where an RO bind would silently shadow
    a writable seed → RAISE.
    """
    from kanibako import templates

    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    if not rom_root.is_dir():
        return {}

    rom_files = templates.walk_shipped_files(rom_root)
    rom_rels = {rel for rel, _ in rom_files}

    # FAIL-CLOSED: the guide SHIPS under the rom root, so if that file is
    # physically present on disk it MUST appear in the enumeration.  Anchoring the
    # guard to the guide's on-disk presence (NOT to a non-empty filtered list)
    # catches the over-broad-filter / empty-glob / broken-walk class where the walk
    # silently returns nothing while the guide still ships — that must RAISE, never
    # short-circuit to a guide-less launch (MEMORY: "check the file COUNT, never
    # just rc").
    guide_shipped = (rom_root / ROM_GUIDE_REL).is_file()
    if guide_shipped and ROM_GUIDE_REL not in rom_rels:
        raise RuntimeError(
            "rom RO-bind enumeration is missing the load-bearing box guide "
            f"{ROM_GUIDE_REL!r} (the guide file ships under rom root {rom_root} but "
            f"the enumeration produced {sorted(rom_rels)}); refusing to launch a "
            "box without the guide."
        )

    # A genuinely empty rom root (the guide is not shipped here) is a no-rom
    # install — emit nothing.  Reached only when the guide is NOT on disk (the
    # fail-closed guard above already raised if it was).
    if not rom_files:
        return {}

    # DISJOINTNESS: no rom RO file may collide with a template writable-seed file
    # (same ~-relative path) — that would silently shadow the writable seed.
    template_root = templates._packaged_base_template()
    if template_root is not None:
        template_rels = {rel for rel, _ in templates.walk_shipped_files(template_root)}
        overlap = rom_rels & template_rels
        if overlap:
            raise RuntimeError(
                "rom RO binds collide with template writable seeds at "
                f"{sorted(overlap)} (an RO bind would shadow the writable seed); "
                "a rom file and a template file must never map to the same ~ path."
            )

    binds: dict[str, tuple[str, str, str]] = {}
    for rel, path in rom_files:
        slug = re.sub(r"[^A-Za-z0-9]", "_", rel)
        digest = hashlib.sha256(rel.encode("utf-8")).hexdigest()[:6]
        key = f"rom_{slug}_{digest}"
        binds[f"box.bindings.ro.{key}"] = (str(path), f"~/{rel}", "ro")
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
        # B2b: helper_log routes through @meta.box.helper_log (the materialized
        # resolved log path) — byte-identical to the probed ``src_path`` because the
        # anchor holds ``str(helper_log_path(std, proj))``.  helper_sock is NOT
        # routed: its host path is the LENGTH-BOUNDED (hashable) socket name
        # ``bounded_socket_name(<box>-<ws>, run_dir)``, which the spec form
        # ``@system.runtime/<box>-<ws>.sock`` cannot reproduce when the name is
        # hashed for the AF_UNIX sun_path limit (JC-B2b-3) — so it keeps its probed
        # literal host_src (the ``.exists()`` gate above is unchanged either way).
        host_src = entry.get("meta_ref", str(src_path))
        binds[f"box.{category}.{entry['key']}"] = (
            host_src,
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
