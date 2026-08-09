"""The step-6b collapse WIRING — ``meta.assembly.*`` is PRODUCED, and drives NOTHING.

Roadmap step 6, verbatim: *"merge the information, but not perform the action"*. So the
oracle here is TWO-SIDED and the second half is the load-bearing one:

* the collapse runs on the REAL launch seam and its output lands at the two declared
  keys (this side is easy to fake green — assert MEANING, not shape);
* the emitted mounts / copies / envs are BYTE-IDENTICAL to a run with the wiring
  removed, INCLUDING the case where the collapse REFUSES the configuration.

That second case is real, not hypothetical: the collapse forbids a bind above a bind,
while ``reconcile_categories`` permits nested binds and errors only on two concrete
declarations at ONE identical dest. Prose: ``llm-docs/kanibako/commands/start.py.md``.
"""

import logging
from pathlib import Path

import pytest

from kanibako.commands.start import (
    _emit_category_mounts,
    _resolve_launch_snapshot,
    _split_home_bind,
)
from kanibako.settings.paths import resolve_project
from kanibako.settings.store_collapse import HOME_DEST
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
    """Everything the live route actually DELIVERS, as comparable plain data."""
    mounts = _emit_category_mounts(reconciled, label="assembly-wiring")
    return (
        [(m.destination, str(m.source), m.options) for m in mounts],
        [(c.box_dest, c.host_src, c.options, c.category) for c in reconciled.copies],
        [(e.box_dest, e.options) for e in reconciled.envs],
        [w.message() for w in reconciled.warnings],
    )


class TestTheCollapseIsProduced:
    """``meta.assembly.{bindings,copies}`` reach the snapshot off a real launch resolve."""

    def test_both_declared_leaves_are_written(self, std, config, project_dir):
        """RED if the wiring is deleted: neither leaf exists at all."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve(std, proj)

        assert sorted(_assembly(snapshot)) == ["bindings", "copies"]

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
        """No home in the entry list ⇒ no box to assemble ⇒ both leaves stay absent."""
        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _rec = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name="claude",
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=_WiringTarget(), agent_cfg=None,
            include_base_families=False,
        )

        assert _assembly(snapshot) == {}


class TestTheLivePathIsUnchanged:
    """⚑⚑ THE SAFETY CLAIM of an information-only step: delivery is byte-identical."""

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

    def test_a_refused_configuration_leaves_both_leaves_absent(
        self, std, config, project_dir,
    ):
        """A partial write would describe a box nothing could assemble — so write neither."""
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


@pytest.mark.parametrize("leaf", ["bindings", "copies"])
def test_the_leaves_are_installed_as_segments_never_a_dotted_key(
    leaf, std, config, project_dir,
):
    """A dest is DATA: the leaf holds ONE value, never a tree shattered on its dots."""
    proj = resolve_project(std, config, str(project_dir), initialize=True)
    snapshot, _rec = _resolve(std, proj)
    value = _assembly(snapshot)[leaf]

    # ⚑ TWO SHAPES, ONE PROPERTY. ``bindings`` is dest-KEYED; ``copies`` became a
    # scope-ordered LIST that CARRIES its dest (2026-08-09d: copies apply to the
    # home bind alone, and a dest MAY repeat). Either way the dest arrives WHOLE.
    assert isinstance(value, dict if leaf == "bindings" else list)
    dests = list(value) if leaf == "bindings" else [entry.dest for entry in value]
    assert all("/" in dest for dest in dests), sorted(dests)
