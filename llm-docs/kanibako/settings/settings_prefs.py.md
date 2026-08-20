# `pref.*` — REQUESTS to set an earlier-resolving key
_the collector, the three filters, and the two extra cascade levels_

`settings_prefs` owns the whole of spec §2h. A `pref.<target-key>` written in a WORKSET or BOX
settings file is a REQUEST to install a value at a key that resolves STRICTLY EARLIER than the
requesting level. It is not a value of its own; it is an instruction consumed during resolution.
Two spellings matter today:

```
pref.system.agent         | <agent name>   SELECTION — "I want to use this agent, by name."
pref.agent.<agent>.<key>  | <value>        CONFIGURATION — a value for that agent, at agent scope.
```

The module reads those requests (`collect_prefs`), validates each against THREE independent filters,
and builds two `KeyStore` overlays that the launch snapshot splices into the cascade. It also
supplies the small read helpers the rest of the tree uses to ask *"was a request made for this
key?"* and *"which request installed this entry?"*.

## How it works here — and why there is no recompute LOOP

§2h says prefs are expanded at the START of their level and the preceding levels are then
**RECOMPUTED** — *"I should have said recompute. We will be careful about it."* That describes a
STAGED resolver. Ours is single-shot (`assemble_levels` → `merge` → `expand`), so prefs are
collected BEFORE the merge and installed as two additional cascade LEVELS. Recompute is then
satisfied *a fortiori*: no value derived from the old value can be stale, because at install time
nothing has been resolved at all.

⚑ The DELTA failure mode the spec warns about is reachable only by the tempting wrong
implementation — patching the EXPANDED snapshot afterwards, beside `_materialize_box_agent_mirror` /
`_install_derived_bindings`. Those are legitimate post-expand `meta.*` materialisations; a pref is
NOT, because a pref's value is an INPUT to resolution.
`tests/test_settings/test_settings_launch.py`
`TestPrefRecomputeNotDelta.test_a_key_derived_from_a_prefd_value_updates` is the discriminator —
its own docstring calls itself *"THE discriminator for the delta implementation"*.

⚑ CORRECTED in the 2026-08-20 relocation pass: the source docstring cited
`tests/test_settings_prefs.py` `test_pref_propagates_to_a_derived_key`, and neither that path nor
that test name exists in the tree.

### Placement

In `settings_launch.build_launch_snapshot`, MOST-SPECIFIC-FIRST:

```
[*box_agent_folds, box, BOX_PREFS, workset, WORKSET_PREFS, …]
```

Each level's overlay sits immediately BELOW that level's own partial, because §2h expands prefs
*before the level resolves* — so the level's own keys are applied after and win. `BOX_PREFS`
precedes `WORKSET_PREFS`, which is box-beats-workset by assignment order (§1A).

Nothing LEGAL contends with either overlay: a box or workset file may not set `system.agent` or
`agent.<a>.*` at all — those are upward writes, dropped by `_drop_upward_scopes`.

## Termination — the recursion bound

§2h's bound is that no pref may change which files feed the cascade. There are two independent
guarantees:

1. The LOCATOR-CLOSURE filter (below).
2. The measured fact that the pref-legal file pair comes from `paths._box_settings_files` — the
   runtime TREEWALK — which consults no settings key at all.

That second fact is what makes `collect_prefs` safe to call as a targeted PRE-READ before the
cascade runs, which is exactly what agent selection needs.

⚑ `pref.system.agent` DOES change a cascade input: it selects `meta.agent.<agent>.settings`. That is
safe, and deliberately excluded from the closure, because an AGENT file may not carry prefs — so a
re-selected agent file cannot introduce new requests.

The other half of the bound is `PREF_LEGAL_LEVELS`, the `("workset", "box")` pair naming the ONLY
levels at which a pref may be WRITTEN: *"This is what BOUNDS the recursion, so it is a hard rule,
not a convenience."*

## `LOCATOR_CLOSURE` — the two members, and the five that are not there

The forbidden-tier arm that is a TERMINATION guarantee, not tidiness: a key in it relocates a
cascade-input settings FILE, so requesting it from a lower level could pull in a different file
carrying its own prefs, which could relocate again — unbounded, and able to oscillate between two
files pointing at each other.

