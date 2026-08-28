"""The step-6a COLLAPSE: four per-scope shapes + the home bind -> a bind map + TWO copy LISTS.

⚑ [[same-arity-shape-flip-passes-silently]] governs this file. The collapse returns
dicts and lists of tuples, so a wrong-but-same-shape answer passes trivially — every
test below asserts MEANING (which dest survived, which mask is GONE, which copy
REPEATS), never merely a shape or a count.

⚑⚑ THE ORACLE TRAP, sprung three times in this module: within ONE scope the parent-first
bind sort and the child-first mask sort make an intra-scope conflict unconstructible, so
a single-scope test passes with the rule or without it. Every discriminating case for the
bind and mask rules below is therefore TWO- or THREE-scope, and the single-scope cases are
present only to pin the SORTS themselves.

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

from kanibako.settings.kb_store import SCOPE_CONTAINMENT, BindEntry
from kanibako.settings.settings_launch import (
  build_launch_snapshot,
  snapshot_category_entries,
)
from kanibako.settings.settings_resolve import GUEST_HOME, ResolveCtx, SettingsError
from kanibako.settings.store_collapse import (
  CLI_PROVENANCE_SCOPE,
  DERIVED_MASKED,
  DERIVED_MOUNT,
  DERIVED_SUPERSEDED,
  HOME_DEST,
  MASK,
  CollapsedBind,
  CollapsedCopy,
  CollapsedEnv,
  CollapsedStore,
  Declaration,
  collapse_env,
  collapse_seeded,
  collapse_store_shapes,
  derivation_result,
  fold_opt,
  pair_declarations,
)
from kanibako.settings.store_shape import (
  CopyRow,
  StoreShape,
  StoreShapeSet,
  build_store_shape_set,
)

GUEST = GUEST_HOME
HOME = BindEntry("/host/store/box/home", "Z,U")

#: The unified bind refusal — one message for "at my point" and "inside me" alike.
COLLIDES = r"collides with the binding\(s\) already collapsed"


def shape(*, ro=None, rw=None, mask=None, seed=None, sync=None) -> StoreShape:
  return StoreShape(
    ro=ro or {}, rw=rw or {}, mask=mask or {}, seed=seed or [], sync=sync or [],
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
    assert collapsed.seeded == []
    assert collapsed.synced == []

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
    # itself, through the ONE bind refusal. Its predicate is inclusive of equality
    # on the subsume side, so a bind AT an occupied dest and a bind OVER one are the
    # same refusal, not two.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(box=shape(rw={"~": BindEntry("/h/other", "Z,U")}))

  def test_the_refusal_at_home_NAMES_the_source_already_bound_there(self):
    with pytest.raises(SettingsError, match=r"'/host/store/box/home'"):
      collapse(box=shape(rw={"~": BindEntry("/h/other", "Z,U")}))

  def test_the_refusal_PRESCRIBES_THE_SAME_REMEDY_THE_SIBLING_PUBLISHES(self):
    """⚑⚑ CUTOVER 2c — the wording is load-bearing and NOTHING ELSE PINS IT.

    Until 2c every one of these refusals was swallowed at ``debug``; the message is a
    user's ONLY diagnostic now. It used to end "Suppress one of them, or bind them at
    distinct destinations" — two paths, no key, and no statement of what "suppress"
    means. The identical class of refusal a layer up
    (``settings_categories.raise_binding_vs_binding``) already publishes the correct
    remedy, and MIGRATION.md §2.2 ships it verbatim, so this says the same thing in the
    same words: one cure, one mechanism.

    🛑 The prefix (``COLLIDES``) was the only thing any test pinned, which meant the
    remedy could be reworded — or made wrong again — with the suite green.

    ⚑ "Suppress" is a present-``None`` at the key, resolved to an OMIT at cascade
    merge. It is NOT masking, and a scope clause about masks would send the user to the
    wrong mechanism (that error was made once and caught in review).
    """
    with pytest.raises(SettingsError) as excinfo:
      collapse(box=shape(rw={"~": BindEntry("/h/other", "Z,U")}))

    message = str(excinfo.value)
    for clause in (
      "you must SUPPRESS the entry you do not want and then declare the one you do",
      "An override is not enough: these are two different KEYS, so both survive the "
      "cascade",
      "Set the unwanted key to null in the settings file for its scope",
      "a file may write its own scope and the scopes it contains",
    ):
      assert clause in message, message
    assert "mask" not in message.lower(), message

  def test_a_bind_ABOVE_home_is_refused(self):
    # ⚑ "Nothing may subsume home" — collapse DESIGN §0.2 states it outright
    # ("/home is SAME-or-PARENT ⇒ ERROR") and says it falls out of the EXISTING
    # rules with none added. The bind refusal is that existing rule, and home is
    # pid 0, so every scope's bind arrives with home already collapsed beneath it.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(system=shape(rw={"/home": BindEntry("/h/homes", "Z,U")}))

  def test_a_bind_at_the_ROOT_subsumes_home_and_is_refused(self):
    with pytest.raises(SettingsError, match=r"'/home/agent'"):
      collapse(box=shape(rw={"/": BindEntry("/h/root", "Z,U")}))


class TestTheSeedPassIsAConcatenation:
  """⚖️ RULED 2026-08-09d — seeds apply to the HOME bind ALONE, so nothing arbitrates them."""

  def test_the_seed_list_is_the_CONCATENATION_in_SCOPE_order(self):
    # ⚑⚑ THE DESTS DISAGREE WITH THE SCOPES ON PURPOSE: ``z`` is declared in the
    # OUTERMOST scope and ``a`` in the innermost, so a list that came back
    # dest-sorted — which is what the dropped ``SortedDict`` gave — reverses this
    # answer. With ``a``/``z`` the other way round the test passes either way and
    # pins nothing; a mutant proved exactly that.
    collapsed = collapse(
      box=shape(seed=[CopyRow(f"{GUEST}/a", BindEntry("/h/box", ""))]),
      system=shape(seed=[CopyRow(f"{GUEST}/z", BindEntry("/h/sys", ""))]),
    )
    assert collapsed.seeded == [
      CollapsedCopy("/h/sys", f"{GUEST}/z", ""),
      CollapsedCopy("/h/box", f"{GUEST}/a", ""),
    ]

  def test_a_dest_REPEATS_and_that_IS_the_seeded_overlay(self):
    # ⚑⚑ THE POINT OF THE LIST. The layered ``seeded.template`` trio is one row per
    # scope, every one of them targeting ``~``. A dest-keyed map would collapse the
    # three into one and silently drop two layers; the later entry must instead
    # survive AFTER the earlier one and overwrite it FILEWISE at apply time.
    collapsed = collapse(
      system=shape(seed=[CopyRow("~", BindEntry("/h/base", ""))]),
      agent=shape(seed=[CopyRow("~", BindEntry("/h/agent", ""))]),
      box=shape(seed=[CopyRow("~", BindEntry("/h/box", ""))]),
    )
    assert [entry.src for entry in collapsed.seeded] == ["/h/base", "/h/agent", "/h/box"]
    assert {entry.dest for entry in collapsed.seeded} == {GUEST}

  def test_a_dest_REPEATS_INSIDE_ONE_SCOPE_TOO_and_the_arm_carries_both(self):
    # ⚑⚑ THE OTHER HALF OF THE REPEAT. The test above pins a repeat ACROSS scopes,
    # which a dest-keyed arm survives because each scope holds its own map. WITHIN
    # one scope it does not: that arm kept the LAST row and dropped the rest with no
    # warning at any log level. Spec `:147-149` — the leaf is a FLAT list and
    # "nothing is arbitrated at a destination", so both rows reach the output in the
    # order they were declared. RED against a dest-keyed ``seed`` arm.
    collapsed = collapse(
      box=shape(seed=[
        CopyRow(f"{GUEST}/x", BindEntry("/h/first", "")),
        CopyRow(f"{GUEST}/x", BindEntry("/h/second", "")),
      ]),
    )
    assert collapsed.seeded == [
      CollapsedCopy("/h/first", f"{GUEST}/x", ""),
      CollapsedCopy("/h/second", f"{GUEST}/x", ""),
    ]

  def test_a_per_row_refusal_fires_from_INSIDE_a_repeated_arm(self):
    # ⚑ The refusals are PER ROW, not per dest: a list arm must not let a second
    # row at one dest ride in behind the first unchecked.
    with pytest.raises(SettingsError, match="outside the home binding"):
      collapse(
        box=shape(seed=[
          CopyRow(f"{GUEST}/ok", BindEntry("/h/ok", "")),
          CopyRow("/opt/thing", BindEntry("/h/nope", "")),
        ]),
      )

  def test_the_dest_is_CARRIED_on_the_entry_and_NORMALIZED(self):
    collapsed = collapse(box=shape(seed=[CopyRow("~/x", BindEntry("/h/x", ""))]))
    assert collapsed.seeded == [CollapsedCopy("/h/x", f"{GUEST}/x", "")]

  def test_a_dotted_dest_survives_WHOLE(self):
    # ⚑⚑ A destination is DATA — never split on its dots.
    collapsed = collapse(box=shape(seed=[CopyRow("~/.cache/uv", BindEntry("/h/uv", ""))]))
    assert [entry.dest for entry in collapsed.seeded] == [f"{GUEST}/.cache/uv"]

  def test_a_seeded_entry_is_a_COPY_and_never_becomes_a_binding(self):
    collapsed = collapse(box=shape(seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/x", ""))]))
    assert [entry.dest for entry in collapsed.seeded] == [f"{GUEST}/x"]
    assert f"{GUEST}/x" not in collapsed.bindings

  def test_the_SYNC_arm_reaches_the_SYNC_list_and_never_the_seed_one(self):
    # ⚑ INVERTED 2026-08-10b. The arm used to reach NEITHER output — the collapse
    # dropped every ``synced`` row, so a declared credential sync gave 1 copy on the
    # live route and 0 here. The two lists must now stay disjoint in BOTH directions.
    collapsed = collapse(box=shape(sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/x", ""))]))
    assert collapsed.seeded == []
    assert collapsed.synced == [CollapsedCopy("/h/x", f"{GUEST}/x", "")]
    assert list(collapsed.bindings) == [GUEST]

  def test_a_SEED_arm_reaches_the_seed_list_and_never_the_sync_one(self):
    collapsed = collapse(box=shape(seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/x", ""))]))
    assert collapsed.seeded == [CollapsedCopy("/h/x", f"{GUEST}/x", "")]
    assert collapsed.synced == []

  def test_the_two_lists_do_not_cross_contaminate_when_BOTH_arms_are_declared(self):
    # ⚑ Both arms, one scope, distinct dests: each row lands in exactly one list.
    collapsed = collapse(
      box=shape(
        seed=[CopyRow(f"{GUEST}/s", BindEntry("/h/seed", ""))],
        sync=[CopyRow(f"{GUEST}/y", BindEntry("/h/sync", ""))],
      ),
    )
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/s", "")]
    assert collapsed.synced == [CollapsedCopy("/h/sync", f"{GUEST}/y", "")]

  def test_one_dest_in_BOTH_arms_keeps_BOTH_ROWS(self):
    # 🛑 RE-INVERTED 2026-08-11, and this time by RULING rather than by measurement.
    # Cutover 2b-2 added a prune here that dropped the seed row at a dest a sync also
    # claimed, reproducing ``settings_categories._resolve_copy_group``. Jei replaced
    # the question with a DELIVERY rule — *"write synced to it once at creation,
    # irrespective of date"* — and confirmed the prune comes out with it.
    #
    # ⚑ "Nothing is arbitrated at a destination" (spec :147-149) now holds ACROSS the
    # two arms as well as within one. Both rows survive; ORDER decides, at create:
    # the seed writes, then ``start._sync_box_at_create`` overwrites UNGATED.
    #
    # 🐞 THE HAZARD THE PRUNE ADDRESSED IS STILL REAL and is closed elsewhere: the
    # seed's ``shutil.copy2`` PRESERVES the source mtime, and
    # ``start._synced_uptodate`` skips whenever ``dest.st_mtime >= src.st_mtime``, so
    # a newer seed source used to pin the SEED's bytes at a credential dest. The
    # create-time UNGATED sync makes the gate compare against the sync's OWN write —
    # pinned by ``test_start_assembly`` (the create-time delivery tests).
    collapsed = collapse(
      box=shape(
        seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/seed", ""))],
        sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/sync", ""))],
      ),
    )
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/x", "")]
    assert collapsed.synced == [CollapsedCopy("/h/sync", f"{GUEST}/x", "")]

  def test_a_seed_INSIDE_a_synced_directory_survives_too(self):
    # The containment case, kept as its own row: no rule here reads the sync arm at
    # all any more, so neither exact-dest nor containment can remove a seed.
    collapsed = collapse(
      box=shape(
        seed=[CopyRow(f"{GUEST}/x/inner", BindEntry("/h/seed", ""))],
        sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/sync", ""))],
      ),
    )
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/x/inner", "")]

  def test_a_seed_at_a_dest_NO_sync_claims_is_UNTOUCHED(self):
    # The control: a seed at an unclaimed dest was never at issue, before or after.
    collapsed = collapse(
      box=shape(
        seed=[CopyRow(f"{GUEST}/kept", BindEntry("/h/seed", ""))],
        sync=[CopyRow(f"{GUEST}/other", BindEntry("/h/sync", ""))],
      ),
    )
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/kept", "")]

  def test_a_sync_in_ANY_SCOPE_removes_NOTHING_from_the_seed_arm(self):
    # Both directions, because a re-introduced sweep in either direction has to fail
    # this. RED if the prune comes back in any form.
    outer_sync = collapse(
      system=shape(sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/sync", ""))]),
      box=shape(seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/seed", ""))]),
    )
    inner_sync = collapse(
      system=shape(seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/seed", ""))]),
      box=shape(sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/sync", ""))]),
    )
    assert outer_sync.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/x", "")]
    assert inner_sync.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/x", "")]

  def test_a_seed_outside_home_is_REFUSED_even_when_a_sync_shares_the_dest(self):
    # ⚑ The prune's one surviving ordering property, now unconditional: a sync at the
    # same dest does not quietly excuse a mis-declared seed dest.
    with pytest.raises(SettingsError, match="outside the home binding"):
      collapse(
        box=shape(
          seed=[CopyRow("/opt/thing", BindEntry("/h/seed", ""))],
          sync=[CopyRow("/opt/thing", BindEntry("/h/sync", ""))],
        ),
      )

  def test_the_BARE_door_sees_the_same_seed_arm_WITH_NO_HOME_BIND_AT_ALL(self):
    """🛑 THE ``box create`` PATH. ``collapse_seeded`` is called BARE there.

    Both doors run the same function, so a rule placed in either one alone would give
    the create path — the ONLY door that writes seeds — a different seed list from the
    launch resolve. RED if a prune is reintroduced on either side.
    """
    shapes = shape_set(
      box=shape(
        seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/seed", ""))],
        sync=[CopyRow(f"{GUEST}/x", BindEntry("/h/sync", ""))],
      ),
    )
    assert collapse_seeded(shapes) == [CollapsedCopy("/h/seed", f"{GUEST}/x", "")]


class TestTheSyncArmIsAPlainConcatenation:
  """⚖️ RULED 2026-08-12 — *"don't check for sync. Let it clobber whatever it wants."*

  ⚑ The pass USED to fold against the final bind map, to refuse a sync at a bind's
  exact point. That refusal is GONE and the parameter went with it, so this arm is
  now the seed arm's twin: a scope-ordered concatenation that arbitrates nothing.
  ⚑⚑ Half these cases are therefore NEGATIVE — they pin an ACCEPTANCE, and each one
  asserts the MOUNT as well as the copy, because dropping the bind to make room for
  the copy is the other way to break this ruling.
  """

  def test_the_sync_list_is_the_CONCATENATION_in_SCOPE_order(self):
    # ⚑⚑ THE DESTS DISAGREE WITH THE SCOPES ON PURPOSE, exactly as the seed case
    # does: the alphabetically LAST dest sits in the OUTERMOST scope, so a
    # dest-sorted implementation returns this list REVERSED and the test goes red.
    collapsed = collapse(
      box=shape(sync=[CopyRow(f"{GUEST}/a", BindEntry("/h/box", ""))]),
      workset=shape(sync=[CopyRow(f"{GUEST}/m", BindEntry("/h/ws", ""))]),
      system=shape(sync=[CopyRow(f"{GUEST}/z", BindEntry("/h/sys", ""))]),
    )
    assert collapsed.synced == [
      CollapsedCopy("/h/sys", f"{GUEST}/z", ""),
      CollapsedCopy("/h/ws", f"{GUEST}/m", ""),
      CollapsedCopy("/h/box", f"{GUEST}/a", ""),
    ]

  def test_a_sync_dest_REPEATS_INSIDE_ONE_SCOPE_TOO_and_the_arm_carries_both(self):
    # The seed arm's within-scope repeat, on the other copy arm.
    collapsed = collapse(
      box=shape(sync=[
        CopyRow(f"{GUEST}/c", BindEntry("/h/first", "")),
        CopyRow(f"{GUEST}/c", BindEntry("/h/second", "")),
      ]),
    )
    assert collapsed.synced == [
      CollapsedCopy("/h/first", f"{GUEST}/c", ""),
      CollapsedCopy("/h/second", f"{GUEST}/c", ""),
    ]

  def test_EVERY_row_of_a_repeated_arm_survives_a_bind_at_one_of_their_dests(self):
    # The sync arm has NO per-row refusal left to fire, so a repeated arm comes out
    # whole no matter what the bind map holds. Goes RED if any row is dropped.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[
        CopyRow(f"{GUEST}/w/inner", BindEntry("/h/inner", "")),
        CopyRow(f"{GUEST}/w", BindEntry("/h/at", "")),
      ]),
    )
    assert collapsed.synced == [
      CollapsedCopy("/h/inner", f"{GUEST}/w/inner", ""),
      CollapsedCopy("/h/at", f"{GUEST}/w", ""),
    ]

  def test_a_sync_dest_OUTSIDE_home_is_ACCEPTED(self):
    # ⚑⚑ THE LOAD-BEARING NEGATIVE: there is deliberately NO home-only rule for
    # ``synced``. Its dest resolves through whichever binding covers it, and home is
    # only the pid-0 foundation among them — so the seed refusal must NOT leak onto
    # this arm. Goes RED the moment ``_refuse_seed_outside_home`` is applied here.
    collapsed = collapse(
      system=shape(rw={"/opt/thing": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[CopyRow("/opt/thing/cred", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", "/opt/thing/cred", "")]

  def test_a_sync_dest_outside_home_with_NO_binding_at_all_is_still_ACCEPTED(self):
    # The rule is about home, not about coverage: the collapse refuses neither.
    collapsed = collapse(box=shape(sync=[CopyRow("/opt/loose", BindEntry("/h/loose", ""))]))
    assert collapsed.synced == [CollapsedCopy("/h/loose", "/opt/loose", "")]

  def test_a_SEED_dest_outside_home_is_STILL_refused(self):
    # The counterpart of the case above, and the reason both are here: the two arms
    # answer this question DIFFERENTLY, and a shared pass would flatten them.
    with pytest.raises(SettingsError, match=r"outside the home binding"):
      collapse(box=shape(seed=[CopyRow("/opt/thing", BindEntry("/h/thing", ""))]))

  def test_a_sync_INSIDE_a_bind_dest_is_accepted_and_carries_the_GUEST_dest(self):
    # ⚑ The normal case, and the one the ordering exists for. The row carries the
    # GUEST dest: resolving it through the bind map to ``/h/mount/cred`` is DELIVERY
    # and lands at the cutover, not here.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[CopyRow(f"{GUEST}/w/cred", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w/cred", "")]

  def test_a_sync_at_a_binds_EXACT_dest_is_ACCEPTED_and_the_BIND_SURVIVES(self):
    """⚖️ RULED 2026-08-12 — *"don't check for sync. Let it clobber whatever it wants."*

    ⚑ THE REVERSAL. Until this ruling the fold REFUSED this arrangement by name.
    It is ordinary: delivery resolves the dest through the bind that covers it, so
    an exact-dest sync writes into that bind's own host source — it clobbers
    CONTENT, and *"most of bind remains intact"*. Both halves are asserted because
    accepting the copy while dropping the mount would be the other way to get this
    wrong.
    """
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w", "")]
    assert collapsed.bindings[f"{GUEST}/w"] == CollapsedBind("/h/mount", "Z,U,rw")

  def test_a_sync_at_a_RO_binds_EXACT_dest_is_ACCEPTED_TOO(self):
    # ⚑ The ro arm folds SEPARATELY from rw (``_scope_binds`` walks both), so both
    # arms are pinned. The collapse does not rule on read-only-ness at all: DELIVERY
    # warns and skips a sync whose cover is ro (``start._synced_host_dest``), and
    # that is a different seam answering a different question.
    collapsed = collapse(
      system=shape(ro={f"{GUEST}/w": BindEntry("/h/mount", "ro")}),
      box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w", "")]
    assert collapsed.bindings[f"{GUEST}/w"] == CollapsedBind("/h/mount", "ro")

  def test_the_sync_arm_TAKES_NO_BIND_MAP_so_scope_ORDER_cannot_matter(self):
    # ⚑ THE DISCRIMINATING SHAPE, INVERTED. It used to prove the refusal read the
    # FINAL map: the bind arrives in the LAST scope, after the sync's own, so only a
    # whole-fold pass could see it. Nothing sees it now, and that is the point —
    # neither arrival order can produce a refusal or a dropped row.
    for shapes in (
      {"system": shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
       "box": shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")})},
      {"system": shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
       "box": shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))])},
    ):
      collapsed = collapse(**shapes)
      assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w", "")]
      assert collapsed.bindings[f"{GUEST}/w"].src == "/h/mount"

  def test_a_sync_at_HOME_itself_is_ACCEPTED_and_home_still_stands(self):
    # ⚑ Home is pid 0 and sits in the map like any other bind, so it inherits the
    # ruling with nothing added — and NOTHING may subsume home, so the assertion
    # that home survives is the load-bearing half.
    collapsed = collapse(box=shape(sync=[CopyRow("~", BindEntry("/h/cred", ""))]))
    assert collapsed.synced == [CollapsedCopy("/h/cred", GUEST, "")]
    assert collapsed.bindings[GUEST] == CollapsedBind(HOME.src, HOME.opts)

  def test_a_sync_at_a_MASKS_exact_point_is_accepted_and_the_mask_stands(self):
    # The one case that was ALREADY accepted, kept so the two now agree for one
    # reason rather than by coincidence. ⚑ Delivery warns and skips it (a tmpfs has
    # no host source); the collapse rules nothing.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/m": True}),
      box=shape(sync=[CopyRow(f"{GUEST}/m", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.bindings[f"{GUEST}/m"] == MASK
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/m", "")]

  def test_a_sync_whose_dest_CONTAINS_a_bind_dest_is_accepted_and_keeps_the_bind(self):
    # Containment never was the rule, and now equality is not one either — the two
    # shapes are pinned side by side so no future narrowing can reach only one.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/w/inner": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w", "")]
    assert collapsed.bindings[f"{GUEST}/w/inner"].src == "/h/mount"

  def test_a_sync_dest_is_NORMALIZED_and_a_dotted_one_survives_WHOLE(self):
    collapsed = collapse(box=shape(sync=[CopyRow("~/.aws/credentials", BindEntry("/h/c", ""))]))
    assert collapsed.synced == [
      CollapsedCopy("/h/c", f"{GUEST}/.aws/credentials", ""),
    ]

  def test_a_sync_dest_REPEATS_across_scopes_and_nothing_is_pruned(self):
    collapsed = collapse(
      system=shape(sync=[CopyRow("~/c", BindEntry("/h/sys", ""))]),
      box=shape(sync=[CopyRow("~/c", BindEntry("/h/box", ""))]),
    )
    assert [entry.src for entry in collapsed.synced] == ["/h/sys", "/h/box"]

  def test_a_syncs_opts_are_carried_VERBATIM_with_no_mode_folded_in(self):
    collapsed = collapse(box=shape(sync=[CopyRow("~/c", BindEntry("/h/c", "Z,U"))]))
    assert collapsed.synced[0].opts == "Z,U"

  def test_a_synced_entry_is_a_COPY_and_never_becomes_a_binding(self):
    # ⚑⚑ ``seeded``/``synced`` ARE COPIES AND STAY COPIES — key shape only.
    collapsed = collapse(box=shape(sync=[CopyRow(f"{GUEST}/c", BindEntry("/h/c", ""))]))
    assert list(collapsed.bindings) == [GUEST]


class TestNothingPrunesACopy:
  """The two halves do not interact. ⚑ Every case here USED to delete the copy."""

  def test_a_bind_at_a_copys_EXACT_dest_no_longer_prunes_it(self):
    collapsed = collapse(
      system=shape(seed=[CopyRow(f"{GUEST}/x", BindEntry("/h/early", ""))]),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert collapsed.seeded == [CollapsedCopy("/h/early", f"{GUEST}/x", "")]
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/mount"

  def test_a_bind_ABOVE_a_copy_no_longer_prunes_it(self):
    collapsed = collapse(
      system=shape(seed=[CopyRow(f"{GUEST}/x/deep/file", BindEntry("/h/f", ""))]),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/mount", "Z,U")}),
    )
    assert [entry.dest for entry in collapsed.seeded] == [f"{GUEST}/x/deep/file"]

  def test_a_mask_no_longer_prunes_the_copies_beneath_it(self):
    collapsed = collapse(
      system=shape(seed=[CopyRow(f"{GUEST}/x/file", BindEntry("/h/f", ""))]),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert [entry.dest for entry in collapsed.seeded] == [f"{GUEST}/x/file"]
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_a_copy_at_a_masks_EXACT_point_neither_refuses_nor_unmasks(self):
    # ⚑ Rule 5 and the S1 unmask are BOTH gone: a copy no longer meets a mask at
    # all, so the mask stays exactly where the mask rules put it and the copy is
    # carried beside it. Whether the copy is then dead is a DELIVERY question.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/planted": True}),
      box=shape(seed=[CopyRow(f"{GUEST}/planted", BindEntry("/h/seed", ""))]),
    )
    assert collapsed.bindings[f"{GUEST}/planted"] == MASK
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/planted", "")]

  def test_an_OUTER_scopes_bind_does_not_reach_a_LATER_scopes_copy(self):
    # Held from the deleted prune's own S4 test: precedence must not invert. It is
    # now true BY CONSTRUCTION rather than by a scope-local key list.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      agent=shape(seed=[CopyRow(f"{GUEST}/x/deep", BindEntry("/h/deep", ""))]),
      box=shape(rw={f"{GUEST}/other": BindEntry("/h/other", "Z,U")}),
    )
    assert [entry.dest for entry in collapsed.seeded] == [f"{GUEST}/x/deep"]


class TestASeedMustLandInsideHome:
  """⚖️ THE SEED ERROR CASE — a seed resolves into the home store or nowhere.

  ⚑ It is the SEED arm's rule alone: ``synced`` deliberately has no home-only rule
  (2026-08-10b), and the sync class below pins that difference from the other side.
  """

  def test_a_copy_OUTSIDE_home_is_refused_by_name(self):
    with pytest.raises(SettingsError, match=r"outside the home binding"):
      collapse(box=shape(seed=[CopyRow("/opt/thing", BindEntry("/h/thing", ""))]))

  def test_the_refusal_NAMES_the_source_and_the_destination(self):
    with pytest.raises(SettingsError, match=r"'/h/thing'.*'/opt/thing'"):
      collapse(box=shape(seed=[CopyRow("/opt/thing", BindEntry("/h/thing", ""))]))

  def test_a_copy_AT_home_ITSELF_is_inside_home(self):
    # The ``seeded`` layers target ``~`` exactly, so equality MUST count as inside.
    collapsed = collapse(box=shape(seed=[CopyRow("~", BindEntry("/h/layer", ""))]))
    assert collapsed.seeded == [CollapsedCopy("/h/layer", GUEST, "")]

  def test_a_SIBLING_of_home_sharing_its_prefix_is_NOT_inside_home(self):
    # ⚑ The separator guard, on the containment predicate the new refusal uses:
    # /home/agent-foo is not inside /home/agent, so this copy is refused.
    with pytest.raises(SettingsError, match=r"outside the home binding"):
      collapse(box=shape(seed=[CopyRow("/home/agent-foo/x", BindEntry("/h/dash", ""))]))

  def test_a_bind_at_the_same_OUTSIDE_dest_does_not_excuse_the_copy(self):
    # ⚑ The halves do not interact: the copy half runs FIRST and reads no binding,
    # so a mount there cannot make an out-of-home copy legal.
    with pytest.raises(SettingsError, match=r"outside the home binding"):
      collapse(
        system=shape(rw={"/opt/thing": BindEntry("/h/mount", "Z,U")}),
        box=shape(seed=[CopyRow("/opt/thing", BindEntry("/h/thing", ""))]),
      )


class TestTheModuleNeverTouchesTheFilesystem:
  """⚑ The collapse is a PURE function of the store shape — no ``is_dir``, no probe."""

  DEST = f"{GUEST}/planted"

  def collapsed_with_source(self, src) -> CollapsedStore:
    return collapse(
      system=shape(mask={self.DEST: True}),
      box=shape(seed=[CopyRow(self.DEST, BindEntry(str(src), ""))]),
    )

  def test_a_real_DIRECTORY_source_onto_a_mask_is_no_longer_refused(self, tmp_path):
    # ⚑ This EXACT config raised until 2026-08-09d, decided by a live ``is_dir()``.
    # The concrete case is ``~/.config/goose/custom_providers``.
    source = tmp_path / "adir"
    source.mkdir()
    collapsed = self.collapsed_with_source(source)
    assert collapsed.seeded == [CollapsedCopy(str(source), self.DEST, "")]

  def test_the_answer_does_NOT_depend_on_whether_the_source_exists(self, tmp_path):
    # The old probe made ONE config refuse or permit according to a fact about the
    # host at resolve time. Same shape, three different filesystems, one answer.
    directory = tmp_path / "adir"
    directory.mkdir()
    afile = tmp_path / "afile"
    afile.write_text("x")
    absent = tmp_path / "absent"
    answers = [
      self.collapsed_with_source(src).seeded[0]._replace(src="<src>")
      for src in (directory, afile, absent)
    ]
    assert answers == [CollapsedCopy("<src>", self.DEST, "")] * 3


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

  def test_a_copys_opts_are_carried_VERBATIM_with_no_mode_folded_in(self):
    # A copy is in no ro/rw ARM, so there is no mode to fold. It carries what the
    # entry stored, exactly as home does.
    collapsed = collapse(box=shape(seed=[CopyRow("~/x", BindEntry("/h/x", "Z,U"))]))
    assert collapsed.seeded[0].opts == "Z,U"


class TestABindCannotSubsumeABind:
  """⚑ Only a LATER scope can trip it — within a scope the parent-first sort forbids it."""

  def test_a_later_scope_bind_ABOVE_an_earlier_deeper_one_is_refused(self):
    # ⚑⚑ THE DISCRIMINATING SHAPE NEEDS TWO SCOPES. The mount order follows the
    # path VALUE, not the declaration order, so the inner bind could never be
    # reached: shipping it would silently drop a declaration.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(
        system=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )

  def test_the_refusal_NAMES_the_binding_it_would_have_swallowed(self):
    with pytest.raises(SettingsError, match=rf"'{GUEST}/x/y' \('/h/deep'\)"):
      collapse(
        system=shape(ro={f"{GUEST}/x/y": BindEntry("/h/deep", "ro")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )

  def test_a_bind_AT_an_occupied_dest_is_the_SAME_refusal_not_a_second_one(self):
    # ⚑ The predicate is inclusive of equality on the subsume side, which is what
    # collapsed the old ``_refuse_double_bind`` into this one rule. Same message.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(
        system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
      )

  def test_the_ro_and_rw_arms_of_ONE_scope_contend_at_one_dest(self):
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(
        box=shape(
          ro={f"{GUEST}/x": BindEntry("/h/ro", "ro")},
          rw={f"{GUEST}/x": BindEntry("/h/rw", "Z,U")},
        ),
      )

  def test_two_SPELLINGS_of_one_dest_are_one_destination(self):
    # Normalization happens at the point of use, so ``~/x`` and ``/home/agent/x``
    # meet in the map and raise the collision they actually are — rather than
    # silently overwriting each other inside a pre-pass.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(
        system=shape(rw={"~/x": BindEntry("/h/sys", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
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
    # The separator guard on the SUBSUME side: /home/agent/foobar is not inside
    # /home/agent/foo, so neither refuses the other.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/foobar": BindEntry("/h/foobar", "Z,U")}),
      box=shape(rw={f"{GUEST}/foo": BindEntry("/h/foo", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/foobar"].src == "/h/foobar"
    assert collapsed.bindings[f"{GUEST}/foo"].src == "/h/foo"

  def test_a_bind_does_not_refuse_a_MASK_beneath_it(self):
    # The bind refusal counts BINDINGS only. A mask beneath is swept, never refused.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/y": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/shallow"


class TestABindSweepsTheMasksItCovers:
  """A bind CAN subsume a mask at its point or inside it, and subsumed means REMOVED."""

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

  def test_a_bind_at_a_masks_EXACT_point_replaces_it(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x": True}),
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/box", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == CollapsedBind("/h/box", "Z,U,rw")


class TestABindCannotBeAChildOfAMask:
  """The mask's tmpfs would swallow the bind, so it is refused by name."""

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

  def test_a_bind_beside_a_mask_is_not_a_child_of_it(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/xy": True}),
      box=shape(rw={f"{GUEST}/xyz": BindEntry("/h/xyz", "Z,U")}),
    )
    assert collapsed.bindings[f"{GUEST}/xyz"].src == "/h/xyz"


