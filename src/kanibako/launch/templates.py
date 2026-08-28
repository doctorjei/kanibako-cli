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

from kanibako.settings.agent_config import store_dirname
from kanibako.settings.core_defaults import ROM_ROOT_PARTS, packaged_data_dir

if TYPE_CHECKING:
    from kanibako.settings.paths import ProjectPaths, StandardPaths


# Layered box seed (spec §2a): three ordered sources — system -> agent -> workset,
# later overlays earlier — into the box store's ONE seeded destination at create.
# Full model in llm-docs/kanibako/launch/templates.py.md § the layered box seed.
#
# ⚑⚑ THERE ARE NO HANDBOOK LAYERS HERE, AND THIS IS NOT AN OVERSIGHT.  Jei ruled
# them OUT of the category 2026-08-07g and §2a no longer declares them; the box's
# own handbook chapter is filled by :func:`install_box_handbook_template`.  Read
# the QUARANTINE block above that function before touching it.

#: The box home seed destination — the GUEST home, RESOLVED to the box store when
#: the copy runs.  ⚑ It is the ONLY one: SEED DESTINATIONS ARE ENUMERATED, NEVER A
#: WHOLE-DIRECTORY COPY, or a template could plant ``<box_dir>/box.yaml`` (=
#: ``meta.box.settings``, the LAST cascade level).
#: ⚑ Do not respell this host-side again; the ``dest_space`` discriminator that made
#: the old ``@meta.box.path/home`` spelling safe is gone (2026-08-08c).
_SEED_DEST_HOME = "~/"

#: The per-layer SOURCE subpaths under each layer's ``template`` root.  The two-level
#: ``box/`` is the declared WHITELIST BOUNDARY (J-2).  ⚑ Only ``home`` is a SEED
#: source; ``canon/handbook`` is read by the host-side copy (2026-08-07g), which is
#: why the two constants no longer sit in the same table.
_SEED_SRC_HOME = "box/home"
_SEED_SRC_HANDBOOK = "box/canon/handbook"

#: The ``template`` entry of a SCOPE STORE.  ⚑ Two things must agree on it: the
#: layer-2/3 source key defaults, and the whitelist entry that permits it.
AGENT_TEMPLATE_STORE_REL = "template"


def template_seed_defaults(
    proj: ProjectPaths, agent_id: str | None
) -> dict[str, object]:
    """Return the layered box-seed DEFAULT-category table (spec §2a — THREE layers).

    ⚑ The SOURCE scalars declared here are shared with the box HANDBOOK
    host-template copy (:func:`handbook_layer_source_keys`), which is gated by them.
    """
    from kanibako.channels.channels import has_workset_channels

    def _layer(source_root: str) -> dict[str, object]:
        # ⚑ A DEST-KEYED map, not a named entry (2026-08-08c): the destination IS
        # the identity and the value is the 1-element ``(src,)`` — ``opts`` is
        # RESERVED on a COPY and no shipped layer sets it.
        return {_SEED_DEST_HOME: (f"{source_root}/{_SEED_SRC_HOME}",)}

    defs: dict[str, object] = {"system.seeded": _layer("@system.template")}
    if agent_id:
        # The §2d DEFAULT-TIER arm of the same SOURCE key — the all-agents
        # fallback, sibling of ``agent.default.canon`` (spec :1143 + :1123 +
        # :1116, the composition the spec performs in prose at :1144).  ⚑ It is
        # a DECLARED key, so an artefact has to carry its value or ``system
        # defaults`` prints a row it cannot source; emitting it here rather than
        # beside the canon arm keeps the template family's value in the module
        # that owns ``AGENT_TEMPLATE_STORE_REL``.
        # ⚑ INERT FOR DELIVERY, and that is the point: the node arm below is
        # emitted unconditionally for every agent, so the §2d fallback to this
        # arm never fires.
        # ⚑ NO NODE-STORE PROBE, unlike ``canon_default_categories``' node arm
        # (``store_canon if node_store.is_dir() else …``): that conditional is
        # the canon key's own behaviour, not this family's.
        defs["agent.default.template"] = (
            f"@config.agents/default/{AGENT_TEMPLATE_STORE_REL}"
        )
        # SOURCE key (spec §2a/§2d), not a hardcoded path: settable, so a user
        # override reroutes the layer by cascade precedence.
        # ⚑⚑ NODE-ROOTED, NEVER THE HARNESS (ruled 2026-08-27).  §2d and the
        # manifest both give ``agent.<agent>.template =
        # @meta.agent.<agent>.path/template``, and ``<agent>`` is the ACTIVE NODE:
        # a persona seeds from its OWN store.  This used to spell
        # ``harness_of(agent_id)`` — the same string for a bare agent, and for a
        # persona a SILENT divergence: ``@config.agents/<harness>/template`` is a
        # perfectly resolvable directory that simply names the WRONG store, so
        # nothing anywhere had cause to complain.  (⚑ The silence is NOT about
        # ``meta_agent_path_floor`` materializing both anchors — this arm never
        # went through ``@meta.agent.<a>.path`` at all.)  The harness's CONTENT
        # still reaches a persona: the shim
        # ``commands.start.ensure_persona_share_symlinks`` links
        # ``agents/<node>/template`` -> ``agents/<harness>/template``, exactly as it
        # already does for ``common``.  SHARED BY LINK, not by copy — a copy would
        # need keeping fresh, and a persona that wants its own template simply
        # replaces the link with a real directory.
        # ⚑ ONE HOP UNROLLED, like the ``canon`` node arm
        # (``core_defaults.canon_default_categories``): ``meta.agent.<a>.path`` IS
        # ``@config.agents/<a>`` (``settings_launch.meta_agent_path_floor`` defines
        # it as that literal), so the two spellings resolve to one place.
        # ⚑ STILL NO NODE-STORE PROBE (see the ``agent.default.template`` note
        # above): the shim guarantees the node's ``template`` entry exists, and
        # ``stage_layers`` is skip-if-absent for the case where it does not.
        # ⚑ KEY vs DIRECTORY: the key segment stays the CANONICAL node, the value is
        # a store path and takes the ``+`` dirname (``agent_config.store_dirname``).
        defs[f"agent.{agent_id}.template"] = (
            f"@config.agents/{store_dirname(agent_id)}/{AGENT_TEMPLATE_STORE_REL}"
        )
        defs[f"agent.{agent_id}.seeded"] = _layer(f"@agent.{agent_id}.template")
    if has_workset_channels(proj):
        # SOURCE key (spec §2c). STANDALONE (no workset channels) omits BOTH the
        # source and the layer — its workset tier is <None>.
        defs["workset.template"] = (
            f"@meta.workset.path/{AGENT_TEMPLATE_STORE_REL}"
        )
        defs["workset.seeded"] = _layer("@workset.template")
    return defs


