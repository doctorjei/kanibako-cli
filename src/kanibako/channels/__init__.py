"""kanibako channels — inter-instance communication.

The channel system boxes talk to each other through, and the helper subsystem
that rides it:

* ``channels``        — channel path resolution + per-instance partition
  addressing (pure helpers; the workset-name token, the box address set).
* ``helpers``         — helper spawning: B-ary tree numbering and spawn budgets.
* ``helper_listener`` — the HOST-side hub: a Unix socket server for spawn/stop
  and message routing, with JSONL logging.
* ``helper_client``   — the CONTAINER-side client for that socket.

PUBLIC SURFACE: the submodules named in ``__all__``.  Consumers outside this
package import the SUBMODULE — ``from kanibako.channels.channels import
box_channel_addresses`` — never a name re-exported here.

⚑ DELIBERATELY IMPORT-FREE.  Eager re-exports would load the whole package on
any submodule import.  That matters especially here: ``helper_client`` has ZERO
intra-kanibako imports and is the module a CONTAINER-side process reaches for,
while ``helper_listener`` pulls in the container runtime and the settings
foundation.  A facade would drag the host-side hub's dependencies into every
box-side client import.  The rule is uniform across every package this pass
creates; see ``plans/refactor-packageification-PLAN.md`` §4.3.

IN-PACKAGE IMPORTS ARE ABSOLUTE (``from kanibako.channels.helpers import X``),
never relative — §4.4 of that plan.
"""

__all__ = [
    "channels",
    "helper_client",
    "helper_listener",
    "helpers",
]
