"""MANIFEST ENFORCEMENT — spec §0's CONTAINMENT TABLE, asserted against live code.

⚑⚑ THE DIRECTION IS **MANIFEST → CODE**, the same arrow as its sibling
``test_manifest_enforces.py`` and for the same reason: the shipped registry is release
authority, so its copy of a rule is the one a user is entitled to believe, and the code
is the thing on trial.  🛑 DO NOT MERGE this into that file.  They share the arrow and
nothing else — that one governs ``policy.seed_whitelists`` and ``categories``, this one
governs ``policy.category_collisions.containment_table``, and each is already long
enough that a reader arriving at a finding needs to know which block it is about.

⚑ WHY IT EXISTS.  Measured 2026-08-29: ``policy.category_collisions`` had **ZERO
readers** — ``containment_table`` occurred exactly once in the whole repository, in the
manifest itself — and the block had drifted behind its own source.  The spec states the
table over FOUR arriving kinds (``bind`` · ``mask`` · ``copy (file)`` · ``copy (dir)``)
against SIX occupant relations; the manifest carried only the two MOUNT rows, so twelve
cells were simply absent.  The sharpest consequence was that the manifest nowhere stated
the one cell the spec calls *"the whole point of two rows"*: at a mask's own point a
copied FILE is ACCEPTED and deletes the mask, while a copied DIRECTORY is REFUSED.  A
reader treating the shipped registry as authority could not answer *"what happens to a
``synced`` file landing on a mask?"*

⚑ BEING UNDER ``policy:`` IS NOT WHAT MAKES A RULE ENFORCING — being read by a named
test is.  Of the sub-blocks under ``policy:``, only ``seed_whitelists``,
``parametric_expansion`` and ``reserved_leaf_names`` were read by anything at all.  This
file is what makes the containment table the fourth.

⚑ THREE TIERS, in the order a finding should be read:

1. **SHAPE / ANTI-VACUITY** (:class:`TestTheTableShape`).  The cells are exactly the
   declared cross product, every value is in the declared outcome vocabulary, and every
   declared outcome is used.  Counts are asserted BEFORE any set difference, so an
   emptied or renamed block reds here rather than passing over nothing.
2. **EVERY CELL DRIVEN AGAINST LIVE CODE** (:class:`TestEveryCellIsWhatTheCodeDoes`).
   The manifest cell supplies the EXPECTED outcome; the code supplies the actual.  🛑 NO
   CELL IS MARKED SHAPE-ONLY.  All 24 are reachable by a direct call — the mount rows
   through ``settings.store_collapse``, the copy rows through the two
   ``commands.start`` functions that take plain ``(copies, bindings)`` arguments — so an
   exemption here would be an exemption nobody needed.
3. **``refusals`` ⇄ ``cells`` CONSISTENCY, BOTH DIRECTIONS**
   (:class:`TestTheRefusalsAndTheCellsAgree`).  This is what structurally prevents a
   repeat of *"the refusals list sat two entries behind the spec"*.

⚑ WHAT IS DELIBERATELY NOT HERE: a SPEC ⇄ REGISTRY arm.  The spec lives in the canon,
outside this repo and absent from CI, so such a pin's green is not observable in CI —
``test_manifest_spec_parity.py`` says so in its own docstring.  Every tier above reads
only SHIPPED data and SHIPPED code, so all three are witnessed by an ordinary CI run.

Indent note: 4 spaces, matching every sibling in ``tests/test_settings/`` (house style
is 2, but this directory is the exception).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

import pytest

from kanibako.commands.start import (
    _refuse_synced_under_mask,
    _synced_host_dest,
    _synced_masks_replaced,
)
from kanibako.settings.kb_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.keyspace_manifest import manifest_doc
from kanibako.settings.settings_resolve import GUEST_HOME, SettingsError
from kanibako.settings.store_collapse import (
    HOME_DEST,
    MASK,
    CollapsedBind,
    CollapsedCopy,
    CollapsedStore,
    collapse_store_shapes,
    is_mask,
    is_within,
    refuse_uncovered_synced,
)
from kanibako.settings.store_shape import StoreShape, StoreShapeSet


# --------------------------------------------------------------------------- #
# The block, and the axes it is stated over
# --------------------------------------------------------------------------- #


def _block() -> dict:
    """The manifest's ``policy.category_collisions`` block (a fresh copy — P8)."""
    return manifest_doc()["policy"]["category_collisions"]


def _table() -> dict:
    """The ``containment_table`` sub-block."""
    return _block()["containment_table"]


def _cells() -> dict:
    """``arriving kind -> occupant relation -> outcome``, as the manifest declares it."""
    return _table()["cells"]


def _kinds() -> list[str]:
    return list(_table()["arriving_kinds"])


