"""Shell template resolution and application."""

from __future__ import annotations

import hashlib
import importlib.resources
import re
import shutil
import tempfile
from collections.abc import Iterable
from typing import TYPE_CHECKING
from pathlib import Path

from kanibako.settings.core_defaults import ROM_ROOT_PARTS, packaged_data_dir

if TYPE_CHECKING:
    from kanibako.settings.paths import ProjectPaths, StandardPaths


# ---------------------------------------------------------------------------
# Layered box seed (spec §2a "Template seed (LAYERED, ordered)").
#
# The seed model layers three ordered sources into the box store's ONE seeded
# destination at creation (base -> agent -> workset; later overlays earlier) —
# THREE keys in total:
#
#   1 system.seeded.template   | (@system.template/box/home,                  @meta.box.path/home)
#   2 agent.<a>.seeded.template| (@agent.<a>.template/box/home,               @meta.box.path/home)
#   3 workset.seeded.template  | (@workset.template/box/home,                 @meta.box.path/home)
#
# ⚑⚑ THERE ARE NO HANDBOOK LAYERS HERE, AND THIS IS NOT AN OVERSIGHT.  Until
# 2026-08-07g three more layers seeded ``@box.canon/handbook`` from each scope's
# ``box/canon/handbook`` subtree.  Jei RULED them OUT of the category — *"they do
# not DIRECTLY interact with the box itself … They are HOST templates, not GUEST
# templates"* — and the ratified spec's §2a no longer declares them, so nothing in
# the keyspace names a ``seeded`` entry at ``@box.canon/handbook``.  The box's own
# handbook chapter is filled by :func:`install_box_handbook_template`, a HOST-side
# copy at create; read the QUARANTINE block above that function before touching it.
# The box HOME layers below stay in the category because the home IS what the guest
# sees at ``~``.
#
# The layer SOURCES are NOT derived on disk here — they are ORDINARY keystore
# ``seeded`` category keys resolved through the launch snapshot (ruled 2026-07-09
# Q1: everything goes through the keystore + seeding, no bespoke template route).
# :func:`template_seed_defaults` declares them as default-category entries; the
# seed seam (``commands.start._apply_init_seeds``) resolves them off the committed
# snapshot and applies the dest's layers IN ORDER via :func:`stage_layers`.
#
# ⚑⚑ THE DEST IS A **HOST** PATH (spec §2a, ruled: the tuple direction is
# ``(source, HOST destination)`` for COPY categories).  ``@meta.box.path/home`` is
# the box store's home dir — the very directory the ``box.bindings.rw.home`` mount
# then delivers at ``~``.  Being host-spelled is what makes the entry's
# ``dest_space`` load-bearing rather than cosmetic: a box store under
# ``/home/agent/.local/share/…`` starts with the GUEST home prefix, so the guest
# translator would happily re-root it under the box home and report success.  The
# entry therefore CARRIES its space; no prefix test could tell the two apart.
#
# ⚑ ``@box.canon`` IS NOT ``~/canon``.  See ``settings_categories.CategoryEntry``.
#
# Sources: @system.template (system.* settings tier) · @agent.<a>.template =
# @config.agents/<harness>/template · @workset.template = @meta.workset.path/template
# (skip-if-absent — the seeded category drops a layer whose source dir is absent).
# ---------------------------------------------------------------------------

#: The box store's HOST-side seed destination, as an ``@``-ref formula (spec §2a).
#: ⚑ ``@meta.box.path/home`` is the ONLY one — "SEED DESTINATIONS ARE ENUMERATED,
#: NEVER A WHOLE-DIRECTORY COPY", because a wholesale ``template/box/* ->
#: <box_dir>/*`` copy could plant ``<box_dir>/settings.yaml``, which IS
#: ``meta.box.settings``, the LAST cascade level.
_SEED_DEST_HOME = "@meta.box.path/home"

#: The per-layer SOURCE subpaths under each layer's ``template`` root.  The two-level
#: ``box/`` is the declared WHITELIST BOUNDARY (J-2): everything under it is box
#: ENDPOINT content and gets the box whitelist; ``home`` and ``canon/handbook`` are
#: the box store's two allowed top-level entries, not decoration.  ⚑ Only ``home``
#: is a SEED source now — ``canon/handbook`` is read by the host-side
#: :func:`install_box_handbook_template` copy (2026-08-07g), which is why the two
#: constants no longer sit in the same table.
_SEED_SRC_HOME = "box/home"
_SEED_SRC_HANDBOOK = "box/canon/handbook"

#: The ``template`` entry of a SCOPE STORE — the subtree the per-agent and
#: per-workset layer SOURCES (``@agent.<a>.template`` / ``@workset.template``) point
#: at by default.  Named because three things must agree on it and one of them is a
#: transition arm whose failure is silent: the layer-2/3 source key defaults, the
#: legacy-payload landing path, and the whitelist entry that permits it.
AGENT_TEMPLATE_STORE_REL = "template"


def template_seed_defaults(
    proj: ProjectPaths, agent_id: str | None
) -> dict[str, object]:
    """Return the layered box-seed DEFAULT-category table (spec §2a — THREE keys).

    Three ordered layers into ONE enumerated destination, as ORDINARY keystore keys,
    ready to fold into the seed-time snapshot's ``default_categories``
    (``commands.start._apply_init_seeds``) so they resolve + apply through the SAME
    single seeded-category route as every other seed — no bespoke template plumbing
    (Q1).  Each is a ``seeded`` COPY into a HOST path under the box store, sourced
    from an ``@``-ref SETTINGS key so the source stays user-repointable through the
    cascade (setting ``workset.template`` / ``agent.<a>.template`` reroutes that
    layer):

    * ``system.seeded.template`` — ALWAYS (Q4: no carve-out).
    * ``agent.<a>.seeded.template`` — only when an agent is bound; the
      source key ``agent.<a>.template`` defaults to ``@config.agents/<harness>/
      template`` (spec §2a/§2d; ``<a>`` = the persona+harness node, Q2). Absent for a
      NO-AGENT box.
    * ``workset.seeded.template`` — only for a PRIMARY/NAMED box (a
      workset tier exists); the source key ``workset.template`` defaults to
      ``@meta.workset.path/template`` (Q3, was ``<None>``). STANDALONE has no workset
      tier, so the layer is OMITTED. Each layer is SKIPPED when its source dir is
      absent — the seeded category's ordinary missing-source semantics.

    The returned dict mixes the SEED tuple keys with their SOURCE scalar keys
    (``workset.template`` / ``agent.<a>.template``) so both land in the snapshot
    floor: the scalar resolves the ``@``-ref, and a user override of the scalar
    (config set / settings file) wins by cascade precedence and reroutes the seed.
    ``system.template`` is already floor-materialized (it is a ``system.*``
    settings-tier path), as are ``@meta.box.path`` and ``@box.canon``
    (``settings_launch.workset_anchor_floor``), so none is re-declared here.

    ⚑ The SOURCE keys are shared with the box HANDBOOK host-template copy
    (:func:`handbook_layer_source_keys`), which reads the SAME three
    ``<scope>.template`` scalars this table declares and is gated by them — that is
    why the handbook layers leaving the ``seeded`` category (2026-08-07g) did not
    make the box handbook any less repointable.
    """
    from kanibako.agent_ref import harness_of
    from kanibako.channels.channels import has_workset_channels

    def _layer(source_root: str) -> dict[str, object]:
        return {
            "template": (f"{source_root}/{_SEED_SRC_HOME}", _SEED_DEST_HOME),
        }

    defs: dict[str, object] = {
        f"system.seeded.{name}": value
        for name, value in _layer("@system.template").items()
    }
    if agent_id:
        harness = harness_of(agent_id)
        # SOURCE key (spec §2a/§2d): the per-agent template dir under the agent's
        # (harness) store — the same @config.agents/<harness>/template the retired
        # on-disk deriver produced, now a resolvable/settable keystore key.
        defs[f"agent.{agent_id}.template"] = (
            f"@config.agents/{harness}/{AGENT_TEMPLATE_STORE_REL}"
        )
        for name, value in _layer(f"@agent.{agent_id}.template").items():
            defs[f"agent.{agent_id}.seeded.{name}"] = value
    if has_workset_channels(proj):
        # SOURCE key (spec §2c; Q3 default @meta.workset.path/template): the
        # workset-local template dir. STANDALONE (no workset channels) omits BOTH
        # the source and the layers (its workset tier is <None>).
        defs["workset.template"] = (
            f"@meta.workset.path/{AGENT_TEMPLATE_STORE_REL}"
        )
        for name, value in _layer("@workset.template").items():
            defs[f"workset.seeded.{name}"] = value
    return defs


