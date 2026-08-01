<!--[STOCK]
# System Tome: Canon

> This file holds the system-wide canon policy; it is read from the global canon/handbook path and
> can only be edited from the host system by the user (not from within the box). Agent-editable
> instructions and information should go in the box's notebook (not here).
>
> This file is seeded from the core package. Defaults contents are included here; they are meant
> as a starting point and can be extended or replaced.
-->
## Canon Structure

The 'Canon' refers to instructions and information metadata of three distinct parts / levels:

1. Bible (default: `~/canon/bible`) - Read-only core tome; read-only (read from core & plugins)
2. Handbook (default: `~/canon/handbook`) - System tome; user editable, but read-only to agents
3. Notebook (default: `~/canon/notebook`) - Project/box specific directives, historical data & resources; agent-editable.
4. Workbook (default: `~/canon/workbook`) - Project/box specific process, progress, state, & other data (optional, but recommended)

By separating the canon elements from the workspace, the workspace itself can remain dedicated to
project source, build, documentation, and other resources required to construct the project (vs
information about its process / progress / state / etc.) 

**These are directives, not suggestions. The user may edit, override, or remove any of them; absent
that, follow them as written.**

When editing, **create each directory listed when first writing into it; do not pre-create them.** A
directory that does not yet exist is not missing — it has no content yet.

---

### Handbook

The user may grant agents access to edit the handbook documents; when doing so, observe and
reinforce existing layouts, design approaches, and structure (subject to user requests or
commands). Handbook editing requires special action by the user to grant an agent access to
those host-level files by invoking a one-time external mount.

---

### Notebook

Split directives and/or information in the notebook into multiple files as useful for organization.
These are the standard, recognized Kanibako playbook formats & paths:

| Path (`~/canon/notebook/`) | Description | Loaded at start by default? |
|------------------|---------------------------------------------------------------------|-----|
| `MY_CONTENTS.md` | Read into context at load by default handbook; notebook entry point | Yes |
| `archives/` | Completed plans & historical devnotes, documents, & information (i.e., "the archive" or "the archives"); the **authoritative historical record** | No |
| `directives/CONVENTIONS.md` | Technical expectations (coding, architecture, commands, etc) | Typically (requires reference) |
| `directives/` | Additional directive files, as appropriate | Typically (requires reference) |
| `procedures/` | Information about specific procedures / actions and how to do them | No |
| `resources/` | Generally useful resources | No |
| `scripts/` | Reusable helper scripts | No |

Keep script documentation in `--help` within scripts themselves, not in session files to avoid
duplication and reduce context consumption.

---

### Workbook

The project/box-specific `~/canon/workbook` also has standard conventions:

| Path (relative) | Description | Loaded at session start by default? |
|---------------------------|-------------|-------------------------------------|
| `devnotes.md` | Detailed changelog & current status; should reflect relevant actions/updates | Yes |
| `tasks.md` | Task board (kanban or simple list) | When working on task(s); _Authoritative_. |
| `designs/` | Project design(s) | As relevant to current task(s); _Authoritative_. |
| `plans/` | Active implementation plans | When executing a plan |
| `specs/` | Project specification(s) | As relevant to current task(s); _Authoritative_. |
| `temp/` | Temporary notes & storage (should be assumed volatile between sessions) | No |
| `temp/scripts` | one-off scripts (used once or a few times at most) | No |
| `temp/testing` | Temporary testing data / logs | No |

**Splitting:**
- If architecture in `CONVENTIONS.md` exceeds ~100 lines, separate into `ARCHITECTURE.md`.
- If task tracking outgrows `tasks.md`, archive completed items before adopting a dedicated tool;
  a dedicated tool is rarely warranted

**Archives:** Don't read the archives unless needed; only reference it when debugging or to
contextualize legacy behavior or past decisions. It preserves history without consuming context.

_All scripts, plans, documents, and other resources that have become historical_ (i.e., are not
related to the current or future design, planning, and/or development) should be moved to the
archives, including completed plans. Current / active documents and/or logs (e.g., `devnotes.md`
and `tasks.md`) should truncate old entries, moving those old entries _verbatim_ into the archives
with an appropriate prefix/suffix.

---

