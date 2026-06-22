# Architecture

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

## Module Map

| Module | Role |
|--------|------|
| `cli.py` | Argparse tree, main() entry, `-v` flag |
| `log.py` | Logging setup (`-v` enables debug output) |
| `config.py` | YAML config loading, defaults, merge logic (`system.*` config tier) |
| `config_interface.py` | Unified config/settings engine (get/set/reset/show across box, workset, agent, system) |
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
| `containers/` | Bundled `Containerfile.template-<name>` toolchain templates (jvm/systems/js/dotnet/android) + `tmux.conf` (base rig images live in the kanibako-images repo) |
| `scripts/` | Bundled scripts: `helper-init.sh` (entrypoint wrapper), `kanibako-entry` (container CLI) |
