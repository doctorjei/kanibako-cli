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

  def test_a_bind_ABOVE_home_is_refused(self):
    # ⚑ "Nothing may subsume home" — collapse DESIGN §0.2 states it outright
    # ("/home is SAME-or-PARENT ⇒ ERROR") and says it falls out of the EXISTING
    # rules with none added. Rule 1 is that existing rule, and home is pid 0, so
    # every scope's bind arrives with home already collapsed beneath it.
    with pytest.raises(SettingsError, match=r"would subsume the binding"):
      collapse(system=shape(rw={"/home": BindEntry("/h/homes", "Z,U")}))

  def test_a_bind_at_the_ROOT_subsumes_home_and_is_refused(self):
    with pytest.raises(SettingsError, match=r"'/home/agent'"):
      collapse(box=shape(rw={"/": BindEntry("/h/root", "Z,U")}))


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

  def test_a_mask_at_the_ROOT_prunes_everything_beneath_it(self):
    # ``rstrip("/") + "/"`` keeps a bare "/" meaning root rather than "//". Spelled
    # with a MASK because a root BIND now subsumes home and is refused (rule 1).
    collapsed = collapse(
      agent=shape(seed={"/anything": BindEntry("/h/a", "")}),
      box=shape(mask={"/": True}),
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


class TestABindCannotSubsumeABind:
  """RULE 1. ⚑ Only a LATER scope can trip it — within a scope the sort forbids it."""

  def test_a_later_scope_bind_ABOVE_an_earlier_deeper_one_is_refused(self):
    # ⚑⚑ THE DISCRIMINATING SHAPE NEEDS TWO SCOPES. The mount order follows the
    # path VALUE, not the declaration order, so the inner bind could never be
    # reached: shipping it would silently drop a declaration.
    with pytest.raises(SettingsError, match=r"would subsume the binding"):
      collapse(
        system=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )

  def test_the_refusal_NAMES_the_binding_it_would_have_swallowed(self):
    with pytest.raises(SettingsError, match=rf"'{GUEST}/x/y'"):
      collapse(
        system=shape(ro={f"{GUEST}/x/y": BindEntry("/h/deep", "ro")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )

  def test_a_later_scope_bind_INSIDE_an_earlier_one_nests_freely(self):
    # The permitted direction, and the one home relies on: every scoped bind is a
    # child of the foundation.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      box=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/shallow"
    assert collapsed.bindings[f"{GUEST}/x/y"].src == "/h/deep"

  def test_a_SIBLING_sharing_a_prefix_is_not_subsumed(self):
    # The separator guard again, on the SUBSUME side: /home/agent/foobar is not
    # inside /home/agent/foo, so neither refuses the other.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/foobar": BindEntry("/h/foobar", "Z,U")}),
      box=shape(rw={f"{GUEST}/foo": BindEntry("/h/foo", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/foobar"].src == "/h/foobar"
    assert collapsed.bindings[f"{GUEST}/foo"].src == "/h/foo"

  def test_a_bind_does_not_subsume_a_MASK_by_this_rule(self):
    # Rule 1 counts BINDINGS only. A mask beneath is rule 2's business and is
    # removed, never refused.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/y": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/shallow"


class TestABindSubsumesMasksAndCopies:
  """RULES 2 + 6 — a bind CAN subsume a mask or copies, and subsumed means REMOVED."""

  def test_a_bind_REMOVES_the_mask_beneath_it_rather_than_leaving_it_inert(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/y": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
    )
    # Removal is the point: a mask left in the map would be emitted as a tmpfs
    # mount inside a bind that has just replaced the region it was hiding.
    assert f"{GUEST}/x/y" not in collapsed.bindings
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/x"]

  def test_a_bind_removes_EVERY_mask_beneath_it_not_merely_the_first(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/a": True, f"{GUEST}/x/b/c": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
    )
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/x"]

  def test_a_bind_does_not_remove_a_mask_OUTSIDE_it(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/other": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/other"] == MASK

  def test_a_bind_subsumes_the_copies_beneath_it_and_at_its_EXACT_dest(self):
    # The copy half of rule 2. Both directions in one place: the prune's
    # equality case IS the "bind clears a copy at its exact dest" rule.
    collapsed = collapse(
      system=shape(seed={
        f"{GUEST}/x": BindEntry("/h/at", ""),
        f"{GUEST}/x/deep": BindEntry("/h/under", ""),
      }),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.copies == {}


class TestABindCannotBeAChildOfAMask:
  """RULE 3 — the mask's tmpfs would swallow the bind, so it is refused by name."""

  def test_a_bind_INSIDE_an_earlier_scopes_mask_is_refused(self):
    with pytest.raises(SettingsError, match=r"sits inside the mask at"):
      collapse(
        system=shape(mask={f"{GUEST}/x": True}),
        box=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
      )

  def test_the_refusal_survives_an_intervening_scope(self):
    with pytest.raises(SettingsError, match=r"sits inside the mask at"):
      collapse(
        system=shape(mask={f"{GUEST}/x": True}),
        agent=shape(rw={f"{GUEST}/unrelated": BindEntry("/h/u", "Z,U")}),
        box=shape(ro={f"{GUEST}/x/deep/y": BindEntry("/h/deep", "ro")}),
      )

  def test_a_bind_at_the_masks_EXACT_point_is_still_allowed(self):
    # Rule 3 is about being a CHILD. Binding over a mask at its own dest is the
    # ratified override and stays legal.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == CollapsedBind("/h/box", "Z,U,rw")

  def test_a_bind_beside_a_mask_is_not_a_child_of_it(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/xy": True}),
      box=shape(rw={f"{GUEST}/xyz": BindEntry("/h/xyz", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/xyz"].src == "/h/xyz"


class TestAMaskMayBeAChildOfABind:
  """RULE 4 — the permissive half. It guards against rule 3 being made symmetric."""

  def test_a_later_scopes_mask_INSIDE_a_bind_is_allowed_and_both_survive(self):
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/x", "Z,U")}),
      box=shape(mask={f"{GUEST}/x/secret": True}),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/x"
    assert collapsed.bindings[f"{GUEST}/x/secret"] == MASK

  def test_a_mask_inside_a_bind_in_ONE_scope_is_allowed(self):
    collapsed = collapse(
      box=shape(
        rw={f"{GUEST}/x": BindEntry("/h/x", "Z,U")},
        mask={f"{GUEST}/x/secret": True},
      ),
    )
    assert collapsed.bindings[f"{GUEST}/x/secret"] == MASK

  def test_every_mask_is_a_child_of_HOME_and_that_is_ordinary(self):
    # Home is pid 0, so this rule is exercised by every mask that ever collapses.
    collapsed = collapse(box=shape(mask={f"{GUEST}/x": True}))
    assert collapsed.bindings[f"{GUEST}/x"] == MASK
    assert collapsed.bindings[GUEST].src == HOME.src


class TestACopiedDirectoryCannotTakeAMasksPoint:
  """RULE 5 — a copied FILE may land on a mask's exact point; a DIRECTORY may not.

  ⚑ ``stat`` is explicitly allowed here (collapse DESIGN §0 ruling 2: *"just data
  collection… you aren't changing disk state"*), so these fixtures build REAL
  sources on disk rather than routing around the check.
  """

  DEST = f"{GUEST}/planted"

  def masked_seed(self, src) -> CollapsedStore:
    return collapse(
      system=shape(mask={self.DEST: True}),
      box=shape(seed={self.DEST: BindEntry(str(src), "")}),
    )

  def test_a_copied_DIRECTORY_onto_the_masks_exact_point_is_refused(self, tmp_path):
    source = tmp_path / "adir"
    source.mkdir()
    with pytest.raises(SettingsError, match=r"source is a DIRECTORY"):
      self.masked_seed(source)

  def test_a_copied_FILE_may_take_the_masks_exact_point(self, tmp_path):
    source = tmp_path / "afile"
    source.write_text("x")
    collapsed = self.masked_seed(source)
    assert self.DEST not in collapsed.bindings
    assert collapsed.copies[self.DEST] == [BindEntry(str(source), "")]

  def test_a_directory_copy_BENEATH_a_mask_is_not_refused(self, tmp_path):
    # The rule names the mask's EXACT point. A copy at a deeper dest lands inside
    # the tmpfs, which is ordinary.
    source = tmp_path / "adir"
    source.mkdir()
    collapsed = collapse(
      system=shape(mask={self.DEST: True}),
      box=shape(seed={f"{self.DEST}/sub": BindEntry(str(source), "")}),
    )
    assert collapsed.copies[f"{self.DEST}/sub"] == [BindEntry(str(source), "")]
    assert collapsed.bindings[self.DEST] == MASK

  def test_a_directory_copy_onto_an_UNMASKED_dest_is_not_refused(self, tmp_path):
    source = tmp_path / "adir"
    source.mkdir()
    collapsed = collapse(box=shape(seed={self.DEST: BindEntry(str(source), "")}))
    assert collapsed.copies[self.DEST] == [BindEntry(str(source), "")]

  def test_a_source_that_does_not_exist_yet_is_NOT_refused(self, tmp_path):
    # ⚑ NOT COVERED BY THE SIX RULES, and decided narrowly rather than invented:
    # the rule refuses a DIRECTORY, and a source that is not there is not one.
    # (spec:641 blesses a not-yet-existing copy source, so the file-vs-directory
    # test is genuinely undecidable for it — that ruling is owed, not assumed.)
    collapsed = self.masked_seed(tmp_path / "absent")
    assert self.DEST not in collapsed.bindings


class TestTheShallowFirstSortWithinAScope:
  """Ruling 1 — the INTRA-scope mechanism that makes rule 1's error meaningful."""

  def test_a_parent_declared_AFTER_its_child_in_one_scope_still_lands_first(self):
    # ⚑ Without the sort this raises: the child is already collapsed when the
    # parent arrives, and rule 1 cannot tell an ordering artefact from a genuine
    # cross-scope conflict. Within a scope there is no precedence to express, so
    # the sort — not a diagnostic — is the answer.
    collapsed = collapse(
      box=shape(rw={
        f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U"),
        f"{GUEST}/x": BindEntry("/h/shallow", "Z,U"),
      }),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/shallow"
    assert collapsed.bindings[f"{GUEST}/x/y"].src == "/h/deep"

  def test_the_sort_spans_BOTH_arms_of_the_scope(self):
    # ro is walked before rw, so a deep ro entry would otherwise beat a shallow
    # rw one. The sort is over the scope's binds, not over each arm.
    collapsed = collapse(
      box=shape(
        ro={f"{GUEST}/x/y": BindEntry("/h/deep", "ro")},
        rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")},
      ),
    )
    assert collapsed.bindings[f"{GUEST}/x"].opts == "Z,U,rw"
    assert collapsed.bindings[f"{GUEST}/x/y"].opts == "ro"

  def test_three_generations_in_one_scope_collapse_in_depth_order(self):
    collapsed = collapse(
      box=shape(rw={
        f"{GUEST}/a/b/c": BindEntry("/h/c", "Z,U"),
        f"{GUEST}/a": BindEntry("/h/a", "Z,U"),
        f"{GUEST}/a/b": BindEntry("/h/b", "Z,U"),
      }),
    )
    assert [collapsed.bindings[d].src for d in (
      f"{GUEST}/a", f"{GUEST}/a/b", f"{GUEST}/a/b/c",
    )] == ["/h/a", "/h/b", "/h/c"]

  def test_the_arm_order_survives_at_EQUAL_depth(self):
    # The sort is STABLE, so his ro-before-rw walk is preserved: the ro entry is
    # the OCCUPANT the double-bind refusal names.
    with pytest.raises(SettingsError, match=r"'/h/ro' already binds"):
      collapse(
        box=shape(
          ro={f"{GUEST}/x": BindEntry("/h/ro", "ro")},
          rw={f"{GUEST}/x": BindEntry("/h/rw", "Z,U")},
        ),
      )

  def test_the_sort_does_not_reach_ACROSS_scopes(self):
    # Scope order is precedence and depth order is intra-scope only. A shallower
    # bind in a LATER scope is exactly the conflict rule 1 exists to catch.
    with pytest.raises(SettingsError, match=r"would subsume the binding"):
      collapse(
        agent=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )


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
