# Architecture

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

## Module Map

| Module | Role |
|--------|------|
| `cli.py` | Argparse tree, main() entry, `-v` flag |
| `log.py` | Logging setup (`-v` enables debug output) |
| `config.py` | TOML config loading, merge logic |
| `config_interface.py` | Unified config engine (get/set/reset/show for all levels) |
| `paths.py` | XDG resolution, mode detection, project init |
| `container.py` | Container runtime (detect, pull, build, run, stop, detach) |
| `shellenv.py` | Environment variable file handling |
| `snapshots.py` | Vault snapshot engine |
| `workset.py` | Working set data model and persistence |
| `names.py` | Project name registry (names.toml): register, resolve, assign |
| `agents.py` | Agent TOML config: load, write, per-agent settings |
| `templates.py` | Shell template resolution and application |
| `freshness.py` | Non-blocking image digest comparison |
| `targets/` | Agent plugin system (Target ABC + NoAgentTarget; ClaudeTarget in `kanibako-agent-claude`) |
| `plugins/` | Namespace package for built-in and bind-mounted plugins |
| `auth_parser.py` | Parse OAuth URL and verification code from `claude auth login` output |
| `auth_browser.py` | Automated OAuth refresh via headless Playwright browser |
| `browser_state.py` | Persistent browser context (cookies, localStorage) for OAuth session reuse |
| `browser_sidecar.py` | On-demand headless Chrome container for agent web access |
| `helpers.py` | B-ary numbering, spawn budget, directory/channel creation |
| `helper_listener.py` | Host-side hub: socket server, message routing, logging |
| `helper_client.py` | Container-side socket client for hub communication |
| `commands/` | CLI subcommand implementations |
| `containers/` | Bundled Containerfiles |
| `scripts/` | Bundled scripts: `helper-init.sh` (entrypoint wrapper), `kanibako-entry` (container CLI) |
