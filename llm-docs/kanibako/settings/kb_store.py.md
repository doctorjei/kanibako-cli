# Kanibako's Store Shape — the values a `KeyStore` holds

`kb_store` is kanibako's instantiation of the container's value space: the `StoreValue` union, the
two binding value types (`Bind`, `BindEntry`), the `BindMap` alias, and the two constants that are
facts about the SHAPE of a store rather than about any one key (`SCOPE_CONTAINMENT`,
`BINDING_DERIVATIONS_NODE`). It defines shape only — **NOT** resolution, merge, cascade, `@`-ref /
`$VAR` / `~` expansion, typed views, or consumers.

It imports nothing from the settings stack beyond the container itself, `keystore`, which makes it
the stack's other leaf: everything from `config_keys` to `store_collapse` can reach it without
cycling.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §0 (directional view/set across CONTAINMENT
levels), §2a (category list + value types). ⚑ **The spec is the LIVE authority; read it first.**

## Why this is a separate module from `keystore`

`keystore` is generic over its leaf type (`KeyStore[V]`) and knows nothing about kanibako — it is
liftable, together with `keystore_strings`, as a unit. **This module is what makes it kanibako's:**
the tree spells `KeyStore[StoreValue]`, and every name below is a statement about kanibako's
settings, not about attribute-dicts. `kb_store` therefore stays behind and imports `keystore` by the
ABSOLUTE path, like every other module in the package; the relative-import exception belongs to the
liftable pair alone.

## `StoreValue` — the union

```python
StoreValue = Union[KeyStore, Bind, BindEntry, str, int, float, bool, list[str], None]
```

The value space a `KeyStore` leaf or node may hold.

* `list[str]` is in the union for genuinely list-valued scalar keys.
* `None` is a legal STORED value (explicit reset), distinct from an absent key. The
  absent-vs-present-`None` probe and the `_MISSING` sentinel it returns live in `keystore`.
* ⚑ **`_MISSING` is deliberately NOT a member.** It is the absence marker, never a stored value;
  keeping it out of the union is what keeps absence out of the value space.
* `masks` is a nested `KeyStore` of `bool | None` leaves, ***NOT*** a bare list.
* ⚑ `BindMap` is NOT a member either — see below.

## `Bind` — the binding value

```python
class Bind(NamedTuple):
    host: str
    box: str
    opts: str | None = None
```

Spec §2a: a binding is a STRUCTURED PAIR (a tuple), **never** a colon-joined `"host:box"` string.
`opts` is the optional per-entry mount-options override; `None` means fall back to the category's
default options.

## `BindEntry` — the DEST-KEYED binding entry

```python
class BindEntry(NamedTuple):
    src: str
    opts: str | None = None
```

The disk-store rework (R-3/R-6) makes a bindings arm `dict[dest -> (src, opts)]` rather than
`dict[name -> (src, dest, opts)]`. **The destination is no longer part of the VALUE; it is the
mapping KEY**, and the entry name is dropped entirely (R-10: the name has no function, bindings are
strictly act-once, and a name never distinguishes two entries at one dest).

`opts` carries exactly the meaning it has on `Bind`: `None` means "fall back to the category's
default options".

⚑ **TEMPORARY NAME.** R-6's final name for this type is `Bind`; the rename, and the deletion of the
legacy 3-tuple `Bind`, happen in P8 of the bindings arc. Two bind names coexisting P5→P8 is a
deliberate, bounded cost of the bridge — **do NOT rename early.**

### ⚑⚑ THE ARITY TRAP — how the two differ

The legacy `Bind` is legally 2 OR 3 elements, and its 2-element form means `(host, box)`.
`BindEntry`'s 2-element form is `(src, opts)`. **Same arity, opposite meaning** — and nothing in the
codebase disambiguates them by LENGTH. Two rules, both mandatory:

* **In value space, discriminate by TYPE.** `Bind` and `BindEntry` are distinct `NamedTuple` classes
  (neither is a subclass of the other), so `isinstance(v, Bind)` is False for a `BindEntry` and vice
  versa. Every consumer branch tests `isinstance`, never `len`.
* **In RAW space (a YAML list, a floor value), discriminate by NODE.** A raw `[a, b]` carries no
  type, so the parse route is chosen by the CALLER's source: a name-keyed bind parses through
  `settings_resolve.unpack_bind`, a dest-keyed node through `settings_resolve.unpack_bind_entry`.
  The shape is never sniffed from the list itself.

## `BindMap` — the dest-keyed alias

```python
BindMap = dict[str, BindEntry]
```

`{box_dest -> BindEntry}` (R-6): the value shape of a bindings category's TERMINAL keys (R-5).

⚑ The inner keys are destinations — **they are NOT part of the keyspace.**

⚑ It is a plain `dict` and deliberately NOT a member of `StoreValue`. Inside a `KeyStore`, a
`BindMap` materialises as a nested `KeyStore` NODE with `BindEntry` leaves (the container's wrapper
turns a plain `dict` into a node), just as `masks` materialises as a nested node of `bool | None`
leaves rather than an opaque dict leaf. That is load-bearing, not incidental: it makes the bindings
arm merge PER-ENTRY across cascade levels through the generic node recursion, instead of a box-level
arm wiping an inherited workset entry wholesale. The alias exists for the view and producer
signatures, which speak in plain mappings.

## `SCOPE_CONTAINMENT` — the containment order

```python
SCOPE_CONTAINMENT: tuple[str, ...] = ("system", "agent", "workset", "box")
```

Spec §0's "directional view/set across CONTAINMENT levels": `system ⊃ agent ⊃ workset ⊃ box`,
**OUTERMOST first.** A scope CONTAINS every scope to its RIGHT, so the tail slice from a scope
onward is the set it may write DOWN into.

This is the **single source** for every directional derivation, and it lives here — in a stack leaf
that imports nothing from the stack — so every consumer can import it without cycling
(`config_interface` → `config` → … would cycle back). The consumers today:

* `config_keys._SCOPE_WRITE_ALLOWED` — the `config set` write-allow sets.
* `settings_assemble._drop_upward_scopes` — the RESOLVE-time drop of containing-scope keys from a
  lower settings file (spec §0, "directional enforcement at RESOLVE").
* `settings_keyspace` — what counts as a scope token at all.
* `settings_launch`, `store_shape`, `store_collapse` — the per-scope iteration order of the launch
  snapshot and the disk-store arms.

⚑ **Four scopes, deliberately not the six cascade levels.** Containment and the cascade are
different orderings and conflating them manufactures contradictions that are not in the spec.

## `BINDING_DERIVATIONS_NODE`

```python
BINDING_DERIVATIONS_NODE: Final[str] = "binding_derivations"
```

The reserved INTERNAL derivations node at the store root (R-8 manifest,
`not_keys.reserved_internal`). **It is not a key.** Declared ONCE here, so the producer
(`settings_categories.derive_binding_keys`) and the assembly drop
(`settings_assemble._drop_upward_scopes`) spell the same token by construction and cannot drift.
