"""The collapse WIRING — ``meta.assembly.*`` is produced, and since 2a-2 it BINDS.

🛑 **CUTOVER STEP 2a-2 MOVED THE LINE THIS FILE USED TO DRAW.** The collapse was
information-only through step 6b; the MAIN launch path now emits its category mounts
from ``meta.assembly.bindings``. What survives unchanged is the RECONCILED route
itself — it still runs, still computes its whole answer, and still feeds the two
narrow resolves, the mask arm and the agent arm. So the oracle here is now THREE-sided:

* the collapse runs on the REAL launch seam and its output lands at the declared keys
  (this side is easy to fake green — assert MEANING, not shape);
* the RECONCILED route is byte-identical to a run with the wiring removed, INCLUDING
  the case where the collapse REFUSES the configuration (that is what keeps a refusal
  from failing a launch until step 2c);
* the emitter consumes the SHAPE, so the collapsed map and the same map translated
  from reconciled winners go through ONE function — and where the two DISAGREE, the
  disagreement is pinned, not smoothed.

The refusal case is real, not hypothetical: the collapse forbids a bind above a bind,
while ``reconcile_categories`` permits nested binds and errors only on two concrete
declarations at ONE identical dest. Prose: ``llm-docs/kanibako/commands/start.py.md``.
"""

import logging
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from kanibako.commands.start import (
    _agent_delivered_dests,
    _bind_map_from_mounts,
    _bind_map_masks,
    _emit_category_mounts,
    _launch_bind_map,
    _resolve_launch_snapshot,
    _snapshot_assembly_bindings,
    _split_home_bind,
)
from kanibako.settings.paths import resolve_project
from kanibako.settings.store_collapse import HOME_DEST
from kanibako.targets.assembly import BindingSourceError
from kanibako.targets.no_agent import NoAgentTarget

#: A bind at ``/home`` — legal on the live route (it collides with no dest and just
#: depth-sorts under the home mount), refused by the collapse's rule 1, because home
#: is pid 0 and is already collapsed beneath it before any scope folds.
_SUBSUMING = {"box.bindings.rw": {"/home": ("/tmp",)}}


class _WiringTarget(NoAgentTarget):
    """A REAL target for the live seam, so a new hook cannot make this pass by accident."""

    def rom_root(self) -> Path | None:
        return None


def _resolve(std, proj, **kw):
    """Drive the REAL launch seam with the base families on."""
    return _resolve_launch_snapshot(
        std=std,
        proj=proj,
        agent_name="claude",
        system_settings_path=None,
        agent_cfg_path=None,
        desc=None,
        install=None,
        target=_WiringTarget(),
        agent_cfg=None,
        deliver_creds=True,
        **kw,
    )


def _assembly(snapshot):
    """The ``meta.assembly`` subtree of *snapshot*, or ``{}`` when it was not written."""
    meta = dict.get(snapshot, "meta") or {}
    return dict.get(meta, "assembly") or {}


def _delivered(reconciled):
    """Everything the RECONCILED route computes, as comparable plain data.

    ⚑ Not "what the box receives" since 2a-2 — the main path's mounts come from the
    collapse now. This is the arm that must stay untouched by the wiring.
    """
    mounts = _emit_category_mounts(
        _bind_map_from_mounts(reconciled.mounts), label="assembly-wiring",
        skip_if_absent=_agent_delivered_dests(reconciled.mounts),
    )
    return (
        [(m.destination, str(m.source), m.options) for m in mounts],
        [(c.box_dest, c.host_src, c.options, c.category) for c in reconciled.copies],
        [(e.box_dest, e.options) for e in reconciled.envs],
        [w.message() for w in reconciled.warnings],
    )


