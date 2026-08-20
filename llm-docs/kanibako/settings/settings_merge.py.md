# Cascade Merge — the depth-sensitive per-name union of the ordered partials
_the ordered `list[KeyStore]` partials → ONE raw merged snapshot_

`settings_merge` is ONE pure function, `merge`, and its helpers. It walks
`kanibako.settings.settings_assemble`'s ordered `list[KeyStore]` partials (MOST-SPECIFIC-FIRST, S8)
and produces ONE raw merged snapshot. It is PURE: no file, env or clock access, same input → same
output, and it NEVER mutates its input partials (S15) — it builds a fresh
`kanibako.settings.keystore.KeyStore`.

## What it replaced, and what it did NOT retire

It is the depth-sensitive successor to `settings_resolve.resolve_value`'s most-specific-first
winner-take-all, which used `""` as a terminal plus a separate `defaults` pass. Here the 3-state
`__MISSING__` / present-`None` model replaces both: assembly already folded the floor INTO the
`base` level, so there is no separate defaults dict.

⚑ **`resolve_value` itself is NOT retired.** It still serves the `config.*` / `system.*` FOUNDATION
path tier (`settings/paths.py`); it is the launch/settings cascade that no longer goes through it.

## Out of scope — hard boundaries

* NO `@`-ref / `$var` / `~` expansion and no cycle detection — that is
  `kanibako.settings.settings_expand`, and refs stay RAW through the merge.
* NO cross-scope `box_dest` collision logic. That is the SEPARATE downstream pass (design §6g); the
  merge keys by NAME only.
* NO typed views (`kanibako.settings.settings_views`).
* NO `config set` (`kanibako.settings.config_interface`).

## Authority

* **`~/vault/rw/keystore-design.md` §6e (depth-sensitive merge — PRIMARY):** a per-NAME recursive
  union of subtrees; a LEAF (scalar / `list` / `Bind`) is replaced WHOLE by the highest-precedence
  scope that SETS it. "Sets" is tested with the `__MISSING__` sentinel — NOT truthiness — at EVERY
  named leaf.
* **Design §3** — the 3-state `None` model plus the present-`None` TERMINAL type-split.
* **Design §6f** — `masks` rides the SAME generic dict-merge.
* **Design §4** — cascade order. The cascade ends at `box`; the former `required` cap is CUT
  (2026-06-29f).
* **Design §6g** — the merge keys by NAME, distinct from reconcile, which keys by `box_dest`.
* **Spec `settings-keyspace-1.8.0.md` §2** (cascade — ends at `box`) / **§2a** (the category list and
  per-name coexistence) / **§2c**.

## The cascade order this module consumes

*Load-bearing — the level list IS the precedence, so an edit to the order is an edit to the
semantics.*

The 6-level bracket, least → most authoritative, is
`base < system < agent.default < agent.<active> < workset < box`.

`merge` receives it REVERSED, MOST-SPECIFIC-FIRST (S8), exactly as block 2a's `assemble_levels`
emits it:

```
[box, workset, agent.<active>, agent.default, system, base]
```

The agent tier keeps its `default` / `<active-name>` discriminator as the true §2d key
(`agent.default.*` / `agent.<active-name>.*`) in 2a — the merge unions by that scope-qualified name
like every other level.

⚑ **The cascade ends at `box`.** The former `required` non-overridable cap was CUT on 2026-06-29f,
so the most-specific level is `box` and the merge is a pure highest-precedence-wins union: **no
level is treated specially anywhere in this module.**

## Seams realized here (`plans/keystore-blocks/SEAMS.md`)

* **S3** — presence is the UNBOUND `dict.get(level, name, __MISSING__) is not __MISSING__` probe.
  Never the bound `.get` (a key named `get` would shadow the method into a crash) and never
  truthiness (a leaf set to `0` / `""` / `False` still SETS and wins). Absent ⇒ `__MISSING__` ⇒
  `False`. The same general rule governs every other walk here: `dict.keys(level)` rather than
  `level.keys()`, `dict.__getitem__` rather than subscripting, so a key named `keys` / `items` /
  `get` cannot shadow the protocol.
* **S15** — the merge does NOT mutate its input partials; it builds a fresh tree.
* **S16** — category-awareness keys off the SAME `_BIND_CATEGORIES` / `masks` segment rule block 2a
  uses. Reused, single-source, not re-derived.

## The per-name rule, in full — `merge` / `_merge_nodes`

For each NAME at each depth, against the ordered partials:

* **absent everywhere** (`__MISSING__` at every level) → not in the snapshot.
* the highest-precedence setter's value AND a lower setter's value are both `KeyStore` subtrees →
  RECURSE, a per-name union of those subtrees. A lower NON-subtree value at the same name is
  shadowed by the higher subtree.
* otherwise the **highest-precedence scope that SETS the name wins** (presence tested via the S3
  probe — never truthiness, never the bound `.get`); its leaf (scalar / `list` / `Bind`) replaces
  any lower value atomically.
* **present-`None` TERMINAL** — the type-split (design §3), keyed by the name's CATEGORY via its
  PATH (S16). A present-`None` winner SETS the name, clearing lower scopes, and the snapshot result
  then depends on the category. See the table below.

Mechanically, `_merge_nodes` collects the setters for a name — each level that SETS it, paired with
their value, MOST-SPECIFIC-FIRST — so `setters[0]` is the winner. That list is always non-empty,
because the name came from the union of SET names in the first place.

`merge` returns the merged `snapshot`. PURE: no I/O; `@`-ref / `$var` / `~` tokens stay RAW
(expansion is block 3); the input partials are NOT mutated (S15). An empty *levels* list yields an
empty snapshot.

