# `commands/flags.py` — the blanket flags, the shared `--null` flag, option-anywhere parsing

`flags` is the cross-cutting CLI flag plumbing: three things that are all argparse plumbing no
single command owns.

1. the blanket `--agent` / `--box` flags and their per-command relevance (W1 Phase D), described
   below;
2. `NULL_FLAG_HELP_TEMPLATE` / `add_null_flag` — the ONE spelling of the `--null` suppression flag
   every scope's `set` verb wires (B-6);
3. `OptionsAnywhereParser` / `hoist_optionals` — the parser class that lets a flag be written in
   ANY position, including between two positionals (B-5).

## The two cross-cutting flags

Two flags are added to (nearly) every subcommand via a post-construction walk of the argparse tree:

* `--agent NAME` — the top-precedence, ephemeral (this-invocation-only) input to the unified agent
  resolver (`config.resolve_agent` cascade). It feeds the C2 resolver seam
  (`getattr(args, "agent", None)`) so a command resolves as if NAME were the configured agent.
  Relevant only to agent-touching commands.
* `--box VALUE` — the SUBJECT selector: which box/project the command acts on (path OR registered
  box name; name precedence), even when not cwd. Routed through
  `kanibako.settings.paths.resolve_box_target`. Relevant only to commands that act on a subject
  box/project.

⚑ **Both are declared `default=None`, and that is the load-bearing part.** An un-given flag installs
NOTHING — it does not install a default agent and it does not install a default subject. The CLI is
its own cascade level, above `box`, and it supplies a value only when the user actually typed one.
So `None` means ABSENT, while every `str` — including `""` — means SET. Collapsing those two is the
bug this distinction exists to prevent.

## Parse everywhere, relevance per-command

The flags PARSE on every command (so `kanibako <cmd> --agent X --box Y` never argparse-errors on an
unknown flag), but each is only MEANINGFUL for a declared set of commands. Passing a flag to an
UNRELATED command is a hard error (not a silent no-op), surfaced by `check_flag_relevance` in the
dispatcher.

Relevance is keyed by the command's dotted path (e.g. `"start"`, `"agent reauth"`, `"box convert"`).

## The relevance sets, and who is deliberately outside them

`AGENT_FLAG_COMMANDS` is the ephemeral agent-resolver override's set: the commands that run the
unified cascade with the explicit-agent seam. That is the launch path (`start` plus its box alias),
`agent reauth`, and create (top-level plus its box alias) —
`run_create` threads the explicit agent to the persona verdict and the home seed, and a persona ref
whose persona-grata store entry exists drives the initial store import.

Two absences from that set are deliberate:

* `shell` is NOT here — it bypasses agent resolution entirely (the no-agent recovery hatch).
* `setup` is NOT here — it has its own persistent `--agent`. It is additionally listed in
  `_AGENT_FLAG_EXCLUDE`: commands that already own a local `--agent` flag with different semantics
  and must be skipped by the blanket injection. `setup` is intentionally EXCLUDED from the blanket
  `--agent` injection because its flag has PERSISTENT semantics (it writes `system.agent`), distinct
  from the blanket flag's ephemeral override — so the two never collide (see the Phase B / Phase D
  reconcile).

`BOX_FLAG_COMMANDS` is the subject/anchor selector's set: every command that acts on a subject
box/project rather than on cwd — the launch/shell paths, `code`, the box lifecycle and inspection
commands, stop, `agent reauth`, and `workset disconnect`. It is NOT relevant to commands with no
single-box subject (list/ps/create, the workset/agent/system/rig groups, setup).

Membership is a CLAIM ABOUT HANDLERS, and the claim is checked: every leaf whose handler reads
`args.box` / `args.agent` must be declared for that flag, asserted over the whole parser tree
(`TestATableEntryAgreesWithWhatTheHandlerReads`). `code` was the case that motivated it — `run_code`
reconciled its `project` positional with `--box` through `resolve_subject_value` while the table
refused the flag, so `kanibako code --box mybox` exited 2. It is reachable by neither the alias rule
nor the shortcut-twin rule, because there is no `box code`.

That converse — declared ⇒ a handler reads it — is deliberately NOT asserted: a command may
legitimately be declared for a flag its own handler does not read, and an over-declaration costs a
user nothing but a flag accepted and ignored.

