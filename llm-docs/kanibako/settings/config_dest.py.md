# Config Destinations — where a key's value LIVES, answered once

`config_dest` answers one question, and every config verb has to answer it before it can act: given
a canonical key and the scope the command was issued at, WHICH FILE and which nested slot does this
value occupy? Read, write and remove must answer it identically, or a value written by `set` is
invisible to `get`.

This module is where the question is answered once, so there is no second opinion left to drift.

⚑ **THE ROUTE IS A DESTINATION, NOT A JUDGMENT.** Whether a key EXISTS is spec §0's closed
keyspace, owned by `kanibako.settings.settings_keyspace`; which FAMILY a spelling belongs to is
`kanibako.settings.config_keys`. This module consumes both and re-implements neither — it maps an
already-classified key to a path. Keeping that line is what stops a routing layer from quietly
becoming a third opinion about what a key is.

Layering: `config_keys` → `config_dest` → `config_interface`.

## The closed arc: a destination is DATA, never split on a dot

⚑⚑ **A DESTINATION IS DATA, NOT A KEY PATH.** `_category_segments` and `agent_file`'s partition
rule both keep it whole.

Splitting a per-entry spelling on `.` would cut `box.caches.~/.cache/uv` into a section `~/` and a
leaf `cache/uv`, so the read would land on a slot no file has, and a hand-authored entry would read
back "(not set)" — making `config_keys.scope_bind_retired_error`'s closing promise (*"reading it
back still works"*) false for exactly the destinations users have.

The rule: **the key STOPS at the terminal category (spec §2a), and everything after it is ONE
destination.** `_category_segments` walks prefixes and returns the addressing segments with that
destination kept whole.

⚑ **The WHOLE-key predicate, never the suffix one (QC).** A scalar leaf that merely ENDS in a
category token — `system.channels.common` — must not have its siblings' path cut apart.

⚑ **PARSING THE STRING IS THE JOB HERE**, which is why the P4 objection that keeps this predicate
OUT of `settings_categories`' derivations does not apply. There, re-parsing would be re-parsing a
string the code joined itself, throwing away segments it held. Here the input is a canonical key the
user TYPED at the CLI: nothing was joined, so there is nothing to carry.

🛑 **Do not rebuild a per-category destination carrier to "fix" this.** The dotted-dest arc is
closed; the destination stays one opaque segment of data.

## The per-node agent families

Four families route to a per-node agent file rather than to a scope's settings file:
`agent.<node>.<persona setting>`, `agent.<node>.bindings.{ro,rw}.<name>`,
`agent.<node>.secret_path.<VAR>`, and the bare terminal category keys. They share one recipe,
`_agent_node_route`.

### A SLOT, not an address

⚑ `_agent_node_route` returns an `AgentFileSlot` carrying the node and the key TAIL, never a
`self`-rooted file ADDRESS — that would be internal traffic in a FILE-SURFACE alias
([spec:15-21, "self"]). The address is produced inside `kanibako.settings.agent_file` when the
value is actually read or written.

### ONE HOME for the recipe

Every per-node key resolution repeats the same four steps: the reserved any-agent tier refusal, the
validate-only ref check, the store path, and the file-shape lookup. The resolvers differ only in
the parse that produces *node* and *tail*; copies of the rest would drift, and a copy that dropped
the guards would let `set` write node refs that `get` and `reset` then refuse to touch.

### The guard pair

`check_agent_node` is the guard, and it refuses three conditions under two reasons (`"reserved"`
for the first, `"malformed"` for the other two):

* **`default` is RESERVED**, the any-agent tier name (`read_agent_settings`: *"no real agent may be
  named default"*). The launch never reads an `agents/default/` dir as a node, so routing one would
  breach the keystore-maps-to-a-real-key rule and foot-gun a user who wants the any-agent default —
  that is the BARE key, e.g. `system set model=…`.
* **The agent file's alias** (`settings_keyspace.file_alias_reason`, the §0 verdict's own carrier),
  refused as a malformed ref: it parses as an agent name, so unrefused, `system set
  agent.self.model=x` would write an agent file for a node named `self` that `system get` refuses.
* **A MALFORMED node ref**, caught by `parse_agent_address` — the ADDRESS grammar, so the `shell`
  node routes: its §2d fence declares `agent.shell.*` and a settings file the shell tier owns
  (`[R175]`), and routing the owner's own settings claims nothing. The node is used AS-IS for the dir and only
  VALIDATED here, never re-swapped: canonicalization happened once, at `config_keys.resolve_key`. So
  breaking the `resolve_key` swap routes a `+` key to an `agents/<node-with-+>/` dir the resolver
  never reads — the canonicalization mutation the gate proves.

