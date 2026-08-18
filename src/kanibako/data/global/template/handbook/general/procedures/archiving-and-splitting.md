# Procedure — Archiving & Splitting Handbook Documents

> **PROCEDURE, not auto-loaded.** How to retire historical material & when to split a growing file.
> **Read this BEFORE archiving anything, truncating `devnotes.md`/`tasks.md`, or splitting a
> document** — the archives are the AUTHORITATIVE historical record, & old entries must move
> VERBATIM. Getting this wrong destroys history rather than filing it.
> Trigger conditions live in `~/canon/handbook/general/directives/rules/CANON.md` (auto-loaded).
> Moved out of `directives/` 2026-08-04 (context cost).


**Splitting:**
- If architecture in `CONVENTIONS.md` exceeds ~100 lines, separate it into `ARCHITECTURE.md`.
- If task tracking outgrows `tasks.md`, archive completed items before adopting a dedicated tool; a
  dedicated tool is rarely warranted.

**Archives:** don't read the archives unless needed — reference them only to debug, or to
contextualize legacy behavior & past decisions. They preserve history without consuming context.
_All scripts, plans, documents & other resources that have become historical_ (i.e. not related to
current or future design, planning &/or development) should move to the archives, completed plans
included. Current / active documents & logs (e.g. `devnotes.md`, `tasks.md`) should truncate old
entries, moving them _verbatim_ into the archives with an appropriate prefix/suffix.
