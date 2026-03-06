# Peer Communication Design

Cross-project communication between kanibako instances via shared
filesystem. No special tooling — agents read/write plain files.

## Identity

Each container gets `KANIBAKO_NAME` env var (from `proj.name`).
Agents use this to sign messages.

## Host Directory

Default: `$XDG_DATA_HOME/kanibako/comms/`, configurable via
`paths.comms` in `kanibako.toml`.

```
comms/
├── broadcast.log          # shared append log (all instances)
└── mailbox/
    ├── droste/
    │   └── messages.log   # droste's inbox
    ├── tenkei/
    │   └── messages.log
    └── kento/
        └── messages.log
```

## Container Mount

`comms/` → `/home/agent/comms/` (read-write, `Z,U`).

Agent sees:
- `/home/agent/comms/broadcast.log` — global chat
- `/home/agent/comms/mailbox/{name}/messages.log` — per-instance inbox

## Message Convention

Not enforced, documented:

```
droste [2026-03-06 15:15:15]: Hello everyone
tenkei [2026-03-06 15:16:02]: Got it, working on the API
```

## Config

```toml
# kanibako.toml
[paths]
comms = "comms"    # relative to data_path, or absolute
```

## Implementation Steps

1. Add `paths_comms` field to `KanibakoConfig` (default `"comms"`)
2. Inject `KANIBAKO_NAME` env var in `start.py` (from `proj.name`)
3. Mount `comms/` directory into container at `/home/agent/comms/`
4. Create `comms/mailbox/{name}/` on project start (lazy)
5. `kanibako setup` creates `comms/` and `comms/mailbox/` dirs
6. Touch `comms/broadcast.log` if it doesn't exist
7. Tests for env var injection, mount generation, directory creation
