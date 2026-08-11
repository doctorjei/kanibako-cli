"""Unit tests for the KeyStore container (storage model; block 1 + 1b reserved-key revision).

Carried over VERBATIM from the tests of the single storage module this pair replaced, when it
split into :mod:`kanibako.settings.keystore` (the container) and :mod:`kanibako.settings.kb_store` (the
kanibako value space). The value-shape tests — ``Bind`` / ``BindEntry`` / ``BindMap`` /
``StoreValue`` — moved to ``test_kb_store.py``; everything here pins the CONTAINER. Assertions
changed only where this split respells a name: ``insert_segments`` is now a METHOD, and
``_RESERVED_KEY_NAMES`` is now the public ``KeyStore.RESERVED_KEY_NAMES``.
(``insert_dotted`` RETIRED with its sole caller, and its two tests went with it.)

Covers the brief's checklist: construction from nested dict literals; attr access
== ``[]`` access; dynamic / keyword / hyphen / dotted keys via ``[]``; present-``None`` stored and
distinguishable from absent via the BOUND ``store.get(k, _MISSING)`` + the ``_MISSING`` sentinel;
masks modeled as ``{box_dest: bool|None}`` (NOT a list); repr / equality sanity.

Block 1b (reserved key names): a key named after a public ``dict`` method
(``get keys values items pop popitem setdefault update clear copy fromkeys``) or
matching the dunder pattern (``__x__``) is REJECTED at write time with
:class:`ReservedKeyError`, at construction AND ``[]``/attr set. With that
guarantee the BOUND ``store.get(k, _MISSING)`` is the canonical, collision-safe
absent-vs-present-None probe; non-reserved near-miss names (``getter``, ``key``,
``item``) are still allowed.
"""

from __future__ import annotations

import pytest

# ⚑ ``Bind`` / ``BindEntry`` appear below only as opaque PAYLOADS — the subject of every test in
# this file is the container, not the value shape. Their own tests live in ``test_kb_store.py``.
from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.keystore import (
    _MISSING,
    KeyStore,
    ReservedKeyError,
)


# --------------------------------------------------------------------------- #
# Construction + nested wrapping                                               #
# --------------------------------------------------------------------------- #


def test_construct_from_nested_dict_literals_wraps_recursively() -> None:
    store = KeyStore(
        {
            "system": {"data": "/data", "cache": {"dir": "/cache"}},
            "count": 3,
        }
    )
    assert isinstance(store, KeyStore)
    assert isinstance(store["system"], KeyStore)
    assert isinstance(store["system"]["cache"], KeyStore)
    assert store["system"]["cache"]["dir"] == "/cache"
    assert store["count"] == 3


def test_construct_from_keyword_pairs() -> None:
    store = KeyStore(alpha=1, beta={"nested": True})
    assert store["alpha"] == 1
    assert isinstance(store["beta"], KeyStore)
    assert store["beta"]["nested"] is True


def test_construct_empty() -> None:
    store = KeyStore()
    assert len(store) == 0
    assert list(store) == []


def test_setitem_wraps_nested_dict() -> None:
    store = KeyStore()
    store["env"] = {"FOO": "bar"}
    assert isinstance(store["env"], KeyStore)
    assert store["env"]["FOO"] == "bar"


def test_setattr_wraps_nested_dict() -> None:
    store = KeyStore()
    store.agent = {"default": {"model": "opus"}}
    assert isinstance(store["agent"], KeyStore)
    assert isinstance(store["agent"]["default"], KeyStore)
    assert store["agent"]["default"]["model"] == "opus"


def test_existing_keystore_not_rewrapped() -> None:
    inner = KeyStore({"x": 1})
    store = KeyStore({"inner": inner})
    # Same object, not a copy.
    assert store["inner"] is inner


def test_too_many_positional_args_rejected() -> None:
    with pytest.raises(TypeError):
        KeyStore({"a": 1}, {"b": 2})  # type: ignore[call-overload]


# --------------------------------------------------------------------------- #
# attr access == [] access                                                     #
# --------------------------------------------------------------------------- #


def test_attr_access_equals_item_access() -> None:
    store = KeyStore({"system": {"data": "/data"}})
    assert store.system is store["system"]
    assert store.system.data == store["system"]["data"] == "/data"


