# `config set` Validation — the set-time value check, and the arm that was deleted

`settings_configset` is Block 5 of the KeyStore implementation: the value check that runs *before*
`config set` writes anything. It is one pure function, `validate_config_set`, returning a typed
`Verdict` — `OK`, or an `Error` carrying a human-readable reason. It touches no file, no
environment and no clock; its only reach into the cascade is an injected callback.

It invents no grammar and no type table of its own. Token parsing calls the resolver's OWN
`match_var` / `match_ref` inside the same escape-aware scan `settings_resolve.expand_expr` uses, and
typing reuses the `config_keys` key registry (`KEY_TYPES` / `_coerce_value`). That single-validator
property is the seam **S25**; hand-rolling either half here is exactly what let them drift before.

The FILE still stores the RAW, unresolved form. Resolution happens for the CHECK only — spec §0,
*"files store UNRESOLVED"*.

## Authority

* `~/vault/rw/keystore-design.md` §6d (`config set` + B4 + B5 — PRIMARY), §2 / §6a (files store
  UNRESOLVED).
* Spec `settings-keyspace-1.8.0.md` §2a (the config-set block: source-only, key-must-exist, value
  types), §0 (files store UNRESOLVED).
* Seams: `plans/keystore-blocks/SEAMS.md`.

## The B5 severity split

Design §6d, ratified by Jei 2026-06-27, as it stands with the category arm gone:

* **Hard ERROR, refuse to write** — do not poison the file. Three cases: malformed `$` / `@` token
  syntax; a type mismatch for a typed scalar key; a dangling reference (an `@`-ref to a
  non-existent config key, or an unknown `$VAR`) in the edited value's own post-edit chain.
* **No `@`-ref-repoint warning (B4).** Repointing a value's `@`-ref to a literal, or the reverse,
  is a normal explicit file edit at the command's own scope — identical to hand-editing the YAML —
  so it does not warn.

## Q9 — the dangling judgement is FULL RESOLUTION, not per-token existence

Spec §2a, ruling 2026-06-29. The dangling / unknown / cycle judgement is made by the injected E3
`resolves` probe, which answers ONE question: *does the edited value resolve cleanly post-edit?*
This REPLACED the retired conservative per-token existence check (`ref_exists` / `var_known`).

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