def seed_keys_of(defs: "dict[str, object]") -> frozenset[str]:
    """The ``seeded`` KEY names inside a :func:`template_seed_defaults` table.

    The single source of the HOST-space key set the launch resolve needs
    (``settings_launch.snapshot_category_entries(host_dest_keys=…)``): every key this
    module declares in the ``seeded`` category targets the ONE ENUMERATED HOST
    destination above (``@meta.box.path/home``), and nothing else in the table does.
    Derived from the table rather than restated so a fourth layer cannot be added in
    one place and forgotten in the other — which would silently route its copy
    through the GUEST translator and land it inside the box home (see
    ``CategoryEntry.dest_space``).

    ⚑ STILL LIVE AND STILL NEEDED after the handbook layers left the category
    (2026-08-07g).  The home dest is spelled ``@meta.box.path/home`` — a HOST path —
    so the discriminator has exactly as much to do as before; only the number of
    host destinations went from two to one.

    A USER-declared ``<scope>.seeded.<name>`` is NOT in here and stays GUEST-space,
    exactly as today.
    """
    return frozenset(key for key in defs if ".seeded." in key)


def stage_layers(dest: Path, layers: list[Path]) -> None:
    """Seed *dest* once from the ordered *layers* via a TEMP staging dir.

    The per-file LAST-WINS merge across the ordered layer dirs is resolved in a
    temporary staging dir (where overwrite is intended), and the merged tree is
    then copied into *dest* with CREATE-IF-ABSENT (an existing *dest* file is
    NEVER overwritten). Two phases:

    1. **Stage.** Copy each layer's files into a temporary dir in order
       (LOWEST -> HIGHEST).  Overwrite WITHIN staging is intended, so a later
       layer's file at the same relative path wins (per-file last-wins).

    2. **Seed.** Copy the merged staged tree into *dest* with
       :func:`copy_resource_tree_if_absent` — a pre-existing *dest* file survives
       untouched.  This is the load-bearing failsafe against re-seed DATA LOSS.

    SKIP-IF-ABSENT: a *layers* entry that is not an existing directory is silently
    skipped (the spec §2a "layer skipped if the source dir is absent" — e.g. an
    unpopulated ``@workset.template``). SEED-ONCE: the caller invokes this only at
    box CREATE (registry MEMBERSHIP is the seed signal, spec §0/§5), never on a
    relaunch. No file is special-cased or merged — every file is a plain ordered
    copy (no CLAUDE.md merge, D-B5).

    This is the layered-copy MECHANISM only; the layer dirs are resolved through
    the keystore by the caller (``commands.start._apply_init_seeds``), NOT derived
    on disk here — the "sole intermediary is the keystore" invariant (Q1/Q3/Q4).
    """
    from kanibako.errors import TemplateScopeError

    present = [layer for layer in layers if layer.is_dir()]
    if not present:
        return
    with tempfile.TemporaryDirectory(prefix="kanibako-seed-") as staging:
        staged = Path(staging)
        for layer in present:
            for entry in sorted(layer.rglob("*")):
                # SOURCE SYMLINK refusal (spec §2a enforcement point 3), checked
                # HERE as well as in ``copy_tree``: staging is where layer content is
                # first read, and ``is_file()`` below would FOLLOW a symlink-to-file
                # so the exfiltrated bytes would already be in the staging dir by the
                # time the shared copier saw them (as a plain file).
                if entry.is_symlink():
                    raise TemplateScopeError(
                        f"template layer {str(layer)!r} contains the SYMLINK "
                        f"{str(entry)!r}. Layer content is copied by VALUE, so "
                        f"following it would stage the bytes of "
                        f"{str(entry.resolve())!r} into the box — the secret-"
                        f"exfiltration escape spec §2a closes. Seed refused."
                    )
                if not entry.is_file():
                    continue
                rel = entry.relative_to(layer)
                dest_file = staged / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                # Overwrite within staging is intended (per-file last-wins).
                shutil.copy2(str(entry), str(dest_file))
        copy_tree(staged, dest)


# ---------------------------------------------------------------------------
# Packaged content install — the ENUMERATED (packaged subtree -> host store) set.
#
# The content ships as STATIC files inside the installed packages (mirroring how
# ``image-baseline.yaml`` ships under ``kanibako.data``):
#
#   core   -> ``kanibako.data`` resource ``global/template/``, whose FOUR subtrees
#             (``box``, ``workset``, ``agent_default``, ``handbook``) each have their
#             OWN destination — the root is never copied wholesale (P-S2)
#   plugin -> ``kanibako.plugins.<agent>`` resource ``data/base/`` (RENAMED from
#             ``data/template`` — D4), the agent STORE payload
#
# Destinations, and their OWNERS, because the copy rule follows the owner:
#
#   @system.template/{box,workset,agent}   SYSTEM-owned STAGING — ``refresh=True``
#                                          may rewrite shipped files here
#   @system.canon/handbook                 USER-owned — create-if-absent ALWAYS
#   @config.agents/{default,<agent>}       USER-owned — create-if-absent ALWAYS
#
# Fired at first-run init (``cli._ensure_initialized``) and at ``kanibako setup``.
# Every copy is CREATE-IF-ABSENT per file except the staging rows under an explicit
# refresh: it adds files the user does not yet have and never clobbers their edits
# (J-3 item 1).
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# THE ONE COPIER — every template/seed/store fill goes through here (P-S4).
# ---------------------------------------------------------------------------

#: Per-SCOPE store-root WHITELISTS (spec §2a, ruled by Jei 2026-07-30).  The KEY
#: INSIGHT that makes them one predicate instead of a list of special cases: each
#: packaged per-scope template subtree MIRRORS THAT SCOPE'S STORE ROOT, so the
#: whitelist is exactly the set of top-level entries that subtree may contain.
#:
#: ⚑ DENY-BY-DEFAULT.  Anything not listed is an ERROR.  The DENIED entries are not
#: hypothetical and their severities differ:
#:   BOX      ``settings.yaml`` = ``meta.box.settings``, the LAST cascade level, so
#:            template content would become the box's TOP-PRIORITY settings, carrying
#:            any key it liked (CORRECTNESS).  Create-if-absent is no defence: on a
#:            fresh box there is nothing there to lose the race.
#:   AGENT    ``settings.yaml`` = ``meta.agent.<a>.settings`` (CORRECTNESS); ``caches/``.
#:   WORKSET  ``settings.yaml``; ``registry.yaml`` = ``workset.registry``, the
#:            AUTHORITATIVE box membership + names, so a templated one could ORPHAN or
#:            COLLIDE boxes (CORRECTNESS); ``auth/`` (CREDENTIALS), ``vault/``,
#:            ``workspaces/`` (THE USER'S CODE); ``boxes/``, ``logs/``, ``channels/``.
#:
#: ⚑ STANDALONE is where this bites hardest: ``<workset_path>`` IS the user's own
#: project directory, so the workset whitelist guards the tree they actually work in.
#:
#: ⚑ The sets are NOT one uniform rule.  ``common/`` is allowed at AGENT scope only
#: (Jei: there are use cases) and behaves differently from its siblings — ``canon/``
#: is delivered RO and ``template/`` is inert until a box is created, whereas
#: ``common/`` is bound RW and SHARED by every box running that agent, so starter
#: content there is live data, not a template.  ONLY ``canon/handbook`` is seedable,
#: never ``canon/`` wholesale: no other canon subtree has a justified seed today, and
#: deny-by-default means a future one needs an explicit decision.
SCOPE_WHITELISTS: dict[str, tuple[str, ...]] = {
    "box": ("home", "canon/handbook"),
    "agent": ("template", "canon/handbook", "common"),
    "workset": ("template", "canon/handbook"),
}


