"""Unit tests for block 2b — the cascade MERGE (settings_merge).

Covers the brief §4 checklist for the pure ``merge(levels) -> snapshot``:

* per-NAME union — a box ``bindings.rw.home`` overrides ONLY that entry while
  workset / system entries SURVIVE by name (deep recursion, not whole-node
  replace);
* most-specific-first LEAF win;
* ``__MISSING__`` vs truthiness — a leaf set to ``0`` / ``""`` / ``False`` SETS and
  wins (NOT skipped);
* present-None TYPE-SPLIT (§3) — scalar → None KEPT; bind → OMITTED; category →
  OMITTED; masks → UNMASKED (omitted);
* masks 3-state via the GENERIC merge;
* agent keys keep their §2d DISCRIMINATED form (``agent.default.*`` /
  ``agent.<name>.*``) through the merge — two agents coexist by name, and a
  higher scope overrides ONE agent by name (§0 per-agent independence; NO bare
  ``agent.*``);
* purity — same input twice → equal output, inputs unmutated; refs stay RAW.

The merge keys by NAME only (NO ``box_dest`` reconcile, §6g) and never expands a
ref (block 3). Authority: design §6e / §3 / §6f / §4; spec §2 / §2a / §2c.
"""

from __future__ import annotations

import copy

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.kb_store import __MISSING__
from kanibako.settings.keystore import KeyStore
from kanibako.settings.settings_merge import merge


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _probe(store: KeyStore, *segments: str) -> object:
    """Walk *segments* into *store* with the unbound-``dict.get`` probe (S3),
    returning ``__MISSING__`` if any segment is absent (so a present-None leaf is
    distinguishable from an absent one)."""
    node: object = store
    for seg in segments:
        if not isinstance(node, KeyStore):
            return __MISSING__
        node = dict.get(node, seg, __MISSING__)
    return node


# --------------------------------------------------------------------------- #
# Empty / trivial                                                             #
# --------------------------------------------------------------------------- #


def test_empty_levels_empty_snapshot() -> None:
    snap = merge([])
    assert snap == KeyStore()


def test_all_empty_partials_empty_snapshot() -> None:
    snap = merge([KeyStore(), KeyStore()])
    assert snap == KeyStore()


def test_absent_everywhere_not_in_snapshot() -> None:
    # A name set nowhere never appears; only the one set name does.
    levels = [KeyStore({"box": {"image": "img"}})]
    snap = merge(levels)
    assert _probe(snap, "box", "image") == "img"
    assert _probe(snap, "box", "missing") is __MISSING__


# --------------------------------------------------------------------------- #
# Most-specific-first leaf win                                                 #
# --------------------------------------------------------------------------- #


def test_most_specific_scalar_wins() -> None:
    # box, workset, ... — index 0 is most specific (box is the cascade top).
    levels = [
        KeyStore({"box": {"image": "box-img"}}),
        KeyStore({"box": {"image": "ws-img"}}),
    ]
    snap = merge(levels)
    assert _probe(snap, "box", "image") == "box-img"


def test_lower_scope_survives_when_higher_absent() -> None:
    levels = [
        KeyStore({"box": {"image": "box"}}),
        KeyStore({"box": {"shell": "ws"}}),
    ]
    snap = merge(levels)
    # Per-name union within the box subtree: both image (box) and shell (workset)
    # survive.
    assert _probe(snap, "box", "image") == "box"
    assert _probe(snap, "box", "shell") == "ws"


# --------------------------------------------------------------------------- #
# __MISSING__ vs truthiness — falsy values SET and win (S3)                       #
# --------------------------------------------------------------------------- #


def test_falsy_zero_sets_and_wins() -> None:
    levels = [
        # box sets helper_num 0 — falsy but SET
        KeyStore({"meta": {"box": {"helper_num": 0}}}),
        # workset 99 — must be shadowed
        KeyStore({"meta": {"box": {"helper_num": 99}}}),
    ]
    snap = merge(levels)
    assert _probe(snap, "meta", "box", "helper_num") == 0


