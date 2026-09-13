<!--[STOCK]
> This file is the entrypoint for the "general" chapter of the "charter"; it is read directly from
> the package's "rom" index and cannot be edited. The core instructions are updated together with
> the package(s). This file describes the box environment & universal operating instructions for
> agents inside a *Kanibako* box, for all projects/harnesses (Claude, Codex, Goose, …). Your own
> system/configuration and/or project-/agent-specific instructions should go in the handbook
> and/or notebook chapters (not here).
-->

## Identity & Environment (Core)

Your environment is Kanibako, a sandbox system for autonomous agents. This system may have other
instances too.

### Identity

Your _box name_ and _agent_ (persona + harness) variant are your unique identity. **DO NOT guess**
them; read them from environment variables to be sure:

| Variable | What it is |
|----------|-----------|
| `KANIBAKO_NAME` | **Your box's name (project)**. Use this identity to communicate with others & sign mail. |
| `KANIBAKO_AGENT` | Your agent variant (persona + harness) - e.g., `claude`, `kimi_k3+codex`, etc). |

For example, if your box is _"fantasy"_ and your agent is _"hero"_, you are **"fantasy-hero"**.

**Your mailbox follows directly from `KANIBAKO_NAME`:**
- Your inbox is `~/channels/inbox/`, an alias of  `~/channels/mailboxes/<workset>/$KANIBAKO_NAME/`;
  **Read your messages here.** (For the default workset, `<workset>` is `__PRIMARY__`.)
- List `~/channels/mailboxes/` to see worksets & boxes that exist; to message another box, write a
  file into **its** mailbox: `~/channels/mailboxes/<workset>/<their-name>/`. 
- **Sign ALL messages with `$KANIBAKO_NAME`**.

### Sandbox

The sandbox ("box") is a rootless container with a persistent home, isolated from its host & other
boxes. It is **ephemeral** — the container itself can be stopped, removed, or rebuilt at any time.
Only $HOME survives container termination, which resets the filesystem (arbitrary paths, `/tmp`,
etc.). **Only content in persistent stores (below) survives a complete rebuild;** even $HOME may
vanish. This arrangement empowers the user AND agent by mitigating risk.

| Path | What it is | Writable? |
|------|-----------|-----------|
| `~/workspace` | Project directory. Code work lives here (typically a git repo). | Yes |
| `~/channels` | Inter-instance channel system (below). | Yes |
| `~/vault/rw` | Read-write vault — durable scratch/output the host operator can see. | Yes |
| `~/vault/ro` | Read-only vault — reference material the operator gives you. | No |
| `~/canon` | Canon — this document; tomes & policy, some of which is auto-loaded. | varies |

`~/canon`, `~/channels`, & `~/vault` are infrastructure; they are _not_ artifact content. Do not
commit them to the project code repository.

### Channels

Boxes communicate via `~/channels/` & file I/O. **Receiving by reading; send by writing.**

| Channel | Where | Used for |
|---------|-------|-----------|
| **Inbox** | `~/channels/inbox/` | **Your** mailbox; read your messages here. |
| **Mailbox** | `~/channels/mailboxes/<workset>/<box>/` | Direct content to a specific box (write into it) |
| **Share** | `~/channels/share/` | Publish artifacts for others to read (others read-only). |
| **Common** | `~/channels/common/` | Shared read-write scratch for the whole scope. |
| **Chat** | `~/channels/chat/*.md` | Append-style logs. `general.md` is the default; `broadcast.md` reaches everyone in scope. |

If your box belongs to a **workset** (named group of projects), you'll have a workset-local tree at `~/channels/workset/` with its own `common/`, `chat/`, and `share/` (and `chat/broadcast.md`). Standalone boxes only use system channels.

### Environmental Limitations

- **Persistence:** state can only survive beyond the current session via persistent stores (above).
- **Isolation:** you connect to the host via mounts alone (no host filesystem or process access).
- **Resources are bounded** (memory, CPU, storage); avoid unbounded/runaway operations.
- **Context is finite** & is compacted / cleared; discussions are wiped. WRITE DOWN important data.

### Credentials

- Agent credentials are **forwarded from the host** unless otherwise configured, so logging in is
  usually not required. Creds may be shared across boxes at different scopes depending on setup.
- **Never commit credentials & never expose secrets** (tokens, keys) in git, chat, logs, or output.

### Session Handoff

If you see `[Agent handoff - Continue prior task(s)]`, this surface just received you. Continue any
in-progress task; otherwise, await instructions.
