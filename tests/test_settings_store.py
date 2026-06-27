"""Unit tests for the KeyStore storage model (block 1: storage + types + masks).

Covers the brief's checklist: construction from nested dict literals; attr access
== ``[]`` access; dynamic / keyword / hyphen / dotted keys via ``[]``; collision
keys (``keys``, ``items``, ``get``, ``values``) resolve to the STORED value; the
``Bind`` round-trip + ``opts=None`` default; present-``None`` stored and
distinguishable from absent via ``.get`` + ``_MISSING``; masks modeled as
``{box_dest: bool|None}`` (NOT a list); repr / equality sanity.
"""

from __future__ import annotations

import pytest

from kanibako.settings_store import _MISSING, Bind, KeyStore, StoreValue


# --------------------------------------------------------------------------- #
# Bind                                                                         #
# --------------------------------------------------------------------------- #


def test_bind_two_tuple_defaults_opts_none() -> None:
    b = Bind("/host/src", "/box/dest")
    assert b.host == "/host/src"
    assert b.box == "/box/dest"
    assert b.opts is None
    # Positional / namedtuple round-trip.
    assert tuple(b) == ("/host/src", "/box/dest", None)
    assert b == Bind(host="/host/src", box="/box/dest", opts=None)


def test_bind_three_tuple_carries_opts() -> None:
    b = Bind("/host/sock", "/box/sock", "z")
    assert b.opts == "z"
    assert tuple(b) == ("/host/sock", "/box/sock", "z")


def test_bind_is_not_a_string() -> None:
    # Load-bearing: a binding is a structured pair, never "host:box".
    b = Bind("/h", "/b")
    assert isinstance(b, tuple)
    assert not isinstance(b, str)


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
# Collision safety — user keys named like dict methods                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    ["keys", "items", "get", "values", "update", "pop", "setdefault", "copy"],
)
def test_collision_keys_resolve_to_stored_value_via_attr(name: str) -> None:
    store = KeyStore({name: "USER_VALUE"})
    # Attribute access yields the STORED value, never the dict method.
    assert getattr(store, name) == "USER_VALUE"
    assert store[name] == "USER_VALUE"


@pytest.mark.parametrize("name", ["keys", "items", "values", "get"])
def test_collision_key_survives_repr_and_copy(name: str) -> None:
    # The module's OWN internals (repr, construction-copy) must not invoke a
    # shadowed method: a stored key named ``items`` must not crash repr or copy.
    store = KeyStore({name: 5})
    r = repr(store)  # would TypeError if repr used self.items()
    assert f"'{name}'" in r and "5" in r
    copied = KeyStore(store)  # would TypeError if __init__ used source.items()
    assert copied[name] == 5
    assert copied == store


def test_dict_methods_still_reachable_despite_collision_keys() -> None:
    store = KeyStore({"keys": 1, "items": 2, "get": 3, "values": 4})
    # The dict protocol is intact via the unbound dict methods / iteration.
    assert set(dict.keys(store)) == {"keys", "items", "get", "values"}
    assert dict.get(store, "keys") == 1
    assert dict.get(store, "absent", "default") == "default"
    # Iteration (a dunder) is unaffected by a key literally named "keys".
    assert set(store) == {"keys", "items", "get", "values"}
    assert "get" in store


def test_collision_key_does_not_break_construction_or_repr() -> None:
    store = KeyStore({"get": {"nested": True}})
    assert isinstance(store["get"], KeyStore)
    assert "get" in repr(store)


# --------------------------------------------------------------------------- #
# present-None vs absent (the _MISSING sentinel, type-space only)             #
# --------------------------------------------------------------------------- #


def test_present_none_is_stored_and_distinct_from_absent() -> None:
    store = KeyStore({"reset_me": None})
    # Present-None: the key IS there, value is None.
    assert "reset_me" in store
    assert store["reset_me"] is None
    # The canonical absent-probe is the UNBOUND dict.get(store, key, _MISSING)
    # form (collision-safe; the bound store.get is shadowed by a key named
    # `get`). It returns _MISSING for absent, None for present-None.
    assert dict.get(store, "reset_me", _MISSING) is None
    assert dict.get(store, "never_set", _MISSING) is _MISSING


def test_missing_sentinel_is_not_none_and_falsy_guard() -> None:
    # _MISSING must be distinguishable from None and from a falsy value, and
    # presence must be tested with `is`, never truthiness.
    assert _MISSING is not None
    assert (_MISSING is None) is False
    assert bool(_MISSING) is False  # defensive: never mistaken for a real value


def test_missing_sentinel_singleton_and_repr() -> None:
    from kanibako.settings_store import _Missing

    assert _Missing() is _MISSING
    assert repr(_MISSING) == "_MISSING"


def test_unbound_probe_survives_a_key_named_get() -> None:
    # SEAM regression (director): the absent-probe block 2b walks at every leaf
    # must be collision-safe even when a leaf is literally named `get`. The
    # canonical UNBOUND form keeps working; the BOUND store.get(...) does NOT,
    # because store.get correctly resolves to the STORED VALUE (a non-callable),
    # so calling it raises -- which is exactly why the unbound form is canonical.
    store = KeyStore({"get": 5})
    # Unbound probe: works regardless of the `get` collision.
    assert dict.get(store, "get", _MISSING) == 5
    assert dict.get(store, "absent", _MISSING) is _MISSING
    # Attribute access yields the STORED value, never the bound method.
    assert store.get == 5
    assert store["get"] == 5
    # And the bound form is unusable here (documents WHY unbound is canonical).
    with pytest.raises(TypeError):
        store.get("absent", _MISSING)  # type: ignore[operator]


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
# Bind stored inside a category                                                #
# --------------------------------------------------------------------------- #


def test_bind_value_stored_in_category_unmodified() -> None:
    bind = Bind("/host/home", "/box/home")
    store = KeyStore({"bindings": {"rw": {"home": bind}}})
    got = store.bindings["rw"]["home"]
    assert got is bind
    assert isinstance(got, Bind)
    assert got.host == "/host/home" and got.box == "/box/home" and got.opts is None


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


def test_storevalue_alias_is_importable() -> None:
    # Public surface sanity: the union alias is exported.
    assert StoreValue is not None