# ⚑ ``seed_keys_of(defs)`` USED TO LIVE HERE and is GONE (2026-08-08c).  The dest
# above is ``~/``, a GUEST path, so there is no host destination left to
# discriminate and nothing for a key set to select.  Do not rebuild one under
# another name — see the llm-doc for what it did and how it broke.


def stage_layers(dest: Path, layers: list[Path]) -> None:
    """Seed *dest* once from the ordered *layers* via a TEMP staging dir.

    ⚑ SEED-ONCE.  The merge is per-file last-wins WITHIN staging, but the staged
    tree lands in *dest* CREATE-IF-ABSENT — a pre-existing *dest* file is NEVER
    overwritten.  That failsafe is the load-bearing guard against re-seed DATA
    LOSS; the caller invokes this only at box CREATE, never on a relaunch.
    SKIP-IF-ABSENT: a *layers* entry that is not an existing directory is skipped.
    """
    from kanibako.errors import TemplateScopeError

    present = [layer for layer in layers if layer.is_dir()]
    if not present:
        return
    with tempfile.TemporaryDirectory(prefix="kanibako-seed-") as staging:
        staged = Path(staging)
        for layer in present:
            for entry in sorted(layer.rglob("*")):
                # SOURCE SYMLINK refusal (spec §2a point 3), checked HERE as well as
                # in ``copy_tree``: ``is_file()`` below FOLLOWS a symlink-to-file, so
                # the exfiltrated bytes would already be staged by the time the shared
                # copier saw them (as a plain file).
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
# THE ONE COPIER — every template/seed/store fill goes through here (P-S4), so the
# whitelist and traversal defences cannot be present on one path and missing on
# another.  The packaged-install set it serves is enumerated in the llm-doc.
# ---------------------------------------------------------------------------

#: Per-SCOPE store-root WHITELISTS (spec §2a, ruled by Jei 2026-07-30).  Each
#: packaged per-scope template subtree MIRRORS THAT SCOPE'S STORE ROOT, so the
#: whitelist is exactly the set of top-level entries that subtree may contain.
#: ⚑ DENY-BY-DEFAULT: anything not listed is an ERROR.  What each scope denies, and
#: why (``box.yaml`` at box scope IS the last cascade level; ``registry.yaml``
#: at workset scope IS the authoritative box membership), is in the llm-doc.
#: ⚑ The sets are NOT one uniform rule — ``common/`` is AGENT-only, and only
#: ``canon/handbook`` is seedable, never ``canon/`` wholesale.
#: ⚑⚑ THE WORKSET ROW IS THE DEFAULT SPELLING, NOT THE ONLY ONE.  Its two entries are
#: ``workset.template`` and ``workset.canon``, both repointable, so the workset stamp
#: respells them per root through :func:`_workset_scope_allowed` — which reads its
#: defaults from HERE.  A repoint MOVES an entry; it never adds one.
SCOPE_WHITELISTS: dict[str, tuple[str, ...]] = {
    "box": ("home", "canon/handbook"),
    "agent": ("template", "canon/handbook", "common"),
    "workset": ("template", "canon/handbook"),
}


