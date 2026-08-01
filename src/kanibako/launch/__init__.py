"""kanibako launch — box identity, lifecycle bookkeeping, and start-time delivery.

The support layer the box lifecycle (create → start → run → stop → remove) runs
on:

* ``box_identity``  — standalone box identity generation.
* ``box_resolve``   — new-model box identity derivation from registry + layout.
* ``journal``       — the write-ahead log of in-flight lifecycle operations.
* ``creds_watcher`` — the HOST-side per-box credential-writeback watcher.
* ``hygiene``       — box shell-directory cleanup.
* ``shells``        — launch-shell resolution for no-agent boxes.
* ``templates``     — shell-template resolution and seed APPLICATION.

⚑ Resolve vs deliver.  ``kanibako.settings.settings_categories`` RESOLVES the
``seeded`` category; ``templates`` here performs the delivery.  Keeping that line
crisp is what keeps the single-route binding invariant legible.

⚑ NOT here: ``box_supervisor`` and ``box_lifecycle`` stay top-level.  They are
the box's in-box PID-1 chain, deliberately stdlib-only, executed in-box by
dotted path (``python3 -m kanibako.box_supervisor``) and guarded by an
import-chain allowlist that asserts on every ANCESTOR PACKAGE.  Ratified 2026-08-01;
the LaunchPlan phase is the scheduled carrier for that seam.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.launch.templates import
apply_shell_template`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE.  Eager re-exports would load the whole package on
any submodule import, which here would close a real loop: ``templates`` imports
``kanibako.settings.core_defaults`` at module scope, and ``core_defaults``
defers its import of ``templates`` precisely to break that cycle.  A facade
would make the deferral a no-op and disarm the ImportError that reports a
closed cycle.  The rule is uniform across every package this pass creates; see
``plans/refactor-packageification-PLAN.md`` §4.3.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.launch.journal import X``),
never relative — §4.4 of that plan.
"""

__all__ = [
    "box_identity",
    "box_resolve",
    "creds_watcher",
    "hygiene",
    "journal",
    "shells",
    "templates",
]
