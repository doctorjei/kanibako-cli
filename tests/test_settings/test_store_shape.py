"""The step-4 ``store_shape`` PRODUCER: the five-arm fold + the WITHIN-SCOPE §0 rows.

⚑ Every fixture here is built through the LIVE route —
``build_launch_snapshot`` → ``snapshot_category_entries`` — and never from a
hand-assembled ``CategoryEntry`` list. The producer's whole contract is about what
the REAL emitter hands it (already-concrete mount options, box-resolved guest
dests, the BARE scope token), so a hand-built fixture would assert the producer
against a fiction. The two exceptions are the closed-keyspace refusals, which are
about entries the real emitter CANNOT produce; they say so at their own site.

The seam under test (producer DESIGN §1): the producer owns exactly what §0 leaves
decidable inside ONE scope — two mounts at one dest REFUSE, bar the one exempt
``caches``/``common`` pair. MASKS and every CROSS-scope pair are the COLLAPSE's, and
several tests below assert that the producer deliberately does NOT decide them.

⚑ THOSE ASSERTIONS STAND ALONE SINCE 6-R3. They used to be CONTRASTS — the same
entries run through the retired cross-scope reconcile, which DID decide them, so the
producer's restraint was visible as a difference. That pass is gone; the surviving
claim is the stronger half and the one the collapse depends on: the producer keeps
BOTH sides so the collapse has something to decide BETWEEN. Where the reconcile side
carried the only statement of a rule (the two-bindings remedy text), it was
re-pointed at the surviving raiser rather than deleted.
"""

from __future__ import annotations

import dataclasses

import pytest

from kanibako.errors import CategoryCollisionError
from kanibako.settings.kb_store import BindEntry
from kanibako.settings.settings_categories import (
  _DELIVERY,
  CategoryEntry,
  raise_binding_vs_binding,
  secret_path_deliveries,
)
from kanibako.settings.settings_launch import (
  build_launch_snapshot,
  snapshot_category_entries,
)
from kanibako.settings.settings_resolve import ResolveCtx, SettingsError
from kanibako.settings.store_shape import (
  _ARM,
  _NO_ARM,
  CopyRow,
  StoreShape,
  StoreShapeSet,
  build_store_shape_set,
)

GUEST = "/home/agent"
DEST = f"{GUEST}/x"


def make_ctx() -> ResolveCtx:
  return ResolveCtx(
    agent_name="claude", workset_name="myws", host_home="/home/u",
    xdg={"XDG_DATA_HOME": "/data"}, config={},
  )


def live_entries(floor: dict) -> list[CategoryEntry]:
  """*floor* → the entry list a real launch produces (the ONE live route)."""
  ctx = make_ctx()
  snap = build_launch_snapshot(
    agent_name="claude", ctx=ctx,
    system_path=None, agent_path=None, workset_path=None, box_path=None,
    default_categories=floor,
  )
  return snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)


def shapes(floor: dict) -> StoreShapeSet:
  return build_store_shape_set(live_entries(floor))


