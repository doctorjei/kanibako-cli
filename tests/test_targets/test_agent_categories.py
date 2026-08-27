"""A plugin's declared agent-scope CATEGORIES reach a PERSONA node, re-rooted.

A plugin declares ``default_common()``, ``default_seeds()`` and
``default_category_binds()`` against its OWN name — the HARNESS — while the §2d read
pick overlays ``agent.default`` ∪ ``agent.<ACTIVE NODE>``. For a persona
(``navigator℘claude``) the harness-keyed tier is therefore never read, and the failure
is SILENT: no mount, no copy, no error, no warning. ``common`` was adapted first
(``agent_categories_for_node``); the other two hooks were still folded raw at THREE
call sites — twice in :func:`~kanibako.commands.start._resolve_launch_snapshot` and
again on the CREATE path in ``_apply_init_seeds``.

⚑ THIS IS INVISIBLE TO THE SHIPPED FLEET. Every first-party plugin returns ``{}`` from
both hooks, so no configuration of claude/goose/codex can show the defect and no test
driven by them can catch it. Every case here therefore drives a target that DECLARES
something, and the module's whole value is that non-empty return.

⚑ RE-KEY IS ONLY HALF. The source is re-rooted onto ``@meta.agent.<node>.path`` too,
which is a SYMLINK ``commands.start.ensure_persona_share_symlinks`` lays at the
harness's real store (ruled 2026-08-27: *"the persona doesn't resolve to the claude
dir. It resolves to its own symlink… the user can change the symlink to a directory or
real target"*). Pointing the source straight at the harness store would look identical
in every arrival assertion and silently destroy that escape hatch — so the escape hatch
is asserted here, not assumed.
"""

from __future__ import annotations

import logging

import pytest

from kanibako.settings.agent_representation import agent_categories_for_node
from kanibako.settings.settings_launch import (
    build_launch_snapshot,
    meta_agent_path_floor,
    meta_identity_floor,
    snapshot_category_entries,
)
from kanibako.settings.settings_resolve import ResolveCtx
from kanibako.targets.no_agent import NoAgentTarget

HARNESS = "claude"
NODE = "navigator℘claude"
#: The node's on-disk store dirname — ``℘`` is a key device, a directory wears ``+``.
NODE_DIR = "navigator+claude"

#: What the declaring target below ships, spelled out so a change to it fails by name.
#: Sources are DECLARATION-ROOTED at the harness's own store, which is what makes them
#: re-rootable; destinations are guest-absolute (R-11) and never move.
DECLARED_BINDS = {
    "agent.claude.caches": {
        "/home/agent/.kani_cache": ("@meta.agent.claude.path/caches/kani",),
    },
    "agent.claude.bindings.ro": {
        "/home/agent/kani_ro": ("@meta.agent.claude.path/robits", "ro"),
    },
}
DECLARED_SEEDS = {
    "agent.claude.seeded": {
        "~/kani_seed": ("@meta.agent.claude.path/seedsrc",),
    },
}


class DeclaringTarget(NoAgentTarget):
    """A REAL target declaring a seed AND two category binds against its HARNESS name."""

    @property
    def name(self) -> str:
        return HARNESS

    def rom_root(self):
        return None

    def default_seeds(self):
        return DECLARED_SEEDS

    def default_category_binds(self):
        return DECLARED_BINDS


def make_ctx(node: str) -> ResolveCtx:
    return ResolveCtx(
        agent_name=node, workset_name=None, host_home="/home/u",
        xdg={"XDG_DATA_HOME": "/data", "XDG_CACHE_HOME": "/xcache"},
        config={"config.data": "/data", "config.agents": "/data/agents"},
    )


def category_entries(table, *, node: str, adapt: bool = True):
    """Resolve *table* through the REAL chain and return its CategoryEntry list.

    *adapt* False builds the floor the launch would have WITHOUT the node adaptation —
    the negative control, which is what shows the adaptation is load-bearing rather
    than merely present.
    """
    ctx = make_ctx(node)
    floor: dict[str, object] = {}
    floor.update(meta_identity_floor(
        box_name="b", project_path="/p", inbox="/i", share_global="/sg",
        share_workset=None, agent_name=node,
    ))
    floor.update(meta_agent_path_floor(node))
    floor.update(
        agent_categories_for_node(table, node_name=node, harness=HARNESS)
        if adapt else table
    )
    snap = build_launch_snapshot(
        agent_name=node, ctx=ctx,
        system_path=None, agent_path=None, workset_path=None, box_path=None,
        default_categories=floor,
    )
    return snapshot_category_entries(snap, active_agent=node, box_ctx=ctx)


