"""Typed access — the 3-tier read surface over the expanded snapshot.

READ-ONLY: every accessor wraps an EXISTING snapshot node and exposes a typed
lens over it. It never merges, expands, reconciles, writes, or mutates, and it
does not copy — the snapshot stays the source of truth.

⚑ Collision safety (S3): every container operation goes through the UNBOUND
``dict`` methods (``dict.get(node, k)``, ``dict.keys(node)``, …), NEVER the
bound ``node.get(...)`` — a category key may legitimately be named ``get`` /
``items`` / ``keys`` and would shadow it into a crash.

⚑ A category accessor exposes ``Bind``, NOT ``Bind | None``, only because the
cascade merge omits every present-``None`` leaf before any consumer sees the
snapshot (design §3/§6e). A ``None`` or ill-typed leaf here is a BUILD-INVARIANT
BREACH and RAISES :class:`ViewError` — never type-launder it (S22).

The tiers, the per-scope boundary (S21) and what is deliberately OUT of scope
are in ``llm-docs/kanibako/settings/settings_views.py.md``.

Authority: ``~/vault/rw/keystore-design.md`` §5, §6f; spec
``settings-keyspace-1.8.0.md`` §2a.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from kanibako.settings.kb_store import Bind, BindEntry
from kanibako.settings.keystore import KeyStore

__all__ = [
    "ViewError",
    "bind_map",
    "bind_maps",
    "derived_bindings",
    "env_view",
    "masks_set",
    "typed_field",
    "as_str",
    "as_bool",
    "as_int",
    "as_float",
    "as_path",
    "as_opt_path",
    "as_argv_fragment",
    "as_mode_table",
    "FiniteView",
    "MetaView",
    "MetaRuntimeView",
    "MetaBoxView",
    "MetaWorksetView",
    "MetaAgentView",
]

class ViewError(Exception):
    """A typed view found a value the snapshot's build invariants forbid.

    It signals a BUILD bug upstream (block 2b/3), NOT bad user input.
    """


# --------------------------------------------------------------------------- #
# Tier-2 — typed CATEGORY accessors                                           #
# --------------------------------------------------------------------------- #


class _BindMapView(Mapping[str, BindEntry]):
    """A read-only ``Mapping[box_dest, BindEntry]`` lens over a DEST-KEYED arm.

    The ONLY bind lens (R-5/R-6): the mapping KEY is the destination and the
    value carries only ``(src, opts)``. Every value is asserted to be a real
    :class:`BindEntry` on read — build dropped present-``None`` entries, so a
    ``None`` here is a build breach → :class:`ViewError` (S22).

    ⚑ The check is ``isinstance(value, BindEntry)``, which is FALSE for a legacy
    3-tuple :class:`Bind` even though both are tuples — a stale name-keyed arm
    handed to this lens is REFUSED at read rather than mis-read.
    """

    __slots__ = ("_node", "_label")

    def __init__(self, node: KeyStore, *, label: str) -> None:
        self._node = node
        self._label = label

    def __getitem__(self, key: str) -> BindEntry:
        if not dict.__contains__(self._node, key):
            raise KeyError(key)
        value = dict.__getitem__(self._node, key)
        return self._checked(key, value)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.keys(self._node))

    def __len__(self) -> int:
        return dict.__len__(self._node)

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self._node, key)

    def _checked(self, key: str, value: Any) -> BindEntry:
        if not isinstance(value, BindEntry):
            raise ViewError(
                f"{self._label}[{key!r}] is {type(value).__name__}, expected "
                f"BindEntry (present-None entries are omitted at build, design "
                f"§3/§6e/S22; a legacy 3-tuple Bind is NOT a BindEntry)"
            )
        return value

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}({self._label!r}, "
            f"{dict.__len__(self._node)} entries)"
        )


class _EnvView(Mapping[str, "str | int | float | bool"]):
    """A read-only ``Mapping[str, scalar]`` lens over an ``env`` NODE.

    ``env.<VAR>`` values are scalars (spec §2a); a non-scalar leaf is a build
    breach → :class:`ViewError`.
    """

    __slots__ = ("_node",)

    def __init__(self, node: KeyStore) -> None:
        self._node = node

    def __getitem__(self, key: str) -> str | int | float | bool:
        if not dict.__contains__(self._node, key):
            raise KeyError(key)
        value = dict.__getitem__(self._node, key)
        return self._checked(key, value)

    def __iter__(self) -> Iterator[str]:
        return iter(dict.keys(self._node))

    def __len__(self) -> int:
        return dict.__len__(self._node)

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self._node, key)

    def _checked(self, key: str, value: Any) -> str | int | float | bool:
        # ``bool`` is admitted (a scoped env flag; bool ⊂ int). ``None`` is
        # rejected: an env var has no consumer default, so nothing to export.
        if not isinstance(value, (str, int, float, bool)):
            raise ViewError(
                f"env[{key!r}] is {type(value).__name__}, expected a scalar "
                f"(str/int/float/bool) per spec §2a"
            )
        return value

    def __repr__(self) -> str:
        return f"_EnvView({dict.__len__(self._node)} vars)"


def bind_map(node: KeyStore, *, label: str = "bindings") -> Mapping[str, BindEntry]:
    """A typed ``Mapping[box_dest, BindEntry]`` lens over a DEST-KEYED arm (tier-2).

    *node* is the :class:`KeyStore` a terminal bind-shaped key holds —
    ``<scope>.bindings.{ro,rw}`` or ``<scope>.{caches,seeded,common,synced}``,
    i.e. ``{box_dest: BindEntry(src, opts)}``. The returned mapping is READ-ONLY
    and does NOT copy the node. A ``None`` / mistyped leaf — including a legacy
    3-tuple :class:`Bind` — RAISES :class:`ViewError` (S22), never type-launders.

    *label* names the node in error messages; no behavioral effect.
    """
    _require_node(node, label)
    return _BindMapView(node, label=label)


def bind_maps(node: KeyStore, *, label: str = "bindings") -> tuple[
    Mapping[str, BindEntry], Mapping[str, BindEntry]
]:
    """Split a whole DEST-KEYED ``bindings`` NODE into its ``(ro, rw)`` lenses.

    A mode ABSENT from the node yields an EMPTY mapping (§3/§6e), never an error.
    """
    _require_node(node, label)
    return (
        bind_map(_sub_or_empty(node, "ro"), label=f"{label}.ro"),
        bind_map(_sub_or_empty(node, "rw"), label=f"{label}.rw"),
    )


def derived_bindings(
    node: KeyStore, *, label: str = "binding_derivations",
) -> dict[str, Bind]:
    """FLATTEN the ``binding_derivations`` subtree to ``{declaration-key: Bind}``.

    *node* is the reserved INTERNAL node at the snapshot root (R-8, not a key)
    carrying the MATERIALISED binding each ABSTRACT declaration (``common`` /
    ``caches`` / ``seeded``) derives, so a reader can see the declaration AND the
    binding it produces (spec §0).

    Returns a fresh dict, not a live view: the node is PARAMETRIC over the whole
    key space below it, so the useful shape is the FLAT declaration key. An
    absent / empty node yields ``{}``; a non-``Bind`` leaf RAISES
    :class:`ViewError` (S22).

    ⚑ The READ half only. The keys are PRODUCED by
    :func:`kanibako.settings.settings_categories.derive_binding_keys`,
    deliberately named differently.
    """
    _require_node(node, label)
    out: dict[str, Bind] = {}

    def _walk(sub: KeyStore, prefix: str) -> None:
        for key in dict.keys(sub):
            value = dict.__getitem__(sub, key)
            dotted = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, Bind):
                out[dotted] = value
            elif isinstance(value, KeyStore):
                _walk(value, dotted)
            else:
                raise ViewError(
                    f"{label}.{dotted} is {type(value).__name__}, expected a "
                    f"Bind (the materialised derivation of a declaration)"
                )

    _walk(node, "")
    return out


def env_view(node: KeyStore, *, label: str = "env") -> Mapping[str, "str | int | float | bool"]:
    """A typed ``Mapping[str, scalar]`` lens over an ``env`` NODE (tier-2, S21).

    *node* is an ``env`` subtree (``store.box.env``) whose leaves are scoped
    env-var scalars. Read-only, no copy; a non-scalar leaf RAISES.
    """
    _require_node(node, label)
    return _EnvView(node)


def masks_set(node: KeyStore, *, label: str = "masks") -> set[str]:
    """The RESOLVED ``masks`` as ``set[box_dest]`` — the masked dests (tier-2, §6f).

    *node* is a ``masks`` subtree, a keyed ``dict[box_dest → bool|None]`` (S5).
    Build DROPS every present-``None`` unmask (design §6f), so every surviving
    key is a mask marker and the honest shape is a ``set``, not a mapping.

    Returns exactly the set of KEYS present. As an S22-style invariant check it
    asserts no surviving value is ``None`` and RAISES if one is found.
    """
    _require_node(node, label)
    masked: set[str] = set()
    for key in dict.keys(node):
        value = dict.__getitem__(node, key)
        if value is None:
            raise ViewError(
                f"{label}[{key!r}] is None — a present-None unmask should have "
                f"been dropped at build (design §6f/S22); not a mask marker"
            )
        masked.add(key)
    return masked


# --------------------------------------------------------------------------- #
# Tier-1 — typed finite-view mechanism + one worked example                   #
# --------------------------------------------------------------------------- #

# Strict CHECKING coercers for tier-1 fields. ⚑ A bare constructor is the
# foot-gun (``str(123) -> "123"``, ``bool("false") -> True``): it would LAUNDER a
# mistyped leaf and silently hide a build bug, defeating the EXACT-type promise
# design §5 makes. These isinstance-CHECK the stored value first and raise
# :class:`ValueError`, which ``typed_field`` wraps in :class:`ViewError`.


def as_str(value: Any) -> str:
    """Checking coercer: the stored value MUST already be a ``str`` (no launder)."""
    if not isinstance(value, str):
        raise ValueError(f"expected str, got {type(value).__name__}")
    return value


def as_bool(value: Any) -> bool:
    """Checking coercer: MUST already be a ``bool`` — ``"false"``/``0`` do NOT pass."""
    if not isinstance(value, bool):  # bool is checked BEFORE int (bool ⊂ int).
        raise ValueError(f"expected bool, got {type(value).__name__}")
    return value


def as_int(value: Any) -> int:
    """Checking coercer: MUST already be an ``int`` (and not a ``bool``)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"expected int, got {type(value).__name__}")
    return value