def _relations() -> list[str]:
    return list(_table()["occupant_relations"])


def _cell_cases() -> list[tuple[str, str]]:
    """Every ``(arriving kind, occupant relation)`` pair the manifest declares.

    ⚑ Derived from the two axis lists rather than from the ``cells:`` mapping itself:
    a row that LOSES a column must still generate its case, or the case meant to catch
    the loss would silently stop running (P13 — a pin may never read its own subject).
    """
    return [(kind, relation) for kind in _kinds() for relation in _relations()]


#: The MOUNT half of the arriving kinds, and the COPY half.  ⚑ Stated as the RULE the
#: axis follows — a mount is what the collapse folds (a bind, or the mask sentinel that
#: is a bind with no source), a copy is a ``synced`` row discriminated by whether its
#: SOURCE is a file or a directory — so :class:`TestTheTableShape` can assert the two
#: halves partition the axis instead of this file listing four names twice.
_MOUNT_KINDS: frozenset[str] = frozenset({"bind", "mask"})
_COPY_KINDS: frozenset[str] = frozenset({"copy_file", "copy_dir"})

#: The relation axis, DERIVED: ``{point, child, parent} x {bind, mask}``.  The three
#: positions are what the block's own prose enumerates (*"an arriving mount may sit AT
#: another's point, INSIDE it, or ABOVE it"*) and the two occupant kinds are the mount
#: kinds above.  A seventh column is therefore RED rather than merely undriven.
_POSITIONS: tuple[str, ...] = ("point", "child", "parent")


def _expected_relations() -> list[str]:
    return [f"{p}_is_{k}" for p in _POSITIONS for k in ("bind", "mask")]


# --------------------------------------------------------------------------- #
# The outcome vocabulary, and how each token is MEASURED
# --------------------------------------------------------------------------- #

#: The classifier's own answers — what a driver below can observe about a cell.  ⚑ These
#: are FACTS, not manifest tokens: the mapping from a manifest token to one of these is
#: :data:`_MEASURED_AS`, and it is where any notation difference is declared out loud.
_REFUSE = "refuse"
_SWEEP = "sweep"
_NEST = "nest"
_APPEND = "append"
_OK = "ok"

#: Each manifest outcome token, mapped to the FACT a driver must observe for it.
#:
#: ⚑⚑ ``not_applicable`` MAPS TO ``ok``, AND THAT IS THE HONEST ANSWER.  The spec writes
#: *n/a* in the two ``copy (file)`` CHILD cells because nothing can sit inside a FILE —
#: the arrangement is expressible as declarations and unrealisable as a filesystem.  The
#: code has no branch for it, so what it DOES is exactly what it does for ``ok``:
#: accept, and leave the occupant alone.  Claiming a second measurable outcome here
#: would be inventing evidence.  What makes the token more than decoration is
#: STRUCTURAL and is pinned by
#: :meth:`TestTheTableShape.test_not_applicable_is_the_copy_file_child_cells_and_no_others`
#: — it may appear ONLY where the arriving copy is a FILE and the occupant is a CHILD,
#: which is precisely the claim "nothing sits inside a file" makes.
#:
#: ⚑ ``sweep_except_home_refuse`` maps to ``None`` because it is COMPOUND: one token
#: naming two outcomes, so it is driven by TWO probes rather than one (see
#: :class:`TestTheHomeCarveOutIsBothHalves`).
_MEASURED_AS: dict[str, str | None] = {
    "refuse": _REFUSE,
    "sweep": _SWEEP,
    "nest": _NEST,
    "append": _APPEND,
    "ok": _OK,
    "not_applicable": _OK,
    "sweep_except_home_refuse": None,
}


# --------------------------------------------------------------------------- #
# The MOUNT driver — an occupant scope folded, then an arriving scope
# --------------------------------------------------------------------------- #
#
# ⚑ THE ROUTE IS THE PRODUCTION ONE.  ``collapse_store_shapes`` is what a launch calls;
# it folds the four scopes in ``SCOPE_CONTAINMENT`` order over the home foundation, and
# ``_merge_bindings`` applies the table one scope at a time.  So the occupant is
# declared in an EARLIER scope and the arrival in a LATER one, which is the shape a
# cross-scope collision actually has.  Building a ``combined`` map by hand and calling
# ``_merge_bindings`` directly would test the same function against an input the
# collapse never produces.

#: A guest path under home, deep enough to have both a parent and a child inside home.
_P = f"{GUEST_HOME}/p"
_CHILD = f"{_P}/inner"

#: Host sources.  ⚑ Distinct strings, because "did the copy resolve THROUGH the
#: occupant" is answered by containment against one of them.
_HOME_SRC = "/host/home"
_OCC_SRC = "/host/occupant"
_ARR_SRC = "/host/arrival"


