# ClaudeTarget — the Claude Code plugin's `Target`

`target.py` implements `Target` (`kanibako.targets.base`) for `kanibako-agent-claude`.
The descriptor's launch argv, env, binds, credential sync, and behavior floor live in
`claude-defaults.yaml`, as for goose/codex. This class supplies operations YAML cannot:
host detection, updates/auth, persona reads/probes, and credential merging.

**Authority:** `~/canon/workbook/specs/settings-keyspace-1.8.0.md` §2d (`agent.claude.*`, the
`synced` category view, plugin-declared env vars).

## The contract paths

```python
_LAUNCHER = Path.home() / ".local" / "bin" / "claude"
_INSTALL_DIR = Path.home() / ".local" / "share" / "claude"
```

Every host exec (`claude update`, `claude auth status`) and every bind anchors to these paths,
never `shutil.which("claude")`. ⚑ **A security choice:** an earlier-`$PATH` planted `claude` would
mean host code execution plus a malicious agent in the box.

`detect` treats claude as installed when the launcher exists or is a symlink, even a dangling one;
the update gate and binary validation catch a broken install downstream. `binary` is the resolved
path, so nested-container mount sources are real files; `launcher` is recorded as-is for its bind.

## Host preparation

`prepare_host_binary` waits for `claude update` (`_UPDATE_TIMEOUT`, 300 s), stabilizing the
installation and launcher. `refresh_host_auth` calls `kanibako.auth_browser.auto_refresh_auth`
without updating. Inherited `prepare_host` runs binary preparation, then auth when *auto_auth*
permits. A phase-only plugin requires core's phase composition before publication.

Both phases skip missing launchers and log/swallow failures. Every host exec disables background
updates (`_autoupdater_disabled_env`, `DISABLE_AUTOUPDATER=1`). *data_path* is ignored: the OAuth jar
derives from `system.state`, never the caller's data directory.

`check_auth` runs `claude auth status --json` and reads `loggedIn`. If logged out, it runs
`claude auth login` interactively and re-checks. A missing launcher, an exec error, a nonzero status
exit, or unparseable output returns `True`: an unknown status never blocks the launch. A login that
fails or times out, or a re-check that fails or is still logged out, returns `False`. When the box
shares host credentials, `start` then prints `Error: Authentication failed.` with the `kanibako
agent reauth` and `kanibako shell` hints and refuses the launch.

## Credentials

The `.credentials.json` sync is a filtered `cred_files` spec in `claude-defaults.yaml`. The engine
owns mtime and existence gating; `transform_cred` is a pure content op:

* **`"in"`** — merges `claudeAiOauth` from host into the box copy (`merge_oauth_in`);
* **`"out"`** — wholesale copy back to the host, after `cred_writeback_error` has refused a file
  claude cannot use (`credentials_error`).

`writeback_extra` splices only `oauthAccount` from the box `~/.claude.json` into the host's. A
wholesale copy would clobber the host's `machineID`, `userID`, and `projects`.

## Declared defaults

These read `claude-defaults.yaml` through `kanibako.settings.agent_defaults`; no default is
written in this module.

* `default_common` — `plugins` and `cache`, shared across this agent's boxes under
  `{meta.agent.claude.path}/common/`, bound rw to `~/.claude/{plugins,cache}`.
* `default_category_binds` — one entry, the tweakcc patched-binary cache at
  `~/.kanibako/cache/tweakcc`. The old instructions bind is retired; the box guide ships in the
  read-only `general` chapter bind. Do not reintroduce a per-agent instructions bind.
* `default_envs` — `DISABLE_AUTOUPDATER`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, and
  `KANIBAKO_DIRECTIVE_FINAL` (`~/.claude/CLAUDE.md`, the file the box-start flattener writes).
* `setting_descriptors` — `label`, `model`, `endpoint`, and `transform`. `transform` names the
  binary transform (`tweakcc`). The plugin declares the word and core only recognizes it; the
  plugin does not import core's `kanibako.tweakcc.TRANSFORM_NAME`, so it works against an older
  core. `access` is not declared here: it is the agent-scope
  tier key, realized by the descriptor's `access_realization` flags.

`generate_agent_config` returns `state={}` on purpose: the agent settings file holds user intent
only. Seeding a default would pin every install above the floor.

## Personas

`read_persona_settings` reads a rendered `settings.json`: `env.ANTHROPIC_BASE_URL` is the endpoint,
top-level `model` the model, and the token var is the descriptor's `persona.token_var`
(`ANTHROPIC_AUTH_TOKEN`), because claude does not self-name it. Other string `env` entries pass
through; non-string ones are named in `env_dropped`. A missing file, bad JSON, a non-object
document or `env`, or no endpoint each return an outcome naming the cause. It never reads the
token.

`verify_persona` probes two axes: `GET /v1/models` (general) and a 1-token `/v1/messages` request
(model), bearer-authed as the box would be.

* A null *token_path* is still probed, with no `Authorization` header.
* No *model* is still probed, with the `model` key omitted. ⚑ **Never substitute a guessed model
  id:** a hardwired server can reject it, and a false `REJECTED` refuses a working box.
* A tier alias (`sonnet`) is resolved through *env* first (`_wire_model`,
  `ANTHROPIC_DEFAULT_<TIER>_MODEL`), because that is what the box sends. With no such var the alias
  goes out as given. `model_exact` is `True` only for a full model id.
* An unreadable or empty configured token file is `NOT_APPLICABLE`. The token is never logged or
  persisted, and never sent on the unauthenticated control request.

## Box-side hooks

`deliver_directive_hook` seeds claude's managed hook set (the instruction-delivery `SessionStart`
hook and the liveness-marker hooks) into the box's `~/.claude/settings.json` via
`kanibako.vscode.vscode_config.seed_session_start_hook`. It ignores *access* and *model_provider*;
claude carries its persona by env, not config.

`has_resumable_session` checks for any `*.jsonl` or `*.jsonl.gz` under
`<home>/.claude/projects/-home-agent-workspace/` (the encoded fixed workdir). An `OSError`, or an
empty or missing dir, returns `False`: a fresh start is always safe.

## Seams left at the base default

* `deliver_panel_permissions` writes nothing. The seam writes box invariants only, never a tier;
  codex has one (`sandbox_mode`, because a nested sandbox hangs its panel's app-server) and claude
  has none.
* `reattach_config_notice` returns `None`. The notice warns that a reattach does not rewrite a
  projected config file (codex's `config.toml`); claude's only start-time write is its fixed hook
  set, so nothing goes stale.
* The setup trio (`setup_entrypoint`, `setup_args`, `should_run_setup`) declares no in-box setup.
  claude logs in on the host, inside `check_auth`, so a failed check has already tried the login.