DS-BL1 = (a), Jei 2026-08-07g — *"accept the loss uniformly"*. Every bind-shaped category is
YAML-only: `config set` / `reset` refuse all six BY NAME in the verb preamble, and nothing routes a
category write. Rather than leave the category half of this module to rot, it was deleted in QA′
(2026-08-08, on Jei's word). There is no `is_category` discrimination any more. The three checks
that remain are the ones that were ALREADY unconditional — nothing was widened from a category
rule into a scalar one.

**Do not rebuild either half without a spec edit and a fresh ruling.** What went:

### `repoint_host_src` — the RAW category write-back (S24)

Deleted with `_bindings_arm_of`, `_refuse_stale_bind_shape` and `ConfigSetError`. Its last caller
was `config_interface._set_category_value`, deleted with the route.

⚑ **R-8's THREE-ELEMENT stale-shape refusal went with it, and R-8 was a RULING** — Jei 2026-08-06e
chose option A (docs only); the 2-element heuristic was option B and was DECLINED. It is recorded
here rather than merely dropped: if a category write route is ever rebuilt, R-8 must be rebuilt
with it.

⚑ **S24 is therefore NO LONGER REALIZED ANYWHERE.** Its whole surface was the CLI category repoint,
retired by DS-BL1 = (a). S25 is the only seam this module still realizes.

### `validate_config_set`'s `is_category=True` arm

That arm carried the `:` `src:dest` refusal, the bare-relative refusal with `_rooted_form_hint`,
and the not-yet-existent-host-path `Warn`. The live caller — `set_config_value`'s E3 set-time probe
— always passed `is_category=False`, so removing it was a zero-behaviour-change deletion.

⚑ **A COLON IS ORDINARY CONTENT ON THIS PATH.** The forbidden `:` `src:dest` notation was a
CATEGORY rule about the bind SHAPE — a structured pair spelled as a joined string. A scalar has no
such shape, and `endpoint = https://api.anthropic.com` is the obvious value that must pass. Do not
reintroduce a colon check here.

### `Warn` and the `HostExists` callback

`Warn` existed for exactly one case — a category `host_src` naming a host path that does not exist
yet — so deleting the category arm left the `Verdict` union with no producer for it.

⚑ **`Verdict` IS A TWO-WAY UNION NOW, AND THE MISSING THIRD MEMBER IS THE POINT.** A union member
no code path can produce is a shape a future consumer branches on for nothing, so it was deleted
rather than kept "in case". Restoring a warn severity means restoring a producer for it in the same
change.

## Out of scope — hard boundaries

No CLI wiring. No rewrite of `set_config_value`'s routing. Does not touch `cli.py` or the `config`
subcommands. No merge, expansion, views or consumer swap. No `@`-ref / `$VAR` / `~` resolution to
literals — files store UNRESOLVED.

## `Verdict`, `OK`, `Error`

`OK` is a singleton instance of the empty frozen dataclass `_OK` — it carries no data, so one
instance is enough; compare with `verdict is OK` or `isinstance(verdict, _OK)`. `Error` carries a
single `message` field, the human-readable reason, and means `config set` must REFUSE to write.

## `_scan_tokens` — the shared parse grammar

Scans a value for `@`-ref and `$VAR` token NAMES and returns `(ref_names, var_names)`, WITHOUT
resolving anything (design §6d: validate references for well-formedness, never expand to a
literal). A malformed `$` / `@` token raises `ValueError`, which the caller maps to an `Error`.

It mirrors `settings_resolve.expand_expr`'s scanner EXACTLY — the same escape rule, and BOTH token
families through the scanner's own parsers, called rather than re-derived:

* `\` consumes the next character as a literal, so `\@` and `\$` are not tokens.
* `$` → `match_var`, covering `$VAR` and `${VAR}`. Same deal as the `@` arm: the resolver's OWN
  parser, never a third copy of it. Both token families speak one grammar AND one error style;
  hand-rolling either arm here is exactly what let them drift.
* `@` → `match_ref`, covering both spellings: bare `@a.b` and braced `@{a.b}`. The braced form lets
  a literal suffix follow — `@{a.b}.jsonl` yields the ONE ref `a.b`, not the swallowed
  `a.b.jsonl`.

So "well-formed" here means EXACTLY what the build expander will later accept: one grammar, not a
second (S25). A DANGLING braced ref is judged exactly like a dangling bare one, because the
judgement is made downstream by the E3 probe, which sees only the ref NAME this function returns.

A leading `~` is the home token (environment; validated for existence elsewhere, or box-deferred).
It carries no name to check, so the scanner ignores it — but a caller must still treat a value
bearing one as a reference expression rather than a literal.

## The three checks, in order

*key* is the canonical config key; *value* is the user's RAW input.

1. **Token well-formedness.** A fast, pure pre-check for MALFORMED syntax — an unterminated `${`, a
   bare `$` — before any snapshot work. It also tells the function whether the value bears tokens
   at all, which step 3 needs. Dangling / unknown / cycle is deliberately NOT judged here.
2. **The E3 full-resolution check.** As above: a reason blocks, `None` continues.
3. **Typed scalar keys.** For a key in `KEY_TYPES`, reuse the key registry's coercion — the H2
   check. A value carrying any token (`@`, `$`, or a leading `~`) is a reference expression, not a
   literal scalar to type-check: its terminal type is only known after build, so it is skipped
   here (§0 again).

⚑ **How step 3 detects failure, and why it is not a prefix match.** This mirrors the live setter in
`set_config_value`: for a TYPED key — `KEY_TYPES.get(key)` truthy — `_coerce_value` returns a `str`
ONLY when coercion FAILED; success yields the typed Python value, e.g. a real `bool`. Since the
branch already gates on `key in KEY_TYPES`, any `str` coming back IS the H2 coercion-failure
signal, and the returned string is the message. No brittle prefix matching is needed or wanted.

---

## Relocation pass, 2026-08-20

Source went from 84.0% comment characters to 59.9% (`[R135]`, 60% bar). Everything
above was MOVED out of the module docstring, the `#:` attribute comments and the three inline step
comments; nothing was dropped as false.

What was cut as duplication rather than relocated, with its surviving carrier:

* The B5 severity split and the Q9 / E3 rule were each stated **twice** — once in the module
  docstring and again in `validate_config_set`'s docstring. One carrier each, here (P12).
* The E3 probe contract was stated **three** times: the `ResolveProbe` `#:` block, the
  `validate_config_set` docstring bullet, and the step-2 inline comment. One carrier, here.
* "files store UNRESOLVED" (§0) appeared five times in the source. It survives here, plus once in
  the module docstring.
* The `~/vault/rw/keystore-design.md` §6d citation and the `SEAMS.md` S24/S25 notes were in both
  the docstring body and the two trailing `Authority` / `Seams realized here` sections.

Kept in source under the keep test, because deleting each lets a future edit break something at
that exact line:

* the ⚑ scalar-only ruling marker on `validate_config_set`, plus the "no colon check" warning —
  both are things a reader would otherwise re-add;
* the ⚑ on `Verdict` about the missing third union member;
* the ⚑ at the step-3 `isinstance(coerced, str)` test, which reads like a type nicety and is
  actually the coercion-failure protocol.
