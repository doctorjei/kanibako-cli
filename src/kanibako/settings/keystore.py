"""KeyStore — the resolved-keyspace data structure (storage + types only).

This module defines the KeyStore class and its supporting types _only_: reserved key errors
(:class:`ReservedKeyError`), module-private absent-vs-present-None sentinel (:data:`_MISSING`), &
recursive attribute-dict container (:class:`KeyStore`) - NOT resolution, merge, cascade, ``@``-ref
/ ``$VAR`` / ``~`` expansion, typed views, or consumers — which live in later blocks. It imports
nothing from the settings stack beyond its own :mod:`keystore_strings`."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from .keystore_strings import (
    ERR_ATTRIBUTE_NO_KEY,
    ERR_RESERVEDKEY_DUNDER,
    ERR_RESERVEDKEY_METHOD,
    ERR_TYPE_KEYSTORE_ARGS,
    ERR_TYPE_NONSTRING_KEY,
)

#: The leaf value space of a :class:`KeyStore`. Kanibako's instantiation lives in :mod:`kb_store`;
#: this module stays value-space-agnostic so it can leave the tree intact.
V = TypeVar("V")


######## Errors / Exceptions ####################################

class ReservedKeyError(KeyError):
    """Raised when a :class:`KeyStore` write uses a RESERVED key name (bad-key, not bad-value)"""


######## Internal Types & Values ###########################

class _Missing:
    """Module-private :data:`_MISSING` sentinel type; distinct singleton type (not ``object``)."""
    _instance: "_Missing | None" = None

    def __new__(cls) -> "_Missing":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "_MISSING"

    # Defensive; _MISSING must never be mistaken for real value. Test presence via `is _MISSING`.
    def __bool__(self) -> bool:
        return False


# Module-private sentinel distinguishing ABSENT key from present-``None`` value at storage surface.
_MISSING: _Missing = _Missing()


######## Main KeyStore Class ###############################

class KeyStore(dict[str, "V | KeyStore[V]"], Generic[V]):
    """Recursive attribute-dict: ``dict[str, V | KeyStore[V]]`` w. attr access. Inherits `dict`."""

    #: The public, non-dunder method names of :class:`dict`. These are forbidden as user keys.
    RESERVED_KEY_NAMES: frozenset[str] = frozenset({"get", "keys", "values",
        "items", "pop", "popitem", "setdefault", "update", "clear", "copy", "fromkeys",})

    # No ``__slots__`` or instance ``__dict__`` use for storage; state lives in underlying `dict`.
    # ⚑ ``self`` is pinned to ``KeyStore[Any]``: an argument-free ``KeyStore()`` has nothing to
    # solve ``V`` from, and an unsolved ``V`` makes mypy demand an annotation at every bare
    # construction. Explicit ``KeyStore[StoreValue]`` annotations still bind normally.
    def __init__(self: KeyStore[Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # Funnel everything through dict.update -> __setitem__ to wrap nested dicts uniformly.
        if args:
            if len(args) > 1:
                raise TypeError(ERR_TYPE_KEYSTORE_ARGS % len(args))
            source = args[0]
            # Use dict.items(source) — NOT source.items() — so source KeyStore with key ``items``
            # does not shadow a method (the very collision this module guards against).
            items = dict.items(source) if isinstance(source, dict) else source
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    # ⚑ A non-dunder class member (this, RESERVED_KEY_NAMES) shadows a same-named KEY at the attr
    # surface — __getattr__ fires on MISS only. Neither is a declared key; the member set is pinned.
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
        """Validate key; return unchanged if non-dunder str & NOT a method name, or raises error."""
        if not isinstance(key, str):
            raise TypeError(ERR_TYPE_NONSTRING_KEY % (type(key).__name__, repr(key)))

        if key.startswith("__") and key.endswith("__"):
            raise ReservedKeyError(ERR_RESERVEDKEY_DUNDER % repr(key))
        if key in KeyStore.RESERVED_KEY_NAMES:
            raise ReservedKeyError(
                ERR_RESERVEDKEY_METHOD % (repr(key), sorted(KeyStore.RESERVED_KEY_NAMES))
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

    # Reject reserved keys at SOURCE so user key cannot shadow dict method; funnels construction,
    # ``[]``-set, & attribute-set (all come here), so bound ``store.get`` is safe.
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

    # Use dict.items(self) — NOT self.items() — so key named ``items`` cannot break repr.
    def __repr__(self) -> str:
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in dict.items(self))
        return f"{type(self).__name__}({{{inner}}})"

    # Remaining `dict` methods (accessors, equality, operators, etc.) are inherited from `dict`.
