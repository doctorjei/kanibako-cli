# The Per-Agent Settings File — the SHAPE boundary
_`settings/agent_file.py`: the ONE module that spells the agent file's root table_

`self` is **not a key**. It is a FILE-SURFACE ALIAS that SUBSTITUTES to `agent.<agent>`, created
*"exclusively for config files (and maybe commandline)"* — *"There's no need for our code to **ever**
use self"* ([spec:15-21, "self"]). Everything past this module traffics in the ACTUAL agent
reference. This module exists so that is true BY CONSTRUCTION rather than by discipline: it is the
only place a `self` string appears in shipped source, and `test_agent_file_boundary.py`'s AST census
pins that.

Provenance: S1 of the `self` rectification (`plans/2026-08-14-self-rectification-PLAN.md` §3).
Six independent sites used to spell the file's shape — and `agent_file_route`'s own docstring
claimed to be the only one (defect D-1). The six are `agent_file_route` (now the `_read_address` / `_write_address` pair),
`load_agent_config` (`load`), `write_agent_config` (`save`), `settings_assemble._BEHAVIOR_TABLE_SHAPES`' row (now `ROOT_SECTIONS`), `settings_assemble._agent_partial` (its shape
half is now `level_table`), and `agent reset --all`'s raw surgery (now `clear_overrides`).

## Authority

* **Spec `settings-keyspace-1.8.0.md` §0** — closed keyspace: an undeclared key is an ERROR that
  NAMES it. The nested refusal below is §0 applied to a spelling that never named a key.
* **Spec §2d** — the ONLY two agent key forms are `agent.default.<key>` and `agent.<agent>.<key>`.
* **Spec §2a** — the SECRET / ENV categories and the bind-shaped categories' representation.
* **RULINGS [R50]-[R52]** (the canon rulings ledger) — the alias semantics, verbatim, and the
  ruling that `self` never appears in our code. The rulings WIN over this file.

## What is in here, and what deliberately is not

IN: the root table's spelling · a key TAIL → its `(sections, leaf)` address · the whole-file
`AgentConfig` round trip · which raw table a cascade level reads · the nested refusal and its cures ·
the file's own spelling as a STRING, for the two messages that quote it at a user.

OUT: `KeyStore` and every store coercion (`settings_assemble._agent_partial`, deliberately — see
below) · whether a key EXISTS (`settings_keyspace`) · which FAMILY a spelling is (`config_keys`) ·
whether a NODE is routable (`config_dest.check_agent_node` — key classification, not file shape).

⚑ **The seam is cut at the SHAPE, not at the level, and that is what keeps the import edge one-way.**
`level_table` returns an `AgentFileLevel` — a §2d discriminator plus a RAW dict. If it returned a
`KeyStore`, this module would import the assembler that imports it.

## The two carriers

```AgentFileSlot(path: Path, tail: str)```  — frozen dataclass
WHERE one per-node value lives.

⚑ **It carries no `node` either, since S3.** The node picks the FILE (`slot_for` still takes it) and
nothing else: once the flatten put every category at the file's top level, no address depends on
whose file it is — `self` IS that node. A `node` field kept only for an address that no longer reads
it is a second copy of a fact, waiting to disagree with the path it was derived from.

⚑⚑ **It carries NO `sections`/`leaf`, and that is the whole point (P3/P4).** The per-node resolvers
in `config_dest` used to hand callers a `(path, sections, leaf)` triple, so SEVEN `config_interface`
sites held a `("self", …)` address — internal traffic in the file-surface alias (defect C-1). The
address is produced inside `read_leaf` / `write_leaf` / `remove_leaf` and is unavailable to a caller:
the violation is not forbidden, it is unrepresentable.

🛑 **A FROZEN DATACLASS, NEVER A `NamedTuple`.** A NamedTuple keeps `isinstance(x, tuple)` True and
every `path, sections, leaf = route` unpacking silently working at the WRONG arity — the same-arity
shape flip that passes green while the meaning changes. The dataclass makes both go loudly false.
*(Measured 2026-08-14: making it a
NamedTuple turns TWO tests red, both in `TestRouteCarriesNoAddress` — `test_returns_a_slot_not_a_tuple`
(all 3 parametrized targets) on `assert not isinstance(route, tuple)`, and `test_slot_is_frozen`,
whose `is_dataclass(slot)` goes False and whose write then raises `AttributeError` rather than
`FrozenInstanceError`. Nothing else in `test_agent_file_boundary.py` or `test_agent_file.py`.)*

