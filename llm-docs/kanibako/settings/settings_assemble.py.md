# Cascade Level Assembly
_per-scope settings files → the ordered `list[KeyStore]` partials the merge consumes_

Every settings file on disk is scope-ROOTED and every cascade level is a FILE. This module is the
one place that turns those files into the ordered `list[KeyStore]` the merge (`settings_merge`)
walks. It performs READS and structural parsing ONLY: no merge / precedence, no `@`-ref / `$var` /
`~` expansion or cycle detection, no typed views, no `config set`. Tokens are left RAW inside binds
(spec §0 "Files store entries UNRESOLVED").

⚑ **This is the LIVE launch cascade, not a parallel build.** `assemble_levels` is called from
`settings_launch.build_launch_snapshot`, which `commands/start.py` calls on the launch path (and
again for `config show --effective`); `config_interface` and `commands/workset_cmd` call it
directly. *(The module docstring used to say it "builds ALONGSIDE the live launch cascades
(`commands/start.py`, `config.py:load_settings`) — it modifies none of them; the swap is block 7."
All three parts of that were false at the time of this pass: the swap happened, `start.py`'s
snapshot routes through here, and `config.py:load_settings` does not exist anywhere in the tree.
Dropped rather than relocated.)*

Provenance: block 2a of the KeyStore implementation.

## Authority

* **Spec `settings-keyspace-1.8.0.md` §2 — the cascade, PRIMARY authority.** The 6-level bracket
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
* **Keyspace audit 2026-06-27c #2** — the `machine` (`/etc/kanibako.yaml`) tier is CUT: the cascade
  FLOOR is `base` (overridable) and the cascade ENDS at `box` (the former `required` non-overridable
  cap was cut 2026-06-29f). Spec §2 states both directly. This module consults no machine path.
  *(The source used to phrase this as "reads NO `machine_config_path()`"; that function no longer
  exists anywhere in the tree, so the symbol name was dropped and the substance kept.)*
* **`keystore-design.md`** (archived at `notebook/archives/keystore-2026-06/`) §2 (storage —
  partials are `KeyStore`s, binds are `Bind`); §6a (files store UNRESOLVED — refs stay raw); §3
  (`None` semantics — the per-entry reset the merge classifies as an OMIT).
  ⚑ Cite spec §2, not design §4, for the bracket: design §4 still writes it with a 7th `required`
  tier, which S14 and spec §2 cut.

## Seams realized here (`plans/keystore-blocks/SEAMS.md`)

* **S7** — partials are NESTED `KeyStore`s (not flat dotted dicts); a scope file's nested tables are
  mirrored verbatim into the partial, SCOPE TOKEN KEPT.
* **S8** — output order is MOST-SPECIFIC-FIRST:
  `[box, workset, agent.<active>, agent.default, system, base]`. The two agent levels keep their
  TRUE discriminated keys (`agent.<active-name>.*` / `agent.default.*`, §2d) — NO bare-`agent`
  collapse; level order is the cascade precedence.
* **S9** — binds parsed at ASSEMBLY with `@`-refs / `$vars` / `~` left RAW inside the entry
  (expansion is block 3).
* **S13** — ONE unified `KeyStore` partial per level holding BOTH behavior leaves AND category
  subtrees together (design §1/§2 single-source).
* **S14** — no `machine` tier; floor → `base`; cascade ends at `box` (no `required` cap).
* **S3** — every walk here uses UNBOUND `dict.get` / `dict.__setitem__`, never the bound method: a
  leaf legitimately named `get` must not shadow the protocol into a crash.

## Scope tokens — KEPT; DOWNWARD/same-scope only (§0)

Settings files are scope-ROOTED on disk (`{system: {bindings: {rw: {…}}}}` → `system.bindings.rw`).
The scope token is LOAD-BEARING — it names the DECLARATION ROOT an abstract-category source is
spelled against, and it picks the mount mode for `bindings` — and namespace is ORTHOGONAL to
cascade level (§0). *(The source attributed the on-disk flattening to `config.py:_flatten_categories`;
no such symbol exists in the tree. The fact is kept, the dead attribution dropped.)*

A file may hold keys of its OWN scope AND of scopes it CONTAINS (`system ⊃ agent ⊃ workset ⊃ box`)
as OVERRIDABLE defaults-down — e.g. a workset file may set `box.*` and it flows. But **directional
enforcement at RESOLVE** (spec §0, Jei-blessed 2026-07-02) DROPS a top-level table of a CONTAINING
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

⚑ *(The source said a box tweaks its agent "via the `box.agent.*` mirror, §2b". FALSE: a settable
`box.agent.<key>` is RETIRED under P7 — `config_interface` refuses it by name at the get, set and
reset sites via `config_keys.box_agent_retired_error`, and `bare_agent_key_scope_error`'s box arm
redirects to `pref.agent.<agent>.<key>`. Spec §2b says it in as many words: "A box tweaks its agent
with `pref.agent.<agent>.<key>` (§2h) instead." The only surviving `box.agent` spelling is the RO
READ-BACK mirror `meta.box.agent.<key>` — a DIFFERENT key, and not a tweak channel. Corrected, not
relocated. The same false claim was dropped from `commands/workset_cmd.py` by its own prose pass in
`2c6b117`; this was the second carrier.)*