def _shape(*, rw: dict | None = None, mask: dict | None = None) -> StoreShape:
    """One scope's realization view — the two arms this file declares into."""
    return StoreShape(rw=dict(rw or {}), mask=dict(mask or {}))


def _shape_set(**per_scope: StoreShape) -> StoreShapeSet:
    """A full four-scope set; every scope not named is EMPTY.

    ⚑ All four are present because ``_collapse_mounts`` walks
    :data:`SCOPE_CONTAINMENT` unconditionally — a partial map would be a shape the
    producer never emits.
    """
    return StoreShapeSet(shapes={
        scope: per_scope.get(scope, StoreShape()) for scope in SCOPE_CONTAINMENT
    })


def _mount_arm(kind: str, dest: str, src: str) -> dict:
    """The arm keyword a mount of *kind* at *dest* folds into."""
    return {"mask": {dest: True}} if kind == "mask" else {"rw": {dest: BindEntry(src)}}


def _mount_dests(relation: str) -> tuple[str, str]:
    """``(occupant dest, arriving dest)`` for *relation*, both under home.

    The occupant's relation to the ARRIVAL is what the column names: at its point, a
    child of it (longer), or its parent (shorter).
    """
    position = relation.split("_", 1)[0]
    return {
        "point": (_P, _P),
        "child": (_CHILD, _P),
        "parent": (_P, _CHILD),
    }[position]


def _mount_is_itself(store: CollapsedStore, kind: str, dest: str, src: str) -> bool:
    """Is the mount of *kind* still ITSELF at *dest* after the fold?

    ⚑ Identity, not mere key presence: at a ``point_is_*`` cell the arrival lands at
    the very same destination, so "the key exists" is true whichever mount won.  Asked
    of the occupant (did it survive?) and of the arrival (did it land?) alike.
    """
    held = store.bindings.get(dest)
    if held is None:
        return False
    return is_mask(held) if kind == "mask" else held.src == src


def _run_mount(
    arriving: str, relation: str, *, occupant_is_home: bool = False,
) -> str:
    """Fold ONE occupant scope and ONE arriving scope; return the measured outcome.

    *occupant_is_home* drives the HOME half of ``sweep_except_home_refuse``: home is
    the pid-0 foundation and is in no scope's shape, so the occupant is not declared at
    all — the arrival is aimed at home's own point (``point_is_bind``) or above it
    (``child_is_bind``, home being the child) and the fold meets the foundation itself.
    """
    occupant_kind = relation.rsplit("_", 1)[1]
    if occupant_is_home:
        occ_dest, occ_src = HOME_DEST, _HOME_SRC
        arr_dest = HOME_DEST if relation.startswith("point") else "/home"
        occupant_shape = StoreShape()
    else:
        occ_dest, arr_dest = _mount_dests(relation)
        occ_src = _OCC_SRC
        occupant_shape = _shape(**_mount_arm(occupant_kind, occ_dest, occ_src))

    shapes = _shape_set(
        system=occupant_shape,
        box=_shape(**_mount_arm(arriving, arr_dest, _ARR_SRC)),
    )
    try:
        store = collapse_store_shapes(shapes, BindEntry(_HOME_SRC))
    except SettingsError:
        return _REFUSE

    if not _mount_is_itself(store, occupant_kind, occ_dest, occ_src):
        return _SWEEP
    assert _mount_is_itself(store, arriving, arr_dest, _ARR_SRC), (
        f"{arriving}/{relation}: the arrival is neither refused nor present at "
        f"{arr_dest!r} — the fold dropped it silently, which no cell of the table "
        f"describes"
    )
    return _NEST


# --------------------------------------------------------------------------- #
# The COPY driver — the launch seam's gate, then the two delivery passes
# --------------------------------------------------------------------------- #
#
# ⚑ THE SEQUENCE IS ``_apply_synced_copies``' OWN, and the order is load-bearing:
# coverage is refused at the collapse seam (``refuse_uncovered_synced``), then
# ``_synced_masks_replaced`` decides the ACCEPTED file-at-a-mask cell and deletes that
# mask, and only then does ``_refuse_synced_under_mask`` look — which is what keeps the
# accepted cell accepted.  Driving the two refusals in the other order would measure a
# composition the launch never performs.
#
# ⚑ EVERY SCENARIO CARRIES THE HOME BIND, and it is not scaffolding: refusal 6 refuses
# a ``synced`` dest NO mount covers, so a copy scenario with no ambient cover would
# measure that refusal instead of the cell.  Home is the mount that always exists.

_COPY_DEST = f"{GUEST_HOME}/p/d"


