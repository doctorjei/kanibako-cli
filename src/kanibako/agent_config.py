"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.agent_config`` moved to :mod:`kanibako.settings.agent_config` in the
package-ification pass (v1.8.0).  This shim exists ONLY for ALREADY-PUBLISHED
plugin wheels: ``kanibako-agent-goose`` 0.3.0 and ``kanibako-agent-codex``
0.3.0 are FINAL on PyPI, ``kanibako-agent-claude`` 1.8.0rc1 is published, and
all three declare ``dependencies = ["kanibako-cli"]`` with NO upper bound — so
a user who upgrades the base gets the new core beside an old plugin that still
imports this path.  Every IN-REPO caller uses the new path.

All three published plugins import :class:`AgentConfig` from here (in a
``TYPE_CHECKING`` block and again inside ``generate_agent_config``); the rest of
the module's public surface is re-exported so a third-party plugin keeps working
too.

⚑ ``agent_file_route`` IS NO LONGER RE-EXPORTED (S1): it moved to
:mod:`kanibako.settings.agent_file` as ``_address``.  No published plugin
imports it (measured against all three wheels); the third-party risk is
theoretical and accepted, and v1.8.0 is a deliberate clean break.
``load_agent_config`` / ``write_agent_config`` moved too (to ``agent_file.load``
/ ``agent_file.save``) but their names SURVIVE in the new module as the S1b
bridge, so this shim keeps re-exporting them — the contract here is "the WHOLE
public surface of the new module", pinned by
``test_plugin_import_compat.test_shim_covers_the_whole_public_surface``.  They
leave this file when S1b deletes the bridge.  ``AgentConfig`` — the one name the
plugins DO import — stays either way.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.settings.agent_config`.
Same gate class as the M-12 items; the full shim table + gate is
``MIGRATION.md`` §3.1, and ``tests/test_plugin_import_compat.py`` pins the
contract.
"""

import warnings

from kanibako.settings.agent_config import (
    AGENT_CATEGORY_DIRNAME as AGENT_CATEGORY_DIRNAME,
)
from kanibako.settings.agent_config import (
    IDENTITY_KEYS as IDENTITY_KEYS,
)
from kanibako.settings.agent_config import (
    AgentConfig as AgentConfig,
)
from kanibako.settings.agent_config import (
    agent_category_dirname as agent_category_dirname,
)
from kanibako.settings.agent_config import (
    agent_category_root as agent_category_root,
)
from kanibako.settings.agent_config import (
    agent_category_root_ref as agent_category_root_ref,
)
from kanibako.settings.agent_config import (
    agent_config_path as agent_config_path,
)
from kanibako.settings.agent_config import (
    agent_settings_path as agent_settings_path,
)
from kanibako.settings.agent_config import (
    agents_dir as agents_dir,
)
from kanibako.settings.agent_config import (
    is_self_resolving as is_self_resolving,
)
from kanibako.settings.agent_config import (
    load_agent_config as load_agent_config,
)
from kanibako.settings.agent_config import (
    root_relative_source as root_relative_source,
)
from kanibako.settings.agent_config import (
    write_agent_config as write_agent_config,
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
    "kanibako.agent_config moved to kanibako.settings.agent_config; this compatibility alias "
    "exists for plugins built against kanibako-cli < 1.8.0 and will be REMOVED in the next "
    "release — upgrade your kanibako-agent-* packages.",
    FutureWarning,
    stacklevel=2,
)
