# kanibako-plugin-claude

Claude Code target plugin for [kanibako](https://github.com/doctorjei/kanibako).

This plugin provides `ClaudeTarget`, which enables kanibako to detect, mount,
and manage Claude Code sessions inside containers.

## Installation

Most users install the `kanibako` meta-package, which includes this plugin:

```bash
pip install kanibako
```

To install the plugin separately (e.g. adding Claude support to a `kanibako-base`
install):

```bash
pip install kanibako-plugin-claude
```

## What it provides

- **Auto-detection** of the Claude Code binary on the host
- **Credential forwarding** — `~/.claude/.credentials.json` synced in/out
- **CLI argument building** — `--continue`, `--resume`, `--dangerously-skip-permissions`
- **Resource scoping** — shared plugins, seeded settings, project-local data
- **Runtime settings** — model selection, permission mode
- **Authentication** — `claude auth status` check with interactive login fallback

## Entry point

Registered as `claude` in the `kanibako.targets` entry point group.

Requires `kanibako-base` (the core package).