def as_float(value: Any) -> float:
    """Checking coercer: MUST already be a ``float`` (or an ``int``, widened)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"expected float, got {type(value).__name__}")
    return float(value)


def as_path(value: Any) -> Path:
    """Checking coercer: a stored ``str`` path → :class:`Path`. Rejects non-str.

    The one legitimate CONVERSION, not a launder: a path is stored as a ``str``
    and ``Path`` is not a stored type.
    """
    if not isinstance(value, str):
        raise ValueError(f"expected a str path, got {type(value).__name__}")
    return Path(value)


def as_opt_path(value: Any) -> Path | None:
    """Checking coercer: a stored ``str`` path → :class:`Path`, OR ``None`` verbatim.

    For a field whose spec value is a path OR ``<None>`` (e.g.
    ``meta.box.share_workset`` for STANDALONE): a present ``None`` is honest and
    returned as-is; a non-str / non-None leaf is rejected.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a str path or None, got {type(value).__name__}")
    return Path(value)


def as_argv_fragment(value: Any) -> list[str]:
    """Checking coercer: a stored argv fragment (``list``/``tuple`` of ``str``) →
    ``list[str]``.

    A tuple (the descriptor's in-memory form) is normalized to a list.
    """
    if not isinstance(value, (list, tuple)) or not all(
        isinstance(part, str) for part in value
    ):
        raise ValueError(
            f"expected an argv fragment (list of str), got {type(value).__name__}"
        )
    return list(value)