def test_attr_write_visible_via_item() -> None:
    store = KeyStore()
    store.foo = 42
    assert store["foo"] == 42


def test_item_write_visible_via_attr() -> None:
    store = KeyStore()
    store["foo"] = 42
    assert store.foo == 42


def test_missing_attr_raises_attributeerror() -> None:
    store = KeyStore({"a": 1})
    with pytest.raises(AttributeError):
        _ = store.nope


def test_delattr_removes_key() -> None:
    store = KeyStore({"a": 1})
    del store.a
    assert "a" not in store
    with pytest.raises(AttributeError):
        del store.a


# --------------------------------------------------------------------------- #
# dynamic / keyword / hyphen / dotted keys via []                             #
# --------------------------------------------------------------------------- #


def test_dynamic_and_nonidentifier_keys_via_item() -> None:
    store = KeyStore(
        {
            "agent.claude": {"model": "opus"},  # dotted
            "env": {"MY-VAR": "v", "class": "k"},  # hyphen, python keyword
        }
    )
    assert store["agent.claude"]["model"] == "opus"
    assert store["env"]["MY-VAR"] == "v"
    assert store["env"]["class"] == "k"


def test_env_var_keyed_subtree() -> None:
    store = KeyStore({"env": {"XDG_DATA_HOME": "/x", "PATH": "/bin"}})
    assert store.env["XDG_DATA_HOME"] == "/x"
    assert store.env["PATH"] == "/bin"


# --------------------------------------------------------------------------- #
# Reserved key names (block 1b) — rejected at the SOURCE                       #
# --------------------------------------------------------------------------- #


def test_reserved_set_equals_dict_public_methods() -> None:
    # The reserved set must be EXACTLY dict's public (non-dunder) method names —
    # 0 unguarded, 0 extras — which is what makes the bound store.get safe and
    # the __getattr__ simplification sound. (Provable completeness, block 1b.)
    public = {n for n in dir(dict) if not (n.startswith("__") and n.endswith("__"))}
    assert KeyStore.RESERVED_KEY_NAMES == public


@pytest.mark.parametrize(
    "name",
    ["get", "keys", "values", "items", "pop", "popitem", "setdefault", "update",
     "clear", "copy", "fromkeys"],
)
def test_reserved_method_name_rejected_at_construction(name: str) -> None:
    # Each dict-method name is rejected when used as a key at CONSTRUCTION
    # (construction funnels through __setitem__).
    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({name: "x"})
    msg = str(exc.value)
    assert name in msg  # names the offending key
    assert "fromkeys" in msg  # lists the (sorted) reserved set -> actionable
    # ReservedKeyError is a KeyError subclass (a bad-KEY error).
    assert isinstance(exc.value, KeyError)


def test_reserved_method_error_names_the_set_without_crashing() -> None:
    # ⚑ REGRESSION, and a REAL bug in the handed-over file: the reserved-set
    # interpolation was left with its f-string braces (``{sorted(...)}``), which
    # builds a SET CONTAINING A LIST -> ``TypeError: unhashable type: 'list'``
    # raised from inside the error path. The user hits it at the exact moment
    # they are being told their key is reserved: the diagnostic destroys itself.
    # So this asserts the ERROR TYPE (not a TypeError) and that the sorted set
    # reaches the message as a list, in order.
    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({"pop": 1})
    msg = str(exc.value)
    assert str(sorted(KeyStore.RESERVED_KEY_NAMES)) in msg
    assert "{" not in msg  # a set literal here would mean the braces survived


@pytest.mark.parametrize(
    "name", ["__init__", "__setitem__", "__class__", "__dict__", "__x__"]
)
def test_dunder_pattern_name_rejected(name: str) -> None:
    # Any __x__ pattern name is reserved (Python data-model attributes).
    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({name: 1})
    assert name in str(exc.value)


def test_reserved_key_rejected_at_item_set_and_attr_set() -> None:
    store = KeyStore()
    # [] set.
    with pytest.raises(ReservedKeyError):
        store["get"] = 1
    # attribute set (routes through __setitem__).
    with pytest.raises(ReservedKeyError):
        store.items = 2  # type: ignore[assignment]
    # A dunder via [] too.
    with pytest.raises(ReservedKeyError):
        store["__len__"] = 3
    # Nothing landed.
    assert len(store) == 0


