# Architecture

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

## Module Map

| Module | Role |
|--------|------|
| `cli.py` | Argparse tree, main() entry, `-v` flag |
| `log.py` | Logging setup (`-v` enables debug output) |
| `config.py` | YAML config loading, defaults, merge logic (`system.*` config tier); agent resolution (`resolve_agent` cascade + installed-count rule, `resolve_and_load_settings` two-pass), setup-marker reader |
| `config_interface.py` | Unified config/settings engine (get/set/reset/show across box, workset, agent, system); `system.*` keys are file-only (refused at set/reset) with a programmatic `write_system_value` for `setup` |
| `errors.py` | Kanibako exception hierarchy (incl. `AgentResolutionError` / `NoAgentSelectedError` / `NoAgentInstalledError` / `AgentNotInstalledError`, surfaced verbatim by the top-level handler) |
| `install_method.py` | Detect kanibako's own install method (pipx/uv/pip) to tailor the "install a plugin" command in agent-resolution errors |
| `paths.py` | XDG resolution, mode detection (primary/named/standalone), box/workset init |
| `container.py` | Box runtime (detect, pull, build, run, stop, detach) |
| `shellenv.py` | Environment variable file handling |
| `snapshots.py` | Vault snapshot engine |
| `workset.py` | Workset data model and persistence (`<root>/settings.yaml`) |
| `names.py` | Project/workset name registry (the `projects`/`worksets` sections of `system.registry`) |
| `registry_store.py` | Consolidated `registry.yaml` index (`projects`/`worksets`/`connected`/`standalone`/`rigs`/`image_shells`) |
| `registry.py` | OCI Distribution API client for remote image digests (stdlib only) |
| `agent_config.py` | Per-agent YAML config (`agents/<agent>/settings.yaml`): load, write, resolve |
| `templates.py` | Layered seed-once template resolution and application (base → agent → workset) |
| `templates_image.py` | Image-template helpers: user-template image naming + bundled-template discovery (`Containerfile.template-<name>` convention, `# kanibako-template:` descriptions) |
| `containerfiles.py` | Resolve bundled/override Containerfiles by suffix (`get_containerfile`, `list_containerfile_suffixes`) |
| `freshness.py` | Non-blocking image digest comparison |
| `deprecation.py` | Deprecation-tracking registry + `@deprecated` decorator + `overdue_deprecations` helper, backing the CI gate (`tests/test_deprecations.py`) |
| `targets/` | Descriptor-only agent plugin system (Target ABC + `PluginDescriptor` + NoAgentTarget; `assembly.py` builds launch argv/binds, `credsync.py` runs the cred lifecycle; Claude/Goose/Codex in `kanibako-agent-*`) |
| `plugins/` | Namespace package for bind-mounted plugins (shipped agents propagate into nested boxes) |
| `auth_parser.py` | Parse OAuth URL and verification code from `claude auth login` output |
| `auth_browser.py` | Automated OAuth refresh via headless Playwright browser |
| `browser_state.py` | Persistent browser context (cookies, localStorage) for OAuth session reuse |
| `browser_sidecar.py` | On-demand headless Chrome container for agent web access |
| `helpers.py` | B-ary numbering, spawn budget, directory/channel creation |
| `helper_listener.py` | Host-side hub: socket server, message routing, logging |
| `helper_client.py` | Container-side socket client for hub communication |
| `commands/` | CLI subcommand implementations |
| `commands/flags.py` | Injects the blanket `--agent`/`--box` flags onto every leaf subparser, checks per-command flag relevance, and reconciles a positional subject against `--box` |
| `containers/` | Bundled `Containerfile.template-<name>` toolchain templates (jvm/systems/js/dotnet/android) + `tmux.conf` (base rig images live in the kanibako-images repo) |
| `scripts/` | Bundled scripts: `helper-init.sh` (entrypoint wrapper), `kanibako-entry` (container CLI) |

## Deprecating something (post-public)

Post-public, breaking changes happen only at **major** versions. To deprecate a
symbol now and remove it cleanly later, record it in `deprecation.py` so the CI
gate can enforce its removal at the right release.

The convention for every record is `{deprecated_in, remove_at, replacement}`:

- `deprecated_in` — the version that announces the deprecation.
- `remove_at` — the version at/after which the symbol MUST be gone; **the next
  major** (e.g. deprecate in `2.3.0` → `remove_at="3.0.0"`).
- `replacement` — what to use instead (free text; `""` if none).

For a **callable** (function/method), decorate it — registers automatically and
warns at runtime:

```python
from kanibako.deprecation import deprecated

@deprecated(deprecated_in="2.3.0", remove_at="3.0.0", replacement="new_thing()")
def old_thing(...):
    ...
```

For a **non-callable** (config key, CLI flag, env var, ...), register
declaratively where it is handled:

```python
from kanibako.deprecation import register

register("--legacy-flag", deprecated_in="2.3.0", remove_at="3.0.0",
         replacement="--new-flag", kind="cli-flag")
```

The gate (`tests/test_deprecations.py::test_no_overdue_deprecations`) fails the
build once `kanibako.__version__` reaches any record's `remove_at` and the entry
is still present — your cue to delete the symbol *and* its registry entry. With
an empty registry the gate passes trivially.
