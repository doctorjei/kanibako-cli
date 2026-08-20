# `CodexTarget` — the OpenAI Codex CLI plugin target

`plugins/codex/target.py` is the `Target` implementation for the OpenAI Codex CLI
(https://github.com/openai/codex). It was the first target written *after* the descriptor-native
plugin interface was generalized, which makes it the proof that the interface holds for an agent
that did not exist when the interface was designed: it implements only the irreducible surface and
inherits the rest.

Codex is also the project's one **CONFIG-FILE harness** — its endpoint and its model reach the box
through `~/.codex/config.toml`, not through environment variables — and most of what is unusual
about this plugin follows from that single fact.

## The irreducible surface

What this plugin implements:

* `name` / `display_name` — identity.
* `detect` — the one genuinely codex-specific bit; see *Host binary detection* below.
* `descriptor` — the declarative `PluginDescriptor`. Core `start.py` assembles launch argv, env,
  delivery binds and credential lifecycle from it. Codex implements no legacy hooks.
* `check_auth` — a lenient credential presence check.
* `setting_descriptors` / `generate_agent_config` — the declarative helpers.
* the config.toml seams: `deliver_panel_permissions`, `deliver_directive_hook`,
  `reattach_config_notice`.
* the persona seams: `read_persona_settings`, `verify_persona`.

Everything else — `build_cli_args`, `binary_mounts`, `refresh_credentials`,
`writeback_credentials`, `transform_cred` — is inherited from the step-3a concrete `Target`
defaults. Codex needs no override there: both its cred files are wholesale copies, and the
descriptor's `init_dirs` create `.codex`.

## The declarative default-set lives in `codex-defaults.yaml`

The descriptor's default-set is the plugin's shipped `codex-defaults.yaml` (P6c coalesce), read by
the thin `kanibako.settings.agent_defaults` loader, which builds the `PluginDescriptor` exactly as
the old hand-written `target.py` values did. That file documents each non-obvious field for codex
0.140.0: the bare `codex` / `codex resume --last` mode grammar; the `codex exec` op; the
`--dangerously-bypass-approvals-and-sandbox` FLAG realization of the `full` access tier; the
`--model` FLAG; the single SYNC `.codex/auth.json` cred file (`filtered=False`, a wholesale copy,
an E2E gate); and the `.codex` init dir.

The split between the two files is deliberate: **declarative** values live in the YAML, and the one
**code-resolved** value is the CRITICAL host binary path, which `detect()` probes at runtime. The
YAML names only, declaratively, that the binary bind's host source is the detected `binary` field
(`origin: binary`); the box-side destination is fixed there too.

Two consequences worth knowing without opening the YAML:

* `default_category_binds()` is currently **EMPTY**. The former `@system.instructions` →
  `~/.codex/AGENTS.md` instructions bind was retired: the box guide now ships INSIDE the RO
  whole-dir canon bind at `~/canon/bible` plus the flattened per-agent FINAL file, not a per-agent
  native-slot bind.
* `default_envs()` declares exactly one variable, `KANIBAKO_DIRECTIVE_FINAL`, naming codex's native
  `~/.codex/AGENTS.md` slot — the file the box-start flattener writes. It is an ordinary settings
  key (spec §2d `agent.codex.env.*`): overridable by the SAME key in a nearer file, and refused
  when a second scope names the same variable.

## Host binary detection

`detect()` honors the host-binary **preference order**: machine-code-compiled executable >
self-contained / contained package (SEA, AppImage — still a single bindable executable) >
runtime-dependent package managers (npm/pip), LAST. The rationale is to avoid the brittleness of
requiring node or python on the host.

**PRIMARY — a standalone executable on `$PATH`.** Resolve `codex` on `$PATH` (symlinks followed).
If the real target is an **ELF** (first four bytes `\x7fELF` — a Rust native build OR a Node
single-executable application, both directly bindable), bind THAT file: an `AgentInstall` with
`binary` = the resolved ELF and `install_dir` = its parent. No node in-box is required.

**FALLBACK — the npm-vendored native binary.** If `codex` is absent from `$PATH`, or it resolves to
a *non*-ELF (the npm `@openai/codex` Node **shim**, a `#!node` text script that is NOT bindable
standalone), fall through to npm:

1. Find the npm global `node_modules` root via `npm root -g`.
2. Under it, locate the per-platform package `@openai/codex-<os>-<arch>` and its vendored binary at
   `vendor/<triple>/bin/codex`.
3. Return an `AgentInstall` pointing `binary` at that real ELF. The descriptor's BINARY binding
   uses `install.binary`, so the static-pie musl ELF binds into the box and runs with no node.

Resolving npm is the last resort precisely because it is the one path that needs npm on the host.

### Why a `$PATH` lookup is the right primitive here

Codex's host install location is genuinely user-chosen — there is no fixed contract path as claude
and goose have. Symlinks are followed and the ELF magic is verified (read-only) before the result is
ever trusted or bound, so this is **not** the PATH-injection vector that anchoring guards against
for the fixed-path agents.

### The npm layout

`@openai/codex` is a Node SHIM; the real binary lives in a per-platform package
`@openai/codex-<suffix>` at `vendor/<triple>/bin/codex`. That platform package may be:

* HOISTED to the top level: `<root>/@openai/codex-<suffix>`
* NESTED under the shim: `<root>/@openai/codex/node_modules/@openai/codex-<suffix>`

Both are checked, in that order, for the resolved `(suffix, triple)`. As a final fallback,
`_resolve_vendored_binary` globs `<root>/**/@openai/codex-*/vendor/*/bin/codex` — any layout, any
vendored triple — so a packaging quirk still resolves.

`_platform_pkg_and_triple` maps `(os, machine)` to that pair, normalizing arch aliases:

| host OS | machine aliases | npm platform package | vendored target triple |
|---|---|---|---|
| linux | `x86_64` / `amd64` / `x64` | `codex-linux-x64` | `x86_64-unknown-linux-musl` |
| linux | `aarch64` / `arm64` | `codex-linux-arm64` | `aarch64-unknown-linux-musl` |
| darwin | `x86_64` / `amd64` / `x64` | `codex-darwin-x64` | `x86_64-apple-darwin` |
| darwin | `aarch64` / `arm64` | `codex-darwin-arm64` | `aarch64-apple-darwin` |

An unrecognized OS/arch returns `None`; detect then falls back to the glob search, and ultimately to
"not installed".

### Every step is failure-tolerant on purpose

Detection never crashes. `_npm_root_global` runs `npm root -g` with a short timeout
(`_NPM_ROOT_TIMEOUT`, 10s) and answers `None` for every failure mode — npm absent, timeout, nonzero
exit, garbage output, a root that is not a directory. `_is_elf` swallows any `OSError` (missing,
unreadable, a directory) as `False`. `_resolve_path_executable` returns `None` when `codex` is not
on `$PATH` or cannot be resolved, and never raises. `detect()` itself returns `None` when neither a
standalone binary nor an npm-vendored one is found.

### ⚑ E2E-GATED

Both `detect` paths — (1) the PRIMARY standalone-ELF-on-PATH bind and (2) the FALLBACK npm-shim →
native-binary resolution, including the exact vendored hoist location and target triple — are
implemented best-effort against the documented codex 0.140.0 layout and MUST be verified on a real
codex install: a standalone-extracted ELF on `$PATH` (primary) and the npm Node shim that vendors a
musl static-pie ELF (fallback). Codex is not present on the dev box, so the unit tests mock `$PATH`,
the npm root and a fake vendored tree.

## Resuming a session

`continue` mode builds `codex resume --last` (`codex-defaults.yaml`), which replays the MOST-RECENT
recorded session — a "rollout" `.jsonl` file codex persists under
`$CODEX_HOME/sessions/<year>/<MM>/<DD>/rollout-<ts>-<uuid>.jsonl`. Verified against openai/codex
`codex-rs/rollout/src`: `SESSIONS_SUBDIR = "sessions"` plus the `year/month/day` push in
`recorder.rs`.

`CODEX_HOME` defaults to `~/.codex`, and kanibako sets NO `CODEX_HOME` — it is not among this
plugin's declared `agent.codex.env.*` keys, whose only member is the directive FINAL slot — so the
box store is `<home>/.codex/sessions/`.

`resume --last` is workdir-AGNOSTIC (the newest session regardless of cwd), so — unlike claude's
per-project transcript dir — `has_resumable_session` checks the WHOLE store, recursively, to cover
the date nesting. Any rollout `*.jsonl` ⇒ `True`.

On a FRESH box the store is absent or empty, so `resume --last` is DOOMED (no session → fast exit);
returning `False` lets `start.py` launch a new session instead. The launch-time crash-and-retry net
was removed, which is why this check has to be right. It is tolerant in the safe direction: any
stat/glob error ⇒ `False`, because a fresh start is always safe.

## The managed `~/.codex/config.toml` — two seams, one key set each

The box's `config.toml` is a RECONCILED PROJECTION (D1): the launch seams re-materialise its
model/provider/approval elements only on the start of a STOPPED box.

### `deliver_panel_permissions` — and only it — owns approval and sandbox

Codex approval/sandbox has NO VS Code settings key, and the `openai.chatgpt` panel spawns its own
in-box codex without kanibako's launch flags, so this config.toml parity is the ONLY way the panel
sees the box's tier. This method is the SOLE writer of the two managed root keys:

* `approval_policy` is TIER-gated: `full` → `"never"`, `editing` → `"on-request"`, `restricted` →
  removed while it still equals a value WE manage, which preserves a user-chosen one.
* `sandbox_mode` is a BOX INVARIANT forced to `"danger-full-access"` ALWAYS, independent of the
  resolved *access* tier — the container is the sandbox, so the panel's app-server must not attempt
  a nested one.

⚑ That invariant is why the panel's middle tier rides the APPROVAL axis while the CLI's rides
`-s workspace-write`: writing `workspace-write` here is the configuration that hangs the app-server.
Implementation: `kanibako.vscode.vscode_config.seed_codex_approval`.

### `deliver_directive_hook` — everything else in the file

It seeds the instruction-delivery `[[hooks.SessionStart]]` group, its pre-computed trust hash, the
directory trust, and — for a codex persona — the `model_provider` region. It writes the
approval/sandbox keys NEVER, so no managed key has two writers; it accepts *access* per the seam
contract and does not use it.

The box-side literals codex keys its trust entries on (the in-box config path and the workdir) are
derived here from the core `kanibako.settings.settings_resolve.GUEST_HOME` constant: the workdir is
the fixed container WORKDIR `GUEST_HOME/workspace`, which tmux `new-session` inherits and which
`has_resumable_session` pins the same way. If either ever becomes configurable, promote a seam
parameter instead of re-deriving it.

### `reattach_config_notice`

A reattach to a LIVE box does NOT re-deliver, and rewriting the file under the panel's
already-running codex app-server is unsafe. So the notice tells the user that codex config changes
(model / provider / approvals) take effect only after restarting the box; kanibako reconciles the
file to the resolved active agent on the next start.

## Auth, and the in-box login

`check_auth` is lenient and never blocks a launch it cannot rule on. `True` when
`~/.codex/auth.json` exists and is non-empty, OR when `OPENAI_API_KEY` is set — and `True` on any
stat error too, treating it as "cannot tell". This matches goose's lenient style of not blocking
when it cannot determine auth state.

When that pre-launch probe fails (no `auth.json` and no `OPENAI_API_KEY`), `start.py` runs
`codex login` interactively IN THE BOX — `setup_entrypoint` / `setup_args` — so the user can
complete the ChatGPT/OAuth flow, then proceeds with launch. Box state persists across reattach.

`should_run_setup` inspects codex's own session output for launch-time ground truth that
`codex login` did NOT produce a bootable auth state. It matches case-insensitively on codex's known
login-needed signals — `not logged in`, the `run 'codex login'` remediation hint, `please log in`,
`please sign in`, `authentication failed`, `401 unauthorized` — so a phrasing change in any one of
them still trips the detector.

## Persona — reading one back, and probing it

### `read_persona_settings`

The persona-grata store renders a codex persona as the SAME `[model_providers.<id>]` shape kanibako
itself emits at launch (`vscode_config.CodexModelProvider` → `_build_codex_provider_region`), so
this reader parses the inverse: `base_url` → endpoint, `env_key` → auth_env (codex configs
SELF-NAME the bearer var), and the top-level `model`.

Provider-table selection: the top-level `model_provider` key when it names a present table (what
kanibako writes), else the single table when exactly one exists. Zero tables, or an ambiguity with
no selector, is a reject.

A codex config carries NO env block, so `env`/`env_dropped` stay at their (empty) defaults — unlike
claude, whose persona env rides the config.

FAIL-SOFT, per the base-class contract: absent or unreadable, malformed TOML, no usable provider
table, a selected entry that is not a table, or a missing/empty `base_url`/`env_key` (a codex
persona is meaningless without both) each return an outcome NAMING that cause and the file. It is a
pure read via stdlib `tomllib`, and never touches the token.

### `verify_persona`

A genuine few-token completion on the RESPONSES wire — the only wire current codex speaks (the
provider block's `wire_api = "responses"`). Per the base contract: 2xx → `PASS`, 401/403 →
`REJECTED`, unreachable/ambiguous → `INCONCLUSIVE`.

⚑ **The endpoint is the provider `base_url`, and by codex's convention that value ALREADY carries
the `/v1`-style prefix** that the probe appends `/responses` to. It is NOT an origin-only host like
goose's `OPENAI_HOST`; the two are not interchangeable, and the difference is easy to state
backwards.

The request is bearer-authed with the token at *token_path* (the `env_key` variable's value in-box)
when one is configured. The one `NOT_APPLICABLE` this method decides for itself is a CONFIGURED
(non-`None`) token file that is unreadable or empty. The token is read transiently for this request
only; never logged or persisted.

Two probe rules, both of which fail in the direction of refusing a box that actually works
(procedure: `~/canon/notebook/procedures/persona-resolution-model.md`):

1. **A PRESENT-null *token_path* is still PROBED, with the `Authorization` header OMITTED**
   (2026-08-17 ruling). A persona whose `secret_path` key is deliberately `null` declares this
   endpoint keyless, and the request is sent bare for the server to decide. Never a placeholder
   credential: a hardwired-auth server can REJECT one it does not serve, and a false `REJECTED` is
   a hard error that would refuse a working box.
2. **A persona that names no *model* is still PROBED, with the `model` key OMITTED from the body.**
   An OpenAI-compatible endpoint may serve exactly one model or apply its own, and declining to
   probe would let a DEAD token reach the box. The answer is read through
   `kanibako.targets.base.probe_outcome_no_model` so a "model required" reply is silent rather than
   a permanent warning. NEVER substitute a placeholder / default / guessed model id to make the call
   go through — same false-`REJECTED` failure as above.

⚑ **The codex LAUNCH gate is stricter than this probe; do not conflate them.** The descriptor
declares `model_required: true` because a config-file harness structurally cannot express "no
model": codex types `model` as `Option<String>` and resolves it with `model.or(cfg.model)` and NO
`unwrap_or`, so an omitted key does not send a model-less request — selection falls through to the
model catalog's RECOMMENDED default, which moves over time. For an **ENV-delivery** harness a
PRESENT-null model SUPPRESSES the veto (it can simply omit the model variable); for **codex** it
CONFLICTS, and the launch refuses with an actionable pre-flight message. `verify_persona` is looser
than the gate because it is also called straight off the store on the CREATE path, where no model
may have been resolved at all.

## Declared settings

`setting_descriptors` declares two behavior keys. Their FLOOR values live in
`codex-defaults.yaml`'s `behavior:` section, not in this module: a default written in plugin code
would be a second declaration site for something the shipped file already owns.

* `model` — freeform, because OpenAI adds models regularly (floor: `gpt-5.5`).
* `endpoint` — the alternate model-provider base-URL (persona); unset means bare/harness-default.
  Unlike claude, a codex endpoint is delivered via the `~/.codex/config.toml`
  `[model_providers.<id>]` block (descriptor `persona.endpoint_delivery: config_file`), NOT an env
  var. It is declared here only to make it a first-class SETTABLE, cascade-resolved behavior key
  (`config set` / `--effective`).

The permission tier is NOT a setting descriptor: it rides the uniform `access` key (the descriptor's
`access_realization.setting_key` is `access`), persisted and cascade-resolved, default permissive;
`-A` / `-S` override it per launch.

`generate_agent_config` returns an EMPTY `state`, and that is the FILE-PURITY invariant rather than
an oversight: the agent settings file holds USER INTENT only, and defaults come from the descriptor
floor. Seeding that same value into the file would pin every install ABOVE the floor, so a later
change to the default could never reach an existing box.

---

## Completeness sweep (relocation pass, 2026-08-20)

`comment-ratio.py`: **62.7% → 52.8%** (19805/31578 → 13018/24662 characters). The module docstring,
`detect`, `has_resumable_session`, `verify_persona` and the `_CODEX_DESCRIPTOR` block comment
carried most of the relocated bulk.

`prose-relocation-check.py`: **281 prose lines at HEAD, 233 removed, 0 scoring below 0.6** against
this document — no removed line is orphaned. (Note the doc path: this is a plugin package, so the
mirror drops everything up to and including the first `src` component, and the checker's default
guess must be overridden with `--doc llm-docs/kanibako/plugins/codex/target.py.md`.)
`prose-pass-check.py`: all three checks pass — 1666 AST lines identical with docstrings stripped,
26 symbols with `added=[] removed=[]`, 104 string literals multiset-identical.

**Deliberate duplication drops — nothing else was cut:**

1. The `_CODEX_BEHAVIOR` comment and `setting_descriptors`'s closing paragraph both said "no
   default value is written in this module". The one-liner at `_CODEX_BEHAVIOR` survives in source;
   the fuller statement is the *Declared settings* section above.
2. The module docstring's E2E-GATED paragraph and `detect`'s were the same warning twice, in the
   same file. A short warning survives at both sites; the full text is *⚑ E2E-GATED* above.

**Warnings deliberately KEPT IN SOURCE**, because deleting each would let a future edit break
something silently at that exact line:

* `deliver_panel_permissions` — the `sandbox_mode` invariant; writing `workspace-write` there hangs
  the panel's app-server, and the line looks like an ordinary tier mapping.
* `deliver_directive_hook` — "NEVER the approval/sandbox keys"; the file is right there and adding
  a second writer to a managed key is a one-line temptation.
* `verify_persona` — both probe rules and the `/v1` `base_url` note. All three are stated backwards
  easily, and each wrong version refuses a box that works.
* `detect` and the module docstring — the E2E gate, so nobody reads mocked unit tests as proof the
  real install resolves.
* `_resolve_path_executable` — why a `$PATH` lookup is not the injection vector anchoring guards
  against for the fixed-path agents.
