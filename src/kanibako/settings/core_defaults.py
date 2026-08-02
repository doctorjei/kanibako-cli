"""System/core category defaults — thin reader of the shipped declarative file.

The STATIC, non-agent-specific launch-path defaults (the ``box.masks`` default,
now empty, and the per-mode channel bind table) live as declarative data in
:mod:`kanibako.data` (``core-defaults.yaml``), mirroring how the image baseline
ships (:mod:`kanibako.runtime.baseline`) and how containerfiles/templates ship via
:mod:`importlib.resources`.  This module reads that file and emits the entries
through the existing category seam so the box-launch path injects them as the
AGENT-level ``default_categories`` exactly as the old in-code emitters did.

Split (documented in the YAML header too):

* STATIC — box-side destinations + the structural shape (which keys exist, their
  per-mode scope).  These are read straight from the file.
* DYNAMIC — host SOURCES that are runtime-PROBED (the channel host roots come
  from :class:`~kanibako.settings.paths.StandardPaths` /
  :func:`kanibako.channels.channels.box_channel_addresses`).  The loader injects each
  probed source into its keyed entry at the seam; the file names the source
  SYMBOLICALLY so the structure stays declarative.
* CONDITIONAL — the workset-local channel binds are emitted only for
  PRIMARY/NAMED boxes; the loader applies that gate (standalone has no workset
  channel paths) at the injection site.
"""

from __future__ import annotations

import importlib.resources
import logging
from collections.abc import Iterable
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from kanibako.settings.paths import ProjectPaths, StandardPaths
    from kanibako.targets.base import PluginDescriptor, Target


def packaged_data_dir(*parts: str) -> Traversable:
    """Resolve a path inside the packaged ``kanibako.data`` tree.

    Single source of truth for ``importlib.resources.files("kanibako.data")``
    joined with ``*parts`` — returns the same ``Traversable`` the inline
    ``files("kanibako.data").joinpath(*parts)`` expression produced (callers wrap
    it in ``Path(str(...))`` as before).  Notably it centralizes the rom-root
    subpath literal :data:`ROM_ROOT_PARTS` (``("global", "rom")``) that is
    resolved in both this module and :mod:`kanibako.launch.templates`.
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
    host path is needed; :func:`~kanibako.settings.settings_resolve.unpack_bind` consumes
    the pair directly.

    ALL MODES (system scope): the five system channel type roots
    (common/chat/share/mailboxes) plus this box's own inbox double-bind (the SAME
    host source bound at both ``~/channels/inbox`` and
    ``~/channels/mailboxes/<ws>/<self>`` — A2).  PRIMARY + NAMED additionally get
    the three workset-local type roots under ``~/channels/workset/``; STANDALONE
    OMITS them (A10 — gated by the absence of workset channel paths).
    """
    from kanibako.channels import channels as _ch

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
    OPTIONAL 3rd slot, consumed by :func:`~kanibako.settings.settings_resolve.unpack_bind`
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
            # because ``box show --effective`` resolves this same table:
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

    HOST-FREE (the F10 de-risk): it takes NO :class:`~kanibako.settings.paths.ProjectPaths`
    / :class:`~kanibako.settings.paths.StandardPaths` and does NO runtime probe or
    create-if-missing — the ONLY thing the set-time must-exist gate needs from the
    floor is ``base[1:]`` (box_dest + options), which are pure declarative literals
    (``settings_configset.repoint_host_src`` discards element 0). So this exposes
    EXACTLY the launch core-floor keys to ``config set`` so a source-only repoint of
    a core bind (``box set box.bindings.rw.home /new``) is no longer refused
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


# ===========================================================================
# The KICKOFF LOADER — the directive-chain ENTRY SLOT (spec §2c, P-5).
# ===========================================================================

# The packaged kickoff loader, relative to the ``kanibako.data`` root.  It sits
# FLAT under ``global/`` beside the two other shipped content trees (``global/rom``,
# the RO canon; ``global/template``, the writable seed) because it is neither of
# them: it is not canon TEXT (it never lands under ~/canon and the box never reads it
# as a directive — the flattener CONSUMES it) and it is not seeded (it is bound RO so
# it version-follows the package instead of freezing at create).
KICKOFF_PACKAGED_PARTS = ("global", "KICKOFF.md")


def _kickoff_entry() -> dict[str, Any]:
    """The one declarative ``kickoff:`` entry, or RAISE if the shipped file lost it.

    The block is a LIST like every other family in the file (``kani``/``helpers``/
    ``images``) and carries EXACTLY ONE entry: there is one directive-chain entry
    slot, and two would mean two files racing for one dest.  Enforced rather than
    assumed — a second entry would otherwise be silently ignored here while the
    ``KANIBAKO_DIRECTIVE_SEED`` env var kept naming the first.
    """
    entries = _load_doc().get("kickoff") or []
    if not isinstance(entries, list) or len(entries) != 1:
        raise RuntimeError(
            f"{CORE_DEFAULTS_FILENAME} must declare EXACTLY ONE 'kickoff:' entry "
            f"(got {entries!r}) — the directive-chain entry slot (spec §2c "
            "box.bindings.ro.kickoff) is declared there and read back by both the "
            "bind emitter and the KANIBAKO_DIRECTIVE_SEED env var."
        )
    return entries[0]


def kickoff_box_dest() -> str:
    """The ``~``-spelled box-side kickoff slot (``~/.config/kanibako/kickoff.md``).

    SINGLE SOURCE OF TRUTH, read from the declarative file: the bind's dest, the
    ``KANIBAKO_DIRECTIVE_SEED`` container env var and the transition gate's dest
    comparison are all the SAME path, and a second literal spelling of it anywhere
    is a drift waiting to happen.
    """
    return str(_kickoff_entry()["box_dest"])


def kickoff_guest_dest() -> str:
    """:func:`kickoff_box_dest` as an ABSOLUTE guest path.

    Two consumers need the expanded form rather than the ``~`` spelling: the
    ``KANIBAKO_DIRECTIVE_SEED`` env var (it is read by a hook shell and at exec
    time, where ``~`` would resolve against whoever's HOME) and the transition
    gate (descriptor box_dests are ``$GUEST_HOME``-expanded by the defaults
    loader, so a ``~``-spelled dest would never match one).
    """
    from kanibako.settings.settings_resolve import GUEST_HOME

    dest = kickoff_box_dest()
    return GUEST_HOME + dest[1:] if dest.startswith("~") else dest


