# Config Destinations — where a key's value LIVES, answered once

`config_dest` answers one question, and every config verb has to answer it before it can act: given
a canonical key and the scope the command was issued at, WHICH FILE and which nested slot does this
value occupy? Read, write and remove must answer it identically, or a value written by `set` is
invisible to `get`.

That is not hypothetical. That exact divergence shipped, was found in an audit, and was repaired by
hand in `3b67e61` — without removing the thirteen copies of the test that made it possible. This
module is where the question is answered once, so there is no second opinion left to drift.

⚑ **THE ROUTE IS A DESTINATION, NOT A JUDGEMENT.** Whether a key EXISTS is spec §0's closed
keyspace, owned by `kanibako.settings.settings_keyspace`; which FAMILY a spelling belongs to is
`kanibako.settings.config_keys`. This module consumes both and re-implements neither — it maps an
already-classified key to a path. Keeping that line is what stops a routing layer from quietly
becoming a third opinion about what a key is.

Layering: `config_keys` → `config_dest` → `config_interface`.

## The closed arc: a destination is DATA, never split on a dot

⚑⚑ **A DESTINATION IS DATA, NOT A KEY PATH.** The comment at `_category_segments` is the fourth
known site of one root cause (`509592a`, `5958572`, `dacd9b7`) — and not the last: a FIFTH lived in
the per-agent file's own address rule, where `agent_file`'s bindings arm did `tail.split(".")`,
until S3 replaced it with a partition rule.

Splitting a per-entry spelling on `.` cut `box.caches.~/.cache/uv` into a section `~/` and a leaf
`cache/uv`, so the read landed on a slot no file has, and a hand-authored entry read back
"(not set)". That is what made `config_keys.scope_bind_retired_error`'s closing promise — *"reading
it back still works"* — false for exactly the destinations users actually have, now that
destinations are guest-side paths.

The rule: **the key STOPS at the terminal category (spec §2a), and everything after it is ONE
destination.** `_category_segments` walks prefixes and returns the addressing segments with that
destination kept whole.

⚑ **The WHOLE-key predicate, never the suffix one (QC).** A scalar leaf that merely ENDS in a
category token — `system.channels.common` — must not have its siblings' path cut apart.

⚑ **PARSING THE STRING IS THE JOB HERE**, which is why the P4 objection that kept this predicate
OUT of `settings_categories`' derivations does not apply. `509592a` objected there that the code
"re-parses a string we joined ourselves" — true there, because that site HELD the segments and threw
them away by joining. Here the input is a canonical key the user TYPED at the CLI: nothing was
joined, so there is nothing to carry.

🛑 **Do not rebuild a per-category destination carrier to "fix" this.** The dotted-dest arc is
closed; the destination stays one opaque segment of data.

## The per-node agent families

Four families route to a per-node agent file rather than to a scope's settings file:
`agent.<node>.<persona setting>`, `agent.<node>.bindings.{ro,rw}.<name>`,
`agent.<node>.secret_path.<VAR>`, and the bare terminal category keys. They share one recipe,
`_agent_node_route`.

### A SLOT, not an address

⚑ `_agent_node_route` used to hand back `(path, sections, leaf)`, which put a `self`-rooted file
ADDRESS in every caller's hands — internal traffic in a FILE-SURFACE alias ([spec:15-21, "self"]).
It now returns an `AgentFileSlot` carrying the node and the key TAIL; the address is produced inside
`kanibako.settings.agent_file` when the value is actually read or written.

### ONE HOME for the recipe

Every per-node key resolution repeats the same four steps: the reserved any-agent tier refusal, the
validate-only ref check, the store path, and the file-shape lookup. The four sites that used to
carry it copied steps two through four verbatim and differed only in the parse that produces *node*
and *tail* — exactly the shape a rule takes just before one copy drifts. The inline copy in
`_set_category_value` had already dropped BOTH guards, so `set` wrote node refs that `get` and
`reset` then refused to touch. (That inline copy is gone with the bind write route it served — R-9.)

