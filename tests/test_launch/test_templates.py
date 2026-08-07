"""Tests for kanibako.launch.templates (the layered home-seed / template trio)."""

from __future__ import annotations

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
# The layered ``seeded.template`` DEFAULT-category table (spec §2a; Q1-Q4).
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
    """``template_seed_defaults`` declares the SIX §2a layers as ordinary keystore
    ``seeded`` keys (+ their ``@``-ref SOURCE keys), gated per mode / agent."""

    def test_system_layer_always_present(self, primary_proj):
        defs = template_seed_defaults(primary_proj, "claude")
        # Layer 1 (base) rides the seed system with NO carve-out (Q4).
        assert defs["system.seeded.template"] == (
            "@system.template/box/home", "@meta.box.path/home",
        )

    def test_system_handbook_layer_targets_box_canon(self, primary_proj):
        """Layer 4's DEST is the KEY ``@box.canon/handbook`` — exactly the SOURCE of
        the ro ``canon_hb_box`` bind, so repointing ``box.canon`` moves both."""
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["system.seeded.handbook"] == (
            "@system.template/box/canon/handbook", "@box.canon/handbook",
        )

    def test_every_seed_dest_is_host_spelled(self, primary_proj):
        """No seed dest is guest-spelled. A ``~``-rooted handbook dest would sit
        INSIDE the rw home bind, so the copy would land in ``<box_dir>/home/...``
        and the ro mount would silently shadow it."""
        defs = template_seed_defaults(primary_proj, "claude")
        dests = [v[1] for k, v in defs.items() if ".seeded." in k]
        assert dests, defs
        assert all(d.startswith("@meta.box.path") or d.startswith("@box.canon")
                   for d in dests), dests

    def test_agent_layer_sources_harness_store(self, primary_proj):
        """Layer 2: agent.<node>.seeded.template reads @agent.<node>.template,
        which defaults to @config.agents/<harness>/template (Q2: node = persona+
        harness; the SOURCE dir is the harness store)."""
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["agent.claude.template"] == "@config.agents/claude/template"
        assert defs["agent.claude.seeded.template"] == (
            "@agent.claude.template/box/home", "@meta.box.path/home",
        )
        assert defs["agent.claude.seeded.handbook"] == (
            "@agent.claude.template/box/canon/handbook", "@box.canon/handbook",
        )

    def test_no_agent_omits_agent_layer(self, primary_proj):
        defs = template_seed_defaults(primary_proj, None)
        assert not any(k.startswith("agent.") for k in defs)
        # system + workset layers still declared.
        assert "system.seeded.template" in defs
        assert "workset.seeded.template" in defs

    def test_workset_layer_default_points_at_workset_template(self, primary_proj):
        """Layer 3 default = @meta.workset.path/template (Q3, was <None>)."""
        defs = template_seed_defaults(primary_proj, "claude")
        assert defs["workset.template"] == "@meta.workset.path/template"
        assert defs["workset.seeded.template"] == (
            "@workset.template/box/home", "@meta.box.path/home",
        )

    def test_named_includes_workset_layer(self, named_proj):
        defs = template_seed_defaults(named_proj, "claude")
        assert "workset.seeded.template" in defs
        assert "workset.seeded.handbook" in defs

    def test_standalone_omits_workset_layer(self, standalone_proj):
        """STANDALONE has no workset tier -> no workset.template source/layer
        (spec §2c workset.template <None>)."""
        defs = template_seed_defaults(standalone_proj, "claude")
        assert "workset.template" not in defs
        assert "workset.seeded.template" not in defs
        assert "workset.seeded.handbook" not in defs
        # base + agent layers still present.
        assert "system.seeded.template" in defs
        assert "agent.claude.seeded.template" in defs

    def test_seed_keys_of_selects_exactly_the_seeded_keys(self, primary_proj):
        """The HOST-space key set is DERIVED from the table, never restated — a
        seventh layer cannot be added in one place and forgotten in the other."""
        from kanibako.launch.templates import seed_keys_of

        defs = template_seed_defaults(primary_proj, "claude")
        assert seed_keys_of(defs) == {
            "system.seeded.template", "system.seeded.handbook",
            "agent.claude.seeded.template", "agent.claude.seeded.handbook",
            "workset.seeded.template", "workset.seeded.handbook",
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


def _seed(std, proj, *, agent="claude", deliver_creds=True):
    """Drive the one-time home seed (the unified keystore-routed route)."""
    from kanibako.commands.start import _apply_init_seeds

    _apply_init_seeds(
        std=std,
        proj=proj,
        agent_name=agent,
        target=_FakeTarget() if agent else None,
        global_config_path=std.settings,
        agent_config_path=std.agents / "claude" / "settings.yaml",
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

    def test_handbook_layer_lands_on_the_host_not_the_home(
        self, std, config, primary_proj,
    ):
        """⚑⚑ THE §0.1 REGRESSION. The handbook seed's dest is a HOST path under the
        box store; it must land at ``<box_dir>/canon/handbook`` and must NOT be
        translated back under the box home. On a host whose user home is
        ``/home/agent`` the two are textually indistinguishable, which is exactly how
        this failed silently before ``dest_space``."""
        install_packaged_templates(std, ["claude"])
        _seed(std, primary_proj)
        box_root = primary_proj.shell_path.parent
        landed = box_root / "canon" / "handbook" / "directives" / "SYS_BOX.md"
        assert landed.is_file(), sorted(box_root.rglob("*"))
        # ...and nowhere inside the box HOME.
        assert not list(primary_proj.shell_path.rglob("SYS_BOX.md"))

    def test_handbook_seed_dest_equals_the_bind_source(
        self, std, config, primary_proj,
    ):
        """"The seed writes precisely what the bind reads, spelled once": the seeded
        dir IS ``@box.canon/handbook``, the source of the ro ``canon_hb_box`` bind."""
        install_packaged_templates(std, ["claude"])
        _seed(std, primary_proj)
        box_canon_handbook = primary_proj.shell_path.parent / "canon" / "handbook"
        assert box_canon_handbook.is_dir()
        # And it is a SIBLING of home, never inside it (§0.3: @box.canon ≠ ~/canon).
        assert box_canon_handbook.parent != primary_proj.shell_path

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

        wsf = std.primary_workset / "settings.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("workset", {})["template"] = str(tmp_path / "custom-tpl")
        wsf.write_text(yaml.safe_dump(doc))

        _seed(std, primary_proj)
        home = primary_proj.shell_path
        assert (home / "CUSTOM.txt").read_text() == "custom"
        # The default workset dir is NOT used once the key is repointed.
        assert not (home / "DEFAULT.txt").exists()

    def test_box_canon_seed_refuses_an_escaping_dest(
        self, std, config, primary_proj, caplog,
    ):
        """§2a enforcement point 2: a settings-declared seed whose HOST dest escapes
        the box store is SKIPPED with a warning, not written."""
        install_packaged_templates(std, ["claude"])
        escape = std.data / "ESCAPED"
        wsf = std.primary_workset / "settings.yaml"
        doc = (yaml.safe_load(wsf.read_text()) if wsf.exists() else {}) or {}
        doc.setdefault("box", {})["canon"] = str(escape)
        wsf.write_text(yaml.safe_dump(doc))
        with caplog.at_level(logging.WARNING):
            _seed(std, primary_proj)
        assert not (escape / "handbook").exists()
        assert any("outside the box store" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# The BOX HANDBOOK HOST-TEMPLATE copy (phase H1) — Jei's 2026-08-07g ruling: the
# handbook templates are HOST templates, not GUEST templates, so they are copied
# beside the workset mould rather than delivered through the ``seeded`` category.
#
# ⚑ PHASE H1 IS ADDITIVE: the three ``<scope>.seeded.handbook`` layers are STILL
# declared and still applied by ``_apply_init_seeds``.  The tests below that drive
# the seam therefore WIPE the seeded route's output before invoking this one, so
# what they observe is this route and not its twin.  H2 removes the twin.
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


def _seed_and_isolate_handbook(std, proj, *, agent="claude"):
    """Run the real seed, WIPE the seeded route's handbook output, return the
    snapshot — so the caller's ``_install_box_handbook`` runs against a clean dest
    and nothing it observes can have come from the still-live ``seeded`` layers."""
    from kanibako.commands.start import _apply_init_seeds

    snapshot = _apply_init_seeds(
        std=std,
        proj=proj,
        agent_name=agent,
        target=_FakeTarget() if agent else None,
        global_config_path=std.settings,
        agent_config_path=std.agents / "claude" / "settings.yaml",
        logger=logging.getLogger("test-seed"),
    )
    shutil.rmtree(_handbook_dir(proj), ignore_errors=True)
    return snapshot


def _install_handbook(std, proj, *, agent="claude", logger=None):
    """Drive step 3 alone, off the snapshot the seed resolve built."""
    from kanibako.commands.start import _install_box_handbook

    snapshot = _seed_and_isolate_handbook(std, proj, agent=agent)
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
        agent_cfg_path=std.agents / "claude" / "settings.yaml",
        system_settings_path=std.settings,
        auth_src=SimpleNamespace(creds_shared=deliver_creds),
        logger=logging.getLogger("test-seed"),
    )


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

    def test_every_key_is_one_template_seed_defaults_declares(self, primary_proj):
        """THE DRIFT PIN.  ``system.template`` is floor-materialized (a ``system.*``
        settings-tier path) and so is not in the table; every OTHER key must be a
        SOURCE scalar the table declares — never a path this module invented."""
        defs = template_seed_defaults(primary_proj, "claude")
        keys = handbook_layer_source_keys(primary_proj, "claude")
        assert keys[0] == "system.template"
        assert "system.template" not in defs
        for key in keys[1:]:
            assert key in defs, key
            # A SOURCE scalar (an ``@``-ref string), NOT a seeded (src, dest) tuple.
            assert isinstance(defs[key], str)


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
        box whitelist would deny (``settings.yaml``, ``registry.yaml``) still lands
        INSIDE the dest — it cannot reach a sibling entry of the box store."""
        dest = self._box(tmp_path)
        box_root = dest.parent.parent
        roots = self._roots(tmp_path, [
            ("sys", {"settings.yaml": "x", "registry.yaml": "y"}),
        ])
        install_box_handbook_template(dest, roots)
        assert (dest / "settings.yaml").read_text() == "x"
        assert not (box_root / "settings.yaml").exists()
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

        wsf = std.primary_workset / "settings.yaml"
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
        wsf = std.primary_workset / "settings.yaml"
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
        ``seeded`` route has always allowed.  That is why there is only one check.

        TWO passes, because under H1 both routes are live: the first is the WHOLE
        ``_seed_box_home`` (the "create succeeds" claim), the second wipes the dest
        and drives step 3 alone (so what refills it can only be THIS route)."""
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
        snapshot = _seed_and_isolate_handbook(std, primary_proj)
        shutil.rmtree(hb, ignore_errors=True)
        _install_box_handbook(
            proj=primary_proj, snapshot=snapshot, agent_id="claude",
            logger=logging.getLogger("test-handbook"),
        )
        assert (hb / "sys-only.md").read_text() == "sys"

    def test_box_create_is_byte_identical_with_and_without_the_new_call(
        self, std, config, tmp_home,
    ):
        """⚑ THE H1 COMPOSITION CHECK.  Both routes are live this phase; they are
        create-if-absent over the same layers in the same order, so a box created
        WITH step 3 has a byte-for-byte identical ``@box.canon/handbook`` to one
        created without it."""
        self._populate(std)
        one = tmp_home / "proj-seed-only"
        one.mkdir()
        two = tmp_home / "proj-both-routes"
        two.mkdir()
        seed_only = resolve_project(std, config, str(one), initialize=True)
        both = resolve_project(std, config, str(two), initialize=True)

        _seed(std, seed_only)          # step 2 alone (the ``seeded`` route)
        _seed_box(std, both)           # steps 1-3, exactly as ``box create`` runs

        before = _tree(_handbook_dir(seed_only))
        after = _tree(_handbook_dir(both))
        assert before, "the seeded route delivered nothing — the check is vacuous"
        assert after == before


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
        ``settings.yaml`` (= ``meta.agent.<a>.settings``) is REFUSED."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import ensure_agent_stores

        install_packaged_templates(std, ["claude"])
        (std.template / "agent" / "settings.yaml").write_text("agent: {}\n")
        with pytest.raises(TemplateScopeError) as exc:
            ensure_agent_stores(std, ["claude"])
        assert "AGENT" in str(exc.value)
        assert "settings.yaml" in str(exc.value)


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
        AUTHORITATIVE box membership — a templated one could ORPHAN boxes. And for a
        STANDALONE project ``<workset_path>`` IS the user's own project dir."""
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


class TestLegacyPluginPayloadArm:
    """The D8 TRANSITION ARM — and the one way it can be present but INERT.

    A NEW base beside an OLD published plugin is the ordinary ``pip install -U
    kanibako-cli`` outcome, and without this arm that box comes up with NO agent
    config at all, silently. The arm only helps if it lands the payload where layer 2
    READS — so "the arm exists" is not the property worth testing; "the arm's landing
    path equals layer 2's resolved source" is.
    """

    def test_landing_path_equals_layer_2_source(self, primary_proj):
        """⚑⚑ THE MUST-FIX, pinned. Both sides DERIVED from the same constants, so
        they cannot drift: a landing path of ``box/home`` (dropping the ``template/``
        prefix) puts the payload at ``agents/<name>/box/home/**``, which nothing
        reads — the arm runs, reports nothing, and the box still has no agent config.
        """
        from kanibako.launch.templates import PLUGIN_LEGACY_PAYLOAD_DEST_REL

        defs = template_seed_defaults(primary_proj, "claude")
        # Layer 2's SOURCE, with its @-ref head resolved the way the cascade does:
        #   @agent.claude.template -> @config.agents/claude/<store rel>
        source = defs["agent.claude.seeded.template"][0]
        store_ref = defs["agent.claude.template"]
        assert source.startswith("@agent.claude.template/")
        resolved = source.replace("@agent.claude.template", store_ref, 1)
        store_relative = resolved.split("@config.agents/claude/", 1)[1]
        assert PLUGIN_LEGACY_PAYLOAD_DEST_REL == store_relative, (
            "the legacy payload lands where NOTHING reads it"
        )

    def test_a_legacy_plugin_still_seeds_its_config(self, std, config, primary_proj,
                                                    monkeypatch, tmp_path):
        """END TO END on the real seed path: an OLD-shaped plugin (``data/template``
        with box-home files at its root) still reaches the box home."""
        from kanibako.commands.start import _apply_init_seeds
        from kanibako.launch import templates as _t

        legacy = tmp_path / "plugin" / "data" / "template"
        (legacy / ".claude").mkdir(parents=True)
        (legacy / ".claude.json").write_text('{"legacy": true}')
        (legacy / ".claude" / "settings.json").write_text("{}")
        monkeypatch.setattr(
            _t, "_packaged_agent_store",
            lambda name: (legacy, True) if name == "claude" else None,
        )
        install_packaged_templates(std, ["claude"])
        # It landed at the layer-2 SOURCE, not at some unread sibling.
        assert (
            std.agents / "claude" / "template" / "box" / "home" / ".claude.json"
        ).is_file()
        assert not (std.agents / "claude" / "box").exists()

        class _T:
            name = "claude"

            def default_seeds(self):
                return {}

        _apply_init_seeds(
            std=std, proj=primary_proj, agent_name="claude", target=_T(),
            global_config_path=std.settings,
            agent_config_path=std.agents / "claude" / "settings.yaml",
            logger=logging.getLogger("t"), deliver_creds=True,
        )
        assert (primary_proj.shell_path / ".claude.json").read_text() == (
            '{"legacy": true}'
        )

    def test_the_legacy_arm_is_still_whitelisted(self, std, tmp_path, monkeypatch):
        """The arm is SCOPED, not exempt: the whitelist reads the STORE-relative path
        (``template/…``, allowed), so a legacy payload is checked like everything
        else — and content that would escape the store is still refused."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch import templates as _t

        legacy = tmp_path / "plugin" / "data" / "template"
        legacy.mkdir(parents=True)
        (legacy / "ok.txt").write_text("fine")
        monkeypatch.setattr(
            _t, "_packaged_agent_store",
            lambda name: (legacy, True) if name == "claude" else None,
        )
        install_packaged_templates(std, ["claude"])
        assert (
            std.agents / "claude" / "template" / "box" / "home" / "ok.txt"
        ).is_file()
        # ...and a symlink in that same legacy payload is still refused.
        (legacy / "escape.txt").symlink_to(tmp_path / "secret")
        (tmp_path / "secret").write_text("PRIVATE")
        with pytest.raises(TemplateScopeError):
            _t.ensure_agent_stores(std, ["claude"])


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
        """``settings.yaml`` at a BOX store root is ``meta.box.settings``, the LAST
        cascade level — template content would become the box's top-priority
        settings, carrying any key it liked."""
        from kanibako.errors import TemplateScopeError
        from kanibako.launch.templates import copy_tree

        src = tmp_path / "src"
        src.mkdir()
        (src / "settings.yaml").write_text("box: {image: evil}\n")
        dest = tmp_path / "dest"
        dest.mkdir()
        with pytest.raises(TemplateScopeError) as exc:
            copy_tree(src, dest, scope="box")
        assert "BOX" in str(exc.value)
        assert not (dest / "settings.yaml").exists()

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
            lambda name: (claude_dir, False) if name == "claude" else None,
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

        self._fake_packaged(monkeypatch, tmp_path, "box", "settings.yaml")
        with pytest.raises(TemplateScopeError) as exc:
            install_packaged_templates(std, ["claude"])
        assert "BOX" in str(exc.value)
        assert not (std.template / "box" / "settings.yaml").exists()

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
