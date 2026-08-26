# The Persona-Grata Store — discovery, pointer resolve, and the LIVE launch bundle

`persona_store` is kanibako's reader for the persona-grata STANDARD: a fixed, external directory
tree that holds harness-NATIVE config per persona and per harness. kanibako does not own the
standard and does not write into it — it CONSUMES it. Everything in this module is a pure read
against that tree, with no box, no network, and no probe.

The design SOT is `designs/persona-grata-autoimport-DESIGN.md` (under `~/canon/workbook/`); the
sections cited below (§1 through §5a) are its sections. ⚑ The module docstring cited it as
`plans/…-DESIGN.md` until 2026-08-20 — a stale pointer: `plans/` holds
`persona-grata-autoimport-BUILD.md`, a DIFFERENT document. The procedure that governs any change here is
`~/canon/notebook/procedures/persona-resolution-model.md`, and it outranks this document.

## The one invariant: persona values are LIVE, never persisted

A `<pid>+<hid>` agent ref whose store entry exists is a *persona agent*. Its endpoint, model,
token pointer and env are re-read from the store at EVERY launch, as a LIVE cascade level spliced
in BELOW the agent settings file and above `agent.default`. They are never copied into any settings
file. A launch that reads the store leaves `agents/<node>/agent.yaml` byte-identical.

⚑ **There is deliberately NO import/sync half any more.** The store used to be copied into
`agents/<node>/agent.yaml` by a verified swap — `build_candidate` / `persist_candidate` /
`import_persona_entry`. That route is GONE and must not be reintroduced, in any spelling: an
import, an adopt, a "cache the resolved persona", or a write-back all rebuild the retired design.
Two reasons, and both are structural rather than stylistic:

* the agent settings file holds USER-INTENT values ONLY (the file-purity invariant). A
  kanibako-written default or synced value in it is an error case. That invariant is exactly what
  makes file-beats-persona semantically forced — a key present in the file can only have got there
  by a user edit;
* a persisted copy of a live source can only go stale.

A broken store config is a HARD launch error naming the cause. There is no last-known-good.

## The layout kanibako reads (DESIGN §1)

```
$XDG_CONFIG_HOME/personas/          <- discovery root (FIXED, not configurable)
  <pid>/                            <- persona (identity segment)
    .secret_path                    <- ONE line: the path to the token file
    <hid>/                          <- harness (e.g. codex/, claude/)
      config.toml | settings.json   <- rendered harness-NATIVE config
```

`persona_store_root` is the single builder for that path, fixed by DESIGN §5a and deliberately not
configurable. It reuses the spec-backed XDG resolution in `kanibako.settings.paths.xdg`, so
`XDG_CONFIG_HOME` is honored iff it is set AND absolute, and otherwise the root is `$HOME/.config`.

**Store PRESENCE decides persona-vs-plain (DESIGN §4).** Every discovery entry point here returns a
clean "not a persona" `None` on a miss, so callers fall straight through to normal agent handling
rather than having to distinguish a miss from a failure.

## What lives here and what does not

Two parts live in this module:

* **discovery and resolve** — pure reads: locate an entry (`locate_entry`), and resolve its
  `.secret_path` token pointer (`resolve_secret_path`). Extracting the harness-native config itself
  is NOT here; it lives on the Target plugins, as
  `kanibako.targets.base.Target.read_persona_settings`.
* **the LIVE bundle** — `PersonaBundle` and `read_persona_bundle`: one read of the store, rendered
  into the harness-neutral values a single launch resolves against, threaded into
  `build_launch_snapshot` as an in-memory level.

The start-flow and create wiring is not here either. And nothing in this module ever reads the
TOKEN file: the pointer is handled arm's-length, exactly like the `secret_path` keyspace category
it feeds.

## `locate_entry` — the two clean misses, and the one raise

`locate_entry` takes any accepted agent ref (`navigator+codex`, `navigator℘codex`, or an
already-canonical node-name) and normalises it through `kanibako.agent_ref.parse_agent_ref`, like
every other ref source in the tree.

It returns `None` — a clean "not a persona" — in exactly two cases:

* the ref is BARE (node == harness, e.g. `claude`). A bare agent never has a persona store entry;
* the store dir `<root>/<pid>/<hid>/` is absent, or is not a directory. Store presence is the
  decision (DESIGN §4), so the caller falls through to normal agent handling. An `OSError` while
  stat'ing it is treated the same way.

A MALFORMED ref raises `kanibako.errors.ConfigError` out of `parse_agent_ref` — the same contract
as every other ref consumer, because a bad ref is a user error and not a store miss.

### ⚑ Path traversal is handled upstream, not here