⚑⚑ **The agent file's SHAPE is not this module's** — `settings/agent_file.py` owns it (the root
table, the flat-category splice, the nested refusal, the address a leaf writes to). What is left
here is the STORE COERCION and the §2d wrap: `_agent_partial` takes an `AgentFileLevel` from
`agent_file.level_table` and turns its raw table into a `KeyStore` under `agent.<sub_key>`. The
seam is cut at the SHAPE so the boundary never imports `KeyStore` and the import edge stays
one-way. See `agent_file.py.md`.

`config.read_agent_settings` PRE-MERGES the default and per-agent sections inside one file; this
module deliberately does not — the separation into two levels is the point.

## Bind-shaped categories — the depth rule

The bind-shaped categories are `bindings.ro`, `bindings.rw`, `caches`, `seeded`, `common`, `synced`.
`masks` (a keyed `dict[box_dest → bool|None]`, S5) and the scalar families `env.<VAR>` /
`secret_path.<VAR>` keep their natural nested shape and are not bind-parsed.

⚑ **EVERY bind-shaped category is DEST-KEYED.** Each is a TERMINAL key whose value is a `BindMap` =
`dict[box_dest → [src[, opts]]]` parsed to `BindEntry` leaves; the destination is the dict key and
the entry's identity, and there is NO entry name (spec §2a REPRESENTATION).
*(The `_BIND_CATEGORIES` comment described the leaf as the retired `[host_src, box_dest[, options]]`
name-keyed pair and said "each is parsed to a `Bind` at assembly"; both were false — `_parse_node`'s
own note says no bind-shaped category reaches the `Bind` branch any more. Dropped, not relocated.)*

⚑ **A DATE DISCREPANCY, DELIBERATELY NOT CARRIED HERE — and NOT fixed in the source either.** The
source dated the four categories' move to dest-keying **2026-08-08c** in four prose sites and two
raise messages. The **spec dates it 2026-08-07f** and says so in eleven places
(§0 "it became true of `bindings` on 2026-08-06c and of the other four on 2026-08-07f"; §2a
"`bindings` was ruled dest-keyed 2026-08-06c a& other four followed 2026-08-07f"; annotations §
"Post-ratification amendment 2026-08-07f"). The `bindings` date, **2026-08-06c**, agrees everywhere.
This is a REPO-WIDE discrepancy, not a local one — `2026-08-08c` appears 59 times across 17 source
files — and two of this module's six sites are inside raise messages, which a prose pass cannot
touch. Fixing four of six here would leave the file internally inconsistent, which is worse than the
drift. **So the four prose dates were DROPPED rather than relocated, the two message dates were left
alone, and the discrepancy is boarded for the director.** Do not re-add a date here from memory.

⚑⚑ **The category set that survives is about WHERE the map sits, not WHETHER there is one.**
`bindings` is the one category whose token is not the whole key: it is followed by an ARM (`ro` /
`rw`) and the map is one level DOWN. The other four ARE the map, terminal one level SHALLOWER.
Reading either at the wrong depth would take a destination for an arm name.

⚑ **This is the other half of why the reader cannot use a blanket `dest_keyed=True`:** a leaf under
a bind category is 2-element-legal in BOTH shapes with OPPOSITE meanings (`(host, box)` vs
`(src, opts)`), so the DEPTH of the walk — never the leaf's arity — is what says which one it is.
That is the arity trap `kb_store.BindEntry` documents, and spec §2a calls the loud refusal of
the stale shape load-bearing for exactly this reason.

⚑ The depth is derived from the CATEGORY token `_parse_node` walks past, so `_file_partial` needs no
flag of its own and there is no flag for a caller to get wrong (P6, P3).

## Retired spellings — refusing by name

Two independent refusals live here. Both share a shape and a reason; they differ only in seam.

⚑ **WHY THEY EXIST AT ALL, given that migration is DOCUMENTATION-ONLY for this arc** (IMPL-PLAN
standing ruling 1). Neither is migration machinery: they read nothing, relocate nothing and write
nothing. They are §0's CLOSED-KEYSPACE rule — *an undeclared key is an ERROR that NAMES it* — applied
to spellings that were retired. The documentation-only ruling was made against failure modes of the
"empty dir beside a populated one" shape; the failures here are categorically worse, and Jei's own
M-7 ruling (hard error with a migration-grade message) is the precedent for loud in exactly this arc.
Scope is deliberately TIGHT in both cases: these keys, nothing else. Neither IS general resolve
enforcement — that landed separately in `settings_launch._refuse_undeclared_snapshot` — but both are
now CALLED BY it (`_refuse_retired_spelling`), because the general refusal reaches a retired spelling
first and would otherwise replace the message written for it with the generic one. Each remains the
ONE carrier of its own text; the resolve seam calls, never copies.