class TestTheFold:
  """`bindings.ro`→ro · `bindings.rw`+`caches`+`common`→rw · masks→mask ·
  seeded→seed · synced→sync."""

  FLOOR = {
    "box.bindings.ro": {"~/ro": ("/h/ro",)},
    "box.bindings.rw": {"~/rw": ("/h/rw",)},
    "box.caches": {"~/cache": ("/h/cache",)},
    "box.common": {"~/common": ("/h/common",)},
    "box.seeded": {"~/seed": ("/h/seed",)},
    "box.synced": {"~/sync": ("/h/sync",)},
    "box.masks": ["~/masked"],
  }

  def test_store_shape_has_exactly_the_five_ratified_arms(self):
    # BY CONSTRUCTION, not by convention: a structure can hold no sixth arm.
    names = [f.name for f in dataclasses.fields(StoreShape)]
    assert names == ["ro", "rw", "mask", "seed", "sync"]

  def test_every_category_lands_in_its_own_arm(self):
    box = shapes(self.FLOOR)["box"]
    assert set(box.ro) == {f"{GUEST}/ro"}
    # ⚑ THE MATERIALISATION: caches and common are ABSTRACT rw mounts and land in
    # ``rw`` beside the concrete binding — there is no ``caches`` arm to land in.
    assert set(box.rw) == {f"{GUEST}/rw", f"{GUEST}/cache", f"{GUEST}/common"}
    assert set(box.mask) == {f"{GUEST}/masked"}
    # ⚑ The copy arms are LISTS, so their dests are read off the rows.
    assert [row.dest for row in box.seed] == [f"{GUEST}/seed"]
    assert [row.dest for row in box.sync] == [f"{GUEST}/sync"]

  def test_bind_arms_are_dest_keyed_src_opts_entries(self):
    box = shapes(self.FLOOR)["box"]
    assert box.rw[f"{GUEST}/rw"] == BindEntry("/h/rw", "Z,U")
    assert box.ro[f"{GUEST}/ro"] == BindEntry("/h/ro", "ro")

  def test_copy_arms_are_ordered_rows_that_CARRY_their_dest(self):
    # ⚑ The MOUNTS arbitrate at a dest and are keyed by it; the COPIES do not and
    # are not. A dest is DATA on a copy row, never its key.
    box = shapes(self.FLOOR)["box"]
    assert box.seed == [CopyRow(f"{GUEST}/seed", BindEntry("/h/seed", ""))]
    assert box.sync == [CopyRow(f"{GUEST}/sync", BindEntry("/h/sync", ""))]

  def test_copies_stay_copies(self):
    # ⚑ The fold changes KEY SHAPE only. A ``seeded``/``synced`` COPY must never
    # surface in a MOUNT arm.
    box = shapes(self.FLOOR)["box"]
    for mount_arm in (box.ro, box.rw, box.mask):
      assert f"{GUEST}/seed" not in mount_arm
      assert f"{GUEST}/sync" not in mount_arm
    assert [row.entry.src for row in box.seed] == ["/h/seed"]
    assert [row.entry.src for row in box.sync] == ["/h/sync"]

  def test_abstract_mounts_carry_the_relabel_policy_through_the_fold(self):
    # The category default supplies TWO facts through one value: the MODE and the
    # RELABEL POLICY. The collapse folds the mode and CANNOT recover ``Z,U``,
    # because by then the category is gone — so it must already be on the entry.
    box = shapes(self.FLOOR)["box"]
    assert box.rw[f"{GUEST}/cache"].opts == "Z,U"
    assert box.rw[f"{GUEST}/common"].opts == "Z,U"

  def test_a_deliberate_empty_options_bind_is_carried_verbatim(self):
    # ⚑ THE REASON THE PRODUCER MUST NOT RE-DERIVE OPTIONS. ``[src, ""]`` means
    # "no mount options" — an rw mount with no relabel, which is well-formed.
    # ``entry.options or bind_options(category)`` would silently upgrade it to
    # ``Z,U``; that is a behaviour change, not a no-op.
    box = shapes({"box.bindings.rw": {"~/x": ("/h/x", "")}})["box"]
    assert box.rw[DEST] == BindEntry("/h/x", "")

  def test_a_per_entry_options_override_survives_the_fold(self):
    box = shapes({"box.caches": {"~/x": ("/h/x", "ro")}})["box"]
    assert box.rw[DEST].opts == "ro"

  def test_a_dotted_dest_survives_whole(self):
    # ⚑⚑ A destination is DATA. ``~/.cache/uv`` must arrive as ONE key, not
    # shattered on its dots.
    box = shapes({"box.caches": {"~/.cache/uv": ("/h/uv",)}})["box"]
    assert set(box.rw) == {f"{GUEST}/.cache/uv"}

  def test_the_mask_arm_is_presence_only(self):
    # The collapse touches ``shape.mask`` in exactly two places and BOTH iterate
    # keys; no mask VALUE is ever unpacked. The arm carries no bind entry.
    box = shapes(self.FLOOR)["box"]
    assert box.mask == {f"{GUEST}/masked": True}


