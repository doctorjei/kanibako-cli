# The Target Plugin Contract — the ABC, the descriptor types, and the bind-shaped map

This module is the entire surface a plugin author sees. Three first-party plugins
(`kanibako-agent-claude`, `-goose`, `-codex`) and any third-party wheel are written against the
names below and nothing else, so almost everything here is contract the type system cannot carry:
preconditions, what a call INVALIDATES, what is deliberately NOT checked, what a value used to be.
That is why the prose is long and why the bar for deleting any of it is high.

The module holds four kinds of thing:

* the two **type aliases** for the terminal bind-shaped key map (`BindArm`, `CategoryBindDefaults`);
* the **declarative value types** a plugin fills in — `TargetSetting`, `Mount`, `AgentInstall`,
  `BindKind` / `HostSrcOrigin` / `BindScope` / `Binding`, `Channel` / `SettingArg`,
  `AccessTierRow` / `AccessRealization`, `PersonaSpec`, `Operation`, `Cadence` / `CredFileSpec`,
  and the `PluginDescriptor` that collects them;
* the **persona read-back and probe machinery** — `http_probe` / `ProbeResponse`,
  `PersonaSettings`, `PersonaReadOutcome`, `PersonaProbeVerdict`, `PersonaProbeOutcome`,
  `ProbeEvidence`, `probe_outcome`, `probe_outcome_no_model`, and the host-binary validator
  `_validate_agent_binary`;
* the **`Target` ABC** itself, whose default method bodies ARE the contract for a plugin that does
  not override.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §0 (closed keyspace, ONE DEST SPACE / TWO
DELIVERIES), §1A (CLI level, the projected-surface exception), §2c (the bible chapter bind), §2d
(the per-agent keyspace this module maps onto). ⚑ The spec is the live authority; read it first.

## The terminal bind-shaped map

```python
BindArm = dict[str, tuple[str, ...]]
CategoryBindDefaults = dict[str, BindArm]
```

`BindArm` is the map stored at ANY terminal bind-shaped key — `<scope>.bindings.{ro,rw}` and, since
2026-08-08c, `<scope>.{caches,seeded,common,synced}`. The KEY is the box DESTINATION, normalized by
`settings_resolve.normalize_bind_dest`; the value is `(host_src[, options])`.

It is **not** `core_defaults.BindArmTable`, which maps the terminal key **to** the map.

`CategoryBindDefaults` is what `Target.default_category_binds` / `default_common` / `default_seeds`
return: a TERMINAL category key mapped to its whole `BindArm`.

### ⚑ `BindDefault` IS GONE — and this is a tombstone, not a description

`BindDefault` — the NAME-keyed `(host_src, box_dest[, options])` tuple — no longer exists. It was
the value of a `<scope>.<category>.<name>` key, and no such key exists in any bind-shaped category
any more: **the destination is the identity, so it cannot also be a tuple element.**

A plugin still emitting one produces a key the reader refuses BY NAME
(`settings_assemble._insert_dotted`) rather than a silent mis-bind — the refusal IS the migration,
v1.8.0 being a clean break. There is no shim.

## The declarative value types

```python
@dataclass(frozen=True)
class TargetSetting:
    key: str
    description: str
    default: str = ""
    choices: tuple[str, ...] = ()
```
A runtime setting a target advertises through `setting_descriptors`. *key* is the setting key in
the agent state dict (`"model"`); empty *choices* means freeform.

```python
@dataclass(frozen=True)
class Mount:
    source: Path
    destination: str
    options: str = ""

    def to_volume_arg(self) -> str
```
A volume mount for a container; `to_volume_arg` returns the `-v` argument string for
podman/docker.

```python
@dataclass
class AgentInstall:
    name: str
    binary: Path
    install_dir: Path
    launcher: Path | None = None
```
Information about an agent installation on the host. *binary* is the host symlink/path to the agent
binary and *install_dir* the root of the installation.

*launcher* is the host entrypoint the PLUGIN owns and binds into the box as-is (e.g.
`~/.local/bin/claude`), anchored to the agent's contract path, **never resolved via `$PATH`.**

```python
class BindKind(Enum):
    FILE = "file"
    DIR = "dir"

class HostSrcOrigin(Enum):
    LAUNCHER = "launcher"
    INSTALL_DIR = "install_dir"
    BINARY = "binary"
    LITERAL = "literal"

class BindScope(Enum):
    AGENT_CRITICAL = "agent_critical"
    AGENT = "agent"
```

`BindKind` — whether a binding mounts a single file or a directory.

`HostSrcOrigin` — where a binding's default host source comes from, before any cascade override:
`AgentInstall.launcher` (detection-derived), `AgentInstall.install_dir`, `AgentInstall.binary`, or
a fixed `Path` in the descriptor (`literal_src`).

`BindScope` — how widely a binding applies **plus its failure semantics**. `AGENT_CRITICAL` is
delivery-essential: a missing source safe-fails, and the binding is ro. `AGENT` is an agent-level
share (plugins), overridable and possibly rw.

```python
@dataclass(frozen=True)
class Binding:
    key: str
    origin: HostSrcOrigin
    box_dest: str
    kind: BindKind
    scope: BindScope
    ro: bool = True
    literal_src: Path | None = None
```
One bound element: a delivery binary/launcher/share, or an agent share.

The resolved host source is the cascade override `agent.<name>.bindings.{ro,rw}.<key>` if set, else
the *origin* — a detection field (`LAUNCHER`/`INSTALL_DIR`/`BINARY`) or `literal_src` (`LITERAL`).
`AGENT_CRITICAL` bindings safe-fail when the source is missing and get bind-as-is inode pinning
plus core dest-symlink clearing; `AGENT` shares are best-effort, so a missing or suppressed share
is fine. `literal_src` is meaningful only when `origin == LITERAL`.

⚑ **The override key is settings-file-only.** There is no `config set` / `config reset` route to it
at any scope, **so do not document one.** The key is still declared, still read by the launch
cascade, and still readable via `config get`; a user, or a plugin's own docs, repoints a binding by
hand-editing `agents/<node>/agent.yaml`.

```python
class Channel(Enum):
    FLAG = "flag"
    ENV = "env"

@dataclass(frozen=True)
class SettingArg:
    setting_key: str
    channel: Channel
    flag: tuple[str, ...] = ()
    env_var: str = ""
```

`Channel` — where a value-bearing arg is emitted: an argv flag or an environment variable.

`SettingArg` — a value-bearing agent setting routed to one of those (model, provider).
*setting_key* is the agent setting supplying the value; *flag* is the FLAG form (`("--model",)`)
and *env_var* the ENV form (`"GOOSE_MODEL"`).

## The `access` tier realization (spec §2d)

```python
@dataclass(frozen=True)
class AccessTierRow:
    flag: tuple[str, ...] = ()
    env_value: str = ""
```
How one `access` tier is realized for one harness: the emission for whichever channel this
harness's `AccessRealization` declares. *flag* is the argv fragment for FLAG
(`("--permission-mode", "acceptEdits")`); *env_value* is the value for ENV (goose
`GOOSE_MODE=approve`).

⚑ **EMPTY and MISSING are different, and keeping them apart is why the rows are OPTIONAL rather
than defaulted.** An EMPTY row (both fields empty) means emit nothing, DELIBERATELY — the right
realization for a tier a default-safe harness already runs at with no argument (claude/codex
`restricted`). A MISSING row means the harness cannot render that tier and the launch refuses (see
`AccessRealization.row`). For a harness whose unset default is UNSAFE (goose's `GOOSE_MODE`
defaults to `auto`), "emit nothing" and "cannot render" would otherwise both silently mean
permissive.

```python
@dataclass(frozen=True)
class AccessRealization:
    channel: Channel
    env_var: str = ""
    restricted: "AccessTierRow | None" = None
    editing: "AccessTierRow | None" = None
    full: "AccessTierRow | None" = None
    setting_key: str = ""

    def row(self, tier: str) -> "AccessTierRow | None"
    def renders(self, tier: str) -> bool
    def rendered_tiers(self) -> tuple[str, ...]
```
The per-harness realization of the `access` permission tier. Unlike a plain `SettingArg`, the value
comes from the resolved permission tier.

