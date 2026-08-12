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
  HOME_DEST,
  MASK,
  CollapsedBind,
  CollapsedCopy,
  CollapsedStore,
  collapse_seeded,
  collapse_store_shapes,
  fold_opt,
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


class TestTheSyncPassRunsLastAgainstTheFinalBindMap:
  """⚖️ RULED 2026-08-10b — *"could we simply copy it last instead? After binds are done?"*"""

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

  def test_a_per_row_sync_refusal_fires_from_INSIDE_a_repeated_arm(self):
    with pytest.raises(SettingsError, match="EXACTLY the"):
      collapse(
        system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
        box=shape(sync=[
          CopyRow(f"{GUEST}/w/inner", BindEntry("/h/ok", "")),
          CopyRow(f"{GUEST}/w", BindEntry("/h/nope", "")),
        ]),
      )

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

  def test_a_sync_at_a_binds_EXACT_dest_is_REFUSED(self):
    with pytest.raises(SettingsError, match=r"EXACTLY the destination"):
      collapse(
        system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
        box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
      )

  def test_a_sync_at_a_RO_binds_EXACT_dest_is_REFUSED_TOO(self):
    # ⚑ The ro arm folds SEPARATELY from rw (``_scope_binds`` walks both), and the
    # refusal reads the COLLAPSED map, so both arms must reach it. Inherited from
    # the retired ``synced_vs_binding`` pair, which covered ro explicitly (5-1b).
    with pytest.raises(SettingsError, match=r"EXACTLY the destination"):
      collapse(
        system=shape(ro={f"{GUEST}/w": BindEntry("/h/mount", "ro")}),
        box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
      )

  def test_the_exact_dest_refusal_carries_NO_rule_changed_paragraph(self):
    """T13, moved with the rule at 5-1b — copy-vs-mount did NOT change.

    ``settings_categories._rule_changed`` marks only the §0 table rows whose
    OUTCOME changed. Putting that paragraph on a rule that did not change trains
    a reader to skip it, so its absence here is a requirement, not an oversight.
    """
    with pytest.raises(SettingsError) as exc:
      collapse(
        system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
        box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
      )
    assert "THIS RULE CHANGED" not in str(exc.value)

  def test_the_exact_dest_refusal_NAMES_the_sync_the_dest_and_the_bound_source(self):
    with pytest.raises(
      SettingsError, match=rf"'/h/cred'.*'{GUEST}/w'.*'/h/mount'",
    ):
      collapse(
        system=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
        box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
      )

  def test_the_refusal_reads_the_FINAL_map_not_the_scope_the_sync_was_declared_in(self):
    # ⚑ THE DISCRIMINATING SHAPE. The binding arrives in the LAST scope, AFTER the
    # sync's own; only a pass that runs after the WHOLE mount fold can see it. A
    # per-scope or copies-first implementation accepts this configuration.
    with pytest.raises(SettingsError, match=r"EXACTLY the destination"):
      collapse(
        system=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
        box=shape(rw={f"{GUEST}/w": BindEntry("/h/mount", "Z,U")}),
      )

  def test_a_sync_at_HOME_itself_is_refused_because_home_is_a_binding(self):
    # Home is pid 0 and sits in the map like any other bind, so the ONE rule covers
    # it with nothing added.
    with pytest.raises(SettingsError, match=rf"'{HOME.src}'"):
      collapse(box=shape(sync=[CopyRow("~", BindEntry("/h/cred", ""))]))

  def test_a_sync_at_a_MASKS_exact_point_is_NOT_refused(self):
    # ⚑ THE BOUNDARY, pinned so that moving it is a DECISION. ``src = None`` marks a
    # MASK, and the rule — with its bound-inode rationale — is about BINDINGS. A
    # sync landing on a tmpfs is a DELIVERY question, as the seed beside it is.
    collapsed = collapse(
      system=shape(mask={f"{GUEST}/m": True}),
      box=shape(sync=[CopyRow(f"{GUEST}/m", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.bindings[f"{GUEST}/m"] == MASK
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/m", "")]

  def test_a_sync_whose_dest_CONTAINS_a_bind_dest_is_not_refused(self):
    # Only EXACT equality is the error. A sync above a binding is not the file-bind
    # overlap the rule is aimed at, and the refusal must not widen into containment.
    collapsed = collapse(
      system=shape(rw={f"{GUEST}/w/inner": BindEntry("/h/mount", "Z,U")}),
      box=shape(sync=[CopyRow(f"{GUEST}/w", BindEntry("/h/cred", ""))]),
    )
    assert collapsed.synced == [CollapsedCopy("/h/cred", f"{GUEST}/w", "")]

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