class TestACopyArmIsAFlatList:
  """Spec `:147-149` — the copy leaves are FLAT SCOPE-ORDERED LISTS and *"nothing is
  arbitrated at a destination"*."""

  # ⚑⚑ HAND-BUILT ON PURPOSE — the THIRD stated exception to this file's live-route
  # rule, and it is stated here rather than assumed. MEASURED 2026-08-11: the live
  # emitter cannot yet produce two copy rows at one dest in one scope, because the
  # store LEAF is itself dest-keyed — ``~/x`` and ``/home/agent/x`` normalize into
  # one key inside ``build_launch_snapshot``, and ``agent.default`` vs
  # ``agent.<active>`` resolve through the cascade before an entry exists. So these
  # assert the ARM'S CONTRACT, not a reproduction of a live loss.
  #
  # The contract is not academic: NOTHING else in the chain prunes a copy for
  # sharing a destination (``test_store_collapse.py::TestNothingPrunesACopy``), so a
  # dest-keyed ``seed``/``sync`` arm would be the ONE place where a declared copy can
  # vanish with no warning at any log level, with the survivor chosen by raw dest
  # SPELLING rather than by the user's file order.

  def rows(self, category: str, *srcs: str) -> list[CategoryEntry]:
    """*srcs* as that many copy entries at ONE dest, in the order given."""
    return [
      CategoryEntry(
        category=category, scope="box", box_dest=DEST, host_src=src,
        delivery="COPY", options="", name=DEST,
        key_segments=("box", category, DEST),
      )
      for src in srcs
    ]

  def test_TWO_seeded_rows_at_ONE_dest_BOTH_survive_in_declaration_order(self):
    box = build_store_shape_set(self.rows("seeded", "/h/first", "/h/second"))["box"]
    assert box.seed == [
      CopyRow(DEST, BindEntry("/h/first", "")),
      CopyRow(DEST, BindEntry("/h/second", "")),
    ]

  def test_TWO_synced_rows_at_ONE_dest_BOTH_survive_in_declaration_order(self):
    box = build_store_shape_set(self.rows("synced", "/h/first", "/h/second"))["box"]
    assert box.sync == [
      CopyRow(DEST, BindEntry("/h/first", "")),
      CopyRow(DEST, BindEntry("/h/second", "")),
    ]

  def test_a_repeat_does_not_reorder_a_copy_at_a_NESTING_dest(self):
    # ⚑ The fold decides the §0 rows per DEST but must emit in DECLARATION order.
    # Grouping by dest pulls the two ``DEST`` rows together and lands the nested
    # one LAST, inverting which copy overwrites which at apply time.
    inner = CategoryEntry(
      category="seeded", scope="box", box_dest=f"{DEST}/inner", host_src="/h/inner",
      delivery="COPY", options="", name=f"{DEST}/inner",
      key_segments=("box", "seeded", f"{DEST}/inner"),
    )
    first, second = self.rows("seeded", "/h/first", "/h/second")
    box = build_store_shape_set([first, inner, second])["box"]
    assert [row.entry.src for row in box.seed] == ["/h/first", "/h/inner", "/h/second"]

  def test_a_copy_row_is_NEVER_a_mount_row(self):
    # ⚑ The arms diverge in TYPE, which is the whole repair: a mount arm arbitrates
    # at a dest and is keyed by it, a copy arm does neither.
    box = build_store_shape_set(self.rows("seeded", "/h/first", "/h/second"))["box"]
    assert (box.ro, box.rw, box.mask) == ({}, {}, {})