**Selection and mirror keys (P7, spec §0 / §2b / §2g; migration M-4).** THREE retired spellings:
`box.agent`, `box.agent_name` and `system.default_agent`. A box that SILENTLY RUNS A DIFFERENT AGENT
— and seeds that agent's CREDENTIALS into itself — is the failure being prevented, so a silent drop
is not an option. The CURE is LEVEL-DEPENDENT: a pref is legal only in a workset or box file (spec
§2h), so telling a SYSTEM-file reader to `box set pref…` would prescribe a write that cannot fix
their file. Called at the SELECTION seam (`settings.agent_select`) and, since the §0 resolve refusal
was armed, from `settings_launch._refuse_retired_spelling` — which in practice reaches it first,
because `load_merged_config` resolves before selection runs. NOT inside `assemble_levels`: a raise
there would break `config set`, i.e. the very command the message prescribes as the cure.
⚑ The resolve seam passes no *box_name*, so its cure carries the `<box>` placeholder; the selection
seam threads `proj.name`. That difference is visible to users and is documented in `MIGRATION.md`
§2.1 rather than left for them to notice.

⚑⚑ **`box.agent` is TWO retirements sharing ONE file path, and the VALUE'S SHAPE is the
discriminator.** Both are manifest `renamed` rows:

* a **SCALAR** is the old agent-SELECTION key (`box.crab` → `box.agent` → `box.agent_name`, all of
  them cured toward `pref.system.agent`, §2h);
* a **TABLE** is the retired SETTABLE MIRROR of the agent's settings, `box.agent.<key>` (R-4), cured
  toward the per-agent request `pref.agent.<agent>.<key>` (§2h). The `box.agent` spelling that
  SURVIVES is the RO read-back `meta.box.agent.<key>` (§2b) — a different key, not a tweak channel.

One cure for both would send half of these users at the wrong key, so `refuse_retired_keys` reads
the shape and hands `_retired_key_cure` a *mirror* argument that selects between the two. The
FRAMING SENTENCE forks with it, which is why there are TWO story constants: *"a box no longer names
its agent with a key of its own"* is false for someone who was tweaking an agent rather than naming
one.

**Behavior key (R-41, spec §2d; migration M-22).** R-41 replaced the boolean `auto_approve` with the
enum `access` (`restricted|editing|full`, default `full`). Under the closed keyspace the old spelling
is UNDECLARED, and before the resolve refusal was armed an undeclared stored key was SILENT at launch
— which on a PERMISSION axis meant a box deliberately configured `auto_approve: false` came up at the
new `full` default with nothing printed. That is a safety-class silent regression in the UNSAFE
direction, so the stale key is REFUSED (RQ-2, ruled by Jei 2026-08-02) with a level-appropriate cure
that NAMES `access` and QUOTES the user's own value through the ruled mapping (`true` → `full`,
`false` → `restricted`). ⚑ The generic refusal says none of that, which is why the resolve seam calls
this one first. Two seams now: the LAUNCH's BEHAVIOR tier (`commands/start.py`) and
`settings_launch._refuse_retired_spelling`; NOT `assemble_levels` — same reason as above.
⚑ NOT EVERY SITE ARRIVES HERE, and none of the others is a silence. MEASURED: a
`pref.agent.<node>.auto_approve` request is refused by `apply_prefs` — inside the assemble, so ahead
of both seams — naming the key, the level and the file, but WITHOUT the tier translation. A
`workset`/`box` file's `agent.<sub>.auto_approve` is dropped as an upward scope with a warning and
never becomes a key at all. The `system` file's `agent.<sub>.auto_approve` is the site MEASURED at
the resolve seam. The agent file's own `self.auto_approve` gets this same message from whichever of
the two seams a launch reaches first — `start.py`'s tier names the real agent, the resolve seam the
`<agent>` placeholder — and which that is was NOT measured, only the message. ⚑ `base` is not
scanned at the resolve seam, so a stale key in `/etc/kanibako/settings_base.yaml` reaches the
generic message.

## Values

```_BIND_CATEGORIES: frozenset[str]```
The bind-shaped category tokens whose subtree holds bind entries.

`{"bindings", "caches", "seeded", "common", "synced"}` — the tokens as they appear in a file.
`bindings` carries the `ro` / `rw` sub-tables, each of which holds the map; the other four hold it
directly. Also read by `settings_merge`.

```_DEST_KEYED_CATEGORY = "bindings"```
The ARMED bind-shaped category — the one whose category token is not the whole key.

```_BIND_ARMS: tuple[str, str] = ("ro", "rw")```
The two arms a dest-keyed `bindings` node carries; each holds a `BindMap`.

```_DEST_KEYED_LEAF_CATEGORIES: frozenset[str]```
The bind-shaped categories whose CATEGORY TOKEN IS THE WHOLE KEY.

