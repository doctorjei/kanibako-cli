"""LEGACY IMPORT PATH — re-export shim.  Do NOT add code here.

``kanibako.vscode_config`` moved to :mod:`kanibako.vscode.vscode_config` in the
package-ification pass (v1.8.0).  This shim exists ONLY for ALREADY-PUBLISHED
plugin wheels: ``kanibako-agent-goose`` 0.3.0 and ``kanibako-agent-codex`` 0.3.0
are FINAL on PyPI, ``kanibako-agent-claude`` 1.8.0rc1 is published, and all three
declare ``dependencies = ["kanibako-cli"]`` with NO upper bound — so a user who
upgrades the base gets the new core beside an old plugin that still imports this
path.  Every IN-REPO caller uses the new path.

The published plugins import seven names from here; five still exist and are
re-exported (``CodexModelProvider``, ``seed_session_start_hook``,
``seed_codex_approval``, ``seed_codex_config``, ``seed_goose_mode``), and the
whole public surface is re-exported so a third-party plugin written against any
of it keeps working too.

⚑ **R-41 (the ``access`` permission tiers) REMOVED two of the seven.**
``seed_claude_bypass_permissions`` / ``clear_claude_bypass_permissions`` were the
two POLARITIES of a boolean axis that no longer exists; the successor is the
single tier-valued ``seed_claude_permission_mode(path, access=…)``.  They are NOT
re-exported and NOT re-implemented here: a compatibility shim that re-invented a
boolean permission API would put two spellings of the permission axis in the tree
(Code Convention 0), and the boolean spelling is the one that cannot express
``editing``.

⚑ **What an old claude wheel beside this core ACTUALLY does — VERIFIED, and NOT
what an earlier draft of this note claimed.** It does not fail at import, and it
would not be visible if it did:

* the published wheel imports those two names FUNCTION-LOCALLY, inside
  ``deliver_panel_permissions`` (its only other reference is a ``TYPE_CHECKING``
  import of ``CodexModelProvider``), so its module imports FINE and the plugin
  LOADS;
* even a module-scope failure would be silent — ``targets/__init__.py``'s plugin
  scan catches a failed plugin import and logs it at ``logger.debug``.

The real behaviour is safer than the claim but arrives by a different route: the
old wheel also ships the old ``claude-defaults.yaml``, whose pre-R-41
``safe_bypass:`` block has no ``tiers:``, so its descriptor loads with ZERO
access rows and the launch's un-rendered-tier gate REFUSES before any delivery
(:func:`kanibako.targets.assembly.access_row`, which diagnoses a zero-row surface
as plugin version skew BY NAME).  The same release also changes
``Target.deliver_panel_permissions`` to take ``access=``, so such a wheel is
substantively incompatible either way; a GENERIC plugin-version-skew error is the
follow-up release's ruled mechanism, not this shim's.

⚑ REMOVAL GATE — delete this file once ``kanibako-agent-claude``,
``kanibako-agent-goose`` AND ``kanibako-agent-codex`` have all PUBLISHED (not
merely merged) releases importing :mod:`kanibako.vscode.vscode_config`.  Same
gate class as the M-12 items; the full shim table + gate is ``MIGRATION.md``
§3.1, and ``tests/test_plugin_import_compat.py`` pins the contract.
"""

import warnings

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
    clear_permission_mode as clear_permission_mode,
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
    merge_permission_mode as merge_permission_mode,
)
from kanibako.vscode.vscode_config import (
    seed_claude_permission_mode as seed_claude_permission_mode,
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

# ⚑ Stage 1 of the two-stage retirement (Jei, 2026-08-01): v1.8.0 KEEPS this
# alias but SAYS SO; the next release deletes it and plugin discovery refuses an
# old plugin by name with upgrade instructions.  FutureWarning deliberately —
# DeprecationWarning is hidden by default outside __main__, which would make this
# silent for exactly the audience it exists for.  Best-effort courtesy: it fires
# on import of the OLD path only (a consumer of the new module never executes
# this file), and a launch path that swallows stderr will swallow it — the next
# release's hard error is the backstop, not this.
warnings.warn(
    "kanibako.vscode_config moved to kanibako.vscode.vscode_config; this compatibility alias "
    "exists for plugins built against kanibako-cli < 1.8.0 and will be REMOVED in the next "
    "release — upgrade your kanibako-agent-* packages.",
    FutureWarning,
    stacklevel=2,
)
