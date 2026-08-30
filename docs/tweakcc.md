# tweakcc Integration

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

tweakcc patches Claude Code's embedded cli.js bundle to customize system
prompts, toolsets, and UI behavior.  When enabled in the agent config,
Kanibako orchestrates the full patching lifecycle:

1. Computes a content hash of the host binary's embedded cli.js
2. Merges config layers: kanibako defaults -> external config file -> inline overrides
3. Checks the flock-based binary cache (at `@system.cache/tweakcc`, i.e.
   `$XDG_CACHE_HOME/kanibako/tweakcc/`)
4. On cache miss, copies the binary and invokes tweakcc to patch it
5. Mounts the cached patched binary into the box
6. Propagates the cache to helper boxes

**Note:** tweakcc is a Node.js package and requires Node.js on the host (or
in the box where patching runs).  The patching invocation is under
active development -- see the implementation plan for current status.

Enable in the agent config (`agents/claude/agent.yaml`):

```yaml
self:
  transform_settings:
    enabled: true
    config: "~/.tweakcc/config.json"
```

Inline settings override the external config:

```yaml
self:
  transform_settings:
    enabled: true
    config: "~/.tweakcc/config.json"
    settings:
      misc:
        mcpConnectionNonBlocking: true
```

If patching fails (missing tweakcc, bad binary, etc.), Kanibako falls back
gracefully to the unpatched binary.

The `config` and inline `settings` are read from the `self.transform_settings:`
section of the agent's settings file (it is part of the per-agent `AgentConfig`).