def test_reserved_key_rejected_when_nested() -> None:
    # A reserved key nested inside a literal is rejected too (the nested dict is
    # wrapped via __setitem__, which validates each key).
    with pytest.raises(ReservedKeyError):
        KeyStore({"box": {"bindings": {"rw": {"get": Bind("/h", "/b")}}}})


def test_non_str_key_rejected() -> None:
    with pytest.raises(TypeError):
        KeyStore({1: "x"})  # type: ignore[dict-item]
    store = KeyStore()
    with pytest.raises(TypeError):
        store[2] = "y"  # type: ignore[index]


def test_reserved_rejection_is_case_sensitive() -> None:
    # The match is case-sensitive (box is Linux). An UPPER variant is NOT
    # reserved, so an env-var-style name like GET is allowed.
    store = KeyStore({"GET": 1, "Items": 2, "Keys": 3})
    assert store["GET"] == 1 and store["Items"] == 2 and store["Keys"] == 3


@pytest.mark.parametrize("name", ["getter", "key", "item", "value", "popper",
                                  "updater", "_get", "get_", "keyring"])
def test_near_miss_non_reserved_names_allowed(name: str) -> None:
    # Names that merely RESEMBLE a reserved name are NOT reserved.
    store = KeyStore({name: "ok"})
    assert store[name] == "ok"
    assert getattr(store, name) == "ok"  # attr surface still works


def test_keystore_class_members_are_exactly_the_declared_four() -> None:
    # CLASS INVARIANT (director/Jei), RESTATED for the split. With the miss-only
    # __getattr__, ANY non-dunder class member resolves BEFORE a same-named key —
    # a collision the reserved set misses. The old module carried zero such
    # members; the split moves four onto the class ON PURPOSE (the public reserved
    # set, the walk that used to be a module function, and the two name-mangled
    # validators). None of the four is a declared key or could be one, so the
    # guard becomes an EXACT pin rather than an empty one: a FIFTH member — the
    # unreviewed kind, which is how a real key gets shadowed — fails here.
    own = {n for n in vars(KeyStore)}
    non_dunder_own = {
        n for n in own if not (n.startswith("__") and n.endswith("__"))
    }
    assert non_dunder_own == {
        "RESERVED_KEY_NAMES",
        "insert_segments",
        "_KeyStore__check_key_name",
        "_KeyStore__wrap",
    }, f"unexpected non-dunder class members: {non_dunder_own}"
    beyond_dict = set(dir(KeyStore)) - set(dir(dict)) - non_dunder_own
    assert all(n.startswith("__") and n.endswith("__") for n in beyond_dict), (
        f"non-dunder attrs beyond dict: "
        f"{[n for n in beyond_dict if not (n.startswith('__') and n.endswith('__'))]}"
    )


def test_underscore_prefixed_key_is_allowed() -> None:
    # A `_`-prefixed NON-dunder name is NOT reserved (it is not a dict method and
    # not a dunder) and — given the class-member pin above — does not shadow any
    # class attribute. So it stores and round-trips by key and by attribute.
    store = KeyStore({"_check_key": 1, "_wrap": 2, "_private": 3})
    assert store["_check_key"] == 1 and store["_wrap"] == 2
    assert store._private == 3  # attribute surface works (no class attr shadows)
    assert getattr(store, "_check_key") == 1


def test_dict_methods_are_the_methods_not_shadowed() -> None:
    # With reserved names forbidden, the bound dict methods are ALWAYS callable
    # (no user key can shadow them).
    store = KeyStore({"home": 1, "vault": 2})
    assert store.get("home") == 1
    assert store.get("absent") is None
    assert set(store.keys()) == {"home", "vault"}
    assert dict(store.items()) == {"home": 1, "vault": 2}


# --------------------------------------------------------------------------- #
# present-None vs absent (the _MISSING sentinel, type-space only)             #
# --------------------------------------------------------------------------- #