`check_agent_node` stands apart from `_agent_node_route` because the guard is the rule, and a rule
spelled once cannot drift into a copy that lets `set` write node refs `get` and `reset` refuse.

### Why a REFUSAL object rather than an error

`NodeRouteRefusal` carries the REASON (`"reserved"` or `"malformed"`, plus the refusal text for the
malformed case) instead of a message. The call sites that resolve a node key refuse the SAME
conditions and then say different things about them, because a persona `set` owes the
user a cure while a `get` of the same shape just reads back "(not set)". Returning the reason keeps
the rule in one place without pretending the callers want one voice: the recipe decides WHETHER a
route exists, each caller decides what to say about it.

### The system-scope reachability rule

All four per-node resolvers return `None` when *agents_root* was not threaded. The per-node store is
GLOBAL, under `config.agents`, so it is reachable only at the SYSTEM scope — a caller that did not
thread its root is not at a scope that can see it.

### `_persona_agent_target` — the three answers

* an `AgentFileSlot` — the node's `agents/<node>/agent.yaml` plus the key TAIL (`model` for a flat
  state leaf, `env.<VAR>` for an env pointer). Where inside the file that lands is the boundary's
  business, not this module's;
* an `"Error: ..."` string — a MALFORMED node ref (validated, never routed), or the reserved-tier
  refusal (`_reserved_tier_refusal`, below);
* `None` — not a persona key, or *agents_root* was not supplied.

### `_reserved_tier_refusal` — why the cure has TWO arms

`default` is the any-agent tier, so there is no `agents/default/agent.yaml` to write and the refusal
is right. The old message then said one thing to everybody: *"set the any-agent default with the bare
key (e.g. `<tail>`)"*. That is true only where a bare key EXISTS, and one tail reaches this arm where
it does not.

`agent.default.env.FOO` is a DECLARED key — keyspec §2a declares `<scope>.env.<VAR>` at every scope
and names `agent.default` among them — and the bare cure for it would be `env.FOO`, the RETIRED
bare docker-`.env` spelling (R-39), which `config set env.FOO` refuses. *A refusal that prescribes
a failing command is worse than no cure at all.*

**WHY `_is_agent_setting` IS THE TEST AND NOT A LIST.** It is the predicate `config_interface`'s own
bare-write branch dispatches on, so "this tail has a working bare spelling" and "the bare cure is
reachable" are the SAME question asked once. It derives through `SCALAR_AGENT_LEAVES` =
`DECLARED_AGENT_LEAVES − TABLE_VALUED_AGENT_LEAVES`, so a leaf declared tomorrow lands on the right
arm with no edit here (P13), and nothing is exempted by name.

**`env.<VAR>` DOES NOT REACH THE FILE ARM.** `agent.default.env.<VAR>` and
`agent.default.secret_path.<VAR>` are the any-agent tier's two SCALAR category families,
`agent_default_tier_category` claims their slot, and every verb DISPATCHES them ahead of this
refusal. The arm's only remaining member is `transform_settings`, and it never arrives either:
`agent_leaf_table_error` refuses it a branch earlier and names the SAME file. The arm still needs
no list — it is the answer for any future declared leaf that has no bare spelling.

**THE FILE CURE IS TRUE OF RUNNING CODE.** A system settings file carrying `agent: default: env:
{UNIVERSAL: …}` produces the category entry `agent.default.env.UNIVERSAL` and collapses into
`meta.assembly.env`. The sentence is `config_keys._AGENT_DEFAULT_TIER_CURE`'s — one key, one
destination, one sentence, whichever door a user knocks on.

**THE READ IS CLOSED AT THE ROUTE.** `get_config_value` gates its persona branch on
`is_agent_default_tier_key` — both halves of the tier's vocabulary, not `agent_default_tier_leaf`,
which declines the dotted `env.` tail — so `config get agent.default.env.FOO` falls through to
`_read_dest` and lands on the slot `set` wrote.

### `_reserved_tier_read_refusal`, `_reserved_tier_category_refusal`, `_reserved_tier_store_refusal` — the other cures

The `agent` noun (`agent_cmd._missing_store_error`) refuses `default` BEFORE any route is asked when
there is no `agents/default/` folder, and it must say what the engine says when a stray folder
exists: two messages for one refusal is the defect (P10). So the refusal text has ONE home here,
with one subject (`_RESERVED_TIER_HEAD`) and four cures, picked by what the verb asked:

