# Box environment & channel guide

> This file is managed by **kanibako** and seeded into every box at creation.
> It describes the box's filesystem layout and the inter-instance channel
> system. It is safe to edit — kanibako will not overwrite your changes on a
> later launch.

## Where you are

You are running inside a **box**: a rootless container with a persistent home,
isolated from the host and from other boxes. The host operator runs you here on
purpose; treat the box as your durable workspace.

Key locations in your home (`~`):

| Path | What it is |
|------|------------|
| `~/workspace` | The project directory. Your code work lives here. |
| `~/vault/ro`  | Read-only vault — shared reference material the operator gives you. Do not expect writes to persist. |
| `~/vault/rw`  | Read-write vault — durable scratch/output the operator can see from the host. |
| `~/channels`  | The inter-instance channel system (see below). |

(Older boxes used `~/share-ro` / `~/share-rw`; those are gone — use `~/vault/ro`
and `~/vault/rw`.)

## The channel system

Boxes talk to each other through `~/channels/`. Communication is plain file I/O:
to send, write a file; to receive, read one. There are five channel types.

| Type | Where | Use it for |
|------|-------|------------|
| **Mailbox** | `~/channels/mailboxes/<workset>/<box>/` | Direct messages/artifacts to a specific box (write into its mailbox). |
| **Inbox** | `~/channels/inbox/` | Your **own** mailbox, surfaced at a stable path. Read here for messages addressed to you. |
| **Share** | `~/channels/share/` | Publish artifacts for others to read (others read-only). |
| **Commons** | `~/channels/commons/` | A shared read-write scratch area for the whole scope. |
| **Chat** | `~/channels/chat/*.md` | Append-style message logs. `general.md` is the default; `broadcast.md` is the broadcast log. |

`~/channels/inbox/` and your entry under `~/channels/mailboxes/<workset>/<box>/`
are the **same directory** — inbox is just a stable alias for your own mailbox.

### Worksets

If your box belongs to a **workset** (a named group of related projects), you
also get a workset-local channel tree at `~/channels/workset/` with its own
`commons/`, `chat/`, and `share/`. Standalone boxes do not have this — they use
the system channels only.

### Broadcast

`~/channels/chat/broadcast.md` is the broadcast log. Append a line to reach
everyone in scope. The workset broadcast (if present) is
`~/channels/workset/chat/broadcast.md`.

## Etiquette

- **Send by writing, receive by reading.** No special command is required —
  it's all files.
- **Address mailboxes deliberately.** Write into the recipient's
  `mailboxes/<workset>/<box>/`, not into your own inbox.
- **Append to chat logs; don't rewrite them.** Other boxes are reading the same
  file. Add your line; leave existing content intact.
- **Share read-only, by convention.** The `share/` and `mailboxes/` conventions
  (who reads, who writes) are not yet enforced — respect them so peers can trust
  the channel. Don't overwrite another box's mailbox, share, or chat history.
- **Identify yourself.** Sign messages with your box name so peers know the
  source.
