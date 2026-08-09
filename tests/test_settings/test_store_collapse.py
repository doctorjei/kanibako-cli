"""The step-6a COLLAPSE: four per-scope shapes + the home bind -> two merged maps.

⚑ [[same-arity-shape-flip-passes-silently]] governs this file. The collapse returns
dicts of tuples, so a wrong-but-same-shape answer passes trivially — every test below
asserts MEANING (which dest survived, which mask is GONE, which copy was NOT pruned),
never merely a shape or a count.

Most fixtures build ``StoreShape``s directly: the collapse's contract is over shapes,
the producer has its own file, and the prefix cases need destinations a floor cannot
conveniently spell. ``TestTheLiveRoute`` closes that gap by running the real
``build_launch_snapshot`` → ``snapshot_category_entries`` → producer → collapse chain.

⚑⚑ THE FUNCTION IS PURE AND UNCONSUMED — it merges the INFORMATION and performs no
action, so nothing here asserts an emission, a copy or a mount.
"""

from __future__ import annotations

import copy

import pytest

from kanibako.settings.settings_launch import (
  build_launch_snapshot,
  snapshot_category_entries,
)
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx, SettingsError
from kanibako.settings.settings_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.store_collapse import (
  HOME_DEST,
  MASK,
  CollapsedBind,
  CollapsedStore,
  collapse_store_shapes,
  fold_opt,
)
from kanibako.settings.store_shape import StoreShape, StoreShapeSet, build_store_shape_set

GUEST = GUEST_HOME
HOME = BindEntry("/host/store/box/home", "Z,U")


def shape(*, ro=None, rw=None, mask=None, seed=None, sync=None) -> StoreShape:
  return StoreShape(
    ro=ro or {}, rw=rw or {}, mask=mask or {}, seed=seed or {}, sync=sync or {},
  )


def shape_set(**by_scope: StoreShape) -> StoreShapeSet:
  assert not set(by_scope) - set(SCOPE_CONTAINMENT), "test names a scope that is not one"
  return StoreShapeSet(
    shapes={scope: by_scope.get(scope, StoreShape()) for scope in SCOPE_CONTAINMENT},
  )


def collapse(**by_scope: StoreShape) -> CollapsedStore:
  """Collapse *by_scope* (unnamed scopes empty) over the standard home bind."""
  return collapse_store_shapes(shape_set(**by_scope), HOME)


class TestHomeIsPidZero:
  """Home does not route through ``bindings.rw``; it is the base plate."""

  def test_home_is_bound_before_any_scope_is_read(self):
    collapsed = collapse()
    assert collapsed.bindings == {GUEST: CollapsedBind(HOME.src, HOME.opts)}
    assert collapsed.copies == {}

  def test_home_is_keyed_by_its_NORMALIZED_dest_not_the_tilde(self):
    # ⚑ His ``{"~": home_bind}`` is spelled symbolically. Left literal, home would
    # compare against nothing and could subsume nothing: a dest is a GUEST path and
    # the guest home is a fixed constant, so the foundation key normalizes too.
    bindings = collapse().bindings
    assert HOME_DEST == GUEST
    assert "~" not in bindings
    assert bindings[GUEST].src == HOME.src

  def test_home_options_are_carried_VERBATIM_not_mode_folded(self):
    # The mode fold belongs to the scoped ro/rw ARMS. Home is in no arm.
    assert collapse().bindings[GUEST].opts == "Z,U"

  def test_everything_inside_home_nests_freely_at_every_scope(self):
    collapsed = collapse(
      system=shape(ro={f"{GUEST}/a": BindEntry("/h/a", "ro")}),
      box=shape(rw={f"{GUEST}/b": BindEntry("/h/b", "Z,U")}),
    )
    assert collapsed.bindings[GUEST].src == HOME.src
    assert collapsed.bindings[f"{GUEST}/a"].src == "/h/a"
    assert collapsed.bindings[f"{GUEST}/b"].src == "/h/b"

  def test_a_SECOND_bind_at_home_is_refused(self):
    # "There should only ever be one bind at home" — enforced by the foundation
    # itself, through the ordinary double-bind refusal. No new rule.
    with pytest.raises(SettingsError, match=r"two bindings target the destination"):
      collapse(box=shape(rw={"~": BindEntry("/h/other", "Z,U")}))

  def test_a_bind_ABOVE_home_coexists_and_does_not_displace_home(self):
    # Subsumption is settled by depth-sorted EMISSION (home lands on top), not by a
    # refusal here: /home and /home/agent are two distinct destinations.
    collapsed = collapse(system=shape(rw={"/home": BindEntry("/h/homes", "Z,U")}))
    assert collapsed.bindings["/home"].src == "/h/homes"
    assert collapsed.bindings[GUEST].src == HOME.src


