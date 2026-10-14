# Agent-Ref Parsing (``persona+harness`` selection grammar)

`--agent` value is a *ref* that names agent as `persona+harness`, e.g., `navigator+claude`. This
is single, dependency-light, pure parser for the grammar; every ref source (explicit `--agent`,
box/workset/system settings) is normalized before resolver so other code only sees *node-names*.

## Terminology (design SOT ``~/canon/workbook/designs/2026-06-24-agent-variant-DESIGN.md``):

* **harness** — the agent runtime/plugin, right of the separator (``claude``).
  Resolves the target/plugin (``resolve_target`` / ``discover_targets`` key).
* **persona** — the identity, left of the separator (``navigator``); a node
  whose persona name differs from its harness is itself called *a persona*.
* **node-name** — the canonical fused form ``persona℘harness`` (``navigator℘claude``).
  This is the keyspace ``agent.<node>.*`` slot; it is what ``resolve_agent`` returns;
  ``KANIBAKO_AGENT`` stamps its ``+`` display form.  ⚑ It is NOT the on-disk dir name: a store
  directory is not a key, so it takes the ``+`` form
  (``settings.agent_config.store_dirname``, ``agents/navigator+claude/``).

## Separators
Human-typable ``+`` (U+002B) & canonical ``℘`` (U+2118, SCRIPT CAPITAL P — "persona").

⚑ **WHY THERE ARE TWO, and it is one reason only:** a key path is split on ``.`` into segments
drawn from ``SEGMENT_CHAR_CLASS``, which admits no ``+`` — so ``agent.nav+claude.model`` matches
only ``agent.nav`` and silently resolves a different key. ``℘`` exists to make a node spellable
INSIDE a key, and nowhere else does it belong: everything a human types or looks at, the store
directory included, is ``+``.

Both are accepted on input; node-name always canonicalizes separator to ``℘``. Only FIRST separator splits
(persona segment may not itself contain separator; harness segment is whatever follows  & also
separator-checked).

### Backward compatibility (LOAD-BEARING)
Bare ref with no separator (``claude``) parses to ``(raw, raw)`` — node == harness == bare name —
so every bare path is byte-for-byte identical to pre-persona behavior.

## Permitted Characters (Persona / Harness)
A segment follows the name rule (`identifiers.name_flaw`, keyspec §0): 1–64 ASCII letters, digits,
or `_`, not starting with a digit. A refusal names the `_` spelling as its cure
(`_segment_refusal`).

### Regex Details
``SEGMENT_CHAR_CLASS`` is the character-class BODY (no brackets) a segment draws from:
``identifiers.NAME_CHAR_CLASS``, named here for the key-path grammar that reads it.

⚑ ``settings_resolve._REF_SEG`` is composed from it: a node-name is a KEY SEGMENT
(``agent.<node>.…``), so every legal character MUST be matchable there or an ``@``-ref naming the
node truncates mid-name. Deriving one class from the other makes the subset relation hold by
construction.

## The entry-point group