* `workset.boxes` → `meta.box.path` (`@workset.boxes[/@meta.box.name]`, §2c) → `meta.box.settings`
  (`@meta.box.path/settings.yaml`, §2c ALL PROJECTS) → THE BOX SETTINGS FILE, i.e. possibly the very
  file the request came from.
* `workset.kuid` → `meta.box.name` for STANDALONE (`<@workset.kuid>_%leaf%`, §2c) → the
  `meta.box.path` LEAF → the same chain, one hop further out.

⚑ **WHY ONLY TWO, where §2h's sketch lists seven.** `config.data`, `config.primary_workset`,
`meta.workset.path`, `meta.box.path` and `meta.box.name` are already barred by the CATEGORICAL tier
(`config.*` / `meta.*`). This tier holds only the SETTABLE, non-meta, non-config keys in the chain.
The tiers are independent filters; a key covered by both is fine.

⚑⚑ `system.agent` IS DELIBERATELY EXCLUDED even though `meta.agent.<agent>.settings` derives from it
(§2d). It is the whole point of the feature, and the termination argument still holds because the
agent file may not carry prefs. **A naive derivation of this closure would capture `system.agent`
and break the headline feature** — read this before implementing the TODO below.

⚑ NOT in the closure, so nobody adds them speculatively: `workset.registry` (the box MEMBERSHIP
index, not a cascade-input settings file), `workset.template` / `<scope>.canon` (seed and bind
sources), `system.cache` / `system.runtime` (no cascade file under them).

### The TODO — derive it, do not hand-list it

Agreed, not now (spec §2h). Take every cascade-input anchor — `meta.workset.settings`,
`meta.box.settings`, `meta.agent.<agent>.settings`, the base/system files — and forbid the
TRANSITIVE CLOSURE of the keys they derive from. A hand-written list rots the moment someone adds a
derivation; a computed closure can be asserted by the consistency checker and yields a precise
error: *"pref.workset.boxes forbidden: meta.box.settings derives from it."*

Accepted consequence: the closure includes `workset.boxes`, which stays settable in a workset FILE —
only REQUESTING it from a lower level is barred.

## Where a pref may be written, and what happens where it may not

`refuse_pref_table` drops a top-level `pref:` table from a base / system / agent file with a warning
naming the file. A pref at those scopes has NO equivalent: it is FLAGGED, never silently relocated
to a legal level and never read as a value.

That is the SAME treatment `_drop_upward_scopes` gives the sibling fault — a containing scope's
table in a lower file — because two behaviours for one fault class is exactly the confusion §0's
convention 0 forbids. Dropping preserves the recursion bound at least as strongly as erroring would.

The HARD refusal §2h calls for lives at the write site: `config set pref.*` at these scopes RAISES,
which is the only way a user creates one short of hand-editing.

## The §0 glob convention

`glob_match` implements it, and `ALLOWLIST` is written in it: `*` matches exactly ONE segment, `**`
matches the remaining tail at ANY depth. `**` is one-or-more *by construction*, not by rule — the
separator is part of the pattern, so a zero-length tail on `agent.*.**` would yield the malformed
`agent.foo.` with a trailing dot. (Ruled by Jei 2026-07-29.)

The empty-segment guard in the matcher is where that argument bites: without it, `agent.*.**` would
MATCH the malformed `agent.claude.` — the very zero-length tail the convention says cannot arise.

## Flattening — `_flatten_pref_node`

⚑ **The dotted-leaf raise.** Flattening walks NESTED tables ONLY. A leaf whose own segment contains
a dot (`pref: {"system.agent": x}`) is an ERROR, not a second accepted spelling.
`settings_assemble._parse_node` decides bind-shapes by ANCESTOR segment, so under the dotted
spelling `pref: {"agent.claude.common.plugins": [src, dest]}` the value would stay a raw `list` and
never become a `kanibako.settings.kb_store.Bind` — the same request behaving differently depending
on how it was spelled. One form, enforced (§0 convention 0).

