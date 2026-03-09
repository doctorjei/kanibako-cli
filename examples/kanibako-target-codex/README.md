# kanibako-target-codex

Moderate-complexity kanibako target plugin for [Codex CLI](https://github.com/openai/codex).

This example demonstrates:

- **Binary detection** with npm tree walking — resolves the `codex` symlink
  and walks up to find the npm package root (similar to the built-in
  ClaudeTarget)
- **Binary mounting** — mounts both the npm package directory and the binary
  symlink into the container
- **File-based credential sync** — copies `config.json` between host and
  project home using mtime-based freshness
- **Home initialization** — creates `.codex/` and seeds it with host config

## Install

```bash
pip install -e .
```

## Usage

```bash
# Explicit target selection (persisted in project config)
kanibako box config target_name=codex
kanibako start
```

## What this example demonstrates

| Method | What it does |
|---|---|
| `detect()` | Walks npm tree to find package root |
| `binary_mounts()` | Mounts install dir + binary symlink (read-only) |
| `init_home()` | Creates `.codex/`, copies `config.json` from host |
| `refresh_credentials()` | Copies `config.json` if host is newer |
| `writeback_credentials()` | Copies `config.json` back if project is newer |
| `build_cli_args()` | Maps `safe_mode` → omit `--full-auto` |

See [docs/writing-targets.md](../../docs/writing-targets.md) for the full
developer guide.