`.` stopped being a legal segment character on 2026-08-04. Before that, `..+claude` would have
resolved to `<root>/../claude` — an ordinary harness config dir — and `navigator+..` to
`<root>/navigator/..`, the store root itself. Both now RAISE from `parse_agent_ref` on the first
line of `locate_entry`, rather than being screened out afterwards.

There is deliberately NO second dot-check in this module. One charset, enforced in one place; a
local re-check here would be a second copy of the rule that could drift away from the grammar.

## `resolve_secret_path` — pointer in, absolute host path out

The pointer file `<pid>/.secret_path` holds EXACTLY ONE line, the path to the token file
(DESIGN §2). The resolution rules run in this order:

* expand `$VAR`, then `~` — `os.path.expandvars` followed by `Path.expanduser`. That is the same
  order as the launch-side `_secret_pointer_usable`, and the pairing is deliberate;
* an ABSOLUTE result is used as-is;
* a RELATIVE result (`./token`, or a bare `token`) is anchored to the directory `.secret_path`
  itself sits in — `entry.persona_dir` — so `./token` becomes `<root>/<pid>/token`.

An absolute HOST path is required because kanibako mounts the token arm's-length into the box,
where a relative anchor would be meaningless (DESIGN §3). The token file itself is NEVER opened or
stat'd here, so the pointer resolves even when the token does not exist yet; deciding whether it is
usable is the launch gate's job.

### The tolerance contract

`resolve_secret_path` never raises through. Every one of these yields
`SecretPathResult(None, <reason>)` for the caller to warn on:

* a missing, unreadable, non-UTF-8, oversized, empty or whitespace-only `.secret_path`;
* a MULTI-line `.secret_path`. A single trailing newline is not a second line — the reader strips
  one trailing `\n` (and a preceding `\r`), and any newline still left means a real second line and
  a malformed file;
* a line that cannot expand to a usable path. `expanduser` raises `RuntimeError` on an unresolvable
  `~nosuchuser`; `resolve` raises `ValueError` on an embedded NUL, or `OSError`. All three are
  malformed-pointer shapes, so all three are caught and reported rather than escaping.

`_POINTER_CAP` is 16 KiB. The file holds one token path, which is PATH_MAX-ish, so anything larger
is a malformed store and is rejected WITHOUT being slurped into memory. That is the tolerance
contract stated as a size: fail warnably, never OOM.

`SecretPathResult` itself sets exactly one of its two fields — `path` (an absolute host path) on
success, else `error` (a caller-warnable reason).

## `PersonaBundle` — the values ONE launch resolves against

`PersonaBundle` is everything `read_persona_bundle` got out of the store in a single read, in the
form the launch consumes it: the behavior scalars, the passthrough env block, and the resolved
token pointer. It is a VALUE for one launch, not a cached view. Nothing in it is written to any
file.

| field | what it carries |
|---|---|
| `endpoint` / `model` | the harness config's alternate base URL and model id; `None` when the config names neither |
| `auth_env` | the env var the bearer token is exported as. It also NAMES the `secret_path.<auth_env>` entry the token pointer lands in |
| `env` | the passthrough env block, already stripped by the reader of the two single-source vars (base URL and token) |
| `env_dropped` | the names the reader SKIPPED as undeliverable — a non-string config value — so a caller can warn. Never silent |
| `token_path` | the resolved absolute host token path, or `None` when the `.secret_path` pointer did not resolve |
| `token_error` | why it did not resolve; set iff `token_path` is `None`. A SOFT condition — the rest of the bundle is still usable, so a caller warns and carries on rather than refusing the launch |
| `reject_reason` | set iff the located entry has a harness reader that REFUSED its config: present but unusable, naming the cause |
| `no_reader` | set iff the harness has NO persona reader at all — today goose and `NoAgentTarget`, which inherit the base no-op |

The `env` and `env_dropped` defaults are immutable by construction (`MappingProxyType({})` and
`()`), because a bare `{}` as a `NamedTuple` field default is ONE object shared by every instance.

### ⚑ Why `reject_reason` and `no_reader` are two fields and not one

They mirror the two non-success arms of `kanibako.targets.base.PersonaReadOutcome` one-for-one, and
keeping them apart is load-bearing:

* a launch HARD-ERRORS on `reject_reason` — a config the harness could read and refused. An entry
  EXISTS and the user needs to hear why it did not take;
* a launch must NOT error on `no_reader`. There was never a config to refuse, so it contributes
  nothing and is likewise not a complaint about any file. A goose persona is configured entirely
  through the keyspace and merely happens to own a store directory (often purely for its
  `.secret_path`); refusing it would break every such launch.

Both render `{}` from `to_persona_values`. Only the DIAGNOSIS differs, which is why folding them
into one field would be a silent regression rather than a simplification.

## `to_persona_values` — the un-discriminated mapping

