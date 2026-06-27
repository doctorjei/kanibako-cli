"""Unit tests for block 2b — the cascade MERGE (settings_merge).

Covers the brief §4 checklist for the pure ``merge(levels) -> (snapshot,
warnings)``:

* per-NAME union — a box ``bindings.rw.home`` overrides ONLY that entry while
  workset / system entries SURVIVE by name (deep recursion, not whole-node
  replace);
* most-specific-first LEAF win;
* ``_MISSING`` vs truthiness — a leaf set to ``0`` / ``""`` / ``False`` SETS and
  wins (NOT skipped);
* present-None TYPE-SPLIT (§3) — scalar → None KEPT; bind → OMITTED; category →
  OMITTED; masks → UNMASKED (omitted);
* ``required`` override — exactly ONE warning over a lower set value, and NONE
  when nothing lower set it; the warning names the dotted key;
* masks 3-state via the GENERIC merge;
* purity — same input twice → equal output, inputs unmutated; refs stay RAW.

The merge keys by NAME only (NO ``box_dest`` reconcile, §6g) and never expands a
ref (block 3). Authority: design §6e / §3 / §6f / §4; spec §2 / §2a / §2c.
"""

from __future__ import annotations

import copy

from kanibako.settings_merge import merge
from kanibako.settings_store import _MISSING, Bind, KeyStore


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _probe(store: KeyStore, *segments: str) -> object:
    """Walk *segments* into *store* with the unbound-``dict.get`` probe (S3),
    returning ``_MISSING`` if any segment is absent (so a present-None leaf is
    distinguishable from an absent one)."""
    node: object = store
    for seg in segments:
        if not isinstance(node, KeyStore):
            return _MISSING
        node = dict.get(node, seg, _MISSING)
    return node


# --------------------------------------------------------------------------- #
# Empty / trivial                                                             #
# --------------------------------------------------------------------------- #


def test_empty_levels_empty_snapshot() -> None:
    snap, warns = merge([])
    assert snap == KeyStore()
    assert warns == []


def test_all_empty_partials_empty_snapshot() -> None:
    snap, warns = merge([KeyStore(), KeyStore(), KeyStore()])
    assert snap == KeyStore()
    assert warns == []


def test_absent_everywhere_not_in_snapshot() -> None:
    # A name set nowhere never appears; only the one set name does.
    levels = [KeyStore(), KeyStore({"box": {"image": "img"}})]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "image") == "img"
    assert _probe(snap, "box", "missing") is _MISSING


# --------------------------------------------------------------------------- #
# Most-specific-first leaf win                                                 #
# --------------------------------------------------------------------------- #


def test_most_specific_scalar_wins() -> None:
    # required, box, workset, ... — index 0 is most specific (the cap).
    levels = [
        KeyStore(),  # required (empty)
        KeyStore({"box": {"image": "box-img"}}),
        KeyStore({"box": {"image": "ws-img"}}),
    ]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "image") == "box-img"


def test_lower_scope_survives_when_higher_absent() -> None:
    levels = [
        KeyStore(),
        KeyStore({"box": {"a": "box"}}),
        KeyStore({"box": {"b": "ws"}}),
    ]
    snap, _ = merge(levels)
    # Per-name union within the box subtree: both a (box) and b (workset) survive.
    assert _probe(snap, "box", "a") == "box"
    assert _probe(snap, "box", "b") == "ws"


# --------------------------------------------------------------------------- #
# _MISSING vs truthiness — falsy values SET and win (S3)                       #
# --------------------------------------------------------------------------- #


def test_falsy_zero_sets_and_wins() -> None:
    levels = [
        KeyStore(),
        KeyStore({"box": {"n": 0}}),  # box sets 0 — falsy but SET
        KeyStore({"box": {"n": 99}}),  # workset 99 — must be shadowed
    ]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "n") == 0


def test_falsy_empty_string_sets_and_wins() -> None:
    levels = [
        KeyStore(),
        KeyStore({"box": {"s": ""}}),
        KeyStore({"box": {"s": "lower"}}),
    ]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "s") == ""


def test_falsy_false_sets_and_wins() -> None:
    levels = [
        KeyStore(),
        KeyStore({"box": {"flag": False}}),
        KeyStore({"box": {"flag": True}}),
    ]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "flag") is False


