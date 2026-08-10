"""KeyStore — the resolved-keyspace data structure (storage + types only).

This module defines _only_ raw storage shape of the keyspace: its value space (:data:`StoreValue`),
binding value (:class:`Bind`), module-private absent-vs-present-None sentinel (:data:`_MISSING`), &
recursive attribute-dict container (:class:`KeyStore`) - NOT resolution, merge, cascade, ``@``-ref
/ ``$VAR`` / ``~`` expansion, typed views, or consumers — which live in later blocks. It imports
nothing from the settings stack & is (for now) imported by nothing."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final, NamedTuple, Union


######## Public Types #######################################
#
# For bindings variants, ``opts`` is the optional per-entry mount-options override; ``None`` means
# fall back to the category's default options".

class Bind(NamedTuple):
    """A binding value: ``(host_src, box_dest[, opts])``; never a colon-joined ``host:box`` str."""
    host: str
    box: str
    opts: str | None = None


# ⚑ **This name is temporary**; eventually it will become ``Bind``, once the conflict is gone.
class BindEntry(NamedTuple):
    """A destination-keyed binding entry: ``(src[, opts])`` — the destination is the key."""
    src: str
    opts: str | None = None


# ⚑ A plain ``dict`` & deliberately NOT member of :data:`StoreValue`; inside :class:`KeyStore`,
#``BindMap`` materialises as a  nested ``KeyStore`` NODE.
BindMap = dict[str, BindEntry]


# Value space a KeyStore leaf or node may hold (design §2); ``_MISSING`` is deliberately excluded.
StoreValue = Union["KeyStore", Bind, BindEntry, str, int, float, bool, list[str], None]


class ReservedKeyError(KeyError):
    """Raised when a :class:`KeyStore` write uses a RESERVED key name (bad-key, not bad-value)"""


######## Public Values #####################################

#: The reserved internal derivations node at the snapshot root; not a key.
BINDING_DERIVATIONS_NODE: Final[str] = "binding_derivations"

# Scope containment order; ``system ⊃ agent ⊃ workset ⊃ box``, outermost first.
SCOPE_CONTAINMENT: tuple[str, ...] = ("system", "agent", "workset", "box")


######## Public Functions ##################################

def insert_segments(store: "KeyStore", segments: "Sequence[str]", value: Any,) -> None:
    """Install *value* at the path *segments* in *store*, VERBATIM; each segment is _one_ node."""

    # ⚑ Entry-point of box-dest, terminal path ``binding_derivations.<declaration-key>.<dest>``.
    # Routinely contain dots (``.``, ``~/.cache/uv``, etc.).
    parts = tuple(segments)
    if not parts:
        raise ValueError("insert_segments needs at least one segment")
    node: KeyStore = store
    for seg in parts[:-1]:
        child = dict.get(node, seg, None)
        if not isinstance(child, KeyStore):
            child = KeyStore()
            node[seg] = child
        node = child
    node[parts[-1]] = value


def insert_dotted(store: "KeyStore", dotted: str, value: Any) -> None:
    """Install *value* at dotted KEY *dotted* — :func:`insert_segments`, split on ``.``."""
    insert_segments(store, dotted.split("."), value)


######## Internal Types ####################################

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


######## Internal Values ###################################

#: The public, non-dunder method names of :class:`dict`. These are forbidden as user keys.
_RESERVED_KEY_NAMES: frozenset[str] = frozenset({"get", "keys", "values",
    "items", "pop", "popitem", "setdefault", "update", "clear", "copy", "fromkeys",})


# Module-private sentinel distinguishing ABSENT key from present-``None`` value at storage surface.
_MISSING: _Missing = _Missing()


######## Internal Functions ################################

def _check_key_name(key: Any) -> str:
    """Validate key; return unchanged if non-dunder str & NOT a method name, or raises error."""
    if not isinstance(key, str):
        raise TypeError(
            f"KeyStore keys must be str, got {type(key).__name__}: {key!r}"
        )
    if key.startswith("__") and key.endswith("__"):
        raise ReservedKeyError(
            f"key {key!r} is reserved: dunder-pattern names (__x__) are not "
            f"allowed (they are Python data-model attributes)"
        )
    if key in _RESERVED_KEY_NAMES:
        raise ReservedKeyError(
            f"key {key!r} is reserved: it shadows a dict method. Reserved "
            f"names: {sorted(_RESERVED_KEY_NAMES)}"
        )
    return key


def _wrap(value: Any) -> StoreValue:
    """Coerce raw value into :data:`StoreValue` space; a plain `dict` becomes :class:`KeyStore`."""
    if isinstance(value, KeyStore):
        return value
    if isinstance(value, dict):
        return KeyStore(value)
    return value


######## Main KeyStore Class ###############################

class KeyStore(dict):  # type: ignore[type-arg]
    """Recursive attribute-dict: ``dict[str, StoreValue]`` w. attr access. Inherits from `dict`."""

    # No ``__slots__`` or instance ``__dict__`` use for storage; state lives in underlying `dict`.
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__()
        # Funnel everything through dict.update -> __setitem__ to wrap nested dicts uniformly.
        if args:
            if len(args) > 1:
                raise TypeError(f"KeyStore expected at most 1 positional argument, got {len(args)}")

            source = args[0]
            # Use dict.items(source) — NOT source.items() — so source KeyStore with key ``items``
            # does not shadow a method (the very collision this module guards against).
            items = dict.items(source) if isinstance(source, dict) else source
            for key, value in items:
                self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    # --- item access: canonical surface; wraps nested dicts on write. ---

    # Reject reserved keys at SOURCE so user key cannot shadow dict method; funnels construction,
    # ``[]``-set, & attribute-set (all come here), so bound ``store.get`` is safe.
    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(_check_key_name(key), _wrap(value))

    # --- attribute access: maps keys; dict methods are reserved; key cannot shadow real attr. ---

    # Fires ONLY on lookup miss; as reserved names cannot be stored as keys, no method collision.
    def __getattr__(self, name: str) -> StoreValue:
        if dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        # Not stored key or attribute, so raise AttributeError (not KeyError).
        raise AttributeError(f"{type(self).__name__!r} object has no key {name!r}")

    # Dunder attributes are real (not used for storage, but we'll this helps avoid accidents).
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
                f"{type(self).__name__!r} object has no key {name!r}"
            ) from None

    # --- representation ---

    # Use dict.items(self) — NOT self.items() — so key named ``items`` cannot break repr.
    def __repr__(self) -> str:
        inner = ", ".join(f"{k!r}: {v!r}" for k, v in dict.items(self))
        return f"{type(self).__name__}({{{inner}}})"

    # Remaining `dict` methods (accessors, equality, operators, etc.) are inherited from `dict`.