`to_persona_values` renders a bundle into the shape `build_launch_snapshot(persona_values=…)`
takes: the bare behavior names `endpoint` and `model`, plus `secret_path.<auth_env>`, plus one
`env.<VAR>` per passthrough var.

The keys are deliberately NOT discriminated onto an agent. The store knows a persona, not a
cascade; `settings_launch._persona_partial` is what wraps them under the active agent slot.

### ⚑ Emptiness is handled per value class, and the asymmetry is the point

* `endpoint`, `model` and the token path are OMITTED when absent or empty. This mapping is a
  cascade LEVEL, so emitting `""` would not mean "unset" — it would OVERRIDE `agent.default` and
  every rung below it with emptiness, turning a store that names no model into a store that names
  the empty model.
* an `env.<VAR>` value passes through VERBATIM, empty string INCLUDED. `"FOO": ""` in a persona
  config is a user deliberately exporting an empty var, and the reader already ruled it deliverable
  by putting it in the passthrough set rather than in `env_dropped`. Dropping it here would be an
  invisible loss of exactly the kind this passthrough exists to end.

An empty var NAME is still skipped: no env var can be named `""`, so it is undeliverable rather
than empty.

## `validate_endpoint` — minimal by design, because a false refusal breaks a box

`validate_endpoint` raises `kanibako.errors.ConfigError` unless the endpoint is a well-formed
`http`/`https` base URL. It is validate-only, exactly like `kanibako.agent_ref.parse_agent_ref`.

It checks minimal well-formedness ONLY: a recognised scheme (`http`/`https`, case-insensitive) and
a non-empty host. Nothing about path, port or query is checked. A persona endpoint is a base URL
the harness appends its own routes to, and a stricter gate risks refusing a shape that works today.
A false refusal here breaks a working box, which is the one outcome this check must never cause.

This is the boundary a live incident crossed uncaught. A scheme-less endpoint such as
`myhost:8080/v1` reads as an `urlsplit` scheme of `"myhost"`, sails past every truthiness check
downstream, and dies inside Node with `Invalid URL`. Catching it here turns that into a named
config error at read time.

`_ENDPOINT_SCHEMES` is the set of schemes the delivered HTTP clients — the urllib probe and the
Node harness runtimes — can act on.

## `read_persona_bundle` — the entry point, and its raise contract

`read_persona_bundle` reads the store ONCE and returns the LIVE values for one launch.

`None` means the ref is NOT a store persona: a bare agent, or a `<pid>+<hid>` with no store entry —
`locate_entry`'s clean miss, store presence deciding persona-vs-plain per DESIGN §4. It is the
"nothing to do" signal, and it is DISTINCT from a located entry that yielded nothing, which comes
back as a bundle carrying `reject_reason` or `no_reader` (see above).

It is a pure read: no probe, no network, no write, and the token file itself is never opened — only
its pointer is resolved, because usability is the launch gate's job.

**It NEVER RAISES, with one deliberate exception:** a malformed *ref* raises `ConfigError` out of
`parse_agent_ref`, exactly as it does for every other ref consumer. Everything downstream of a
successfully located entry is fail-soft. That is what makes the function safe to call from the
credential-lifecycle paths (`stop`, creds-watch), where a raise would break an unrelated operation.

### The third-party plugin guard

`target.read_persona_settings` is called inside a bare `except Exception`. The `Target` contract
says it never raises — but a THIRD-PARTY plugin can still break that contract, and this seam rides
paths that must not fail closed. A plugin exception is converted into a `reject_reason` naming the
harness, the exception class and its message. It is reported, never swallowed silently.

### The endpoint rejection message

When the harness config names an endpoint that fails `validate_endpoint`, the resulting
`reject_reason` names the entry, the config dir, and the underlying error, and then offers the
override that does not require editing the store:
`kanibako system set agent.<display>.endpoint=<url>`, where `<display>` is the ref rendered through
`display_agent_ref` (the human-typable `+` form, not the canonical `℘`).

## Two probe rules that are easy to get BACKWARDS

They do not live in this module, but they belong to the same model and both fail in the direction
of refusing a box that actually works:

1. **"no model" is not "cannot verify".** Some servers need no model entry at all, so the probe is
   SENT with the key OMITTED and the server decides.
2. **NEVER substitute a placeholder model id.** A hardwired-model server can reject an id it does
   not serve, and a false REJECTED refuses a working box.

## Related reading

* `~/canon/notebook/procedures/persona-resolution-model.md` — the governing procedure.
* `designs/persona-grata-autoimport-DESIGN.md` §1, §2, §3, §4, §5a — the design SOT.
* `llm-docs/kanibako/agent_ref.py.md` — the `persona+harness` ref grammar this module normalises
  through.
* `kanibako.targets.base` — `Target.read_persona_settings` and `PersonaReadOutcome`, the harness
  half of the read.
