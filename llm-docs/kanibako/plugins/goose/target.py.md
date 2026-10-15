# GooseTarget — the goose plugin's `Target`, and what it deliberately does not decide

`target.py` is the whole of what `kanibako-agent-goose` implements against the `Target` ABC in
`kanibako.targets.base`. It is thin on purpose: goose is **descriptor-bearing**, so nearly every
declaration lives in the shipped `goose-defaults.yaml` beside it, and this class is the code that
(a) finds the host binary, (b) answers the few questions a YAML
file cannot answer, and (c) states the two places kanibako must NOT speak for goose — its
provider/model, and its own `config.yaml`.

Plugins declare `kanibako-cli>=1.8.0.dev0,<2.0`. The declarations live in the data file, which the
core loader reads: a retired descriptor key (`safe_bypass:`, `container_env:`) is refused by name
(`settings.agent_defaults`), and a plugin that fails to import is skipped at discovery with a named
warning (`targets.discover_targets`).

**Authority:** `~/canon/workbook/specs/settings-keyspace-1.8.0.md` — §2d (the per-agent keyspace: `agent.goose.env.*`,
the `synced` category view, which env vars are plugin-declared and which are user preference).

## The contract path

```python
_BINARY = Path.home() / ".local" / "bin" / "goose"
```

Goose is a single self-contained ELF at `~/.local/bin/goose`. Detection (`detect`), the auth probe
(`check_auth`) and the delivery bind all anchor to this known install location instead of
`shutil.which("goose")`.

⚑ **This is a security choice, not a convenience.** `which` trusts `$PATH` to locate a binary we
then bind into the box: an earlier-`$PATH` planted `goose` would smuggle a malicious agent into the
container. Anchoring confines trust to the user's own home directory. The claude plugin does the
same thing for the same reason.

`detect` treats goose as installed iff the contract path exists *or* is a (possibly dangling)
symlink. `binary` is the RESOLVED, symlink-free path so that nested-container mount sources are real
files; `install_dir` is its parent. `launcher` stays `None` — goose has no separate launcher, and
its binding uses the BINARY origin (`install.binary`). A dangling or unresolvable binary is still
"installed" per the contract; the downstream binary validation is what surfaces the real problem.

## The descriptor, and what `goose-defaults.yaml` declares

`_GOOSE_DESCRIPTOR` is the declarative descriptor for the generalized plugin interface, and it is
LIVE: core `start.py` assembles goose's launch argv, env, delivery mounts and credential lifecycle
from it.

The descriptor's default-set lives in this plugin's shipped `goose-defaults.yaml` and
is read by the thin `kanibako.settings.agent_defaults` loader. That file documents each non-obvious
field; everything below was empirically verified against **goose 1.37.0**:

* the mode grammar — bare `session` for a new session, `session --resume` for `continue`;
* the exec op — `run --no-session -t`;
* the **access realization**, a SYMMETRIC ENV `GOOSE_MODE` (`auto` / `approve`). The `restricted`
  row's value is MANDATORY, because goose's unset default is the permissive `auto`;
* model and provider, routed as ENV `GOOSE_MODEL` / `GOOSE_PROVIDER` with **no default value**, so
  goose falls back to its own `config.yaml` written by `goose configure`;
* the declared `env:` keys (`agent.goose.env.*`) — `GOOSE_DISABLE_KEYRING`, `CONTEXT_FILE_NAMES`
  and the `KANIBAKO_DIRECTIVE_FINAL` slot;
* the three two-way SYNC cred files — `secrets.yaml`, `config.yaml` and `custom_providers/` — which
  persist an in-box `goose configure` back to the host.

⚑ The one CRITICAL thing that stays code-resolved rather than declared is the host binary path
(`_BINARY`, above), read in `detect()`.

`_GOOSE_BEHAVIOR` is the declared BEHAVIOR floor — the file's `behavior:` section. No default value
is written in this module; the provider, model, and endpoint floors are empty on purpose, and the
YAML file states why.

## The category binds are EMPTY, and that is the current design

