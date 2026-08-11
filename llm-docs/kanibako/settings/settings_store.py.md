# KeyStore
_resolved-keyspace data structure (storage + types only)_

This module defines _only_ the raw storage shape of kanibako's settings keyspace - its value space
(:data:`StoreValue`), binding value (:class:`Bind`), module-private absent-vs-present-None sentinel
(:data:`_MISSING`), & recursive attribute-dict container (:class:`KeyStore`) - NOT resolution,
merge, cascade, ``@``-ref / ``$VAR`` / ``~`` expansion, typed views, or consumers — which live in
later blocks. It imports nothing from settings stack & is (for now) imported by nothing.

Authority: **spec `settings-keyspace-1.8.0.md`** — §0 (files store UNRESOLVED), §2 (the cascade), §2a
(category list + value types). ⚑ **The spec is the LIVE authority; read it first.**

Historical: `keystore-design.md` §2 (storage model), §3 (`None` semantics — type-space consequence
only), §6f (`masks` is a keyed `dict[box_dest -> bool|None]`, NOT a list). ⚑ **It is ARCHIVED, at
`~/canon/notebook/archives/keystore-2026-06/keystore-design.md`** — the path this file used to give
(`~/vault/rw/keystore-design.md`) **does not exist and has not for some time.** 🛑 **Its §4 writes the
cascade bracket with a 7th `required` tier that S14 and spec §2 CUT** — so on the cascade the archive
is WRONG and the spec wins. Cite it for the storage model only, never for tier structure.

## Summary of Storage Model (Design §2)

* `KeyStore = dict[str, StoreValue]`: `dict` subclass with added attribute access
* `StoreValue = KeyStore | Bind | BindEntry | str | int | float | bool | list[str] | None`
  - StoreValue includes `list[str]` for genuinely list-valued scalar keys
  - `masks` is nested `KeyStore` of `bool | None` leaves, *NOT* bare list
  - `Bind` is typed `NamedTuple(host, box, opts=None)`: `(host_src, box_dest[, opts])`. Bind value
    NEVER colon-joined string.
  * `BindEntry` is typed `NamedTuple(src, opts=None)` of DEST-KEYED rework (R-6): destination moves
    out of value & becomes key of `BindMap`. ⚑ Both bind types are live during P5→P8 bridge & their
    2-element forms are OPPOSITES — see `BindEntry` for two rules (discriminate by TYPE in value
    space, by NODE in raw space) that make it safe. `BindEntry` to be RENAMED to `Bind` in P8.
* Access is attribute-style with `[]` fallback for non-identifier / dynamic keys (`agent.<name>`,
  `env.<VAR>`, hyphens, dots, Python keywords). Both surfaces return SAME `StoreValue` union
* SAME `KeyStore` serves per-level partials, resolved snapshot; block builds only raw union surface

### `None` Semantics (Type Space, Design §3)

Key may be **absent** (unset) or present as **None** (explicit reset). Present-`None` is legal
stored `StoreValue`. Absent-vs-present-`None` probe is **BOUND** `store.get(key, _MISSING)` (design
§3): it returns `_MISSING` iff key is absent, `None` iff present-`None`, else value. Bound form is
safe; keys can't named be `get` — `get` is RESERVED name (see collision safety). `store.get` is
ALWAYS inherited `dict` method, not stored value. **Unbound** `dict.get(store, key, _MISSING)` form
remains equally valid & still used by existing consumers (2b merge, typed views, `config set`); was
canonical pre-prohibition of reserved names; no retrofitting needed. `_MISSING` is **never stored**
& **never** member of `StoreValue` union. It is consulted by Merge LOGIC in 2b; here, only its
sentinel is defined to prevent _MISSING from entering value space.

### Collision Safety (Reserved Names, Design §2)