``AGENT_ENTRY_POINT_GROUP`` (``"kanibako.agents"``) is the group a plugin registers its agent under,
spelled once here beside the reservation a registered name must pass. Its readers:
``targets.discover_targets`` (runtime discovery), ``settings.agent_file._installed`` (whether a
node names an installed agent), ``settings.kinemata_keyspace.in_tree_agents`` (the dev-tooling
keyspace gate's agent-node set), and ``kinemata.toml``'s ``reserved-agent-names`` parity, which
calls that function. A distribution's ``pyproject.toml`` still spells the string,
because packaging requires it there.

## Functions

```_is_segment_safe(segment: str) -> bool```
Whether a segment follows the name rule: `identifiers.name_flaw(segment) is None`.

```_first_sep_index(raw: str) -> int```
Index of FIRST separator in *raw*, or `-1` if none.

```parse_agent_ref(raw: str) -> tuple[str, str]```
Parse an agent ref into `(node, harness)`.

* Bare (no separator): `"claude"` -> `("claude", "claude")` — node & harness same (backward comp.)
* Composite: `"navigator+claude"` -> `("navigator℘claude", "claude")` — persona (left) & harness
  (right) are validated & re-joined with canonical ``℘`` separator to form node-name.

Splits on FIRST separator only; second separator in EITHER segment is rejected (each segment
follows the name rule, `identifiers.NAME_CHAR_CLASS`; the refusal offers the `_` spelling).

raises ConfigError: on empty, empty segment, or segment w. illegal character (ex: stray separator),
or a segment that is a RESERVED pseudo-agent name (`PSEUDO_AGENT_NAMES`, keyspec §2d).

```parse_agent_address(raw: str) -> tuple[str, str]```
`parse_agent_ref` for a ref that ADDRESSES an agent rather than NAMING one: `--agent`,
`system.agent`, the `kanibako agent <agent>` positional, a `KANIBAKO_AGENT` stamp read back, a
persona-store lookup. The one difference: a ref naming the built-in shell pseudo-agent (any case,
stripped) parses to `(GENERAL_SLOT, GENERAL_SLOT)` instead of raising.

⚑ **The §2d reservation refuses a CLAIMANT, not an address.** Keyspec §2b invokes the shell box
*"as any other agent"* (`--agent shell`, `system.agent: shell`), and its §2d fence declares its own
settings file (`meta.agent.shell.settings`). Addressing the built-in claims nothing (`[R175]`), so
everything that would CLAIM the name still refuses: a plugin (`targets`), and `shell` as a persona
or harness segment inside a composite (keyspec §2d "Pseudo-agent(s)").
⚑ **Only `shell`.** `default` is the any-agent fallback TIER — no spec line selects it as an agent,
and its fence declares no settings file — so it refuses here as in `parse_agent_ref`.
⚑ **Which one to call:** a site that CREATES or DECLARES a name uses `parse_agent_ref`; a site that
SELECTS or LOOKS UP an existing agent uses this. The KEY-SEGMENT RECOGNIZERS —
`settings_keyspace.is_valid_agent_segment` / `settings_keyspace.agent_declared_leaves`,
`settings_prefs.AgentNames.__contains__` — stay on `canonicalize_agent_ref` on purpose: they read
the `<agent>` segment of a KEY, not a selection, and the keyspace answers the pseudo-agent tiers
itself (`is_valid_agent_segment` admits `PSEUDO_AGENT_NAMES` before it canonicalizes).
⚑ `config_keys.resolve_key` is NOT one of them: it REWRITES the segment to the node a write lands
in, so it goes through `agent_address_node` (below, via `config_keys.agent_key_node`).

```def agent_address_node(raw: str) -> str```
The NODE an agent ADDRESS reaches: `parse_agent_address`, then the HARNESS segment folded through
`identifiers.agent_node_case` (keyspec §0 "An agent's canonical case is carried by its _name_": a
node is lowercase; canonicalizing changes no case). The persona segment keeps the user's case —
the same cut the launch makes when it builds its node from `target.name`, so both name one store.
**The one carrier of this rule:** the `agent` noun's positional (via
`config_keys.agent_key_node`), a typed `agent.<node>.*` key (`resolve_key`, same route), and a
`KANIBAKO_AGENT` stamp read back (`stop`, `code`, the creds watcher, `start`'s reattach). ⚑ **One
exception, a different question:** `config.resolve_agent` SELECTS an agent to launch, so it
substitutes the installed registry's stored spelling for the harness (`find_identifier`) and
refuses an uninstalled one, rather than folding an unchecked spelling. Raises as
`parse_agent_address` does.

```def agent_segment_case(segment: str) -> str```
An `agent.<HERE>` segment a user wrote, with only its CASE folded to the node's: a pseudo-agent
name whole (`Shell` -> `shell`), otherwise the harness, as `agent_address_node` folds it — but the
separator stays as written (`Nav+Claude` -> `Nav+claude`), so a changed result is always a case
change and the caller may warn about it. A segment that is no ref comes back unchanged, for the
keyspace verdict to refuse. The Q87 boundary fold's one derivation: `settings_assemble.
fold_agent_nodes` (files), `config_keys._typed_key_node` (typed keys) and the verdict's
lowercase cure (`settings_keyspace._bad_agent_reason`) all read it.

```def harness_of(node: str) -> str```
Return harness (part right of `℘`) of a *node*-name.

Bare node with no separator IS own harness (`"claude" -> "claude"`). Only canonical `℘` recognized;
callers pass node-names (always canonicalized).  `+` is deliberately NOT split here so literal `+`
in already-canonical node (there should be none) is not mistaken for separator; use
:func:`parse_agent_ref` / :func:`canonicalize_agent_ref` for raw, possibly-``+`` input.

```def persona_of(node: str) -> str```
Return the persona segment (part LEFT of `℘`) of a *node*-name.

Inverse of :func:`harness_of`; node (`navigator℘claude`) yields identity segment (`navigator`) —
name its persona-store entry & generated config-provider block use. Bare node w. no separator has
no distinct persona (`"claude" -> "claude"`); only used for personas (`harness_of(node) != node`).

Reuses same canonical `℘` split as :func:`harness_of` / :func:`with_harness` (design law: never
re-split ref on raw separator at call site).

```def with_harness(node: str, harness: str) -> str```
Return *node* with its harness segment REPLACED by *harness*.

Preserves persona name (left of `℘`) while swapping harness. Its callers use it to put the harness
segment into the RESOLVED target's NODE spelling (`identifiers.agent_node_case`, keyspec §0 "An
agent's canonical case is carried by its _name_": a plugin declaring `Claude` keys the lowercase
`claude` store directory), or into the registry's stored spelling (`config.resolve_agent`), so the
store dir + keyspace slot follow the real target while the persona segment keeps the user's case.

* bare node (`"claude"`, harness `"claude"`) -> `"claude"` (unchanged);
* bare node, re-cased harness (`"Claude"`, `"claude"`) -> `"claude"`;
* persona node (`"navigator℘claude"`, `"claude"`) -> `"navigator℘claude"`;
* persona node, re-cased harness (`"Navigator℘Claude"`, `"claude"`) -> `"Navigator℘claude"`
  (persona keeps its case, harness takes the node's).

⚑ It does not validate, so it will compose a node the grammar refuses: `with_harness(
"navigator℘claude", "shell")` returns `navigator℘shell`, which `parse_agent_ref` refuses — a persona
cannot ride a pseudo-agent (keyspec §2d "Pseudo-agent(s)"). No launch path asks for that: a NAMED
target never falls back to `ShellTarget` (`targets.resolve_target` raises `KeyError`; only the
name-less auto-detect ends at `ShellTarget`), and a missing binary warns and keeps the plugin's
target.

```def display_agent_ref(node: str) -> str```
Return the USER-FACING form of a *node*-name (`℘` -> `+`).

Presentation-only inverse of :func:`canonicalize_agent_ref`. **The KEYSPACE keeps canonical `℘`
form** — that is the one thing `℘` exists for. Everything a human can see or type takes the `+`:
error text, `box` listings, session labels, log lines, **the on-disk store dir**
(`settings.agent_config.store_dirname`) and **the `KANIBAKO_AGENT` stamp**, which an in-box agent
is told to read by the shipped ROM directive.

⚑ The stamp is the one that ROUND-TRIPS: emitted `+`, and canonicalized back to `℘` by every reader
before anything is derived from it (`stop`, `code`, the creds watcher, `start`'s reattach). That is
also what keeps a box stamped by an older version working — both parsers accept both separators.
Every one of those readers goes through `parse_agent_address`, because a plain-shell box is stamped
`shell`: its agent IS the shell pseudo-agent, resolved to `ShellTarget`. On such a box `stop`'s
writeback runs and is a no-op (the shell box's auth source is the private box tier), `code` gets
the `shell` node and no VS Code extension, and a creds watcher run by hand exits at its private-box
arm — a launch never spawns one for a box that shares no credentials.

Bare names contain no `℘` -> returned unchanged (existing output byte-identical).
Does NOT validate: it is pure display swap tolerant of any string.

```def canonicalize_agent_ref(raw: str) -> str```
Return the canonical *node*-name for a raw ref (`+` -> `℘`).

Idempotent: already-canonical or base node returns unchanged. Validates via
:func:`parse_agent_ref`, so malformed ref raises `ConfigError`.
