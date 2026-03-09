# Kanibako Target Plugin Examples

Three example plugins demonstrating how to write a kanibako target at
increasing levels of complexity.  Each is a complete, pip-installable
package that you can copy as a starting point for your own target.

See **[docs/writing-targets.md](../docs/writing-targets.md)** for the full
developer guide.

## Examples

| Example | Agent | Complexity | Key concepts |
|---|---|---|---|
| [kanibako-target-aider](kanibako-target-aider/) | [Aider](https://aider.chat) | Minimal | No-op credentials, no binary mounts |
| [kanibako-target-codex](kanibako-target-codex/) | [Codex CLI](https://github.com/openai/codex) | Moderate | npm tree walking, file-based credential sync |
| [kanibako-target-goose](kanibako-target-goose/) | [Goose](https://github.com/block/goose) | Advanced | Single-binary mount, YAML config filtering, session mapping |

## Quick start

Install an example in development mode:

```bash
pip install -e examples/kanibako-target-aider
```

Verify it registers correctly:

```bash
kanibako agent list   # should list "aider" under available targets
```

Run its tests:

```bash
pytest examples/kanibako-target-aider/tests/ -v
```

## Creating your own target

1. Copy the example closest to your agent's setup
2. Rename the package directory and update `pyproject.toml`
3. Implement the `Target` methods for your agent
4. Register the entry point under `kanibako.targets`
5. `pip install -e .` and test:
   ```bash
   kanibako agent list                        # verify target is registered
   kanibako box config target_name=yourname   # select it for a project
   kanibako start                             # launch
   ```
