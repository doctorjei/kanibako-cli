# Category entries — the declaration list, mount options

⚠️ **PARTIAL MIRROR.** `settings_categories.py` is a large module (the category dataclasses, the
two §0 refusal texts, the launch seam's delivery carrier). Only the parts whose prose has been
migrated out of source appear here; absence of a symbol below means "not migrated yet", never "does
not exist". Migration happens as files are touched, not in a big-bang pass.

## What this module IS — a vocabulary, not a resolver

Unified scope-category resolution, pure and additive. It GENERALIZES the (now-folded)
`settings_shares` + `settings_seeds` resolvers into one *category* primitive: every
path-delivery mechanism the settings framework exposes at a scope is a CATEGORY, and what
lives here is that VOCABULARY — which category names exist and what key SHAPE each one takes,
the `CategoryEntry` record an emitted entry is, what its *delivery* (COPY vs MOUNT) means, and
the collision refusals a category set must satisfy.

⚑ **It does not discover, resolve or emit.** `settings_launch.snapshot_category_entries` walks
the precedence levels, resolves each key through `settings_resolve` and CONSTRUCTS every
`CategoryEntry` there — the four construction sites are all in that module. This one is pure
and imports only stdlib: `re`, `dataclasses`, `typing`. ⚑ That import list is the invariant,
not a coincidence: a resolver import here would close a cycle, which is precisely why the
emission sits at the launch seam and the vocabulary sits here.

The OLD by-NAME `LevelView` resolver was RETIRED long before the reconcile was — it was wrong
in a number of cases, which is why the snapshot pipeline replaced it. Its frozen, non-shipping
remnant lives ONLY in `tests/test_flawed_oracle.py` as a drift tripwire, NOT a correctness
authority. (⚑ That path read `tests/support/flawed_oracle.py` until 6-R3; the module moved in
beside the tests that drive it so the retired model occupies exactly ONE file, and a stale
`.pyc` under `tests/support/__pycache__` is all that is left at the old location.) The
`settings_shares` / `settings_seeds` wrapper modules it used to feed were retired in 7c — the
launch and `workset share` paths now resolve through the snapshot pipeline.

## The nine categories

Available at every scope `{system, agent, workset, box}`:

| category | key shape | host_src | delivery |
|---|---|---|---|
| `masks` | `{scope}.masks` (TERMINAL) | `None` | MOUNT |
| `bindings.ro` | `{scope}.bindings.ro` (TERMINAL) | entry | MOUNT |
| `bindings.rw` | `{scope}.bindings.rw` (TERMINAL) | entry | MOUNT |
| `caches` | `{scope}.caches.{name}` | bind | MOUNT |
| `seeded` | `{scope}.seeded.{name}` | bind | COPY |
| `common` | `{scope}.common.{name}` | bind | MOUNT |
| `synced` | `{scope}.synced.{name}` | bind | COPY |
| `env` | `{scope}.env.{VAR}` (value) | `None` | ENV |
| `secret_path` | `{scope}.secret_path.{VAR}` (path) | host path | MOUNT |

⚑ `masks` and `bindings.{ro,rw}` are TERMINAL keys (R-5): the whole arm is ONE key and the
inner names are NOT part of the keyspace. A `bindings` arm is DEST-KEYED (R-6) — its inner key
IS the box destination and its "entry" value is the 2-element `[host_src[, options]]` unpacked
by `settings_resolve.unpack_bind_entry`. `masks` is keyed by box destination too, its value
carrying only presence (a masked dest).

The four abstract categories are still keyed by NAME, and a "bind" value there is the
3-element `[host_src, box_dest[, options]]` unpacked by `settings_resolve.unpack_bind` (spec
§2a — never a colon-joined string). ⚑ **The two unpackers are NOT interchangeable:** both
accept a 2-element list and the two mean OPPOSITE things, so the route is chosen from the NODE
the value came from, never sniffed off the value. `env` carries a scalar value for `{VAR}` (no
host source, no guest *path* — its `box_dest` field is the VAR name).

### `secret_path`, the arm's-length SECRET category

`secret_path` (spec §2a SECRET category, 2026-07-06) is SCALAR-valued like `env` — a host PATH
to secret material, e.g. a 0600 bearer-token file — but delivered ARM'S-LENGTH as a ro MOUNT.
At launch the cascade-resolved host path is ro-bind-mounted to a fixed, non-persistent in-box
location (`SECRET_MOUNT_DIR/{VAR}`) and a box-side shim exports `{VAR}` from that mount, so
kanibako NEVER reads the secret VALUE — never in process memory, the podman argv, the
snapshot, keystore or logs. It is keyed per `{VAR}` (same cascade merge and reserved-name floor
as `env.{VAR}`); its `box_dest` IS a real guest path, so it participates in box_dest
collisions as a CONCRETE-layer peer of `bindings.{ro,rw}` (see `CONCRETE_CATEGORIES` for the
per-VAR carve-out), and its `host_src` is the SCALAR path, NOT a Bind tuple.

`SECRET_MOUNT_DIR` is `/run/kanibako/secrets` — the fixed, non-persistent in-box directory
each host secret file is ro-mounted into as `{SECRET_MOUNT_DIR}/{VAR}`. It is deliberately NOT
under the box `~` home, so it is disjoint from the home / workspace / vault mounts and stays
OUT of the `~`-rooted mount depth-sort. The box-side export shim in `start.py` reads each file
here into `{VAR}` at agent start; kanibako only ever writes the mount PATH.

### Delivery

* `seeded` and `synced` are file **COPIES** (synced creds inode-swap, which breaks single-file
  binds — they are copy-synced).
* `caches`, `bindings.ro`, `bindings.rw`, `common`, `masks` are podman **MOUNTs** that
  physically shadow whatever is at the same path.
* `env` is neither — it is delivered as an environment variable (`ENV`).

### Two orthogonal axes

Unchanged from `common` / `seeded`:

* **The KEY's scope** selects — for `bindings` — the mount mode, and names the DECLARATION
  ROOT an abstract-category source is spelled against when it is declared (spec §2a; the
  rooting happens at the declaration site, never in this module).
* **The LEVEL where the key is SET** decides *precedence*. A box may set a system-scoped key to
  a terminal `""` to suppress an inherited entry.

`DECLARATION_ROOT_REF` is the single copy of spec §2a's per-SCOPE declaration-root table —
`system` → `@config.data`, `agent` → `@meta.agent.{agent}.path`, `workset` →
`@meta.workset.path`, `box` → `@meta.box.path`. `{agent}` is the only placeholder, because the
agent tier is discriminated and its root names the agent. Its consumers are the
declaration-time ref builder (agent scope) and the `config set` refusal message, which must
name the root of the scope the user actually typed.

### Accumulate / apply order

Distinct `(category, scope, name)` entries accumulate. For a single key, the most-specific
level that set it wins (`resolve_value`). Entries are returned in scope *apply* order `system,
agent, workset, box` — the REVERSE of precedence — so the most-specific scope lands LAST,
letting a later copy overlay an earlier one and podman's "last `-v` wins" dedup honor box over
system. Within a scope they are ordered `(category, name)` ascending for determinism. The §0
collision table is imposed on top of this order by the three seams, each over the same list in
the same order. `_SCOPE_APPLY_ORDER` is that order as data, read by `_most_specific`.

### No root-join

There is NONE, by rule. Every `host_src` reaching this module already resolves ON ITS OWN —
absolute, `~`, `$var` or an `@`-ref (spec §2a) — because the ABSTRACT categories
(`common` / `caches` / `seeded`) are rooted at DECLARATION: an author writes a bare leaf and
the declaring loader stores the full `@<scope-root>/<category>/<leaf>`. A layer that prefixed a
root on the way to a mount is the shape §2a calls FORBIDDEN — it hides the true source and
resolves differently in any other context. Nothing here joins anything.

### Abstract vs concrete

`ABSTRACT_CATEGORIES` is `common`, `caches`, `seeded` — the three that let an author write a
bare LEAF, rooted at declaration under `<scope-root>/<category>/`. The rest
(`bindings.{ro,rw}`, `synced`) are CONCRETE: they take NO root at any scope, so a relative
source there is a defect, not a shorthand.

`CONCRETE_CATEGORIES` is the CONCRETE MOUNT layer — `bindings.ro`, `bindings.rw`,
`secret_path` — which §0 calls the SOURCE OF TRUTH. A mount is emitted from a
`bindings.{ro,rw}` declaration and from nothing else; the abstract categories reach a mount
only by DERIVING one of these. `secret_path` is filed there because it is likewise an
explicit, actionable declaration that emits a mount directly and takes no root — a concrete
peer, not an abstraction.

⚑ **D2 carve-out:** a group whose concrete members are ALL `secret_path` is NOT a row-1
collision. Its dest is `SECRET_MOUNT_DIR/{VAR}` by construction, so two such entries at one
dest are always the SAME VAR arriving from two scopes — the ordinary per-VAR cascade that spec
§2a documents as a FEATURE ("a box `secret_path.<VAR>` overrides a workset's pointer for the
same VAR"), not two different names contending for one destination.

## `ReconciledCategories` is GONE, and so is the pass that returned it

Cutover 6-R3 deleted `reconcile_categories`, `ReconciledCategories` and the three group resolvers
beneath them. There is no single cross-scope pass any more. §0's table is applied by the per-scope
`store_shape` PRODUCER (rows 3, 5 and row 1 SAME-scope), by the assembly COLLAPSE (rows 2, 4 and
row 1 cross-scope), and by this module's two launch-seam functions for the inputs the collapse
cannot see — `secret_path_deliveries` (a `secret_path` dest has no arm in the store shape) and
`narrow_table_winners` (a narrow resolve, where the collapse returns early).

⚑ **The class went for the same reason its `warnings` field went before it.** That field carried the
§0 row-5 ambiguities until cutover 5-1c, when the per-scope producer became the sole builder; two
feeds of one channel printed one line only because both arms happened to build an EQUAL
`CategoryCollision` and the emitter memoises on `(box_dest, scope)` — a property of the two
constructions, not of the channel. Making a second implementation UNAVAILABLE rather than unused
(P3) is what stops it drifting back; re-adding one now means re-adding a function and a type, which
is a visible design act. Full reasoning and the mutation proof:
`llm-docs/kanibako/commands/start.py.md`, "The row-5 warning lives HERE, and ONLY here".

⚑ **`CategoryCollision` itself still lives in this module**, and that is not leftovers: its message
is spec §0's, written once, beside the two refusal texts (`raise_binding_vs_binding`,
`raise_extension_onto_occupied`) that the producer also raises. `store_shape` imports all three from
here. It is DEFINED here and BUILT there.

⚑ **Row 5's BEHAVIOUR is unchanged in this module.** "Proceed on the existing ordering" is still
`_resolve_mount_group`'s `_most_specific` pick, which is the same pick row 4 makes. Only the
announcement moved. Rows 1 and 3 still RAISE here, and the row-2 mask override still applies here —
none of that is 5-1c's.

## Mount options: one writer, one reader

`CategoryEntry.options` is a podman mount-option string. Exactly two functions know its grammar,
and they are a pair:

```_bind_options(category: str) -> str```
The category-default table — `bindings.ro` is `"ro"`, every other rw bind category
(`bindings.rw`, `caches`, `common`) gets `"Z,U"` (SELinux relabel + userns chown).

```is_read_only(options: str | None) -> bool```
True when *options* carries `ro` as a comma-separated TOKEN. `None` and `""` are False.

⚑ **`is_read_only` is a TOKEN test, never a substring test, and never an equality test.**

* **Not equality.** Two call sites used to ask `options == "ro"` directly — the launch path
  (`commands/start._emit_category_mounts`) and the `workset` binding display
  (`commands/workset_cmd`). That was correct only for as long as `_bind_options` emits exactly one
  of two literals. The collapse folds options into a comma list, and the moment a read-only entry
  is spelled `"ro,Z"` an equality test answers *rw*. In the launch path that answer is not a
  cosmetic mis-label: the rw arm is L7 **guarantee-create**, so a missing read-only source would be
  `mkdir`'d instead of dropped with a warning. The flip keeps arity, passes mypy, and turns no
  existing test red — which is exactly why the predicate exists rather than a second literal
  comparison.
* **Not substring.** `nodirop` and `prod` both CONTAIN `ro`. A substring test calls them read-only
  and skips the guarantee-create. `tests/test_seed_hostdest.py`
  (`TestReadOnlyIsDecidedByTokenNotEquality`) pins that negative on the live launch path;
  `tests/test_settings/test_mount_options.py` pins the predicate itself.

⚑ **Ask this question through the predicate, not by hand.** A new `== "ro"` anywhere is the same
defect re-introduced under a different name.

## Four tuples, and each answers a different question

The bind-shaped categories are the six whose value is a 2-/3-element
`[host_src, box_dest[, options]]` tuple. `masks` (a keyed list) and `env` (a scalar) have
bespoke key shapes handled separately. Conflating any two of the four tuples below is what
this split exists to prevent:

* **`_BIND_CATEGORIES`** — every bind-shaped category, i.e. which keys carry the tuple VALUE
  SHAPE. All six are declared keys, authored in YAML, delivered at launch. The other three
  tuples are ITS SUBSETS. NOTE the regex order it is listed in: `bindings.ro` / `bindings.rw`
  must precede a bare `bindings` (there is none), and `caches` / `seeded` / `common` / `synced`
  are distinct tokens. Listed longest-first so every alternation built from it is unambiguous.
* **`_TERMINAL_BIND_CATEGORIES`** — the ones where the CATEGORY KEY IS THE WHOLE KEY: its
  value is a map keyed by box DESTINATION, and a destination is DATA, so
  `<scope>.<category>.<name>` is NOT a key at all (R-5/R-6). No per-entry dotted spelling can
  be read, refused or set under one. ⚑ It is a MIRROR, NOT THE DEFINITION — the keyspace
  validator owns that, in `settings_keyspace.TERMINAL_CATEGORY_TAILS`, which also lists
  `masks` (a terminal category, but not bind-shaped, so it is absent here). It is DERIVED from
  `_BIND_CATEGORIES` rather than imported because this module is deliberately stdlib-only, and
  the two are PINNED EQUAL by
  `test_settings_keyspace.test_the_bind_shaped_terminal_mirror_cannot_drift` so the mirror
  cannot drift. ⚑ It FINISHED MOVING 2026-08-08c: it is now ALL SIX. A category is added here
  in the same pass that flips its parsing, never before.
* **`_NON_TERMINAL_BIND_CATEGORIES`** — the COMPLEMENT, derived, so "terminal" has exactly one
  definition here and this can never disagree with it: the ones that DO have a per-entry
  dotted key, so `config get` can read one at a dotted path, the positional-vs-key
  disambiguator must read the shape as a key, and the write verbs must be able to name one in
  a refusal. ⚑ MEMBERSHIP MOVES, THE QUESTION DOES NOT: as each category goes dest-keyed it
  moves to the terminal tuple and drops out of this one automatically. Nothing asserts anything
  about WHICH categories those are — and as of 2026-08-08c it is EMPTY, because all six moved.
  That is the DERIVATION working, not a special case: the per-entry dotted key does not exist
  for any bind-shaped category any more.
* **`SETTABLE_BIND_CATEGORIES`** — which bind-shaped categories may be named in a `config set`
  / `config reset` key. **EMPTY. All six are YAML-only.** It is kept as an EMPTY TUPLE rather
  than deleted because it is the ONE definition of "settable" that `RETIRED_BIND_CATEGORIES`
  is derived from, so the two can never drift and re-admitting a category would be a one-line
  edit here — plus a visible spec edit, because the keyspace is CLOSED.

⚑ **Nothing may build a regex ALTERNATION from `SETTABLE_BIND_CATEGORIES`:** `"|".join(())` is
the empty string, which yields a group that matches the EMPTY string and would accept
`system..foo`. Every alternation in the module is built from a tuple that cannot be empty.

### The CLI write route is retired for all six

Ruling DS-BL1 = (a), 2026-08-07g: *"accept the loss uniformly"*. `bindings.{ro,rw}` lost the
write route first (R-9, two steps) because their per-name key stopped existing; the other four
lost it by the uniform ruling rather than by shape, and it is a KNOWN, ACCEPTED user-surface
loss — a bind-shaped entry is authored in YAML, full stop. Every retired spelling stays
RECOGNISED so the verbs refuse it BY NAME instead of degrading to "unknown config key"; the
closed keyspace (spec §0) refuses, never accepts quietly. All six stay READABLE via
`config get`:

* `{system,workset,box}.<any-of-the-six>.<name>` — recognised by `SCOPE_BIND_KEY_RE`, refused
  by `config_keys.scope_bind_retired_error`.
* `agent.<node>.<any-of-the-six>.<name>` — recognised by the node-splitting
  `AGENT_BIND_KEY_RE` (the node segment may itself be dotted, so it cannot be folded into the
  regex above), refused by `config_keys.agent_node_bind_retired_error`. The `bindings` arms are
  ALSO matched by the narrower `config_keys._AGENT_NODE_BIND_RE`, which is the READ parser, not
  the recogniser.

⚑ **The READ route is the TERMINAL KEY, NOT THE ENTRY** (2026-08-08c). The per-entry dotted
spelling is not a key at ANY scope now, so `config get` reads `<scope>.<category>` — the WHOLE
dest-keyed map — through `settings_keyspace.is_terminal_category_tail`, exactly as it already
read `<scope>.bindings.ro` and `<scope>.masks`. ⚑ A per-entry spelling is therefore refused
WITHOUT a read to offer: the refusal names the terminal key and says the entry lives inside its
value, rather than promising a `config get` of the entry.

⚑ **The one exception is the AGENT-SCOPE TERMINAL READ.** `config get agent.<node>.<category>`
still resolves through `config_dest`'s known-broken `_CATEGORY` arm to the NOUN's settings file
rather than to `agents/<node>/agent.yaml`, so the value it returns is the wrong file's. That
arm's repointing is an OWED, separately-ruled pass — it moves `agent_file`'s address rule, the
per-agent file-shape SoT — and it is named here so nothing writes a message promising that
read works.

## The key-shape regexes: recognition is not resolution

⚠ **The AGENT scope is DISCRIMINATED and there is NO exception.** It is written
`agent.<agent>` (an explicit agent name) or `agent.default` (the agent tier's FALLBACK). A BARE
`agent.<category>.<name>` is NOT A KEY — the keyspace is CLOSED (spec §0), an undeclared key is
not a key, so these patterns must REFUSE it rather than quietly accept it. Do not "helpfully"
widen this back.

**`RETIRED_BIND_CATEGORIES`** is the categories NO scope may name in a set/reset key any more,
derived as the DIFFERENCE from `_BIND_CATEGORIES`, so "settable" has exactly one definition
(`SETTABLE_BIND_CATEGORIES`) and the regexes cannot drift apart. Since DS-BL1 = (a) that
difference is ALL SIX. It feeds BOTH scope regexes, so the file and agent doors cover the same
categories by DERIVATION rather than by two hand lists. ⚑ `config_keys._AGENT_NODE_BIND_RE`
also pins itself against this tuple, as a SUBSET rather than an equality, and that is a
MEASUREMENT: it is the agent-scope READ parser, and since S3 the agent file's address rule
reads EVERY category flat with the destination whole — so widening that parser would no longer
mis-address the other four, it would ADMIT them. `config_keys.agent_read_key_error` carves the
`bindings` arms out of the closed-keyspace read gate precisely because their per-entry READ
survived R-9, and the other four have no such carve-out (spec §0 — a per-entry spelling is not
a key at any scope). Recognition is derived here; resolution is not.

**`SCOPE_BIND_KEY_RE`** — `{system,workset,box}.<bind-shaped category>.<name>`, the RETIRED
FILE-scope bind route. It covers all six since DS-BL1 = (a) emptied `SETTABLE_BIND_CATEGORIES`;
it reads `RETIRED_BIND_CATEGORIES`, so it widened by DERIVATION, not by an edit. It exists ONLY
to be RECOGNISED and refused by name: the verbs call `config_keys.scope_bind_retired_error` on
it, `config get` reads the tuple through the slot it routes, and the `pref` value guard uses it
to keep refusing a scalar written at a bind-shaped target. ⚑ It deliberately does NOT cover the
AGENT scope — `agent.<node>`'s node segment must be split NON-GREEDILY and canonicalized
(`+` → `℘`) before anything else can be done with it, so the agent-scope spelling has its own
recogniser built from the SAME alternation.

**`AGENT_BIND_KEY_RE`** — `agent.<node>.<bind-shaped category>.<name>`, the AGENT-scope twin,
over the SAME derived alternation so the two scopes cannot come to cover different category
sets. It exists for ONE reason: to be RECOGNISED and refused BY NAME
(`config_keys.agent_node_bind_retired_error`, `config_keys.is_known_key`).

⚑ **The node is NON-GREEDY so the FIRST category segment splits node from name.** A DEST tail
may itself contain a DOT-PRECEDED category token (`caches.~/.caches.x` — measured: greedy
parses node=`claude.caches.~/`), and a greedy node would swallow everything up to the LAST one.
*(An earlier revision claimed node segments may contain dots — measured false 2026-08-15:
`parse_agent_ref` refuses `.` in an agent name outright. The split rule stands on the dest side
alone.)* An UNDISCRIMINATED `agent.<category>.<name>` therefore does NOT match: the agent tier
is discriminated (spec §0/§2d) and an undeclared spelling must stay unrecognised rather than be
quietly admitted.

⚑⚑ **Recognition only — it picks NO read route, and that separation is load-bearing.**
`config_keys._AGENT_NODE_BIND_RE` is the narrower parser that DOES pick one (the agent file's
address rule), and it covers the `bindings` arms alone because those are the only per-entry
spellings whose READ survived R-9. Recognising a spelling in order to REFUSE it is a different
job from resolving one, so it gets a different parser.

**`BIND_KEY_RE`** — the PER-ENTRY bind-shaped key shape, a NON-TERMINAL bind category's
`<scope>.<category>.<name>` at any scope (the file scopes AND the discriminated
`agent.<node>`).

⚑⚑ **It is NOT a "settable" shape any more.** Under DS-BL1 = (a) nothing bind-shaped is
CLI-settable, so an alternation over `SETTABLE_BIND_CATEGORIES` would be EMPTY — a
`(?P<category>)` group that matches the empty string and accepts `system..foo`. What its
consumers needed was the per-ENTRY shape, for a question that is not settability: does a
per-entry dotted key EXIST here? So it is built from the NON-TERMINAL complement; a terminal
arm is absent for the reason it always was — there is no per-entry spelling of one at all.

⚑⚑⚑ **It FAILS CLOSED, and since 2026-08-08c that is its only state:** the last non-terminal
category went dest-keyed, `_NON_TERMINAL_BIND_CATEGORIES` is `()`, and this compiles `(?!)` —
no per-entry key exists under any bind-shaped category, therefore nothing matches. An empty
ALTERNATION would instead have produced the degenerate `system..foo`-accepting pattern, which
is why the empty case is spelled explicitly rather than left to `"|".join`.

⚑ **The READ ROUTE this guard once owed its consumers is PAID**, and it was paid by ANSWERING
the question rather than by widening anything: a per-entry spelling is not a key at any scope,
so there is no per-entry read to route. What the consumers needed instead was RECOGNITION — so
the verbs refuse a retired spelling BY NAME (spec §0 refuses loudly, never quietly) and
`is_known_key` does not mistake it for a project name. That is `SCOPE_BIND_KEY_RE` at the file
scopes and `AGENT_BIND_KEY_RE` at the agent scope. ⚑ **Do not re-open this regex to restore a
refusal:** a match here would mean "this per-entry key exists", which is the one thing that is
no longer true.

**The remaining shapes.** `MASK_KEY_RE` matches `{scope}.masks`, a value-less category (a list
of box_dest paths); the KEY has no per-entry name and entries are expanded per list element,
with the name being the index. `ENV_KEY_RE` matches `{scope}.env.{VAR}`, a scalar env var whose
VAR may NOT contain dots. `SECRET_KEY_RE` matches `{scope}.secret_path.{VAR}` — the SECRET
category (spec §2a, 2026-07-06), a scalar host PATH keyed by the env VAR it delivers, with VAR
in the env-name shape and never dotted. `SECRET_VAR_RE` is that same bare-VAR shape enforced
AGAIN at launch emit: the VAR is interpolated into a generated `sh -c` export shim, so a VAR
that slipped past `config set` validation — a hand-edited YAML, or a future settable surface —
must be re-checked before it reaches the shell. Keep the two in sync.

`_RULE_CHANGE_RELEASE` is `1.8.0`, the release in which the §0 collision table replaced the
flat authority ladder. It is named ONCE: it appears in the migration-grade paragraph of every
error message whose OUTCOME changed (M-7), and nowhere else.

## `CategoryEntry` — what each field carries

One resolved scope-category entry, pre-collision-resolution. *category* is the category token;
*scope* is the KEY's scope; *box_dest* is the in-box destination (a guest path for path
categories, INCLUDING `secret_path`'s `SECRET_MOUNT_DIR/{VAR}`; the VAR name for `env`);
*host_src* is the resolved host source path, or `None` for the value-only categories `masks`
and `env`. For `secret_path`, *host_src* is the SCALAR host path, *box_dest* is
`SECRET_MOUNT_DIR/{VAR}` and *name* is the VAR the box-side shim exports. *delivery* is COPY /
MOUNT / ENV per the category. *options* carries mount flags (`"ro"` / `"Z,U"`) for MOUNT
entries — and for `env` entries it holds the resolved variable VALUE instead, since env carries
no path or mount flags and its *box_dest* is the variable NAME. *name* is the `<name>` leaf,
for diagnostics.

### `key_segments`, and why it is segments

*key_segments* is the DECLARATION KEY plus the ENTRY'S DESTINATION, ONE SEGMENT PER NODE —
`("agent", "claude", "common", "~/.claude/plugins")`, `("box", "bindings", "rw", "~/w")`.
⚑ It is not a keyspace key: since 2026-08-08c every bind-shaped category is TERMINAL, so the
part that IS a key ends at the category and the LAST segment is the map's DEST, which is data.
(It read `("agent", "claude", "common", "plugins")` while that per-name spelling was still a
key.) It carries the DISCRIMINATED spelling, so its agent segment names a real agent tier
(`agent.<agent>` / `agent.default`) and never the bare `agent.` form §0 forbids. It is DISTINCT
from *scope*, which is the BARE precedence token, and it replaces nothing: three consumers need
it and none can be served by *scope* + *category* + *name* —

* the derived-binding materialisation entry (`binding_derivations.<declaration-key>.<dest>`,
  the reserved internal snapshot node, R-8);
* every collision error / warning message (a message that named only the category would not
  tell a user which declaration to edit);
* the base-vs-extension provenance the §0 collision table reads.

⚑⚑ **SEGMENTS, NOT A DOTTED STRING, AND THAT IS THE POINT.** A destination routinely contains
`.` (`~/.cache/uv`, `/home/agent/.claude/plugins`), so a dotted spelling is AMBIGUOUS with the
key-path separator: the materialisation used to hand the joined form to a splitting installer
and the dest shattered into extra tree levels, and two dests whose shattered paths nest
(`~/.claude` under `~/.claude.json`) silently overwrote one another. Carrying segments makes
the split unnecessary rather than careful. The `key` property is the DOTTED spelling, DERIVED
— for messages and for matching, never for structure.

### `is_credential` and `optional`

*is_credential* tags an entry whose content is an agent CREDENTIAL. It is the hook
`gate_credential_delivery` (D-M4) keys off for `seeded` entries: a credential `seeded` copy is
suppressed when the box is PRIVATE (`deliver_creds` False), exactly as `synced` (always
credential-bearing) is. Core never sets it; the agent plugin marks its cred seeds (Phase 8).
Defaults to False.

*optional* marks a MOUNT whose SOURCE is legitimately allowed not to exist, so the emitter
DROPS it SILENTLY instead of warning (spec §2c "SKIP-IF-ABSENT"). The handbook's per-scope
chapter binds are the live users: a workset or box that has written no chapter is the NORMAL
case, and warning about it on every launch of almost every box is the noise that trains users
to ignore warnings. The ro-drop WARNING stays for every non-optional bind — it is the safety
net for a mis-pathed one and must not be softened globally. It is set by KEY NAME at the
emitter (`settings_launch.snapshot_category_entries(optional_keys=…)`), never by a resolve-time
`exists()` probe, because this module is PURE.

🛑 **Nothing reads *optional* any more** (cutover step 3, 2026-08-10). The emitter takes the
same policy as a DEST SET parameter — `optional` is a DECLARATION fact and the fold into
`CollapsedBind(src, opts)` has no room for it. The field is left standing deliberately; its
retirement is step 5's.

### There is no `dest_space` field, and its absence is the design

(2026-08-08c.) `box_dest` is a GUEST path for EVERY category, bind-shaped or copy-shaped —
spec §0 *"ONE DEST SPACE, TWO DELIVERIES"*. A COPY's guest dest is the SPELLING; the copy is
RESOLVED to a host path when it runs — a `seeded` dest by `container._guest_dest_to_host`, a
`synced` dest by `commands.start._synced_host_dest`, which resolves it through the bind that
covers it rather than under the box home (cutover 2b-3). Either way there are no longer two
namespaces to tell apart, nothing for an entry to carry, and every seam that groups entries
groups on the bare `box_dest`.

⚑ **What the retired field was for**, kept as the record of a real bug it closed: the §2a seed
layers used to spell their dest `@meta.box.path/home` — an ABSOLUTE HOST path. On a host whose
user home IS `/home/agent` (this project's own dev box, and the seadog LXC test envs) such a
path STARTS WITH the guest home prefix, so the guest translator mapped it BACK under the box
home and the copy landed where nothing reads it, silently reporting success. No prefix test
could tell the two apart, so the entry carried its space. The RESPELL removed the ambiguity at
the source instead: the seed dest is now `~/`, which needs no discriminator because it is not a
host path at all. ⚑ **Do not reintroduce a host-spelled dest** — the discriminator that made it
safe is gone.

### The `canon` naming trap

Quarantined on the dataclass because both spellings meet there: a box store holds TWO different
`canon` directories. `<box_dir>/home/canon` is the box's ASSEMBLED GUEST VIEW (`~/canon`,
delivered by the home bind). `<box_dir>/canon` (= the key `@box.canon`) is the box's
CONTRIBUTION root, whose `handbook/` is ONE CHAPTER bound RO at `~/canon/handbook/box`.
**`@box.canon` is NOT `~/canon`** — same word, adjacent paths, opposite directions of travel.

## The launch seam — the two questions the collapse does not answer

### `gate_credential_delivery`

Drops what a PRIVATE box must not receive (D-M4); public, pure and idempotent. ⚑ **This is the
ONLY spelling of the rule, and it is APPLIED ONCE** — at the launch seam
(`commands/start._resolve_launch_snapshot`), above EVERY consumer of the entry list: the
ASSEMBLY COLLAPSE and `launch_deliveries`. One application of one spelling is what makes those
routes describe the SAME private box; a second of either is how a credential reaches a box the
user made private.

### `path_depth`

Path-depth of a guest dest for the mount depth-sort, shallower first. Depth is the number of
non-empty path components: `~/` and `/` are shallowest, `~/workspace` deeper, `~/workspace/vault`
deeper still. Guest dests are already `@`-expanded (`~` → `/home/agent`) before reaching it. It
is PUBLIC because emission depth-sorts too — the collapsed bind map is dest-keyed and carries
NO order (`store_collapse.CollapsedBindings`), so `commands/start._emit_category_mounts` sorts
on this same key. One depth rule, two consumers; a second spelling would drift the podman mount
order away from the reconcile's on exactly the nested dests it exists to resolve.

### `LaunchDeliveries`

The launch seam's carrier for what the entry list delivers BESIDE the assembly collapse's mount
set: the arm's-length SECRET mounts and the dests the agent's own delivery binds land at. It is
built ONCE at the seam (`commands.start._resolve_launch_snapshot`) off the CREDENTIAL-GATED
entry list — the same list the collapse sees, so the two describe one box.

* *secrets* — the `secret_path` mounts the launch delivers (`secret_path_deliveries`: the
  per-VAR winners, minus what §0 gives to a `masks` at the same dest), in the emitter's order.
* *agent_dests* — the normalized dests carrying the agent's delivery binds (the emitter's
  SKIP-IF-ABSENT set). It is a PARAMETER of `launch_deliveries` rather than a filter written
  here: the predicate that decides what an agent delivery IS belongs to the launch emitter that
  applies the policy (`commands.start._is_agent_delivery`), and one spelling of it is the point.
* *narrow_bindings* — a NARROW resolve's whole mount product, and `None` on every other
  resolve. A narrow resolve describes an INJECTED TABLE, not a box, so the assembly collapse
  returns before writing `meta.assembly.bindings` and there is no collapsed map to emit from;
  this field is what a narrow caller emits instead. ⚑ It is `None` UNLESS THE CALLER ASKED —
  the seam builds it only for a resolve that named its table's dests
  (`commands.start._resolve_launch_snapshot`'s *narrow_bind_dests*), so the main path cannot
  reach a map it never requested (P3).
* *declared_by* — the FOLD's own `store_collapse.CollapsedStore.declared_by`: dest → the
  declaration key that took it, EMPTY on a narrow resolve (which folds no bind map at all). It
  is HANDED to `launch_deliveries` by the seam, never derived here — this function has no bind
  map to read one off. ⚑ **It rides this carrier because this carrier IS the resolve's one
  out-of-band channel**: a second channel for one map is the two-carriers defect, and the
  alternative — a fourth `meta.assembly` leaf — is the closed-keyspace addition
  `store_collapse` forbids by name. Its reader is `settings.config_display`, so that
  `box show --effective` can print the KEY that swallowed a declaration and not just the path.

🛑 **THE ENVIRONMENT IS NOT ON THIS CARRIER ANY MORE.** An `envs` list rode it until the env
slots became a collapse output: the variables are arbitrated by `store_collapse.collapse_env`
and read off `meta.assembly.env`, which carries the winner's value AND its provenance. A
second, un-arbitrated view of the same declarations is exactly the shape that let a per-VAR
contest be settled silently by a consumer's `dict.update`, so it is gone rather than kept
beside the leaf. `launch_deliveries` likewise applies NO `env` filter, and adding one back
would be a second route: the `env` rows leave through the collapse, off this same list, so the
variables a box receives and the mounts it receives are folded from one input.

🛑 **It is a RETURN VALUE, NEVER A SNAPSHOT KEY.** `meta.assembly.*` is a CLOSED set of
DECLARED leaves (spec §0 · the keyspace manifest ·
`settings_keyspace.DECLARED_META_ASSEMBLY_LEAVES`), and an UNDECLARED one would be installed
SILENTLY — `insert_segments` writes what it is handed, and only the config-verb path refuses an
undeclared key. Producer DESIGN §9.1's precedent governs: what is not a settings key is PASSED
function-to-function. Adding a field to the carrier is therefore cheap, and adding a leaf is a
keyspace change — which is what `meta.assembly.env` went through before the collapse could
write it.

### `secret_path_winners` and `secret_path_deliveries`

`secret_path_winners` is spec §2a's per-VAR cascade at the seam: *"a box `secret_path.<VAR>`
overrides a workset's pointer for the same VAR"*. Every `secret_path` entry's dest is
`SECRET_MOUNT_DIR/{VAR}` BY CONSTRUCTION, so a group sharing a dest is ONE VAR arriving from
several scopes — the ordinary per-VAR cascade, picked by `_most_specific` (scope precedence
first, then input order). It is the D2 carve-out made explicit: the retired by-dest reconcile
reached the same `_most_specific` call over the same set by falling THROUGH that carve-out, and
this function is where the pick lives now — one spelling of the scope order, at the launch
seam. The result is sorted on the mount depth-sort's key so it is byte-identical to the
reconciled mounts filtered to this category — one order, one consumer
(`commands.start._emit_secret_mounts`). ⚑ **P7 — what it does NOT decide:** it answers "which
pointer wins for each VAR", not "does anything ELSE contend for that dest". That CROSS-CATEGORY
question is `secret_path_deliveries`', which composes the two.

⚑⚑ **The §0 cross-category gate for secret dests lives in `secret_path_deliveries`** (cutover
6-R2), BECAUSE NOTHING ELSE HOLDS THE INPUTS. `secret_path` carries no arm in the disk-store
shape (producer DESIGN §7.4), so the assembly COLLAPSE never sees a secret and cannot answer
"does anything else contend for this destination"; the only other answer was inside the by-dest
reconcile, retired at 6-R3. So the rows that decided it are applied there, over the SAME entry
list and in the SAME order, restricted to the destinations a secret claims:

* **row 1 / row 3** — a `bindings.*` row, or an abstraction deriving one, aimed at
  `SECRET_MOUNT_DIR/<VAR>` REFUSES the launch through the same two public raisers, naming BOTH
  declarations. ⚑ Several `secret_path` rows at one dest are the documented per-VAR cascade and
  not a collision — the D2 carve-out, which is the CALLER's test in exactly the way
  `raise_binding_vs_binding` says it is.
* **row 2** — a `masks` at the dest takes it, and the VAR is simply not delivered. Hiding a
  bound path is a mask's whole job, and the tmpfs lands there either way, so a secret mounted
  beside it would be hidden anyway. SILENT, as it has been since the flat authority ladder put
  `masks` on top.

⚑ **EXACT DEST ONLY, and that is not a narrowing:** a bind or a mask over the secrets DIRECTORY
never contended with `SECRET_MOUNT_DIR/<VAR>` — the secret mounts inside it, deeper in the
depth-sort (MEASURED at 6-R2, both cases). 🛑 **The dest group is the WHOLE mount group,
INCLUDING the per-VAR losers**, so the refusal lists what the reconcile listed: a message that
named one participant of a two-participant collision would be worse than the one it replaces.

### `narrow_table_winners`

A NARROW resolve's mount winners: its OWN table's dests, one row each. A narrow resolve carries
only one injected table (`include_base_families=False`) but still resolves the user's whole
CASCADE, so a user's `bindings.*` / `caches` / `common` / `masks` declaration reaches it.
Emitting those is the D1 defect: the MAIN path already emits every one of them from the
collapse, so the narrow path mounted each a SECOND time and did it from RAW rows, defeating a
later-scope `masks` sweep the collapse had applied. Filtering to the table's own dests, read
from the rows that declare the binds, deletes the exposure rather than arbitrating it (P4): a
user declaration cannot collide inside a narrow resolve unless it names an internal dest
outright.

At a dest that IS the table's, §0 still decides, and the two rows it needs are the two a narrow
resolve has nobody else to get:

* **row 2** — a `masks` at the dest OVERRIDES the table's bind (hiding a bound path is its
  whole job). It is the COLLAPSE's rule, and the collapse returns early on a narrow resolve, so
  it is applied here.
* **row 1 / row 3** — anything else contending for a table dest is REFUSED by name. The
  per-scope producer (`settings.store_shape`) already raised both for a SAME-scope pair — it
  runs above the collapse's `whole_box` gate, so it runs on narrow resolves too — and what is
  left for this function is the CROSS-scope pair, which the collapse would have refused on a
  whole-box resolve and which nothing else sees here. A bare dest-filter would let both rows
  through into a dest-keyed map and resolve them by INSERTION ORDER, silently.

⚑ **No row-4/5 silent pick, deliberately.** Normally the table's own CONCRETE row occupies the
dest and a second abstraction meets row 3; where the table row was skip-gated (a helper source
that does not exist), two user abstractions could meet alone — and they are refused too,
because "two mounts at one dest are an error in every scope combination" is the ratified rule
and a narrow resolve has no cross-scope arbiter to defer to.

### `_most_specific`

The winner among same-layer peers: SCOPE PRECEDENCE first, then input order. The scope order is
authoritative and the CALLER's list order must not be able to override it — it takes an
ARBITRARY list, and only the live adapter happens to hand it apply-ordered. Within one scope the
input order decides, LAST wins. ⚑ **ONE CALLER since 6-R3** — `secret_path_winners`, where the
rule it implements is spec §2a's per-VAR cascade. It was written for spec §0 row 4 as well, and
the retired by-dest reconcile used it for both; the row-4 pick now belongs to the assembly
collapse.

## The two §0 refusal texts

Both are PUBLIC because their rows are decidable inside ONE scope as well as across two, so
each has THREE callers: the per-scope `store_shape` producer, and the two launch-seam functions
above that hold inputs the collapse never sees — `secret_path_deliveries` and
`narrow_table_winners`. The messages are spec-mandated, so each is written ONCE, here; a second
remedy text is the drift this extraction exists to prevent.

* **`raise_binding_vs_binding`** — §0 row 1, two CONCRETE declarations at one `box_dest`. The
  D2 `secret_path` carve-out is the CALLER's test, not this function's: it decides whether the
  set in hand is a collision at all.
* **`raise_extension_onto_occupied`** — §0 row 3, an extension onto a base's `box_dest`. The
  BASE always survives, so the remedy names it without the row-1 "either one may be the one you
  keep" hedge.

**`_rule_changed`** renders the migration-grade paragraph (M-7), and ONLY on a rule whose
outcome changed. Putting it on a rule that did NOT change trains a reader to skip it, so a rule
the table left alone deliberately does not carry it.

**`_entry_lines`** renders `    <key>  ->  <host_src>` lines, key-column aligned.

### `_suppress_then_add` — the §0 remedy, spelled as the YAML edit it really is

⚑ **There is no CLI verb that can express THIS suppression.** `config set` writes a string
value, the category set path is source-only by contract, and `reset` REMOVES this file's own
override — the opposite operation, since it re-exposes the inherited entry. The one CLI
suppression channel, `set --null pref.<key>`, does not reach here either: a pref may be WRITTEN
only at workset/box (`settings_prefs.PREF_LEGAL_LEVELS`, enforced by `refuse_pref_table`) and
may TARGET only `system.agent` or `agent.*.**` (`settings_prefs.ALLOWLIST`), while the occupant
named here can be any category key of any scope. So the remedy is a hand edit of the settings
file that owns the key, and the message says so rather than naming a command that would not
work. It also names the SCOPE, because a box file may not suppress a containing scope's key by
writing that scope's top-level table (`settings_assemble._drop_upward_scopes` drops it) — the
edit belongs in that scope's own file.

*ambiguous* is True when the caller could not know WHICH entry the user wants to keep (row 1:
two peers, either is a legitimate choice), so the printed block is labelled an example rather
than a prescription. Row 3 passes False — there the occupant is determined, because the base
always survives.

⚑ **Segments, never a dotted split.** The LAST segment is the entry's DESTINATION, which is
data and routinely carries dots of its own (`~/.cache/uv`); splitting shattered it across YAML
levels and printed a block that is not a declaration at all.

⚑ **The AGENT-scope caveat.** An agent's own file has NO node level at all: it is that node's
file, so its root `self:` IS `agent.<node>` and the category table sits DIRECTLY under it — the
shape `_agent_partial` reads back since the S2 flatten. The canonical `agent.<node>` spelling
is what a CONTAINING scope's file writes as a defaults-down override. Printing one shape
without saying so would hand the reader an edit that silently does nothing in the other file.
⚑ The file's own spelling comes from the BOUNDARY (`agent_file.file_spelling`), never a literal
— same reason as the bind cure in `config_keys`: the caveat QUOTES the agent file at the user,
so the flatten changed it in ONE place. ⚑ And it quotes the TABLE, never the entry: the last
segment is the DEST, so a spelling that swallowed it would print something unreadable back.

## `derive_binding_keys` — the materialised derived bindings

`common` / `caches` / `seeded` are ROOTED declarations that EXTEND `bindings.rw`; §0 requires
the binding each one produces to be materialised BESIDE the declaration so `--effective` can
show both and a reader can see WHY a mount exists. The entry is
`binding_derivations.<declaration-key>.<dest>` — R-8: the reserved INTERNAL node at the
snapshot root, NOT a key — mechanically one fixed prefix on the entry's own segments, so the
pairing cannot drift.

⚑ **Keyed by SEGMENTS**, and the installer that consumes this map (`KeyStore.insert_segments`)
splits nothing. The last segment is the entry's DESTINATION, which is DATA and routinely
contains `.`; the dotted spelling that used to be returned here shattered such a dest across
tree levels and could silently overwrite a sibling derivation.

⚑ **It is deliberately NOT written into `<scope>.bindings.rw.<name>`,** which is §0's own
ruling. Be precise about WHY, because the obvious argument is wrong: this map does not feed
back into the entry list, so filing it under `bindings.rw` would NOT break row 3 at runtime —
the reconcile never reads it. What it would break is MEANING. The concrete category is the
layer §0 calls the source of truth; a key sitting in it that no user wrote and that emits no
mount is a FORGERY of the one thing the table reads. Every reader — `config show`, the
`--effective` block, a future validator, the next person to add a consumer — would have to
learn a rule ("some `bindings.rw.*` are real and some are shadows") that the
`binding_derivations` prefix states structurally and for free. The node is also not a key at
all, and is unwritable by TWO protections: the config verbs refuse the head (the closed head
dispatch, R-8) and assembly DROPS a file-borne top-level table with a warning
(`settings_assemble._drop_upward_scopes`) — exactly the status a derivation has.

It is PURE: it takes the adapter's entry list and returns a fresh `{key: Bind}` map; the ONE
seam that installs it into a snapshot is `commands.start._resolve_launch_snapshot`. Every
abstract entry that survived the credential gate gets one, WINNERS AND LOSERS ALIKE — a losing
declaration's derivation is exactly what explains the warning that names it, so hiding it would
defeat the purpose. `seeded` derives a COPY rather than a mount; the resolved pair is
identical, only the delivery differs.

⚑ Distinct by NAME from the READ lens `settings_views.derived_bindings` — one PRODUCES the
keys, the other READS them back off a snapshot.

## `declaration_delivery` — the COPY/MOUNT answer, beside the table that holds it

Parses a declaration KEY by POSITION and reads `_DELIVERY`. The category is the segment after
the scope, and the AGENT scope is DISCRIMINATED — two segments (`agent.<tier>`) where every
other scope is one — so position, not substring search: a trailing DESTINATION that happens to
spell a category (`box.caches.common`) must not be misread as the category.

⚑ It was `config_display._declaration_delivery` until the `--effective` pairing landed
(2026-08-26). It moved here because a SECOND caller appeared and `_DELIVERY` lives here: a
renderer keeping its own copy would drift the moment a category moved between COPY and MOUNT,
and `seeded` deriving a COPY rather than a mount is precisely the distinction that would go.

## `effective_bindings_and_template_sources` — the declaration/delivery pairing

Returns `store_collapse.Derivation` rows, one per ABSTRACT declaration, sorted by declaration
key. It discharges keyspec `:88`: *"`--effective` shows BOTH the declaration and the derived
binding and a user can see WHY a mount exists."*

**The single source.** This is the ONE place the pairing is calculated. A caller that recomputes
its own answer is a second opinion about what the box sees, which is the failure `--effective`
exists to DETECT, not to commit. Nothing inside it re-folds anything either: the arbitrated
answer is READ off `meta.assembly.*`.

### It takes TWO inputs and both are load-bearing

| input | what it supplies | when it is written |
|---|---|---|
| `binding_derivations.<decl-key>.<dest>` | the DECLARATIONS | BEFORE arbitration, deliberately |
| `meta.assembly.{bindings,seeded,synced}` | what the box RECEIVES | AFTER the collapse |

**The obvious shortcut is WRONG, and it was measured wrong on 2026-08-23.** Reading the
installed `binding_derivations` node ALONE looks sufficient and is not. That node is populated
at `commands/start._resolve_launch_snapshot` **before** the credential gate and the collapse,
deliberately — *"a derived binding is a property of the DECLARATION, not of whether the box may
receive it"* — and `derive_binding_keys` materialises a row for WINNERS AND LOSERS ALIKE,
because a loser's derivation is what explains the warning that names it. So every row in the
node reads as a live mount. Measured: an `agent.claude.common` declaration under a box-scope
`masks` entry at the same dest collapses to `CollapsedBind(src=None)` — a mask sentinel, **no
mount** — while the node still described a live mount there. A display built on it asserted
`(mount)` for a mount that does not exist and did not print the mask. Collision rows 1/3/5
raise and surface as `category_error`; **the mask path is silent, which is the dangerous one.**

⚑ That episode's own lesson: it conflated *"the spec no longer FORBIDS this route"* with *"this
route is SOUND"*, and a green suite gave false cover because **the distinguishing test was never
written**. It is written now —
`tests/test_categories_live.py::TestTheNaiveReadIsWrong` states the disagreement between the two
leaves as a FACT, so a future rewrite cannot re-derive the display from the node and pass.

### Why nothing was added to `CollapsedBind` / `CollapsedCopy`

The stub's own diagnosis was that *"the collapse carries no declaration provenance"*, and that
is TRUE — but only for the REVERSE question. The obligation is DECLARATION → DELIVERY, and that
is answered by CONTAINMENT against the finished map (`store_collapse.covering_bind`): which dest
covers this declaration's dest, and what sits there. Given a collapsed bind, naming the
declaration that produced it is the OTHER direction, the one `_refuse_bind_over_bind`'s boarded
🐞 wants, and it still needs the producer shape change.

🛑 **And the tuples may not grow.** The keyspec declares `meta.assembly.bindings` as
`dict[guest_dest → (host_src, opts)]` (`:434`) and both copy leaves as
`list[(host_src, guest_dest, opts)]` (`:440`, `:450`). Those arities are NORMATIVE, and the env
leaf's `(value, scope, key)` (`:467`) shows the spec grants provenance in a tuple deliberately
where it means to. Widening either tuple to carry a declaration key would put the code in
contradiction with the spec.

### The two halves DO NOT share a shape, and that is not an accident

Bindings **COLLAPSE**: a destination is occupied by exactly one winner, so the answer is a
per-dest pick and the losers are visible only as an explanation of why. Template sources
**LAYER**: the seed arm is ordered and every layer contributes, later over earlier, so no member
may be dropped. The pairing keeps both: a MOUNT declaration is answered by containment against
the bind map, a COPY declaration by finding its own row in the concatenated copy lists, and a
declaration with no row is a LOSS (a §0 row-5 loser the producer dropped) rather than a silent
absence.

⚑ **What it deliberately does NOT claim: the ORDER copies apply in.** Rows come back sorted by
declaration key, which is not scope order. `meta.assembly.{seeded,synced}` remain the authority
for which layer lands last; this function says WHAT each declaration delivers, never WHEN.

### The outcome vocabulary

`store_collapse` owns it: `DERIVED_MOUNT` · `DERIVED_COPY` · `DERIVED_MASKED` ·
`DERIVED_SUPERSEDED` · `DERIVED_AMBIGUOUS` · `DERIVED_UNCOVERED`. Every one of them prints
something, and every LOSS prints WHERE — `Derivation.at` is the destination that actually
carries the outcome, which for a mask ABOVE the declaration is not a destination the user's own
key names. "No mount" without it is an answer a user cannot act on.

⚑ `DERIVED_AMBIGUOUS` exists because ONE tie is constructible from this feed and cannot be
settled by it: two same-scope abstractions at one dest whose SOURCES are equal. Row 5 drops one,
but the collapsed map records only a source, so it cannot say which of the two the surviving
mount came from. Saying so beats picking.

⚑ `DERIVED_UNCOVERED` is not "no mount" — it is "no map". A NARROW resolve writes no bindings
leaf (`commands/start._install_assembly_collapse`'s whole-box gate), and reporting every
declaration as unmounted there would be the same class of confident wrongness this function was
written to remove.