A different, exact converse IS asserted: **every declared key must NAME A REACHABLE COMMAND** — a
canonical dotted path `command_key` can actually report (`TestEveryDeclaredKeyNamesAReachableCommand`).
The sets are not private tables; `check_flag_relevance` joins each one verbatim into the refusal a
user reads, so a key no parse can produce is a command the message tells them to try and that then
fails. `"reauth"` was that case: declared in BOTH sets with no top-level `reauth` parser behind it —
`kanibako reauth` parses as `start reauth`, consuming the word as a box name, so `kanibako reauth
--help` printed `start`'s usage and `kanibako reauth` died with "no box at reauth". It changed no
behaviour, because `command_key` could never report it; it only ever reached the user, in the
enumeration. The real command was carried throughout by the separate `"agent reauth"` entry, so
dropping the bare key left `agent reauth --box` / `--agent` untouched. Whether to wire a top-level
`reauth` shortcut remains a CLI-shape decision, and an open one — the key is gone, not the question.

The oracle is the canonical LEAF keys. A GROUP parser is absent from it by construction and
correctly so: `_walk` injects the blanket flags on leaves only, so a group with a
`set_defaults(func=...)` fallback (`kanibako box` → list) carries no `--box` action, `args.box` never
exists, and there is no refusal for a declaration to disagree with. If a group ever gains the flags,
the oracle must widen with them.

## The shared `--null` suppression flag (B-6)

`NULL_FLAG_HELP_TEMPLATE` is ONE help string for every scope's `set` verb (box / workset / system /
agent). It has to TEACH the feature, because suppression has no verb of its own: Jei rejected a
dedicated `suppress` command as "unnecessary and redundant" (2026-07-31), which makes this help text
the whole discoverability surface. So it says both halves — what `--null` DOES (it suppresses the
value the key would otherwise inherit, writing an explicit null (present-None) at this scope, so the
scopes above it stop supplying the key and the consumer sees it as dropped, spec section 2h) and how
to UNDO it (the sibling `reset` verb, since this WRITES an override rather than removing one).

⚑ **`reset`, not `--reset`.** There is no `--reset` FLAG on any parser. `reset` is a sibling
SUBCOMMAND (`box reset <key>`, `system reset <key>` …); `args.reset` is only the internal namespace
attribute the shared engine reads. Naming a flag the user cannot type is the failure this text
avoids.

⚑ **The undo example is PER-VERB, not shared.** Each scope's `reset` takes a different positional
shape (`workset reset <workset> <key>` needs its noun, `system reset <key>` takes none). One literal
example would be untypeable for three of the four verbs, and an example the user cannot type teaches
the opposite of what it is for.

That is why `add_null_flag` takes *undo* as its keyword-only, required argument: *undo* is the
verb's OWN `reset` spelling, so a new scope's `set` cannot inherit another scope's untypeable
example by accident. The function is the single source for the sentence, so the four `set` verbs
cannot drift apart — they were four copies of one string before.

## Option-anywhere parsing (B-5)

### Why a flag written between two positionals strands what follows it

argparse matches positionals in GROUPS split by the optionals between them, and a flag written
BETWEEN two positionals strands what follows it. There are TWO ways that bites, and the second is
version-dependent:

1. a VARIADIC positional (`nargs="*"`) swallows its whole group, leaving nothing for the tail —
   every Python version:

   ```
   box set mybox --null pref.system.agent
   → error: unrecognized arguments: pref.system.agent
   ```

2. ⚑ **Python < 3.13 only.** `_match_arguments_partial` matched a ZERO-WIDTH trailing positional
   into the FIRST group and then dropped it from the pending list, so the tail had no positional
   left to bind:

   ```
   workset set myws --null model        # 3.11: unrecognized: model
   ```

   For `[workset (nargs=None), key_value (nargs="?")]` against the pattern `'AOA'`, 3.11 returns
   `[1, 0]` (both actions consumed, `key_value` matched empty) where 3.13 returns `[1]`. CPython
   3.13 added the trailing-zero strip — *"if the next pattern char is an optional, defer the empty
   positionals"* — which is exactly this repair, upstream. We support `>=3.11` (`requires-python`;
   CI pins 3.11), so the rewrite must not lean on it: 3.13 masked the defect locally while CI
   reddened.

### What the rewrite does