`default_category_binds` reads goose's AGENT-scope `@`-ref-sourced category binds from
`goose-defaults.yaml`, and the answer today is an empty map.

The former `{system.instructions}` → `~/.config/goose/KANIBAKO.md` instructions bind was **retired**.
The box guide now ships INSIDE the read-only `general` chapter bind at `~/canon/charter/general`,
together with the flattened FINAL file. Do not reintroduce a per-agent instructions bind to "fix" the
emptiness. ⚑ The book ROOT `~/canon/charter` is not bound at all — each chapter is its own
sibling bind, so there is no whole-dir canon bind to ship inside.

## The env defaults, and the two variables kanibako refuses to declare

`default_envs` reads `goose-defaults.yaml`'s `env:` section (spec §2d, `agent.goose.env.*`):

* `GOOSE_DISABLE_KEYRING` — there is no D-Bus secret service inside a box;
* `CONTEXT_FILE_NAMES` — the filenames goose loads, which must list the flattened FINAL file;
* `KANIBAKO_DIRECTIVE_FINAL` — naming that file.

These are the PLUGIN-REQUIRED variables that §2d keeps plugin-declared. The user-preference ones —
`GOOSE_PROVIDER` and `GOOSE_MODEL` — are deliberately NOT declared, because goose owns those in its
own persistent config.

## `config.yaml` is GOOSE's own file

The rule: a box's `GOOSE_MODE` stays in that box; every other key syncs both ways.

`transform_cred` is the credsync engine's per-spec filter hook. Only `config.yaml` is FILTERED —
`secrets.yaml` and `custom_providers/` stay unfiltered wholesale copies — and the engine calls this
hook for that spec alone:

* **`"in"` (host → box seed/refresh)** — wholesale copy. The box gets the host's `config.yaml`
  verbatim.
* **`"out"` (box → host writeback)** — merge the box's `config.yaml` back to the host BUT preserve
  the host's OWN `GOOSE_MODE`: a box's permission mode never overwrites the host's. Every OTHER key
  the user changed in-box — provider, model, extensions, … — still flows to the host. If the host
  had no `GOOSE_MODE` at all, the box-local one is dropped rather than introduced.

It is defensive throughout, matching the warn-and-skip credential helpers: a missing source or a
malformed YAML degrades to a safe no-op or an empty dict, never raises. Two of those degradations
are deliberate protections rather than tidiness — an empty or unparseable box `config.yaml`
(`read_yaml` degrades to `{}`) must not clobber the host's real config down to an empty file, and a
writeback whose only difference was the box-local `GOOSE_MODE` leaves the host file, and its mtime,
untouched. Any other filtered spec that ever reaches the hook falls back to a plain wholesale copy,
the base-class default.

## Panel and directive seams

goose overrides `deliver_directive_hook` but not `deliver_panel_permissions`, which stays the
inherited no-op. The `block.vscode-goose` panel spawns its own in-box goose, which reads
`GOOSE_MODE` from the box's `config.yaml`; kanibako does not write the `access` tier there. The
launched goose gets the tier only as the `GOOSE_MODE` env var (`access_realization`), and the
launch refuses `editing`, which no goose mode realizes.

## The canon hooks plugin

goose 1.38 loads hooks only from plugins: any directory under `~/.agents/plugins/` with a
`hooks/hooks.json` (claude-shaped; no manifest or trust step). `deliver_directive_hook` writes the
kanibako-owned `~/.agents/plugins/kanibako/hooks/hooks.json` from this package's
`data/agents-plugin/hooks/` at EVERY start, so existing boxes get it too; a file whose bytes
already match is left alone (the seam returns whether it wrote). That is the only file written:
every hook runs in place from canon, and the `mail-stop.sh` adapter ships in this plugin's charter
chapter (`data/rom/scripts/hooks/`, bound read-only at `~/canon/charter/agent`, as claude's
`statusline.sh` is). The write goes through `kanibako.boxfs`; a symlink at the leaf or a parent
skips it with a warning. Directives still reach goose as a context file
(`goose-defaults.yaml`, `KANIBAKO_DIRECTIVE_FINAL`), not through this plugin.

