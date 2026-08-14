# The Per-Agent Settings File — the SHAPE boundary
_`settings/agent_file.py`: the ONE module that spells the agent file's root table_

`self` is **not a key**. It is a FILE-SURFACE ALIAS that SUBSTITUTES to `agent.<agent>`, created
*"exclusively for config files (and maybe commandline)"* — *"There's no need for our code to **ever**
use self"* (rulings 50-52, 2026-08-14). Everything past this module traffics in the ACTUAL agent
reference. This module exists so that is true BY CONSTRUCTION rather than by discipline: it is the
only place a `self` string appears in shipped source, and `test_agent_file_boundary.py`'s AST census
pins that.

Provenance: S1 of the `self` rectification (`plans/2026-08-14-self-rectification-PLAN.md` §3).
Six independent sites used to spell the file's shape — and `agent_file_route`'s own docstring
claimed to be the only one (defect D-1). The six are `agent_file_route` (now `_address`),
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

```AgentFileSlot(path: Path, node: str, tail: str)```  — frozen dataclass
WHERE one per-node value lives.

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

⚑⚑ **The agent file's top-level table is `self:`, NOT `agent:`.** `_address` is the SoT for the per-agent file
shape, and `level_table` reads `raw[_ROOT]`. *(The `_agent_partial`
docstring claimed "rooted at a top-level `agent:` table"; that is `config.read_agent_settings`'s
shape, over a different file. Dropped, not relocated.)* Inside `self:`:

* flat state (`model` / `endpoint` / `access` / …) lives DIRECTLY under `self`;
* `env.*` and `secret_path.*` live DIRECTLY under `self` (`self` EXPANDS to `agent.<node>`, so a
  second `<node>` level would read `agent.<node>.<node>.*`) — which is why `_agent_partial` splices
  BOTH in for the ACTIVE layer only (`_FLAT_AGENT_CATEGORIES`). ⚑ For BOTH, the flat table is the
  ONLY spelling: nesting either under a second level is REFUSED BY NAME
  (`_refuse_nested_agent_categories`, rulings 49c + 50), and the literal `default` sub-table with it
  — the all-agents tier of each is written in the SYSTEM file as `agent: default: <category>:`;
* `bindings.{ro,rw}` still live in the DISCRIMINATED `self.<node>.*` sub-table — ruling 50's
  universal reaches them in principle, but the refusal above deliberately does NOT, because nested
  is bindings' only spelling today. The flatten that would make refusing it safe is Jei's call and
  is not started.


## Constants

```_FLAT_AGENT_CATEGORIES: tuple[str, ...] = ("secret_path", "env")```
The categories the per-agent file stores FLAT under `self` rather than in the discriminated
`self.<node>` sub-table. ORDER IS NOT SIGNIFICANT. Its WRITE-side twin is
`_address`. See "the FLAT-CATEGORY splice" below.