def _check_whitelist(store_rel: Path, scope: str) -> None:
    """RAISE unless *store_rel*'s leading components are inside *scope*'s allow-list.

    ⚑ *store_rel* is relative to the SCOPE STORE ROOT (``copy_tree``'s *dest_root*),
    NOT to the copy's source.  The two coincide for a whole-store copy, but they
    DIVERGE the moment a copy targets a subdirectory of a store — the legacy
    plugin-payload arm lands ``data/template/**`` at ``<store>/template/box/home``,
    where the source-relative path (``.claude.json``) says nothing about which
    top-level store entry is being written and the store-relative path
    (``template/box/home/.claude.json``) says exactly that.  Checking the wrong one
    would either refuse a legal copy or wave through an illegal one.
    """
    from kanibako.errors import TemplateScopeError

    allowed = SCOPE_WHITELISTS[scope]
    posix = store_rel.as_posix()
    if any(posix == a or posix.startswith(f"{a}/") for a in allowed):
        return
    raise TemplateScopeError(
        f"template content for the {scope.upper()} scope may not contain "
        f"{store_rel.parts[0]!r} (offending entry: {posix!r}). The {scope} store's "
        f"allowed top-level entries are: {', '.join(allowed)} — anything else is "
        f"DENIED by default (spec §2a). Move the file under an allowed entry, or "
        f"remove it from the template."
    )


def _assert_contained(target: Path, root: Path, *, what: str) -> None:
    """RAISE unless *target*'s REAL path stays inside *root*'s real path.

    Catches BOTH residual escapes §2a names: a ``..`` component in a declared dest,
    and a symlinked intermediate DIRECTORY in the destination tree that ``mkdir`` +
    ``copy2`` would happily write THROUGH.  ``resolve()`` on both sides is what makes
    the second one visible — a plain string comparison would not see it.
    """
    from kanibako.errors import TemplateScopeError

    real_root = root.resolve()
    real_target = target.resolve()
    if real_target != real_root and real_root not in real_target.parents:
        raise TemplateScopeError(
            f"{what} {str(target)!r} resolves to {str(real_target)!r}, which is "
            f"OUTSIDE the destination subtree {str(real_root)!r}. Copy refused "
            f"(spec §2a: the copier must reject '..' components and must not follow "
            f"symlinks out of the destination subtree)."
        )


def copy_tree(
    src: Path,
    dest: Path,
    *,
    overwrite: bool = False,
    scope: str | None = None,
    dest_root: Path | None = None,
    check_only: bool = False,
) -> None:
    """Copy every file under *src* into *dest*, skipping files that exist.

    **The ONE copier.**  Every template stage, box seed and host-store fill routes
    here (P-S4), so the whitelist and the traversal defences below cannot be present
    on one path and missing on another.

    Mirrors the relative tree of *src* into *dest*.  Existing destination files are
    left untouched (create-if-absent) so user edits to a seeded template survive a
    later kanibako upgrade.  Directories are created as needed.

    When *overwrite* is True (the TRUE-REFRESH path used by
    ``install_packaged_templates(..., refresh=True)``) an existing destination file IS
    replaced with the packaged version.  ⚑ That flag is confined to the SYSTEM-OWNED
    packaged STAGING (``@system.template/**``) — user-owned stores
    (``@system.canon/handbook``, ``@config.agents/**``, worksets, boxes) are
    create-if-absent on EVERY path, always, and their differences are REPORTED rather
    than written (J-3 item 1).  The default (False) preserves the load-bearing
    create-if-absent contract — the alias :data:`copy_resource_tree_if_absent`
    (reused by the box-SEED apply in ``commands.start``) must NEVER clobber a per-box
    home file.

    *scope* (``"box"`` / ``"agent"`` / ``"workset"``) turns on the §2a WHITELIST,
    evaluated on each entry's path RELATIVE TO *dest_root* — so it is correct both for
    a whole-store copy (*dest* IS the store root) and for a copy into a subdirectory
    of one (the legacy plugin-payload arm).  It is OMITTED only where the dest is not
    a scope store at all: the box SEED's dest is key-fixed at ``home``, the box
    handbook host-template copy's is key-fixed at ``canon/handbook``
    (:func:`install_box_handbook_template`), and the packaged handbook's dest is
    inside the canon root.

    *dest_root* is BOTH the containment boundary and the whitelist's frame of
    reference (defaults to *dest*).

    Enforcement points — all four of §2a's, and every one of them BEFORE any write:

    1. WHITELIST on the leading component(s) of the store-relative path;
    2. CONTAINMENT of the resolved destination parent (the ``..`` escape, and a
       symlinked intermediate DIRECTORY) — checked ahead of the ``mkdir``, so a
       refused copy leaves nothing behind;
    3. SOURCE SYMLINK refusal — ``Path.rglob`` does not recurse into symlinked dirs,
       but ``entry.is_file()`` FOLLOWS a symlink-to-file and ``copy2`` would then copy
       the TARGET's bytes, so a template containing ``x -> ~/.ssh/id_ed25519``
       exfiltrates it into the box home;
    4. DEST SYMLINK refusal — the LEAF, by ``lstat``.  The parent check cannot see
       this one: a dangling symlink reads as absent to ``exists()`` and gets written
       through, and a live one is followed so ``overwrite`` replaces a file outside
       the subtree entirely.

    *check_only* runs every one of those four and writes NOTHING — the PRE-FLIGHT
    form.  It exists because "refuse loudly" is not the same as "refuse cleanly": a
    refusal part-way through a walk leaves the files copied before the offender, and
    where the caller has already REGISTERED something (``workset create``) the user
    is left with a half-built store to clean up by hand.  ⚑ It is the SAME function,
    deliberately — a separate validator would be a second copy of the rules, free to
    drift from the one that actually writes.  Pair it with the real call; do not use
    it as a substitute for one (nothing here is atomic against a concurrent writer).
    """
    from kanibako.errors import TemplateScopeError

    if not src.is_dir():
        return
    root = dest_root if dest_root is not None else dest
    _assert_contained(dest, root, what="copy destination")
    for entry in sorted(src.rglob("*")):
        # (3) SOURCE SYMLINK — checked BEFORE is_file(), which would follow it.
        if entry.is_symlink():
            raise TemplateScopeError(
                f"template source {str(entry)!r} is a SYMLINK. Template content is "
                f"copied by VALUE, so following it would copy the bytes of "
                f"{str(entry.resolve())!r} into the destination — the secret-"
                f"exfiltration escape spec §2a closes. Copy refused; replace the "
                f"symlink with a real file or remove it."
            )
        if not entry.is_file():
            continue
        rel = entry.relative_to(src)
        target = dest / rel
        if scope is not None:
            # (1) WHITELIST — on the STORE-relative path (see _check_whitelist).
            _check_whitelist(target.relative_to(root), scope)
        # (2)+(4) BEFORE ANY WRITE, INCLUDING THE mkdir. A refused copy must leave
        # NOTHING behind: creating the parent first would litter directories outside
        # the destination subtree on the very path we are about to refuse.
        # ``resolve()`` sees through a symlinked intermediate dir that already
        # exists, which is the attack; a not-yet-existing parent resolves to its
        # would-be path and passes.
        _assert_contained(target.parent, root, what="copy destination directory")
        # (4b) A SYMLINKED FINAL TARGET, checked with lstat BEFORE the two branches
        # below — both of which follow it.  ``target.exists()`` follows a symlink, so
        # a DANGLING one reads as absent and ``copy2`` then writes THROUGH it; and
        # with *overwrite* a live one is followed and a file OUTSIDE the subtree is
        # replaced.  Neither is caught by the parent check above, because the escape
        # is the leaf itself.
        if target.is_symlink():
            raise TemplateScopeError(
                f"copy destination {str(target)!r} is a SYMLINK (to "
                f"{str(target.resolve())!r}). Writing through it would put template "
                f"content outside the destination subtree {str(root)!r} — the escape "
                f"spec §2a closes. Copy refused; remove the symlink."
            )
        if check_only:
            continue  # PRE-FLIGHT: every guard above ran; nothing is written.
        if target.exists() and not overwrite:
            continue
        if overwrite and target.exists() and _equivalent(entry, target):
            # PREVIEW AND ACTION MUST TELL ONE TRUTH. ``plan_template_refresh``
            # classifies an EQUIVALENT file as "current" and does not report it, so a
            # refresh that rewrote its bytes anyway would silently revert a user's
            # whitespace/comment edit in the staging the preview just called
            # unchanged. Same classifier, same verdict, both sides.
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(entry), str(target))


