# Typed Access — the three-tier read surface over the expanded snapshot

`settings_views` is the READ side of the settings store. The expanded snapshot (assemble → merge →
expand) is a recursive `kanibako.settings.keystore.KeyStore` whose leaves carry the loose
`kanibako.settings.kb_store.StoreValue` union. This module adds the TYPED read surface design §5
calls for, confining that loose union to genuinely mixed nodes only.

It is READ-ONLY in the strongest sense: every accessor wraps an EXISTING snapshot node and exposes a
typed lens over it. It never merges, expands, reconciles, writes, or mutates, and it does not copy
the node — the snapshot stays the single source of truth.

Authority: `~/vault/rw/keystore-design.md` §5 (typed access — PRIMARY, including the load-bearing
`Bind`-not-`Bind|None` coupling) and §6f (resolved `masks` = `set`); spec
`settings-keyspace-1.8.0.md` §2a (categories and their value types).

## The three tiers (design §5)

1. **Typed VIEWS over finite subtrees** — system paths and `meta`: fixed names, exact static types
   (`Path` / `str` / `bool`). This module ships the MECHANISM (`typed_field` plus the `FiniteView`
   base) and ONE small worked example (`MetaView`). It does NOT port `StandardPaths`, which still
   resolves through `resolve_value` on the FOUNDATION path tier (that port is block 7).
2. **Typed CATEGORY accessors** — each §2a category has DYNAMIC keys but ONE known value type, so it
   is a typed read-only mapping. `bind_map` gives `Mapping[box_dest, BindEntry]` for EVERY
   bind-shaped category: the `bindings.{ro,rw}` arms and `caches` / `seeded` / `common` / `synced`,
   all of them dest-keyed terminal keys (R-5/R-6; the four followed on 2026-08-08c). `env_view`
   gives `Mapping[str, scalar]` for `env`. `masks_set` gives `set[box_dest]` for the resolved
   `masks` (design §6f).
3. **Raw attribute / `[]` access** returning the full union — already provided by `KeyStore` itself
   (block 1), for genuinely mixed nodes only.

⚑ The NAME-keyed `bind_category` / `bindings` lenses were DELETED in the 2026-08-08c pass. They had
no production caller and their `Mapping[str, Bind]` contract was the retired shape, so leaving them
would have left a typed accessor promising a value the store can no longer hold.

## PER-SCOPE, not cross-scope (S21)

Every accessor is a typed LENS over a category NODE WHEREVER it appears in the scope-qualified
snapshot — `store.box.bindings.rw` → `Mapping[str, BindEntry]`. It is NOT a cross-scope aggregator,
and it does NOT aggregate one dest across scopes — that is reconcile. Resolving the SAME `box_dest`
declared at different scopes is the job of the `store_shape` producer plus the assembly collapse
(design §6g), a SEPARATE downstream pass that is out of scope here.

## The load-bearing `Bind`-not-`Bind | None` coupling (S22)

A category accessor exposes `Bind` (NOT `Bind | None`) ONLY because the cascade merge OMITS every
present-`None` bind, category and masks leaf before any consumer sees the snapshot (design §3/§6e).
This module RELIES on that and does NOT re-admit `None`. If it ever encounters a `None` — or an
otherwise ill-typed — leaf under a category node, that is a BUILD-INVARIANT BREACH, so it raises
`ViewError` rather than silently type-laundering it.

The same reasoning makes `masks_set` honestly a `set`: a resolved `masks` value is the mask marker,
because present-`None` unmasks were dropped at build (§6f).

`ViewError` is therefore a signal about a BUILD bug upstream (block 2b/3), NOT about bad user input.
It is raised when an accessor sees a leaf its tier's contract says cannot exist — a `None` or
non-`Bind` leaf under a bind category (the S22 coupling breach), a non-scalar under `env`, or a
missing / wrong-typed field under a finite view.

## Collision safety (S3) — why every read uses the unbound `dict` methods

A category key may legitimately be named `get`, `items` or `keys`. Every container operation over a
node therefore goes through the UNBOUND `dict` methods — `dict.get(node, k)`, `dict.keys(node)`,
`dict.__getitem__`, `dict.__contains__`, `dict.__len__` — and NEVER the bound `node.get(...)` that a
user key would shadow into a crash. This is the standing `KeyStore` foot-gun.