def _check_whitelist(store_rel: Path, scope: str,
                     allowed: tuple[str, ...] | None = None) -> None:
    """RAISE unless *store_rel*'s leading components are inside *scope*'s allow-list.

    ⚑ *store_rel* is relative to the SCOPE STORE ROOT (``copy_tree``'s *dest_root*),
    NOT to the copy's source.  They diverge whenever a copy targets a subdirectory of
    a store, and checking the wrong one would either refuse a legal copy or wave
    through an illegal one.

    ⚑ *allowed* overrides :data:`SCOPE_WHITELISTS` for ONE call, and it exists for one
    reason: two of the workset entries are REPOINTABLE KEYS, so the entry a resolved
    dest must match is not always the entry the default table spells.  It RESPELLS the
    scope's entries, it never adds to them — see :func:`_workset_scope_allowed`.
    """
    from kanibako.errors import TemplateScopeError

    if allowed is None:
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

    ⚑ ``resolve()`` on both sides is load-bearing: it is what makes a symlinked
    intermediate DIRECTORY visible, which a plain string comparison would not see.
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
    allowed: tuple[str, ...] | None = None,
) -> None:
    """Copy every file under *src* into *dest*, skipping files that exist.

    **The ONE copier** (P-S4).  Create-if-absent, so user edits to a seeded template
    survive a later kanibako upgrade.  *dest_root* (default *dest*) is BOTH the
    containment boundary and the whitelist's frame of reference; *scope* turns on
    the §2a whitelist; *check_only* is the PRE-FLIGHT form, running all four guards
    below and writing nothing.  *allowed* RESPELLS that whitelist for one call and is
    the workset stamp's only user (:func:`_workset_scope_allowed`); it never widens a
    scope, and ``None`` means the declared table.

    ⚑ *overwrite* is confined to the SYSTEM-OWNED packaged STAGING
    (``@system.template/**``).  User-owned stores — ``@system.canon/handbook``,
    ``@config.agents/**``, worksets, boxes — are create-if-absent on EVERY path,
    always (J-3 item 1), and the alias :data:`copy_resource_tree_if_absent` reused by
    the box-SEED apply must NEVER clobber a per-box home file.

    ⚑ *check_only* is the SAME function deliberately; a separate validator would be a
    second copy of the rules, free to drift.  Pair it with the real call, never use
    it as a substitute (nothing here is atomic against a concurrent writer).

    The llm-doc carries the four §2a enforcement points and why each is placed where
    it is.
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
            _check_whitelist(target.relative_to(root), scope, allowed)
        # (2) BEFORE ANY WRITE, INCLUDING THE mkdir: creating the parent first would
        # litter directories outside the subtree on the very path we are refusing.
        _assert_contained(target.parent, root, what="copy destination directory")
        # (4) A SYMLINKED FINAL TARGET, by lstat, BEFORE the two branches below —
        # both of which follow it.  The parent check cannot see this one; the escape
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
            # PREVIEW AND ACTION MUST TELL ONE TRUTH: ``plan_template_refresh`` calls
            # an EQUIVALENT file "current" and does not report it, so rewriting its
            # bytes here would silently revert a user edit the preview called
            # unchanged.  Same classifier, same verdict, both sides.
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(entry), str(target))


# The create-if-absent spelling other modules (e.g. the seed-once apply in
# commands.start) reuse; skip-if-present is what the longer name asserts.
copy_resource_tree_if_absent = copy_tree


#: Subtrees of the packaged template root, by their role.  ⚑ The install is an
#: ENUMERATED set of (packaged subtree → host dest) pairs, NEVER a whole-tree copy
#: (P-S2): copying the root wholesale would leave a SECOND, never-read copy of the
#: handbook at ``@system.template/handbook``.
PACKAGED_BOX_TEMPLATE = "box"
PACKAGED_WORKSET_TEMPLATE = "workset"
PACKAGED_AGENT_DEFAULT = "agent_default"
PACKAGED_HANDBOOK = "handbook"

#: The AGENT MOULD's dir name under ``@system.template`` — the host copy every agent
#: install stamps from (J-5).  ⚑ There is deliberately NO packaged ``template/agent``
#: directory: the mould ships EMPTY (D5), so the host dir is GUARANTEE-CREATED by the
#: install action (D7).  Shipping structure only is what keeps it OVERLAP-FREE with
#: ``agent_default`` — see :func:`ensure_agent_stores`.
AGENT_MOULD_DIRNAME = "agent"

#: The box-template SKELETON a scope store gets guarantee-created (D7) so the shape
#: is discoverable.  ⚑ Spelled to the SPEC shape — ``home/canon/{notebook,workbook}``,
#: NOT the samples' ``home/{notebook,workbook}`` (D6 calls that a sample-tree
#: oversight).
#: ⚑⚑ RELATIVE TO THE TEMPLATE DIR, not to the store root, and that is exactly what
#: lets its TWO consumers spell that dir differently: at an AGENT store it is the fixed
#: leaf :data:`AGENT_TEMPLATE_STORE_REL`; at a WORKSET root it is whatever the
#: repointable ``workset.template`` RESOLVES to.  Fold the ``template/`` prefix back in
#: here and the workset half silently ignores that key again.
_BOX_TEMPLATE_SKELETON = (
    "box/home/canon/notebook",
    "box/home/canon/workbook",
    "box/canon/handbook",
)

#: The CANON HALF of the workset stamp ON THE MOULD SIDE — the subtree of
#: ``@system.template/workset`` the canon-only stamp reads.  ⚑ A MOULD-LAYOUT literal,
#: and it stays one: the mould is a SYSTEM-tier tree that every workset stamps from, so
#: one workset's ``workset.canon`` repoint moves the DESTINATION and never the source.
_MOULD_CANON_ROOT = "canon"

#: The chapter leaf under a canon root, guarantee-created (D7) in EVERY mode.  ⚑ Spec
#: ``:962``: ``workset.canon`` is *"UNIFORM IN EVERY MODE — deliberately NOT a
#: per-mode key"*, so a lone standalone box has this tier too.  Its sibling half — the
#: ``template/`` skeleton above — does NOT transfer: ``workset.template`` is <None> in
#: standalone (spec ``:936``; :func:`template_seed_defaults` omits the key there), and
#: a workset template seeds FUTURE boxes, of which a standalone root will never have
#: one.
#: ⚑ ``handbook`` and not ``canon/handbook``: the canon ROOT it hangs off is now
#: RESOLVED per workset (:func:`_workset_stamp_dirs`), so only the leaf is fixed.
_CANON_CHAPTER_LEAF = "handbook"


def _packaged_base_template() -> Path | None:
    """Locate the packaged TEMPLATE ROOT (``kanibako.data/global/template``).

    ⚑ It is NOT itself an install dest, and a root-relative path is NOT a
    home-relative one — the box-HOME seed source is ``template/box/home``, two levels
    down.  Code treating the two as the same is silently wrong (see
    :func:`kanibako.settings.core_defaults.assert_canon_bind_seed_disjoint`).
    """
    try:
        ref = packaged_data_dir("global", "template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def packaged_box_home_template() -> Path | None:
    """The packaged BOX-HOME seed source (``template/box/home``), or None.

    ⚑ The HOME-RELATIVE root: every path under it is spelled exactly as it lands in a
    box home.  Its consumers would be wrong one level up.
    """
    base = _packaged_base_template()
    if base is None:
        return None
    path = base / PACKAGED_BOX_TEMPLATE / "home"
    return path if path.is_dir() else None


def _packaged_shared_bundle() -> Path | None:
    """Locate the packaged read-only built-in CANON tree (the rom root).

    ⚑ BOUND, never installed — it has no ``install``/``plan_template_refresh``
    target and is enumerated only for the content DIGEST.  The rom ROOT is the digest
    root, so a file added anywhere under ``rom/`` is watched.
    """
    try:
        ref = packaged_data_dir(*ROM_ROOT_PARTS)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


#: A plugin's packaged AGENT-STORE PAYLOAD dir — the ONE spelling (D4).
#: ⚑⚑ IT IS STAMPED INTO THE AGENT STORE ROOT, of which ``template/`` is one entry, so
#: the payload itself carries the ``template/box/home`` prefix.  That prefix is the
#: half that is easy to drop: a payload spelled home-relative lands at
#: ``agents/<name>/<file>``, where NOTHING reads it — the stamp runs, reports nothing,
#: and still leaves the box with no agent config.  Layer 2's resolved source is what
#: the prefix must equal, pinned by
#: ``TestTemplateSeedDefaults.test_landing_path_equals_layer_2_source``.
PLUGIN_STORE_PAYLOAD_DIRNAME = "base"


def _packaged_agent_store(agent_name: str) -> Path | None:
    """Locate a plugin's packaged AGENT-STORE payload dir, or ``None``.

    ``None`` if the plugin is not installed or ships no ``data/base``.

    ⚑ There is NO pre-D4 ``data/template`` fallback and there must not be one again:
    the spelling is closed, and a plugin that ships anything else contributes NOTHING
    rather than landing its payload somewhere unread.
    """
    try:
        ref = importlib.resources.files(
            f"kanibako.plugins.{agent_name}"
        ).joinpath("data", PLUGIN_STORE_PAYLOAD_DIRNAME)
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def ensure_agent_stores(
    std: StandardPaths, agent_names: "Iterable[str]",
) -> list[str]:
    """Materialise each agent's STORE — the J-6 **A-action**, one implementation.

    Per name: the MOULD, then the SPECIFIC payload, then the box-template SKELETON.
    Every stamp is create-if-absent, which makes the whole thing IDEMPOTENT and
    SELF-HEALING.  Returns the names whose store was touched, for the caller's report.

    ⚑ MOULD FIRST IS SAFE ONLY BECAUSE THE MOULD IS OVERLAP-FREE.  On an overlapping
    path the EARLIER copy wins, so the mould would beat the specific content; it
    therefore ships STRUCTURE ONLY (D5).  If it ever gains content, this order must
    flip to specific-first.
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
            src = _packaged_agent_store(name)
            if src is not None:
                copy_tree(src, store, scope="agent")
        # (3) the discoverable box-template skeleton (D7), under the AGENT store's
        # FIXED ``template/`` leaf — an agent store is not a workset, so nothing here
        # is repointable and ``workset.template`` has no say over it.
        for rel in _BOX_TEMPLATE_SKELETON:
            (store / AGENT_TEMPLATE_STORE_REL / rel).mkdir(parents=True, exist_ok=True)
        touched.append(name)
    return touched


