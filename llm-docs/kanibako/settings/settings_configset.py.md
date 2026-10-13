# `config set` Validation — the set-time value check

`settings_configset` holds the value check that runs *before* `config set` writes anything:
`validate_config_set`, returning a typed `Verdict` — `OK`, or an `Error` carrying a human-readable
reason — plus the two helpers it and other set-time readers share, `scan_tokens` and
`holds_verbatim_text`. It touches no file, no environment, and no clock; its only reach into the
cascade is an injected callback.

It invents no grammar and no type table of its own. Token parsing reads the resolver's OWN
tokenizer, `settings_resolve.iter_expr_tokens` — the one `expand_expr` reads — and typing reuses the `config_keys` key registry (`KEY_TYPES` /
`_coerce_value`). That single-validator property is the seam **S25**; hand-rolling either half here
lets the two grammars drift.

The FILE stores the RAW, unresolved form. Resolution happens for the CHECK only — spec §0, *"files
store UNRESOLVED"*.

## Authority

* Spec `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §2a "VALIDATION at set-time" (the
  config-set block: source-only, key-must-exist, value types), §0 (files store UNRESOLVED).
* Historical (NOT AUTHORITY): `~/canon/notebook/archives/resources/keystore-2026-06/keystore-design.md`
  §6d (the no-repoint-warning ruling, which no live clause restates).
* Seams: `~/canon/workbook/plans/keystore-blocks/SEAMS.md`.

## The severity split

* **Hard ERROR, refuse to write** — do not poison the file. Three cases: malformed `$` / `@` token
  syntax; a type mismatch for a typed scalar key; a dangling reference (an `@`-ref to a
  non-existent config key, or an unknown `$VAR`) in the edited value's own post-edit chain.
* **No `@`-ref-repoint warning.** Repointing a value's `@`-ref to a literal, or the reverse, is a
  normal explicit file edit at the command's own scope — identical to hand-editing the YAML — so it
  does not warn.

## The dangling judgment is FULL RESOLUTION, not per-token existence

The dangling / unknown / cycle judgment is made by the injected E3 `resolves` probe, which answers
ONE question: *does the edited value resolve cleanly post-edit?*

The caller builds the full lenient COMMAND-target snapshot (assemble → merge → expand [lenient]),
applies the candidate raw value at *key*, and lenient-`expand`s it:

* `None` — it resolves cleanly. **ALLOW.**
* a reason string — the edited value's own transitive UPSTREAM chain stays unresolvable (a dangling
  `@`-ref, an unknown `$VAR`, or a cycle the edit does NOT fix). **BLOCK**, naming the broken
  upstream dependency.

An UNRELATED or DOWNSTREAM defect, or one the edit re-points away from or fixes, leaves the edited
key clean and therefore ALLOWS. That asymmetry is the point of the rule: it keeps `config set`
usable to REPAIR a broken config. The probe never resolves a stored literal (§0).

The live wiring is `config_interface.set_config_value`, which gates on `_probes_at_set_time(key)`
and builds the real snapshot via `_category_set_lookups`; tests pass simple stubs. From this
module's perspective the callback is pure.

## ⚑⚑⚑ SCALAR-ONLY, AND THAT IS A RULING

Every bind-shaped category is YAML-only: `config set` / `reset` refuse each of them BY NAME in the
verb preamble, and nothing routes a category write. This module has no category arm and no
`is_category` discrimination. **Do not add one without a spec edit and a fresh ruling.** If a
category write route is ever built, the three-element stale-shape refusal (a ruling: docs only, no
two-element heuristic) must be built with it. Seam S24 (the CLI category repoint) is realized
nowhere; S25 is the only seam this module realizes.

⚑ **A COLON IS ORDINARY CONTENT ON THIS PATH.** The forbidden `:` `src:dest` notation is a CATEGORY
rule about the bind SHAPE — a structured pair spelled as a joined string. A scalar has no such
shape, and `endpoint = https://api.anthropic.com` is the obvious value that must pass. Do not add a
colon check here.

## Out of scope — hard boundaries

