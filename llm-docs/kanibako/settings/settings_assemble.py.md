# Cascade Level Assembly
_per-scope settings files → the ordered `list[KeyStore]` partials the merge consumes_

Every settings file on disk is scope-ROOTED and every cascade level is a FILE. This module is the
one place that turns those files into the ordered `list[KeyStore]` the merge (`settings_merge`)
walks. It performs READS and structural parsing ONLY: no merge / precedence, no `@`-ref / `$var` /
`~` expansion or cycle detection, no typed views, no `config set`. Tokens are left RAW inside binds
(spec §0 "Files store entries UNRESOLVED").

⚑ **This is the LIVE launch cascade.** Files are READ once, by `read_settings_files` (usually
through `cascade_files`), for a `ReadPurpose` that fixes which refusals run; `assemble_levels` then
builds the partials from those already-read files. The launch path is
`settings_launch.build_launch_snapshot` → `cascade_files(ReadPurpose.RESOLVE)` →
`settings_launch.assemble_cascade` → `assemble_levels`; `commands/start.py` calls it on the launch
path and for `config show --effective`. `config_interface` and `paths` call `cascade_files`
directly; `commands/workset_cmd` reads its workset file through `read_settings_files`
(`ReadPurpose.NARROW`) and reaches `cascade_files` only through `build_launch_snapshot`.

## Authority

* **Spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §2 — the cascade, PRIMARY authority.** The 6-level bracket
  `base < system < agent.default < agent.<active> < workset < box`, reversed here to high→low
  precedence. `agent.default` is an EXPLICIT level and both agent layers reuse the same linear
  `__MISSING__` precedence (no nested mini-cascade) — the LEVEL ORDER *is* the precedence.
  ⚑ §2 also records that `agent.default` "is NOT an authority tier of its own — it is the agent
  tier's FALLBACK"; writing it as a linear level is the IMPLEMENTATION choice this module makes,
  and it is equivalent because `__MISSING__` is tested per entry key.
* **Spec §2d** — the ONLY two agent key forms are `agent.default.<key>` and `agent.<agent>.<key>`
  (a concrete agent name); §0 forbids a bare `agent.<key>`.
* **Spec §2** (cascade + scopes) / **§2a** (categories + value types) / **§0** (namespace ORTHOGONAL
  to cascade; closed keyspace; files store UNRESOLVED).
* **Spec §2** — there is no `machine` tier: the cascade FLOOR is `base` (overridable) and the
  cascade ENDS at `box` (no non-overridable cap). This module consults no machine path.
* Archived design (NOT AUTHORITY):
  `~/canon/notebook/archives/resources/keystore-2026-06/keystore-design.md` §2 (storage —
  partials are `KeyStore`s, binds are `Bind`). The per-entry reset the merge classifies as an
  OMIT: spec §2a "PER-ENTRY CASCADE MERGE".
  ⚑ Cite spec §2, not design §4, for the bracket: design §4 writes it with a 7th `required` tier
  that S14 and spec §2 do not have.

## Seams realized here (`~/canon/workbook/plans/keystore-blocks/SEAMS.md`)

* **S7** — partials are NESTED `KeyStore`s (not flat dotted dicts); a scope file's nested tables are
  mirrored verbatim into the partial, SCOPE TOKEN KEPT.
* **S8** — output order is MOST-SPECIFIC-FIRST:
  `[box, workset, agent.<active>, agent.default, system, base]`. The two agent levels keep their
  TRUE discriminated keys (`agent.<active-name>.*` / `agent.default.*`, §2d) — NO bare-`agent`
  collapse; level order is the cascade precedence.
