"""Kanibako Store Shape — values in the KeyStore

This module defines only the shape of kanibako value space: (:data:`StoreValue`), binding value
(:class:`Bind`), the two structural constants naming it (:data:`SCOPE_CONTAINMENT`,
:data:`BINDING_DERIVATIONS_NODE`) & the absent-vs-present-None sentinel (:data:`__MISSING__`, the
marker for what the value space does NOT hold) - NOT resolution, merge, cascade, ``@``-ref /
``$VAR`` / ``~`` expansion, typed views, or consumers — which live in later blocks. It imports
nothing from the settings stack beyond the container itself, :mod:`keystore`."""

from __future__ import annotations

from typing import Final, NamedTuple, Union

from kanibako.settings.keystore import KeyStore

# Scope order is ``system ⊃ agent ⊃ workset ⊃ box``, outermost first.
SCOPE_CONTAINMENT: tuple[str, ...] = ("system", "agent", "workset", "box")  # Containment order
BINDING_DERIVATIONS_NODE: Final[str] = "binding_derivations"  # Reserve internal derivation nodes

# For bindings variants, ``opts`` is the optional per-entry mount-options override; ``None`` means
# fall back to the category's default options.

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
# ``BindMap`` materialises as a nested ``KeyStore`` NODE.
BindMap = dict[str, BindEntry]

######## The absence marker ################################

# ⚑ DUNDER-NAMED so a stored key can never shadow either name at a :class:`KeyStore` attribute
# surface — a dunder is refused as a key at write time, so the collision cannot arise. NOT
# name-mangled (two trailing underscores), so importers spell both verbatim. ``_instance`` is
# deliberately left alone: ``__Missing__`` is not a ``KeyStore``, so it has no key/attribute
# collision to close.
class __Missing__:
    """Sentinel TYPE for :data:`__MISSING__`; a distinct singleton type (not ``object``)."""
    _instance: "__Missing__ | None" = None

    def __new__(cls) -> "__Missing__":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "__MISSING__"

    # Defensive; __MISSING__ must never be mistaken for a real value. Test it via `is __MISSING__`.
    def __bool__(self) -> bool:
        return False


# The sentinel distinguishing an ABSENT key from a present-``None`` value at the storage surface.
# Module-OWNED, not module-private: this module defines it, and consumers outside it
# (``agent_select``, ``settings_launch``, ``settings_merge``, ``config_interface``,
# ``config_display``, ``workset_cmd``, and the tests) import it deliberately — it is the canonical
# absent-vs-present-``None`` probe value. It lives HERE, with the value space it is defined against,
# rather than in :mod:`keystore`: the container is value-space-agnostic so it can leave the tree.
__MISSING__: __Missing__ = __Missing__()


# Value space a KeyStore leaf or node may hold (design §2); ``__MISSING__``, defined just above, is
# deliberately excluded — it marks the ABSENCE of a value and is never itself stored.
StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]
