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
from dataclasses import replace
from pathlib import Path

import pytest

from kanibako.settings_categories import reconcile_categories
from kanibako.settings_launch import build_launch_snapshot, snapshot_category_entries
from kanibako.settings_resolve import GUEST_HOME, ResolveCtx
from kanibako.targets import resolve_target
from kanibako.targets.assembly import descriptor_mounts
from kanibako.targets.base import (
    AgentInstall,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
)

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


# --- STEP 2b — the claude LOADER (~/.claude/CLAUDE.md) -----------------------
#
# 2a delivers the KANIBAKO.md CONTENT (bound RO to ~/.claude/KANIBAKO.md).  2b
# delivers the claude-only LOADER that makes claude READ it: a tiny shipped static
# file bound RO to ~/.claude/CLAUDE.md (claude's user-memory slot, loaded every
# session) whose whole content is TWO RELATIVE imports — `@KANIBAKO.md` +
# `@AGENTS.md` — resolved by claude next to the file in ~/.claude/.  RELATIVE (not
# `@$HOME/...`, not `@~/...`): verified to load both on real claude in both secure
# and skip-permissions modes (the earlier managed /etc/claude-code pointer was
# dropped — its @import only expanded under skip-permissions, not for ~).  It flows
# through the plugin's OWN delivery path (a descriptor `bindings:` entry →
# `descriptor_mounts`), NOT the @-ref category cascade, and is BEST-EFFORT (scope
# AGENT, never AGENT_CRITICAL) so a missing source is skipped, not crash-inducing.

_LOADER_DEST = f"{GUEST_HOME}/.claude/CLAUDE.md"
_LOADER_IMPORTS = ["@KANIBAKO.md", "@AGENTS.md"]


def _loader_binding():
    """The claude descriptor's ~/.claude/CLAUDE.md loader binding (claude-only)."""
    desc = resolve_target("claude", None).descriptor
    assert desc is not None
    ptrs = [b for b in desc.bindings if b.box_dest == _LOADER_DEST]
    assert len(ptrs) == 1, "claude descriptor missing the ~/.claude/CLAUDE.md loader bind"
    return ptrs[0]


def _dummy_install() -> AgentInstall:
    # The loader binding is LITERAL-origin, so descriptor_mounts never consults
    # these install fields for it (they matter only for the AGENT_CRITICAL binds).
    p = Path("/nonexistent")
    return AgentInstall(name="claude", binary=p, install_dir=p, launcher=p)


def test_loader_content_is_relative_imports():
    """The shipped loader's content is EXACTLY the two RELATIVE imports (not ~/$HOME)."""
    b = _loader_binding()
    assert b.literal_src is not None
    text = b.literal_src.read_text()
    lines = text.splitlines()
    # Both relative import lines MUST be present verbatim…
    for imp in _LOADER_IMPORTS:
        assert imp in lines, f"loader missing relative import {imp!r}"
    # …and NO absolute/home-anchored import form may ship (those would not resolve
    # relative to ~/.claude/ the way the verified design requires).
    assert "@$HOME/" not in text
    assert "@~/" not in text
    assert "@/home/" not in text


def test_loader_delivered_ro_at_user_memory_slot():
    """Through the plugin's own delivery path, the loader is a RO mount at ~/.claude/CLAUDE.md.

    Isolating the loader binding (the AGENT_CRITICAL delivery binds need a real
    install) and running it through ``descriptor_mounts`` proves the shipped source
    resolves and mounts read-only at the exact user-memory slot, carrying the two
    relative imports.  A wrong dest, rw options, or an absolute/`~` import reddens.
    """
    b = _loader_binding()
    d = PluginDescriptor(command=("claude",), bindings=(b,), mode={"start": ()})
    mounts = descriptor_mounts(d, _dummy_install())
    assert len(mounts) == 1
    m = mounts[0]
    assert m.destination == _LOADER_DEST
    assert m.options == "ro"
    delivered = Path(m.source).read_text().splitlines()
    for imp in _LOADER_IMPORTS:
        assert imp in delivered


def test_loader_is_best_effort_missing_source_skipped():
    """A missing loader source is SKIPPED (not raised) — a launch can't crash.

    Repointing the LITERAL source at a nonexistent path and running
    ``descriptor_mounts`` must yield NO mount and NO ``BindingSourceError`` — the
    AGENT (best-effort) scope contract, mirroring 2a's non-critical bind.
    """
    b = _loader_binding()
    assert b.scope is BindScope.AGENT  # NOT agent_critical
    assert b.origin is HostSrcOrigin.LITERAL
    broken = replace(b, literal_src=Path("/nonexistent/kanibako/claude-md-loader"))
    d = PluginDescriptor(command=("claude",), bindings=(broken,), mode={"start": ()})
    # No raise, empty mounts — the best-effort skip.
    assert descriptor_mounts(d, _dummy_install()) == []


@pytest.mark.parametrize("agent", ["goose", "codex"])
def test_only_claude_ships_the_loader(agent: str):
    """The ~/.claude/CLAUDE.md loader is claude-ONLY (goose/codex slots are natively read)."""
    desc = resolve_target(agent, None).descriptor
    assert desc is not None
    assert not any(b.box_dest == _LOADER_DEST for b in desc.bindings)
