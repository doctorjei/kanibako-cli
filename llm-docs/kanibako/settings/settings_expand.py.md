# `@`-Reference Expansion — the merged snapshot's tokens resolved to terminals

`settings_expand` is the eager build-time expansion pass. ONE pure function, `expand`, walks the raw
merged `KeyStore` snapshot produced by `kanibako.settings.settings_merge` (block 2b) and resolves
every `@`-ref (CONFIG, on both bind sides) and every host-side `$VAR` / `~` (ENVIRONMENT) to
terminals — TRANSITIVELY, as a fixpoint, with cycle detection.

It is PURE: no file, env or clock access, same input → same output, and it NEVER mutates the input
snapshot (S19). It builds and returns a FRESH `KeyStore`.

⚑ **A reference resolves to a DECLARED key or it does not resolve at all.** The keyspace is closed:
this pass never fabricates a default for a name it cannot find. An absent referent propagates
ABSENCE — the holder key is DROPPED (whole-value, §6b) or substitutes the empty string (embedded) —
and every other unresolvable case is an ERROR that NAMES the key: a cycle, a depth-cap breach, an
unknown `$VAR`, a `@pref.*` reference, or a binding destination that would resolve to no path.

## It REUSES the single-expression engine; it does not fork it

The scanner is `kanibako.settings.settings_resolve.expand_expr` — escapes, `~`, `$VAR` / `${VAR}`,
`@ref`, the `chain` cycle-guard, `MAX_REF_DEPTH`. This module WRAPS it and does not modify it: that
same single-expr engine is shared with the `config.*` / `system.*` FOUNDATION path tier, which still
resolves through `resolve_value` in `paths.py` rather than through this pass.

What this module adds are the three things the single-expression engine lacks (brief §3):

1. **Snapshot-backed TRANSITIVE lookup.** The `lookup` callback resolves a ref by reading the
   snapshot at that dotted path AND fully expanding THAT value first (recursing), so chains collapse
   to terminals regardless of dict order — `A=@B`, `B=@C`, `C=term` collapses to `term`. It reuses
   `_expand_ref`'s `chain`-based cycle guard. Results are MEMOIZED, so the pass is a fixpoint, not
   re-resolution per reference.
2. **Whole-value `@`-ref 3-state propagation (§6b / §6h).** A value that IS exactly one `@x`
   (decided by PARSE — S18, never guessed) inherits the referent's 3-state through every link:
   referent absent → this key ABSENT (dropped from the snapshot); referent present-`None` → `None`
   (kept, the §3 terminal a bind/category consumer then OMITs); else the terminal value. An EMBEDDED
   token (`@x` inside a larger string) is pure SUBSTITUTION via `expand_expr` (absent/None → empty
   string; never deletes the key).
3. **CONFIG-vs-ENV deferral (§6a / B6 — S17).** For a `Bind`: `host_src` expands FULLY host-side
   (`@`-refs plus `$XDG` / `~`). `box_dest` expands its `@`-refs (CONFIG, the same on both sides) but
   leaves `$XDG` / `~` (ENVIRONMENT, host ≠ box) RAW — a DEFERRED token resolved box-side at mount.
   The expanded `Bind.box` may therefore still carry a `$XDG` / `~` token: a known, bounded residue,
   NOT lazy config re-resolution.

## The per-leaf case enumeration

`expand(snapshot, ctx)` takes block 2b's raw merged store (refs / vars / `~` intact) and a
`ResolveCtx` carrying the host-side expansion namespace — `host_home`, `xdg`, `agent_name`,
`workset_name` — consumed for host-side `$VAR` / `~`. Per leaf:

* **scalar str** → expanded host-side (`space="host"`); a whole-value `@`-ref inherits the referent's
  3-state (absent → the key is DROPPED; present-None → `None`); an embedded token substitutes per
  `expand_expr`.
* **`Bind`** → `host_src` expanded FULLY host-side; `box_dest` expands its `@`-refs but leaves
  `$XDG` / `~` RAW (deferred box-side, S17). If a whole-value `host_src` `@`-ref resolves
  absent/None, the WHOLE Bind is dropped, or carried as that terminal (§3 — a bind/category consumer
  OMITs it). `opts` is carried verbatim; it never holds tokens.
* **`BindEntry`** (the dest-keyed shape, R-5 / R-6) → `src` expanded exactly as `Bind.host_src`, with
  the SAME 3-state rule; the destination is the node KEY, so it is expanded on the walk
  (`_expand_dest_key`) in the same `$XDG` / `~`-deferred space `box_dest` uses. Two stored dests that
  resolve to ONE destination RAISE rather than silently clobbering.
