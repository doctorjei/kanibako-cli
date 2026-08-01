"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.agent_defaults`` moved to :mod:`kanibako.settings.agent_defaults` in
the package-ification pass (v1.8.0).  This shim exists ONLY for
ALREADY-PUBLISHED plugin wheels: ``kanibako-agent-goose`` 0.3.0 and
``kanibako-agent-codex`` 0.3.0 are FINAL on PyPI, ``kanibako-agent-claude``
1.8.0rc1 is published, and all three declare ``dependencies = ["kanibako-cli"]``
with NO upper bound — so a user who upgrades the base gets the new core beside
an old plugin that still imports this path.  Every IN-REPO caller uses the new
path.

⚑ This is the shim that matters most: all THREE published plugins import it at
MODULE SCOPE (claude/target.py:13, codex:51, goose:9), so without it every
agent plugin fails to import and no agent is detected at all.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.settings.agent_defaults`.
Same gate class as the M-12 items; the full shim table + gate is
``MIGRATION.md`` §3.1, and ``tests/test_plugin_import_compat.py`` pins the
contract.
"""

import warnings

from kanibako.settings.agent_defaults import (
    load_category_binds as load_category_binds,
)
from kanibako.settings.agent_defaults import (
    load_common as load_common,
)
from kanibako.settings.agent_defaults import (
    load_descriptor as load_descriptor,
)

# ⚑ Stage 1 of the two-stage retirement (Jei, 2026-08-01): v1.8.0 KEEPS this
# alias but SAYS SO; the next release deletes it and plugin discovery refuses an
# old plugin by name with upgrade instructions.  FutureWarning deliberately —
# DeprecationWarning is hidden by default outside __main__, which would make this
# silent for exactly the audience it exists for.  Best-effort courtesy: it fires
# on import of the OLD path only (a consumer of the new module never executes
# this file), and a launch path that swallows stderr will swallow it — the next
# release's hard error is the backstop, not this.
warnings.warn(
    "kanibako.agent_defaults moved to kanibako.settings.agent_defaults; this compatibility "
    "alias exists for plugins built against kanibako-cli < 1.8.0 and will be REMOVED in the "
    "next release — upgrade your kanibako-agent-* packages.",
    FutureWarning,
    stacklevel=2,
)