`hoist_optionals` moves the optionals (each with its value) to the FRONT, preserving their relative
order, and leaves the positionals in theirs. Nothing is dropped and nothing changes meaning —
argparse binds an optional by NAME, never by position — so the only observable difference is that
the previously-fatal orderings now parse, identically on every supported version.

A `--` ends the rewrite: everything from it on is passed through verbatim, which is how a user
writes a positional that looks like a flag.

### When the rewrite is INERT

The rewrite is inert unless the parser actually has the shape that breaks:

* a `REMAINDER` or subparsers positional → argv returned untouched, because those deliberately
  capture it verbatim and reordering would corrupt the very thing they exist to preserve. This is
  checked FIRST: a raw-argv positional vetoes the rewrite outright, whatever else the parser's shape
  looks like.
* positionals that cannot be split (`_splits_positionals`) → argv returned untouched, since no group
  boundary can fall between them.

`_splits_positionals` answers whether an interleaved optional could SPLIT this parser's positionals.
Because argparse matches positionals in groups divided by the optionals between them, a group
boundary can only fall BETWEEN two positional slots — which needs either two or more positional
actions, or a single action that consumes more than one token (`nargs` `"*"` / `"+"` / an int > 1).
A parser with one `None` / `"?"` / `1` positional can never be split and is left strictly alone.

### Option arity

`_option_take` answers how many EXTRA argv tokens a token consumes, or `None` if it is a positional.
`0` is a toggle (`store_true` and friends) or the self-contained `--opt=value` form, which carries
its own value; `1` is a single-value option (`--box NAME`); `_BAIL` is any other arity, which makes
the caller leave argv alone. `_BAIL` itself is the sentinel for "an option whose arity this rewriter
will not reason about", and `_take_for` is the mapping from an action's `nargs` to that answer.

Abbreviations are resolved too — an unambiguous long-option ABBREVIATION, matching argparse's own
`allow_abbrev` behaviour. Without this, `--nul` would parse before the positionals and not after
them: one flag with two behaviours, which is worse than either.

### The bail on a value-taking option with nothing to take

A value-taking option can sit at the END of argv, or have `--` in its value slot. Hoisting the bare
flag would move it in front of the positionals, where the FIRST positional becomes adjacent to it
and is silently bound as its value (`box show mybox --box` → `--box mybox`, exit 0, wrong subject).
The rewrite bails instead, so argparse raises the error it owns: "expected one argument".

### `OptionsAnywhereParser` — where it is installed

The class is installed as the `parser_class` of the TOP-LEVEL subparsers action, so every subcommand
inherits it — and every NESTED subcommand too, since `add_subparsers` defaults `parser_class` to the
type of the parser it is called on. One rule for the whole CLI ("a flag may go anywhere") beats
per-verb patching, and `hoist_optionals` is inert for the parsers that never had the problem.

⚑ The hook is `parse_known_args` rather than `parse_args` because that is the entry point
`_SubParsersAction.__call__` uses for a subcommand.

## The injection walk

`inject_blanket_flags` walks the whole argparse tree and adds `--agent`/`--box` to the leaves. A
*leaf* is any (sub)parser that does NOT itself hold further subparsers. Group parsers that only
dispatch to subcommands (`box`, `agent`, `workset`, `rig`, `system`, `baseline`, `workset share`)
are not runnable on their own, so they get nothing.

Flags are added unconditionally to every leaf so `--agent`/`--box` PARSE everywhere (relevance is
checked post-parse). Two exceptions:

* `setup` (and any command in `_AGENT_FLAG_EXCLUDE`) keeps its own `--agent`; only the blanket
  `--box` is added.
* a parser that already defines an option string is left alone for that option, so injection never
  collides with a flag a leaf declares for itself. That test is `_has_option`. It is what lets
  `setup` own the `--agent` spelling; it is NOT a way for a leaf to opt out of the blanket flag,
  because a leaf that declares the flag itself also takes on answering for it.

⚑ A parser can BOTH have subparsers AND be runnable via `set_defaults(func=...)` as a fallback (e.g.
`box` defaults to list); those fallbacks take no subject, so not injecting on the group is correct.

### Parsing is unconditional; ADVERTISING is not

Adding the flag and advertising it are two decisions, and `_walk` makes them separately. Every leaf
gets the flag; a leaf whose `cmd_key` is outside the flag's declared set gets it with
`help=argparse.SUPPRESS`, which `_add_agent_flag` / `_add_box_flag` take as their `advertise=False`
branch.

