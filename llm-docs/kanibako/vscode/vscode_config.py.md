# In-Box Agent Config Writers (the surfaces a VS Code panel actually reads)

Every agent kanibako launches has **two** faces: the CLI process kanibako spawns itself, which
gets its behaviour from argv flags and env; and the **panel** — the agent's own VS Code
extension, which spawns a *second* agent process inside the box and sees **none** of kanibako's
launch flags or launch env. Anything the box was configured with that must reach the panel has to
be **written to disk, in-box**, before the panel starts. That is what this module is: the
host-side writers for those on-disk surfaces, one per agent, plus the VS Code attach config that
gets the panel installed in the first place.

The box home is a bind mount, so "in-box `~/.claude/settings.json`" is a host path here and a box
path to the agent. Writing it from the host is how a per-box value gets in.

⚑ **These files are NOT ours.** Every one of them — the VS Code image config, the claude
`settings.json`, the codex `config.toml`, the goose `config.yaml` — is a file a user or another
program also writes. So every writer in this module is a **read-modify-write union-merge or a
surgical line edit**, never a create-and-clobber, and every one is **idempotent**: re-running it
over its own output changes nothing and skips the write.

## Attached container configuration (the `kanibako code` seed)

VS Code's Dev Containers extension reads an *attached container configuration* — a
devcontainer.json subset — when you attach to a running container. `kanibako code` seeds it so
that on attach VS Code opens the box's workspace folder and auto-installs the box agent's
extension (e.g. `anthropic.claude-code`, which is what makes claude's `/ide` integration work
in-box).

CONFIRMED by the Phase-0 VS Code test: the config is **IMAGE-keyed, not box-keyed**:

    <config_home>/Code/User/globalStorage/
        ms-vscode-remote.remote-containers/imageConfigs/<ENC>.json

`<ENC>` is the image reference percent-encoded (`/` → `%2f`, `:` → `%3a`) with **LOWERCASE** hex
escapes: VS Code uses an `encodeURIComponent`-style encoder and then lowercases, so
`ghcr.io/doctorjei/kanibako-oci:latest` becomes the file
`ghcr.io%2fdoctorjei%2fkanibako-oci%3alatest.json`. Python's `urllib.parse.quote` emits UPPERCASE
escapes, hence the `.lower()` in `_encode_image_ref` — that call is the entire content of the
function and must not be dropped.

⚑ UNVERIFIED, and left as an assumption on purpose: for a ref with an UPPERCASE **tag** we assume
VS Code does `encodeURIComponent(x).toLowerCase()` — whole-string lowercase. OCI image NAMES are
already lowercase per spec, so only a tag's case can differ, and whole-string lowercase is
identical to escape-only lowercasing for any all-lowercase ref. It is the simpler and more common
encoder idiom, so it is the safer default. A Phase-0 uppercase-tag confirm would settle it.

Because the file is image-SHARED (one file per image, used by every box on that image) and VS Code
**owns** it — it reads and *accumulates* `extensions` as the user installs more — the seed is a
union-merge, never a create-if-absent:

* `extensions` — this box's agent extension is added iff absent (dedup, existing order preserved,
  never removed). When the caller passes no extension the array is left entirely alone and no key
  is created.
* `workspaceFolder` — set only if the key is ABSENT; VS Code's own value or the user's is never
  clobbered. The `kanibako code` launcher passes an explicit `--folder-uri` anyway, so this is
  only a fallback default.
* every other key and every other extension already present is preserved.

`remoteUser` is deliberately NOT added. VS Code omits it and infers the container user; the schema
VS Code itself writes is exactly `extensions` (a JSON array) plus `workspaceFolder`, and matching
that schema exactly is the point.

`_read_existing_config` never raises: absent, unreadable, invalid JSON, or valid JSON that is not
an object all collapse to `{}`. VS Code owns the file and may have written anything into it.

## JSONC

VS Code's `settings.json` is JSONC — it permits `//` and `/* */` comments and trailing commas,
none of which `json.loads` accepts. `load_jsonc` tries strict JSON first (the common case: VS Code
writes valid JSON when the settings UI does the editing) and falls back to `_strip_jsonc`.

`_strip_jsonc` is a light best-effort pass, NOT a JSONC parser: block comments, then **whole-line**
`//` comments only, then trailing commas before `}`/`]`.

