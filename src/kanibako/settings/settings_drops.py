"""Which scope tables a settings file may carry — spec §0 directional enforcement, §2h.

⚑ WHY THIS IS ITS OWN MODULE. The containment rule has readers on opposite sides of import edges:
``settings_assemble`` (the cascade, which drops and WARNS), ``config_keys`` (the write-direction
guard) and, next, ``agent_file`` (the per-agent file's shape). The assembler imports the agent
file, so the rule cannot live in the assembler; here, below all of them, it has one derivation and
every reader imports it.
"""

from __future__ import annotations

from kanibako.settings.kb_store import BINDING_DERIVATIONS_NODE, SCOPE_CONTAINMENT
from kanibako.settings.settings_prefs import PREF_LEGAL_LEVELS, PREF_ROOT


def containing_scopes(file_scope: str) -> frozenset[str]:
  """The scope tokens that CONTAIN *file_scope*: the head of :data:`SCOPE_CONTAINMENT` before it."""
  idx = SCOPE_CONTAINMENT.index(file_scope)
  return frozenset(SCOPE_CONTAINMENT[:idx])


def contained_scopes(file_scope: str) -> tuple[str, ...]:
  """The scope tokens *file_scope* CONTAINS: the tail of :data:`SCOPE_CONTAINMENT` after it."""
  idx = SCOPE_CONTAINMENT.index(file_scope)
  return SCOPE_CONTAINMENT[idx + 1:]


def writable_scopes(level: str) -> frozenset[str]:
  """The scope tokens a *level* command may WRITE: its own and every scope it contains (spec §0)."""
  return frozenset({level, *contained_scopes(level)})


def upward_scope_drop_set(file_scope: str) -> frozenset[str]:
  """The top-level tokens directional enforcement removes from a *file_scope* file (spec §0).

  The containing scopes UNION the always-dropped tokens. ``base`` is not in SCOPE_CONTAINMENT, so
  it takes an empty containing set. ⚑ The RULE without the warning: the silent reader
  (``settings_assemble.cascade_view``) and the warning one (``_drop_upward_scopes``) share it.
  """
  containing = (
    containing_scopes(file_scope)
    if file_scope in SCOPE_CONTAINMENT
    else frozenset()
  )
  return containing | frozenset({"meta", BINDING_DERIVATIONS_NODE})


def cascade_drop_set(level: str) -> frozenset[str]:
  """Every top-level token the cascade drops from a *level* file before the merge.

  :func:`upward_scope_drop_set`, plus ``pref`` wherever §2h forbids writing a request
  (:data:`~kanibako.settings.settings_prefs.PREF_LEGAL_LEVELS`) — the two filters
  ``settings_assemble.assemble_levels`` applies, each with its warning.
  """
  drop_set = upward_scope_drop_set(level)
  if level not in PREF_LEGAL_LEVELS:
    drop_set = drop_set | frozenset({PREF_ROOT})
  return drop_set
