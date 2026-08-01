"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.agent_config`` moved to :mod:`kanibako.settings.agent_config` in the
package-ification pass (v1.8.0).  This shim exists ONLY for ALREADY-PUBLISHED
plugin wheels: ``kanibako-agent-goose`` 0.3.0 and ``kanibako-agent-codex``
0.3.0 are FINAL on PyPI, ``kanibako-agent-claude`` 1.8.0rc1 is published, and
all three declare ``dependencies = ["kanibako-cli"]`` with NO upper bound — so
a user who upgrades the base gets the new core beside an old plugin that still
imports this path.  Every IN-REPO caller uses the new path.

All three published plugins import :class:`AgentConfig` from here (in a
``TYPE_CHECKING`` block and again inside ``generate_agent_config``); the whole
public surface is re-exported so a third-party plugin keeps working too.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.settings.agent_config`.
Same gate class as the M-12 items; the full shim table + gate is
``MIGRATION.md`` §3.1, and ``tests/test_plugin_import_compat.py`` pins the
contract.
"""

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
    agent_file_route as agent_file_route,
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