def _workset_stamp_dirs(workset_path: Path) -> tuple[Path, Path]:
    """*workset_path*'s RESOLVED ``(workset.canon, workset.template)`` dirs.

    ⚑⚑ THE STAMP FOLLOWS THE KEYS, NOT THE LITERALS.  Both are declared repointable
    (spec ``:962`` / ``:936``), and a stamp at the literal leaf seeds a tier the box's
    own key resolution then never reads — the chapter binds ask ``@workset.canon``.

    ⚑ ONE read of the root's ``workset.yaml`` feeds both, exactly as
    :func:`kanibako.project.workset._workset_skeleton_dirs` does for the other four:
    reading it per key opens a window for two answers about one file.  A root with no
    ``workset.yaml`` — which is EVERY root ``workset create`` makes, since it refuses a
    root that already exists and writes no settings file — yields the literal defaults,
    so the unrepointed stamp lands exactly where it always did.
    """
    from kanibako.project.workset import (
        load_workset_settings_doc, resolve_workset_canon, resolve_workset_template,
    )

    doc = load_workset_settings_doc(workset_path)
    return (resolve_workset_canon(workset_path, doc),
            resolve_workset_template(workset_path, doc))


def _workset_scope_allowed(workset_path: Path,
                           canon_root: Path, template_root: Path) -> tuple[str, ...]:
    """The workset whitelist RESPELLED against this root's resolved leaves.

    ⚑ A repoint MOVES the tier; it does not widen it.  ``SCOPE_WHITELISTS["workset"]``
    is the set of top-level store entries the mould may write, spelled for the DEFAULT
    leaves — so once the dest follows ``workset.canon`` the frame it is judged in has
    to follow too, or the deny-by-default predicate refuses the copy it was written to
    permit.  Only the two spellings change; ``canon/`` is still seedable at
    ``handbook`` and nowhere else.

    ⚑ A leaf resolving OUTSIDE *workset_path* keeps its DEFAULT spelling here and is
    left to ``_assert_contained``, which refuses it: an entry that is not under the
    store root has no store-relative path to whitelist in the first place.

    ⚑ The defaults are READ OFF ``SCOPE_WHITELISTS``, never re-spelled: an unrepointed
    root must produce that tuple EXACTLY, which is what
    ``test_the_respelling_degenerates_to_the_declared_table`` pins.
    """
    default_template, default_chapter = SCOPE_WHITELISTS["workset"]

    def store_rel(root: Path) -> str | None:
        try:
            return root.relative_to(workset_path).as_posix()
        except ValueError:
            return None

    template_rel, canon_rel = store_rel(template_root), store_rel(canon_root)
    return (
        default_template if template_rel is None else template_rel,
        default_chapter if canon_rel is None else f"{canon_rel}/{_CANON_CHAPTER_LEAF}",
    )


