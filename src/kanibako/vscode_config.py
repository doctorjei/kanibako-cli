"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.vscode_config`` moved to :mod:`kanibako.vscode.vscode_config` in the
package-ification pass (v1.8.0).  This shim exists ONLY for ALREADY-PUBLISHED
plugin wheels: ``kanibako-agent-goose`` 0.3.0 and ``kanibako-agent-codex`` 0.3.0
are FINAL on PyPI, ``kanibako-agent-claude`` 1.8.0rc1 is published, and all three
declare ``dependencies = ["kanibako-cli"]`` with NO upper bound — so a user who
upgrades the base gets the new core beside an old plugin that still imports this
path.  Every IN-REPO caller uses the new path.

The published plugins import seven names from here (``CodexModelProvider``,
``clear_claude_bypass_permissions``, ``seed_claude_bypass_permissions``,
``seed_session_start_hook``, ``seed_codex_approval``, ``seed_codex_config``,
``seed_goose_mode``); the whole public surface is re-exported so a third-party
plugin written against any of it keeps working too.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.vscode.vscode_config`.  Same
gate class as the M-12 items; the full shim table + gate is ``MIGRATION.md``
§3.1, and ``tests/test_plugin_import_compat.py`` pins the contract.
"""

from kanibako.vscode.vscode_config import (
    AGENT_MARKERS_DIR as AGENT_MARKERS_DIR,
)
from kanibako.vscode.vscode_config import (
    CodexModelProvider as CodexModelProvider,
)
from kanibako.vscode.vscode_config import (
    attached_container_config_path as attached_container_config_path,
)
from kanibako.vscode.vscode_config import (
    clear_bypass_permissions as clear_bypass_permissions,
)
from kanibako.vscode.vscode_config import (
    clear_claude_bypass_permissions as clear_claude_bypass_permissions,
)
from kanibako.vscode.vscode_config import (
    codex_trusted_hash as codex_trusted_hash,
)
from kanibako.vscode.vscode_config import (
    load_jsonc as load_jsonc,
)
from kanibako.vscode.vscode_config import (
    merge_attached_container_config as merge_attached_container_config,
)
from kanibako.vscode.vscode_config import (
    merge_bypass_permissions as merge_bypass_permissions,
)
from kanibako.vscode.vscode_config import (
    merge_codex_config as merge_codex_config,
)
from kanibako.vscode.vscode_config import (
    merge_codex_model_provider as merge_codex_model_provider,
)
from kanibako.vscode.vscode_config import (
    merge_marker_remove_hook as merge_marker_remove_hook,
)
from kanibako.vscode.vscode_config import (
    merge_marker_write_hook as merge_marker_write_hook,
)
from kanibako.vscode.vscode_config import (
    merge_session_start_hook as merge_session_start_hook,
)
from kanibako.vscode.vscode_config import (
    seed_attached_container_config as seed_attached_container_config,
)
from kanibako.vscode.vscode_config import (
    seed_claude_bypass_permissions as seed_claude_bypass_permissions,
)
from kanibako.vscode.vscode_config import (
    seed_codex_approval as seed_codex_approval,
)
from kanibako.vscode.vscode_config import (
    seed_codex_config as seed_codex_config,
)
from kanibako.vscode.vscode_config import (
    seed_goose_mode as seed_goose_mode,
)
from kanibako.vscode.vscode_config import (
    seed_session_start_hook as seed_session_start_hook,
)