def as_mode_table(value: Any) -> dict[str, list[str]]:
    """Checking coercer: the ``meta.agent.<a>.mode`` NODE → ``dict[str, list[str]]``.

    The launch-grammar table is materialized as a KeyStore sub-node (spec §2d
    ``dict[mode_key → argv fragment]``); each mode's value must itself be an
    argv fragment.
    """
    if not isinstance(value, KeyStore):
        raise ValueError(
            f"expected the mode table node, got {type(value).__name__}"
        )
    table: dict[str, list[str]] = {}
    for key in dict.keys(value):
        table[str(key)] = as_argv_fragment(dict.__getitem__(value, key))
    return table


T = TypeVar("T")


class typed_field(Generic[T]):
    """A typed read-only field descriptor for a tier-1 finite VIEW (the mechanism).

    Reads the named key off the wrapped node, converts it with the field's
    *coerce* callable, and returns the EXACT static type ``T`` rather than the
    loose ``StoreValue`` union (design §5 tier-1). No ``__set__``.

    ⚑ Hand it a CHECKING coercer, never a bare constructor (see above). A
    missing field, or a value the coerce rejects, RAISES :class:`ViewError`: a
    finite view promises every named field is present and well-typed.

    Use *key* to point the field at a stored key whose name differs from the
    Python attribute (``global`` keyword → ``global_dir``); it defaults to the
    attribute name.
    """

    __slots__ = ("_coerce", "_key", "_name")

    def __init__(self, coerce: Callable[[Any], T], *, key: str | None = None) -> None:
        self._coerce = coerce
        self._key = key
        self._name = key or ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        if self._key is None:
            self._key = name

    def __get__(self, obj: FiniteView | None, owner: type | None = None) -> T:
        if obj is None:  # pragma: no cover - class-level access
            return self  # type: ignore[return-value]
        node = obj._node
        key = self._key or self._name
        if not dict.__contains__(node, key):
            raise ViewError(
                f"{type(obj).__name__}.{self._name}: key {key!r} absent from the "
                f"snapshot node (a finite view requires every field present)"
            )
        raw = dict.__getitem__(node, key)
        try:
            return self._coerce(raw)
        except (TypeError, ValueError) as exc:
            raise ViewError(
                f"{type(obj).__name__}.{self._name}: cannot coerce {raw!r} via "
                f"{getattr(self._coerce, '__name__', self._coerce)}: {exc}"
            ) from exc


