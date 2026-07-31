"""Typed access — the 3-tier read surface over the expanded snapshot.

Block 4 of the KeyStore implementation. The expanded snapshot (blocks 1-3) is a
recursive :class:`~kanibako.settings_store.KeyStore` whose leaves carry the loose
:data:`~kanibako.settings_store.StoreValue` union. This module adds the TYPED
read surface design §5 calls for, confining that loose union to genuinely mixed
nodes only. It is READ-ONLY: it wraps an EXISTING snapshot node and exposes a
typed lens over it — it never merges, expands, reconciles, writes, or mutates.

Three tiers (design §5)
-----------------------
1. **Typed VIEWS over finite subtrees** (system paths, ``meta``) — fixed names,
   exact static types (``Path`` / ``str`` / ``bool``). This module ships the
   MECHANISM (:func:`typed_field` + the :class:`FiniteView` base) and ONE small
   worked example (:class:`MetaView`); it does NOT port ``StandardPaths`` (that
   is block 7 consumer wiring).
2. **Typed CATEGORY accessors** — each §2a category has DYNAMIC keys but ONE
   known value type, so it is a typed read-only mapping:
   :func:`bind_category` → ``Mapping[str, Bind]`` for ``bindings.{ro,rw}`` /
   ``caches`` / ``seeded`` / ``common`` / ``synced``; :func:`env_view` →
   ``Mapping[str, scalar]`` for ``env``; :func:`masks_set` → ``set[box_dest]``
   for the resolved ``masks`` (design §6f).
3. **Raw attribute / ``[]`` access** returning the full union — already on
   :class:`KeyStore` (block 1), for genuinely mixed nodes only.

PER-SCOPE, not cross-scope (S21)
--------------------------------
Every accessor is a typed LENS over a category NODE WHEREVER it appears in the
scope-qualified snapshot (``store.box.bindings.rw`` → ``Mapping[str, Bind]``).
It is NOT a cross-scope aggregator — resolving the SAME ``box_dest`` declared at
different scopes is ``reconcile_categories`` (design §6g), a SEPARATE downstream
pass that is OUT of scope here.

The load-bearing ``Bind``-not-``Bind|None`` coupling (S22)
----------------------------------------------------------
A category accessor exposes ``Bind`` (NOT ``Bind | None``) ONLY because the
block-2b merge OMITS every present-``None`` bind / category / masks leaf before
any consumer sees the snapshot (design §3/§6e). This module RELIES on that and
does NOT re-admit ``None``: if it ever encounters a ``None`` (or otherwise
ill-typed) leaf under a category node, that is a BUILD-INVARIANT BREACH, so it
RAISES :class:`ViewError` rather than silently type-laundering it. Likewise a
resolved ``masks`` value is honestly the mask marker (present-``None`` unmasks
were dropped at build, §6f), so :func:`masks_set` is honestly a ``set``.

Collision safety (S3)
---------------------
A category key may legitimately be named ``get`` / ``items`` / ``keys``. Every
container operation over a node therefore goes through the UNBOUND ``dict``
methods (``dict.get(node, k)`` / ``dict.keys(node)`` / ``dict.__getitem__`` /
``dict.__contains__`` / ``dict.__len__``) — NEVER the bound ``node.get(...)`` a
user key would shadow into a crash (the standing block-1 foot-gun).

OUT of scope (hard boundaries)
------------------------------
NO ``reconcile_categories`` / cross-scope ``box_dest`` collision (design §6g);
NO merge / expansion / cycle detection (blocks 2b/3); NO ``config set`` (block
5); NO consumer swap / ``StandardPaths`` port (block 7). This module does NOT
modify ``settings_store`` / ``settings_merge`` / ``settings_expand`` / ``paths``
/ ``start`` — it builds ALONGSIDE them and only READS the snapshot.

Authority: ``~/vault/rw/keystore-design.md`` §5 (typed access — PRIMARY, incl.
the load-bearing ``Bind``-not-``Bind|None`` coupling), §6f (resolved ``masks`` =
``set``); spec ``settings-keyspace-1.6.0-target.md`` §2a (categories + value
types).
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Callable, Generic, TypeVar

from kanibako.settings_store import Bind, KeyStore

__all__ = [
    "ViewError",
    "bind_category",
    "bindings",
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
    "FiniteView",
    "MetaView",
    "MetaRuntimeView",
    "MetaBoxView",
    "MetaWorksetView",
    "MetaAgentView",
]

class ViewError(Exception):
    """A typed view found a value the snapshot's build invariants forbid.

    Raised when an accessor sees a leaf its tier's contract says cannot exist —
    a ``None`` (or non-:class:`Bind`) leaf under a bind category (the S22
    coupling breach), a non-scalar under ``env``, or a missing / wrong-typed
    field under a finite view. It signals a BUILD bug upstream (block 2b/3), NOT
    bad user input; the view refuses to type-launder it (design §5).
    """


# --------------------------------------------------------------------------- #
# Tier-2 — typed CATEGORY accessors                                           #
# --------------------------------------------------------------------------- #


class _BindCategoryView(Mapping[str, Bind]):
    """A read-only ``Mapping[str, Bind]`` lens over a bind-category NODE.

    Wraps an EXISTING category :class:`KeyStore` node (e.g.
    ``store.box.bindings.rw`` or ``store.box.caches``) WITHOUT copying it — the
    node stays the single source of truth (design §0). Every value is asserted
    to be a real :class:`Bind` on read (the S22 coupling: build dropped
    present-``None`` binds, so a ``None`` here is a build breach → :class:`ViewError`).

    Read-only: there is no ``__setitem__`` (``Mapping``, not ``MutableMapping``),
    so the lens cannot mutate the snapshot. All container ops use the UNBOUND
    ``dict`` methods (S3) so a key named ``get`` / ``items`` cannot shadow them.
    """

    __slots__ = ("_node", "_label")

    def __init__(self, node: KeyStore, *, label: str) -> None:
        self._node = node
        self._label = label

    def __getitem__(self, key: str) -> Bind:
        # Unbound dict probe (S3): a key named ``get`` must not crash the lookup.
        if not dict.__contains__(self._node, key):
            raise KeyError(key)
        value = dict.__getitem__(self._node, key)
        return self._checked(key, value)

    def __iter__(self) -> Iterator[str]:
        # dict.keys(node) — NOT node.keys() (S3).
        return iter(dict.keys(self._node))

    def __len__(self) -> int:
        return dict.__len__(self._node)

    def __contains__(self, key: object) -> bool:
        return dict.__contains__(self._node, key)

    def _checked(self, key: str, value: Any) -> Bind:
        # S22: the typed contract is ``Bind``, not ``Bind | None``. Build omitted
        # present-None binds, so anything else here is an upstream invariant
        # breach — RAISE, never silently type-launder a None into the mapping.
        if not isinstance(value, Bind):
            raise ViewError(
                f"{self._label}[{key!r}] is {type(value).__name__}, expected Bind "
                f"(present-None binds are omitted at build, design §3/§6e/S22)"
            )
        return value

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self._label!r}, {dict.__len__(self._node)} binds)"


class _EnvView(Mapping[str, "str | int | float | bool"]):
    """A read-only ``Mapping[str, scalar]`` lens over an ``env`` NODE.

    Wraps an EXISTING ``env`` :class:`KeyStore` node (``store.box.env``) without
    copying. ``env.<VAR>`` values are scalars (spec §2a); a non-scalar leaf is a
    build breach → :class:`ViewError`. Read-only; unbound ``dict`` ops (S3).
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
        # ``bool`` is a subclass of ``int`` — admitted (a scoped env flag). A
        # ``None`` is rejected: ``env.<VAR>`` is a scalar value (spec §2a) and an
        # env var has no "consumer default" to fall back to, so a None leaf is a
        # build breach (nothing to export), not a legitimate value.
        if not isinstance(value, (str, int, float, bool)):
            raise ViewError(
                f"env[{key!r}] is {type(value).__name__}, expected a scalar "
                f"(str/int/float/bool) per spec §2a"
            )
        return value

    def __repr__(self) -> str:
        return f"_EnvView({dict.__len__(self._node)} vars)"