def kickoff_default_categories(
    descriptor: "PluginDescriptor | None" = None,
) -> dict[str, tuple[str, str, str]]:
    """Build the core KICKOFF bind as ``default_categories`` (spec §2c, P-5).

    ::

        box.bindings.ro.kickoff = (<packaged global/KICKOFF.md>, ~/.config/kanibako/kickoff.md, ro)

    The directive-chain ENTRY SLOT: the flattener reads this file at box start,
    follows its ``@~/canon/COLLECTION.md`` import to full depth, and writes the flat
    result into the harness's native instruction slot.  INTERNAL, like ``kani_pkg``
    and ``images_conf``: absent from every set-time floor registry, so ``config set``
    refuses it; not repointable (spec §0's test — a user has nothing to configure
    here, the file is generated content at a fixed location, and repointing it would
    redirect the entire directive chain).

    ⚑⚑ THE TRANSITION GATE — core YIELDS to a plugin that still ships a kickoff.

    P-5 moved the kickoff CONTENT from the three agent plugins into core.  The
    plugins publish INDEPENDENTLY of the base and pin no base version, so a NEW base
    beside an OLD plugin is not just reachable, it is the ordinary
    ``pip install -U kanibako-cli`` outcome — and both would deliver a file to the
    SAME dest: core's bind here plus the plugin's descriptor binding (``managed_pointer``
    in all three first-party plugins).  Two CONCRETE bindings at one box_dest is a
    row-1 collision in spec §0's identical-dest table, i.e. a HARD LAUNCH ERROR
    (``CategoryCollisionError``).  Refusing to launch a box because its base got newer
    is not an acceptable upgrade experience, so core defers: with a plugin-supplied
    kickoff present, this emitter yields NOTHING and the plugin's file is delivered
    exactly as before.

    ⚑ REMOVAL CONDITION — delete the gate (and this paragraph) once every published
    agent plugin has dropped its own kickoff delivery: ``data/KICKOFF.md`` +
    the ``managed_pointer`` descriptor binding gone from ``kanibako-agent-claude``,
    ``-codex`` and ``-goose``, with those releases PUBLISHED (not merely merged).
    After that the gate can only ever be false, and an ungated unconditional bind is
    the honest shape.  Until then the gate is what keeps the base and the plugins
    independently upgradable.  Recorded in migration M-12.

    *descriptor* is the descriptor whose bindings the SAME resolve represents in the
    launch snapshot (see :func:`kanibako.settings.agent_representation.agent_default_partial`).
    Passing ``None`` (a no-agent box, or a narrow resolve that carries no agent
    bindings) means nothing else can be delivering a kickoff, so the bind is emitted.

    FAIL-CLOSED: the packaged loader must exist.  A box whose kickoff is missing has
    NO directive chain at all — the flattener finds no source, the launch shim's
    ``|| true`` swallows it, and every session runs with an empty instruction set.
    That is precisely the silent degradation the canon work exists to end, so a
    missing packaged file RAISES here rather than emitting a bind whose source
    ``_emit_category_mounts`` would drop with a one-line warning.
    """
    from kanibako.targets.assembly import declares_box_dest

    if declares_box_dest(descriptor, kickoff_guest_dest()):
        return {}

    entry = _kickoff_entry()
    # The host SOURCE, resolved from the entry's SYMBOLIC name exactly as
    # ``kani_default_categories`` resolves its own (the file names sources
    # symbolically; the loader supplies the resolved path).
    sources = {"kickoff": Path(str(packaged_data_dir(*KICKOFF_PACKAGED_PARTS)))}
    src = sources[str(entry["source"])]
    if not src.is_file():
        raise RuntimeError(
            f"the packaged kickoff loader is missing at {src} — it is the entry "
            "point of the whole directive chain (spec §2c box.bindings.ro.kickoff), "
            "so a box launched without it would run with NO directives at all. This "
            "is a PACKAGING defect; refusing to launch."
        )
    return {
        f"box.{entry['category']}.{entry['key']}": (
            str(src),
            str(entry["box_dest"]),
            str(entry["options"]),
        ),
    }


# The packaged rom root — the READ-ONLY built-in CANON content (the BIBLE, plus the
# COLLECTION.md index that enters it).  A module constant (symmetric with
# :func:`templates._packaged_base_template`'s hardcoded ``("global","template")``
# writable-seed root): rom is the RO-bind DUAL of that writable template seed.
ROM_ROOT_PARTS = ("global", "rom")

# ⚑ The packaged rom tree is FLAT — ``rom/{COLLECTION.md, bible/**}``, with NO
# ``canon/`` wrapper level (J-7, 2026-07-31).  It therefore NO LONGER mirrors the
# guest layout, so a rom-relative path is NOT its own ``~/``-dest: every guest dest
# is built by :func:`_canon_dest`, which prefixes the guest canon root.
CANON_GUEST_ROOT = "canon"

# The rom-ROOT-relative posix paths of the packaged CANON bind SOURCES (spec §2c).
ROM_COLLECTION_REL = "COLLECTION.md"
ROM_BIBLE_REL = "bible"
ROM_CONTENTS_REL = f"{ROM_BIBLE_REL}/ROM_CONTENTS.md"

# The handbook BOOK root, guest-only (nothing packages a handbook).  Declared here
# beside ``ROM_BIBLE_REL`` for symmetry and because the managed-region deny list
# below needs both book roots.
HANDBOOK_REL = "handbook"

# The load-bearing box guide (the bible's GENERAL chapter), rom-root-relative.  It
# MUST ship whenever the rom root is populated (fail-closed guard) — a box launched
# without the guide is a silent degradation of EVERY box.
ROM_GUIDE_REL = "bible/general/directives/ROM_GENERAL.md"

# The bible chapters core PACKAGES, one whole-directory sibling bind each.  ⚑ There
# is deliberately no ``agent`` here: J-7 retired the packaged placeholder chapter
# along with the nested-bind model that needed it (a wheel cannot ship an empty
# directory, and a mountpoint must never live inside a bind SOURCE).
ROM_BIBLE_CHAPTERS = ("general", "workset", "box")