# ``copy_resource_tree_if_absent`` is the create-if-absent spelling other modules
# (e.g. the seed-once apply in commands.start) reuse; it names ``copy_tree``, whose
# skip-if-present behaviour is what the longer name is asserting.
copy_resource_tree_if_absent = copy_tree


#: Subtrees of the packaged template root, by their role.  ⚑ The install is an
#: ENUMERATED set of (packaged subtree → host dest) pairs, NEVER a whole-tree copy
#: (P-S2): copying the root wholesale would leave a SECOND, never-read copy of the
#: handbook at ``@system.template/handbook``, which is the duplicated-shared-data
#: defect design principle 2 forbids — and §2a states the same rule ("SEED
#: DESTINATIONS ARE ENUMERATED … AND THIS HOLDS AT EVERY LEVEL") for every level.
PACKAGED_BOX_TEMPLATE = "box"
PACKAGED_WORKSET_TEMPLATE = "workset"
PACKAGED_AGENT_DEFAULT = "agent_default"
PACKAGED_HANDBOOK = "handbook"

#: The AGENT MOULD's dir name under ``@system.template`` — the host copy every agent
#: install stamps from (J-5).  ⚑ There is deliberately NO packaged ``template/agent``
#: directory: the mould ships EMPTY (D5), and a wheel cannot ship an empty dir, so the
#: host dir is GUARANTEE-CREATED by the install action (D7).  Shipping structure only
#: is also what keeps it OVERLAP-FREE with ``agent_default``: both are stamped
#: create-if-absent into the same store, so an overlapping mould file would win over
#: the default agent's own content.
AGENT_MOULD_DIRNAME = "agent"

#: The box-template SKELETON a scope store gets guarantee-created (D7) so the shape
#: is discoverable: a user who wants a per-workset or per-agent box template can see
#: where the files go instead of having to know the layout.  ⚑ Spelled to the SPEC
#: shape — ``home/canon/{notebook,workbook}``, NOT the samples' ``home/{notebook,
#: workbook}`` (D6 records that as an oversight in the sample tree).
_BOX_TEMPLATE_SKELETON = (
    "template/box/home/canon/notebook",
    "template/box/home/canon/workbook",
    "template/box/canon/handbook",
)