The split exists because the two properties want opposite answers. Parsing must be universal, or a
command outside the declared set would fail with argparse's bare *"unrecognized arguments"* instead
of `check_flag_relevance`'s message, which names the commands the flag DOES apply to — the useful
error is only reachable if the flag parses first. Advertising must NOT be universal, because a
`--help` that lists a flag the command answers with exit 2 is help that promises a refusal. Before
this split, 95 command keys offered `--agent` and 82 offered `--box` that way.

⚑ Aliases share ONE parser object (`add_parser(aliases=[...])`), so the advertisement is a property
of the parser, not of the key: `box rm` and `box delete` cannot differ. `command_key`, by contrast,
reports the alias the user actually TYPED — so `box mv --box X` is refused where `box move --box X`
is accepted, and the three alias keys (`box delete`, `box inspect`, `box mv`) still advertise
`--box`. That mismatch is about the relevance SETS, not about the injector.

The property is pinned by a derived test
(`tests/test_commands/test_flags.py::TestBlanketFlagsAreAdvertisedOnlyWhereTheyApply`): it walks the
whole tree and asserts advertisement against the declared sets rather than a hand-written list, so a
new command is born suppressed unless it joins a set.

`command_key` computes the dotted command path for a parsed namespace, mirroring the keys in
`AGENT_FLAG_COMMANDS` / `BOX_FLAG_COMMANDS`. It walks the known nested-subparser dest chain so a
command and its subcommand are joined by a single space. The dests are listed in walk order; only
one level is needed for the relevance sets above, but the full chain is included for completeness.

## Subject reconciliation

`resolve_subject_value` reconciles a command's positional subject with its `--box` flag. The general
rule wherever a positional and `--box` coexist (§Design 8):

* only one supplied → use it;
* both supplied, SAME string → warn + continue (return the value);
* both supplied, DIFFERENT strings → `SubjectConflictError`.

It returns the effective subject string, or `None` for cwd. Equality is a plain string compare —
callers resolve the winner through their own path-or-name resolver, so two spellings of the same box
(a name and its path) are treated as DIFFERENT here and rejected, which is the safe choice: the user
gave conflicting selectors.

## The relevance check

`check_flag_relevance` errors if `--agent`/`--box` is set for a command it is irrelevant to. A flag
PARSES on every command, but is only MEANINGFUL for its declared set. Setting it elsewhere is a user
error (not a silent no-op), so it raises an actionable `FlagRelevanceError` — the exception raised
when `--agent`/`--box` is passed to an unrelated command — which the dispatcher turns into a
non-zero exit.

For `--agent` the check runs only when the blanket flag is the one in play. setup's local `--agent`
lives on a command (`setup`) that is not in `AGENT_FLAG_COMMANDS`, but it is legitimate there — so
the check is skipped for the excluded commands entirely, and setup's own `--agent` is never seen
here (it is excluded from the blanket injection and routed through setup's own handling). Only a
real string value counts as "set": a non-str sentinel from a MagicMock test stub is ignored, and an
un-given flag is `None` rather than a value.

`shell` (and `box shell`) is a shell — it never launches an agent, so `--agent` is meaningless
rather than wrong. Product decision: IGNORE it with a clear note (don't hard-error), then proceed to
open a plain shell. `run_shell` never reads `args.agent`, so the value is dropped here.

---

## Notes from the prose pass (2026-08-20)

**One drifted reference corrected, not relocated.** The pre-pass module docstring named a
`:data:` role pointing at `NULL_FLAG_HELP`. No such symbol exists in the module; the constant is
`NULL_FLAG_HELP_TEMPLATE` (it is a `.format()` template, which is the whole point of the *undo*
argument). The surviving docstring names the real symbol.

**What stayed in the `.py`.** The `⚑` warnings that name a specific bug or invariant at the line
they protect: the `default=None` absent-vs-set distinction; `reset` not `--reset`; the per-verb undo
example; the CPython-3.13 non-reliance on `hoist_optionals`; the REMAINDER veto being checked first;
the abbreviation arm; the bail that would otherwise bind a positional as a flag's value; the
`parse_known_args` hook; the subparsers-plus-`set_defaults` fallback; the plain-string-compare
equality; and the shell ignore. Each is the warning plus a pointer here for the reasoning.