`{"caches", "seeded", "common", "synced"}` — terminal ONE LEVEL SHALLOWER than a `bindings` arm,
with a `BindMap` for a value. See "the depth rule" above for why this set is not redundant with
`_BIND_CATEGORIES`.

```_AGENT_DEFAULT_SUB = "default"```
The agent sub-table that supplies the all-agents `agent.default` cascade level.

⚑ **THE LEVEL IT SUPPLIES IS STRUCTURALLY EMPTY OUT OF THE AGENT FILE** (S2): `self:` IS
`agent.<node>`, so a `default:` level under it reads `agent.<node>.default.*` and REFUSES. That
tier's route is the SYSTEM file's `agent: default:` table. The `_agent_partial` call at
`assemble_levels` is KEPT — deleting it re-indexes every `base_levels[n]` consumer — with the
emptiness stated at the call site.

⚑ `_FLAT_AGENT_CATEGORIES` · the nested REFUSAL · its cure placeholders MOVED to
`settings/agent_file.py` with the machinery that reads them — see `agent_file.py.md`. *(The refusal
is one PREDICATE over the root table there now, not the enumerated
`_REFUSED_NESTED_AGENT_CATEGORIES` tuple this line used to name.)*

```RETIRED_FILE_KEYS: dict[tuple[str, ...], str]```
Retired agent-SELECTION and agent-MIRROR spellings: nested FILE path of the retired leaf → the
retired KEY name (M-4).

THREE rows: `box.agent`, `box.agent_name`, `system.default_agent`. ⚑ The ROW count is not the
RETIREMENT count — `("box", "agent")` is one path carrying TWO retired spellings, told apart by the
value's SHAPE. See "Retired spellings" above.

```_PREF_LEGAL_LEVELS: frozenset[str]```
The levels where a `pref` REQUEST may be WRITTEN (spec §2h) — `{"workset", "box"}`.

The single fact that decides which cure a retired `box.agent_name` gets.

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
level deeper (`<sub>.<key>=<value>`) rather than a repr that cannot work. Both of those were caught
by PROBING the emitted commands rather than reading them; a cure is a command a user pastes.

```_cure_subject(level: str, box_name: str | None) -> str```
The REQUIRED subject positional for `kanibako <level> set` — ONE derivation, shared by every cure
this module emits.