| claude event | goose event | runs |
|---|---|---|
| SessionStart `startup` / `resume` | SessionStart (no matcher) | charter `startup.sh "$PPID"` |
| SessionEnd `logout` / `prompt_input_exit` / `other` | SessionEnd (no matcher) | charter `end.sh "$PPID"` |
| PostToolUse `Write\|Edit` | AfterFileEdit | charter `edited.sh` |
| Stop | Stop | charter `stop.sh`, then `mail-stop.sh` |
| PostToolUse check-comms (mail) | Stop | `mail-stop.sh` |

goose tests a matcher on SessionStart/SessionEnd against `""`, so those rules carry none; `$PPID`
in the hook shell is goose.

**No goose equivalent:**

* SessionStart `clear` / `compact` (`clear-start.sh`, `compact.sh`) — goose emits SessionStart only
  when a CLI session starts, with no source; a resume runs `startup.sh`, not `resume.sh`.
* SessionEnd `clear` (`clear-end.sh`) — goose has no clear event.
* The SessionStart `import-directives.py` hook — goose hooks cannot inject context.

**Mail.** goose discards hook output for every event but PreToolUse and Stop, and `check-comms.sh`
advances its mail marker once it has printed, so wired to PostToolUse it would mark mail seen
without showing it. `mail-stop.sh` runs `check-comms.sh` at Stop instead and turns its alert into
`{"decision":"block","reason":<alert>}`; goose adds the reason to the context and continues the
turn, and the next Stop finds nothing new. It never exits non-zero (goose would ignore a failure,
and exit 2 would block with stderr). A `check-comms.sh` fault re-alerts on every run, and goose
would otherwise block each turn end up to `GOOSE_STOP_HOOK_BLOCK_CAP` (default 8) times, so a
repeat is not blocked when the same goose session (stdin `session_id`) already got that exact
alert and `check-comms.sh` moved neither marker (`last-mail-check` mtime, `last-bcast-check`
content) on this run; a moved marker means news, such as a broadcast whose text matches the last
one. Each session sees a persistent fault once. The memos
(`/tmp/kanibako-comms-<name>/goose-stop-last-<session>`) clear on the first clean check.

⚑ **KNOWN LIMITS.** goose fires SessionStart/SessionEnd only from goose-cli sessions (`goose acp` /
`serve` were not checked). A turn that ends without reaching a blocking Stop (cancel, error, max
turns) fires Stop fire-and-forget, so mail found there is marked seen without being shown. The
Stop stdin has no transcript, so notebook stop hooks that read one see nothing.

## Setup — detecting an unconfigured goose

`should_run_setup` reads launch output for ground truth that `goose configure` did NOT produce a
bootable config. goose's verbatim line is *"Goose is not configured. Run 'goose configure' to set
up."*, and the check matches case-insensitively on EITHER the `not configured` phrase or the
`goose configure` remediation hint, so a phrasing tweak in one half still trips the detector.
`check_auth` prints that same sentence when the host's `secrets.yaml` is missing or empty, or
`config.yaml` is missing; it returns True when the binary is absent, deferring to later warnings.

`setup_entrypoint` / `setup_args` name `goose configure`, goose's one-time interactive provider
setup. When the pre-launch `check_auth` probe fails, `start.py` runs it interactively IN THE BOX so
the user can select a provider/model and enter a key, then proceeds with the launch. Box state
persists across reattach, so this is a one-time step per box.

## Resume detection

`has_resumable_session` answers whether `continue` — which builds `goose session --resume` — has
anything to resume. That command resumes the most recent session in goose's data-dir session store,
`~/.local/share/goose/sessions` in the box, which the descriptor's `init_dirs` pre-creates EMPTY.