* **non-str scalar** (`int` / `float` / `bool` / `None` / `list`) → carried verbatim; there is no
  token to expand. A present-`None` stored leaf is a real terminal, not `_ABSENT`.

## `_ABSENT` — the absence sentinel

`_Absent` is a module-private singleton meaning *a ref resolved to a LEGITIMATELY ABSENT key* (§6b
propagation). It is distinct from a *cycle* (which raises) and from a stored `None` (present-None, a
real terminal). A whole-value `@`-ref to an absent key propagates THIS sentinel up the chain; at the
top, `_expand_node` drops the host key from the snapshot. An EMBEDDED token coerces it to `""`. It is
never stored and is never a member of `kanibako.settings.kb_store.StoreValue`.

## Whole-value vs embedded — decided by PARSE (S18)

`_is_whole_value_ref` returns the dotted ref name iff the value IS exactly one `@`-reference and
NOTHING else — no leading or trailing characters, no embedded literal. It uses `match_ref`, the
SHARED grammar, so BOTH the bare `@a.b` and the braced `@{a.b}` spellings qualify.

`"@a.b"` / `"@{a.b}"` → `"a.b"`. `"@a-@b"` / `"x@a"` / `"@a/c"` / `"@a "` / `"@{a.b}x"` → `None`
(embedded — handled by `expand_expr` substitution). A leading `~` or `$` is therefore never
whole-value: those are environment tokens, not config refs.

⚑ **THE BRACED FORM MUST LAND HERE, NOT ON THE EMBEDDED PATH.** This predicate is the ONLY thing
that decides the shape, and the two paths differ in a way that is invisible until it bites: a
whole-value ref inherits the referent's full 3-state VERBATIM (absent → the key is dropped;
present-`None` → `None`), while an embedded token is pure string substitution and `_lookup_str`
coerces absent/`None` to `""`. So a braced whole-value ref misrouted to the embedded path would
silently turn a `None` (the §3 "omit this bind" terminal) into an empty-string terminal — a real
value where the spec means absence. `@{a.b}` and `@a.b` therefore resolve through the SAME call.

**It NEVER RAISES — a total predicate.** A malformed reference (`"@{a.b"`, `"@{"`) answers `None` so
it falls through to `_Expander._expand_embedded`, where `expand_expr` raises it with the same message
from the same place it always has. That keeps error provenance identical for malformed input in
STRICT and LENIENT mode alike; the only behaviour delta in this function is that a WELL-FORMED braced
ref now answers its name instead of `None`.

## Cycles, the depth cap, and the memo

A cycle is a hard build ERROR (`SettingsError`), covering whole-value AND embedded tokens (B7), with
the chain in the message. It is KEPT DISTINCT from a legitimately absent or present-`None` referent —
that is §6b propagation, NOT an error.