class TestAMaskMayBeAChildOfABind:
  """The permissive half. It guards against the bind-under-mask rule being made symmetric."""

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


class TestAMaskSweepsTheMountsItCovers:
  """A mask CAN replace or subsume a bind at its point or inside it — and REMOVES it."""

  def test_a_mask_over_a_binding_at_its_EXACT_point_replaces_it(self):
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_a_mask_REMOVES_the_binding_nested_INSIDE_it(self):
    # ⚑⚑ THE SWEEP'S DISCRIMINATING CASE, and it needs two scopes: until 2026-08-09d
    # the mask arm was one unguarded assignment, so this bind SURVIVED beneath the
    # tmpfs and would have been emitted as a mount into a void.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert f"{GUEST}/x/y" not in collapsed.bindings
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/x"]

  def test_a_mask_does_not_remove_a_binding_BESIDE_it(self):
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/xyz": BindEntry("/h/xyz", "Z,U")}),
      box=shape(mask={f"{GUEST}/xy": True}),
    )
    assert collapsed.bindings[f"{GUEST}/xyz"].src == "/h/xyz"

  def test_a_mask_and_a_binding_at_one_dest_in_ONE_scope_is_not_an_error(self):
    # S3, RULED: within a scope the mask applies and the cure is not declaring it.
    # No diagnostic — the masks merge after the arms and simply win.
    collapsed = collapse(
      box=shape(rw={f"{GUEST}/x": BindEntry("/h/x", "Z,U")}, mask={f"{GUEST}/x": True}),
    )
    assert collapsed.bindings[f"{GUEST}/x"] == MASK