def test_falsy_empty_string_sets_and_wins() -> None:
    levels = [
        KeyStore({"box": {"image": ""}}),
        KeyStore({"box": {"image": "lower"}}),
    ]
    snap = merge(levels)
    assert _probe(snap, "box", "image") == ""


def test_falsy_false_sets_and_wins() -> None:
    levels = [
        KeyStore({"box": {"share_images": False}}),
        KeyStore({"box": {"share_images": True}}),
    ]
    snap = merge(levels)
    assert _probe(snap, "box", "share_images") is False


def test_falsy_empty_list_sets_and_wins() -> None:
    levels = [
        KeyStore({"agent": {"default": {"run_args": []}}}),
        KeyStore({"agent": {"default": {"run_args": ["a", "b"]}}}),
    ]
    snap = merge(levels)
    assert _probe(snap, "agent", "default", "run_args") == []


# --------------------------------------------------------------------------- #
# Deep per-name union of bind categories (§6e — the headline behavior)         #
# --------------------------------------------------------------------------- #


def test_bind_per_name_union_overrides_only_that_entry() -> None:
    # box overrides ONLY bindings.rw.home; workset's .vault and system's global
    # cache SURVIVE by name. This is the spec's per-name coexistence (§2c).
    box = KeyStore(
        {"box": {"bindings": {"rw": {"home": Bind("/h/box", "/home")}}}}
    )
    workset = KeyStore(
        {
            "box": {
                "bindings": {
                    "rw": {
                        "home": Bind("/h/ws", "/home"),
                        "vault": Bind("/h/vault", "/vault"),
                    }
                }
            }
        }
    )
    system = KeyStore({"system": {"caches": {"pip": Bind("/h/pip", "/pip")}}})
    snap = merge([box, workset, system])

    assert _probe(snap, "box", "bindings", "rw", "home") == Bind("/h/box", "/home")
    assert _probe(snap, "box", "bindings", "rw", "vault") == Bind(
        "/h/vault", "/vault"
    )
    assert _probe(snap, "system", "caches", "pip") == Bind("/h/pip", "/pip")


def test_bind_tuple_is_atomic_never_half_merged() -> None:
    # A Bind LEAF is replaced WHOLE — box's full tuple wins, not a field-merge.
    box = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h/box", "/dbox")}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h/ws", "/dws", "z")}}}})
    snap = merge([box, ws])
    # box's whole tuple wins (opts None); ws's 3-tuple with 'z' is fully gone.
    assert _probe(snap, "box", "bindings", "rw", "home") == Bind("/h/box", "/dbox")


def test_deep_recursion_three_levels() -> None:
    # Deep per-NAME recursion under the §2d discriminated key agent.<name>.*: a
    # higher scope (box) overrides ONE deep leaf while a sibling set only at the
    # lower scope (the agent.<active> level) survives by name. Both carry the TRUE
    # key agent.claude.bindings.* — NOT a bare `agent.*` (the §0 form this
    # revision forbids). box overriding agent.claude.* is exactly the §0 "a box
    # file MAY set an agent.<agent>.* key" capability.
    box = KeyStore(
        {"agent": {"claude": {"bindings": {"ro": {"bin": Bind("/a/box", "/bin")}}}}}
    )
    active = KeyStore(
        {
            "agent": {
                "claude": {
                    "bindings": {
                        "ro": {
                            "bin": Bind("/a/act", "/bin"),
                            "lib": Bind("/a/lib", "/lib"),
                        }
                    }
                }
            }
        }
    )
    # order: box, workset, agent.<active>, agent.default, ...
    snap = merge([box, KeyStore(), active, KeyStore()])
    # box wins the overridden leaf; the active-level sibling survives by name.
    assert (
        _probe(snap, "agent", "claude", "bindings", "ro", "bin")
        == Bind("/a/box", "/bin")
    )
    assert (
        _probe(snap, "agent", "claude", "bindings", "ro", "lib")
        == Bind("/a/lib", "/lib")
    )
    # No bare agent.bindings.* form is ever produced (a §0 violation).
    assert _probe(snap, "agent", "bindings") is __MISSING__