```AgentFileLevel(node: str, table: dict, path: Path | None = None)```  — frozen dataclass
ONE cascade tier read out of the file: its §2d discriminator and its RAW table.

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
consumer means the walk itself belongs in here.
*(Measured: flipping `_ROOT` to `"selfx"` leaves `TestRootViaConstant` GREEN — it asserts THROUGH the
constant — and turns 22 agent-file fixture cases red. That asymmetry is the design: the pin proves
the root is reached via the constant, the fixtures prove which root ships.)*

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

⚑ **THE FLATTEN (S2) IS WHY THIS IS ONE LIST AND NOT THREE.** `secret_path` flattened at
2026-07-14b, `env` at MBR-1 P3, and `bindings` was the last occupant of the nested shape — kept
there ONLY because nested was its live spelling and refusing before a flat route existed would have
deleted a delivery path. S2 landed the flat route for all six remaining categories at once
(*"self: claude: bindings should never have existed. None of them should"*), so the exception
closed and the refusal became universal.


## Constants

```_MODELED_KEYS: Final[frozenset[str]] = frozenset({"run_args", "env", "secret_path", "transform_settings"})```
The SCHEMA-OWNED root keys — the ones `AgentConfig` holds as FIELDS of its own, as against the flat
categories `category_tables` carries opaquely. Three separate questions read it and none of them
keeps a list beside it: what `load` must NOT sweep into `cfg.state`, what `_CARRIED_CATEGORIES`
subtracts, and what `_ROOT_TABLES` unions with the flat categories to admit at the root.

🛑 **A CATEGORY MUST NEVER JOIN IT WITHOUT BOTH AN `AgentConfig` FIELD AND A `save` EMISSION.** The
failure is silent DATA LOSS, not a read that comes back empty: `load` would capture the table out of
the opaque carrier, and `save`, having no field to emit it from, would never put it back.

⚑ **`run_args` sits here with the other three, not behind a set borrowed from elsewhere.** It is a
modelled field exactly like them; the only thing odd about it is that its VALUE is not a table
(`_SCALAR_WRITABLE_KEYS`, below). *(Measured by removing it: `cfg.state` gains `run_args: "['--a',
'--b']"` — the `str()` of the list — beside the correctly typed field, and `state_level` then carries
that string into the launch cascade as `agent.<agent>.run_args`. Two carriers of one key,
disagreeing in TYPE, with nothing refusing either. It is NOT that the undeclared-state refusal never
sees it: `run_args` is declared, so that refusal would see it and ACCEPT it.)*

```_FLAT_AGENT_CATEGORIES: tuple[str, ...] = ("bindings", "caches", "seeded", "common", "synced", "masks", "secret_path", "env")```
EVERY category the file stores FLAT under `self`. **EIGHT TOKENS, NINE CATEGORIES** — `bindings` is
one token whose `{ro, rw}` table rides WHOLE, exactly as the canonical `agent.<node>.bindings` key
holds both arms. ORDER IS NOT SIGNIFICANT. It is ALSO `_read_address`'s category set, so the shape a
value is STORED in and the shape the cascade READS are one fact rather than two lists agreeing.

```_ROOT_TABLES: Final[frozenset[str]] = _MODELED_KEYS | frozenset(_FLAT_AGENT_CATEGORIES)```
⚑⚑ **THIS SET *IS* THE REFUSAL RULE.** There is no second, enumerated list of refused names,
because there is nothing to enumerate: what is not in here is refused. That is P4 — the
representation deletes the code that would otherwise enforce the rule — and it is what replaced the
`_REFUSED_NESTED_AGENT_CATEGORIES` tuple plus its per-category loop.

The argument the rule rests on: a dict-valued root key outside this set is a nested `self.<sub>:`
sub-table, and `self` is NOT A KEY — it SUBSTITUTES to `agent.<agent>`, so `self.<sub>.<x>` READS
`agent.<agent>.<sub>.<x>`, a key that cannot exist because `agent.claude` does not contain a
`claude` level (*"That would be agent.claude.claude"* / *"never ever ever"*). The argument is
UNIFORM over any `<sub>`, which is why the literal `default` refuses on the same line.

⚑ **So the set holds one key whose value is not a table, deliberately** (`_SCALAR_WRITABLE_KEYS`). A
malformed dict-valued `run_args:` is a mistyped scalar, not a nested sub-table; it keeps its old
handling — `load` coerces it and the carrier never captures it — rather than becoming a refusal about
nesting (pinned by `test_schema_owned_dict_keys_never_captured`). `name` was the other such key until
D8b retired it, so a dict-valued `name:` refuses as a nesting now.

```_CARRIED_CATEGORIES: Final[frozenset[str]] = frozenset(_FLAT_AGENT_CATEGORIES) - _MODELED_KEYS```
The categories that ride `AgentConfig.category_tables` OPAQUELY — every flat category the record
does not model as a field. **ONE set for BOTH ends of the round trip**, which is what makes the
old two-condition write guard unnecessary: a modelled table cannot be captured into the carrier
(load) nor clobbered from it (write), and nothing the carrier can emit is a shape `load` refuses.

