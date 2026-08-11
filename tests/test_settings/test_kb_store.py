"""Unit tests for the kanibako store SHAPE — the value space inside a KeyStore.

Carried over VERBATIM from the tests of the single storage module this pair replaced, when it
split into :mod:`kanibako.settings.keystore` (the container, tested in ``test_keystore.py``) and
:mod:`kanibako.settings.kb_store` (this module's subject: :class:`Bind`, :class:`BindEntry`,
``BindMap``, :data:`StoreValue`). No assertion changed — only the import path.

The container is deliberately value-space-AGNOSTIC, so these are the tests that would NOT travel
with it if ``keystore`` ever leaves the tree: the ``Bind`` round-trip + ``opts=None`` default, the
``BindEntry`` arity trap, and the ``BindMap``-as-a-NODE guarantee that makes a dest-keyed arm merge
per entry.
"""

from __future__ import annotations

from kanibako.settings.kb_store import Bind, BindEntry, StoreValue
from kanibako.settings.keystore import KeyStore


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
# StoreValue                                                                   #
# --------------------------------------------------------------------------- #


def test_storevalue_alias_is_importable() -> None:
    # Public surface sanity: the union alias is exported.
    assert StoreValue is not None


# --------------------------------------------------------------------------- #
# BindEntry / BindMap — the DEST-KEYED shape (R-5/R-6) and the P5→P8 bridge    #
# --------------------------------------------------------------------------- #


def test_bind_entry_one_element_defaults_opts_none() -> None:
    e = BindEntry("/host/src")
    assert e.src == "/host/src"
    assert e.opts is None
    assert tuple(e) == ("/host/src", None)


def test_bind_entry_carries_explicit_opts() -> None:
    e = BindEntry("/host/src", "ro")
    assert (e.src, e.opts) == ("/host/src", "ro")


def test_bind_and_bind_entry_are_mutually_exclusive_types() -> None:
    # ⚑ THE ARITY TRAP. Both shapes admit a 2-element tuple with OPPOSITE
    # meanings, so every consumer discriminates by TYPE. Neither NamedTuple is a
    # subclass of the other, so ``isinstance`` separates them exactly — this is
    # the property the whole bridge rests on.
    assert not isinstance(BindEntry("/a"), Bind)
    assert not isinstance(Bind("/a", "/b"), BindEntry)
    # And a 2-element value of each shape is NOT equal to the other's, because
    # ``Bind`` always materialises 3 elements (opts defaults into the tuple).
    assert BindEntry("/a", "/b") != Bind("/a", "/b")


def test_bind_entry_round_trips_through_a_keystore() -> None:
    store = KeyStore({"box": {"bindings": {"rw": {"~/.claude": BindEntry("/h/c")}}}})
    entry = store["box"]["bindings"]["rw"]["~/.claude"]
    assert type(entry) is BindEntry
    assert entry == BindEntry("/h/c")


def test_bindmap_materialises_as_a_node_not_an_opaque_leaf() -> None:
    # ⚑ Load-bearing (see the ``BindMap`` docstring): a plain dict assigned into a
    # KeyStore is WRAPPED into a nested KeyStore node, so a dest-keyed arm merges
    # PER ENTRY through the generic node recursion rather than wholesale.
    store = KeyStore()
    store["arm"] = {"~/a": BindEntry("/h/a"), "~/b": BindEntry("/h/b", "ro")}
    arm = store["arm"]
    assert isinstance(arm, KeyStore)
    assert set(dict.keys(arm)) == {"~/a", "~/b"}
    assert type(dict.__getitem__(arm, "~/b")) is BindEntry