class FiniteView:
    """Base for a tier-1 typed finite view — wraps a finite snapshot NODE.

    Subclasses declare :class:`typed_field` attributes (one per known key) and
    get exact-typed, read-only access to a FIXED-name subtree. ``StandardPaths``
    is NOT ported here (block 7).
    """

    __slots__ = ("_node",)

    _node: KeyStore

    def __init__(self, node: KeyStore) -> None:
        _require_node(node, type(self).__name__)
        self._node = node


class MetaView(FiniteView):
    """A small WORKED EXAMPLE of a tier-1 finite view (design §5 tier-1).

    ⚑ NOT the full ``meta`` schema, and NOT wired into any consumer (block 7).
    """

    name: str = typed_field(as_str)  # type: ignore[assignment]
    root: Path = typed_field(as_path)  # type: ignore[assignment]


class MetaRuntimeView(FiniteView):
    """Typed finite view over the ``meta.runtime`` NODE (block B1, spec §1A).

    The runtime-resolved identity anchors: the workset root ``ws_root`` and the
    resolved mode token ``project_type`` (``"primary"`` / ``"named"`` /
    ``"standalone"``). ADDITIVE — no consumer reads it yet (B1).

    ⚑ There is NO ``ws_settings`` field: ``meta.runtime.ws_settings`` is CUT from
    the keyspace (spec §1A). The workset-tier settings FILE is
    ``MetaWorksetView.settings``, spelled directly off ``ws_root``.
    """

    ws_root: Path = typed_field(as_path)  # type: ignore[assignment]
    project_type: str = typed_field(as_str)  # type: ignore[assignment]