```_VERB_WRITABLE_CATEGORIES: Final[frozenset[str]] = frozenset({"env", "secret_path"})```
The categories `agent set` can actually WRITE, and so the only ones a cure may name that verb for.
⚑ The dest-keyed families take a LIST value the verb cannot express — `agent set claude
caches.X=…` would store a dotted literal — and **a message must never prescribe a verb that does
not work** (the same rule `config_keys`' retired-bind cure follows). Their cure is the hand edit
alone. *(Pinned per category, both directions, by
`TestNothingNestsUnderSelfButTheCategories::test_the_nested_spelling_refuses_when_no_flat_table_exists`:
the flat-table YAML is asserted for all nine, the verb line only where it is TRUE — `assert (verb in
message) is (category in ("env", "secret_path"))`.)*
⚑ **It is ALSO `_write_address`'s routing set, and that is one fact, not two** (S3): these are the only
categories holding a SCALAR per name, which is exactly why they are the only ones a scalar write can
address AND the only ones a cure may name the verb for.

```_SCALAR_WRITABLE_KEYS: Final[frozenset[str]] = frozenset({"run_args"})```
Every ROOT key that TAKES a scalar from the command line — the question *can a SCALAR be written AT
this key?*, asked in its own words rather than borrowed. It used to be `IDENTITY_KEYS`
(`kanibako.settings.agent_config`), subtracted below; that set meant *"the root keys that are NOT
tables"* at this site, *"keep this out of `cfg.state`"* at `_MODELED_KEYS`, and *"admit this at the
write door"* in `config_keys` — three unrelated jobs under one false name, dissolved 2026-09-18.
Only the first two have successors: D8b DELETED the write-door allowlist rather than rehoming it —
the §2d gate it short-circuited admits `run_args` unaided (`config_keys.py.md`, the agent-key gate).
⚑ **This is the ONE enumeration of the three**; `agent_config.py.md` points here rather than telling
it a second way.
⚑ It is NOT a claim about the STORED shape: `run_args` takes the scalar and stores it as argv WORDS,
so `_LIST_VALUED_KEYS` is a SUBSET of this set. A plain-scalar modelled key would belong here and not
there. **That subset is ASSERTED at import beside `_LIST_VALUED_KEYS`, not left to prose** (P15): a
member added there and not here fails loudly the moment the module loads.

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
membership question is answered HERE either way, so no caller learns which leaves are lists.

⚑ **THE DEFECT THAT PUT IT HERE (2026-08-29, measured on a real store).** The split lived in
`agent_cmd`'s own writer and nowhere else, so the file had two write routes with two shapes:
`agent set claude run_args="--e --f"` stored a list, `config set agent.claude.run_args="--c --d"`
stored the string. `load` read a list or nothing, so the second route's value was accepted, echoed
at rc 0 and DISCARDED — `agent show` printed no `run_args` line and the launch got no arguments,
while `agent get` (which falls back to the raw file) echoed it back. Two carriers of one shape, the
defect class this tree keeps closing.

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

⚑ **A TAIL, NOT A KEY**, because `AgentFileSlot` carries no node: the caller holding the canonical
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
var-keyed categories is not a key at all, and writing it would lay down a nested sub-table `load`
then REFUSES).

```_is_table_valued(tail: str) -> bool```
Does *tail* name a whole TABLE of the file, so that no scalar can live AT it? `env.<VAR>` and
`secret_path.<VAR>` are the ONLY exception (`_VERB_WRITABLE_CATEGORIES`); every other category — and
`transform_settings`, and a bare `env:` / `secret_path:` — IS the value. It is the one predicate
`_write_address` and `table_value_error` share.

```table_value_error(tail, *, path, verb) -> str | None```
Why *tail* takes no scalar `agent set` / `agent reset` — **the D-7 cure.** `transform_settings`,
`masks` and the dest-keyed tables all hold a MAP, so a scalar written at one is a wrong SHAPE, not a
wrong value; until this refused, a scalar `transform_settings` crashed every subsequent `load` — i.e.
every launch, list, info and show.

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
`agent set`/`reset` goes through these.

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
because two display surfaces need it: `agent_cmd._show_agent_config` (which printed the Python repr
`run_args = ['--a', '--b']` at the user) and `_get_agent_key`.

⚑ **`argv_words` is public since `[R169]` (it was `_argv_words`), and the reason is the LAUNCH.**
The behaviour table hands `run_args` over as the command-line string `argv_text` joined — so a
stored list and a string hand-written into a YAML arrive at `start.py`'s `all_extra` seam
identically — and that seam splits it back with THIS function. One parser, both directions, no
second answer to "what is a word".

