# The Per-Agent Settings File — the SHAPE boundary
_`settings/agent_file.py`: the ONE module that spells the agent file's root table_

`self` is **not a key**. It is a FILE-SURFACE ALIAS that SUBSTITUTES to `agent.<agent>`, created
*"exclusively for config files (and maybe commandline)"* — *"There's no need for our code to **ever**
use self"* ([spec:15-21, "self"]). Everything past this module traffics in the ACTUAL agent
reference. This module exists so that is true BY CONSTRUCTION rather than by discipline: it is the
only place a `self` string appears in shipped source, and `test_agent_file_boundary.py`'s AST census
pins that.

## Authority

* **Spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §0** — closed keyspace: an undeclared key is an ERROR that
  NAMES it. The nested refusal below is §0 applied to a spelling that never named a key.
* **Spec §2d** — the ONLY two agent key forms are `agent.default.<key>` and `agent.<agent>.<key>`.
* **Spec §2a** — the SECRET / ENV categories and the bind-shaped categories' representation.
* **RULINGS [R50]-[R52]** (the canon rulings ledger) — the alias semantics, verbatim, and the
  ruling that `self` never appears in our code. The rulings WIN over this file.

## What is in here, and what deliberately is not

IN: the root table's spelling · a key TAIL → its `(sections, leaf)` address · the whole-file
`AgentConfig` round trip · which raw table a cascade level reads · the nested and top-level refusals ·
the file's own spelling as a STRING, for the two messages that quote it at a user.

OUT: `KeyStore` and every store coercion (`settings_assemble._agent_partial`, deliberately — see
below) · whether a key EXISTS (`settings_keyspace`) · which FAMILY a spelling is (`config_keys`) ·
whether a NODE is routable (`config_dest.check_agent_node` — key classification, not file shape).

⚑ **The seam is cut at the SHAPE, not at the level, and that is what keeps the import edge one-way.**
`level_table` returns an `AgentFileLevel` — a §2d discriminator plus a RAW dict. If it returned a
`KeyStore`, this module would import the assembler that imports it.

## The two carriers

```AgentFileSlot(path: Path, tail: str, node: str)```  — frozen dataclass
WHERE one per-node value lives.

⚑ **It carries the `node`.** The file's `agent:` table is READ (Q92), so `agent: <node>:` is a
second spelling of the node's own settings, and which spelling holds a value depends on the node.
`_spelled_sections` asks the file: the own-node spelling when it HOLDS the leaf, else `self:`, for
every read, write and remove. Written under `self:` beside a value already under `agent: <node>:`,
a `set` would produce a file every reader then refuses (the Q103 two-spelling refusal).

⚑⚑ **It carries NO `sections`/`leaf`, and that is the whole point (P3/P4).** A caller holding a
`("self", …)` address would be internal traffic in the file-surface alias. The address is produced
inside `read_leaf` / `write_leaf` / `remove_leaf` and is unavailable to a caller:
the violation is not forbidden, it is unrepresentable.

🛑 **A FROZEN DATACLASS, NEVER A `NamedTuple`.** A NamedTuple keeps `isinstance(x, tuple)` True and
every `path, sections, leaf = route` unpacking silently working at the WRONG arity — the same-arity
shape flip that passes green while the meaning changes. The dataclass makes both go loudly false;
`TestRouteCarriesNoAddress` pins it.

```AgentFileLevel(node: str, table: dict, path: Path | None = None, scope: dict = {})```  — frozen dataclass
ONE cascade tier read out of the file: its §2d discriminator and its RAW table.

*scope* is the file's top-level `agent:` table, RAW, on BOTH tiers (Q92), already judged by
`_contribution`; `settings_assemble._agent_partial` parses it with the scope files' own builder.
It defaults to empty (`field(default_factory=dict)`).

*node* is the discriminator the tier merges under (`default` or the active agent), NOT necessarily
the agent whose file it is — `assemble_levels` builds BOTH tiers from the one file.

*path* is the file the table was read from, or `None` when the caller did not say. It travels with
the table for the same reason *node* does: `settings_launch`'s read-time [R147] check names the file
a bare-relative value sits in, and the focused behavior reads in `commands/start.py` hand
`build_launch_snapshot` a level without an `agent_path`.

## The root

```_ROOT: Final[str] = "self"``` · ```ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)```

⚑ `ROOT_SECTIONS` exists for the ONE raw-walk site that cannot take a slot or a level:
`settings_assemble._BEHAVIOR_TABLE_SHAPES`, whose rows are uniform `(prefix, depth-of-<sub>)`
pairs. It is **not an invitation** — every OTHER consumer takes an `AgentFileSlot`, an
`AgentFileLevel` or `file_spelling` and never sees the spelling at all, so a second raw-walk
consumer means the walk itself belongs in here. `TestRootViaConstant` asserts THROUGH the constant,
and the agent-file fixtures pin which root ships.

## The file's shape

⚑⚑ **The agent file's top-level table is `self:`, NOT `agent:`.** `_read_address` / `_write_address` are the
SoT for the per-agent file shape, and `level_table` reads `raw[_ROOT]`. *(The `_agent_partial`
docstring claimed "rooted at a top-level `agent:` table"; that is `config.read_agent_settings`'s
shape, over a different file. Dropped, not relocated.)* Inside `self:`:

* flat state (`model` / `endpoint` / `access` / …) lives DIRECTLY under `self`;
* **EVERY category** — `env` · `secret_path` · `bindings` (the `{ro, rw}` pair, as ONE token) ·
  `caches` · `seeded` · `common` · `synced` · `masks` — lives DIRECTLY under `self` too. `self`
  EXPANDS to `agent.<node>`, so a second `<node>` level would read `agent.<node>.<node>.*`;
* **there is nothing else.** A dict-valued root key outside those two groups is a nested
  `self.<sub>:` sub-table and REFUSES BY NAME (`_refuse_nested_tables`; [spec:15-21, "self"]),
  the literal `default` included — the agent file has **no spelling for the all-agents
  tier at all**, which is
  written in the SYSTEM file as `agent: default: <category>:`.