class TestPerScope:
  """Four scopes, kept SEPARATE — comparing them is the collapse's job."""

  def test_every_scope_has_a_shape_even_when_it_declared_nothing(self):
    # The collapse indexes all four unconditionally; a missing scope would be a
    # KeyError inside his loop.
    produced = shapes({"box.bindings.rw": {"~/x": ("/h/x",)}})
    assert set(produced.shapes) == {"system", "agent", "workset", "box"}
    for scope in ("system", "agent", "workset"):
      assert produced[scope] == StoreShape()

  def test_subscript_is_the_collapse_spelling(self):
    produced = shapes({"system.bindings.rw": {"~/x": ("/h/x",)}})
    assert produced["system"] is produced.shapes["system"]

  def test_the_agent_scope_folds_under_its_bare_token(self):
    # The snapshot's agent tier is DISCRIMINATED (``agent.<active>``); the entry's
    # scope — and so the shape it lands in — is the BARE precedence token.
    produced = shapes({"agent.claude.common": {"~/x": ("/h/a",)}})
    assert produced["agent"].rw[DEST].src == "/h/a"
    assert produced["box"] == StoreShape()

  def test_one_dest_in_two_scopes_is_LEFT_FOR_THE_COLLAPSE(self):
    # ⚑ The CROSS-SCOPE case is the COLLAPSE's (its double-bind error), so the
    # producer keeps BOTH — one per scope shape — and says nothing.
    #
    # ⚑ THE CONTRAST HALF DIED AT 6-R3: it also asserted that the retired
    # cross-scope reconcile REFUSED these very entries, which is what made the
    # producer's silence legible. The collapse refuses them instead, and that is
    # asserted where the collapse is —
    # ``test_store_collapse.py`` (``_refuse_bind_over_bind``). What is left here is
    # the producer's own contract, which is what this file is for.
    floor = {
      "system.bindings.rw": {"~/x": ("/h/sys",)},
      "box.bindings.rw": {"~/x": ("/h/box",)},
    }
    produced = shapes(floor)
    assert produced["system"].rw[DEST].src == "/h/sys"
    assert produced["box"].rw[DEST].src == "/h/box"
    assert produced.warnings == ()

  def test_a_cross_scope_abstraction_pair_is_left_whole(self):
    # A CROSS-SCOPE abstraction pair is the COLLAPSE's, never the producer's — it
    # REFUSES them, as it does any two mounts at one dest. The producer must not
    # pre-decide it: both entries survive, in their own scopes.
    produced = shapes({
      "system.caches": {"~/x": ("/h/sys",)},
      "box.common": {"~/x": ("/h/box",)},
    })
    assert produced["system"].rw[DEST].src == "/h/sys"
    assert produced["box"].rw[DEST].src == "/h/box"
    assert produced.warnings == ()