class TestAMaskMayNotSubsumeHome:
  """⚖️ RULED 2026-08-09d — "nothing may subsume home" is ABSOLUTE and covers MASKS.

  ⚑ A DELIBERATE BEHAVIOUR CHANGE, not a regression: until this ruling a mask at
  ``~`` (or at ``/``) swept the home binding away and the box launched with no home,
  because the rule fell out of the BIND refusal alone, which counts bindings only.
  His words: *"of course we should prohibit masking home directly or allowing a mask
  that would have home as a child path (ie that would shadow home)"*.
  """

  #: The one message, for "at home's point" and "over home" alike.
  SUBSUMES_HOME = r"lands at or above the home binding"

  @pytest.mark.parametrize("spelling", ["~", GUEST])
  def test_a_mask_AT_homes_dest_is_refused_in_either_spelling(self, spelling):
    # This case is what `948910c` pinned the OTHER way round; it INVERTS here.
    with pytest.raises(SettingsError, match=self.SUBSUMES_HOME):
      collapse(box=shape(mask={spelling: True}))

  def test_a_mask_at_the_ROOT_is_refused_because_home_is_a_CHILD_of_it(self):
    with pytest.raises(SettingsError, match=self.SUBSUMES_HOME):
      collapse(box=shape(mask={"/": True}))

  def test_a_mask_at_an_ANCESTOR_of_home_is_refused(self):
    with pytest.raises(SettingsError, match=self.SUBSUMES_HOME):
      collapse(box=shape(mask={"/home": True}))

  def test_the_refusal_NAMES_the_offending_dest_and_homes_own(self):
    with pytest.raises(SettingsError, match=rf"'/home'.*'{GUEST}'"):
      collapse(box=shape(mask={"/home": True}))

  def test_a_mask_INSIDE_home_is_ordinary_and_still_collapses(self):
    # ⚑ THE OVER-REACH GUARD: home is every mask's parent, so a refusal written as a
    # bare comparative rather than as CONTAINMENT would refuse every mask there is.
    collapsed = collapse(box=shape(mask={f"{GUEST}/x/y": True}))
    assert collapsed.bindings[f"{GUEST}/x/y"] == MASK
    assert collapsed.bindings[GUEST].src == HOME.src

  def test_a_SIBLING_of_home_sharing_its_prefix_is_not_over_home(self):
    # The separator guard, on the containment predicate the new refusal uses:
    # /home/agent-foo does not contain /home/agent, so it masks nothing of home's.
    collapsed = collapse(box=shape(mask={"/home/agent-foo": True}))
    assert collapsed.bindings["/home/agent-foo"] == MASK
    assert collapsed.bindings[GUEST].src == HOME.src

  def test_a_mask_over_home_is_refused_with_other_scopes_already_collapsed(self):
    # ⚑ NOT AN ORDERING PIN — a swept-then-refused arrival cannot be read back out
    # of a pure function that raises, so "refuse before sweep" is structural only.
    # What this does pin: the refusal does not depend on home being ALONE in the map.
    with pytest.raises(SettingsError, match=self.SUBSUMES_HOME):
      collapse(
        system=shape(rw={f"{GUEST}/x": BindEntry("/h/sys", "Z,U")}),
        agent=shape(mask={f"{GUEST}/x/secret": True}),
        box=shape(mask={"/": True}),
      )


