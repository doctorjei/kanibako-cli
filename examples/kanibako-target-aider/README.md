# kanibako-target-aider

Minimal kanibako target plugin for [Aider](https://aider.chat).

This is the simplest possible target implementation, intended as a starting
point for writing your own.  Most methods are no-ops because aider:

- Is a pip-installed Python package (no binary mounting needed)
- Uses environment variables for API keys (no credential sync)
- Has a simple CLI interface

## Install

```bash
pip install -e .
```

## Usage

```bash
# Auto-detect (if aider is on PATH and no other target takes priority)
kanibako start

# Explicit target selection (persisted in project config)
kanibako box config target_name=aider
kanibako start
```

## What this example demonstrates

- Minimal `detect()` using `shutil.which`
- Empty `binary_mounts()` return
- No-op `init_home()`, `refresh_credentials()`, `writeback_credentials()`
- Simple `build_cli_args()` mapping `safe_mode` to `--yes`

See [docs/writing-targets.md](../../docs/writing-targets.md) for the full
developer guide.