Goose 1.37 keeps sessions in a sqlite db INSIDE that dir (`sessions/sessions.db` plus `-wal` /
`-shm`), and a FAILED resume attempt itself creates the db. That is why a plain **dir-entry** check
splits exactly right: a FRESH box (init_dirs only, dir empty) reads `False` — the fix — while a box
whose earlier doomed attempt left an empty db reads `True`.

The motivating bug: on a box's FIRST agent launch the store is empty, so the resume is DOOMED
("no session found to resume" → fast container death → a raw attach-race error surfacing to the
user). Returning `False` lets `start.py` go straight to a new session.

*home* is the box home as seen from the HOST (the home bind source), so the store is readable
without touching the container. FAIL-SAFE direction: `False` only when the store positively contains
no entry (missing dir, or empty dir); ANY entry — or any read error — returns `True`, so a real
resume is never wrongly denied.

⚑ **KNOWN LIMIT.** `GOOSE_PATH_ROOT` is user-settable (the env category / `-e`) and would redirect
goose's store; this hook's fixed signature (*home* only) cannot see that, so it checks the DEFAULT
store location the descriptor lays down. Kanibako itself never sets `GOOSE_PATH_ROOT` — it is not
among this plugin's declared `agent.goose.env.*` keys.

## Credentials

goose's `secrets.yaml` host↔box SYNC is the `CredFileSpec` set in `goose-defaults.yaml`, realized
by the credsync engine (`seed_cred_files` / `refresh_cred_files` / `writeback_cred_files`) — the
§2d `synced` category view.

`credential_check_path` (base, derived from the first declared cred file) names
`~/.config/goose/secrets.yaml`, the file whose presence stands for "goose has credentials".

`cred_writeback_error` refuses to write back a box `secrets.yaml` that is not a non-empty YAML
mapping — the mapping `credentials.read_yaml` reads; an empty one is refused as the `config.yaml`
writeback refuses one.

## Provider and model are NEVER pinned by kanibako

This is one rule stated by three symbols, and it is the single easiest thing to break here.

`generate_agent_config` returns `state={}` — intentionally EMPTY. Forcing `GOOSE_PROVIDER` /
`GOOSE_MODEL` as defaults would override the user's in-box `goose configure` choice, because goose's
env vars win over its own `config.yaml`; that clobbers a provider (and key) the user selected
interactively.

`setting_descriptors` returns `label` and three declared behavior keys, none with a value:

* **`provider`** and **`model`** set nothing, so with nothing set the launch omits the env vars
  entirely — goose then reads provider/model from its own `config.yaml`. `provider` is goose's own
  key, so its `<None>` is written `default: null` (a PRESENT `None` at `agent.goose.provider`);
  `model` omits `default:` and inherits `agent.default.model`, so a user's
  `agent.default.model` reaches goose as `GOOSE_MODEL`. An EXPLICIT `agent.goose.provider` /
  `agent.goose.model` setting still wins the cascade and IS emitted, so a user who *wants* to pin a
  provider through kanibako settings still can.
* **`endpoint`** (persona) is the alternate OpenAI-compatible base URL; unset means bare /
  harness-default. It is delivered via the descriptor's `endpoint`→`OPENAI_HOST` ENV `SettingArg`
  (goose's built-in `openai` provider reads `OPENAI_HOST`), and is declared here to make it a
  first-class SETTABLE, cascade-resolved behavior key (`config set` / `--effective`), MIRRORING
  claude's `endpoint` descriptor.

The keys and their FLOOR values are declared in `goose-defaults.yaml`'s `behavior:` section, not in
this module: a default written in plugin code is a second declaration site for something the shipped
file already owns.

The translation half is the descriptor, not a method: `provider` → `GOOSE_PROVIDER` and `model` →
`GOOSE_MODEL` are ENV `SettingArg` rows in `goose-defaults.yaml`, emitted by `assemble_env` when
the resolved value is non-empty, and for `model` also when it is `""` (`GOOSE_MODEL=`, the empty
model; neither row declares `empty:`). Goose overrides provider/model by env var rather than CLI
flag, so neither contributes an argv token. The descriptor rows are the whole route.