```stored_leaf_shape(tail, value) -> object``` · ```stored_leaf_display(tail, value) -> str``` · ```stored_leaf_value(slot) -> object```
`stored_leaf_shape` is the WRITE-side twin of `stored_leaf_text`: given a leaf's tail it returns
the value in the shape a FILE holds it in, or the value unchanged where this module owns no rule
for the pair. `None` PASSES THROUGH, because it is the `--null` suppression idiom (spec §2h) and
splitting it would forge an empty argv line.

⚑ **IT WAS PRIVATE (`_stored_shape`) UNTIL `[R169]`, AND WHAT CHANGED IS NOT THE NAME.** It is now
a SEAM: `config_interface` calls it to normalize before a value lands in a system, workset or box
settings file, so that a `run_args` typed at the CLI is one shape on disk whichever command wrote
it. Before that, `config set run_args="--z --y"` stored the string while `agent set` stored the
list — the same two-carrier defect described above, one tier up.

🛑 **THE ARGUMENT IS THE FILE TAIL, NEVER THE WRITTEN LEAF'S NAME**, and a caller that confuses the
two breaks a user's data. A user may name an environment variable `run_args`; its tail is the
DOTTED `env.run_args` (`_address`), which is in no leaf set, so the scalar they typed stays a
scalar. That dotted tail is the WHOLE of the protection — there is no second check behind it, and a
caller keying on the last segment of a key would shell-split every `env.<VAR>` so named.

```python
def stored_leaf_display(tail: str, value: object) -> str
```
`stored_leaf_text`, falling back to `config_io.render_stored_scalar` for anything this module owns
no rule for — **the composed pair, PUBLIC so no surface has to compose it again** (P10). It was
private (`_render_argv`) and framed as `read_leaf`'s argv renderer until 2026-09-20; the body never
was argv-specific, and every verb that shows a stored leaf owes BOTH halves. A surface taking only
the scalar half printed the Python repr `['--a', '--b']` at a user; one taking only the shape half
printed `None` for a stored null. TOTAL, like the convention it falls back to.

```python
def stored_leaf_value(slot: AgentFileSlot) -> object
```
The RAW value the file holds at *slot*, UNRENDERED, or `None` when it holds none — over
`config_io.stored_leaf_object`, so there is still one walk.

🛑 **FOR A DOOR THAT JUDGES THE VALUE, WHICH `read_leaf` CANNOT SERVE.** The `get` renderings are
deliberately not injective: a stored `""` and a stored `'""'` both read back `""` (spec §2h). So a
door asking "did the user leave this empty?" must ask the OBJECT. `agent_cmd._agent_label` asked
the text for one commit and discarded a user's two-character label, falling through to the
plugin-declared one with no message.

🛑 **ABSENT AND A PRESENT-`None` BOTH ANSWER `None` HERE**, which is why this is not a `get` route:
both mean "this file names no value at *slot*", exactly what a FALLING-THROUGH door wants and
exactly what a verb reporting `(not set)` must not be told. That verb uses `read_leaf`.

⚑ An EMPTY LIST renders BLANK through `stored_leaf_display`, not `""` and not `(not set)`. A
present `run_args: []` is the user's explicit "no arguments", so it is a VALUE and reads back as
the empty command line it is; the two-character `""` spelling is the empty STRING's, a different
idiom (spec §2h) that must not be handed to a shape which is not it. The three states stay apart:
absent → `None` (`(not set)`), present-empty-list → a blank line, present-with-words → the words.
⚑⚑ **AND SINCE `[R169]` THE RECORD KEEPS THEM APART TOO** (`AgentConfig.run_args: list[str] | None`).
It used to collapse absent and present-empty into one `[]`, which was harmless only while this file
was the argv's sole source: now `agent.default.run_args` reaches a launch, so the difference is
whether this agent OPTS OUT of that default or lets it through. The display convention here and the
record's three states are the same fact, and they were briefly out of step — the file surface kept
the pair apart while the record did not, so `agent get` reported an opt-out that no launch honoured.
⚑ A STRING here renders through the scalar convention unchanged — that is what the other write
route stored before the routes agreed, and `load` reads it the same way.

```clear_overrides(path: Path) -> int```
Drop every user override from the file at *path*; return the count.

This was `agent reset --all`'s hand-rolled read-modify-write on the raw document, in a command
module — the sixth shape site. "Remove all user overrides" is the `self:` root table deleted
outright — `run_args`, all state keys and every category table go with it — and the file is left
SPARSE, no default key re-materialized in their place. The COUNT is part of the contract, not a
detail: **each removed ROOT key counts once**, whatever it holds (a category table counts as the
one override it is), which is what makes the printed number agree with the other scopes'
`reset_all`.

