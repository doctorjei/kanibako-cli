"""Uniform per-box seed-once query/record API.

This is the single read/write surface the launch path (Step 3) will call to
ask "has this box already been seeded?" and to record "this box is now
seeded".  It hides where each box's seeded flag lives by dispatching on
``proj.mode`` (a :class:`kanibako.paths.BoxMode`) to whichever registry owns
the box:

* ``BoxMode.primary``    → the ``seeded`` section's ``projects`` domain in
  ``registry.yaml`` (keyed by ``proj.name``).
* ``BoxMode.standalone`` → the ``seeded`` section's ``standalone`` domain in
  ``registry.yaml`` (keyed by ``proj.name``).
* ``BoxMode.named``      → ``WorksetProject.seeded`` inside the owning
  workset's ``settings.yaml`` (located via ``proj.group.root``).

It is the explicit, registry-resident successor to the brittle ``.seeded``
sentinel file (now REMOVED): Step 3 wired launch-time detection over to here,
ORing this registry flag with an existing-inbox backstop for legacy boxes.
Layering: this module
depends on ``paths`` (types only), ``registry_store``, and ``workset``; none of
those import ``box_seed``, so there is no import cycle.
"""

from __future__ import annotations

from kanibako import registry_store
from kanibako.paths import BoxMode, ProjectPaths, StandardPaths


def box_is_seeded(proj: ProjectPaths, std: StandardPaths) -> bool:
    """Return whether *proj*'s box has completed its one-time seed.

    Dispatches on ``proj.mode`` to the owning registry.  Returns False for a
    box that has never been seeded (or, defensively, a ``named`` box whose
    group/workset-project cannot be resolved).
    """
    if proj.mode is BoxMode.primary:
        return registry_store.is_box_seeded(std.data_path, "projects", proj.name)
    if proj.mode is BoxMode.standalone:
        return registry_store.is_box_seeded(std.data_path, "standalone", proj.name)
    # BoxMode.named: the flag lives on the WorksetProject.
    if proj.group is None:
        return False
    from kanibako.errors import WorksetError
    from kanibako.workset import load_workset

    try:
        ws = load_workset(proj.group.root)
    except WorksetError:
        # Defensive: a named box whose workset cannot be loaded (e.g. its
        # settings.yaml is absent or not yet materialised) reads as unseeded.
        return False
    for p in ws.projects:
        if p.name == proj.name:
            return p.seeded
    return False


def mark_box_seeded(proj: ProjectPaths, std: StandardPaths) -> None:
    """Record that *proj*'s box has completed its one-time seed.

    Dispatches on ``proj.mode`` to the owning registry.  Idempotent; a no-op
    (defensive) for a ``named`` box whose group/workset-project cannot be
    resolved.
    """
    if proj.mode is BoxMode.primary:
        registry_store.mark_box_seeded_entry(std.data_path, "projects", proj.name)
        return
    if proj.mode is BoxMode.standalone:
        registry_store.mark_box_seeded_entry(std.data_path, "standalone", proj.name)
        return
    # BoxMode.named: persist on the WorksetProject via the workset's public API.
    if proj.group is None:
        return
    from kanibako.errors import WorksetError
    from kanibako.workset import load_workset, set_project_seeded

    try:
        ws = load_workset(proj.group.root)
        set_project_seeded(ws, proj.name)
    except WorksetError:
        # Defensive: a named box whose workset cannot be loaded/updated is a
        # no-op (mirrors the missing-group guard above).
        return