⚑ The whole-line restriction is deliberate and is a **string-safety** decision. A trailing inline
comment (`"...": "podman" // note`) is NOT stripped, because reliably telling a real comment from a
`//` inside a string value — `"http://..."` is the obvious case — needs a real tokenizer, and a
wrong guess **corrupts a string value**. A hand-edited file like that therefore fails to parse and
degrades to `None` in `load_jsonc`, which is a visible non-answer rather than a silent false read.

This is the shared JSONC reader for the tree: `commands/diagnose.py` and `commands/code_cmd.py`
both import it.

## Access tiers → three different panel surfaces

kanibako's per-agent `access` tier (`restricted` | `editing` | `full`, ordered least → most
permissive in `settings_keyspace.ACCESS_TIERS`) reaches each CLI agent through its argv flag row.
None of that reaches the panel. Each agent therefore gets a projection written into a per-box file,
and the tier→value knowledge for all three lives **here**, beside its siblings, rather than in
each plugin's branch — one place decides what a tier means for this surface (R-41).

⚑ The projection is driven by the box's **CASCADE-resolved** `access`, never by the ephemeral
`-S`/`-A` launch flags. A projection outlives the launch that wrote it (spec §1A), so seeding it
from a one-shot flag would leave the next launch running at a tier nobody asked for.

| tier | claude `permissions.defaultMode` | codex `approval_policy` | goose `GOOSE_MODE` |
|---|---|---|---|
| `full` | `bypassPermissions` | `never` | `auto` |
| `editing` | `acceptEdits` | `on-request` | REFUSED |
| `restricted` | CLEARED | `untrusted` | `approve` |

An **unknown** tier raises in every arm. This surface must never fall through to the permissive
value, and "clear it" is an equally wrong guess for a tier we do not understand.

### Why only claude clears

claude is the one agent whose unset default is safe: with no `permissions.defaultMode`, claude
**prompts**. So `restricted` can be delivered by removing our value.

Neither of the others can. goose's unset `GOOSE_MODE` default is `auto` — permissive — so a
`restricted` goose box must persist `approve` **explicitly**; clearing would silently restore
permissive. codex used to clear too, and that was a promise kanibako could not keep: clearing left
codex at its **own** default approval policy, a value not documented anywhere in 0.141.0's local
help and therefore unknown to us, while the same function was forcing `sandbox_mode` to
`danger-full-access`. "Restricted" would have meant *unknown approval behaviour with full disk
access* — and on a file already carrying `on-request` it may well have been byte-identical to what
`editing` delivers. Writing `untrusted`, the most guarded member of the verified enum, makes the
tier mean something kanibako controls.

The consequence is deliberate: kanibako **owns** `approval_policy` and `sandbox_mode` outright. A
user-chosen value in either is overwritten at every tier, not preserved. The tier the box was
configured with is the tier the panel gets.

### The claude clear is narrow

`clear_permission_mode` removes `permissions.defaultMode` **only** when its current value is one
kanibako manages (`_MANAGED_MODES` — every value in the tier table). A user-chosen mode we never
write (`plan`, `default`, `dontAsk`, `auto`) survives untouched, and an absent key is a no-op.
Because the guard is the SET of managed values rather than one value, switching a box from
`editing` to `restricted` removes the `acceptEdits` **we** wrote instead of leaving a stale middle
tier behind. Sibling `permissions.allow`/`deny` and every other top-level key are preserved; if
removing `defaultMode` empties the `permissions` object entirely then that object was ours to
create and it goes, while one that still holds other keys stays.

`seed_claude_permission_mode` is a no-op when the file is absent and the tier is `restricted` —
there is nothing to clear, and it never creates a file just to clear it.

### goose refuses `editing` rather than approximating it

No `GOOSE_MODE` value realizes the middle tier. `smart_approve` auto-approves READ-ONLY tools and
prompts for writes, which is the *inverse* of `acceptEdits`. Because this surface is PERSISTED, a
silent substitution would outlive the launch that made it.

⚑ The raise inside `seed_goose_mode` is the **second** fence, not the gate. Callers wrap panel
delivery best-effort, so this raise on its own would be swallowed. The real refusal happens at
`targets.assembly.access_row`, which stops the launch and names the tiers the agent can render
before any delivery runs. The check here exists so the function is honest in isolation.

