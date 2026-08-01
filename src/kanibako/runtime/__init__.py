"""kanibako runtime — the container runtime and the image/rig layer.

Everything that talks to podman/docker or to an image registry:

* ``container``       — ``ContainerRuntime``: detect, pull, build, run, stop, rm.
* ``registry``        — the OCI Distribution API client (stdlib only).
* ``freshness``       — non-blocking local-vs-remote digest comparison.
* ``containerfiles``  — bundled Containerfile resolution (package data).
* ``templates_image`` — user image-template management.
* ``image_sharing``   — nested image sharing via ``additionalImageStores``.
* ``rig_*``           — rig metadata, export bundles, the host-side rig
  registry, name resolution and source detection.
* ``baseline``        — the image-baseline manifest (the in-box tool contract).

⚑ The "registry" name collision this package dissolves: ``kanibako.runtime.registry``
is the OCI client and is now unmistakable beside ``kanibako.project.registry_store`` (the
global name registry), ``kanibako.runtime.rig_registry`` (host-side rigs) and
``kanibako.project.workset_registry`` (per-workset box membership).  Four different things
that were four bare ``registry`` spellings.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.runtime.container import
ContainerRuntime`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE.  Eager re-exports would load the whole package on
any submodule import, and ``registry`` is deliberately stdlib-only: a facade
would put the whole rig/image layer behind it.  The rule is uniform across every
package this pass creates; see ``plans/refactor-packageification-PLAN.md`` §4.3.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.runtime.registry import X``),
never relative — §4.4 of that plan.
"""

__all__ = [
    "baseline",
    "container",
    "containerfiles",
    "freshness",
    "image_sharing",
    "registry",
    "rig_bundle",
    "rig_meta",
    "rig_registry",
    "rig_resolve",
    "rig_source",
    "templates_image",
]