### The guard pair

`check_agent_node` is the pair, and it refuses two conditions:

* **`default` is RESERVED**, the any-agent tier name (`read_agent_settings`: *"no real agent may be
  named default"*). The launch never reads an `agents/default/` dir as a node, so routing one would
  breach the keystore-maps-to-a-real-key rule and foot-gun a user who wants the any-agent default —
  that is the BARE key, e.g. `system set model=…`.
* **A MALFORMED node ref**, caught by `parse_agent_ref`. The node is used AS-IS for the dir and only
  VALIDATED here, never re-swapped: canonicalisation happened once, at `config_keys.resolve_key`. So
  breaking the `resolve_key` swap routes a `+` key to an `agents/<node-with-+>/` dir the resolver
  never reads — the canonicalization mutation the gate proves.

`check_agent_node` was split out of `_agent_node_route` for a caller that supplied its own
destination file (the `agent.<node>.bindings.*` category repoint, whose node file the command
handler had already resolved) and therefore needed the guards WITHOUT the path lookup. ⚑ That caller
is GONE — R-9 retired the bind CLI write route, and the refusal now runs before any node is parsed.
The split is kept anyway, because the guard PAIR is the rule, and a rule spelled once cannot drift
back into the two-of-four-steps copy that let `set` write node refs `get` and `reset` then refused
to touch.

### Why a REFUSAL object rather than an error

`NodeRouteRefusal` carries the REASON (`"reserved"` or `"malformed"`, plus the `ConfigError` text
for the malformed case) instead of a message. The four call sites that resolve a node key refuse the
SAME two conditions and then say four different things about them, because a persona `set` owes the
user a cure while a `get` of the same shape just reads back "(not set)". Returning the reason keeps
the rule in one place without pretending the callers want one voice: the recipe decides WHETHER a
route exists, each caller decides what to say about it.

### The system-scope reachability rule

All four per-node resolvers return `None` when *agents_root* was not threaded. The per-node store is
GLOBAL, under `config.agents`, so it is reachable only at the SYSTEM scope — a caller that did not
thread its root is not at a scope that can see it.

### `_persona_agent_target` — the three answers

* an `AgentFileSlot` — the node's `agents/<node>/settings.yaml` plus the key TAIL (`model` for a flat
  state leaf, `env.<VAR>` for an env pointer). Where inside the file that lands is the boundary's
  business, not this module's;
* an `"Error: ..."` string — a MALFORMED node ref (validated, never routed), or the reserved-tier
  refusal rendered as the cure ("set the any-agent default with the bare key");
* `None` — not a persona key, or *agents_root* was not supplied.

### `_node_bind_target` — READ-ONLY since R-9

It resolves `agent.<node>.bindings.{ro,rw}.<name>` (item-0) to its FILE READ location, and it was
the get/reset twin of a `config set` repoint. That write route is retired and the verbs refuse the
key by name, so the ONE caller left is `config_interface.get_config_value`. The read survives
because the key does: still declared, still hand-authored in this very file, still delivered at
launch — and hand-editing it is the cure the refusal prescribes.

⚑ **The read-only claim is true of EVERY verb since S3.** Before S3 the `agent` noun had its own
writer and no gate, so `agent set claude bindings.ro.x=…` was a live write route past this one. It
now takes the SAME retirement refusal, from the same recogniser.

⚑⚑ The returned slot's tail is `bindings.<ro|rw>.<dest>`, and `kanibako.settings.agent_file` places
it at EXACTLY the table the launch reads — `self: bindings: <arm>:`, flat, with the DESTINATION
whole (S2 flattened the read, S3 the address rule). The read this function serves and the read the
cascade performs are therefore ONE address. Before S3 they were two, and a hand-authored dotted
destination read back "(not set)" here while the launch delivered it (D-4).