### The present-`None` outcomes

| category of the leaf | result |
|---|---|
| scalar leaf | KEEP `None` — the consumer applies its default |
| bind / category leaf | OMIT — no mount (build-4 tier-2 honesty, design §5) |
| `masks` leaf | OMIT — unmask that path (design §6f) |
| anything under the top-level `pref` table | KEEP verbatim, with NO classification at all |

### Why the recursion fires on ANY subtree winner

`_merge_nodes` recurses whenever the winner is a subtree — ALWAYS, not only when a LOWER setter is
also a subtree (design §6e). Three reasons, and each one alone is sufficient:

1. Recursing a LONE subtree winner is what carries the §3 present-`None` type-split into its leaves.
   A present-`None` category or `masks` leaf under a subtree that ONLY ONE level sets must still be
   OMITted.
2. The recursion yields a fresh, non-aliasing node (S15), so it subsumes the old single-subtree
   deep-copy branch.
3. A lower non-subtree setter at this name is shadowed by the higher subtree — it is filtered out of
   the `subtrees` list — which leaves that behaviour unchanged.

Non-`KeyStore` leaves are either immutable (a `Bind` tuple, `str`, `int`, `bool`) and stored
verbatim, or a `list`, which is copied into a fresh `list` so the snapshot can never ALIAS an input
partial (S15).

### Name order

`_names_in_order` returns the union of every name SET across the levels, in first-seen
MOST-SPECIFIC order. It is deterministic, which is part of purity: a name SET at several levels
appears once, at its most-specific occurrence, and iteration order within a level follows insertion
(`dict` order), so the same input always yields the same snapshot key order.

## `_deep_copy_store`

Deep-copies a `KeyStore` subtree into a fresh, non-aliasing tree (S15). It recurses into nested
KeyStores and copies `list` leaves; immutable leaves (`Bind` tuples, scalars, `None`) are shared
safely. Unbound `dict` iteration throughout (S3) so a key named `keys` / `items` cannot shadow the
protocol.

## `_Omit` / `_OMIT`

A module-private sentinel meaning: a present-`None` leaf that must be OMITTED from the snapshot — a
bind / category / masks reset, where there is no default to synthesize (design §3). It is never
stored, and it is distinct from `__MISSING__` (absence) and from present-`None` (a kept scalar
reset). Its `__repr__` is a debug aid only.

## `_resolve_present_none` — the classification

Classifies a present-`None` leaf at *path* by CATEGORY: the §3 type-split, keyed by PATH per S16,
using the SAME `_BIND_CATEGORIES` / `masks` segment rule block 2a uses. It returns `_OMIT` for a
bind / category / masks leaf (drop it — no mount, or unmask) or `None` for a scalar leaf (keep it —
the consumer's default). *path* is the full segment trail to the leaf.

A leaf is a category leaf when EITHER:

* an ANCESTOR segment is a bind category or `masks` — an ENTRY reset like `bindings.rw[/p] = None`,
  `common[~/x] = None`, `masks./p = None`. This is the same "any ancestor is a category" test 2a's
  `_insert_dotted` uses; OR
* the leaf's OWN segment IS a category — a whole-CATEGORY-ROOT reset like `bindings = None`,
  `caches = None`, `masks = None`.

A non-category scalar leaf keeps `None`.

### ⚑ The `pref` subtree is EXEMPT — no classification at all

A pref is a REQUEST, not a value (spec §2h), and the request's own path MIRRORS its TARGET's. So
`pref.agent.claude.common.<box_dest>` carries `common` among its ancestors, and the category rule
above would OMIT it — deleting the RECORD of a `null` request from the snapshot while the request
itself was applied, so `config show` / `--effective` could not show it.

It is also the literal implementation of §2h's *"the pref layer MUST NOT interpret emptiness AT
ALL"*: three idioms already exist downstream (present-`None`, terminal `""`, and the COPY-disable
sentinel), and the pref layer must not become a fourth place deciding what "empty" means.

The classification the spec DOES want happens at the INSTALLED target key, where the path is
`agent.claude.common.<box_dest>` and the ordinary rule applies.

⚑ The trailing segment there is a DESTINATION, not a name. The four categories went TERMINAL and
dest-keyed on 2026-08-08c, so `.plugins` — which this note used to spell — is no longer a key
segment anywhere.

### ⚑ Both reset spellings survived the 2026-08-08c dest-key retool with NO edit here

That is the frozenset doing its job: `_BIND_CATEGORIES` already held all five tokens, and the
ancestor test never cared whether the segment below a category was a NAME or a DESTINATION. The path
shape changed; the classification did not.

### Why the ROOT case OMITs

The root case keeps the design §5 tier-2 coupling honest: a category accessor promises a `Mapping` /
`set`, so a present-`None` at the category root must OMIT — drop the whole category — never KEEP a
bare `None` where a mapping is contracted.

⚑ Design §3 spells the ENTRY case only. The ROOT case is its consistent extension; the design is
silent on a whole-category reset, and that flag was raised in chat.

## Module-private constants

* `_MASKS_SEGMENT = "masks"` — the scope-category segment whose present-`None` leaf means UNMASK,
  not a scalar reset. S16: reuse 2a's category awareness. `masks` is the one keyed `bool|None`
  category (S5); the bind-shaped categories are `_BIND_CATEGORIES`, imported from
  `settings_assemble`.
* `_PREF_ROOT = "pref"` — the top-level table holding `pref.*` REQUESTS (spec §2h). It is SPELLED
  here rather than imported from `settings_prefs` to keep this module's import surface at
  `settings_assemble` plus `keystore` / `kb_store`: it is one fixed token, and `settings_prefs`
  imports the settings stack, which would cycle.
