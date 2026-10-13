# The Collapse

The "grand unification": four per-scope `StoreShape`s plus the home bind, merged into ONE dest-keyed
bindings map and TWO scope-ordered copy LISTS. Scope order — `system`, `agent`, `workset`, `box` — IS
the precedence; a later scope beats an earlier one.

**Authority:** `~/canon/workbook/designs/grand-unification-collapse-DESIGN.md` §2 (the algorithm,
verbatim) and **§2a, which supersedes §2's head** · its §0 ruling 1 (parent-first per scope) · the
SUBSUMPTION RULES, verbatim at `~/canon/workbook/designs/collapse-implementation-DESIGN.md` **§7**,
with the worked refusability table and the operations at **§8** · **§9, which rules that copies
apply to the HOME bind ALONE** · `~/canon/workbook/designs/store-shape-producer-DESIGN.md` §7 (the
input arms) · the spec's `meta.assembly.*` rows, which split the copy output in two and put
`synced` at the far end of the fold.

## This IS the delivery path, and it is PURE

⚑⚑ This function computes and returns: it drives no emission, executes no copy and touches no
filesystem. The live path is `snapshot_category_entries` → this collapse → emission, with the launch
seam answering separately for the two things the collapse cannot see (`secret_path` dests and a
narrow resolve's own table — `settings_categories.secret_path_deliveries` /
`narrow_table_winners`).

Its consumers are `start.py:_install_assembly_collapse`, which writes the result to
`meta.assembly.bindings` / `meta.assembly.seeded` / `meta.assembly.synced`, and
`workset share list --effective` (`commands.workset_cmd`). ⚑ **All three leaves are read** —
`_launch_bind_map`, `_launch_seed_list`, `_launch_synced_list` — and an ABSENT leaf on a whole-box
resolve is a NAMED error, not a fallback. This function's REFUSALS are the launch's: they propagate
out of the resolve and stop the box.

⚑ **A FOURTH LEAF, `meta.assembly.env`, is written by the same seam, and it is READ.**
`collapse_env` arbitrates the env VAR slots off the ENTRY LIST rather than the shape set — `env`
folds into no `StoreShape` arm, so the shapes do not carry it. **Both of its consumers read it** —
the launch's container env and `box show --effective`, through `start._launch_env_map` — so there is
no second view of a variable to disagree with this one. Its REFUSAL runs on every whole-box resolve,
whether or not anything reads the result.

⚑⚑ **AND THE PER-RUN `-e` IS APPLIED INSIDE IT.** `collapse_env(entries, cli_env)`
takes the launch's parsed `-e` map and, AFTER the containment walk, hands it to `_apply_cli_env`:
per variable, an owned slot has its VALUE replaced (provenance kept — the key still owns the
variable) and an unowned one gets a slot of its own with `("cli", "-e <VAR>")` provenance. ⚖️
system-design § "Delivery at launch", [Jei:2026-08-14] —
*"-e should override the key values, not the environment variables themselves"* — plus
[Jei:2026-08-14] *"e must win… so it must be part of the collapse"* (spec `env.<VAR>`:
*"`-e` beats a realization uniformly (the CLI level overrides the owning key — §1A)"*).

🛑 **AFTER the walk, never inside it.** `-e` has
no scope and must never CONTEST a slot: it overrides an owner or fills a vacancy, so no refusal can
name it. Ride it as a settings LEVEL instead and it must spell a concrete scope (`key_validity`
refuses `cli.env.FOO` and the scope-less `env.FOO`), at which point a `-e` naming a variable the
user's own key already names becomes a two-scope twin and `_refuse_env_twin` refuses the launch —
the opposite of the ruling. The four scopes are still the only things that can contend for a slot.