⚑ **THE VERB IS THE LEVEL, and the subject is never optional.** Both pref-legal verbs take a
subject — `box set <box>`, `workset set <workset>` — and only a box-level refusal knows what it is
(`_retired_key_cure`'s *box_name*). Anything else renders `<box>` / `<workset>`: a PLACEHOLDER,
never nothing.

Why nothing is the worst of the three options, and why it is not caught by reading: dropping the
positional does not fail loudly at either level. `workset set` binds the KEY to its `workset`
positional and leaves `key_value` empty, so the pasted line hunts for a working set named
`pref.agent.<agent>.access=full`. `box set` takes its arguments as a LIST, so the key alone parses
and the write lands on whatever box the reader's cwd resolves to — a different box, silently. Both
were measured against the real parser; both read fine on the page.

⮕ This function exists because the two cures DIVERGED: `_retired_mirror_cure` forked on the level
correctly while `_retired_key_cure` hardcoded `box` and populated the positional only at box level,
so a workset file got `box set` with no subject. One derivation, so they cannot drift again.

```_retired_mirror_cure(*, level: str, box_name: str | None, table: dict[Any, Any]) -> str```
The LEVEL-APPROPRIATE fix for a TABLE-valued `box.agent` — the retired settable agent MIRROR (R-4).

One `_cure_assignment` tail per leaf in the stored table (an empty table falls back to the bare
`<key>=<value>` shape). Where a request may be WRITTEN it is the §2h `pref.agent.<agent>.<key>`
form; elsewhere it is the same REMOVE-it refusal the scalar cure gives at base / system / agent
scope, offering `kanibako agent set` for a user who meant to tweak the agent everywhere.

⚑ The agent renders as the `<agent>` PLACEHOLDER — the `_retired_behavior_cure` shape. This seam
runs BEFORE selection, so naming an agent here would be a GUESS, and it is exactly the guess a box
carrying this table most needs kanibako not to make.

⚑ The verb and its subject come from `_cure_subject`.

```_retired_key_cure(key: str, *, level: str, value: str, box_name: str | None = None, mirror: dict[Any, Any] | None = None) -> str```
The LEVEL-APPROPRIATE fix for a retired key (M-4).

*mirror* is the TABLE a `box.agent` leaf holds when that is its shape — the ONE discriminator
between this key's two retired spellings (see "Retired spellings" above). Not `None` ⇒ the cure is
delegated whole to `_retired_mirror_cure`. `None` means the SCALAR agent-name spelling, and every
other retired key.

`system.default_agent` always gets the same cure — the replacement is a SYSTEM-scope key wherever the
stale leaf was found. A scalar `box.agent` / `box.agent_name` gets the §2h request, but ONLY where a
request may be written. Elsewhere, M-4: *"A `box.agent_name` found in a system or agent file has no
legal pref equivalent — flag it rather than silently relocating it."*

*box_name* is the addressable box the cure is FOR: `kanibako box set` takes the box as a REQUIRED
`[project]` positional unless the caller's cwd already resolves to that box, and Jei hit that gap
live on a cure that omitted it. Threaded ONLY for a box-level refusal — `None` at any other *level*,
where no single box is being refused for. The verb and its subject then come from `_cure_subject`,
so a WORKSET file's cure is `workset set <workset> …`, not `box set …`.

⚑ The base / system / agent REMOVE-it arm names no single box either, so the "if you meant one box"
line it closes with carries the `<box>` placeholder rather than a bare `box set`.

```_nested_present(raw: Any, parts: tuple[str, ...]) -> Any```
Read *raw* at the nested *parts* path, or `_NO_LEAF` when ABSENT.

Distinguishes ABSENT from PRESENT-`None` (see `_NO_LEAF`).

```refuse_retired_keys(raw: Any, *, level: str, path: Path | None, box_name: str | None = None) -> None```
RAISE when *raw* still carries a RETIRED agent-selection or agent-mirror key (P7).

The three keys are `RETIRED_FILE_KEYS`. The message names the KEY, the FILE, the fact that THE RULE
CHANGED, and the one-line cure — it must never read as "your config is wrong" (the M-7 precedent).
Never a warning and never a silent drop: a dropped `box.agent_name` would leave the box launching the
system default with that agent's credentials, which is the exact failure this refusal exists to
prevent. Called at the SELECTION seam (`settings.agent_select`) and from the §0 resolve refusal
(`settings_launch._refuse_retired_spelling`), not inside `assemble_levels`.

⚑ **This is where the SHAPE discriminator is read:** a found `box.agent` holding a `dict` is the
MIRROR, anything else is the agent NAME. That one test picks BOTH halves of the message — the story
constant that opens it, and the *mirror* argument that steers the cure.

The cure carries the value the user ACTUALLY has, so it is copy-pasteable rather than a shape to fill
in; a present-`None` (an empty `agent_name:` leaf) has no value to quote, so it gets the shape.

*box_name* is passed straight through to `_retired_key_cure`, and is only meaningful at
`level="box"`.

```_behavior_leaf_sites(raw: Any, leaf: str) -> list[tuple[tuple[str, ...], Any]]```
Every (nested path, value) where *leaf* is present in *raw*.

Walks exactly the `_BEHAVIOR_TABLE_SHAPES` — no free-form recursion, so an unrelated user key that
happens to be spelled `auto_approve` deeper in some other table is not swept up.

```_retired_behavior_cure(successor: str, *, level: str, tier: str, subject: str | None) -> str```
The LEVEL-APPROPRIATE fix for a retired BEHAVIOR key (M-22).

`access` is an AGENT-scope key, so where it may be WRITTEN depends on the file the stale value was
found in — the same asymmetry `_retired_key_cure` handles for the selection keys:

* `base` / `system` — a bare agent key is a DOWNWARD write from system scope, so the system verb sets
  it for every agent.
* `agent` — the per-agent file has its own verb (*subject* is the node).
* `workset` / `box` — a BARE agent key is an UPWARD write there (`agent ⊃ workset ⊃ box`) and is
  dropped at assembly, so the legal spelling is the §2h REQUEST, which is exactly where a per-box
  permission tier belongs.

⚑ **Two different subjects, one word.** *subject* here names the AGENT node, NOT the box or workset
the verb addresses. This seam is handed no box name at all, so the verb's own positional comes from
`_cure_subject(level, None)` and is always the placeholder. Reading *subject* as the verb's subject
is how this cure shipped as `workset set pref.agent.<agent>.access=full`, with the positional
missing outright.

```refuse_retired_behavior_keys(raw, *, level, path, subject=None) -> None```
RAISE when *raw* still carries a RETIRED behavior key (R-41 / RQ-2).

Today that is exactly one key, `RETIRED_BEHAVIOR_KEYS`. The message names the KEY, the SPELLING
found, the LEVEL, the FILE, the fact that THE RULE CHANGED, the user's own value translated through
the RULED mapping, and the one-line cure. Never a warning and never a silent drop: the whole point is
that an undeclared stored key is silent, and silence on the permission axis resolves to the PERMISSIVE
default.

*subject* is the agent node the cure should name (the file's own node for an agent file, the box's
active agent otherwise); `None` renders the shape `<agent>`. Called at the LAUNCH's behavior tier and
from the §0 resolve refusal (`settings_launch._refuse_retired_spelling`, which passes no *subject* —
it runs before agent selection, so naming one would be a guess), not inside `assemble_levels`.

⚑ The value line only ever states a translation the RULING makes. An unparseable stored value gets
the legal tiers instead of a guess.

```_containing_scopes(file_scope: str) -> frozenset[str]```
The scope tokens that CONTAIN *file_scope* (spec §0, the drop-set).

A settings file contributes keys of its OWN scope and of scopes it CONTAINS (defaults-down); a
top-level key naming a CONTAINING scope is an UPWARD write that `_drop_upward_scopes` drops at
assembly. Containment is `system ⊃ agent ⊃ workset ⊃ box` (`kb_store.SCOPE_CONTAINMENT`, single
source), so the containing set is the HEAD-slice strictly BEFORE *file_scope*. The outermost scope
(`system`) has an empty set — nothing contains it.

```_drop_upward_scopes(raw: dict, *, file_scope: str, path: Path | None) -> dict```
Drop a CONTAINING-scope, `meta:` or `binding_derivations:` top-level table (spec §0).

**THREE dropped tokens, THREE distinct rationales, one warning each.**

1. **A CONTAINING scope's table.** Directional enforcement at RESOLVE: a settings file may set keys of
   its own scope and of scopes it CONTAINS, but a top-level key of a CONTAINING scope (`system:` /
   `workset:` in a box file) is an UPWARD write, dropped here before it enters the partial. Downward
   and same-scope tables are untouched — a workset file's `box:` defaults-down table still flows (the
   Jei-ruled defaults-down mechanism).