def bind_category(node: KeyStore, *, label: str = "bindings") -> Mapping[str, Bind]:
    """A typed ``Mapping[str, Bind]`` lens over a bind-category NODE (tier-2, S21).

    *node* is a category :class:`KeyStore` subtree whose leaves are all
    :class:`Bind` — a ``caches`` / ``seeded`` / ``common`` / ``synced`` node, or
    one of the ``bindings.ro`` / ``bindings.rw`` sub-nodes (see :func:`bindings`
    for splitting a whole ``bindings`` node into its two modes). The returned
    mapping is READ-ONLY and does NOT copy the node (the snapshot stays the
    single source of truth). Every value is checked to be a real ``Bind`` on
    read (S22): a ``None`` / mistyped leaf RAISES :class:`ViewError` (a build
    breach), never type-launders.

    *label* names the node in error messages (e.g. ``"box.bindings.rw"``); it
    has no behavioral effect.

    This is a PER-SCOPE lens — it does NOT aggregate the same dest across scopes
    (that is reconcile, design §6g, out of scope).
    """
    _require_node(node, label)
    return _BindCategoryView(node, label=label)


def bindings(node: KeyStore, *, label: str = "bindings") -> tuple[
    Mapping[str, Bind], Mapping[str, Bind]
]:
    """Split a whole ``bindings`` NODE into its ``(ro, rw)`` typed lenses.

    *node* is a ``bindings`` :class:`KeyStore` subtree holding the ``ro`` and/or
    ``rw`` sub-nodes (spec §2a: ``bindings.ro`` / ``bindings.rw``). Returns a
    pair of read-only ``Mapping[str, Bind]`` lenses, one per mode. A mode ABSENT
    from the node yields an EMPTY mapping (the build omits an absent / fully
    reset category, §3/§6e), never an error. Per-scope (S21).
    """
    _require_node(node, label)
    return (
        bind_category(_sub_or_empty(node, "ro"), label=f"{label}.ro"),
        bind_category(_sub_or_empty(node, "rw"), label=f"{label}.rw"),
    )