No CLI wiring. No rewrite of `set_config_value`'s routing. Does not touch `cli.py` or the `config`
subcommands. No merge, expansion, views or consumer swap. No `@`-ref / `$VAR` / `~` resolution to
literals — files store UNRESOLVED.

## `Verdict`, `OK`, `Error`

`Verdict` is the two-way union `_OK | Error`. `OK` is a singleton instance of the empty frozen
dataclass `_OK` — it carries no data, so one instance is enough; compare with `verdict is OK` or
`isinstance(verdict, _OK)`. `Error` carries a single `message` field, the human-readable reason, and
means `config set` must REFUSE to write.

⚑ **There is no warn severity.** A union member no code path can produce is a shape a future
consumer branches on for nothing; adding a warn severity means adding a producer for it in the same
change.

## `scan_tokens` — the shared parse grammar

Scans a value for `@`-ref and `$VAR` token NAMES and returns `(ref_names, var_names)`, WITHOUT
resolving anything (spec §2a "VALIDATION at set-time": resolution is for the check only and the
file keeps the RAW form; resolving is the E3 probe's job, not this scanner's). Every refusal of
the tokenizer — a malformed `$` / `@` token, an unpaired `{` or `}`, an empty or malformed `{…}`, an
illegal escape — is re-raised as `ValueError` with the resolver's own text, which the caller maps to
an `Error` naming the key (`'<key>': malformed value …`).

It collects the `ref` and `var` tokens of `settings_resolve.iter_expr_tokens`, the tokenizer
`expand_expr` reads, so the two cannot disagree about a token or a refusal:

* an escape (`\\`, `\~`, and while they are sigils `\@`, `\$`) is no token;
* `$VAR` / `${VAR}` and `{$X}` are variables;
* `@a.b`, `@{a.b}` and `{a.b}` are refs. A braced form lets a literal suffix follow —
  `{a.b}.jsonl` yields the ONE ref `a.b`, not the swallowed `a.b.jsonl`;
* `{{` / `}}` are literal braces.

So "well-formed" here means EXACTLY what the build expander will later accept: one grammar, not a
second (S25). A DANGLING braced ref is judged exactly like a dangling bare one, because the
judgment is made downstream by the E3 probe, which sees only the ref NAME this function returns.

A leading `~` is the home token. It carries no name, so the scanner ignores it, and a value whose
only token is a `~` is type-checked in step 3 like a literal.

Other readers: `config` and `config_interface` call `scan_tokens` for their own set-time checks.

## `holds_verbatim_text(key)`

Whether the value at the CLI key *key* is TEXT (`settings_resolve.is_verbatim_text`) — never an
expression. It is judged at the slot the value is WRITTEN to (`config_dest._key_slot`), so the bare
`endpoint` is `agent.default.endpoint`; a `pref.<target>` request is judged at its target.

## `validate_config_set(key, value, *, resolves)` — the checks, in order

*key* is the canonical config key; *value* is the user's RAW input.

0. **Text keys.** For a key `holds_verbatim_text` answers yes, steps 1 and 2 are skipped: a text
   value has no tokens and no upstream chain, and the scan would refuse
   `https://user:key@host/v1` for a ref `@host` it does not contain.
1. **Token well-formedness.** A fast, pure pre-check for MALFORMED syntax — an unterminated `${`, a
   bare `$` — before any snapshot work. It also tells the function whether the value bears tokens
   at all, which step 3 needs. Dangling / unknown / cycle is deliberately NOT judged here.
2. **The E3 full-resolution check.** As above: a reason blocks, `None` continues.
3. **Typed scalar keys.** For a key in `KEY_TYPES`, reuse the key registry's coercion — the H2
   check. A value carrying any `@` or `$` token is a reference expression, not a literal scalar to
   type-check: its terminal type is only known after build, so it is skipped here (§0 again).

⚑ **How step 3 detects failure, and why it is neither a prefix match nor a type sniff.** This
mirrors the live setter in `set_config_value`: `_coerce_value` returns a `CoercionError` and nothing
else on failure, so the check is `isinstance(coerced, CoercionError)` and the carried `.message` is
what the user reads. A `str` result is not a failure: `KEY_TYPES` carries `path` (`[R147]`), and a
path stays a raw string.