The error message lists legal tiers in `ACCESS_TIERS` order (least → most permissive). A `sorted()`
would print "full | restricted" and read as a ladder running the wrong way.

⚑ `seed_goose_mode` writes through the `Path` object rather than `config_io.dump_doc`, matching its
claude/codex siblings. `dump_doc`'s `atomic_write_text` coerces the path via `Path()`/`mkstemp` and
performs a **real** `mkdir`, which under a mocked `proj.shell_path` materializes a stray on-disk
directory. The siblings stay mock-safe by writing via the Path object. A best-effort re-seed is
idempotent, so the non-atomic parity is fine.

## Instruction delivery: the SessionStart hook

The kickoff SEED (`~/.config/kanibako/kickoff.md`) is a single `@import` chain. The flattener
resolves it and, in `--additional-context` mode, prints a SessionStart hook payload whose
`hookSpecificOutput.additionalContext` is the flattened text. A `SessionStart` hook runs the
flattener and injects that as context.

The hook is the delivery mechanism **because a file rewrite would be too late**: claude reads its
memory file BEFORE hooks fire.

The flattener is RO-delivered by the `kani_pkg` bind at
`/opt/kanibako/kanibako/scripts/import-directives.py`. It is machinery, so it ships in the package
and not in the canon. ⚑ That absolute path literal is carried a **second** time by
`start._directive_flatten_shim` (`commands/start.py`), and the two must move together.

The command is silent-safe (`|| true`): a missing seed or a flattener error never aborts the
session. `$KANIBAKO_DIRECTIVE_SEED` expands at hook-run time in-box; the flattener path is an
absolute package-bind path, so no `$HOME` appears in it.

Both agents nest a `SessionStart` group of `{matcher, hooks:[{type:"command", command}]}`, and both
schemas accept the OR-pattern matcher `startup|resume|clear|compact` (verified against
code.claude.com/docs/en/hooks.md and learn.chatgpt.com/docs/hooks). The **surfaces** differ:

* claude → `~/.claude/settings.json`, JSON `hooks.SessionStart` (`seed_session_start_hook`).
* codex → `~/.codex/config.toml`, an INLINE `[hooks]` table in config.toml — **not** a separate
  `hooks.json`. That path was openai/codex#17532 speculation and is wrong for 0.141.0. codex also
  gates a config-defined hook behind a content-hash trust, so `seed_codex_config` writes the hook
  group, the pre-computed `trusted_hash`, and the directory trust together, which is what makes the
  FIRST launch fire the hook with no interactive `/hooks` prompt.

`_merge_managed_command_hook` is the shared idempotent-append primitive behind every claude JSON
hook kanibako seeds. ⚑ Its idempotency keys on the **command string, not the matcher**, and that is
load-bearing: it keeps each managed command in its OWN independent group, so the marker-write hook
and the instruction-delivery hook coexist under `SessionStart` without either one's idempotency
check swallowing the other. Existing groups, other event keys, and every other top-level key are
preserved; a group with no matcher omits the key entirely rather than writing a null.

## Agent liveness markers (per-PID)

`box_supervisor` READS a box-local markers DIRECTORY to enumerate which agent sessions are live —
that feeds panel-watch's dead-panel detection and newcomer detection. This module is the WRITE
side, and it is delivered per agent:

* **claude** — a `SessionStart` hook writes the marker file `<dir>/$PPID`, and a `SessionEnd` hook
  removes it on a clean exit, so a clean shutdown clears its own marker. Both are managed as their
  own groups, idempotent and user-hook-preserving, exactly like the directive hook.
* **codex** — the SAME marker-write command, delivered as the second managed
  `[[hooks.SessionStart]]` group in the config.toml region. There is no remove side (below).
* **goose** — no marker delivery; there is no panel-liveness surface for it yet.

⚑ **Per-PID, not a single `agent.pid` file.** The FILENAME is the PID; the content is `$PPID` too,
purely for debuggability, since readers key on the filename. The old single-path scheme was
last-writer-wins, so a CLI incumbent and a VS Code panel newcomer sharing one path could not both
be held. A directory of per-PID files enumerates the FULL set of live agent PIDs, which the
supervisor prunes-dead via `kill -0` in order to detect a newcomer — a live marker PID that is not
its own agent.