def derived_bindings(
    node: KeyStore, *, label: str = "meta.derived",
) -> dict[str, Bind]:
    """FLATTEN the ``meta.derived`` subtree to ``{declaration-key: Bind}`` (§0).

    *node* is the ``meta.derived`` :class:`KeyStore` subtree — the MATERIALISED
    binding each ABSTRACT declaration (``common`` / ``caches`` / ``seeded``)
    derives, filed at ``meta.derived.<declaration-key>`` so a reader can see the
    declaration AND the binding it produces (spec §0, declared 2026-07-31).

    Unlike the tier-2 lenses this returns a fresh dict rather than a live view:
    the family is PARAMETRIC over the whole key space below it
    (``meta.derived.agent.claude.common.plugins``), so the useful shape is the
    FLAT declaration key, which no lens over one node can present. The Binds
    themselves are shared, not copied — they are immutable.

    An absent / empty node yields ``{}``. A non-``Bind`` leaf RAISES
    :class:`ViewError` (S22 — a build breach, never type-laundered).

    ⚑ This is the READ half only. The keys are PRODUCED by
    :func:`kanibako.settings_categories.derive_binding_keys`, which is
    deliberately named differently: two functions with one name in two modules is
    exactly the confusion the conventions open with.
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

    *node* is an ``env`` :class:`KeyStore` subtree (``store.box.env``) whose
    leaves are scoped env-var scalars (spec §2a). Read-only, no copy; a
    non-scalar leaf RAISES :class:`ViewError`. Per-scope.
    """
    _require_node(node, label)
    return _EnvView(node)