def _workset_stamp_copy(std: StandardPaths, workset_path: Path, canon_only: bool,
                        canon_root: Path) -> tuple[Path, Path]:
    """The (source, destination) pair of the workset stamp's copy — ONE definition.

    ⚑ The PRE-FLIGHT and the STAMP must narrow identically or the check would clear a
    copy it never looked at; both read this, neither respells it.

    ⚑ The caller always passes ``dest_root=workset_path``, NOT this *dest*.  The
    whitelist reads the STORE-relative path (see :func:`_check_whitelist`), so
    narrowing the copy to the canon tier must not narrow the frame it is judged in — do
    that and every entry looks top-level and the deny-by-default predicate goes blind.

    ⚑ The SOURCE stays :data:`_MOULD_CANON_ROOT` while the DEST is the resolved
    ``workset.canon``: the mould is one SYSTEM tree shared by every workset, so a
    per-workset repoint moves where content lands, never where it is read from.
    """
    mould = std.template / PACKAGED_WORKSET_TEMPLATE
    if canon_only:
        return mould / _MOULD_CANON_ROOT, canon_root
    return mould, workset_path


def check_workset_template(std: StandardPaths, workset_path: Path, *,
                           canon_only: bool = False) -> None:
    """PRE-FLIGHT the workset mould against the workset whitelist; write nothing.

    ⚑ Runs FIRST, before anything is registered or created: a refusal part-way
    through :func:`install_workset_template` would leave a REGISTERED workset with a
    PARTIAL copy, recoverable only by ``workset rm``.

    ⚑⚑ *canon_only* pre-flights the STANDALONE stamp, and that reason does NOT
    transfer — nothing is registered yet on that path.  A DIFFERENT one does, and it
    is stronger: the destination is a directory THE USER ALREADY HAD, which kanibako
    never deletes, so a refusal cannot be cleaned up by removing the destination.  The
    refusal has to land before the first byte, not after.
    """
    canon_root, template_root = _workset_stamp_dirs(workset_path)
    src, dest = _workset_stamp_copy(std, workset_path, canon_only, canon_root)
    copy_tree(src, dest, dest_root=workset_path, scope="workset", check_only=True,
              allowed=_workset_scope_allowed(workset_path, canon_root, template_root))