## The bind lens

`_BindMapView` is the ONLY bind lens (R-5/R-6): the mapping KEY is the destination and the value
carries only `(src, opts)`. It wraps an existing `KeyStore` node WITHOUT copying, and asserts on
every read that the value is a real `BindEntry` — the S22 coupling, since build dropped
present-`None` entries, so a `None` here is a build breach. It is a `Mapping`, not a
`MutableMapping`, so there is no mutation path at all.

⚑ The check is `isinstance(value, BindEntry)`, which is FALSE for a legacy 3-tuple `Bind` even
though both are tuples. A stale name-keyed arm handed to this lens is REFUSED at read rather than
mis-read. The P5→P8 bridge keeps both shapes alive, and nothing may tell them apart by arity.

`bind_map(node, label=...)` is the public entry. *node* is the `KeyStore` that a terminal
bind-shaped key holds — `<scope>.bindings.{ro,rw}` or `<scope>.{caches,seeded,common,synced}` — i.e.
`{box_dest: BindEntry(src, opts)}`. The returned mapping is read-only and does not copy. *label*
names the node in error messages and has no behavioral effect.

`bind_maps(node, label=...)` splits a whole dest-keyed `bindings` NODE into its `(ro, rw)` lenses.
It is the dest-keyed counterpart of the retired `bindings` accessor: the arm is still its own key
segment (R-5), and only the entries below it are dest-keyed. A mode ABSENT from the node yields an
EMPTY mapping (§3/§6e), never an error — which is what `_sub_or_empty` is for.

## `env` and `masks`

`_EnvView` / `env_view` wrap an existing `env` `KeyStore` node such as `store.box.env` without
copying. `env.<VAR>` values are scalars per spec §2a; a non-scalar leaf is a build breach.

Inside the check, `bool` is admitted because it is a subclass of `int` — a scoped env flag is a
legitimate value. A `None` is rejected: `env.<VAR>` is a scalar value and an env var has no
"consumer default" to fall back to, so a `None` leaf means there is nothing to export, which is a
build breach rather than a legitimate value.

`masks_set` takes a `masks` subtree (`store.box.masks`), which is a keyed `dict[box_dest → bool|None]`
(S5). AFTER build, every present-`None` unmask has been DROPPED (design §6f), so every SURVIVING key
is a mask marker and the honest resolved shape is a `set` of masked dests, NOT a mapping. The
function returns exactly the set of KEYS present in the node. As an S22-style invariant check it
asserts that no surviving value is `None` — a `None` here would mean the unmask should have been
dropped and was not — and raises `ViewError` if one is found. It reads via unbound `dict` ops and
neither copies nor mutates the node.

## `derived_bindings` — the read half of `binding_derivations`

*node* is the `binding_derivations` subtree: the reserved INTERNAL node at the snapshot root (R-8,
not a key) carrying the MATERIALISED binding that each ABSTRACT declaration (`common` / `caches` /
`seeded`) derives, filed at `binding_derivations.<declaration-key>` so a reader can see the
declaration AND the binding it produces (spec §0).

Unlike the tier-2 lenses this returns a fresh dict rather than a live view. The node is PARAMETRIC
over the whole key space below it — `binding_derivations.agent.claude.common.~/.claude/plugins` is
the declaration key plus the entry's DEST, since the four categories went terminal and dest-keyed on
2026-08-08c — so the useful shape is the FLAT declaration key, which no lens over one node can
present. The `Bind`s themselves are shared, not copied, because they are immutable.

An absent or empty node yields `{}`. A non-`Bind` leaf raises `ViewError` (S22 — a build breach,
never type-laundered).

⚑ This is the READ half only. The keys are PRODUCED by
`kanibako.settings.settings_categories.derive_binding_keys`, which is deliberately named
differently: two functions with one name in two modules is exactly the confusion the conventions
open with.

## Tier-1 — the checking coercers