class TestUnmaskingPlantsIntoTheBindings:
  """S1 — the un-mask branch deletes the MASK, never the copy it is about to plant."""

  DEST = f"{GUEST}/planted"

  def test_a_later_scope_copy_removes_the_mask_it_plants_into(self):
    seed = BindEntry("/h/seed", "")
    collapsed = collapse(
      system=shape(mask={self.DEST: True}),
      box=shape(seed={self.DEST: seed}),
    )
    # Both halves matter: the mask that would shadow the copy is GONE, and the copy
    # the branch decided to plant is actually there.
    assert self.DEST not in collapsed.bindings
    assert collapsed.copies[self.DEST] == [seed]

  def test_planting_into_a_masked_dest_does_not_need_an_existing_copy(self):
    # ``del final_copies[dest]`` would KeyError here: nothing has ever been copied
    # to this destination.
    collapsed = collapse(
      agent=shape(mask={self.DEST: True}),
      workset=shape(seed={self.DEST: BindEntry("/h/s", "")}),
    )
    assert self.DEST not in collapsed.bindings

  def test_a_mask_still_prunes_the_copies_that_preceded_it(self):
    # The mask's own scope prunes what was already collapsed; a LATER scope may then
    # plant afresh. The earlier copy must not come back with it.
    early, late = BindEntry("/h/early", ""), BindEntry("/h/late", "")
    collapsed = collapse(
      system=shape(seed={self.DEST: early}),
      agent=shape(mask={self.DEST: True}),
      box=shape(seed={self.DEST: late}),
    )
    assert collapsed.copies[self.DEST] == [late]
    assert self.DEST not in collapsed.bindings

  def test_a_mask_and_a_binding_in_ONE_scope_is_not_an_error(self):
    # S3, RULED: within a scope the mask applies and the cure is not declaring it.
    # No diagnostic — the mask merges after the arms and simply wins.
    collapsed = collapse(
      box=shape(rw={self.DEST: BindEntry("/h/x", "Z,U")}, mask={self.DEST: True}),
    )
    assert collapsed.bindings[self.DEST] == MASK


