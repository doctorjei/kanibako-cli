"""Shell template resolution and application."""

from __future__ import annotations

import hashlib
import importlib.resources
import shutil
import tempfile
from typing import TYPE_CHECKING
from pathlib import Path

from kanibako.core_defaults import packaged_data_dir

if TYPE_CHECKING:
    from kanibako.paths import ProjectPaths, StandardPaths


# ---------------------------------------------------------------------------
# Layered home-seed (spec §2a "Template seed (LAYERED, ordered)").
#
# The 1.6.0 home-seed model layers three ordered ``seeded.template`` sources into
# the box home at creation (base -> agent -> workset; later overlays earlier).
# The layer SOURCES are NO LONGER derived on disk here — they are ORDINARY
# keystore ``seeded`` category keys resolved through the launch snapshot (spec
# §2a; ruled 2026-07-09 Q1: everything goes through the keystore + seeding, no
# bespoke template route). :func:`template_seed_defaults` declares those keys as
# default-category entries; the seed seam (``commands.start._apply_init_seeds``)
# resolves them off the committed snapshot, then applies each ``~``-targeted
# layer, IN ORDER, via :func:`stage_layers` (the per-file last-wins + create-if-
# absent staging that used to be ``stage_and_seed_templates``).
#
#   layer 1  system.seeded.template  | (@system.base_template, ~)   base (global)
#   layer 2  agent.<a>.seeded.template| (@agent.<a>.template, ~)     per-agent (persona+harness)
#   layer 3  workset.seeded.template  | (@workset.template, ~)       per-workset (primary/named)
#
# Sources: @system.base_template (system.* settings tier) · @agent.<a>.template =
# @config.agents/<harness>/template · @workset.template = @meta.workset.path/template
# (skip-if-absent — the seeded category drops a layer whose source dir is absent).
# ---------------------------------------------------------------------------


def template_seed_defaults(
    proj: ProjectPaths, agent_id: str | None
) -> dict[str, object]:
    """Return the layered ``seeded.template`` DEFAULT-category table (spec §2a).

    The three template layers as ORDINARY keystore keys, ready to fold into the
    seed-time snapshot's ``default_categories`` (``commands.start._apply_init_seeds``)
    so they resolve + apply through the SAME single seeded-category route as every
    other seed — no bespoke template plumbing (Q1). Each is a ``seeded`` COPY into
    ``~`` (create-time home), sourced from an ``@``-ref SETTINGS key so the source
    stays user-repointable through the cascade (setting ``workset.template`` /
    ``agent.<a>.template`` reroutes the seed):

    * ``system.seeded.template``   = ``(@system.base_template, ~)`` — ALWAYS (Q4:
      no carve-out; the base template rides the seed system like every layer, so
      every file in it is seeded).
    * ``agent.<a>.seeded.template`` = ``(@agent.<a>.template, ~)`` — only when an
      agent is bound; the source key ``agent.<a>.template`` defaults to
      ``@config.agents/<harness>/template`` (spec §2a/§2d; ``<a>`` = the persona+
      harness node, Q2). Absent for a NO-AGENT box.
    * ``workset.seeded.template`` = ``(@workset.template, ~)`` — only for a
      PRIMARY/NAMED box (a workset tier exists); the source key
      ``workset.template`` defaults to ``@meta.workset.path/template`` (Q3, was
      ``<None>``). STANDALONE has no workset tier, so the layer is OMITTED (spec
      §2c L483 ``workset.template <None>``). The layer is SKIPPED when the source
      dir is absent — the seeded category's ordinary missing-source semantics.

    The returned dict mixes the SEED tuple keys with their SOURCE scalar keys
    (``workset.template`` / ``agent.<a>.template``) so both land in the snapshot
    floor: the scalar resolves the ``@``-ref, and a user override of the scalar
    (config set / settings file) wins by cascade precedence and reroutes the seed.
    ``system.base_template`` is already floor-materialized (it is a ``system.*``
    settings-tier path), so it is NOT re-declared here.
    """
    from kanibako.agent_ref import harness_of
    from kanibako.channels import has_workset_channels

    # box_dest ``~`` (NOT ``~/``) so it expands to exactly ``/home/agent`` (GUEST_HOME,
    # no trailing slash) — the create-time home the seed seam maps to proj.shell_path,
    # matching the core ``box.bindings.rw.home`` dest.
    defs: dict[str, object] = {
        "system.seeded.template": ("@system.base_template", "~"),
    }
    if agent_id:
        harness = harness_of(agent_id)
        # SOURCE key (spec §2a/§2d): the per-agent template dir under the agent's
        # (harness) store — the same @config.agents/<harness>/template the retired
        # on-disk deriver produced, now a resolvable/settable keystore key.
        defs[f"agent.{agent_id}.template"] = f"@config.agents/{harness}/template"
        defs[f"agent.{agent_id}.seeded.template"] = (
            f"@agent.{agent_id}.template", "~",
        )
    if has_workset_channels(proj):
        # SOURCE key (spec §2c L507; Q3 default @meta.workset.path/template): the
        # workset-local template dir. STANDALONE (no workset channels) omits BOTH
        # the source and the layer (its workset tier is <None>).
        defs["workset.template"] = "@meta.workset.path/template"
        defs["workset.seeded.template"] = ("@workset.template", "~")
    return defs


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
       :func:`_copy_resource_tree_if_absent` — a pre-existing *dest* file survives
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
    present = [layer for layer in layers if layer.is_dir()]
    if not present:
        return
    with tempfile.TemporaryDirectory(prefix="kanibako-seed-") as staging:
        staged = Path(staging)
        for layer in present:
            for entry in sorted(layer.rglob("*")):
                if not entry.is_file():
                    continue
                rel = entry.relative_to(layer)
                dest_file = staged / rel
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                # Overwrite within staging is intended (per-file last-wins).
                shutil.copy2(str(entry), str(dest_file))
        _copy_resource_tree_if_absent(staged, dest)