⚑ **NOTHING IS EXEMPT, AND THAT IS D8b (2026-09-15).** The one key it used to hold back — `name`,
the file's non-key identity field, spared from the deletion and left out of the count — is retired,
and a verb whose whole promise is that it clears the user's settings may not keep one of them. A
file still carrying a `name:` line loses it here, and the printed number counts it: the count is
the root table's whole length. The widening is user-visible and recorded in `MIGRATION.md`.

⚑ **THE COUNT MOVED AT S2, DELIBERATELY.** The per-VAR arm (each `secret_path` entry counting
individually, parity with the old flat `env_file` count) only ever fired for entries found INSIDE
the `<node>` sub-table — a shape the flatten refuses, so it is unreachable. The fixture that used
to report 5 reports 4, with the reasoning written into the test rather than left as a number that
changed. ✅ **The `node` branch and the `node` PARAMETER are GONE at S3**, with the write side —
keeping them at S2 would have made the removal a rider on the read flatten. Deletion behaviour is
unchanged; only the count for a legacy nested file (a shape the flatten refuses anyway) could differ.

```load(path: Path) -> AgentConfig``` · ```save(path: Path, cfg: AgentConfig) -> None```
The WHOLE-FILE round trip — the `agent` verbs' own reads (`info` / `show` / `get`) and the
first-use generate.

⚑ **`load` RUNS THE SAME REFUSAL THE CASCADE DOES** (S2, call (b)): two readers of ONE file must
not disagree about what the file means. Before it, `load` accepted a nested sub-table the launch
refused, so `agent show` described a shape that could not start a box. The escape hatch is intact
and was checked: `agent reset --all` reaches `clear_overrides` only, never `load`, so a file in the
refused shape can still be cleared. The loudest surface is `start.py`'s per-launch load, which is
why the message quality matters more here than anywhere.

⚑ **Sparse on the way out**: an EMPTY category is not materialized, or `agent reset --all` would
count a phantom `{}` as an override. ⚑ **`category_tables` is an OPAQUE carry** — RENAMED from
`node_tables` at S2, and the name is the fact: there is no per-node sub-table any more, so what the
carrier holds is exactly the flat categories the record has no field for. Guarded at BOTH ends by
ONE set (`_CARRIED_CATEGORIES`), which is also why nothing it emits is a shape `load` would refuse.
**Measured: no live caller makes the load→write round trip it protects** — all four `save` callers
persist a freshly generated config (both `start.py` sites gate on `agent_cfg_dirty`,
first-use-only; both `cli.py` sites build inline). A guard, not a live guarantee. 🛑 The rule for
moving a category INTO `_MODELED_KEYS`, and the data-loss shape that breaks if it is ignored, is at
that constant's own block above.

⚑ **`load` and `save` are the ONLY names for this round trip** (S1b). The transitional
`agent_config.load_agent_config` / `write_agent_config` forwards — the S1b BRIDGE, which existed
only because `start.py` was held by the P4b lane — are DELETED, together with the flat
`kanibako.agent_config` shim that re-exported them. ⚑ **That whole shim is GONE in v1.8.0** —
the four flat re-export modules were deleted outright (clean break, no deprecation window), so
`import kanibako.agent_config` now raises `ModuleNotFoundError`; the module is
`kanibako.settings.agent_config`. `commands/start.py` imports this module and
calls `agent_file.load` / `agent_file.save`, and `tests/conftest.py` patches `load` HERE.
`agent_file_route` needed no bridge and is likewise GONE (its body SPLIT at S3 into `_read_address` and `_write_address`).

### What `load` coerces, and the three places it deliberately does NOT

**Flat state is the SCALAR knobs only.** `cfg.state` takes every root entry that is neither a
MODELLED key nor dict-valued. A category table (`env`, `secret_path`, `bindings`, …) is a dict, so
it is never flat state — those ride `_agent_partial`, not the `_agent_state_partial` state channel.

⚑ **The test is EVERY modelled key, not just the one with a scalar slot (S3/D-7).** A narrower test
differs only for a MALFORMED file: a scalar written where a table belongs (`env: oops`) is not
dict-valued, so it swept into state and the launch then carried a modelled field's garbage as an
agent-state knob. ⚑ Dropping `run_args` from the set is the same defect from the other side —
**measured 2026-09-18**: `run_args: ["--a", "--b"]` then lands in `cfg.state` as the string
`"['--a', '--b']"` beside the properly typed field, and `state_level` carries it into the launch
cascade. `_refuse_undeclared_state` does NOT catch that — `run_args` is a declared §2d leaf, so the
§0 gate accepts it.

