"""kanibako net — stdlib-only outbound-HTTP primitives shared across packages.

A leaf: nothing here imports another kanibako module, so a consumer such as the
Anthropic proxy (``kanibako.proxy``) can use it without loading the settings or
target layers.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers import the
SUBMODULE — ``from kanibako.net.redirects import NoRedirects`` — never a name
re-exported here.

⚑ DELIBERATELY IMPORT-FREE, the uniform rule for every package in this tree.
"""

__all__ = [
  "redirects",
]