class TestThePrefixMatchNeedsASeparator:
  """S2 — ``startswith`` is wrong in two directions, and the prune uses both."""

  def test_a_sibling_sharing_a_PREFIX_is_not_inside_the_bind(self):
    # /home/agent/foobar is NOT inside /home/agent/foo.
    kept = BindEntry("/h/foobar", "")
    collapsed = collapse(
      agent=shape(seed={f"{GUEST}/foobar": kept}),
      box=shape(ro={f"{GUEST}/foo": BindEntry("/h/foo", "ro")}),
    )
    assert collapsed.copies == {f"{GUEST}/foobar": [kept]}

  def test_a_sibling_differing_after_the_separator_position_is_not_inside(self):
    # /opt/agent-foo is NOT inside /opt/agent. (Spelled outside home so the case is
    # about the prefix compare and nothing else.)
    kept = BindEntry("/h/dash", "")
    collapsed = collapse(
      agent=shape(seed={"/opt/agent-foo": kept}),
      box=shape(rw={"/opt/agent": BindEntry("/h/opt", "Z,U")}),
    )
    assert collapsed.copies == {"/opt/agent-foo": [kept]}

  def test_the_EXACT_dest_is_pruned(self):
    # Equality is wanted: a mount AT a copy's destination shadows it.
    collapsed = collapse(
      agent=shape(seed={f"{GUEST}/x": BindEntry("/h/x", "")}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.copies == {}
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/mount"

  def test_a_child_of_the_dest_is_pruned(self):
    collapsed = collapse(
      agent=shape(seed={f"{GUEST}/x/deep/file": BindEntry("/h/f", "")}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.copies == {}

  def test_a_mask_prunes_the_copies_beneath_it_too(self):
    # ``key_list`` is ro | rw | mask: a mask shadows a copy exactly as a mount does.
    collapsed = collapse(
      agent=shape(seed={f"{GUEST}/x/file": BindEntry("/h/f", "")}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert collapsed.copies == {}

  def test_a_bind_at_the_ROOT_prunes_everything_beneath_it(self):
    # ``rstrip("/") + "/"`` keeps a bare "/" meaning root rather than "//".
    collapsed = collapse(
      agent=shape(seed={"/anything": BindEntry("/h/a", "")}),
      box=shape(rw={"/": BindEntry("/h/root", "Z,U")}),
    )
    assert collapsed.copies == {}


class TestThePruneIsScopeOrdered:
  """S4 — ``key_list`` is the CURRENT scope's keys ONLY. 🛑 Never accumulate it."""

  def test_a_system_bind_does_not_prune_a_box_copy_declared_LATER(self):
    # Accumulating the prune list would let an OUTER scope reach forward and delete
    # an INNER scope's copy — precedence inverted. The bind and the copy coexist.
    late = BindEntry("/h/late", "")
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      box=shape(seed={f"{GUEST}/x": late}),
    )
    assert collapsed.copies == {f"{GUEST}/x": [late]}
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/sys"

  def test_a_system_bind_does_not_prune_a_box_copy_BENEATH_it(self):
    late = BindEntry("/h/deep", "")
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      box=shape(seed={f"{GUEST}/x/deep": late}),
    )
    assert collapsed.copies == {f"{GUEST}/x/deep": [late]}

  def test_an_OUTER_bind_does_not_reach_forward_through_a_LATER_scopes_prune(self):
    # ⚑⚑ THE TEST THAT ACTUALLY DISCRIMINATES, and the two above do not: within one
    # scope the prune runs BEFORE the plant, so a copy declared in the SAME scope as
    # the prune is never offered to it either way. Only a copy planted in a MIDDLE
    # scope, with a further scope still to run, can be reached by an accumulated
    # list — and here it must not be.
    deep = BindEntry("/h/deep", "")
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      agent=shape(seed={f"{GUEST}/x/deep": deep}),
      box=shape(rw={f"{GUEST}/other": BindEntry("/h/other", "Z,U")}),
    )
    assert collapsed.copies == {f"{GUEST}/x/deep": [deep]}

  def test_an_outer_bind_does_not_reach_forward_to_a_copy_at_its_OWN_dest(self):
    same = BindEntry("/h/same", "")
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      agent=shape(seed={f"{GUEST}/x": same}),
      box=shape(mask={f"{GUEST}/elsewhere": True}),
    )
    assert collapsed.copies == {f"{GUEST}/x": [same]}

  def test_a_bind_and_a_copy_in_ONE_scope_both_survive(self):
    # The prune runs before the plant, so a scope never prunes its OWN copies. His
    # algorithm's order, pinned as-is: changing it is a ruling, not a tidy-up.
    same_scope = BindEntry("/h/copy", "")
    collapsed = collapse(
      box=shape(
        rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")},
        seed={f"{GUEST}/x": same_scope},
      ),
    )
    assert collapsed.copies == {f"{GUEST}/x": [same_scope]}
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/mount"

  def test_an_EARLIER_copy_IS_pruned_by_a_later_scope_bind(self):
    # The positive direction, and the whole point of pruning: a shadowed copy must
    # not be pointlessly performed. Silent removal, not an error.
    collapsed = collapse(
      system=shape(seed={f"{GUEST}/x": BindEntry("/h/early", "")}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.copies == {}


class TestDoubleBind:
  """Row 1's CROSS-SCOPE case — the collapse's own refusal."""

  def test_two_scopes_binding_one_dest_is_refused(self):
    with pytest.raises(SettingsError, match=r"/h/sys.*would bind over|two bindings"):
      collapse(
        system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
      )

  def test_the_ro_and_rw_arms_of_ONE_scope_contend_at_one_dest(self):
    with pytest.raises(SettingsError, match=r"two bindings target the destination"):
      collapse(
        box=shape(
          ro={f"{GUEST}/x": BindEntry("/h/ro", "ro")},
          rw={f"{GUEST}/x": BindEntry("/h/rw", "Z,U")},
        ),
      )

  def test_a_MASK_may_be_bound_over_and_is_not_a_double_bind(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == CollapsedBind("/h/box", "Z,U,rw")

  def test_a_mask_over_a_binding_overrides_it_silently(self):
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_two_SPELLINGS_of_one_dest_are_one_destination(self):
    # Normalization happens at the point of use, so ``~/x`` and ``/home/agent/x``
    # meet in the map and raise the double-bind they actually are — rather than
    # silently overwriting each other inside a pre-pass.
    with pytest.raises(SettingsError, match=r"two bindings target the destination"):
      collapse(
        system=shape(rw={"~/x": BindEntry("/h/sys", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
      )


class TestTheOptsFold:
  """``opts.add(mode)`` returns ``None``. ``opts`` is a STRING and the fold is pure."""

  @pytest.mark.parametrize(
    "opts,token,expected",
    [
      ("Z,U", "rw", "Z,U,rw"),  # ORDER-PRESERVING: the declared tokens keep their place.
      ("ro", "ro", "ro"),  # DEDUP: bindings.ro already carries ``ro``.
      ("Z,ro,U", "ro", "Z,ro,U"),  # dedup wherever the token already sits.
      ("", "rw", "rw"),  # a deliberate "" is NOT upgraded to a category default.
      (None, "ro", "ro"),
      (" Z , U ", "rw", "Z,U,rw"),  # whitespace stripped, same rule as is_read_only.
      (",,", "rw", "rw"),  # empties dropped.
    ],
  )
  def test_the_fold_is_order_preserving_and_dedups(self, opts, token, expected):
    assert fold_opt(opts, token) == expected

  def test_the_fold_returns_a_STRING_never_a_set(self):
    folded = fold_opt("Z,U", "rw")
    assert isinstance(folded, str)
    # A set would have come back "U,Z,rw" — losing the order the user declared is
    # the observable difference, and it is why the fold is a string fold.
    assert folded == "Z,U,rw"

  def test_the_ro_arm_does_not_print_its_mode_twice(self):
    collapsed = collapse(box=shape(ro={f"{GUEST}/x": BindEntry("/h/x", "ro")}))
    assert collapsed.bindings[f"{GUEST}/x"].opts == "ro"

  def test_an_empty_options_bind_folds_to_the_bare_mode(self):
    # ``[src, ""]`` means "no mount options" and must not acquire ``Z,U``.
    collapsed = collapse(box=shape(rw={f"{GUEST}/x": BindEntry("/h/x", "")}))
    assert collapsed.bindings[f"{GUEST}/x"].opts == "rw"

  def test_an_rw_entry_whose_options_say_ro_is_refused_by_name(self):
    # Reachable today (a per-entry override is taken verbatim). Joined, it would
    # read "ro,rw" — a contradiction, not a mount option list.
    with pytest.raises(SettingsError, match=r"sits in the 'rw' arm"):
      collapse(box=shape(rw={f"{GUEST}/x": BindEntry("/h/x", "ro")}))

  def test_a_ro_entry_whose_options_say_rw_is_refused_by_name(self):
    with pytest.raises(SettingsError, match=r"sits in the 'ro' arm"):
      collapse(box=shape(ro={f"{GUEST}/x": BindEntry("/h/x", "Z,rw")}))


class TestPathsAreCaseSensitive:
  """⚖️ The one "typo" whose obvious repair is wrong: Linux paths are case-SENSITIVE."""

  def test_dests_differing_only_in_CASE_are_two_destinations(self):
    upper = BindEntry("/h/upper", "Z,U")
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/lower", "Z,U")}),
      box=shape(rw={"/Home/agent/x": upper}),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/lower"
    assert collapsed.bindings["/Home/agent/x"].src == "/h/upper"

  def test_a_case_variant_bind_does_not_prune_a_copy(self):
    # Case-folding the prune compare would silently delete this copy.
    kept = BindEntry("/h/f", "")
    collapsed = collapse(
      agent=shape(seed={f"{GUEST}/x": kept}),
      box=shape(rw={"/Home/agent/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.copies == {f"{GUEST}/x": [kept]}


class TestTheCopiesMap:
  """One dest holds a LIST — copies combine filewise, not bindwise."""

  def test_copies_at_one_dest_accumulate_in_SCOPE_order(self):
    first, second = BindEntry("/h/first", ""), BindEntry("/h/second", "")
    collapsed = collapse(
      system=shape(seed={f"{GUEST}/x": first}),
      box=shape(seed={f"{GUEST}/x": second}),
    )
    assert collapsed.copies[f"{GUEST}/x"] == [first, second]

  def test_the_copies_map_is_DEST_ordered(self):
    # What his ``SortedDict`` gave for free. The ``bisect_left`` scan it existed to
    # serve is gone with it; the ordering is not.
    collapsed = collapse(
      system=shape(seed={f"{GUEST}/z": BindEntry("/h/z", "")}),
      box=shape(seed={f"{GUEST}/a": BindEntry("/h/a", "")}),
    )
    assert list(collapsed.copies) == [f"{GUEST}/a", f"{GUEST}/z"]

  def test_a_seeded_entry_is_a_COPY_and_never_becomes_a_binding(self):
    collapsed = collapse(box=shape(seed={f"{GUEST}/x": BindEntry("/h/x", "")}))
    assert f"{GUEST}/x" in collapsed.copies
    assert f"{GUEST}/x" not in collapsed.bindings

  def test_a_dotted_dest_survives_WHOLE(self):
    # ⚑⚑ A destination is DATA — never split on its dots.
    collapsed = collapse(box=shape(seed={"~/.cache/uv": BindEntry("/h/uv", "")}))
    assert list(collapsed.copies) == [f"{GUEST}/.cache/uv"]

  def test_the_SYNC_arm_is_never_read(self):
    # ⚑ HIS ALGORITHM WALKS ``shape.seed`` ONLY. ``synced`` reaches neither map, and
    # the ``synced_vs_binding`` refusal is therefore not reproduced here either —
    # the live delivery path still raises it, untouched by step 6. Pinned so that
    # changing it is a DECISION, not a drive-by.
    collapsed = collapse(box=shape(sync={f"{GUEST}/x": BindEntry("/h/x", "")}))
    assert collapsed.copies == {}
    assert list(collapsed.bindings) == [GUEST]


class TestPurity:
  """It merges the INFORMATION. It performs no action and mutates no input."""

  def test_the_input_shapes_are_untouched(self):
    given = shape_set(
      system=shape(rw={"~/x": BindEntry("/h/sys", "Z,U")}, mask={"~/m": True}),
      box=shape(seed={"~/s": BindEntry("/h/s", "")}),
    )
    before = copy.deepcopy(given)
    collapse_store_shapes(given, HOME)
    assert given == before

  def test_two_runs_over_one_input_agree(self):
    given = shape_set(box=shape(rw={"~/x": BindEntry("/h/x", "Z,U")}))
    first = collapse_store_shapes(given, HOME)
    second = collapse_store_shapes(given, HOME)
    assert first == second


class TestTheLiveRoute:
  """The real chain: floor → snapshot → entries → producer → collapse."""

  def ctx(self) -> ResolveCtx:
    return ResolveCtx(
      agent_name="claude", workset_name="myws", host_home="/home/u",
      xdg={"XDG_DATA_HOME": "/data"}, config={},
    )

  def collapsed(self, floor: dict) -> CollapsedStore:
    ctx = self.ctx()
    snap = build_launch_snapshot(
      agent_name="claude", ctx=ctx,
      system_path=None, agent_path=None, workset_path=None, box_path=None,
      default_categories=floor,
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    return collapse_store_shapes(build_store_shape_set(entries), HOME)

  def test_a_real_floor_collapses_to_folded_binds_and_planted_copies(self):
    collapsed = self.collapsed({
      "system.bindings.ro": {"~/ro": ("/h/ro",)},
      "box.caches": {"~/cache": ("/h/cache",)},
      "box.seeded": {"~/seed": ("/h/seed",)},
    })
    assert collapsed.bindings[f"{GUEST}/ro"] == CollapsedBind("/h/ro", "ro")
    assert collapsed.bindings[f"{GUEST}/cache"] == CollapsedBind("/h/cache", "Z,U,rw")
    assert collapsed.bindings[GUEST].src == HOME.src
    assert collapsed.copies[f"{GUEST}/seed"] == [BindEntry("/h/seed", "")]

  def test_a_real_mask_over_a_real_bind_from_an_outer_scope(self):
    collapsed = self.collapsed({
      "system.bindings.rw": {"~/x": ("/h/sys",)},
      "box.masks": ["~/x"],
    })
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_a_real_agent_scope_bind_collapses_before_the_box(self):
    # The agent tier is DISCRIMINATED on the way in and folds under its BARE scope
    # token, which is what makes it collapse third-from-outermost.
    collapsed = self.collapsed({
      "agent.claude.common": {"~/x": ("/h/agent",)},
      "box.seeded": {"~/x/file": ("/h/file",)},
    })
    # The agent bind is collapsed FIRST, so it does not prune the box copy: the
    # prune list is the CURRENT scope's keys only.
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/agent"
    assert collapsed.copies[f"{GUEST}/x/file"] == [BindEntry("/h/file", "")]