* *channel* — where EVERY row of this harness is emitted: FLAG (argv, claude/codex) or ENV
  (*env_var*, goose `GOOSE_MODE`). **One channel per harness:** expressing one tier as a flag and
  another as an env var would be two mechanisms for one axis.
* *restricted* / *editing* / *full* — the tier rows. `None` means this harness cannot render that
  tier; the launch then REFUSES, naming the tiers it CAN render, rather than substituting silently
  or falling through to the permissive arm. The fields are named for the tiers because the spec
  CLOSES the tier set.
* *setting_key* — the persisted key the launch reader redeems (all three shipped agents use
  `"access"`, default `full`); empty = per-launch `-S`/`-A` only.

`row(tier)` returns the row for *tier*, or `None` when unrenderable. `None` covers BOTH "this
harness declared no row for that tier" (goose `editing`) AND "that is not a tier at all". The
caller refuses either way, which is the safe collapse: **an unknown tier must never reach an
emission.**

`renders(tier)` is the presence of a row.

`rendered_tiers()` returns the tiers this harness can render, least→most permissive. Used by the
launch REFUSAL to name the legal alternatives for THIS agent rather than the abstract enum. The
order comes from `settings_keyspace.ACCESS_TIERS`, the one declaration of the tier vocabulary; this
class spells the tier names as FIELDS, which is what makes a missing row a DECLARATION rather than
a lookup miss, but it does not get its own opinion about their ORDER.

## Personas — how a harness delivers an alternate endpoint

```python
@dataclass(frozen=True)
class PersonaSpec:
    token_var: str = ""
    endpoint_delivery: str = "env"          # "env" | "config_file"
    wire_api: str = "responses"
    provider_pin: tuple[tuple[str, str], ...] = ()
    model_required: bool = False
```
How a persona's alternate endpoint and bearer token are delivered for this harness.

A persona (`agent.<persona>℘<harness>`) points the harness at a third-party model endpoint with a
bearer token. How those two values reach the box is harness-specific, so the plugin declares it
here, and the persona preflight in `start.py` consults it.

