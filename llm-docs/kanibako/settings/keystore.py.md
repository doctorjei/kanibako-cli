# The `KeyStore` Container — storage and nothing else

`KeyStore` is the recursive attribute-dict every resolved keyspace lives in: a `dict` subclass whose
keys are also attributes, whose nested plain `dict` literals are wrapped on write, and whose
leaves are whatever value space the caller instantiates it over. It defines the container, the
reserved-key refusal (`ReservedKeyError`), the absent-vs-present-`None` sentinel (`_MISSING`) and
the one segment-wise walk — **NOT** resolution, merge, cascade, `@`-ref / `$VAR` / `~` expansion,
typed views, or any consumer of them.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §0 (files store UNRESOLVED; the reserved-name
floor), §2 (the cascade), §2a (category list + value types). ⚑ **The spec is the LIVE authority;
read it first.**

Historical: `keystore-design.md` §2 (storage model), §3 (`None` semantics — type-space consequence
only), §6f (`masks` is a keyed `dict[box_dest -> bool|None]`, NOT a list). ⚑ **It is ARCHIVED, at
`~/canon/notebook/archives/keystore-2026-06/keystore-design.md`.** 🛑 **Its §4 writes the cascade
bracket with a 7th `required` tier that S14 and spec §2 CUT** — so on the cascade the archive is
WRONG and the spec wins. Cite it for the storage model only, never for tier structure.

## This module + `keystore_strings` are the LIFTABLE UNIT

The container knows nothing about kanibako. That is the point of its split from `kb_store`, which
holds kanibako's value space (`StoreValue`, `Bind`, `BindEntry`, `BindMap`, `SCOPE_CONTAINMENT`,
`BINDING_DERIVATIONS_NODE`) and stays behind.

⚑ **`keystore` imports `keystore_strings` RELATIVELY, and it is the ONE deliberate exception** to
this package's absolute-in-package-import rule (`settings/__init__.py`). A unit's user-facing
strings are part of its surface, and an absolute path would name the package the pair is meant to
be able to leave. **Nothing else may copy this edge** — `kb_store` spells the absolute path like
every other module, precisely because it is NOT part of the liftable unit.

The five message constants live in `keystore_strings` as `%`-format templates:
`ERR_RESERVEDKEY_DUNDER`, `ERR_RESERVEDKEY_METHOD`, `ERR_TYPE_NONSTRING_KEY`,
`ERR_TYPE_KEYSTORE_ARGS`, `ERR_ATTRIBUTE_NO_KEY`. The reserved-key message names the offending key
AND renders the sorted reserved set, so it is actionable rather than merely correct.

## Generic over its value space

```python
V = TypeVar("V")
class KeyStore(dict[str, "V | KeyStore[V]"], Generic[V]):
```

`V` is the LEAF value space; a node is another `KeyStore[V]`. Kanibako spells its instantiation
`KeyStore[StoreValue]` (`kb_store`); the container never mentions that union and must not acquire a
mention of it. **The generic parameter IS the seam** — it is what lets the pair leave the tree
intact later.

⚑ `__init__` pins `self` to `KeyStore[Any]`. An argument-free `KeyStore()` has nothing to solve `V`
from, and an unsolved `V` makes mypy demand an annotation at every bare construction — including the
intermediate nodes `insert_segments` creates. Explicit `KeyStore[StoreValue]` annotations still bind
normally.

## The storage model

* Construct from any mapping or keyword pairs; nested plain `dict` literals are wrapped into
  `KeyStore` recursively, so the whole tree is uniform attribute-dicts.
* Attribute access (`store.foo`) and item access (`store["foo"]`) read and write the SAME keys and
  return the SAME union. Use `[]` for keys that are not valid Python identifiers — `agent.<name>`,
  `env.<VAR>`, hyphens, dots, Python keywords.
* The SAME `KeyStore` serves per-level partials and the resolved snapshot; this module builds only
  the raw storage surface.
* At most ONE positional argument (`ERR_TYPE_KEYSTORE_ARGS`); keys must be `str`
  (`ERR_TYPE_NONSTRING_KEY`).