class TestTheDeclarationsArriveForAPersona:
    """The positive story, through the real resolve chain, per category."""

    @pytest.mark.parametrize("table,dests", [
        (DECLARED_BINDS, ["/home/agent/.kani_cache", "/home/agent/kani_ro"]),
        (DECLARED_SEEDS, ["/home/agent/kani_seed"]),
    ])
    def test_a_persona_resolves_every_declared_destination(self, table, dests):
        entries = category_entries(table, node=NODE)
        assert sorted(e.box_dest for e in entries) == sorted(dests)

    @pytest.mark.parametrize("table", [DECLARED_BINDS, DECLARED_SEEDS])
    def test_a_harness_keyed_floor_reaches_a_persona_with_NOTHING(self, table):
        """⚑ THE NEGATIVE CONTROL, measured through the real chain.

        Feed the resolve the plugin's own HARNESS-keyed table — the floor the launch
        built before the fix — and a persona node resolves NONE of it. Nothing
        raises and nothing warns; the box simply comes up without the mount or the
        copy, which is why the positive cases above are not enough on their own.
        """
        assert category_entries(table, node=NODE, adapt=False) == []

    @pytest.mark.parametrize("table", [DECLARED_BINDS, DECLARED_SEEDS])
    def test_a_bare_agent_resolves_the_same_either_way(self, table):
        """BYTE-IDENTICAL for every non-persona launch — the whole shipped fleet.

        ⚑ Both arms are asserted EQUAL rather than merely non-empty: the adaptation
        is an identity for a bare node, so a change that altered a bare launch would
        show here rather than in a downstream count.
        """
        assert category_entries(table, node=HARNESS) == category_entries(
            table, node=HARNESS, adapt=False,
        )

    def test_the_source_is_rooted_at_the_NODE_store_not_the_harness(self):
        """🛑 THE HALF AN ARRIVAL ASSERTION CANNOT SEE.

        Re-keying alone makes every case above pass while the persona binds the
        harness store DIRECTLY — and then replacing the node's symlink with a real
        directory changes nothing, which is exactly the freedom the indirection
        exists to give.
        """
        entries = category_entries(DECLARED_BINDS, node=NODE)
        for entry in entries:
            assert f"/agents/{NODE_DIR}/" in entry.host_src, entry.host_src
            assert f"/agents/{HARNESS}/" not in entry.host_src, entry.host_src


class TestTheLaunchSeamCarriesTheDeclarations:
    """The REAL seam, through ``_resolve_launch_snapshot`` — the two launch sites.

    Everything above resolves a floor this module assembles, so it stays green even
    if ``commands.start`` never applies the adapter. These ask the seam itself and
    read the winners off the collapsed leaves a launch consumes.
    """

    def _launch(self, std, config, project_dir, *, node: str):
        from kanibako.commands.start import (
            _launch_bind_map,
            _launch_seed_list,
            _resolve_launch_snapshot,
        )
        from kanibako.settings.paths import resolve_project

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        snapshot, _deliveries = _resolve_launch_snapshot(
            std=std, proj=proj, agent_name=node,
            system_settings_path=None, agent_cfg_path=None,
            desc=None, install=None, target=DeclaringTarget(),
            agent_cfg=None, deliver_creds=True,
        )
        return _launch_bind_map(snapshot), _launch_seed_list(snapshot)

    def test_a_bare_agent_launch_carries_both_hooks(self, std, config, project_dir):
        binds, seeds = self._launch(std, config, project_dir, node=HARNESS)
        assert binds["/home/agent/.kani_cache"].src == str(
            std.agents / HARNESS / "caches" / "kani",
        )
        assert binds["/home/agent/kani_ro"].opts == "ro"
        assert [s.src for s in seeds if s.dest == "/home/agent/kani_seed"] == [
            str(std.agents / HARNESS / "seedsrc"),
        ]

    def test_a_persona_launch_carries_the_category_binds(
        self, std, config, project_dir,
    ):
        """RED if the seam folds ``default_category_binds()`` in harness-keyed: the
        dests vanish from the collapsed bind map entirely."""
        binds, _seeds = self._launch(std, config, project_dir, node=NODE)
        assert binds["/home/agent/.kani_cache"].src == str(
            std.agents / NODE_DIR / "caches" / "kani",
        )
        assert binds["/home/agent/kani_ro"].src == str(
            std.agents / NODE_DIR / "robits",
        )
        assert binds["/home/agent/kani_ro"].opts == "ro"

    def test_a_persona_launch_carries_the_seed(self, std, config, project_dir):
        """RED if the seam folds ``default_seeds()`` in harness-keyed. ⚑ THE OTHER
        SEED SITE IS THE CREATE PATH — fixing one leaves the other broken, which is
        why ``TestTheCreatePathCarriesTheSeed`` exists separately."""
        _binds, seeds = self._launch(std, config, project_dir, node=NODE)
        assert [s.src for s in seeds if s.dest == "/home/agent/kani_seed"] == [
            str(std.agents / NODE_DIR / "seedsrc"),
        ]