def install_workset_template(std: StandardPaths, workset_path: Path, *,
                             canon_only: bool = False) -> None:
    """Stamp a NEW workset store from the host workset mould — the J-6 A-action.

    This is the LIVE single-source shape for a host template: one mould, one
    ``copy_tree``, one whitelist.  Create-if-absent, so re-running adds only what is
    missing.

    ⚑⚑ *canon_only* is the STANDALONE half: the CANON tier only, never the
    ``template/`` skeleton.  Both halves are spec-backed and the reasons differ — see
    :data:`_CANON_CHAPTER_LEAF`.  ⚑ The canon chapter is guarantee-created on BOTH
    paths, which is why that one line sits outside the branch: it is the half the two
    modes SHARE, not something the standalone path also happens to do.

    ⚑⚑ BOTH LEAVES ARE RESOLVED KEYS, NEVER LITERALS (:func:`_workset_stamp_dirs`).
    The reachable repoint is the STANDALONE one: that destination is a directory the
    user ALREADY HAD, so it may already carry a ``workset.yaml`` that repoints
    ``workset.canon``.  Stamping the literal ``canon/`` there seeds a tier nothing
    reads, because the chapter bind asks the key.

    ⚑ The whitelist matters MOST here, and not for the reason this comment used to
    give.  A STANDALONE ``<workset_path>`` is a kanibako-MANAGED wrapper
    (``workset.yaml`` + ``box_data/`` + ``vault/{ro,rw}/`` + ``workspace/``); the
    user's own code lives one level down in ``workspace/`` (the workspace is a SUBDIR
    of the root — ``paths.py``, drift H) and no stamp ever reaches it.  What IS true is
    that the wrapper is a directory the user already had — ``resolve_standalone_project``
    requires ``root.is_dir()`` — so the deny-by-default predicate guards a tree nothing
    here is entitled to clean up afterwards.
    """
    canon_root, template_root = _workset_stamp_dirs(workset_path)
    src, dest = _workset_stamp_copy(std, workset_path, canon_only, canon_root)
    copy_tree(src, dest, dest_root=workset_path, scope="workset",
              allowed=_workset_scope_allowed(workset_path, canon_root, template_root))
    if not canon_only:
        for rel in _BOX_TEMPLATE_SKELETON:
            (template_root / rel).mkdir(parents=True, exist_ok=True)
    (canon_root / _CANON_CHAPTER_LEAF).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# ⚑⚑⚑ QUARANTINE — A NAMED SPECIAL CASE.  DO NOT COPY THIS SHAPE.
