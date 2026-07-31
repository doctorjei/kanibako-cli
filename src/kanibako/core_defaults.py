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
from collections.abc import Iterable
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths
    from kanibako.targets.base import Target


def packaged_data_dir(*parts: str) -> Traversable:
    """Resolve a path inside the packaged ``kanibako.data`` tree.

    Single source of truth for ``importlib.resources.files("kanibako.data")``
    joined with ``*parts`` — returns the same ``Traversable`` the inline
    ``files("kanibako.data").joinpath(*parts)`` expression produced (callers wrap
    it in ``Path(str(...))`` as before).  Notably it centralizes the rom-root
    subpath literal :data:`ROM_ROOT_PARTS` (``("global", "rom")``) that is
    resolved in both this module and :mod:`kanibako.templates`.
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
    (common/chat/share/mailboxes) plus this box's own inbox double-bind (the SAME
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
        "channels_common": str(std.channels_common),
        "channels_chat": str(std.channels_chat),
        "channels_share": str(std.channels_share),
        "channels_mailboxes": str(std.channels_mailboxes),
        "inbox": str(addr.inbox),
    }
    if wch is not None:
        sources["workset_common"] = str(wch.common)
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
    std: StandardPaths, proj: ProjectPaths, *, enable_vault: bool, mode: str,
    guarantee_create: bool = True,
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
            #
            # ⚑ *guarantee_create* False suppresses ONLY this mkdir — the bind is
            # still emitted with the same host_src, so a read-only consumer sees
            # exactly what a launch would mount without making it so. It exists
            # because ``box config show --effective`` resolves this same table:
            # a DISPLAY verb must not write to disk.
            if guarantee_create:
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


# The packaged rom root — the READ-ONLY built-in CANON content (the BIBLE, plus the
# COLLECTION.md index that enters it).  A module constant (symmetric with
# :func:`templates._packaged_base_template`'s hardcoded ``("global","template")``
# writable-seed root): rom is the RO-bind DUAL of that writable template seed.
ROM_ROOT_PARTS = ("global", "rom")

# The rom-ROOT-relative posix paths of the two CANON bind SOURCES (spec §2c).  The
# packaged tree MIRRORS the guest layout, so each rel path is also its ``~/``-dest.
ROM_COLLECTION_REL = "canon/COLLECTION.md"
ROM_BIBLE_REL = "canon/bible"

# The load-bearing box guide (the bible's GENERAL chapter), rom-root-relative.  It
# MUST ship whenever the rom root is populated (fail-closed guard) — a box launched
# without the guide is a silent degradation of EVERY box.
ROM_GUIDE_REL = "canon/bible/general/directives/ROM_GENERAL.md"

# ⚑ The plugin chapter's MOUNTPOINT, which core's packaged rom must ship as a REAL
# directory.  A nested bind's mountpoint has to exist inside its PARENT's SOURCE —
# and when it does not, podman does NOT error: it silently ``mkdir``s the missing
# mountpoint INTO the parent bind's host source, which here is the packaged tree in
# site-packages (bifrost experiment, podman 5.4.2/crun 1.21, 2026-07-31).  The
# 0-byte ``agent/directives/ROM_AGENT.md`` placeholder is what makes this directory
# exist in git AND in the wheel; it is load-bearing, not decoration.
ROM_AGENT_CHAPTER_REL = "canon/bible/agent"

# The plugin-rom EMISSION GATE marker, relative to a plugin's ``data/rom`` chapter
# root: a plugin gets a bible chapter bind ONLY if it actually ships one.
PLUGIN_CHAPTER_MARKER_REL = "directives/ROM_AGENT.md"


def assert_canon_bind_seed_disjoint(
    bind_dests: Iterable[str], seed_rels: Iterable[str],
) -> None:
    """RAISE if any template SEED lands at or under a ``~/canon`` BIND dest.

    Both arguments are ``~``-RELATIVE posix paths (``canon/bible``,
    ``playbook/CONTENTS.md``, …): *bind_dests* are the canon binds' guest dests,
    *seed_rels* the files a seed layer would copy to the box home.

    ⚑ SCOPE OF WHAT IS ACTUALLY CHECKED TODAY. The only caller
    (:func:`rom_default_categories`) passes the PACKAGED BASE template layer's walk
    — i.e. layer 1 of the three in :func:`kanibako.templates.template_seed_defaults`.
    The AGENT and WORKSET layers (``@agent.<a>.template`` / ``@workset.template``,
    both user-repointable and both resolved at seed time, not here) are NOT covered,
    and neither are a plugin's ``default_seeds()``. This function does not decide
    that scope, it only enforces what it is handed — WIDENING THE INPUTS IS THE
    CALLER'S JOB, which is exactly why the bind dests and seed rels are parameters
    rather than computed inside. The seeds/handbook sub-phase extends BOTH sides:
    it appends ``canon/handbook`` (+ its three chapter dests) to *bind_dests* and,
    where it can resolve them, the remaining seed layers to *seed_rels*.

    PREFIX CONTAINMENT, not set intersection.  A whole-directory bind shadows a
    whole SUBTREE, so a seed does not have to hit the bind's exact path to be
    swallowed — ``canon/bible/general/x.md`` is just as invisible under the
    ``canon/bible`` bind as ``canon/bible`` itself would be.  Spec §0's
    copy-vs-mount rule makes that shadowing ORDER-INDEPENDENT and SILENT: the
    seeded bytes are neither merged nor reported, they simply never appear.  Hence
    a guard rather than a runtime resolution.

    THE SHARED ENTRY POINT between the two C-CANON halves (brief §4): the ROM half
    supplies ``{canon/COLLECTION.md, canon/bible}``; the SEEDS/handbook half extends
    it, with no edit to the rom emitter.  Spec §2c states the rule this enforces:
    *"a template MUST NOT seed into ``canon/COLLECTION.md``, ``canon/bible/…`` or
    ``canon/handbook/…``; seeds target ``canon/{notebook,workbook}`` ONLY."*
    """
    dests = sorted(set(bind_dests))
    violations: list[str] = []
    for rel in sorted(set(seed_rels)):
        for dest in dests:
            if rel == dest or rel.startswith(f"{dest}/"):
                violations.append(f"{rel!r} is at/under the canon bind dest {dest!r}")
    if violations:
        raise RuntimeError(
            "template seed collides with a canon RO bind (the mount SHADOWS the "
            "copied file at the same path regardless of order, so the seeded "
            "content would be silently invisible — never merged, never an error):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\nSeeds target canon/{notebook,workbook} ONLY (spec §2c)."
        )


def rom_default_categories() -> dict[str, tuple[str, str, str]]:
    """Build the TWO read-only CANON binds as ``default_categories`` (spec §2c).

    ::

        box.bindings.ro.canon_collection = (<rom>/canon/COLLECTION.md, ~/canon/COLLECTION.md, ro)
        box.bindings.ro.canon_bible      = (<rom>/canon/bible,         ~/canon/bible,         ro)

    Both are INTERNAL/generated binds, not user keys: they are absent from every
    set-time floor registry (:func:`core_default_bind_keys` covers home/workspace/
    vault only), so ``config set`` refuses them exactly as it does ``kani_pkg`` and
    ``images_conf``.  Spec §0's test — *"could a user reasonably want to override
    it?"* — answers itself here: the one book a user cannot edit is also the one
    they cannot repoint, and ``COLLECTION.md`` is the INDEX that defines the canon's
    shape and load order, so a repointable index would mean no guaranteed structure.

    ⚑ WHOLE-DIR, replacing the retired per-LEAF-FILE enumeration (and its
    ``rom_<slug>_<hash>`` keys).  That enumeration existed for ONE reason — rom used
    to land inside the template-seeded WRITABLE ``~/playbook``, where a directory
    bind would have turned the user's own tree read-only.  ``~/canon/bible`` is a
    dedicated root with no writable co-tenant, so the constraint is gone.
    ``COLLECTION.md`` stays a FILE bind because ``~/canon`` ITSELF must remain
    writable for the SEEDED ``notebook/`` + ``workbook/`` books.

    FAIL-CLOSED guards (a mis-pathed or half-shipped canon must RAISE, never
    silently launch a box with no directives):

    * the guide is physically on disk but absent from the shipped-file walk → the
      over-broad-filter / empty-glob / broken-walk class;
    * the rom root is POPULATED but any of ``COLLECTION.md`` / the guide /
      ``bible/`` / the ``bible/agent/`` mountpoint placeholder is missing.

    An absent or genuinely EMPTY rom root yields an empty dict — a no-rom install,
    which is fine.

    DISJOINTNESS: delegated to :func:`assert_canon_bind_seed_disjoint` (prefix
    containment against the template seed tree) — the shared entry point the
    seeds/handbook sub-phase extends.
    """
    from kanibako import templates

    rom_root = Path(str(packaged_data_dir(*ROM_ROOT_PARTS)))
    if not rom_root.is_dir():
        return {}

    rom_files = templates.walk_shipped_files(rom_root)
    rom_rels = {rel for rel, _ in rom_files}

    # FAIL-CLOSED (a): the guide SHIPS under the rom root, so if that file is
    # physically present on disk it MUST appear in the shipped-file walk.  Anchoring
    # the guard to the guide's on-disk presence (NOT to a non-empty filtered list)
    # catches the over-broad-filter / empty-glob / broken-walk class where the walk
    # silently returns nothing while the guide still ships — that must RAISE, never
    # short-circuit to a guide-less launch (MEMORY: "check the file COUNT, never
    # just rc").
    guide_shipped = (rom_root / ROM_GUIDE_REL).is_file()
    if guide_shipped and ROM_GUIDE_REL not in rom_rels:
        raise RuntimeError(
            "rom shipped-file walk is missing the load-bearing box guide "
            f"{ROM_GUIDE_REL!r} (the guide file ships under rom root {rom_root} but "
            f"the walk produced {sorted(rom_rels)}); refusing to launch a box "
            "without the guide."
        )

    # A genuinely empty rom root (the guide is not shipped here either) is a no-rom
    # install — emit nothing.  Reached only when the guide is NOT on disk (the
    # fail-closed guard above already raised if it was).
    if not rom_files:
        return {}

    # FAIL-CLOSED (b): the rom root is POPULATED, so the WHOLE canon payload must be
    # there.  Under a whole-dir bind an empty/half-shipped ``bible/`` no longer
    # raises anything by itself (there is no per-file enumeration left to come up
    # short), so each required member is checked explicitly.
    required: list[tuple[str, bool]] = [
        (ROM_COLLECTION_REL, (rom_root / ROM_COLLECTION_REL).is_file()),
        (ROM_GUIDE_REL, guide_shipped),
        (ROM_BIBLE_REL, (rom_root / ROM_BIBLE_REL).is_dir()),
        (ROM_AGENT_CHAPTER_REL, (rom_root / ROM_AGENT_CHAPTER_REL).is_dir()),
    ]
    missing = [rel for rel, present in required if not present]
    if missing:
        raise RuntimeError(
            f"the packaged canon under rom root {rom_root} is incomplete — missing "
            f"{missing}. The rom root is populated, so this is a PACKAGING defect, "
            "not a no-rom install; refusing to launch a box with a partial canon. "
            f"(NOTE {ROM_AGENT_CHAPTER_REL!r} must exist as a real DIRECTORY: it is "
            "the plugin chapter's nested mountpoint, and podman silently mkdirs a "
            "missing one into this packaged tree.)"
        )

    # DISJOINTNESS: no template seed may land at or under a canon bind dest.
    template_root = templates._packaged_base_template()
    if template_root is not None:
        assert_canon_bind_seed_disjoint(
            (ROM_COLLECTION_REL, ROM_BIBLE_REL),
            (rel for rel, _ in templates.walk_shipped_files(template_root)),
        )

    return {
        "box.bindings.ro.canon_collection": (
            str(rom_root / ROM_COLLECTION_REL), f"~/{ROM_COLLECTION_REL}", "ro",
        ),
        "box.bindings.ro.canon_bible": (
            str(rom_root / ROM_BIBLE_REL), f"~/{ROM_BIBLE_REL}", "ro",
        ),
    }


def rom_agent_default_categories(
    target: "Target",
) -> dict[str, tuple[str, str, str]]:
    """Build the PLUGIN's bible chapter bind — the SEVENTH canon bind (spec §2c).

    ::

        box.bindings.ro.canon_bible_agent = (<plugin pkg>/data/rom, ~/canon/bible/agent, ro)

    Emitted by CORE from the RESOLVED *target*, beside the two core canon binds —
    NOT by the plugin, and NOT through the agent-scope descriptor route.  That
    choice is the whole design: an ``agent.<node>.bindings.ro.rom`` key would ride
    ``agent_default_bind_keys`` into the set-time floor and make the bible's agent
    chapter the SOLE repointable page of an otherwise unrepointable book, and it
    would discriminate on the NODE (a persona) while the content is a property of
    the HARNESS PACKAGE.  As a box-scoped INTERNAL bind there is no discriminator
    at all, which is spec §2d's *"storage is varied, binding is not"* verbatim.

    ⚑ bible/agent = per-HARNESS (packaged, one per plugin).  handbook/agent =
    per-AGENT-NODE (host, ``agent.<agent>.canon``, personas included).  A persona
    has no package, so it has no bible chapter; what it can have is a handbook
    chapter.  Two books, two cardinalities, no overlap.

    NESTING is by DESIGN, not a collision: this dest sits INSIDE ``canon_bible``'s,
    so the plugin's chapter SHADOWS core's placeholder one (whole-directory
    shadowing, never a merge — spec §2c).  The existing ASCENDING mount depth-sort
    in :func:`~kanibako.settings_categories.reconcile_categories` already lands the
    deeper dest last, and the collision table explicitly blesses nested-but-
    different dests.

    GATE — emit ONLY when the plugin actually ships a chapter (``rom_root`` exists
    AND contains ``directives/ROM_AGENT.md``).  A plugin shipping a bare/empty
    ``data/rom/`` would otherwise SHADOW core's placeholder chapter with nothing,
    turning ``ROM_CONTENTS.md``'s ``@agent/directives/ROM_AGENT.md`` into a dangling
    import.  (Belt: ``_emit_category_mounts`` drops a ro bind with a missing source
    anyway — but with a per-launch WARNING, which is the wrong signal for the
    perfectly ordinary "this plugin has no chapter".)
    """
    rom_root = target.rom_root()
    if rom_root is None:
        return {}
    if not (rom_root / PLUGIN_CHAPTER_MARKER_REL).is_file():
        return {}
    return {
        "box.bindings.ro.canon_bible_agent": (
            str(rom_root), f"~/{ROM_AGENT_CHAPTER_REL}", "ro",
        ),
    }


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
        # B2b: helper_log routes through the spec's own formula
        # ``@workset.logs/@{meta.box.name}.jsonl`` (§2c) — byte-identical to the
        # probed ``src_path`` in all three modes, since ``workset.logs`` and
        # ``meta.box.name`` resolve to exactly what ``helper_log_path(std, proj)``
        # builds (gated by a before/after comparison of the resolved bind, PHASE R).
        # ⚑ The ``.exists()`` gate above keys off the PROBED path while the emitted
        # host_src is the FORMULA, so a user repointing ``workset.logs`` moves the
        # MOUNT but not the hub's WRITER — see migration M-14.  helper_sock is NOT
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