def masks_set(node: KeyStore, *, label: str = "masks") -> set[str]:
    """The RESOLVED ``masks`` as ``set[box_dest]`` — the masked dests (tier-2, §6f).

    *node* is a ``masks`` :class:`KeyStore` subtree (``store.box.masks``), a
    keyed ``dict[box_dest → bool|None]`` (S5). AFTER build, every present-``None``
    unmask has been DROPPED (design §6f), so every SURVIVING key is a mask marker
    — the honest resolved shape is a ``set`` of the masked dests, NOT a mapping.

    This returns exactly the set of KEYS present in the node. As an S22-style
    invariant check it asserts no surviving value is ``None`` (a ``None`` here
    would be a build breach — the unmask should have been dropped) and RAISES
    :class:`ViewError` if one is found. Reads via unbound ``dict`` ops (S3);
    does not copy or mutate the node. Per-scope (S21).
    """
    _require_node(node, label)
    masked: set[str] = set()
    for key in dict.keys(node):
        value = dict.__getitem__(node, key)
        if value is None:
            # Build (§6f) drops present-None unmasks before any consumer reads
            # the snapshot; a surviving None is an invariant breach, not a mask.
            raise ViewError(
                f"{label}[{key!r}] is None — a present-None unmask should have "
                f"been dropped at build (design §6f/S22); not a mask marker"
            )
        masked.add(key)
    return masked


# --------------------------------------------------------------------------- #
# Tier-1 — typed finite-view mechanism + one worked example                   #
# --------------------------------------------------------------------------- #

# Strict CHECKING coercers for tier-1 fields. A finite view promises EXACT types
# (design §5), so a field's coerce must REJECT a mistyped stored leaf, not launder
# it. A bare constructor (``str`` / ``bool`` / ``int``) is the foot-gun: ``str(123)
# -> "123"`` and ``bool("false") -> True`` would silently HIDE a build bug (e.g. a
# stored "false" surfacing as the bool True). These helpers isinstance-CHECK the
# stored value first and raise :class:`ValueError` on a mismatch (``typed_field``
# wraps that in :class:`ViewError`). ``as_path`` is the one legitimate CONVERSION:
# a system path is stored as a ``str`` (spec) and ``Path`` is not a stored type, so
# it checks ``str`` then wraps — never launders a non-str.


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

    The one legitimate CONVERSION (not a launder): a system path is stored as a
    ``str`` and ``Path`` is not a stored type, so this checks ``str`` then wraps.
    A non-str (int / bool / Bind) RAISES rather than being stringified.
    """
    if not isinstance(value, str):
        raise ValueError(f"expected a str path, got {type(value).__name__}")
    return Path(value)


def as_opt_path(value: Any) -> Path | None:
    """Checking coercer: a stored ``str`` path → :class:`Path`, OR ``None`` verbatim.

    The optional-path variant of :func:`as_path` for a finite-view field whose
    spec value is a path OR ``<None>`` (a whole-value ``@``-ref None terminal that
    survived as a real ``None`` leaf — e.g. ``meta.box.share_workset`` for
    STANDALONE, spec §2c L469).  A present ``None`` is honest and returned as-is; a
    non-str / non-None leaf is rejected (never laundered).
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"expected a str path or None, got {type(value).__name__}")
    return Path(value)


T = TypeVar("T")