class TestAMaskCannotTakeOrEnterAnotherMask:
  """"A void within a void" — the direction of prohibition is the INVERSE of a bind's."""

  def test_a_mask_at_an_earlier_masks_EXACT_point_is_refused(self):
    # ⚑ TWO SCOPES ARE REQUIRED: one scope's mask arm is dest-keyed, so the same
    # point twice inside one scope is unconstructible and pins nothing.
    with pytest.raises(SettingsError, match=r"lands on the mask"):
      collapse(
        system=shape(mask={f"{GUEST}/x": True}),
        box=shape(mask={"~/x": True}),
      )

  def test_a_mask_INSIDE_an_earlier_mask_is_refused(self):
    with pytest.raises(SettingsError, match=r"a void within a void"):
      collapse(
        system=shape(mask={f"{GUEST}/x": True}),
        box=shape(mask={f"{GUEST}/x/y": True}),
      )

  def test_the_refusal_NAMES_the_mask_it_landed_on(self):
    with pytest.raises(SettingsError, match=rf"'{GUEST}/x'"):
      collapse(
        system=shape(mask={f"{GUEST}/x": True}),
        box=shape(mask={f"{GUEST}/x/deep/y": True}),
      )

  def test_a_mask_may_SUBSUME_an_earlier_child_mask_and_it_is_REMOVED(self):
    # ⚑⚑ THE INVERSION, and the case a mirror-of-the-bind-rule would get backwards:
    # a mask that is a PARENT of an existing one is FINE, and the child goes.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/y": True}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/x"]
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_a_SIBLING_mask_sharing_a_prefix_is_neither_refused_nor_swept(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/foobar": True}),
      box=shape(mask={f"{GUEST}/foo": True}),
    )
    assert collapsed.bindings[f"{GUEST}/foobar"] == MASK
    assert collapsed.bindings[f"{GUEST}/foo"] == MASK

  def test_a_bind_between_two_masks_does_not_make_them_legal(self):
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/x/y": True}),
      agent=shape(rw={f"{GUEST}/elsewhere": BindEntry("/h/e", "Z,U")}),
      box=shape(mask={f"{GUEST}/x": True}),
    )
    assert collapsed.bindings[f"{GUEST}/elsewhere"].src == "/h/e"
    assert f"{GUEST}/x/y" not in collapsed.bindings


