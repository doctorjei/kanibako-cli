# kanibako-target-goose

Advanced kanibako target plugin for [Goose](https://github.com/block/goose).

This example demonstrates the full range of target capabilities:

- **Single-binary detection and mounting** — Goose is a standalone compiled
  binary, so detection is simple and only one mount is needed
- **Config filtering** — only safe keys are copied from host config;
  credential keys are handled separately
- **Credential field merging** — credential keys are merged into/from the
  project config without disturbing other settings
- **Full CLI argument mapping** — maps `resume_mode` to `session resume` vs
  `session start`, and `safe_mode` to `--approve-all`

## Install

```bash
pip install -e .
```

## Usage

```bash
# Explicit target selection (persisted in project config)
kanibako box config target_name=goose
kanibako start
```

## What this example demonstrates

| Method | What it does |
|---|---|
| `detect()` | Single binary via `shutil.which` |
| `binary_mounts()` | Single mount: binary → `/home/agent/.local/bin/goose:ro` |
| `init_home()` | Creates `.config/goose/`, copies filtered config (no credentials) |
| `refresh_credentials()` | Merges credential keys from host config into project config |
| `writeback_credentials()` | Merges credential keys from project config back to host |
| `build_cli_args()` | `session start`/`resume` + `--approve-all` mapping |

## Config filtering

Goose stores both settings and credentials in the same config file.  This
plugin separates them:

- **Safe keys** (copied in `init_home`): `provider`, `model`, `extensions`,
  `instructions`
- **Credential keys** (synced in `refresh/writeback`): `GOOSE_API_KEY`,
  `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`

See [docs/writing-targets.md](../../docs/writing-targets.md) for the full
developer guide.
