# GooseTarget — the goose plugin's `Target`, and what it deliberately does not decide

`target.py` is the whole of what `kanibako-agent-goose` implements against the `Target` ABC in
`kanibako.targets.base`. It is thin on purpose: goose is **descriptor-bearing**, so nearly every
declaration a plugin used to make in Python now lives in the shipped `goose-defaults.yaml` beside
it, and this class is the code that (a) finds the host binary, (b) answers the few questions a YAML
file cannot answer, and (c) states the two places kanibako must NOT speak for goose — its
provider/model, and its own `config.yaml`.

Plugins declare `kanibako-cli>=1.8.0.dev0,<2.0` (bounded 2026-09-01). **Every wheel published before
v1.8.0 carries a bare `["kanibako-cli"]`**, so an old plugin wheel can still land on a new core; when
the contract it was written against is gone, the failure is a NAMED error rather than a silent
misbehaviour. The bound removes that pairing only for wheels published from v1.8.0 on, so the named
error stays the safety net and the reason this surface stays small. That is the reason the surface here stays small and the
declarations stay in the data file.

**Authority:** `specs/settings-keyspace-1.8.0.md` — §2d (the per-agent keyspace: `agent.goose.env.*`,
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
from it. The legacy `build_cli_args` / `binary_mounts` / refresh / writeback hooks are bypassed for
goose entirely.

The descriptor's default-set lives in this plugin's shipped `goose-defaults.yaml` (P6c coalesce) and
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
is written in this module; goose's three floors are EMPTY on purpose, and the YAML file states why.

## The category binds are EMPTY, and that is the current design

`default_category_binds` reads goose's AGENT-scope `@`-ref-sourced category binds from
`goose-defaults.yaml`, and the answer today is an empty map.

The former `@system.instructions` → `~/.config/goose/KANIBAKO.md` instructions bind was **retired**.
The box guide now ships INSIDE the read-only whole-dir canon bind at `~/canon/bible`, together with
the flattened FINAL file. Do not reintroduce a per-agent instructions bind to "fix" the emptiness.

## The env defaults, and the two variables kanibako refuses to declare

`default_envs` reads `goose-defaults.yaml`'s `env:` section (spec §2d, `agent.goose.env.*`):

* `GOOSE_DISABLE_KEYRING` — there is no D-Bus secret service inside a box;
* `CONTEXT_FILE_NAMES` — the filenames goose loads, which must list the flattened FINAL file;
* `KANIBAKO_DIRECTIVE_FINAL` — naming that file.

These are the PLUGIN-REQUIRED variables that §2d keeps plugin-declared. The user-preference ones —
`GOOSE_PROVIDER` and `GOOSE_MODEL` — are deliberately NOT declared, because goose owns those in its
own persistent config.

## `config.yaml` is GOOSE's own file — kanibako only projects into it

Two hooks touch it, from opposite directions, and the rule joining them is that the box-local
`GOOSE_MODE` is kanibako's projection while everything else in the file is the user's.

`transform_cred` is the credsync engine's per-spec filter hook. Only `config.yaml` is FILTERED —
`secrets.yaml` and `custom_providers/` stay unfiltered wholesale copies — and the engine calls this
hook for that spec alone:

* **`"in"` (host → box seed/refresh)** — wholesale copy. The box gets the host's `config.yaml`
  verbatim; its box-local panel-parity `GOOSE_MODE` is (re)seeded separately by
  `deliver_panel_permissions` at launch.
* **`"out"` (box → host writeback)** — merge the box's `config.yaml` back to the host BUT preserve
  the host's OWN `GOOSE_MODE`. The in-box value is a box-local PANEL-parity value (the panel's
  yolo), NOT user config, so it must never overwrite the host's setting. Every OTHER key the user
  changed in-box — provider, model, extensions, … — still flows to the host. If the host had no
  `GOOSE_MODE` at all, the box-local one is dropped rather than introduced.

It is defensive throughout, matching the warn-and-skip credential helpers: a missing source or a
malformed YAML degrades to a safe no-op or an empty dict, never raises. Two of those degradations
are deliberate protections rather than tidiness — an empty or unparseable box `config.yaml`
(`read_yaml` degrades to `{}`) must not clobber the host's real config down to an empty file, and a
writeback whose only difference was the box-local `GOOSE_MODE` leaves the host file, and its mtime,
untouched. Any other filtered spec that ever reaches the hook falls back to a plain wholesale copy,
the base-class default.