Prevously, `KeyStore` stored *any* key & used attribute access resolution to STORED key over named
`dict` method; call of *bound* dict-method-named attribute (`x.get(...)`, `x.items()`) on collision
prone store crashed when user key shared name (§1's `.get` absent-probe & `items` repr crashes). 1b
removes collision at SOURCE: **reserved keys rejected at write time** (`_RESERVED_KEY_NAMES`).
+ dunder pattern), so user keys can't shadow real attributes. Reserved =
* **dunder-pattern** (`name.startswith("__") and name.endswith("__")`, Python data-model attr.; AND
* `dict` methods `_RESERVED_KEY_NAMES`: `dir(dict)` non-dunder names (equal: 0 unguarded, 0 extras).
  Spec keys du not use these (env vars are UPPER_SNAKE; category/scope names are fixed words, e.g.,
  `bindings`/`box`); reservation costs nothing, but makes bound `store.get` safe.

Reserved key is rejected loudly at `KeyStore.__setitem__` (& thus construction, attribute-set, &
`[]`-set, which all funnel through it) with `ReservedKeyError`, a **CASE-SENSITIVE** match (box is
Linux -> case-sensitive env variables). *Windows future note:* Windows HOST folds env-name case; if
Windows env support is added, widen reservation. Two options: case-insensitive reservation in
Windows mode, OR Windows-only key-mangling; not implemented (design §2).

With reserved names forbidden, plain `__getattr__` (fires on lookup MISS) suffices for attribute
access. No key shadows real attribute; custom `__getattribute__` interception unneeded.


## Supporting Types, Values, & Functions

### Public Types

`class Bind(NamedTuple)`
_Binding value: `(host_src, box_dest[, opts])`._

Design §2 / spec §2a: binding is STRUCTURED PAIR (tuple), never colon-joined `"host:box"` string.
`opts` is optional per-entry mount options override (3rd element); defaults to `None` when 2-tuple.


`class BindEntry(NamedTuple)`
_DEST-KEYED binding entry: `(src[, opts])`: destination is KEY._

Disk-store rework R-3/R-6: bindings arm becomes `dict[dest -> (src, opts)]` `BindMap` (vs
`dict[name -> (src, dest, opts)]`). Destination is no longer part of VALUE; it is mapping KEY, &
name is dropped entirely (R-10: name has no function, as bindings are strictly act-once; name never
distinguishes two entries at one dest).

`opts` is optional mount-options override; `None` means "fall back to category's default options",
exactly as 2-element `Bind` means today.

⚑ **TEMPORARY NAME.** R-6's final name for type is `Bind`. Rename & deletion of legacy 3-tuple
`Bind` happens in P8 of bindings arc. Two bind names coexisting P5→P8 is deliberate, bounded cost of
tbridge — do NOT rename early.

⚑⚑ **THE ARITY TRAP — how two differ.** Legacy `Bind` is legally 2 OR 3 elements & 2-element form
means `(host, box)`; `BindEntry` 2-element form is `(src, opts)`: **Same arity, opposite meaning.**
Nothing in codebase disambiguates them by LENGTH. Two rules, both mandatory:

* **In value space, discriminate by TYPE.** `Bind` & `BindEntry` are distinct `NamedTuple` classes
  (neither is subclass of other); `isinstance(v, Bind)` is False for `BindEntry` & vice versa.
  Every consumer branch tests `isinstance`, never `len`.
* **In RAW space (YAML list / floor value), discriminate by NODE.** Raw `[a, b]` carries no type;
  parse route is chosen via CALLER source: name-keyed bind parses `settings_resolve.unpack_bind`,
  dest-keyed node via `settings_resolve.unpack_bind_entry`. Shape is never sniffed by list itself.


`BindMap: dict[str, BindEntry]` (Alias)
_Dest-keyed bindings: `{box_dest -> BindEntry}` (R-6)_; value shape of bindings' TERMINAL keys, R-5

⚑ Inner keys are NOT part of the keyspace.

⚑ Plain `dict`, deliberately NOT member of `StoreValue`: inside `KeyStore`, `BindMap` materialises
as nested `KeyStore` NODE with `BindEntry` leaves (`_wrap` wraps a plain dict), just as `masks`
materialises as nested node of `bool | None` leaves rather than an opaque dict leaf. It is load
bearing, not incidental: causes bindings arm merge PER-ENTRY across cascade levels through generic
node recursion, instead of box-level arm wiping an inherited workset entry wholesale. It exists for
view & producer signatures, which speak in plain mappings.


`class ReservedKeyError(KeyError)`
_Raised when a `KeyStore` write uses RESERVED key name._

`KeyError` subclass (bad-KEY error, not bad-value); Dunder-pattern names (`__x__`) & `dict` method
names (`_RESERVED_KEY_NAMES`) are reserved & prevented at write time;  `store.get(key, _MISSING)`
probe is collision-safe (Design §2/§3; 1b). Message names offending key AND lists reserved set so
it is actionable.


`StoreValue: Union["KeyStore", Bind, BindEntry, str, int, float, bool, list[str], None]`
_Value space KeyStore leaf or node may hold (design §2)_

`_MISSING` is absence member, never stored value & thus deliberately NOT member. `BindEntry`
(dest-keyed pair, R-6) joins `Bind` (legacy 3-tuple) for P5→P8 bridge window; `BindMap` itself is
NOT member — see its docstring (it materialises as nested `KeyStore` node of `BindEntry`).


### Public Values

`BINDING_DERIVATIONS_NODE: Final[str]`
Reserved INTERNAL derivations node @ root (R-8 manifest, `not_keys.reserved_internal`); NOT key.
Declared ONCE (settings-stack leaf); producer (`settings_categories.derive_binding_keys`), assembly
drop (`settings_assemble._drop_upward_scopes`) spell same token by construction; cannot drift.


`SCOPE_CONTAINMENT: tuple[str, ...]`
_Scope CONTAINMENT order (spec §0 "Directional view/set across CONTAINMENT levels")_

Order: `system ⊃ agent ⊃ workset ⊃ box`, OUTERMOST first. **Single source** for every directional
derivation: `config set` write-allow sets (`config_keys._SCOPE_WRITE_ALLOWED`) & RESOLVE-time drop
of containing-scope keys from lower settings file (`settings_assemble`, spec §0 "Directional
enforcement at RESOLVE"). It lives HERE, in settings-stack leaf that imports nothing from stack, so
both consumers import it without cycling (`config_interface` → `config` →… would cycle back). Scope
CONTAINS every scope to its RIGHT; tail-slice from scope onward is set it may write DOWN into.


### Public Functions

`insert_segments(store: "KeyStore", segments: "Sequence[str]", value: Any,) -> None`
_Install *value* at the path *segments* in *store*, VERBATIM._

*THE* walk. Each element of *segments* is ONE node, taken opaquely — a segment containing `.` is
*one* segment, not two. Walks/creates intermediate `KeyStore` nodes & sets terminal leaf to *value*
exactly as given — no bind parsing, no coercion, no emptiness interpretation. Non-`KeyStore` value
sitting at intermediate segment is REPLACED by fresh node (caller is installing deeper key, so
shallower leaf cannot survive as leaf).

⚑ Entry point for box DESTINATION terminal path — `binding_derivations.<declaration-key>.<dest>`,
installed by `kanibako.commands.start._install_derived_bindings`. Destination is DATA & routinely
contains `.` (`~/.cache/uv`, `/home/agent/.claude/plugins`), so it can only travel as a segment:
joined into dotted string it shatters into extra tree levels, & two dests whose shattered paths nest
(`~/.claude` under `~/.claude.json`) silently overwrite one another.

RAISES `ValueError` on EMPTY *segments*: no path for it; alternative is writing at invented root.

Uses UNBOUND `dict.get` (S3): key legitimately named `get` must not shadow protocol into a crash.


`insert_dotted(store: "KeyStore", dotted: str, value: Any) -> None`
_Install *value* at dotted KEY *dotted* — `insert_segments`, split on `.`._

Dotted front-end, for caller whose path is validated keyspace KEY: every segment of key is
dot-free, so the split is total and lossless. ⚑ Path whose terminal is DATA (box destination) must
NOT come through here — call `insert_segments` &pass destination as one segment.

Its one caller is `pref.*` overlay builder (`kanibako.settings.settings_prefs.pref_overlay`), whose
target is declared key (terminal category target carries its dests in VALUE map, never in key) &
whose contract is *"values are installed VERBATIM — including `None`"* (spec §2h).

DELIBERATELY distinct from `kanibako.settings.settings_assemble._insert_dotted`, which does 
DIFFERENT job: that one PARSES terminal through `_parse_node` so floor entry under bind-shaped
category becomes `Bind`. Two spellings of one walk would be rule-0 trap; two walks with different
jobs are not, provided difference is stated — which is what this paragraph is for.


### Internal Values

`_RESERVED_KEY_NAMES -> frozenset[str]`

_Non-Dunder Methods of `dict`_
User keys must NOT be in this set, as this would shadow inherited methods. Contains `dir(dict)`'s
non-dunder names, verified equal, 1b (0 unguarded, 0 extras); dunder pattern is checked separately.


`_MISSING: _Missing = _Missing()` (see below for `class _Missing`)
_Module-private sentinel distinguishing ABSENT key from present-`None` value at storage surface._

Canonical probe (1b) = BOUND `store.get(key, _MISSING) is _MISSING` == absent — safe because `get`
is reserved key name (see module docstring), so `store.get` is always dict method. Also valid is
UNBOUND `dict.get(store, key, _MISSING)` form, still used by existing consumers. Never stored;
never member of `StoreValue`. Consumed by merge logic in block 2b.


### Internal Types

`class _Missing`
_Module-private `_MISSING` sentinel type._

Distinct singleton type (not `object()`) so it has legible `repr` & static type-checkers can reason
about `StoreValue | _Missing` @ internal call sites (2b) distinguishing absent from present-`None`.

**_Methods_**

`_instance: "_Missing | None" = None` - singleton instance
`__new__(cls) -> "_Missing"` - Pseudo-constructor
`__repr__(self) -> str` - `"_MISSING"` (always)
`__bool__(self) -> bool` - False (always); defense.

`_MISSING` must never be mistaken for real value. Test presence with `is _MISSING`, never as bool.


### Internal Functions

`_check_key_name(key: Any) -> str`
_Validate key for storage; returns it unchanged or raises error._

Rejects:
* Non-`str` key: `TypeError` (keys always strings)
* **Dunder-pattern** name (`__x__`): `ReservedKeyError` (below)
* `dict` method name (`_RESERVED_KEY_NAMES`): `ReservedKeyError` (below)

Match is CASE-SENSITIVE (design §2 — box is Linux). Invoked from `KeyStore.__setitem__`; covers
construction, `[]`-set, & attribute-set (all funnel through `__setitem__`).


`_wrap(value: Any) -> StoreValue`
_Coerce raw value into `StoreValue` space._

Plain `dict` (nested literal) becomes `KeyStore` recursively, so tree is uniform attribute-dicts.
`KeyStore` is left as is (already wrapped). Everything else — `Bind`, scalars, `list`, `None` — is
stored verbatim. `list` is NOT descended into: union only admits `list[str]` scalar lists, never
nested KeyStores inside list.


## KeyStore Class

`class KeyStore(dict):  # type: ignore[type-arg]`
_Recursive attribute-dict: `dict[str, StoreValue]` with attribute access._

Construct from any mapping (or keyword pairs); nested plain `dict` literals are wrapped into
`KeyStore` recursively so entire tree is uniform. Attribute access (`store.foo`) & item access
(`store["foo"]`) read & write SAME keys & return SAME `StoreValue` union; use `[]` for keys not in
valid Python identifiers (`agent.<name>`, hyphens, dots, keywords). User keys cannot collide with
method — every operation is `__dunder__` or inherited `dict` method reached via `[]`.

**CLASS INVARIANT (don't break): `KeyStore` defines ONLY dunder members**, not non-dunder method or
attribute of class. Because `__getattr__` fires only on normal-lookup MISS (1b), non-dunder class
attribute would resolve BEFORE same-named stored key, re-introducing collision reserved set does
not cover. Keep every helper MODULE-LEVEL (e.g. `_wrap`, `_check_key_name`), never `self._helper`.
Holding this keeps reserved set == `dict`'s public methods EXACTLY (`_`-prefixed user key like
`_check_key` remains valid, non-colliding key).

NOTE: no `__slots__` or instance `__dict__` use for storage — state lives entirely in underlying
`dict`. Attribute writes are redirected to keys (see `__setattr__`); object never grows competing
hattribute namespace keys could disagree with.

_**Methods**_

`__init__(self, *args: Any, **kwargs: Any) -> None`
`__setitem__(self, key: str, value: Any) -> None` (valid keys only)
`__getattr__(self, name: str) -> StoreValue` (non-attributes go to item getter)
`__setattr__(self, name: str, value: Any) -> None` (non-dunders go to item setter)
`__delattr__(self, name: str) -> None` (reroutes item deletion)
`__repr__(self) -> str` (returns inner type for items as `dict` should)

`KeyStore` inherits other `dict` methods unchanged (operators, comparators, accessors, etc.).