# The bible's PLUGIN chapter.  Guest-only: it is a mountpoint the box-create
# skeleton materialises in the box home, never a packaged directory.
BIBLE_AGENT_CHAPTER = "agent"

# The plugin-rom EMISSION GATE marker, relative to a plugin's ``data/rom`` chapter
# root: a plugin gets a bible chapter bind ONLY if it actually ships one.
PLUGIN_CHAPTER_MARKER_REL = "directives/ROM_AGENT.md"

# ⚑ The MANAGED CANON REGION that no template seed may write into (spec §2c: *"a
# template MUST NOT seed into canon/COLLECTION.md, canon/bible/… or
# canon/handbook/…; seeds target canon/{notebook,workbook} ONLY"*), as ``~``-relative
# prefixes.
#
# ⚑ WHY PREFIXES AND NOT THE LITERAL BIND DESTS.  Under J-7's SIBLING binds
# ``canon/bible`` is no longer itself a bind dest — only its chapters are — so
# passing the literal dests would silently stop rejecting a seed at
# ``canon/bible/agent/x.md``.  That seed is still forbidden, and under J-7 doubly
# so: the whole region is root-owned, so the copy would fail with EACCES at create
# rather than merely be shadowed at launch.  The deny list therefore names the
# managed REGION, which is what §2c actually states.
#
# ⚑ ``canon/handbook`` IS ALREADY IN THE LIST, even though nothing BINDS it until the
# seeds half lands.  The trigger is not "is it bound?" but "does the box-create
# SKELETON own it?" — and it does: ``materialize_canon_skeleton`` creates
# ``canon/handbook/`` + its four chapter mountpoints + ``SYS_CONTENTS.md`` and makes
# them root-owned TODAY.  A template seeding there would fail with EACCES right now,
# so deferring the deny entry to R2 would leave a real hole open in between.
CANON_SEED_DENY_PREFIXES = (
    f"{CANON_GUEST_ROOT}/COLLECTION.md",
    f"{CANON_GUEST_ROOT}/{ROM_BIBLE_REL}",
    f"{CANON_GUEST_ROOT}/{HANDBOOK_REL}",
)


def _canon_dest(rel: str) -> str:
    """Return the ``~``-relative guest dest for a canon path *rel*.

    *rel* is spelled relative to the BOOK ROOT (``COLLECTION.md``, ``bible/general``,
    …), which is the rom-root-relative spelling for packaged sources and the
    home-relative-under-``canon`` spelling for everything else.
    """
    return f"~/{CANON_GUEST_ROOT}/{rel}"


def assert_canon_bind_seed_disjoint(
    bind_dests: Iterable[str], seed_rels: Iterable[str],
) -> None:
    """RAISE if any template SEED lands at or under a MANAGED ``~/canon`` path.

    Both arguments are ``~``-RELATIVE posix paths (``canon/bible``,
    ``canon/notebook/MY_CONTENTS.md``, …): *bind_dests* are the managed canon prefixes
    (:data:`CANON_SEED_DENY_PREFIXES` — the BOOK ROOTS, which under J-7's sibling
    binds are a superset of the literal bind dests; see that constant for why),
    *seed_rels* the files a seed layer would copy to the box home.

    ⚑ SCOPE OF WHAT IS ACTUALLY CHECKED TODAY. The only caller
    (:func:`rom_default_categories`) passes the PACKAGED BOX-HOME template walk
    (``template/box/home``) — i.e. layer 1 of the three in
    :func:`kanibako.launch.templates.template_seed_defaults`. The AGENT and WORKSET layers
    (``@agent.<a>.template`` / ``@workset.template``, both user-repointable and both
    resolved at seed time, not here) are NOT covered, and neither are a plugin's
    ``default_seeds()``. This function does not decide that scope, it only enforces
    what it is handed — WIDENING THE INPUTS IS THE CALLER'S JOB, which is exactly why
    the bind dests and seed rels are parameters rather than computed inside.

    ⚑⚑ BOTH SIDES MUST BE HOME-RELATIVE, and that is easy to break silently. The
    ``~``-relative bind prefixes (``canon/bible``, ``canon/handbook``) can only be
    compared against seed rels spelled the same way. Before the canon restructure the
    packaged template ROOT happened to BE the home-relative root, so passing its walk
    worked by coincidence; it no longer is (``box/home/...``), and passing the root
    walk today would make every comparison a guaranteed miss — a guard that runs,
    passes, and checks nothing. Hence :func:`kanibako.launch.templates.
    packaged_box_home_template`, which names the level that IS home-relative.

    PREFIX CONTAINMENT, not set intersection.  A whole-directory bind shadows a
    whole SUBTREE, so a seed does not have to hit the bind's exact path to be
    swallowed — ``canon/bible/general/x.md`` is just as invisible under the
    ``canon/bible`` bind as ``canon/bible`` itself would be.  Spec §0's
    copy-vs-mount rule makes that shadowing ORDER-INDEPENDENT and SILENT: the
    seeded bytes are neither merged nor reported, they simply never appear.  Hence
    a guard rather than a runtime resolution.

    THE SHARED ENTRY POINT between the two C-CANON halves (brief §4): the ROM half
    supplies :data:`CANON_SEED_DENY_PREFIXES`; the SEEDS/handbook half appends
    ``canon/handbook`` to it, with no edit to the rom emitter.  Spec §2c states the
    rule this enforces: *"a template MUST NOT seed into ``canon/COLLECTION.md``,
    ``canon/bible/…`` or ``canon/handbook/…``; seeds target
    ``canon/{notebook,workbook}`` ONLY."*
    """
    dests = sorted(set(bind_dests))
    violations: list[str] = []
    for rel in sorted(set(seed_rels)):
        for dest in dests:
            if rel == dest or rel.startswith(f"{dest}/"):
                violations.append(
                    f"{rel!r} is at/under the managed canon path {dest!r}"
                )
    if violations:
        raise RuntimeError(
            "template seed collides with a canon RO bind (the mount SHADOWS the "
            "copied file at the same path regardless of order, so the seeded "
            "content would be silently invisible — never merged, never an error):\n"
            + "\n".join(f"  - {v}" for v in violations)
            + "\nSeeds target canon/{notebook,workbook} ONLY (spec §2c)."
        )


