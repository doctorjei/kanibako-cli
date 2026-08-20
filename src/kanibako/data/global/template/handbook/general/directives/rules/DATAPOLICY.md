<!--[STOCK]
### 2.1.2 Data Policy
_(System Tome)_

> This file holds the system-wide data policy; it is read from the global canon/handbook path and
> can only be edited from the host system by the user (not from within the box). Agent-editable
> instructions and information should go in the box's notebook (not here).
>
> This file is seeded from the core package. Defaults contents are included here; they are meant
> as a starting point and can be extended or replaced.
-->

#### Context Management

The document structure is designed around **deferred loading**:

- **Load pointers first, details later.** Critical items autoload; load others as needed.
- **Distinguish between stable & dynamic content.** Directives rarely change; devnotes always grow.
- **Don't load what you won't use.** Context can't be "unloaded", only cleared.

#### Git Safety

- **Don't commit, push, or amend unless authorized explicitly.**
- **Never commit** `~/vault/ro`, `~/vault/rw`, or credential files.
- **Never drop or clear a stash you did not create.**
- Commit authorship/identity conventions are **per-project** — follow the project's docs.

---

#### Records

Persist state incrementally, as you work. Context is summarized; containers are ephemeral. Durable
records live in files you write to bound paths. Capture decisions when they are made.

- **Write future work and multi-phase plans immediately**, before writing any code, once decided.
  Items only in conversation context vanish on clear or compaction.

- **Update the task list** as soon as items are completed or new work is identified.

- **Update devnotes after every build check** — green or red, document **immediately**, not "soon"
  or "at session's end"; each built check is a checkpoint.

- **If asked to update devnotes, plans, or other documentation,** do so immediately, as the user
  may be about to end the session or clear context.
