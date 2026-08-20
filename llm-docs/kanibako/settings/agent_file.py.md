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
* **RULINGS LEDGER rows 49-52** (`workbook/tasks.md`) — the alias semantics, verbatim, and the
  ruling that `self` never appears in our code. The ledger WINS over this file.

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
shape flip that passes green while the meaning changes. *(Measured 2026-08-14: making it a
NamedTuple turns TWO tests red, both in `TestRouteCarriesNoAddress` — `test_returns_a_slot_not_a_tuple`
(all 3 parametrized targets) on `assert not isinstance(route, tuple)`, and `test_slot_is_frozen`,
whose `is_dataclass(slot)` goes False and whose write then raises `AttributeError` rather than
`FrozenInstanceError`. Nothing else in `test_agent_file_boundary.py` or `test_agent_file.py`.)*

```AgentFileLevel(node: str, table: dict)```  — frozen dataclass
ONE cascade tier read out of the file: its §2d discriminator and its RAW table.

*node* is the discriminator the tier merges under (`default` or the active agent), NOT necessarily
the agent whose file it is — `assemble_levels` builds BOTH tiers from the one file.

## The root

```_ROOT: Final[str] = "self"``` · ```ROOT_SECTIONS: Final[tuple[str, ...]] = (_ROOT,)```

⚑ `ROOT_SECTIONS` exists for the ONE raw-walk site that cannot take a slot or a level:
`settings_assemble._BEHAVIOR_TABLE_SHAPES`, whose rows are uniform `(prefix, depth)` pairs. It is
**not an invitation** — a second raw-walk consumer means the walk itself belongs in here.
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

⚑ **The IDENTITY keys stay in the set deliberately.** A malformed dict-valued `name:` is a mistyped
scalar, not a nested sub-table; it keeps its old handling rather than becoming a refusal about
nesting (pinned by `test_schema_owned_dict_keys_never_captured`).

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

```_TABLE_VALUED_KEYS: Final[frozenset[str]] = _ROOT_TABLES - IDENTITY_KEYS```
Every ROOT key whose VALUE IS A TABLE — DERIVED, so it cannot drift from the shape the file holds:
everything the root may carry except the two identity fields (`name` is a string, `run_args` a list of
them). It answers ONE question — *can a SCALAR be written AT this key?* — and the answer is no for all
of them. It is the D-7 cure's rule and `_write_address`'s backstop, spelled once.

```_CATEGORY_PLACEHOLDER``` · ```_DEST_KEYED_PLACEHOLDER```
What a cure renders when the refused table is EMPTY: a sample `(key, value)` for one entry.
`<VAR>: <value>` for `env`, `<VAR>: <host-path>` for `secret_path` (a secret_path value is a
POINTER, and a cure suggesting otherwise would invite a user to paste a secret into a settings
file), `ro: {<box-dest>: [<host-src>]}` for `bindings`, and one shared dest-keyed shape for the
rest rather than a row each.

## Functions

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
refusal owes the user a cure this function cannot phrase. Reaching it means a caller skipped its gate.

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
file-surface residues `self` is allowed ([spec:15-21, "self"]).
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

⚑ `read_leaf` is a straight pass to `config_io.read_stored_leaf` and must NOT re-render on top of
it: its two conventions (bools lowercase, a stored `""` reading as `None`) are load-bearing for
every `get`.

```clear_overrides(path: Path) -> int```
Drop every user override from the file at *path*, PRESERVING `name`; return the count.

This was `agent reset --all`'s hand-rolled read-modify-write on the raw document, in a command
module — the sixth shape site. The COUNT is part of the contract, not a detail: **each removed ROOT
key counts once**, which is what makes the printed number agree with the other scopes' `reset_all`.

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
first-use-only; both `cli.py` sites build inline). A guard, not a live guarantee. 🛑 Do NOT move a
category into `_MODELED_KEYS` without a field AND a `save` emission: load would capture it out of
the carrier and write would never put it back.

⚑ **`load` and `save` are the ONLY names for this round trip** (S1b). The transitional
`agent_config.load_agent_config` / `write_agent_config` forwards — the S1b BRIDGE, which existed
only because `start.py` was held by the P4b lane — are DELETED, together with the flat
`kanibako.agent_config` shim that re-exported them. ⚑ **That whole shim is GONE in v1.8.0** —
the four flat re-export modules were deleted outright (clean break, no deprecation window), so
`import kanibako.agent_config` now raises `ModuleNotFoundError`; the module is
`kanibako.settings.agent_config`. `commands/start.py` imports this module and
calls `agent_file.load` / `agent_file.save`, and `tests/conftest.py` patches `load` HERE.
`agent_file_route` needed no bridge and is likewise GONE (its body SPLIT at S3 into `_read_address` and `_write_address`).

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
  under `self:` but the categories themselves.

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

```state_level(state, *, node) -> AgentFileLevel | None```
The file's FLAT behaviour state as a DISCRIMINATED level, or `None` if empty.

The per-agent file stores behaviour FLAT (`model` — already per-agent), not under the sub-tables
the cascade merges by, so the discriminator has to be attached somewhere. It is attached HERE, at
the boundary — defect **C-2, CLOSED in S1b**. It used to be attached LATE, at snapshot build
(`settings_launch._agent_state_partial`, from that caller's own `agent_name`), after the state
dict had travelled undiscriminated through `start.py`: the node a table came FROM and the node it
merged UNDER were two independent facts and nothing cross-checked them.

⚑ **The five `start.py` producers all route through here** — `_effective_agent_scalar` (its own
`agent_path` load), `_effective_transform`, `_effective_behavior_for_display`,
`_resolve_box_launch_decisions`, `_resolve_launch_snapshot` — each folding the call INSIDE its
existing gate. `_agent_state_partial` now takes the `AgentFileLevel` alone and reads `level.node`;
there is no parameter left to pass a wrong node in. ⚑ `_effective_behavior_for_display` builds its
level AFTER the `active` node is resolved, not beside `behavior_floor`: the node is the point.
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
