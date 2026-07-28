# Handbook Structure

The 'Handbook' refers to metadata of three distinct parts / levels:

1. Playbook (default: `~/playbook`) - The global, workset, and agent directives & resources
2. Notebook (default: `~/notebook`) - Project/box specific directives, historical data, and resources
3. Workbook (default: `~/workbook`) - Project/box specific process, progress, state, & other data

By separating the handbook elements from the workspace, the workspace itself can remain dedicated
to project source, build, documentation, and other resources required to construct the project (vs
information about its process / progress / state / etc.)

**These are directives, not suggestions. The user may edit, override, or remove any of them; absent
that, follow them as written.**

**Create each directory listed below when first writing into it; do not pre-create them.** A
directory that does not yet exist is not missing — it has no content yet.

## Playbook

Split directives and/or information in the playbook into multiple files as useful for organization.
These are the standard, recognized Kanibako playbook formats & paths:

| Path (From `~/playbook/`) | Description | Loaded at session start by default? |
|---------------------------|-------------|-------------------------------------|
| `<scope>/directives/BRIEF_<scope>.md` | Initial / startup briefing for the specified scope | Yes |
| `<scope>/directives/CONVENTIONS.md` | Technical expectations (coding, architecture, commands, etc) | If referenced |
| `<scope>/directives/` | Additional directive files, as appropriate | If referenced |
| `<scope>/scripts/` | Reusable helper scripts applying to the scope | No |

Keep script documentation in `--help` within scripts themselves, not in session files to avoid
duplication and reduce context consumption.

--

## Notebook & Workbook

The project/box-specific `~/notebook` and `~/workbook` also have standard conventions:

### Notebook

| Path (relative) | Description | Loaded at session start by default? |
|---------------------------|-------------|-------------------------------------|
| `archives/` | Completed plans & historical devnotes, documents, & information (i.e., "the archive" or "the archives"); the **authoritative historical record** | No |
| `directives/BRIEF_BOX.md` | Initial / startup briefing for this specific box (project) | Yes |
| `directives/CONVENTIONS.md` | Technical expectations (coding, architecture, commands, etc) | If referenced |
| `directives/` | Additional directive files, as appropriate | If referenced |
| `scripts/` | Reusable helper scripts applying to the scope | No |

### Workbook

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
