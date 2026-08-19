"""KeyStore — the resolved-keyspace data structure (storage + types only).

This module defines the KeyStore class & its supporting types _only_: the recursive attribute-dict
container (:class:`KeyStore`) & reserved key errors (:class:`ReservedKeyError`) - NOT resolution,
merge, cascade, ``@``-ref / ``$VAR`` / ``~`` expansion, typed views, or consumers — which live
elsewhere. It imports nothing from the settings stack beyond its own :mod:`keystore_strings`.

⚑ This is purely a data structure, mostly unopionated about its contents; its value-agnostic design
intentionally limits cross-dependencies."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from .keystore_strings import (
    ERR_ATTRIBUTE_NO_KEY,
    ERR_RESERVEDKEY_DUNDER,
    ERR_RESERVEDKEY_SHADOW,
    ERR_TYPE_KEYSTORE_ARGS,
    ERR_TYPE_NONSTRING_KEY,
)

# Leaf value space of a :class:`KeyStore`
V = TypeVar("V")


### Errors / Exceptions ###

class ReservedKeyError(KeyError):
    """Raised when a :class:`KeyStore` write uses a RESERVED key name (bad-key, not bad-value)"""


### Main KeyStore Class ###

class KeyStore(dict[str, "V | KeyStore[V]"], Generic[V]):
    """Recursive attribute-dict: ``dict[str, V | KeyStore[V]]`` w. attr access. Inherits `dict`."""

    # Public, non-dunder methods of :class:`dict`, PLUS KeyStore's public members - all forbidden
    # as user keys.
    #
    # ⚑ EXPOSURE, stated correctly: ``__getattr__`` fires on lookup MISS ONLY, so class members
    # ALWAYS win. A key spelled like a member becomes SILENTLY UNREADABLE through attribute access
    # (``store[name]`` still available); ``store.name`` still returns member. That's intentional.
    #
    # EVERY CLASS MEMER satisfies this rule: it is NAME-MANGLED (``_KeyStore__*``), a DUNDER, or is
    # LISTED HERE. Mangled & dunder names cannot be keys by construction; no key is spelled
    # ``_KeyStore__*``, & dunder keys are refused at write time; internals need no entry. PUBLIC
    # members lack these protections & must be named, hence this set of member named.
    RESERVED_KEY_NAMES: frozenset[str] = frozenset({"get", "keys", "values",
        "items", "pop", "popitem", "setdefault", "update", "clear", "copy", "fromkeys",
        "RESERVED_KEY_NAMES", "insert_segments",})

    # No ``__slots__`` instance ``__dict__`` for storage; state is in inherited `dict`. ⚑ ``self``
    # is pinned to ``KeyStore[Any]``: an arg-free ``KeyStore()`` has nothing to solve ``V`` from,
    # & unsolved ``V`` makes mypy demand an annotation on bare construction. Explicit
    # ``KeyStore[StoreValue]`` annotations bind as usual.
    def __init__(self: KeyStore[Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # Funnel everything through dict.update -> __setitem__ to wrap nested dicts uniformly.
        if args:
            if len(args) > 1:
                raise TypeError(ERR_TYPE_KEYSTORE_ARGS % len(args))
            source = args[0]
            # dict.items(source) (NOT source.items()) — reads REAL storage of the received mapping.
            # ⚑ For KeyStore, they're identical: ``items`` is a reserved, & even if force-written
            # past the check, METHOD still wins lookup (__getattr__ fires only on a MISS). Unbound
            # calls earn their keep against OTHER dict subclasses, whose items() may be overridden
            # to provide something other than its contents.
            items = dict.items(source) if isinstance(source, dict) else source
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    # ⚑ PUBLIC, so it is SELF-LISTED in RESERVED_KEY_NAMES. Called from other modules, it can be
    # neither mangled nor dundered, and as a plain name it would win over a same-named KEY at the
    # attr surface (__getattr__ fires on MISS only) — leaving that key readable by [] but not by
    # attribute. Naming it in the reserved set refuses the key instead, at write time.
    def insert_segments(self, segments: "Sequence[str]", value: Any) -> None:
        """Install *value* at the path *segments* VERBATIM; each segment is _one_ node."""
        # ⚑ Entry-point of box-dest, terminal path ``binding_derivations.<declaration-key>.<dest>``.
        # Routinely contain dots (``.``, ``~/.cache/uv``, etc.).
        parts = tuple(segments)
        if not parts:
            raise ValueError("insert_segments needs at least one segment")
        node: KeyStore = self
        for seg in parts[:-1]:
            child = dict.get(node, seg, None)
            if not isinstance(child, KeyStore):
                child = KeyStore()
                node[seg] = child
            node = child
        node[parts[-1]] = value

    # --- item access: canonical surface; wraps nested dicts on write. ---

    @staticmethod
    def __check_key_name(key: Any) -> str:
        """Validate key; return unchanged if non-dunder str & NOT a reserved name, or raise."""
        if not isinstance(key, str):
            raise TypeError(ERR_TYPE_NONSTRING_KEY % (type(key).__name__, repr(key)))

        if key.startswith("__") and key.endswith("__"):
            raise ReservedKeyError(ERR_RESERVEDKEY_DUNDER % repr(key))
        if key in KeyStore.RESERVED_KEY_NAMES:
            raise ReservedKeyError(
                ERR_RESERVEDKEY_SHADOW % (repr(key), sorted(KeyStore.RESERVED_KEY_NAMES))
            )
        return key

    @staticmethod
    def __wrap(value: Any) -> V | KeyStore[V]:
        """Coerce raw value into stored space (``V`` | node); a plain `dict` becomes a node."""
        if isinstance(value, KeyStore):
            return value
        if isinstance(value, dict):
            return KeyStore(value)
        return value

    # THE single write funnel — construction, ``[]``-set & attribute-set all arrive here, so the
    # reserved-name check cannot be routed around. ⚑ It is not the METHOD that needs protecting: a
    # class attribute ALWAYS wins the lookup, so no stored key can break ``store.get``. What needs
    # protecting is the VALUE. A key spelled like a member would be stored where ``store.<name>``
    # can never read it while ``store[name]`` still returns it — a silent split. Refusing the key
    # at write time is what keeps that unreadable state from ever existing.
    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(self.__check_key_name(key), self.__wrap(value))

    # --- attribute access: maps keys; dict methods are reserved; key cannot shadow real attr. ---

    # Fires ONLY on lookup miss; as reserved names cannot be stored as keys, no method collision.
    def __getattr__(self, name: str) -> V | KeyStore[V]:
        if dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        # Not stored key or attribute, so raise AttributeError (not KeyError).
        raise AttributeError(ERR_ATTRIBUTE_NO_KEY % (repr(type(self).__name__), repr(name)))

    # Dunder attributes are real (not used for storage; this helps avoid accidents).
    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("__") and name.endswith("__"):
            object.__setattr__(self, name, value)
            return
        self[name] = value

    def __delattr__(self, name: str) -> None:
        try:
            del self[name]
        except KeyError:
            raise AttributeError(
                ERR_ATTRIBUTE_NO_KEY % (repr(type(self).__name__), repr(name))
            ) from None

    # --- representation ---

    # Use dict.items(self) — NOT self.items() — the unbound form reads storage directly and cannot
    # be answered by an override. (A key named ``items`` could not break this in any case: it is
    # reserved, and an attribute lookup finds the method whatever is stored.)
    def __repr__(self) -> str:
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in dict.items(self))
        return f"{type(self).__name__}({{{inner}}})"

    # Remaining `dict` methods (accessors, equality, operators, etc.) are inherited from `dict`.
