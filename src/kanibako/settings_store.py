"""KeyStore — the resolved-keyspace data structure (storage + types only).

This module defines the raw storage shape of kanibako's settings keyspace and
nothing more: the value space (:data:`StoreValue`), the binding value
(:class:`Bind`), the recursive attribute-dict container (:class:`KeyStore`), and
the module-private absent-vs-present-None sentinel (:data:`_MISSING`). It holds
NO resolution, merge, cascade, ``@``-ref / ``$VAR`` / ``~`` expansion, typed
views, or consumers — those live in later blocks. It imports nothing from the
settings stack and is (for now) imported by nothing.

Authority: ``~/vault/rw/keystore-design.md`` §2 (storage model — primary), §3
(``None`` semantics — type-space consequence only), §6f (``masks`` is a keyed
``dict[box_dest -> bool|None]``, NOT a list); spec
``settings-keyspace-1.6.0-target.md`` §0 (files store UNRESOLVED) + §2a (the
category list + value types).

Storage model (design §2)
-------------------------
* ``KeyStore = dict[str, StoreValue]`` — a real ``dict`` subclass, so every
  dict capability is preserved, with attribute access layered on top.
* ``StoreValue = KeyStore | Bind | str | int | float | bool | list[str] | None``
  — ``list[str]`` is for genuinely list-valued scalar keys; ``masks`` is a
  nested ``KeyStore`` of ``bool | None`` leaves, NOT a bare list.
* ``Bind`` is a typed ``NamedTuple(host, box, opts=None)`` — the
  ``(host_src, box_dest[, opts])`` binding value. NEVER a colon-joined string.
* Access is attribute-style with a ``[]`` fallback for non-identifier / dynamic
  keys (``agent.<name>``, ``env.<VAR>``, hyphens, dots, Python keywords). Both
  surfaces return the SAME :data:`StoreValue` union.
* The SAME :class:`KeyStore` type serves the per-level partials AND the resolved
  snapshot — this block builds only the raw union surface.

``None`` semantics — type space only (design §3)
------------------------------------------------
A key may be **absent** (unset) or present with value **None** (an explicit
reset). Present-``None`` is a legal stored :data:`StoreValue`. The canonical,
collision-safe absent-vs-present-``None`` probe is the **UNBOUND**
``dict.get(store, key, _MISSING)`` (design §3's own form): it returns
:data:`_MISSING` iff the key is absent, ``None`` iff present-``None``, else the
value. Use the unbound ``dict.get`` form — **NOT** the bound ``store.get(...)``,
which is itself shadowed when a key is literally named ``get`` (then
``store.get`` IS the stored value, not the method, and calling it raises). That
asymmetry is by design (collision safety below); the unbound form sidesteps it,
so every consumer (the block-2b merge that probes at every leaf) MUST use it.
``_MISSING`` is **never stored** and is **never** a member of the
:data:`StoreValue` union. The merge LOGIC that consults it lives in block 2b;
here we only define the sentinel and the rule that it never enters value space.

Collision safety (the "MonkeyDict" point, design §2)
----------------------------------------------------
A user key may legitimately be named ``keys``, ``items``, ``get``, ``values``,
``class``, etc. Attribute access therefore resolves to the STORED KEY, never to
a method. Every container operation is exposed only through a ``__dunder__``
(``__iter__``, ``__contains__``, ``__eq__``, ``__repr__``, ...) or through the
inherited ``dict`` methods reached via ``[]``/explicit call — there is no
public, non-dunder method on :class:`KeyStore` that a user key could shadow.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Union


class Bind(NamedTuple):
    """A binding value: ``(host_src, box_dest[, opts])``.

    Design §2 / spec §2a: a binding is a STRUCTURED PAIR (a tuple), never a
    colon-joined ``"host:box"`` string. ``opts`` is the optional per-entry mount
    options override (the 3rd tuple element); it defaults to ``None`` when the
    binding is a plain 2-tuple.
    """

    host: str
    box: str
    opts: str | None = None


# The value space a KeyStore leaf or node may hold (design §2). ``_MISSING`` is
# deliberately NOT a member: it is an absence marker, never a stored value.
StoreValue = Union["KeyStore", Bind, str, int, float, bool, list[str], None]


class _Missing:
    """Type of the module-private :data:`_MISSING` sentinel.

    A distinct singleton type (not ``object()``) so it has a legible ``repr`` and
    so static type-checkers can reason about ``StoreValue | _Missing`` at the few
    internal call sites (block 2b) that distinguish absent from present-``None``.
    """

    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "_MISSING"

    def __bool__(self) -> bool:
        # Defensive: _MISSING must never be mistaken for a real value. Presence
        # is tested with `is _MISSING`, never by truthiness (design §6e).
        return False


#: Module-private sentinel distinguishing an ABSENT key from a present-``None``
#: value at the storage surface, probed with the UNBOUND form
#: ``dict.get(store, key, _MISSING) is _MISSING`` == absent (never the bound
#: ``store.get`` — it is shadowed by a key named ``get``; see the module
#: docstring). Never stored; never a member of :data:`StoreValue`. Consumed by
#: the merge logic in block 2b.
_MISSING: _Missing = _Missing()


def _wrap(value: Any) -> StoreValue:
    """Coerce a raw value into the :data:`StoreValue` space.

    A plain ``dict`` (a nested literal) becomes a :class:`KeyStore` recursively,
    so the whole tree is uniform attribute-dicts. A :class:`KeyStore` is left as
    is (already wrapped). Everything else — :class:`Bind`, scalars, ``list``,
    ``None`` — is stored verbatim. ``list`` is NOT descended into: the union only
    admits ``list[str]`` scalar lists, never nested KeyStores inside a list.
    """
    if isinstance(value, KeyStore):
        return value
    if isinstance(value, dict):
        return KeyStore(value)
    return value


class KeyStore(dict):  # type: ignore[type-arg]
    """Recursive attribute-dict: ``dict[str, StoreValue]`` with attr access.

    Construct from any mapping (or keyword pairs); nested plain ``dict`` literals
    are wrapped into :class:`KeyStore` recursively so the entire tree is uniform.
    Attribute access (``store.foo``) and item access (``store["foo"]``) read and
    write the SAME keys and return the SAME :data:`StoreValue` union; use ``[]``
    for keys that are not valid Python identifiers (``agent.<name>``, hyphens,
    dots, keywords). User keys can never collide with a method — every operation
    is a ``__dunder__`` or an inherited ``dict`` method reached via ``[]``.
    """

    # NOTE: no ``__slots__`` and no instance ``__dict__`` use for storage —
    # state lives entirely in the underlying ``dict``. Attribute writes are
    # redirected to keys (see ``__setattr__``), so the object never grows a
    # competing attribute namespace that a key could disagree with.

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # Funnel everything through dict.update -> __setitem__ so nested dicts
        # are wrapped uniformly regardless of construction style.
        if args:
            if len(args) > 1:
                raise TypeError(
                    f"KeyStore expected at most 1 positional argument, got {len(args)}"
                )
            source = args[0]
            # dict.items(source) — NOT source.items() — so a source KeyStore
            # holding a key named ``items`` does not shadow the method (the very
            # collision this module guards against).
            items = dict.items(source) if isinstance(source, dict) else source
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    # --- item access: the canonical surface; wraps nested dicts on write ---

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, _wrap(value))

    # __getitem__ / __delitem__ / __contains__ / __iter__ / __len__ / get /
    # keys / items / values are inherited from dict unchanged. They are reached
    # via subscription or explicit call, so a user key named ``get`` shadows the
    # method only at the ATTRIBUTE surface (where it correctly resolves to the
    # key); the dict method is still reachable as ``dict.get(store, ...)`` or
    # ``store["get"]`` returns the stored value. That asymmetry is the point.

    # --- attribute access: maps to keys, collision-safe ---

    def __getattribute__(self, name: str) -> Any:
        # Collision safety (design §2): a STORED key must win over a same-named
        # dict method. ``__getattr__`` alone is insufficient — it fires only when
        # normal lookup FAILS, so a key named ``keys``/``get``/``items`` would
        # resolve to the inherited method, never the value. So we intercept
        # non-dunder attribute access here and prefer the stored key.
        #
        # Dunders (``__iter__``, ``__eq__``, ``__class__``, ...) and the private
        # helpers below are ALWAYS real attributes — never shadowable by a user
        # key (no spec key is a dunder), so they bypass the key lookup. This
        # keeps the dict protocol intact while the attribute surface speaks keys.
        if not (name.startswith("__") and name.endswith("__")):
            # dict.__contains__ avoids recursing back through this method.
            if dict.__contains__(self, name):
                return dict.__getitem__(self, name)
        return object.__getattribute__(self, name)

    def __getattr__(self, name: str) -> StoreValue:
        # Reached only for a non-dunder name that is neither a stored key nor a
        # real attribute. Raise AttributeError (not KeyError) to honor the
        # attribute protocol (and to keep hasattr / getattr-with-default sane).
        raise AttributeError(
            f"{type(self).__name__!r} object has no key {name!r}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        # Dunder attributes are real attributes (none are used for storage, but
        # keep the door closed against accidental shadowing of the protocol).
        if name.startswith("__") and name.endswith("__"):
            object.__setattr__(self, name, value)
            return
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(
                f"{type(self).__name__!r} object has no key {name!r}"
            ) from None

    # --- representation ---

    def __repr__(self) -> str:
        # dict.items(self) — NOT self.items() — so a stored key named ``items``
        # cannot shadow the method and break repr (collision safety, design §2).
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in dict.items(self))
        return f"{type(self).__name__}({{{inner}}})"

    # Equality / hashing inherited from dict: two KeyStores are equal iff they
    # hold equal keys -> equal values, and a KeyStore equals a plain dict with
    # the same (wrapped) contents. dict is unhashable; KeyStore stays unhashable.