* *token_var* — the `secret_path` key (== the in-box env var) carrying the bearer token. Claude uses
  the fixed `ANTHROPIC_AUTH_TOKEN`. A config-file harness (codex) leaves this EMPTY, which is
  DYNAMIC: the single configured `secret_path` key is the token var, and it doubles as the
  model-provider `env_key`.
  ⚑ That key's VALUE is **THREE-STATE** (2026-08-17 ruling): a configured path, ABSENT (never
  configured — the launch refuses), or an explicit `null` — this endpoint is deliberately KEYLESS,
  and the launch proceeds with no token mounted and no `Authorization` header on the verify probe.
  Whether an endpoint needs a token is a property of that SERVER, so it is declared per persona ON
  THE KEY (`start.py`'s two preflight gates), **never on a field here** — a harness-level flag
  could not express "this one persona of mine is keyless" while a sibling persona on the same
  harness needs a real token.
* *endpoint_delivery* — `"env"` (claude: the endpoint rides the descriptor's
  `endpoint`→`ANTHROPIC_BASE_URL` ENV `SettingArg`) or `"config_file"` (codex: the launch config
  generator writes it into `~/.codex/config.toml`'s `[model_providers.<id>]` block, not an env
  var). This is the ONLY thing that picks between the two preflight gates in `start.py`.
* *wire_api* — the config-file harness's model-provider wire protocol
  (`[model_providers.<id>].wire_api`); default `"responses"`, since Codex removed the `"chat"` wire
  (openai/codex#7782). Ignored for `"env"` delivery.
* *provider_pin* — `(setting_key, value)` pairs force-applied to the launch's effective setting
  state whenever this persona resolves an active endpoint, so a harness whose endpoint REQUIRES a
  specific provider cannot be misconfigured. Goose pins `("provider", "openai")`, and the
  descriptor's `provider`→`GOOSE_PROVIDER` `SettingArg` then emits it in the box. Empty
  (claude/codex) = no pin, byte-identical; a bare box with no active endpoint is never touched.
* *model_required* — whether a persona with a resolved endpoint but NO cascade-resolved model is a
  hard error. A missing model is not automatically invalid, since some endpoints need no model
  spec, so absence means "this persona needs none" unless the harness VETOES here. Claude keeps
  `False` (its model rides its own channels); goose and codex set `True` — a third-party
  OpenAI-compatible endpoint has no meaningful default, and a config-file harness cannot express
  "no model" at all.

### ⚑ `model_required` was NARROWED, not superseded

The 2026-08-17 ruling made `agent.<node>.model` itself three-state, so a persona-level explicit
`null` now lets a user declare "THIS endpoint needs no model" directly on the key, distinct from
simply never configuring one.

`model_required` still answers a DIFFERENT, HARNESS-CAPABILITY question — *"can this DELIVERY
MECHANISM express no model at all"* — not a per-server one, and the two can conflict:

* For an **ENV-delivery** harness (claude, goose) the veto is a RECOMMENDATION the key's
  per-persona fact can override: env delivery can simply omit the model, so a present-null model
  SUPPRESSES `model_required` there.
* For a **CONFIG-FILE** harness (codex) it cannot: the generated provider block types `model` as a
  non-optional field, so there is no shape a config-file persona could emit for "no model". A
  present-null model on a config-file harness is therefore a declared CONFLICT, refused BY NAME
  (`start.py`'s `_preflight_config_file_persona`), never silently resolved either way.

`model_required` is not deleted for this: the config-file conflict check is UNCONDITIONAL on
`endpoint_delivery == "config_file"`, not on this field, because the structural limit is the
DELIVERY MECHANISM, not the flag.

### The `PersonaSpec`-less fallback

A target with no `PersonaSpec` (`descriptor.persona is None`) resolves through the fallback in
`start.py`'s `_persona_wiring`, which spells out the claude shape EXPLICITLY: ENV endpoint
delivery, `ANTHROPIC_AUTH_TOKEN` token var, no provider pin, no model gate. That explicit spelling
is what keeps the fallback claude-shaped; the field defaults above are the declared-nothing
defaults.

```python
class ProbeResponse(NamedTuple):
    status: int | None
    body: str = ""

def http_probe(url: str, *, headers: dict[str, str], body: dict, timeout: float) -> ProbeResponse
```
POST *body* as JSON to *url*; return the HTTP status and the provider's error text.

The shared transport for `Target.verify_persona` probes. ANY HTTP response yields its integer
status — an error status like 401 is a real ANSWER from the endpoint, not a transport failure — and
`None` means a transport-level failure (DNS, refused, TLS, timeout, malformed URL), the probe's
unreachable/can't-tell shape.

⚑ **Never raises, and never logs the request: *headers* carry a bearer token.**

⚑ **Redirects are NOT followed** (the private `_NoRedirects` handler), because urllib would re-send
every header — the `Authorization` bearer included — to the redirect target, possibly cross-origin.
A 3xx comes back as its status instead. The handler names NO header, deliberately: stripping a
named one and following the redirect anyway would carry a third-party plugin's `x-api-key` to the
target — the same name-keyed shape `_request_secrets` refuses below.

The bare `except Exception` arm catches `URLError` / `socket.timeout` / `ConnectionError` / ssl /
`ValueError` (bad URL) — all transport shapes, and the contract is never-raises.

**The ERROR body is read, capped and scrubbed** (`_provider_text`, `_PROVIDER_READ_CAP` bytes off
the wire then `_PROVIDER_TEXT_CAP` characters). The status alone answers "was this refused" but not
"what was refused", and the difference is what made a 403 on a model the account may not use read
as a dead credential. A success body is still not read — there is nothing to explain.

⚑ **The credential scrub lives HERE, beside the request, and that placement is the whole
guarantee.** The credentials are in *headers* at this call and nowhere downstream, so a provider
that echoes one back in its own error cannot reach any message a caller prints.

**EVERY caller-supplied header value is a secret, not just `Authorization`** (`_request_secrets`).
`http_probe` is a *published* helper: a third-party plugin authenticating with `x-api-key` — or a
header name nobody here foresaw — must not depend on this module recognizing it. Both first-party
plugins happen to send `Authorization`, which made them safe by luck rather than by construction.
A scheme-prefixed value contributes its remainder too, so `Bearer <tok>` redacts on the bare token
as well. **The only exclusion is LENGTH, never a header name** — `_SECRET_MIN_CHARS` — because a
name-keyed exemption list is exactly the shape that lets the next credential through under an
unforeseen name; an empty value would splice the marker between every character of the body, and
nothing that short carries credential entropy. The floor is *eight* because that is where ordinary
header values stop being collateral (`gzip` and `close` are under it, `keep-alive` and
`application/json` are caught by it) while staying below anything anyone issues as a key.

The set is returned **longest-first**, and `_scrub` replaces in that order: where one header value
strictly prefixes another, replacing the short one first would eat the long one's anchor and leave
its tail in cleartext. Sorting makes that unavailable rather than resting a security property on
`dict` insertion order.

⚑ **The set is therefore not all credentials, and a false member is not cheap.** `2023-06-01` — the
claude plugin's `anthropic-version` — is in it; an error naming that date has the date redacted and
can trip `_survives` and lose the *whole* message. The trade still favors redaction, and it is why
`_WITHHELD` reports a **match against a request value** and never accuses the provider of echoing a
key back.

⚑ **The scrub runs on the DECODED body, never on the wire bytes alone.** A literal replace sees
only a secret's raw spelling, so any *escaped* rendering walks past it: a key containing `/`,
echoed by a provider whose encoder writes it `\/` (PHP's `json_encode` does that by default),
printed in full. `_provider_text` parses the body first and scrubs the decoded strings — keys
included (`_scrub_decoded`) — so the whole JSON escape alphabet is undone by the parser instead of
enumerated here, and the re-encoding that follows can only escape text the secret has already left.
A body that does not parse as JSON has no encoding layer to undo and is scrubbed as plain text.

🛑 **"Does not parse" means `ValueError` and nothing else.** A body that *is* JSON but whose decode
could not FINISH has an encoding layer, un-undone, and the raw spelling is all the plain-text scrub
can see — so it withholds. It gets its OWN message, `_UNREADABLE`: `_WITHHELD` reports a *match*
against a request value, nothing matched here, and reusing it would tell the user about a match that
never happened. This was a
leak: a parse returns trees far deeper than the walk over its result can follow — that walk is
Python frames — and a swallowed `RecursionError` printed the wire body with a `&`-escaped key
legible in it (`_survives` deletes backslashes, so it read that as `u0026`, not `&`; Go's
`encoding/json` HTML-escapes `&`, `<` and `>` by *default*, so a Go gateway echoing such a key
writes exactly this). The safe default therefore lives on the **catch-all**, not on the one failure
that was foreseen — and only the parse itself sits inside the `ValueError` guard, so a `ValueError`
out of the walk or the re-serialization cannot take the "not JSON" arm.

🛑 **The catch-all has FOUR owners, and both `except Exception` arms are written bare for that reason:**
the stack already consumed above this call, `_compact`, a shape nobody foresaw, and `json.loads`
itself. `_compact` is the owner a reader narrowing the second arm will miss — `json.dumps` recurses
once per container over the tree the walk just built, up to `_NEST_DEPTH` deep, and before 3.12 that
is charged against `sys.getrecursionlimit()` exactly as the decoder is (from 3.12 it draws on a
separate C-recursion budget). The re-serialization is therefore a `RecursionError` source in its own
right, not the walk's postscript. Neither depth starts from an empty stack, either: `_provider_text`
runs under `verify_persona` and whatever called that, so how much limit is left is the caller's to
spend and not this module's to know. And narrowing an arm costs more than a message —
`_provider_text` is called from inside `http_probe`'s `except HTTPError` handler, where a raised
exception is NOT caught by that `try`'s sibling `except Exception`, so it leaves `http_probe` and
breaks the never-raises contract.

🛑 **`json.loads` itself is one of the catch-all's owners, and on the minimum supported interpreter
it is an ordinary one.** Before 3.12 the decoder's own recursion is charged against
`sys.getrecursionlimit()`, so a merely deep body — 995 levels, under 2 KB, less than a quarter of
`_PROVIDER_READ_CAP`, something a server can serve — dies in the parse and the user loses the whole
message. `json.loads` exposes no depth knob, so the floor is stated rather than guarded;
`_provider_text`'s docstring carries the command that re-derives it.

⚑ **The walk carries TWO budgets, and neither is spent on the other's axis.** `_EMBED_DEPTH` buys a
descent into an embedded document; `_NEST_DEPTH` buys one ordinary container, so a deep tree of
dicts and lists is one document and costs no embed budget. `_NEST_DEPTH` exists so the walk's depth
is not `sys.getrecursionlimit()` — a global any code in the process may retune, and no place to
rest a security property. It carries ACROSS an embed hop rather than restarting: restarting would
multiply the two, and a body spending the product fits under `_PROVIDER_READ_CAP`.

Both are BOUNDS, not measured limits. `_EMBED_DEPTH` is one step per proxy hop, and because the
input is already capped at `_PROVIDER_READ_CAP` the hostile work a body can buy is at most that cap
times the depth.

⚑ **`_NEST_DEPTH` is HALF THE STACK, not a corner of it, and that is deliberate.** A container level
costs TWO Python frames before 3.12 — the comprehension takes one of its own until PEP 709 inlines
it — and one after, so a full budget peaks at a recursion limit of **508** on the oldest interpreter
kanibako supports, against a default of 1000. It is stated rather than shrunk because spending it
all is NOT a leak: a `RecursionError` out of this walk lands on `_provider_text`'s catch-all and
takes `_UNREADABLE`, so a budget set wrong costs the user a message, never an unscrubbed one. The
`_NEST_DEPTH` comment carries the command that re-derives the peak.

⚑ **Past EITHER budget the region is replaced by `_REDACTED` IN PLACE**, never skipped and never
escalated to withholding the body: a document we declined to read is a place a secret could be, so
it is treated as one, while its scrubbed surroundings still reach the user. The unread document is
the only thing lost. A corollary for anyone reading the output: **a `<redacted>` in
`ProbeResponse.body` is not evidence a secret was there** — the same marker stands for an
over-budget region.

⚑ **`_scrub_embedded` carries the decode move inward.** A gateway fronting another provider
routinely echoes the upstream error body as a **string value**, and that string keeps its own
escape layer — the identical defect, one level down. A string that parses to a *container* is
followed and scrubbed recursively; anything else (a bare number, `null`, a quoted word) is left as
written, so ordinary prose is never rewritten into a JSON rendering of itself. Its own decode guard
is `ValueError` and nothing wider, for the same reason `_provider_text`'s is: a wider catch would
treat a decode it could not FINISH as plain text and scrub it for raw spellings alone, which is
exactly the shape the decode exists to stop.

⚑ **A JSON body is RE-SERIALIZED, not edited in place.** The printed line is the provider's
*meaning*, not its bytes: a pretty-printed body prints compacted, `é` prints as itself (the
`ensure_ascii=False` in `_compact` is load-bearing — the default would re-escape a non-ASCII
credential into a `\uXXXX` spelling the following scrub cannot see), `1.50` prints as `1.5`, `1e3`
as `1000.0`, and duplicate keys collapse to the last. None of that is reversible from the output. A
non-JSON body passes through unchanged apart from the whitespace collapse.

⚑ **There are TWO caps and only one of them is ordered safely.** `_PROVIDER_TEXT_CAP` is applied
after the scrub deliberately — truncating first could leave half a token standing.
`_PROVIDER_READ_CAP` is not `_provider_text`'s to order: `http_probe` cut the body at that many
*bytes* before the scrub ever saw it, so a key straddling the cut arrives bisected, matches no
secret, and its surviving head prints whenever the whitespace collapse pulls it inside the character
cap. Reading more of a hostile body is the only fix, and it is not one.

⚑ **The scrub runs on BOTH SIDES of the whitespace collapse, and neither call is redundant.**
Collapsing runs of whitespace can JOIN two runs into a match that was not there before, and can
equally DESTROY one that was — a header value carrying a double space matches the wire body and not
the collapsed body. It is also the only scrub the non-JSON path gets, so it does not get to be
order-sensitive about which side it runs on.

⚑ **`_survives` is the last guard, it FAILS LOUD — and it is a guard, not coverage.** After the
scrub it re-reads the text with backslash escaping deleted; if a secret is still recoverable — a
body escaped *twice* is the case it catches — the whole body is replaced by `_WITHHELD` rather than
printed. 🛑 **The two outcomes are not the same outcome.** What it *catches* costs the user an error
message. What *walks past* it — any re-encoding that is not backslash escaping: `%2F`, `&#47;`,
base64, an entity nobody has thought of — is **not caught and IS printed, credential and all**;
nothing is withheld, because nothing here can tell that anything happened. Closing one of those
classes takes another **decode layer ahead of the scrub**, the way `_scrub_embedded` closed embedded
JSON; widening this guard cannot do it, and trying turns it into an enumeration of spellings that is
wrong the moment a provider picks a new one.

⚑ **So `ProbeResponse.body` says "scrubbed", never "clean".** The residue above is documented on
`ProbeResponse`'s own class docstring — the surface `help(ProbeResponse)` prints, so an author's
tooling shows it without opening the source — and it is a residue that **reaches printed output**,
so the warning is not reserved for some other use: the value is an error message fit to show a
human, not a string proven free of secrets. The same docstring carries the re-serialization note,
because printing `response.body` is where an author meets it, and one guarantee it *does* make:
the string is always encodable, so printing it to a strict stream (`sys.stdout`) cannot raise.

⚑ **That encodability is BUILT, not inherited, and the final re-encode in `_provider_text` is what
builds it.** `json.loads` turns a `\udXXX` escape into a REAL lone surrogate and `_compact`'s
`ensure_ascii=False` — load-bearing, above — writes it straight back, so without that round-trip the
returned string raises `UnicodeEncodeError` in the CALLER's print. Every sink kanibako itself ships
is `sys.stderr`, whose `backslashreplace` handler hides it; `http_probe` is a PUBLISHED helper, and
a plugin printing to `sys.stdout` would get the traceback instead of the error it asked for. The
round-trip runs AFTER the scrub, never before: a replacement can only DESTROY characters, so it
cannot rebuild a secret the scrub already took, and running it first would hand `_survives` a string
the scrub never saw.

```python
class PersonaSettings(NamedTuple):
    endpoint: str | None
    model: str | None
    auth_env: str
    env: Mapping[str, str] = MappingProxyType({})
    env_dropped: tuple[str, ...] = ()
```
Persona-specific values extracted from a rendered harness config.

The persona-grata store (`$XDG_CONFIG_HOME/personas/<pid>/<hid>/`) lays down a harness-native
config file (codex `config.toml`, claude `settings.json`); `Target.read_persona_settings` parses
the one it understands into this harness-neutral record, which the auto-import maps onto the agent
keyspace (`self.endpoint` / `self.model` / `self.secret_path.<auth_env>`) and whose `env` rides the
launch as plain passthrough.

**`PersonaSpec` declares HOW a harness delivers these values INTO a box; this is the values
themselves, read back OUT of a rendered config.**

*endpoint* is the alternate base URL (codex `base_url` / claude `ANTHROPIC_BASE_URL`); *model* the
provider model id when the config names one; *auth_env* the env var the bearer token is exported as
(codex `env_key` / claude's fixed `ANTHROPIC_AUTH_TOKEN`).

`env` is the harness config's env block MINUS the base URL and the bearer token, which ride their
own channels — `endpoint` here and the secret-path bind — and so **must never be duplicated into
the passthrough.** A config entry whose value is not a string cannot be delivered as an env value
(a JSON number/bool/null would be `str()`'d into a Python repr), so its NAME lands in `env_dropped`
for a caller to warn about. **Nothing is ever dropped silently.**

⚑ `env` defaults to a `MappingProxyType`, not a bare `{}`/`[]`: a mutable default on a `NamedTuple`
field is ONE object shared by every instance.

```python
class PersonaReadOutcome(NamedTuple):
    settings: PersonaSettings | None
    reject_reason: str | None
```
The tri-state result of `Target.read_persona_settings`.

* *settings* non-`None`, *reject_reason* `None` — a usable persona config was read;
* *settings* `None`, *reject_reason* a specific human-readable cause naming the offending file and
  what was wrong with it — the config is PRESENT but UNUSABLE, and the caller reports the reason
  verbatim;
* BOTH `None` — this harness has no persona reader at all (today goose and
  `no_agent.NoAgentTarget`, which inherit the base no-op). **Not a complaint about any file.**

`settings` and `reject_reason` are never both non-`None`. Splitting "no reader" from "unusable
config" is the point: a reject must NAME ITS OWN CAUSE instead of collapsing into a bare `None` the
caller can only report as a vague "no usable config".

```python
class PersonaProbeVerdict(Enum):
    PASS = "pass"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"
    NOT_APPLICABLE = "not_applicable"

@dataclass(frozen=True)
class ProbeEvidence:
    endpoint: str
    model: str | None = None
    model_origin: str = ""
    token_path: Path | None = None
    status: int | None = None
    provider_text: str = ""

class PersonaProbeOutcome(NamedTuple):
    verdict: PersonaProbeVerdict
    reason: str | None = None
    evidence: ProbeEvidence | None = None

    @classmethod
    def passed(cls) -> "PersonaProbeOutcome"
    @classmethod
    def rejected(cls, evidence: ProbeEvidence) -> "PersonaProbeOutcome"
    @classmethod
    def inconclusive(cls, reason: str, evidence=None) -> "PersonaProbeOutcome"
    @classmethod
    def not_applicable(cls, reason: str) -> "PersonaProbeOutcome"
```

`PersonaProbeVerdict` is the FOUR distinct answers `Target.verify_persona` can give. They are kept
apart so a caller never reports *"the probe ran and could not decide"* and *"no probe ever ran, and
none ever will for this input"* as the same thing — **the latter covers configurations that are
perfectly valid and that no user can act on.**

`PersonaProbeOutcome` pairs that verdict with a named cause:

* `PASS` — 2xx; the endpoint accepted the token and answered;
* `REJECTED` — 401/403; the endpoint POSITIVELY refused it;
* `INCONCLUSIVE` — the probe WAS attempted and could not decide: the endpoint was unreachable, or
  answered something that is neither an accept nor an auth reject. A transient, reportable
  condition, and the one the launch warning exists for (DESIGN §5b: never punish a launch for a
  blip, but do say the endpoint went unanswered);
* `NOT_APPLICABLE` — nothing was learned about the token and nothing will be for this input: the
  harness implements no probe, the token could not be read, or the endpoint answered that it
  requires a model this persona does not name (`probe_outcome_no_model`). Nothing is wrong and
  there is nothing a user could do, so a caller reports it to the LOG, not to the user.
  ⚑ **"The persona names no model" is NOT on this list on its own:** such a persona is probed with
  the field OMITTED, because the endpoint may not need one.

*reason* is a human-readable clause naming the specific cause, set for the two non-answer arms and
`None` for `PASS`/`REJECTED`, which name themselves. It is written to interpolate into a sentence,
e.g. `f"could not verify … ({outcome.reason})"`.

### `ProbeEvidence` — what was sent, and what came back

🛑 **A REFUSAL MESSAGE NAMES THE REFUSAL, NEVER A CULPRIT.** The message this type exists to fix
asserted *"the endpoint rejected the token"* and told the user to replace it; the measured cause
(2026-08-30) was a model the account had no entitlement to, on a token that was perfectly valid.
**A 401/403 does not say which input was at fault**, and the probe has no way to find out — so it
states the status, the inputs and the provider's own words, and the reading is the user's.

*model* is what went ON THE WIRE, which is not always what the user configured: a harness that
resolves a tier alias through an env var records the id it SENT and says where it came from in
*model_origin*, or the message reads as if kanibako invented an id. *token_path* is a PATH, never a
value, so it carries no secret by construction. *provider_text* is different: `http_probe` capped
and scrubbed it of every header value the request carried, and it is also **re-serialized** when the
body was JSON — the provider's meaning, not its bytes. 🛑 **Scrubbed is not proven clean.** The
record inherits `ProbeResponse.body`'s documented residue verbatim; read this block as an error
message fit for a human, not as a record proven free of secrets.

It is filled in TWO halves — a plugin builds the request half before the call, `probe_outcome`
fills *status* / *provider_text* from the answer, **so no plugin can forget to.** `rejected` takes
it POSITIONALLY for the same reason: a refusal whose inputs a user cannot see is the failure mode.

`PersonaProbeOutcome.evidence_block()` is THE single renderer for both consumers (the launch hard
error in `commands/start.py::_persona_probe_error` and the create warning in
`commands/box/_parser.py::_check_persona_store_for_create`), so the two shapes cannot drift. It is
EMPTY unless the endpoint ANSWERED: a probe that never reached it has no facts to lay out, and four
lines restating the caller's own inputs is noise. The closing "which input was at fault" sentence
is emitted for a REFUSAL status ONLY — for any other status the caller's own *reason* already says
what was seen, and a second reading would be a guess.

```python
def probe_outcome(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome
```
Map an HTTP probe *response* onto a `PersonaProbeOutcome`.

Covers ONLY the arms an ATTEMPTED HTTP probe can reach — every caller has already decided a request
was possible, so `NOT_APPLICABLE` never comes from here:

* 2xx → `PASS`; 401/403 → `REJECTED`;
* `None` (transport failure: DNS, refused, TLS, timeout, bad URL) → `INCONCLUSIVE` "unreachable";
* anything else (404 wrong path, 429 rate-limit — the token was accepted, 5xx, a 3xx we refuse to
  follow) → `INCONCLUSIVE` naming the status. **Never punish a launch for an endpoint blip**
  (DESIGN §5b).

```python
_MODEL_REQUIRED_STATUSES = (400, 422)

def probe_outcome_no_model(response: ProbeResponse, sent: ProbeEvidence) -> PersonaProbeOutcome
```

`_MODEL_REQUIRED_STATUSES` is the set of statuses an endpoint answers when the request is
well-formed EXCEPT for a missing `model` field: 400 (the `invalid_request_error` the reference
anthropic/OpenAI APIs return) and 422 (the validation status FastAPI/vLLM-style
OpenAI-compatible servers use for the same thing). Consulted ONLY by `probe_outcome_no_model`.

`probe_outcome_no_model` is `probe_outcome`, for a probe that deliberately OMITTED `model`.

A persona that names no model is NOT invalid: a persona endpoint is a third-party
anthropic-/OpenAI-compatible provider, not the reference API, and such a server may serve exactly
one model or apply its own default. So the probe **ASKS rather than declining to run** — declining
would let a dead token sail past the launch gate and 401 inside the box, which is the one
protection the per-launch probe exists to give.

The answer then needs one reading `probe_outcome` cannot make, because only the CALLER knows the
field was left out:

* `PASS` / `REJECTED` / `INCONCLUSIVE` are UNCHANGED. An auth reject is an auth reject whether or
  not a model was named, and preserving that arm for a model-less persona is the entire point of
  probing one.
* a model-required answer (`_MODEL_REQUIRED_STATUSES`) becomes `NOT_APPLICABLE` rather than a
  warning: it says this endpoint needs a model and this persona names none, so nothing was learned
  about the token, and the harness may still supply its own default at runtime. **It must neither
  block a launch nor nag on every one.**

⚑ The status set is an **INFERENCE** — the wire does not say "you omitted the model" — so it is
deliberately NARROW and applies ONLY when the caller omitted the field. It never widens
`probe_outcome`'s own mapping.

## Launch shape and credential lifecycle

```python
@dataclass(frozen=True)
class Operation:
    fragment: tuple[str, ...]

class Cadence(Enum):
    SYNC = "sync"
    SEED_ONCE = "seed_once"

@dataclass(frozen=True)
class CredFileSpec:
    home_rel: str
    host_rel: str
    cadence: Cadence = Cadence.SYNC
    mtime_gate: bool = True
    filtered: bool = False
    is_dir: bool = False
```

`Operation` — a standalone op fragment (exec/headless) with no session mode; spliced after
`command`.

`Cadence` — the credential/config file sync cadence. `SYNC` is bidirectional, mtime-gated each
launch (credentials/token files); `SEED_ONCE` is one-way host→project at init, never written back
(config files).

`CredFileSpec` — a credential/config file's lifecycle; the filter/merge PAYLOAD stays a plugin
hook (`Target.transform_cred`). *home_rel* is the path under the project shell home
(`".claude/settings.json"`), *host_rel* the path under the host home. *mtime_gate* is only
meaningful for `SYNC`; `filtered=True` is what makes the `transform_cred` hook run; `is_dir=True`
means a DIRECTORY — recursive copy, no mtime gate, no filter.

```python
@dataclass(frozen=True)
class PluginDescriptor:
    command: tuple[str, ...]
    bindings: tuple[Binding, ...]
    mode: dict[str, tuple[str, ...]]
    operations: dict[str, Operation] = field(default_factory=dict)
    access_realization: AccessRealization | None = None
    settings: tuple[SettingArg, ...] = ()
    persona: "PersonaSpec | None" = None
    cred_files: tuple[CredFileSpec, ...] = ()
    host_prep: bool = False
    init_dirs: tuple[str, ...] = ()
    auth_share_support: bool = False
    vscode_extension: str | None = None
```
The declarative data a plugin exposes via `Target.descriptor`; divergent LOGIC stays in hooks.

* *command* — the box argv prefix (`("claude",)`).
* *bindings* — ALL bound elements; ordered; at least one.
* *mode* — INTERACTIVE launch ONLY: `{"start", "continue"}`.
* *operations* — standalone ops, no mode.
* *persona* — harness-specific persona endpoint/token delivery; `None` = claude-style env +
  `ANTHROPIC_AUTH_TOKEN`.
* *host_prep* — `True` makes core call `Target.prepare_host` before mounts.
* *init_dirs* — extra dirs to `mkdir` in the project home (home-relative).
* *auth_share_support* — an **RO CAPABILITY** (spec §2d): does this agent SUPPORT shared
  credentials? Materialized as `meta.agent.<agent>.auth.share_support` (plugin-set).
* *vscode_extension* — the VS Code Marketplace extension id auto-installed into the box on attach
  (`kanibako code`); `None` = the agent ships no editor extension.

### How the descriptor maps onto the keyspace

It maps onto the per-agent keyspace (spec §2d), keyed by the `<agent>` DISCRIMINATOR — which for a
plugin's own declarations is its `name` property, the harness. ⚑ Not by the *value* of
`meta.agent.<agent>.name`: that is the store DIRNAME (the `+` spelling), and for a persona node it
differs from the discriminator. A few §2d keys are *informational* — they describe where CORE
derives a path, not a descriptor field:

* `agent.<agent>.path` (`@config.agents/<name>`, derived in core),
* `agent.<agent>.template` (the layer-2 seed source, owned by the templates layer),
* `agent.<agent>.transform` — it NAMES which binary transform runs; a plugin declares its value
  through `setting_descriptors`, so it is a behavior SETTING, not a descriptor field, and it is
  realized on no channel.

The `synced` category in §2d is the spec VIEW of `cred_files` (realized by the credsync engine);
`critical` is the set of `AGENT_CRITICAL` `bindings` keys.

### ⚑ THERE IS NO `container_env` FIELD — tombstone

A plugin's environment variables are **SETTINGS KEYS** (`agent.<agent>.env.<VAR>`,
`Target.default_envs`), declared at the defaults file's top-level `env:` section and delivered by
the launch's ONE settings channel.

A descriptor field would be a second, unoverridable route to the same box env, so
`agent_defaults.load_descriptor` refuses `container_env:` BY NAME — a plugin still declaring one
FAILS rather than silently losing its variables.

## The host-binary validator

```python
def _validate_agent_binary(binary: Path) -> str | None
```
Validate that *binary* is a usable host agent executable; return a short human-readable REASON when
it is unusable, or `None` when it looks fine.

The check runs on the HOST path (`AgentInstall.binary`) at launch time, BEFORE the container is
mounted/run.

⚑ **Deliberately LENIENT**, to avoid false positives on legitimate native binaries OR shebang
wrappers: it fails only when the path is missing, zero bytes (the documented 0-byte/corrupt-binary
incident), not marked executable, or begins with all-NUL bytes (a truncated/corrupt download).
**ELF magic is deliberately NOT required.**

The NUL check reads only a few bytes and never rejects on a read failure: a non-empty file whose
leading bytes are all NUL is neither a native binary nor a shebang wrapper.

## `Target` — the ABC

```python
class Target(ABC)
```
The abstract base class for agent targets. A target holds ALL agent-specific knowledge — detection,
declared defaults, credential lifecycle, persona delivery — so that kanibako's core stays
agent-agnostic.

⚑ **The per-METHOD launch hooks are GONE** (see `descriptor` below). Core assembles launch argv,
bindings, container env and credential sync DECLARATIVELY from `PluginDescriptor`; do not write
against `binary_mounts()`, `init_home()`, `build_cli_args()`, `resource_mappings()`,
`apply_state()`, `should_retry_new_session()` or `instruction_files()` — **none of them is
declared on `Target` or called by core.** Every surviving mention of those names in the tree is
itself a removal note
(`targets/assembly.py`, `targets/credsync.py`, the three plugins' module headers), and the
credential half of `init_home` was replaced by `credsync.seed_cred_files`.

### Identity and detection

```python
@property
@abstractmethod
def name(self) -> str

@property
@abstractmethod
def display_name(self) -> str

@abstractmethod
def detect(self) -> AgentInstall | None

@property
def has_binary(self) -> bool          # default True

@property
def descriptor(self) -> "PluginDescriptor | None"    # default None

def check_auth(self) -> bool          # default True
```

`name` is the short identifier (`'claude'`); `display_name` the human-readable one
(`'Claude Code'`); `detect` returns the host installation or `None` if it is not installed;
`has_binary` says whether this target requires a host-installed binary; `check_auth` returns
whether the agent is authenticated.

`descriptor` is the declarative plugin descriptor, `None` ONLY for the built-in no-agent shell.
Core assembles launch argv, bindings, container env and credential sync declaratively from it; the
legacy per-method launch hooks were removed for the public release. **Every shipped agent plugin
returns a descriptor.** The sole descriptor-less target is `no_agent.NoAgentTarget`, which launches
a plain shell with no agent argv and no delivery binds.

### Host preparation

```python
def prepare_host(self, install: "AgentInstall", *, auto_auth: bool, data_path: Path) -> None
```
Plugin-owned pre-launch host preparation. Default: no-op.

Called by core `start.py` once a host install is detected and BEFORE mounts are built, so the
plugin can own everything agent-specific that must touch the host FIRST (updating the host binary
to a stable version, refreshing host auth with the right environment). Core stays agent-agnostic:
it just invokes this hook.

⚑ **Implementations MUST NOT crash the launch** — a failure here should be logged and swallowed; a
hard auth/binary failure is surfaced separately via `check_auth` / `_validate_agent_binary`.

*install* is the detected `AgentInstall`; *auto_auth* says whether automated browser auth should be
attempted; *data_path* is the kanibako data dir, for auth cookie storage.

### The declared AGENT-level defaults

Four of these hooks (`default_common`, `default_seeds`, `default_envs`, `default_category_binds`)
are injected as the AGENT level's declared defaults (`default_categories`) in the category
resolver, so a user can OVERRIDE or SUPPRESS (terminal `""`) any entry at a more-specific level.
All four default to an empty table.

```python
def default_common(self) -> dict[str, BindArm]
```
Declare default AGENT-scope common/caches binds for this agent.

Maps a DISCRIMINATED TERMINAL category key (`agent.<agent>.common` / `agent.<agent>.caches`) to its
whole dest-keyed `BindArm` `{box_dest: (host_src[, options])}` — structured, **not** a colon-joined
string.

```python
def default_seeds(self) -> dict[str, BindArm]
```
Declare default copy-once-at-init seeds for this agent.

Maps the DISCRIMINATED TERMINAL seed key (`agent.<agent>.seeded`) to its whole dest-keyed
`BindArm`, exactly as `default_common`; a user can override or suppress (terminal `""` or the
"empty" sentinel) any entry. No target ships a seed yet.

⚑ A `seeded` dest is spelled GUEST-side like every other dest (spec §0 "ONE DEST SPACE, TWO
DELIVERIES") and RESOLVED to the box store when the copy runs. **It STAYS A COPY** — the shape it
shares with `bindings` says how the entry is WRITTEN DOWN, never what is done with it.

```python
def default_envs(self) -> dict[str, str]
```
Declare default AGENT-scope environment VARIABLES for this agent.

Maps a DISCRIMINATED `agent.<agent>.env.<VAR>` KEY to its scalar value, so the values reach the box
through the ONE settings channel: a user overrides one by writing the SAME key in a nearer file,
and the SAME variable named at a SECOND scope is a launch REFUSAL naming both keys
(`store_collapse.collapse_env` — the write-once arbitration is the collapse's, and there is none
here).

⚑ **This is the ONLY route a plugin has for a STATIC variable.** The launch folds the table into
its default-categories floor, RE-KEYED to the ACTIVE NODE so a persona sees its harness's
variables. A descriptor's ENV-channel `settings` / `access_realization` are a DIFFERENT job — they
REALIZE a resolved value per launch, they do not DECLARE one.

A plugin that ships its declarations in its `<agent>-defaults.yaml` `env:` section gets them from
`agent_defaults.load_envs`, which is what all three first-party plugins call.

```python
def rom_root(self) -> Path | None
```
Locate this plugin's packaged BIBLE CHAPTER root (`<pkg>/data/rom`), or `None`.

The source of the `box.bindings.ro.canon_bible_agent` bind (spec §2c), which CORE emits from the
resolved target — see `core_defaults.rom_agent_default_categories`.

The plugin's `data/rom` **IS** the chapter root: it ships `directives/ROM_AGENT.md`, not a deep
`canon/bible/agent/...` mirror, so CONTAINMENT holds BY CONSTRUCTION — a plugin cannot place a file
outside its own chapter, so there is nothing to guard and no silently-ignored out-of-chapter file.

⚑ The package is derived from `sys.modules[type(self).__module__].__package__` rather than from
`name`: `name` is the HARNESS name and only HAPPENS to match `kanibako.plugins.<name>` for the
first-party plugins, while `__package__` is correct whether the `Target` class lives in
`<pkg>/target.py` or in `<pkg>/__init__.py` (a naive `__module__.rsplit(".", 1)[0]` is wrong for
the latter).

Returns `None` on ANY failure — no such module entry, no `__package__`, an unimportable/absent
package, or no `data/rom` directory. Directory plugins
(`~/.local/share/kanibako/plugins/`, `{project}/box_data/plugins/`) are not `kanibako.plugins.*`
packages and simply resolve to `None`, which is the RIGHT answer for them.

```python
def default_category_binds(self) -> CategoryBindDefaults
```
Declare default AGENT-scope `@`-ref-sourced category binds.

Returns a UNIFORM table of DISCRIMINATED scoped category keys — *agent* is the declaring plugin's
own name, and the agent tier is ALWAYS discriminated (spec §2d / §0: there is no bare
`agent.<key>`). ONE shape, for every category:

every key is TERMINAL — `agent.<agent>.bindings.{ro,rw}` for an ARMED category,
`agent.<agent>.{caches,seeded,common,synced}` for the rest — and its whole VALUE is a `BindArm`,
i.e. `{box_dest: (meta_ref[, "ro"])}`. **The box DESTINATION is the KEY; there is no entry name.**

⚑ Each destination must be normalized with `settings_resolve.normalize_bind_dest`, because the
launch floor merge in `commands.start` DEDUPES on these keys BEFORE anything parses them, so an
un-normalized `~/x` and a `/home/agent/x` would collide at one mountpoint as two surviving entries.
`core_defaults.add_bind` does the normalizing and enforces act-once: one category map admits ONE
entry per destination.

⚑ **ONE SHAPE, TWO DELIVERIES.** `seeded` and `synced` are COPIES and stay copies — sharing the
dest-keyed shape says how an entry is WRITTEN DOWN, never what is done with it.

⚑ A plugin still returning a retired name-keyed `agent.<agent>.<category>.<name>` key is REFUSED BY
NAME at `settings_assemble._insert_dotted` when the launch floor is assembled, not silently
ignored. **There is no shim** (v1.8.0 is a clean break).

The HOST SOURCE stays a raw `@`-ref STRING; the launch category cascade folds this table into the
floor and `expand` resolves the ref, so a plugin declares a bind to a shared source with NO
per-harness path knowledge in core (spec §2d). A user can override or suppress (terminal `""`) any
of them at a more-specific level, **BY ITS DESTINATION**, since that is now the key.

A plugin owns its own harness-slot `box_dest` while an `@`-ref source keeps core agent-agnostic. ⚑ A
plugin's own bible chapter is **NOT** declared here: it is the INTERNAL `canon_bible_agent` bind
core emits from `rom_root`, kept out of the agent keyspace precisely so it stays UNREPOINTABLE like
the rest of the book.

A plugin that ships its declarations in its `<agent>-defaults.yaml` `category_binds:` section gets
the arm shape for free from `agent_defaults.load_category_binds`, which is what all three
first-party plugins call.

### Settings and state

```python
def setting_descriptors(self) -> list[TargetSetting]              # default []
def generate_agent_config(self) -> AgentConfig
```

`setting_descriptors` declares what runtime settings this target supports, as `TargetSetting`
entries.

`generate_agent_config` returns a default `AgentConfig` for this target; subclasses override to
provide agent-specific defaults (template variant, state knobs, shared caches).

There is no per-method state hook. `apply_state` — which translated agent-state values into
`(cli_args, env_vars)` — was retired with the other legacy launch hooks: a state key reaches the
box as the `SettingArg` the plugin declares for it in its `<agent>-defaults.yaml`, resolved by
`kanibako.targets.assembly`. Declaring the route is what a plugin does; translating it is not.

### The launch seams

```python
@property
def default_entrypoint(self) -> str | None      # default None = use bash

def has_resumable_session(self, home: Path) -> bool       # default True
def should_run_setup(self, output: str) -> bool           # default False

@property
def setup_entrypoint(self) -> str | None        # default None
@property
def setup_args(self) -> list[str]               # default []

@property
def config_dir_name(self) -> str                # default ".{name}"

def credential_check_path(self, home: Path) -> Path | None    # default None
```

`default_entrypoint` is the binary name for the container entrypoint. `config_dir_name` is the
agent config dir relative to home (`'.claude'`). `credential_check_path` is the path to check for
credential existence.

#### `has_resumable_session` — the continue-vs-fresh guard

Reports whether this agent has a session to resume under *home*.

*home* is the box home directory as seen from the HOST (the home bind source — the same seam
`credential_check_path` and the credsync hooks receive as `proj.shell_path`). `start.py` consults
this at the continue-vs-new seam: when the DEFAULT continue mode was selected but the target
POSITIVELY reports nothing to resume, it builds the new-session command directly instead of
ATTEMPTING a doomed resume, whose fast-dying container races the attach path into a raw runtime
error.

⚑ **This is now the SOLE continue-vs-fresh guard**, the launch-time crash-and-retry net having been
removed with the dead-pane dependency it relied on, so a target with a doomable resume MUST
override it to positively detect an empty store.

Implementations read HOST-side state only — no container exec. ⚑ **Return `False` only on a
POSITIVE determination that no resumable session exists: a wrong `False` silently drops a real
conversation.** The `True` default keeps the always-attempt-continue behavior byte-identical for an
agent that does not override.

#### `should_run_setup` — the launch is ground truth

Checks whether a launched session's output PROVES the config did NOT take.

The LAUNCH is ground truth for a bootable config: a clean host-side `check_auth` probe, and even a
clean in-box setup exit code, cannot guarantee the agent will actually start (the partial-config
case). After the in-box setup has run and the real session has launched, `start.py` matches this
against the captured session logs; a match means the agent reported it is still not configured
(e.g. goose's *"Goose is not configured. Run 'goose configure' to set up."*), and `start.py`
surfaces a clear error and returns.

⚑ **BOUNDED:** setup already ran ONCE this invocation, so a post-launch match only ERRORS — it
never loops back into setup. The `False` default is claude, which has no setup step.

#### `setup_entrypoint` / `setup_args` — the one-time interactive setup

`None` (the default) means the target declares NO setup step; the auth-probe setup branch in
`start.py` and in `agent reauth` is SKIPPED ENTIRELY, and a failed `check_auth` errors out.

A target that needs an in-box setup (goose → `goose configure`) returns its setup binary here and
the sub-command in `setup_args`. When `check_auth` fails for such a target, `start.py` runs the
command INTERACTIVELY in the box, inheriting stdio so the user can complete configuration in-box,
then proceeds with the normal launch. Setup runs in box-state, which persists across reattach.

### The native-config delivery seams

```python
def deliver_panel_permissions(self, *, config_root: Path, access: str) -> bool      # default False

def deliver_directive_hook(
    self, *, config_root: Path, access: str,
    model_provider: "CodexModelProvider | None" = None,
) -> bool                                                                            # default False

def reattach_config_notice(self) -> str | None                                       # default None
```

All three are **best-effort**: the caller wraps the call, so a failure never blocks the launch —
but implementations should still be merge-preserving and idempotent. The two `deliver_*` hooks
return whether a write occurred.

#### `deliver_panel_permissions`

Persists the box's resolved `access` TIER onto this agent's panel-visible config.

*config_root* is the box home as seen from the HOST (`proj.shell_path`); the launch call site
passes it UNCONDITIONALLY for EVERY agent, and each implementation appends its own config surface
beneath it (claude `.claude/settings.json`, goose `.config/goose/config.yaml`, codex
`.codex/config.toml`).

The VS Code panel spawns its OWN in-box agent WITHOUT kanibako's launch env, so the box's
configured permission tier must be PERSISTED onto the agent's native config surface to reach it.
⚑ This hook is that delivery, keyed on the CASCADE-resolved `access` and **never** on the
per-launch `-S`/`-A` flags — spec §1A's projected-surface exception, because the projection
OUTLIVES the launch.

*access* is one of `restricted` / `editing` / `full`. ⚑ **An implementation MUST render every tier
explicitly and MUST NOT fall through to the permissive arm for a tier it does not recognise;** the
launch has already refused a tier this agent's descriptor cannot render, so an unexpected value
here is a BUG and should raise.

The `False` default is inherited by an agent with no panel-permission surface.

#### `deliver_directive_hook`

Seeds this agent's instruction-delivery `SessionStart` hook, plus any coupled managed config, onto
its NATIVE config surface under *config_root* (as `deliver_panel_permissions`).

⚑ Box-side literals an implementation needs (codex's in-box config path and cwd for its trust keys)
are derived by the PLUGIN from the core `settings_resolve.GUEST_HOME` constant — deliberately NOT
seam parameters while they are constants with a single consumer. **If the in-box workdir ever
becomes key-configurable, promote an agent-agnostic `box_workdir` parameter here** instead of
letting plugins drift.

*model_provider* is the launch's resolved persona model-provider bundle, `None` for bare /
non-persona launches — the write must then be BYTE-IDENTICAL to a provider-less one. Today only
codex consumes it; the type generalizes when the emitter bodies move into the plugins.

The `False` default is inherited by an agent with no directive-hook surface (goose, the no-agent
shell).

#### `reattach_config_notice`

A heads-up to print when REATTACHING to an ALREADY-RUNNING box.

The launch-time delivery seams above re-materialise this agent's native config surface only on
(re)start of a STOPPED box; a reattach to a LIVE box early-returns and does NOT re-deliver, because
**it is unsafe to rewrite config under an app-server that already read it.**

So an agent whose config is a RECONCILED PROJECTION (codex's `config.toml`
model/provider/approval) returns a one-line notice that config changes will not take effect until
the box is restarted, and core prints it on the reattach path, never rewriting the live file.
`None` (the default) is no notice.

### The persona seams

```python
def read_persona_settings(self, config_dir: Path) -> PersonaReadOutcome

def verify_persona(
    self, endpoint: str, token_path: Path | None, model: str | None,
    *, env: Mapping[str, str] | None = None, timeout: float = 5.0,
) -> PersonaProbeOutcome
```

#### `read_persona_settings`

Extracts persona values from a rendered harness config in *config_dir*.

*config_dir* is a persona-grata store entry's harness dir
(`$XDG_CONFIG_HOME/personas/<pid>/<hid>/`) holding this harness's NATIVE config file. A plugin that
models the store's rendering parses it into a `PersonaSettings`, and the auto-import maps that onto
the agent keyspace.

⚑ **FAIL-SOFT contract:** an absent, unreadable, malformed or missing-required-keys config NEVER
raises; it returns an outcome carrying the SPECIFIC reject reason rather than a bare `None`, so the
caller can report WHY instead of guessing (see `PersonaReadOutcome`). **Pure read: never writes,
never reads the token file.**

The default `PersonaReadOutcome(None, None)` means this harness has no persona reader
(goose/no_agent), which is **NOT** a reject.

#### `verify_persona`

Probes *endpoint*, credential-authed with the token at *token_path* — a minimal real ack.

The persona verify probe (DESIGN §3b): a FEW-token genuine completion round-trip against the
persona's endpoint, specific to the harness API (anthropic messages vs OpenAI responses wire).
Returns a `PersonaProbeOutcome`, whose four arms separate a probe that RAN and could not decide
(`INCONCLUSIVE`) from one that learned nothing about the token and never will for this input
(`NOT_APPLICABLE`, carrying the named cause).

⚑ ***token_path* MAY be `None`** (2026-08-17 ruling): a persona whose `secret_path` key is
PRESENT-null declares itself deliberately KEYLESS, and an implementation must still PROBE it, with
the `Authorization` header OMITTED rather than decline — the request is sent bare and the SERVER
decides (a 2xx confirms the endpoint really is keyless; a 401/403 is a genuine, useful `REJECTED` —
the user's belief was wrong). ⚑ **Never substitute a placeholder/dummy credential to fill the
header:** a hardwired-auth server can reject one it does not serve, producing a false `REJECTED`
that refuses a working box — the same rule *model* already follows.

⚑ ***model* MAY be `None`:** a persona that names none is valid, and an implementation must probe
it with the `model` field OMITTED rather than decline — never with a placeholder or default id (see
`probe_outcome_no_model`).

⚑ ***env* is the PERSONA-STORE passthrough env block** — the store entry's own `env:` map, less
the two single-source vars (`PersonaBundle.env`). A harness whose RUNTIME rewrites the model from
one of them MUST apply that rewrite here, or the probe asks the provider a question the box never
asks. **MEASURED, 2026-08-30:** a persona resolving `model: sonnet` was probed with `sonnet` on the
wire, the endpoint answered 403 *"team not allowed to access model"*, and the launch was refused on
a VALID token — in the box, Claude Code reads that alias through `ANTHROPIC_DEFAULT_SONNET_MODEL`
and sends `gemma-4-31b-it`, which the same endpoint serves. Claude implements this in `_wire_model`;
codex names its model in `config.toml` and sends it verbatim, so it accepts *env* and ignores it.

🛑 **IT IS NOT THE COLLAPSED `env` FAMILY, AND THE DIFFERENCE IS AN OPEN DEFECT, NOT A NUANCE.** The
variables a box actually receives are the arbitrated `meta.assembly.env` slots, folded by
`store_collapse.collapse_env` inside `_install_assembly_collapse` — *below* its `if not whole_box`
gate, so ONLY the whole-box resolve (`commands.start._resolve_launch_snapshot`) produces them, and
on the launch path that resolve runs long AFTER the pre-flight that probes. What the pre-flight has
already resolved (`_resolve_box_launch_decisions`) is a `build_launch_snapshot` carrying the raw
per-scope cascade and the persona tier — no collapse, no base families, no realized env, no `-e`.
So a mapping written at the agent-file rung (`kanibako agent set <a> env.<VAR>=…` — the DOCUMENTED
route), at the workset or box rung, or on a keyspace-only persona that has no store block at all,
**does not reach this argument**: the alias goes out raw, the endpoint answers 403, and the launch
hard-errors on a box that would have run. Closing it needs the collapse before box materialization,
which is a design change, not a wiring.

⚑ **Resolving a mapping the USER wrote is not the substitution the rule above forbids; inventing
one is.** A model with no such var goes out AS GIVEN, and `None` stays `None`.

The OUTCOME is all this reports; what to do with it belongs to the CALLER, and the two callers
deliberately answer it DIFFERENTLY. The per-LAUNCH probe treats `REJECTED` as a HARD ERROR, because
a token the provider rejects cannot work and saying so beats an in-box 401, while the CREATE-path
probe is WARN-ONLY on both answered-but-not-`PASS` verdicts so a fixable token never blocks a
create. BOTH are SILENT on `NOT_APPLICABLE`: it names a valid configuration the user cannot act on.

⚑ **Contract: NEVER raises; SHORT *timeout*, because a launch must not hang on a blip; the token
value is read TRANSIENTLY for the request only — never logged, never persisted, never returned.**
The default `NOT_APPLICABLE` means this harness has no probe.

### The credential seams

```python
def invalidate_credentials(self, home: Path) -> None

def transform_cred(self, spec: CredFileSpec, src: Path | None, dst: Path, direction: str) -> None

def refresh_credentials(self, home: Path) -> None
def writeback_credentials(self, home: Path) -> None
def writeback_extra(self, *, project_home: Path, host_home: Path) -> None
```

`invalidate_credentials` removes credential files when switching to distinct auth. Default: no-op.

#### `transform_cred`

Transforms a FILTERED credential/config file between host and project.

Called by the credential-sync engine **ONLY** for specs with `filtered=True`. *direction* is
`"in"` (host→project: seed/refresh) or `"out"` (project→host: writeback). *src* is `None` when no
source file is available (distinct auth, or the host file absent at seed time) — the plugin decides
whether to write a default *dst* or do nothing.

The default is a plain copy when *src* exists, so a plugin that flags a file `filtered` but does
not override still gets a sensible wholesale copy. Plugins override to filter/merge (claude
`claudeAiOauth` merge + `.claude.json` allowlist; goose `config.yaml` allowlist).

#### `refresh_credentials` / `writeback_credentials`

Refresh agent credentials from host into the project home, and write them back. Both default to
no-op: descriptor-native plugins sync creds via `descriptor.cred_files` (core's credsync engine);
legacy plugins override.

#### `writeback_extra`

Plugin-specific post-session writeback BEYOND `cred_files` specs. Default: no-op.

Called by core on EVERY session-end path (clean exit, detach, reattach-exit, `kanibako stop`) AFTER
the descriptor `cred_files` writeback, for state that CANNOT be modelled as a `SYNC`
`CredFileSpec`. The motivating case is claude's `~/.claude.json` `oauthAccount`: the box's login
writes the account block there and it must reach the host, but the file cannot be a normal `SYNC`
spec, because that would also IMPORT host→project and a wholesale copy would clobber host-specific
`machineID` / `userID` / `projects`. So the plugin MERGES just its own keys back.

⚑ **MUST be defensive — never raise on a malformed or absent file;** core wraps it, but a clean
teardown is the contract.