* NO `__slots__` and no instance `__dict__` used for storage — state lives entirely in the
  underlying `dict`. Attribute writes are redirected to keys by `__setattr__`, so the object never
  grows a competing attribute namespace the keys could disagree with. Dunder attributes are the
  exception and stay real.
* `__delattr__` deletes the key and re-raises a miss as `AttributeError`, never `KeyError`.

⚑ Three places deliberately use the UNBOUND spelling — `dict.items(source)` in `__init__`,
`dict.get(node, seg, None)` in `insert_segments`, `dict.items(self)` in `__repr__`. A source store
holding a key named `items` must not shadow the protocol the container is built on. The reserved set
below makes this belt-and-braces, and it stays.

## `None` semantics and the `_MISSING` sentinel

A key may be **absent** (unset) or present as **`None`** (explicit reset). Present-`None` is a legal
stored value. The absent-vs-present-`None` probe is the **BOUND** `store.get(key, _MISSING)`: it
returns `_MISSING` iff the key is absent, `None` iff present-`None`, else the value. The bound form
is safe precisely because `get` is a reserved key name — `store.get` is ALWAYS the inherited `dict`
method, never a stored value. The **UNBOUND** `dict.get(store, key, _MISSING)` form remains equally
valid and is still used by existing consumers (`settings_merge`, the typed views, `config set`); it
was canonical before reserved names existed, and nothing needs retrofitting.

`_MISSING` is **never stored** and is deliberately NOT a member of the value space (`kb_store`
excludes it from `StoreValue` by construction). It is consulted by merge LOGIC elsewhere; here only
the sentinel itself is defined, to keep it out of the value space.

`class _Missing` is a distinct singleton TYPE, not a bare `object()`, so it has a legible `repr`
(`"_MISSING"`) and static type-checkers can reason about `StoreValue | _Missing` at internal call
sites. Its `__bool__` returns `False` defensively — **test presence with `is _MISSING`, never as a
bool.**

## Collision safety — `RESERVED_KEY_NAMES`, rejected at the SOURCE

Before this rule existed, `KeyStore` stored *any* key and resolved attribute access to the stored key
over the same-named `dict` method, so calling a bound method on a collision-prone store crashed
(`x.get(...)` for the absent-probe, `x.items()` inside `repr`). The collision is now removed at the
source: **a reserved key is refused at write time.**

Reserved means either of:

* the **dunder pattern** — `name.startswith("__") and name.endswith("__")` (the Python data model's
  attribute space); or
* a **public `dict` method name** — `KeyStore.RESERVED_KEY_NAMES`, exactly `dir(dict)`'s non-dunder
  names (verified equal: 0 unguarded, 0 extras).

Spec keys do not use these (env vars are UPPER_SNAKE; category and scope names are fixed words such
as `bindings` / `box`), so the reservation costs nothing and makes the bound `store.get` safe. A
`_`-prefixed key such as `_wrap` is NOT reserved and stores normally.

The refusal is `ReservedKeyError`, a `KeyError` subclass (a bad-KEY error, not a bad-value one),
raised from `KeyStore.__setitem__` — which construction, `[]`-set and attribute-set all funnel
through. The match is **CASE-SENSITIVE** (the box is Linux, so env variable names are).
*Windows future note:* a Windows HOST folds env-name case; if Windows env support is ever added the
reservation must widen — either case-insensitive in Windows mode, or Windows-only key mangling.
Neither is implemented.

⚑ **`RESERVED_KEY_NAMES` is PUBLIC** (it was module-private `_RESERVED_KEY_NAMES` before the split)
because it has a second reader: `settings_keyspace.RESERVED_LEAF_NAMES` **IS this object** — the
closed-keyspace floor binds a local name to it (an alias, never a copy), so the validator and the
write-time floor are one set and cannot disagree. They must not be two: a name accepted by the
validator and refused by the store fails deep in the store with no reference to the key the user
wrote.