# ---------------------------------------------------------------------------
# Packaged curated-template install (Phase 9c).
#
# The base + per-agent template content ships as STATIC files inside the
# installed packages (mirroring how ``image-baseline.yaml`` ships under
# ``kanibako.data``):
#
#   base   -> ``kanibako.data`` resource ``templates/base/``
#   agent  -> ``kanibako.plugins.<agent>`` resource ``data/template/``
#
# On first-run init (``cli._ensure_initialized`` / ``install.run``) these are
# COPIED into the runtime template dirs (``@system.base_template`` and
# ``@config.agents/<agent>/template``) where the layered seed-once apply above
# reads them at box creation.  The copy is CREATE-IF-ABSENT per file: it adds
# files the user does not yet have but never clobbers a user-edited template
# (an explicit "refresh from package" is out of scope for 1.6.0).
# ---------------------------------------------------------------------------


def _copy_resource_tree_if_absent(
    src: Path, dest: Path, *, overwrite: bool = False,
) -> None:
    """Copy every file under *src* into *dest*, skipping files that exist.

    Mirrors the relative tree of *src* into *dest*.  Existing destination files
    are left untouched (create-if-absent) so user edits to a seeded template
    survive a later kanibako upgrade.  Directories are created as needed.

    When *overwrite* is True (the TRUE-REFRESH path used by
    ``install_packaged_templates(..., refresh=True)``) an existing destination
    file IS replaced with the packaged version.  The default (False) preserves
    the load-bearing create-if-absent contract — the public alias
    :data:`copy_resource_tree_if_absent` (reused by the box-SEED apply in
    ``commands.start``) must NEVER clobber a per-box home file.
    """
    if not src.is_dir():
        return
    for entry in src.rglob("*"):
        if not entry.is_file():
            continue
        rel = entry.relative_to(src)
        target = dest / rel
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(entry), str(target))