class TestTheIntraScopeSorts:
  """Ruling 1's mechanism, and its INVERSE for masks: no answer may turn on dict order."""

  def test_a_parent_bind_declared_AFTER_its_child_in_one_scope_still_lands_first(self):
    # ⚑ Without the sort this raises: the child is already collapsed when the
    # parent arrives, and the rule cannot tell an ordering artefact from a genuine
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

  def test_the_bind_sort_spans_BOTH_arms_of_the_scope(self):
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

  def test_three_generations_of_bind_in_one_scope_collapse_parent_first(self):
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

  def test_the_arm_order_survives_at_EQUAL_length(self):
    # The sort is STABLE, so his ro-before-rw walk is preserved: the ro entry is
    # the OCCUPANT the collision names.
    with pytest.raises(SettingsError, match=r"'/h/ro'"):
      collapse(
        box=shape(
          ro={f"{GUEST}/x": BindEntry("/h/ro", "ro")},
          rw={f"{GUEST}/x": BindEntry("/h/rw", "Z,U")},
        ),
      )

  def test_the_bind_sort_does_not_reach_ACROSS_scopes(self):
    # Scope order is precedence and containment order is intra-scope only. A
    # shallower bind in a LATER scope is exactly the conflict the rule catches.
    with pytest.raises(SettingsError, match=COLLIDES):
      collapse(
        agent=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
        box=shape(rw={f"{GUEST}/x": BindEntry("/h/shallow", "Z,U")}),
      )

  @pytest.mark.parametrize("declared", [
    ((f"{GUEST}/x", f"{GUEST}/x/y")),
    ((f"{GUEST}/x/y", f"{GUEST}/x")),
  ])
  def test_nested_masks_in_ONE_scope_collapse_CHILD_first_either_way(self, declared):
    # ⚑⚑ THE INVERSE SORT, and it is load-bearing: the mask rule refuses a PARENT
    # and permits a CHILD, so masks must arrive child-first or one declaration
    # order raises and the other does not. Ruling 1's argument, direction flipped —
    # within a scope there is no precedence to express, so the sort is the answer.
    collapsed = collapse(box=shape(mask=dict.fromkeys(declared, True)))
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/x"]

  def test_three_generations_of_mask_in_one_scope_leave_only_the_outermost(self):
    collapsed = collapse(box=shape(mask={
      f"{GUEST}/a/b": True, f"{GUEST}/a/b/c": True, f"{GUEST}/a": True,
    }))
    assert list(collapsed.bindings) == [GUEST, f"{GUEST}/a"]


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

  def test_a_case_variant_mask_does_not_sweep_a_binding(self):
    # Case-folding the containment compare would silently delete this binding.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/x/y": BindEntry("/h/deep", "Z,U")}),
      box=shape(mask={"/Home/agent/x": True}),
    )
    assert collapsed.bindings[f"{GUEST}/x/y"].src == "/h/deep"