def test_present_none_is_stored_and_distinct_from_absent() -> None:
    store = KeyStore({"reset_me": None})
    # Present-None: the key IS there, value is None.
    assert "reset_me" in store
    assert store["reset_me"] is None
    # Canonical absent-probe (block 1b) is the BOUND store.get(key, _MISSING) —
    # safe because `get` is a reserved key name, so store.get is always the dict
    # method. It returns _MISSING for absent, None for present-None.
    assert store.get("reset_me", _MISSING) is None
    assert store.get("never_set", _MISSING) is _MISSING
    # The UNBOUND form stays equally valid (the pre-1b canonical form).
    assert dict.get(store, "reset_me", _MISSING) is None
    assert dict.get(store, "never_set", _MISSING) is _MISSING


def test_missing_sentinel_is_not_none_and_falsy_guard() -> None:
    # _MISSING must be distinguishable from None and from a falsy value, and
    # presence must be tested with `is`, never truthiness.
    assert _MISSING is not None
    assert (_MISSING is None) is False
    assert bool(_MISSING) is False  # defensive: never mistaken for a real value


def test_missing_sentinel_singleton_and_repr() -> None:
    from kanibako.settings.keystore import _Missing

    assert _Missing() is _MISSING
    assert repr(_MISSING) == "_MISSING"


def test_missing_sentinel_lives_at_module_level_not_inside_keystore() -> None:
    # ⚑ REGRESSION, and a REAL bug in the handed-over file: ``_Missing`` and
    # ``_MISSING`` were indented INSIDE ``class KeyStore`` while the module
    # docstring and their own comment both call them module-level/module-private.
    # Nested, ``from ...keystore import _MISSING`` raises ImportError — and
    # ELEVEN modules import it exactly that way. So this pins the LOCATION.
    import kanibako.settings.keystore as ks

    assert isinstance(ks._MISSING, ks._Missing)
    assert ks._Missing.__qualname__ == "_Missing"  # not "KeyStore._Missing"
    # ...and they are NOT reachable through the container class.
    assert not hasattr(KeyStore, "_Missing")
    assert not hasattr(KeyStore, "_MISSING")


def test_key_named_get_is_rejected_so_bound_probe_is_safe() -> None:
    # SEAM regression (block 1b, was test_unbound_probe_survives_a_key_named_get):
    # the block-1 foot-gun was a leaf literally named `get` shadowing the bound
    # store.get into a crash. 1b forbids that name at the SOURCE, so it can never
    # arise -> the bound probe is permanently safe.
    with pytest.raises(ReservedKeyError):
        KeyStore({"get": 5})
    # Because `get` can never be a key, the bound store.get is ALWAYS the method.
    store = KeyStore({"home": 5})
    assert store.get("home", _MISSING) == 5
    assert store.get("absent", _MISSING) is _MISSING


def test_bound_probe_is_canonical_three_state() -> None:
    # The bound store.get(k, _MISSING) probe distinguishes the three states
    # (block 1b canonical): absent -> _MISSING, present-None -> None, value.
    store = KeyStore({"present_none": None, "present_value": 7})
    assert store.get("present_value", _MISSING) == 7
    assert store.get("present_none", _MISSING) is None  # present-None, not absent
    assert store.get("absent", _MISSING) is _MISSING
    # nested leaf (the per-leaf merge case, block 2b) — bound form works there too.
    nested = KeyStore({"bindings": {"rw": {"home": None}}})
    rw = nested["bindings"]["rw"]
    assert rw.get("home", _MISSING) is None
    assert rw.get("absent", _MISSING) is _MISSING


def test_present_none_at_attr_surface() -> None:
    store = KeyStore({"reset_me": None})
    assert store.reset_me is None  # present-None reachable by attribute too


def test_nested_present_none_leaf() -> None:
    # present-None inside a category dict (the per-leaf merge case, block 2b).
    store = KeyStore({"bindings": {"rw": {"home": None}}})
    rw = store["bindings"]["rw"]
    assert dict.get(rw, "home", _MISSING) is None
    assert dict.get(rw, "other", _MISSING) is _MISSING


# --------------------------------------------------------------------------- #
# masks — keyed dict[box_dest -> bool|None], NOT a list (design §6f)          #
# --------------------------------------------------------------------------- #