def test_falsy_empty_list_sets_and_wins() -> None:
    levels = [
        KeyStore(),
        KeyStore({"box": {"xs": []}}),
        KeyStore({"box": {"xs": ["a", "b"]}}),
    ]
    snap, _ = merge(levels)
    assert _probe(snap, "box", "xs") == []


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
    snap, _ = merge([KeyStore(), box, workset, system])

    assert _probe(snap, "box", "bindings", "rw", "home") == Bind("/h/box", "/home")
    assert _probe(snap, "box", "bindings", "rw", "vault") == Bind(
        "/h/vault", "/vault"
    )
    assert _probe(snap, "system", "caches", "pip") == Bind("/h/pip", "/pip")


def test_bind_tuple_is_atomic_never_half_merged() -> None:
    # A Bind LEAF is replaced WHOLE — box's full tuple wins, not a field-merge.
    box = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h/box", "/dbox")}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h/ws", "/dws", "z")}}}})
    snap, _ = merge([KeyStore(), box, ws])
    # box's whole tuple wins (opts None); ws's 3-tuple with 'z' is fully gone.
    assert _probe(snap, "box", "bindings", "rw", "home") == Bind("/h/box", "/dbox")


def test_deep_recursion_three_levels() -> None:
    # agent.<active> overrides one deep leaf; agent.default sibling survives.
    active = KeyStore({"agent": {"bindings": {"ro": {"bin": Bind("/a/act", "/bin")}}}})
    default = KeyStore(
        {
            "agent": {
                "bindings": {
                    "ro": {
                        "bin": Bind("/a/def", "/bin"),
                        "lib": Bind("/a/lib", "/lib"),
                    }
                }
            }
        }
    )
    # order: required, box, workset, agent.<active>, agent.default, ...
    snap, _ = merge([KeyStore(), KeyStore(), KeyStore(), active, default])
    assert _probe(snap, "agent", "bindings", "ro", "bin") == Bind("/a/act", "/bin")
    assert _probe(snap, "agent", "bindings", "ro", "lib") == Bind("/a/lib", "/lib")


def test_higher_subtree_shadows_lower_nonsubtree() -> None:
    # A higher KeyStore subtree at a name shadows a lower scalar at the same name.
    high = KeyStore({"box": {"x": KeyStore({"a": 1})}})
    low = KeyStore({"box": {"x": "scalar"}})
    snap, _ = merge([KeyStore(), high, low])
    assert _probe(snap, "box", "x", "a") == 1
    assert _probe(snap, "box", "x") == KeyStore({"a": 1})


# --------------------------------------------------------------------------- #
# present-None TYPE-SPLIT (§3)                                                 #
# --------------------------------------------------------------------------- #


