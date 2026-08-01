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
``settings-keyspace-1.8.0.md`` §0 (files store UNRESOLVED) + §2a (the
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
reset). Present-``None`` is a legal stored :data:`StoreValue`. The canonical
absent-vs-present-``None`` probe is the **BOUND** ``store.get(key, _MISSING)``
(design §3): it returns :data:`_MISSING` iff the key is absent, ``None`` iff
present-``None``, else the value. The bound form is safe because no user key can
ever be named ``get`` — ``get`` is a RESERVED name (collision safety below), so
``store.get`` is ALWAYS the inherited ``dict`` method, never a stored value. The
**unbound** ``dict.get(store, key, _MISSING)`` form remains equally valid and
is still used by the existing consumers (the block-2b merge, the typed views,
``config set``); it was the canonical form before block 1b forbade reserved
names, so no consumer needs retrofitting. ``_MISSING`` is **never stored** and is
**never** a member of the :data:`StoreValue` union. The merge LOGIC that consults
it lives in block 2b; here we only define the sentinel and the rule that it never
enters value space.

Collision safety (the "MonkeyDict" point, design §2) — RESERVED NAMES
---------------------------------------------------------------------
Earlier this type let *any* key name be stored and relied on attribute access
resolving to the STORED KEY over a same-named ``dict`` method. That left a latent
foot-gun: any code that called a *bound* dict-method-named attribute
(``x.get(...)``, ``x.items()``) on a collision-prone store crashed when a user
key happened to share that name (block 1's ``.get`` absent-probe and ``items``
repr crashes). Block 1b removes the collision at the SOURCE: **reserved key names
are rejected at write time** (:data:`_RESERVED_KEY_NAMES` + the dunder pattern),
so no user key can ever shadow a real attribute. Reserved =

* any **dunder-pattern** name (``name.startswith("__") and name.endswith("__")``)
  — these are the Python data-model attributes; AND
* the public ``dict`` method names :data:`_RESERVED_KEY_NAMES` — exactly
  ``dir(dict)``'s non-dunder names (verified equal: 0 unguarded, 0 extras). No
  spec key uses any of these (env vars are UPPER_SNAKE; category/scope names are
  fixed words like ``bindings``/``box``), so the reservation costs the keyspace
  nothing while making the bound ``store.get`` permanently safe.

A reserved key is rejected loudly at :meth:`KeyStore.__setitem__` (and therefore
at construction, ``[]``-set, and attribute-set, which all funnel through it) with
:class:`ReservedKeyError`. The match is **CASE-SENSITIVE** (the box is Linux;
env var names are case-sensitive there). *Windows future note:* a Windows HOST
folds env-name case, so if Windows-host env support is ever added the reservation
must widen — two options to pick from then: a case-insensitive reservation in
Windows mode, OR Windows-only key-mangling. Not implemented here (design §2).