With reserved names forbidden, a plain `__getattr__` (which fires only on a normal-lookup MISS)
suffices; no key shadows a real attribute and no `__getattribute__` interception is needed.

## The class-member pin — EXACTLY four non-dunder members

⚑ **This replaces the older "`KeyStore` defines ONLY dunder members" invariant, which the split
retires.** The reasoning is unchanged and is the reason the pin exists at all: because `__getattr__`
fires only on a MISS, ANY non-dunder class member resolves BEFORE a same-named stored key — a
collision the reserved set does not cover.

The split moves four members onto the class ON PURPOSE:

| member | what it is |
|---|---|
| `RESERVED_KEY_NAMES` | the public reserved set (above) |
| `insert_segments` | the walk, promoted from module function to method |
| `_KeyStore__check_key_name` | name-mangled static validator |
| `_KeyStore__wrap` | name-mangled static wrapper |

None of the four is a declared key or could be one, so the guard becomes an EXACT pin rather than an
empty one: `test_keystore_class_members_are_exactly_the_declared_four` asserts the set, and a FIFTH
member — the unreviewed kind, which is how a real key gets shadowed — reddens it. **Do not add a
non-dunder member without deciding that question first.**

The two validators are private static methods, not module functions:

* `__check_key_name(key)` — returns the key unchanged, or raises. Rejects a non-`str` key with
  `TypeError`, a dunder-pattern name and a `dict` method name with `ReservedKeyError`.
* `__wrap(value)` — coerces a raw value into the stored space. A plain `dict` becomes a `KeyStore`
  node recursively; an existing `KeyStore` passes through; everything else — binds, scalars, lists,
  `None` — is stored verbatim. A `list` is NOT descended into: the value space admits only scalar
  lists, never nested stores inside a list.

## `insert_segments` — THE walk, now a METHOD

```python
store.insert_segments(segments: Sequence[str], value: Any) -> None
```

Installs *value* at the path *segments* VERBATIM. **Each element of *segments* is ONE node, taken
opaquely — a segment containing `.` is *one* segment, not two.** It walks and creates intermediate
`KeyStore` nodes and sets the terminal leaf exactly as given: no bind parsing, no coercion, no
emptiness interpretation. A non-`KeyStore` value sitting at an intermediate segment is REPLACED by a
fresh node (the caller is installing a deeper key, so a shallower leaf cannot survive as a leaf).
It raises `ValueError` on EMPTY *segments* — there is no path for it, and the alternative is writing
at an invented root.

⚑ It is the entry point for a box DESTINATION terminal path —
`binding_derivations.<declaration-key>.<dest>`, installed by
`kanibako.commands.start._install_derived_bindings`, and the launch-snapshot `meta.assembly.*`
leaves. **A destination is DATA and routinely contains `.`** (`~/.cache/uv`,
`/home/agent/.claude/plugins`), so it can only travel as a segment: joined into a dotted string it
shatters into extra tree levels, and two dests whose shattered paths nest (`~/.claude` under
`~/.claude.json`) silently overwrite one another.

⚑ **It was a module-level function `insert_segments(store, segments, value)` and is now a method.**
The call is `store.insert_segments(...)`; there is no free function of that name.

### `insert_dotted` is RETIRED

The dotted front-end is **gone entirely** — do not write against it and do not reintroduce it. Its
sole caller was the `pref.*` overlay builder (`settings_prefs.pref_overlay`), which now splits at the
call site: `overlay.insert_segments(req.target.split("."), req.value)`. That is sound there and only
there — a `pref` target is a validated keyspace KEY, so every segment is dot-free and the split is
total and lossless. **A path whose terminal is DATA must never be split this way.**

⚑ Do not confuse the retired function with `settings_assemble._insert_dotted`, which is alive and
does a DIFFERENT job: it PARSES the terminal through `_parse_node`, so a floor entry under a
bind-shaped category becomes a `Bind`. Two spellings of one walk would be a rule-0 trap; two walks
with different jobs are not, provided the difference is stated — which is what this paragraph is for.