⚑ `AGENT_MARKERS_DIR` is the **single source of truth** for both ends of the contract. It is
defined in this (low-level) module and imported by `commands/start.py` for BOTH the supervisor's
`--agent-markers-dir` (read end) and the `KANIBAKO_AGENT_MARKERS_DIR` env it seeds (write end), so
the two ends cannot desync. The hook command prefers the seeded env
(`${KANIBAKO_AGENT_MARKERS_DIR:-...}`) and falls back to the same literal built from the constant,
so it still works where a `podman exec` panel agent does not inherit the podman-set env.

⚑ It MUST stay a LITERAL box-local path, byte-identical on both ends: podman sets the env verbatim
and the supervisor reads `--agent-markers-dir` verbatim, so a shell expression like
`${XDG_RUNTIME_DIR:-/tmp}` would resolve only in a shell context and would otherwise land as a
literal `${...}` path — the two ends would then disagree. `/tmp` is a box-local tmpfs and the
markers are tiny, so it is a safe universal home. The dir is created by the write hook; the reader
treats an absent dir as "no agents yet".

⚑ **VALIDATION-PENDING — do not claim these hold; check at the bifrost e2e.**
1. That `$PPID` inside a claude SessionStart `command` hook equals the claude agent PID. A
   `type:command` hook is spawned by claude, so `$PPID` is plausibly claude, but this is
   undocumented and unverified. If it is wrong, swap the write command for a `/proc` scan.
2. That the VS Code panel claude executes the box's seeded `~/.claude/settings.json` hooks at all.
   Likely, but only checkable on a real claude-in-podman box.

## The codex `config.toml` manager

codex-cli 0.141.0 fires a `[hooks.SessionStart]` hook defined inline in `~/.codex/config.toml` and
injects its additionalContext in-session — the same delivery as claude, a different file and
format. It gates the hook behind a content-hash trust (`[hooks.state]`) PLUS a directory trust
(`[projects."<cwd>"] trust_level`); pre-seeding both is what avoids the interactive prompt.

### Surgical, never a round-trip

kanibako ships stdlib-only (argcomplete/PyYAML/packaging), so there is **no tomlkit dependency**.
`tomllib` is read-only and cannot round-trip comments, and re-serialising an arbitrary user config
through a hand-rolled emitter risks corrupting exotic TOML — multiline strings, datetimes, floats.
So the manager edits ONLY kanibako-managed lines and leaves every other byte of the user's file,
all comments and all data, untouched. Two mechanisms:

* **Regions.** The hook group, trust hash and directory trust live in one comment-delimited
  managed region regenerated at the file's end. The model-provider table lives in a **second**
  region with DISTINCT markers, so the two strip and regenerate independently and can coexist.
* **Root-key line surgery.** The top-level scalar keys are reconciled in place in the ROOT section.
  ⚑ They cannot live in a trailing region: a bare key after a `[table]` header would **bind to that
  table**. So they are edited where top-level keys legally belong — before the first table header.

Only the root section is ever considered, so a same-named key inside a user table (a
`[profiles.x]` override, `[profiles.x].model`) is never touched.

### Two writers, split along the Target seams

No managed key is ever written by both:

* `seed_codex_config` (`CodexTarget.deliver_directive_hook`) — hook group, trust hash, directory
  trust, and the persona model-provider region. Everything region-shaped.
* `seed_codex_approval` (`CodexTarget.deliver_panel_permissions`) — ONLY the top-level
  `approval_policy`/`sandbox_mode` parity keys.

Both share the same primitives — regions, root-key surgery, and `_assemble_codex_managed` — so
their composed output is byte-stable. ⚑ `_assemble_codex_managed` is the single source of truth for
the separator and trailing-newline bytes between body and regions, which is precisely why the two
writers can never drift on assembly bytes. It expects a body already rstripped of trailing
newlines.

⚑ `_extract_delimited_region` exists in an EXTRACT form, rather than only a strip form, for
`seed_codex_approval`: that writer must edit the root-section keys while carrying the managed
regions through VERBATIM. Root-key surgery inserts before the first `[table]` line, and in a
region-bearing file with no user tables **that first table is the managed region's own** — a naive
in-place insert would land INSIDE the region and be swallowed by the next regeneration. A malformed
region missing its END marker extends to end-of-file.