With reserved names forbidden, a plain :meth:`__getattr__` (fires only on a
lookup MISS) suffices for attribute access — no key can shadow a real attribute,
so the custom ``__getattribute__`` interception of block 1 is no longer needed.
"""

from __future__ import annotations

from typing import Any, NamedTuple, Union


class ReservedKeyError(KeyError):
    """Raised when a :class:`KeyStore` write uses a RESERVED key name.

    A :class:`KeyError` subclass (this is a bad-KEY error, not a bad-value one).
    Reserved = any dunder-pattern name (``__x__``) or a public ``dict`` method
    name (:data:`_RESERVED_KEY_NAMES`). Forbidding these at write time is what
    makes the bound ``store.get(key, _MISSING)`` probe permanently collision-safe
    (design §2/§3; block 1b). The message names the offending key AND lists the
    reserved set so it is actionable.
    """


#: The public, non-dunder method names of :class:`dict`. A user key may NOT take
#: any of these (it would shadow the inherited method at the attribute surface).
#: This frozenset is exactly ``dir(dict)``'s non-dunder names — verified equal at
#: block 1b (0 unguarded, 0 extras); the dunder pattern is checked separately.
_RESERVED_KEY_NAMES: frozenset[str] = frozenset(
    {
        "get",
        "keys",
        "values",
        "items",
        "pop",
        "popitem",
        "setdefault",
        "update",
        "clear",
        "copy",
        "fromkeys",
    }
)


def _check_key_name(key: Any) -> str:
    """Validate a key for storage; return it unchanged or raise.

    Rejects:
    * a non-``str`` key — :class:`TypeError` (spec keys are always strings);
    * a **dunder-pattern** name (``__x__``) — :class:`ReservedKeyError`;
    * a public ``dict`` method name (:data:`_RESERVED_KEY_NAMES`) —
      :class:`ReservedKeyError`.

    The match is CASE-SENSITIVE (design §2 — the box is Linux). Invoked from
    :meth:`KeyStore.__setitem__`, so it covers construction, ``[]``-set, and
    attribute-set (all funnel through ``__setitem__``).
    """
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


#: The scope CONTAINMENT order (spec §0 "Directional view/set across CONTAINMENT
#: levels"): ``system ⊃ agent ⊃ workset ⊃ box``, OUTERMOST first. THE single
#: source for every directional derivation: the ``config set`` write-allow sets
#: (``config_keys._SCOPE_WRITE_ALLOWED``) and the RESOLVE-time drop of
#: containing-scope keys from a lower settings file (``settings_assemble``, spec
#: §0 "Directional enforcement at RESOLVE"). It lives HERE — in the settings-stack
#: leaf that imports nothing from the stack — so both consumers import it without
#: a cycle (``config_interface`` → ``config`` → … would cycle back). A scope
#: CONTAINS every scope to its RIGHT; the tail-slice from a scope onward is the
#: set it may write DOWN into.
SCOPE_CONTAINMENT: tuple[str, ...] = ("system", "agent", "workset", "box")


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
#: value at the storage surface. Canonical probe (block 1b) = the BOUND
#: ``store.get(key, _MISSING) is _MISSING`` == absent — safe because ``get`` is a
#: reserved key name (see the module docstring), so ``store.get`` is always the
#: dict method. The UNBOUND ``dict.get(store, key, _MISSING)`` form is equally
#: valid and still used by existing consumers. Never stored; never a member of
#: :data:`StoreValue`. Consumed by the merge logic in block 2b.
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

    **CLASS INVARIANT (do not break): :class:`KeyStore` defines ONLY dunder
    members** — no non-dunder method or attribute on the class. Because
    :meth:`__getattr__` fires only on a normal-lookup MISS, a non-dunder class
    attribute would resolve BEFORE a same-named stored key, re-introducing a
    collision the reserved set does not cover. Keep every helper MODULE-LEVEL
    (e.g. :func:`_wrap`, :func:`_check_key_name`), never ``self._helper``. Holding
    this keeps the reserved set == ``dict``'s public methods EXACTLY (a ``_``-
    prefixed user key like ``_check_key`` stays a valid, non-colliding key).
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
        # Reject reserved key names at the SOURCE (design §2 / block 1b) so no
        # user key can shadow a dict method. This funnels construction, ``[]``-set
        # and attribute-set (all reach here), making the bound ``store.get`` safe.
        super().__setitem__(_check_key_name(key), _wrap(value))

    # __getitem__ / __delitem__ / __contains__ / __iter__ / __len__ / get /
    # keys / items / values / ... are inherited from dict unchanged. No user key
    # can be named after any of them (reserved, ``__setitem__`` above), so the
    # bound ``store.get(...)`` / ``store.items()`` are ALWAYS the dict methods —
    # the block-1 attribute-surface asymmetry is gone.

    # --- attribute access: maps to keys (a stored key can never shadow a real
    #     attribute, since the dict method names are reserved) ---

    def __getattr__(self, name: str) -> StoreValue:
        # ``__getattr__`` fires ONLY on a normal-lookup MISS. Because reserved
        # names (every dict method) can never be stored keys, a stored key never
        # collides with a real attribute, so a plain miss-only ``__getattr__``
        # is sufficient (block 1b — the custom ``__getattribute__`` is retired):
        # ``store.foo`` for a stored ``foo`` misses the attribute table and lands
        # here; ``store.get`` resolves to the dict method before we are reached.
        if dict.__contains__(self, name):
            return dict.__getitem__(self, name)
        # Neither a stored key nor a real attribute. Raise AttributeError (not
        # KeyError) to honor the attribute protocol (hasattr / getattr-default).
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


def insert_dotted(store: "KeyStore", dotted: str, value: Any) -> None:
    """Install *value* at the dotted path *dotted* in *store*, VERBATIM.

    Walks/creates the intermediate :class:`KeyStore` nodes and sets the terminal
    leaf to *value* exactly as given — no bind parsing, no coercion, no
    emptiness interpretation. A non-``KeyStore`` value sitting at an
    intermediate segment is REPLACED by a fresh node (the caller is installing a
    deeper key, so the shallower leaf cannot survive as a leaf).

    THE single non-parsing dotted installer. Two callers need exactly this:
    the ``meta.derived.*`` materialisation
    (``kanibako.commands.start._install_derived_bindings``) and the ``pref.*``
    overlay builder (:func:`kanibako.settings.settings_prefs.pref_overlay`), whose
    contract is *"values are installed VERBATIM — including ``None``"* (spec
    §2h). It is DELIBERATELY distinct from
    ``kanibako.settings.settings_assemble._insert_dotted``, which does a DIFFERENT job:
    that one PARSES the terminal through ``_parse_node`` so a floor entry under
    a bind-shaped category becomes a :class:`Bind`. Two spellings of one walk
    would be a rule-0 trap; two walks with different jobs are not, provided the
    difference is stated — which is what this paragraph is for.

    Uses the UNBOUND ``dict.get`` (S3): a key legitimately named ``get`` must
    not shadow the protocol into a crash.
    """
    parts = dotted.split(".")
    node: KeyStore = store
    for seg in parts[:-1]:
        child = dict.get(node, seg, None)
        if not isinstance(child, KeyStore):
            child = KeyStore()
            node[seg] = child
        node = child
    node[parts[-1]] = value