def test_higher_subtree_shadows_lower_nonsubtree() -> None:
    # A higher KeyStore subtree at a name shadows a lower scalar at the same name.
    high = KeyStore({"box": {"auth": KeyStore({"global_enabled": True})}})
    low = KeyStore({"box": {"auth": "scalar"}})
    snap = merge([high, low])
    assert _probe(snap, "box", "auth", "global_enabled") is True
    assert _probe(snap, "box", "auth") == KeyStore({"global_enabled": True})


# --------------------------------------------------------------------------- #
# Agent keys: §2d discriminated form survives the merge (the conformance fix)   #
# — per-agent independence (§0) + active-over-default is by LEVEL, by NAME   #
# --------------------------------------------------------------------------- #


def test_two_agents_coexist_in_snapshot_by_name() -> None:
    # The brief §3 mandate (merge level): agent.claude.* and agent.goose.* set at
    # the box scope COEXIST in the snapshot under their own §2d names — the
    # capability the bare-`agent` collapse destroyed (it made them indistinguishable).
    box = KeyStore(
        {
            "agent": {
                "claude": {"model": "cm"},
                "goose": {"model": "gm"},
            }
        }
    )
    snap = merge([box])
    assert _probe(snap, "agent", "claude", "model") == "cm"
    assert _probe(snap, "agent", "goose", "model") == "gm"
    # No bare agent.model (a §0 violation) is produced.
    assert _probe(snap, "agent", "model") is __MISSING__


def test_higher_scope_overrides_one_agent_by_name() -> None:
    # The brief §3 mandate (merge level): a higher-scope (box) agent.<name>.* key
    # overrides the agent-level same key BY NAME (box > agent.<active>), per §0
    # "a box file MAY set an agent.<agent>.* key … override a specific agent". The
    # agent.default.* layer and the OTHER agent's key both survive untouched.
    # order: box, workset, agent.<active>, agent.default, ...
    box = KeyStore({"agent": {"claude": {"model": "box_claude"}}})
    agent_active = KeyStore({"agent": {"claude": {"model": "cm", "bootstrap": "tmux"}}})
    agent_default = KeyStore({"agent": {"default": {"model": "dm"}}})
    snap = merge([box, KeyStore(), agent_active, agent_default])
    # box wins claude's model; claude's other key (only at the active level) survives.
    assert _probe(snap, "agent", "claude", "model") == "box_claude"
    assert _probe(snap, "agent", "claude", "bootstrap") == "tmux"
    # agent.default.* survives by its own true name (NOT erased / collapsed).
    assert _probe(snap, "agent", "default", "model") == "dm"


# --------------------------------------------------------------------------- #
# present-None TYPE-SPLIT (§3)                                                 #
# --------------------------------------------------------------------------- #