class TestPurity:
  """It merges the INFORMATION. It performs no action and mutates no input."""

  def test_the_input_shapes_are_untouched(self):
    given = shape_set(
      system=shape(rw={"~/x": BindEntry("/h/sys", "Z,U")}, mask={"~/m": True}),
      box=shape(seed=[CopyRow("~/s", BindEntry("/h/s", ""))]),
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

  def test_a_real_floor_collapses_to_folded_binds_and_a_copy_list(self):
    collapsed = self.collapsed({
      "system.bindings.ro": {"~/ro": ("/h/ro",)},
      "box.caches": {"~/cache": ("/h/cache",)},
      "box.seeded": {"~/seed": ("/h/seed",)},
    })
    assert collapsed.bindings[f"{GUEST}/ro"] == CollapsedBind("/h/ro", "ro")
    assert collapsed.bindings[f"{GUEST}/cache"] == CollapsedBind("/h/cache", "Z,U,rw")
    assert collapsed.bindings[GUEST].src == HOME.src
    assert collapsed.seeded == [CollapsedCopy("/h/seed", f"{GUEST}/seed", "")]

  def test_a_real_synced_declaration_reaches_the_sync_list(self):
    # ⚑⚑ THE MEASURED GAP, on the REAL chain: `box.synced{~/sy}` gave 1 copy on the
    # live delivery route and 0 through the collapse, because the `sync` arm was
    # produced and never read. Nothing shipped declares `synced`, which is why the
    # drop never showed. RED against the pre-2026-08-10b collapse.
    collapsed = self.collapsed({"box.synced": {"~/sy": ("/h/sy",)}})
    assert collapsed.synced == [CollapsedCopy("/h/sy", f"{GUEST}/sy", "")]
    assert collapsed.seeded == []

  def test_a_real_mask_over_a_real_bind_from_an_outer_scope(self):
    collapsed = self.collapsed({
      "system.bindings.rw": {"~/x": ("/h/sys",)},
      "box.masks": ["~/x"],
    })
    assert collapsed.bindings[f"{GUEST}/x"] == MASK

  def test_a_real_seeded_layer_UNDER_a_real_bind_is_carried_not_pruned(self):
    # The agent tier is DISCRIMINATED on the way in and folds under its BARE scope
    # token, which is what makes it collapse third-from-outermost.
    collapsed = self.collapsed({
      "agent.claude.common": {"~/x": ("/h/agent",)},
      "box.seeded": {"~/x/file": ("/h/file",)},
    })
    assert collapsed.bindings[f"{GUEST}/x"].src == "/h/agent"
    assert collapsed.seeded == [CollapsedCopy("/h/file", f"{GUEST}/x/file", "")]


class TestDeclarationProvenance:
  """``CollapsedStore.declared_by`` — WHICH DECLARATION put the mount at each dest.

  ⚑ THE SHAPES MAY NOT CARRY IT. ``meta.assembly.bindings`` is spec'd
  ``dict[guest_dest -> (host_src, opts)]`` and every bind-shaped entry as a 1-or-2
  element tuple (keyspec ``:434``/``:603-605``), so the key travels BESIDE the fold,
  off the ENTRY LIST — the seam ``collapse_env`` already uses. Everything below is
  therefore driven through the LIVE route (floor → snapshot → entries → producer →
  collapse), because a hand-built shape has no entry list to pair against and would
  pass whatever the fold did.

  🛑 THE DISCRIMINATING CASE IS ``test_the_key_is_the_mask_that_SURVIVED…``: a
  dest-keyed lookup over the entry list — the obvious cheap answer — names the WRONG
  scope there, and nothing else in this class would catch it.
  """

  def ctx(self) -> ResolveCtx:
    return ResolveCtx(
      agent_name="claude", workset_name="myws", host_home="/home/u",
      xdg={"XDG_DATA_HOME": "/data"}, config={},
    )

  def entries(self, floor: dict):
    ctx = self.ctx()
    snap = build_launch_snapshot(
      agent_name="claude", ctx=ctx,
      system_path=None, agent_path=None, workset_path=None, box_path=None,
      default_categories=floor,
    )
    return snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)

  def collapsed(self, floor: dict) -> CollapsedStore:
    entries = self.entries(floor)
    return collapse_store_shapes(build_store_shape_set(entries), HOME, entries)

  def test_omitting_the_entry_list_folds_identically_and_files_nothing(self):
    """The launch's own call site passes no entries, and must be unaffected."""
    floor = {
      "system.bindings.ro": {"~/ro": ("/h/ro",)},
      "box.masks": ["~/m"],
      "box.seeded": {"~/seed": ("/h/seed",)},
    }
    entries = self.entries(floor)
    shapes = build_store_shape_set(entries)
    bare = collapse_store_shapes(shapes, HOME)
    with_keys = collapse_store_shapes(shapes, HOME, entries)
    assert bare.declared_by == {}
    assert bare.bindings == with_keys.bindings
    assert bare.seeded == with_keys.seeded
    assert bare.synced == with_keys.synced
    assert with_keys.declared_by != {}

  def test_a_bind_and_a_mask_are_each_named_by_their_own_key(self):
    collapsed = self.collapsed({
      "system.bindings.ro": {"~/ro": ("/h/ro",)},
      "box.masks": ["~/m"],
    })
    assert collapsed.declared_by[f"{GUEST}/ro"] == f"system.bindings.ro.{GUEST}/ro"
    assert collapsed.declared_by[f"{GUEST}/m"] == "box.masks.~/m"

  def test_home_is_named_by_no_key_because_no_key_names_it(self):
    """Home is pid 0, built by the SEAM off the RO derived key — it is in no arm."""
    assert GUEST not in self.collapsed({"box.masks": ["~/m"]}).declared_by

  def test_a_subsumed_binds_key_LEAVES_WITH_IT(self):
    """The sweep takes the declaration too: a key left behind names a mount the box lacks."""
    collapsed = self.collapsed({
      "system.bindings.rw": {"~/x": ("/h/sys",)},
      "box.masks": ["~/x"],
    })
    assert collapsed.bindings[f"{GUEST}/x"] == MASK
    assert collapsed.declared_by[f"{GUEST}/x"] == "box.masks.~/x"

  def test_the_key_is_the_mask_that_SURVIVED_not_the_one_that_was_swept(self):
    """🛑 THE ORACLE. Two scopes mask one dest; only the LATER one is the occupant.

    A bind may take a mask's own point (the sweep removes the mask), and a later
    scope's mask may then retake that point — so the finished map holds ONE mask at a
    dest that TWO scopes' keys name. Reading provenance off the entry list by
    destination answers ``system.masks.~/x`` here, which is a key whose mask the box
    does not have. Only recording at the FOLD gets it right.
    """
    collapsed = self.collapsed({
      "system.masks": ["~/x"],
      "workset.bindings.rw": {"~/x": ("/h/ws",)},
      "box.masks": ["~/x"],
    })
    assert collapsed.bindings[f"{GUEST}/x"] == MASK
    assert collapsed.declared_by[f"{GUEST}/x"] == "box.masks.~/x"

  def test_a_copy_sharing_a_binds_destination_does_not_claim_it(self):
    """``synced`` at a bind's EXACT dest is ordinary (spec §2a) — and it is not a mount."""
    collapsed = self.collapsed({
      "box.bindings.rw": {"~/x": ("/h/x",)},
      "box.synced": {"~/x": ("/h/x",)},
    })
    assert collapsed.declared_by[f"{GUEST}/x"] == f"box.bindings.rw.{GUEST}/x"

  def test_an_abstraction_folded_into_the_rw_arm_is_named_by_ITS_key(self):
    """``caches``/``common`` fold into ``rw``; the key filed must be the one written."""
    collapsed = self.collapsed({"box.caches": {"~/cache": ("/h/cache",)}})
    assert collapsed.declared_by[f"{GUEST}/cache"] == f"box.caches.{GUEST}/cache"