class TestTheCollapseIsProduced:
    """``meta.assembly.{bindings,seeded,synced}`` reach the snapshot off a real resolve."""

    def test_all_three_declared_leaves_are_written(self, std, config, project_dir):
        """RED if the wiring is deleted: no leaf exists at all."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(std, proj)

        assert sorted(_assembly(snapshot)) == ["bindings", "seeded", "synced"]

    def test_home_is_pid_zero_and_folded_exactly_once(
        self, std, config, project_dir,
    ):
        """Home is lifted OUT of the shapes, so it seeds the fold instead of colliding."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(std, proj)

        bindings = _assembly(snapshot)["bindings"]
        assert HOME_DEST in bindings
        # The box home SOURCE, and its options carried whole (``Z,U`` — home does
        # not pass through the arm fold, so no ``rw`` is appended to it).
        assert bindings[HOME_DEST].src.endswith("/home")
        assert bindings[HOME_DEST].opts == "Z,U"

    def test_the_fold_sees_the_same_declarations_the_live_route_does(
        self, std, config, project_dir,
    ):
        """Every reconciled MOUNT dest is a collapsed bind dest, and vice versa."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj)

        # ⚑ Holds because this configuration triggers no subsumption; the collapse
        # REMOVES what it subsumes, so this is a pin on THIS fixture, not a law.
        assert set(_assembly(snapshot)["bindings"]) == {
            m.box_dest for m in reconciled.mounts
        }

    def test_a_narrow_resolve_writes_nothing(self, std, config, project_dir):
        """No home in the entry list ⇒ no box to assemble ⇒ every leaf stays absent."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )

        assert _assembly(snapshot) == {}


class TestTheLivePathIsUnchanged:
    """⚑⚑ THE SAFETY CLAIM that OUTLIVED 2a-2: the reconciled route is byte-identical.

    Producing the collapse must not perturb the route that still feeds the narrow
    resolves, the mask arm and the agent arm — most of all when the collapse REFUSES,
    because that refusal must reach nobody until step 2c takes the swallow out.
    """

    def _both_ways(self, monkeypatch, std, proj, **kw):
        """Resolve twice — once with the wiring, once with it patched to a no-op."""
        with_wiring = _delivered(_resolve(std, proj, **kw)[1])
        monkeypatch.setattr(
            "kanibako.commands.start._install_assembly_collapse",
            lambda *_a, **_kw: None,
        )
        return with_wiring, _delivered(_resolve(std, proj, **kw)[1])

    def test_delivery_is_identical_with_and_without_the_wiring(
        self, monkeypatch, std, config, project_dir,
    ):
        """Mounts, copies, envs and warnings all match a run that never collapses."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        wired, bare = self._both_ways(monkeypatch, std, proj)

        assert wired == bare
        assert wired[0], "the fixture must actually deliver mounts"

    def test_a_refused_configuration_still_launches_and_delivers_identically(
        self, monkeypatch, std, config, project_dir,
    ):
        """⚑ THE SHARPEST CASE: the collapse RAISES, the launch does not notice."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        wired, bare = self._both_ways(
            monkeypatch, std, proj, extra_default_categories=_SUBSUMING,
        )

        assert wired == bare
        assert ("/home", "/tmp", "Z,U") in wired[0]

    def test_a_refused_configuration_leaves_every_leaf_absent(
        self, std, config, project_dir,
    ):
        """A partial write would describe a box nothing could assemble — so write none."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(
            std, proj, extra_default_categories=_SUBSUMING,
        )

        assert _assembly(snapshot) == {}

    def test_the_refusal_is_reported_not_swallowed(
        self, caplog, std, config, project_dir,
    ):
        """The cause is logged at DEBUG — visible to the cutover, silent to the user."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        with caplog.at_level(logging.DEBUG, logger="kanibako.kanibako.commands.start"):
            _resolve(std, proj, extra_default_categories=_SUBSUMING)

        assert any(
            "meta.assembly.* not folded" in r.message for r in caplog.records
        ), [r.message for r in caplog.records]