def _copy_dests(relation: str) -> tuple[str, str]:
    """``(occupant dest, copy dest)`` for *relation* — the copy dest is fixed."""
    position = relation.split("_", 1)[0]
    return {
        "point": (_COPY_DEST, _COPY_DEST),
        "child": (f"{_COPY_DEST}/inner", _COPY_DEST),
        "parent": (str(Path(_COPY_DEST).parent), _COPY_DEST),
    }[position]


def _copy_source(kind: str, tmp_path: Path) -> Path:
    """A real host source of the arriving kind — the file/dir split is a host STAT."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    src = tmp_path / "source"
    if kind == "copy_dir":
        src.mkdir()
    else:
        src.write_text("payload\n", encoding="utf-8")
    return src


def _copy_delivery(
    arriving: str, relation: str, tmp_path: Path, *, with_occupant: bool = True,
) -> tuple[bool, str | None, dict]:
    """One copy through the live composition: ``(refused, host dest, final bindings)``.

    ⚑ THE SEQUENCE IS ``_apply_synced_copies``' OWN — see the block comment above.

    *with_occupant* omits the occupant entirely, leaving only the home cover.  It is
    the control :class:`TestACopyNeverReadsWhatIsBelowIt` compares against, and it is
    why this returns FACTS rather than an outcome token: with no occupant declared,
    "did the occupant survive?" has no answer, while "was it refused, and where does
    the copy land?" has the same answer either way.
    """
    occupant_kind = relation.rsplit("_", 1)[1]
    occ_dest, dest = _copy_dests(relation)
    bindings = {HOME_DEST: CollapsedBind(_HOME_SRC, None)}
    if with_occupant:
        bindings[occ_dest] = (
            MASK if occupant_kind == "mask" else CollapsedBind(_OCC_SRC, None)
        )
    copies = [CollapsedCopy(str(_copy_source(arriving, tmp_path)), dest, None)]

    try:
        refuse_uncovered_synced(bindings, copies)
        for replaced in _synced_masks_replaced(copies, bindings):
            del bindings[replaced]
        _refuse_synced_under_mask(copies, bindings)
    except SettingsError:
        return True, None, bindings

    host = _synced_host_dest(dest, bindings, logger=logging.getLogger(__name__))
    return False, None if host is None else str(host), bindings


def _run_copy(arriving: str, relation: str, tmp_path: Path) -> str:
    """Drive one copy cell and classify the outcome."""
    occupant_kind = relation.rsplit("_", 1)[1]
    occ_dest, _dest = _copy_dests(relation)
    refused, host, bindings = _copy_delivery(arriving, relation, tmp_path)
    if refused:
        return _REFUSE
    held = bindings.get(occ_dest)
    survived = held is not None and (
        is_mask(held) if occupant_kind == "mask" else held.src == _OCC_SRC
    )
    if not survived:
        return _SWEEP
    return _APPEND if host is not None and is_within(host, _OCC_SRC) else _OK


def _run_cell(arriving: str, relation: str, tmp_path: Path) -> str:
    """The one door every cell case goes through."""
    if arriving in _COPY_KINDS:
        return _run_copy(arriving, relation, tmp_path)
    return _run_mount(arriving, relation)


# --------------------------------------------------------------------------- #
# 1. Shape / anti-vacuity — the exhaustiveness that makes tier 2 binding
# --------------------------------------------------------------------------- #

class TestTheTableShape:
    """The cells ARE the declared cross product, and every value is declared."""

    def test_the_block_exists_and_carries_exactly_its_four_fields(self):
        """Anti-vacuity: a renamed, deleted or extended block reds HERE, not silently.

        A NEW field lands as unclassified, which is the correct outcome — somebody must
        decide whether it is a fifth axis, a second table, or policy prose — and a
        DELETED one lands here too, so this cannot rot in either direction.
        """
        assert "category_collisions" in manifest_doc()["policy"]
        assert set(_table()) == {
            "arriving_kinds", "occupant_relations", "outcomes", "cells",
        }, sorted(_table())

    def test_the_axes_are_the_measured_size(self):
        """⚑ COUNTS FIRST.  Two empty axes are equal to each other and to nothing."""
        assert len(_kinds()) == 4
        assert len(_relations()) == 6
        assert len(_cell_cases()) == 24

    def test_the_relation_axis_is_the_position_by_occupant_cross_product(self):
        """P13 — the columns are DERIVED from the rule, never copied from the block.

        A seventh column, or a column dropped, reds here rather than quietly changing
        how many cells tier 2 drives.
        """
        assert _relations() == _expected_relations(), (
            f"occupant_relations {_relations()} is not "
            f"{{point, child, parent}} x {{bind, mask}} = {_expected_relations()}"
        )

    def test_the_arriving_kinds_split_into_mounts_and_copies(self):
        """The axis' own rule: two MOUNT kinds and two COPY kinds, nothing else.

        A fifth kind is RED until somebody classifies it — which is the decision this
        file needs made, because the two halves are driven through different seams.
        """
        assert set(_kinds()) == _MOUNT_KINDS | _COPY_KINDS, sorted(_kinds())
        assert not _MOUNT_KINDS & _COPY_KINDS

    def test_every_declared_kind_has_a_row_and_every_row_is_a_declared_kind(self):
        assert len(_cells()) == len(_kinds())
        assert set(_cells()) == set(_kinds()), (
            f"cells: has rows the arriving_kinds axis does not declare: "
            f"{sorted(set(_cells()) - set(_kinds()))}; and the axis declares kinds "
            f"with no row: {sorted(set(_kinds()) - set(_cells()))}"
        )

    @pytest.mark.parametrize("kind", sorted(_kinds()))
    def test_every_row_carries_exactly_the_relation_columns(self, kind):
        """A new column on ONE row is RED — the table may not go ragged."""
        row = _cells()[kind]
        assert len(row) == len(_relations())
        assert set(row) == set(_relations()), (
            f"{kind}: columns {sorted(row)} vs the declared relations "
            f"{sorted(_relations())}"
        )

    @pytest.mark.parametrize(("kind", "relation"), _cell_cases())
    def test_every_cell_value_is_in_the_declared_vocabulary(self, kind, relation):
        outcome = _cells()[kind][relation]
        assert outcome in _table()["outcomes"], (
            f"{kind}.{relation} = {outcome!r}, which the block's outcomes: vocabulary "
            f"does not declare: {sorted(_table()['outcomes'])}"
        )

    def test_the_vocabulary_is_classified_and_fully_used(self):
        """Both directions: no undeclared value, no dead token, no unclassified token.

        ⚑ A DEAD TOKEN IS THE QUIET FAILURE.  An outcome nobody writes is a promise the
        table does not keep, and it would leave :data:`_MEASURED_AS` carrying a mapping
        no case ever exercises.
        """
        declared = set(_table()["outcomes"])
        used = {_cells()[k][r] for k, r in _cell_cases()}
        assert used == declared, (
            f"outcomes: declares tokens no cell uses: {sorted(declared - used)}; and "
            f"cells use tokens outcomes: does not declare: {sorted(used - declared)}"
        )
        assert declared == set(_MEASURED_AS), (
            f"outcome tokens this file does not know how to measure: "
            f"{sorted(declared - set(_MEASURED_AS))}; measurements for tokens the "
            f"manifest no longer declares: {sorted(set(_MEASURED_AS) - declared)}"
        )

    def test_not_applicable_is_the_copy_file_child_cells_and_no_others(self):
        """⚑⚑ THE STRUCTURAL HALF OF ``not_applicable``, and the whole of its content.

        It measures identically to ``ok`` (:data:`_MEASURED_AS` says so and why), so
        this is what stops the token being decoration: the spec writes *n/a* for one
        reason — nothing sits inside a FILE — and that reason licenses it in exactly
        the two ``copy_file`` CHILD cells.  Written on any other cell it would be a
        claim the reason does not support, and this reds.
        """
        placed = {(k, r) for k, r in _cell_cases()
                  if _cells()[k][r] == "not_applicable"}
        assert placed == {
            ("copy_file", "child_is_bind"), ("copy_file", "child_is_mask"),
        }, sorted(placed)

    def test_append_is_a_copy_outcome_only(self):
        """A MOUNT never appends: it takes the destination or it is refused.

        The other half of the rule ``append`` states — the copy lands on top of a mount
        and writes THROUGH it — which has no meaning for an arrival that IS a mount.
        """
        appending = {k for k, r in _cell_cases() if _cells()[k][r] == "append"}
        assert appending <= _COPY_KINDS, sorted(appending)
        assert appending, "no cell uses `append` — the copy rows lost their point"


# --------------------------------------------------------------------------- #
# 2. Every cell, driven against live code
# --------------------------------------------------------------------------- #

class TestEveryCellIsWhatTheCodeDoes:
    """⚑ THE CASE THIS FILE WAS WRITTEN FOR: the manifest states it, the code does it.

    The manifest cell is the EXPECTED value and the code is on trial.  🛑 If one of
    these ever reds, the fix is NOT to edit the cell to match the code and NOT to edit
    the code to match the cell: a divergence between the shipped registry and the
    shipped behaviour at a spec-stated cell is an approved-breakage question, and the
    finding names the cell, the outcome declared and the outcome measured.
    """

    @pytest.mark.parametrize(("kind", "relation"), _cell_cases())
    def test_the_cell_is_the_measured_outcome(self, kind, relation, tmp_path):
        declared = _cells()[kind][relation]
        expected = _MEASURED_AS[declared]
        if expected is None:
            pytest.skip(
                f"{declared!r} is COMPOUND — driven by both its probes in "
                f"TestTheHomeCarveOutIsBothHalves, not by this single-outcome case"
            )
        measured = _run_cell(kind, relation, tmp_path)
        assert measured == expected, (
            f"containment table drift at {kind}.{relation}: the shipped manifest "
            f"declares {declared!r} (measured as {expected!r}) and the live code does "
            f"{measured!r}. This is spec §0's table — do NOT reconcile it by editing "
            f"either side."
        )

    def test_no_cell_was_skipped_for_any_other_reason(self):
        """⚑ ANTI-EXEMPTION.  The ONLY skip above is the compound token's, and it is
        not an exemption at all — both of its halves are driven below.

        Stated as an assertion so that adding a shape-only cell, an allowlist or an
        origin discriminator to :data:`_MEASURED_AS` reds rather than shrinking the
        corpus quietly.  All 24 cells are reachable by a direct call; there is nothing
        here an exemption could be for.
        """
        unmeasured = {t for t, m in _MEASURED_AS.items() if m is None}
        assert unmeasured == {"sweep_except_home_refuse"}, sorted(unmeasured)

    def test_every_measurable_outcome_is_actually_produced(self, tmp_path):
        """⚑ ANTI-VACUITY FOR THE DRIVERS.  A classifier stuck on one answer would
        satisfy every case whose cells happened to agree with it.

        The corpus is the table itself, so this also states what the table is FOR:
        five distinguishable things can happen to an arrival, and all five are reached.
        """
        produced = {
            _run_cell(k, r, tmp_path / f"{k}-{r}")
            for k, r in _cell_cases()
            if _MEASURED_AS[_cells()[k][r]] is not None
        }
        assert produced == {_REFUSE, _SWEEP, _NEST, _APPEND, _OK}, sorted(produced)


class TestTheHomeCarveOutIsBothHalves:
    """``sweep_except_home_refuse`` — ONE token naming TWO outcomes, both driven.

    ⚑ THIS IS NOT AN EXEMPTION, IT IS THE OPPOSITE.  A single-outcome case would have
    to pick one half and would then pass with the other half broken — a mask silently
    taking home would leave the box with no home at all, which is the failure refusal 4
    exists for.  So the compound token gets TWO probes and both are asserted.

    ⚑ HOME IS NOT A DECLARATION.  It is the pid-0 foundation
    (``store_collapse._collapse_mounts`` seeds it before the loop and no bind-shaped
    key names it), so the home probes declare no occupant — they aim the arriving mask
    at home's own point, and at a path above it.
    """

    @pytest.mark.parametrize(
        "relation",
        sorted(r for k, r in _cell_cases()
               if _cells()[k][r] == "sweep_except_home_refuse"),
    )
    def test_the_ordinary_occupant_is_swept(self, relation):
        assert _run_mount("mask", relation) == _SWEEP

    @pytest.mark.parametrize(
        "relation",
        sorted(r for k, r in _cell_cases()
               if _cells()[k][r] == "sweep_except_home_refuse"),
    )
    def test_home_at_that_same_relation_is_refused(self, relation):
        assert _run_mount("mask", relation, occupant_is_home=True) == _REFUSE

    def test_the_compound_token_is_the_mask_rows_and_the_bind_occupants(self):
        """Where the token sits, asserted — the two halves only make sense there.

        Home is a BIND, so only a ``*_is_bind`` column can hold it, and only a MASK
        arrival can subsume it (a bind at home's point is already refused as row 1).
        """
        placed = {(k, r) for k, r in _cell_cases()
                  if _cells()[k][r] == "sweep_except_home_refuse"}
        assert placed == {("mask", "point_is_bind"), ("mask", "child_is_bind")}, (
            sorted(placed)
        )


class TestACopyNeverReadsWhatIsBelowIt:
    """⚑⚑ THE RULE UNDER BOTH CHILD COLUMNS OF BOTH COPY ROWS — ``ok`` and ``n/a`` alike.

    Those four cells measure identically to *no occupant at all*, and stating only that
    would leave them looking like cells nothing drives.  What they actually assert is a
    property of the code, and it is checkable: the copy seams read the destination's own
    point (``_synced_masks_replaced``) and the mount COVERING it (``covering_bind``, at
    or above), and NOTHING below it.  So an occupant inside an arriving copy is not
    "handled leniently" — it is not consulted, which is exactly what ``ok`` claims for a
    directory and what makes the spec's *n/a* the honest word for a file.

    ⚑ THE COMPARISON IS AGAINST THE SAME SCENARIO MINUS THE OCCUPANT, so a seam that
    began reading downward would red here whichever way it then decided.
    """

    @pytest.mark.parametrize(
        ("kind", "relation"),
        [(k, r) for k, r in _cell_cases()
         if k in _COPY_KINDS and r.startswith("child")],
    )
    def test_an_occupant_below_the_copy_changes_nothing(self, kind, relation, tmp_path):
        present = _copy_delivery(kind, relation, tmp_path / "with")[:2]
        absent = _copy_delivery(
            kind, relation, tmp_path / "without", with_occupant=False,
        )[:2]
        assert present == absent, (
            f"{kind}.{relation}: with the child occupant the delivery is "
            f"(refused, host) = {present}; with nothing there at all it is {absent} — "
            f"the copy seams have started reading BELOW the destination"
        )
        assert _run_copy(kind, relation, tmp_path / "cell") == _OK

    def test_the_corpus_is_the_four_child_copy_cells(self):
        """Anti-vacuity: an empty parametrize list runs zero cases and stays green."""
        assert len([
            (k, r) for k, r in _cell_cases()
            if k in _COPY_KINDS and r.startswith("child")
        ]) == 4


# --------------------------------------------------------------------------- #
# 3. refusals ⇄ cells — the consistency that keeps the list from falling behind
# --------------------------------------------------------------------------- #
#
# ⚑ THE MAPPING IS BY PREDICATE, NOT BY INVENTORY.  Each numbered refusal is
# re-expressed as the condition its OWN WORDS state, so the cells it claims are
# COMPUTED.  A cell added to the table is claimed or unclaimed automatically, which is
# what makes the two directions below meaningful rather than a pair of hand-kept lists
# agreeing with each other.
#
# ⚑ AND EACH PREDICATE IS TIED TO THE MANIFEST STRING IT COMES FROM by a discriminating
# substring (the ``_COMPOSITION_RULES`` precedent in the sibling file), matched
# one-to-one in both directions.  A SEVENTH refusal is RED until a predicate claims it;
# a deleted or reworded one is red too.  That pairing is the structural answer to "the
# refusals list sat two entries behind the spec".

_Predicate = Callable[[str, str], bool]

#: Refusal number -> (a substring that identifies it in the manifest, the condition it
#: states).  ⚑ The substrings are chosen to sit inside ONE source line, because YAML
#: folds a multi-line double-quoted scalar's newlines into spaces.
_REFUSAL_RULES: dict[int, tuple[str, _Predicate]] = {
    1: (
        "PARENT of the dest refuses EVERY arrival",
        lambda kind, relation: relation == "parent_is_mask",
    ),
    2: (
        "an arriving BIND is refused by an existing bind",
        lambda kind, relation: kind == "bind"
        and relation in ("point_is_bind", "child_is_bind"),
    ),
    3: (
        "an arriving MASK is refused by an existing mask",
        lambda kind, relation: kind == "mask"
        and relation in ("point_is_mask", "parent_is_mask"),
    ),
    4: (
        "refused if HOME is at its dest",
        lambda kind, relation: kind == "mask"
        and relation in ("point_is_bind", "child_is_bind"),
    ),
    5: (
        "an arriving COPY of a DIRECTORY",
        lambda kind, relation: kind == "copy_dir" and relation == "point_is_mask",
    ),
    6: ("if NO mount COVERS its dest", lambda kind, relation: False),
}

#: The one refusal that claims NO cell, with the reason.  ⚑ NOT an exemption from being
#: checked — :meth:`TestTheRefusalsAndTheCellsAgree.test_the_uncovered_refusal_is_live`
#: drives it against the live function.  It is an exemption from the CELL mapping only,
#: and the reason is structural: every column of the table names an OCCUPANT, and this
#: refusal's premise is that there is none.
_CLAIMS_NO_CELL: dict[int, str] = {
    6: (
        "every occupant_relation presupposes something already collapsed at, inside "
        "or above the dest; refusal 6 fires when NOTHING covers it, which is the "
        "absence of an occupant and so not a column the table has"
    ),
}

#: The ONE cell two refusals both claim, and why it is real rather than sloppy: an
#: arriving MASK under a mask PARENT satisfies refusal 1 (a mask parent refuses EVERY
#: arrival) and refusal 3 (an arriving mask is refused by a mask parent) alike.  The
#: code has one raiser for it (``_refuse_mask_on_mask``); the LIST genuinely overlaps.
#: ⚑ Pinned as the ONLY overlap, so a new one is a decision somebody has to make.
_KNOWN_OVERLAP: tuple[tuple[str, str], ...] = (("mask", "parent_is_mask"),)


def _claimed_by(number: int) -> set[tuple[str, str]]:
    _substring, predicate = _REFUSAL_RULES[number]
    return {(k, r) for k, r in _cell_cases() if predicate(k, r)}


def _refusing_cells() -> set[tuple[str, str]]:
    """Every cell whose outcome REFUSES — the plain token and the compound one's half."""
    return {
        (k, r) for k, r in _cell_cases()
        if _cells()[k][r] in ("refuse", "sweep_except_home_refuse")
    }


class TestTheRefusalsAndTheCellsAgree:
    """The numbered list and the table are two views of ONE rule, and they must agree."""

    def test_every_refusal_is_matched_to_exactly_one_manifest_entry(self):
        """Both directions: a seventh refusal is RED, a dropped one is red too."""
        refusals = _block()["refusals"]
        assert len(refusals) == len(_REFUSAL_RULES), (
            f"the manifest carries {len(refusals)} refusals and this file re-expresses "
            f"{len(_REFUSAL_RULES)}"
        )
        for number, (substring, _predicate) in _REFUSAL_RULES.items():
            hits = [entry for entry in refusals if substring in entry]
            assert len(hits) == 1, (
                f"refusal {number} matches {len(hits)} of the manifest's refusals on "
                f"{substring!r} — it must match exactly one"
            )
        unclaimed = [
            entry for entry in refusals
            if not any(sub in entry for sub, _p in _REFUSAL_RULES.values())
        ]
        assert not unclaimed, (
            f"the manifest states refusals this file does not re-express as a "
            f"condition over the table: {unclaimed}"
        )

    def test_every_refusing_cell_is_named_by_a_refusal(self):
        """FORWARD: the table may not refuse for a reason the list never states."""
        claimed = set().union(*(_claimed_by(n) for n in _REFUSAL_RULES))
        orphans = _refusing_cells() - claimed
        assert not orphans, (
            f"cells that REFUSE but that no numbered refusal accounts for: "
            f"{sorted(orphans)} — either the list is behind the table again, or the "
            f"cell is wrong"
        )

    @pytest.mark.parametrize("number", sorted(_REFUSAL_RULES))
    def test_every_refusal_claims_only_refusing_cells(self, number):
        """REVERSE: a refusal may not claim a cell the table ACCEPTS.

        This is the direction that catches a refusal stated too broadly — the way a
        list falls out of step without anything going red.
        """
        wrong = _claimed_by(number) - _refusing_cells()
        assert not wrong, (
            f"refusal {number} claims cells the table does not refuse: "
            f"{sorted((k, r, _cells()[k][r]) for k, r in wrong)}"
        )

    @pytest.mark.parametrize("number", sorted(_REFUSAL_RULES))
    def test_every_refusal_claims_a_cell_or_says_why_not(self, number):
        """ANTI-VACUITY: a predicate that claims nothing checks nothing.

        The one refusal with no cell is classified in :data:`_CLAIMS_NO_CELL` with its
        reason, and a SECOND one appearing there is a decision, not a default.
        """
        if number in _CLAIMS_NO_CELL:
            assert not _claimed_by(number), (
                f"refusal {number} is classified as claiming no cell but now claims "
                f"{sorted(_claimed_by(number))} — remove the classification"
            )
            return
        assert _claimed_by(number), (
            f"refusal {number} claims no cell of the table and is not classified in "
            f"_CLAIMS_NO_CELL — say why, or fix the condition"
        )

    def test_the_only_overlap_is_the_declared_one(self):
        """Two refusals covering one cell is allowed ONCE, and it is stated.

        A new overlap means two entries of a list-that-is-"stated once" now say the
        same thing about one cell, which is exactly the duplication that lets one of
        them rot unnoticed (P10).
        """
        overlaps = {
            cell for cell in _cell_cases()
            if sum(cell in _claimed_by(n) for n in _REFUSAL_RULES) > 1
        }
        assert overlaps == set(_KNOWN_OVERLAP), sorted(overlaps)

    def test_the_uncovered_refusal_is_live(self, tmp_path):
        """Refusal 6 claims no CELL, so it is asserted against the code directly.

        ⚑ It is what makes every copy scenario above carry the home bind: without a
        covering mount this fires first and the cell would never be measured.  Both
        halves are stated — an uncovered dest refuses, and a covered one does not —
        because a refusal that refused everything would satisfy the first alone.
        """
        source = str(_copy_source("copy_file", tmp_path))
        home_only = {HOME_DEST: CollapsedBind(_HOME_SRC, None)}
        with pytest.raises(SettingsError):
            refuse_uncovered_synced(
                home_only, [CollapsedCopy(source, "/nowhere/at/all", None)],
            )
        refuse_uncovered_synced(
            home_only, [CollapsedCopy(source, _COPY_DEST, None)],
        )