The guard lives in `_resolve_ref`. The `chain` argument is the in-progress ref trail, ending in the
path being resolved: the caller (`expand_expr`'s `_expand_ref`, or `_expand_str`) has already checked
and appended it, mirroring `expand_expr`'s own contract. A PRIOR occurrence of the path in the chain
means we re-entered a ref that is still in progress → a cycle, raised with the full trail. The depth
cap `MAX_REF_DEPTH` bounds pathological non-cyclic chains.

⚑ The cycle test is checked **BEFORE the memo**, so a cycle can never be masked by a half-built memo
entry (none is stored mid-resolution anyway).

The chain is SEEDED at the leaf: `_expand_leaf` starts it at the leaf's own dotted path, so a
self-referential whole-value `@` back to the leaf's own key is caught as a cycle rather than running
away as an infinite recurse.

The memo maps a dotted path to its fully-resolved value, or to `_ABSENT`. A path mid-resolution is
NOT in the memo; the `chain` argument detects a cycle before the memo would, on a self-revisit. ⚑
`None` and `_ABSENT` are both VALID memo values (a present-None terminal and a legitimately-absent
ref), so absence from the memo is tested with `in`, never by comparing against a sentinel value.

`_Expander` holds this per-pass state — the source snapshot, the ctx, and the memo. There is one
instance per `expand` call, so there is no cross-call state; the snapshot is read-only within it
(S19) and the fresh tree is built by `run`.

## LENIENT (error-COLLECTING) mode — Q9 set-time validation

Spec §2a / design Q9. `expand` takes an opt-in `collect_errors` flag: additive, default OFF, and
STRICT mode is byte-identical to what it was before the flag existed — the launch read-path is
unchanged.

When ON, expansion resolves everything resolvable and, instead of raising or silently dropping,
RECORDS each unresolvable leaf in an error map keyed by the leaf's dotted path: a dangling `@`-ref
(whole-value or embedded, target absent), an unknown / unset / malformed `$VAR`, an `@`-ref CYCLE, or
a depth-cap breach. It returns `(snapshot, errors)` — path → human reason — and OMITS the defective
leaf, while every clean leaf still resolves. The pass still TERMINATES on a cycle: the `chain` guard
fires as usual, and lenient mode records and skips instead of raising.

The signal itself is `_LenientDefect`, an internal lenient-mode-only exception raised when a leaf's
resolution hits a defect that STRICT mode would either raise on (unknown `$VAR` / cycle / depth-cap)
or silently drop (a dangling `@`-ref → `_ABSENT`). `_Expander._expand_node` catches it at the OWNING
leaf and records the dotted path → *reason*. It NEVER escapes `expand` — it is a leaf-local control
signal, not a user error — and it is never raised in strict mode. `_expand_node` also catches
`SettingsError` in this branch, which is how the `@pref.*` refusal below reaches the error map.

Set-time `config set` validation is the only lenient consumer. It implements the E3 rule: apply the
candidate RAW value into the merged snapshot at the edited key, lenient-`expand` the result, and
ALLOW iff the edited key is NOT in the error map — that is, iff its own transitive upstream chain
resolved cleanly.

An embedded dangling ref is a set-time DEFECT rather than the strict `""` coercion, per the
director's 2026-06-29 ruling, and it is attributed to the owning edited leaf. A present-`None`
referent is still a legitimate `""` and is NOT a defect. So the strict embedded-`""` behaviour is
unchanged; only the absent case diverges, and only when `collect_errors=True`.

## The `pref` subtree — carried through verbatim, never referenceable

`_PREF_ROOT` is the top-level table holding `pref.*` REQUESTS (spec §2h). Its subtree is carried
through UNEXPANDED and may not be `@`-referenced — prefs *"never participate in resolution as
derivable keys"* (§2h). It is one fixed token, spelled in this module rather than imported, to keep
the module free of a `settings_prefs` import, which would cycle through the settings stack.

Two halves enforce it:

* **`_expand_node` carries it through.** Spec §2h: *"pref.* keys never participate in resolution as
  derivable keys — `resolve_key_set` ignores them."* This is what keeps the RAW REQUEST readable
  after resolution: `--effective` shows BOTH the request (here, raw) and the result (at the TARGET
  key, resolved), which is what makes *"why did `system.agent` resolve to zippity"* answerable from
  the snapshot. Expanding it would also resolve every pref value TWICE, once here and once at its
  target. The carry-through is guarded on the ROOT path (`not path`) so a category legitimately NAMED
  `pref` deeper in the tree is unaffected. The copy goes through `settings_merge._deep_copy_store`.
* **`_lookup_raw` REFUSES a `@pref.…` reference** rather than resolving it. It is RAISED rather than
  answered `_ABSENT` because absent would silently DROP the referring key (the §6b whole-value
  propagation), which is exactly the class of failure this phase exists to eliminate. The message
  says a pref is a REQUEST, not a value, and tells the reader to reference the TARGET key instead.
  The raise lands in the lenient error map too.

## The resolver SPLIT — `config.*` goes to the foundation

Spec §1A / JC-2. In `_lookup_raw`, a `config.*` ref routes to the Layer-1 CONFIG-key FOUNDATION
(`ctx.config`), NOT the settings snapshot: config is a foundation, not a cascade level. Every other
prefix — `system.*` / `workset.*` / `box.*` / `agent.*` — walks the merged snapshot.

Foundation values are already-resolved absolute paths (terminals), so a `config.*` hit returns its
string verbatim; a `config.*` miss is `_ABSENT`, a dangling config ref. The routing is prefix-driven,
which keeps it single-route.

The snapshot walk itself uses the UNBOUND `dict.get(node, seg, _ABSENT)` probe (S3): any missing
segment, or a non-`KeyStore` node reached before the last segment, yields `_ABSENT` — the path does
not exist. The final segment's value is returned verbatim, so a present-`None` leaf answers `None`.

## Destinations: the dest-keyed arm and its two refusals

`_expand_dest_key` answers the OUTPUT key for a value. For every value shape but `BindEntry` a node
key is a plain keyspace token and is carried through verbatim. A `BindEntry` lives in a DEST-KEYED
bindings arm (R-5 / R-6), where the destination has moved out of the value and become the mapping
KEY — so the key, not the value, is the box-side path EXPRESSION, and it expands exactly as
`Bind.box` does: `@`-refs resolved, `$XDG` / `~` left RAW for the box side (`space="defer"`, S17).

⚑ Discrimination is by TYPE (`isinstance(value, BindEntry)`), never by the key's spelling or the
value's arity — a legacy `Bind` and a `BindEntry` are both 2-element-legal with opposite meanings.

**Refusal 1 — a destination that resolves to no path.** A whole-value `@`-ref destination that
resolves absent or present-`None` RAISES, in `_expand_dest_key` and, for the name-keyed shape, in
`_expand_bind`. A destination cannot resolve to no path, and an empty dest is a mount foot-gun rather
than a legitimate omission. On the `Bind` side there is a further reason it is a raise and not a
propagation: a `box_dest` is a path EXPRESSION, not a key whose absence deletes the bind, so it never
returns `_ABSENT` / `None` from the EMBEDDED path (an embedded token coerces to `""`). The ONLY way
`box` is `_ABSENT` / `None` there is a WHOLE-VALUE `box_dest` `@`-ref to an absent or present-`None`
config key — and the spec has NO whole-value `box_dest` (every `box_dest` is `~/…` or `$XDG…` or an
embedded `@`-path). It is therefore an unreachable-on-spec-forms config error, and it raises loudly
with the bind in the message rather than silently emitting an empty dest.

**Refusal 2 — two dests colliding on one destination.** Files store UNRESOLVED, so
`@meta.box.path/home` and a literal spelling of that same path are two DISTINCT keys that resolve to
one place (design §2b-CAVEAT). Installing the second would silently DELETE the first, which is a data
loss no downstream check can see, because only one entry would ever reach it. It is raised rather
than recorded EVEN IN LENIENT MODE: the fault is the PAIR, so there is no single owning leaf to
attribute it to.

## Bind and BindEntry — the two bind shapes

`_expand_bind` handles the name-keyed `Bind`: `host_src` fully host-side, `box_dest` `@`-refs only.
If the `host_src` is a whole-value `@`-ref that resolves absent/None, the WHOLE Bind takes that
3-state — the binding cannot point anywhere: an absent host → `_ABSENT` (drop the bind); a
present-None host → `None` (the §3 bind/category OMIT terminal). Otherwise both halves are strings
and `opts` is carried verbatim, since it never holds tokens.

`_expand_bind_entry` is the dest-keyed counterpart. It expands ONE half, because the other half — the
destination — is the mapping KEY and is expanded by `_expand_dest_key` on the node walk (R-5 / R-6).
The 3-state rule is unchanged from the name-keyed shape: a whole-value `src` ref that resolves absent
makes the WHOLE entry `_ABSENT` (drop it — the binding cannot point anywhere); a present-`None` `src`
yields `None` (the §3 bind/category OMIT terminal). `opts` is carried verbatim.

## Embedded substitution and the `expand_expr` lookup

`_expand_embedded` substitutes embedded tokens via `expand_expr`. An `@`-ref token resolves through
`_lookup_str` (absent/None → `""`); `~` / `$VAR` expand host-side for `space="host"` and are left RAW
(`defer_env=True`) for `space="defer"` (S17). A cycle reached through an embedded token still raises
(B7 — the chain guard is in `expand_expr`'s `_expand_ref` AND in `_resolve_ref`).

It reuses the SINGLE `expand_expr` scanner for BOTH spaces, with no fork: the box-side deferral is
the engine's additive `defer_env` flag, proposed in chat and held pending the director's call.

`_lookup_str` is the callback `expand_expr` calls. It resolves the dotted path through the transitive
resolver — so embedded refs are also fixpoint- and cycle-guarded (B7) — and coerces the result to a
SUBSTITUTION string per the embedded-token rule (§6b). STRICT: an absent or present-None referent →
`""`, an empty substitution that never deletes the host key; a resolved scalar → its string form. The
`chain` it receives is `expand_expr`'s already-extended trail.

Two degenerate shapes are handled to keep the function total. An embedded ref to a whole `Bind` has
no single string form, so it substitutes the Bind's `host` (the source path). The dest-keyed shape
does the same with `src` — the destination is the key, not part of the value.

## Nested-KeyStore referents

A ref whose target is a nested `KeyStore` (a whole subtree) is degenerate — the spec never refs a
whole subtree — but it MUST be resolved through `_expand_node` rather than carried, for two reasons.
(a) FRESHNESS, S19: a bare `resolved = raw` would make `out[...] is snapshot[...]`, letting a later
output edit leak back into a partial. (b) FULL EXPANSION: its inner `@` / `$` tokens must resolve,
matching how the same subtree is expanded at its own location. The dotted path seeds the child cycle
chains.

## OUT of scope — the hard boundaries

* NO cascade merge / precedence — that is `kanibako.settings.settings_merge`; this consumes its
  output.
* NO cross-scope `box_dest` collision resolution (§6g) — that is the SEPARATE downstream pass, today
  the `store_shape` producer plus the assembly collapse. (The collision this module DOES raise on is
  the narrower same-node case above.)
* NO typed views — `kanibako.settings.settings_views`.
* NO `config set` — `kanibako.settings.config_interface`.

## Authority

* `~/vault/rw/keystore-design.md` §6h (transitive expansion + cycle — PRIMARY), §6a (CONFIG-vs-ENV
  split; box-side `$XDG` / `~` deferred), §6b (whole-value vs embedded `@`-ref shapes), §3 (the
  3-state).
* Spec `settings-keyspace-1.8.0.md` §0, §1 (the box-side XDG line, ~line 94), §2c, §2h, §2a, §1A.
* The merged snapshot this pass consumes is `settings_merge`'s raw output (`d33db5c`).

## Seams realized here (`plans/keystore-blocks/SEAMS.md`)

* **S17** — box-side `$XDG` / `~` left RAW in `Bind.box`; `@`-refs expand BOTH sides. The concrete
  realization of S12's deferral contract.
* **S18** — whole-value vs embedded `@`-ref decided by PARSE, never by guess.
* **S19** — expansion does NOT mutate the input snapshot (pure; fresh tree).
* **S3** — every snapshot access uses the UNBOUND `dict.<method>(obj, …)` bypass, so a key named
  `get` / `items` / `keys` cannot shadow the protocol into a crash.

---

## Completeness sweep (relocation pass, 2026-08-20)

Every prose line removed from `settings_expand.py` in this pass was RELOCATED into a section above,
in substance. Nothing was dropped as a false claim.

**Deliberate content drops: 0.** Two categories were cut as pure DUPLICATION rather than relocated,
and both surviving carriers are in this document:

1. The module docstring's per-leaf enumeration and its restatement inside `expand`'s docstring said
   the same thing twice in one file (P12). The single carrier is *The per-leaf case enumeration*
   above.
2. The `_expand_bind` / `_expand_bind_entry` docstrings each restated the 3-state rule that the
   module docstring already stated. The single carrier is *The three things it adds* item 2, with the
   per-shape wording under *Bind and BindEntry*.

**Warnings KEPT IN SOURCE** under the keep test, because deleting each would let a future edit break
something silently at that exact line:

* the closed-keyspace / no-fabricated-default statement in the module docstring — the invariant the
  whole pass exists to hold;
* the `$XDG`-deferral statement in the module docstring — S17 is the one thing about this pass that
  surprises every new reader of `Bind.box`;
* `_is_whole_value_ref`'s ⚑ braced-form warning — the misroute it names is silent and turns absence
  into a real value;
* `_expand_dest_key`'s ⚑ discrimination-by-TYPE warning — `Bind` and `BindEntry` are both
  2-element-legal with opposite meanings;
* `_expand_node`'s ⚑ `pref`-carried-verbatim comment and `_lookup_raw`'s ⚑ `pref`-refusal comment —
  the two halves of §2h, and the refusal's "raise, do not answer absent" is the non-obvious half;
* the ⚑ on the destination-collision raise — a silent data loss no downstream check can see;
* the ⚑ on the `box_dest` absent/None raise — it reads like dead code until you know the spec has no
  whole-value `box_dest`;
* the ⚑ on the memo — `None` and `_ABSENT` are valid memo values, so `in` is load-bearing;
* the ⚑ on the cycle guard preceding the memo lookup;
* the ⚑ on the nested-`KeyStore` referent routing through `_expand_node` — aliasing breaks S19
  silently.