class typed_field(Generic[T]):
    """A typed read-only field descriptor for a tier-1 finite VIEW (the mechanism).

    A finite view (system paths, ``meta``) has FIXED names of KNOWN type. This
    descriptor declares one such field: it reads the named key off the wrapped
    snapshot node (via the UNBOUND ``dict`` probe, S3), converts it with the
    field's *coerce* callable, and returns the EXACT static type ``T`` — so a
    consumer of ``view.field`` gets ``T``, not the loose ``StoreValue`` union
    (design §5 tier-1). Read-only: the descriptor has no ``__set__``.

    Use a CHECKING coercer (:func:`as_str` / :func:`as_bool` / :func:`as_int` /
    :func:`as_float` / :func:`as_path`), NOT a bare constructor: a bare ``str`` /
    ``bool`` would LAUNDER a mistyped leaf (``str(123)`` → ``"123"``,
    ``bool("false")`` → ``True``) and silently hide a build bug, defeating the §5
    EXACT-type promise. The checking coercers isinstance-verify the stored value
    and reject a mismatch (``as_path`` is the one legitimate conversion: a stored
    ``str`` → ``Path``).

    A missing field, or a value the coerce rejects, RAISES :class:`ViewError`
    (a finite view promises every named field is present and well-typed). Use
    *key* to point the field at a stored key whose name differs from the Python
    attribute (e.g. ``global`` keyword → ``global_dir``); it defaults to the
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
    get exact-typed, read-only access to a FIXED-name subtree. The view does NOT
    copy the node (snapshot stays the source of truth) and exposes no mutation
    path. This is the MECHANISM design §5 tier-1 calls for; ``StandardPaths`` is
    NOT ported here (block 7).
    """

    __slots__ = ("_node",)

    _node: KeyStore

    def __init__(self, node: KeyStore) -> None:
        _require_node(node, type(self).__name__)
        self._node = node


class MetaView(FiniteView):
    """A small WORKED EXAMPLE of a tier-1 finite view (design §5 tier-1).

    Wraps a ``meta`` NODE and exposes a few representative fields at their EXACT
    types — a ``Path`` (``root``) and a ``str`` (``name``). It demonstrates the
    :class:`typed_field` + :class:`FiniteView` mechanism; it is NOT the full
    ``meta`` schema and is NOT wired into any consumer (that is block 7). Add more
    fields by declaring more :class:`typed_field` descriptors.
    """

    name: str = typed_field(as_str)  # type: ignore[assignment]
    root: Path = typed_field(as_path)  # type: ignore[assignment]


class MetaRuntimeView(FiniteView):
    """Typed finite view over the ``meta.runtime`` NODE (block B1, spec §1A L230-241).

    Surfaces the runtime-resolved identity anchors at their EXACT types: the
    workset root (``ws_root`` — a resolved ``Path``) and the resolved mode token
    (``project_type`` — a ``str``, one of ``"primary"`` / ``"named"`` /
    ``"standalone"``). Read-only; wraps ``store.meta.runtime``. ADDITIVE — no
    consumer reads it yet (B1).

    ⚑ There is NO ``ws_settings`` field: ``meta.runtime.ws_settings`` is CUT from the
    keyspace (spec §1A L367-368). The workset-tier settings FILE is
    ``MetaWorksetView.settings``, which now spells itself directly off ``ws_root``.
    """

    ws_root: Path = typed_field(as_path)  # type: ignore[assignment]
    project_type: str = typed_field(as_str)  # type: ignore[assignment]


