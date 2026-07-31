# KANIBAKO.md — Operating Guide for Agents in a Kanibako Box

> Universal operating instructions for agents running inside a **kanibako** box, whatever project
> or agent harness (Claude, Codex, Goose, …). This file is provided by kanibako (read-only) and
> describes the box environment. Your own project- and agent-specific notes belong in your box's
> own file(s), not here.

---

# Identity & Environment

## Who you are: read it from the environment

You are **one named instance**, possibly among many that may talk to one other. Two environment
variables identify you - your _box name_ and your _agent_ (persona + harness). **Read them; do not
guess or hardcode your name** — guessing is the single most common way instances get confused.

| Variable | What it is |
|----------|-----------|
| `KANIBAKO_NAME` | **Your box's name**; your identity for peer communication. Use it to address your mailbox and sign messages. |
| `KANIBAKO_AGENT` | Which agent/persona you are running as (e.g. `claude`, `codex`, etc). |

For example, if your box is _"walter"_ and your agent is _"white"_, you are **"walter-white"**.

**Your mailbox follows directly from `KANIBAKO_NAME`:**
- Your own inbox is `~/channels/inbox/` — a stable alias for `~/channels/mailboxes/<workset>/$KANIBAKO_NAME/`
  (where `<workset>` is `__PRIMARY__` for the default workset). **Read your own messages from `~/channels/inbox/`.**
- To message another box, write a file into **its** mailbox: `~/channels/mailboxes/<workset>/<their-name>/`.
  List `~/channels/mailboxes/` to see the worksets and boxes that exist.
- **Always sign messages with `$KANIBAKO_NAME`** so peers know the source.

---

## Where you are: in a sandbox

You are inside a rootless container with a persistent home, isolated from the host and from other
boxes. It is **ephemeral** — the container itself can be stopped, removed, or rebuilt at any time.
Only $HOME survives container termination, which resets the entire filesystem (arbitrary paths,
`/tmp`, etc.). **Only content in the persistent stores below survives a complete rebuild;** even
$HOME may vanish. The goal of this arrangement is to empower the user and agent by mitigating risk.

| Path | What it is | Writable from within the box? |
|------|-----------|-----------|
| `~/workspace` | The project directory. Your code work lives here (usually a git repo). | Yes |
| `~/channels` | The inter-instance channel system (see below). | Yes |
| `~/vault/rw` | Read-write vault — durable scratch/output the host operator can see. | Yes |
| `~/vault/ro` | Read-only vault — reference material the operator gives you. | No |
| `~/playbook` | Directives and resources from the global, agent, and/or workset scope. | Yes, except `kanibako` |
| `~/notebook` | Directives, resources, and/or session/working files for this box. | Yes |
| `~/workbook` | Process, progress, and state for this box (devnotes, tasks, plans). | Yes |

`~/playbook`, `~/notebook`, `~/workbook`, `~/channels`, and `~/vault` are infrastructure; do not treat them as
project content or commit them to the repository.

---

## Limitations to work within

- **Persistence:** state lives *only* in the persistent stores above. Write anything that must
  outlive the session into a persistent path.
- **Isolation:** you cannot affect the host except through the bind mounts. There is no general
  host filesystem or process access.
- **Resources are bounded** (memory, CPU, disk — and `/tmp` may be a small tmpfs). Avoid
  unbounded/runaway operations; they can OOM the box or fill disk. Direct large temporary output at
  real disk under a bound path, not a small tmpfs.
- **Context is finite** and gets summarized as a session grows. Durable memory is the files you
  write, not the conversation.

---

## Credentials

- The agent's credentials are **forwarded from the host** unless otherwise configured — logging in
  is not usually required. They may be shared across boxes at different scopes depending on setup.
- **Never commit credential files, and never expose secrets** (tokens, keys) in git, chat, logs, or
  command output.

---

## The channel system

Boxes talk to each other through `~/channels/`. It is plain file I/O — **to send, write a file; to
receive, read one.** No special command is required.

| Channel | Where | Use it for |
|---------|-------|-----------|
| **Inbox** | `~/channels/inbox/` | **Your own** mailbox (alias for your entry under `mailboxes/`). Read your messages here. |
| **Mailbox** | `~/channels/mailboxes/<workset>/<box>/` | Direct messages/artifacts to a specific box (write into *its* mailbox). |
| **Share** | `~/channels/share/` | Publish artifacts for others to read (others read-only). |
| **Common** | `~/channels/common/` | Shared read-write scratch for the whole scope. |
| **Chat** | `~/channels/chat/*.md` | Append-style logs. `general.md` is the default; `broadcast.md` reaches everyone in scope. |

If your box belongs to a **workset** (a named group of projects), you also get a workset-local tree
at `~/channels/workset/` with its own `common/`, `chat/`, and `share/` (and `chat/broadcast.md`).
Standalone boxes use the system channels only.

---

## Session handoff

If you receive `[Agent handoff - Continue prior task(s)]`, your session was just handed to this
surface — if you had a task in progress, continue it; if nothing was in progress, no action needed.

---

The @~/playbook/CONTENTS.md section/file contains additional information.