class TestHomeIsLiftedOut:
    """``_split_home_bind`` — the one seam that keeps pid 0 out of every scope's shape."""

    def test_no_home_entry_yields_no_bind_and_the_list_untouched(self):
        """Zero candidates ⇒ nothing to build on; the caller must not fold at all."""
        assert _split_home_bind([]) == (None, [])

    def test_several_home_entries_refuse_to_name_pid_zero(self, std, config, project_dir):
        """Two mounts at ``~`` cannot name ONE foundation, so the fold is skipped."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        _snapshot, reconciled = _resolve(std, proj)
        home = next(m for m in reconciled.mounts if m.box_dest == HOME_DEST)

        assert _split_home_bind([home, home]) == (None, [home, home])

    def test_the_home_entry_is_removed_from_what_the_shapes_fold(
        self, std, config, project_dir,
    ):
        """The lifted entry is gone from the remainder — else it collides with the seed."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        _snapshot, reconciled = _resolve(std, proj)
        entries = list(reconciled.mounts)
        home_bind, folded = _split_home_bind(entries)

        assert home_bind is not None
        assert len(folded) == len(entries) - 1
        assert all(e.box_dest != HOME_DEST for e in folded)


class TestTheEmitterConsumesTheShape:
    """Cutover 2a-2: one emitter, one dest-keyed ``(src, opts)`` shape, two sources."""

    def _both_shapes(self, std, proj):
        """The collapsed map off the snapshot, and the same shape from reconciled rows."""
        snapshot, reconciled = _resolve(std, proj)
        collapsed = _snapshot_assembly_bindings(snapshot)
        assert collapsed is not None, "the fixture must actually collapse"
        return collapsed, _bind_map_from_mounts(reconciled.mounts), reconciled

    def test_the_snapshot_reader_returns_a_copy_not_the_live_node(
        self, std, config, project_dir,
    ):
        """P8 — a caller mutating what it read must not rewrite the snapshot."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(std, proj)
        first = _snapshot_assembly_bindings(snapshot)
        first.clear()

        assert _snapshot_assembly_bindings(snapshot), "the node was emptied through the read"

    def test_absent_reads_as_None_so_the_caller_can_tell_empty_from_missing(
        self, std, config, project_dir,
    ):
        """A refusal leaves the leaf ABSENT; ``None`` is what routes the fallback."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(std, proj, extra_default_categories=_SUBSUMING)

        assert _snapshot_assembly_bindings(snapshot) is None

    def test_the_main_path_takes_the_COLLAPSED_map_when_there_is_one(
        self, std, config, project_dir,
    ):
        """The switch itself, over a REAL resolve — and the two maps are not equal."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj)

        chosen = _launch_bind_map(snapshot, reconciled)
        assert chosen == _snapshot_assembly_bindings(snapshot)
        assert chosen != _bind_map_from_mounts(reconciled.mounts)

    def test_a_refused_collapse_falls_back_to_the_RECONCILED_rows(
        self, std, config, project_dir,
    ):
        """🛑 The safety arm: a refusal must lose the box NOTHING before step 2c.

        RED if the fallback is dropped or emptied — the launch would hand podman an
        empty category mount set on a configuration that works today.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(
            std, proj, extra_default_categories=_SUBSUMING,
        )
        assert _snapshot_assembly_bindings(snapshot) is None, "the collapse must refuse"

        chosen = _launch_bind_map(snapshot, reconciled)
        assert chosen == _bind_map_from_mounts(reconciled.mounts)
        assert len(chosen) > 1, chosen

    def test_both_shapes_emit_the_same_destinations(self, std, config, project_dir):
        """⚑ THE DESTS AGREE on the shipped fixture — so a difference below is REAL."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        collapsed, from_rows, reconciled = self._both_shapes(std, proj)
        agent = _agent_delivered_dests(reconciled.mounts)

        def dests(binds):
            return {
                m.destination for m in _emit_category_mounts(
                    binds, label="shape", skip_if_absent=agent,
                )
            }

        assert dests(collapsed) == dests(from_rows)

    def test_the_collapse_folds_THE_MODE_INTO_THE_OPTIONS(
        self, std, config, project_dir,
    ):
        """🛑 THE MEASURED DIVERGENCE, PINNED — not smoothed over.

        The five-arm shape carries ro/rw as the ARM, so the collapse folds the mode
        back into the option string (``store_collapse.fold_opt``): a rw bind the
        reconciled route emits as ``Z,U`` arrives as ``Z,U,rw``. Podman's default IS
        rw, so nothing about the box changes — but the option string podman receives
        does, and that is exactly the kind of difference a suite must state out loud
        rather than let a later reader discover in an argv.

        ⚑ HOME is the exception BY CONSTRUCTION: it is pid 0, lifted out before any
        scope folds, so no arm ever appends to its options.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        collapsed, from_rows, reconciled = self._both_shapes(std, proj)
        agent = _agent_delivered_dests(reconciled.mounts)

        def opts(binds):
            return {
                m.destination: m.options for m in _emit_category_mounts(
                    binds, label="shape", skip_if_absent=agent,
                )
            }

        collapsed_opts, row_opts = opts(collapsed), opts(from_rows)
        assert collapsed_opts != row_opts, "the two routes cannot be indistinguishable"
        assert collapsed_opts[HOME_DEST] == row_opts[HOME_DEST] == "Z,U"
        rw = [d for d, o in row_opts.items() if o == "Z,U" and d != HOME_DEST]
        assert rw, "the fixture must carry a rw bind that is not home"
        assert all(collapsed_opts[d] == "Z,U,rw" for d in rw), collapsed_opts
        ro = [d for d, o in row_opts.items() if o == "ro"]
        assert ro, "the fixture must carry a ro bind"
        assert all(collapsed_opts[d] == "ro" for d in ro), collapsed_opts

    def test_A_MASK_NOW_HIDES_THE_BIND_NESTED_UNDER_IT(
        self, std, config, project_dir,
    ):
        """⚑⚑ THE USER-VISIBLE DIVERGENCE OF 2a-2 — the one that is not cosmetic.

        The live route emits a mask and a bind INSIDE it as two mounts, and podman's
        depth-sort then lands the bind on top: the mask hides only what nothing else
        claimed. The collapse SWEEPS everything at or inside a mask, which is what a
        mask means. It does NOT refuse this configuration, so the difference reaches
        the box — hence CHANGELOG + MIGRATION §2.27 in this same commit.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {"~/masked/inside": ("/tmp",)},
            "box.masks": ["~/masked"],
        })
        chosen = _launch_bind_map(snapshot, reconciled)

        assert chosen is not None
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        assert "/home/agent/masked/inside" not in chosen, sorted(chosen)
        # …and the reconciled route — which still drives the mask arm — still has it.
        assert "/home/agent/masked/inside" in _bind_map_from_mounts(reconciled.mounts)

    def test_a_mask_in_the_map_emits_no_mount(self):
        """A MASK has no host source; the tmpfs arm takes it (:class:`TestTheMaskArm`)."""
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        emitted = _emit_category_mounts(
            {"/home/agent/w": MASK, "/tmp": CollapsedBind("/tmp", "rw")},
            label="shape",
        )

        assert [m.destination for m in emitted] == ["/tmp"]

    def test_the_emitter_depth_sorts_the_map_it_is_given(self, tmp_path):
        """⚑ A dest-keyed map carries NO order, so EMISSION owns the depth-sort.

        Podman resolves nested dests by last-``-v``-wins/depth-sort, so the deepest
        mount must be emitted LAST. The reconcile used to hand the emitter a sorted
        list; a map cannot, and taking insertion order would ship the fold's scope
        order to podman.
        """
        from kanibako.settings.store_collapse import CollapsedBind

        src = str(tmp_path)
        deep, mid, shallow = "/home/agent/w/v/u", "/home/agent/w", "/home"
        emitted = _emit_category_mounts(
            {d: CollapsedBind(src, "ro") for d in (deep, mid, shallow)},
            label="shape",
        )

        assert [m.destination for m in emitted] == [shallow, mid, deep]


class TestTheMaskArm:
    """Cutover 2a-4: the tmpfs masks and the bind mounts are halves of ONE map.

    The mask arm used to be built from the RECONCILED rows while the mounts beside it
    came from the collapse, so the two could — and at one dest did — disagree about
    what the box gets. Every test here asks the question the divergence asked: does
    the mask arm say the same thing the mount arm says, off the same value?
    """

    @staticmethod
    def _mounted(bindings):
        """The dests the emitter would mount from *bindings* (sources that exist)."""
        return [m.destination for m in _emit_category_mounts(bindings, label="mask-arm")]

    def test_a_declared_mask_reaches_the_tmpfs_arm_THROUGH_THE_COLLAPSE(
        self, std, config, project_dir,
    ):
        """The live seam: a ``<scope>.masks`` entry arrives as a mask IN THE MAP.

        RED if the arm goes back to the reconciled rows only by accident — the
        assertion is that the map it is taken from IS the collapsed one.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(
            std, proj, extra_default_categories={"box.masks": ["~/private"]},
        )
        collapsed = _snapshot_assembly_bindings(snapshot)
        assert collapsed is not None, "the fixture must actually collapse"

        assert _bind_map_masks(_launch_bind_map(snapshot, reconciled)) == [
            "/home/agent/private",
        ]
        # …and it is the COLLAPSE's own answer, not the fallback's.
        assert _bind_map_masks(collapsed) == ["/home/agent/private"]

    def test_the_mask_is_emitted_and_the_bind_UNDER_it_is_not(
        self, std, config, project_dir,
    ):
        """⚑⚑ BOTH ARMS, ONE CONFIGURATION — the divergence this step closes.

        A bind inside a mask is swept by the fold (MIGRATION §2.27, shipped at 2a-2
        for the mount arm). What 2a-4 adds is that the SAME map answers the mask arm,
        so the tmpfs the box receives is the one the sweep was performed against.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {"~/private/notes": ("/tmp",)},
            "box.masks": ["~/private"],
        })
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        chosen = _launch_bind_map(snapshot, reconciled)

        assert _bind_map_masks(chosen) == ["/home/agent/private"]
        assert "/home/agent/private/notes" not in self._mounted(chosen)

    def test_a_bind_AT_a_mask_dest_takes_the_point_instead_of_emitting_BOTH(
        self, std, config, project_dir,
    ):
        """🛑 THE MEASURED DIVERGENCE OF 2a-4, and the reason it is not cosmetic.

        A mask may be TAKEN by a bind at its own destination in a later scope (the
        fold sweeps the mask and the bind lands). The reconcile resolves that same
        collision the other way — §0 row 2, a mask OVERRIDES a binding at its dest —
        so with the arms on two sources the launch emitted a ``-v`` bind AND a
        ``--mount type=tmpfs`` at ONE destination. Off one map it emits the bind.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            "agent.claude.masks": ["~/contested"],
            "box.bindings.ro": {"~/contested": ("/tmp",)},
        })
        assert _snapshot_assembly_bindings(snapshot) is not None, "must NOT refuse"
        chosen = _launch_bind_map(snapshot, reconciled)

        assert _bind_map_masks(chosen) == []
        assert "/home/agent/contested" in self._mounted(chosen)
        # The retired arm's answer, still computed, and still the OPPOSITE one.
        assert "/home/agent/contested" in [
            e.box_dest for e in reconciled.mounts if e.category == "masks"
        ]

    def test_a_mask_ABOVE_a_LATER_scopes_bind_refuses_so_BOTH_arms_fall_back(
        self, std, config, project_dir,
    ):
        """⚑ MEASURED, and the limit of what MIGRATION §2.27 can claim today.

        Whether a mask sweeps a bind nested under it depends on the SCOPE DIRECTION:
        the sweep only happens when the mask folds LAST. A mask whose scope strictly
        PRECEDES the bind's is a bind arriving INSIDE an existing mask, which the
        collapse REFUSES — so the leaf stays absent, both arms fall back, and the box
        gets the pre-collapse answer (mask AND bind). That is what keeps a refusal
        costing nothing until step 2c, and it is why §2.27 is written about a mask and
        a bind declared at the same scope.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            "agent.claude.masks": ["~/private"],
            "box.bindings.ro": {"~/private/notes": ("/tmp",)},
        })
        assert _snapshot_assembly_bindings(snapshot) is None, "the collapse must refuse"
        chosen = _launch_bind_map(snapshot, reconciled)

        assert _bind_map_masks(chosen) == ["/home/agent/private"]
        assert "/home/agent/private/notes" in self._mounted(chosen)

    def test_a_REFUSED_collapse_takes_BOTH_arms_from_the_reconciled_rows(
        self, std, config, project_dir,
    ):
        """The safety arm, on the mask side: a refusal must lose the box no mask.

        RED if the arm is read from ``_snapshot_assembly_bindings`` directly instead
        of from ``_launch_bind_map`` — the mask would vanish on a configuration the
        live route still delivers.
        """
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            **_SUBSUMING, "box.masks": ["~/private"],
        })
        assert _snapshot_assembly_bindings(snapshot) is None, "the collapse must refuse"

        assert _bind_map_masks(_launch_bind_map(snapshot, reconciled)) == [
            "/home/agent/private",
        ]

    def test_the_two_arms_PARTITION_one_map(self, tmp_path):
        """⚑ ONE VALUE, BOTH ARMS: mutate the map and BOTH answers move with it.

        The arms are complementary halves of the same keys — nothing is delivered
        twice and nothing is dropped. Flipping one entry moves it from one arm to the
        other, which is the property two independent sources could never have.
        """
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        src = str(tmp_path)
        bindings = {
            "/home/agent/bound": CollapsedBind(src, "ro"),
            "/home/agent/hidden": MASK,
        }
        assert self._mounted(bindings) == ["/home/agent/bound"]
        assert _bind_map_masks(bindings) == ["/home/agent/hidden"]

        bindings["/home/agent/bound"] = MASK

        assert self._mounted(bindings) == []
        assert _bind_map_masks(bindings) == ["/home/agent/bound", "/home/agent/hidden"]

    def test_the_LAUNCH_reads_the_map_ONCE_and_serves_both_arms_from_it(
        self, start_mocks, tmp_path,
    ):
        """⚑⚑ THE PRODUCTION SEAM, not the functions: `_run_container` → `runtime.run`.

        Patching ``_launch_bind_map`` makes the map the ONLY place either arm could
        have come from — the harness's reconciled rows carry no mask at all, so the
        retired spelling would hand podman an empty mask list while mounting the bind.
        The call COUNT is asserted too: two reads is not one value, and while the
        fallback lives nothing says two reads answer from the same arm.
        """
        from kanibako.commands.start import _run_container
        from kanibako.settings.store_collapse import MASK, CollapsedBind

        bindings = {
            "/home/agent/hidden": MASK,
            "/home/agent/bound": CollapsedBind(str(tmp_path), "ro"),
        }
        with start_mocks() as m, patch(
            "kanibako.commands.start._launch_bind_map", return_value=bindings,
        ) as m_map:
            _run_container(
                project_dir=None, entrypoint=None, image_override=None,
                new_session=False, safe_mode=False, resume_mode=False,
                extra_args=[],
            )
            kwargs = m.runtime.run.call_args.kwargs

        assert m_map.call_count == 1, "the map is read ONCE for both arms"
        assert kwargs["tmpfs_masks"] == ["/home/agent/hidden"]
        assert "/home/agent/bound" in [
            str(mount.destination) for mount in kwargs["extra_mounts"]
        ]

    def test_the_mask_arm_depth_sorts_on_the_key_the_mounts_use(self):
        """A dest-keyed map carries no order, so the tmpfs arm sorts too — same key.

        Podman is handed the masks and the binds as one argv; sorting them on two
        different keys would order a mask against the mount it sits inside by luck.

        ⚑ ``/home/agent/w/a`` before ``/home/agent/x`` is where the two orders PART:
        a plain lexicographic sort puts the deeper one first, and every shallower-is-
        alphabetically-earlier fixture passes under both.
        """
        from kanibako.settings.store_collapse import MASK

        deep, mid, shallow = "/home/agent/w/a", "/home/agent/x", "/home"

        assert _bind_map_masks(dict.fromkeys((deep, mid, shallow), MASK)) == [
            shallow, mid, deep,
        ]


class TestTheThreeMissingSourcePolicies:
    """One emitter, three per-dest answers to "the host source is not there" (2a-3).

    ⚑ The set membership tests are spelled against a dest that does NOT exist on
    disk, because every one of these policies is reachable only through the missing
    branch — a test whose source exists passes under all three and pins none.
    """

    _CRITICAL = "/home/agent/.local/bin/claude"

    def _emit(self, dest, src, **policy):
        from kanibako.settings.store_collapse import CollapsedBind

        return _emit_category_mounts(
            {dest: CollapsedBind(src, "ro")}, label="shape", **policy,
        )

    def test_a_missing_critical_source_RAISES_instead_of_dropping(self):
        """MUST-EXIST: the AGENT_CRITICAL safe-fail, now the emitter's own branch.

        RED if ``must_exist`` stops reaching the emitter — a missing agent binary
        would be dropped with a warning and reach podman as a crun crash instead of
        the clean exit-1.
        """
        with pytest.raises(BindingSourceError, match=re.escape(self._CRITICAL)):
            self._emit(
                self._CRITICAL, "/nonexistent/claude",
                must_exist=frozenset({self._CRITICAL}),
            )

    def test_a_must_exist_set_spelled_as_KEYS_matches_nothing_and_is_caught(self):
        """⚑⚑ THE HISTORICAL BUG, in its third home.

        ``critical_keys`` once held descriptor KEY NAMES while the arm was keyed by
        DESTINATION, so every critical bind silently degraded to best-effort. The
        collapsed map is dest-keyed too, so a key-spelled ``must_exist`` matches
        NOTHING — and the failure is SILENT (a warn-and-drop that looks ordinary).
        This test is the noise that makes it loud.
        """
        emitted = self._emit(
            self._CRITICAL, "/nonexistent/claude",
            must_exist=frozenset({"launcher", f"agent.claude.bindings.ro.{self._CRITICAL}"}),
        )
        assert emitted == [], "a key-spelled must_exist must not match a dest"

    def test_must_exist_beats_skip_if_absent_at_a_shared_dest(self):
        """A dest in BOTH sets must raise: must-exist wins its own dests outright.

        The live call site subtracts the critical dests from the skip set, so this
        can only fire on a caller that does not — and "which test ran first" is not
        an answer a safe-fail may depend on.
        """
        with pytest.raises(BindingSourceError):
            self._emit(
                self._CRITICAL, "/nonexistent/claude",
                must_exist=frozenset({self._CRITICAL}),
                skip_if_absent=frozenset({self._CRITICAL}),
            )

    def test_a_missing_critical_source_RAISES_before_any_mkdir(self, tmp_path):
        """⚑⚑ THE ORDERING, PINNED: the policy is read BEFORE the rw guarantee-create.

        An ``rw`` critical dest whose source vanished must RAISE. Consulted after the
        guarantee-create instead, ``mkdir(parents=True)`` would manufacture the very
        thing must-exist asks about — the safe-fail becomes an EMPTY DIRECTORY bound
        over the agent's binary, and nothing anywhere reports it.

        RED two ways: the raise disappears, AND the directory appears on disk.
        """
        from kanibako.settings.store_collapse import CollapsedBind

        src = tmp_path / "gone" / "claude"
        with pytest.raises(BindingSourceError):
            _emit_category_mounts(
                {self._CRITICAL: CollapsedBind(str(src), "rw")},
                label="shape", must_exist=frozenset({self._CRITICAL}),
            )
        assert not src.exists(), "the policy was consulted AFTER the guarantee-create"

    def test_skip_if_absent_drops_silently_and_the_default_warns(self, caplog):
        """SKIP-IF-ABSENT vs WARN-AND-DROP: same mount set, different noise.

        The agent's best-effort dests join the skip set for exactly this reason — a
        missing or suppressed agent share is ordinary, and warning on it every launch
        is what trains users to ignore warnings.
        """
        dest, src = "/home/agent/canon/handbook/box", "/nonexistent/chapter"
        with caplog.at_level(logging.WARNING):
            assert self._emit(dest, src, skip_if_absent=frozenset({dest})) == []
        assert caplog.records == []
        with caplog.at_level(logging.WARNING):
            assert self._emit(dest, src) == []
        assert [r.levelname for r in caplog.records] == ["WARNING"]

    def test_a_narrow_resolve_drops_the_agent_rows_it_must_not_emit(self, tmp_path):
        """A narrow resolve reads the user's cascade files, so an agent row reaches it.

        The MAIN path emits every agent delivery bind from the collapse, so a narrow
        caller emitting the same row would double-mount it. RED if
        ``_narrow_bind_map`` degrades to ``_bind_map_from_mounts``.
        """
        from kanibako.commands.start import _narrow_bind_map
        from kanibako.settings.settings_categories import CategoryEntry

        def row(scope, category, dest):
            return CategoryEntry(
                category=category, scope=scope, box_dest=dest,
                host_src=str(tmp_path), delivery="MOUNT", options="ro", name=dest,
                key_segments=(scope, *category.split("."), dest),
            )

        rows = [
            row("agent", "bindings.ro", "/home/agent/.local/bin/claude"),
            row("agent", "common", "/home/agent/.claude/plugins"),
            row("box", "bindings.ro", "/opt/kanibako"),
        ]

        # The agent BIND is dropped; an agent-scope COMMON is not a delivery bind
        # and stays, exactly as the box-scope bind does.
        assert sorted(_narrow_bind_map(rows)) == [
            "/home/agent/.claude/plugins", "/opt/kanibako",
        ]
        assert "/home/agent/.local/bin/claude" in _bind_map_from_mounts(rows)

    def test_a_BOX_scope_bind_at_a_CRITICAL_dest_takes_THAT_DESTS_policy(
        self, std, config, project_dir,
    ):
        """The scope-blindness the emitter's dest-keying implies, MEASURED end to end.

        The policy sets are dest-spelled, and by the time the emitter reads the map
        the scope is gone — so a user's own bind that WINS one of the agent's delivery
        destinations inherits that destination's must-exist safe-fail rather than the
        warn-and-drop it would have taken on the old route. Documented in MIGRATION
        §2.28; this is the half of it the reconcile and the fold have to agree on —
        that the box-scope declaration actually lands at that exact key.
        """
        critical = "/home/agent/.local/bin/claude"
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, reconciled = _resolve(std, proj, extra_default_categories={
            "box.bindings.ro": {critical: ("/nonexistent/kanibako-test-source",)},
        })
        chosen = _launch_bind_map(snapshot, reconciled)
        assert chosen[critical].src == "/nonexistent/kanibako-test-source"

        with pytest.raises(BindingSourceError, match=re.escape(critical)):
            _emit_category_mounts(
                chosen, label="policy", must_exist=frozenset({critical}),
            )

    def test_an_rw_dest_with_no_policy_still_guarantee_creates(self, tmp_path):
        """The L7 rw arm is UNMOVED — it just runs after the policy now."""
        from kanibako.settings.store_collapse import CollapsedBind

        src = tmp_path / "made" / "here"
        emitted = _emit_category_mounts(
            {"/home/agent/w": CollapsedBind(str(src), "rw")}, label="shape",
        )

        assert src.is_dir()
        assert [m.destination for m in emitted] == ["/home/agent/w"]


@pytest.mark.parametrize("leaf", ["bindings", "seeded", "synced"])
def test_the_leaves_are_installed_as_segments_never_a_dotted_key(
    leaf, std, config, project_dir,
):
    """A dest is DATA: the leaf holds ONE value, never a tree shattered on its dots."""
    proj = resolve_project(std, config, str(project_dir), initialize=True)
    snapshot, _rec = _resolve(std, proj)
    value = _assembly(snapshot)[leaf]

    # ⚑ TWO SHAPES, ONE PROPERTY. ``bindings`` is dest-KEYED; ``seeded`` and
    # ``synced`` are scope-ordered LISTS that CARRY their dest (2026-08-09d for the
    # first, 2026-08-10b for the split). Either way the dest arrives WHOLE.
    assert isinstance(value, dict if leaf == "bindings" else list)
    dests = list(value) if leaf == "bindings" else [entry.dest for entry in value]
    assert all("/" in dest for dest in dests), sorted(dests)
