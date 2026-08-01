"""kanibako VS Code integration — attached-container config + remote plumbing.

The host side of ``kanibako code``: generating the attached-container
configuration a VS Code window needs (``vscode_config``) and the ``code
--remote`` SSH/tunnel plumbing that points a local window at a box on another
host (``vscode_remote``).

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.vscode.vscode_config import
seed_session_start_hook`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE.  Eager re-exports would load the whole package on
any submodule import.  Across the tree that would render the deferred-import
cycle-breakers (``config`` → ``config_interface``, ``paths`` → ``workset``, …)
into no-ops and disarm the ImportError that today tells you a cycle was closed.
The rule is uniform across every package this pass creates; see
``plans/refactor-packageification-PLAN.md`` §4.3.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.vscode.vscode_config import
X``), never relative — §4.4 of that plan.
"""

__all__ = [
    "vscode_config",
    "vscode_remote",
]