class TestTheCreatePathCarriesTheSeed:
    """``_apply_init_seeds`` — the SECOND ``default_seeds()`` fold, and it copies.

    The launch fold and this one are independent call sites reading the same hook, so
    a fix applied to one leaves the other silently broken. These cases run the real
    create-time seed apply and assert on the BYTES that land in the box home, which
    is the only place the difference between "resolved" and "delivered" shows.
    """

    def _apply(self, std, config, project_dir, *, node: str):
        from kanibako.commands.start import (
            _apply_init_seeds,
            ensure_persona_share_symlinks,
        )
        from kanibako.settings.paths import resolve_project

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        target = DeclaringTarget()
        harness_src = std.agents / HARNESS / "seedsrc"
        harness_src.mkdir(parents=True, exist_ok=True)
        (harness_src / "declared.md").write_text("from the harness\n")
        ensure_persona_share_symlinks(std, node, target)
        _apply_init_seeds(
            std=std, proj=proj, agent_name=node, target=target,
            global_config_path=None, agent_config_path=None,
            logger=logging.getLogger("test.seed"),
        )
        return proj.shell_path / "kani_seed" / "declared.md"

    def test_a_bare_agent_create_copies_the_declared_seed(
        self, std, config, project_dir,
    ):
        assert self._apply(
            std, config, project_dir, node=HARNESS,
        ).read_text() == "from the harness\n"

    def test_a_persona_create_copies_it_THROUGH_the_link(
        self, std, config, project_dir,
    ):
        """The harness's content reaches a persona box — by VALUE, through the
        symlink the shim laid at the node's re-rooted source.

        (Mutation: revert this call site alone to ``target.default_seeds()`` and the
        file is never written, while every launch-seam case above stays green.)
        """
        assert self._apply(
            std, config, project_dir, node=NODE,
        ).read_text() == "from the harness\n"

    def test_the_escape_hatch_gives_the_persona_its_own_content(
        self, std, config, project_dir,
    ):
        """🛑 THE WHOLE POINT OF THE INDIRECTION, and the assertion a re-root
        straight to the harness store would fail.

        *"the user can change the symlink to a directory or real target"* — a real
        ``agents/<node>/seedsrc`` is never clobbered by the shim, so the persona
        seeds from ITS OWN content while the harness keeps its.
        """
        own = std.agents / NODE_DIR / "seedsrc"
        own.mkdir(parents=True)
        (own / "declared.md").write_text("the persona's own\n")
        assert self._apply(
            std, config, project_dir, node=NODE,
        ).read_text() == "the persona's own\n"

    def test_without_the_link_the_rerooted_source_is_simply_ABSENT(
        self, std, config, project_dir,
    ):
        """⚑ THE CONTROL FOR THE HAZARD THE RE-ROOT CREATES.

        Re-rooting moves the source onto a node path that DOES NOT EXIST until the
        shim links it — so a re-root whose coverage the shim does not match is
        today's symptom moved one hop, not a fix. Skipping the shim here reproduces
        exactly that, which is what makes the two cases above meaningful.
        """
        from kanibako.commands.start import _apply_init_seeds
        from kanibako.settings.paths import resolve_project

        proj = resolve_project(std, config, str(project_dir), initialize=True)
        harness_src = std.agents / HARNESS / "seedsrc"
        harness_src.mkdir(parents=True, exist_ok=True)
        (harness_src / "declared.md").write_text("from the harness\n")
        # NO ``ensure_persona_share_symlinks`` call — the one difference.
        _apply_init_seeds(
            std=std, proj=proj, agent_name=NODE, target=DeclaringTarget(),
            global_config_path=None, agent_config_path=None,
            logger=logging.getLogger("test.seed"),
        )
        assert not (proj.shell_path / "kani_seed").exists()
