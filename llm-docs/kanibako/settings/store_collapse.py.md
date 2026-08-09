# The Collapse (roadmap step 6a)

The "grand unification": four per-scope `StoreShape`s plus the home bind, merged into ONE dest-keyed
bindings map and ONE dest-keyed copies map. Scope order — `system`, `agent`, `workset`, `box` — IS
the precedence; a later scope beats an earlier one.

**Authority:** `designs/grand-unification-collapse-DESIGN.md` §2 (Jei's algorithm, verbatim) and
**§2a, which supersedes §2's head** · `designs/collapse-implementation-DESIGN.md` (how it is built
and what it must not disturb) · `designs/store-shape-producer-DESIGN.md` §7 (the input arms).

## Status: ADDITIVE, PURE and UNCONSUMED

⚑⚑ Step 6 *"merges the **information**, but not the action"* — the roadmap's own words. This
function computes and returns. It drives no emission, executes no copy, changes no mount and
deletes nothing: `snapshot_category_entries` → `reconcile_categories` → emission is still the whole
live delivery path, including `reconcile_categories`' arbitration half, its `synced_vs_binding`
refusal and its row-5 warning channel. Nothing calls this module yet.

Writing the result into `meta.assembly.bindings` / `meta.assembly.copies` is step 6b and lands in
`commands/start.py`. Still no emission then either.

## Home is pid 0

Home is not in any scope's `store_shape` and the producer must not emit it. It arrives as its own
`home_bind` parameter and seeds `combined_bindings` BEFORE the loop, so every other binding enters
as a CHILD of an already-collapsed entry. Three properties fall out of the existing rules, with no
new rule added:

* **nothing may subsume home** — a bind at `/home` is a shallower dest and emission depth-sorts, so
  home lands on top of it;
* **exactly one bind at home** — a second one is a double-bind and raises;
* **everything inside home nests freely, at any scope.**

The key is `normalize_bind_dest("~")` = `/home/agent`, not the literal `~`. A dest is a GUEST path
and the guest home is fixed machinery, so the foundation key normalizes like every other dest —
otherwise `~` and `/home/agent/...` would not compare and home would subsume nothing.

⚑ `home_bind`'s options are carried VERBATIM: the mode fold applies to the scoped arms, and home is
not in an arm.

## Every dest is normalized at the point of use

`normalize_bind_dest` is idempotent and is applied at every producer and again on read, so calling
it here is a no-op on well-formed input and states the precondition rather than assuming it. It is
done at each point of use rather than in a `normalize_paths` pre-pass over the arms, and that is
load-bearing: a pre-pass rebuilding an arm's dict would let `~/x` and `/home/agent/x` in ONE arm
silently overwrite each other, where at the point of use the second one meets the first in
`combined_bindings` and raises the double-bind it actually is.

⚑⚑ A box destination is DATA. It is never `.split(".")`/rejoined — dests routinely contain dots.

## The prefix match needs a separator guard, on BOTH loops

`startswith(dest)` is wrong in two directions and the copy prune uses both:

* `/home/agent/foobar` is NOT inside `/home/agent/foo`;
* `/home/agent-foo` is NOT inside `/home/agent`.

So the test is `d == dest or d.startswith(dest.rstrip("/") + "/")` — the exact-equality case is
wanted (a mount AT a copy's dest shadows it), the separator makes "inside" mean inside. `rstrip`
also makes a bare `/` behave: it yields the prefix `/`, and everything is inside root.

## The prune list is the CURRENT scope's keys ONLY

🛑 **`_scope_dests` must NOT become an accumulating set.** That is what makes the prune
scope-ordered: a system-scope bind prunes only the copies collapsed BEFORE it, and a box-scope copy
declared later is untouched. Accumulating would let an outer scope reach forward and delete an
inner scope's copy — precedence inverted. A test pins it, because it looks exactly like an
oversight worth tidying.

## Unmasking plants into the BINDINGS, not the copies

A mask lives in `combined_bindings[dest] = (None, None)`. When a later scope seeds a copy at a
masked dest, the mask must go — deleting from the copies map instead would leave the mask in place,
shadowing the very copy the branch just decided to plant, and would `KeyError` when no copy is
there yet.

⚑ A mask and a binding may share ONE scope, and that is not an error: within the scope the mask
applies (it merges after the arms) and the cure is not declaring it. The collapse adds no
diagnostic for it.

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
  is REFUSED BY NAME rather than joined into a meaningless `"ro,rw"`. It is reachable today
  (`_emit_bind` takes a per-entry override verbatim); before the fold it merely read oddly, after
  the fold it would read as a contradiction.

## Case is NOT folded

The draft lowercased the copy dest before the prefix compare (and only one side of it). **Linux
paths are case-SENSITIVE**: folding would silently merge `/Home/x` and `/home/x` into one
destination. The call is dropped entirely, not repaired, and a test pins the two as distinct.

## What comes back

`CollapsedStore(bindings, copies)`.

* `bindings: dict[dest -> CollapsedBind(src, opts)]`, an ORDINARY dict — `SortedDict` is dropped.
  Its order is not its meaning: SCOPE order decided precedence while it was being built, and PATH
  order is emission's business (`settings_categories` depth-sorts shallowest-first).
* `copies: dict[dest -> list[BindEntry]]`, **dest-ordered**, one dest holding a list appended in
  scope order — copies combine filewise, not bindwise. The sort is what the draft's `SortedDict`
  gave for free; the `bisect_left` scan it existed to serve is gone with it.
* A MASK is `CollapsedBind(None, None)`. ⚑ `BindEntry.src` is NOT widened to `str | None` to carry
  it: that would relax the storage type for every consumer to serve the collapse's output shape.
  The second slot is DEAD, not reserved — a mask is a tmpfs with no host source and has no
  mount-option vocabulary.
* `seeded`/`synced` ARE COPIES AND STAY COPIES. The collapse changes key shape, never delivery.

## Two things the algorithm deliberately does not do

Recorded so neither is mistaken for an omission and neither is "fixed" without a ruling.

* **`shape.sync` is never read.** Jei's algorithm walks `shape.seed` only, so `synced` entries reach
  neither map. `_resolve_dest_group`'s `synced_vs_binding` refusal is therefore not reproduced here
  either — and it does not need to be, because the live path still raises it. Both facts are already
  recorded in the producer's own notes.
* **No bind-over-bind SUBSUMPTION check.** The per-scope shallow-first depth sort was ratified as
  the intra-scope mechanism that makes such an error meaningful, but the algorithm this module
  implements carries no subsume error, and with no such error the sort has no observable effect on
  the result: within one scope the arms are dest-keyed, so processing order changes nothing.

## The refusals

* **Double bind** — two real sources at one dest, ACROSS scopes (row 1's cross-scope case; the
  same-scope case is the producer's). A binding may override a MASK, never another binding. The
  message cannot reuse `settings_categories.raise_binding_vs_binding`: that one is written against
  `CategoryEntry` objects, and by here the entries are gone — a dest, a source and a mode are all
  that is left.
* **Mode contradiction** — see the fold, above.