⚑⚑ **The walk stops at a terminal dest-keyed category** — `<scope>.masks` and
`<scope>.bindings.{ro,rw}`, tested with
`kanibako.settings.settings_keyspace.is_terminal_category_tail`. Those keys' VALUES are maps keyed
by box DESTINATION, and a destination is DATA, not a key segment (spec §2a; disk-store R-5/R-10).

Descending into one would manufacture a target that is not a key —
`pref.agent.claude.bindings.rw./home/agent/x` — and, because a real destination contains dots, it
would trip the dotted-key raise ABOVE first and report a SPELLING fault the user did not commit.

So the arm itself is the request: one `PrefRequest` carrying the WHOLE map, which `pref_overlay`
installs at the arm and `settings_merge` then merges PER-ENTRY across levels (the `masks`
three-state precedent). That is exactly what makes the spec's per-entry suppression spelling
`pref.<scope>.bindings.ro: {<dest>: null}` work. The dotted-key raise is UNCHANGED for every other
node: only these terminal keys stop the walk, and they stop it before their data keys are ever read.

## `PrefRequest` — the value is carried VERBATIM

A `PrefRequest` is ONE `pref.<target>: <value>` as read from ONE settings file, and its *value* is
carried verbatim, including `None` (spec §2h). This layer performs NO emptiness interpretation of
any kind: present-`None`, terminal `""` and the COPY-disable sentinel all forward untouched, so the
pref does not become a FOURTH place deciding what "empty" means.

`collect_prefs` parses through `settings_assemble._file_partial` — the SAME parse the cascade uses —
so a bind-shaped pref value arrives as a `Bind`, exactly as it would at its target key. Re-reading
the file is deliberate: it is ONE spelling of the parse, called from one collector.

## The three independent filters

### Filter 1 — `key_reason`: is the target a VALID key?

⚑ VALIDITY, not EXISTENCE. `agent.claude.env.BOOOOOO` is legal: a new name inside a parametric
family is exactly what a user may want to add. An existence test would permit only modifying keys
that already hold a value.

⚑⚑ **NO BIND-SHAPED CATEGORY IS SUCH A FAMILY ANY MORE, and this docstring twice said otherwise.**
Its example was `agent.claude.bindings.rw.boooooo` until those arms went TERMINAL and DEST-KEYED
(spec §2a; R-5/R-10), then `agent.claude.common.boooooo` until 2026-08-08c did the same to `common`
/ `caches` / `seeded` / `synced`. Both spellings are now REFUSED by `key_validity` — correctly — so
only the BARE terminal key (`agent.claude.common`) is a valid pref target and the destinations live
inside its value. The families that DO still carry a free `<name>` are `env.<VAR>`,
`secret_path.<VAR>` and the agent discriminator itself.

### Filter 2 — `allowlist_reason`: is the target requestable IN PRINCIPLE?

Membership alone is NOT sufficient; filter 3 still applies. The agent segment of `agent.*.**` is
INVALID unless it names a valid agent or `default` — and the test is *is it a VALID agent*, NOT *is
it the ACTIVE agent*, so pre-configuring an agent you may switch to is legal.

When discovery FAILED, the message says so rather than blaming the name: reporting *"'claude' is not
a valid agent"* when the plugin registry could not be read sends the user to fix a correct name.

**The "set it directly instead" suggestion is conditional.** Only suggest a direct set where one is
actually possible. `meta.*` is RO by contract, `config.*` is hand-edited in the bootstrap file, and
`pref.*` is not a value scope at all — telling a user to "set it directly at the meta scope" would
send them somewhere that does not exist.

⚑ SAME RULE, SECOND CLASS OF TARGET: a YAML-only key has no direct set either. The bind-shaped
categories lost their CLI write route (R-9 for the two `bindings` arms, DS-BL1 = (a) for `caches` /
`seeded` / `common` / `synced`) and `masks` never had one, so the suggestion would prescribe a
command that refuses. ONE predicate answers it for both message sites —
`config_keys.has_no_cli_write_route` — deferred-imported to keep this module free of a module-scope
edge back to the key registry.

### Filter 3 — `forbidden_tier_reason`: is the target barred by a forbidden TIER?

It returns a REASON string, never a bool: §2h requires the error to say WHY, and the design's item-1
ruling makes that explicit — *"the forbidden-tier check must return a REASON, not a boolean."*

