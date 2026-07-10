"""Increment 2a — the PLUGIN-DECLARATION half of instruction delivery.

Each agent plugin ships a KICKOFF-LOADER (the flattener SEED): a tiny static file
whose whole content is a single ``@~/playbook/kanibako/directives/KANIBAKO.md``
import.  The plugin declares it as a best-effort descriptor ``managed_pointer``
binding delivered read-only to ``~/.config/kanibako/kickoff.md``, and names the
native instruction slot the box-start flattener will write the flattened per-agent
FINAL file to via the ``KANIBAKO_DIRECTIVE_FINAL`` container env var.  These tests
prove that declaration for all three first-party agents:

* the ``managed_pointer`` binding resolves its shipped kickoff-loader source and,
  driven through ``descriptor_mounts``, mounts RO at the kickoff slot;
* the kickoff-loader content is exactly the single directive import; and
* ``descriptor.container_env["KANIBAKO_DIRECTIVE_FINAL"]`` is the right native slot.

The former Route-A ``@system.instructions`` → native-slot category bind is RETIRED
(the guide now reaches the box via the RO ``~/playbook/kanibako/`` bundle + the
flattened FINAL file), so we also prove no plugin still emits it.  The start-time
flatten + hook is a LATER increment and is NOT exercised here.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from kanibako.settings_resolve import GUEST_HOME
from kanibako.targets import resolve_target
from kanibako.targets.assembly import descriptor_mounts
from kanibako.targets.base import (
    AgentInstall,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
)

_AGENTS = ["claude", "codex", "goose"]

# The single box-side kickoff slot the SEED is delivered to (uniform across agents).
_KICKOFF_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# The kickoff-loader's whole content: one @import of the RO directive bundle.
_DIRECTIVE_IMPORT = "@~/playbook/kanibako/directives/KANIBAKO.md"

# The native instruction slot the box-start flattener writes the FINAL file to.
_EXPECTED_FINAL = {
    "claude": f"{GUEST_HOME}/.claude/CLAUDE.md",
    "codex": f"{GUEST_HOME}/.codex/AGENTS.md",
    "goose": f"{GUEST_HOME}/.config/goose/.additionalContext.md",
}


def _kickoff_binding(agent: str):
    """The plugin descriptor's ``managed_pointer`` kickoff-loader binding."""
    desc = resolve_target(agent, None).descriptor
    assert desc is not None
    ptrs = [b for b in desc.bindings if b.key == "managed_pointer"]
    assert len(ptrs) == 1, f"{agent}: expected exactly one managed_pointer binding"
    return ptrs[0]


def _dummy_install(agent: str) -> AgentInstall:
    # The kickoff-loader binding is LITERAL-origin, so descriptor_mounts never
    # consults these install fields for it (they matter only for the
    # AGENT_CRITICAL binary/launcher/share binds we isolate away below).
    p = Path("/nonexistent")
    return AgentInstall(name=agent, binary=p, install_dir=p, launcher=p)


# --- the kickoff-loader binding (the flattener SEED) -------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_kickoff_binding_shape(agent: str):
    """Each plugin's ``managed_pointer`` is a best-effort RO literal at the kickoff slot."""
    b = _kickoff_binding(agent)
    assert b.origin is HostSrcOrigin.LITERAL
    assert b.scope is BindScope.AGENT  # best-effort, NOT agent_critical
    assert b.ro is True
    assert b.box_dest == _KICKOFF_DEST
    # The literal source is the plugin's shipped kickoff-loader file and resolves.
    assert b.literal_src is not None
    assert b.literal_src.is_file(), f"{agent}: kickoff-loader source missing"


@pytest.mark.parametrize("agent", _AGENTS)
def test_kickoff_content_is_single_directive_import(agent: str):
    """The shipped kickoff-loader content is exactly the one directive @import."""
    b = _kickoff_binding(agent)
    assert b.literal_src is not None
    text = b.literal_src.read_text()
    import_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--")
    ]
    assert import_lines == [_DIRECTIVE_IMPORT], (
        f"{agent}: kickoff-loader must be the single directive import, got {import_lines!r}"
    )


