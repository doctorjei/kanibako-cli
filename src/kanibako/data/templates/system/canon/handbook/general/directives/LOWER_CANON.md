<!--[STOCK]
> Lower Canon (System Tome)
>
> This file holds the system-wide canon policy; it is read from the global canon/handbook path and
> can only be edited from the host system by the user (not from within the box). Agent-editable
> instructions and information should go in the box's notebook (not here).
>
> This file is seeded from the core package. Defaults contents are included here; they are meant
> as a starting point and can be extended or replaced.
-->
### Lower Canon

Lower Canon includes these books:

- Notebook (`~/canon/notebook`): Box tome; often directives, archives, & resources; read-write.
- Workbook (`~/canon/workbook`): Box working tome; specific process, progress, state, & other
  data (optional, but recommended)

Non-draft specification and design documents are also considered _Law_.

#### Notebook

The notebook is agent-editable and can include Law and Lore. Directories are used for primary
organization, with `directives` & `procedures` (and `specs` if applicable) holding Local Law
texts. These are the standard, recognized Kanibako canon paths:

| Path (`~/canon/notebook/`) | Description | Loaded at start by default? |
|------------------|---------------------------------------------------------------------|-----|
| `MY_CONTENTS.md` | Read into context at start; notebook entry point | Yes |
| `archives/` | Completed plans & historical devnotes, documents, & information (i.e., "the archive" or "archives"); **authoritative historical record** | No |
| `directives/CONVENTIONS.md` | Technical expectations (coding, architecture, commands, etc) | Typically (requires reference) |
| `directives/` | Local directive files, as needed | Typically (requires reference) |
| `procedures/` | Information about specific procedures / actions and how to do them | No |
| `references/` | Canon Lore; Reference material & information consulted to address ambiguity in Law — e.g. rulings history & evidence | No |
| `resources/` | Raw resource files (captures, fixtures, data) — not prose, which belongs in `references` | No |
| `scripts/` | Reusable helper scripts | No |

Keep script documentation in `--help` within scripts, **not session files**, to avoid duplication &
minimize context consumption.

#### Workbook

The project-specific `~/canon/workbook` uses these conventions:

| Path (relative) | Description | Loaded at start by default? |
|---------------------------|-------------|-------------------------------------|
| `designs/` | Project design(s) | No; _Authoritative_. |
| `plans/` | Active implementation plans | When executing a plan |
| `specs/` | Project specification(s) | No; _Authoritative_. |
| `state/` | All files regarding the project's current state | No; _Authoritative_. |
| `state/devnotes.md` | Detailed changelog; should reflect relevant actions/updates | No |
| `state/status.md` | Detailed current status; should reflect relevant actions/updates | No |
| `tasks/` | Task boards (kanban or simple list) | No; _Authoritative_. |
| `temp/` | Temporary storage (volatile between sessions) | No |
| `temp/scripts` | one-off scripts (used once or a few times at most) | No |
| `temp/testing` | Temporary testing data / logs | No |

**Archives:** To preserve context, DO NOT read unless needed (e.g., as reference for context about
legacy behavior / decisions). It is useful when necessary but rarely needed.

_All scripts, plans, documents, & other resources that have become historical_ (i.e., not related
to current or future design, planning, and/or development) should move to the archives.