A finite view promises EXACT types (design §5), so a field's coerce must REJECT a mistyped stored
leaf rather than launder it. A bare constructor (`str` / `bool` / `int`) is the foot-gun: `str(123)`
→ `"123"` and `bool("false")` → `True` would silently HIDE a build bug, such as a stored `"false"`
surfacing as the bool `True`. The helpers isinstance-CHECK the stored value first and raise
`ValueError` on a mismatch, which `typed_field` wraps in a `ViewError`.

* `as_str` / `as_bool` / `as_int` / `as_float` — the stored value must ALREADY be of that type.
  `as_bool` checks `bool` before `int` because `bool` is a subclass of `int`; `as_int` and
  `as_float` reject a `bool` for the same reason. `as_float` widens an `int`.
* `as_path` — the one legitimate CONVERSION rather than a launder: a system path is stored as a
  `str` (spec) and `Path` is not a stored type, so it checks `str` then wraps. A non-str (int, bool,
  `Bind`) raises rather than being stringified.
* `as_opt_path` — the optional-path variant, for a finite-view field whose spec value is a path OR
  `<None>`: a whole-value `@`-ref None terminal that survived as a real `None` leaf, e.g.
  `meta.box.share_workset` for STANDALONE (spec §2c). A present `None` is honest and returned
  as-is; a non-str / non-None leaf is rejected.