def rom_default_categories() -> dict[str, tuple[str, str, str]]:
    """Build the FIVE read-only packaged-CANON binds as ``default_categories``.

    ::

        canon_collection     = (<rom>/COLLECTION.md,         ~/canon/COLLECTION.md,         ro)
        canon_bible_contents = (<rom>/bible/ROM_CONTENTS.md, ~/canon/bible/ROM_CONTENTS.md, ro)
        canon_bible_general  = (<rom>/bible/general,         ~/canon/bible/general,         ro)
        canon_bible_workset  = (<rom>/bible/workset,         ~/canon/bible/workset,         ro)
        canon_bible_box      = (<rom>/bible/box,             ~/canon/bible/box,             ro)

    (all under ``box.bindings.ro.``; the sixth canon bind, ``canon_bible_agent``, is
    the plugin's chapter — see :func:`rom_agent_default_categories`.)

    All INTERNAL/generated binds, not user keys: they are absent from every set-time
    floor registry (:func:`core_default_bind_keys` covers home/workspace/vault only),
    so ``config set`` refuses them exactly as it does ``kani_pkg`` and
    ``images_conf``.  Spec §0's test — *"could a user reasonably want to override
    it?"* — answers itself here: the one book a user cannot edit is also the one they
    cannot repoint, and ``COLLECTION.md`` is the INDEX that defines the canon's shape
    and load order, so a repointable index would mean no guaranteed structure.

    ⚑⚑ SIBLINGS, NOT A WHOLE-DIR BOOK (J-7, 2026-07-31 — REPLACES R1's single
    ``canon_bible`` directory bind, which shipped only in the unreleased ``93b9a9d``).
    Every entry is its own bind onto a mountpoint that ALREADY EXISTS in the box home,
    materialised by :func:`materialize_canon_skeleton` at box create.  Nothing nests
    inside anything, so no mountpoint ever has to live inside a bind SOURCE — which is
    what killed the whole-dir model: the plugin chapter's mountpoint would have had to
    exist inside site-packages (where a wheel cannot ship an empty directory and no
    runtime may write), and the handbook chapters' inside the user's own stores.
    The nested-mount physics PASSED on real podman; the model was retired anyway
    because of where it forced the mountpoints to live.

    ``COLLECTION.md`` and ``ROM_CONTENTS.md`` are FILE binds (file-onto-file, over the
    0-byte mountpoints the skeleton creates).  They stay BINDS rather than seeded
    copies because they are rom TRUTH: a copy would freeze at create and drift from
    the installed package.

    FAIL-CLOSED guards (a mis-pathed or half-shipped canon must RAISE, never silently
    launch a box with no directives):

    * the guide is physically on disk but absent from the shipped-file walk → the
      over-broad-filter / empty-glob / broken-walk class;
    * the rom root is POPULATED but any EMITTED BIND'S SOURCE is missing.  Now that
      every source is its own bind, "the required members" and "the emitted sources"
      are the same list — so the guard is generated from it rather than restated.

    ⚑ ``bible/agent/`` is deliberately NOT required (and must NOT ship): J-7 retired
    the packaged placeholder chapter together with the nesting that needed it.

    An absent or genuinely EMPTY rom root yields an empty dict — a no-rom install,
    which is fine.

    DISJOINTNESS: delegated to :func:`assert_canon_bind_seed_disjoint` (prefix
    containment against the template seed tree) — the shared entry point the
    seeds/handbook sub-phase extends.
    """
    from kanibako.launch import templates

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

    # The SIBLING bind set, as ``(key-leaf, rom-relative source, is_dir)``.  ⚑ ONE
    # declaration drives BOTH the fail-closed completeness guard and the emission, so
    # the guard cannot drift away from what is actually bound.
    binds: list[tuple[str, str, bool]] = [
        ("canon_collection", ROM_COLLECTION_REL, False),
        ("canon_bible_contents", ROM_CONTENTS_REL, False),
        *(
            (f"canon_bible_{chapter}", f"{ROM_BIBLE_REL}/{chapter}", True)
            for chapter in ROM_BIBLE_CHAPTERS
        ),
    ]

    # FAIL-CLOSED (b): the rom root is POPULATED, so the WHOLE canon payload must be
    # there — every emitted bind's SOURCE, plus the guide (which has no bind of its
    # own: it rides the ``general`` chapter's).  A half-shipped canon is a PACKAGING
    # defect, and a bind with a missing source would otherwise be silently DROPPED by
    # ``_emit_category_mounts`` with only a per-launch warning.
    present: list[tuple[str, bool]] = [
        (rel, (rom_root / rel).is_dir() if is_dir else (rom_root / rel).is_file())
        for _key, rel, is_dir in binds
    ]
    present.append((ROM_BIBLE_REL, (rom_root / ROM_BIBLE_REL).is_dir()))
    present.append((ROM_GUIDE_REL, guide_shipped))
    missing = [rel for rel, ok in present if not ok]
    if missing:
        raise RuntimeError(
            f"the packaged canon under rom root {rom_root} is incomplete — missing "
            f"{sorted(set(missing))}. The rom root is populated, so this is a "
            "PACKAGING defect, not a no-rom install; refusing to launch a box with a "
            "partial canon."
        )

    # DISJOINTNESS: no template seed may land at or under the MANAGED canon region.
    #
    # ⚑ RE-ANCHORED to the BOX-HOME template root (``template/box/home``), NOT the
    # template ROOT.  Both sides of this guard must be HOME-RELATIVE for the prefix
    # comparison to mean anything, and after the canon restructure a root-relative
    # walk yields ``box/home/...`` / ``workset/...`` / ``handbook/...`` — none of
    # which can ever match a ``canon/...`` prefix.  Left un-re-anchored the guard
    # would still run, still pass, and check NOTHING.
    home_template_root = templates.packaged_box_home_template()
    if home_template_root is not None:
        assert_canon_bind_seed_disjoint(
            CANON_SEED_DENY_PREFIXES,
            (rel for rel, _ in templates.walk_shipped_files(home_template_root)),
        )

    return {
        f"box.bindings.ro.{key}": (str(rom_root / rel), _canon_dest(rel), "ro")
        for key, rel, _is_dir in binds
    }


