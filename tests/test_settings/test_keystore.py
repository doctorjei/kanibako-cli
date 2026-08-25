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
distinguishable from absent via the BOUND ``store.get(k, __MISSING__)`` + the ``__MISSING__`` sentinel;
masks modeled as ``{box_dest: bool|None}`` (NOT a list); repr / equality sanity.

Block 1b (reserved key names): a key named after a public ``dict`` method
(``get keys values items pop popitem setdefault update clear copy fromkeys``), or after one of the
container's OWN public members (``RESERVED_KEY_NAMES``, ``insert_segments`` — each self-listed
because a public name can be neither mangled nor dundered), or
matching the dunder pattern (``__x__``) is REJECTED at write time with
:class:`ReservedKeyError`, at construction AND ``[]``/attr set. With that
guarantee the BOUND ``store.get(k, __MISSING__)`` is the canonical, collision-safe
absent-vs-present-None probe; non-reserved near-miss names (``getter``, ``key``,
``item``) are still allowed.
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

import pytest

# ⚑ ``Bind`` / ``BindEntry`` appear below only as opaque PAYLOADS — the subject of every test in
# this file is the container, not the value shape. Their own tests live in ``test_kb_store.py``.
from kanibako.settings.kb_store import __MISSING__, Bind, BindEntry
from kanibako.settings.keystore import (
    KeyStore,
    ReservedKeyError,
)

#: Stands in for the container's own value parameter when a test needs a class with
#: KeyStore's exact bases and none of its members.
_V = TypeVar("_V")


# --------------------------------------------------------------------------- #
# Construction + nested wrapping                                               #
# --------------------------------------------------------------------------- #