# Public alias so other modules (e.g. the seed-once apply in commands.start)
# can reuse the create-if-absent tree copy without reaching for a private name.
copy_resource_tree_if_absent = _copy_resource_tree_if_absent


def _packaged_base_template() -> Path | None:
    """Locate the packaged base-template SEED content.

    Repointed (instruction-delivery redesign) from the retired
    ``kanibako.data/templates/base`` (which shipped only ``INSTRUCTIONS.md``) to
    ``kanibako.data/global/template`` — the SEEDED, writable user tree
    (``playbook/CONTENTS.md`` + the scoped directive skeleton).  Because that dir
    contains ``playbook/...``, installing it into ``@system.base_template`` and
    seeding that layer at box home ``~`` deposits ``~/playbook/...`` (create-if-
    absent).  It carries NO ``kanibako/`` subdir, so it never collides with the RO
    built-in bundle bound live at ``~/playbook/kanibako``.
    """
    try:
        ref = packaged_data_dir("global", "template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def _packaged_shared_bundle() -> Path | None:
    """Locate the packaged read-only built-in directive bundle.

    ``kanibako.data/global/rom/playbook/kanibako`` — the KANIBAKO.md +
    flattener scripts that the launch path bind-mounts LIVE (ro) at their mirrored
    ``~`` paths (see ``core_defaults.rom_default_categories``, the per-file rom RO
    enumerator).  It is NOT copied/seeded to a host runtime dir, so it has no
    ``install``/``plan_template_refresh`` target; it is enumerated here only for the
    staleness DIGEST (this bundle root is the whole rom subtree today) so the setup
    gate still trips when the shipped bundle content drifts.
    """
    try:
        ref = packaged_data_dir("global", "rom", "playbook", "kanibako")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def _packaged_agent_template(agent_name: str) -> Path | None:
    """Locate a plugin's packaged template content (``kanibako.plugins.<agent>/data/template``).

    Returns ``None`` if the plugin is not installed or ships no ``data/template/``
    (e.g. ``no_agent`` / a third-party target without curated content).
    """
    try:
        ref = importlib.resources.files(
            f"kanibako.plugins.{agent_name}"
        ).joinpath("data", "template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def install_packaged_templates(
    std: StandardPaths, agent_names: list[str], refresh: bool = False,
) -> None:
    """Copy packaged curated-template content into the runtime template dirs.

    Populates ``@system.base_template`` from the packaged base content and each
    ``@config.agents/<agent>/template`` from the agent plugin's packaged
    ``template/``.  Called from first-run init; safe to re-run (idempotent for
    unchanged trees).

    The agent-agnostic box guide (``KANIBAKO.md``) is NOT installed here — it is
    delivered LIVE from the read-only built-in bundle (bound at
    ``~/playbook/kanibako`` + flattened into each agent's native instruction slot
    at launch), so it has no host runtime-install target.

    Default (``refresh=False``) is CREATE-IF-ABSENT (never clobbers user edits) —
    the first-run behaviour.  ``refresh=True`` is the TRUE-REFRESH path (``kanibako
    setup``): shipped files (base tree, each agent tree) are OVERWRITTEN to their
    current packaged versions.  User-only files are never in the packaged src
    loop, so they stay untouched either way.
    """
    base_src = _packaged_base_template()
    if base_src is not None:
        _copy_resource_tree_if_absent(base_src, std.base_template, overwrite=refresh)

    for agent_name in agent_names:
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        # Runtime per-agent template store dir (@config.agents/<agent>/template) —
        # the install DEST for the packaged content the layered seed later reads via
        # the ``@agent.<agent>.template`` key (this is a packaged->runtime install,
        # not the seed-SOURCE resolution the keystore owns).
        dest = std.agents / agent_name / "template"
        _copy_resource_tree_if_absent(agent_src, dest, overwrite=refresh)


def _is_shipped_content(entry: Path) -> bool:
    """True iff *entry* is a real shipped file (not a build/editor artifact).

    The RO built-in bundle is the first digest source to contain ``.py`` files,
    so a dev/editable checkout (or the repo's own test suite, which
    ``exec_module``s ``import-directives.py``) can drop a ``__pycache__/*.pyc``
    beside them.  Those never ship in a wheel, so hashing them would make the
    staleness digest non-deterministic across environments/Python versions and
    spuriously trip the setup gate.  Exclude Python bytecode caches and common
    editor/OS junk from the CONTENT manifest.
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
    staleness DIGEST (:func:`_packaged_manifest_entries`) and the rom RO-bind
    enumerator (:func:`kanibako.core_defaults.rom_default_categories`).  Walks
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
    (``_packaged_base_template``), each installed agent's ``template/``
    (``_packaged_agent_template``), AND the RO built-in bundle
    (``_packaged_shared_bundle``, which is bind-mounted rather than installed but
    still needs drift detection; it carries the KANIBAKO.md guide at
    ``directives/KANIBAKO.md``).  Each file contributes exactly ONE
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

    # The RO built-in bundle (bound live at ~/playbook/kanibako, never installed)
    # is enumerated here ONLY so the setup gate still trips when the shipped
    # KANIBAKO.md/flattener-script content drifts — it has no install target.  It
    # is the SOLE source of the KANIBAKO.md guide in this manifest (the retired
    # ``@system.instructions`` flat-copy no longer contributes a second entry).
    # NOTE: the digest walks the shared BUNDLE root (rom/playbook/kanibako) so the
    # namespaced keys stay ``shared/directives/...`` — the rom RO-bind enumerator
    # walks the rom ROOT (rom/) via the SAME helper for its ~-mirrored dests.
    bundle_src = _packaged_shared_bundle()
    if bundle_src is not None:
        for rel, entry in walk_shipped_files(bundle_src):
            entries.append((f"shared/{rel}", entry.read_bytes()))

    for agent_name in sorted(agent_names):
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        for rel, entry in walk_shipped_files(agent_src):
            entries.append((f"agent/{agent_name}/{rel}", entry.read_bytes()))

    entries.sort(key=lambda item: item[0])
    return entries


def packaged_templates_digest(agent_names: list[str]) -> str:
    """Return a content-manifest sha256 over the packaged template src trees.

    A CONTENT hash over :func:`_packaged_manifest_entries`, not a version marker:
    it trips ONLY when packaged template content actually changes (so the
    staleness gate never hard-errors on a version bump that doesn't touch
    templates), and it is immune to the ``setup_completed`` silent forward-bump
    that would mask template drift.
    """
    digest = hashlib.sha256()
    for key, data in _packaged_manifest_entries(agent_names):
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
    return digest.hexdigest()


def plan_template_refresh(
    std: StandardPaths, agent_names: list[str],
) -> tuple[list[Path], list[Path]]:
    """Classify every packaged src file by its host target for the prompt preview.

    Returns ``(added, overwritten)`` lists of HOST target paths:

    * ADDED — the packaged file has no host counterpart yet (create).
    * OVERWRITTEN — a host file exists but its bytes DIFFER from the packaged
      version (a true-refresh replaces it).
    * unchanged (host bytes == packaged bytes) — skipped, in neither list.

    User-only files never appear (they are not in the packaged src loop).  This
    is a pure classification (no writes) driving the ``kanibako setup`` preview.
    """
    added: list[Path] = []
    overwritten: list[Path] = []

    def _classify(src_file: Path, target: Path) -> None:
        if not target.exists():
            added.append(target)
        elif target.read_bytes() != src_file.read_bytes():
            overwritten.append(target)
        # else: byte-identical -> unchanged, skipped.

    base_src = _packaged_base_template()
    if base_src is not None:
        for entry in base_src.rglob("*"):
            if entry.is_file():
                _classify(entry, std.base_template / entry.relative_to(base_src))

    for agent_name in agent_names:
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        dest = std.agents / agent_name / "template"
        for entry in agent_src.rglob("*"):
            if entry.is_file():
                _classify(entry, dest / entry.relative_to(agent_src))

    return added, overwritten