class TestTheRefusalsNameBothParticipants:
  """All FOUR mount refusals name a declaration KEY, not only a source and a dest.

  keyspec ``:153-165``: *"the error MUST name the extending declaration, the occupant,
  and the dest. The refusal is symmetric; the diagnosis is not."*  Each message's own
  remedy tells the reader to null a KEY, so one that named no key asked for an edit
  and did not say where to make it.

  ⚑ ALL FOUR OR NONE — a mixed set is worse than none, because a reader who gets a key
  from one refusal reads its absence in the next as "there is no key".

  ⚑⚑ AND THE CLAUSES VANISH CLEANLY. Every case below is asserted BOTH ways off one
  floor: with the entry list (a launch, and ``workset share list --effective``) and
  without it (any caller that only wants the assembly leaves), where the text is
  exactly what it always was. That is what lets ``TestABindCannotBeAChildOfAMask``
  above go on matching the bare sentence.
  """

  def ctx(self) -> ResolveCtx:
    return ResolveCtx(
      agent_name="claude", workset_name="myws", host_home="/home/u",
      xdg={"XDG_DATA_HOME": "/data"}, config={},
    )

  def refusal(self, floor: dict, *, with_keys: bool) -> str:
    """The refusal *floor* provokes, folded WITH or WITHOUT the entry list."""
    ctx = self.ctx()
    snap = build_launch_snapshot(
      agent_name="claude", ctx=ctx,
      system_path=None, agent_path=None, workset_path=None, box_path=None,
      default_categories=floor,
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    with pytest.raises(SettingsError) as excinfo:
      collapse_store_shapes(
        build_store_shape_set(entries), HOME, entries if with_keys else None,
      )
    return str(excinfo.value)

  def both(self, floor: dict) -> tuple[str, str]:
    return self.refusal(floor, with_keys=True), self.refusal(floor, with_keys=False)

  def test_bind_over_bind_names_the_ARRIVING_and_the_SUBSUMED_key(self):
    keyed, bare = self.both({
      "system.bindings.rw": {"~/x": ("/h/sys",)},
      "box.bindings.rw": {"~/x": ("/h/box",)},
    })
    assert f"the binding declared by 'box.bindings.rw.{GUEST}/x' of '/h/box'" in keyed
    assert f"'/h/sys' declared by 'system.bindings.rw.{GUEST}/x'" in keyed
    # The remedy the keys exist to make actionable is untouched.
    assert "Set the unwanted key to null" in keyed
    assert "declared by" not in bare
    assert "the binding of '/h/box'" in bare

  def test_bind_under_mask_names_the_BINDING_and_the_MASK_key(self):
    keyed, bare = self.both({
      "system.masks": ["~/x"],
      "box.bindings.rw": {"~/x/inner": ("/h/inner",)},
    })
    assert f"the binding declared by 'box.bindings.rw.{GUEST}/x/inner'" in keyed
    assert "sits inside the mask declared by 'system.masks.~/x'" in keyed
    assert "declared by" not in bare
    assert "sits inside the mask at" in bare

  def test_mask_on_mask_names_BOTH_masks_which_have_no_source_at_all(self):
    """🛑 The refusal that needed keys most: neither participant has a host source."""
    keyed, bare = self.both({
      "system.masks": ["~/x"],
      "box.masks": ["~/x/inner"],
    })
    assert "the mask declared by 'box.masks.~/x/inner'" in keyed
    assert f"collapsed at '{GUEST}/x' declared by 'system.masks.~/x'" in keyed
    assert "declared by" not in bare
    assert f"the mask at '{GUEST}/x/inner' lands on" in bare

  def test_mask_over_home_names_the_mask_and_SAYS_home_has_no_key(self):
    """One participant genuinely has none — home is pid 0, in no scope's arm."""
    keyed, bare = self.both({"box.masks": ["~"]})
    assert "the mask declared by 'box.masks.~' at" in keyed
    assert "no settings key declares it, so there is nothing to suppress" in keyed
    assert "declared by" not in bare
    # ⚑ The home clause is NOT provenance and is printed either way: it explains the
    # absence of a second key rather than supplying one.
    assert "no settings key declares it, so there is nothing to suppress" in bare

  def test_the_agent_tiers_key_is_the_DISCRIMINATED_spelling(self):
    """🛑 THE ORACLE AGAINST REBUILDING A KEY from the fold's own scope token.

    The fold walks ``SCOPE_CONTAINMENT``, whose agent token is the bare ``agent``.
    A message that composed ``<scope>.<category>.<dest>`` would print
    ``agent.masks.~/x`` — a key that is in nobody's settings file. The key is READ
    off ``CategoryEntry.key_segments``, which carries the node.
    """
    keyed, _bare = self.both({
      "agent.claude.masks": ["~/x"],
      "box.bindings.rw": {"~/x/inner": ("/h/inner",)},
    })
    assert "the mask declared by 'agent.claude.masks.~/x'" in keyed
    assert "agent.masks." not in keyed


class TestTheResultPhrasesTakeTheProvenance:
  """``derivation_result`` names the declaration that TOOK a destination, when known.

  ⚑ THE MOUNT LOSSES BOTH TAKE IT — masked and superseded alike. Keying one and not
  the other was defensible only while the refusals named no keys either; once they do,
  a display that keyed every outcome except the superseding binding is the odd one out.

  ⚑ Driven through ``pair_declarations`` rather than a hand-built ``Derivation``: the
  outcome is decided there, and a hand-built row could assert a phrase for a state the
  pairing never produces.
  """

  MASK_KEY = "workset.masks./opt/m"
  BIND_KEY = "box.bindings.rw./opt/b"

  def rows(self, declarations, bindings, copies=()):
    return pair_declarations(declarations, bindings, copies)

  def test_a_masked_declaration_names_the_mask_that_covers_it(self):
    (row,) = self.rows(
      [Declaration("workset.bindings.ro./opt/m/in", "/opt/m/in", "/h/s", "MOUNT")],
      {"/opt/m": MASK},
    )
    assert row.outcome == DERIVED_MASKED
    keyed = derivation_result(row, {"/opt/m": self.MASK_KEY})
    assert f"the mask declared by '{self.MASK_KEY}' at /opt/m covers" in keyed
    assert "the mask at /opt/m covers" in derivation_result(row)

  def test_a_superseded_declaration_names_the_binding_that_occupies_it(self):
    (row,) = self.rows(
      [Declaration("workset.bindings.ro./opt/b", "/opt/b", "/h/mine", "MOUNT")],
      {"/opt/b": CollapsedBind("/h/theirs", "Z,U,rw")},
    )
    assert row.outcome == DERIVED_SUPERSEDED
    keyed = derivation_result(row, {"/opt/b": self.BIND_KEY})
    assert f"the binding declared by '{self.BIND_KEY}' of /h/theirs at /opt/b" in keyed
    assert "the binding of /h/theirs at /opt/b occupies" in derivation_result(row)

  def test_a_superseded_COPY_takes_no_key_because_no_MOUNT_took_it(self):
    """⚑ The one deliberate exception: the taker is another COPY row, not a mount.

    ``declared_by`` records MOUNTS. Naming the mount at this destination would name a
    delivery that did not take it — so this branch stays silent rather than confident.
    """
    (row,) = self.rows(
      [Declaration("box.seeded./opt/c", "/opt/c", "/h/c", "COPY")], {}, [],
    )
    assert row.outcome == DERIVED_SUPERSEDED
    assert row.bind is None
    assert derivation_result(row, {"/opt/c": "box.seeded./opt/c"}) == (
      derivation_result(row)
    )

  def test_a_LIVE_mount_gains_no_clause_at_all(self):
    """The guard against a fix that annotates the rows that are working fine."""
    (row,) = self.rows(
      [Declaration("box.bindings.rw./opt/b", "/opt/b", "/h/b", "MOUNT")],
      {"/opt/b": CollapsedBind("/h/b", "Z,U,rw")},
    )
    assert row.outcome == DERIVED_MOUNT
    assert derivation_result(row, {"/opt/b": self.BIND_KEY}) == derivation_result(row)
    assert "declared by" not in derivation_result(row, {"/opt/b": self.BIND_KEY})


class TestThePerRunOverride:
  """``-e VAR=VALUE`` is the CASCADE'S CLI LEVEL over the env slots (MBR-1 P4c-1).

  Jei, 2026-08-14: *"-e should override the key values, not the environment variables
  themselves"* — so it is applied INSIDE the collapse, after the containment walk, and
  it does exactly two things: it replaces the VALUE of the key that owns a variable, or
  it fills a variable no key owns.

  ⚑⚑ WHY THAT ORDER IS THE DESIGN AND NOT A DETAIL: ``-e`` can never CONTEST a slot, so
  no refusal can name it and none should be taught to. Ride it as a settings level
  instead and it must spell a concrete scope, at which point it becomes a SECOND scope's
  key naming the user's own variable and the twin refusal fires on the very
  configurations the flag exists to serve. Both halves are pinned below.

  🛑 THE CASES ARE DRIVEN THROUGH THE LIVE ROUTE (floor → snapshot → entries →
  ``collapse_env``), the chain ``_install_assembly_collapse`` runs. Hand-built entries
  would let a fold that never reads a real ``env`` key pass.
  """

  def ctx(self) -> ResolveCtx:
    return ResolveCtx(
      agent_name="claude", workset_name="myws", host_home="/home/u",
      xdg={"XDG_DATA_HOME": "/data"}, config={},
    )

  def slots(self, floor: dict, cli_env=None):
    ctx = self.ctx()
    snap = build_launch_snapshot(
      agent_name="claude", ctx=ctx,
      system_path=None, agent_path=None, workset_path=None, box_path=None,
      default_categories=floor,
    )
    entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
    return collapse_env(entries, cli_env)

  def test_no_cli_map_leaves_the_slots_exactly_as_the_scopes_settled_them(self):
    """The default is the whole compatibility story: no ``-e``, no change."""
    plain = self.slots({"box.env.EDITOR": "vim"})
    assert plain == self.slots({"box.env.EDITOR": "vim"}, None)
    assert plain["EDITOR"] == CollapsedEnv("vim", "box", "box.env.EDITOR")

  def test_it_replaces_the_value_of_the_key_that_owns_the_variable(self):
    slots = self.slots({"box.env.EDITOR": "vim"}, {"EDITOR": "ed"})
    assert slots["EDITOR"].value == "ed"

  def test_the_overridden_slot_KEEPS_the_owning_scope_and_key(self):
    """The key still OWNS the variable; ``-e`` supplied a value for one launch.

    ⚑ The mutation this kills: re-stamping the slot with CLI provenance. The value
    would be identical and every arrival assertion would stay green, while the leaf
    stopped saying which key a user has to edit to make the change stick.
    """
    slots = self.slots({"system.env.EDITOR": "vim"}, {"EDITOR": "ed"})
    assert slots["EDITOR"] == CollapsedEnv("ed", "system", "system.env.EDITOR")

  def test_a_variable_no_key_owns_gets_a_slot_of_its_own(self):
    slots = self.slots({}, {"SCRATCH": "1"})
    assert slots["SCRATCH"] == CollapsedEnv("1", "cli", "-e SCRATCH")

  def test_the_vacancy_provenance_is_not_a_scope_and_is_not_a_key(self):
    """It is a LABEL. Nothing resolves against ``cli`` and no such key can be written.

    Pinned because the two fields are honest STRINGS in a tuple every consumer reads
    positionally — the moment one is treated as a scope token or a settable key,
    ``-e`` acquires a cascade rank it must not have.
    """
    assert CLI_PROVENANCE_SCOPE not in SCOPE_CONTAINMENT
    slots = self.slots({}, {"SCRATCH": "1"})
    assert slots["SCRATCH"].key.startswith("-e ")

  def test_it_never_contests_a_slot_so_no_twin_refusal_can_name_it(self):
    """Two SCOPES naming one variable refuse; a ``-e`` naming that variable does not.

    🛑 THE DISCRIMINATING PAIR, and it is why the ``-e`` overlay runs AFTER the walk.
    The same variable is used twice: declared at two scopes it refuses, and named by
    ``-e`` over ONE scope's declaration it collapses cleanly. A ``-e`` folded into the
    walk as a scope would make the second case refuse too.
    """
    with pytest.raises(SettingsError, match="claimed by two keys"):
      self.slots({"system.env.EDITOR": "vi", "box.env.EDITOR": "vim"})
    assert self.slots({"box.env.EDITOR": "vim"}, {"EDITOR": "ed"})["EDITOR"].value == "ed"

  def test_it_reaches_a_variable_at_any_scope_including_the_outermost(self):
    """One flag, one behaviour, whichever scope happens to own the variable.

    The core ``KANIBAKO_*`` stamps are ``system.env.*`` keys (MBR-1 P4b), the
    outermost scope there is — so this is the case that makes MIGRATION §2.36's
    *"-e reaches them"* true.
    """
    for scope in ("system", "workset", "box"):
      slots = self.slots({f"{scope}.env.KANIBAKO_NAME": "real"}, {"KANIBAKO_NAME": "x"})
      assert slots["KANIBAKO_NAME"] == CollapsedEnv("x", scope, f"{scope}.env.KANIBAKO_NAME")

  def test_it_touches_only_the_variables_it_names(self):
    slots = self.slots(
      {"box.env.EDITOR": "vim", "box.env.PAGER": "less"}, {"EDITOR": "ed"},
    )
    assert slots["PAGER"] == CollapsedEnv("less", "box", "box.env.PAGER")


class TestCoveringBind:
  """``covering_bind`` — the INNERMOST collapsed dest covering a path.

  ⚑ It moved here from ``commands.start._synced_cover`` when a THIRD asker
  appeared (``pair_declarations``); that name is now a one-line delegation. The
  cases below pin the rule where it is DEFINED, so a caller cannot grow a second
  idea of which mount owns a path.
  """

  def test_the_INNERMOST_cover_wins_not_the_first_or_the_outermost(self):
    from kanibako.settings.store_collapse import covering_bind

    given = {"/h": CollapsedBind("/o", "Z,U"), "/h/a": CollapsedBind("/i", "Z,U")}
    assert covering_bind(given, "/h/a/deep") == "/h/a"

  def test_a_dest_at_a_binds_own_point_covers_itself(self):
    from kanibako.settings.store_collapse import covering_bind

    assert covering_bind({"/h/a": CollapsedBind("/i", None)}, "/h/a") == "/h/a"

  def test_a_MASK_covers_exactly_as_a_bind_does(self):
    # The map holds both kinds and the lookup does not discriminate — deciding
    # what a mask MEANS is the caller's, and both callers do it with ``is_mask``.
    from kanibako.settings.store_collapse import covering_bind

    assert covering_bind({"/h/a": MASK}, "/h/a/inside") == "/h/a"

  def test_a_SIBLING_prefix_is_not_a_cover(self):
    from kanibako.settings.store_collapse import covering_bind

    assert covering_bind({"/h/foo": CollapsedBind("/i", None)}, "/h/foobar") is None

  def test_nothing_covering_is_None_never_an_empty_string(self):
    from kanibako.settings.store_collapse import covering_bind

    assert covering_bind({}, "/h/a") is None
