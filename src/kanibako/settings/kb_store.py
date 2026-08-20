"""Kanibako store shape: the value space a :class:`KeyStore` holds — shape only, no resolution."""

from __future__ import annotations

from typing import Final, NamedTuple, Union

from kanibako.settings.keystore import KeyStore

# Containment order, OUTERMOST first: ``system ⊃ agent ⊃ workset ⊃ box``. Single source for every
# directional derivation. ⚑ Four scopes, NOT the six cascade levels.
SCOPE_CONTAINMENT: tuple[str, ...] = ("system", "agent", "workset", "box")
BINDING_DERIVATIONS_NODE: Final[str] = "binding_derivations"  # ⚑ Reserved internal node, NOT a key


class Bind(NamedTuple):
    """A binding value: ``(host_src, box_dest[, opts])``; never a colon-joined ``host:box`` str."""
    host: str
    box: str
    opts: str | None = None  # ``None`` = the category's default mount options


# ⚑ TEMPORARY NAME — becomes ``Bind`` once the conflict is gone; do NOT rename early.
class BindEntry(NamedTuple):
    """A destination-keyed binding entry: ``(src[, opts])`` — the destination is the key."""
    src: str
    opts: str | None = None


# ⚑ A plain ``dict`` & deliberately NOT a member of :data:`StoreValue`: inside a
# :class:`KeyStore` it materialises as a nested NODE, so arms merge per-entry.
BindMap = dict[str, BindEntry]

#### The absence marker ####

# ⚑ DUNDER-NAMED so a stored key can never shadow either name at a :class:`KeyStore` attribute
# surface. NOT name-mangled, so importers spell both verbatim.
class __Missing__:
    """Sentinel TYPE for :data:`__MISSING__`; a distinct singleton type (not ``object``)."""
    _instance: "__Missing__ | None" = None

    def __new__(cls) -> "__Missing__":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "__MISSING__"

    # Defensive; __MISSING__ is never a real value. Test it via `is __MISSING__`.
    def __bool__(self) -> bool:
        return False


# ABSENT key vs present-``None`` at the storage surface. Module-OWNED, not module-private.
__MISSING__: __Missing__ = __Missing__()


# Value space a KeyStore leaf or node may hold (design §2). ⚑ ``__MISSING__`` is excluded
# deliberately: it marks ABSENCE and is never itself stored.
StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]
