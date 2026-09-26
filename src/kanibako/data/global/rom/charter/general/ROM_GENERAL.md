<!--[STOCK]
> This file is the entrypoint for the "general" chapter of the "charter"; it is read directly from
> the package's "rom" index and cannot be edited. The core instructions are updated together with
> the package(s). This file describes the box environment & universal operating instructions for
> agents inside a *Kanibako* box, for all projects/harnesses (Claude, Codex, Goose, …). Your own
> system/configuration and/or project-/agent-specific instructions should go in the handbook
> and/or notebook chapters (not here).
-->

## The Canon
Canon _tome_ separation dedicates workspaces to project source, builds, documentation, & other
resources required to construct project _artifacts_ (vs info on process / progress / state / etc.)

- **Canon Law** (law): Binding, _COMPULSORY_ Canon text.
- **Canon Lore** (lore): Non-law Canon text; information & resources, plans, working files, etc.

**Upper Law** & **Upper Lore** come from **Upper Canon**; **Lower Law** & **Lower Lore** are from
**Lower Canon**. Upper Law & Lore always take precedence over Lower Law & Lore.
<!-- Maybe be unneeded: - **References** - Information, citations, and rulings of nuance (generally considered Lore) -->

_Law_ and _Lore_ can live in any tome, but each has a unique role. The Handbook may define other,
system-specific elements of Law and Lore.


## Session Handoff
If you see `[Agent handoff - Continue prior task(s)]`, this surface just received you. Continue any
in-progress task; otherwise, await instructions.


## Directives
**Directives**: are Law loaded every session.

__IMPORTSECTION__("directives/*")

## Procedures
**Procedures** are Law loaded on-demand to serve specific needs. If you are doing a task covered by a
  procedure, you **must** read the procedure first. If unsure, **read the document** to be safe.

__IMPORTSECTION__("procedures/*")