### `_node_secret_target` — the get/set/reset symmetry twin

It resolves `agent.<node>.secret_path.<VAR>` (SECRET category) to a slot on the node's own settings
file with the tail `secret_path.<VAR>`. `agent_file` places it at EXACTLY the table
`_agent_partial` reads into the launch cascade and `agent_file.load` reads back into
`AgentConfig.secret_path`. Its `None` conditions mirror `_node_bind_target`'s.

## The FILE-scope destination rule (H2)

### `DestRoute` and the optional path

`DestRoute` is a file plus the nested slot inside it. `path` may be `None` when the caller supplied
no file for the chosen arm — the verbs already treat a missing file as "nothing stored", so the
route stays representable rather than raising.

The `file` property is the WRITE side's accessor. A write always has a file: the verbs that write
take a required `config_path`, so every arm of the rule resolves to a real path. Reads may
legitimately have none (a scope with no settings file yet), which is why `path` is optional and this
accessor exists — the invariant is asserted ONCE there instead of at each of the ten write sites.

### `noun_settings_file` — the one occurrence of this test

*settings_path* when the noun keeps its settings apart from its config file (the system scope), else
the noun's own file.

⚑ This test was written out THIRTEEN times across the verbs, where it silently did double duty as
"am I the system scope" — and two of those copies drifted into a user-reachable split between where
`set` wrote and where `get` read. `3b67e61` re-synced them by hand and left the copies in place.
Here it answers only the question it can actually answer: does this noun keep its settings in a
separate file?

### The three file rules — `_NOUN`, `_SCOPED`, `_CATEGORY`

Which FILE rule a family follows:

* `NOUN` — always the noun's settings file;
* `SCOPED` — the key's own scope token picks between the settings file and the command's config file;
* `CATEGORY` — the bind-shaped category families, which follow the `SCOPED` rule.

This is a per-FAMILY fact, not a per-caller option: the pref request, the non-agent secret pointer,
the non-agent env var and the bare agent key are SETTINGS by construction and have no config-file
form, while a category or routed key can land in either. It reads as a field here and becomes a
field on the KeyKind descriptor later — the same fact, declared once.