#
# Everything between here and ``install_packaged_templates`` is the BOX HANDBOOK
# host-template copy: a DELIBERATE, RULED EXCEPTION to the live model.  Jei,
# 2026-08-07g, on the handbook specifically: *"Yes, handbook copy keeps all three
# layers. It is a special case."*  A new host template follows the SINGLE-SOURCE
# shape of ``install_workset_template`` above instead.  ⚑ NOT a pattern, NOT a
# precedent, and not to be imitated for any other host template.
#
# ⚑⚑ SINGLE-ROUTE IS INTACT, NOT BENT.  Single-route governs what enters A BOX; a
# host template never enters one.  What enters is the RO BIND, an ordinary key.
#
# ⚑⚑ AND THE THREE LAYERS ARE NOT REDUNDANT WITH THE CHAPTER BINDS — measured, so
# nobody "simplifies" them to one source and silently DROPS the agent's and the
# workset's contributions.  The reasoning, and the HOST/GUEST criterion that put
# this copy outside the ``seeded`` category, are in the llm-doc § QUARANTINE.
# ---------------------------------------------------------------------------


def handbook_layer_source_keys(
    proj: ProjectPaths, agent_id: str | None
) -> tuple[str, ...]:
    """The ORDERED dotted SOURCE keys whose values root the three handbook layers.

    ⚑ DERIVED FROM :func:`template_seed_defaults`, not restated beside it — that is
    the whole point of the function: the gate deciding whether a layer exists is read
    from the one table rather than re-implemented here where it could drift.
    ``system.template`` is named directly because it is already floor-materialized.

    ⚑ THESE STAY KEYS.  They carry the user's repoint route (``config set
    workset.template`` reroutes this copy, pinned by the repoint tests); nothing here
    hardcodes a path.
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
    THIS.**  *dest* is the resolved ``@box.canon/handbook``; *layer_roots* are the
    RESOLVED ``<scope>.template`` roots in apply order.

    ⚑ THE ROOTS ARE PARAMETERS, DELIBERATELY — resolved at the seam that holds the
    launch snapshot, so nothing here can disagree with what the snapshot said.

    ⚑ SEED-ONCE / CREATE-IF-ABSENT via :func:`stage_layers`, so a re-create into a
    leftover box store never overwrites a chapter the user has edited.  That failsafe
    answers a shipped data-loss bug; it is not refactorable away.

    ⚑ NO DEST WHITELIST HERE, and that is deliberate — do not "restore" one.  There
    is ONE dest policy on this path (``_host_copy_dest``'s warn-and-skip at the
    caller), and a ``scope="box"`` check could only ever fire on that same key-fixed
    dest: a SECOND SPELLING OF ONE CONDITION (CONVENTIONS §0), disagreeing about
    severity.  Likewise NO PRE-FLIGHT TWIN (contrast :func:`check_workset_template`):
    this copy has no refusal to pre-flight.  Both contrasts are worked out in the
    llm-doc.
    """
    # GUARANTEE-CREATE: unconditional, so the ``optional: true`` RO ``canon_hb_box``
    # bind ALWAYS mounts — empty, if all three layers are.
    dest.mkdir(parents=True, exist_ok=True)
    stage_layers(dest, [Path(root) / _SEED_SRC_HANDBOOK for root in layer_roots])


def install_packaged_templates(
    std: StandardPaths, agent_names: list[str], refresh: bool = False,
) -> None:
    """Install the packaged content into its host stores — an ENUMERATED set (P-S2).

    Each (packaged subtree → host dest) pair is named because each has a different
    OWNER and therefore a different copy rule; the llm-doc tabulates them.

    ⚑⚑ ``refresh=True`` (the ``kanibako setup`` TRUE-REFRESH) reaches the STAGING
    rows ONLY.  The user-owned rows are create-if-absent on EVERY path — J-3 item 1:
    *"user-owned canon stores are NEVER overwritten by any implicit path"*.  Their
    differences are REPORTED instead (:func:`plan_template_refresh`'s ``kept`` list).
    """
    base_src = _packaged_base_template()
    if base_src is not None:
        # STAGING (system-owned): the box + workset moulds, refreshable.
        #
        # ⚑ SCOPED, and this is where J-2's box whitelist actually BITES: staging is
        # the earliest — and only — point a planted settings file can be REFUSED.
        # Nothing downstream re-checks it, because the two downstream copies read
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
        # root, so there is no store whitelist to apply.
        copy_tree(
            base_src / PACKAGED_HANDBOOK, std.canon / "handbook",
        )
    # The agent MOULD dir exists even though nothing packages it (D5/D7).
    (std.template / AGENT_MOULD_DIRNAME).mkdir(parents=True, exist_ok=True)
    # USER-OWNED: the agent stores — the A-action, default included.
    ensure_agent_stores(std, ["default", *agent_names])