class MetaBoxView(FiniteView):
    """Typed finite view over the ``meta.box`` NODE (block B1 + B2).

    The RO identity anchors materialized for the box (spec §2c; §0 meta-RO).
    ``mode`` is surfaced from ``@meta.runtime.project_type`` (spec §2b — it was
    the settable ``box.mode``); ``workspace`` / ``inbox`` are the dests that
    ``box.bindings.rw`` routes through. ``container_name`` / ``helper_num`` are
    a non-bind RENDER and are NOT materialized here (JC-B2-3).

    ⚑ ``settings`` is UNIFORM in every mode; standalone's
    ``<root>/box_data/box.yaml`` is a real path merely ABSENT BY DEFAULT
    (§5), NOT a ``None`` terminal. It is typed ``Path | None`` only because a
    narrow/partial resolve may materialize no box tier.
    """

    mode: str = typed_field(as_str)  # type: ignore[assignment]
    name: str = typed_field(as_str)  # type: ignore[assignment]
    workspace: Path = typed_field(as_path)  # type: ignore[assignment]
    inbox: Path = typed_field(as_path)  # type: ignore[assignment]
    share_global: Path = typed_field(as_path)  # type: ignore[assignment]
    share_workset: "Path | None" = typed_field(as_opt_path)  # type: ignore[assignment]
    settings: "Path | None" = typed_field(as_opt_path)  # type: ignore[assignment]


class MetaWorksetView(FiniteView):
    """Typed finite view over the ``meta.workset`` NODE (block B1 + B2, spec §1A/§2c).

    ``name`` is the partition token (``__PRIMARY__`` / ``<named>`` /
    ``__STANDALONE__``); ``path`` and ``settings`` both re-root off the single
    source ``@meta.runtime.ws_root``.
    """

    path: Path = typed_field(as_path)  # type: ignore[assignment]
    settings: "Path | None" = typed_field(as_opt_path)  # type: ignore[assignment]
    name: str = typed_field(as_str)  # type: ignore[assignment]


class MetaAgentView(FiniteView):
    """Typed finite view over a ``meta.agent.<agent>`` NODE (block B2 + B5, spec §2d).

    ``path`` is the agent STORE ROOT (``@config.agents/<agent>``) and doubles as
    §2a's agent DECLARATION ROOT, so an abstract-category source storing
    ``@meta.agent.<agent>.path/<category>/<leaf>`` resolves for real. The
    B5-materialized trio (§3.3 rulings) is ``settings`` (the agent-tier cascade
    FILE), ``mode`` (the harness's INTERACTIVE launch grammar) and ``exec`` (the
    STANDALONE one-shot fragment).

    ⚑ ``exec`` is ABSENT for an agent with no ``exec`` operation — access it
    only where it is materialized.
    """

    name: str = typed_field(as_str)  # type: ignore[assignment]
    path: str = typed_field(as_str)  # type: ignore[assignment]
    settings: Path = typed_field(as_path)  # type: ignore[assignment]
    mode: "dict[str, list[str]]" = typed_field(as_mode_table)  # type: ignore[assignment]
    exec: "list[str]" = typed_field(as_argv_fragment)  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _require_node(node: Any, label: str) -> None:
    """Guard: an accessor must wrap a real :class:`KeyStore` node, not a leaf.

    An accessor is meaningless over a scalar / ``Bind`` / ``None`` leaf. Passing
    one is a CALLER bug (a wrong path into the snapshot), so it RAISES rather
    than producing an empty or wrong lens.
    """
    if not isinstance(node, KeyStore):
        raise ViewError(
            f"{label}: expected a KeyStore category node, got "
            f"{type(node).__name__} (accessors wrap a NODE, not a leaf)"
        )


def _sub_or_empty(node: KeyStore, key: str) -> KeyStore:
    """The *key* sub-node of *node* as a :class:`KeyStore`, or an EMPTY one.

    Splits ``bindings`` into ``ro`` / ``rw``: a mode the build omitted is simply
    absent → an empty lens (§3/§6e), not an error. A present-but-non-KeyStore
    sub-value is a build breach → :class:`ViewError`.
    """
    if not dict.__contains__(node, key):
        return KeyStore()
    sub = dict.__getitem__(node, key)
    if not isinstance(sub, KeyStore):
        raise ViewError(
            f"bindings.{key} is {type(sub).__name__}, expected a KeyStore sub-node"
        )
    return sub
