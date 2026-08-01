"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.settings_resolve`` moved to :mod:`kanibako.settings.settings_resolve`
in the package-ification pass (v1.8.0).  This shim exists ONLY for
ALREADY-PUBLISHED plugin wheels: ``kanibako-agent-goose`` 0.3.0 and
``kanibako-agent-codex`` 0.3.0 are FINAL on PyPI, ``kanibako-agent-claude``
1.8.0rc1 is published, and all three declare ``dependencies = ["kanibako-cli"]``
with NO upper bound — so a user who upgrades the base gets the new core beside
an old plugin that still imports this path.  Every IN-REPO caller uses the new
path.

The published plugins import :data:`GUEST_HOME` from here (claude and codex);
the whole public surface is re-exported so a third-party plugin written against
any of it keeps working too.

⚑ ``GUEST_HOME`` is deliberately RE-EXPORTED, never re-assigned: the golden
fixture (``tests/test_defaults_golden.py``) asserts the constant is ASSIGNED in
exactly one file.  A ``GUEST_HOME = "..."`` line here would be a second
definition and would turn that guard red — correctly.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.settings.settings_resolve`.
Same gate class as the M-12 items; the full shim table + gate is
``MIGRATION.md`` §3.1, and ``tests/test_plugin_import_compat.py`` pins the
contract.
"""

import warnings

# Re-imported by settings_resolve from kanibako.agent_ref, so it was reachable
# at this path before the move.  Carried explicitly: cheap, and a published
# wheel reaching for it keeps working.
from kanibako.settings.settings_resolve import (
    CANONICAL_SEP as CANONICAL_SEP,
)
from kanibako.settings.settings_resolve import (
    GUEST_GID as GUEST_GID,
)
from kanibako.settings.settings_resolve import (
    GUEST_HOME as GUEST_HOME,
)
from kanibako.settings.settings_resolve import (
    GUEST_UID as GUEST_UID,
)
from kanibako.settings.settings_resolve import (
    MAX_REF_DEPTH as MAX_REF_DEPTH,
)
from kanibako.settings.settings_resolve import (
    UNSET as UNSET,
)
from kanibako.settings.settings_resolve import (
    LevelView as LevelView,
)
from kanibako.settings.settings_resolve import (
    ResolveCtx as ResolveCtx,
)
from kanibako.settings.settings_resolve import (
    ResolvedValue as ResolvedValue,
)
from kanibako.settings.settings_resolve import (
    SettingsError as SettingsError,
)
from kanibako.settings.settings_resolve import (
    expand_expr as expand_expr,
)
from kanibako.settings.settings_resolve import (
    match_ref as match_ref,
)
from kanibako.settings.settings_resolve import (
    match_var as match_var,
)
from kanibako.settings.settings_resolve import (
    resolve_value as resolve_value,
)
from kanibako.settings.settings_resolve import (
    split_bind as split_bind,
)
from kanibako.settings.settings_resolve import (
    unpack_bind as unpack_bind,
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
    "kanibako.settings_resolve moved to kanibako.settings.settings_resolve; this "
    "compatibility alias exists for plugins built against kanibako-cli < 1.8.0 and will be "
    "REMOVED in the next release — upgrade your kanibako-agent-* packages.",
    FutureWarning,
    stacklevel=2,
)