def _packaged_base_template() -> Path | None:
    """Locate the packaged TEMPLATE ROOT (``kanibako.data/global/template``).

    One of the two packaged content roots (``global/rom`` is BOUND, ``global/
    template`` is INSTALLED — the same bound-vs-seeded split §2c draws for the canon
    books themselves).  Its four subtrees are enumerated above and each has its OWN
    host destination; this function returns the ROOT, which is what the staleness
    DIGEST walks and what :func:`install_packaged_templates` indexes into.

    ⚑ It is NOT itself an install dest.  Before the canon restructure this root WAS
    copied wholesale into ``@system.base_template`` and seeded at the box home ``~``,
    so a root-relative path was also a home-relative one.  That is no longer true —
    the box-HOME seed source is ``template/box/home``, two levels down — and any code
    treating a root-relative walk as home-relative is now silently wrong (see
    :func:`kanibako.settings.core_defaults.assert_canon_bind_seed_disjoint`, whose caller had
    to be re-anchored for exactly this reason).
    """
    try:
        ref = packaged_data_dir("global", "template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def packaged_box_home_template() -> Path | None:
    """The packaged BOX-HOME seed source (``template/box/home``), or None.

    The HOME-RELATIVE root: every path under it is spelled exactly as it lands in a
    box home.  Named because two consumers need precisely this level and would be
    wrong one level up — the disjointness guard (whose seed rels must be comparable
    with the ``canon/...`` bind dests) and any future home-layout check.
    """
    base = _packaged_base_template()
    if base is None:
        return None
    path = base / PACKAGED_BOX_TEMPLATE / "home"
    return path if path.is_dir() else None


def _packaged_shared_bundle() -> Path | None:
    """Locate the packaged read-only built-in CANON tree (the rom root).

    ``kanibako.data/global/rom`` — the ``canon/COLLECTION.md`` index + the whole
    ``canon/bible/`` book, which the launch path bind-mounts LIVE (ro) at
    ``~/canon/COLLECTION.md`` and ``~/canon/bible`` (see
    :func:`kanibako.settings.core_defaults.rom_default_categories`).  It is NOT
    copied/seeded to a host runtime dir, so it has no
    ``install``/``plan_template_refresh`` target; it is enumerated here only for the
    content DIGEST, so a drift in the shipped canon content is visible to the
    release-time check that requires the matching ``SETUP_FCV``/``SETUP_BCV`` bump
    (R-38 retired the host-side staleness gate that used to consume the digest).

    ⚑ Repointed from the retired ``global/rom/playbook/kanibako`` bundle root when
    rom became the canon: the rom ROOT is now the digest root, so a file added
    anywhere under ``rom/`` is watched.
    """
    try:
        ref = packaged_data_dir(*ROM_ROOT_PARTS)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


#: A plugin's packaged AGENT-STORE PAYLOAD dir.  ⚑ RENAMED ``data/template`` →
#: ``data/base`` (D4): the dir is stamped into the agent STORE ROOT
#: (``@config.agents/<agent>``), of which ``template/`` is only one entry — it also
#: carries ``canon/handbook/`` and may carry ``common/`` — so calling the whole thing
#: "template" named it after one of its children.
PLUGIN_STORE_PAYLOAD_DIRNAME = "base"

#: The pre-D4 payload dir a PUBLISHED-BUT-NOT-YET-UPDATED plugin still ships.
PLUGIN_LEGACY_PAYLOAD_DIRNAME = "template"

#: Where a LEGACY payload's content belongs in the new store layout: the old
#: ``data/template/`` held box-HOME files (``.claude.json``, ``.codex/config.toml``,
#: …) that the old seed copied straight to ``~``.
#:
#: ⚑⚑ IT MUST EQUAL LAYER 2's SOURCE, RESOLVED — i.e. what
#: ``@agent.<a>.template/box/home`` becomes once ``agent.<a>.template`` expands to
#: ``@config.agents/<harness>/template``.  That is ``<store>/template/box/home``, and
#: the ``template/`` prefix is the half that is easy to drop: a value of just
#: ``box/home`` lands the payload at ``agents/<name>/box/home/**``, which NOTHING
#: reads — so the transition arm would run, report nothing, and still leave the box
#: with no agent config.  Derived from the SAME constants the seed key is built from
#: (:data:`_SEED_SRC_HOME`, plus the ``template`` element of the layer-2 source key),
#: and pinned by a test that asserts the two are equal.
PLUGIN_LEGACY_PAYLOAD_DEST_REL = f"{AGENT_TEMPLATE_STORE_REL}/{_SEED_SRC_HOME}"


def _packaged_agent_store(agent_name: str) -> tuple[Path, bool] | None:
    """Locate a plugin's packaged AGENT-STORE payload → ``(path, is_legacy)``.

    Returns ``None`` if the plugin is not installed or ships neither payload dir
    (``no_agent`` / a third-party target without curated content).

    ⚑⚑ THE TRANSITION ARM, and why it is here rather than in a migration note.  The
    plugins publish INDEPENDENTLY of the base and pin no base version, so a NEW base
    beside an OLD plugin is not merely reachable — it is the ordinary
    ``pip install -U kanibako-cli`` outcome.  A new base looks for ``data/base``; an
    old plugin ships ``data/template``.  With no arm here that plugin contributes
    NOTHING, and the failure is silent and total: the box comes up with no agent
    config at all (no ``.claude.json``, no ``config.toml``, no ``config.yaml``).
    Same failure class, same reasoning and same remedy as the kickoff yield gate
    C-CANON R2 shipped.

    A legacy payload is installed to ``<store>/template/box/home``, which is exactly
    the dest that reproduces the old delivery byte-for-byte: the pre-restructure seed
    copied ``data/template/**`` to ``~``, and the new layer-2 source
    ``@agent.<a>.template/box/home`` resolves to that same subtree.

    ⚑ REMOVAL CONDITION — delete this arm (and ``PLUGIN_LEGACY_PAYLOAD_*``) once
    ``kanibako-agent-claude``, ``-codex`` and ``-goose`` have all PUBLISHED (not
    merely merged) releases carrying ``data/base``.  Recorded in migration M-12.
    """
    for dirname, legacy in (
        (PLUGIN_STORE_PAYLOAD_DIRNAME, False),
        (PLUGIN_LEGACY_PAYLOAD_DIRNAME, True),
    ):
        try:
            ref = importlib.resources.files(
                f"kanibako.plugins.{agent_name}"
            ).joinpath("data", dirname)
        except (ModuleNotFoundError, FileNotFoundError):
            return None
        path = Path(str(ref))
        if path.is_dir():
            return path, legacy
    return None


def ensure_agent_stores(
    std: StandardPaths, agent_names: "Iterable[str]",
) -> list[str]:
    """Materialise each agent's STORE — the J-6 **A-action**, one implementation.

    An A-action is INSTANTIATION: stamp a new store from the current host mould at
    the moment of the action, then the store is the user's.  J-6's "two paths, one
    action" pair share this one implementation — the deliberate trigger at ``kanibako
    setup`` (which reports) and the lazy backstop in ``cli._ensure_initialized``
    (silent, first run only).  Both must run the SAME full per-file stamp; the bare
    ``mkdir`` the lazy path used to do is what this replaces.

    Per name, in order:

    1. the MOULD — ``@system.template/agent`` → ``agents/<name>``.  Uniform for every
       agent, ``default`` included (J-5), and read AS IT STANDS so a user's mould
       customisation reaches FUTURE stores only.
    2. the SPECIFIC payload — ``agents/default`` gets the packaged
       ``template/agent_default`` DIRECTLY from the package (no host staging: with
       exactly one default agent, a staged copy would be read once and never again —
       the principle-2 dead-copy class); every other name gets its plugin's
       ``data/base``.
    3. the box-template SKELETON, guarantee-created (D7).

    ⚑ MOULD FIRST IS SAFE ONLY BECAUSE THE MOULD IS OVERLAP-FREE.  Every stamp is
    create-if-absent, so on an overlapping path the EARLIER copy wins — the mould
    would beat the specific content.  The mould therefore ships STRUCTURE ONLY (D5);
    if it ever gains content, this order must flip to specific-first.

    Create-if-absent per file makes the whole thing IDEMPOTENT and SELF-HEALING: a
    partially-written store completes at the next trigger, and a user's edits are
    never touched.  Fill-out happens AT ACTION TIME — the launch path CONSUMES
    stores and never creates template content.

    Returns the names whose store was touched, for the caller's report.
    """
    mould = std.template / AGENT_MOULD_DIRNAME
    base = _packaged_base_template()
    touched: list[str] = []
    for name in agent_names:
        store = std.agents / name
        store.mkdir(parents=True, exist_ok=True)
        # (1) the host mould — user-customizable, read at EVERY agent install.
        copy_tree(mould, store, scope="agent")
        # (2) the specific payload.
        if name == "default":
            if base is not None:
                copy_tree(base / PACKAGED_AGENT_DEFAULT, store, scope="agent")
        else:
            found = _packaged_agent_store(name)
            if found is not None:
                src, legacy = found
                if legacy:
                    # ⚑ ``dest_root=store`` is what makes ``scope="agent"`` correct
                    # here: the whitelist reads the STORE-relative path
                    # (``template/box/home/.claude.json`` → leading ``template/``,
                    # which the agent allow-list carries), not the source-relative
                    # one. With the dest_root omitted it would read ``.claude.json``
                    # and refuse a perfectly legal legacy payload.
                    copy_tree(
                        src, store / PLUGIN_LEGACY_PAYLOAD_DEST_REL,
                        dest_root=store, scope="agent",
                    )
                else:
                    copy_tree(src, store, scope="agent")
        # (3) the discoverable box-template skeleton (D7).
        for rel in _BOX_TEMPLATE_SKELETON:
            (store / rel).mkdir(parents=True, exist_ok=True)
        touched.append(name)
    return touched


def check_workset_template(std: StandardPaths, workset_path: Path) -> None:
    """PRE-FLIGHT the workset mould against the workset whitelist; write nothing.

    ``workset create`` must be ATOMIC in the way that matters to a user: either the
    workset exists and is well-formed, or nothing happened.  Refusing part-way
    through :func:`install_workset_template` satisfied "loud and leak-free" but left
    a REGISTERED workset with a root, its own ``settings.yaml`` and a PARTIAL chapter
    copy — recoverable only by ``workset rm``.

    So the check runs FIRST, before anything is registered or created — the same
    order ``workset.create_workset`` already uses for its name guards ("BEFORE any
    on-disk side effect below"), which is why this is a pre-flight rather than an
    unwind: it matches how this command already handles the class.
    """
    copy_tree(
        std.template / PACKAGED_WORKSET_TEMPLATE, workset_path,
        scope="workset", check_only=True,
    )


def install_workset_template(std: StandardPaths, workset_path: Path) -> None:
    """Stamp a NEW workset store from the host workset mould — the J-6 A-action.

    ``@system.template/workset`` → ``<workset_path>``, under the WORKSET whitelist
    (``template/`` + ``canon/handbook/`` only).  Called from ``workset create``; there
    was no template step there before, which is why the workset's handbook chapter
    and its own box template never existed.

    ⚑ The whitelist matters MOST here.  For a STANDALONE project ``<workset_path>``
    IS the user's own project directory — it holds their ``workspace/``, their
    ``vault/``, their ``auth/`` and the AUTHORITATIVE ``registry.yaml`` — so the
    deny-by-default predicate is guarding the tree they actually work in, not a
    kanibako-managed store.

    Create-if-absent, so re-running over an existing workset adds only what is
    missing.
    """
    src = std.template / PACKAGED_WORKSET_TEMPLATE
    copy_tree(src, workset_path, scope="workset")
    for rel in _BOX_TEMPLATE_SKELETON:
        (workset_path / rel).mkdir(parents=True, exist_ok=True)
    (workset_path / "canon" / "handbook").mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# ⚑⚑⚑ QUARANTINE — A NAMED SPECIAL CASE.  DO NOT COPY THIS SHAPE.
#
# Everything between here and ``install_packaged_templates`` is the BOX HANDBOOK
# host-template copy, and it is a DELIBERATE, RULED EXCEPTION to the live model.
#
#   THE LIVE MODEL for a host template is the SINGLE-SOURCE copy immediately
#   above — ``install_workset_template``: one mould, one ``copy_tree``, one
#   whitelist.  A new host template follows THAT.
#
#   THIS ONE stages THREE ordered layers.  Jei, 2026-08-07g, ruling on the
#   handbook specifically: *"Yes, handbook copy keeps all three layers. It is a
#   special case."*  It is the ONE place the single-source shape does not reach,
#   and he named that himself.  ⚑ It is NOT a pattern, NOT a precedent, and must
#   not be imitated for any other host template.
#
# WHY IT IS HERE AT ALL — the HOST/GUEST criterion (his ruling, verbatim): *"we
# should NOT be copying the handbook templates as if they are 'box' templates -
# they are system level templates that happen to be generated on behalf of a box,
# but crucially, they do not DIRECTLY interact with the box itself. That's the key.
# They are HOST templates, not GUEST templates."*  The box-HOME templates land in
# the very directory the guest sees at ``~`` ⇒ GUEST, and they stay in the
# ``seeded`` category.  The handbook templates fill a HOST location that a separate
# RO bind (``canon_hb_box``: ``@box.canon/handbook`` -> ``~/canon/handbook/box``)
# later reads ⇒ HOST, so they leave the category and are copied here instead.
#
# ⚑⚑ SINGLE-ROUTE IS INTACT, NOT BENT.  Single-route governs what enters A BOX.  A
# host template never enters one; what enters is the RO BIND, which remains an
# ordinary settings key.  Bespoke host-side copy + keyed bind = single route.
#
# ⚑⚑ AND THE THREE LAYERS ARE NOT REDUNDANT WITH THE CHAPTER BINDS — measured, so
# that nobody "simplifies" them to one source.  The five ``canon_hb_*`` binds read
# each scope's OWN canon store (``@system.canon/handbook``,
# ``@agent.<active>.canon/handbook``, ``@workset.canon/handbook``).  These three
# layers read each scope's TEMPLATE subdir (``<scope>.template/box/canon/handbook``)
# — *what that scope wants in a NEW BOX's own chapter*.  DIFFERENT TREES.
# Collapsing to one source would silently DROP the agent's and the workset's
# contributions to the box chapter.
# ---------------------------------------------------------------------------


def handbook_layer_source_keys(
    proj: ProjectPaths, agent_id: str | None
) -> tuple[str, ...]:
    """The ORDERED dotted SOURCE keys whose values root the three handbook layers.

    ``system.template`` -> ``agent.<a>.template`` -> ``workset.template``, lowest
    layer first, gated exactly as the box-home layers are gated (no agent bound ->
    no agent layer; STANDALONE has no workset tier -> no workset layer).

    ⚑ DERIVED FROM :func:`template_seed_defaults`, not restated beside it, and that
    is the whole point of the function: the per-agent / per-workset SOURCE scalars
    are declared THERE, so the gate that decides whether a layer exists is read from
    the one table rather than re-implemented here where it could drift.
    ``system.template`` is not in that table because it is already floor-
    materialized (a ``system.*`` settings-tier path), so it is named directly.

    ⚑ THESE STAY KEYS.  They are separate declared SOURCE keys — NOT ``seeded``
    entries — and they carry the user's repoint route (``config set
    workset.template`` reroutes this copy, pinned by the repoint tests).  The
    handbook copy leaving the ``seeded`` category does not make its sources any less
    keyed: the caller resolves these off the launch snapshot and passes the resolved
    roots in.  Nothing here hardcodes a path.
    """
    defs = template_seed_defaults(proj, agent_id)
    keys = ["system.template"]
    agent_key = f"agent.{agent_id}.template" if agent_id else None
    if agent_key is not None and agent_key in defs:
        keys.append(agent_key)
    if "workset.template" in defs:
        keys.append("workset.template")
    return tuple(keys)


def install_box_handbook_template(
    dest: Path, layer_roots: Iterable[Path],
) -> None:
    """Fill a NEW box's OWN handbook chapter from the three host template layers.

    ⚑⚑⚑ **THE SPECIAL CASE.  READ THE QUARANTINE BLOCK ABOVE BEFORE COPYING ANY OF
    THIS.**  The live host-template shape is :func:`install_workset_template`
    (single source); three ordered layers here are a RULED exception to it.

    *dest* is the resolved ``@box.canon/handbook`` — the box store's CONTRIBUTION
    root, which lives OUTSIDE the box home and is the SOURCE of the RO
    ``canon_hb_box`` bind.  *layer_roots* are the RESOLVED ``<scope>.template``
    roots in apply order (system -> agent -> workset), each of which contributes its
    ``box/canon/handbook`` subtree.

    ⚑ THE ROOTS ARE PARAMETERS, DELIBERATELY.  They are settings keys
    (:func:`handbook_layer_source_keys`) and are resolved at the seam that already
    holds the launch snapshot; this function neither re-resolves them nor keeps
    module state, so nothing here can disagree with what the snapshot said.

    SEED-ONCE / CREATE-IF-ABSENT: the layers are merged per-file last-wins in a temp
    dir and then copied in CREATE-IF-ABSENT (:func:`stage_layers`), so a re-create
    into a leftover box store never overwrites a chapter the user has edited.  That
    failsafe answers a shipped data-loss bug; it is not refactorable away.
    SKIP-IF-ABSENT: a layer whose ``box/canon/handbook`` dir does not exist is
    skipped (an unpopulated ``@workset.template`` is the normal case).

    GUARANTEE-CREATE, and it is a real (intended) behaviour change, so it is stated
    rather than left to be discovered: the ``mkdir`` below is UNCONDITIONAL, so
    ``@box.canon/handbook`` exists after every create even when all three layers are
    empty or absent.  The RO ``canon_hb_box`` bind is declared ``optional: true``,
    i.e. omitted when its source is missing — so it now ALWAYS mounts, and a user who
    has emptied all three handbook template subtrees gets an EMPTY read-only mount
    where the bind used to be dropped.  :func:`install_workset_template` guarantee-
    creates its own ``canon/handbook`` the same way for the same reason (the chapter
    is a place the user is expected to fill later, not an artefact of the template).

    ⚑ NO DEST WHITELIST HERE, and that is deliberate — do not "restore" one.  There
    is ONE dest policy on this path: ``_host_copy_dest``'s warn-and-skip at the
    caller.  A ``scope="box"`` :data:`SCOPE_WHITELISTS` check would be a SECOND
    SPELLING OF THE SAME CONDITION (CONVENTIONS §0), because it could only ever fire
    on the DEST: the dest is key-fixed at ``@box.canon/handbook`` and
    :func:`stage_layers` builds its relative paths by ``rglob`` UNDER the staged tree,
    so layer CONTENT cannot reach a non-whitelisted top-level entry of the box store
    no matter what a template ships.  Two checks on one condition, disagreeing about
    severity (raise vs skip), is worse than one.
    ⚑ This is where it differs from :func:`install_workset_template`, whose whitelist
    guards something real: that mould lands at ``<workset_path>`` — for a STANDALONE
    project, the user's OWN directory — where template CONTENT could plant a
    ``settings.yaml`` or a ``registry.yaml``.  Its whitelist is not an oversight
    missing here; the two copies simply have different attack surfaces.

    NO PRE-FLIGHT TWIN, decided explicitly (contrast :func:`check_workset_template`).
    ``workset create`` needs one because :func:`install_workset_template` can REFUSE
    part-way, leaving a REGISTERED workset only ``workset rm`` can clean up.  This
    copy has no refusal to pre-flight: the one dest policy is warn-and-skip, so a
    misdeclared ``box.canon`` costs the box its handbook chapter and nothing else,
    and the box is left well-formed.  A second validator would be a second copy of
    rules with nothing to prevent, free to drift.
    """
    dest.mkdir(parents=True, exist_ok=True)
    stage_layers(dest, [Path(root) / _SEED_SRC_HANDBOOK for root in layer_roots])


def install_packaged_templates(
    std: StandardPaths, agent_names: list[str], refresh: bool = False,
) -> None:
    """Install the packaged content into its host stores — an ENUMERATED set (P-S2).

    Four destination pairs, each named because each has a different OWNER and
    therefore a different copy rule:

    ====================================== ======================================
    packaged subtree                        host destination
    ====================================== ======================================
    ``template/box``                        ``@system.template/box``   (STAGING)
    ``template/workset``                    ``@system.template/workset`` (STAGING)
    *(none — ships empty, D5)*              ``@system.template/agent``  (STAGING)
    ``template/handbook``                   ``@system.canon/handbook``  (USER-OWNED)
    ``template/agent_default`` + plugins    ``@config.agents/<name>``   (USER-OWNED)
    ====================================== ======================================

    ⚑⚑ ``refresh=True`` (the ``kanibako setup`` TRUE-REFRESH) reaches the STAGING
    rows ONLY.  The user-owned rows are create-if-absent on EVERY path — J-3 item 1:
    *"user-owned canon stores are NEVER overwritten by any implicit path"*, and §2a's
    *"a package upgrade must not silently revert their edits"*.  Their differences
    are REPORTED instead (:func:`plan_template_refresh`'s ``kept`` list), and an
    explicit opt-in update verb is the future mechanism for actually applying them.
    That split is the whole point of J-6's action taxonomy: a B-action (template
    update) changes what FUTURE instantiations get and never touches an existing
    store; only a C-action (instance update), which does not exist yet and will never
    be implicit, may rewrite one.

    The agent-agnostic box guide (the bible's ``ROM_GENERAL.md``) is NOT installed
    here — it is delivered LIVE from the read-only packaged canon (bound at
    ``~/canon/bible/general`` + flattened into each agent's native instruction slot at
    launch), so it has no host runtime-install target.
    """
    base_src = _packaged_base_template()
    if base_src is not None:
        # STAGING (system-owned): the box + workset moulds, refreshable.
        #
        # ⚑ SCOPED, and this is where J-2's box whitelist actually BITES. The mould
        # MIRRORS the store it stamps, so it is subject to that store's whitelist at
        # the moment it is staged — which is the earliest point a planted
        # ``settings.yaml`` (= ``meta.box.settings``, the LAST cascade level) can be
        # REFUSED rather than carried forward. Unscoped, the deny-list would only be
        # dead prose: nothing downstream re-checks it, because the two downstream
        # copies (the box-home seed and the box-handbook host template) read
        # ``box/home`` and ``box/canon/handbook`` directly.
        copy_tree(
            base_src / PACKAGED_BOX_TEMPLATE,
            std.template / PACKAGED_BOX_TEMPLATE,
            overwrite=refresh, scope="box",
        )
        copy_tree(
            base_src / PACKAGED_WORKSET_TEMPLATE,
            std.template / PACKAGED_WORKSET_TEMPLATE,
            overwrite=refresh, scope="workset",
        )
        # USER-OWNED: the system handbook — create-if-absent ALWAYS (J-3 item 1).
        # ⚑ UNSCOPED on purpose: the dest is INSIDE the canon root, not a scope store
        # root, so there is no store whitelist to apply (the equivalent guarantee is
        # that the dest is key-fixed).
        copy_tree(
            base_src / PACKAGED_HANDBOOK, std.canon / "handbook",
        )
    # The agent MOULD dir exists even though nothing packages it (D5/D7).
    (std.template / AGENT_MOULD_DIRNAME).mkdir(parents=True, exist_ok=True)
    # USER-OWNED: the agent stores — the A-action, default included.
    ensure_agent_stores(std, ["default", *agent_names])


def _is_shipped_content(entry: Path) -> bool:
    """True iff *entry* is a real shipped file (not a build/editor artifact).

    Build and editor junk (``__pycache__``/``*.pyc``, ``.DS_Store``) never ships in
    a wheel, so hashing it would make the content digest non-deterministic across
    environments/Python versions and report drift that never shipped.  The filter
    was written when the RO bundle still carried the flattener ``.py`` beside the
    guide (a dev checkout, or the repo's own suite ``exec_module``-ing it, dropped a
    ``__pycache__`` right in the digest source).  The canon ships NO ``.py`` at all
    now — the flattener moved into the package proper — but the filter stays as the
    general junk guard for every packaged content tree it walks.
    """
    if not entry.is_file():
        return False
    if "__pycache__" in entry.parts:
        return False
    if entry.suffix in (".pyc", ".pyo"):
        return False
    if entry.name == ".DS_Store":
        return False
    return True


def walk_shipped_files(root: Path) -> list[tuple[str, Path]]:
    """Return the SORTED ``(posix-relpath, file-path)`` shipped-file list under *root*.

    The ONE traversal shared by the two consumers of a packaged content tree — the
    staleness DIGEST (:func:`_packaged_manifest_entries`) and the canon rom emitter
    (:func:`kanibako.settings.core_defaults.rom_default_categories`, which no longer binds
    per file but still walks the rom root for its fail-closed guards).  Walks
    *root* recursively, keeps only real SHIPPED files (:func:`_is_shipped_content`
    drops ``__pycache__``/``.pyc``/``.DS_Store`` build-and-editor junk), and returns
    each survivor as ``(<root-relative posix path>, <absolute path>)`` SORTED by the
    relative path so the enumeration is deterministic across machines and Python
    filesystem-walk order.
    """
    files: list[tuple[str, Path]] = []
    for entry in root.rglob("*"):
        if _is_shipped_content(entry):
            files.append((entry.relative_to(root).as_posix(), entry))
    files.sort(key=lambda item: item[0])
    return files


def _packaged_manifest_entries(agent_names: list[str]) -> list[tuple[str, bytes]]:
    """Return the SORTED ``(namespaced-path, file-bytes)`` content manifest.

    Enumerates every packaged file the setup gate must watch — the base seed tree
    (``_packaged_base_template``), each installed agent's store payload
    (``_packaged_agent_store``), AND the RO packaged canon
    (``_packaged_shared_bundle``, which is bind-mounted rather than installed but
    still needs drift detection; it carries the box guide at
    ``canon/bible/general/directives/ROM_GENERAL.md``).  Each file contributes ONE
    ``(namespaced-relative-path, file-bytes)`` pair under a source-distinct
    prefix (``base/`` / ``shared/`` / ``agent/<name>/``), so no file is
    double-counted; the pairs are SORTED so the manifest is deterministic across
    runs and machines regardless of filesystem walk order.
    """
    entries: list[tuple[str, bytes]] = []

    base_src = _packaged_base_template()
    if base_src is not None:
        for rel, entry in walk_shipped_files(base_src):
            entries.append((f"base/{rel}", entry.read_bytes()))

    # The RO packaged canon (bound live at ~/canon/{COLLECTION.md,bible}, never
    # installed) is enumerated here ONLY so the setup gate still trips when the
    # shipped canon content drifts — it has no install target.  It is the SOLE
    # source of the box guide in this manifest (the retired ``@system.instructions``
    # flat-copy no longer contributes a second entry).  Both this digest and the
    # canon emitter now walk the SAME rom ROOT, so the namespaced keys read
    # ``shared/canon/...``.
    bundle_src = _packaged_shared_bundle()
    if bundle_src is not None:
        for rel, entry in walk_shipped_files(bundle_src):
            entries.append((f"shared/{rel}", entry.read_bytes()))

    for agent_name in sorted(agent_names):
        found = _packaged_agent_store(agent_name)
        if found is None:
            continue
        agent_src, _legacy = found
        for rel, entry in walk_shipped_files(agent_src):
            entries.append((f"agent/{agent_name}/{rel}", entry.read_bytes()))

    entries.sort(key=lambda item: item[0])
    return entries


def packaged_templates_digest(agent_names: list[str]) -> str:
    """Return a content-manifest sha256 over the packaged template src trees.

    A CONTENT hash over :func:`_packaged_manifest_entries`, not a version marker:
    it moves ONLY when packaged template content actually changes, and it is immune
    to the ``setup_completed`` silent forward-bump that would mask template drift.

    ⚑ NO RUNTIME CONSUMER since R-38 (verified 2026-08-02: the host-side
    ``template_staleness_gate`` and both stamp writers were deleted; the only callers
    left in-tree are this module's own tests).  It is kept for the RELEASE-TIME
    check — compare the digest against the previous tag and REQUIRE the matching
    ``SETUP_FCV``/``SETUP_BCV`` bump — which is planned CI work (plan step C2) and is
    NOT wired yet.
    """
    digest = hashlib.sha256()
    for key, data in _packaged_manifest_entries(agent_names):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# The J-3 REPORTING classifier — three tiers, keyed by suffix.
#
# ⚑ REPORTING ONLY. It never decides whether to copy: create-if-absent (or the
# staging refresh) already decided that. Its whole job is to keep the setup report
# HONEST — spec §2a: *"report a skip ONLY where the packaged file DIFFERS"*, because
# reporting every skip trains the user to ignore the output, which costs exactly the
# signal the report exists to carry.
#
# ⚑ ACCEPTED CONSEQUENCE (J-3 item 5): a comment-only upstream change compares
# EQUIVALENT and goes unreported today. The ``[STOCK]`` comment convention is what
# makes comment-aware handling possible later, in the explicit opt-in update verb.
# ---------------------------------------------------------------------------

#: HTML comments, non-greedy, across lines — stripped by the MD normaliser because
#: both equivalence tiers ignore comments (J-3 item 5).
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

#: A fenced code block (``` or ~~~), captured whole so the normaliser can leave its
#: interior ALONE: whitespace is SEMANTIC inside a fence.
_FENCE_RE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n.*?(\n\2[^\n]*(?=\n|$))", re.DOTALL)


def _normalise_markdown(text: str) -> str:
    """Normalise *text* for the MD equivalence tier — CONSERVATIVELY.

    Strips HTML comments, then collapses insignificant whitespace: CRLF → LF,
    trailing whitespace per line, runs of blank lines, and the final newline.

    ⚑ TWO THINGS ARE DELIBERATELY LEFT ALONE, because in markdown they are not
    whitespace but SYNTAX:

    * the interior of a FENCED CODE BLOCK — indentation and blank lines there are
      content;
    * a TRAILING TWO-SPACE hard line break — stripping it would merge two lines.

    Anything this misses only costs a spurious "different" report, never a wrong
    copy; anything it over-normalises would HIDE a real change, which is why the
    bias is conservative.
    """
    fences: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        fences.append(match.group(0))
        return f"\x00FENCE{len(fences) - 1}\x00"

    text = text.replace("\r\n", "\n")
    text = _HTML_COMMENT_RE.sub("", text)
    text = _FENCE_RE.sub(_stash, text)
    lines = [
        line if line.endswith("  ") and line.strip() else line.rstrip()
        for line in text.split("\n")
    ]
    out: list[str] = []
    for line in lines:
        if not line and out and not out[-1]:
            continue  # collapse blank-line runs
        out.append(line)
    text = "\n".join(out).strip("\n")
    for i, fence in enumerate(fences):
        text = text.replace(f"\x00FENCE{i}\x00", fence)
    return text


def _equivalent(src_file: Path, target: Path) -> bool:
    """True when *target* is byte-equal to, or EQUIVALENT to, *src_file*.

    ONE strategy table keyed by suffix (J-3 item 2):

    * ``.yaml`` / ``.yml`` — ``safe_load`` both sides and deep-compare. A parse
      failure on EITHER side ⇒ "different" (never equivalent: an unparseable file is
      exactly the case a report should surface).
    * ``.md`` — :func:`_normalise_markdown` both sides and compare.
    * anything else — bytes only.
    """
    try:
        src_bytes = src_file.read_bytes()
        target_bytes = target.read_bytes()
    except OSError:
        return False
    if src_bytes == target_bytes:
        return True
    suffix = src_file.suffix.lower()
    if suffix in (".yaml", ".yml"):
        import yaml

        try:
            return yaml.safe_load(src_bytes.decode()) == yaml.safe_load(
                target_bytes.decode()
            )
        except (yaml.YAMLError, UnicodeDecodeError):
            return False
    if suffix == ".md":
        try:
            return _normalise_markdown(src_bytes.decode()) == _normalise_markdown(
                target_bytes.decode()
            )
        except UnicodeDecodeError:
            return False
    return False


def plan_template_refresh(
    std: StandardPaths, agent_names: list[str],
) -> tuple[list[Path], list[Path], list[Path]]:
    """Classify every packaged src file by its host target for the setup preview.

    Returns ``(added, overwritten, kept)`` lists of HOST target paths:

    * ADDED — the packaged file has no host counterpart yet (create).
    * OVERWRITTEN — a STAGING file (``@system.template/**``, system-owned) exists and
      DIFFERS from the packaged version, so a true-refresh replaces it.
    * KEPT — a USER-OWNED file (``@system.canon/handbook/**``, ``@config.agents/**``)
      exists and DIFFERS. It is NEVER overwritten (J-3 item 1); it is reported so the
      user knows an upstream version moved on and their copy stayed.
    * current — byte-equal OR :func:`_equivalent`; in NO list, deliberately
      unreported (an identical file is not a "skip", and neither is one that differs
      only in comments or insignificant whitespace).

    User-only files never appear (they are not in the packaged src loop). This is a
    pure classification (no writes) driving the ``kanibako setup`` preview.
    """
    added: list[Path] = []
    overwritten: list[Path] = []
    kept: list[Path] = []

    def _classify(src_file: Path, target: Path, *, user_owned: bool) -> None:
        if not target.exists():
            added.append(target)
        elif not _equivalent(src_file, target):
            (kept if user_owned else overwritten).append(target)
        # else: current (byte-equal or equivalent) -> unreported.

    def _walk(src: Path | None, dest: Path, *, user_owned: bool) -> None:
        if src is None or not src.is_dir():
            return
        for entry in sorted(src.rglob("*")):
            if entry.is_file() and not entry.is_symlink():
                _classify(entry, dest / entry.relative_to(src), user_owned=user_owned)

    base_src = _packaged_base_template()
    if base_src is not None:
        # STAGING (refreshable).
        _walk(
            base_src / PACKAGED_BOX_TEMPLATE,
            std.template / PACKAGED_BOX_TEMPLATE, user_owned=False,
        )
        _walk(
            base_src / PACKAGED_WORKSET_TEMPLATE,
            std.template / PACKAGED_WORKSET_TEMPLATE, user_owned=False,
        )
        # USER-OWNED (create-if-absent; differences reported, never written).
        _walk(base_src / PACKAGED_HANDBOOK, std.canon / "handbook", user_owned=True)
        _walk(
            base_src / PACKAGED_AGENT_DEFAULT, std.agents / "default", user_owned=True,
        )

    for agent_name in agent_names:
        found = _packaged_agent_store(agent_name)
        if found is None:
            continue
        agent_src, legacy = found
        store = std.agents / agent_name
        dest = store / PLUGIN_LEGACY_PAYLOAD_DEST_REL if legacy else store
        _walk(agent_src, dest, user_owned=True)

    return added, overwritten, kept