class TestWithinScopeRows:
  """The refusals and the one warning §0 leaves decidable inside ONE scope."""

  def test_row1_ro_and_rw_at_one_dest_in_one_scope_is_refused(self):
    # ⚑ THE CASE THAT IS CURRENTLY INVISIBLE: the two fold into DIFFERENT arms, so
    # a producer that skipped the check would let both survive silently, one per
    # arm, and the contradiction would surface nowhere.
    floor = {
      "box.bindings.ro": {"~/x": ("/h/a",)},
      "box.bindings.rw": {"~/x": ("/h/b",)},
    }
    with pytest.raises(CategoryCollisionError) as excinfo:
      build_store_shape_set(live_entries(floor))
    err = excinfo.value
    assert err.kind == "binding_vs_binding"
    assert err.box_dest == DEST
    assert {key for key, _ in err.entries} == {
      f"box.bindings.ro.{DEST}", f"box.bindings.rw.{DEST}",
    }

  def test_row1_uses_the_one_spec_mandated_remedy_text(self):
    # Single-sourced: ONE remedy text exists, in ``raise_binding_vs_binding``, and
    # the producer must not have grown a second spelling of it.
    #
    # ⚑ RE-POINTED AT THE RAISER (6-R3). The other side was the retired cross-scope
    # reconcile, which reached the SAME public raiser — so the comparison was really
    # always with that function, and it is compared with it directly now. The other
    # two callers (``secret_path_deliveries``, ``narrow_table_winners``) reach it the
    # same way; there is no second text for any of them to drift toward.
    floor = {
      "box.bindings.ro": {"~/x": ("/h/a",)},
      "box.bindings.rw": {"~/x": ("/h/b",)},
    }
    entries = [
      e for e in live_entries(floor) if e.category.startswith("bindings")
    ]
    with pytest.raises(CategoryCollisionError) as producer_err:
      build_store_shape_set(live_entries(floor))
    with pytest.raises(CategoryCollisionError) as raiser_err:
      raise_binding_vs_binding(DEST, entries)
    assert str(producer_err.value) == str(raiser_err.value)
    assert "SUPPRESS" in str(producer_err.value)

  def test_row3_an_abstraction_onto_an_occupied_dest_is_refused(self):
    floor = {
      "box.bindings.rw": {"~/x": ("/h/base",)},
      "box.common": {"~/x": ("/h/ext",)},
    }
    with pytest.raises(CategoryCollisionError) as excinfo:
      build_store_shape_set(live_entries(floor))
    err = excinfo.value
    assert err.kind == "extension_onto_occupied"
    # The BASE survives and the EXTENSION is refused — the message must say which
    # is which, so the two keys are not interchangeable here.
    assert f"'box.common.{DEST}' extends onto" in str(err)
    assert f"'box.bindings.rw.{DEST}' already binds" in str(err)

  def test_row3_fires_for_a_read_only_base_too(self):
    with pytest.raises(CategoryCollisionError) as excinfo:
      build_store_shape_set(live_entries({
        "box.bindings.ro": {"~/x": ("/h/base",)},
        "box.caches": {"~/x": ("/h/ext",)},
      }))
    assert excinfo.value.kind == "extension_onto_occupied"

  def test_row5_two_abstractions_at_one_dest_warn_and_the_last_wins(self):
    produced = shapes({
      "box.caches": {"~/x": ("/h/cache",)},
      "box.common": {"~/x": ("/h/common",)},
    })
    # The existing ordering stands: ``common`` sorts after ``caches``, so it wins
    # — the same answer the retired ladder gave.
    assert produced["box"].rw[DEST] == BindEntry("/h/common", "Z,U")
    assert len(produced.warnings) == 1
    warning = produced.warnings[0]
    assert warning.box_dest == DEST
    assert warning.scope == "box"
    assert warning.winner_key == f"box.common.{DEST}"
    assert warning.loser_keys == (f"box.caches.{DEST}",)

  def test_row5_warns_every_launch_and_never_raises(self):
    # §0's exempt pair is PROCEED + WARN, not refuse and not silent. The warning is
    # the producer stays pure and the one emission seam renders it.
    floor = {
      "box.caches": {"~/x": ("/h/cache",)},
      "box.common": {"~/x": ("/h/common",)},
    }
    first, second = shapes(floor), shapes(floor)
    assert len(first.warnings) == len(second.warnings) == 1
    assert "ignored" in first.warnings[0].message()

  def test_a_same_scope_abstraction_pair_at_DIFFERENT_dests_is_no_collision(self):
    produced = shapes({
      "box.caches": {"~/a": ("/h/cache",)},
      "box.common": {"~/b": ("/h/common",)},
    })
    assert produced.warnings == ()
    assert set(produced["box"].rw) == {f"{GUEST}/a", f"{GUEST}/b"}


