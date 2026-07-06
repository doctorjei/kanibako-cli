"""STEP 2a — the PLUGIN-declared instructions bind (spec §2d L608).

Each agent plugin declares an AGENT-scope ``@``-ref-sourced category bind
``agent.<agent>.bindings.ro.instructions = (@system.instructions, <harness slot>)``
via its ``<agent>-defaults.yaml`` ``category_binds:`` section, read by
:func:`kanibako.agent_defaults.load_category_binds` and exposed as
``target.default_category_binds()``.  These tests prove:

* the loader emits the RAW ``@system.instructions`` ref (NOT a fixed path), so core
  stays agent-agnostic; and
* the launch category cascade (``build_launch_snapshot`` fold →
  ``snapshot_category_entries`` → ``reconcile_categories``) RESOLVES that ref to the
  concrete ``<data>/global/KANIBAKO.md`` path, mounted read-only at the correct
  per-harness slot.  A mount whose source is still the literal ``@system.instructions``
  string (unexpanded) or whose dest/options are wrong would redden these.
"""

from __future__ import annotations

import json

import pytest

from kanibako.settings_categories import reconcile_categories
from kanibako.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target

# Per-harness expected box-side slot (spec §2d L608 / default-plugin-config DESIGN).
# ``~`` is resolved box-side to the guest home by the adapter.
_EXPECTED_DEST = {
    "claude": f"{GUEST_HOME}/.claude/KANIBAKO.md",
    "goose": f"{GUEST_HOME}/.config/goose/KANIBAKO.md",
    "codex": f"{GUEST_HOME}/.codex/AGENTS.md",
}

# A concrete resolved value for system.instructions (what ``std.instructions`` yields
# at launch = ``@config.data/global/KANIBAKO.md`` resolved).  The @-ref must expand
# to EXACTLY this.
_RESOLVED_INSTRUCTIONS = "/data/global/KANIBAKO.md"


def _ctx(agent: str) -> ResolveCtx:
    return ResolveCtx(
        agent_name=agent,
        workset_name=None,
        host_home="/home/host",
        xdg={"XDG_DATA_HOME": "/data"},
    )


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_loader_emits_raw_ref_and_slot(agent: str):
    """``default_category_binds`` emits the RAW @-ref source + per-harness slot, ro."""
    target = resolve_target(agent, None)
    binds = target.default_category_binds()
    key = "agent.bindings.ro.instructions"
    assert key in binds, f"{agent}: missing instructions category bind"
    tup = binds[key]
    # Element 0 is the RAW @-ref STRING — core carries no per-harness path knowledge.
    assert tup[0] == "@system.instructions"
    assert tup[1] == _EXPECTED_DEST[agent].replace(GUEST_HOME, "~", 1)
    assert tup[2] == "ro"


@pytest.mark.parametrize("agent", ["claude", "goose", "codex"])
def test_instructions_ref_resolves_through_cascade(agent: str):
    """The @-ref RESOLVES to the concrete KANIBAKO.md path at the harness slot, ro.

    Mirrors ``start._build_launch_snapshot``: the plugin's category binds are unioned
    into ``default_categories`` alongside the resolved ``system.instructions`` floor
    entry, then adapted + reconciled.  The winning mount's source MUST be the resolved
    path (NOT the literal ``@system.instructions``) — the proof the ref expands.
    """
    target = resolve_target(agent, None)
    default_categories: dict[str, object] = dict(target.default_category_binds())
    # The resolved system.* floor entry the @-ref views (start.py folds resolved_sys).
    default_categories["system.instructions"] = _RESOLVED_INSTRUCTIONS

    snap = build_launch_snapshot(
        agent_name=agent,
        ctx=_ctx(agent),
        system_path=None,
        agent_path=None,
        workset_path=None,
        box_path=None,
        default_categories=default_categories,
    )
    entries = snapshot_category_entries(snap, active_agent=agent, box_ctx=_ctx(agent))
    rec = reconcile_categories(entries)

    by_name = {m.name: m for m in rec.mounts}
    assert "instructions" in by_name, f"{agent}: instructions bind not reconciled"
    m = by_name["instructions"]
    assert m.scope == "agent"
    assert m.category == "bindings.ro"
    # PROOF: the @-ref expanded to the resolved path (not the literal ref string).
    assert m.host_src == _RESOLVED_INSTRUCTIONS
    assert m.host_src != "@system.instructions"
    # Correct per-harness slot (box-side resolved) + read-only.
    assert m.box_dest == _EXPECTED_DEST[agent]
    assert m.options == "ro"


def test_goose_context_file_names_env():
    """goose ships CONTEXT_FILE_NAMES so it loads the bound KANIBAKO.md file.

    The value is a JSON array STRING (goose's expected format) that re-includes the
    default context filenames so a user's own files still load alongside ours.
    """
    desc = resolve_target("goose", None).descriptor
    assert desc is not None
    val = desc.container_env.get("CONTEXT_FILE_NAMES")
    assert val is not None, "goose descriptor missing CONTEXT_FILE_NAMES"
    # Valid JSON array with our file first + the re-included defaults.
    assert json.loads(val) == ["KANIBAKO.md", "AGENTS.md", ".goosehints"]
    # The existing keyring disable is untouched.
    assert desc.container_env.get("GOOSE_DISABLE_KEYRING") == "true"