* **S9** — binds parsed at ASSEMBLY with `@`-refs / `$vars` / `~` left RAW inside the entry
  (expansion is `settings_expand`'s).
* **S13** — ONE unified `KeyStore` partial per level holding BOTH behavior leaves AND category
  subtrees together (single source).
* **S14** — no `machine` tier; floor → `base`; cascade ends at `box` (no `required` cap).
* **S3** — every walk here uses UNBOUND `dict.get` / `dict.__setitem__`, never the bound method: a
  leaf legitimately named `get` must not shadow the protocol into a crash.

## Scope tokens — KEPT; DOWNWARD/same-scope only (§0)

Settings files are scope-ROOTED on disk (`{system: {bindings: {rw: {…}}}}` → `system.bindings.rw`).
The scope token is LOAD-BEARING — it names the DECLARATION ROOT an abstract-category source is
spelled against, and it picks the mount mode for `bindings` — and namespace is ORTHOGONAL to
cascade level (§0).

A file may hold keys of its OWN scope AND of scopes it CONTAINS (`system ⊃ agent ⊃ workset ⊃ box`)
as OVERRIDABLE defaults-down — e.g. a workset file may set `box.*` and it flows. But **directional
enforcement at RESOLVE** (spec §0) DROPS a top-level table of a CONTAINING
scope found in a lower file (`system:` / `workset:` / `agent:` in a box file) at assembly, with a
warning naming the file + token; it never enters the merge (`_drop_upward_scopes`).

So a level partial mirrors its file's WHOLE nested content MINUS any upward table, SCOPE TOKEN KEPT
(`box.image`, `box.caches` at box; a workset file keeps `workset.*` plus its `box.*` defaults) — the
LEVEL identity is the FILE, not a lifted sub-table. The merge then works by scope-qualified name
across levels. The `base` code floor is EXEMPT for SCOPE keys (it is the system-scope floor).
`@`-refs still view UP, read-only.

A top-level `meta:` table is ALWAYS dropped from EVERY file (`base` included) — `meta.*` is a
TOP-LEVEL protected namespace set by the construct-time/bootstrap layer and stays RO everywhere
(spec §0 / clause 4). The sole sanctioned meta source is the runtime/identity FLOOR
(`dotted_partial`), which is never dropped. Warning rules: `_drop_upward_scopes` below.

## The AGENT tier — two cascade levels from one file

The agent tier yields TWO separate cascade levels from the one agent settings file (spec §2): the
all-agents fallback layer `agent.default.<key>` and the per-agent layer `agent.<agent>.<key>`. Each
becomes a SEPARATE cascade LEVEL and the per-agent DISCRIMINATOR is KEPT VERBATIM — these are the
ONLY two agent key forms the spec allows (§2d; §0 forbids a bare `agent.<key>`).

The two levels merge BY THEIR TRUE NAMES; the LEVEL ORDER (active above default, S8) is the explicit
cascade precedence (§2, "explicit in the cascade … no nested mini-cascade"). The thin
active-over-default value-pick (`agent.<active>.<key> | agent.default.<key>`, §2d) is an
effective-agent READ deferred to the consumer, NOT a name collapse here.

Keeping the discriminator preserves §0 per-agent independence: `agent.<other>.*` set within the
AGENT scope (or higher) survives the merge by its own name — but a box file may NOT set
`agent.<other>.*` (that is an upward write, dropped above; a box tweaks its agent through the §2h
`pref.agent.<agent>.<key>` request).

⚑⚑ **The agent file's SHAPE is not this module's** — `settings/agent_file.py` owns it (the root
table, the flat-category splice, the nested refusal, the address a leaf writes to). What is left
here is the STORE COERCION and the §2d wrap: `agent_file_partial` takes an `AgentFileLevel` from
`agent_file.level_table` — which owns both refusals that precede it, the nested `self:` and the
stray top-level key — and turns its raw table into a `KeyStore` under `agent.<sub_key>`. The
seam is cut at the SHAPE so the boundary never imports `KeyStore` and the import edge stays
one-way. See `agent_file.py.md`.

⚑ **THE FILE'S `agent:` TABLE IS READ (Q92)** — the level carries it RAW as `scope`, and
`_scope_nodes` parses it with `file_partial`, the scope files' own builder (node fold, bind
parse, file named in every refusal). The all-agents tier takes its `default` node; the active tier
takes every other node, merged beside the re-rooted `self:` (`_overlay`). What that means, BY
CONSTRUCTION, since the file is read only while its agent is active: in foo's file,
`agent: {bar: …}` merges but is never picked (bar's own file is read when bar is), and
`agent: {default: …}` sets `agent.default.*` on this file's rung — above the system file — applying
only while foo is. ⚑ **WHAT THE BOUNDARY GUARANTEES THE `_overlay`:** `agent_file._contribution`
(every judging reader) has refused an own-node entry that is not a table (`_refuse_node_values`)
and any own-node setting that equals, or is a prefix of or under, one `self:` sets
(`_refuse_two_spellings`, Q103). Before the overlay the level holds at most the own node's `self:`
categories, so nothing `self:` set is replaced; the other nodes land where nothing was. Pinned in `test_agent_file.py` (`TestTheAgentTable`) and
`test_settings_launch.py` (`TestAgentFileAgentTable`, the production pair).

`config.read_agent_settings` PRE-MERGES the default and per-agent sections inside one file; this
module deliberately does not — the separation into two levels is the point.

## Bind-shaped categories — the depth rule

The bind-shaped categories are `bindings.ro`, `bindings.rw`, `caches`, `seeded`, `common`, `synced`.
`masks` (a keyed `dict[box_dest → bool|None]`, S5) and the scalar families `env.<VAR>` /
`secret_path.<VAR>` keep their natural nested shape and are not bind-parsed.

⚑ **EVERY bind-shaped category is DEST-KEYED.** Each is a TERMINAL key whose value is a `BindMap` =
`dict[box_dest → [src[, opts]]]` parsed to `BindEntry` leaves; the destination is the dict key and
the entry's identity, and there is NO entry name (spec §2a REPRESENTATION).
⚑⚑ **The category set that survives is about WHERE the map sits, not WHETHER there is one.**
`bindings` is the one category whose token is not the whole key: it is followed by an ARM (`ro` /
`rw`) and the map is one level DOWN. The other four ARE the map, terminal one level SHALLOWER.
Reading either at the wrong depth would take a destination for an arm name.

⚑ **This is the other half of why the reader cannot use a blanket `dest_keyed=True`:** a leaf under
a bind category is 2-element-legal in BOTH shapes with OPPOSITE meanings (`(host, box)` vs
`(src, opts)`), so the DEPTH of the walk — never the leaf's arity — is what says which one it is.
That is the arity trap `kb_store.BindEntry` documents, and spec §2a calls the loud refusal of
the stale shape load-bearing for exactly this reason.

⚑ The depth is derived from the CATEGORY token `parse_node` walks past, so `file_partial` needs no
flag of its own and there is no flag for a caller to get wrong (P6, P3).

## Retired spellings — refusing by name

Two independent refusals live here. Both share a shape and a reason; they differ only in seam.

⚑ **WHY THEY EXIST AT ALL, given that migration is DOCUMENTATION-ONLY.** Neither is migration
machinery: they read nothing, relocate nothing, and write nothing. They are §0's CLOSED-KEYSPACE
rule — *an undeclared key is an ERROR that NAMES it* — applied to retired spellings, with a
migration-grade message. Scope is deliberately TIGHT: these keys, nothing else.

**Seams.** Both run inside `read_settings_files`, over each file's VIEW (`_file_view`): the
selection and mirror keys under `ReadPurpose.SELECT` (`settings.agent_select`), the behavior key
under `ReadPurpose.RESOLVE` (the launch's `cascade_files` read and `agent_record`). Both also run
through `retired_cure`, which picks the TAILORED message once §0 has decided to refuse: the launch's
`settings_launch._refuse_undeclared_snapshot` and stage (h)'s `refuse_undeclared_entries` call it
before the generic text, because the general refusal would otherwise replace the message written for
a retired spelling. Neither runs inside `assemble_levels`: a raise there would break `config set`,
the very command the message prescribes as the cure. Each remains the ONE carrier of its own text.

**Selection and mirror keys (spec §0 / §2b / §2g).** THREE retired spellings:
`box.agent`, `box.agent_name` and `system.default_agent`. A box that SILENTLY RUNS A DIFFERENT AGENT
— and seeds that agent's CREDENTIALS into itself — is the failure being prevented, so a silent drop
is not an option. The CURE is LEVEL-DEPENDENT: a pref is legal only in a workset or box file (spec
§2h), so telling a SYSTEM-file reader to `box set pref…` would prescribe a write that cannot fix
their file. ⚑ The selection seam threads *box_ref* = `paths.project_cure_ref(proj)` (already
shell-quoted; a member is `<workset>/<box>`, a standalone its root); `retired_cure` passes none, so
its cure carries the `<box>` placeholder.

⚑⚑ **`box.agent` is TWO retirements sharing ONE file path, and the VALUE'S SHAPE is the
discriminator.** Both are manifest `renamed` rows:

* a **SCALAR** is the old agent-SELECTION key (`box.crab` → `box.agent` → `box.agent_name`, all of
  them cured toward `pref.system.agent`, §2h);
* a **TABLE** is the retired SETTABLE MIRROR of the agent's settings, `box.agent.<key>`, cured
  toward the per-agent request `pref.agent.<agent>.<key>` (§2h). The `box.agent` spelling that
  SURVIVES is the RO read-back `meta.box.agent.<key>` (§2b) — a different key, not a tweak channel.

One cure for both would send half of these users at the wrong key, so `refuse_retired_keys` reads
the shape and hands `retired_key_cure` a *mirror* argument that selects between the two. The
FRAMING SENTENCE forks with it, which is why there are TWO story constants: *"a box no longer names
its agent with a key of its own"* is wrong for someone who was tweaking an agent rather than naming
one.

**Behavior key (spec §2d).** The boolean `auto_approve` is retired in favor of the
enum `access` (`restricted|editing|full`, default `full`). Under the closed keyspace the old spelling
is UNDECLARED; on a PERMISSION axis a silently ignored `auto_approve: false` would bring a box up at
the `full` default with nothing printed — a safety-class silent regression in the UNSAFE direction.
So the stale key is REFUSED [Jei:2026-08-02] with a level-appropriate cure that NAMES `access` and
QUOTES the user's own value through the ruled mapping (`true` → `full`, `false` → `restricted`). ⚑
The generic refusal says none of that, which is why `retired_cure` runs first.
⚑ NOT EVERY SITE ARRIVES HERE, and none of the others is a silence. A
`pref.agent.<node>.auto_approve` request is refused by `apply_prefs` naming the key, the level, and
the file, but WITHOUT the tier translation. A `workset`/`box` file's `agent.<sub>.auto_approve` is
dropped as an upward scope with a warning and never becomes a key at all; the scans read the VIEW,
so that table cannot HIJACK another key's refusal to prescribe a cure for itself. The same holds
for a `pref:` table in a base / system / agent file, which §2h drops before the merge. ⚑ `base` IS
scanned: `cascade_files` reads `settings_base_path()` itself, and a stale key in
`/etc/kanibako/settings_base.yaml` defaults DOWN into every box on the machine.

## Values

```_DEST_KEYED_CATEGORY = "bindings"```
The ARMED bind-shaped category — the one whose category token is not the whole key.

```_BIND_ARMS: tuple[str, str] = ("ro", "rw")```
The two arms a dest-keyed `bindings` node carries; each holds a `BindMap`.

`settings_keyspace.BIND_LEAF_CATEGORIES` (imported, never copied here) is the set of bind-shaped
categories whose CATEGORY TOKEN IS THE WHOLE KEY: `{"caches", "seeded", "common", "synced"}` —
terminal ONE LEVEL SHALLOWER than a `bindings` arm, with a `BindMap` for a value. See "the depth
rule" above for why this set is not redundant with `BIND_CATEGORY_TOKENS`.

```_MARKER_LEAF_CATEGORIES: frozenset[str]```
The dest-keyed terminal categories whose token is the whole key but whose value is the 3-state MARKER
rather than a `BindMap` — `{"masks"}`, DERIVED as every one-segment `TERMINAL_CATEGORY_TAILS` entry that
is not a bind leaf. `parse_node` and `_insert_dotted` hand such a map to `parse_marker_map`, which
canonicalizes each dest with `normalize_bind_dest` (spec §2a "A MOUNT DESTINATION IS CANONICALIZED")
and keeps each value as authored, so `masks: {/m/: true}` and a lower scope's `{/m: null}` are one
entry and the unmask meets the mask. Like `parse_bind_map`, two spellings of one dest inside ONE map
collapse last-wins. `//`, `.` and `..` are NOT collapsed.

```BIND_CATEGORY_TOKENS: frozenset[str]```
The bind-shaped category tokens whose subtree holds bind entries. Spelled
`frozenset({_DEST_KEYED_CATEGORY} | BIND_LEAF_CATEGORIES)`.

`{"bindings", "caches", "seeded", "common", "synced"}` — the tokens as they appear in a file. These
are path SEGMENTS met on a tree walk, so `bindings` is UNSPLIT here: the walk meets that segment
before it can see the arm. `bindings` carries the `ro` / `rw` sub-tables, each of which holds the
map; the other four hold it directly. Also read by `settings_merge`, which is why the name carries
NO leading underscore — the import crosses a module boundary.

⚑ **DERIVED (P13), not hand-listed** — the ARMED category plus the terminal leaves ARE the tokens,
so the declaration sits BELOW its two operands and the spellings cannot drift. No pin test guards
it, and none should be added: both operands live in THIS file, so there is nothing to drift against
and a pin would assert a tautology. Contrast `settings_categories._TERMINAL_BIND_CATEGORIES`, which
mirrors a constant in a module it deliberately cannot import and therefore DOES need one.

⚑ The `frozenset(...)` wrap is load-bearing, not decoration: `{x} | frozenset(...)` evaluates to a
plain `set`, so without it the annotation would be a lie.

```_AGENT_DEFAULT_SUB = "default"```
The agent sub-table that supplies the all-agents `agent.default` cascade level.

⚑ **OUT OF THE AGENT FILE, THE LEVEL IT SUPPLIES HOLDS ONLY THE FILE'S `agent: default:` TABLE**
(Q92): `self:` IS `agent.<node>`, so a `default:` level under it reads `agent.<node>.default.*` and
REFUSES; the file's top-level `agent:` table has the spelling, as every settings file does. The
SYSTEM file's `agent: default:` table arrives on the system level below it.

⚑ `_FLAT_AGENT_CATEGORIES` · the nested REFUSAL · its cure placeholders live in
`settings/agent_file.py` with the machinery that reads them — see `agent_file.py.md`. The refusal
is one PREDICATE over the root table there.

```RETIRED_FILE_KEYS: dict[tuple[str, ...], str]```
Retired agent-SELECTION and agent-MIRROR spellings: nested FILE path of the retired leaf → the
retired KEY name.

THREE rows: `box.agent`, `box.agent_name`, `system.default_agent`. ⚑ The ROW count is not the
RETIREMENT count — `("box", "agent")` is one path carrying TWO retired spellings, told apart by the
value's SHAPE. See "Retired spellings" above.

```_NO_LEAF: Any = object()```
The "no such leaf" sentinel for `_nested_present`.

⚑ NOT `None`: a `box: {agent_name:}` leaf is PRESENT with the value `None`, and it is still the
retired key. Conflating present-null with absent is the same 3-state mistake §2h warns about for
prefs, and it would let the exact config the refusal exists to catch slip through silently.

```_SELECTION_STORY``` / ```_MIRROR_STORY```
The framing sentence a selection refusal / a mirror refusal opens with — WHY the spelling is refused.

TWO constants and not one, because the two retirements are different things. `_SELECTION_STORY`
says a box no longer names its agent with a key of its own and points at `pref.system.agent` (§2h)
and `system.agent` (§2g); `_MIRROR_STORY` says a box no longer carries a SETTABLE mirror of its
agent's settings, points at `pref.agent.<agent>.<key>` (§2h), and names the RO read-back
`meta.box.agent.<key>` (§2b). Each closes with what the refusal is BUYING — a guessed agent whose
credentials get seeded, versus every override in the table silently vanishing. `refuse_retired_keys`
picks between them with the same shape test that picks the cure.

```RETIRED_BEHAVIOR_KEYS: dict[str, str]```
The RETIRED behavior leaf → its successor key (R-41): `{"auto_approve": "access"}`.

```_RETIRED_BEHAVIOR_VALUE_MAP: dict[str, dict[bool, str]]```
The RULED value mapping for the retired boolean (R-41).

What the user's own stored value becomes in the successor enum. Keys are the `coerce_bool` results;
an UNPARSEABLE stored value maps to nothing, and the cure then names the legal tiers instead of
quoting a translation — never guess a tier.

```_BEHAVIOR_TABLE_SHAPES: tuple[tuple[tuple[str, ...], int], ...]```
The nested TABLES a behavior leaf can live under, per settings-file shape.

Each entry is `(prefix, has-a-<sub>-level)`:

* `(("agent",), 1)` — scope file: `agent.<sub>.<leaf>`, `<sub>` = `default` or an agent node
* `(("pref", "agent"), 1)` — §2h request: `pref.agent.<node>.<leaf>`
* `(agent_file.ROOT_SECTIONS, 0)` — agent file: `<root>.<leaf>`. ⚑ Taken from the BOUNDARY, not
  spelled here: rows are uniform `(prefix, depth)` pairs, so this walk needs the prefix and
  cannot take a slot. It is the ONE raw-walk consumer of `ROOT_SECTIONS`; a second one means the
  walk itself belongs in `agent_file`.

## Functions

```_cure_assignment(sub: str, value: Any) -> str```
The `<key>=<value>` tail a cure can be COPY-PASTED with, for ONE retired mirror leaf.

TWO shapes, because the file's leaf has two. A SCALAR is quoted verbatim so the command runs AS
PRINTED — a `bool` through YAML's spelling, never Python's `True` / `False`, which is not what the
CLI parses. A nested TABLE has no single-token spelling at all, so its tail stays a PLACEHOLDER one
level deeper (`<sub>.<key>=<value>`) rather than a repr that cannot work. Probe the emitted
commands rather than reading them; a cure is a command a user pastes.

```cure_subject(level: str, box_ref: str | None) -> str```
The REQUIRED subject positional for `kanibako <level> set` — ONE derivation, shared by every cure
this module emits.

⚑ **THE VERB IS THE LEVEL, and the subject is never optional.** Both pref-legal verbs take a
subject — `box set <box>`, `workset set <workset>` — and only a box-level refusal knows what it is
(`retired_key_cure`'s *box_ref*, `paths.project_cure_ref`'s answer, used as given because it is
already quoted). Anything else renders `<box>` / `<workset>`: a PLACEHOLDER, never nothing.

Why nothing is the worst of the three options, and why it is not caught by reading: dropping the
positional does not fail loudly at either level. `workset set` binds the KEY to its `workset`
positional and leaves `key_value` empty, so the pasted line hunts for a working set named
`pref.agent.<agent>.access=full`. `box set` takes its arguments as a LIST, so the key alone parses
and the write lands on whatever box the reader's cwd resolves to — a different box, silently. Both
read fine on the page.

⮕ Every cure takes its verb and subject from here, so a workset file's cure cannot come out as
`box set` with no subject. One derivation, so the cures cannot drift apart.

```_retired_mirror_cure(*, level: str, box_ref: str | None, table: dict[Any, Any]) -> str```
The LEVEL-APPROPRIATE fix for a TABLE-valued `box.agent` — the retired settable agent MIRROR (R-4).

One `_cure_assignment` tail per leaf in the stored table (an empty table falls back to the bare
`<key>=<value>` shape). Where a request may be WRITTEN it is the §2h `pref.agent.<agent>.<key>`
form; elsewhere it is the same REMOVE-it refusal the scalar cure gives at base / system / agent
scope, offering `kanibako agent set` for a user who meant to tweak the agent everywhere.

⚑ The agent renders as the `<agent>` PLACEHOLDER — the `retired_behavior_cure` shape. This seam
runs BEFORE selection, so naming an agent here would be a GUESS, and it is exactly the guess a box
carrying this table most needs kanibako not to make.

⚑ The verb and its subject come from `cure_subject`.

```retired_key_cure(key: str, *, level: str, value: str, box_ref: str | None = None, mirror: dict[Any, Any] | None = None) -> str```
The LEVEL-APPROPRIATE fix for a retired key.

*mirror* is the TABLE a `box.agent` leaf holds when that is its shape — the ONE discriminator
between this key's two retired spellings (see "Retired spellings" above). Not `None` ⇒ the cure is
delegated whole to `_retired_mirror_cure`. `None` means the SCALAR agent-name spelling, and every
other retired key.

`system.default_agent` always gets the same cure — the replacement is a SYSTEM-scope key wherever the
stale leaf was found. A scalar `box.agent` / `box.agent_name` gets the §2h request, but ONLY where a
request may be written. Elsewhere, the migration rule: *"A `box.agent_name` found in a system or
agent file has no legal pref equivalent — flag it rather than silently relocating it."*

*box_ref* is the addressable box the cure is FOR: `kanibako box set` takes the box as a REQUIRED
`[project]` positional unless the caller's cwd already resolves to that box, so a cure that omits it
fails from anywhere else. Threaded ONLY for a box-level refusal — `None` at any other *level*,
where no single box is being refused for. The verb and its subject then come from `cure_subject`,
so a WORKSET file's cure is `workset set <workset> …`, not `box set …`.

⚑ The base / system / agent REMOVE-it arm names no single box either, so the "if you meant one box"
line it closes with carries the `<box>` placeholder rather than a bare `box set`.

```_nested_present(raw: Any, parts: tuple[str, ...]) -> Any```
Read *raw* at the nested *parts* path, or `_NO_LEAF` when ABSENT.

Distinguishes ABSENT from PRESENT-`None` (see `_NO_LEAF`).

```refuse_retired_keys(raw: Any, *, level: str, path: Path | None, box_ref: str | None = None) -> None```
RAISE when *raw* still carries a RETIRED agent-selection or agent-mirror key.

The three keys are `RETIRED_FILE_KEYS`. The message names the KEY, the FILE, the fact that THE RULE
CHANGED, and the one-line cure — it must never read as "your config is wrong".
Never a warning and never a silent drop: a dropped `box.agent_name` would leave the box launching the
system default with that agent's credentials, which is the exact failure this refusal exists to
prevent. Run by `read_settings_files` under `ReadPurpose.SELECT` (`settings.agent_select`) and by
`retired_cure`, not inside `assemble_levels`.

⚑ **This is where the SHAPE discriminator is read:** a found `box.agent` holding a `dict` is the
MIRROR, anything else is the agent NAME. That one test picks BOTH halves of the message — the story
constant that opens it, and the *mirror* argument that steers the cure.

The cure carries the value the user ACTUALLY has, so it is copy-pasteable rather than a shape to fill
in; a present-`None` (an empty `agent_name:` leaf) has no value to quote, so it gets the shape.

*box_ref* is passed straight through to `retired_key_cure`, and is only meaningful at
`level="box"`.

```_behavior_leaf_sites(raw: Any, leaf: str) -> list[tuple[tuple[str, ...], Any]]```
Every (nested path, value) where *leaf* is present in *raw*.

Walks exactly the `_BEHAVIOR_TABLE_SHAPES` — no free-form recursion, so an unrelated user key that
happens to be spelled `auto_approve` deeper in some other table is not swept up.

```retired_behavior_cure(successor: str, *, level: str, tier: str, subject: str | None, box_ref: str | None = None, node: str | None = None) -> str```
The LEVEL-APPROPRIATE fix for a retired BEHAVIOR key.

⚑ **The cure's agent is the node the entry is STORED under** (*node*, read from the entry's own
path), never the agent being resolved (*subject*): a file may store another agent's entry. Only the
agent file's root stores no node and falls back to *subject*; with neither, the cure renders
`<agent>`. `access` is an AGENT-scope key (spec §2d), so where it may be WRITTEN depends on the file
the stale value was found in — the same asymmetry `retired_key_cure` handles for the selection
keys:

* `base` / `system` — `kanibako system set agent.<agent>.access=<tier>`; for the reserved `default`
  tier, which has no node, the bare `kanibako system set access=<tier>`.
* `agent` — the per-agent file has its own verb: `kanibako agent set <agent> access=<tier>`.
* `workset` / `box` — a BARE agent key is an UPWARD write there (`agent ⊃ workset ⊃ box`) and is
  dropped at assembly, so the legal spelling is the §2h REQUEST,
  `kanibako <level> set <subject> pref.agent.<agent>.access=<tier>`.

⚑ **Two different subjects.** *subject* names the AGENT node; the verb's own positional (the box or
workset) is *box_ref*, through `cure_subject(level, box_ref)`, or a placeholder when none is given.