**Beside `self:`, the file's `agent:`, `workset:` and `box:` tables are read (Q92, Q85)** — the
contributed set is ONE constant, `_CONTRIBUTED` (`self`, `FILE_SCOPE` and the contained scopes
`contained_scopes("agent")`) — and a key there that the cascade neither reads nor drops REFUSES BY
NAME (`_refuse_stray_roots`, below), because no other audit can see it. The
`agent:` table is read like that table in any settings file: `agent: {<node>: {…}}`, the
all-agents `default` node included; a VALUE there (`agent: 5`) refuses (`_refuse_scope_value`),
and a bare `agent:` is no table.

⚑ **EVERY category is flat, so the refusal is universal** (*"self: claude: bindings should never
have existed. None of them should"*).


## Constants

```_MODELED_KEYS: Final[frozenset[str]] = frozenset({"run_args", "env", "secret_path", "transform_settings"})```
The SCHEMA-OWNED root keys — the ones `AgentConfig` holds as FIELDS of its own, as against the flat
categories `category_tables` carries opaquely. Three separate questions read it and none of them
keeps a list beside it: what `record` must NOT sweep into `cfg.state`, what `_CARRIED_CATEGORIES`
subtracts, and what `_ROOT_TABLES` unions with the flat categories to admit at the root.

🛑 **A CATEGORY MUST NEVER JOIN IT WITHOUT BOTH AN `AgentConfig` FIELD AND A `save` EMISSION.** The
failure is silent DATA LOSS, not a read that comes back empty: `record` would capture the table out of
the opaque carrier, and `save`, having no field to emit it from, would never put it back.

⚑ **`run_args` sits here with the other three, not behind a set borrowed from elsewhere.** It is a
modeled field exactly like them; the only thing odd about it is that its VALUE is not a table
(`_SCALAR_WRITABLE_KEYS`, below). Without it, `cfg.state` would gain the `str()` of the list beside
the correctly typed field — two carriers of one key in the record, disagreeing in TYPE — and the
undeclared-state refusal would accept it, since `run_args` is declared.

```_FLAT_AGENT_CATEGORIES: tuple[str, ...] = tuple(sorted(CATEGORY_FAMILY_ROOTS))```
EVERY category the file stores FLAT under `self` — DERIVED from `settings_categories.CATEGORY_FAMILY_ROOTS`.
It is NOT the dest-keyed set (`settings_keyspace.TERMINAL_CATEGORY_TAILS`):
`env` and `secret_path` are VAR-keyed families stored flat as well. **EIGHT TOKENS, NINE CATEGORIES** — `bindings` is
one token whose `{ro, rw}` table rides WHOLE, exactly as the canonical `agent.<node>.bindings` key
holds both arms. ORDER IS NOT SIGNIFICANT. It is ALSO `_read_address`'s category set, so the shape a
value is STORED in and the shape the cascade READS are one fact rather than two lists agreeing.

```_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)```
⚑⚑ **THIS SET *IS* THE REFUSAL RULE.** There is no second, enumerated list of refused names,
because there is nothing to enumerate: what is not in here is refused. That is P4 — the
representation deletes the code that would otherwise enforce the rule.

The argument the rule rests on: a dict-valued root key outside this set is a nested `self.<sub>:`
sub-table, and `self` is NOT A KEY — it SUBSTITUTES to `agent.<agent>`, so `self.<sub>.<x>` READS
`agent.<agent>.<sub>.<x>`, a key that cannot exist because `agent.claude` does not contain a
`claude` level (*"That would be agent.claude.claude"* / *"never ever ever"*). The argument is
UNIFORM over any `<sub>`, which is why the literal `default` refuses on the same line.

⚑ **So the set holds one key whose value is not a table, deliberately** (`_SCALAR_WRITABLE_KEYS`). A
malformed dict-valued `run_args:` is a mistyped scalar, not a nested sub-table: `record` coerces it
and the carrier never captures it, rather than refusing it as nesting (pinned by
`test_schema_owned_dict_keys_never_captured`). A dict-valued `name:` refuses as a nesting.

```_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS```
The categories that ride `AgentConfig.category_tables` OPAQUELY — every flat category the record
does not model as a field. **ONE set for BOTH ends of the round trip**: a modeled table cannot be
captured into the carrier (read) nor clobbered from it (write), and nothing the carrier can emit is
a shape `record` refuses.

```_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"env", "secret_path"})```
The categories `agent set` can actually WRITE, and so the only ones a cure may name that verb for.
⚑ The dest-keyed families take a LIST value the verb cannot express — `agent set claude
caches.X=…` would store a dotted literal — and **a message must never prescribe a verb that does
not work** (the same rule `config_keys`' retired-bind cure follows). Their cure is the hand edit
alone. Pinned per category, both directions, by
`TestNothingNestsUnderSelfButTheCategories::test_the_nested_spelling_refuses_when_no_flat_table_exists`.
⚑ **It is ALSO `_write_address`'s routing set, and that is one fact, not two**: these are the only
categories holding a SCALAR per name, which is exactly why they are the only ones a scalar write can
address AND the only ones a cure may name the verb for.

```_SCALAR_WRITABLE_KEYS: Final[frozenset[str]] = frozenset({"run_args"})```
Every ROOT key that TAKES a scalar from the command line — the question *can a SCALAR be written AT
this key?*, asked in its own words rather than borrowed. "Keep this out of `cfg.state`" is
`_MODELED_KEYS`' question, and the write door is the §2d agent-key gate, which admits `run_args`
unaided (`config_keys.py.md`).
⚑ It is NOT a claim about the STORED shape: `run_args` takes the scalar and stores it as argv WORDS,
so `_LIST_VALUED_KEYS` is a SUBSET of this set. A plain-scalar modeled key would belong here and not
there. That subset is pinned by `test_agent_leaf_shape.py` (P15).

```_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - _SCALAR_WRITABLE_KEYS```
Every ROOT key whose VALUE IS A TABLE — the complement, so it cannot drift from the shape the file
holds. The answer to the question above is NO for all of them: an entry inside one of these tables is
DATA (a box destination, a VAR, a transform knob), never a key segment of its own. It is the D-7
cure's rule and `_write_address`'s backstop, spelled once.

```_LIST_VALUED_KEYS: Final[frozenset[str]] = frozenset({"run_args"})```
Every ROOT key the file stores as a LIST OF ARGV WORDS rather than as the one string the command
line hands over. **A DIFFERENT QUESTION FROM `_TABLE_VALUED_KEYS`**, and the contrast is the point:
a table-valued key takes NO scalar and is refused by name; one of these TAKES the scalar and stores
it as words. So a translation exists, and both ends of it live here — `stored_leaf_shape` splits
in, `stored_leaf_text` joins back out. ⚑ **BOTH ENDS ARE PUBLIC SINCE `[R169]`, AND THE REASON IS
THAT THE SCOPE SETTINGS FILES HOLD THESE LEAVES TOO.** `write_leaf` / `read_leaf` are this file's
callers of them; `config_interface`'s scope-file doors are the system / workset / box files'. The
membership question is answered HERE either way, so no caller learns which leaves are lists. A
split living in one writer alone gives the file two write routes with two shapes, and a value
stored by the other route is reported set and delivered to no launch.

⚑ **THE PIN IS DERIVED FROM `AgentConfig`, NOT TYPED TWICE** (P13):
`test_agent_file.py::TestTheArgvSHAPEIsTheFileS::test_the_list_valued_set_is_exactly_AgentConfig_s_
list_FIELDS` reads the record's own annotations, so a second list-shaped field reds HERE — where
the split is decided — instead of silently reaching a write route that does not split for it.

```_CATEGORY_PLACEHOLDER``` · ```_DEST_KEYED_PLACEHOLDER```
What a cure renders when the refused table is EMPTY: a sample `(key, value)` for one entry.
`<VAR>: <value>` for `env`, `<VAR>: <host-path>` for `secret_path` (a secret_path value is a
POINTER, and a cure suggesting otherwise would invite a user to paste a secret into a settings
file), `ro: {<box-dest>: [<host-src>]}` for `bindings`, and one shared dest-keyed shape for the
rest rather than a row each.

## Functions

```scalar_family_of(tail: str) -> str | None```
The SCALAR family *tail* names — `"env"` / `"secret_path"` — else `None`.
`_VERB_WRITABLE_CATEGORIES`' question asked of a READ: the two categories holding a scalar per NAME
are exactly the two spec §2a declares scalar, so a caller applying the non-scalar refusal
(`settings_categories.refuse_non_scalar_family_value`) asks this instead of matching the tail itself.
The partition is `_read_address`'s — first segment the category, everything after it ONE name.

⚑ **A TAIL, NOT A KEY**, because the tail is all this asks about: the caller holding the canonical
key phrases the refusal, since §2a requires the whole key be named and a tail is not one. That is why
`read_leaf` does not refuse for itself and `config_interface._read_slot` does it instead.

```_read_address(tail: str) -> tuple[tuple[str, ...], str]```
Map a per-agent-file key TAIL to the `(sections, leaf)` it is READ from — the file-shape SoT.

⚑⚑ **THE PARTITION RULE, AND IT IS THE WHOLE OF IT:** the FIRST segment is the CATEGORY; `bindings`
— and only `bindings` — then takes an ARM; EVERYTHING after that is ONE DESTINATION. Two
`str.partition` calls, never `split(".")`: **a dest is DATA** (a guest-side path, dots and all), so it
is never cut apart and never re-joined. The primitives underneath are dotted-leaf-safe —
`write_nested_key` / `read_stored_leaf` treat the leaf as a literal dict key.

`env.<VAR>` → `(root, "env") / <VAR>` · `secret_path.<VAR>` → `(root, "secret_path") / <VAR>` ·
`bindings.<arm>.<dest…>` → `(root, "bindings", <arm>) / <dest…>` · `<category>.<dest…>` →
`(root, <category>) / <dest…>` · anything else → `(root,) / <tail>`.

⚑ **THE FALLTHROUGH IS LOAD-BEARING** — a tail whose head is not a category is a FLAT root leaf and
reads `(root,) / tail`, including a dotted one; a `settings_categories` claim depends on it.

✅ **D-4 IS CLOSED HERE (S3).** The old arm did `segs = tail.split(".")` and shattered
`bindings.ro.~/.cache/uv` across YAML levels, so the read landed on a slot no file has and a
hand-authored entry read back "(not set)" — while the sibling BOX scope handled the identical
destination fine. It was the **FIFTH** instance of one root cause. Pinned by
`TestTheDestIsData`, per category, with the mutation named in the test.

```_write_address(tail: str) -> tuple[tuple[str, ...], str]```
Where a SCALAR is WRITTEN — **narrower than the read side BY CONSTRUCTION, and that is the point
(P3/P4).** The file holds exactly three kinds of scalar: a flat root leaf, an `env.<VAR>` and a
`secret_path.<VAR>`. Every other category is DEST-KEYED — its entries are destinations INSIDE the
value — so there is no address to produce and this RAISES rather than inventing one. D-4 shipped
because the write side could express a per-entry address at all; it now cannot.

⚑ **THE RAISE IS A BACKSTOP, NOT THE USER-FACING REFUSAL.** Every write caller gates first and names
the key itself (`agent_cmd._agent_key_gate`, `config_interface`'s retired-route preamble), because a
refusal owes the user a cure this function cannot phrase. Reaching it means a caller skipped one of
two gates: the VALUE-SHAPE one (`table_value_error` — the tail names a whole table) or the
CLOSED-KEYSPACE one (`config_keys.agent_write_key_error` — a dotted tail under anything but the two
var-keyed categories is not a key at all, and writing it would lay down a nested sub-table `record`
then REFUSES).

```_is_table_valued(tail: str) -> bool```
Does *tail* name a whole TABLE of the file, so that no scalar can live AT it? `env.<VAR>` and
`secret_path.<VAR>` are the ONLY exception (`_VERB_WRITABLE_CATEGORIES`); every other category — and
`transform_settings`, and a bare `env:` / `secret_path:` — IS the value. It is the one predicate
`_write_address` and `table_value_error` share.

```table_value_error(tail, *, path, verb) -> str | None```
Why *tail* takes no scalar `agent set` / `agent reset` — **the D-7 cure.** `transform_settings`,
`masks` and the dest-keyed tables all hold a MAP, so a scalar written at one is a wrong SHAPE, not a
wrong value; unrefused, a scalar `transform_settings` would break every later read — every launch,
list, info and show.

⚑ **SET AND RESET TAKE IT ALIKE.** A CLI reset would remove the WHOLE table, a different operation
from the per-entry removal the spelling suggests, and "set cannot reach what reset can" is the
get/set-asymmetry class this module's siblings exist to prevent. The hand edit is the honest cure for
both — since `set` can never CREATE one of these tables, every one that exists was hand-authored.
(`agent reset --all` still drops them wholesale: it is the file-wide verb, not a per-key one.)

⚑ It lives HERE, not in the verb, because the cure QUOTES the file's own spelling — one of the two
file-surface residues `self` is allowed ([spec:15-21, "self"]): the boundary writes the file's bytes
and the boundary quotes them back.
`file_spelling(tail)` takes the tail WHOLE: it JOINS under the root and never splits, so a
dotted arm (`bindings.ro`) renders as itself.

```file_spelling(*segments: str) -> str```
The agent file's OWN spelling of *segments*, under the root — `self.env`, `self.claude.bindings`.

VARARGS since S2, because the two callers now want different depths: a CURE names the FLAT table
(one segment — `self.caches`), a REFUSAL names the NESTED shape the user actually wrote (two).
Empty segments are dropped, which is what lets the refusal pass an optional category without a
branch of its own.

Message surfaces that QUOTE the file at a user, all built through here and never with a literal:
`config_keys.agent_node_bind_retired_error`'s cure (which table to hand-edit — the NODE left that
string at S2, and the path it also prints is what still identifies the file),
`settings_categories`' occupant caveat, and this module's own refusal.

⚑ **`test_agent_file_boundary.py`'s AST census is what enforces this**, and it earns its keep: the
S2 caveat rewrite in `settings_categories` reached for a literal `'self:'` inside an f-string and
the census caught it on the first run. Route the site through `file_spelling()`.

```slot_for(agents_root, node, tail) -> AgentFileSlot``` · ```read_leaf(slot)``` ·
```write_leaf(slot, value)``` · ```remove_leaf(slot)```
The per-VALUE half of the boundary — every `config_interface` per-node get/set/reset and every
`agent set`/`reset` goes through these. Each addresses the value under whichever of the node's two
spellings holds it (`_spelled_sections`, above); a new value goes under `self:`.

⚑ `read_leaf` goes through `config_io.read_stored_leaf` and must NOT re-render on top of it: its
conventions (bools lowercase, and spec §2h's empty idioms each spelled apart — present-`None` →
`null`, terminal `""` → `""`) are load-bearing for every `get`. The ONE leaf whose stored shape is
not a scalar hands its own renderer IN instead (`read_stored_leaf(..., render=stored_leaf_display)`,
bound to the tail), which is why the conventions stay untouched rather than being wrapped — pinned
by `test_read_does_not_re_render`, which uses a scalar leaf.

⚑⚑ **EVERY WRITE ROUTE APPLIES `stored_leaf_shape`, AND NO CALLER MAY PRE-SPLIT.** That is the
single-carrier rule for `_LIST_VALUED_KEYS` above: the shape is decided in ONE place, so a second
copy in a caller cannot exist to drift. `write_leaf` applies it for this file; `config_interface`'s
two raw scope-file doors — the bare `agent.default.<leaf>` write and the `pref.*` write — apply it
for the system / workset / box settings files (`[R169]`: one parser, two entry points, one stored
shape).

```argv_words(value: str) -> list[str]``` · ```argv_text(words) -> str```
The two halves of the argv translation. `argv_words` is deliberately `str.split`, NOT
`shlex.split` — adding quote handling would change the MEANING of values already on disk rather
than fix one; a word that must contain a space is hand-edited into the list. `argv_text` is public
because two display surfaces need it: `agent_cmd._show_agent_config` and `_get_agent_key`.

⚑ **`argv_words` is public because of the LAUNCH (`[R169]`).**
The behavior table hands `run_args` over as the command-line string `argv_text` joined — so a
stored list and a string hand-written into a YAML arrive at `start.py`'s `all_extra` seam
identically — and that seam splits it back with THIS function. One parser, both directions, no
second answer to "what is a word".

```stored_leaf_shape(tail, value) -> object``` · ```stored_leaf_display(tail, value) -> str``` · ```stored_leaf_value(slot) -> object```
`stored_leaf_shape` is the WRITE-side twin of `stored_leaf_text`: given a leaf's tail it returns
the value in the shape a FILE holds it in, or the value unchanged where this module owns no rule
for the pair. `None` PASSES THROUGH, because it is the `--null` suppression idiom (spec §2h) and
splitting it would forge an empty argv line.

⚑ **IT IS A SEAM (`[R169]`):** `config_interface` calls it to normalize before a value lands in a
system, workset or box settings file, so that a `run_args` typed at the CLI is one shape on disk
whichever command wrote it.

🛑 **THE ARGUMENT IS THE FILE TAIL, NEVER THE WRITTEN LEAF'S NAME**, and a caller that confuses the
two breaks a user's data. A user may name an environment variable `run_args`; its tail is the
DOTTED `env.run_args` (`_address`), which is in no leaf set, so the scalar they typed stays a
scalar. That dotted tail is the WHOLE of the protection — there is no second check behind it, and a
caller keying on the last segment of a key would shell-split every `env.<VAR>` so named.

```python
def stored_leaf_display(tail: str, value: object) -> str
```
`stored_leaf_text`, falling back to `config_io.render_stored_scalar` for anything this module owns
no rule for — **the composed pair, PUBLIC so no surface has to compose it again** (P10). Every verb
that shows a stored leaf owes BOTH halves. A surface taking only
the scalar half printed the Python repr `['--a', '--b']` at a user; one taking only the shape half
printed `None` for a stored null. TOTAL, like the convention it falls back to.

```python
def stored_leaf_value(slot: AgentFileSlot) -> object
```
The RAW value the file holds at *slot*, UNRENDERED, or `None` when it holds none — over
`config_io.stored_leaf_object`, so there is still one walk.

🛑 **FOR A DOOR THAT JUDGES THE VALUE, WHICH `read_leaf` CANNOT SERVE.** The `get` renderings are
deliberately not injective: a stored `""` and a stored `'""'` both read back `""` (spec §2h). So a
door asking "did the user leave this empty?" must ask the OBJECT (`agent_cmd._agent_label` does).

🛑 **ABSENT AND A PRESENT-`None` BOTH ANSWER `None` HERE**, which is why this is not a `get` route:
both mean "this file names no value at *slot*", exactly what a FALLING-THROUGH door wants and
exactly what a verb reporting `(not set)` must not be told. That verb uses `read_leaf`.

⚑ An EMPTY LIST renders BLANK through `stored_leaf_display`, not `""` and not `(not set)`. A
present `run_args: []` is the user's explicit "no arguments", so it is a VALUE and reads back as
the empty command line it is; the two-character `""` spelling is the empty STRING's, a different
idiom (spec §2h) that must not be handed to a shape which is not it. The three states stay apart:
absent → `None` (`(not set)`), present-empty-list → a blank line, present-with-words → the words.
⚑⚑ **THE RECORD KEEPS THEM APART TOO** (`AgentConfig.run_args: list[str] | None`, `[R169]`):
`agent.default.run_args` reaches a launch, so the difference is whether this agent OPTS OUT of that
default or lets it through. The display convention here and the record's three states are the
same fact.
⚑ A STRING here renders through the scalar convention unchanged, and `record` splits it the same way.

```clear_overrides(path: Path) -> int```
Drop every user override from the file at *path*; return the count.

It is `agent reset --all`. "Remove all user overrides" deletes every table the file contributes
outright — `run_args`, all state keys and every category table go with it — and the file is left
SPARSE, no default key re-materialized in their place. WHICH tables go is `contributed_tables`
(`self:`, `agent:`, `workset:` and `box:`), so the reset clears and counts exactly what the cascade
reads. The COUNT is part of the
contract, not a detail: **each removed LEAF counts once** (`config_io.count_leaves`) — a category
table counts its entries, a list counts once — the unit every scope's `reset --all` prints.

⚑⚑ **IT IS THE REPAIR DOOR, SO IT REFUSES NOTHING.** It reads `contributed_tables`, never
`_contribution`: a file carrying a stray top-level key, or an undeclared leaf under `self:`, stops
`agent_record` and the launch, and `agent reset <node> --all` must still clear it. A stray is not an
override, so it survives the reset and the next start still names it; the reset's job is the
overrides. Pinned in `test_agent_cmd.py`.

⚑ **NOTHING IS EXEMPT.** A verb whose whole promise is that it clears the user's settings may not
keep one of them: a file still carrying a `name:` line loses it here, and the printed number
counts it.

The count is the unit `config_interface.reset_all` uses for a scope table: one `env:` table of two
variables counts 2 here and under a workset file's `box:`, through one counter in `config_io`.

```record(level: AgentFileLevel, *, node: str) -> AgentConfig``` · ```save(path: Path, cfg: AgentConfig) -> None```
The WHOLE-FILE round trip. `record` builds the record from `level_table`'s ACTIVE level for the
file's own node; it is reached through `settings_assemble.agent_record(path, *, node, purpose)`,
THE ONE READER of the agent file — the `agent` verbs' own reads (`list` / `info` / `show` / `get`),
every `start.py` read and `config_interface`'s display. `save` is the first-use generate.

⚑ **EVERY REFUSAL THE FILE OWES RUNS AS IT IS READ** (Q101 option 1: every settings file is
checked for unknown keys as it is read). `agent_record` reads the file once through
`read_settings_files`; `level_table` runs the top-level stray check and the nested one through
`_contribution`, the one verdict the launch also takes; `record` runs the undeclared-leaf check
(`_refuse_undeclared_state`). Two readers of ONE file must not disagree about what the file means,
so `agent show` never describes a file that cannot start a box. The escape hatch is intact:
`agent reset --all` reaches `clear_overrides` only, never `agent_record`.
`agent_record` also refuses the file's alias as its *node* (`settings_keyspace.file_alias_reason`):
a leftover agent store folder named `self` stops every reader by path, while
`agent reset self --all` still clears it.

⚑ **THE RECORD HOLDS BOTH SPELLINGS OF THE FILE'S OWN NODE** (`_own_node_settings`, S4): `self:`
and the `agent:` table's entry for *node* (folded, Q87) are united before the record is built, so
`agent list` / `get` / `show` / `info` display what the launch uses. The union (`utils.deep_merge`)
loses nothing: `_refuse_two_spellings` has refused one setting
written under both. The verbs show a setting once, with no mark of which spelling holds it — they are ONE key.
The record feeds the launch's state level with those values too, beside the `agent:` table
riding the level as *scope*: one value at one node, merged twice, one answer.
⚑ **`node` IS REQUIRED**, keyword-only. The undeclared-leaf check judges a leaf against ONE agent's
declared keys, so a read that could not name its agent could not be checked at all; and every
refusal names `agent.<node>` rather than a placeholder. Each caller already holds the node it built
the path from (`agent list` canonicalizes the store dirname through `config_keys.agent_key_node`,
the same route `agent info`'s positional takes).
⚑ **`agent list` stops at the first refused file**, by name. No list verb in the tree reports an
error per row (the two that catch per row, `workset list` and `image list`, substitute a fallback
value silently), and printing the row anyway would be the silent accept §0 bars. The loudest surface is `start.py`'s
per-launch read, which is why the message quality matters more here than anywhere.
⚑ The stray check reads its drop-set from `settings_drops`, which sits below both this module and
`settings_assemble`, so it passes the tables the cascade drops (`pref:`, `meta:` …) instead of
refusing them.

⚑ **Sparse on the way out**: an EMPTY category is not materialized, or `agent reset --all` would
count a phantom `{}` as an override. ⚑ **`category_tables` is an OPAQUE carry**: there is no
per-node sub-table, so what the carrier holds is exactly the flat categories the record has no
field for. Guarded at BOTH ends by ONE set (`_CARRIED_CATEGORIES`), which is also why nothing it
emits is a shape `record` would refuse. **No live caller makes the read→write round trip it
protects** — all three `save` callers persist a freshly generated config (both `start.py` sites
gate on `agent_cfg_dirty`, first-use-only; the one `cli.py` site builds inline). A guard, not a
live guarantee. 🛑 The rule for
moving a category INTO `_MODELED_KEYS`, and the data-loss shape that breaks if it is ignored, is at
that constant's own block above.

⚑ **`agent_record` / `record` and `save` are the ONLY names for this round trip.** The record type
lives in `kanibako.settings.agent_config`; there is no flat `kanibako.agent_config` module.

### What `record` coerces, and the three places it deliberately does NOT

**Flat state is the SCALAR knobs only.** `cfg.state` takes every root entry that is neither a
MODELED key nor dict-valued. A category table (`env`, `secret_path`, `bindings`, …) is a dict, so
it is never flat state. `cfg.state` serves the agent verbs' reads; the launch reads the file's root
through `level_table`'s *state*, set beside the categories by `settings_assemble._agent_partial`.

⚑ **The test is EVERY modeled key, not just the one with a scalar slot.** A narrower test differs
only for a MALFORMED file: a scalar written where a table belongs (`env: oops`) is not dict-valued,
so it would sweep into state and the launch would carry a modeled field's garbage as an
agent-state knob. Dropping `run_args` from the set is the same defect from the other side: the
list's `str()` would land in `cfg.state` beside the properly typed field, and
`_refuse_undeclared_state` would not catch it — `run_args` is a declared §2d leaf.

⚑ **A `None` value is KEPT as `None`, never coerced through `str()`** — in `state`, in
`secret_path` and in `env`, which is why `AgentConfig.env` and `.secret_path` are typed
`dict[str, str | bool | None]`: a record typed `dict[str, str]` would FORCE the coercion. Coerced,
a hand-edited or `--null`-written `model: null` would become the four-byte string `"None"`: a BOGUS
model id reaching the launch cascade as a real value, silently defeating the "this persona needs no
model" declaration the persona-model gate depends on. The cascade reads the file's values verbatim
(`settings_assemble._agent_partial`), so a present-`None` survives all the way to its per-key
active-over-default pick — exactly where it needs to keep meaning "explicitly reset/keyless" rather than a string. For
`secret_path` the same `None` means "this VAR is deliberately keyless": a DECLARED third state, not
a malformed second one, where coercion would produce a garbage path indistinguishable from a
typo'd one. A `secret_path` value is a POINTER — the file's CONTENTS are never persisted here nor
read, only ro-mounted and exported IN-BOX at launch.

⚑ **A stored `run_args` STRING is SPLIT, not discarded** — a READ rule, so a string hand-written
into the file works, and the next `save` normalizes it. A bare
`run_args:` parses to `None` and means "no arguments", never the word `"None"` — the same trap the
`model: null` paragraph above records; anything else scalar is one word's worth of text and splits
like one. ⚑ **The KEY'S ABSENCE is read by MEMBERSHIP (`"run_args" not in agent_sec`), not by the
value being `None`, and the record then holds `None` rather than `[]`** — the three-state read
(`[R169]`). A bare `run_args:` is a PRESENT "no arguments" and still loads as `[]`, so the two
`None`-looking YAML shapes part company here: one says nothing, the other refuses the any-agent
default. Pinned by `test_a_stored_run_args_STRING_is_split_not_discarded`.

⚑ **Every modeled table is ISINSTANCE-GUARDED, so the record builds.** A VALUE where `env`'s or
`secret_path`'s table goes is refused by name at the end of `record` (`agent.<node>.env` is a
namespace), under both spellings; `agent reset --all` is the door that opens the file. Only
`transform_settings` — a declared key, whatever its shape — is coerced to `{}` here. The WRITE side
refuses a wrong shape (`table_value_error`).

**`save` is sparse for the same reason at both ends.** An EMPTY `transform_settings: {}` or `env: {}`
would be counted as an override by `agent reset --all`, so nothing empty is materialized.
`transform_settings` is NOT a reset-all exception — when set it is a normal override, wiped like any
other. The file lives at `agents/<agent>/agent.yaml`, and `save` creates that directory.

```level_table(raw, *, sub_key, node=None, path=None) -> AgentFileLevel```
Which RAW table one agent-tier level reads — the SHAPE half of the cascade seam.

⚑ **`sub_key` selects the TIER, not a sub-table.** Since the flatten it does not index into the
document at all: the ACTIVE tier is the file's own flat category tables, and the all-agents
`default` tier's table is **EMPTY** — `self:` has no spelling for it. Both tiers carry the file's
`agent:` table as *scope* (Q92), whose `default` node IS a spelling for that tier; which nodes a
tier takes is decided in `settings_assemble._agent_partial`, after the cascade's node fold.

⚑ **The FLAT-CATEGORY re-root, and why it is ACTIVE-LAYER ONLY.** `self` IS `agent.<active-node>`,
so the categories at the file's top level belong to THIS node; re-rooting them for the all-agents
layer would hand one agent's binds to every agent. Without the re-root a category is not in the
cascade at all: the launch SECRET export never sees an agent-scope `secret_path` and no token is
mounted.

🛑 **`env` JOINED THAT LIST AT MBR-1 P3, AND ITS ABSENCE WAS A DEFECT, NOT A DESIGN.** The file's env
table was delivered instead as a private under-layer inside `commands.start._build_config_env`, which
cost it two things the cascade gives every other key: it sat BELOW `system.env.*`, inverting the
bracket in which the agent tier outranks system, and it was never a snapshot leaf so it never reached
the expand pass — one written `~` or `$VAR` behaved two ways depending on which FILE spelled it. Both
close by construction here: an `agent.<node>.env.<VAR>` is now an ordinary key that cascades to its
true rung and realizes through the collapse's env slots like every other scope's. The under-layer is
GONE; do not reintroduce a second env channel.

⚑⚑ **AGENT-FILE *STATE* RIDES THIS LEVEL, BESIDE THE CATEGORIES, AND NOWHERE ELSE (D-3, K4).**
`model` / `access` / `endpoint` / … once had TWO live routes: a flat one (a separate rung the
producers built from the record and spliced above this level) and a nested `self: <node>: model:`
that rode THIS level and lost to the flat one **silently**. The nested spelling refuses, and the
separate rung is deleted: *state* (every non-category root key) is set on this level's node beside
*table*, so every resolve that reads the file reads all of it — one value, one route. A second rung
for the same keys is the defect returning.
*(Pinned by `test_settings_assemble.test_agent_file_state_rides_the_agent_level_beside_its_categories`
and `test_a_bare_relative_agent_file_canon_is_refused_once`.)*

⚑ **`base_levels[3]` — the `agent.default` level built from this file — holds ONLY the file's
`agent: default:` table** (Q92): `self:` has no spelling for that tier, the `agent:` table does.
Which nodes of *scope* each tier takes is `settings_assemble._scope_nodes`' call.

The top-level refusals run first, then the nested one. A missing `self` table, or a non-dict
root, yields an empty *table*; the level still carries the file's `agent:` table as *scope*.

```_nested_agent_cure(category: str | None, sub_key: str, *, entries: list[tuple[Any, Any]], rest: list[str], placeholder: bool = False, home: _SubHome | None = None) -> str```
The ARM-APPROPRIATE fix for a refused `self.<sub>:` sub-table.

⚑ **The EXPLANATION is uniform (alias expansion); the CURES are not** — which is why this is a
function and not one message. THREE arms:

* **`default`** — there is **no agent-file spelling at all**, so the cure is the SYSTEM file's
  `agent: default: <category>:`. Sending an all-agents value to the flat table would silently
  NARROW it to one node, so this arm must **not** name `agent set`.
* **an active node, verb-writable category** (`env` / `secret_path`) — `kanibako agent set <node>
  <category>.<VAR>=<value>`, **or** the hand edit.
* **an active node, dest-keyed category** — the hand edit ALONE. See
  `_VERB_WRITABLE_CATEGORIES` above for why naming the verb here would be a false cure.
* **`category is None`** — the sub-table holds nothing that is a category (state knobs, a typo,
  another agent's name), so there is no table to point at and the cure is the RULE: nothing nests
  under `self:` but the categories themselves. ⚑ This arm must NOT prescribe the DELETION — the
  caller's closing line already does, and a cure that also said "delete it" read as "delete the
  content", the opposite of what to do with it.

`_refused_category` picks the first category the refused sub-table holds, in FILE order rather than
sorted: it names the table the user wrote first, which is the one they are most likely looking at.

⚑ **Every route a cure names is live** — a cure naming a dead route is worse than no cure. The
`agent set <node> secret_path.<VAR>=<path>` verb is pinned by
`test_agent_cmd.py::test_config_set_secret_path_key`, and the all-agents seed route end to end on
the production path by `test_start.py::TestApplyInitSeeds::test_all_agents_seed_tier_is_the_system_file`.

```_refuse_nested_tables(root_tbl: dict, *, node: str | None, path: Path | None) -> None```
RAISE when the file's ROOT holds a table that is not one of its own.

⚑⚑ **ONE PREDICATE, OVER THE ROOT — this is the whole design (P4).** *Any dict-valued root key not
in `_ROOT_TABLES` is a nested sub-table and refuses by name.* One rule closes FOUR cases a
per-category loop needed separate handling for, or could not express at all: the CATEGORY case, the
STATE case (a scalar carrier, D-3), the all-agents `default` arm, and every spelling nobody has
thought of yet. There is no refused list to drift from the flat list. The predicate is the sole
enforcement, and the pins (`TestNothingNestsUnderSelfButTheCategories`, `TestLevelTable`) are on it
rather than beside it.

⚑ **PRESENCE, not truthiness.** An empty `claude: {}` is still the refused spelling. ⚑ But a BARE
`claude:` leaf parses to `None` and is NOT refused HERE: it is not a table, so `record` sweeps it
into state as the scalar it parsed to, like any other root leaf, and the undeclared-leaf check
(`_refuse_undeclared_state`) refuses it there by name.

⚑ **THE MESSAGE NAMES the offending sub-table, its inner keys, the file, the ALIAS EXPANSION, the
per-arm history and the cure.** The expansion is load-bearing rather than decorative: a refusal
that only asserts "not a key" is authority; one that says *your spelling reads
`agent.claude.claude.bindings`* is an argument the user can check against the one rule they now
know. The `node` parameter exists for exactly that sentence.

⚑ **WHY REFUSE A SPELLING THAT RESOLVED.** Not a rename and not migration machinery — §0 applied to
a spelling that never named a key. Unrefused, the nested table would resolve to the very SAME
`agent.<node>.<category>.*` keys as the flat one, so two spellings would mean one thing (code
conventions rule 0), and a file carrying both would lose the nested table WHOLESALE — every entry
spelled only there would vanish with no message. The refusal makes that loss unreachable.

🛑 **DIFFERENT LAYER, DIFFERENT RAISE from the cross-scope twin refusal** (`store_collapse.
_refuse_env_twin`, the sole twin raise site). That one arbitrates two DECLARED keys contesting one
slot at COLLAPSE time; this one rejects a FILE SPELLING at ASSEMBLY time, before any key exists.
Neither weakens the other and neither test may stand in for the other's.

```contributed_tables(raw: Any) -> dict``` · ```_contribution(raw, *, node, path) -> dict```
`contributed_tables` answers WHICH top-level tables the cascade reads (`_CONTRIBUTED`) and judges
nothing; `_contribution` is the same answer after the file's TOP-LEVEL refusals. Every reader
that JUDGES the file (`level_table`, so `agent_record` — `agent show` / `info` / `list` / `get` —
as well as the launch) takes `_contribution`; the reset takes `contributed_tables`, and stays the
repair door.

⚑ **THE FIVE TOP-LEVEL REFUSALS `_contribution` RUNS**, in order: `_refuse_stray_roots` (below);
`_refuse_scope_value` — a VALUE where the `agent:` table's node tables go (`agent: 5`), which merged
as one would replace every other file's agent tables; `refuse_node_spelled_twice` — two keys of
that table reaching ONE node (`Claude:` beside `claude:`, `nav+Claude:` beside `nav℘claude:`),
PUBLIC because it is the one carrier the cascade's fold (`settings_assemble._fold_node_table`)
calls too; `_refuse_node_values` — a NODE of that table
holding a value or nothing (`agent: {bar: 5}`, a bare `claude:`), which names an agent tier, not a
key (§2d), as the launch's §0 audit also says; for the file's own node beside a non-empty `self:`
the message adds that it writes the node twice and would replace every setting under `self:`;
and `_refuse_two_spellings` (Q103) — one setting written under both `self:` and
`agent: <own node>:`, where a PREFIX counts too (`model` beside `model.x`). The last matches the node as the
cascade folds it (`agent_ref.agent_address_node` over `agent_segment_case`, so `Claude:` is
`claude`) and compares SETTINGS as the merge sees them (`_setting_leaves`): a table descends to
its leaves, except that a TABLE-VALUED agent key (`settings_keyspace.TABLE_VALUED_AGENT_LEAVES`,
`transform_settings`) is ONE setting whole, and in a bind category
(`settings_keyspace.BIND_CATEGORIES`) each DEST is one, compared through
`settings_resolve.normalize_bind_dest` (`/a/` is `/a`). `env.A` beside `env.B` merges; so do two
dests of one arm. Every pair is named AS WRITTEN. It needs *node*, so a caller that passes none
skips it. Pinned in `test_agent_file.py` (`TestTheAgentTable`) and `test_agent_cmd.py` (the verbs). ⚑
`_CONTRIBUTED` is the ONE constant every reader takes: `agent:` (Q92), `workset:` and `box:` (Q85).
The reader's view (`settings_assemble.read_settings_files`, and so
`config_interface._noun_stored_view`) takes the agent file through `contributed_tables` too, so the
verbs judge what the merge reads.

```_refuse_stray_roots(raw: dict, *, node: str | None, path: Path | None) -> None```
RAISE on a key at the FILE's top level that the file neither contributes nor drops (spec §0).

⚑ **WHY IT HAS TO BE HERE.** In the other settings files an unknown top-level entry rides into the
launch snapshot, where `settings_launch._refuse_undeclared_snapshot` can refuse it by name. This file
contributes only its own tables, so a stray beside `self:` would never reach that audit: a `model:`
written one level too high would set nothing and say nothing.

⚑⚑ **THE TABLES THE CASCADE DROPS ARE NOT STRAYS.** `system:`, `meta:`, `binding_derivations:`
and `pref:` (`settings_drops.cascade_drop_set("agent")`) are dropped WITH A WARNING by
`assemble_levels` before `level_table` runs; a non-launch reader sees them undropped, so the rule
passes them by the same derivation. `agent:`, `workset:` and `box:` are not strays: they are READ
(Q92, Q85, `_CONTRIBUTED`; spec §0, *"a settings file contributes keys of its OWN scope and of
scopes it CONTAINS"*). Pinned in
`test_agent_file.py`: `TestLevelTable` (the refusal, and the scope-token control) and
`TestTheStrayRuleOnTheProductionPath` (every upstream drop, DERIVED from the drop rules, still warns
and builds).

⚑ It runs in `level_table`, through `_contribution`, so in every `agent_record` read and the
launch; never in `clear_overrides`.

```_refuse_undeclared_state(entries, *, node, path) -> None``` · ```_undeclared_entries(own, scope, *, node)``` · ```_node_tables(own, scope, *, node)```
RAISE naming EVERY agent-file entry that is not a declared key (spec §0), once each, in one
refusal, as the launch's `settings_launch._refuse_undeclared_snapshot` does — the last refusal
`record` runs. Each entry is `(shown, spelled, reason)`, produced by `_undeclared_entries` over the
file's node tables (`_node_tables`: `self:` judged as the file's own *node*, then each `agent:` node
as the cascade folds it, Q87) in TWO passes, each the launch's own verdict:

1. every KEY of a node table except a category key holding a TABLE, through
   `config_keys.agent_key_reason` — which concedes a node whose vocabulary is unreadable (`[R150]`),
   measured to give the launch's §0 audit's verdict on `agent.claude.bogus`, `agent.goose.bogus`,
   `agent.default.bogus`, `agent.Claude.bogus` and `agent.claude.self` (refused) and on
   `agent.nosuchharness.x`, `agent.goose.provider` and `agent.claude+nav.bogus` (conceded). A
   category key holding a value is judged like any key, so `env: 5` refuses and `caches: 'x'` is
   conceded, as the launch does. ⚑ **`self:` takes the SAME partition**, so `self: {env: 5}` is
   judged too;
2. every PATH, category contents included, through the launch's whole-snapshot audit
   (`settings_keyspace.undeclared_store_paths` over `settings_keyspace_probe.keyspace_verdict`, the
   node table placed at `agent.<node>`): an undeclared bind arm (`bindings: {zz: …}`), a malformed
   `env` VAR, and an `agent: {self: {}}` node.

⚑ **What pass 2 does NOT cover:** three malformed bind entries — a retired name-keyed entry
(`bindings.ro: {/x: {q: 1}}`), an entry of three elements, and a bare relative source. They are
refused by `settings_assemble`'s bind parse, which `agent_record`'s read runs as the launch does. Two spellings of ONE node in the `agent:` table (`claude:` beside `Claude:`) are NOT in that
set: `_contribution` refuses them through `refuse_node_spelled_twice`, before either pass runs.

⚑ **THE PLUGIN UNION IS LOAD-BEARING, not a nicety.** `config_keys.agent_key_reason` unions the
leaves the INSTALLED targets declare, and without it a legitimate `agent.goose.provider` would
refuse a working box. The message names the key, says kanibako will not start a box on the file or display it, and
points the cure at the file's own *path* — or at `kanibako agent reset <node> --all`, the one verb
that still opens a refused file.