def rom_agent_default_categories(
    target: "Target",
) -> dict[str, tuple[str, str, str]]:
    """Build the PLUGIN's bible chapter bind — the SIXTH canon bind (spec §2c).

    ::

        box.bindings.ro.canon_bible_agent = (<plugin pkg>/data/rom, ~/canon/bible/agent, ro)

    Emitted by CORE from the RESOLVED *target*, beside the five core canon binds —
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

    ⚑ A SIBLING, not a nested bind (J-7, 2026-07-31 — REPLACES R1's shadow model).
    Its dest no longer sits inside another bind's: ``~/canon/bible`` is not bound at
    all, only its chapters are, and this one lands on a mountpoint the box-create
    skeleton already made.  Nothing shadows anything, so the ascending mount
    depth-sort is not load-bearing here any more.

    GATE — emit ONLY when the plugin actually ships a chapter (``rom_root`` exists
    AND contains ``directives/ROM_AGENT.md``).  With no packaged placeholder chapter
    left to shadow, an ungated bare ``data/rom/`` would bind an EMPTY directory over
    the mountpoint: visibly identical to emitting nothing, but paid for with a
    per-launch missing-source WARNING from ``_emit_category_mounts`` — the wrong
    signal for the perfectly ordinary "this plugin has no chapter".  Gate-false is
    the honest shape: an empty root-owned mountpoint plus ONE dangling-import warning
    from the flattener, which is exactly what J-7 accepts.
    """
    rom_root = target.rom_root()
    if rom_root is None:
        return {}
    if not (rom_root / PLUGIN_CHAPTER_MARKER_REL).is_file():
        return {}
    return {
        "box.bindings.ro.canon_bible_agent": (
            str(rom_root),
            _canon_dest(f"{ROM_BIBLE_REL}/{BIBLE_AGENT_CHAPTER}"),
            "ro",
        ),
    }


# ===========================================================================
# The HANDBOOK binds + the <scope>.canon keys (spec §2c/§2b/§2d/§2g).
# ===========================================================================

# The ACTIVE-AGENT placeholder in the declarative ``canon:`` rows.  The bind source
# for the agent chapter discriminates on the ACTIVE AGENT NODE, which no static file
# can spell; the loader substitutes it.  A literal that could never occur in a real
# key (``<``/``>`` are not key characters), so the substitution cannot collide.
CANON_ACTIVE_AGENT_TOKEN = "<active>"


def canon_optional_bind_keys() -> frozenset[str]:
    """The SKIP-IF-ABSENT canon bind keys, read from the same declarative rows.

    Fed to :func:`kanibako.settings.settings_launch.snapshot_category_entries` as
    ``optional_keys`` at the ONE launch aggregation site, so an absent workset/box
    chapter drops its bind SILENTLY instead of warning on every launch of almost
    every box (spec §2c "SKIP-IF-ABSENT").  Derived from the file rather than
    restated: a row that gains or loses ``optional: true`` moves both the
    declaration and this set at once.
    """
    return frozenset(
        f"box.{entry['category']}.{entry['key']}"
        for entry in _load_doc().get("canon", [])
        if entry.get("optional")
    )