```refuse_retired_behavior_keys(raw, *, level, path, subject=None, box_ref=None) -> None```
RAISE when *raw* still carries a RETIRED behavior key — `RETIRED_BEHAVIOR_KEYS`.

The message names the KEY, the SPELLING found, the LEVEL, the FILE, the fact that THE RULE CHANGED,
the user's own value translated through the RULED mapping, and the one-line cure. Never a warning
and never a silent drop: silence on the permission axis resolves to the PERMISSIVE default.

*subject* is the agent being RESOLVED, used only for the agent file's root, which stores no node;
`None` renders `<agent>`. *box_ref* is the box or workset the `set` verb needs as its positional,
passed straight through to `retired_behavior_cure` and meaningful only at `level="box"`. Run by
`read_settings_files` under `ReadPurpose.RESOLVE` and by `retired_cure` (which passes neither), not
inside `assemble_levels`.

⚑ The value line only ever states a translation the RULING makes. An unparseable stored value gets
the legal tiers instead of a guess.

```refuse_config_table(raw: Any, *, level: str, path: Path | None) -> None```
REFUSE (never drop) a top-level `config:` table in the base, system, workset or box settings file,
naming the file, each entry and its cure. `stored_config_entries` reads the table exactly as the
Layer-1 reader would (`config._flatten_leaves`, the walk that reader's `_flatten_dotted`
stringifies), and `config_entry_groups` splits it on that reader's own declared set
(`bootstrap.CONFIG_PATH_DEFAULTS`): a declared key moves under `config:` in the Layer-1 file at its
RESOLVED path (`config.user_config_file`, site-wide `config.config_base_path`), where it relocates
that path for every box; anything else is deleted: an undeclared or nested entry, or a `config:`
holding a value that is not a table, that file would refuse too, and a `config:` that flattens to
nothing means nothing in either file. The stored view shares both helpers, so it names the same
entries with the same cures. Run by `read_settings_files` under `ReadPurpose.RESOLVE` (the
launch's read), never from `assemble_levels`, which also serves the narrow `box.enable_vault`
resolve every box verb runs — a raise there would stop the stored `box show` that shows the user the
line to delete. Spec §1: the `config.*` keys live only in the `.cfg` files and are not a settings
tier. It is NOT a fourth token of the drop set below, and the difference is deliberate: §0's
directional DROP governs a key of a CONTAINING scope in a lower file of the cascade, and `config` is
not a cascade scope at all — the same reason the mirror case (a settings table inside the `.cfg`
file) refuses. `config.*` stays a KEY to the keyspace (`config set` / `get` address it); what is
refused is a settings FILE carrying it.

```_drop_upward_scopes(raw: dict, *, file_scope: str, path: Path | None) -> dict```
Drop a CONTAINING-scope, `meta:` or `binding_derivations:` top-level table (spec §0).

**THREE dropped tokens, THREE distinct rationales, one warning each.**

1. **A CONTAINING scope's table.** Directional enforcement at RESOLVE: a settings file may set keys of
   its own scope and of scopes it CONTAINS, but a top-level key of a CONTAINING scope (`system:` /
   `workset:` in a box file) is an UPWARD write, dropped here before it enters the partial. Downward
   and same-scope tables are untouched — a workset file's `box:` defaults-down table still flows (the
   ruled defaults-down mechanism).