⚑ **A `None` value is KEPT as `None`, never coerced through `str()`** — the 2026-08-17 ruling, and
it applies to `state`, to `secret_path` **and, since 2026-09-20, to `env`**. 🛑 `env` was the one
table left out, two lines from `secret_path` in the source and disagreeing with it about one
idiom, and the miss is instructive: `AgentConfig.env` was typed `dict[str, str]`, so the coercion
was not a choice the reader made but one the RECORD forced. The four-byte `"None"` then sat INSIDE
the record, past every file-level fallback — which is exactly why `agent get <node>
secret_path.<VAR>` printed `null` while `agent get <node> env.<VAR>` printed `None` over the same
YAML. The field is `dict[str, str | None]` now. The coercion used to turn a hand-edited or
`--null`-written `model: null` into the four-byte string `"None"`: a BOGUS model id that reached the
launch cascade as a real value, silently defeating the exact "this persona needs no model"
declaration the persona-model gate now depends on. `state_level` passes every value through
unchanged, so a present-`None` survives all the way to the cascade's per-key active-over-default
pick — exactly where it needs to keep meaning "explicitly reset/keyless" rather than a string. For
`secret_path` the same `None` means "this VAR is deliberately keyless": a DECLARED third state, not
a malformed second one, where the old coercion produced a garbage path indistinguishable from a
typo'd one. A `secret_path` value is a POINTER — the file's CONTENTS are never persisted here nor
read, only ro-mounted and exported IN-BOX at launch.

⚑ **A stored `run_args` STRING is SPLIT, not discarded**, and that is what made the write routes'
old disagreement recoverable without touching anyone's data. This reader took a list or NOTHING, so
every value the `config set agent.<node>.run_args=…` route wrote — verbatim, as a string — came
back empty. It is a READ rule, not a shim: nothing writes a string here any more, the files that
route already wrote work from the next command on, and the next `save` normalises them. A bare
`run_args:` parses to `None` and means "no arguments", never the word `"None"` — the same trap the
`model: null` paragraph above records; anything else scalar is one word's worth of text and splits
like one. ⚑ **The KEY'S ABSENCE is read by MEMBERSHIP (`"run_args" not in agent_sec`), not by the
value being `None`, and the record then holds `None` rather than `[]`** — the three-state read
(`[R169]`). A bare `run_args:` is a PRESENT "no arguments" and still loads as `[]`, so the two
`None`-looking YAML shapes part company here: one says nothing, the other refuses the any-agent
default. *(The old pin `test_run_args_must_be_list` asserted the empty list and was true of the
code while wrong about the product; it is replaced by
`test_a_stored_run_args_STRING_is_split_not_discarded`.)*

⚑ **Every modelled table is ISINSTANCE-GUARDED, and the READ side stays permissive on purpose**
(S3/D-7). A hand-authored SCALAR at a table-valued key is a wrong SHAPE, but `agent info` / `list` /
`show` are how a user SEES a broken file, so they must not be the thing the broken file kills. The
WRITE side refuses the shape (`table_value_error`), which is what stops one being made.

**`save` is sparse for the same reason at both ends.** An EMPTY `transform_settings: {}` or `env: {}`
would be counted as an override by `agent reset --all`, so nothing empty is materialized.
`transform_settings` is NOT a reset-all exception — when set it is a normal override, wiped like any
other. The file lives at `agents/<agent>/agent.yaml`, and `save` creates that directory.

```level_table(raw, *, sub_key, node=None, path=None) -> AgentFileLevel```
Which RAW table one agent-tier level reads — the SHAPE half of the cascade seam.

⚑ **`sub_key` selects the TIER, not a sub-table.** Since the flatten it does not index into the
document at all: the ACTIVE tier is the file's own flat category tables, and the all-agents
`default` tier is **STRUCTURALLY EMPTY** — the file has no spelling for it.

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