Three arms, checked in the spec's order:

* **Structural** — the target must resolve STRICTLY EARLIER than the level setting it. A
  later-resolving key needs no pref: set it directly. `_LEVEL_ORDER` is the rank table
  (`config` `meta` `base` `system` `agent` `workset` `box`, L0.1 → L4.2, spec §1A).
* **Categorical** — never `meta.*`, `config.*` or `pref.*` itself. ⚑ This does NOT stop meta VALUES
  from changing: `meta.box.auth.workset_path` changes because `system.agent` changed, which is the
  entire point. Only DIRECT targeting is barred.
* **Locator closure** — see above.

### `validate_pref` — all three, joined

⚑ ALL failing filters are reported, in filter order — not just the first. The filters are
INDEPENDENT (§2h) and the decision is their conjunction, so reporting every failure is faithful.
Reporting only the first would make message quality hostage to the validator's SUPPORTING-surface
completeness: a gap there would make `pref.box.image` read "not a declared key" instead of the
actionable "not requestable".

`apply_prefs` raises `SettingsError` on the FIRST invalid request, naming the key, the LEVEL, the
FILE and the REASON (spec §2h: *"We don't want to just moving on with bad settings"*). The launch
FAILS rather than proceeding with a partially-applied request, and never a silent skip. Only the
first offender is reported: fix-one-then-see-the-next matches every other config error in this
codebase.

## Installation — present-`None` is a REQUEST, not an absence

