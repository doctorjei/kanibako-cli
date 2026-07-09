"""Shell template resolution and application."""

from __future__ import annotations

import hashlib
import importlib.resources
import shutil
import tempfile
from typing import TYPE_CHECKING
from pathlib import Path

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
#   agent  -> ``kanibako.plugins.<agent>`` resource ``template/``
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
    """Locate the packaged base-template content (``kanibako.data/templates/base``)."""
    try:
        ref = importlib.resources.files("kanibako.data").joinpath("templates", "base")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def _packaged_agent_template(agent_name: str) -> Path | None:
    """Locate a plugin's packaged template content (``kanibako.plugins.<agent>/template``).

    Returns ``None`` if the plugin is not installed or ships no ``template/``
    (e.g. ``no_agent`` / a third-party target without curated content).
    """
    try:
        ref = importlib.resources.files(
            f"kanibako.plugins.{agent_name}"
        ).joinpath("template")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_dir() else None


def _packaged_instructions() -> Path | None:
    """Locate the packaged default box-guidance file (``kanibako.data/global/KANIBAKO.md``)."""
    try:
        ref = importlib.resources.files("kanibako.data").joinpath("global", "KANIBAKO.md")
    except (ModuleNotFoundError, FileNotFoundError):
        return None
    path = Path(str(ref))
    return path if path.is_file() else None


def install_packaged_templates(
    std: StandardPaths, agent_names: list[str], refresh: bool = False,
) -> None:
    """Copy packaged curated-template content into the runtime template dirs.

    Populates ``@system.base_template`` from the packaged base content and each
    ``@config.agents/<agent>/template`` from the agent plugin's packaged
    ``template/``.  Also installs the shipped default box-guidance file to
    ``@system.instructions`` (``<data>/global/KANIBAKO.md``).  Called from
    first-run init; safe to re-run (idempotent for unchanged trees).

    Default (``refresh=False``) is CREATE-IF-ABSENT (never clobbers user edits) —
    the first-run behaviour.  ``refresh=True`` is the TRUE-REFRESH path (``kanibako
    setup``): shipped files (base tree, each agent tree, and KANIBAKO.md) are
    OVERWRITTEN to their current packaged versions.  User-only files are never in
    the packaged src loop, so they stay untouched either way.
    """
    base_src = _packaged_base_template()
    if base_src is not None:
        _copy_resource_tree_if_absent(base_src, std.base_template, overwrite=refresh)

    # Agent-agnostic box-guidance source (@system.instructions): a single
    # shipped default installed create-if-absent (or overwritten on refresh),
    # resolved via the keyspace so a user who repoints/edits the key or the file
    # keeps their copy on first-run.  Plugins bind this host source read-only
    # into each harness slot (delivery = plugin layer).
    instr_src = _packaged_instructions()
    if instr_src is not None and (refresh or not std.instructions.exists()):
        std.instructions.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(instr_src), str(std.instructions))

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


def packaged_templates_digest(agent_names: list[str]) -> str:
    """Return a content-manifest sha256 over the packaged template src trees.

    Hashes exactly the packaged content that :func:`install_packaged_templates`
    would install — the base tree (``_packaged_base_template``), the shipped
    KANIBAKO.md (``_packaged_instructions``), and each installed agent's
    ``template/`` (``_packaged_agent_template``).  Each file contributes a
    ``(namespaced-relative-path, file-bytes)`` pair; the pairs are SORTED before
    hashing so the digest is deterministic across runs and machines regardless
    of filesystem walk order.

    This is a CONTENT hash, not a version marker: it trips ONLY when packaged
    template content actually changes (so the staleness gate never hard-errors
    on a version bump that doesn't touch templates), and it is immune to the
    ``setup_completed`` silent forward-bump that would mask template drift.
    """
    entries: list[tuple[str, bytes]] = []

    base_src = _packaged_base_template()
    if base_src is not None:
        for entry in base_src.rglob("*"):
            if entry.is_file():
                rel = entry.relative_to(base_src).as_posix()
                entries.append((f"base/{rel}", entry.read_bytes()))

    instr_src = _packaged_instructions()
    if instr_src is not None:
        entries.append(("instructions/KANIBAKO.md", instr_src.read_bytes()))

    for agent_name in sorted(agent_names):
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        for entry in agent_src.rglob("*"):
            if entry.is_file():
                rel = entry.relative_to(agent_src).as_posix()
                entries.append((f"agent/{agent_name}/{rel}", entry.read_bytes()))

    entries.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    for key, data in entries:
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

    instr_src = _packaged_instructions()
    if instr_src is not None:
        _classify(instr_src, std.instructions)

    for agent_name in agent_names:
        agent_src = _packaged_agent_template(agent_name)
        if agent_src is None:
            continue
        dest = std.agents / agent_name / "template"
        for entry in agent_src.rglob("*"):
            if entry.is_file():
                _classify(entry, dest / entry.relative_to(agent_src))

    return added, overwritten