⚑⚑ **AGENT-FILE *STATE* DOES NOT COME THROUGH HERE, AND THAT IS DEFECT D-3 CLOSED.** `model` /
`access` / `endpoint` / … had TWO live routes: the flat one (`cfg.state` → `state_level` →
`_agent_state_partial`'s OWN rung, spliced just above this level at the launch seam) and a nested
`self: <node>: model:` that rode THIS level and lost to the flat one **silently**. The nested
spelling refuses now and this level carries CATEGORIES ONLY — one value, one route. A state key
reappearing in this table is a second rung.
*(Pinned by `test_settings_assemble.test_agent_file_state_does_not_ride_the_file_cascade_level`,
and it is why the launch tests that contend a behaviour scalar build the PRODUCTION PAIR —
`agent_path=` **plus** `agent_state=` — rather than writing the file alone.)*

⚑ **`base_levels[3]` — the `agent.default` level built from this file — is a PERMANENTLY EMPTY
rung**, and `settings_assemble` says so at the call site. The call is KEPT rather than deleted:
dropping it re-indexes every `base_levels[n]` consumer, and whether a structurally-empty rung
should be encoded at all is a re-encoding question boarded on its own.

A missing `self` table, or a non-dict root, yields an empty level.

```_nested_agent_cure(category: str | None, sub_key: str, *, var: str, value: str) -> str```
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

⚑ **BOTH ROUTES MEASURED LIVE (2026-08-14), FOR EVERY CATEGORY** — a cure naming a dead route is
worse than no cure. The agent-file `self: default: <cat>:` and the system-file `agent: default:
<cat>:` emitted byte-identical `CategoryEntry` lists, non-empty, for all six; the `agent set <node>
secret_path.<VAR>=<path>` verb is live (`test_agent_cmd.py::test_config_set_secret_path_key`). The
all-agents seed route is exercised end to end on the production path by
`test_start.py::TestApplyInitSeeds::test_all_agents_seed_tier_is_the_system_file`, which exists
precisely so the flatten cannot prove "the tier stopped working here" without also proving "it
still works there".

```_refuse_nested_tables(root_tbl: dict, *, node: str | None, path: Path | None) -> None```
RAISE when the file's ROOT holds a table that is not one of its own.

⚑⚑ **ONE PREDICATE, OVER THE ROOT — this is the whole design (P4).** *Any dict-valued root key not
in `_ROOT_TABLES` is a nested sub-table and refuses by name.* One rule closes FOUR cases a
per-category loop needed separate handling for, or could not express at all: the CATEGORY case, the
STATE case (a scalar carrier, D-3), the all-agents `default` arm, and every spelling nobody has
thought of yet. It replaced `_REFUSED_NESTED_AGENT_CATEGORIES` plus its loop, and with them the
standing hazard that the refused list and the flat list would drift.

*(MUTATION-MEASURED, 2026-08-14: neutering the predicate — `if True: continue` — turns **64 named
refusal cases red across four files** and NOTHING else: every case of
`TestNothingNestsUnderSelfButTheCategories` (all 9 categories × 5 tests),
`TestLevelTable::test_nested_category_refuses_by_name` (9 × 2 tiers),
`test_nested_state_refuses_too`, `test_an_unknown_root_table_refuses_even_with_no_category_in_it`,
`TestLoadSharesTheRefusal::test_load_refuses_what_the_cascade_refuses`,
`test_settings_assemble::test_another_agents_sub_table_in_the_agent_file_refuses` and
`test_agent_envs::test_a_second_node_level_under_self_refuses_naming_the_spelling`. The 200+ other
launch tests, 80 assemble tests and 54 agent-file tests stay green — the predicate is the sole
enforcement, and the pins are on it rather than beside it.)*

*(AND THE WIDENING IS LOAD-BEARING PER CATEGORY: dropping `bindings` from `_FLAT_AGENT_CATEGORIES`
reds exactly 13 bindings-specific tests across four files while `test_agent_envs.py` stays fully
green — the categories are not riding one another's coverage.)*

⚑ **PRESENCE, not truthiness.** An empty `claude: {}` is still the refused spelling. ⚑ But a BARE
`claude:` leaf parses to `None` and is NOT refused: it is not a table, carries nothing, and
delivers nothing, so `load` sweeps it into state as the scalar it parsed to, like any other stray
root leaf.

⚑ **THE MESSAGE NAMES the offending sub-table, its inner keys, the file, the ALIAS EXPANSION, the
per-arm history and the cure.** The expansion is load-bearing rather than decorative: a refusal
that only asserts "not a key" is authority; one that says *your spelling reads
`agent.claude.claude.bindings`* is an argument the user can check against the one rule they now
know. The `node` parameter exists for exactly that sentence.

⚑ **WHY REFUSE A SPELLING THAT RESOLVED.** Not a rename and not migration machinery — §0 applied to
a spelling that never named a key. Before the refusal the nested table resolved to the very SAME
`agent.<node>.<category>.*` keys as the flat one, so two spellings meant one thing (code
conventions rule 0), and a file carrying both lost the nested table WHOLESALE — every entry spelled
only there vanished with no message. The refusal makes that loss unreachable rather than merely
documented.

🛑 **DIFFERENT LAYER, DIFFERENT RAISE from the cross-scope twin refusal** (`store_collapse.
_refuse_env_twin`, the sole twin raise site). That one arbitrates two DECLARED keys contesting one
slot at COLLAPSE time; this one rejects a FILE SPELLING at ASSEMBLY time, before any key exists.
Neither weakens the other and neither test may stand in for the other's.

```state_level(cfg, *, node, path=None) -> AgentFileLevel | None```
The file's BEHAVIOR as a DISCRIMINATED level, or `None` if it sets none. *path*, the file *cfg* was
read from, rides on the level (see `AgentFileLevel`).

⚑⚑ **IT TAKES THE RECORD, NOT `cfg.state`, AND THAT IS THE `run_args` CASCADE (`[R169]`).**
`run_args` is a behaviour leaf the record models as a FIELD of its own (`_MODELED_KEYS`), so a level
built from `cfg.state` alone dropped it — and the file's argv reached the launch by a SECOND route,
read straight off `AgentConfig.run_args` at `start.py`'s `all_extra` seam, where no
`agent.default.run_args` could ever contest it. Folded in here, the §2d active-over-default pick does
the override with nothing added for it. The fold is **`is not None`, never truthy**: a present
`run_args: []` must SET the key, because that is how an agent opts OUT of the any-agent default;
folding on truthiness hands that agent the very default its empty list refuses.
⚑ It rides as the stored LIST — `effective_behavior` renders it through `stored_leaf_text`, and the
consumer splits that string back with `argv_words`.

The per-agent file stores behaviour FLAT (`model` — already per-agent), not under the sub-tables
the cascade merges by, so the discriminator has to be attached somewhere. It is attached HERE, at
the boundary — defect **C-2, CLOSED in S1b**. It used to be attached LATE, at snapshot build
(`settings_launch._agent_state_partial`, from that caller's own `agent_name`), after the state
dict had travelled undiscriminated through `start.py`: the node a table came FROM and the node it
merged UNDER were two independent facts and nothing cross-checked them.

⚑⚑ **AND IT IS WHERE THE FORWARD-COMPAT PASSTHROUGH CLOSES** (S3, D-5's other end). An undeclared
scalar in the file used to ride into the launch snapshot VERBATIM — the "old
`agent.<name>.<anyleaf>` behaviour" spec §0 SPECIFICALLY EXCLUDES — so the garbage `agent set`
stored was not merely dead, it reached the box. The refusal lives at the boundary and is
**LAUNCH-ONLY on purpose**: `agent list` / `info` read `cfg.state` directly and the repair verbs
never call `load`, so a poisoned file still LISTS, still DISPLAYS, and can still be fixed — only
starting a box on it refuses, by name. (The persona precedent: a broken config is a hard launch
error, never a last-known-good.)

⚑ **The five `start.py` producers all route through here** — `_agent_scalar_pick` (its own
`agent_path` load, which RAISES on a file it cannot read), `_effective_transform`,
`_effective_behavior_for_display`, `_resolve_box_launch_decisions`, `_resolve_launch_snapshot` —
each folding the call INSIDE its existing gate. `_agent_state_partial` now takes the
`AgentFileLevel` alone and reads `level.node`; there is no parameter left to pass a wrong node in.
⚑ `_effective_behavior_for_display` builds its level AFTER the `active` node is resolved, not
beside `behavior_floor`: the node is the point.
Pinned in three places that do NOT substitute for one another: `TestStateLevel` on the boundary
itself; `test_persona_loses_to_the_agent_file_flat_state` and
`test_behavior_floor_and_per_agent_state`, which go red on a wrong node at the CONSUMER SEAM
(`_agent_state_partial`, which files the table by `level.node`) and at no producer; and the
producers' own node arguments, by `TestTheLaunchAgentFileStateMergesUnderTheLaunchNode`
(`tests/test_commands/test_start_assembly.py`) for the launch path (`_resolve_launch_snapshot`) and
`test_box_config_effective_display_matches_launch_behavior_read`
(`tests/test_settings/test_settings_launch_equivalence.py`) for the display path. ⚑ Both producer
pins are mutation-measured; the launch one was UNCOVERED until the S1b fix round wrote it —
`start_mocks` stubs `_resolve_launch_snapshot` out, and the real-chain callers pass `agent_cfg=None`.

```_refuse_undeclared_state(state, *, node) -> None```
RAISE on the first agent-file state key that is not a declared key (spec §0) — the refusal
`state_level` runs, and the reason that boundary is launch-only.

⚑ **THE PLUGIN UNION IS LOAD-BEARING, not a nicety.** `config_keys.agent_key_reason` unions the
leaves the INSTALLED targets declare, and without it a legitimate `agent.goose.provider` would
refuse a working box at launch. The message names the key, says kanibako will not start a box on
it, and points the cure at `agents/<node>/agent.yaml` while noting that `kanibako agent info
<node>` still lists what the file holds.