```_REFUSED_NESTED_AGENT_CATEGORIES: tuple[str, ...] = ("env", "secret_path")```
The categories NOTHING may nest under inside `self:` (ruling 49c, EXTENDED by **ruling 50** —
*"There is no self:&lt;agent&gt;:foo. It is just self:foo"* / *"self is not a key, it's just an alias /
pointer to agent.&lt;agent&gt;"*).

⚑⚑ **THE MODEL IS ALIAS EXPANSION, not "a second embedding is redundant".** `self` is **not a key**:
it SUBSTITUTES to `agent.<agent>`, so `self.<sub>.<category>` READS
`agent.<agent>.<sub>.<category>` — a key that cannot exist, because *"agent.claude does not contain
«claude»"* (*"That would be agent.claude.claude"* · *"never ever ever"*). The argument is **uniform
over any `<sub>`**, which is why the literal `default` refuses on identical ground and why the agent
file has **no spelling for the `agent.default` tier at all**. *(The earlier "re-spells the node"
framing, and the arm-split explanation that went with it, were superseded as WORDING by ruling 50;
the per-arm CURES stay, because they really do differ.)*

🛑 **`bindings` IS DELIBERATELY ABSENT and that is not an oversight.** Nested is bindings' ONLY
spelling today, so refusing it before a flat route exists would delete a live delivery path
(additive route FIRST). Ruling 50's universal *does* reach it — `self: <node>: bindings:` reads
`agent.<agent>.<node>.bindings`, equally impossible — so the bindings FLATTEN is implied; but the
sequencing is Jei's and **not started**. Do not add it here.

🛑 **NOT `_FLAT_AGENT_CATEGORIES`, and the two must not be merged.** They answer different questions:
that one says where a category IS READ FROM, this one says which nesting is REFUSED. They happen to
list the same two names today; that agreement is a coincidence of the current rulings, not a shared
definition. **Widening either takes a ruling, not a symmetry argument.**

```_CATEGORY_VALUE_PLACEHOLDER: dict[str, str]```
What a cure renders in place of a value when the refused table is EMPTY — `<value>` for `env`,
`<host-path>` for `secret_path` (a secret_path value is a POINTER, and a cure that suggested
otherwise would invite a user to paste a secret into a settings file).

## Functions

```_address(tail: str, node: str) -> tuple[tuple[str, ...], str]```
Map a per-agent-file key TAIL to its `(sections, leaf)` inside the file — the file-shape SoT.

`secret_path.<VAR>` → `(root, "secret_path") / <VAR>` · `env.<VAR>` → `(root, "env") / <VAR>` ·
`bindings.<arm>.<name>` → `(root, node, "bindings") / <arm>…` (the DISCRIMINATED sub-table, still
bindings' only spelling until S2) · anything else → `(root,) / <tail>`.

🛑 **THE `bindings` ARM SPLITS THE DEST ON `.` AND THAT IS A KNOWN DEFECT (D-4), MOVED HERE
VERBATIM ON PURPOSE.** A dotted destination (`bindings.ro.~/.cache/uv`) is shattered across YAML
levels: the read lands on a slot no file has and the write lays down an unusable shape. It is the
FIFTH instance of one root cause (a dest is DATA and is never split). **S3 fixes it red-then-green**
— the write arm refuses with the retirement message, the read arm takes the dest verbatim. S1 is
behaviour-preserving, and a silent repair inside a relocation is exactly how a behaviour change
hides in a move.

```file_spelling(node: str, tail: str = "") -> str```
The agent file's OWN spelling of *node*'s sub-table, with an optional tail.

For the TWO message surfaces that QUOTE the file at a user: `config_keys.
agent_node_bind_retired_error`'s cure (which table to hand-edit) and `settings_categories`'
occupant caveat (how the node is spelled in the file vs. in a containing scope's file). Both used
to build the string with a literal. ⚑ It renders the DISCRIMINATED sub-table, which is what those
two messages are about; S2's flatten changes the wording in ONE place.

```slot_for(agents_root, node, tail) -> AgentFileSlot``` · ```read_leaf(slot)``` ·
```write_leaf(slot, value)``` · ```remove_leaf(slot)```
The per-VALUE half of the boundary — every `config_interface` per-node get/set/reset and every
`agent set`/`reset` goes through these.

⚑ `read_leaf` is a straight pass to `config_io.read_stored_leaf` and must NOT re-render on top of
it: its two conventions (bools lowercase, a stored `""` reading as `None`) are load-bearing for
every `get`.

```clear_overrides(path: Path, node: str) -> int```
Drop every user override from *node*'s file, PRESERVING `name`; return the count.

This was `agent reset --all`'s hand-rolled read-modify-write on the raw document, in a command
module — the sixth shape site. The COUNT is part of the contract, not a detail: each `secret_path`
entry under the node sub-table counts individually (parity with the old flat `env_file` count) and
each other removed key counts once, which is what makes the printed number agree with the other
scopes' `reset_all`.

```load(path: Path) -> AgentConfig``` · ```save(path: Path, cfg: AgentConfig) -> None```
The WHOLE-FILE round trip — the `agent` verbs' own reads (`info` / `show` / `get`) and the
first-use generate.

⚑ **Sparse on the way out**: an EMPTY category is not materialized, or `agent reset --all` would
count a phantom `{}` as an override. ⚑ **`node_tables` is an OPAQUE carry** for the discriminated
sub-table, guarded at BOTH ends against a modelled key riding through. **Measured: no live caller
makes the load→write round trip it protects** — all four `save` callers persist a freshly
generated config (both `start.py` sites gate on `agent_cfg_dirty`, first-use-only; both `cli.py`
sites build inline). The carrier's fate is S2's question; do not restate the guard as a live
guarantee.

⚑ **`load` and `save` are the ONLY names for this round trip** (S1b). The transitional
`agent_config.load_agent_config` / `write_agent_config` forwards — the S1b BRIDGE, which existed
only because `start.py` was held by the P4b lane — are DELETED, together with the flat
`kanibako.agent_config` shim's two re-exports of them; `commands/start.py` imports this module and
calls `agent_file.load` / `agent_file.save`, and `tests/conftest.py` patches `load` HERE.
`agent_file_route` needed no bridge and is likewise GONE (its body is `_address`).

```level_table(raw, *, sub_key, node=None, path=None) -> AgentFileLevel```
Which RAW table one agent-tier level reads — the SHAPE half of the cascade seam.

⚑ **The FLAT-CATEGORY splice, and why it is ACTIVE-LAYER ONLY.** `self` IS `agent.<active-node>`: the
FLATTENED cascade categories — `secret_path` (since the 2026-07-14b flatten) and `env` — live at the
file's TOP level (`self.secret_path` / `self.env`), NOT in the nested `self.<node>` sub-table (which
still holds bindings alone). They belong to THIS node, so `_FLAT_AGENT_CATEGORIES`
is re-rooted alongside the sub-table for the ACTIVE layer ONLY — never the all-agents `default` layer.
Without the splice a category is not in the cascade at all: the launch SECRET export never sees an
agent-scope `secret_path` and no token is mounted.

🛑 **`env` JOINED THAT LIST AT MBR-1 P3, AND ITS ABSENCE WAS A DEFECT, NOT A DESIGN.** The file's env
table was delivered instead as a private under-layer inside `commands.start._build_config_env`, which
cost it two things the cascade gives every other key: it sat BELOW `system.env.*`, inverting the
bracket in which the agent tier outranks system, and it was never a snapshot leaf so it never reached
the expand pass — one written `~` or `$VAR` behaved two ways depending on which FILE spelled it. Both
close by construction here: an `agent.<node>.env.<VAR>` is now an ordinary key that cascades to its
true rung and realizes through the collapse's env slots like every other scope's. The under-layer is
GONE; do not reintroduce a second env channel.

A missing `self` table, or a *sub_key* with no matching sub-table (e.g. an active agent absent from
the file), yields an empty `KeyStore` level.


```_nested_agent_cure(category: str, sub_key: str, *, var: str, value: str) -> str```
The ARM-APPROPRIATE fix for a refused `self.<sub>.<category>`.

⚑ **The EXPLANATION is uniform (alias expansion); the CURES are not** — which is why this is a
function and not one message. For the ACTIVE node the cure is
`kanibako agent set <node> <category>.<VAR>=<value>`, i.e. the flat `self: <category>:` table. For the
literal `default` sub-table there is **no agent-file spelling at all** — the flat table is re-rooted
for the active layer only, so the all-agents tier is written in the SYSTEM file as
`agent: default: <category>:`. Sending an all-agents value to the flat table would silently NARROW it
to one node, so the cure must **not** name `agent set` there.

⚑ **BOTH ROUTES MEASURED LIVE (2026-08-14), for BOTH categories** — a cure naming a dead route is
worse than no cure. `env`: system-file `agent: default: env:` arrives as `agent.default.env.<VAR>` and
does not beat the active node. `secret_path`: same, arriving as `agent.default.secret_path.<VAR>`
through `secret_path_winners`. The `agent set <node> secret_path.<VAR>=<path>` verb is live
(`_address` returns the flat `("self", "secret_path")`; exercised by
`test_agent_cmd.py::test_config_set_secret_path_key`).

```_refuse_nested_agent_categories(node_tbl: dict, *, sub_key: str, node: str | None, path: Path | None) -> None```
RAISE when the agent file nests a `_REFUSED_NESTED_AGENT_CATEGORIES` table under a second level
inside `self:`.

*node* is the agent whose FILE this is; it renders the ALIAS EXPANSION in the message
(`agent.claude.claude.env`) and is never read. `None` renders the shape `<agent>`.

⚑⚑ **THE PLACEMENT IS THE WHOLE DESIGN — three constraints, each of which a plausible implementation
gets wrong.**

1. **It tests `node_tbl`, the sub-table AS READ, and runs BEFORE the flat splice.** After the splice,
   `node_tbl` also holds the flat table, so a perfectly legal flat-only file is indicted for the
   nested table's sin. *(Measured: moving the call after the splice turns
   `test_agent_partial_surfaces_flat_env`, `test_the_agent_file_beats_the_plugin_default` and the
   env arm of `test_persona_loses_to_the_agent_file_active_table` red.)*
2. **A check placed after the splice also catches only the BOTH-spellings case** — and then names the
   WRONG SURVIVOR, since the flat table has already replaced the nested one. Nested-ONLY is the common
   case and would sail through.
3. **It iterates `_REFUSED_NESTED_AGENT_CATEGORIES`, not `_FLAT_AGENT_CATEGORIES`.** ⚑ The two tuples
   list the same two names TODAY, which makes this the easiest constraint to erase by "simplifying"
   — and the one whose erasure is silent. They are different questions, and the next ruling to move
   either (the bindings flatten is the live candidate) separates them again. *(Measured: dropping
   `secret_path` from the refused tuple turns exactly its five parametrized cases red and leaves
   every `env` case green — the coverage is real, not riding its neighbour.)*

⚑ **PRESENCE, not truthiness.** A bare `env:` leaf parses to `None` and an empty one to `{}`; both are
the refused spelling, and both refuse. Same trap `_NO_LEAF` exists for above.

⚑ **WHY REFUSE A SPELLING THAT RESOLVED.** It is not a rename and not migration machinery — it is §0
applied to a spelling that never named a key in the first place. Before the refusal the nested table
resolved to the very SAME `agent.<node>.<category>.<VAR>` keys as the flat one, so two spellings meant
one thing (code conventions rule 0), and a file carrying both lost the nested table WHOLESALE to the
splice — every entry spelled only there vanished with no message. *(Measured for BOTH categories:
`ONLY_NESTED` was absent from the resolve entirely, not merely outranked.)* The refusal makes that
loss unreachable rather than merely documented.

⚑ **THE MESSAGE STATES THE EXPANSION, and that is load-bearing rather than decorative.** A refusal
that only asserts "not a key" is authority; one that says *your spelling reads
`agent.claude.claude.env`* is an argument the user can check against the one rule they now know
(`self` = `agent.<agent>`). The `node` parameter exists for exactly that sentence.

⚑ **PER-ARM HISTORY, and it is not symmetric.** The wholesale-replacement history is true of the
ACTIVE arm only — the splice skips `default`, so nothing there was ever replaced; that arm's history
is instead "it resolved as though it were a tier the SYSTEM file spells". ⚑ For `secret_path` the
nested spelling long predates the 2026-07-14b flatten, so a file written by an earlier kanibako can
carry it in the wild — unlike `env`, whose nesting was only ever hand-written.

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