⚑⚑ **AND THE ENTRY LIST CARRIES THE TARGET'S REALIZATIONS — with nothing here to receive them,
which is the point.** goose's `GOOSE_MODE`/`GOOSE_MODEL`/`GOOSE_PROVIDER`/`OPENAI_HOST` and
claude's `ANTHROPIC_BASE_URL` are not pasted onto the finished environment:
`commands/start._install_realized_env` writes them into the snapshot as
`agent.<node>.env.<VAR>` keys BEFORE `snapshot_category_entries`, so they arrive here as ordinary
agent-scope ENV entries and `collapse_env` cannot tell them from a declared key. **That is what
makes the cross-scope case free**: a user's `box.env.GOOSE_MODE` against a realization is
`_refuse_env_twin`, the existing raise, with no realization-shaped branch anywhere in this module.
🛑 Do not add one. The SAME-scope case (a declared `agent.<node>.env.<VAR>` the §2d pick would merge
into the realization's own node) is refused at the install site instead, because it cannot reach
this walk as two entries — see `_refuse_realized_twin`, whose separate existence is about the CURE
(a realization has no key to move to) and not about a second arbitration.

⚑ **THE THIRD CONTESTED-VAR CASE IS NOT THIS MODULE'S EITHER, AND FOR A STRUCTURAL REASON.**
A `secret_path.<VAR>` fills the same in-box variable as an `env.<VAR>`, but its VALUE is never read
into kanibako (arm's-length delivery), so it can never become a `CollapsedEnv` and this walk sees
only the `env` half of that slot. Keyspec §2a refuses the pair — naming both keys, with no
precedence between the families — at the launch seam, in
`settings_categories.refuse_env_secret_twins`, which holds the whole entry list and therefore both
halves. 🛑 Do not move it in here by teaching the walk to read `secret_path` entries: that is the
seam's own reason for existing, and the `-e` exclusion above applies to it unchanged.

⚑ **No fourth `CollapsedEnv` field, deliberately.** An overridden slot carries no "cli" marker:
nothing displays env provenance today (`box show --effective` prints `env K = V`), so a fourth field
would cost a spec + manifest + closure change to say something no user can see. Adding a DEFAULTED
one later is additive.

## Three passes, in the ONE order that is a ruling

⚖️ **A ruling: the two copy categories resolve at OPPOSITE ENDS of the fold**, and the
order below is the ruling itself (system-design § "Collision arbitration"; [Jei:2026-08-10]) —
*"could we simply copy it last instead? After binds are done?"*

1. **the SEED pass** — every scope's `seed` arm, concatenated. It reads no binding at all.
2. **the MOUNT fold** — the bind and mask arms, over the home foundation.
3. **the SYNC pass** — every scope's `sync` arm, concatenated. It, too, reads no binding at all.

⚑⚑ **Step 3 touches the bind map NOT AT ALL** — ⚖️ system-design § "Collision arbitration"
([Jei:2026-08-12]), *"don't check for sync. Let it clobber whatever it wants."* It takes no bind
map, so the two copy arms are structurally identical passes. Nothing is pruned, no mount is
deleted, no copy competes with a mount, and step 3 is not even a lookup.

### The seed pass: a copy applies to the HOME bind alone

⚖️ **A ruling.** A seed's destination is always inside home and it resolves into the home
bind's source — the box home store — so no mount can arbitrate one. That makes the pass a plain
**concatenation** that completes BEFORE any binding or mask fold.

Consequences:

* **no MOUNT prunes a copy.** A bind or mask at, above or beneath a copy's dest leaves it alone.
  Whether such a copy is then dead is a DELIVERY question, not a collapse-time one.
  * ⚑ **AND NO COPY PRUNES A COPY EITHER**, by ruling. The section below records why: the hazard
    a copy-vs-copy prune would address is real, and it is closed at DELIVERY instead.
* **a dest MAY repeat**, and that is the point: the layered `seeded` overlay is one row per scope,
  every one of them targeting `~`. A dest-keyed map would silently drop all but one layer. The
  later entry overwrites the earlier FILEWISE at apply time — already ruled, and not this
  function's job.
  * ⚑ **A dest may repeat WITHIN one scope too**, so the INPUT arms `StoreShape.seed`/`.sync` are
    `CopyList`s, not dest-keyed maps; see the `store_shape` llm-doc for what the live emitter can
    produce.
* **the copy never meets a mask** here, so there is no copied-directory-onto-a-mask rule and no
  un-mask branch in the fold.
* ⚑⚑ **the module touches no filesystem.** A rule decided by a live `Path(src).is_dir()` would make
  one config refuse or permit according to whether the source happened to exist yet.

**Its one error case:** a SEED whose dest is not inside home. Every shipped seed is home-relative
by construction; a user seed outside home is refused BY NAME, never dropped.

### Nothing is arbitrated at a destination — including ACROSS the two arms

🛑 **NO SEED ROW IS PRUNED FOR SHARING A DEST WITH A SYNC — that is the ruling.** The question is
answered by a DELIVERY rule instead — *"at box creation, since that's the only time seeded is copied,
find the top-most bind in the bindings and write synced to it once at creation, irrespective of
date"*.

**The hazard, and where it is closed.** It is real and worth keeping in view:

1. the seed runs FIRST (create, create-if-absent) via `shutil.copy2` — the file arm directly, the
   directory arm through `copy_tree`'s leaf `copy2` — and **`copy2` PRESERVES the source mtime**;
2. `start._synced_uptodate` skips the sync when `dest.st_mtime >= src.st_mtime`;
3. ⇒ a seed source newer than the sync source would pin the SEED's bytes at a credential dest.

Step 2 is the actual defect: **an mtime comparison against the destination only means anything if the
destination was last written BY THE SYNC**. `start._sync_box_at_create` makes that true — it runs
immediately after the create-time seed, **UNGATED** (`skip_if=None`, his
*"irrespective of date"*), so from creation onward every sync dest holds sync-written bytes and the
launch gate compares against its own prior write. A prune would delete a declared copy to work
around a gate; this keeps the gate's own invariant instead.

**What that buys the keyspace.** `nothing is arbitrated at a destination` (system-design § *The
collapse*) holds across the two arms as well as within one: a dest may carry a seed row AND a sync row, both survive
the collapse, and DELIVERY ORDER decides — seed, then sync, at create. A user who declares both gets
both, in the order the two categories mean.

**REFUSED, not pruned.** `_refuse_seed_outside_home` runs on every seed row, and a sync sharing the
dest does not quietly excuse a mis-declared seed dest.

⚑ **Where the collapse is still the wrong place for this.** `collapse_seeded` is called BARE by the
create-side resolve (`_install_assembly_collapse`), which has no home bind, so it can never see a
bind map — the sync arm's dests are visible to it, but where those dests LAND is not. Delivery order
is a fact about the create path, and it belongs there.

### `refuse_uncovered_synced` — a copy destination must be covered by a mount

⚖️ **[Jei:2026-08-28]** — *"I don't think we should be checking for XDG; we should be checking
that the paths resolve. That's it."* A `synced` dest that no mount covers is refused.

**Why it is a refusal and not a warning.** `synced` resolves THROUGH the mount containing its dest,
so with nothing bound at or above it the copy is written into the container's own ephemeral storage
and is gone when the box stops. A `synced` row is a credential more often than not, so a skip
would produce a box that starts and then fails to authenticate inside the harness, naming nothing.

⚑ **IT CHECKS THE CONDITION, NEVER A CAUSE.** Refusing a `{$XDG_*}` TOKEN on sight is wrong in both
directions: it missed a literal `/data/z` (identically
broken, no variable anywhere) and it refused a user deliberately mirroring the box's layout under a
bind of their own (not broken at all). Coverage answers both correctly without naming XDG.

🛑 **A MASK COUNTS AS A COVER — a delegation, not an exemption.** A mask is IN the map, so
`covering_bind` finds it; what a mask does to a copy is spec §0's two copy rows, enforced by
`commands.start._refuse_synced_under_mask`. Treating a mask as "no cover" would put two refusals on
one destination with two different messages, and would refuse the file-at-a-mask's-own-point cell
the table ACCEPTS.

⚑ **`seeded` IS NOT ASKED HERE, because it is asked already** — see `_refuse_seed_outside_home`
below. Same invariant, two moments.

⚑ **THE CALLER IS THE LAUNCH SEAM, NOT `_collapse_synced`, AND THAT IS MEASURED.** Every other
refusal in this module is MONOTONE in the scope set: adding a scope can create a conflict, never
dissolve one. Coverage runs the other way — a further scope can only ADD binds. The fold has a
second caller that builds a deliberately partial map (`commands.workset_cmd` previews a working set
with no box tier and a stand-in home), and there a `workset.synced` at `/opt/cred` reads UNCOVERED
while the launch that also carries `box.bindings.rw./opt` reads COVERED. Refusing inside the fold
would make a listing refuse a configuration that launches. So the rule lives here with its siblings
and is asked from `commands.start._install_assembly_collapse`, inside the `whole_box` region, which
is the first point that can honestly say the map is a whole box's.

⚑ **§0 STATES IT.** The coverage refusal is listed in its own right — *"an arriving `synced` copy
is refused if NO mount COVERS its destination — nothing would persist it"* — and the `synced` bullet
names it beside the two mask refusals. This function is what MAKES coverage true.

### The sync pass: NOT home-only, and it arbitrates NOTHING

⚑ **There is deliberately no home-only rule for `synced`.** A sync dest resolves through the
collapsed bindings to the innermost bind containing it — so a cred file inside a bound directory
lands in THAT BIND'S SOURCE, where the box can actually see it, instead of being written under home
and shadowed. Home is simply the pid-0 foundation among those binds. ⚑ Applying
`_refuse_seed_outside_home` to this arm too would reintroduce the rule his ordering deleted.

🛑 **That resolution is DELIVERY and is NOT done here.** The emitted row carries the GUEST dest,
exactly as a seed row does; `commands.start._synced_host_dest` does the innermost-bind lookup.

**It has NO error case.** ⚖️ **system-design § "Collision arbitration"** ([Jei:2026-08-12]) —
*"don't check for sync. Let it clobber whatever it wants."* A sync dest that EXACTLY EQUALS a bind
dest is not refused, even for a file bind.

⚑ **The file-bind concern is narrow; a blanket refusal is not the ruling.** The consistent ruling is
the opposite — *"sometimes we want to copy onto a bind"*, *"copy | bind, same, OK"*, *"copy | bind copies on top of the bind, and most of
bind remains intact"*, *"it's ok for a synced item to apply to the exact same root as a bind, just
like a copy can"*. At an exact-dest pair delivery resolves the sync through that very bind, so it
writes into the bind's own host source: it clobbers CONTENT, and the mount survives.

⚑ Because the arm arbitrates nothing, the mask case needs no carve-out either: a sync at a MASK's
exact point is accepted for the same reason as everything else. Whether such a sync then stands is a DELIVERY question, and the delivery half answers
it with spec §0's copy rows: `start._refuse_synced_under_mask` RAISES on the two the table refuses (a
mask as the copy's PARENT, a DIRECTORY at a mask's own point), while `start._synced_host_dest` warns
and skips the residue the table does not name — no cover, a read-only cover, and a mask at the dest's
own point over a missing source.

⚑ **§0 AGREES:** a copy at a bind's point is *"ok — append"*, and the section says outright *"do not
reintroduce a `synced`-vs-binding refusal at an exact destination"*.

## Home is pid 0

Home is not in any scope's `store_shape` and the producer must not emit it. It arrives as its own
`home_bind` parameter and seeds `combined_bindings` BEFORE the loop, so every other binding enters
as a CHILD of an already-collapsed entry. Two properties fall out of the existing rules, with no new
rule added:

* **nothing may subsume home** — a bind at `/home` (or at `/`) is an ancestor of the foundation, so
  the bind refusal catches it; a second bind AT home is the same refusal, equality being on the
  subsume side;
* **everything inside home nests freely, at any scope.**

⚖️ **[Jei:2026-08-09d] — that first property is ABSOLUTE and covers MASKS too.** The BIND refusal
counts BINDINGS only, so without a rule of its own a mask at `~` (or at `/`) would sweep the home
binding away and the box would launch with no home. The ruling: *"of course we should prohibit
masking home directly or allowing a mask that would have home as a child path (ie that would shadow
home)"*. That one IS a rule added rather than a consequence, and it is the mask arm's second
refusal.

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

⚑ **Equality is on the SUBSUME side, not in a branch of its own.** The rules say *"same or parent
mount point"* in one breath, so `is_within` is inclusive and the `d != dest` guards disappear with
it. Exactly ONE equality guard survives, in `_refuse_bind_under_mask`, and it states a RULE rather
than patching a predicate: a bind may take a mask's own point, and may only never sit inside one.

## The rules: one refusal set and one sweep, per arrival

⚑⚑ **The direction of prohibition is REVERSED between the two mounts** [Jei:2026-08-09d]. A mask is
the INVERSE of a bind, not its mirror.

| arriving | refuses | sweeps |
|---|---|---|
| **bind** | a mask that CONTAINS its dest (strictly) · a bind AT its dest or INSIDE it | everything at or inside its dest |
| **mask** | a mask AT its dest or CONTAINING it · HOME at its dest or INSIDE it | everything at or inside its dest |

* a **bind** may nest INSIDE a bind, and refuses its own kind at its point or inside it;
* a **mask** may CONTAIN a mask, and refuses its own kind at its point or containing it —
  *"a mask inside a mask is a void within a void"*;
* a **mask** may be a child of a bind; a **bind** may not be a child of a mask.

**Subsumed means REMOVED, not skipped.** One sweep expresses the bind replacing a mask at its point,
the mask replacing a bind at its point, and the mask subsuming a child mask, so no separate function
encodes each case (P4: a good representation deletes the code that would otherwise enforce the
rule).

## Two intra-scope sorts, in OPPOSITE directions

Neither answer may turn on the order keys happen to sit in a dict. Within a scope there is no
precedence to express, so a SORT — not a diagnostic — is the answer (collapse DESIGN §0 ruling 1).

* **binds collapse PARENT-FIRST**, across BOTH `ro` and `rw` arms together, so a parent always lands
  before its children and intra-scope subsumption cannot arise at all. The refusal then fires
  exactly when a LATER SCOPE introduces a bind at or above an earlier scope's — the genuine
  cross-scope conflict. The sort is STABLE, so the `ro`-before-`rw` walk survives at equal length:
  at one dest, the `ro` entry is the occupant the collision names.
* **masks collapse CHILD-FIRST** — the inverse order, for the inverted prohibition. A mask refuses a
  PARENT and subsumes a CHILD, so masks must arrive innermost-first or `{~/x, ~/x/y}` would raise
  while `{~/x/y, ~/x}` would not. The §0 ruling 1 argument with its direction flipped.

Masks merge AFTER the scope's bind arms, so a mask and a binding at one dest in ONE scope is not an
error: the mask applies and the cure is not declaring it (S3, ruled).

## The mode fold: `opts` is a STRING

`opts` is not a set (`set.add` returns `None`). It is a
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
here. ⚖️ [Jei:2026-08-10]: *"We should have `Z,U` survive the collapse, yes. … we should just be
adding to it."*

🛑 **The phantom this paragraph exists to refuse.** `BindEntry.opts` is typed `str | None`, so the
TYPE cannot say the value is concrete by now; reading `fold_opt(entry.opts, mode)` in isolation and
substituting the STORED opts yields `fold_opt(None, "rw")` = `"rw"` and manufactures a regression in
which every options-less rw bind silently loses its SELinux relabel and userns chown. That defect
does not exist. The measurement that settles it is a one-line mutant:
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

* `bindings: dict[dest -> CollapsedBind(src, opts)]`, an ORDINARY dict, not a `SortedDict`.
  Its order is not its meaning: SCOPE order decided precedence while it was being built, and PATH
  order is emission's business (`settings_categories` depth-sorts shallowest-first).
* `seeded: list[CollapsedCopy(src, dest, opts)]`, in SCOPE order. The dest is not the key, so
  the entry CARRIES it. Not dest-sorted and not deduplicated: the order IS the overlay order.
* `synced: list[CollapsedCopy(src, dest, opts)]`, the same row type in the same SCOPE order. TWO
  lists rather than one tagged list because no consumer wants both: the two live appliers already
  filter, in opposite directions, and each would have to filter again. ⚑ ONE list serves BOTH
  halves of sync — delivery host→guest at launch and writeback guest→host at session end — because
  a synced row spells both sides fully and takes no root, so DIRECTION IS A PROPERTY OF THE READ.
  That is why there is no `meta.assembly.backup` leaf.
* A MASK is `CollapsedBind(None, None)`. ⚑ `BindEntry.src` is NOT widened to `str | None` to carry
  it: that would relax the storage type for every consumer to serve the collapse's output shape.
  The second slot is DEAD, not reserved — a mask is a tmpfs with no host source and has no
  mount-option vocabulary.
* `seeded`/`synced` ARE COPIES AND STAY COPIES. The collapse changes key shape, never delivery.

## What the algorithm deliberately does not do

* **it does not resolve a sync dest through the bind map.** That is the DELIVERY half
  (`commands.start._synced_host_dest`); here the row carries the guest dest.
* **it does not arbitrate a sync against the bind map AT ALL** — ⚖️ a ruling, *"don't check for
  sync. Let it clobber whatever it wants."* No stage refuses a `synced`↔`binding` pair, and
  `_collapse_synced` takes no `bindings` parameter. A sync at a bind's EXACT dest is ordinary:
  delivery resolves it through that bind into the bind's host source, so it overwrites CONTENT and
  *"most of bind remains intact"*.
  ⚑ **§0 AGREES:** *"A sync dest that EXACTLY EQUALS a bind dest is ACCEPTED — the copy lands ON TOP
  of the bind and writes through into that bind's source, and the bind remains."*

## The refusals, and what each one names

* **bind collision** — a bind at, or above, a dest a binding already occupies. ONE message for both,
  naming every colliding dest with the source bound there. It cannot reuse
  `settings_categories.raise_binding_vs_binding`: that one is written against `CategoryEntry`
  objects, and by here the entries are gone — a dest, a source and a mode are all that is left.

  ⚑⚑ **Its REMEDY sentence is `raise_binding_vs_binding`'s — now the SAME OBJECT, not a matching
  copy:** `settings_categories.SUPPRESS_THEN_ADD`, which the two mask refusals below also take and
  which `_suppress_then_add` WRAPS for its YAML block (and MIGRATION.md § "Mount collisions are now hard
  errors" ships that text): suppress the entry you do not want, then declare the one you do; an override is
  not enough, because these are two different keys and both survive the cascade; set the unwanted key
  to null in the settings file for its scope. The refusal is fatal, so this is a user's ONLY
  diagnostic for the arrangement. A user who meets one of these has met the other, so two spellings
  of one cure would send them to two mechanisms.

  ⚑ **"Suppress" is a present-`None` at the key**, resolved to an OMIT at cascade merge, entirely
  upstream of this module. It is NOT masking, and a scope clause about masks would send the user to
  the wrong mechanism.

  ⚑ **It names BOTH participants by KEY** — the arriving declaration and each subsumed one —
  which is what system-design § *Collision arbitration* obliges. The remedy above tells the reader to null a KEY, so a
  message naming none would ask for an edit without saying where to make it. The key travels
  BESIDE the shape, off the ENTRY LIST —
  see "Why nothing was added to `CollapsedBind` / `CollapsedCopy`" below — and widening the arm's
  entry tuple is barred by the spec, not merely awkward. Its sibling one layer up still publishes
  more: the owning scope and a copy-pasteable YAML block, which this module cannot render because it
  holds DOTTED keys rather than `key_segments`. ⚑ MEASURED: carrying segments instead would mean
  retyping `DeclaringKeys`, which `config_display`, `config_interface` and `commands/box/_parser`
  all read as `Mapping[str, str]` — so the BLOCK stays one carrier's, while the PROSE is shared.
  ⚑ **Its last sentence is `_which_to_keep`'s**: "Either declaration may be the one you keep",
  UNLESS a participant is an internal bind (spec §2c) — then `settings_categories.internal_bind_cure`,
  the ONE predicate and sentence every collision remedy shares (the two in `settings_categories`
  too), names it as not suppressible and sends the reader to the other declaration.
  `settings_launch` refuses a settings entry whose WRITTEN dest is internal; this is reached by
  NESTING (an earlier scope's bind inside an internal directory bind) and by a dest spelled through
  an `@`-ref or `$VAR`, which that check compares unexpanded.
* **bind under a mask** — names the mask that would swallow it, and both participants' keys, **and
  offers the cure**. ⚑ It fires hardest CROSS-SCOPE, where *"do not declare the mask"* would name
  an edit in a scope the reader may not own; present-`None` is the only route left them. 🛑 No "either one may be the one you keep" hedge: the occupant is DETERMINED.
* **mask on a mask** — names every mask it lands on or inside, and every one of their keys, **and
  offers the cure**. ⚑ The
  refusal that needs provenance most: NEITHER participant has a host source, so without the keys it
  would name two bare destinations and nothing a reader could match to a file they had written. ⚑ It is
  ALWAYS cross-scope — one scope's mask arm is dest-keyed, so two masks at one point cannot be built
  inside one scope — so *"declare one of them, not both"* alone could never be enough.
* **mask over home** — names the offending dest and its key, and home's own dest. 🛑 It names NO key
  for home and says why in the message: home is pid 0, built by the launch seam from the RO derived
  `meta.box.home` and in no scope's arm, so there is no bind-shaped key to suppress. Pointing a
  reader at a key they cannot write would be worse than silence. This is the one
  refusal with a single-sided diagnosis, and *"the refusal is symmetric; the diagnosis is not"*
  (system-design § *Collision arbitration*) is the sentence that licenses it
  it. Refused BEFORE the sweep, so a mask that cannot be accepted deletes nothing first.
* **seed outside home** — names the source and the destination, points at the home bind, and offers
  `synced` as the category that is not home-only.
* **mode contradiction** — see the fold, above.

⚑ **The sync arm contributes NO refusal to this list** (by ruling) — it is the one arm that
can raise nothing at all.

## The READER: `covering_bind` and `pair_declarations`

Everything above PRODUCES the collapsed map. These two READ it, and they are here because the
questions are the map's own — a second spelling elsewhere is how a display comes to disagree with
the box about which mount owns a path.

### `covering_bind(bindings, dest)` — the INNERMOST cover

The LONGEST prefix in the map that contains *dest*, or `None`. Longest = innermost = the mount the
box actually sees at that path. Every candidate is a prefix of ONE string, so length totally orders
them and there is no tie to break. Separator-guarded through `is_within`, so `~/foobar` is not
inside `~/foo`.

⚑ **Its askers:** `commands/start._synced_cover` delegates to it in one line, for
`_refuse_synced_under_mask` (does the covering mount refuse this copy) and `_synced_host_dest`
(which mount does this copy resolve through); `pair_declarations` (what did this DECLARATION
actually get); and `refuse_uncovered_synced` (is this copy covered at all). A second lookup is how a row gets refused against one
mount and delivered through another.

⚑ It does not discriminate a mask from a bind. Deciding what a mask MEANS is the caller's, and every
caller does it with `is_mask` — one rule, one spelling.

### `pair_declarations(declarations, bindings, copies)` — declaration → what the box receives

PURE. Takes the DECLARATIONS and the ARBITRATED outputs (`meta.assembly.bindings` and the two copy
lists concatenated), and returns one `Derivation` per declaration. Nothing is re-folded: a second
fold is exactly the second opinion `--effective` exists to DETECT.

⚑ **THERE ARE TWO FEEDS OF DECLARATIONS, and the function is indifferent to which one it got.**
`box show --effective` feeds the ABSTRACT declarations off the reserved `binding_derivations` node
(via `settings_categories.effective_bindings_and_template_sources`) and pairs them against a live
box's `meta.assembly.*`. `kanibako workset share list --effective` feeds the workset's own CONCRETE
`bindings.{ro,rw}` entries and pairs them against a map it collapses on the spot from that one
workset file. Same question, two subjects — and one answer, which is the point.

**The input MAY contain arbitration losers, and it is meant to.** `derive_binding_keys` materializes
a derivation for winners and losers alike, deliberately. A loser is identified HERE, by what
occupies its destination — which is why the pairing, not the node, is the display's source.
See `llm-docs/kanibako/settings/settings_categories.py.md` for the measured failure that rule
comes from.

The decision, in full:

| the declaration | the outcome |
|---|---|
| delivery is COPY and a row matches `(dest, src)` | `DERIVED_COPY` |
| delivery is COPY and no row matches | `DERIVED_SUPERSEDED` — the producer dropped it (same-scope pair) |
| nothing covers the dest | `DERIVED_UNCOVERED` — no MAP, which is not "no mount" |
| the cover is a MASK, at the dest or above it | `DERIVED_MASKED` |
| the cover is elsewhere, or its src differs | `DERIVED_SUPERSEDED` |
| the cover IS the dest and the src matches, uniquely | `DERIVED_MOUNT` |
| …and a second declaration matches the same `(dest, src)` | `DERIVED_AMBIGUOUS` |

⚑ `Derivation.at` is the destination the outcome was found AT — the declaration's own when it holds
its point, an ANCESTOR when something above swallowed it. It is the field that turns "no mount" into
a diagnosis, and for a mask ABOVE the declaration it names a path the user's own key does not.

⚑ A mask AT the dest and a mask ABOVE it are ONE outcome, not two: a mask is a tmpfs with no host
source, so the box sees nothing at that path either way. The sweep case is the one a renderer
misses on its own — the declaration's dest is not in the collapsed map AT ALL, so a lookup by dest
finds nothing and "absent" reads as "fine".

### `derivation_result(row)` — one `Derivation` as the phrase a display prints

Every outcome prints something, and a LOSS prints WHY and WHERE. "No mount" alone is an answer a
user cannot act on; the destination that swallowed the declaration is the whole diagnosis, and for a
mask ABOVE it that destination is not one the user's own key names.

⚑ **IT LIVES HERE, beside the `DERIVED_*` constants it names, because there are two displays:**
`box show --effective` (`config_display`) and `commands/workset_cmd._print_effective_shares`. One
sentence about what a mask did to a declaration is not a thing to keep
two copies of — two carriers of one shape is the defect class this whole section exists because of.

⚑ It takes `Any`, not `Derivation`: the optional halves of that tuple are decided BY the outcome,
and each branch reads only the half its own outcome guarantees.

⚑ **The second argument is the fold's `declared_by`, and BOTH loss phrases take it** — the mask
("the mask **declared by `'box.masks[/opt/x]'`** at …") and the superseding binding alike. It is
OPTIONAL because a caller must be HANDED the map: it cannot be recovered from the collapsed leaf,
which carries no key. Omitted, every phrase is byte-identical to what it was. The COPY-loss branch
is the one genuine exception and says why in place — the taker there is another copy row, and the
side map records mounts.

⚑ The MOUNT phrase is this function's, but a display may render a delivered row in its own
vocabulary and call in only for the rest — `workset share list --effective` does, because its column
is the share's `ro`/`rw` MODE and not the collapsed mount options. That is a FORMAT choice, never a
decision: the outcome is still `pair_declarations`'.

### 🛑 Why nothing was added to `CollapsedBind` / `CollapsedCopy`

The pairing is asked in ONE direction, declaration → delivery, and containment answers it.

The REVERSE question — given a collapsed dest, name the declaration that put a mount there — is
answered by **`CollapsedStore.declared_by`**, a dest-keyed SIDE MAP built off the ENTRY LIST and
recorded AT THE FOLD. ⚑ Its values are `CategoryEntry.label`s (`box.masks[/opt/x]`), the spelling
`settings_prefs.pref_entry_keys` yields, so `commands.start._annotate_pref_origin` can match a
refusal here against the `pref` that installed the entry; a dotted `.key` would never match.
`collapse_store_shapes` takes that list as an OPTIONAL third argument; a
caller that omits it collapses byte-identically and files nothing, which is why every message here
still has a bare spelling. The launch seam passes it (`commands.start._install_assembly_collapse`),
and so does `workset share list --effective`; both displays then print from it.

⚑ **Off the ENTRY LIST, because that is the only seam that has the keys** — the same one
`collapse_env` takes, and for the same reason: `store_shape.build_store_shape` drops
`CategoryEntry.key_segments` when it writes the arm. An entry is filed only when the scope's shape
holds the arm row it produced, so `store_shape`'s category→arm table is not copied here and the
same-scope pair's WINNER is filed rather than the loser the producer dropped.

⚑ **Recorded at the FOLD, never derived from the finished map.** "Which scope's mask survived at
this dest" is not answerable afterwards: a bind may take a mask's own point, the sweep removes that
mask, and a LATER scope's mask may retake the point — two scopes' keys then name one dest and only
one of them is the occupant. The sweep drops the side map's row with the mount it describes, so a
key can never outlive the mount it named.

🛑 **`declared_by` IS NOT A FOURTH `meta.assembly` LEAF and must not become one.** The three leaves
are written field by field at the seam; nothing carries this map into the store. Landing it as a
leaf would be a closed-keyspace addition — a spec and manifest edit, and not the code's to make.

⚑ **So it is THREADED instead, and BOTH displays get it.** `_install_assembly_collapse` RETURNS the
map beside the leaves it writes; `_resolve_launch_snapshot` puts it on
`settings_categories.LaunchDeliveries.declared_by`, the resolve's one out-of-band return channel;
`box show --effective` — which builds its snapshot IN PROCESS, per command — takes it from there and
hands it to `derivation_result`. ⚑ The map that display prints from is therefore the SAME fold's as
the `meta.assembly.bindings` it pairs against, which is the only way the key and the mount can be
one answer. 🛑 **What the leaf cannot do is carry the key ITSELF** — a caller holding nothing but a
snapshot still has no key to give, and re-deriving one from the finished map is impossible for the
reason above.

And the tuples may not grow regardless: the keyspec declares `meta.assembly.bindings` as
`dict[guest_dest → (host_src, opts)]` and both copy leaves as
`list[(host_src, guest_dest, opts)]` (§1A's `meta.assembly.*` rows). Those arities are NORMATIVE —
and the env leaf's `(value, scope, key)` shows the spec grants provenance in a tuple deliberately where
it means to.