### `sandbox_mode` is a box invariant, not a tier value

A kanibako box is a hardened rootless podman container: the container **is** the security boundary.
The codex `openai.chatgpt` panel's own in-box `codex app-server` reads this key and, unlike the
kanibako-launched CLI codex, gets no `--dangerously-bypass-approvals-and-sandbox` flag, so it must
run with sandboxing OFF.

⚑ `workspace-write` makes the app-server attempt a **nested bubblewrap sandbox**, which needs
nested user namespaces podman blocks; it stalls on sandbox setup with "could not find bubblewrap".
kanibako therefore owns the key and forces `"danger-full-access"` — always present, independent of
`access`, never removed by the restricted path, and any prior value (the old managed
`"workspace-write"`, or a user-chosen one that would re-break the panel) is MIGRATED to it. This is
parity with the CLI's unconditional bypass.

⚑ This is also **why the panel's middle tier rides the APPROVAL axis and not `sandbox_mode`**: the
CLI's `editing` row uses `-s workspace-write`, but writing that same value *here* is the exact
configuration that hangs the panel's app-server. Two surfaces, one tier, different realizations —
each verified against the surface it is written to.

### The approval enum

`_CODEX_APPROVAL_BY_TIER` values are exact members of the codex approval enum, verified verbatim
from `codex --help` (codex-cli 0.141.0):

* `untrusted` — "Only run \"trusted\" commands (e.g. ls, cat, sed) without asking for user
  approval. Will escalate to the user if the model proposes a command that is not in the
  \"trusted\" set" → the `restricted` tier
* `on-request` — "The model decides when to ask the user for approval" → the `editing` tier
* `never` — "Never ask for user approval Execution failures are immediately returned to the model"
  → the `full` tier

Every tier has an entry — the table is TOTAL over `ACCESS_TIERS`, which is what makes "kanibako owns
this key" true rather than aspirational. `on-failure` is the enum's fourth member and is
deliberately unused: its own help text marks it DEPRECATED.

⚑ FLAG FOR THE LIVE MATRIX. `never`↔`full` is the pre-existing, shipped pairing.
`on-request`↔`editing` and `untrusted`↔`restricted` are judgments read off the verified enum text.
The **vocabulary** is verified; the tiers' *semantics on the panel* are not separately verified and
must be confirmed by the bifrost matrix.

### Ordering is load-bearing

`_reconcile_codex_approval` emits `sandbox_mode` FIRST, before the tier-gated `approval_policy`.
Emitting the always-present invariant first makes the freshly-inserted order canonical: because
`_apply_root_key` inserts at the first-table index, the second insert lands *after* the first, so a
fresh file always gets `sandbox_mode` above `approval_policy`.

### `_apply_root_key`'s three modes

*desired* is the value this launch wants — SET it, in place if a root-section line exists, else
inserted before the first table header — or `None` to CLEAR the line.

* **SET** — used by both approval keys and by the provider keys.
* **CLEAR, guarded** — removes the line only when the current value is one of *managed*, the values
  kanibako itself may have written, so a user-chosen value survives (symmetric with
  `clear_permission_mode`) while a stale value from a DIFFERENT tier of ours is correctly removed.
* **CLEAR, `unconditional`** — removes the line regardless of value. That is for keys kanibako owns
  OUTRIGHT as a derived projection rather than a hand-edit surface: the codex
  `model`/`model_provider` wipe when a box goes bare. *managed* is irrelevant and unused there.

⚑ The guarded CLEAR branch is currently **unreachable in production**: both managed approval keys
are SET at every tier, and the only CLEAR caller passes `unconditional=True`.
`_CODEX_MANAGED_APPROVALS` is passed for symmetry and stands as the single record of the values
kanibako owns; nothing consults it today.

### The trust hash

