# The `KeyStore` Container — storage and nothing else

`KeyStore` is the recursive attribute-dict every resolved keyspace lives in: a `dict` subclass whose
keys are also attributes, whose nested plain `dict` literals are wrapped on write, and whose
leaves are whatever value space the caller instantiates it over. It defines the container, the
reserved-key refusal (`ReservedKeyError`) and the one segment-wise walk — **NOT** resolution, merge,
cascade, `@`-ref / `$VAR` / `~` expansion, typed views, or any consumer of them, and **not** the
`__MISSING__` sentinel, which is value-space and lives in `kb_store`.

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
`ERR_RESERVEDKEY_DUNDER`, `ERR_RESERVEDKEY_SHADOW`, `ERR_TYPE_NONSTRING_KEY`,
`ERR_TYPE_KEYSTORE_ARGS`, `ERR_ATTRIBUTE_NO_KEY`. The reserved-key message names the offending key
AND renders the sorted reserved set, so it is actionable rather than merely correct.

⚑ `ERR_RESERVEDKEY_SHADOW` was `ERR_RESERVEDKEY_METHOD` until the store's own public members joined
the set. What it reports is no longer `dict` method names alone — `RESERVED_KEY_NAMES` is a constant
and `insert_segments` is not a `dict` method — so `_METHOD` had become false OF ITS OWN CONTENTS, and `_SHADOW` states the actual reason both it and its `_DUNDER` sibling
refuse a key. **These constant names are the join key across future per-language strings files**, so
a rename is cheap only while this is the only such file; it was made at that moment deliberately.

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

## `None` semantics — and where the sentinel LIVES

A key may be **absent** (unset) or present as **`None`** (explicit reset). Present-`None` is a legal
stored value, so the two must stay distinguishable at this surface.

⚑ **The `__MISSING__` sentinel that distinguishes them is NOT defined here — it lives in
`kb_store`**, beside the `StoreValue` union that deliberately excludes it. Absence is a fact about
the VALUE space, and this module is value-space-agnostic precisely so it can leave the tree. See
`llm-docs/kanibako/settings/kb_store.py.md`.

What belongs to the container is why the probe is SAFE: the **BOUND** `store.get(key, __MISSING__)`
works because `get` is a reserved key name, so `store.get` is ALWAYS the inherited `dict` method and
never a stored value. The **UNBOUND** `dict.get(store, key, __MISSING__)` form remains equally valid
and is still used by existing consumers (`settings_merge`, the typed views, `config set`); it was
canonical before reserved names existed, and nothing needs retrofitting.

## Collision safety — `RESERVED_KEY_NAMES`, rejected at the SOURCE

Before this rule existed, `KeyStore` stored *any* key and resolved attribute access to the stored key
over the same-named `dict` method, so calling a bound method on a collision-prone store crashed
(`x.get(...)` for the absent-probe, `x.items()` inside `repr`). The collision is now removed at the
source: **a reserved key is refused at write time.**

Reserved means either of:

* the **dunder pattern** — `name.startswith("__") and name.endswith("__")` (the Python data model's
  attribute space); or
* a **name that would shadow a real attribute on the store** — `KeyStore.RESERVED_KEY_NAMES`,
  exactly `dir(dict)`'s non-dunder names PLUS this class's own PUBLIC members
  (`RESERVED_KEY_NAMES`, `insert_segments`) (verified equal: 0 unguarded, 0 extras).

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

## The class-member invariant — unreachable by any declared key

⚑ **State the exposure the right way round.** `__getattr__` fires on a lookup **MISS ONLY**, so a
class member **ALWAYS wins**. A key spelled like a member does NOT break the member; the key becomes
silently **UNREADABLE** through attribute access — `store[name]` still returns it, `store.name` hands
back the member. The failure is a wrong answer with no error, which is why it needs a pin at all.

This was not hypothetical. With the members spelled as ordinary public names,
`store["insert_segments"] = "X"` was ACCEPTED and `store.insert_segments` handed back the bound
METHOD.

**THE INVARIANT: every class member is a DUNDER, or NAME-MANGLED (`_KeyStore__*`), or LISTED in
`RESERVED_KEY_NAMES`.** Each route makes the member unreachable by a declared key — a dunder key is
refused at write time, no key is spelled `_KeyStore__*`, and a listed name is refused too.

| member | what it is | why a key cannot reach it |
|---|---|---|
| `RESERVED_KEY_NAMES` | the public reserved set (above) | **self-LISTED** in the set |
| `insert_segments` | the walk, promoted from module function to method | **self-LISTED** in the set |
| `_KeyStore__check_key_name` | static validator | name-mangled (private) |
| `_KeyStore__wrap` | static wrapper | name-mangled (private) |

⚑ **The split is PUBLIC vs PRIVATE, and it is the PEP 8 point.** A dunder name is the data model's
namespace and is not ours to mint, so neither route uses one. The two validators are implementation
details: they take the leading-double-underscore private spelling and Python mangles them to
`_KeyStore__*`, which no declared key is spelled as. The two public members cannot be mangled —
callers outside the class name them — so they take the only remaining route and NAME THEMSELVES in
the reserved set, which refuses the colliding key at write time.

The pin is `test_every_non_dunder_class_member_is_mangled_or_reserved`. **It asserts the RULE, not
an inventory** — a new PRIVATE helper passes with no test edit, while a new PUBLIC member fails until
someone decides to add it to the reserved set. That is the decision the pin exists to force.

The two validators are private static methods, not module functions:

* `__check_key_name(key)` — returns the key unchanged, or raises. Rejects a non-`str` key with
  `TypeError`, and a dunder-pattern name or any `RESERVED_KEY_NAMES` entry with `ReservedKeyError`.
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
The call is `store.insert_segments(...)`; there is no free function of that name. Being PUBLIC, the
name is self-listed in `RESERVED_KEY_NAMES`, so `insert_segments` is NOT a storable key.

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
