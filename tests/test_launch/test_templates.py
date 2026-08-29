"""Tests for kanibako.launch.templates (the layered home-seed / template trio)."""

from __future__ import annotations

import argparse
import logging
import shutil

import pytest
import yaml

from kanibako.settings.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.launch.templates import (
    _packaged_base_template,
    _packaged_manifest_entries,
    copy_resource_tree_if_absent,
    handbook_layer_source_keys,
    install_box_handbook_template,
    install_packaged_templates,
    packaged_templates_digest,
    plan_template_refresh,
    stage_layers,
    template_seed_defaults,
)
from kanibako.settings.core_defaults import ROM_GUIDE_REL as _GUIDE_REL
from kanibako.project.workset import add_project, create_workset


class TestStageLayers:
    """The staging primitive: per-file last-wins merged in a temp dir, then
    seeded into the dest with create-if-absent (never clobbers an existing file);
    an absent-dir layer is skipped."""

    def _layer(self, root, name, files):
        d = root / name
        d.mkdir(parents=True)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_ordered_workset_wins_per_file(self, tmp_path):
        """base -> agent -> workset staged in order; highest layer wins per-file."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {
            "base-only.txt": "base",
            "shared.txt": "base version",
            "two.txt": "base two",
        })
        agent = self._layer(tmp_path, "agent", {
            "agent-only.txt": "agent",
            "shared.txt": "agent version",
            "two.txt": "agent two",
        })
        workset = self._layer(tmp_path, "workset", {
            "workset-only.txt": "workset",
            "shared.txt": "workset version",
        })

        stage_layers(home, [base, agent, workset])

        # Each layer's unique files all land.
        assert (home / "base-only.txt").read_text() == "base"
        assert (home / "agent-only.txt").read_text() == "agent"
        assert (home / "workset-only.txt").read_text() == "workset"
        # Highest layer to set a file wins (workset > agent > base).
        assert (home / "shared.txt").read_text() == "workset version"
        # agent overlays base where workset is silent.
        assert (home / "two.txt").read_text() == "agent two"

    def test_existing_home_file_survives_reseed(self, tmp_path):
        """THE CLOBBER REGRESSION: a pre-existing home file survives a re-seed.

        A layer shipping the same relative path must NOT overwrite the user's
        content (create-if-absent seed).  This is the load-bearing data-loss guard.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "shared.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "shared.txt": "base version",
            "base-only.txt": "base",
        })

        stage_layers(home, [base])

        # Pre-existing file preserved (NOT clobbered by the layer's same-path file).
        assert (home / "shared.txt").read_text() == "user changes"
        # New file from the layer still lands.
        assert (home / "base-only.txt").read_text() == "base"

    def test_existing_nested_file_not_clobbered(self, tmp_path):
        """A pre-existing file in a nested home subdir survives a re-seed."""
        home = tmp_path / "home"
        nested = home / ".claude"
        nested.mkdir(parents=True)
        (nested / "settings.json").write_text("user settings")
        agent = self._layer(tmp_path, "agent", {".claude/settings.json": "shipped"})

        stage_layers(home, [agent])

        assert (home / ".claude" / "settings.json").read_text() == "user settings"

    def test_nested_directories(self, tmp_path):
        """Layers with nested directory structure are seeded correctly."""
        home = tmp_path / "home"
        home.mkdir()
        agent = tmp_path / "agent"
        nested = agent / ".claude"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("# Instructions")

        stage_layers(home, [agent])

        assert (home / ".claude" / "CLAUDE.md").read_text() == "# Instructions"

    def test_no_layers_is_noop(self, tmp_path):
        """No layers -> dest is untouched."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "existing.txt").write_text("untouched")

        stage_layers(home, [])

        assert (home / "existing.txt").read_text() == "untouched"
        assert sorted(p.name for p in home.iterdir()) == ["existing.txt"]

    def test_absent_layer_dir_skipped(self, tmp_path):
        """A layer whose source dir does not exist is silently skipped (skip-if-
        absent, spec §2a) — the remaining present layers still seed."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {"base-only.txt": "base"})
        missing = tmp_path / "nope"  # never created

        stage_layers(home, [base, missing])

        assert (home / "base-only.txt").read_text() == "base"

    def test_all_layers_absent_is_noop(self, tmp_path):
        """Every layer absent -> nothing staged, dest untouched (no temp dir churn)."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "keep.txt").write_text("keep")

        stage_layers(home, [tmp_path / "a", tmp_path / "b"])

        assert sorted(p.name for p in home.iterdir()) == ["keep.txt"]


# ---------------------------------------------------------------------------
# The layered ``seeded`` DEFAULT-category table (spec §2a; Q1-Q4).  ⚑ ``seeded`` is
# a TERMINAL DEST-KEYED key since 2026-08-08c: ``<scope>.seeded`` holds the whole
# ``{box_dest: (host_src[, options])}`` map and the entry NAME (``.template``) is
# gone — the destination is the identity.
# ---------------------------------------------------------------------------

@pytest.fixture
def primary_proj(std, config, project_dir):
    return resolve_project(std, config, str(project_dir), initialize=True)


@pytest.fixture
def named_proj(std, config, tmp_home):
    ws_root = tmp_home / "worksets" / "my-set"
    ws = create_workset("my-set", ws_root, std)
    source = tmp_home / "original-project"
    source.mkdir()
    add_project(ws, "cool-app", source)
    return resolve_workset_project(
        WorksetSpec.from_workset(ws), "cool-app", std, config, initialize=True,
    )


@pytest.fixture
def standalone_proj(std, config, project_dir, credentials_dir):
    return resolve_standalone_project(
        std, config, str(project_dir), initialize=True,
    )


class TestTemplateSeedDefaults:
    """``template_seed_defaults`` declares the THREE §2a layers as ordinary keystore
    ``seeded`` keys (+ their ``@``-ref SOURCE keys), gated per mode / agent."""

    def test_system_layer_always_present(self, primary_proj):
        defs = template_seed_defaults(primary_proj, "claude")
        # Layer 1 (base) rides the seed system with NO carve-out (Q4).
        # ⚑ DEST-KEYED and TERMINAL (2026-08-08c): ``system.seeded`` IS the key and
        # its value is the whole ``{box_dest: (src,)}`` map. There is no entry name.
        assert defs["system.seeded"] == {
            "~/": ("@system.template/box/home",),
        }

    def test_the_only_seed_dest_is_the_box_home(self, primary_proj):
        """⚑ THE H2 PIN, RESPELLED. Every declared seed dest is the GUEST home
        ``~/`` and NOTHING targets ``@box.canon/handbook`` any more.

        The handbook layers left the category on 2026-08-07g (HOST templates, not
        GUEST templates), so no declared ``seeded`` entry names the handbook dest any
        more.  Asserted as an EQUALITY on the whole dest set rather than as a ``not
        in``: a bare absence assertion would stay green if the layers came back under
        any other name — and, since the dests are now the map KEYS, the same equality
        also catches a fourth layer added at a dest of its own.

        ⚑ It ALSO pins the 2026-08-08c RESPELL: the dest used to be the absolute
        HOST path ``@meta.box.path/home``, which needed a per-entry space
        discriminator to stop the guest translator re-rooting it (see
        ``settings_categories.CategoryEntry``). A host-spelled dest coming back
        makes this RED, which is the point — the discriminator that made one safe
        is gone.
        """
        defs = template_seed_defaults(primary_proj, "claude")
        dests = [
            dest for key, value in defs.items() if key.endswith(".seeded")
            for dest in value
        ]
        assert dests, defs
        assert set(dests) == {"~/"}, dests

    def test_agent_layer_sources_the_nodes_own_store(self, primary_proj):
        """Layer 2: ``agent.<node>.seeded`` reads ``@agent.<node>.template``, which
        defaults to the NODE's OWN store — ``@config.agents/<node>/template``.

        ⚑ RULED 2026-08-27, and the test NAME used to say the opposite ("sources
        harness store").  The emitter spelled ``harness_of(agent_id)``; §2d and the
        manifest both root the key at the ACTIVE NODE, so the code moved.  A persona
        still gets the harness's CONTENT — by SYMLINK, laid by
        ``commands.start.ensure_persona_share_symlinks`` — which is what makes the
        node-rooted source resolve to something.
        """
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["agent.claude.template"] == "@config.agents/claude/template"
        assert defs["agent.claude.seeded"] == {
            "~/": ("@agent.claude.template/box/home",),
        }

    def test_a_persona_node_sources_its_own_store_not_the_harness(
        self, primary_proj,
    ):
        """THE CASE A BARE AGENT CANNOT SHOW: node != harness.

        For ``claude`` the node-rooted and harness-rooted spellings are the SAME
        STRING, so every assertion above stays green under either implementation.
        Only a persona separates them — which is exactly why the old harness-rooted
        emit survived so long, and why this case exists.

        (Mutation: restore ``harness = harness_of(agent_id)`` and root the value at
        it → this goes RED with ``@config.agents/claude/template``.)
        """
        node = "navigator℘claude"
        defs = template_seed_defaults(primary_proj, node)
        # ⚑ KEY vs DIRECTORY, both literal: the key segment stays the canonical node,
        # the VALUE is a store path and carries the ``+`` dirname.
        assert defs[f"agent.{node}.template"] == (
            "@config.agents/navigator+claude/template"
        )
        assert "@config.agents/claude/template" not in defs.values()
        # The layer keys are the node's too — nothing here is harness-keyed.
        assert defs[f"agent.{node}.seeded"] == {
            "~/": (f"@agent.{node}.template/box/home",),
        }
        assert not any("claude" == k.split(".")[1] for k in defs if
                       k.startswith("agent.") and not k.startswith("agent.default."))

    def test_landing_path_equals_layer_2_source(self, primary_proj):
        """⚑⚑ THE MUST-FIX, pinned: layer 2's SOURCE ref resolves to exactly the
        STORE-RELATIVE path the seed reads, and both sides are DERIVED from the same
        constants, so they cannot drift.

        The ``template/`` prefix is the half that is easy to drop: a store-relative
        landing of just ``box/home`` puts the content at ``agents/<name>/box/home/**``,
        which NOTHING reads — the stamp runs, reports nothing, and the box still comes
        up with no agent config. That is what a plugin's ``data/base`` payload has to
        be spelled against.

        ⚑ It arrived here with the legacy plugin-payload arm's removal (2026-08-26);
        the property it pins was never about legacy payloads.
        """
        from kanibako.launch.templates import (
            AGENT_TEMPLATE_STORE_REL,
            _SEED_SRC_HOME,
        )

        defs = template_seed_defaults(primary_proj, "claude")
        # Layer 2's SOURCE, with its @-ref head resolved the way the cascade does:
        #   @agent.claude.template -> @config.agents/claude/<store rel>
        source = defs["agent.claude.seeded"]["~/"][0]
        store_ref = defs["agent.claude.template"]
        assert source.startswith("@agent.claude.template/")
        resolved = source.replace("@agent.claude.template", store_ref, 1)
        store_relative = resolved.split("@config.agents/claude/", 1)[1]
        assert store_relative == f"{AGENT_TEMPLATE_STORE_REL}/{_SEED_SRC_HOME}", (
            "the seeded payload lands where NOTHING reads it"
        )

    def test_no_agent_omits_agent_layer(self, primary_proj):
        defs = template_seed_defaults(primary_proj, None)
        assert not any(k.startswith("agent.") for k in defs)
        # system + workset layers still declared.
        assert "system.seeded" in defs
        assert "workset.seeded" in defs

    def test_workset_layer_default_points_at_workset_template(self, primary_proj):
        """Layer 3 default = @meta.workset.path/template (Q3, was <None>).

        ⚑⚑ THE SOURCE KEY IS THE FLOOR'S, NOT THIS TABLE'S (2026-08-29).  This table
        only ``@``-REFERENCES ``@workset.template``; ``settings_launch
        .workset_anchor_floor`` declares it, beside ``workset.registry``.  A source key
        spelled only here answered for a box being CREATED and for no box that already
        existed — this table's one consumer is the create-time seed resolve.

        Both halves are asserted TOGETHER on purpose: the reference and the declaration
        are what make the layer resolve, and a test that checked only one of them would
        stay green while the other went missing.
        """
        from kanibako.settings.settings_launch import workset_anchor_floor

        defs = template_seed_defaults(primary_proj, "claude")
        assert "workset.template" not in defs
        assert defs["workset.seeded"] == {
            "~/": ("@workset.template/box/home",),
        }
        assert (
            workset_anchor_floor(mode="primary")["workset.template"]
            == "@meta.workset.path/template"
        )

    def test_named_includes_workset_layer(self, named_proj):
        defs = template_seed_defaults(named_proj, "claude")
        assert "workset.seeded" in defs

    def test_standalone_omits_workset_layer(self, standalone_proj):
        """STANDALONE has no workset tier -> no workset.template source/layer
        (spec §2c workset.template <None>)."""
        defs = template_seed_defaults(standalone_proj, "claude")
        assert "workset.template" not in defs
        assert "workset.seeded" not in defs
        # base + agent layers still present.
        assert "system.seeded" in defs
        assert "agent.claude.seeded" in defs

    def test_exactly_three_seed_layer_keys_are_declared(self, primary_proj):
        """The seed LAYER SET is pinned as an equality — a fourth layer cannot be
        added without this test naming it.

        ⚑ THIS REPLACES ``test_seed_keys_of_selects_exactly_the_seeded_keys``.
        That test derived a HOST-space key set via ``templates.seed_keys_of``, which
        was DELETED with the 2026-08-08c respell (every dest is guest-spelled now, so
        there is no second namespace to select). Its anti-drift half, however, did NOT
        dissolve, and is restored here.

        ⚑⚑ IT IS NOT SUBSUMED BY ``test_the_only_seed_dest_is_the_box_home``, and the
        difference is the whole reason this exists: that test asserts the set of
        DESTS is ``{"~/"}``, so a FOURTH layer added at the same ``~/`` dest leaves it
        GREEN. Only an equality on the KEYS catches one. (A layer DROPPED is caught by
        the per-layer tests above, which assert each key's value individually.)
        """
        defs = template_seed_defaults(primary_proj, "claude")
        assert {k for k in defs if k.endswith(".seeded")} == {
            "system.seeded",
            "agent.claude.seeded",
            "workset.seeded",
        }


# ---------------------------------------------------------------------------
# End-to-end layered home-seed through the keystore (_apply_init_seeds).
# ---------------------------------------------------------------------------

class _FakeTarget:
    """Minimal resolved-agent stand-in: only ``.name`` + ``default_seeds()``
    (empty) are read by the seed seam for a non-descriptor test target."""

    name = "claude"

    def default_seeds(self):
        return {}


def _seed(std, proj, *, agent="claude", deliver_creds=True, agent_cfg_path=None):
    """Drive the one-time home seed (the unified keystore-routed route)."""
    from kanibako.commands.start import _apply_init_seeds

    _apply_init_seeds(
        std=std,
        proj=proj,
        agent_name=agent,
        target=_FakeTarget() if agent else None,
        global_config_path=std.settings,
        agent_config_path=(
            agent_cfg_path if agent_cfg_path is not None
            else std.agents / "claude" / "agent.yaml"
        ),
        logger=logging.getLogger("test-seed"),
        deliver_creds=deliver_creds,
    )


class TestLayeredHomeSeed:
    """The template trio seeded through the SINGLE keystore ``seeded`` route
    (``_apply_init_seeds``) — base -> agent -> workset, per-file last-wins,
    create-if-absent, skip-if-absent, credential-gate-exempt."""

    def _populate(self, std, primary_proj):
        install_packaged_templates(std, ["claude"])
        base_home = std.template / "box" / "home"
        base_home.mkdir(parents=True, exist_ok=True)
        (base_home / "base-only.txt").write_text("base")
        (base_home / "shared.txt").write_text("base")
        agent_home = std.agents / "claude" / "template" / "box" / "home"
        agent_home.mkdir(parents=True, exist_ok=True)
        (agent_home / "agent-only.txt").write_text("agent")
        (agent_home / "shared.txt").write_text("agent")
        ws_home = std.primary_workset / "template" / "box" / "home"
        ws_home.mkdir(parents=True, exist_ok=True)
        (ws_home / "workset-only.txt").write_text("workset")
        (ws_home / "shared.txt").write_text("workset")
        return ws_home

    def test_all_three_layers_seed_every_file(self, std, config, primary_proj):
        """Q4: every file present in EACH layer dir is seeded — base + agent +
        workset, packaged content included (not an enumerated subset)."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj)
        home = primary_proj.shell_path
        # Base layer — the packaged notebook AND the custom marker.
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home / "canon" / "workbook" / "devnotes.md").is_file()
        assert (home / "base-only.txt").read_text() == "base"
        # Agent layer — the packaged .claude.json/settings AND the custom marker.
        assert (home / ".claude.json").is_file()
        assert (home / ".claude" / "settings.json").is_file()
        assert (home / "agent-only.txt").read_text() == "agent"
        # Workset layer.
        assert (home / "workset-only.txt").read_text() == "workset"

    def test_the_seed_route_no_longer_writes_the_handbook_chapter(
        self, std, config, primary_proj,
    ):
        """⚑⚑ THE H2 CUT, end to end. ``_apply_init_seeds`` ALONE must leave
        ``@box.canon/handbook`` untouched: the three handbook layers left the
        ``seeded`` category (2026-08-07g), so the only route that fills the chapter
        is step 3's host-side copy.

        MUTATION-PROVED, and it had to be: this is a negative about a route, and the
        packaged system template really does ship ``box/canon/handbook/directives/
        SYS_BOX.md``, so the source exists and the assertion discriminates.  Putting
        the handbook entry back into ``templates._layer()`` makes it FAIL.
        ``TestBoxHandbookHostCopyThroughTheSeam`` pins that the chapter still
        ARRIVES, so this is not proving delivery was dropped."""
        install_packaged_templates(std, ["claude"])
        # The source the retired layer 4 read is really shipped and really there.
        assert (
            std.template / "box" / "canon" / "handbook" / "directives" / "SYS_BOX.md"
        ).is_file()
        _seed(std, primary_proj)
        box_root = primary_proj.shell_path.parent
        assert not (box_root / "canon" / "handbook").exists(), sorted(
            box_root.rglob("*"),
        )
        # ...and nothing leaked into the box HOME either.
        assert not list(primary_proj.shell_path.rglob("SYS_BOX.md"))

    def test_layer_order_last_wins_per_file(self, std, config, primary_proj):
        """base -> agent -> workset: the highest layer to ship a file wins."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj)
        assert (primary_proj.shell_path / "shared.txt").read_text() == "workset"

    def test_agent_overlays_base_when_workset_silent(self, std, config, primary_proj):
        """A file shipped by base + agent (not workset) resolves to the AGENT
        version (later layer overlays earlier) — the create-if-absent-per-seed
        FIRST-wins bug this route replaced would have kept the base version."""
        install_packaged_templates(std, ["claude"])
        (std.template / "box" / "home" / "two.txt").write_text("base two")
        agent_home = std.agents / "claude" / "template" / "box" / "home"
        agent_home.mkdir(parents=True, exist_ok=True)
        (agent_home / "two.txt").write_text("agent two")
        _seed(std, primary_proj)
        assert (primary_proj.shell_path / "two.txt").read_text() == "agent two"

    def test_existing_home_file_never_clobbered(self, std, config, primary_proj):
        """A user-owned home file survives the layered seed (create-if-absent)."""
        self._populate(std, primary_proj)
        home = primary_proj.shell_path
        home.mkdir(parents=True, exist_ok=True)
        (home / "shared.txt").write_text("USER OWNED")
        _seed(std, primary_proj)
        assert (home / "shared.txt").read_text() == "USER OWNED"

    def test_absent_workset_template_skipped(self, std, config, primary_proj):
        """No @workset.template dir on disk -> the layer is skipped (skip-if-
        absent) and base + agent still seed (no crash)."""
        install_packaged_templates(std, ["claude"])
        # Deliberately do NOT create std.primary_workset/template.
        _seed(std, primary_proj)
        home = primary_proj.shell_path
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_standalone_has_no_workset_layer(self, std, config, standalone_proj):
        """STANDALONE seeds base + agent only (no workset tier)."""
        install_packaged_templates(std, ["claude"])
        _seed(std, standalone_proj)
        home = standalone_proj.shell_path
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()

    def test_no_agent_box_seeds_base_only(self, std, config, primary_proj):
        """A NO-AGENT box seeds the base layer but NOT the agent layer."""
        install_packaged_templates(std, ["claude"])
        (std.template / "box" / "home" / "base-only.txt").write_text("base")
        _seed(std, primary_proj, agent="")
        home = primary_proj.shell_path
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home / "base-only.txt").is_file()
        # No agent template layer.
        assert not (home / ".claude.json").exists()

    def test_private_box_keeps_template_layers(self, std, config, primary_proj):
        """deliver_creds=False (PRIVATE box) suppresses CREDENTIAL seeds only — the
        template layers are non-credential and STILL seed (D-M4 gate exemption)."""
        self._populate(std, primary_proj)
        _seed(std, primary_proj, deliver_creds=False)
        home = primary_proj.shell_path
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()
        assert (home / ".claude.json").is_file()
        assert (home / "workset-only.txt").is_file()

    def test_workset_template_repoint_reroutes_seed(self, std, config, primary_proj, tmp_path):
        """MUTATION PROOF (settable source): setting ``workset.template`` in the
        workset settings file reroutes the layer-3 seed to the new dir — the seed
        reads the KEY, not a hardcoded path."""
        install_packaged_templates(std, ["claude"])
        custom = tmp_path / "custom-tpl" / "box" / "home"
        custom.mkdir(parents=True)
        (custom / "CUSTOM.txt").write_text("custom")
        # Default dir populated too — to prove the OVERRIDE wins over the default.
        ws_default = std.primary_workset / "template" / "box" / "home"
        ws_default.mkdir(parents=True, exist_ok=True)
        (ws_default / "DEFAULT.txt").write_text("default")

        wsf = std.primary_workset / "workset.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("workset", {})["template"] = str(tmp_path / "custom-tpl")
        wsf.write_text(yaml.safe_dump(doc))

        _seed(std, primary_proj)
        home = primary_proj.shell_path
        assert (home / "CUSTOM.txt").read_text() == "custom"
        # The default workset dir is NOT used once the key is repointed.
        assert not (home / "DEFAULT.txt").exists()

    # ⚑ The escaping-HOST-dest refusal (§2a enforcement point 2) was pinned here
    # against a ``box.canon`` repointed out of the box store, back when the handbook
    # trio made ``box.canon`` a seed dest.  It no longer is, and its deletion was
    # FORCED rather than hygienic: with no handbook seed dest NOTHING escapes, so no
    # warning is emitted and its ``assert any("outside the box store" in r.message
    # ...)`` would FAIL outright.  Only its FIRST assertion (that the escaped dir was
    # not written) would have gone vacuous.  The live pins are
    # ``TestBoxHandbookHostCopyThroughTheSeam.test_an_escaping_box_canon_is_skipped
    # _with_a_warning`` (the same claim, on the route that now owns the dest) and
    # ``test_seed_hostdest.TestHostCopyDest`` (the containment check itself).


# ---------------------------------------------------------------------------
# The BOX HANDBOOK HOST-TEMPLATE copy — Jei's 2026-08-07g ruling: the handbook
# templates are HOST templates, not GUEST templates, so they are copied beside the
# workset mould rather than delivered through the ``seeded`` category.
#
# ⚑ THE TWIN IS GONE (phase H2).  The three ``<scope>.seeded.handbook`` layers are
# no longer declared and ``_apply_init_seeds`` no longer touches
# ``@box.canon/handbook``, so this is the ONE route that fills the box's chapter and
# the tests below observe it directly — no wipe, no isolation.  That the seeded
# route no longer writes there is pinned by
# ``TestLayeredHomeSeed.test_the_seed_route_no_longer_writes_the_handbook_chapter``.
# ---------------------------------------------------------------------------

def _handbook_dir(proj):
    """``@box.canon/handbook`` at its DEFAULT resolution — a sibling of the home."""
    return proj.shell_path.parent / "canon" / "handbook"


def _tree(root):
    """``{relative path: bytes}`` for every file under *root* (missing -> ``{}``)."""
    if not root.is_dir():
        return {}
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*")) if p.is_file()
    }


def _seed_snapshot(std, proj, *, agent="claude"):
    """Run step 2 (the real seed) and return the snapshot it built — which is what
    ``_install_box_handbook`` reads its dest and layer roots off.

    No wipe is needed: since H2 the ``seeded`` route writes nothing at
    ``@box.canon/handbook``, so anything the caller then observes there came from
    step 3."""
    from kanibako.commands.start import _apply_init_seeds

    return _apply_init_seeds(
        std=std,
        proj=proj,
        agent_name=agent,
        target=_FakeTarget() if agent else None,
        global_config_path=std.settings,
        agent_config_path=std.agents / "claude" / "agent.yaml",
        logger=logging.getLogger("test-seed"),
    )


def _install_handbook(std, proj, *, agent="claude", logger=None):
    """Drive step 3 alone, off the snapshot the seed resolve built."""
    from kanibako.commands.start import _install_box_handbook

    snapshot = _seed_snapshot(std, proj, agent=agent)
    _install_box_handbook(
        proj=proj, snapshot=snapshot, agent_id=agent,
        logger=logger or logging.getLogger("test-handbook"),
    )


def _seed_box(std, proj, *, agent="claude", deliver_creds=True):
    """Drive the WHOLE create-time seed — all three ordered steps of the real
    ``_seed_box_home``, which is what ``box create`` calls."""
    from types import SimpleNamespace

    from kanibako.commands.start import _seed_box_home

    _seed_box_home(
        std=std,
        proj=proj,
        target=_FakeTarget() if agent else None,
        desc=None,
        agent_id=agent,
        agent_cfg_path=std.agents / "claude" / "agent.yaml",
        system_settings_path=std.settings,
        auth_src=SimpleNamespace(creds_shared=deliver_creds),
        logger=logging.getLogger("test-seed"),
    )


class TestPersonaTemplateLayerThroughTheLink:
    """⚑⚑ THE 2026-08-27 RULING, END TO END — a persona's layer 2 is NODE-ROOTED and
    the harness's CONTENT arrives through the SYMLINK SHIM.

    Two halves that must both hold, because either alone is satisfiable by the wrong
    implementation:

    * the SOURCE is ``@config.agents/<node>/template`` (pinned in
      ``TestTemplateSeedDefaults``), and
    * ``commands.start.ensure_persona_share_symlinks`` links that path at the
      harness's store, so the source RESOLVES TO CONTENT.

    Drop the link and the persona box seeds with layer 2 EMPTY — which is why the
    control case below is here rather than only the happy path.  ⚑ ``template`` is a
    COPY source, not a bind, so "shared" means the copier reads THROUGH the link and
    lands BYTES; the seeded file must not itself be a symlink.
    """

    _HARNESS = "claude"
    _NODE = "navigator℘claude"

    def _shim_target(self):
        """A stub declaring EVERY category hook the shim reads, each ``{}``.

        ⚑ TYPED LIKE THE REAL COLLABORATOR: `Target` gives all three hooks concrete
        ``{}`` defaults, so a real target always answers them and only a hand-built
        stub can omit one — an omission that reads as a shim bug rather than as the
        stub lying. Empty tables isolate the TEMPLATE half, so nothing here depends
        on which categories the claude plugin declares.
        """
        from types import SimpleNamespace

        return SimpleNamespace(
            name=self._HARNESS,
            default_common=lambda: {},
            default_seeds=lambda: {},
            default_category_binds=lambda: {},
        )

    def _node_store(self, std):
        """The node's store dir, from the production helper — the link, the key and
        this test must all name ONE directory or the store silently splits."""
        from kanibako.settings.agent_config import store_dirname

        return std.agents / store_dirname(self._NODE)

    def _harness_marker(self, std):
        home = std.agents / self._HARNESS / "template" / "box" / "home"
        home.mkdir(parents=True, exist_ok=True)
        (home / "persona-layer2.txt").write_text("from the harness template")
        return home

    def _seed_node(self, std, proj):
        _seed(
            std, proj, agent=self._NODE,
            agent_cfg_path=self._node_store(std) / "agent.yaml",
        )

    def test_persona_seeds_the_harness_template_through_the_link(
        self, std, config, primary_proj,
    ):
        from kanibako.commands.start import ensure_persona_share_symlinks

        install_packaged_templates(std, [self._HARNESS])
        self._harness_marker(std)
        ensure_persona_share_symlinks(std, self._NODE, self._shim_target())
        self._seed_node(std, primary_proj)

        # ⚑ THE SPLIT-STORE GUARD, and it is LITERAL on purpose.  The link and the
        # ``agent.<node>.template`` key are composed by DIFFERENT code; leave either
        # one on the ``℘`` spelling and they name different dirs, the guarantee-create
        # makes the missing one, and the seed reads an empty half-store with no error
        # raised anywhere.  Both halves are asserted, so either direction reds.
        assert (std.agents / "navigator+claude" / "template").is_symlink()
        assert not (std.agents / "navigator℘claude").exists()

        landed = primary_proj.shell_path / "persona-layer2.txt"
        assert landed.is_file(), sorted(primary_proj.shell_path.rglob("*"))
        assert not landed.is_symlink(), "seeded BY VALUE, never as a link"
        assert landed.read_text() == "from the harness template"
        # The packaged claude payload rides the same layer.
        assert (primary_proj.shell_path / ".claude.json").is_file()

    def test_without_the_link_layer_2_is_EMPTY_not_the_harness_store(
        self, std, config, primary_proj,
    ):
        """THE CONTROL — and the reason this file needs one.

        If the emitter still rooted layer 2 at the harness, the previous test would
        pass with no shim at all.  Skipping ``ensure_persona_share_symlinks``
        therefore has to leave the box home WITHOUT the harness marker: that is what
        proves the content came through the LINK rather than through a
        harness-rooted key.  ⚑ The BASE layer still seeds — an absent layer 2 is
        skip-if-absent, not a failed create.
        """
        install_packaged_templates(std, [self._HARNESS])
        self._harness_marker(std)
        # NO ensure_persona_share_symlinks call.
        self._seed_node(std, primary_proj)

        home = primary_proj.shell_path
        assert not (home / "persona-layer2.txt").exists()
        assert not (home / ".claude.json").exists()
        # ...and the create still succeeded: layer 1 landed.
        assert (home / "canon" / "notebook" / "MY_CONTENTS.md").is_file()

    def test_a_persona_owned_template_dir_beats_the_link(
        self, std, config, primary_proj,
    ):
        """THE ESCAPE HATCH, end to end: *"the user can always remove the symlink if
        they want to create a separate template for the persona-based agent"* (Jei,
        2026-08-27).  A real ``agents/<node>/template`` is never replaced by the
        shim, and the seed reads it instead of the harness's.
        """
        from kanibako.commands.start import ensure_persona_share_symlinks

        install_packaged_templates(std, [self._HARNESS])
        self._harness_marker(std)
        own = self._node_store(std) / "template" / "box" / "home"
        own.mkdir(parents=True)
        (own / "persona-only.txt").write_text("mine")
        ensure_persona_share_symlinks(std, self._NODE, self._shim_target())

        node_template = self._node_store(std) / "template"
        assert node_template.is_dir() and not node_template.is_symlink()
        self._seed_node(std, primary_proj)

        home = primary_proj.shell_path
        assert (home / "persona-only.txt").read_text() == "mine"
        assert not (home / "persona-layer2.txt").exists()

    def test_bare_agent_seed_is_unchanged(self, std, config, primary_proj):
        """THE NO-CHANGE CONTROL.  For a bare agent the node-rooted and
        harness-rooted spellings are ONE STRING, so the ruling must be invisible
        here — no shim runs, and layer 2 seeds exactly as it always did."""
        install_packaged_templates(std, [self._HARNESS])
        self._harness_marker(std)
        _seed(std, primary_proj, agent=self._HARNESS)
        assert (
            primary_proj.shell_path / "persona-layer2.txt"
        ).read_text() == "from the harness template"
        assert not (std.agents / self._HARNESS / "template").is_symlink()


class TestHandbookLayerSourceKeys:
    """``handbook_layer_source_keys`` names the ORDERED source KEYS — derived from
    ``template_seed_defaults`` so the layer gate cannot drift between the two."""

    def test_three_keys_in_apply_order(self, primary_proj):
        assert handbook_layer_source_keys(primary_proj, "claude") == (
            "system.template", "agent.claude.template", "workset.template",
        )

    def test_no_agent_omits_the_agent_layer(self, primary_proj):
        assert handbook_layer_source_keys(primary_proj, None) == (
            "system.template", "workset.template",
        )

    def test_standalone_omits_the_workset_layer(self, standalone_proj):
        assert handbook_layer_source_keys(standalone_proj, "claude") == (
            "system.template", "agent.claude.template",
        )

    def test_every_key_is_a_declared_source_scalar_somewhere(self, primary_proj):
        """THE DRIFT PIN.  Every key names a SOURCE scalar SOME artefact declares —
        never a path this module invented.

        ⚑ THREE KEYS, THREE CARRIERS, and naming each is the point:

        * ``system.template`` — floor-materialized as a ``system.*`` settings-tier path,
          so it is in neither table below;
        * ``agent.<node>.template`` — ``launch.templates.agent_template_defaults``, which
          ``template_seed_defaults`` composes;
        * ``workset.template`` — ``settings_launch.workset_anchor_floor`` since
          2026-08-29, which is why this case no longer requires the seed table to carry
          every key.  Requiring that WOULD be the drift it is guarding against, in
          reverse: it would forbid the fix that made the key answer for an existing box.

        Each value is asserted to be an ``@``-ref STRING, not a seeded ``(src,)`` tuple —
        that is the half of the pin the carrier split does not touch.
        """
        from kanibako.settings.settings_launch import workset_anchor_floor

        defs = template_seed_defaults(primary_proj, "claude")
        floor = workset_anchor_floor(mode="primary")
        keys = handbook_layer_source_keys(primary_proj, "claude")
        assert keys[0] == "system.template"
        assert "system.template" not in defs and "system.template" not in floor
        for key in keys[1:]:
            carrier = defs if key in defs else floor
            assert key in carrier, key
            # A SOURCE scalar (an ``@``-ref string), NOT a seeded (src, dest) tuple.
            assert isinstance(carrier[key], str)
        # ...and the split is the one described above, spelled out so a key silently
        # changing carrier reds here rather than passing the loop either way.
        assert "agent.claude.template" in defs
        assert "workset.template" in floor and "workset.template" not in defs


class TestInstallBoxHandbookTemplate:
    """The copier itself, driven directly: three ordered layers, create-if-absent,
    skip-if-absent, guarantee-create — and NO dest whitelist of its own."""

    def _roots(self, tmp_path, spec):
        """Build ``<root>/box/canon/handbook/<rel>`` trees; ``None`` = no dir."""
        roots = []
        for name, files in spec:
            root = tmp_path / name
            roots.append(root)
            if files is None:
                continue
            d = root / "box" / "canon" / "handbook"
            d.mkdir(parents=True)
            for rel, content in files.items():
                p = d / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content)
        return roots

    def _box(self, tmp_path):
        """The DEST alone — ``@box.canon/handbook`` at its default resolution.

        The copier takes no box root: there is ONE dest policy on this path and it
        lives at the caller (``start._host_copy_dest``).  See
        ``install_box_handbook_template``'s "NO DEST WHITELIST HERE".
        """
        box_root = tmp_path / "boxes" / "mybox"
        (box_root / "home").mkdir(parents=True)
        return box_root / "canon" / "handbook"

    def test_all_three_layers_land(self, tmp_path):
        dest = self._box(tmp_path)
        roots = self._roots(tmp_path, [
            ("sys", {"a.md": "sys", "shared.md": "sys"}),
            ("agent", {"b.md": "agent", "shared.md": "agent"}),
            ("ws", {"c/deep.md": "ws", "shared.md": "ws"}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "a.md").read_text() == "sys"
        assert (dest / "b.md").read_text() == "agent"
        assert (dest / "c" / "deep.md").read_text() == "ws"

    def test_order_last_wins_per_file(self, tmp_path):
        dest = self._box(tmp_path)
        roots = self._roots(tmp_path, [
            ("sys", {"shared.md": "sys"}),
            ("agent", {"shared.md": "agent"}),
            ("ws", {"shared.md": "ws"}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "shared.md").read_text() == "ws"

    def test_agent_overlays_system_when_workset_is_silent(self, tmp_path):
        dest = self._box(tmp_path)
        roots = self._roots(tmp_path, [
            ("sys", {"shared.md": "sys"}),
            ("agent", {"shared.md": "agent"}),
            ("ws", {}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "shared.md").read_text() == "agent"

    def test_absent_layer_dir_is_skipped(self, tmp_path):
        """An unpopulated ``@workset.template`` is the NORMAL case — the layer is
        skipped and the others still land (no crash, no empty-dir sentinel)."""
        dest = self._box(tmp_path)
        roots = self._roots(tmp_path, [
            ("sys", {"a.md": "sys"}),
            ("agent", None),
            ("ws", None),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "a.md").read_text() == "sys"
        assert sorted(p.name for p in dest.rglob("*")) == ["a.md"]

    def test_all_layers_absent_still_guarantee_creates_the_dest(self, tmp_path):
        """GUARANTEE-CREATE, and the consequence is intended: because the dir always
        exists after create, the ``optional: true`` RO bind ``canon_hb_box`` ALWAYS
        mounts — a user who has emptied all three template subtrees gets an EMPTY
        read-only mount where the bind used to be omitted.
        ``install_workset_template`` guarantee-creates its chapter the same way."""
        dest = self._box(tmp_path)
        roots = self._roots(tmp_path, [("sys", None), ("agent", None)])
        install_box_handbook_template(dest, roots)
        assert dest.is_dir()
        assert list(dest.rglob("*")) == []

    def test_existing_dest_file_is_never_clobbered(self, tmp_path):
        """CREATE-IF-ABSENT, the failsafe against the shipped re-seed data-loss bug:
        a chapter file the user has edited survives a re-create into a leftover box
        store, even when EVERY layer ships that same file."""
        dest = self._box(tmp_path)
        dest.mkdir(parents=True)
        (dest / "shared.md").write_text("USER OWNED")
        roots = self._roots(tmp_path, [
            ("sys", {"shared.md": "sys", "new.md": "sys"}),
            ("agent", {"shared.md": "agent"}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "shared.md").read_text() == "USER OWNED"
        # ...and the copy is still ADDITIVE around it.
        assert (dest / "new.md").read_text() == "sys"

    def test_the_copier_applies_no_dest_whitelist_of_its_own(self, tmp_path):
        """⚑ THE ANTI-RESTORATION PIN.  There is ONE dest policy on this path —
        ``start._host_copy_dest``'s warn-and-skip at the caller — and the copier
        deliberately adds no ``scope="box"`` :data:`SCOPE_WHITELISTS` check of its
        own.  A dest inside the box store but OUTSIDE ``canon/handbook`` is COPIED,
        not refused: the whitelist could only ever fire on the dest (which is
        key-fixed, and already checked with the opposite severity), never on layer
        CONTENT, because ``stage_layers`` builds its relative paths by ``rglob``
        under the staged tree.  Two spellings of one condition (CONVENTIONS §0).
        If this test starts failing, a whitelist has been "restored" — read the
        function docstring before changing it back."""
        box_root = tmp_path / "boxes" / "mybox"
        (box_root / "home").mkdir(parents=True)
        roots = self._roots(tmp_path, [("sys", {"a.md": "sys"})])
        repointed = box_root / "canon2" / "handbook"  # NOT ``canon/handbook``.
        install_box_handbook_template(repointed, roots)
        assert (repointed / "a.md").read_text() == "sys"

    def test_layer_content_cannot_escape_the_dest_subtree(self, tmp_path):
        """And the reason no content whitelist is owed: ``stage_layers`` relativises
        every entry UNDER each layer root, so a layer that ships a top-level name the
        box whitelist would deny (``box.yaml``, ``registry.yaml``) still lands
        INSIDE the dest — it cannot reach a sibling entry of the box store."""
        dest = self._box(tmp_path)
        box_root = dest.parent.parent
        roots = self._roots(tmp_path, [
            ("sys", {"box.yaml": "x", "registry.yaml": "y"}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "box.yaml").read_text() == "x"
        assert not (box_root / "box.yaml").exists()
        assert not (box_root / "registry.yaml").exists()


class TestBoxHandbookHostCopyThroughTheSeam:
    """The ``_seed_box_home`` step-3 seam: KEYS in, resolved paths out."""

    def _populate(self, std):
        install_packaged_templates(std, ["claude"])
        for root, marker in (
            (std.template, "sys"),
            (std.agents / "claude" / "template", "agent"),
            (std.primary_workset / "template", "workset"),
        ):
            d = root / "box" / "canon" / "handbook"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{marker}-only.md").write_text(marker)
            (d / "shared.md").write_text(marker)

    def test_the_copy_fills_box_canon_handbook_from_all_three_layers(
        self, std, config, primary_proj,
    ):
        self._populate(std)
        _install_handbook(std, primary_proj)
        hb = _handbook_dir(primary_proj)
        assert (hb / "sys-only.md").read_text() == "sys"
        assert (hb / "agent-only.md").read_text() == "agent"
        assert (hb / "workset-only.md").read_text() == "workset"
        assert (hb / "shared.md").read_text() == "workset"
        # The packaged box chapter rides the system layer.
        assert (hb / "directives" / "SYS_BOX.md").is_file()

    def test_it_lands_on_the_host_store_and_never_in_the_box_home(
        self, std, config, primary_proj,
    ):
        """It is a HOST template: nothing it writes may appear under the box home,
        which is the directory the guest actually sees at ``~``."""
        self._populate(std)
        _install_handbook(std, primary_proj)
        assert _handbook_dir(primary_proj).parent != primary_proj.shell_path
        assert not list(primary_proj.shell_path.rglob("sys-only.md"))

    def test_a_no_agent_box_gets_system_and_workset_only(
        self, std, config, primary_proj,
    ):
        """⚑ END-TO-END CONFIRMATION, NOT THE PIN.  ``agent-only.md`` is absent here
        for TWO independent reasons (the key list omits the agent layer, AND the
        no-agent snapshot never declares ``agent.<a>.template`` for it to resolve),
        so this negative does not discriminate on its own — it survives a mutation
        that puts the agent key back.  The DISCRIMINATING pin is
        ``TestHandbookLayerSourceKeys.test_no_agent_omits_the_agent_layer``."""
        self._populate(std)
        _install_handbook(std, primary_proj, agent="")
        hb = _handbook_dir(primary_proj)
        assert (hb / "sys-only.md").is_file()
        assert (hb / "workset-only.md").is_file()
        assert not (hb / "agent-only.md").exists()
        assert (hb / "shared.md").read_text() == "workset"

    def test_workset_template_repoint_reroutes_the_copy(
        self, std, config, primary_proj, tmp_path,
    ):
        """MUTATION PROOF that the sources are KEYS: setting ``workset.template`` in
        the workset settings file reroutes layer 3.  Nothing here reads a path this
        module chose."""
        self._populate(std)
        custom = tmp_path / "custom-tpl" / "box" / "canon" / "handbook"
        custom.mkdir(parents=True)
        (custom / "CUSTOM.md").write_text("custom")

        wsf = std.primary_workset / "workset.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("workset", {})["template"] = str(tmp_path / "custom-tpl")
        wsf.write_text(yaml.safe_dump(doc))

        _install_handbook(std, primary_proj)
        hb = _handbook_dir(primary_proj)
        assert (hb / "CUSTOM.md").read_text() == "custom"
        # The DEFAULT workset template dir is no longer read.
        assert not (hb / "workset-only.md").exists()

    def _set_box_canon(self, std, value):
        """Repoint ``box.canon`` in the workset settings file — the user's route."""
        wsf = std.primary_workset / "workset.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("box", {})["canon"] = str(value)
        wsf.write_text(yaml.safe_dump(doc))

    def test_an_escaping_box_canon_is_skipped_with_a_warning(
        self, std, config, primary_proj, caplog, tmp_path,
    ):
        """§2a enforcement point 2, and the ONE dest policy on this path: a
        ``box.canon`` outside the box store is refused by SKIPPING with a warning,
        not by raising — a mis-declared key must not cost the user the box.

        ⚑ Driven through the WHOLE ``_seed_box_home``, not step 3 alone, because
        "does not cost the user the box" is the claim: create must still complete and
        the box's other seeds must still be on disk afterwards."""
        self._populate(std)
        escape = tmp_path / "ESCAPED"
        self._set_box_canon(std, escape)
        with caplog.at_level(logging.WARNING):
            _seed_box(std, primary_proj)     # steps 1-3, as ``box create`` runs
        # Nothing was written outside the box store...
        assert not escape.exists()
        assert not (escape / "handbook").exists()
        # ...the skip was LOUD...
        assert any(
            "handbook template" in r.getMessage()
            and "outside the box store" in r.getMessage()
            for r in caplog.records
        )
        # ...and the create still succeeded: the box HOME seed landed regardless.
        assert (
            primary_proj.shell_path / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()

    def test_a_box_canon_repointed_inside_the_box_store_still_creates(
        self, std, config, primary_proj,
    ):
        """⚑ THE REGRESSION THIS FIX EXISTS TO PREVENT.  ``box.canon`` repointed to
        another entry INSIDE the box store is a legal, accepted configuration: the
        one dest policy (``_host_copy_dest``) checks CONTAINMENT only, so ``box
        create`` SUCCEEDS and the chapter lands at the repointed dest.

        A ``scope="box"`` whitelist on the copier would have RAISED here —
        ``canon2`` is not in ``SCOPE_WHITELISTS["box"]`` — killing a create the
        retired ``seeded`` route always allowed.  That is why there is only one
        check.

        TWO passes, and they claim different things: the first is the WHOLE
        ``_seed_box_home`` ("create succeeds and the chapter lands"), the second
        wipes the dest and drives step 3 alone (so what REFILLS it is unambiguously
        this route, not an artefact of an earlier step)."""
        from kanibako.commands.start import _install_box_handbook

        self._populate(std)
        box_root = primary_proj.shell_path.parent
        self._set_box_canon(std, box_root / "canon2")
        hb = box_root / "canon2" / "handbook"

        _seed_box(std, primary_proj)         # steps 1-3; must NOT raise
        assert (hb / "sys-only.md").read_text() == "sys"
        assert (hb / "workset-only.md").read_text() == "workset"
        # The create completed: the box home seed landed too.
        assert (
            primary_proj.shell_path / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()

        # Step 3 ALONE also accepts the repointed dest.
        snapshot = _seed_snapshot(std, primary_proj)
        shutil.rmtree(hb, ignore_errors=True)
        _install_box_handbook(
            proj=primary_proj, snapshot=snapshot, agent_id="claude",
            logger=logging.getLogger("test-handbook"),
        )
        assert (hb / "sys-only.md").read_text() == "sys"

    def test_box_create_still_delivers_the_whole_chapter_by_this_route_alone(
        self, std, config, tmp_home,
    ):
        """⚑⚑ THE H2 DELIVERY CHECK, and the point of the phase: with the ``seeded``
        handbook layers GONE, a box created the way ``box create`` creates one still
        has its full three-layer ``@box.canon/handbook`` — delivered by this route
        alone.

        Both halves are asserted against the SAME populated store, so neither is
        vacuous: step 2 alone leaves the dest ABSENT, and the whole
        ``_seed_box_home`` fills it with every layer's marker plus the packaged box
        chapter.  A one-sided check would pass if the copy silently stopped running
        (nothing would be there to compare) or if a twin route came back."""
        self._populate(std)
        one = tmp_home / "proj-seed-only"
        one.mkdir()
        two = tmp_home / "proj-real-create"
        two.mkdir()
        seed_only = resolve_project(std, config, str(one), initialize=True)
        created = resolve_project(std, config, str(two), initialize=True)

        _seed(std, seed_only)          # step 2 alone (the ``seeded`` route)
        _seed_box(std, created)        # steps 1-3, exactly as ``box create`` runs

        # The retired route delivers NOTHING to the chapter...
        assert _tree(_handbook_dir(seed_only)) == {}
        # ...and the real create delivers ALL THREE layers, last-wins per file.
        after = _tree(_handbook_dir(created))
        assert after.get("sys-only.md") == b"sys"
        assert after.get("agent-only.md") == b"agent"
        assert after.get("workset-only.md") == b"workset"
        assert after.get("shared.md") == b"workset"
        assert "directives/SYS_BOX.md" in after, sorted(after)


# ---------------------------------------------------------------------------
# Packaged curated-template install (Phase 9c) — the packaged->runtime copy.
# ---------------------------------------------------------------------------

class TestInstallPackagedTemplates:
    """The ENUMERATED install (P-S2): four (packaged subtree → host dest) pairs, each
    with its own owner and therefore its own copy rule."""

    def test_box_and_workset_moulds_staged(self, std):
        """``template/{box,workset}`` → ``@system.template/{box,workset}`` (STAGING)."""
        install_packaged_templates(std, ["claude", "goose", "codex"])
        assert (
            std.template / "box" / "home" / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()
        assert (
            std.template / "box" / "home" / "canon" / "workbook" / "devnotes.md"
        ).is_file()
        assert (
            std.template / "box" / "canon" / "handbook" / "directives" / "SYS_BOX.md"
        ).is_file()
        assert (
            std.template / "workset" / "canon" / "handbook" / "directives"
            / "SYS_WORKSET.md"
        ).is_file()

    def test_handbook_goes_straight_to_system_canon(self, std):
        """P-S2: the packaged handbook installs DIRECTLY to ``@system.canon/handbook``
        — never staged under ``@system.template``, which would leave a second,
        never-read copy (the duplicated-shared-data defect)."""
        install_packaged_templates(std, ["claude"])
        assert (std.canon / "handbook" / "SYS_CONTENTS.md").is_file()
        assert (
            std.canon / "handbook" / "general" / "directives" / "SYS_GENERAL.md"
        ).is_file()
        assert not (std.template / "handbook").exists()

    def test_system_handbook_ships_no_scope_chapter_stubs(self, std):
        """The D2 cut: the system store supplies SYS_CONTENTS.md + ``general`` ONLY.
        An absent scope chapter shows an empty root-owned mountpoint, not the
        system's copy — there is deliberately no system-supplied fallback."""
        install_packaged_templates(std, ["claude"])
        hb = std.canon / "handbook"
        assert not (hb / "agent").exists()
        assert not (hb / "workset").exists()
        assert not (hb / "box").exists()

    def test_agent_mould_dir_is_guarantee_created_empty(self, std):
        """J-5/D5: the agent MOULD ships EMPTY (a wheel cannot ship an empty dir), so
        the host dir is guarantee-created by the install action (D7)."""
        install_packaged_templates(std, ["claude"])
        mould = std.template / "agent"
        assert mould.is_dir()
        assert list(mould.rglob("*")) == []

    def test_agent_default_store_stamped_from_the_package(self, std):
        """``agents/default`` gets ``template/agent_default`` DIRECTLY from the
        package — no host staging, because one default agent means a staged copy
        would be read once and never again."""
        install_packaged_templates(std, ["claude"])
        assert (
            std.agents / "default" / "canon" / "handbook" / "directives"
            / "SYS_AGENT.md"
        ).is_file()

    def test_claude_store_landed(self, std):
        """The claude plugin's ``data/base`` payload is stamped into its STORE:
        the box-home template AND the plugin's own handbook chapter."""
        install_packaged_templates(std, ["claude"])
        store = std.agents / "claude"
        assert (store / "template" / "box" / "home" / ".claude.json").is_file()
        assert (
            store / "template" / "box" / "home" / ".claude" / "settings.json"
        ).is_file()
        assert (
            store / "canon" / "handbook" / "directives" / "SYS_AGENT.md"
        ).is_file()
        import json
        data = json.loads(
            (store / "template" / "box" / "home" / ".claude.json").read_text()
        )
        assert data.get("hasCompletedOnboarding") is True

    def test_goose_and_codex_stores_landed(self, std):
        install_packaged_templates(std, ["goose", "codex"])
        assert (
            std.agents / "goose" / "template" / "box" / "home" / ".config" / "goose"
            / "config.yaml"
        ).is_file()
        assert (
            std.agents / "codex" / "template" / "box" / "home" / ".codex"
            / "config.toml"
        ).is_file()

    def test_unknown_agent_gets_a_store_but_no_payload(self, std):
        """An agent with no packaged payload (e.g. no_agent) still gets its store
        skeleton (the mould stamp + D7 dirs) but no content."""
        install_packaged_templates(std, ["no_agent"])
        store = std.agents / "no_agent"
        assert (store / "template" / "box" / "home").is_dir()
        assert not (store / "canon" / "handbook" / "directives").exists()

    def test_create_if_absent_does_not_clobber(self, std):
        """A user-edited template file survives a re-install (create-if-absent)."""
        install_packaged_templates(std, ["claude"])
        mine = std.template / "box" / "home" / "MINE.md"
        mine.write_text("MY EDITS")
        install_packaged_templates(std, ["claude"])
        assert mine.read_text() == "MY EDITS"

    def test_kanibako_md_not_installed_to_host(self, std):
        """The box guide is delivered live (RO bundle + launch-flatten), NOT
        flat-copied to a host runtime path by the template install (the retired
        ``@system.instructions`` vestige)."""
        install_packaged_templates(std, ["claude"])
        assert not (std.data / "global" / "KANIBAKO.md").exists()


class TestEnsureAgentStores:
    """J-6's A-action: ONE stamp implementation behind two triggers."""

    def test_idempotent_and_self_healing(self, std):
        from kanibako.launch.templates import ensure_agent_stores

        install_packaged_templates(std, ["claude"])
        chapter = (
            std.agents / "claude" / "canon" / "handbook" / "directives"
            / "SYS_AGENT.md"
        )
        chapter.unlink()
        ensure_agent_stores(std, ["claude"])
        assert chapter.is_file(), "a partial store must complete at the next trigger"

    def test_user_edit_survives_a_restamp(self, std):
        from kanibako.launch.templates import ensure_agent_stores

        install_packaged_templates(std, ["claude"])
        chapter = (
            std.agents / "claude" / "canon" / "handbook" / "directives"
            / "SYS_AGENT.md"
        )
        chapter.write_text("MY CHAPTER")
        ensure_agent_stores(std, ["claude"])
        assert chapter.read_text() == "MY CHAPTER"

    def test_host_mould_reaches_every_store(self, std):
        """The mould is read AS IT STANDS at action time, so a user's customisation
        reaches FUTURE stores (and this one, on its next self-heal)."""
        from kanibako.launch.templates import ensure_agent_stores

        install_packaged_templates(std, ["claude"])
        mould_file = std.template / "agent" / "common" / "MOULD.md"
        mould_file.parent.mkdir(parents=True, exist_ok=True)
        mould_file.write_text("from the mould")
        ensure_agent_stores(std, ["claude"])
        assert (
            std.agents / "claude" / "common" / "MOULD.md"
        ).read_text() == "from the mould"

    def test_mould_content_outside_the_whitelist_is_refused(self, std):
        """Deny-by-default at AGENT scope: a mould that would plant
        ``agent.yaml`` (= ``meta.agent.<a>.settings``) is REFUSED."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import ensure_agent_stores

        install_packaged_templates(std, ["claude"])
        (std.template / "agent" / "agent.yaml").write_text("agent: {}\n")
        with pytest.raises(TemplateScopeError) as exc:
            ensure_agent_stores(std, ["claude"])
        assert "AGENT" in str(exc.value)
        assert "agent.yaml" in str(exc.value)


class TestInstallWorksetTemplate:
    def test_stamps_the_workset_mould(self, std, tmp_path):
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        ws = tmp_path / "ws"
        ws.mkdir()
        install_workset_template(std, ws)
        assert (
            ws / "canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        ).is_file()
        assert (ws / "template" / "box" / "home").is_dir()

    def test_refuses_a_registry_planted_by_the_mould(self, std, tmp_path):
        """⚑ The severity case: ``registry.yaml`` is ``workset.registry``, the
        AUTHORITATIVE box membership — a templated one could ORPHAN boxes. And a
        STANDALONE ``<workset_path>`` is a directory the USER already had, which
        nothing here is entitled to clean up after a refusal."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        (std.template / "workset" / "registry.yaml").write_text("boxes: {}\n")
        ws = tmp_path / "ws"
        ws.mkdir()
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, ws)
        assert "WORKSET" in str(exc.value)
        assert not (ws / "registry.yaml").exists()


class TestWorksetStampSplit:
    """The stamp is TWO halves, and a STANDALONE root gets only the CANON one.

    ⚑ Both halves are spec-backed and the reasons differ. ``workset.canon`` is
    *"UNIFORM IN EVERY MODE — deliberately NOT a per-mode key"* (spec ``:962``), so a
    lone box has that tier. ``workset.template`` is <None> for standalone (spec
    ``:936``) — a workset template seeds FUTURE boxes and a standalone root will never
    have one — so stamping ``template/`` there would be structure for a key the mode
    does not have.
    """

    def _fresh_root(self, tmp_home, name):
        root = tmp_home / name
        root.mkdir()
        return root

    def test_named_workset_create_stamps_both_halves(self, std, config_file, tmp_home):
        """⚑ Driven through ``workset create``, the PRODUCT path — not the stamp in
        isolation, which is the shape ~200 existing fixtures build and the product
        never produces."""
        from kanibako.commands.workset_cmd import run_create

        install_packaged_templates(std, ["claude"])
        ws_root = tmp_home / "named-ws"
        rc = run_create(argparse.Namespace(
            path=str(ws_root), name=None, standalone=False, image=None, no_vault=False,
        ))
        assert rc == 0
        assert (ws_root / "canon" / "handbook" / "directives" / "SYS_WORKSET.md").is_file()
        assert (ws_root / "template" / "box" / "home" / "canon" / "notebook").is_dir()
        assert (ws_root / "template" / "box" / "canon" / "handbook").is_dir()

    def test_standalone_init_stamps_canon_and_never_template(self, std, config, tmp_home):
        """First-time standalone init gets the canon tier and NOT the template half."""
        install_packaged_templates(std, ["claude"])
        root = self._fresh_root(tmp_home, "solo")
        resolve_standalone_project(std, config, str(root), initialize=True)
        assert (root / "canon" / "handbook" / "directives" / "SYS_WORKSET.md").is_file()
        # ⚑ THE POINT OF THE SPLIT.
        assert not (root / "template").exists()
        # A second resolve is the recovery pass; it must not grow the template half.
        resolve_standalone_project(std, config, str(root), initialize=True)
        assert not (root / "template").exists()

    def test_canon_only_stamp_is_idempotent_and_clobbers_nothing(self, std, tmp_home):
        """Create-if-absent, so a re-run adds only what is missing — and the
        destination is the user's own tree, so a clobber here is DATA LOSS."""
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        root = self._fresh_root(tmp_home, "solo-idem")
        install_workset_template(std, root, canon_only=True)
        stamped = root / "canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        stamped.write_text("MINE\n")
        theirs = root / "canon" / "handbook" / "notes.md"
        theirs.write_text("keep me\n")
        install_workset_template(std, root, canon_only=True)
        assert stamped.read_text() == "MINE\n"
        assert theirs.read_text() == "keep me\n"
        assert not (root / "template").exists()

    def test_canon_only_still_refuses_an_out_of_scope_entry(self, std, tmp_home):
        """⚑ Narrowing the COPY to ``canon/`` must not narrow the WHITELIST'S FRAME:
        ``dest_root`` stays the store root, so ``canon/notebook/…`` is still judged as
        the store-relative path it is and DENIED (only ``canon/handbook`` is allowed)."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        bad = std.template / "workset" / "canon" / "notebook" / "NOTES.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("nope\n")
        root = self._fresh_root(tmp_home, "solo-deny")
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, root, canon_only=True)
        assert "WORKSET" in str(exc.value)
        assert not (root / "canon" / "notebook").exists()

    def test_canon_only_preflight_refuses_before_the_first_byte(self, std, tmp_home):
        """⚑ The ordering the standalone create depends on: the pre-flight writes
        NOTHING, so a doomed stamp leaves no litter in a directory kanibako may never
        delete."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import check_workset_template

        install_packaged_templates(std, ["claude"])
        bad = std.template / "workset" / "canon" / "notebook" / "NOTES.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("nope\n")
        root = self._fresh_root(tmp_home, "solo-preflight")
        with pytest.raises(TemplateScopeError):
            check_workset_template(std, root, canon_only=True)
        assert not (root / "canon").exists()

    def test_standalone_create_refuses_whole_and_leaves_nothing(self, std, config, tmp_home):
        """⚑⚑ The SEAM property, and the reason the stamp is the create's FIRST write:
        a refusal aborts before ``box_data/`` exists, so the create guard is still true
        and a corrected re-run does the whole create. There is no unwind — and there
        must not be one: the root is the user's own directory."""
        from kanibako.errors import TemplateScopeError

        install_packaged_templates(std, ["claude"])
        bad = std.template / "workset" / "canon" / "notebook" / "NOTES.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("nope\n")
        root = self._fresh_root(tmp_home, "solo-abort")
        with pytest.raises(TemplateScopeError):
            resolve_standalone_project(std, config, str(root), initialize=True)
        assert not (root / "box_data").exists()
        assert not (root / "canon").exists()
        assert root.is_dir()


class TestWorksetStampFollowsTheKeys:
    """The stamp's two leaves are ``workset.canon`` and ``workset.template``, and BOTH
    are declared repointable (spec ``:962`` / ``:936``) — so the stamp must land where
    the KEY says, not where the literal used to.

    ⚑ THE REACHABLE CASE IS THE STANDALONE ONE, and only that one. ``workset create``
    refuses a root that already exists and writes no ``workset.yaml`` of its own, so a
    repoint can never be on disk when the named stamp runs (``create_workset``'s own
    comment). The STANDALONE stamp's destination is a directory the user ALREADY HAD,
    which may already carry a ``workset.yaml`` — that is where a repoint is real.
    """

    def _root_with_repoint(self, tmp_home, name, **repoints):
        root = tmp_home / name
        root.mkdir()
        if repoints:
            (root / "workset.yaml").write_text(
                yaml.safe_dump({"workset": dict(repoints)})
            )
        return root

    def test_standalone_init_honours_a_workset_canon_repoint(
        self, std, config, tmp_home
    ):
        """⚑⚑ THE ORACLE, on the PRODUCT path: a standalone create into a root whose
        own ``workset.yaml`` repoints ``workset.canon``. The chapter and the stamped
        file must land under the repointed root and NOWHERE else — a stamp at the
        literal ``canon/`` is a tier the box's own key resolution will never read."""
        install_packaged_templates(std, ["claude"])
        root = self._root_with_repoint(tmp_home, "solo-repoint", canon="my_canon")
        resolve_standalone_project(std, config, str(root), initialize=True)
        assert (
            root / "my_canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        ).is_file()
        assert (root / "my_canon" / "handbook").is_dir()
        assert not (root / "canon").exists()

    def test_canon_only_stamp_honours_a_workset_canon_repoint(self, std, tmp_home):
        """The same thing at the seam, so a failure names the stamp and not the create."""
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        root = self._root_with_repoint(tmp_home, "solo-seam", canon="elsewhere/canon")
        install_workset_template(std, root, canon_only=True)
        assert (
            root / "elsewhere" / "canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        ).is_file()
        assert not (root / "canon").exists()

    def test_the_preflight_narrows_to_the_SAME_repointed_dest(self, std, tmp_home):
        """⚑ Pre-flight and stamp share ``_workset_stamp_copy`` precisely so they
        cannot judge different destinations. A pre-flight that still looked at the
        literal ``canon/`` would clear a copy it never examined."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import check_workset_template

        install_packaged_templates(std, ["claude"])
        bad = std.template / "workset" / "canon" / "notebook" / "NOTES.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("nope\n")
        root = self._root_with_repoint(tmp_home, "solo-pre", canon="my_canon")
        with pytest.raises(TemplateScopeError):
            check_workset_template(std, root, canon_only=True)
        assert not (root / "my_canon").exists()

    def test_a_repointed_canon_is_still_whitelisted_to_handbook_only(
        self, std, tmp_home
    ):
        """⚑ A repoint MOVES the canon tier; it does not widen it. ``notebook/`` under
        the repointed root is denied exactly as it is under the literal one."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        bad = std.template / "workset" / "canon" / "notebook" / "NOTES.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("nope\n")
        root = self._root_with_repoint(tmp_home, "solo-deny-repoint", canon="my_canon")
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, root, canon_only=True)
        assert "WORKSET" in str(exc.value)
        assert not (root / "my_canon" / "notebook").exists()

    def test_template_skeleton_honours_a_workset_template_repoint(
        self, std, tmp_path
    ):
        """⚑ NOT REACHABLE THROUGH TODAY'S PRODUCT CALLERS — it pins the resolver.
        ``workset create`` cannot present a root that already carries a
        ``workset.yaml``, so this drives the seam directly. It is here because the
        skeleton's leaf IS ``workset.template`` and a future caller with an existing
        root would otherwise stamp structure into a dir the key does not name."""
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        ws = tmp_path / "ws-template-repoint"
        ws.mkdir()
        (ws / "workset.yaml").write_text(
            yaml.safe_dump({"workset": {"template": "moulds"}})
        )
        install_workset_template(std, ws)
        assert (ws / "moulds" / "box" / "home" / "canon" / "notebook").is_dir()
        assert (ws / "moulds" / "box" / "home" / "canon" / "workbook").is_dir()
        assert (ws / "moulds" / "box" / "canon" / "handbook").is_dir()
        assert not (ws / "template").exists()

    def test_the_respelling_degenerates_to_the_declared_table(self, tmp_path):
        """⚑ ONE CARRIER FOR THE DEFAULT SPELLING. An unrepointed root must produce
        ``SCOPE_WHITELISTS["workset"]`` EXACTLY — if the respelling ever drifted from
        the declared table, every unrepointed stamp would be judged against a set the
        table does not contain, and the drift would be invisible in a green suite."""
        from kanibako.launch.templates import (
            SCOPE_WHITELISTS, _workset_scope_allowed,
        )

        root = tmp_path / "ws"
        assert _workset_scope_allowed(
            root, root / "canon", root / "template"
        ) == SCOPE_WHITELISTS["workset"]

    def test_a_leaf_outside_the_root_keeps_the_declared_entry(self, tmp_path):
        """A repoint that leaves the store root has NO store-relative path to
        whitelist, so the declared entry stands — the only spelling that cannot widen
        the scope. ⚑ THIS ARM IS NOW A FAILSAFE, NOT THE LIVE ANSWER: the stamp refuses
        an out-of-root leaf in ``_workset_stamp_dirs`` before this function is reached
        (see ``test_an_out_of_root_canon_repoint_names_the_key_file_and_value``), so
        what is pinned here is that the respelling still says nothing dangerous if it
        ever is."""
        from kanibako.launch.templates import (
            SCOPE_WHITELISTS, _workset_scope_allowed,
        )

        root = tmp_path / "ws"
        assert _workset_scope_allowed(
            root, tmp_path / "elsewhere", root / "template"
        ) == SCOPE_WHITELISTS["workset"]

    def test_a_dotdot_leaf_does_not_widen_the_allow_list(self, tmp_path):
        """⚑⚑ RESPELLS, NEVER WIDENS — and ``relative_to`` alone does not enforce it.
        ``<root>/../up`` IS lexically relative to the root, so the respelling used to
        emit the allow-list ENTRY ``../up/handbook``: a standing permission to write
        outside the store, produced by the function whose whole contract is that it
        cannot. Containment has to be resolved, not string-matched."""
        from kanibako.launch.templates import (
            SCOPE_WHITELISTS, _workset_scope_allowed,
        )

        root = tmp_path / "ws"
        root.mkdir()
        allowed = _workset_scope_allowed(
            root, root / ".." / "up", root / ".." / "moulds",
        )
        assert allowed == SCOPE_WHITELISTS["workset"]
        assert not any(".." in entry for entry in allowed)

    def test_no_repoint_lands_exactly_where_it_always_did(self, std, tmp_path):
        """The unrepointed default is the literal leaf, unchanged — the resolvers
        degenerate to ``<root>/<leaf>`` when there is no ``workset.yaml`` to read."""
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        ws = tmp_path / "ws-plain"
        ws.mkdir()
        install_workset_template(std, ws)
        assert (
            ws / "canon" / "handbook" / "directives" / "SYS_WORKSET.md"
        ).is_file()
        assert (ws / "template" / "box" / "home" / "canon" / "notebook").is_dir()
        assert (ws / "template" / "box" / "canon" / "handbook").is_dir()


class TestTheRespellingCannotWiden:
    """⚑⚑ ENFORCED, NOT DOCUMENTED. ``copy_tree``/``_check_whitelist`` used to take a
    finished ``allowed`` tuple, so *a repoint MOVES an entry, it never ADDS one* was
    held by a docstring and by there being exactly one well-behaved caller. The
    parameter now takes the PATHS the respelling is derived from
    (``WorksetStampScope``), so every entry the copier can ever see is
    ``SCOPE_WHITELISTS``' own — a widened workset scope is unrepresentable."""

    def test_no_route_accepts_an_allow_list(self, tmp_path):
        """THE MUTATION THE OLD SIGNATURE PERMITTED, constructed and refused: there is
        no parameter left through which entries can arrive."""
        import inspect

        from kanibako.launch.templates import copy_tree

        assert "allowed" not in inspect.signature(copy_tree).parameters
        with pytest.raises(TypeError):
            copy_tree(
                tmp_path / "src", tmp_path / "dest",
                scope="workset", allowed=("anything", "at", "all"),
            )

    def test_the_scope_object_carries_paths_and_nothing_else(self):
        """⚑ Its FIELDS are the three roots. Were an entries field ever added, the
        respelling would stop being derived and this class would become a second
        carrier of the declared table."""
        import dataclasses

        from kanibako.launch.templates import WorksetStampScope

        assert [f.name for f in dataclasses.fields(WorksetStampScope)] == [
            "workset_path", "canon_root", "template_root",
        ]
        assert WorksetStampScope.name == "workset"

    def test_every_respelling_keeps_the_declared_cardinality_and_shape(self, tmp_path):
        """⚑ CARDINALITY AND SHAPE, pinned across every repoint shape a root can have:
        the declared number of entries, ``canon/`` seedable at ``handbook`` and nowhere
        else, and never an absolute or ``..`` entry."""
        from kanibako.launch.templates import SCOPE_WHITELISTS, WorksetStampScope

        root = tmp_path / "ws"
        root.mkdir()
        declared = SCOPE_WHITELISTS["workset"]
        cases = [
            (root / "canon", root / "template"),          # unrepointed
            (root / "my_canon", root / "moulds"),         # both moved, in-root
            (root / "deep" / "canon", root / "template"),  # nested
            (tmp_path / "far", root / "template"),        # canon out of root
            (root / ".." / "up", root / ".." / "away"),   # lexical escape
        ]
        for canon_root, template_root in cases:
            allowed = WorksetStampScope(root, canon_root, template_root).allowed()
            assert len(allowed) == len(declared), (canon_root, allowed)
            assert allowed[1].split("/")[-1] == "handbook", allowed
            assert not any(
                entry.startswith("/") or ".." in entry.split("/") for entry in allowed
            ), allowed

    def test_an_unrepointed_root_still_yields_the_declared_table(self, tmp_path):
        """The degeneracy property of ``test_the_respelling_degenerates_to_the_declared_table``,
        restated through the type that is now the only route to it."""
        from kanibako.launch.templates import SCOPE_WHITELISTS, WorksetStampScope

        root = tmp_path / "ws"
        scope = WorksetStampScope(root, root / "canon", root / "template")
        assert scope.allowed() == SCOPE_WHITELISTS["workset"]


class TestWorksetStampRefusesAnEscapingLeaf:
    """Both stamp leaves are REPOINTABLE, so both can name a directory outside the
    workset root — and the stamp writes only inside the root it is stamping.

    ⚑⚑ TWO SEPARATE GUARDS, and the tests below keep them separable. The LEAF check
    (``_assert_stamp_leaf_in_root``) judges the resolved ``workset.{canon,template}``
    and refuses NAMING THE KEY; the two guarantee-create ``mkdir``\\ s are additionally
    ``_assert_contained``-checked, which is what catches a SYMLINKED INTERMEDIATE under
    a leaf that is itself perfectly in-root.

    ⚑ These ``mkdir``\\ s run AFTER ``copy_tree`` and reach none of its guards, so
    before this they were refused only INCIDENTALLY — by a copy that happened to share
    the destination. Take the mould's content away and the escape was silent.
    """

    def _root(self, tmp_path, name, **repoints):
        root = tmp_path / name
        root.mkdir()
        if repoints:
            (root / "workset.yaml").write_text(
                yaml.safe_dump({"workset": dict(repoints)})
            )
        return root

    def test_an_out_of_root_canon_repoint_names_the_key_file_and_value(
        self, std, tmp_path
    ):
        """⚑⚑ THE MESSAGE IS THE POINT. A refusal the user cannot act on is the defect:
        the old text said only that some path was ``OUTSIDE the destination subtree``
        and named neither ``workset.canon``, nor the file it was read from, nor the
        value to change. The bar is ``workset_dirkeys``' unresolvable-repoint refusal —
        key, file, offending token, reason, remedy."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        outside = tmp_path / "elsewhere" / "canon"
        root = self._root(tmp_path, "solo-out", canon=str(outside))
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, root, canon_only=True)
        text = str(exc.value)
        assert "workset.canon" in text                       # the KEY
        assert str(root / "workset.yaml") in text             # the FILE
        assert str(outside) in text                           # the OFFENDING TOKEN
        assert "OUTSIDE the workset root" in text             # the REASON
        assert "Repoint workset.canon" in text                # the REMEDY
        assert not outside.exists()

    def test_the_preflight_refuses_with_the_same_named_message(self, std, tmp_path):
        """⚑ BEFORE THE FIRST BYTE. ``check_workset_template`` exists so a destination
        kanibako may not clean up is never half-written; the leaf check runs inside the
        shared ``_workset_stamp_dirs``, so pre-flight and stamp cannot disagree."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import check_workset_template

        install_packaged_templates(std, ["claude"])
        outside = tmp_path / "elsewhere-pre" / "canon"
        root = self._root(tmp_path, "solo-out-pre", canon=str(outside))
        with pytest.raises(TemplateScopeError) as exc:
            check_workset_template(std, root, canon_only=True)
        assert "workset.canon" in str(exc.value)
        assert str(outside) in str(exc.value)
        assert not outside.exists()

    def test_an_out_of_root_template_repoint_plants_no_skeleton_outside(
        self, std, tmp_path
    ):
        """MEASURED BEFORE THE FIX: ``workset.template: ../escaped`` created all three
        skeleton dirs (seven directories in all) outside the workset root, and nothing
        refused — the skeleton loop never reached a guard."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        root = self._root(tmp_path, "ws-escape", template="../escaped")
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, root)
        assert "workset.template" in str(exc.value)
        assert not (tmp_path / "escaped").exists()

    def test_a_standalone_canon_escape_is_refused_with_an_EMPTY_mould(
        self, std, tmp_path
    ):
        """⚑⚑ THE REACHABLE ONE, and the reason the incidental refusal was not enough.
        With the mould's ``canon/`` half absent ``copy_tree`` returns on its first line,
        so its ``_assert_contained`` never runs — and the chapter ``mkdir`` then created
        the directory outside the root, silently."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        shutil.rmtree(std.template / "workset" / "canon")
        outside = tmp_path / "OUTSIDE"
        root = self._root(tmp_path, "solo-empty-mould", canon=str(outside))
        with pytest.raises(TemplateScopeError):
            install_workset_template(std, root, canon_only=True)
        assert not outside.exists()

    def test_a_symlinked_skeleton_intermediate_writes_nothing_outside(
        self, std, tmp_path
    ):
        """⚑ WHAT THE LEAF CHECK CANNOT SEE. ``workset.template`` is unrepointed and
        squarely in-root, but ``template/box`` is a symlink out — and the skeleton
        descends four levels below the leaf, so ``mkdir(parents=True)`` would build the
        tree THROUGH the link. Only ``_assert_contained`` on the real target sees it."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        outside = tmp_path / "linked-away"
        outside.mkdir()
        root = self._root(tmp_path, "ws-symlink-skel")
        (root / "template").mkdir()
        (root / "template" / "box").symlink_to(outside, target_is_directory=True)
        with pytest.raises(TemplateScopeError):
            install_workset_template(std, root)
        assert list(outside.iterdir()) == [], sorted(outside.rglob("*"))

    def test_a_symlinked_chapter_leaf_writes_nothing_outside(self, std, tmp_path):
        """⚑ The chapter's own twin of the case above, on the arm where the copy is
        silent: the mould's ``canon/`` half is gone, ``workset.canon`` is the plain
        in-root default, and only ``canon/handbook`` is the link out."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        shutil.rmtree(std.template / "workset" / "canon")
        outside = tmp_path / "chapter-away"
        outside.mkdir()
        root = self._root(tmp_path, "ws-symlink-chapter")
        (root / "canon").mkdir()
        (root / "canon" / "handbook").symlink_to(outside, target_is_directory=True)
        with pytest.raises(TemplateScopeError):
            install_workset_template(std, root, canon_only=True)
        assert list(outside.iterdir()) == [], sorted(outside.rglob("*"))

    def test_a_symlinked_default_leaf_says_it_took_the_default(self, std, tmp_path):
        """⚑ NO REPOINT TO QUOTE. When the DEFAULT leaf is itself a link out there is no
        settings value to name, and a message reading ``workset.canon is set to None``
        would be a worse answer than the one it replaced."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        outside = tmp_path / "default-away"
        outside.mkdir()
        root = self._root(tmp_path, "ws-symlink-default")
        (root / "canon").symlink_to(outside, target_is_directory=True)
        with pytest.raises(TemplateScopeError) as exc:
            install_workset_template(std, root, canon_only=True)
        assert "takes its default 'canon' leaf" in str(exc.value)
        assert "None" not in str(exc.value)

    def test_canon_only_ignores_an_out_of_root_template_repoint(self, std, tmp_path):
        """⚑ THE CHECK IS PER-LEAF, and standalone's ``workset.template`` is ``<None>``
        (spec ``:936``): that path consults neither the key nor the skeleton, so a value
        it never uses must not refuse a root that is otherwise fine."""
        from kanibako.launch.templates import install_workset_template

        install_packaged_templates(std, ["claude"])
        root = self._root(tmp_path, "solo-template-noise", template="../nowhere")
        install_workset_template(std, root, canon_only=True)
        assert (root / "canon" / "handbook").is_dir()
        assert not (tmp_path / "nowhere").exists()


class TestCopierEnforcement:
    """The four §2a enforcement points, on the ONE shared copier."""

    def test_a_symlinked_FINAL_target_is_refused(self, tmp_path):
        """⚑⚑ THE SECOND MUST-FIX. The parent check cannot see this: the escape is
        the LEAF. With ``overwrite`` a live symlink is followed and a file OUTSIDE
        the subtree is replaced wholesale."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        outside = tmp_path / "outside.txt"
        outside.write_text("PRECIOUS")
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "a.txt").symlink_to(outside)
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest, overwrite=True)
        assert outside.read_text() == "PRECIOUS"

    def test_a_DANGLING_symlink_target_is_refused(self, tmp_path):
        """The create-if-absent arm's version of the same hole: a dangling symlink
        reads as ABSENT to ``exists()``, so ``copy2`` writes THROUGH it."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        outside = tmp_path / "not-yet.txt"
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "a.txt").symlink_to(outside)
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest)
        assert not outside.exists()

    def test_a_refused_copy_creates_no_directories(self, tmp_path):
        """The containment check runs BEFORE the ``mkdir``: refusing a copy must not
        litter directories outside the destination subtree on the way to saying no."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        outside = tmp_path / "outside"
        outside.mkdir()
        src = tmp_path / "src"
        (src / "sub" / "deep").mkdir(parents=True)
        (src / "sub" / "deep" / "a.txt").write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "sub").symlink_to(outside, target_is_directory=True)
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest)
        assert list(outside.iterdir()) == [], sorted(outside.rglob("*"))

    def test_source_symlink_is_refused(self, tmp_path):
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        secret = tmp_path / "id_ed25519"
        secret.write_text("PRIVATE KEY")
        src = tmp_path / "src"
        src.mkdir()
        (src / "innocent.txt").symlink_to(secret)
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest)
        assert not (dest / "innocent.txt").exists()

    def test_stage_layers_refuses_a_source_symlink(self, tmp_path):
        """The staging pass reads layer content FIRST, so it needs its own check —
        by the time the shared copier saw it, the exfiltrated bytes would already be
        staged as a plain file."""
        from kanibako.errors import TemplateScopeError

        secret = tmp_path / "secret.txt"
        secret.write_text("PRIVATE KEY")
        layer = tmp_path / "layer"
        layer.mkdir()
        (layer / "innocent.txt").symlink_to(secret)
        home = tmp_path / "home"
        home.mkdir()
        with pytest.raises(TemplateScopeError):
            stage_layers(home, [layer])
        assert not (home / "innocent.txt").exists()

    def test_dest_symlink_cannot_escape_the_subtree(self, tmp_path):
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        outside = tmp_path / "outside"
        outside.mkdir()
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "sub" / "a.txt").write_text("payload")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "sub").symlink_to(outside, target_is_directory=True)
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest)
        assert not (outside / "a.txt").exists()

    def test_box_whitelist_denies_a_planted_settings_file(self, tmp_path):
        """``box.yaml`` at a BOX store root is ``meta.box.settings``, the LAST
        cascade level — template content would become the box's top-priority
        settings, carrying any key it liked."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        src = tmp_path / "src"
        src.mkdir()
        (src / "box.yaml").write_text("box: {image: evil}\n")
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(TemplateScopeError) as exc:
            copy_tree(src, dest, scope="box")
        assert "BOX" in str(exc.value)
        assert not (dest / "box.yaml").exists()

    def test_box_whitelist_allows_the_two_declared_entries(self, tmp_path):
        from kanibako.launch.templates import copy_tree

        src = tmp_path / "src"
        (src / "home").mkdir(parents=True)
        (src / "home" / "x.txt").write_text("x")
        (src / "canon" / "handbook").mkdir(parents=True)
        (src / "canon" / "handbook" / "y.md").write_text("y")
        dest = tmp_path / "dest"
        dest.mkdir()
        copy_tree(src, dest, scope="box")
        assert (dest / "home" / "x.txt").is_file()
        assert (dest / "canon" / "handbook" / "y.md").is_file()

    def test_canon_is_not_seedable_wholesale(self, tmp_path):
        """ONLY ``canon/handbook`` is seedable, never ``canon/`` wholesale."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        src = tmp_path / "src"
        (src / "canon" / "bible").mkdir(parents=True)
        (src / "canon" / "bible" / "z.md").write_text("z")
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(TemplateScopeError):
            copy_tree(src, dest, scope="box")


# ---------------------------------------------------------------------------
# Template-update setup-gate arc: content-manifest digest, TRUE-REFRESH copy,
# and the refresh plan partition.
# ---------------------------------------------------------------------------

class TestPackagedTemplatesDigest:
    """``packaged_templates_digest`` — deterministic content hash over the
    packaged base + RO canon (carrying the box guide) + each agent template tree."""

    def _fake_trees(self, monkeypatch, tmp_path, *, base="B", guide="G", claude="C"):
        base_dir = tmp_path / "pbase"
        base_dir.mkdir()
        (base_dir / "INSTRUCTIONS.md").write_text(base)
        # The RO packaged CANON carries the box guide at
        # canon/bible/general/directives/ROM_GENERAL.md — the SOLE manifest
        # source of the guide now (C-CANON R1).
        bundle_dir = tmp_path / "pbundle"
        (bundle_dir / _GUIDE_REL).parent.mkdir(parents=True)
        (bundle_dir / _GUIDE_REL).write_text(guide)
        claude_dir = tmp_path / "pclaude"
        claude_dir.mkdir()
        (claude_dir / ".claude.json").write_text(claude)
        monkeypatch.setattr(
            "kanibako.launch.templates._packaged_base_template", lambda: base_dir
        )
        monkeypatch.setattr(
            "kanibako.launch.templates._packaged_shared_bundle", lambda: bundle_dir
        )
        monkeypatch.setattr(
            "kanibako.launch.templates._packaged_agent_store",
            lambda name: claude_dir if name == "claude" else None,
        )
        return base_dir, bundle_dir, claude_dir

    def test_deterministic_and_order_independent(self, monkeypatch, tmp_path):
        self._fake_trees(monkeypatch, tmp_path)
        d1 = packaged_templates_digest(["claude", "no_agent"])
        d2 = packaged_templates_digest(["no_agent", "claude"])
        assert d1 == d2
        assert len(d1) == 64  # sha256 hex

    def test_changes_when_base_file_changes(self, monkeypatch, tmp_path):
        base_dir, _, _ = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (base_dir / "INSTRUCTIONS.md").write_text("CHANGED")
        assert packaged_templates_digest(["claude"]) != before

    def test_changes_when_guide_changes(self, monkeypatch, tmp_path):
        _, bundle_dir, _ = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (bundle_dir / _GUIDE_REL).write_text("NEW GUIDE")
        assert packaged_templates_digest(["claude"]) != before

    def test_kanibako_md_hashed_exactly_once(self, monkeypatch, tmp_path):
        """Regression: the guide is enumerated ONCE (only via the RO bundle).

        The retired ``@system.instructions`` flat-copy used to add a SECOND
        ``instructions/KANIBAKO.md`` manifest entry beside the bundle's own —
        double-hashing the same bytes so a relocation flipped the digest and
        spuriously tripped the setup gate.
        """
        self._fake_trees(monkeypatch, tmp_path)
        keys = [k for k, _ in _packaged_manifest_entries(["claude"])]
        guide = [k for k in keys if k.endswith("ROM_GENERAL.md")]
        assert guide == [f"shared/{_GUIDE_REL}"], keys

    def test_changes_when_agent_template_changes(self, monkeypatch, tmp_path):
        _, _, claude_dir = self._fake_trees(monkeypatch, tmp_path)
        before = packaged_templates_digest(["claude"])
        (claude_dir / ".claude.json").write_text("NEW")
        assert packaged_templates_digest(["claude"]) != before

    def test_agent_membership_changes_digest(self, monkeypatch, tmp_path):
        self._fake_trees(monkeypatch, tmp_path)
        # ``claude`` contributes a packaged tree; ``no_agent`` contributes none.
        assert packaged_templates_digest(["claude"]) != packaged_templates_digest(
            ["no_agent"]
        )

    def test_real_packaged_digest_stable(self):
        """Over the REAL packaged data the digest is stable across calls."""
        from kanibako.targets import discover_targets

        names = sorted(discover_targets())
        assert packaged_templates_digest(names) == packaged_templates_digest(names)
        assert len(packaged_templates_digest(names)) == 64


class TestInstallPackagedTemplatesRefresh:
    """``install_packaged_templates(..., refresh=True)`` = TRUE REFRESH, and J-3
    item 1's boundary: it reaches the system-owned packaged STAGING **only**."""

    def test_refresh_overwrites_changed_staged_file(self, std):
        install_packaged_templates(std, ["claude"])
        rel = ("box", "home", "canon", "notebook", "MY_CONTENTS.md")
        shipped = std.template.joinpath(*rel)
        shipped.write_text("STALE USER EDIT")
        install_packaged_templates(std, ["claude"], refresh=True)
        packaged = _packaged_base_template().joinpath(*rel).read_text()
        assert shipped.read_text() == packaged
        assert shipped.read_text() != "STALE USER EDIT"

    def test_refresh_adds_missing_shipped_file(self, std):
        """A never-installed host: refresh ADDS every shipped file."""
        install_packaged_templates(std, ["claude"], refresh=True)
        assert (
            std.template / "box" / "home" / "canon" / "notebook" / "MY_CONTENTS.md"
        ).is_file()
        assert (
            std.agents / "claude" / "template" / "box" / "home" / ".claude.json"
        ).is_file()

    def test_refresh_leaves_user_only_file(self, std):
        install_packaged_templates(std, ["claude"])
        user_file = std.template / "box" / "home" / "MY_NOTES.md"
        user_file.write_text("user only")
        install_packaged_templates(std, ["claude"], refresh=True)
        assert user_file.read_text() == "user only"

    def test_refresh_never_overwrites_the_system_handbook(self, std):
        """⚑ J-3 item 1: user-owned canon stores are NEVER overwritten by any
        implicit path. ``kanibako setup`` may refresh the STAGING; it may not revert
        the user's own handbook."""
        install_packaged_templates(std, ["claude"])
        mine = std.canon / "handbook" / "SYS_CONTENTS.md"
        mine.write_text("MY HANDBOOK")
        install_packaged_templates(std, ["claude"], refresh=True)
        assert mine.read_text() == "MY HANDBOOK"

    def test_refresh_never_overwrites_an_agent_store(self, std):
        install_packaged_templates(std, ["claude"])
        stub = std.agents / "claude" / "template" / "box" / "home" / ".claude.json"
        stub.write_text("{}")
        install_packaged_templates(std, ["claude"], refresh=True)
        assert stub.read_text() == "{}"


class TestCreateIfAbsentRegression:
    """The create-if-absent default (box-seed path) is UNCHANGED by the refresh
    variant — it must still skip existing files."""

    def test_install_default_still_create_if_absent(self, std):
        install_packaged_templates(std, ["claude"])
        mine = std.template / "box" / "home" / "MINE.md"
        mine.write_text("MY EDITS")
        install_packaged_templates(std, ["claude"])  # refresh defaults False
        assert mine.read_text() == "MY EDITS"

    def test_public_alias_still_skips_existing(self, tmp_path):
        """``copy_resource_tree_if_absent`` (the box-seed apply reuses it) must
        still leave an existing dest file untouched (the data-loss guard)."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("shipped")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "a.txt").write_text("USER OWNED")
        copy_resource_tree_if_absent(src, dest)
        assert (dest / "a.txt").read_text() == "USER OWNED"


class TestPlanTemplateRefresh:
    """``plan_template_refresh`` → (added, overwritten, kept); files that are
    byte-equal OR EQUIVALENT, and user-only files, never appear."""

    _STAGED = ("box", "home", "canon", "notebook", "MY_CONTENTS.md")

    def test_all_added_on_empty_host(self, std):
        added, overwritten, kept = plan_template_refresh(std, ["claude"])
        assert overwritten == []
        assert kept == []
        assert any(p.name == "MY_CONTENTS.md" for p in added)
        assert any(p.name == "SYS_CONTENTS.md" for p in added)

    def test_unchanged_after_install_is_empty(self, std):
        install_packaged_templates(std, ["claude"])
        assert plan_template_refresh(std, ["claude"]) == ([], [], [])

    def test_changed_staged_file_is_overwritten_partition(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template.joinpath(*self._STAGED)
        target.write_text("# changed\n\nreal content change\n")
        added, overwritten, kept = plan_template_refresh(std, ["claude"])
        assert target in overwritten
        assert added == []

    def test_changed_user_owned_file_is_KEPT_not_overwritten(self, std):
        """A user-owned store's difference is REPORTED, never scheduled for a
        rewrite — that is what makes ``kept`` a distinct list."""
        install_packaged_templates(std, ["claude"])
        target = std.canon / "handbook" / "SYS_CONTENTS.md"
        target.write_text("# mine\n\nentirely my own words\n")
        added, overwritten, kept = plan_template_refresh(std, ["claude"])
        assert target in kept
        assert target not in overwritten

    def test_missing_file_is_added_partition(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template.joinpath(*self._STAGED)
        target.unlink()
        added, _overwritten, _kept = plan_template_refresh(std, ["claude"])
        assert target in added

    def test_user_only_file_absent_from_plan(self, std):
        install_packaged_templates(std, ["claude"])
        (std.template / "box" / "home" / "USER.md").write_text("mine")
        added, overwritten, kept = plan_template_refresh(std, ["claude"])
        assert all(p.name != "USER.md" for p in added + overwritten + kept)


class TestRefreshEquivalenceTiers:
    """J-3 item 2: three tiers, REPORTING ONLY — byte-equal and EQUIVALENT are both
    "current" and go unreported; only a real difference is named."""

    def test_markdown_comment_and_whitespace_change_is_equivalent(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template / "box" / "home" / "canon" / "workbook" / "devnotes.md"
        text = target.read_text()
        # A comment edit, ONE trailing space (insignificant — two would be a
        # markdown HARD BREAK, which the normaliser deliberately preserves), CRLF
        # line endings, and extra blank lines: all noise, none of it content.
        target.write_text(
            text.replace("<!--", "<!--[STOCK]", 1)
            .replace("\n", " \r\n")
            .rstrip()
            + "\n\n\n"
        )
        added, overwritten, kept = plan_template_refresh(std, ["claude"])
        assert overwritten == [] and kept == [] and added == []

    def test_markdown_body_change_is_different(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template / "box" / "home" / "canon" / "workbook" / "devnotes.md"
        target.write_text(target.read_text() + "\nA REAL NEW LINE\n")
        _added, overwritten, _kept = plan_template_refresh(std, ["claude"])
        assert target in overwritten

    def test_yaml_reordering_is_equivalent(self, tmp_path):
        from kanibako.launch.templates import _equivalent

        src = tmp_path / "a.yaml"
        dst = tmp_path / "b.yaml"
        src.write_text("a: 1\nb: 2\n")
        dst.write_text("b: 2\na: 1\n")
        assert _equivalent(src, dst)

    def test_unparseable_yaml_is_different(self, tmp_path):
        from kanibako.launch.templates import _equivalent

        src = tmp_path / "a.yaml"
        dst = tmp_path / "b.yaml"
        src.write_text("a: 1\n")
        dst.write_text("a: [1, 2\n")
        assert not _equivalent(src, dst)

    def test_fenced_code_whitespace_is_significant(self, tmp_path):
        """CONSERVATIVE normalisation: inside a fence, whitespace is CONTENT."""
        from kanibako.launch.templates import _equivalent

        src = tmp_path / "a.md"
        dst = tmp_path / "b.md"
        src.write_text("t\n\n```\n  indented\n```\n")
        dst.write_text("t\n\n```\nindented\n```\n")
        assert not _equivalent(src, dst)

    def test_trailing_hard_break_is_preserved(self, tmp_path):
        from kanibako.launch.templates import _normalise_markdown

        assert _normalise_markdown("one  \ntwo\n") == "one  \ntwo"


class TestStagingIsScoped:
    """S-2/J-2: the box + workset whitelists are LIVE at the staging copy — the
    earliest point a planted file can be REFUSED rather than carried forward.

    ⚑ Nothing downstream re-checks it: the box seed copies from ``box/home`` and
    ``box/canon/handbook`` DIRECTLY, so an unscoped staging copy would make J-2's
    deny-by-default ruling dead prose.
    """

    def _fake_packaged(self, monkeypatch, tmp_path, scope_dir, plant):
        from kanibako.launch import templates as _t

        root = tmp_path / "packaged"
        real = _t._packaged_base_template()
        shutil.copytree(real, root)
        (root / scope_dir / plant).parent.mkdir(parents=True, exist_ok=True)
        (root / scope_dir / plant).write_text("box: {image: evil}\n")
        monkeypatch.setattr(_t, "_packaged_base_template", lambda: root)
        return root

    def test_a_planted_box_settings_yaml_is_REFUSED_by_the_real_install(
        self, std, monkeypatch, tmp_path,
    ):
        from kanibako.errors import TemplateScopeError

        self._fake_packaged(monkeypatch, tmp_path, "box", "box.yaml")
        with pytest.raises(TemplateScopeError) as exc:
            install_packaged_templates(std, ["claude"])
        assert "BOX" in str(exc.value)
        assert not (std.template / "box" / "box.yaml").exists()

    def test_a_planted_workset_registry_is_REFUSED_by_the_real_install(
        self, std, monkeypatch, tmp_path,
    ):
        from kanibako.errors import TemplateScopeError

        self._fake_packaged(monkeypatch, tmp_path, "workset", "registry.yaml")
        with pytest.raises(TemplateScopeError) as exc:
            install_packaged_templates(std, ["claude"])
        assert "WORKSET" in str(exc.value)


class TestRefreshHonoursTheClassifier:
    """S-6: the PREVIEW and the ACTION must tell ONE truth.

    ``plan_template_refresh`` calls an EQUIVALENT staging file "current" and does not
    report it. A refresh that rewrote its bytes anyway would silently revert the very
    edit the preview just said it would leave alone.
    """

    _STAGED = ("box", "home", "canon", "workbook", "devnotes.md")

    def test_refresh_leaves_an_EQUIVALENT_staged_file_alone(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template.joinpath(*self._STAGED)
        edited = (
            target.read_text().replace("<!--", "<!--[STOCK]", 1).replace("\n", " \r\n")
        )
        target.write_text(edited, newline="")
        before = target.read_bytes()
        # The preview agrees it is "current"...
        assert plan_template_refresh(std, ["claude"]) == ([], [], [])
        install_packaged_templates(std, ["claude"], refresh=True)
        # ...so the refresh must not have rewritten it.  BYTES, deliberately: text
        # mode normalises CRLF on read, which would make this pass either way.
        assert target.read_bytes() == before

    def test_refresh_still_replaces_a_genuinely_DIFFERENT_staged_file(self, std):
        install_packaged_templates(std, ["claude"])
        target = std.template.joinpath(*self._STAGED)
        target.write_text("# mine\n\nreal content change\n")
        _added, overwritten, _kept = plan_template_refresh(std, ["claude"])
        assert target in overwritten
        install_packaged_templates(std, ["claude"], refresh=True)
        assert "real content change" not in target.read_text()


class TestPackagingGlobs:
    """The wheel must carry HIDDEN entries under ``data/global``.

    ⚑ setuptools' ``**/*`` does NOT recurse into hidden directories. There are no
    dotfiles under ``data/global`` TODAY, so this is not yet a live bug — and that is
    precisely the trap: ``template/box/home/`` is a BOX HOME seed, so the first
    ``.claude/`` or ``.config/`` dropped there would ship EMPTY and seed nothing,
    with no build error and no launch warning. The patterns were added ahead of the
    first such file; this pins them so a tidy-up cannot quietly remove them.

    Asserted at the PATTERN level rather than by building a wheel: a real build takes
    ~20 s and needs network-isolated venv creation, so it belongs in the release
    check, not the per-file gate. (Verified once by build: a ``.probefile`` and a
    nested ``.probe/settings.json`` under ``template/box/home`` both land in the
    wheel with these patterns.)
    """

    def _patterns(self) -> list[str]:
        import tomllib

        from tests.support.repo import REPO_ROOT

        root = REPO_ROOT / "pyproject.toml"
        data = tomllib.loads(root.read_text())
        return data["tool"]["setuptools"]["package-data"]["kanibako.data"]

    def test_dot_dir_and_dot_file_patterns_present(self):
        pats = set(self._patterns())
        required = {
            "global/**/.*",        # a dotfile at any depth
            "global/.*/**/*",      # content under a top-level dot-dir
            "global/.*/*",
            "global/**/.*/**/*",   # content under a NESTED dot-dir (.claude/, .config/)
            "global/**/.*/*",
        }
        missing = required - pats
        assert not missing, (
            f"the hidden-entry globs for kanibako.data are missing {sorted(missing)} "
            "— a dotfile under data/global/template/box/home would ship EMPTY and "
            "the box seed would deliver nothing, silently"
        )

    def test_packaged_template_tree_is_the_declared_shape(self):
        """The four ENUMERATED subtrees, and nothing else, under the template root.

        ⚑ ``agent/`` is deliberately ABSENT: the agent MOULD ships EMPTY (D5) and a
        wheel cannot ship an empty directory, so its host dir is guarantee-created by
        the install action instead (D7). A packaged ``agent/`` appearing here would
        mean someone put content in the mould, which would then WIN over
        ``agent_default`` on every overlapping path (create-if-absent, mould first).
        """
        base = _packaged_base_template()
        assert base is not None
        assert {p.name for p in base.iterdir() if p.is_dir()} == {
            "box", "workset", "agent_default", "handbook",
        }