## The panel parity seam

`deliver_panel_permissions` persists the box's CASCADE-resolved `access` TIER as the top-level
`GOOSE_MODE` in the box's in-box `~/.config/goose/config.yaml` (FF-5 permission parity).

It exists because the `block.vscode-goose` panel spawns its own in-box goose WITHOUT kanibako's
launch env, so it never sees the `GOOSE_MODE` env var the CLI entrypoint sets. Without this write,
the panel would run at goose's own default while the CLI ran at the resolved tier.

⚑ **ASYMMETRIC vs claude.** `restricted` writes `approve` EXPLICITLY: an unset `GOOSE_MODE` defaults
to the permissive `auto`, so *clearing* the key would silently restore permissive — the opposite of
what a restricted tier asks for.

⚑ **`editing` is REFUSED.** goose has no mode that realizes it — `smart_approve` prompts for writes,
which is the inverse of claude's acceptEdits — and the launch has already refused that tier before
delivery ever runs.

The write is merge-preserving and idempotent; the mechanics live in
`kanibako.vscode.vscode_config.seed_goose_mode`. The descriptor is passed in from here because the
tier→`GOOSE_MODE` values are THIS descriptor's `access_realization` rows — the same ones the launch
emits, so the panel cannot drift from the CLI — and because core must not reach back into a named
plugin to read them.

goose declares NO directive-hook surface, so `deliver_directive_hook` stays the inherited no-op.

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

## Credentials — no legacy overrides here

There are deliberately no `refresh_credentials` / `writeback_credentials` overrides. goose is
descriptor-bearing, so its `secrets.yaml` host↔box SYNC is the `CredFileSpec` set in
`goose-defaults.yaml`, realized by the credsync engine (`seed_cred_files` / `refresh_cred_files` /
`writeback_cred_files`) — the §2d `synced` category view. The base no-op hooks are correct: the
legacy per-plugin refresh/writeback path is reached only when `desc is None`, which never holds for
goose.

`credential_check_path` and `invalidate_credentials` both name `~/.config/goose/secrets.yaml`, the
file whose presence stands for "goose has credentials".

## Provider and model are NEVER pinned by kanibako

This is one rule stated by three symbols, and it is the single easiest thing to break here.

`generate_agent_config` returns `state={}` — intentionally EMPTY. Forcing `GOOSE_PROVIDER` /
`GOOSE_MODEL` as defaults would override the user's in-box `goose configure` choice, because goose's
env vars win over its own `config.yaml`; that clobbers a provider (and key) the user selected
interactively.

`setting_descriptors` returns the three declared behavior keys, all with an EMPTY floor:

* **`provider`** and **`model`** carry NO default (`default=""`). The resolver floor therefore
  resolves them to empty when unset, and `assemble_env`'s `if value:` omits the env vars entirely —
  goose then reads provider/model from its own `config.yaml`. An EXPLICIT `agent.goose.provider` /
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
`GOOSE_MODEL` are ENV `SettingArg` rows in `goose-defaults.yaml`, emitted by `assemble_env` only
when the resolved value is non-empty. Goose overrides provider/model by env var rather than CLI
flag, so neither contributes an argv token. The retired `apply_state` hook did the same translation
in Python; it was removed once nothing dispatched it, and the descriptor rows are the whole route.

---

## Relocation pass, 2026-08-20

Comment ratio 66.0% → 50.0%. Everything removed from the source is above, in substance; nothing was
dropped as false or obsolete. `prose-relocation-check.py`: 179 prose lines at HEAD, 163 removed
(most of them reflowed rather than cut), **0 scoring below 0.6** against this document.

Kept in the `.py` under the keep test, because each one guards a specific line against a plausible
"tidy-up":

* the `_BINARY` `⚑` — `shutil.which` looks like the obvious idiom and is the vulnerability;
* `transform_cred`'s two in-body `⚑`s — the empty-box-config guard and the no-diff early return
  both read as redundancy and are data-protection;
* `deliver_panel_permissions`' two `⚑`s — clearing `GOOSE_MODE` restores permissive, and `editing`
  has no goose realization;
* the descriptor-passing `⚑` at `seed_goose_mode` — a reader would otherwise import the descriptor
  in core;
* the `NOTE:` on the absent credential overrides — it reads as a missing implementation;
* `generate_agent_config` / `setting_descriptors` — the empty state and empty floors read as
  oversights and are the whole provider/model rule.