def test_present_none_scalar_kept() -> None:
    # box resets a scalar to None → KEEP None (consumer applies default), and the
    # lower workset value is CLEARED.
    box = KeyStore({"box": {"image": None}})
    ws = KeyStore({"box": {"image": "ws-img"}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "image") is None  # present, value None


def test_present_none_bind_omitted() -> None:
    # box sets bindings.rw.foo = None → OMIT; the workset bind does NOT survive.
    box = KeyStore({"box": {"bindings": {"rw": {"foo": None}}}})
    ws = KeyStore({"box": {"bindings": {"rw": {"foo": Bind("/h", "/foo")}}}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "bindings", "rw", "foo") is _MISSING  # OMITTED


def test_present_none_category_leaf_omitted() -> None:
    # A None directly under a bind category (caches.<name> = None) is OMITTED.
    box = KeyStore({"system": {"caches": {"pip": None}}})
    sysf = KeyStore({"system": {"caches": {"pip": Bind("/h/pip", "/pip")}}})
    snap, _ = merge([KeyStore(), box, sysf])
    assert _probe(snap, "system", "caches", "pip") is _MISSING


def test_present_none_masks_unmasked() -> None:
    # workset masks a path; box sets it to None → UNMASK (omitted). A sibling
    # mask survives. masks rides the generic merge (§6f).
    box = KeyStore({"box": {"masks": {"/secret": None}}})
    ws = KeyStore({"box": {"masks": {"/secret": True, "/other": True}}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "masks", "/secret") is _MISSING  # unmasked
    assert _probe(snap, "box", "masks", "/other") is True  # sibling survives


def test_present_none_category_root_omitted() -> None:
    # A whole-category-root reset (bindings = None) OMITs the whole category — never
    # a bare None where a tier-2 Mapping is contracted (§5 coupling). The lower
    # workset bindings subtree is fully cleared.
    box = KeyStore({"box": {"bindings": None}})
    ws = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/h", "/home")}}}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "bindings") is _MISSING


def test_present_none_masks_root_omitted() -> None:
    # A masks-root reset (masks = None) OMITs the whole masks category.
    box = KeyStore({"box": {"masks": None}})
    ws = KeyStore({"box": {"masks": {"/a": True}}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "masks") is _MISSING


def test_masks_three_state_generic_merge() -> None:
    # present (True) at box wins over absent at workset; present-None unmasks.
    box = KeyStore({"box": {"masks": {"/a": True, "/b": None}}})
    ws = KeyStore({"box": {"masks": {"/b": True, "/c": True}}})
    snap, _ = merge([KeyStore(), box, ws])
    assert _probe(snap, "box", "masks", "/a") is True  # box masks
    assert _probe(snap, "box", "masks", "/b") is _MISSING  # box unmasks /b
    assert _probe(snap, "box", "masks", "/c") is True  # workset mask survives


# --------------------------------------------------------------------------- #
# required-override WARNING (S10)                                              #
# --------------------------------------------------------------------------- #


def test_required_override_warns_once() -> None:
    required = KeyStore({"box": {"image": "REQ"}})
    box = KeyStore({"box": {"image": "user"}})
    snap, warns = merge([required, box])
    assert _probe(snap, "box", "image") == "REQ"
    assert len(warns) == 1
    assert "box.image" in warns[0]
    assert "REQ" in warns[0]


def test_required_no_warn_when_nothing_lower_set() -> None:
    # required sets a name no lower scope set — no override, no warning.
    required = KeyStore({"box": {"image": "REQ"}})
    box = KeyStore({"box": {"other": "x"}})
    _snap, warns = merge([required, box])
    assert warns == []


def test_required_override_warns_at_deep_leaf() -> None:
    required = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/req", "/home")}}}})
    box = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/usr", "/home")}}}})
    snap, warns = merge([required, box])
    assert _probe(snap, "box", "bindings", "rw", "home") == Bind("/req", "/home")
    assert len(warns) == 1
    assert "box.bindings.rw.home" in warns[0]


def test_required_present_none_override_warns_and_omits() -> None:
    # required resets a bind to None over a user bind → OMIT + a warning fires
    # (the cap overrode the user's value, even though the result is an omit).
    required = KeyStore({"box": {"bindings": {"rw": {"home": None}}}})
    box = KeyStore({"box": {"bindings": {"rw": {"home": Bind("/usr", "/home")}}}})
    snap, warns = merge([required, box])
    assert _probe(snap, "box", "bindings", "rw", "home") is _MISSING
    assert len(warns) == 1
    assert "box.bindings.rw.home" in warns[0]


def test_required_subtree_merges_and_warns_per_leaf() -> None:
    # required sets two leaves under a subtree; box set both → two warnings, one
    # per overridden leaf, deep-merged with a surviving box-only sibling.
    required = KeyStore({"box": {"bindings": {"rw": {"a": Bind("/r", "/a"),
                                                     "b": Bind("/r", "/b")}}}})
    box = KeyStore({"box": {"bindings": {"rw": {"a": Bind("/u", "/a"),
                                                "b": Bind("/u", "/b"),
                                                "c": Bind("/u", "/c")}}}})
    snap, warns = merge([required, box])
    assert _probe(snap, "box", "bindings", "rw", "a") == Bind("/r", "/a")
    assert _probe(snap, "box", "bindings", "rw", "c") == Bind("/u", "/c")
    assert len(warns) == 2
    assert any("box.bindings.rw.a" in w for w in warns)
    assert any("box.bindings.rw.b" in w for w in warns)


# --------------------------------------------------------------------------- #
# Refs stay RAW (no expansion — block 3)                                       #
# --------------------------------------------------------------------------- #


def test_refs_left_raw() -> None:
    box = KeyStore({"box": {"bindings": {"rw": {"v": Bind("@workset.vault", "~/v")}}}})
    snap, _ = merge([KeyStore(), box])
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

    snap1, warns1 = merge(_levels())
    snap2, warns2 = merge(_levels())
    assert snap1 == snap2
    assert warns1 == warns2


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
    snap, _ = merge([KeyStore(), box])
    snap_rw = _probe(snap, "box", "bindings", "rw")
    assert snap_rw == inner
    assert snap_rw is not inner  # a distinct, freshly-built node