* a key WRITTEN or RESET — `_reserved_tier_refusal(tail)`, the engine's own tail-aware choice;
  EXCEPT the tier's category entries (`env.<VAR>`, `secret_path.<VAR>`), which the engine routes
  AHEAD of that refusal (`config_keys.agent_default_tier_category`). A caller asking before the
  engine's dispatch must take the same branch, so they get `_reserved_tier_category_refusal(tail)`,
  naming their working system-scope key `agent.default.<tail>`;
* a key READ (`agent get default <tail>`) — `_reserved_tier_read_refusal(tail)`. A write cure is
  wrong there. The read that works is `kanibako system get agent.default.<tail>`, because
  `get_config_value` sends that spelling to the tier's slot (above); the command is
  `config_keys._SCOPE_READ_COMMAND["system"]`, not a new spelling;
* no key (`show`, `info`, `reset --all`) — `_reserved_tier_store_refusal()`: the tier has no store,
  and `_AGENT_DEFAULT_TIER_CURE` names the file that holds it.

### `_node_bind_target` — READ-ONLY (R-9)

It resolves `agent.<node>.bindings.{ro,rw}.<name>` (item-0) to its FILE READ location. There is no
write route — the verbs refuse the key by name — so the ONE caller is
`config_interface.get_config_value`. The read exists
because the key does: still declared, still hand-authored in this very file, still delivered at
launch — and hand-editing it is the cure the refusal prescribes.

⚑ **The read-only claim is true of EVERY verb:** `agent set claude bindings.ro.x=…` takes the SAME
retirement refusal, from the same recognizer.

⚑⚑ The returned slot's tail is `bindings.<ro|rw>.<dest>`, and `kanibako.settings.agent_file` places
it at EXACTLY the table the launch reads — `self: bindings: <arm>:`, flat, with the DESTINATION
whole. The read this function serves and the read the cascade performs are therefore ONE address.

### `_node_secret_target` — the get/set/reset symmetry twin

It resolves `agent.<node>.secret_path.<VAR>` (SECRET category) to a slot on the node's own settings
file with the tail `secret_path.<VAR>`. `agent_file` places it at EXACTLY the table
`_agent_partial` reads into the launch cascade and `agent_file.record` reads back into
`AgentConfig.secret_path`.

⚑⚑ **IT HAS THREE OUTCOMES.** A slot; `None` for an UNTHREADED `agents_root` ("no store here");
and an `"Error: …"` string for a REFUSED node, exactly as `_persona_agent_target` returns. Folding
the refusal into `None` would force the caller to guess which refusal happened, and the guess
prints a false sentence (*"only settable at the system scope"* at the system scope). The two are
the per-node write routes of one tier and a caller handles them identically.

⚑ **NO `reserved` ARM, BY DISPATCH.** `agent.default.secret_path.<VAR>` is a real key with a real
destination and every verb claims it before this route is consulted, so the reserved node cannot
arrive. The arm exists only to NAME that condition if the claiming branch is ever removed — it does
not re-cure the key.

## The FILE-scope destination rule (H2)

### `DestRoute` and the optional path

`DestRoute` is a file plus the nested slot inside it. `path` may be `None` when the caller supplied
no file for the chosen arm — the verbs already treat a missing file as "nothing stored", so the
route stays representable rather than raising.

The `file` property is the WRITE side's accessor. A write always has a file: the verbs that write
take a required `config_path`, so every arm of the rule resolves to a real path. Reads may
legitimately have none (a scope with no settings file yet), which is why `path` is optional and this
accessor exists — the invariant is asserted ONCE there instead of at each write site.

### `noun_settings_file` — the one occurrence of this test

*settings_path* when the noun keeps its settings apart from its config file (the system scope), else
the noun's own file.

⚑ Copies of this test across the verbs would do silent double duty as "am I the system scope", and
two copies that drift split where `set` writes from where `get` reads. Here it answers only the
question it can actually answer: does this noun keep its settings in a separate file?

### The three file rules — `_NOUN`, `_SCOPED`, `_CATEGORY`

Which FILE rule a family follows:

* `NOUN` — always the noun's settings file;
* `SCOPED` — the key is scope-rooted, and its scope token is CHECKED against
  `_SETTINGS_SCOPE_TOKENS` on the way to the settings file;
* `CATEGORY` — the bind-shaped category families, which follow the `SCOPED` rule.