def test_masks_modeled_as_keyed_dict_not_list() -> None:
    store = KeyStore(
        {
            "masks": {
                "/box/secret": True,  # present (mask)
                "/box/unmask-me": None,  # present-None (unmask)
            }
        }
    )
    masks = store["masks"]
    # A normal nested KeyStore, not a list.
    assert isinstance(masks, KeyStore)
    assert not isinstance(masks, list)
    assert masks["/box/secret"] is True
    # present-None unmask leaf is distinguishable from absent (unbound probe).
    assert dict.get(masks, "/box/unmask-me", _MISSING) is None
    assert dict.get(masks, "/box/inherit", _MISSING) is _MISSING


def test_masks_three_states_at_the_leaf() -> None:
    store = KeyStore({"masks": {"/a": True, "/b": None}})
    masks = store["masks"]
    # present=mask, present-None=unmask, absent=inherit -- three states, no list.
    assert dict.get(masks, "/a", _MISSING) is True  # mask
    assert dict.get(masks, "/b", _MISSING) is None  # unmask
    assert dict.get(masks, "/c", _MISSING) is _MISSING  # inherit


def test_scalar_list_value_is_still_allowed() -> None:
    # list[str] remains a legal StoreValue for genuine scalar lists; it must not
    # be wrapped/descended like a dict.
    store = KeyStore({"some_list": ["a", "b", "c"]})
    assert store["some_list"] == ["a", "b", "c"]
    assert isinstance(store["some_list"], list)


# --------------------------------------------------------------------------- #
# repr / equality sanity                                                       #
# --------------------------------------------------------------------------- #


def test_equality_with_keystore_and_plain_dict() -> None:
    a = KeyStore({"x": 1, "y": {"z": 2}})
    b = KeyStore({"x": 1, "y": {"z": 2}})
    assert a == b
    # Equal to a plain dict whose nested values are equivalently wrapped.
    assert a == {"x": 1, "y": KeyStore({"z": 2})}
    assert a != KeyStore({"x": 1, "y": {"z": 3}})


def test_repr_is_legible_and_names_the_type() -> None:
    store = KeyStore({"a": 1})
    r = repr(store)
    assert r.startswith("KeyStore(")
    assert "'a'" in r and "1" in r


def test_keystore_is_a_dict_subclass() -> None:
    # The design says KeyStore = dict[str, StoreValue]; it must BE a dict so all
    # dict machinery (and downstream typing) holds.
    assert isinstance(KeyStore(), dict)


# --------------------------------------------------------------------------- #
# insert_segments — the ONE walk, now a METHOD on the container                #
# --------------------------------------------------------------------------- #


def test_insert_segments_takes_a_dotted_segment_whole() -> None:
    # ⚑ THE POINT OF THE METHOD. The terminal of a ``binding_derivations`` path
    # is a box DESTINATION — data — and real destinations carry dots
    # (``~/.cache/uv``, ``/home/agent/.claude/plugins``). One segment is one node,
    # whatever it spells.
    store = KeyStore()
    store.insert_segments(
        ("binding_derivations", "agent", "claude", "common",
         "/home/agent/.claude/plugins"),
        BindEntry("/store/plugins"),
    )
    node = store["binding_derivations"]["agent"]["claude"]["common"]
    assert list(dict.keys(node)) == ["/home/agent/.claude/plugins"]
    assert dict.__getitem__(node, "/home/agent/.claude/plugins") == BindEntry(
        "/store/plugins",
    )


def test_insert_segments_keeps_two_dests_that_would_nest_once_split() -> None:
    # ⚑ THE DATA LOSS this replaced: split on ``.``, ``~/.claude`` becomes the
    # path ``~/`` → ``claude`` and ``~/.claude.json`` becomes ``~/`` → ``claude``
    # → ``json``, so installing the second REPLACES the first's leaf with a node
    # and the first derivation disappears with no error.
    store = KeyStore()
    store.insert_segments(("caches", "~/.claude"), BindEntry("/h/a"))
    store.insert_segments(("caches", "~/.claude.json"), BindEntry("/h/b"))
    assert set(dict.keys(store["caches"])) == {"~/.claude", "~/.claude.json"}
    assert dict.__getitem__(store["caches"], "~/.claude") == BindEntry("/h/a")


def test_insert_segments_refuses_an_empty_path() -> None:
    with pytest.raises(ValueError):
        KeyStore().insert_segments((), "x")
