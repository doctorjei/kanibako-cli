"""Kanibako Store Shape — values in the KeyStore

This module defines only the shape of kanibako value space: (:data:`StoreValue`), binding value
(:class:`Bind`) & the two structural constants naming it (:data:`SCOPE_CONTAINMENT`,
:data:`BINDING_DERIVATIONS_NODE`) - NOT resolution, merge, cascade, ``@``-ref / ``$VAR`` / ``~``
expansion, typed views, or consumers — which live in later blocks. It imports nothing from the
settings stack beyond the container itself, :mod:`keystore`."""

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

# Value space a KeyStore leaf or node may hold (design §2); ``_MISSING`` is deliberately excluded.
StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]
