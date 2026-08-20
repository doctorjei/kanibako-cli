# Agent Selection — which agent a box runs, and why the answer is installed

`agent_select` is the ONE seam every command uses to answer *"which agent is this box's?"*. It
resolves the settings side of the question with a narrow pre-pass, applies `--agent` on top, and
hands the winner to `kanibako.settings.config.resolve_agent`, which owns everything that is NOT a
key: name validation against the installed set, persona-ref canonicalisation, and the
installed-count rule.

The module is deliberately thin. It contains one dataclass, one context builder, and one resolve
function; the interesting content is the set of rules below, all of which are spec rules
(`specs/settings-keyspace-1.8.0.md` §1A / §2b / §2g / §2h) rather than local choices.

## The selection order, and where each tier is settled

Selection order (spec §2h, least to most specific):

```
system.agent  <  workset pref  <  box pref  <  --agent (the §1A CLI level)
```

The first three are settled INSIDE the settings cascade. `system.agent` is the stored default, and
a `pref.system.agent` request from the workset or box file overrides it, box beating workset by
assignment order. This module resolves that much with a NARROW pre-pass,
`kanibako.settings.settings_launch.resolve_selected_agent`, and then applies `--agent` on top.

`SELECTION_KEY` is the key naming the agent a box runs (spec §2g), re-exported from here so callers
spell it once.

## P7 (v1.8.0) — what changed and why

The box tier used to be the flat scalar key `box.agent_name`, read by a private loader
(`config.load_merged_config` → `KanibakoConfig.box_agent_name`) that had nothing to do with the
keyspace. Spec §2b RETIRED it: it made the L4.1 anchors `meta.box.auth.workset_path` and
`meta.box.agent.auth.share_support` derive from a SETTABLE key at their own level, which §0's
bootstrap rule forbids.

The replacement is the §2h REQUEST `pref.system.agent`, and the system default is now the
spec-named `system.agent` (§2g) living in the `system:` table of the system settings file.

## The selection LEVEL, and why the resolved answer is installed unconditionally

Whatever wins, `select_agent` reports it and the launch installs it at `system.agent` as the §1A
top-most level (`build_launch_snapshot(cli_level=…)`). That is not tidiness — three separate
readers now dereference `@system.agent` (both re-pointed §2c anchors today, the handbook's agent
chapter tomorrow), so the snapshot MUST agree with the process that runs.

Without the install there are three ways to disagree:

* `--agent claude` with `pref.system.agent: goose` — the snapshot says goose while claude runs;
* the INSTALLED-COUNT rule — the commonest host has one agent installed and nothing stored, so the
  box runs claude while `system.agent` is ABSENT and every dereference of it resolves to `""`;
* a persona ref stored as `nav+claude` — the running node is the canonical `nav℘claude`.

Suppressing the pref when `--agent` is given (the alternative considered) fixes none of these: it
only changes WHICH wrong value the snapshot reports. One rule — *the resolved selection is
installed at the top* — covers all three, is a no-op when the cascade already said it, and is
exactly the mechanism P8 generalised to every key-shadowing flag.

## P8 (v1.8.0) landed that generalisation

The level is now built by `kanibako.settings.settings_cli_level.build_cli_level`, which owns the
flag→key table and carries this selection alongside the launch's ephemeral flag values, and it is
validated by `guard_cli_level` inside `build_launch_snapshot`.

`AgentSelection.selection_level` keeps its name because it really is only the selection — it is
this module's INPUT to that builder.

Which resolves see the flags is a separate rule, stated at `build_launch_snapshot`: the selection
rides EVERY resolve, the flags ride only the launch resolve, and no resolve whose output is written
to disk sees a flag.

## Why the selection key is excluded from §2h's LOCATOR CLOSURE