def test_present_none_scalar_kept() -> None:
    # box resets a scalar to None → KEEP None (consumer applies default), and the
    # lower workset value is CLEARED.
    box = KeyStore({"box": {"image": None}})
    ws = KeyStore({"box": {"image": "ws-img"}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "image") is None  # present, value None


def test_present_none_bind_omitted() -> None:
    # box sets bindings.rw.foo = None → OMIT; the workset bind does NOT survive.
    box = KeyStore({"box": {"bindings": {"rw": {"foo": None}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"foo": Bind("/h", "/foo")}}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings", "rw", "foo") is __MISSING__  # OMITTED


def test_present_none_category_leaf_omitted() -> None:
    # A None directly under a bind category (caches.<name> = None) is OMITTED.
    box = KeyStore({"system": {"caches": {"pip": None}}})
    sysf = KeyStore({"system": {"caches": {"pip": Bind("/h/pip", "/pip")}}})
    snap = merge([box, sysf])
    assert _probe(snap, "system", "caches", "pip") is __MISSING__


def test_present_none_lone_subtree_category_omitted_scalar_kept() -> None:
    # The LONE-subtree case (only ONE level sets the containing subtree): the §3
    # type-split must STILL fire on its leaves. A category present-None inside a
    # single-setter subtree is OMITTED; a scalar present-None in the SAME subtree is
    # KEPT. (Before the recursion-always merge fix, a lone KeyStore winner was
    # deep-copied VERBATIM, so its present-None leaves survived un-classified — the
    # F8 latent crash when a box.agent null had no co-setting default.)
    lone = KeyStore(
        {"agent": {"claude": {
            "seeded": {"x": None},   # category leaf None → OMIT
            "model": None,           # scalar None → KEEP
        }}}
    )
    snap = merge([lone])
    assert _probe(snap, "agent", "claude", "seeded", "x") is __MISSING__  # OMITTED
    # The seeded subtree survives (now empty of x); the scalar None is kept.
    assert _probe(snap, "agent", "claude", "model") is None  # KEPT


def test_present_none_masks_unmasked() -> None:
    # workset masks a path; box sets it to None → UNMASK (omitted). A sibling
    # mask survives. masks rides the generic merge (§6f).
    box = KeyStore({"box": {"masks": {"/secret": None}}})
    ws = KeyStore({"box": {"masks": {"/secret": True, "/other": True}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "masks", "/secret") is __MISSING__  # unmasked
    assert _probe(snap, "box", "masks", "/other") is True  # sibling survives


def test_present_none_category_root_omitted() -> None:
    # A whole-category-root reset (bindings = None) OMITs the whole category — never
    # a bare None where a tier-2 Mapping is contracted (§5 coupling). The lower
    # workset bindings subtree is fully cleared.
    box = KeyStore({"box": {"bindings": None}})
    ws = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h", "/home")}}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings") is __MISSING__


def test_present_none_masks_root_omitted() -> None:
    # A masks-root reset (masks = None) OMITs the whole masks category.
    box = KeyStore({"box": {"masks": None}})
    ws = KeyStore({"box": {"masks": {"/a": True}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "masks") is __MISSING__


def test_masks_three_state_generic_merge() -> None:
    # present (True) at box wins over absent at workset; present-None unmasks.
    box = KeyStore({"box": {"masks": {"/a": True, "/b": None}}})
    ws = KeyStore({"box": {"masks": {"/b": True, "/c": True}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "masks", "/a") is True  # box masks
    assert _probe(snap, "box", "masks", "/b") is __MISSING__  # box unmasks /b
    assert _probe(snap, "box", "masks", "/c") is True  # workset mask survives


# --------------------------------------------------------------------------- #
# Refs stay RAW (no expansion — block 3)                                       #
# --------------------------------------------------------------------------- #


def test_refs_left_raw() -> None:
    box = KeyStore({"box": {"bindings": {"rw": {"v": Bind("@workset.vault", "~/v")}}}})
    snap = merge([box])
    # The merge never resolves a ref — both raw tokens survive verbatim.
    assert _probe(snap, "box", "bindings", "rw", "v") == Bind("@workset.vault", "~/v")


# --------------------------------------------------------------------------- #
# Purity — determinism + no input mutation (S15)                              #
# --------------------------------------------------------------------------- #


def test_purity_same_input_equal_output() -> None:
    def _levels() -> list[KeyStore]:
        return [
            KeyStore({"box": {"image": "REQ"}}),
            KeyStore({"box": {"image": "u", "bindings": {"rw": {"h": Bind("/h", "/d")}}}}),
            KeyStore({"workset": {"masks": {"/m": True}}}),
        ]

    snap1 = merge(_levels())
    snap2 = merge(_levels())
    assert snap1 == snap2


def test_purity_inputs_not_mutated() -> None:
    levels = [
        KeyStore({"box": {"bindings": {"rw": {"home": Bind("/box", "/home")}}}}),
        KeyStore(
            {
                "box": {
                    "bindings": {
                        "rw": {
                            "home": Bind("/ws", "/home"),
                            "vault": Bind("/v", "/vault"),
                        }
                    }
                }
            }
        ),
    ]
    before = [copy.deepcopy(lv) for lv in levels]
    merge(levels)
    # S15: the merge builds a fresh tree; the input partials are untouched.
    assert levels == before


def test_snapshot_is_fresh_not_aliased() -> None:
    # The snapshot subtree must not be the SAME object as an input subtree, so a
    # later mutation of the snapshot cannot leak back into a partial.
    inner = KeyStore({"home": Bind("/h", "/home")})
    box = KeyStore({"box": {"bindings": {"rw": inner}}})
    snap = merge([box])
    snap_rw = _probe(snap, "box", "bindings", "rw")
    assert snap_rw == inner
    assert snap_rw is not inner  # a distinct, freshly-built node


# ---------------------------------------------------------------------------
# ``pref.*`` — the REQUEST subtree is EXEMPT from the present-None type-split
# ---------------------------------------------------------------------------

class TestPrefSubtreeIsNotClassified:
    """spec §2h — 'the pref layer MUST NOT interpret emptiness AT ALL'.

    ⚑ Without the exemption the category rule fires on the REQUEST's own path
    (``pref.agent.claude.common.plugins`` has ``common`` among its ancestors),
    so a ``null`` request would be OMITted from the snapshot — applied, but
    invisible to ``config show`` / ``--effective``.
    """

    def test_null_request_at_a_category_shaped_target_survives_the_merge(self):
        """INVERT: remove the ``pref`` guard in ``_resolve_present_none`` ->
        this reddens."""
        box = KeyStore(
            {"pref": {"agent": {"claude": {"common": {"plugins": None}}}}}
        )
        snap = merge([box])
        node = snap["pref"]["agent"]["claude"]["common"]
        assert "plugins" in dict.keys(node)
        assert dict.__getitem__(node, "plugins") is None

    def test_null_request_at_a_masks_shaped_target_survives(self):
        box = KeyStore({"pref": {"agent": {"claude": {"masks": None}}}})
        snap = merge([box])
        assert "masks" in dict.keys(snap["pref"]["agent"]["claude"])

    def test_the_installed_target_is_still_classified_normally(self):
        """The exemption is scoped to the REQUEST record; the ordinary rule
        still OMITs a present-None category leaf at the TARGET path."""
        overlay = KeyStore({"agent": {"claude": {"common": {"plugins": None}}}})
        snap = merge([overlay])
        assert "plugins" not in dict.keys(snap["agent"]["claude"]["common"])

    def test_a_category_named_pref_deeper_down_is_unaffected(self):
        """The guard keys on the ROOT segment, not on the name ``pref``."""
        box = KeyStore({"box": {"common": {"pref": None}}})
        snap = merge([box])
        assert "pref" not in dict.keys(snap["box"]["common"])


# --------------------------------------------------------------------------- #
# DEST-KEYED bindings (BindMap) — the merge is VERIFY-ONLY for the P5 bridge   #
# --------------------------------------------------------------------------- #
#
# ⚑ ``settings_merge`` is NOT edited by the dest-keyed rework (implementation
# plan §5(d)): ``_resolve_present_none`` classifies by PATH SEGMENT (an ancestor
# named ``bindings``), never by the value's shape, and the per-entry union is the
# generic ``_merge_nodes`` recursion over KeyStore nodes. These tests PIN that —
# they are the evidence for the "no change needed" claim, so the claim cannot
# quietly stop being true.
#
# The property under test is the one the whole reshape rests on (design §2b-RESOLVED):
# a dest-keyed arm merges PER ENTRY across levels. A box-level binding at one
# destination must NOT wipe a workset-level binding at a DIFFERENT destination.


def test_bindmap_merges_per_entry_across_levels() -> None:
    # The BindMap analogue of ``test_masks_three_state_generic_merge``: a box arm
    # and a workset arm at DIFFERENT dests both survive; the box entry does NOT
    # replace the arm wholesale.
    box = KeyStore({"box": {"bindings": {"rw": {"~/a": BindEntry("/h/a")}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"~/c": BindEntry("/w/c", "ro")}}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings", "rw", "~/a") == BindEntry("/h/a")
    # ⚑ THE assertion: the inherited workset entry SURVIVES the box-level arm.
    assert _probe(snap, "box", "bindings", "rw", "~/c") == BindEntry("/w/c", "ro")


def test_bindmap_same_dest_most_specific_wins() -> None:
    # At the SAME destination the more specific level wins the whole entry
    # (including its opts) — a leaf win, not a field-wise blend.
    box = KeyStore({"box": {"bindings": {"rw": {"~/a": BindEntry("/box/a")}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"~/a": BindEntry("/ws/a", "ro")}}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings", "rw", "~/a") == BindEntry("/box/a")


def test_bindmap_present_none_unsets_just_that_dest() -> None:
    # A present-``None`` entry OMITs exactly ONE destination (§3 bind-entry reset,
    # classified by the ``bindings`` ancestor segment) and leaves its siblings —
    # at this level AND at the level below — completely alone.
    box = KeyStore(
        {"box": {"bindings": {"rw": {"~/a": BindEntry("/h/a"), "~/b": None}}}}
    )
    ws = KeyStore(
        {
            "box": {
                "bindings": {
                    "rw": {"~/b": BindEntry("/w/b"), "~/c": BindEntry("/w/c")}
                }
            }
        }
    )
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings", "rw", "~/a") == BindEntry("/h/a")
    assert _probe(snap, "box", "bindings", "rw", "~/b") is __MISSING__  # unset
    assert _probe(snap, "box", "bindings", "rw", "~/c") == BindEntry("/w/c")


def test_bindmap_entries_keep_their_type_through_the_merge() -> None:
    # Tuple equality would pass for a plain ``("/h/a",)``; the merge must carry the
    # TYPED value through untouched, because every consumer discriminates the two
    # bind shapes by ``isinstance``, never by arity.
    box = KeyStore({"box": {"bindings": {"ro": {"~/a": BindEntry("/h/a", "z")}}}})
    snap = merge([box])
    entry = _probe(snap, "box", "bindings", "ro", "~/a")
    assert type(entry) is BindEntry
    assert entry.src == "/h/a" and entry.opts == "z"


def test_bindmap_dotted_dest_is_one_key_not_a_path() -> None:
    # A destination legitimately contains dots (``~/.claude/settings.json``). It is
    # a single mapping KEY, never split into keyspace segments — so a sibling dest
    # sharing a prefix is an independent entry and neither shadows the other.
    box = KeyStore(
        {
            "box": {
                "bindings": {
                    "rw": {"~/.claude/settings.json": BindEntry("/h/settings.json")}
                }
            }
        }
    )
    ws = KeyStore(
        {"box": {"bindings": {"rw": {"~/.claude": BindEntry("/h/claude")}}}}
    )
    snap = merge([box, ws])
    arm = _probe(snap, "box", "bindings", "rw")
    assert isinstance(arm, KeyStore)
    assert set(dict.keys(arm)) == {"~/.claude/settings.json", "~/.claude"}


def test_bindmap_root_reset_still_omits_the_whole_arm() -> None:
    # The whole-category-root reset is unchanged by the reshape: ``bindings = None``
    # drops the category, dest-keyed entries and all.
    box = KeyStore({"box": {"bindings": None}})
    ws = KeyStore({"box": {"bindings": {"rw": {"~/a": BindEntry("/w/a")}}}})
    snap = merge([box, ws])
    assert _probe(snap, "box", "bindings") is __MISSING__