`codex_trusted_hash` reproduces codex's own `command_hook_hash` (codex-rs/hooks discovery +
config/fingerprint): a canonical, key-sorted, whitespace-free JSON encoding of the hook IDENTITY,
SHA-256'd and prefixed `sha256:`. The identity is a WIRE FORMAT:

    {"event_name": <event_key>,
     "matcher": <matcher>,                       # key OMITTED when None
     "hooks": [{"type": "command",
                "command": <RAW command, pre-${ENV} expansion>,
                "timeout": <timeout_sec, default 600>,
                "async": false}]}

⚑ `command` is the RAW string BEFORE any `${ENV}` expansion — codex hashes the config **text**, not
the expanded command — and `timeout` normalises to the 600 s default. Pinned to a real-oracle
vector in the tests.

`_CODEX_EVENT_KEY` is codex's INTERNAL snake_case event id (`session_start`), used both in the trust
state-table key `<cfg>:<event>:<group>:<handler>` and in the hash identity's `event_name`. The
config.toml table key is PascalCase (`[hooks.SessionStart]`); codex maps it to the event id
internally.

### The managed hook region

`_build_codex_managed_region` holds TWO `[[hooks.SessionStart]]` groups, each with its own
pre-computed `[hooks.state]` trusted hash, plus the `[projects."<codex_cwd>"] trust_level =
"trusted"` directory trust:

* group *group_index* — the instruction-delivery hook, exactly as claude's;
* group *group_index*+1 — the per-PID liveness marker write, reusing claude's
  `_AGENT_MARKER_WRITE_COMMAND` VERBATIM (one source of truth for the command bytes on both
  agents).

⚑ TWO SINGLE-HANDLER groups, deliberately NOT one two-handler group: the trust hash is oracle-pinned
for the `{event_name, matcher, hooks:[ONE command]}` identity, and a multi-handler group's
per-handler hash shape is unverified upstream.

⚑ **codex has NO SessionEnd/exit hook event.** `HookEventName` was verified identical at
`rust-v0.141.0` and 0.144.x — SessionStart/Stop/… where `Stop` is per-TURN, not process exit. So
there is no marker-REMOVE hook, and a cleanly-exited codex leaves a stale marker whose PID
`box_supervisor`'s `kill -0` scan classifies dead. That is the intended semantics: the panel's
sessions are threads inside one long-lived `codex app-server` process, so marker-PID death is
effectively "the panel process itself is gone".

⚑ `box_config_path` is the BOX-absolute config path (`/home/agent/.codex/config.toml`), NOT the host
write path — codex keys trust by the path it reads in-box. `group_index` is the count of USER
`[[hooks.SessionStart]]` groups, since the managed groups are appended after them.
`_count_session_start_groups` is text-based rather than tomllib-based so a corrupt user file still
yields a usable index (0 for the empty template, the common case, matching the oracle `:0:0`).

## The model-provider projection