2. **`meta`**, for EVERY file (`base` included). `meta.*` is a TOP-LEVEL protected read-only namespace
   set by the construct-time/bootstrap layer, RO everywhere (spec §0 / clause 4, "`meta.*` remains RO
   everywhere"); a settings file may not set it. `meta` is NOT a containing scope, so it earns a
   DISTINCT warning. ⚑⚑ **NO WORKSET-SCOPE CARVE-OUT, since 2026-08-22.** A workset file's
   `meta.workset` member used to be the spec-sanctioned NAMED-root identity marker and dropped
   SILENTLY, with the warning naming only the OTHER members. A workset root has NO identity table
   now — it is named by the global registry — so that member is a RETIRED shape which
   `project/workset.py`'s `refuse_retired_workset_identity` hard-refuses upstream of this warning; it
   warns like every other scope, and the warning names the whole table. ⚑ **The drop is TOP-LEVEL ONLY:** the loop iterates top-level keys and
   never descends, so a nested `<scope>.meta` table rides under its scope untouched. The sole
   sanctioned meta source is the FLOOR (`dotted_partial`), inserted separately and never routed
   through this drop.
3. **`binding_derivations`** (spec §0 fault class: "never enters the merge"). It is the RESERVED
   INTERNAL derivations node at the snapshot root (R-8, manifest `not_keys.reserved_internal`) —
   machinery output regenerated per launch by `commands.start._install_derived_bindings`, not a key.
   A hand-forged table in a settings file would otherwise ride into the snapshot beside the real
   materialisation: phantom `--effective` lines, and a non-`Bind` leaf crashes the `derived_bindings`
   lens with `ViewError`. Same profile as `meta` — EVERY file, TOP-LEVEL ONLY. SCOPE TIGHT: this ONE
   name only; arbitrary unknown top-level tables still ride, because general unknown-table refusal is
   the backlogged keyspace-ENFORCEMENT work, not this drop.

`base` is EXEMPT for SCOPE keys (its containing set is empty — it is the system-scope floor) but NOT
for `meta`: a base-file top-level `meta:` table would clobber the floor's materialized identity
anchors, so it drops too. Defensively, `base` is not in `SCOPE_CONTAINMENT` at all, so `.index` would
raise — an unknown/base scope takes an empty containing set.

Returns a shallow copy with the dropped keys removed (never mutates *raw*); a non-dict *raw* is
returned unchanged. Warning-only side effect, no raise — a mis-scoped key is a config mistake, not a
hard error.

```_parse_node(value, *, in_binds: bool, dest_keyed: bool = False, at_bindings: bool = False) -> Any```
Recursively coerce a raw settings node into the `StoreValue` space.

*in_binds* is True while descending the subtree of a bind-shaped category (`bindings.{ro,rw}` /
`caches` / `seeded` / `common` / `synced`), where a list/tuple LEAF is a structured entry parsed to a
bind value (S9). Refs inside stay RAW (spec §0). A plain `dict` descends; any other leaf (scalar /
`None` / a genuine `list[str]`) is stored verbatim (`KeyStore` wraps `None` and scalars as-is; a list
is not descended).

*dest_keyed* selects WHICH bind shape a leaf under a bind category has, and is the ONLY thing that
decides it (disk-store rework R-3/R-6):

* `False` — NAME-keyed, a leaf is `[host_src, box_dest[, opts]]` → `Bind`. ⚑ NO bind-shaped category
  reaches this branch any more; it survives for a MALFORMED node that `parse_bind_map` handed back.
* `True` — DEST-keyed, the key IS the destination and a leaf is `[src[, opts]]` → `BindEntry`.

⚑⚑ Both shapes admit a 2-element list with OPPOSITE meanings, so the choice is made by this CONTEXT
FLAG — passed down from the caller that knows which node it is reading — and NEVER by inspecting the
leaf's arity.

*at_bindings* says "the dict I am about to descend is the `bindings` category node, so its keys are
ARMS and each arm's value is a `BindMap`". It is set by this function itself when it walks past a
`bindings` token, which is what wires user settings FILES onto the dest-keyed route (P6) —
`_file_partial` needs no flag of its own, because the shape is a property of the CATEGORY, not of the
caller. ⚑ It is deliberately NOT set for a `bindings` token encountered while already `in_binds`:
inside a bind category the keys are names/destinations, and a user with a `caches` entry literally
named `bindings` must not have it re-read as a category. The `not in_binds` guard on the terminal
four carries the same protection.

⚑ A non-dict at an ARM (or at one of the terminal four) is a MALFORMED node, and it is deliberately
left to the legacy leaf path so that `settings_launch._assert_declared_categories` still produces the
arm-shape message that names the key. Do not raise here instead.

⚑ A malformed ARITY in a structured bind leaf raises `SettingsError` — the structured shape is
load-bearing.

```parse_bind_map(raw: Any, *, category: str = "bindings") -> KeyStore```
Parse a raw DEST-KEYED category map into a `KeyStore` of `BindEntry`.

*raw* is the mapping stored at ANY terminal bind-shaped key: a `bindings` arm
(`<scope>.bindings.ro` / `.rw`, R-5) or one of the four whose category token is itself the whole key
(`<scope>.caches` / `.seeded` / `.common` / `.synced`). Both hold the SAME
`{box_dest: [src[, opts]]}` shape, which is why there is ONE parser and not two — *category* only
names the key in the refusals.

Returns the map as a nested `KeyStore` node, NOT an opaque dict leaf, so it merges PER-ENTRY across
cascade levels through the generic node recursion (the `masks` precedent) instead of a box-level arm
wiping an inherited workset entry wholesale. A `None` entry value is preserved VERBATIM — the
per-entry reset the merge classifies as an OMIT (design §3). A non-mapping *raw* raises
`SettingsError`; so does a malformed entry (`settings_resolve.unpack_bind_entry`).

⚑ **THIS IS THE ONE PLACE A STORED DEST IS CANONICALIZED ON READ** (R-11): every key goes through
`settings_resolve.normalize_bind_dest`, so `~` and `~/` are ONE entry and not two colliding at one
destination. Producers normalize too — they must, because the floor merge in `commands.start` dedupes
on these keys BEFORE anything is parsed — and the function is idempotent, so doing it in both places
costs nothing and neither place is load-bearing alone.
⚑ The VALUE is never canonicalized: a `host_src` stays exactly as authored.

⚑ A nested MAPPING entry is REFUSED by name: under dest-keying an arm's value is a flat
`{dest: [src[, opts]]}`, so a sub-table is the retired `{name: {…}}` shape (spec §2a, "STALE SHAPES
ARE REFUSED LOUDLY"). A 3-element list is refused one level down, by `unpack_bind_entry`.

```_file_partial(raw: dict) -> KeyStore```
Build ONE level partial from a settings file's WHOLE nested content.

*raw* is the parsed file (`load_doc` output). The full scope-ROOTED tree is mirrored into a nested
`KeyStore` with the SCOPE TOKEN KEPT (§0: namespace orthogonal to cascade), refs raw. EVERY
bind-shaped category is read as a DEST-KEYED `BindMap` of `BindEntry` leaves (R-5/R-6): at the ARM for
`bindings.{ro,rw}`, at the CATEGORY TOKEN for the other four. ⚑ The DEPTH is not chosen here —
`_parse_node` derives it from the CATEGORY token it walks past, so there is no flag for a caller to
get wrong.

An empty / non-dict file yields an empty `KeyStore`. This is the rule for every NON-agent level
(`base` / `system` / `workset` / `box`); the agent tier uses `_agent_partial`.

```_agent_partial(raw: dict, *, sub_key: str, path: Path | None = None, node: str | None = None) -> KeyStore```
Build an AGENT-tier level partial (`agent.default` or `agent.<active>`) — the STORE half of the
seam.

It asks `agent_file.level_table` which raw table this level reads (that call is where the nested
refusal fires, BEFORE the flat splice), then `_parse_node`s the table and wraps it under its TRUE
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
parsed to `Bind` (reachable only for a malformed node — see `_parse_node`).

⚑⚑ **DO NOT PUT A CATEGORY-ENTRY SPELLING IN THIS DOCSTRING AS AN EXAMPLE.** `_insert_dotted` REFUSES
BY NAME every floor key that goes DEEPER than the terminal category key. Two examples have now been
burned here: `"box.bindings.rw.home"` (caught when the `bindings` arms went terminal) and its
replacement `"box.caches.pip"` (caught by this pass — verified refused with `SettingsError:
Default-category key 'box.caches.pip' names a 'caches' entry by ENTRY NAME`). A safe example is a
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

That refusal is not decoration: the deeper spelling is exactly what every floor producer emitted
before P6, it still type-checks all the way to launch, and the `<name>` segment would land as a
sibling of real destinations inside the map — a name sitting where a path belongs, which nothing
downstream can tell apart. It also catches a third-party plugin's `default_category_binds()` still
returning the old shape (spec §2d; empty for all first-party plugins today).

⚑ Uses UNBOUND `dict.get` (S3): never the bound `node.get` — a leaf named `get` would shadow the
method into a crash. These stores are module-built, but keeping the collision-safe convention uniform
is the point.

```assemble_levels(*, agent_name, base_path=None, system_path=None, agent_path=None, workset_path=None, box_path=None, floor=None) -> list[KeyStore]```
Read each cascade scope's settings file into ONE nested `KeyStore` partial; return them ordered.

MOST-SPECIFIC-FIRST (S8). The 6 levels, in order:

    [box, workset, agent.<active>, agent.default, system, base]

matching spec §2's `base < system < agent.default < agent.<active> < workset < box` reversed to
high→low precedence — the merge walks this order and the first scope that SETS a leaf wins.

Each non-agent level's partial is its file's WHOLE nested content, scope token KEPT (§0). The agent
file yields BOTH agent levels via its `default` and `<active>` sub-tables, each kept under its TRUE
discriminated name (§2d) — NO bare-`agent` collapse.

* *agent_name* selects the active agent's sub-table for the `agent.<active>` level; `agent.default`
  reads the `default` sub-table from the SAME file.
* *base_path* defaults to `settings_base_path()` (the `/etc` floor) — no `machine` tier (S14); the
  cascade ends at `box` (no `required` cap). The base file uses the SAME scoped keyspace as every
  other file, NOT a synthetic `base:` wrapper.
* *floor* (declared defaults + default-categories) is folded UNDER the base file's content into the
  `base` level — a base-FILE set-value beats the floor at the same key, and the floor is the ultimate
  fallback. The floor is also the SOLE sanctioned `meta.*` source: a top-level `meta:` table is
  dropped from every FILE view (base included, spec §0 / clause 4) BEFORE it is built, so it can never
  clobber the floor's identity anchors; the floor itself is inserted separately and never dropped.

Binds are parsed with `@`-ref / `$var` / `~` tokens left RAW (S9 / spec §0). Absent / unreadable files
yield an empty `KeyStore` partial, skipped cleanly by the merge. NO `machine` path is consulted.

⚑ **The two filters run on the RAW file view, BEFORE the partial is built, and that ordering is
load-bearing.** The agent tier never mirrors a non-`self:` table into its partial, so a post-partial
filter could not see — or warn about — a `system:` or `pref:` table in the agent file. The raw view
catches both.

* `refuse_pref_table` — a `pref:` table is legal in the WORKSET and BOX files ONLY (spec §2h, "this is
  what BOUNDS the recursion"). In the base / system / agent file it is DROPPED with a warning, the
  SAME treatment the sibling mis-scope gets: two behaviours for one fault class is the confusion §0's
  convention 0 forbids, and dropping preserves the recursion bound at least as strongly as erroring
  would. The HARD refusal §2h calls for lives at the WRITE site (`config set pref.*` at these scopes
  RAISES), which is the only way a user creates one short of hand-editing.
* `_drop_upward_scopes` — each USER settings file may set keys of its OWN scope and of scopes it
  CONTAINS, but not of a CONTAINING scope. The `system` file's containing set is empty (outermost), so
  its scope-key pass is a no-op. The `base` level (floor dict + `/etc` base file) is a CODE FLOOR and
  is EXEMPT for SCOPE keys — it is the system-scope floor from which the auth gate is set — but a
  top-level `meta:` table is dropped from it too. `file_scope="base"` yields an empty containing set,
  so base loses ONLY `meta`; its `system.*` scope floor stays exempt.

⚑ The ONE agent file builds TWO levels, so the drop-and-warn runs ONCE on the shared raw view.

⚑ In the base level the floor is inserted FIRST and the file leaves overlay it, so a base-file entry
wins WITHIN this single level.

```_overlay(base: KeyStore, top: KeyStore) -> None```
Deep-overlay *top*'s leaves onto *base*, in place (same-level combine).

Used ONLY to layer a base-FILE partial over the declared-default floor WITHIN the single `base` level,
so a base-file set-value beats the floor default. **This is NOT the cascade merge** — it is a
same-level union of two SOURCES (floor defaults + the base file).

It descends matching `KeyStore` subtrees so a deep base-file leaf overlays the same deep floor leaf
without clobbering sibling floor leaves; a non-subtree leaf in *top* replaces *base*'s same key
wholesale, because the file is the authoritative source at this level. Uses unbound `dict` ops (S3).