class MetaBoxView(FiniteView):
    """Typed finite view over the ``meta.box`` NODE (block B1 + B2).

    Exposes the RO identity anchors materialized for the box (spec §2c; §0 meta-RO):

    * ``mode`` (B1) — the resolved mode token surfaced from
      ``@meta.runtime.project_type`` (spec §2b L486 — was the settable ``box.mode``).
    * ``name`` (B2) — the box name (``proj.name``; the @meta.box.* binds key off it).
    * ``workspace`` (B2) — the resolved in-box workspace SOURCE
      (= ``str(proj.project_path)``); ``box.bindings.rw.workspace`` routes through
      ``@meta.box.workspace`` (spec §2c L476).
    * ``inbox`` (B2) — this box's own mailbox dir (spec §2c L467);
      ``box.bindings.rw.inbox`` routes through ``@meta.box.inbox`` (L475).
    * ``share_global`` (B2) — this box's system-scope share dir (spec §2c L468).
    * ``share_workset`` (B2) — this box's workset-local share dir, ``None`` for
      STANDALONE (spec §2c L469).
    * ``settings`` — the RO box-TIER settings-file path, UNIFORM in every mode (spec
      §2c ALL PROJECTS L817). Standalone's is ``<root>/box_data/settings.yaml``, a
      real path that is merely ABSENT BY DEFAULT (§5 L1407) — NOT a ``None`` terminal.
      (Typed ``Path | None`` because a narrow/partial resolve may materialize no box
      tier; the launch always supplies one.)

    Read-only; wraps ``store.meta.box``. (``container_name`` / ``helper_num`` are
    a non-bind RENDER, not materialized here — JC-B2-3.)
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

    Exposes the single-source-re-rooted ``path`` (= ``@meta.runtime.ws_root``, a
    resolved ``Path``) and ``settings`` (= ``@meta.runtime.ws_root/settings.yaml``, a
    ``Path`` for ALL modes incl. standalone, whose ROOT file plays the workset tier —
    spelled directly off ``ws_root`` now that the ``meta.runtime.ws_settings`` hop is
    CUT, spec §1A L367-368), plus the
    ``name`` partition token (``__PRIMARY__`` / ``<named>`` / ``__STANDALONE__``) —
    now the ``@meta.runtime.ws_name`` anchor (block B1, single source, spec §1A/§2c
    2026-07-04; was a direct B2 literal). Read-only; wraps ``store.meta.workset``.
    """

    path: Path = typed_field(as_path)  # type: ignore[assignment]
    settings: "Path | None" = typed_field(as_opt_path)  # type: ignore[assignment]
    name: str = typed_field(as_str)  # type: ignore[assignment]


class MetaAgentView(FiniteView):
    """Typed finite view over a ``meta.agent.<agent>`` NODE (block B2, spec §2d).

    Exposes the plugin-set ``name`` (spec §2d L514 — REQUIRED when an agent
    exists; identifies the store dir & cascade key) and the agent STORE ROOT
    ``path`` (§2d L515 = ``@config.agents/<agent>``), which is also §2a's agent
    DECLARATION ROOT: an abstract-category source stores
    ``@meta.agent.<agent>.path/<category>/<leaf>``, so the key resolves for real.
    Read-only; wraps ``store.meta.agent.<agent>``. (``settings`` — §2d L516 — is
    still unmaterialized; its consumers have not moved onto @meta.* yet.)
    """

    name: str = typed_field(as_str)  # type: ignore[assignment]
    path: str = typed_field(as_str)  # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _require_node(node: Any, label: str) -> None:
    """Guard: an accessor must wrap a real :class:`KeyStore` node, not a leaf.

    A category / finite-view accessor is meaningless over a scalar / ``Bind`` /
    ``None`` leaf. Passing one is a CALLER bug (wrong path into the snapshot), so
    it RAISES :class:`ViewError` rather than producing an empty or wrong lens.
    """
    if not isinstance(node, KeyStore):
        raise ViewError(
            f"{label}: expected a KeyStore category node, got "
            f"{type(node).__name__} (accessors wrap a NODE, not a leaf)"
        )


def _sub_or_empty(node: KeyStore, key: str) -> KeyStore:
    """The *key* sub-node of *node* as a :class:`KeyStore`, or an EMPTY one.

    Used to split ``bindings`` into ``ro`` / ``rw`` (a mode the build omitted is
    simply absent → an empty lens, §3/§6e — not an error). Unbound ``dict`` ops
    (S3). A present-but-non-KeyStore sub-value is a build breach → :class:`ViewError`.
    """
    if not dict.__contains__(node, key):
        return KeyStore()
    sub = dict.__getitem__(node, key)
    if not isinstance(sub, KeyStore):
        raise ViewError(
            f"bindings.{key} is {type(sub).__name__}, expected a KeyStore sub-node"
        )
    return sub