def test_construct_from_nested_dict_literals_wraps_recursively() -> None:
    store = KeyStore(
        {
            "system": {"template": "/data", "cache": {"dir": "/cache"}},
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
    store = KeyStore({"system": {"template": "/data"}})
    assert store.system is store["system"]
    assert store.system.template == store["system"]["template"] == "/data"


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


def test_reserved_set_equals_dict_public_methods_plus_its_own_name() -> None:
    # The reserved set must be EXACTLY dict's public (non-dunder) method names —
    # 0 unguarded, 0 extras — which is what makes the bound store.get safe and
    # the __getattr__ simplification sound. (Provable completeness, block 1b.)
    # PLUS this class's own PUBLIC members. A public member can be neither
    # mangled nor dundered, so it is the one kind the other two routes cannot
    # cover; listing it is what closes that gap — see
    # test_a_key_named_for_a_public_member_is_refused.
    public = {n for n in dir(dict) if not (n.startswith("__") and n.endswith("__"))}
    own_public = {"RESERVED_KEY_NAMES", "insert_segments"}
    assert KeyStore.RESERVED_KEY_NAMES == public | own_public


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


def test_reserved_key_error_is_BOTH_a_kanibako_error_and_a_key_error() -> None:
    # ⚑ BOTH BASES, AND THE ORDER, ARE THE FIX. A settings file carrying ``box: get:``
    # walks through ``_file_partial`` into a store, so this raised out of
    # ``assemble_levels`` BEFORE the keyspace oracle ran; as a bare ``KeyError`` it
    # passed every ``except KanibakoError`` and reached the user as a TRACEBACK.
    #
    # ``KeyError`` is KEPT because callers depend on it: this is a bad-KEY error on a
    # dict-like store, and ``config_interface``'s H1 "returns an error, NEVER raises"
    # probe was written against it. Dropping either base is a silent control-flow change,
    # so both are pinned here rather than left to the end-to-end row that motivated it.
    from kanibako.errors import KanibakoError

    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({"get": 1})
    assert isinstance(exc.value, KanibakoError)
    assert isinstance(exc.value, KeyError)
    # MRO order — ``KanibakoError`` first, or the linearization is not what cli.py sees.
    mro = ReservedKeyError.__mro__
    assert mro.index(KanibakoError) < mro.index(KeyError)


def test_reserved_key_error_str_is_the_message_NOT_a_repr_of_it() -> None:
    # ⚑ ``KeyError.__str__`` REPR-WRAPS its single argument, and it still wins the MRO
    # above (``KanibakoError`` defines none), so without KeyStore's own ``__str__`` the
    # CLI's ``Error: {e}`` line reaches the user wrapped in stray double quotes that no
    # other refusal in the tree has. The message is already a sentence.
    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({"get": 1})
    msg = str(exc.value)
    assert msg == exc.value.args[0]
    assert not msg.startswith('"')
    assert msg.startswith("key 'get' is reserved")


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


@pytest.mark.parametrize("name", ["items", "RESERVED_KEY_NAMES", "insert_segments"])
def test_reserved_refusal_message_is_true_for_every_kind_of_name(name: str) -> None:
    # The reserved set holds THREE kinds of name and ONE message reports all of
    # them: dict's public methods, the store's public CONSTANT, and the store's
    # public METHOD. The old wording asserted "(dict method name)" of whichever
    # name triggered it, and that became FALSE the moment RESERVED_KEY_NAMES
    # joined the set — the diagnostic told the user something untrue at the exact
    # moment they were being corrected. So the message may not make the method
    # claim AT ALL; it names the real reason, which is true of all three.
    # NON-VACUITY: the three probes really are three different kinds.
    assert callable(getattr(dict, "items", None))  # a dict method
    assert not callable(getattr(KeyStore, "RESERVED_KEY_NAMES"))  # a constant
    assert callable(getattr(KeyStore, "insert_segments"))  # a KeyStore method
    assert not hasattr(dict, "insert_segments")  # ...and NOT a dict one

    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({name: "x"})
    msg = str(exc.value)
    assert name in msg  # names the offending key
    assert str(sorted(KeyStore.RESERVED_KEY_NAMES)) in msg  # still actionable
    assert "method" not in msg, (
        f"the refusal calls {name!r} a method, which is false for RESERVED_KEY_NAMES "
        f"(a class attribute, not a method): {msg}"
    )


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


def _key_can_never_shadow(name: str) -> bool:
    """THE INVARIANT, stated ONCE: a class member no declared key can reach.

    Three ways, and a member needs any ONE of them:
      * DUNDER  — ``__x__`` is refused as a key at write time.
      * MANGLED — ``_KeyStore__x``; no declared key is spelled that way.
      * LISTED  — the name is in ``RESERVED_KEY_NAMES``, so it is refused too.
    Asserting the RULE rather than an inventory is deliberate: a new private
    helper passes without editing this test, while a new PUBLIC member fails
    until someone decides to add it to the reserved set.
    """
    return (
        (name.startswith("__") and name.endswith("__"))
        or name.startswith(f"_{KeyStore.__name__}__")
        or name in KeyStore.RESERVED_KEY_NAMES
    )


def test_every_non_dunder_class_member_is_mangled_or_reserved() -> None:
    # CLASS INVARIANT (director/Jei). ``__getattr__`` fires on a lookup MISS ONLY,
    # so a class member ALWAYS wins over a same-named stored key. The exposure is
    # NOT that a key breaks a method — it is that such a key becomes silently
    # UNREADABLE by attribute access while ``store[name]`` still returns it.
    # Every member must therefore be unreachable by any declared key, by one of
    # the three routes in _key_can_never_shadow.
    unguarded = {n for n in vars(KeyStore) if not _key_can_never_shadow(n)}
    assert not unguarded, (
        f"class members a declared key could silently shadow: {sorted(unguarded)} — "
        f"each must be dunder, name-mangled, or listed in RESERVED_KEY_NAMES"
    )
    # ⚑ The baseline is CONSTRUCTED, not listed, and that is the point: on 3.11 —
    # the floor, and what CI runs — typing puts ``_is_protocol`` on a generic dict
    # subclass, while 3.13 does not. It is INHERITED, so the ``vars()`` pin above
    # never saw it and only this one reddened, in CI, on a tree green locally.
    # Naming the noise would hardcode one interpreter's answer AND my guess about
    # WHERE it comes from; building an empty class with the same bases asks the
    # running interpreter both questions at once. An unguarded member of ours is
    # not on the baseline, so the pin still bites — see the sibling guard below.
    class _SameBasesNoMembers(dict[str, object], Generic[_V]):
        pass

    beyond_bases = set(dir(KeyStore)) - set(dir(_SameBasesNoMembers))
    assert all(_key_can_never_shadow(n) for n in beyond_bases), (
        f"attrs beyond dict that a declared key could shadow: "
        f"{sorted(n for n in beyond_bases if not _key_can_never_shadow(n))}"
    )


def test_an_unguarded_member_is_not_swallowed_by_the_baseline() -> None:
    # The pin above is only worth its line if it can still FAIL. Subtracting a
    # constructed baseline is what makes it version-proof, and the risk of any
    # "subtract the noise" fix is that it subtracts the SIGNAL too. This pins the
    # mechanism on a stand-in rather than by mutating the real class, so it costs
    # nothing and cannot leave a mutated KeyStore behind if it fails midway.
    class _Baseline(dict[str, object], Generic[_V]):
        pass

    class _WithUnguarded(dict[str, object], Generic[_V]):
        SNEAKY = 1

    leaked = set(dir(_WithUnguarded)) - set(dir(_Baseline))
    assert leaked == {"SNEAKY"}, (
        f"the baseline subtraction swallowed a real member, so the invariant pin "
        f"cannot fail and pins nothing: {leaked}"
    )
    # ...and the rule must actually REJECT that member, or the pin is vacuous.
    assert not _key_can_never_shadow("SNEAKY")


def test_the_invariant_accepts_each_of_its_three_routes() -> None:
    # NON-VACUITY for the rule itself. If any arm silently stopped matching, the
    # pin would start failing on real members; if the rule were widened to accept
    # everything, it would stop failing on anything. Both directions pinned here.
    assert _key_can_never_shadow("__setitem__")  # dunder
    assert _key_can_never_shadow("_KeyStore__check_key_name")  # mangled
    assert _key_can_never_shadow("RESERVED_KEY_NAMES")  # listed (a constant)
    assert _key_can_never_shadow("insert_segments")  # listed (a method)
    # A plain public name matching none of the three is REJECTED by the rule.
    assert not _key_can_never_shadow("helper")
    assert not _key_can_never_shadow("check_key_name")  # the UNmangled spelling


def test_mangling_returns_the_plain_names_to_key_space() -> None:
    # ⚑ This test USED to pin "a key named for a dundered member no longer shadows
    # it", storing ``insert_segments`` and reading it back. That scenario is now
    # UNREACHABLE and the test would have been asserting something impossible:
    #   * ``insert_segments`` is PUBLIC and self-listed, so the key is REFUSED —
    #     covered by test_reserved_refusal_message_is_true_for_every_kind_of_name;
    #   * and no key can shadow a DUNDER member at all, because the dunder branch
    #     refuses every ``__x__`` key wholesale, whatever members exist.
    # What IS still reachable, and worth pinning, is the other half of the rule:
    # name-MANGLING moves a member out of the key namespace, so the PLAIN spelling
    # of a mangled member is an ordinary key. The members are
    # ``_KeyStore__check_key_name`` / ``_KeyStore__wrap``; nothing is called
    # ``check_key_name`` or ``wrap``, so those store and read back normally.
    store: KeyStore[Any] = KeyStore()
    for name in ("check_key_name", "wrap"):
        assert not hasattr(KeyStore, name)  # non-vacuity: no member by this name
        store[name] = f"value-of-{name}"
        assert store[name] == f"value-of-{name}"
        assert getattr(store, name) == f"value-of-{name}"  # attr surface, not a member
    # ...and the mangled members are still callable internally — a write proves it,
    # since __setitem__ routes through both of them.
    store.insert_segments(("a", "b"), 1)
    assert store["a"]["b"] == 1


@pytest.mark.parametrize("name", ["RESERVED_KEY_NAMES", "insert_segments"])
def test_a_key_named_for_a_public_member_is_refused(name: str) -> None:
    # A PUBLIC member cannot be mangled (callers outside the class name it) and
    # cannot be dundered (a dunder is the data model's namespace, not ours to
    # mint), so neither construction-route covers it. It covers itself: its name
    # is IN the set, which routes it through the existing reserved-name refusal —
    # no new enforcement code. Drop either self-listing and this test reddens.
    with pytest.raises(ReservedKeyError) as exc:
        KeyStore({name: "x"})
    assert name in str(exc.value)
    # Same refusal on the [] and attribute surfaces (all funnel to __setitem__).
    store: KeyStore[Any] = KeyStore()
    with pytest.raises(ReservedKeyError):
        store[name] = "x"
    with pytest.raises(ReservedKeyError):
        setattr(store, name, "x")
    # The class member is untouched by the failed writes.
    assert name in KeyStore.RESERVED_KEY_NAMES
    assert hasattr(KeyStore, name)


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
# present-None vs absent (the __MISSING__ sentinel, type-space only)             #
# --------------------------------------------------------------------------- #


def test_present_none_is_stored_and_distinct_from_absent() -> None:
    store = KeyStore({"reset_me": None})
    # Present-None: the key IS there, value is None.
    assert "reset_me" in store
    assert store["reset_me"] is None
    # Canonical absent-probe (block 1b) is the BOUND store.get(key, __MISSING__) —
    # safe because `get` is a reserved key name, so store.get is always the dict
    # method. It returns __MISSING__ for absent, None for present-None.
    assert store.get("reset_me", __MISSING__) is None
    assert store.get("never_set", __MISSING__) is __MISSING__
    # The UNBOUND form stays equally valid (the pre-1b canonical form).
    assert dict.get(store, "reset_me", __MISSING__) is None
    assert dict.get(store, "never_set", __MISSING__) is __MISSING__


def test_missing_sentinel_is_not_none_and_falsy_guard() -> None:
    # __MISSING__ must be distinguishable from None and from a falsy value, and
    # presence must be tested with `is`, never truthiness.
    assert __MISSING__ is not None
    assert (__MISSING__ is None) is False
    assert bool(__MISSING__) is False  # defensive: never mistaken for a real value


def test_missing_sentinel_singleton_and_repr() -> None:
    from kanibako.settings.kb_store import __Missing__

    assert __Missing__() is __MISSING__
    assert repr(__MISSING__) == "__MISSING__"


def test_missing_sentinel_lives_at_module_level_in_kb_store() -> None:
    # ⚑ REGRESSION, and a REAL bug in the handed-over file: ``__Missing__`` and
    # ``__MISSING__`` were indented INSIDE ``class KeyStore`` while the module
    # docstring and their own comment both call them module-level. Nested, the
    # ``from ...import __MISSING__`` spelling raises ImportError — and TWELVE
    # modules import it exactly that way. So this pins the LOCATION.
    #
    # The location itself MOVED: the sentinel now lives in ``kb_store`` beside the
    # ``StoreValue`` it is defined against, so that ``keystore`` — the container,
    # the unit that can leave the tree — carries no value-space concept at all.
    import kanibako.settings.kb_store as kb
    import kanibako.settings.keystore as ks

    assert isinstance(kb.__MISSING__, kb.__Missing__)
    assert kb.__Missing__.__qualname__ == "__Missing__"  # not "KeyStore.__Missing__"
    # ...and they are NOT reachable through the container class OR its module.
    assert not hasattr(KeyStore, "__Missing__")
    assert not hasattr(KeyStore, "__MISSING__")
    assert not hasattr(ks, "__Missing__")
    assert not hasattr(ks, "__MISSING__")


def test_key_named_get_is_rejected_so_bound_probe_is_safe() -> None:
    # SEAM regression (block 1b, was test_unbound_probe_survives_a_key_named_get):
    # the block-1 foot-gun was a leaf literally named `get` shadowing the bound
    # store.get into a crash. 1b forbids that name at the SOURCE, so it can never
    # arise -> the bound probe is permanently safe.
    with pytest.raises(ReservedKeyError):
        KeyStore({"get": 5})
    # Because `get` can never be a key, the bound store.get is ALWAYS the method.
    store = KeyStore({"home": 5})
    assert store.get("home", __MISSING__) == 5
    assert store.get("absent", __MISSING__) is __MISSING__


def test_bound_probe_is_canonical_three_state() -> None:
    # The bound store.get(k, __MISSING__) probe distinguishes the three states
    # (block 1b canonical): absent -> __MISSING__, present-None -> None, value.
    store = KeyStore({"present_none": None, "present_value": 7})
    assert store.get("present_value", __MISSING__) == 7
    assert store.get("present_none", __MISSING__) is None  # present-None, not absent
    assert store.get("absent", __MISSING__) is __MISSING__
    # nested leaf (the per-leaf merge case, block 2b) — bound form works there too.
    nested = KeyStore({"bindings": {"rw": {"home": None}}})
    rw = nested["bindings"]["rw"]
    assert rw.get("home", __MISSING__) is None
    assert rw.get("absent", __MISSING__) is __MISSING__


def test_present_none_at_attr_surface() -> None:
    store = KeyStore({"reset_me": None})
    assert store.reset_me is None  # present-None reachable by attribute too


def test_nested_present_none_leaf() -> None:
    # present-None inside a category dict (the per-leaf merge case, block 2b).
    store = KeyStore({"bindings": {"rw": {"home": None}}})
    rw = store["bindings"]["rw"]
    assert dict.get(rw, "home", __MISSING__) is None
    assert dict.get(rw, "other", __MISSING__) is __MISSING__


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
    assert dict.get(masks, "/box/unmask-me", __MISSING__) is None
    assert dict.get(masks, "/box/inherit", __MISSING__) is __MISSING__


def test_masks_three_states_at_the_leaf() -> None:
    store = KeyStore({"masks": {"/a": True, "/b": None}})
    masks = store["masks"]
    # present=mask, present-None=unmask, absent=inherit -- three states, no list.
    assert dict.get(masks, "/a", __MISSING__) is True  # mask
    assert dict.get(masks, "/b", __MISSING__) is None  # unmask
    assert dict.get(masks, "/c", __MISSING__) is __MISSING__  # inherit


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
# insert_segments — the ONE walk, now a DUNDER METHOD on the container    #
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