@pytest.mark.parametrize("agent", _AGENTS)
def test_kickoff_delivered_ro_at_kickoff_slot(agent: str):
    """Through the plugin's own delivery path the SEED mounts RO at the kickoff slot.

    Isolating the kickoff binding (the AGENT_CRITICAL binary binds would need a real
    install) and running it through ``descriptor_mounts`` proves the shipped source
    resolves and mounts read-only at the exact box-side kickoff slot, carrying the
    directive import.  A wrong dest, rw options, or an unresolvable source reddens.
    """
    b = _kickoff_binding(agent)
    d = PluginDescriptor(command=(agent,), bindings=(b,), mode={"start": ()})
    mounts = descriptor_mounts(d, _dummy_install(agent))
    assert len(mounts) == 1
    m = mounts[0]
    assert m.destination == _KICKOFF_DEST
    assert m.options == "ro"
    assert _DIRECTIVE_IMPORT in Path(m.source).read_text()


@pytest.mark.parametrize("agent", _AGENTS)
def test_kickoff_is_best_effort_missing_source_skipped(agent: str):
    """A missing kickoff source is SKIPPED (not raised) — a launch can't crash.

    Repointing the LITERAL source at a nonexistent path and running
    ``descriptor_mounts`` must yield NO mount and NO ``BindingSourceError`` — the
    AGENT (best-effort) scope contract.
    """
    b = _kickoff_binding(agent)
    broken = replace(b, literal_src=Path("/nonexistent/kanibako/kickoff-loader"))
    d = PluginDescriptor(command=(agent,), bindings=(broken,), mode={"start": ()})
    assert descriptor_mounts(d, _dummy_install(agent)) == []


# --- the FINAL-slot env var --------------------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_directive_final_env_names_native_slot(agent: str):
    """``KANIBAKO_DIRECTIVE_FINAL`` names the agent's native instruction slot."""
    desc = resolve_target(agent, None).descriptor
    assert desc is not None
    assert desc.container_env.get("KANIBAKO_DIRECTIVE_FINAL") == _EXPECTED_FINAL[agent]


def test_goose_context_file_names_lists_additional_context_md():
    """goose loads its FINAL file because CONTEXT_FILE_NAMES lists its name.

    The FINAL flattened guide lands at ~/.config/goose/.additionalContext.md (a
    deliberately conspicuous name — a stopgap breadcrumb until goose gains hook
    additionalContext injection).  goose only reads the filenames in
    CONTEXT_FILE_NAMES, so `.additionalContext.md` must be listed, and the retired
    KANIBAKO.md must be gone.  The existing keyring disable is untouched.
    """
    desc = resolve_target("goose", None).descriptor
    assert desc is not None
    val = desc.container_env.get("CONTEXT_FILE_NAMES")
    assert val is not None, "goose descriptor missing CONTEXT_FILE_NAMES"
    names = json.loads(val)
    assert ".additionalContext.md" in names, names
    assert "KANIBAKO.md" not in names, names
    assert desc.container_env["KANIBAKO_DIRECTIVE_FINAL"].endswith(
        "/.config/goose/.additionalContext.md"
    )
    assert desc.container_env.get("GOOSE_DISABLE_KEYRING") == "true"


# --- the retired Route-A category bind ---------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_route_a_instructions_bind_retired(agent: str):
    """No plugin emits the old ``@system.instructions`` → native-slot category bind."""
    binds = resolve_target(agent, None).default_category_binds()
    assert "agent.bindings.ro.instructions" not in binds
    # And nothing left points a category bind at @system.instructions.
    assert not any(
        isinstance(v, tuple) and v and v[0] == "@system.instructions"
        for v in binds.values()
    ), f"{agent}: a category bind still references @system.instructions: {binds!r}"
