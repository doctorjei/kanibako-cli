# The Collapse (roadmap step 6a)

The "grand unification": four per-scope `StoreShape`s plus the home bind, merged into ONE dest-keyed
bindings map and TWO scope-ordered copy LISTS. Scope order — `system`, `agent`, `workset`, `box` — IS
the precedence; a later scope beats an earlier one.

**Authority:** `designs/grand-unification-collapse-DESIGN.md` §2 (Jei's algorithm, verbatim) and
**§2a, which supersedes §2's head** · its §0 ruling 1 (parent-first per scope) · the SUBSUMPTION
RULES, verbatim at `designs/collapse-implementation-DESIGN.md` **§7**, with the worked refusability
table and the operations at **§8** · **§9, which rules that copies apply to the HOME bind ALONE**
(2026-08-09d) · `designs/store-shape-producer-DESIGN.md` §7 (the input arms) · the spec's
`meta.assembly.*` rows and the **2026-08-10b** amendment, which splits the copy output in two and
moves `synced` to the far end of the fold.

## Status: ADDITIVE, PURE and INFORMATION-ONLY

⚑⚑ Step 6 *"merges the **information**, but not the action"* — the roadmap's own words. This
function computes and returns. It drives no emission, executes no copy, changes no mount and
deletes nothing: `snapshot_category_entries` → `reconcile_categories` → emission is still the whole
live delivery path, including `reconcile_categories`' arbitration half, its `synced_vs_binding`
refusal and its row-5 warning channel.

Its one consumer is `start.py:_install_assembly_collapse`, which writes the result to
`meta.assembly.bindings` / `meta.assembly.seeded` / `meta.assembly.synced`. ⚑ **Of those three,
only `bindings` is read**: since 2a-3 `start.py:_launch_bind_map` emits mounts from it, falling back
to the reconciled rows when the collapse wrote none. **The two COPY leaves are read by nothing** —
their consumers move at 2b-2/2b-3 — so a change confined to `seeded`/`synced` alters nothing a box
receives today. The collapse REFUSES shapes the shipped route still accepts, so that seam catches
`SettingsError` and leaves the leaves it governs absent; the tightening lands at the CUTOVER.

## Three passes, in the ONE order that is a ruling

⚖️ **RULED 2026-08-10b: the two copy categories resolve at OPPOSITE ENDS of the fold**, and the
order below is the ruling itself — *"could we simply copy it last instead? After binds are done?"*

1. **the SEED pass** — every scope's `seed` arm, concatenated. It reads no binding at all.
2. **the MOUNT fold** — the bind and mask arms, over the home foundation.
3. **the SYNC pass** — every scope's `sync` arm, concatenated, against a bind map that is by now
   COMPLETE and IMMUTABLE.

⚑⚑ **Step 3 is a LOOKUP, not an arbitration.** Nothing is pruned, no mount is deleted, no copy
competes with a mount; the sync pass only READS the map, to refuse the one case below. The
2026-08-09d simplification is untouched.

### The seed pass: a copy applies to the HOME bind alone

⚖️ **RULED 2026-08-09d.** A seed's destination is always inside home and it resolves into the home
bind's source — the box home store — so no mount can arbitrate one. That makes the pass a plain
**concatenation** that completes BEFORE any binding or mask fold.

Consequences, all of them removals rather than patches:

* **no MOUNT prunes a copy.** A bind or mask at, above or beneath a copy's dest leaves it alone.
  Whether such a copy is then dead is a DELIVERY question, not a collapse-time one.
  * 🛑 **READ THE SCOPE OF THAT CLAUSE.** It is about MOUNTS arbitrating copies, which is what
    every "nothing is arbitrated at a destination" line in the spec and the manifest points at
    (the containment table IS the mount table). It has never spoken to **copy-vs-copy**, and there
    IS a copy-vs-copy rule — see *A sync OWNS its dest* below. Until 2026-08-11 this bullet read
    "nothing prunes a copy" flat, which invited exactly the wrong generalization.
* **a dest MAY repeat**, and that is the point: the layered `seeded` overlay is one row per scope,
  every one of them targeting `~`. A dest-keyed map would silently drop all but one layer. The
  later entry overwrites the earlier FILEWISE at apply time — already ruled, and not this
  function's job.
  * ⚑ **A dest may repeat WITHIN one scope too, and the INPUT arm had to be fixed to say so**
    (2026-08-11). The output list always allowed it, but `StoreShape.seed`/`.sync` were dest-keyed
    `BindMap`s, so a repeat inside one scope was dropped one layer EARLIER than this list — before
    the concatenation ever saw it. Both arms are now `CopyList`; see the `store_shape` llm-doc for
    the measurement, including what the live emitter can and cannot yet produce.
* **the copy never meets a mask**, so the copied-directory-onto-a-mask rule and the un-mask branch
  that went with it are both GONE.
* ⚑⚑ **the module no longer touches the filesystem.** The directory rule was decided by a live
  `Path(src).is_dir()`, which made one config refuse or permit according to whether the source
  happened to exist yet. `settings_categories.py:492` states the standard for its own module:
  *"never by a resolve-time `exists()` probe: this module is PURE."* The probe is removed by
  removing its cause.

**Its one error case:** a SEED whose dest is not inside home. Every seed shipped today is
home-relative by construction, so this is structural rather than a behaviour change — and it is
refused BY NAME, never dropped.

### A sync OWNS its dest: the one copy-vs-copy prune (`_sync_dests`, cutover 2b-2)

⚑ **This is a REPRODUCTION of shipped behaviour, not a new rule, and not a spec change.** No key
changes and no leaf shape changes. `settings_categories._resolve_copy_group` has always done it:

```python
synced = [e for e in copy_sub if e.category == "synced"]
if synced:
    return [_most_specific(synced)]
```

— at a dest both arms target, every `seeded` row is DROPPED and the sync wins. Its own docstring
states the stakes: *"a `synced` cred copy-sync is not a layer: it REPLACES whatever else copies to
that dest … it is the CREDENTIAL pick — getting it wrong copies the wrong credentials into the box,
silently."*

**Why it had to move here.** The collapse had no such rule while nothing read its output. Cutover
2b-2 pointed `_apply_init_seeds` at `meta.assembly.seeded`, which removed the arbiter from the seed
path — and the resulting failure is not cosmetic:

1. the seed runs FIRST (create, create-if-absent) via `shutil.copy2` — the file arm directly, the
   directory arm through `copy_tree`'s leaf `copy2` — and **`copy2` PRESERVES the source mtime**;
2. `start._synced_uptodate` skips the sync when `dest.st_mtime >= src.st_mtime`;
3. ⇒ **a seed source newer than the sync source permanently pins the SEED's bytes at a credential
   dest.** Silently. `_apply_synced_copies` staying on the reconciled route does not save it: the
   seed still writes first and the sync's own gate still skips.

**Why `collapse_seeded` and nowhere else.** It already receives the whole shape set, so the sync arm
is in hand **without a home bind** — which is what preserves the public signature that makes the
create-side resolve work at all (§2a, home is pid 0). And both doors run this one function
(`_install_assembly_collapse` calls it bare; `collapse_store_shapes` calls the same function), so a
rule placed anywhere else would prune the launch resolve and not the create path — the only path
that writes seeds.

**Two properties that are deliberate, and each has its own test:**

* **EXACT DEST EQUALITY, never containment.** `_resolve_copy_group` groups on the exact `box_dest`,
  so this matches it byte for byte. A seed *inside* a synced directory is unhandled there too;
  widening it here would be a new rule, and it is not one.
* **REFUSED FIRST, PRUNED SECOND.** `_refuse_seed_outside_home` runs on every seed row, pruned or
  not. A sync at the same dest must not quietly excuse a mis-declared seed dest.

⚑ Fixing the mtime gate instead was rejected: it would still write the seed's bytes to disk before
overwriting them (a transient wrong-credential file), and it would need a "was this written by a
seed" fact that nothing carries.

### The sync pass: LAST, and NOT home-only

⚑ **There is deliberately no home-only rule for `synced`.** A sync dest resolves through the
collapsed bindings to the innermost bind containing it — so a cred file inside a bound directory
lands in THAT BIND'S SOURCE, where the box can actually see it, instead of being written under home
and shadowed. Home is simply the pid-0 foundation among those binds. ⚑ Applying
`_refuse_seed_outside_home` to this arm too would reintroduce the rule his ordering deleted.

🛑 **That resolution is DELIVERY and is NOT done here.** The emitted row carries the GUEST dest,
exactly as a seed row does; the innermost-bind lookup lands at the CUTOVER's step 2.

**Its one error case:** a sync dest that EXACTLY EQUALS a bind dest. A file bind's dest IS the file,
so writing through it would replace the bound inode; strictly INSIDE a bind dest is fine and is the
normal case. ⚑ The rule is stated STRUCTURALLY — as dest equality rather than as "a file bind" —
because a PURE module cannot tell a file bind from a directory bind, and the probe that could is
gone for good. Broader than the file-bind case it is aimed at, narrower than a probe, and
**deliberately strict**: a refusal can be LOOSENED later without breaking a box that works today,
whereas tightening one cannot.

⚑ Exact equality is expressed as the dict lookup itself. Both sides are normalized dests, so no
containment predicate is involved and none was added — `_is_within` is inclusive of equality and
would answer a different question.

⚑ A sync at a MASK's exact point is NOT refused: `src = None` marks a mask, and the rule and its
inode rationale are about BINDINGS. Whether such a sync is then dead is a delivery question, like
the seed at a mask's point beside it.

## Home is pid 0

Home is not in any scope's `store_shape` and the producer must not emit it. It arrives as its own
`home_bind` parameter and seeds `combined_bindings` BEFORE the loop, so every other binding enters
as a CHILD of an already-collapsed entry. Two properties fall out of the existing rules, with no new
rule added:

* **nothing may subsume home** — a bind at `/home` (or at `/`) is an ancestor of the foundation, so
  the bind refusal catches it; a second bind AT home is the same refusal, equality being on the
  subsume side;
* **everything inside home nests freely, at any scope.**

⚖️ **RULED 2026-08-09d — that first property is ABSOLUTE and covers MASKS too.** It used to fall out
of the BIND refusal alone, which counts BINDINGS only, so a mask at `~` (or at `/`) swept the home
binding away and the box launched with no home. His words: *"of course we should prohibit masking
home directly or allowing a mask that would have home as a child path (ie that would shadow home)"*.
That one IS a rule added rather than a consequence, and it is the mask arm's second refusal.

The key is `normalize_bind_dest("~")` = `/home/agent`, not the literal `~`. A dest is a GUEST path
and the guest home is fixed machinery, so the foundation key normalizes like every other dest —
otherwise `~` and `/home/agent/...` would not compare and home would subsume nothing.

⚑ `home_bind`'s options are carried VERBATIM: the mode fold applies to the scoped arms, and home is
not in an arm. A copy's options are carried verbatim for the same reason.

⚑ The home refusal is written as CONTAINMENT and in the direction that is easy to get backwards:
it fires when HOME is at, or INSIDE, the arriving mask's dest. Home is every mask's parent, so the
opposite direction — or a bare comparative — would refuse every mask there is.

## Every dest is normalized at the point of use

`normalize_bind_dest` is idempotent and is applied at every producer and again on read, so calling
it here is a no-op on well-formed input and states the precondition rather than assuming it. It is
done at each point of use rather than in a `normalize_paths` pre-pass over the arms, and that is
load-bearing: a pre-pass rebuilding an arm's dict would let `~/x` and `/home/agent/x` in ONE arm
silently overwrite each other, where at the point of use the second one meets the first in
`combined_bindings` and raises the collision it actually is.

⚑⚑ A box destination is DATA. It is never `.split(".")`/rejoined — dests routinely contain dots.

## The containment test needs a separator guard

`startswith(dest)` is wrong in two directions and every rule here uses containment:

* `/home/agent/foobar` is NOT inside `/home/agent/foo`;
* `/home/agent-foo` is NOT inside `/home/agent` — which is also what keeps a sibling of home from
  passing the copy half's inside-home check.

So the test is `d == dest or d.startswith(dest.rstrip("/") + "/")`. `rstrip` makes a bare `/`
behave: it yields the prefix `/`, and everything is inside root.

⚑ **Equality is on the SUBSUME side, not in a branch of its own.** Jei's rules say *"same or parent
mount point"* in one breath, so `_is_within` is inclusive and the `d != dest` guards disappear with
it. Exactly ONE equality guard survives, in `_refuse_bind_under_mask`, and it states a RULE rather
than patching a predicate: a bind may take a mask's own point, and may only never sit inside one.

## The rules: one refusal set and one sweep, per arrival

⚑⚑ **The direction of prohibition is REVERSED between the two mounts** (his words). A mask is the
INVERSE of a bind, not its mirror.

| arriving | refuses | sweeps |
|---|---|---|
| **bind** | a mask that CONTAINS its dest (strictly) · a bind AT its dest or INSIDE it | everything at or inside its dest |
| **mask** | a mask AT its dest or CONTAINING it · HOME at its dest or INSIDE it | everything at or inside its dest |

* a **bind** may nest INSIDE a bind, and refuses its own kind at its point or inside it;
* a **mask** may CONTAIN a mask, and refuses its own kind at its point or containing it —
  *"a mask inside a mask is a void within a void"*;
* a **mask** may be a child of a bind; a **bind** may not be a child of a mask.

**Subsumed means REMOVED, not skipped.** One sweep expresses the bind replacing a mask at its point,
the mask replacing a bind at its point, and the mask subsuming a child mask — which is why the four
functions that used to encode those separately are gone (P4: a good representation deletes the code
that would otherwise enforce the rule).

## Two intra-scope sorts, in OPPOSITE directions

Neither answer may turn on the order keys happen to sit in a dict. Within a scope there is no
precedence to express, so a SORT — not a diagnostic — is the answer (ruling 1).

* **binds collapse PARENT-FIRST**, across BOTH `ro` and `rw` arms together, so a parent always lands
  before its children and intra-scope subsumption cannot arise at all. The refusal then fires
  exactly when a LATER SCOPE introduces a bind at or above an earlier scope's — the genuine
  cross-scope conflict. The sort is STABLE, so Jei's `ro`-before-`rw` walk survives at equal length:
  at one dest, the `ro` entry is the occupant the collision names.
* **masks collapse CHILD-FIRST** — the inverse order, for the inverted prohibition. A mask refuses a
  PARENT and subsumes a CHILD, so masks must arrive innermost-first or `{~/x, ~/x/y}` would raise
  while `{~/x/y, ~/x}` would not. Ruling 1's argument with its direction flipped.

Masks merge AFTER the scope's bind arms, so a mask and a binding at one dest in ONE scope is not an
error: the mask applies and the cure is not declaring it (S3, ruled).

## The mode fold: `opts` is a STRING

`opts.add(mode)` was set semantics in the draft, and `set.add` returns `None`. `opts` is a
comma-joined multi-token string at every boundary it touches — the user types it that way, it
stores that way, and `to_volume_arg` emits podman's own comma list — so the fold is a pure string
fold: split on `,`, strip, drop empties, append the token if absent, rejoin. Order-preserving (the
emitted `-v` string stays stable) and deduped (`bindings.ro` already carries `ro`, so folding the
`ro` mode onto it must not print it twice).

⚑⚑ **THE FOLD ADDS; IT NEVER STANDS IN FOR THE CATEGORY DEFAULT — because it never has to.** The
arms arrive with their options ALREADY CONCRETE (`StoreShape`'s own words), applied upstream at
`settings_launch._emit_bind`: `opts if opts is not None else _bind_options(category)`. The collapse
is DOWNSTREAM of that line, reading `CategoryEntry.options`, so a stored `None` — what
`parse_bind_map` records for a 1-element entry — never reaches `fold_opt` at all. An options-less
`bindings.rw` entry therefore arrives as `Z,U` and collapses to **`Z,U,rw`**; `Z,U` cannot be lost
here. ⚖️ Jei, 2026-08-10: *"We should have `Z,U` survive the collapse, yes. … we should just be
adding to it."*

🛑 **The phantom this paragraph exists to refuse.** `BindEntry.opts` is typed `str | None`, so the
TYPE cannot say the value is concrete by now; reading `fold_opt(entry.opts, mode)` in isolation and
substituting the STORED opts yields `fold_opt(None, "rw")` = `"rw"` and manufactures a regression in
which every options-less rw bind silently loses its SELinux relabel and userns chown. It was briefed
as a live defect once and does not exist. The measurement that settles it is a one-line mutant:
delete the default at `_emit_bind` and the collapsed value drops to `"rw"` — i.e. the default was
reaching the collapse through that line the whole time. Pinned by
`tests/test_settings/test_mount_options.py::test_the_category_default_reaches_the_COLLAPSED_route_intact`.

* A deliberate `""` means *no mount options* and is NOT upgraded to a category default: it folds to
  exactly the mode token.
* ⚑ `opt_tokens` applies the same token rule as `settings_categories.is_read_only`, and the two
  cannot share an implementation: `settings_categories` is upstream of this module (via
  `store_shape`) and importing back would cycle. They are one rule spelled twice by necessity, so a
  change to either belongs in both.
* A per-entry override that contradicts its own arm — an `rw`-arm entry whose options carry `ro` —
  is REFUSED BY NAME rather than joined into a meaningless `"ro,rw"`.

## Case is NOT folded

The draft lowercased the copy dest before the prefix compare (and only one side of it). **Linux
paths are case-SENSITIVE**: folding would silently merge `/Home/x` and `/home/x` into one
destination. The call is dropped entirely, not repaired, and a test pins the two as distinct.

## What comes back

`CollapsedStore(bindings, seeded, synced)`.

* `bindings: dict[dest -> CollapsedBind(src, opts)]`, an ORDINARY dict — `SortedDict` is dropped.
  Its order is not its meaning: SCOPE order decided precedence while it was being built, and PATH
  order is emission's business (`settings_categories` depth-sorts shallowest-first).
* `seeded: list[CollapsedCopy(src, dest, opts)]`, in SCOPE order. The dest is no longer the key, so
  the entry CARRIES it. Not dest-sorted and not deduplicated: the order IS the overlay order.
* `synced: list[CollapsedCopy(src, dest, opts)]`, the same row type in the same SCOPE order. TWO
  lists rather than one tagged list because no consumer wants both: the two live appliers already
  filter, in opposite directions, and each would have had to filter again. ⚑ ONE list serves BOTH
  halves of sync — delivery host→guest at launch and writeback guest→host at session end — because
  a synced row spells both sides fully and takes no root, so DIRECTION IS A PROPERTY OF THE READ.
  That is why the reserved `meta.assembly.backup` leaf is retired rather than filled in.
* A MASK is `CollapsedBind(None, None)`. ⚑ `BindEntry.src` is NOT widened to `str | None` to carry
  it: that would relax the storage type for every consumer to serve the collapse's output shape.
  The second slot is DEAD, not reserved — a mask is a tmpfs with no host source and has no
  mount-option vocabulary.
* `seeded`/`synced` ARE COPIES AND STAY COPIES. The collapse changes key shape, never delivery.

## What the algorithm deliberately does not do

* **it does not resolve a sync dest through the bind map.** That is the DELIVERY half and it lands
  at the CUTOVER's step 2; here the row carries the guest dest.
* **it does not reproduce `_resolve_dest_group`'s `synced_vs_binding` refusal**, which the same
  ruling RETIRES: it existed because a copy could be shadowed by a live mount, and under copy-last
  the copy goes INTO the mount's source. The live route still raises it until the cutover deletes
  it. ⚑ Its stated replacement was the `mount_forbidden` backlog item; the actual replacement is
  the ORDERING, plus the exact-dest refusal above — so that backlog item is answered, not pending.
* ⚑ **it does not carry the live route's "a `synced` row REPLACES every other copy at a shared
  dest"** (`settings_categories._resolve_copy_group`). Two lists leave that rule no home in the
  collapse. The reading is that it falls out of TIME and POLARITY instead — a seed lands once at
  create, `if_absent`, and a sync overwrites at every launch — but that is a reading and the
  cutover owes it a measurement.

## The refusals, and what each one names

* **bind collision** — a bind at, or above, a dest a binding already occupies. ONE message for both,
  naming every colliding dest with the source bound there. It cannot reuse
  `settings_categories.raise_binding_vs_binding`: that one is written against `CategoryEntry`
  objects, and by here the entries are gone — a dest, a source and a mode are all that is left.
* **bind under a mask** — names the mask that would swallow it.
* **mask on a mask** — names every mask it lands on or inside.
* **mask over home** — names the offending dest and home's own, for a mask at home's point or above
  it. Refused BEFORE the sweep, so a mask that cannot be accepted deletes nothing first.
* **seed outside home** — names the source and the destination, points at the home bind, and offers
  `synced` as the category that is not home-only.
* **sync at a bind's exact dest** — names the sync's source, the shared destination and the source
  bound there, and points at "strictly inside" as the cure.
* **mode contradiction** — see the fold, above.