* `as_argv_fragment` — a stored argv fragment (`list`/`tuple` of `str`) → `list[str]`, for the
  plugin-set launch-grammar leaves `meta.agent.<a>.exec` and the values inside
  `meta.agent.<a>.mode` (spec §2d / B5). It rejects any non-str element, and normalizes a tuple (the
  descriptor's in-memory form) to a list.
* `as_mode_table` — the `meta.agent.<a>.mode` NODE → `dict[str, list[str]]`. The launch-grammar
  table is materialized as a KeyStore sub-node (the floor's dict value, spec §2d
  `dict[mode_key → argv fragment]`), and each mode's value must itself be an argv fragment. A
  non-node (scalar or `Bind`) or a malformed fragment is rejected.

## Tier-1 — the mechanism

`typed_field` is a typed read-only field descriptor. A finite view (system paths, `meta`) has FIXED
names of KNOWN type; this descriptor declares one such field. It reads the named key off the wrapped
snapshot node via the unbound `dict` probe (S3), converts it with the field's *coerce* callable, and
returns the EXACT static type `T` — so a consumer of `view.field` gets `T`, not the loose
`StoreValue` union (design §5 tier-1). It is read-only: the descriptor has no `__set__`.

Always hand it a CHECKING coercer, never a bare constructor, for the laundering reason above — that
is what the §5 exact-type promise rests on.

A missing field, or a value the coerce rejects, raises `ViewError`, because a finite view promises
that every named field is present and well-typed. The *key* argument points a field at a stored key
whose name differs from the Python attribute (e.g. a `global` keyword → `global_dir`); it defaults
to the attribute name.

`FiniteView` is the base class. Subclasses declare `typed_field` attributes, one per known key, and
get exact-typed read-only access to a FIXED-name subtree. The view does not copy the node and
exposes no mutation path.

## The finite views that exist today

**`MetaView`** is a small WORKED EXAMPLE (design §5 tier-1). It wraps a `meta` NODE and exposes a
few representative fields at their exact types — a `Path` (`root`) and a `str` (`name`) — to
demonstrate the `typed_field` + `FiniteView` mechanism. It is NOT the full `meta` schema and is NOT
wired into any consumer (that is block 7). Add more fields by declaring more `typed_field`
descriptors.

**`MetaRuntimeView`** wraps `store.meta.runtime` (block B1, spec §1A) and surfaces the
runtime-resolved identity anchors at their exact types: the workset root `ws_root` (a resolved
`Path`) and the resolved mode token `project_type` (a `str`, one of `"primary"` / `"named"` /
`"standalone"`). ADDITIVE — no consumer reads it yet (B1).

⚑ There is NO `ws_settings` field. `meta.runtime.ws_settings` is CUT from the keyspace (spec §1A);
the workset-tier settings FILE is `MetaWorksetView.settings`, which now spells itself directly off
`ws_root`.

**`MetaBoxView`** wraps `store.meta.box` (block B1 + B2) and exposes the RO identity anchors
materialized for the box (spec §2c; §0 meta-RO):

* `mode` (B1) — the resolved mode token surfaced from `@meta.runtime.project_type` (spec §2b; this
  was the settable `box.mode`).
* `name` (B2) — the box name (`proj.name`; the `@meta.box.*` binds key off it).
* `workspace` (B2) — the resolved in-box workspace SOURCE (= `str(proj.project_path)`);
  `box.bindings.rw.workspace` routes through `@meta.box.workspace` (spec §2c).
* `inbox` (B2) — this box's own mailbox dir (spec §2c); `box.bindings.rw.inbox` routes through
  `@meta.box.inbox`.
* `share_global` (B2) — this box's system-scope share dir (spec §2c).
* `share_workset` (B2) — this box's workset-local share dir, `None` for STANDALONE (spec §2c).
* `settings` — the RO box-TIER settings-file path, UNIFORM in every mode (spec §2c, ALL PROJECTS).
  Standalone's is `<root>/box_data/box.yaml`, a real path that is merely ABSENT BY DEFAULT
  (§5) — NOT a `None` terminal. It is typed `Path | None` only because a narrow or partial resolve
  may materialize no box tier; the launch always supplies one.

`container_name` and `helper_num` are a non-bind RENDER and are deliberately not materialized here
(JC-B2-3).

**`MetaWorksetView`** wraps `store.meta.workset` (block B1 + B2, spec §1A/§2c). It exposes the
single-source re-rooted `path` (= `@meta.runtime.ws_root`, a resolved `Path`); `settings`
(= `@meta.runtime.ws_root/workset.yaml`, a `Path` for ALL modes including standalone, whose ROOT
file plays the workset tier — spelled directly off `ws_root` now that the `meta.runtime.ws_settings`
hop is CUT, spec §1A); and the `name` partition token (`__PRIMARY__` / `<named>` /
`__STANDALONE__`), which is now the `@meta.runtime.ws_name` anchor (block B1, single source, spec
§1A/§2c 2026-07-04; it was a direct B2 literal).

**`MetaAgentView`** wraps `store.meta.agent.<agent>` (block B2 + B5, spec §2d). It exposes:

* `name` — the plugin-set name (spec §2d), REQUIRED when an agent exists; it identifies the store
  dir and the cascade key.
* `path` — the agent STORE ROOT (§2d = `@config.agents/<agent>`), which is also §2a's agent
  DECLARATION ROOT: an abstract-category source stores
  `@meta.agent.<agent>.path/<category>/<leaf>`, so the key resolves for real.
* the B5-materialized trio (§3.3 rulings): `settings` (the agent-tier settings cascade FILE,
  `@meta.agent.<a>.path/agent.yaml`, resolved); `mode` (the harness's INTERACTIVE launch grammar,
  `dict[mode_key → argv fragment]`); and `exec` (the STANDALONE one-shot fragment, declared under
  the Python-safe attribute `exec`). `exec` is ABSENT for an agent with no `exec` operation, so
  access it only where it is materialized.

## Helpers

`_require_node` guards that an accessor wraps a real `KeyStore` node rather than a leaf. A category
or finite-view accessor is meaningless over a scalar, `Bind` or `None` leaf; passing one is a CALLER
bug (a wrong path into the snapshot), so it raises `ViewError` rather than producing an empty or
wrong lens.

`_sub_or_empty` returns a named sub-node as a `KeyStore`, or an EMPTY one. It is what lets `bindings`
split into `ro` / `rw` where a mode the build omitted is simply absent → an empty lens (§3/§6e), not
an error. It reads through the unbound `dict` ops (S3). A present-but-non-`KeyStore` sub-value is a
build breach → `ViewError`.

## OUT of scope — hard boundaries

* NO cross-scope `box_dest` collision resolution (design §6g).
* NO merge, expansion or cycle detection — `kanibako.settings.settings_merge` and
  `kanibako.settings.settings_expand` own those.
* NO `config set` — `kanibako.settings.config_interface` owns that.
* NO `StandardPaths` port.

This module does not modify `keystore`, `kb_store`, `settings_merge`, `settings_expand`, `paths` or
`start`. It only READS the snapshot.
