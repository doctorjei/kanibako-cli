"""The agent-ref grammar's own refusal text, DERIVED rather than quoted.

Three suites assert that a blank ``--agent`` / a blank stored ``system.agent``
is refused *by the grammar owner* (``agent_ref.parse_agent_ref``) and not by a
second predicate spelled at the selection seam, the arbiter, or the launch.  The
point of the assertion is that all of them carry ONE message, so the expected
text is obtained by asking the owner — a literal copy in each file would go on
passing after the owner changed its wording, and would let the three drift apart
without a single test reddening.
"""

from __future__ import annotations


def blank_ref_refusal() -> str:
    """The message ``parse_agent_ref`` raises for a ref that is empty after stripping."""
    from kanibako.agent_ref import parse_agent_ref
    from kanibako.errors import ConfigError

    try:
        parse_agent_ref("")
    except ConfigError as exc:
        return str(exc)
    raise AssertionError(
        "parse_agent_ref no longer refuses a blank ref — the grammar, not this "
        "helper, is what every blank-ref pin is written against."
    )