⚑⚑ **CATEGORY AND SCOPED NOW PICK THE SAME FILE, AND THAT IS THE REPAIR, NOT AN OVERSIGHT.**
`CATEGORY` was distinguished for exactly one reason: it carried the deliberately-broken agent-scope
WRITE arm — an `agent.<node>.<category>` set aimed at the command's own config file, which is in no
cascade level, so it was a SILENT NO-OP write. DS-BL1 = (a) retired the category write route,
leaving the arm unreachable from every verb, and QA′ (2026-08-08, on Jei's word) deleted it.

⚑ **THE TERM IS KEPT ANYWAY, DELIBERATELY, AND IT IS NOT DEAD DATA.** `_key_slot` still answers
`CATEGORY` for every TERMINAL category key at every scope and for every FILE-scope per-entry
spelling — it is the declared FAMILY of the key, which is the fact this triple exists to carry into
the KeyKind descriptor. What it no longer does is change the destination. Collapsing it into
`SCOPED` would throw away a family distinction to save a string compare that no longer happens.

### `_key_slot` — the six FILE-scope families

`_key_slot` returns `(sections, leaf, file_rule)` and covers the six families whose value lives in a
scope's own settings/config file. The per-node agent families are NOT here — their slot depends on
the node's file shape and is resolved by `_agent_node_route`. A key no family claims returns `None`,
and falls through to `_KEY_ROUTES`.

`<scope>.env.<VAR>` is the SIBLING of the scope secret pointer: a scalar in the noun's settings file
at `<scope>.env.<VAR>`, the shape `settings_assemble._file_partial` reads into the cascade and
`settings_launch._emit_scope_node` emits as a `category="env"` entry. It answers `_NOUN` for the
same reason the secret pointer does — env is a SETTINGS category and has no Layer-1 config-file form.

#### The category branch: three terms, one slot rule

The two bind arms and the terminal-category arm share ONE slot rule because they are one storage
shape: a category tuple at the nested dotted path in the scope's settings file. That is why one
branch serves three questions (2026-08-08c):

* **`is_terminal_category_key`** — the DECLARED keys: `<scope>.masks`, `<scope>.bindings.{ro,rw}`
  and `<scope>.{caches,seeded,common,synced}`, each holding a whole dest-keyed map. This term is
  what pays the debt the terminalization opened: since P6 a hand-authored `box.bindings.ro` read
  back "(not set)" because no term claimed the BARE key, and the four would have joined it here. A
  declared key must be readable (spec §0).

  ⚑ THE WHOLE-KEY PREDICATE, NOT THE SUFFIX ONE (QC). The suffix test claimed
  `system.channels.common` and `workset.channels.common` — CHANNEL type-roots, ordinary path
  SCALARS — while their siblings `…channels.chat` / `…channels.share` fell through to `_KEY_ROUTES`,
  so one family was read by two rules. MEASURED, both keep their read: `system.channels.*` is a
  STRUCTURAL system path and `get_config_value` reads it from the config file before this rule is
  consulted (which is why `chat` already read fine with no slot at all), and
  `workset.channels.common` has a `_KEY_ROUTES` entry giving the IDENTICAL `(sections, leaf)` — only
  the family label changes, and CATEGORY and SCOPED pick the same file.
* **`_is_scope_bind_key`** — the RETIRED per-name FILE-scope spelling, kept claimed so the read lands
  somewhere explicable rather than falling to the unknown-key table.
* **`_is_path_category_key`** — ⚑ now answers False for EVERY key: it is `BIND_KEY_RE`, whose
  non-terminal complement emptied in the same pass. The term is left in place rather than quietly
  dropped because deleting the predicate is a separate, ruled follow-up (QA′) with two other callers;
  it is named so a reader does not take it for a live route.

⚑⚑ **BOTH BIND ARMS ARE NOW READ-ONLY.** R-9 retired the CLI *write* route for
`{system,workset,box}.bindings.{ro,rw}.<name>`; DS-BL1 = (a) (2026-08-07g) retired it for `caches` /
`seeded` / `common` / `synced` at every scope as well. The write verbs refuse all six in their
preamble before any destination is resolved — but the keys are still DECLARED and still authored in
YAML, so `config get` must keep reading the value the launch actually uses. Dropping the slot
instead would make a hand-authored key read back "(not set)": a silent lie, and the exact
get/set-asymmetry class of bug this rule site exists to prevent.

⚑ **CONSEQUENCE FOR `_CATEGORY`, MEASURED:** with no category WRITE left, no `_CATEGORY` slot can
ever reach `_write_dest`, so the deliberately broken agent-scope arm that used to live there is
unreachable — which is why it could be deleted rather than repaired.

⚑ **SEGMENTS, NOT A DOTTED SPLIT.** The category branch calls `_category_segments`, so a per-entry
destination stays one segment. For a TERMINAL key there is no entry and the segments are the key's
own, so one rule serves both: the value lives at the last addressing unit, whatever that unit is.

### `_dest` — the shared body

*command_scope* is accepted and deliberately unused for the file choice: the scope a command was
issued at does not pick the file, the noun's own file layout does (`noun_settings_file`). It is
threaded so the rule site HAS the scope — the H2 design's explicit-scope requirement — for the
refusal and descriptor work that consumes this route, instead of inferring the scope from a path
being non-`None` the way the copies did.

⚑ **THERE IS NO `agent_scope_to_config` PARAMETER ANY MORE, AND ITS ABSENCE IS THE POINT:** read and
write now answer IDENTICALLY for every key. The flag WAS the deliberately-broken agent-scope
category WRITE arm, deleted in QA′ once DS-BL1 = (a) had made it unreachable from every verb. Do not
reintroduce a per-caller destination switch here — *"set writes where get cannot read"* is the exact
bug class this module exists to prevent, and a flag is how it got in.

### `_write_dest` and `_read_dest` — two names, one answer

`_write_dest` is where `set` writes and `reset` removes a FILE-scope key. `_read_dest` is where a
plain `get` reads it: the value STORED at this noun.

⚑⚑ **THE KNOWN-BROKEN AGENT-SCOPE CATEGORY ARM IS GONE (QA′, 2026-08-08).** It aimed a non-bind
agent-scope category (`agent.<node>.{common,caches,seeded,synced}`) at the command's own file, which
is in no cascade level, so the set was a SILENT NO-OP — the state `3b67e61` found, deliberately left
alone and NAMED rather than smuggled a fix into. It died by its ROUTE being retired, not by being
repaired: DS-BL1 = (a) retired the category write route, after which no `_CATEGORY` slot could reach
`_write_dest`. MEASURED end-to-end: `set` and `reset` fall through to the routing table and answer
"unknown config key" for every agent-scope terminal category key, and all six bind-shaped categories
are refused BY NAME in the verb preamble at the file scopes. With no reachable caller the arm was
deleted rather than left as a flag that documents a bug.

The read side had always used the noun's settings file for an agent-scope category while the write
side aimed at the command's own file; that asymmetry WAS the broken destination, and removing the
write half is what closed it.

⚑ **SO THE TWO FUNCTIONS ARE NOW BYTE-IDENTICAL, AND BOTH NAMES ARE KEPT ON PURPOSE.** Agreement
between the write route and the read route is this module's whole reason to exist; two names that
provably resolve the same way state that at every call site. Merging them is a naming decision, not
a behavioural one, and it is a separate pass.

### The agent-scope category route is still wrong — the half QA′ did not touch

⚑ **WHAT REACHES THAT ROUTE NOW IS THE TERMINAL KEY, NOT AN ENTRY.** It used to be read for
`config get agent.<node>.common.<name>`; the 2026-08-08c shape flip made that spelling not a key at
all (`_is_path_category_key` answers False for every key), so the only agent-scope category keys
that still route here are the bare terminal ones — `agent.<node>.{caches,seeded,common,synced}`,
`agent.<node>.bindings.{ro,rw}`, `agent.<node>.masks`.

⚑⚑ **AND FOR THOSE THE ROUTE IS WRONG, MEASURABLY.** It answers the NOUN's settings file, while the
agent tier is assembled from `agents/<node>/settings.yaml`'s FLAT category tables directly under
`self:` (`settings_assemble._agent_partial`; the S2 flatten — and a nested `self.<node>` table is now
REFUSED by name). So a hand-authored `self.caches` reads back "(not set)" while a stray
`agent.claude.caches` in the system settings file reads back instead.

Re-pointing it is a STORAGE-SHAPE change that moves `agent_file`'s address rule — the per-agent
file-shape SoT shared with the `agent` noun's own verbs — and is a separately-boarded pass. ⚑ Until
it lands, NO message may promise that `config get <agent terminal key>` works (see
`config_keys.agent_node_bind_retired_error`).

⚑ **S3 did NOT close this.** It gave the `agent` NOUN's own verbs the boundary read (`agent get
<node> caches` answers off the agent file), which is a second route to the same value, not a repoint
of this one.

## Retired: the `system.default_agent` special case

⚑ `system.default_agent`'s four-site SPECIAL CASE is GONE (P7). The key is now `system.agent` (spec
§2g) and routes like any other scope-prefixed settings key, through `_KEY_ROUTES` → the `system:`
table of the settings file. The special case existed only because the old spelling was stored in the
reserved `agent.default` table — a location that made it an undeclared key inside the AGENT tier of
the real cascade.
