# Efficiency in Memory

## Context Management

The document structure is designed around **deferred loading**:

- **Load pointers first and details later.** Critical items are autoloaded; load others as needed.
- **Distinguish between stable & dynamic content.** Directives rarely change; devnotes always grow.
- **Script docs live in `--help`;** don't load documentation until you need to run the tool.
- **Avoid loading what you won't use.** Context can't be "unloaded" cheaply.

---

## Git Safety

- **Don't commit, push, or amend unless explicitly asked.**
- **Never commit** `~/vault/ro`, `~/vault/rw`, or credential files.
- Commit authorship/identity conventions are **per-project** — follow the project's own docs.

---

## Records

Persist state as you go. Context is summarized; the container is ephemeral. The durable record
is the files you write to bound paths. Capture decisions when they are made.

- **Write future work immediately.** When future work is discussed in conversation, record it right
  away. Items that only exist in conversation context are lost on clear or compaction.

- **Update the task list** — when items are completed or new work identified, update the file ASAP.

- **Record multi-phase plans immediately** — before writing any code. Any plans that only exist in
  conversation context are lost on if/when context is cleared and sometimes on compaction.

- **Update devnotes after every build check** — green or red, document immediately. Not at phase
  end, not at session end - each build check is a checkpoint.

- **If asked to update devnotes, plans, or any other documentation,** do so immediately. The user
  may be about to end the session.

---