`pref_overlay` installs values VERBATIM, including `None` (spec §2h). `if value is None: continue`
is the most natural guard to write there and it silently implements the REJECTED reading ("no
request"), deleting a box's ONLY suppression channel with no error and no visible diff. There is
deliberately no such guard, and
`TestPrefNullSuppression.test_a_null_pref_suppresses_an_inherited_agent_bind` in
`tests/test_settings/test_settings_launch.py` reddens if one appears (⚑ CORRECTED in the same pass
from the non-existent `test_null_pref_suppresses_agent_bind`). The companion
`test_a_null_pref_on_a_SCALAR_leaf_is_kept_as_none` pins the scalar half.

A present-`None` lands on the target key and is then classified by the ORDINARY present-`None` rule
at the TARGET's path (`settings_merge`): OMIT for a bind / category / masks leaf, KEPT `None` for a
scalar leaf. This layer classifies nothing.

⚑ So `pref.system.agent: null` is KEPT as `None` — `system.agent` is a scalar leaf — and it means
the NO-AGENT box (§2b). `pref_value` cannot express that distinction through its return type, which
is deliberate for this case because "no agent selected" is the same outcome; a caller that needs
present-`None` told apart from absent uses `pref_request_for` instead.

## Agent discovery — `AgentNames` and the memo

`AgentNames` is the `valid_agents` collection: discovered HARNESSES plus any persona NODE built on
one. It is a `Collection[str]` rather than a bare `frozenset` because the valid set is not
enumerable — the keyspace agent tier is discriminated by NODE (`navigator℘claude`), and persona
nodes are user-created, so membership is a PREDICATE (*is this ref built on a discovered harness?*)
while iteration yields the finite harness list that an error message should name.

⚑ Membership is deliberately a VALIDITY test, not an EXISTENCE test: §2h rules that a pref may
pre-configure *"an agent you may switch to"*, so requiring the persona's store dir to already exist
would be the same existence error the spec rejects for keys.

Two fields carry the rest. `leaves` holds the PLUGIN-declared agent keys, unioned over the core §2d
contract by the validator (§0 *"Agent specifics are PLUGIN-declared"*). `discovery_failed` records
that discovery FAILED — an environment fault, as distinct from "no agents are installed". Without it
an unreadable plugin dir reports *"'claude' is not a valid agent"*, blaming the user's spelling for
a broken box.

`default_valid_agents` is the production supplier: every DISCOVERED agent plus the agent keys those
plugins DECLARE. It is MEMOIZED in `_DISCOVERY` and reached only when a request actually names
`agent.*` (`_needs_agent_discovery`), so the "lazy" claim is enforced by the call site, not just
asserted. A discovery FAILURE is recorded on the result rather than swallowed: an environment fault
must not be reported as a bad agent name.

`_DISCOVERY` exists because discovery walks entry points, a module namespace and two plugin
DIRECTORIES; a launch runs several resolves, and repeating it per resolve is pure waste.
`reset_discovery_cache` is the test seam, and mirrors `commands.start.reset_collision_warnings`.

⚑ `apply_prefs` tests `valid_agents is None`, NOT falsiness. An EMPTY `AgentNames` — a box with no
agent plugins installed — is falsy, so a truthiness test would discard a caller's deliberate empty
set and silently re-discover.

⚑ `_needs_agent_discovery` returns True only for an agent-scope target. `pref.system.agent` does
NOT need discovery: its VALUE names an agent, but §2h validates the target key, not the value, and a
not-yet-installed agent name is legal there.

## Entry keys and origins — `pref_entry_keys` / `pref_origin`

A settings ENTRY is identified downstream (collision errors, `binding_derivations.*`) by
`<decl-scope>.<category>.<dest>`. For most targets that string IS the pref target —
`pref.agent.claude.env.FOO` requests exactly the key `agent.claude.env.FOO`, because `<VAR>` is a
key SEGMENT. For the SEVEN terminal dest-keyed categories (the six bind-shaped ones plus `masks`) it
is not: the target stops at the category — `key_reason` REFUSES `agent.claude.common.<name>` — and
the destinations live INSIDE the value. One request there accounts for one entry key PER DESTINATION
IT DECLARES.

⚑⚑ **THE DESTINATIONS ARE READ FROM THE REQUEST'S OWN VALUE, not derived by trimming the entry
key.** A bare prefix test (`key.startswith(target + ".")`) is the tempting one-liner and it
MISATTRIBUTES: two prefs may target one category from the workset and box levels while declaring
DIFFERENT destinations, and the entry at a given dest may not have come from a pref at all — the
agent settings file and the launch floor also write these keys, they just resolve LOWER. Containment
answers both: a request that does not declare the destination cannot be its origin, and two requests
that both declare it are separated by the same last-wins rule the overlays use.

⚑ A DECLARED-`None` destination is EXCLUDED. Present-`None` is the per-entry suppression spelling
(§2h / §6e): it removes the entry rather than installing one, so a surviving entry at that dest is
somebody else's. Erring this way costs at most a missing annotation; the other way prints a wrong
file path.

⚑ The terminal-category test gates on the KEYSPACE (`is_terminal_category_key`), not on "the value
happens to be a dict" — only a terminal category's value is a dest-keyed map, and deriving the set
from the keyspace is what stops this falling behind the next flip. A terminal target whose value is
NOT a map (a malformed `pref.agent.claude.common: "oops"`) yields the bare target, which is exactly
what the adapter's own error names.

⚑ THE WHOLE-KEY PREDICATE, NOT THE SUFFIX ONE (QC). `req.target` is a canonical scope-rooted key, so
a category token counts only where the SCOPE ends. Under the suffix test a dict at a scalar leaf
ending in a category token would have been expanded into per-destination entry keys that are not
keys.

`pref_origin` answers the enrichment question. A collision error identifies an entry by the
DECLARATION KEY plus that entry's DEST (`agent.claude.common.~/newthing`) — an identifier a user who
wrote `pref.agent.claude.common` never wrote and cannot write, because the dest lives INSIDE the
value that pref carries. This lets the one CLI seam that renders such an error say where the entry
actually came from.

Matching is containment in `pref_entry_keys`, which is the EXACT `PrefRequest.target` for every
non-terminal target and the per-destination expansion for the seven terminal dest-keyed categories.
Last request wins, matching the overlay precedence (box after workset).

⚑ `pref_request_for` is deliberately NOT reused there. Its contract is exact target equality, the
read that agent SELECTION depends on (`pref_value(prefs, "system.agent")`), and widening it so this
diagnostic could reach the dest-keyed categories would silently change that read. Two questions, two
functions.

## The hand-constructed-request guard

`apply_prefs` also refuses a request whose `level` is not in `PREF_LEGAL_LEVELS`. That is
unreachable from the collector, which labels by FILE, but a caller constructing requests by hand
must not be able to smuggle in a level where a pref is illegal — that is the recursion bound.