class TestTheMaskTrap:
  """⚑⚑ The MASK rule is the COLLAPSE's. Applying it per scope is a SILENT wrong answer."""

  FLOOR = {
    "box.bindings.rw": {"~/x": ("/h/bound",)},
    "box.masks": ["~/x"],
  }

  def test_a_mask_and_a_binding_at_one_dest_in_one_scope_BOTH_survive(self):
    # If the producer applied the mask rule, the mask would EAT the binding here and
    # ``shape.rw`` would reach the collapse missing the entry the collapse's own
    # mask loop is written to override. The collapse would be correct and the
    # answer still wrong.
    box = shapes(self.FLOOR)["box"]
    assert box.rw[DEST] == BindEntry("/h/bound", "Z,U")
    assert box.mask == {DEST: True}

  # 🕯️ ``test_the_cross_scope_pass_by_contrast_resolves_it_to_the_mask`` DIED AT
  # 6-R3. It ran the SAME entries through the retired cross-scope reconcile and
  # asserted ONE winner, the mask — the per-scope structure the producer must not
  # throw away, shown by exhibiting who does throw it away. The collapse's mask loop
  # is the successor and is pinned where the collapse is
  # (``test_store_collapse.py``); the sibling above states the producer's half, which
  # is the only half this file is responsible for.

  def test_a_mask_in_one_scope_does_not_touch_a_binding_in_another(self):
    produced = shapes({
      "system.bindings.rw": {"~/x": ("/h/bound",)},
      "box.masks": ["~/x"],
    })
    assert produced["system"].rw[DEST].src == "/h/bound"
    assert produced["system"].mask == {}
    assert produced["box"].mask == {DEST: True}
    assert produced["box"].rw == {}

  def test_a_mask_over_an_abstraction_does_not_suppress_the_row5_warning(self):
    # The exempt pair's ambiguity is real whether or not something later hides its
    # consequence — the same reasoning §0 gives for evaluating the refusals before
    # the mask override.
    produced = shapes({
      "box.caches": {"~/x": ("/h/cache",)},
      "box.common": {"~/x": ("/h/common",)},
      "box.masks": ["~/x"],
    })
    assert len(produced.warnings) == 1
    assert produced["box"].rw[DEST].src == "/h/common"
    assert produced["box"].mask == {DEST: True}


class TestCategoriesWithNoArm:
  """``env`` is not a path delivery; ``secret_path`` is PARKED out of the shape."""

  def test_the_arm_table_covers_every_declared_category_disjointly(self):
    # ⚑ CLOSED KEYSPACE: adding a category without deciding its arm must fail
    # loudly here rather than silently dropping its entries at launch.
    assert set(_ARM) | _NO_ARM == set(_DELIVERY)
    assert not (set(_ARM) & _NO_ARM)

  def test_env_reaches_no_arm(self):
    box = shapes({"box.env": {"FOO": "bar"}})["box"]
    assert box == StoreShape()

  def test_secret_path_reaches_no_arm(self):
    # PARKED, deliberately: it is a CONCRETE mount whose dest is fixed by
    # construction, and the five-key shape was ratified without it.
    box = shapes({"box.secret_path": {"TOK": "/h/tok"}})["box"]
    assert box == StoreShape()
    # It is still a live CONCRETE mount on the shipped route — the producer
    # parking it does not delete it from the launch. ⚑ Read at the LAUNCH SEAM since
    # 6-R3: ``secret_path`` has no arm in the shape, so the carrier is where a secret
    # is delivered from and the only place its survival is observable.
    delivered = secret_path_deliveries(
      live_entries({"box.secret_path": {"TOK": "/h/tok"}}),
    )
    assert [m.category for m in delivered] == ["secret_path"]

  def test_an_undeclared_category_is_REFUSED_not_dropped(self):
    # ⚑ HAND-BUILT ON PURPOSE: the live emitter cannot produce this entry, and
    # that is the point — the refusal is what keeps "an undeclared key is not a
    # key" true if some future emitter can.
    bogus = CategoryEntry(
      category="bogus", scope="box", box_dest=DEST, host_src="/h/x",
      delivery="MOUNT", options="", name=DEST,
      key_segments=("box", "bogus", DEST),
    )
    with pytest.raises(SettingsError, match="no store_shape arm"):
      build_store_shape_set([bogus])

  def test_an_undeclared_scope_is_REFUSED_not_dropped(self):
    bogus = CategoryEntry(
      category="bindings.rw", scope="cli", box_dest=DEST, host_src="/h/x",
      delivery="MOUNT", options="Z,U", name=DEST,
      key_segments=("cli", "bindings", "rw", DEST),
    )
    with pytest.raises(SettingsError, match="not one of the declared scopes"):
      build_store_shape_set([bogus])
