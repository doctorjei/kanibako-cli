"""Synthetic legacy selections without plugin discovery or store reads."""

from kanibako.agent_ref import (
  ADDRESSABLE_PSEUDO_AGENTS,
  AgentIdentity,
  harness_of,
  persona_of,
)
from kanibako.settings.agent_select import AgentSelection


def agent_selection(*, node: str, source: str) -> AgentSelection:
  """Build a legacy fixture value, including the retired empty-node test case."""
  pseudo = not node or node in ADDRESSABLE_PSEUDO_AGENTS
  harness = harness_of(node)
  return AgentSelection(
    identity=AgentIdentity(
      node=node,
      registered_name=None,
      persona=None if pseudo else persona_of(node),
      harness=None if pseudo else harness,
      target_name=node if pseudo else harness,
      entry_path=None,
    ),
    source=source,
  )