2. **`meta`**, for EVERY file (`base` included). `meta.*` is a TOP-LEVEL protected read-only namespace
   set by the construct-time/bootstrap layer, RO everywhere (spec §0 / clause 4, "`meta.*` remains RO
   everywhere"); a settings file may not set it. `meta` is NOT a containing scope, so it earns a
   DISTINCT warning. ⚑⚑ **NO WORKSET-SCOPE CARVE-OUT.** A workset root has NO identity table — it
   is named by the global registry — so a `meta.workset` member is a RETIRED shape which
   `project/workset.py`'s `refuse_retired_workset_identity` hard-refuses upstream of this warning; it
   warns like every other scope, and the warning names the whole table. ⚑ **The drop is TOP-LEVEL ONLY:** the loop iterates top-level keys and
   never descends, so a nested `<scope>.meta` table rides under its scope untouched. The sole
   sanctioned meta source is the FLOOR (`dotted_partial`), inserted separately and never routed
   through this drop.
3. **`binding_derivations`** (spec §0 fault class: "never enters the merge"). It is the RESERVED
   INTERNAL derivations node at the snapshot root (manifest `not_keys.reserved_internal`) —
   machinery output regenerated per launch by `commands.start._install_derived_bindings`, not a key.
   A hand-forged table in a settings file would otherwise ride into the snapshot beside the real
   materialization: phantom `--effective` lines, and a non-`Bind` leaf crashes the `derived_bindings`
   lens with `ViewError`. Same profile as `meta` — EVERY file, TOP-LEVEL ONLY. SCOPE TIGHT: this ONE
   name only. Any other unknown top-level entry is not dropped here: it rides into the merge and is
   REFUSED by name at the launch's §0 audit (`settings_launch._refuse_undeclared_snapshot`), which
   judges the snapshot as a WHOLE store and so has no `UNROOTED` escape (spec §0: no bare top-level
   keys). Not in the per-agent file, which contributes its root (`self:`), `agent:` (Q92), and the
   contained scopes' `workset:` / `box:` tables, READ as defaults (Q85; `agent_file._CONTRIBUTED`):
   there `agent_file.level_table` REFUSES by name (`_refuse_stray_roots`) whatever the drops leave
   (this one takes `system:`, `meta:`, `binding_derivations:`; `settings_prefs` takes `pref:`).

`base` is EXEMPT for SCOPE keys (its containing set is empty — it is the system-scope floor) but NOT
for `meta`: a base-file top-level `meta:` table would clobber the floor's materialized identity
anchors, so it drops too. Defensively, `base` is not in `SCOPE_CONTAINMENT` at all, so `.index` would
raise — an unknown/base scope takes an empty containing set.

Returns a shallow copy with the dropped keys removed (never mutates *raw*); a non-dict *raw* is
returned unchanged. Warning-only side effect, no raise — a mis-scoped key is a config mistake, not a
hard error. The warnings themselves are `_warn_upward_drops`'s (below).

```_warn_upward_drops(raw: Any, *, file_scope: str, path: Path | None) -> None```
Emit the three warnings above, each only when `announce_drop_once` says it is the first.

```announce_drop_once(path: Path | None, token: str) -> bool```
⚑ **THE ONE GUARD for every settings-file read warning** (spec §0: dropped *"with a warning naming
the file and key"*) — the three §0 drops, `settings_prefs.refuse_pref_table`'s §2h `pref:` one, and
`fold_agent_nodes`'s capital-node fold (plus `config_keys._typed_key_node`'s, keyed by the typed
spelling with no file).
Returns `True` (and records the pair) the first time this process drops *token* from the file at
*path*; the caller warns only then. One command reads one settings file through several resolves,
and without the guard each would announce the drop again. The memo is
`_DROP_WARNED`, a module-level set of `(file, key)` pairs; it changes no resolution outcome (the drop
runs on every read), which is why it may be module-level — the same footing as
`commands.start._COLLISION_WARNED`. It lives for the process and never beyond it, so the next command
warns again until the file is fixed. `_drop_upward_scopes`, `refuse_pref_table` and `_file_view`
all warn through it, so a file read by several in one command still names each
key once.

```fold_agent_nodes(raw: Any, *, path: Path | None) -> Any```
*raw* with every `agent.<Node>` and `pref.agent.<Node>` segment folded to its node's case
(`agent_ref.agent_segment_case`: a pseudo-agent name whole, otherwise the harness, separator kept).
Q87: *"in our code, we refuse; if the key comes from a file or commandline, we should warn
loudly and accept."* So a user's capital is ACCEPTED here, with one warning per `(file, key)` naming
the file and both spellings, and the keyspace verdict does not fold — a capital built by code is
refused there. Without the fold, a system file's `agent: {Claude: {model: opus}}` would be neither
refused nor delivered. Two spellings of ONE node in one file (`Claude:` beside `claude:`)
are REFUSED naming both (FA = Q103: neither may silently win) by
`agent_file.refuse_node_spelled_twice`, the one carrier the agent file's own readers share; "one
node" is judged on the CANONICAL node (separator and case), so `nav+Claude:` beside `nav℘claude:` is
refused too. Called by `file_partial` (every
file level, and `settings_prefs.collect_prefs`'s pref parse) and by `_file_view` AFTER its drops,
so a table the cascade drops is neither folded nor judged. Copies only what it changes.

```reset_drop_warnings() -> None```
Clear `_DROP_WARNED` — the test seam that stands in for a fresh process.

The tokens rules 1-3 above remove, WITHOUT the warnings, are
`settings_drops.upward_scope_drop_set` — one declaration, three readers here: `_drop_upward_scopes`
(the drop), `_warn_upward_drops` (what to announce) and `_file_view` (the view). See
`settings_drops.py.md`.

```_file_view(raw: Any, *, level: str, path: Path | None, fold: bool = True) -> Any```
The part of a RAW settings doc at *level* that `assemble_levels` actually MERGES. Private; its
callers are `read_settings_files`.

⛑ **WHY IT EXISTS.** A consumer judging a settings file has to judge what the file CONTRIBUTES,
not what it CONTAINS. A retirement scan over the raw doc would prescribe a cure for an entry the
cascade had already dropped: a `box.yaml` carrying both `agent: {claude: {auto_approve: true}}` and
`box: {zippity: wibble}` would refuse by naming `auto_approve` — for a table directional enforcement
drops — while `box.zippity`, the key that actually stops the resolve, goes unnamed. A cure for a
no-op is worse than no cure.

Each dropped table is announced through `announce_drop_once`, the guard every reader warns through,
so a command that reads one file several times names each key once.

**THREE filters, one per rule**, each READ from its one declaration:

1. A `pref:` table survives only at a level §2h lets one be WRITTEN
   (`settings_prefs.PREF_LEGAL_LEVELS`); elsewhere `refuse_pref_table` drops it with a warning.
2. Directional enforcement — `_drop_upward_scopes` (`settings_drops.upward_scope_drop_set`).
3. The per-agent file contributes `agent_file.contributed_tables` — its root, `agent:` (Q92),
   and the contained scopes' `workset:` / `box:` tables, READ as defaults (Q85) — after
   `_warn_upward_drops` names what it drops.

What survives is then folded by `fold_agent_nodes` unless *fold* is off (the `NARROW` read folds
at parse time).

🛑 **TOP-LEVEL ONLY**: which TABLES reach the merge, never which leaves inside one survive.

## Reading the files

```class ReadPurpose(Enum)```
WHY a settings file is read; each member fixes its stages, their order, and the file order it walks
(`.order`):

* `RESOLVE` — the launch snapshot (box, workset, agent, system, base). Refuses a `config:` table on
  the raw doc of every non-agent file (`refuse_config_table`), then a retired behavior spelling on
  each view (`refuse_retired_behavior_keys`).
* `SELECT` — agent selection (base, system, workset, box). Refuses a retired selection or mirror
  spelling on each view (`refuse_retired_keys`).
* `NARROW` — assembly alone (base→box); every `pref:` drop, then every upward drop, then the
  unfolded views. The node fold and the agent file's shape run in `assemble_levels`.
* `DISPLAY` — plain `show`: the view and its warnings, no refusal.

⚑ No purpose runs stage (h) (`refuse_undeclared_per_file`) inside the read. A caller that runs it
does so AFTER its own refusals, so a caller's tailored cure is the one a user reads.

```class SettingsFile(level, path, stored, view)```
One settings file as read: the doc on disk (*stored*) and what the cascade merges (*view*).
`loaded` is True when a file is on disk at *path*.

```read_settings_files(files, *, purpose, subject=None, box_ref=None) -> tuple[SettingsFile, ...]```
Read *files* (`(level, path)` pairs) for *purpose*, in its file order; each phase runs over every
file before the next starts. *subject* (the agent node) and *box_ref* only fill the cures a retired
spelling's refusal prints. A level whose file is absent gets an empty view.

```cascade_files(*, purpose, system_path, agent_path, workset_path, box_path, base_path=None, subject=None, box_ref=None) -> tuple[SettingsFile, ...]```
The five cascade files read for *purpose*; *base_path* defaults to `settings_base_path()`, the site
base file.

```refuse_undeclared_per_file(files) -> None```
Stage (h): RAISE for the first file, MOST-SPECIFIC FIRST (`_H_AUDITED_LEVELS` = box, workset,
system, base), whose OWN view carries an undeclared entry. The walk does not follow the read's
order: `NARROW` reads base→box, and its order would name the WORKSET file when both carry one.
Callers: the launch (`settings_launch`, after its §0 audit) and `commands/workset_cmd`.

```refuse_undeclared_entries(view, *, level, path, stored=None) -> None```
RAISE when *view* carries an undeclared entry, naming EVERY such key and *path*, with the cure to
remove them BY HAND. `retired_cure` runs first, since a retired spelling is the more specific fault.
`DISPLAY` never reaches it: `show` LISTS undeclared entries (Q5).

```undeclared_listing(findings) -> tuple[str, str, str]```
`(listing, count phrase, pronoun)` for `undeclared_store_paths` findings — the one listing stage (h)
and the launch's §0 refusal both print (P10).

```retired_cure(files) -> None```
RAISE the TAILORED refusal when a loaded file's view still carries a RETIRED spelling:
`refuse_retired_keys`, then `refuse_retired_behavior_keys`, per file, most-specific first. Called
only once §0 has decided to refuse, to pick WHICH refusal the user reads; nothing is re-read.
Callers: `settings_launch._refuse_undeclared_snapshot` and `refuse_undeclared_entries`.

```agent_record(path: Path, *, node: str, purpose: ReadPurpose) -> AgentConfig```
THE ONE READER of an agent's settings file: one `read_settings_files` read, the shape judged by
`agent_file.level_table` (the same view `agent_file_partial` takes), and the record built by
`agent_file.record`, then `refuse_undeclared_entries` over the level's contained tables. Every
reader — the launch, `agent show` / `info` / `list` / `get` — takes the record from here, so one
file gets one verdict. Returns an empty record if the file does not exist. A store folder named for
the file alias (`self`), or a present folder named for the reserved `default` tier, is refused
through `agent_store_refusal`.

```agent_store_refusal(store: Path, why: str, *, fix: str | None = None) -> SettingsError```
THE refusal of folder *store* as an agent store, because of *why*. The default cure renames or moves
the folder; *fix* replaces it when the folder can hold no agent's settings at all.

```parse_node(value, *, in_binds: bool, dest_keyed: bool = False, at_bindings: bool = False, path: tuple[str, ...] = (), for_pref_requests: bool = False) -> Any```
Recursively coerce a raw settings node into the `StoreValue` space.

*in_binds* is True while descending the subtree of a bind-shaped category (`bindings.{ro,rw}` /
`caches` / `seeded` / `common` / `synced`), where a list/tuple LEAF is a structured entry parsed to a
bind value (S9). Refs inside stay RAW (spec §0). A plain `dict` descends; any other leaf (scalar /
`None` / a genuine `list[str]`) is stored verbatim (`KeyStore` wraps `None` and scalars as-is; a list
is not descended).

*dest_keyed* selects WHICH bind shape a leaf under a bind category has, and is the ONLY thing that
decides it (R-3/R-6):

* `False` — NAME-keyed, a leaf is `[host_src, box_dest[, opts]]` → `Bind`. ⚑ NO bind-shaped category
  reaches this branch; it serves a MALFORMED node that `parse_bind_map` hands back.
* `True` — DEST-keyed, the key IS the destination and a leaf is `[src[, opts]]` → `BindEntry`.

⚑⚑ Both shapes admit a 2-element list with OPPOSITE meanings, so the choice is made by this CONTEXT
FLAG — passed down from the caller that knows which node it is reading — and NEVER by inspecting the
leaf's arity.

*at_bindings* says "the dict I am about to descend is the `bindings` category node, so its keys are
ARMS and each arm's value is a `BindMap`". It is set by this function itself when it walks past a
`bindings` token, which is what wires user settings FILES onto the dest-keyed route (P6) —
`file_partial` needs no flag of its own, because the shape is a property of the CATEGORY, not of the
caller. ⚑ It is deliberately NOT set for a `bindings` token encountered while already `in_binds`:
inside a bind category the keys are names/destinations, and a user with a `caches` entry literally
named `bindings` must not have it re-read as a category. The `not in_binds` guard on the terminal
four carries the same protection.

⚑ A non-dict at an ARM (or at one of the terminal four) is a MALFORMED node, and it is deliberately
left to the leaf path so that `settings_launch._assert_declared_categories` still produces the
arm-shape message that names the key. Do not raise here instead.

⚑ A malformed ARITY in a structured bind leaf raises `SettingsError` — the structured shape is
load-bearing.

*path* is the KEY PATH walked so far, doc-root first. It carries the SCOPE to
`_declaration_root_ref`, so an abstract category's bare leaf is rooted here (spec §2a) rather than
downstream; a caller whose document does not START at a scope token seeds it (`agent_file_partial`,
because `self:` IS `agent.<node>`). *for_pref_requests* is threaded to `parse_bind_map` as
*defer_shape* for the pref-request parse (see `file_partial`).

```parse_bind_map(raw: Any, *, category: str = "bindings", root_ref: str | None = None, declared: bool = True, defer_shape: bool = False, pref_agent: str | None = None) -> KeyStore```
Parse a raw DEST-KEYED category map into a `KeyStore` of `BindEntry`.

*raw* is the mapping stored at ANY terminal bind-shaped key: a `bindings` arm
(`<scope>.bindings.ro` / `.rw`, R-5) or one of the four whose category token is itself the whole key
(`<scope>.caches` / `.seeded` / `.common` / `.synced`). Both hold the SAME
`{box_dest: [src[, opts]]}` shape, which is why there is ONE parser and not two — *category* only
names the key in the refusals.

Returns the map as a nested `KeyStore` node, NOT an opaque dict leaf, so it merges PER-ENTRY across
cascade levels through the generic node recursion (the `masks` precedent) instead of a box-level arm
wiping an inherited workset entry wholesale. A `None` entry value is preserved VERBATIM — the
per-entry reset the merge classifies as an OMIT (spec §2a "PER-ENTRY CASCADE MERGE"). A
non-mapping *raw* raises `SettingsError`; so does a malformed entry
(`settings_resolve.unpack_bind_entry`).

⚑ **THIS IS THE ONE PLACE A STORED DEST IS CANONICALIZED ON READ** (R-11): every key goes through
`settings_resolve.normalize_bind_dest`, so `~` and `~/` are ONE entry and not two colliding at one
destination. Producers normalize too — they must, because the floor merge in `commands.start` dedupes
on these keys BEFORE anything is parsed — and the function is idempotent, so doing it in both places
costs nothing and neither place is load-bearing alone.
⚑ The VALUE is never canonicalized: a `host_src` stays exactly as authored.

*root_ref* is the declaration root an abstract category's bare source is rooted at
(`_declared_source`) — the DECLARATION-LOAD seam, since what gets STORED must resolve on its own.
*declared* says the map is a declaration rather than a request, for `check_bind_map`'s verdicts.
*defer_shape* withholds ONLY the bare-scalar entry verdict, carrying the value for
`settings_prefs.refuse_deferred_pref_shapes`; every other check still runs here, on the whole map.
*pref_agent* is the agent segment the map sits under, judged before any shape
(`settings_prefs.agent_segment_reason`).

⚑ A nested MAPPING entry is REFUSED by name: under dest-keying an arm's value is a flat
`{dest: [src[, opts]]}`, so a sub-table is the retired `{name: {…}}` shape (spec §2a, "STALE SHAPES
ARE REFUSED LOUDLY"). A 3-element list is refused one level down, by `unpack_bind_entry`.

```file_partial(raw: dict, *, path: Path | None = None, for_pref_requests: bool = False) -> KeyStore```
Build ONE level partial from a settings file's WHOLE nested content.

*raw* is the parsed file (`load_doc` output). The full scope-ROOTED tree is mirrored into a nested
`KeyStore` with the SCOPE TOKEN KEPT (§0: namespace orthogonal to cascade), refs raw. EVERY
bind-shaped category is read as a DEST-KEYED `BindMap` of `BindEntry` leaves (R-5/R-6): at the ARM for
`bindings.{ro,rw}`, at the CATEGORY TOKEN for the other four. ⚑ The DEPTH is not chosen here —
`parse_node` derives it from the CATEGORY token it walks past, so there is no flag for a caller to
get wrong.

*path* names the file in every refusal the parse raises — a key with no file is a cure the user
has to hunt for. *for_pref_requests* marks the one reader whose consumer judges the agent segment
(`settings_prefs.apply_prefs`), so only there is a bare-scalar entry's verdict deferred.

An empty / non-dict file yields an empty `KeyStore`. This is the rule for every NON-agent level
(`base` / `system` / `workset` / `box`); the agent tier uses `agent_file_partial`. The node segments are folded first (`fold_agent_nodes`).

```agent_file_partial(raw: dict, *, sub_key: str, path: Path | None = None, node: str | None = None) -> KeyStore```
Build an AGENT-tier level partial (`agent.default` or `agent.<active>`) — the STORE half of the
seam.

It asks `agent_file.level_table` which raw table this level reads (that call is where the nested
refusal fires, BEFORE the flat splice), then `parse_node`s the table and wraps it under its TRUE
discriminated name `agent.<sub_key>`. *path* and *node* are display-only: they render the
boundary's refusal message and are never read.

The per-agent DISCRIMINATOR is KEPT VERBATIM — `agent.default.<key>` for the default layer,
`agent.<active-name>.<key>` for the active layer, the ONLY two agent key forms the spec defines
(§2d; §0 forbids a bare `agent.<key>`). The two agent levels then merge BY THEIR TRUE NAMES; the
active-over-default value-pick is a thin effective-agent READ deferred to the consumer — the
cascade's job is precedence by LEVEL ORDER, not a name collapse. This preserves §0 per-agent
independence: a box/workset that sets `agent.<other>.*` (or directly sets `agent.default.*`) keeps
its true name and survives the merge intact.

An EMPTY level (missing root table, or a *sub_key* with no matching sub-table) yields an empty
`KeyStore`.

```dotted_partial(floor: dict[str, object] | None) -> KeyStore```
Build a merge LEVEL from a flat `{dotted key: value}` mapping.

*floor* is the caller's declared `{key: default}` behavior defaults plus default-categories —
`commands/start.py` gathers it and passes it through `settings_launch.build_launch_snapshot`. Its keys
are the same SCOPE-QUALIFIED logical keys the files use, flat dotted; dotted keys are EXPLODED to the
nested keyspace (S7) so the floor merges uniformly with the other partials. A name-keyed bind value is
parsed to `Bind` (reachable only for a malformed node — see `parse_node`).

⚑⚑ **DO NOT PUT A CATEGORY-ENTRY SPELLING IN THIS DOCSTRING AS AN EXAMPLE.** `_insert_dotted` REFUSES
BY NAME every floor key that goes DEEPER than the terminal category key: `"box.bindings.rw.home"`
and `"box.caches.pip"` are both refused (`SettingsError: Default-category key 'box.caches.pip'
names a 'caches' entry by ENTRY NAME`). A safe example is a
BEHAVIOR key such as `"agent.access"`, or a terminal category key such as `"box.caches"`.

```_insert_dotted(store: KeyStore, dotted: str, value: Any) -> None```
Insert *value* at the dotted path, exploding to nested `KeyStore` nodes (S7).

The terminal leaf is parsed: a value under a bind-shaped category segment becomes a bind value;
otherwise verbatim.

⚑ **EVERY bind-shaped category is the exception (R-5/R-6).** A floor key ENDS at the terminal key —
`box.bindings.ro` for an ARMED category, `agent.claude.common` for the other four — and its value is a
whole dest-keyed `BindMap`, parsed by `parse_bind_map`. A floor key that goes DEEPER
(`box.bindings.ro.<name>`, `agent.claude.common.<name>`) is the retired name-keyed producer shape and
is REFUSED here, loudly and by name.

That refusal is not decoration: the deeper spelling type-checks all the way to launch, and the
`<name>` segment would land as a sibling of real destinations inside the map — a name sitting where
a path belongs, which nothing downstream can tell apart. It also catches a third-party plugin's
`default_category_binds()` returning the name-keyed shape (spec §2d; empty for all first-party
plugins).

⚑ Uses UNBOUND `dict.get` (S3): never the bound `node.get` — a leaf named `get` would shadow the
method into a crash. These stores are module-built, but keeping the collision-safe convention uniform
is the point.

```assemble_levels(*, agent_name: str, files: Iterable[SettingsFile], floor: dict[str, object] | None = None) -> list[KeyStore]```
Build each cascade scope's ONE nested `KeyStore` partial from the already-read *files*; return them
ordered.

MOST-SPECIFIC-FIRST (S8). The 6 levels, in order:

    [box, workset, agent.<active>, agent.default, system, base]

matching spec §2's `base < system < agent.default < agent.<active> < workset < box` reversed to
high→low precedence — the merge walks this order and the first scope that SETS a leaf wins.

* *files* come from `read_settings_files` / `cascade_files`, with the purpose the caller chose; a
  level with no file there contributes an empty partial. No path is taken here: the filters (the
  `pref:` drop, the upward-scope drop, the agent file's contributed tables) already ran in
  `_file_view` during the read, and the base file's path is `cascade_files`' default.
* *agent_name* selects the active agent's sub-table for the `agent.<active>` level. The
  `agent.default` level holds only the agent file's `agent: default:` table (Q92); the SYSTEM file's
  `agent: default:` table arrives on the system level.
* *floor* (declared defaults + default-categories) is folded UNDER the base file's content into the
  `base` level — the floor is inserted FIRST and the base-file leaves overlay it (`_overlay`), so a
  base-FILE set-value beats the floor at the same key, and the floor is the ultimate fallback. The
  floor is also the SOLE sanctioned `meta.*` source: a top-level `meta:` table is dropped from every
  FILE view (base included, spec §0) before the partial is built.

Each non-agent level's partial is its file's view, scope token KEPT (§0), through `file_partial`.
The agent file yields BOTH agent levels through `agent_file_partial`, each kept under its TRUE
discriminated name (§2d) — NO bare-`agent` collapse. The agent file's SHAPE checks
(`agent_file.level_table`: a stray root, a nested `self:`) judge the stored file minus its dropped
tables (`_agent_shape_input`), so a stray still refuses by name. Binds are parsed with `@`-ref /
`$var` / `~` tokens left RAW (S9 / spec §0). NO `machine` path is consulted (S14).

```_overlay(base: KeyStore, top: KeyStore) -> None```
Deep-overlay *top*'s leaves onto *base*, in place (same-level combine).

Used ONLY to layer a base-FILE partial over the declared-default floor WITHIN the single `base` level,
so a base-file set-value beats the floor default. **This is NOT the cascade merge** — it is a
same-level union of two SOURCES (floor defaults + the base file).

It descends matching `KeyStore` subtrees so a deep base-file leaf overlays the same deep floor leaf
without clobbering sibling floor leaves; a non-subtree leaf in *top* replaces *base*'s same key
wholesale, because the file is the authoritative source at this level. Uses unbound `dict` ops (S3).