⚑ `system.agent` DOES select a cascade-input file (`meta.agent.<agent>.settings`), which normally
puts a key in §2h's locator closure. It is deliberately excluded there, and the same argument covers
the CLI level (§1A's locator caveat): an AGENT file may not carry prefs, so a re-selected agent file
cannot introduce new requests, and the selection level is an initial-value overlay that triggers no
re-read. See `settings_prefs.LOCATOR_CLOSURE`.

## `AgentSelection` — the two field vocabularies

*node* is the canonical agent NODE-name (`persona℘harness`; a bare agent is byte-identical to its
harness), or `""` for a **NO-AGENT plain-shell box** (spec §2b, D-M6).

*source* answers *"why did system.agent resolve to that?"* without reading files:

| `source` | meaning |
|---|---|
| `"cli"` | `--agent` |
| `"settings"` | the stored key, or a pref |
| `"autopick"` | the installed-count rule |
| `"suppressed"` | `pref.system.agent: null` |

## `has_agent` — `""` means OPPOSITE things on the two sides of the target seam

Does this box run an agent AT ALL? (spec §2b D-M6.)

⚑⚑ **USE THIS — never `bool(selection.node)` at a call site, and NEVER pass an empty node on to
`resolve_target`.** Two vocabularies meet at that seam:

* to SELECTION, `node == ""` means *this box runs NO agent* (a `pref.system.agent: null`
  suppression);
* to `kanibako.targets.resolve_target`, an empty/absent name means *no name was given —
  AUTO-DETECT one*, which is its documented contract for other callers and is not a bug there.

Handing the first to the second LAUNDERS a deliberate suppression into auto-detection: bifrost
measured a suppressed box launching claude, with claude's binary, commons and CREDENTIALS
delivered. Route every selection→target conversion through the idiom below. ⚑ **There is no helper
that does it for you** — the `has_agent` guard IS the translation, spelled at each seam, so look for
the guard and not for a function.

**The idiom, at every seam that needs a target:**

```python
target = resolve_target(harness_of(sel.node), path) if sel.has_agent else None
```

`None` is the shipped PLAIN-SHELL shape — exactly what `kanibako shell` produces — and every
downstream gate already keys on `target is None` (no agent binds, no agent config, no credential
delivery, no `KANIBAKO_AGENT` stamp, `agent_id` = `"general"`).

⚑ Deliberately NOT `NoAgentTarget()`: that is a RESOLVED target, so it would earn a
`KANIBAKO_AGENT` stamp — and the stamp is what drives the stop / creds-watch writeback, which would
then run a credential lifecycle against a box that has none. `NoAgentTarget` stays the right answer
for `resolve_target`'s own auto-detect-found-nothing case; it is not the right answer for "the user
asked for NO agent".

The property is keyed on *source* (not on the string) so the distinction survives even if a future
source ever produced an empty node for a different reason; the `node` test is the belt-and-braces
half.

## `selection_level` — a NO-AGENT box installs NOTHING

The §1A top-most level to install, or `None` for a no-agent box.

`system.agent` must stay absent/`None` so the box skips the agent binds, credentials and the
layer-2 template. In particular it must NOT be pinned to the `"general"` slot the launch uses as
the agent-scope discriminator for a shell box — that is a template fallback, not an agent.

## `launch_resolve_ctx` — the ONE ctx builder, and the resolver split

Builds the host-side `kanibako.settings.settings_resolve.ResolveCtx`. It is the one ctx builder for
every snapshot resolve — `start.py`'s `_launch_snapshot_inputs` calls this too, so the selection
pre-pass and the launch snapshot cannot drift in what `@config.*` / `$XDG_*` / `~` mean.

**Resolver SPLIT (spec §1A / JC-2):** the Layer-1 `config.*` foundation goes into `ctx.config` (so
`@config.*` category refs route THERE, not the snapshot); the Layer-2 `system.*` path settings stay
folded into the snapshot floor so `@system.*` resolves from it.

The xdg map is the canonical FULL host map — a data-home-only partial map raised on stored
`$XDG_CACHE_HOME/...` values — anchored on the resolved `std.data_home`.

*agent_name* is `None` for the SELECTION pass: no agent is known yet, so a `$AGENT` anywhere
resolves to a refusal rather than to a silent `""`. That refusal is recorded (never raised) because
selection expands LENIENTLY; see `kanibako.settings.settings_launch.resolve_selected_agent`.

## `select_agent` — what it raises, and who is allowed to swallow it

It raises the typed `kanibako.errors.AgentResolutionError` subclasses that `config.resolve_agent`
raises (Gate-2a / Gate-2b / not-installed), a `kanibako.settings.settings_resolve.SettingsError`
when the selection key itself does not resolve, and the retired-key refusal when a settings file
still carries `box.agent_name` / `system.default_agent` (migration M-4).

Informational callers that must degrade rather than fail (`box info`, the `config --effective`
display) keep their existing `try/except` — that is what makes the launch loud and the read verbs
quiet, deliberately.

### The retired-key sweep, tier by tier

RETIRED spellings are refused by name BEFORE anything is resolved (P7 / M-4). The check sits here
rather than inside `assemble_levels` because a raise there would also break `config set` — the very
command the message prescribes.

⚑ EVERY tier the cascade reads is checked, BASE included (M-4's sweep names
`/etc/kanibako/settings_base.yaml`): a site admin's stale `box.agent_name` would default DOWN into
every box on the machine, which is the worst version of the silent-wrong-agent failure, not an
exempt one.

The cure is LEVEL-APPROPRIATE — a pref is not writable at base/system, so those levels are told to
REMOVE the key rather than to run a `box set` that cannot fix them.

⚑ The BOX-level cure names the box explicitly (Jei: the bare form only works from a resolvable cwd,
and is required otherwise) — `proj.name` is threaded only for `level == "box"`. A NAMELESS box
(`proj.name` falsy — spec: the short-hash fallback `settings/paths.py` uses for display is NOT an
addressable `box set` positional) degrades to today's bare cure line rather than emit an argument
that would not resolve.

### PRESENT-`None` is not `__MISSING__`

Only the cascade can suppress or supply, so `--agent` short-circuits the pre-pass entirely and a
launch that names its agent pays for no extra resolve.

When the pre-pass returns PRESENT-`None`, that is an explicit `pref.system.agent: null`
SUPPRESSION ⇒ the NO-AGENT plain-shell box (D-M6). ⚑ This arm is the capability the retired
`box.agent_name` could NOT express (a stored system default always re-supplied an agent), so it
must be kept distinct from `__MISSING__` — never collapse them with a falsiness test.

---

## Completeness sweep

This document was created by the llm-docs 60% pass (`[R135]`). Everything above was MOVED out of
`src/kanibako/settings/agent_select.py`; nothing was dropped as false, and nothing was dropped as
duplication.

What stayed in the source, under the keep test:

* the `⚑⚑` on `has_agent` — the warning plus the idiom line, because the launder-a-suppression bug
  happens at the call site and a pointer alone would not stop it;
* the `⚑` at the PRESENT-`None` arm — collapsing it with `__MISSING__` looks like a simplification
  and silently deletes the no-agent capability;
* the `⚑` on the retired-key loop — it reads as over-broad until you know the base tier is the
  worst case;
* the `⚑` on `launch_resolve_ctx`'s `agent_name=None` — the argument looks optional;
* a one-line docstring on every symbol.