⚑ `config_keys.SETUP_MARKER_KEY` (`system.setup_completed`) is a Layer-2 `system.*` SETTINGS key
(spec §2g), written to and read from `{config.settings}`, so it routes through `_KEY_ROUTES` under
`SCOPED` beside `system.agent`.

🛑 **THERE IS NO "WRITE THE CONFIG FILE" RULE AND THERE MUST NOT BE ONE.** `config.*` keys LOCATE
the files everything else lives in and are refused from every scope before any destination is asked
for (spec §2a), so no key routes to Layer 1 at all.

This is a per-FAMILY fact, not a per-caller option: the pref request, the non-agent secret pointer,
the non-agent env var and the bare agent key are SETTINGS by construction and have no config-file
form. It reads as a field here and becomes a field on the KeyKind descriptor later — the same fact,
declared once.

⚑⚑ **THE SCOPE-TOKEN TEST IS AN ASSERTION, NOT A FORK.** Nothing legitimate reaches a non-scope
token — the routing table, both bind recognizers and `is_terminal_category_key` all require a head
in `SCOPE_CONTAINMENT` — and falling through to the command's Layer-1 config file would let an
UNDECLARED flat spelling (`box_image`) slot at `box: image:` in `kanibako.cfg` below the declared
`box.image`: the spelling would choose the precedence, silently. So the arm says so instead.

⚑⚑ **CATEGORY AND SCOPED PICK THE SAME FILE.** There is no category WRITE route (the bind-shaped
categories are refused by name in the verb preamble), so no category slot can aim at a file outside
the cascade.

⚑ **THE TERM IS KEPT, DELIBERATELY, AND IT IS NOT DEAD DATA.** `_key_slot` answers `CATEGORY` for
every TERMINAL category key at every scope and for every FILE-scope per-entry spelling — it is the
declared FAMILY of the key, which is the fact this triple exists to carry into the KeyKind
descriptor. It does not change the destination.

### `_key_slot` — the six FILE-scope families

`_key_slot` returns `(sections, leaf, file_rule)` and covers the six families whose value lives in a
scope's own settings/config file. The per-node agent families are NOT here — their slot depends on
the node's file shape and is resolved by `_agent_node_route`. A key no family claims returns `None`,
and falls through to `_KEY_ROUTES`.

`<scope>.env.<VAR>` is the SIBLING of the scope secret pointer: a scalar in the noun's settings file
at `<scope>.env.<VAR>`, the shape `settings_assemble._file_partial` reads into the cascade and
`settings_launch._emit_scope_node` emits as a `category="env"` entry. It answers `_NOUN` for the
same reason the secret pointer does — env is a SETTINGS category and has no Layer-1 config-file form.

⚑ **THE AGENT TIER TAKES BOTH OF THEM AT A DEEPER SLOT.** `<scope>` in §2a includes `agent.default`,
so `agent.default.env.<VAR>` and `agent.default.secret_path.<VAR>` are declared too — but the tier is
a TABLE, not a file: there is no `agents/default/agent.yaml`, and `assemble_levels` leaves the
`agent.default` rung structurally empty and reads the tier off the SYSTEM file's `agent: default:`
table. So `agent_default_tier_category` claims `("agent", "default", <category>) / <VAR>` under
`_NOUN` — the same table its bare-leaf sibling `agent_default_tier_leaf` claims, one level deeper.
Without it the tier's only route would be the per-node one, which refuses the reserved node.

#### The category branch: three terms, one slot rule

The two bind arms and the terminal-category arm share ONE slot rule because they are one storage
shape: a category tuple at the nested dotted path in the scope's settings file. That is why one
branch serves three questions:

* **`is_terminal_category_key`** — the DECLARED keys: `<scope>.masks`, `<scope>.bindings.{ro,rw}`
  and `<scope>.{caches,seeded,common,synced}`, each holding a whole dest-keyed map, so a
  hand-authored `box.bindings.ro` reads back. A declared key must be readable (spec §0).

  ⚑ THE WHOLE-KEY PREDICATE, NOT THE SUFFIX ONE (QC). A suffix test would claim
  `system.channels.common` and `workset.channels.common` — CHANNEL type-roots, ordinary path
  SCALARS — while their siblings `…channels.chat` / `…channels.share` fall through to
  `_KEY_ROUTES`, so one family would be read by two rules. Both read through their own route:
  `system.channels.*` is a STRUCTURAL system path that `get_config_value` reads before this rule is
  consulted, and `workset.channels.common` has a `_KEY_ROUTES` entry.
