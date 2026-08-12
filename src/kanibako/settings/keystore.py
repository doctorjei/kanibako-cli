"""KeyStore — the resolved-keyspace data structure (storage + types only).

This module defines the KeyStore class and its supporting types _only_: reserved key errors
(:class:`ReservedKeyError`) & the recursive attribute-dict container (:class:`KeyStore`) - NOT
resolution, merge, cascade, ``@``-ref / ``$VAR`` / ``~`` expansion, typed views, or consumers —
which live in later blocks. It imports nothing from the settings stack beyond its own
:mod:`keystore_strings`.

⚑ The absent-vs-present-None sentinel (``__MISSING__``) is NOT here: it belongs to kanibako's VALUE
space, so it lives in :mod:`kb_store` beside :data:`StoreValue`, which excludes it. This module is
the unit that can LEAVE the tree; keeping it value-space-agnostic is what makes that possible."""

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

#: The leaf value space of a :class:`KeyStore`. Kanibako's instantiation lives in :mod:`kb_store`;
#: this module stays value-space-agnostic so it can leave the tree intact.
V = TypeVar("V")


######## Errors / Exceptions ####################################

class ReservedKeyError(KeyError):
    """Raised when a :class:`KeyStore` write uses a RESERVED key name (bad-key, not bad-value)"""


######## Main KeyStore Class ###############################

class KeyStore(dict[str, "V | KeyStore[V]"], Generic[V]):
    """Recursive attribute-dict: ``dict[str, V | KeyStore[V]]`` w. attr access. Inherits `dict`."""

    #: The public, non-dunder method names of :class:`dict`, PLUS this class's OWN public members.
    #: All are forbidden as user keys.
    #:
    #: ⚑ THE EXPOSURE, stated the right way round: ``__getattr__`` fires on a lookup MISS ONLY, so
    #: a class member ALWAYS wins. A key spelled like a member does not break the member — it
    #: becomes SILENTLY UNREADABLE through attribute access (``store[name]`` still returns it,
    #: ``store.name`` hands back the member). That silence is the whole reason for this set.
    #:
    #: THE RULE every class member satisfies: it is NAME-MANGLED (``_KeyStore__*``), or a DUNDER,
    #: or LISTED HERE. Mangled and dunder names are out of reach of a declared key by construction
    #: — no key is spelled ``_KeyStore__*``, and a dunder key is refused at write time — so the
    #: internals need no entry. A PUBLIC member has neither protection and must name itself, which
    #: is why this set carries its own members alongside ``dict``'s. Pinned by
    #: ``test_every_non_dunder_class_member_is_mangled_or_reserved``.
    RESERVED_KEY_NAMES: frozenset[str] = frozenset({"get", "keys", "values",
        "items", "pop", "popitem", "setdefault", "update", "clear", "copy", "fromkeys",
        "RESERVED_KEY_NAMES", "insert_segments",})

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
            # Use dict.items(source) — NOT source.items() — so the REAL storage of whatever mapping
            # arrived is what gets read. ⚑ For a KeyStore source the two are identical: ``items`` is
            # a reserved key name, and even force-written past that check the METHOD still wins the
            # attribute lookup (__getattr__ fires on a MISS only). The unbound call earns its keep
            # against any OTHER dict subclass, whose items() may be overridden to answer something
            # that is not its contents.
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