def _is_shipped_content(entry: Path) -> bool:
    """True iff *entry* is a real shipped file (not a build/editor artifact).

    ⚑ Junk never ships in a wheel, so hashing it would make the content digest
    non-deterministic across environments and report drift that never shipped.
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

    The ONE traversal shared by both consumers of a packaged content tree.  ⚑ SORTED
    so the enumeration is deterministic across machines and filesystem walk order.
    """
    files: list[tuple[str, Path]] = []
    for entry in root.rglob("*"):
        if _is_shipped_content(entry):
            files.append((entry.relative_to(root).as_posix(), entry))
    files.sort(key=lambda item: item[0])
    return files


def _packaged_manifest_entries(agent_names: list[str]) -> list[tuple[str, bytes]]:
    """Return the SORTED ``(namespaced-path, file-bytes)`` content manifest.

    ⚑ The source-distinct prefix (``base/`` / ``shared/`` / ``agent/<name>/``) is what
    stops a file being double-counted; SORTED keeps the manifest deterministic.
    """
    entries: list[tuple[str, bytes]] = []

    base_src = _packaged_base_template()
    if base_src is not None:
        for rel, entry in walk_shipped_files(base_src):
            entries.append((f"base/{rel}", entry.read_bytes()))

    # The RO packaged canon is enumerated here ONLY so the setup gate still trips
    # when the shipped canon content drifts — it has no install target.
    bundle_src = _packaged_shared_bundle()
    if bundle_src is not None:
        for rel, entry in walk_shipped_files(bundle_src):
            entries.append((f"shared/{rel}", entry.read_bytes()))

    for agent_name in sorted(agent_names):
        agent_src = _packaged_agent_store(agent_name)
        if agent_src is None:
            continue
        for rel, entry in walk_shipped_files(agent_src):
            entries.append((f"agent/{agent_name}/{rel}", entry.read_bytes()))

    entries.sort(key=lambda item: item[0])
    return entries


def packaged_templates_digest(agent_names: list[str]) -> str:
    """Return a content-manifest sha256 over the packaged template src trees.

    A CONTENT hash, not a version marker: it is immune to the ``setup_completed``
    silent forward-bump that would mask template drift.

    ⚑ NO RUNTIME CONSUMER since R-38 (verified 2026-08-02) — do not read that as
    dead.  It is kept for the RELEASE-TIME check, which is planned CI work (plan step
    C2) and is NOT wired yet.
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
# ⚑ REPORTING ONLY.  It never decides whether to copy; create-if-absent (or the
# staging refresh) already did.  Its job is to keep the setup report HONEST —
# spec §2a: *"report a skip ONLY where the packaged file DIFFERS"*.
#
# ⚑ ACCEPTED CONSEQUENCE (J-3 item 5): a comment-only upstream change compares
# EQUIVALENT and goes unreported today.
# ---------------------------------------------------------------------------

#: HTML comments, non-greedy, across lines — stripped because both equivalence tiers
#: ignore comments (J-3 item 5).
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)

#: A fenced code block, captured whole so the normaliser can leave its interior
#: ALONE: whitespace is SEMANTIC inside a fence.
_FENCE_RE = re.compile(r"(^|\n)(```|~~~)[^\n]*\n.*?(\n\2[^\n]*(?=\n|$))", re.DOTALL)


def _normalise_markdown(text: str) -> str:
    """Normalise *text* for the MD equivalence tier — CONSERVATIVELY.

    ⚑ TWO THINGS ARE DELIBERATELY LEFT ALONE, because in markdown they are SYNTAX,
    not whitespace: the interior of a FENCED CODE BLOCK, and a TRAILING TWO-SPACE
    hard line break.  Under-normalising costs a spurious "different" report;
    over-normalising would HIDE a real change, hence the bias.
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

    ONE strategy table keyed by suffix (J-3 item 2).  ⚑ A YAML parse failure on
    EITHER side ⇒ "different", never equivalent: an unparseable file is exactly the
    case a report should surface.
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

    Returns ``(added, overwritten, kept)`` lists of HOST target paths; a file that is
    byte-equal or :func:`_equivalent` lands in NO list and is deliberately unreported.
    A pure classification (no writes).  ⚑ A KEPT file is a USER-OWNED one that
    DIFFERS: reported so the user knows upstream moved on, NEVER overwritten
    (J-3 item 1).
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
        agent_src = _packaged_agent_store(agent_name)
        if agent_src is None:
            continue
        _walk(agent_src, std.agents / agent_name, user_owned=True)

    return added, overwritten, kept
