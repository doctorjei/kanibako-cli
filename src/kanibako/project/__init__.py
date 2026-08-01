"""kanibako project — worksets, box membership, and the name registries.

Who exists and what they are called: the authoritative stores of workset and box
identity, and the drop-in import that reconciles them against on-disk truth.

* ``workset``          — the ``Workset``/``WorksetProject`` model and its
  persistence (``<root>/settings.yaml``), plus the workset CRUD API.
* ``workset_registry`` — the per-workset ``registry.yaml`` ``boxes:`` membership,
  the SOLE authoritative store of box names (including PRIMARY).
* ``registry_store``   — the global ``registry.yaml`` (``worksets``/``standalone``
  /``rigs``/``image_shells``).
* ``names``            — global name resolution over those registries.
* ``import_reconcile`` — drop-in import-on-discovery: reconcile the registry from
  what is actually on disk.

⚑ This package is where **H5** lands (the playbook's ``BoxRegistry``/``Entry``
consolidation: 15 free functions and untyped dicts spread across ~10 modules).
The boundary is drawn here so that work reshapes modules INSIDE it without
re-litigating where they live.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.project.workset import
load_workset`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE.  Eager re-exports would load the whole package on
any submodule import, and that would break this package specifically:
``kanibako.settings.paths`` imports ``names`` at MODULE scope while deferring
``workset``, ``workset_registry``, ``registry_store`` and ``import_reconcile``
into function bodies — those deferrals are the cycle-breakers for
paths ↔ project, and a facade here would load all five from the module-scope
``names`` import and close the loop.  The rule is uniform across every package
this pass creates; see ``plans/refactor-packageification-PLAN.md`` §4.3.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.project.registry_store import
X``), never relative — §4.4 of that plan.
"""

__all__ = [
    "import_reconcile",
    "names",
    "registry_store",
    "workset",
    "workset_registry",
]