* **`_is_scope_bind_key`** — the RETIRED per-name FILE-scope spelling, kept claimed so the read lands
  somewhere explicable rather than falling to the unknown-key table.
* **`_is_path_category_key`** — ⚑ answers False for EVERY key: it is `BIND_KEY_RE`, whose
  non-terminal complement is empty. The term is named so a reader does not take it for a live
  route; deleting the predicate is a separate change, with two other callers.

⚑⚑ **BOTH BIND ARMS ARE READ-ONLY.** There is no CLI *write* route for
`{system,workset,box}.bindings.{ro,rw}.<name>` (R-9) nor for `caches` / `seeded` / `common` /
`synced` at any scope. The write verbs refuse all six in their preamble before any destination is
resolved — but the keys are DECLARED and authored in YAML, so `config get` must keep reading the
value the launch uses. Dropping the slot would make a hand-authored key read back "(not set)": a
silent lie, and the exact get/set-asymmetry class of bug this rule site exists to prevent. With no
category WRITE, no `_CATEGORY` slot reaches `_write_dest`.

⚑ **SEGMENTS, NOT A DOTTED SPLIT.** The category branch calls `_category_segments`, so a per-entry
destination stays one segment. For a TERMINAL key there is no entry and the segments are the key's
own, so one rule serves both: the value lives at the last addressing unit, whatever that unit is.

### `_dest` — the shared body

*command_scope* is accepted and deliberately unused for the file choice: the scope a command was
issued at does not pick the file, the noun's own file layout does (`noun_settings_file`). It is
threaded so the rule site HAS the scope — the H2 design's explicit-scope requirement — for the
refusal and descriptor work that consumes this route, instead of inferring the scope from a path
being non-`None` the way the copies did.

⚑ **THERE IS NO per-caller destination flag, AND ITS ABSENCE IS THE POINT:** read and write answer
IDENTICALLY for every key. Do not introduce a per-caller destination switch here — *"set writes
where get cannot read"* is the exact bug class this module exists to prevent, and a flag is how it
gets in.

### `_write_dest` and `_read_dest` — two names, one answer

`_write_dest` is where `set` writes and `reset` removes a FILE-scope key. `_read_dest` is where a
plain `get` reads it: the value STORED at this noun.

⚑⚑ **There is no agent-scope category WRITE.** `set` and `reset` fall through to the routing table
and answer "unknown config key" for every agent-scope terminal category key, and all six
bind-shaped categories are refused BY NAME in the verb preamble at the file scopes.

⚑ **SO THE TWO FUNCTIONS ARE BYTE-IDENTICAL, AND BOTH NAMES ARE KEPT ON PURPOSE.** Agreement
between the write route and the read route is this module's whole reason to exist; two names that
provably resolve the same way state that at every call site. Merging them is a naming decision, not
a behavioral one, and it is a separate pass.

### The agent-scope category READ route is wrong

⚑ **WHAT REACHES THAT ROUTE IS THE TERMINAL KEY, NOT AN ENTRY.** `agent.<node>.common.<name>` is
not a key (`_is_path_category_key` answers False for every key), so the only agent-scope category
keys that route here are the bare terminal ones — `agent.<node>.{caches,seeded,common,synced}`,
`agent.<node>.bindings.{ro,rw}`, `agent.<node>.masks`.

⚑⚑ **AND FOR THOSE THE ROUTE IS WRONG, MEASURABLY.** It answers the NOUN's settings file, while the
agent tier is assembled from `agents/<node>/agent.yaml`'s FLAT category tables directly under
`self:` (`settings_assemble._agent_partial`; a nested `self.<node>` table is REFUSED by name). So a
hand-authored `self.caches` reads back "(not set)" while a stray
`agent.claude.caches` in the system settings file reads back instead.

Re-pointing it is a STORAGE-SHAPE change that moves `agent_file`'s address rule — the per-agent
file-shape SoT shared with the `agent` noun's own verbs — and is a separately-boarded pass. ⚑ Until
it lands, NO message may promise that `config get <agent terminal key>` works (see
`config_keys.agent_node_bind_retired_error`).

⚑ The `agent` NOUN's own verbs read the agent file (`agent get <node> caches`), which is a second
route to the same value, not a repoint of this one.

## `system.agent`

`system.agent` (spec §2g) routes like any other scope-prefixed settings key, through `_KEY_ROUTES`
→ the `system:` table of the settings file. There is no special case.