def canon_default_categories(
    std: StandardPaths, agent_name: str | None,
) -> dict[str, object]:
    """Build the HANDBOOK binds + the agent-scope ``canon`` floor (spec §2c).

    Returns a MIXED table — the five ``box.bindings.ro.canon_hb_*`` bind tuples PLUS
    the agent-scope SCALAR keys their ``@``-refs resolve against.  Mixing the two is
    the established shape (:func:`kanibako.launch.templates.template_seed_defaults` does the
    same for the seed layers and their source keys): both land in the SAME snapshot
    floor, the scalar resolves the ref, and a user override of the scalar wins by
    cascade precedence and reroutes the bind.

    The other three ``<scope>.canon`` keys are floored elsewhere, each beside the
    anchor it is spelled against: ``system.canon`` in the resolved ``system.*`` tier
    (``StandardPaths.canon``), ``workset.canon`` and ``box.canon`` in
    :func:`kanibako.settings.settings_launch.workset_anchor_floor`.

    ⚑ THE REPOINT ROUTES DIFFER PER SCOPE, and the difference is inherited, not
    chosen here: ``workset.canon`` / ``box.canon`` are ordinary ``config set`` keys
    (wired like ``workset.template``), ``agent.<a>.canon`` is settable at the SYSTEM
    scope only (the per-persona agent-leaf rule ``agent.<a>.template`` already
    follows), and ``system.canon`` is CLI-REFUSED as a structural path key — it is a
    ``SYSTEM_PATH_DEFAULTS`` member, so it lives in the hand-edited ``[system]``
    table of ``kanibako_config.yaml``, exactly like ``system.template``.

    ⚑⚑ THE AGENT TIER — J-1 option (a) ("the ``agent.default`` tier must be able to
    win"), implemented against a resolver that has NO agent-tier fallback:

    * ``agent.default.canon`` is ALWAYS floored at ``@config.agents/default/canon``.
    * the ACTIVE NODE's ``agent.<node>.canon`` is floored too, but its FLOOR VALUE
      is chosen by whether that node's own store actually PROVIDES a canon dir —
      ``@config.agents/<node>/canon`` when it does, and the REF
      ``@agent.default.canon`` when it does not.  So a plugin agent whose install
      stamped its own chapter binds that chapter, and a PERSONA (no package, no
      stamped store) falls back to the DEFAULT store — which is precisely the
      beneficiary case J-1 names.
    * being a FLOOR entry, any ``agent.<node>.canon`` a plugin or the user sets in
      the cascade OVERRIDES it.

    A literal reading of option (a) — flooring ONLY ``agent.default.canon`` — is not
    implementable here: ``settings_expand._lookup_raw`` walks the merged snapshot with
    no per-tier fallback, and ``@agent.<node>.canon/handbook`` is an EMBEDDED ref, so
    an undeclared node key coerces to ``""`` (spec §6b) and yields the degenerate host
    path ``/handbook`` rather than falling back to the default tier.

    A NO-AGENT box (*agent_name* None) emits NEITHER the agent scalar NOR the
    ``canon_hb_agent`` entry — deliberately not an entry with an empty ref, which
    would produce exactly that degenerate path.
    """
    store_canon = f"@config.agents/{agent_name}/canon" if agent_name else None
    out: dict[str, object] = {}
    if agent_name:
        out["agent.default.canon"] = "@config.agents/default/canon"
        node_store = std.agents / agent_name / "canon"
        out[f"agent.{agent_name}.canon"] = (
            store_canon if node_store.is_dir() else "@agent.default.canon"
        )

    for entry in _load_doc().get("canon", []):
        ref = str(entry["meta_ref"])
        if CANON_ACTIVE_AGENT_TOKEN in ref:
            if not agent_name:
                continue  # NO-AGENT box: no agent tier at all, so no chapter bind.
            ref = ref.replace(CANON_ACTIVE_AGENT_TOKEN, agent_name)
        out[f"box.{entry['category']}.{entry['key']}"] = (
            ref,
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return out


# ===========================================================================
# The box-create CANON SKELETON (J-7).
# ===========================================================================

# The handbook's chapters.  Their BINDS are the seeds/handbook half's (they need
# ``<scope>.canon`` resolution, which does not exist yet), but their MOUNTPOINTS are
# part of this one closed skeleton: J-7 specifies the skeleton as a single set, an
# absent chapter is REQUIRED to show as an empty root-owned dir, and creating them
# later would mean mkdir-ing into an already-555 tree.
HANDBOOK_CHAPTERS = ("general", "agent", "workset", "box")
HANDBOOK_CONTENTS_REL = f"{HANDBOOK_REL}/SYS_CONTENTS.md"

# ⚑⚑ THE IMPORT-FALLBACK FILES (seeds-gate F1, 2026-08-01) — the per-scope chapters
# whose ENTRY FILE the skeleton pre-creates 0-byte, keyed chapter → entry filename.
#
# WHY THEY EXIST, and why skip-if-absent did NOT already cover it: the packaged
# ``SYS_CONTENTS.md`` imports all FOUR chapters UNCONDITIONALLY, and skip-if-absent
# governs the BIND, not the INDEX.  So on a box with no workset chapter — i.e. every
# primary box — the flattener printed ``unresolved import
# @workset/directives/SYS_WORKSET.md`` on EVERY launch.  That is the warning-noise
# failure the skip-if-absent work exists to prevent, arriving through the other door.
#
# With a 0-byte entry file already inside the mountpoint: an UNBOUND chapter
# RESOLVES-TO-EMPTY (no warning, no content), and a BOUND one has its whole directory
# replaced by the mount, so the store's real file SHADOWS the fallback. No branch, no
# gate, no second mechanism.
#
# ⚑ ``general`` is deliberately ABSENT: the system store always supplies it, so a
# fallback there would mask a genuinely missing system handbook — which is exactly
# what ``canon_hb_general`` being NON-optional exists to surface.
#
# ⚑ MACHINERY, NOT CONTENT. Jei's D2 cut stands: the global handbook STORE still
# ships ``general`` only. These files live in the BOX's skeleton, are root-owned like
# the rest of it, and are never installed anywhere.
HANDBOOK_FALLBACK_ENTRIES: tuple[tuple[str, str], ...] = (
    ("agent", "SYS_AGENT.md"),
    ("workset", "SYS_WORKSET.md"),
    ("box", "SYS_BOX.md"),
)

# The directory each chapter's entry file sits in — the ``@<chapter>/directives/...``
# spelling ``SYS_CONTENTS.md`` imports.
HANDBOOK_DIRECTIVES_DIRNAME = "directives"

# ⚑⚑ THE OWNER THAT APPEARS AS ROOT INSIDE A BOX — deliberately NOT 0.
#
# J-7's prose says ``podman unshare chown 0:0``, but 0 does not do what it reads
# like.  ``podman unshare`` enters the rootless INTERMEDIATE user namespace, whose
# mapping is ``ns-uid 0 -> the real host user`` and ``ns-uid 1.. -> the host user's
# subuid range``.  kanibako runs every box with
# ``--userns=keep-id:uid=1000,gid=1000`` (:data:`kanibako.runtime.container.KEEP_ID_USERNS`),
# under which the real host user appears IN-BOX as uid 1000 — the agent.  So
# ``chown 0:0`` inside unshare produces an AGENT-OWNED skeleton, the exact opposite
# of J-7's stated effect ("in-box: root-owned, unwritable").  Container uid 0 under
# keep-id is the host user's FIRST SUBUID, which inside ``podman unshare`` is ns-uid
# 1 — hence 1.
#
# The property J-7 actually needs is *owner != the host user*, which holds for ANY
# subuid; 0 is the one value that provably breaks it.  Named constants so a bifrost
# measurement corrects this in one place.
#
# ⚑ CAVEAT: in a DEGENERATE configuration — a host ``/etc/subuid`` range shorter than
# GUEST_UID (so keep-id cannot fill container 0..999 from subuids at all) — ns-uid 1
# may not render as container-root in-box.  The SAFETY property is unaffected: the
# owner is still a subuid and still not the host user, so the books stay unwritable
# by the agent; only the cosmetic "shows as uid 0" would differ.
UNSHARE_BOX_ROOT_UID = 1
UNSHARE_BOX_ROOT_GID = 1

# ⚑ TWO MODES, NOT ONE (spec J-7 banner, amended 2026-07-31).
#
# DIRS ``r-xr-xr-x``: unwritable by everyone, but the SEARCH bit stays set so crun's
# openat2 destination resolution can still traverse ``~/canon`` and ``~/canon/bible``
# to reach the chapter mountpoints — a 444 directory would break every canon bind.
#
# FILE mountpoints ``r--r--r--``: the search bit is meaningless on a file, and 555
# would mark a 0-byte ``.md`` executable for no reason.  Applied as a SEPARATE chmod
# call precisely because the two sets need different modes.
CANON_SKELETON_DIR_MODE = "555"
CANON_SKELETON_FILE_MODE = "444"


def canon_skeleton_rels() -> tuple[tuple[str, bool], ...]:
    """The canon skeleton as ``(home-relative posix path, is_dir)`` pairs (J-7).

    ⚑ DERIVED FROM THE SAME CONSTANTS AS THE BIND DESTS, never restated: the
    skeleton IS the mirror image of the canon binds, so one edit to
    :data:`ROM_BIBLE_CHAPTERS` / :data:`HANDBOOK_CHAPTERS` moves both sides at once.
    A hand-kept second list is exactly the duplicated-shared-data class the design
    principles forbid — and a skeleton that drifts from the binds is a mountpoint
    podman then creates itself, which is the whole failure J-7 exists to remove.

    Ordered PARENTS-FIRST so a caller can create them in sequence.

    ``canon/notebook`` and ``canon/workbook`` are ABSENT by design: they are SEEDED,
    agent-owned and writable, and become undeletable only because their parent is
    555 — which is intended, not a side effect.

    ⚑ THE CHAPTER MOUNTPOINTS ARE NOT EMPTY (F1): three of them carry a 0-byte
    IMPORT-FALLBACK entry file, so ``SYS_CONTENTS.md``'s unconditional imports
    resolve-to-empty instead of warning on every launch of every box that has no
    workset or box chapter. A BOUND chapter replaces the whole directory, fallback
    included. See :data:`HANDBOOK_FALLBACK_ENTRIES`.
    """
    root = CANON_GUEST_ROOT
    rels: list[tuple[str, bool]] = [
        (root, True),
        (f"{root}/{ROM_COLLECTION_REL}", False),
        (f"{root}/{ROM_BIBLE_REL}", True),
        (f"{root}/{ROM_CONTENTS_REL}", False),
    ]
    rels += [
        (f"{root}/{ROM_BIBLE_REL}/{chapter}", True)
        # ⚑ ``agent`` is ALWAYS pre-created, emission gate or not (J-7): a gate-false
        # launch must show an EMPTY root-owned mountpoint, not a missing directory.
        for chapter in (*ROM_BIBLE_CHAPTERS, BIBLE_AGENT_CHAPTER)
    ]
    rels += [
        (f"{root}/{HANDBOOK_REL}", True),
        (f"{root}/{HANDBOOK_CONTENTS_REL}", False),
    ]
    rels += [
        (f"{root}/{HANDBOOK_REL}/{chapter}", True) for chapter in HANDBOOK_CHAPTERS
    ]
    # The IMPORT-FALLBACK entry files (F1), INSIDE three of those mountpoints.  Their
    # ``directives/`` parents are part of the skeleton too — root-owned like
    # everything else here, so nothing in the books is agent-creatable.
    for chapter, entry in HANDBOOK_FALLBACK_ENTRIES:
        chapter_dir = f"{root}/{HANDBOOK_REL}/{chapter}"
        rels.append((f"{chapter_dir}/{HANDBOOK_DIRECTIVES_DIRNAME}", True))
        rels.append((f"{chapter_dir}/{HANDBOOK_DIRECTIVES_DIRNAME}/{entry}", False))
    return tuple(rels)


def materialize_canon_skeleton(
    shell_path: Path,
    *,
    logger: "logging.Logger | None" = None,
    quiet: bool = False,
) -> None:
    """Create the canon SKELETON in a box home and make it root-owned + unwritable.

    The J-7 assembly model in one function.  Called at box CREATE (after the seed,
    before the create-journal entry is cleared) and again after any box-home COPY;
    the LAUNCH path never touches it.

    ⚑ ORDER IS LOAD-BEARING: seed FIRST, protect SECOND.  The seeds/handbook half
    will seed ``canon/notebook`` + ``canon/workbook``, which live UNDER ``canon/``;
    if the 555 landed first those copies would fail with EACCES.

    ⚑ IDEMPOTENT, BUT NOT EXTENSIBLE ONCE PROTECTED.  Re-running over an
    already-materialised skeleton is a no-op (every ``mkdir``/``touch`` is
    create-if-absent) and the ownership pass is a plain re-assert, so calling this
    after a box-home COPY restores what the copy could not carry.  It does NOT,
    however, let a FUTURE release add a new mountpoint to
    :func:`canon_skeleton_rels` and have existing boxes pick it up: creating a new
    entry inside an already-root-owned parent fails with EACCES, and this function
    swallows that (per-path ``OSError`` is debug-logged and skipped, because one
    unmakeable mountpoint must not cost a box the other thirteen — podman's own
    error at launch is the honest signal).  ⇒ **Growing the skeleton is a MIGRATION,
    not a redeploy.**  That is why the handbook mountpoints are created NOW, ahead of
    the binds that will use them in the seeds half.

    ⚑ WHY OWNERSHIP AND NOT MODE ALONE.  A 555 directory the agent OWNS is not
    protection — the owner can ``chmod +w`` it back.  Only an owner the in-box agent
    is not can make ``~/canon`` un-litterable.

    DEGRADED PATH (no container runtime, docker, or a failing ``unshare``): the
    skeleton is left agent-owned and writable and ONE warning is logged.  Box create
    must not hard-fail on a missing runtime — creating a box works today with no
    podman installed — and the skeleton is what makes the binds land, so a box in
    this state is fully functional, just not litter-proof.
    """
    log = logger or _skeleton_logger()
    dirs: list[Path] = []
    files: list[Path] = []
    for rel, is_dir in canon_skeleton_rels():
        p = shell_path / rel
        try:
            if is_dir:
                p.mkdir(parents=True, exist_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.touch()
        except OSError as exc:
            log.debug("canon skeleton: could not create %s (%s)", p, exc)
            continue
        (dirs if is_dir else files).append(p)

    if not dirs and not files:
        return
    _protect_canon_skeleton(dirs, files, log, quiet=quiet)


def materialize_canon_skeleton_if_present(
    shell_path: Path, *, logger: "logging.Logger | None" = None,
) -> None:
    """Re-assert an EXISTING canon skeleton; do nothing if the home has none.

    The post-start re-protect for homes that are not box homes — helper boxes today.
    ``materialize_canon_skeleton`` would CREATE the skeleton, which is wrong here: a
    helper home is not a box and gaining canon mountpoints from a launch would be a
    silent layout change made by the wrong seam.  Re-asserting what is already there
    is always right, because ``:U`` re-chowns whatever the bind source holds.
    """
    if not (shell_path / CANON_GUEST_ROOT).is_dir():
        return
    materialize_canon_skeleton(shell_path, logger=logger, quiet=True)


def _skeleton_logger() -> "logging.Logger":
    return logging.getLogger(__name__)


def _protect_canon_skeleton(
    dirs: list[Path], files: list[Path], log: "logging.Logger", *, quiet: bool = False,
) -> None:
    """Make the skeleton root-owned + unwritable from inside the user namespace.

    THREE ``podman unshare`` calls: one ``chown`` over everything, then a ``chmod``
    per mode class — dirs ``555`` (the search bit is what lets crun traverse
    ``~/canon`` to reach the chapter mountpoints) and file mountpoints ``444``.

    ⚑ NEVER ``-R``: a recursive sweep of ``canon/`` would take the SEEDED,
    agent-owned ``notebook/`` + ``workbook/`` with it, which must stay writable.  The
    skeleton is a closed, enumerated set, so an explicit list is both safer and no
    harder.
    """
    from kanibako.runtime.container import ContainerError, ContainerRuntime

    everything = dirs + files
    try:
        runtime = ContainerRuntime()
    except ContainerError:
        _warn_unprotected(
            everything[0], log, "no container runtime is available", True, quiet,
        )
        return

    if not runtime.unshare_chown(
        everything, UNSHARE_BOX_ROOT_UID, UNSHARE_BOX_ROOT_GID,
    ):
        _warn_unprotected(
            everything[0], log, "podman unshare chown did not succeed", True, quiet,
        )
        return
    for group, mode in ((dirs, CANON_SKELETON_DIR_MODE),
                        (files, CANON_SKELETON_FILE_MODE)):
        if group and not runtime.unshare_chmod(group, mode):
            _warn_unprotected(
                everything[0], log,
                f"podman unshare chmod {mode} did not succeed",
                False, quiet,
            )
            return
    log.debug(
        "canon skeleton protected (%d dirs + %d files under %s)",
        len(dirs), len(files), everything[0],
    )


def _warn_unprotected(
    root: Path, log: "logging.Logger", reason: str, agent_owned: bool,
    quiet: bool = False,
) -> None:
    """Report a skeleton that did not get its full lockdown.

    ⚑ *quiet* demotes the report to DEBUG.  The POST-START caller sets it: that hook
    runs while the user's terminal is being handed to the agent (tmux, a TUI), and on
    a docker host — or anywhere ``unshare`` cannot work — this fires on EVERY launch,
    so at WARNING it would paint over the session, forever, for a condition the user
    already learned about at box create.  Create reports it loudly; the per-launch
    re-assert does not repeat it.

    ⚑ The two arms are genuinely different and must not share wording.  If the CHOWN
    did not happen (*agent_owned*), the tree is the agent's and fully writable in-box.
    If the chown SUCCEEDED and only a chmod failed, the tree is root-owned at its
    default mode (0755/0644) — the agent CANNOT write it, but the world-traversal and
    file modes are not the declared ones, and a later chmod re-assert is still owed.
    """
    emit = log.debug if quiet else log.warning
    if agent_owned:
        emit(
            "canon books at %s are left writable from inside the box (%s). The box "
            "works normally — every canon bind still lands on its mountpoint — but "
            "the agent can create stray files under ~/canon instead of only under "
            "~/canon/{notebook,workbook}.",
            root, reason,
        )
    else:
        emit(
            "canon books at %s are root-owned but keep their default modes (%s). The "
            "agent still cannot write them, and the box works normally; only the "
            "declared 555/444 modes were not applied.",
            root, reason,
        )


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
    graph_root: Path | None,
    storage_conf_path: Path,
) -> dict[str, tuple[str, str, str] | str]:
    """Build the image-sharing binds as ``default_categories`` (Phase B, D-M8).

    Maps ``box.bindings.ro.<key>`` → a STRUCTURED 3-TUPLE
    ``(host_src, box_dest, options)`` for the host image graph root + the GENERATED
    ``storage.conf``, routed through the category resolver (the sole route; the
    old hardwired Mounts are gone).  The box-side destinations + options come from
    the declarative file (``images:`` list); the host SOURCES (the runtime-probed
    *graph_root* and the already-GENERATED *storage_conf_path*) are injected here.

    ⚑ B3 (spec §2b / §2c, D-M8): the store bind ``box.bindings.ro.images`` is
    ROUTED THROUGH THE USER KEY — its shipped host_src is the @-ref
    ``@box.images_store`` (the file's ``meta_ref``, helper_log parity), and the
    runtime-probed *graph_root* enters the keyspace HERE as that key's DEFAULT: a
    floor scalar in the returned table.  The floor is the least-specific cascade
    level (``base``), so a ``box:``/``workset:``/``system:`` FILE value for
    ``images_store`` overrides it by name and the ONE expand pass resolves the
    bind's host_src to the winning value.  ``images_conf`` stays an INTERNAL bind
    and NOT a key (fixed location, generated content — spec §0's test).

    ⚑ 11a (2026-08-02): the PROBE feeds ONLY that default — *graph_root* may be
    ``None`` (probe failed), in which case NO floor scalar is emitted and the
    ``@box.images_store`` host_src resolves solely from a set tier value (or
    propagates ABSENT, dropping the bind).  The caller's gate is now just
    "image-sharing requested" — the code realization of the spec's
    ``%if @box.share_images%`` condition on the ``images`` row; whether the
    RESOLVED store exists is decided after the resolve, at the seam.
    """
    sources: dict[str, str] = {
        "images_conf": str(storage_conf_path),
    }

    binds: dict[str, tuple[str, str, str] | str] = {}
    if graph_root is not None:
        sources["images_store"] = str(graph_root)
        # The USER KEY behind the store bind: the probe lands as the DEFAULT of
        # ``box.images_store`` (manifest §2b row — "<runtime-probed podman
        # graphroot>"); the ``images`` bind's ``@box.images_store`` host_src
        # resolves against whatever value wins the cascade.
        binds["box.images_store"] = str(graph_root)
    for entry in _load_doc().get("images", []):
        category = entry["category"]
        # ``meta_ref`` (when declared) is the emitted host_src — the spec's own
        # @-ref spelling; the symbolic ``source`` stays the probed-literal
        # fallback (helper_default_categories parity; LAZY so a probe-fail
        # ``None`` only raises if an entry actually needs the probed literal).
        host_src = entry.get("meta_ref")
        if host_src is None:
            host_src = sources[entry["source"]]
        binds[f"box.{category}.{entry['key']}"] = (
            host_src,
            str(entry["box_dest"]),
            str(entry["options"]),
        )
    return binds
