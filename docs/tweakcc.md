# tweakcc Integration

> This section was moved from the main README.  See
> [README.md](../README.md) for an overview of Kanibako.

tweakcc patches Claude Code's embedded cli.js bundle to customize system
prompts, toolsets, and UI behavior.  When enabled in the agent config,
Kanibako orchestrates the full patching lifecycle:

1. Computes a content hash of the host binary's embedded cli.js
2. Merges config layers: kanibako defaults -> external config file -> inline overrides
3. Checks the flock-based binary cache (at `$XDG_CACHE_HOME/kanibako/tweakcc/`)
4. On cache miss, copies the binary and invokes tweakcc to patch it
5. Mounts the cached patched binary into the container
6. Propagates the cache to helper containers

**Note:** tweakcc is a Node.js package and requires Node.js on the host (or
in the container where patching runs).  The patching invocation is under
active development -- see the implementation plan for current status.

Enable in the agent TOML:

```toml
[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"
```

Inline settings override the external config:

```toml
[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"

[tweakcc.settings.misc]
mcpConnectionNonBlocking = true
```

If patching fails (missing tweakcc, bad binary, etc.), Kanibako falls back
gracefully to the unpatched binary.
