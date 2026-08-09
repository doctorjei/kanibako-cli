# The Collapse (roadmap step 6a)

The "grand unification": four per-scope `StoreShape`s plus the home bind, merged into ONE dest-keyed
bindings map and ONE scope-ordered copy LIST. Scope order — `system`, `agent`, `workset`, `box` — IS
the precedence; a later scope beats an earlier one.

**Authority:** `designs/grand-unification-collapse-DESIGN.md` §2 (Jei's algorithm, verbatim) and
**§2a, which supersedes §2's head** · its §0 ruling 1 (parent-first per scope) · the SUBSUMPTION
RULES, verbatim at `designs/collapse-implementation-DESIGN.md` **§7**, with the worked refusability
table and the operations at **§8** · **§9, which rules that copies apply to the HOME bind ALONE**
(2026-08-09d) · `designs/store-shape-producer-DESIGN.md` §7 (the input arms).

## Status: ADDITIVE, PURE and INFORMATION-ONLY

⚑⚑ Step 6 *"merges the **information**, but not the action"* — the roadmap's own words. This
function computes and returns. It drives no emission, executes no copy, changes no mount and
deletes nothing: `snapshot_category_entries` → `reconcile_categories` → emission is still the whole
live delivery path, including `reconcile_categories`' arbitration half, its `synced_vs_binding`
refusal and its row-5 warning channel.

Its one consumer is `start.py:_install_assembly_collapse`, which writes the result to
`meta.assembly.bindings` / `meta.assembly.copies` — leaves nothing reads. The collapse REFUSES
shapes the shipped route still accepts, so that seam catches `SettingsError` and leaves both leaves
absent; the tightening lands at the CUTOVER.

## Two halves that do not interact

⚖️ **RULED 2026-08-09d: a copy applies to the HOME bind alone.** A copy's destination is always
inside home and it resolves into the home bind's source — the box home store — so no mount can
arbitrate one. That makes the copy half a plain **concatenation** that completes BEFORE any binding
or mask fold, reading no binding at all.

Consequences, all of them removals rather than patches:

* **nothing prunes a copy.** A bind or mask at, above or beneath a copy's dest leaves it alone.
  Whether such a copy is then dead is a DELIVERY question, not a collapse-time one.
* **a dest MAY repeat**, and that is the point: the layered `seeded` overlay is one row per scope,
  every one of them targeting `~`. A dest-keyed map would silently drop all but one layer. The
  later entry overwrites the earlier FILEWISE at apply time — already ruled, and not this
  function's job.
* **the copy never meets a mask**, so the copied-directory-onto-a-mask rule and the un-mask branch
  that went with it are both GONE.
* ⚑⚑ **the module no longer touches the filesystem.** The directory rule was decided by a live
  `Path(src).is_dir()`, which made one config refuse or permit according to whether the source
  happened to exist yet. `settings_categories.py:492` states the standard for its own module:
  *"never by a resolve-time `exists()` probe: this module is PURE."* The probe is removed by
  removing its cause.

**The one new error case:** a copy whose dest is not inside home. Every copy shipped today is
home-relative by construction, so this is structural rather than a behaviour change — and it is
refused BY NAME, never dropped.

## Home is pid 0

Home is not in any scope's `store_shape` and the producer must not emit it. It arrives as its own
`home_bind` parameter and seeds `combined_bindings` BEFORE the loop, so every other binding enters
as a CHILD of an already-collapsed entry. Two properties fall out of the existing rules, with no new
rule added:

* **nothing may subsume home** — a bind at `/home` (or at `/`) is an ancestor of the foundation, so
  the bind refusal catches it; a second bind AT home is the same refusal, equality being on the
  subsume side;
* **everything inside home nests freely, at any scope.**

The key is `normalize_bind_dest("~")` = `/home/agent`, not the literal `~`. A dest is a GUEST path
and the guest home is fixed machinery, so the foundation key normalizes like every other dest —
otherwise `~` and `/home/agent/...` would not compare and home would subsume nothing.

⚑ `home_bind`'s options are carried VERBATIM: the mode fold applies to the scoped arms, and home is
not in an arm. A copy's options are carried verbatim for the same reason.

⚠️ **A MASK at home replaces it.** The ratified refusability table gives an arriving mask
*"ok — delete it"* over a bind at its own point, with no home exception, and "nothing may subsume
home" is stated as falling out of the BIND rule, which counts bindings only. Implemented as the
table has it and pinned by a test that says so; making it a refusal is a RULING, not a tidy-up.

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
| **mask** | a mask AT its dest or CONTAINING it | everything at or inside its dest |

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

`CollapsedStore(bindings, copies)`.

* `bindings: dict[dest -> CollapsedBind(src, opts)]`, an ORDINARY dict — `SortedDict` is dropped.
  Its order is not its meaning: SCOPE order decided precedence while it was being built, and PATH
  order is emission's business (`settings_categories` depth-sorts shallowest-first).
* `copies: list[CollapsedCopy(src, dest, opts)]`, in SCOPE order. The dest is no longer the key, so
  the entry CARRIES it. Not dest-sorted and not deduplicated: the order IS the overlay order.
* A MASK is `CollapsedBind(None, None)`. ⚑ `BindEntry.src` is NOT widened to `str | None` to carry
  it: that would relax the storage type for every consumer to serve the collapse's output shape.
  The second slot is DEAD, not reserved — a mask is a tmpfs with no host source and has no
  mount-option vocabulary.
* `seeded`/`synced` ARE COPIES AND STAY COPIES. The collapse changes key shape, never delivery.

## One thing the algorithm deliberately does not do

* **`shape.sync` is never read.** Jei's algorithm walks `shape.seed` only, so `synced` entries reach
  neither output. `_resolve_dest_group`'s `synced_vs_binding` refusal is therefore not reproduced
  here either — and it does not need to be, because the live path still raises it. ⚑ The deferred
  `synced` change (producer DESIGN §9.4) LOST ITS PREMISE with the copy ruling: it read *"a shadowed
  sync's copy is REMOVED from the collapsed list"*, and nothing is removed any more. Its replacement
  is the `mount_forbidden` list, on the backlog.

## The refusals, and what each one names

* **bind collision** — a bind at, or above, a dest a binding already occupies. ONE message for both,
  naming every colliding dest with the source bound there. It cannot reuse
  `settings_categories.raise_binding_vs_binding`: that one is written against `CategoryEntry`
  objects, and by here the entries are gone — a dest, a source and a mode are all that is left.
* **bind under a mask** — names the mask that would swallow it.
* **mask on a mask** — names every mask it lands on or inside.
* **copy outside home** — names the source and the destination, and points at the home bind.
* **mode contradiction** — see the fold, above.
