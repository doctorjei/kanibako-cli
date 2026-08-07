"""Instruction delivery — the KICKOFF LOADER, from BOTH sides of the P-5 move.

⚑ TWO DELIVERY ROUTES COEXIST IN THIS RELEASE, deliberately, and this module covers
each with its own class:

* ``box.bindings.ro.kickoff`` — CORE's bind (spec §2c, P-5 / C-CANON R2).  The
  kickoff CONTENT now ships in the base (``data/global/KICKOFF.md``) and is emitted
  by ``core_defaults.kickoff_default_categories``.  See ``TestCoreKickoffBind``.
* ``managed_pointer`` — each PLUGIN's descriptor binding, still shipped (the
  deletion is DEFERRED one release; see ``TestCoreKickoffBind`` for why and for the
  transition gate that keeps the two from colliding).

Increment 2a — the PLUGIN-DECLARATION half of instruction delivery.

Each agent plugin ships a KICKOFF-LOADER (the flattener SEED): a tiny static file
whose whole content is the canon entry import ``@~/canon/COLLECTION.md`` plus, for
ONE transition release, the pre-canon ``@~/playbook/kanibako/directives/KANIBAKO.md``
import.  The plugin declares it as a best-effort descriptor ``managed_pointer``
binding delivered read-only to ``~/.config/kanibako/kickoff.md``, and names the
native instruction slot the box-start flattener will write the flattened per-agent
FINAL file to via the ``KANIBAKO_DIRECTIVE_FINAL`` container env var.  These tests
prove that declaration for all three first-party agents:

* the ``managed_pointer`` binding resolves its shipped kickoff-loader source and,
  driven through ``descriptor_mounts``, mounts RO at the kickoff slot;
* the kickoff-loader content is exactly the declared directive imports; and
* ``descriptor.container_env["KANIBAKO_DIRECTIVE_FINAL"]`` is the right native slot.

The former Route-A ``@system.instructions`` → native-slot category bind is RETIRED
(the guide now reaches the box inside the RO ``~/canon/bible`` bind + the flattened
FINAL file), so we also prove no plugin still emits it.  The start-time flatten +
hook is a LATER increment and is NOT exercised here.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.settings import core_defaults
from kanibako.settings.paths import resolve_project
from kanibako.settings.settings_resolve import GUEST_HOME
from kanibako.targets import resolve_target
from kanibako.targets.assembly import descriptor_mounts
from kanibako.targets.base import (
    AgentInstall,
    BindKind,
    Binding,
    BindScope,
    HostSrcOrigin,
    PluginDescriptor,
)
from kanibako.targets.no_agent import NoAgentTarget

_AGENTS = ["claude", "codex", "goose"]

# The single box-side kickoff slot the SEED is delivered to (uniform across agents).
_KICKOFF_DEST = f"{GUEST_HOME}/.config/kanibako/kickoff.md"

# The kickoff-loader's whole content: the canon ENTRY POINT (spec §2c / C-CANON
# P-4) plus, for ONE transition release, the pre-canon import.
_DIRECTIVE_IMPORT = "@~/canon/COLLECTION.md"

# ⚑ TRANSITION (one release only — brief §3.4 mitigation 1). The KICKOFF ships in
# three INDEPENDENTLY PUBLISHED plugin packages while the canon paths live in the
# base, so either publish order alone would silently break EVERY box: the flattener
# degrades a missing import target to an inert backticked mention, and the launch
# shim's ``|| true`` hides it. Carrying BOTH imports makes the plugin work against
# either base. Pinned here so REMOVING it (migration M-12) is deliberate.
_LEGACY_DIRECTIVE_IMPORT = "@~/playbook/kanibako/directives/KANIBAKO.md"

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
def test_kickoff_content_is_the_declared_directive_imports(agent: str):
    """The shipped kickoff-loader content is exactly the declared @imports — the
    canon entry FIRST, then the one transition import, and nothing else."""
    b = _kickoff_binding(agent)
    assert b.literal_src is not None
    text = b.literal_src.read_text()
    import_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith("<!--")
    ]
    assert import_lines == [_DIRECTIVE_IMPORT, _LEGACY_DIRECTIVE_IMPORT], (
        f"{agent}: kickoff-loader must carry exactly the canon entry plus the "
        f"transition import, got {import_lines!r}"
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
    assert _LEGACY_DIRECTIVE_IMPORT in Path(m.source).read_text()


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
    # And nothing left points a category bind at @system.instructions.
    assert not any(
        isinstance(v, tuple) and v and v[0] == "@system.instructions"
        for v in binds.values()
    ), f"{agent}: a category bind still references @system.instructions: {binds!r}"


# ===========================================================================
# P-5 — the CORE-owned kickoff bind (``box.bindings.ro.kickoff``, spec §2c).
# ===========================================================================


class _KickoffTarget(NoAgentTarget):
    """A REAL ``Target`` (the built-in fallback) carrying a supplied descriptor.

    Subclassing the ABC's own fallback rather than duck-typing means every other
    hook the launch seam reads comes from the real class, so a wiring test cannot
    pass by accident when that seam grows a new call.
    """

    def __init__(self, descriptor: PluginDescriptor | None = None) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> PluginDescriptor | None:
        return self._descriptor


def _kickoff_descriptor() -> PluginDescriptor:
    """A minimal descriptor that delivers SOMETHING to the kickoff slot."""
    return PluginDescriptor(
        command=("fake",),
        bindings=(
            Binding(
                key="managed_pointer",
                origin=HostSrcOrigin.LITERAL,
                box_dest=_KICKOFF_DEST,
                kind=BindKind.FILE,
                scope=BindScope.AGENT,
                ro=True,
                literal_src=Path("/nonexistent/kickoff.md"),
            ),
        ),
        mode={"start": ()},
    )


class TestCoreKickoffBind:
    """The kickoff CONTENT is core's as of P-5 — and core YIELDS while plugins ship it.

    ⚑ WHY THE PLUGIN FILES ARE STILL HERE (C-CANON R2 decision, recorded in migration
    M-12).  Deleting ``data/KICKOFF.md`` + ``managed_pointer`` in the same release
    that adds this bind leaves NO safe publish order: the plugins pin no base version
    and publish independently, so "plugins first" yields a new plugin (no kickoff)
    against an old base (no core bind) = a box with NO directive chain at all, and
    "base first" yields a new base against an OLD published plugin whose kickoff
    carries only the legacy pre-canon import = the same silent loss.  Shipping core's
    bind now and deleting the plugin copies in a FOLLOW-UP release keeps every
    combination working.  These tests pin the gate that makes the overlap safe.
    """

    def test_packaged_source_and_declared_slot(self):
        """The emitter delivers the packaged loader RO at the declared slot.

        ⚑ Re-derived for dest-keying (R-3/R-5/R-11, P6): the emitter returns the
        ONE terminal ``box.bindings.ro`` arm, and the slot — which used to be the
        tuple's 2nd element — is the arm's single map KEY, R-11-absolutized from
        the declarative file's ``~`` spelling. The declaration is still checked
        against ``kickoff_box_dest()``, which keeps the ``~`` form because it is
        also what the ``KANIBAKO_DIRECTIVE_SEED`` env var carries into the box.
        """
        cats = core_defaults.kickoff_default_categories(None)
        assert set(cats) == {"box.bindings.ro"}
        (dest, (src, opts)), = cats["box.bindings.ro"].items()
        assert Path(src).is_file(), "the packaged kickoff loader must ship"
        assert Path(src).name == "KICKOFF.md"
        assert dest == core_defaults.kickoff_guest_dest() == _KICKOFF_DEST
        assert core_defaults.kickoff_box_dest() == "~/.config/kanibako/kickoff.md"
        assert opts == "ro"

    def test_the_slot_has_exactly_one_spelling(self):
        """The bind dest, the ``KANIBAKO_DIRECTIVE_SEED`` env var and the gate all
        read the SAME declaration — two spellings of this path is how the env var
        and the bind end up naming different files."""
        assert core_defaults.kickoff_guest_dest() == _KICKOFF_DEST

    def test_core_content_is_the_canon_entry_and_nothing_else(self):
        """⚑ ONE import, deliberately NOT the plugins' two.

        The legacy ``@~/playbook/...`` line exists so a PLUGIN release also works
        against an older base.  A base-shipped kickoff ships in the same wheel as the
        canon binds it imports, so that line could never resolve — it would buy a
        permanent per-launch ``unresolved import`` warning and nothing else.
        """
        src = Path(core_defaults.kickoff_default_categories(None)[
            "box.bindings.ro"
        ][_KICKOFF_DEST][0])
        import_lines = [
            ln.strip()
            for ln in src.read_text().splitlines()
            if ln.strip() and not ln.strip().startswith("<!--")
        ]
        assert import_lines == [_DIRECTIVE_IMPORT]
        assert _LEGACY_DIRECTIVE_IMPORT not in src.read_text()

    def test_gate_yields_to_a_descriptor_that_delivers_the_slot(self):
        """GATE, positive arm: a plugin-supplied kickoff means core emits NOTHING."""
        assert core_defaults.kickoff_default_categories(_kickoff_descriptor()) == {}

    @pytest.mark.parametrize("agent", _AGENTS)
    def test_gate_yields_for_every_first_party_plugin_today(self, agent: str):
        """All three still ship ``managed_pointer``, so core yields on every real
        agent box this release.  ⚑ When the follow-up deletes those bindings this
        test flips to asserting the OPPOSITE — that is the signal the gate itself can
        be deleted (see ``kickoff_default_categories``' removal condition)."""
        desc = resolve_target(agent, None).descriptor
        assert desc is not None
        assert core_defaults.kickoff_default_categories(desc) == {}

    def test_gate_keys_on_the_DEST_not_the_key_name(self):
        """A third-party plugin that names its kickoff binding something else still
        collides at the DEST, so the gate must key on the dest.  (Same descriptor as
        the positive arm, one field renamed.)"""
        desc = _kickoff_descriptor()
        renamed = replace(desc, bindings=(
            replace(desc.bindings[0], key="my_own_loader"),
        ))
        assert core_defaults.kickoff_default_categories(renamed) == {}

    def test_a_descriptor_that_delivers_elsewhere_does_not_gate(self):
        """GATE, negative arm: an unrelated binding must not suppress the kickoff."""
        desc = _kickoff_descriptor()
        elsewhere = replace(desc, bindings=(
            replace(desc.bindings[0], box_dest=f"{GUEST_HOME}/.config/kanibako/other.md"),
        ))
        # ⚑ Re-derived: "the kickoff is still emitted" is now "the arm still holds
        # the kickoff's DEST" — asserting only the arm key would pass on an EMPTY
        # arm, which is exactly the suppression this negative arm must rule out.
        assert set(core_defaults.kickoff_default_categories(elsewhere)) == {
            "box.bindings.ro"
        }
        assert set(
            core_defaults.kickoff_default_categories(elsewhere)["box.bindings.ro"]
        ) == {_KICKOFF_DEST}

    def test_missing_packaged_loader_raises_rather_than_launching_empty(self, tmp_path):
        """FAIL-CLOSED: a box with no kickoff has no directive chain AT ALL (the
        flattener finds no source and the launch shim's ``|| true`` hides it), so a
        packaging defect must RAISE, not emit a bind whose source gets dropped with a
        one-line warning."""
        real = core_defaults.packaged_data_dir

        def _fake(*parts: str):
            if tuple(parts) == core_defaults.KICKOFF_PACKAGED_PARTS:
                return tmp_path / "gone" / "KICKOFF.md"
            return real(*parts)

        with patch.object(core_defaults, "packaged_data_dir", _fake):
            with pytest.raises(RuntimeError, match="packaged kickoff loader is missing"):
                core_defaults.kickoff_default_categories(None)


class TestKickoffLaunchWiring:
    """⚑ THE CALL SITE ITSELF — the emitter is actually CALLED by the real launch
    path, and the gate is applied there with the descriptor the launch really has.

    Everything above enters at the emitter, which proves it CORRECT but not that
    anything invokes it: deleting the ``default_categories.update(...)`` line from
    ``start._resolve_launch_snapshot`` would leave those tests green.
    """

    def _launch_mounts(self, std, proj, *, target, desc=None, install=None) -> dict:
        """Every Mount a launch would pass to podman, by destination.

        ⚑ BOTH emitters, exactly as ``_run_container`` calls them: box-scope winners
        through ``_emit_category_mounts`` and the agent-scope delivery binds through
        ``agent_delivery_mounts`` (they are split because the AGENT_CRITICAL
        must-exist safe-fail differs from L7's drop-with-warning). Emitting only one
        of the two would make "no kickoff mount" indistinguishable from "the OTHER
        route emitted it" — which is precisely the question these tests ask.
        """
        from kanibako.commands.start import (
            _emit_category_mounts,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.settings_launch import agent_delivery_mounts
        from kanibako.targets.base import BindScope as _BindScope

        _snapshot, reconciled = _resolve_launch_snapshot(
            std=std,
            proj=proj,
            agent_name="claude",
            system_settings_path=None,
            agent_cfg_path=None,
            desc=desc,
            install=install,
            target=target,
            agent_cfg=None,
            deliver_creds=True,
        )
        mounts = list(_emit_category_mounts(reconciled, label="kickoff-wiring"))
        if target is not None and install is not None and desc is not None:
            mounts += agent_delivery_mounts(
                reconciled.mounts,
                critical_keys=frozenset(
                    b.key for b in desc.bindings
                    if b.scope is _BindScope.AGENT_CRITICAL
                ),
            )
        return {m.destination: m for m in mounts}

    def test_core_kickoff_reaches_a_real_launch_when_no_plugin_supplies_one(
        self, std, config, project_dir,
    ):
        """A no-agent box has no descriptor at all — so core's kickoff is delivered,
        and a plain-shell box gets the canon entry point for the first time."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(std, proj, target=_KickoffTarget())

        assert _KICKOFF_DEST in by_dest, sorted(by_dest)
        m = by_dest[_KICKOFF_DEST]
        assert m.options == "ro"
        assert Path(m.source).name == "KICKOFF.md"
        assert _DIRECTIVE_IMPORT in Path(m.source).read_text()

    def test_gate_applies_at_the_launch_seam_through_the_targets_descriptor(
        self, std, config, project_dir,
    ):
        """⚑ THE ``desc=None`` + real-``target`` PATH: ``box config show --effective``
        resolves the launch that way, so the gate must fall back to the target's own
        descriptor — otherwise the display advertises a bind the launch never emits."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(
            std, proj, target=_KickoffTarget(_kickoff_descriptor()),
        )
        assert _KICKOFF_DEST not in by_dest, "core must yield to the plugin's kickoff"

    def test_a_real_claude_launch_delivers_exactly_one_kickoff_from_the_plugin(
        self, std, config, project_dir, tmp_path,
    ):
        """⚑ THE COMBINATION THE GATE EXISTS FOR, driven through the real seam with
        the real claude descriptor: ONE mount at the slot, sourced from the PLUGIN,
        and no collision error.  Without the gate this raises ``CategoryCollisionError``
        (proved by ``test_without_the_gate_the_two_binds_are_a_hard_error`` below)."""
        target = resolve_target("claude", None)
        desc = target.descriptor
        assert desc is not None
        # The AGENT_CRITICAL share/launcher binds must-exist (their whole point), so
        # this install has to point at real paths for the delivery emit to run.
        share = tmp_path / "claude-install"
        share.mkdir()
        launcher = tmp_path / "claude"
        launcher.write_text("#!/bin/sh\n")
        install = AgentInstall(
            name="claude", binary=launcher, install_dir=share, launcher=launcher,
        )

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        by_dest = self._launch_mounts(
            std, proj, target=target, desc=desc, install=install,
        )

        assert _KICKOFF_DEST in by_dest
        source = Path(by_dest[_KICKOFF_DEST].source)
        assert source.is_file()
        assert "plugins/claude" in source.as_posix(), (
            f"expected the PLUGIN's kickoff during the transition, got {source}"
        )

    def test_without_the_gate_the_two_binds_are_a_hard_error(self):
        """GUARD THE GUARD: prove the collision the gate prevents is REAL.

        Core's box-scope bind and the plugin's agent-scope binding are both CONCRETE
        declarations at one box_dest — spec §0's row-1 collision, which RAISES.  If
        this ever stops raising, the gate is no longer load-bearing and the whole
        transition dance can go; until then, it is the difference between a working
        upgrade and a box that refuses to launch.
        """
        from kanibako.settings.agent_representation import agent_default_partial
        from kanibako.errors import CategoryCollisionError
        from kanibako.settings.settings_launch import (
            build_launch_snapshot,
            snapshot_category_entries,
        )
        from kanibako.settings.settings_categories import reconcile_categories
        from kanibako.settings.settings_resolve import ResolveCtx

        desc = resolve_target("claude", None).descriptor
        assert desc is not None
        p = Path("/nonexistent")
        install = AgentInstall(name="claude", binary=p, install_dir=p, launcher=p)
        ctx = ResolveCtx(
            agent_name="claude", workset_name=None, host_home="/home/host",
            xdg={"XDG_DATA_HOME": "/data"},
        )

        snap = build_launch_snapshot(
            agent_name="claude",
            ctx=ctx,
            system_path=None,
            agent_path=None,
            workset_path=None,
            box_path=None,
            # UNGATED core kickoff (what the launch would inject without the gate)…
            default_categories=dict(core_defaults.kickoff_default_categories(None)),
            # …beside the plugin's own descriptor delivery binds.
            agent_partial=agent_default_partial(desc, install, node_name="claude"),
        )
        entries = snapshot_category_entries(snap, active_agent="claude", box_ctx=ctx)
        with pytest.raises(CategoryCollisionError):
            reconcile_categories(entries)