A codex persona (e.g. `navigator℘codex`) reaches an external OpenAI-compatible model provider by
SELECTING it in config.toml: a top-level `model` + `model_provider` pair plus a
`[model_providers.<id>]` table carrying name/base_url/wire_api/env_key. `CodexModelProvider` is the
immutable six-value bundle, constructed from the persona keyspace
(`agent.<persona>℘codex.{endpoint,model,secret_path.<VAR>,...}`); its fields map 1:1 onto the
emitted TOML — *provider_id* is both the table id and the top-level `model_provider` value,
*name*/*base_url*/*wire_api*/*env_key* are the table's four keys, *model* is the top-level `model`.

The table id is emitted as a QUOTED key (`[model_providers."navigator"]`) — equivalent to the
unquoted form but safe for ids with special characters, and matching how the hook region quotes
`[projects."<cwd>"]`.

⚑ This is a **reconciled projection kanibako owns**, reconciled SYMMETRICALLY on every merge. The
managed region and both root keys are stripped/removed FIRST, regardless of whether a provider was
supplied:

* provider supplied (persona active) → **REPLACE**: the region is regenerated and the two root keys
  are SET.
* provider `None` (bare / non-persona) → **WIPE**: the region stays stripped and the two root keys
  are removed UNCONDITIONALLY, so a stale persona or custom selection never lingers on a box that
  has gone bare.

The wipe is SCOPED to that critical set. A user's INDEPENDENT (non-managed)
`[model_providers.<other>]` table and every non-critical key and comment are preserved
byte-for-byte; only the two named root keys and the delimited managed region are touched.
Idempotent in BOTH directions — a second WIPE finds nothing left to strip or remove.

⚑ In `merge_codex_config` the provider root keys are applied to the CLEAN body, before any region is
appended, so they land in the legal top-level position and OUTSIDE both regenerated regions and are
never swallowed by a re-strip. Each strip is followed by `rstrip("\n")` so re-merges do not
accumulate blank lines.

## Functions

```_strip_jsonc(text: str) -> str```
Best-effort strip of JSONC (comments + trailing commas) to plain JSON.

```load_jsonc(text: str) -> object | None```
Parse JSONC text, returning the object or `None` if unparseable.

```_encode_image_ref(ref: str) -> str```
Percent-encode *ref* the way VS Code keys `imageConfigs` files.

```attached_container_config_path(image_ref: str, config_home: Path) -> Path```
Return the host path VS Code reads the IMAGE-keyed attached config from. *config_home* is the user
config home (`xdg("XDG_CONFIG_HOME", ".config")`); *image_ref* is the box's image reference.

```merge_attached_container_config(existing: dict, *, workspace_folder: str, extension: str | None) -> dict```
UNION-MERGE the box's config into *existing*, returning a NEW dict. Pure and deterministic;
*existing* is deep-copied and never mutated.

```_read_existing_config(path: Path) -> dict```
Read *path* as a JSON object → `{}` on absence/corruption; NEVER raises.

```seed_attached_container_config(path: Path, *, workspace_folder: str, extension: str | None) -> bool```
Read-modify-write UNION-MERGE into *path*; `True` iff it wrote. Writes pretty JSON (`indent=2`),
creating parent dirs. The non-atomic write is acceptable: VS Code re-reads the file on each attach
and the seed is idempotent, so a torn write self-heals on the next run.

```_claude_managed_mode(access: str) -> str | None```
The managed `defaultMode` for *access*, or `None` to CLEAR; RAISES on unknown.

```merge_permission_mode(settings: dict, mode: str) -> dict```
UNION-MERGE `permissions.defaultMode = <mode>`, returning a NEW dict. `permissions` is created if
absent; its other sub-keys and every other top-level key (`$schema`, `includeCoAuthoredBy`, …)
survive.

```clear_permission_mode(settings: dict) -> dict```
Return a NEW dict with kanibako's MANAGED `permissions.defaultMode` removed.

```_write_if_changed(path: Path, existing: dict, merged: dict) -> bool```
Write *merged* to *path* as pretty JSON iff it differs from disk (idempotent).

```seed_claude_permission_mode(settings_path: Path, *, access: str) -> bool```
Project the box's `access` tier into the box's claude `settings.json`. The WHOLE of
`ClaudeTarget.deliver_panel_permissions`. Idempotent; `True` iff it wrote.

```_merge_managed_command_hook(settings: dict, *, event: str, matcher: str | None, command: str) -> dict```
UNION-MERGE ONE kanibako-managed `type:command` hook into `hooks.<event>`.

```merge_session_start_hook(settings: dict) -> dict```
UNION-MERGE the instruction-delivery `SessionStart` hook; returns a NEW dict.

```merge_marker_write_hook(settings: dict) -> dict```
UNION-MERGE the per-PID marker-WRITE `SessionStart` hook (its own managed group).

```merge_marker_remove_hook(settings: dict) -> dict```
UNION-MERGE the per-PID marker-REMOVE `SessionEnd` hook; returns a NEW dict.

```seed_session_start_hook(settings_path: Path) -> bool```
Seed all THREE managed claude hooks into `settings.json` in one read-modify-write. The CLAUDE
surface only — codex's hook lives in config.toml. Callers wrap this best-effort so a failure never
blocks the launch.

```codex_trusted_hash(event_key: str, matcher: str | None, command: str, timeout_sec: int = 600) -> str```
Return codex's content-trust hash (`sha256:`) for a single command hook.

```_toml_basic_string(value: str) -> str```
Encode *value* as a TOML basic (double-quoted) string; also used for quoted KEYS
(`[hooks.state."a:b"]`), which share basic-string escaping. Escapes backslash, double-quote and the
common control chars — the only cases our managed values (commands with `"` and `$`, colon/slash
paths) can hit.

```_first_table_index(lines: list[str]) -> int```
Index of the first TOML table header (`[` / `[[`), i.e. the end of the root section, or
`len(lines)` when there is none.

```_extract_delimited_region(text: str, begin: str, end: str) -> tuple[str, str | None]```
Split a managed region (*begin*..*end*, inclusive) OUT of *text*, returning
`(body_without_region, region_text | None)`.

```_strip_delimited_region(text: str, begin: str, end: str) -> str```
Body-only form of `_extract_delimited_region`.

```_assemble_codex_managed(body: str, regions: list[str]) -> str```
Assemble a managed config.toml: clean user *body* + managed *regions*, in order.

```_strip_codex_region(text: str) -> str```
Remove the kanibako-managed instruction-delivery region (inclusive).

```_codex_managed_approval(access: str) -> str```
The managed `approval_policy` for *access* — one per tier; RAISES on unknown.

```_reconcile_codex_approval(text: str, access: str) -> str```
Reconcile the managed `sandbox_mode` (invariant) and `approval_policy` (tier) keys.

```_apply_root_key(lines: list[str], key: str, *, desired: str | None, managed: tuple[str, ...], unconditional: bool = False) -> list[str]```
Apply the SET-or-CLEAR discipline for one managed root key. Returns a NEW list.

```_count_session_start_groups(text: str) -> int```
Count user `[[hooks.SessionStart]]` groups — the index the managed ones follow.

```_build_codex_managed_region(*, box_config_path: str, codex_cwd: str, group_index: int) -> str```
Build the regenerated managed TOML region (hook groups + trust hashes + dir trust).

```merge_codex_config(text: str, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None = None) -> str```
Return *text* with kanibako's managed codex config MERGED in (pure, idempotent). Hook, trust and
provider ONLY — the `approval_policy`/`sandbox_mode` parity keys are never touched here.

```class CodexModelProvider(NamedTuple)```
The six resolved values selecting a codex external model provider.

```_strip_codex_provider_region(text: str) -> str```
Remove the kanibako-managed model-provider region (inclusive).

```_apply_provider_root_keys(body: str, *, model: str, provider_id: str) -> str```
SET the managed top-level `model`/`model_provider` root keys on *body*.

```_remove_provider_root_keys(body: str) -> str```
REMOVE the kanibako-owned `model`/`model_provider` root keys (the WIPE side).

```_build_codex_provider_region(*, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str) -> str```
Build the regenerated `[model_providers.<id>]` TOML region.

```merge_codex_model_provider(text: str, *, provider_id: str, name: str, base_url: str, wire_api: str, env_key: str, model: str) -> str```
Return *text* with the codex model-provider selection MERGED in (pure, idempotent). The standalone
generator: strips any prior provider region, SETs the root keys, regenerates the table region at the
file's end. Updating a single field changes ONLY that emitted value, never a user key. Top-level
keys are emitted before the table, so the result is valid TOML.

```seed_codex_config(config_path: Path, *, box_config_path: str, codex_cwd: str, model_provider: CodexModelProvider | None = None) -> bool```
Write the box's `~/.codex/config.toml` managed hook, trust and provider regions — the codex
DIRECTIVE-HOOK write (`CodexTarget.deliver_directive_hook`). Reads tolerantly (absent → empty; the
file is TEXT, so a "corrupt" TOML file is handled at the text level and never crashes) and writes
iff it changed. *codex_cwd* is the in-box directory codex runs in (the trusted project). Callers
wrap best-effort.

```seed_codex_approval(config_path: Path, *, access: str) -> bool```
Write ONLY the managed codex approval/sandbox parity keys; the SOLE writer of them. Short-circuits
to `False` — no write, no normalization of user bytes — when the reconciled state already matches,
which also means an OFF pass over an absent file never creates one. An UNKNOWN tier RAISES. Callers
wrap best-effort.

```seed_goose_mode(config_path: Path, *, access: str) -> bool```
SET the box's goose `GOOSE_MODE` to its `access` tier parity value — the emitter behind
`GooseTarget.deliver_panel_permissions`. Merge-preserving: only the top-level `GOOSE_MODE` key is
set, every other key survives, and an absent file is created with just `GOOSE_MODE`. Idempotent —
no write when the key already equals the desired value. RAISES for `editing` and for any unknown
tier.
