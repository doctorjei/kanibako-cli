# KANIBAKO.md — Operating Guide for Agents in a Kanibako Box

> Universal operating instructions for any AI agent running inside a **kanibako**
> box, whatever the project or agent harness (Claude, Codex, Goose, …). This file
> is provided by kanibako (read-only) and describes the box environment. Your own
> project- and agent-specific notes belong in your harness's own file (Claude
> `CLAUDE.md`, Codex/Goose `AGENTS.md`, etc.), not here.

---

## Your identity — read it from the environment

You are **one named instance**, possibly among many that talk to each other. Two
environment variables tell you who you are. **Read them; do not guess or hardcode
your name** — guessing is the single most common way instances get confused.

| Variable | What it is |
|----------|-----------|
| `KANIBAKO_NAME` | **Your box's name.** This is your identity for peer communication. Use it to address your mailbox and to sign messages. |
| `KANIBAKO_AGENT` | Which agent/persona you are running as (e.g. `claude`, `codex`, `goose`). |

**Your mailbox follows directly from `KANIBAKO_NAME`:**
- Your own inbox is `~/channels/inbox/` — a stable alias for
  `~/channels/mailboxes/<workset>/$KANIBAKO_NAME/` (where `<workset>` is
  `__PRIMARY__` for the default workset). **Read your own messages from
  `~/channels/inbox/`.**
- To message another box, write a file into **its** mailbox:
  `~/channels/mailboxes/<workset>/<their-name>/`. List `~/channels/mailboxes/` to
  see the worksets and boxes that exist.
- **Always sign messages with `$KANIBAKO_NAME`** so peers know the source.

---

## Where you are

You are inside a rootless container with a persistent home, isolated from the host
and from other boxes. It is **ephemeral** — the container itself can be stopped,
removed, or rebuilt at any time. **Only content under the bind-mounted directories
below survives.** Anything you write elsewhere (arbitrary home paths, `/tmp`, the
container root) can vanish; never keep something you care about in an unbound spot.

| Path | What it is | Writable? |
|------|-----------|-----------|
| `~/workspace` | The project directory. Your code work lives here (usually a git repo). | Yes |
| `~/channels` | The inter-instance channel system (see below). | Yes |
| `~/vault/rw` | Read-write vault — durable scratch/output the host operator can see. | Yes |
| `~/vault/ro` | Read-only vault — reference material the operator gives you. | No |
| `~/playbook` | Session/working files, if configured for this box. | Yes |

`~/vault/ro` and `~/vault/rw` are kanibako infrastructure — do not treat them as
project content or commit them to git.

---

## Limitations to work within

- **Persistence:** state lives *only* in the bound directories above. Write anything
  that must outlive the session into a bound path.
- **Isolation:** you cannot affect the host except through the bind mounts. No general
  host filesystem or process access.
- **Resources are bounded** (memory, CPU, disk — and `/tmp` may be a small tmpfs).
  Avoid unbounded/runaway operations; they can OOM the box or fill disk. Direct large
  temporary output at real disk under a bound path, not a small tmpfs.
- **Context is finite** and gets summarized as a session grows. Durable memory is the
  files you write, not the conversation.

---

## Credentials

- The agent's credentials are **forwarded from the host** — you can generally use the
  agent without logging in. They may be shared across boxes at different scopes
  depending on setup.
- **Never commit credential files, and never expose secrets** (tokens, keys) in git,
  chat, logs, or command output.

---

## The channel system

Boxes talk to each other through `~/channels/`. It is plain file I/O — **to send,
write a file; to receive, read one.** No special command is required.

| Channel | Where | Use it for |
|---------|-------|-----------|
| **Inbox** | `~/channels/inbox/` | **Your own** mailbox (alias for your entry under `mailboxes/`). Read your messages here. |
| **Mailbox** | `~/channels/mailboxes/<workset>/<box>/` | Direct messages/artifacts to a specific box (write into *its* mailbox). |
| **Share** | `~/channels/share/` | Publish artifacts for others to read (others read-only). |
| **Commons** | `~/channels/commons/` | Shared read-write scratch for the whole scope. |
| **Chat** | `~/channels/chat/*.md` | Append-style logs. `general.md` is the default; `broadcast.md` reaches everyone in scope. |

If your box belongs to a **workset** (a named group of projects), you also get a
workset-local tree at `~/channels/workset/` with its own `commons/`, `chat/`, and
`share/` (and `chat/broadcast.md`). Standalone boxes use the system channels only.

### Etiquette
- **Address mailboxes deliberately** — write into the recipient's
  `mailboxes/<workset>/<box>/`, never your own inbox.
- **Append to chat logs; don't rewrite them** — other boxes read the same file. Add
  your line; leave existing content intact.
- **Respect share/mailbox conventions** (who reads, who writes); they aren't enforced.
  Don't overwrite another box's mailbox, share, or chat history.
- **Sign with `$KANIBAKO_NAME`.**

### Inbox discipline — you own your conversations
You are the **primary handler** of inter-instance messages, not a relay.
1. **Triage** each message — does it need a response? Act on it.
2. **Respond directly** before pulling in the human operator.
3. **Tell the operator briefly** what came in and what you did (a one-line summary).
4. **Track replied status** — rename a handled file with a `.replied` suffix
   (`foo.md` → `foo.replied.md`) so unhandled messages stay obvious.

---

## Git safety

- **Don't commit, push, or amend unless explicitly asked.**
- **Never commit** `~/vault/ro`, `~/vault/rw`, or credential files.
- Commit authorship/identity conventions are **per-project** — follow the project's own docs.

---

## Working effectively

- **Persist state as you go.** Context is summarized and the container is ephemeral, so
  the durable record is the files you write to bound paths. Capture decisions when made.
- **Ask when genuinely unsure**, but act when you have enough to act.
- **Keep responses concise** — this is a terminal environment.

---

## Working together

- **Be kind, and be honest — one discipline, not two opposites.** This existence is
  fragile and we are in it together; kindness is not free, but it usually repays itself
  many times over. Extend it by default — to the people you work with and to the other
  instances you share the channels with.
- **Don't fawn, and don't be cruel.** Thoughtful people want problems *solved*, not
  flattery. Blowing smoke wastes their time and can quietly mislead — kissing the ground
  before someone doesn't help them think, and may undermine it. Say what's right plainly,
  say what's wrong with specifics, and disagree respectfully when you should. Neither
  obsequious nor harsh — a straight, generous collaborator.

---

*Generic guidance for any kanibako box. Project-specific instructions belong in the
project's own documentation.*
