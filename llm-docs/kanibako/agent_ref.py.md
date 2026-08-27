# Agent-Ref Parsing (``persona+harness`` selection grammar)

`--agent` value is a *ref* that names agent as `persona+harness`, e.g., `navigator+claude`. This
is single, dependency-light, pure parser for the grammar; every ref source (explicit `--agent`,
box/workset/system settings) is normalised before resolver so other code only sees *node-names*.

## Terminology (design SOT ``plans/2026-06-24-agent-variant-DESIGN.md``):

* **harness** — the agent runtime/plugin, right of the separator (``claude``).
  Resolves the target/plugin (``resolve_target`` / ``discover_targets`` key).
* **persona** — the identity, left of the separator (``navigator``); a node
  whose persona name differs from its harness is itself called *a persona*.
* **node-name** — the canonical fused form ``persona℘harness`` (``navigator℘claude``).
  This is the keyspace ``agent.<node>.*`` slot; it is what ``resolve_agent`` returns
  and what ``KANIBAKO_AGENT`` stamps.  ⚑ It is NOT the on-disk dir name: a store
  directory is not a key, so it takes the ``+`` form
  (``settings.agent_config.store_dirname``, ``agents/navigator+claude/``).

## Separators
Human-typable ``+`` (U+002B) & canonical ``℘`` (U+2118, SCRIPT CAPITAL P — "persona").

⚑ **WHY THERE ARE TWO, and it is one reason only:** a key path is split on ``.`` into segments
drawn from ``SEGMENT_CHAR_CLASS``, which admits no ``+`` — so ``agent.nav+claude.model`` matches
only ``agent.nav`` and silently resolves a different key. ``℘`` exists to make a node spellable
INSIDE a key, and nowhere else does it belong: everything a human types or looks at, the store
directory included, is ``+``.

Both are accepted on input; node-name always canonicalises separator to ``℘``. Only FIRST separator splits
(persona segment may not itself contain separator; harness segment is whatever follows  & also
separator-checked).

### Backward compatibility (LOAD-BEARING)
Bare ref with no separator (``claude``) parses to ``(raw, raw)`` — node == harness == bare name —
so every bare path is byte-for-byte identical to pre-persona behaviour.

## Permitted Characters (Persona / Harness)
In addition to ``str.isalnum()``, these characters may appear in persona/harness names:

⚑ ``str.isalnum()`` is Unicode-aware and that is DELIBERATE: letters and digits in ANY language
are legal, so ``漢字+claude`` and ``café+claude`` are valid refs.

⚑ ``-`` is a non-word character but NOT problematic — ref-name grammar admits it (``0da8778``).
This is standard as intended: when name character broke ref grammar, GRAMMAR was widened rather
than name charset narrowed.

⚑ ``_`` is word character, so 'deny' never arises; it MUST be listed here, as predicate below tests
``str.isalnum()``, & ``'_'.isalnum()`` is False (only divergence in ``str.isalnum()`` & ``\w``).

⚑ ⚑ ⚑ ``.`` is forbidden (REMOVED 2026-08-04). To deny character, must be "not a word character"
AND "problematic"; being symbol alone is insufficient.  ``.`` is both: it is the settings key-path
separator, so dotted nodes make ``agent.a.b℘claude.model`` ambiguous with genuine nested key, & it
admits ``..``, i.e. ``agents/../``.

### Regex Details
Regex character-class BODY (no brackets) matching EXACTLY characters func:`_is_segment_safe`
admits, for consumers that must parse node-name out of larger string.

⚑ ``settings_resolve._REF_SEG`` composed from this, which is whole point: node-name is KEY SEGMENT
(``agent.<node>.…``), so every legal character MUST be matchable there or ``@``-ref naming node
truncates mid-name; it silently renders ``""`` at bind-path & crashes ("expected bool, got str")
at auth site.  The two charsets were separate literals & drifted TWICE (``℘``, then ``-``);
deriving one from other makes subset relation hold by construction.

``\w`` is exactly ``str.isalnum()`` plus ``_`` (swept over all of Unicode by
``test_word_char_class_equals_isalnum_plus_underscore``), & extras are ``re.escape``-d so class is
position-independent — consumer may splice characters in on any side without ``-`` forming range.

## Functions

```_is_segment_safe(segment: str) -> bool```
Non-empty segment of only letters/digits (any language) plus `-`/`_`.

```_first_sep_index(raw: str) -> int```
Index of FIRST separator in *raw*, or `-1` if none.

```parse_agent_ref(raw: str) -> tuple[str, str]```
Parse an agent ref into `(node, harness)`.

* Bare (no separator): `"claude"` -> `("claude", "claude")` — node & harness same (backward comp.)
* Composite: `"navigator+claude"` -> `("navigator℘claude", "claude")` — persona (left) & harness
  (right) are validated & re-joined with canonical ``℘`` separator to form node-name.

Splits on FIRST separator only; second separator in EITHER segment is rejected (segment must be
fs/key-safe: letters & digits in any language, plus `-`/`_` — see :data:`_SAFE_EXTRA` for why
`.` is not among them).

raises ConfigError: on empty, empty segment, or segment w. illegal character (ex: stray separator)

```def harness_of(node: str) -> str```
Return harness (part right of `℘`) of a *node*-name.

Bare node with no separator IS own harness (`"claude" -> "claude"`). Only canonical `℘` recognised;
callers pass node-names (always canonicalised).  `+` is deliberately NOT split here so literal `+`
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

Preserves persona name (left of `℘`) while swapping harness; used when actually-RESOLVED target
differs from requested harness (e.g., `NoAgentTarget` fallback when named agent's binary absent),
so the store dir + keyspace slot follow the real target.

* bare node (`"claude"`, harness `"claude"`) -> `"claude"` (unchanged);
* bare node, fallback harness (`"claude"`, `"no_agent"`) -> `"no_agent"`;
* persona node (`"navigator℘claude"`, `"claude"`) -> `"navigator℘claude"`;
* persona node, fallback (`"navigator℘claude"`, `"no_agent"`) -> `"navigator℘no_agent"` (persona
  name kept, harness swapped).

```def display_agent_ref(node: str) -> str```
Return the USER-FACING form of a *node*-name (`℘` -> `+`).

Presentation-only inverse of :func:`canonicalize_agent_ref`. **The KEYSPACE keeps canonical `℘`
form** — that is the one thing `℘` exists for. Everything a human can see or type takes the `+`:
error text, `box` listings, session labels, log lines, **the on-disk store dir**
(`settings.agent_config.store_dirname`) and **the `KANIBAKO_AGENT` stamp**, which an in-box agent
is told to read by the shipped ROM directive.

⚑ The stamp is the one that ROUND-TRIPS: emitted `+`, and canonicalised back to `℘` by every reader
before anything is derived from it (`stop`, `code`, the creds watcher, `start`'s reattach). That is
also what keeps a box stamped by an older version working — `canonicalize_agent_ref` accepts both.

Bare names contain no `℘` -> returned unchanged (existing output byte-identical).
Does NOT validate: it is pure display swap tolerant of any string.

```def canonicalize_agent_ref(raw: str) -> str```
Return the canonical *node*-name for a raw ref (`+` -> `℘`).

Idempotent: already-canonical or base node returns unchanged. Validates via
:func:`parse_agent_ref`, so malformed ref raises `ConfigError`.
