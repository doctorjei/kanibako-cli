"""Tests for kanibako.templates."""

from __future__ import annotations

import pytest

from kanibako.paths import (
    WorksetSpec,
    resolve_project,
    resolve_standalone_project,
    resolve_workset_project,
)
from kanibako.templates import (
    agent_template_dir,
    apply_template_layers,
    base_template_dir,
    workset_template_dir,
)
from kanibako.workset import add_project, create_workset


class TestApplyTemplateLayers:
    def _layer(self, root, name, files):
        d = root / name
        d.mkdir(parents=True)
        for rel, content in files.items():
            p = d / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        return d

    def test_ordered_last_wins(self, tmp_path):
        """base -> agent -> workset applied in order; later layer wins per-file."""
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

        apply_template_layers(home, [base, agent, workset])

        # Each layer's unique files all land.
        assert (home / "base-only.txt").read_text() == "base"
        assert (home / "agent-only.txt").read_text() == "agent"
        assert (home / "workset-only.txt").read_text() == "workset"
        # Last layer to set a file wins (workset > agent > base).
        assert (home / "shared.txt").read_text() == "workset version"
        # agent overlays base where workset is silent.
        assert (home / "two.txt").read_text() == "agent two"

    def test_seed_once_does_not_overwrite_existing_user_file(self, tmp_path):
        """A pre-existing (user-edited) file in home is NOT clobbered by a re-seed.

        Re-running the apply must not overwrite a file the user changed after the
        first seed (seed-once, D-B6).  The caller gates on ``proj.is_new``; this
        asserts the apply itself never touches a file no layer ships.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "user-edited.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {"base-only.txt": "base"})

        apply_template_layers(home, [base])

        assert (home / "user-edited.txt").read_text() == "user changes"
        assert (home / "base-only.txt").read_text() == "base"

    def test_existing_home_file_not_clobbered_by_same_path_layer(self, tmp_path):
        """A pre-existing home file is preserved even when a layer ships the same path.

        This is the re-seed DATA-LOSS guard: a marker-less migrated box has a
        home full of user-edited files; a layer with a file at the same relative
        path must NOT overwrite the user's content.
        """
        home = tmp_path / "home"
        home.mkdir()
        (home / "shared.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "shared.txt": "base version",
            "base-only.txt": "base",
        })

        apply_template_layers(home, [base])

        # Pre-existing file preserved (NOT clobbered by the layer's same-path file).
        assert (home / "shared.txt").read_text() == "user changes"
        # New file from the layer still lands.
        assert (home / "base-only.txt").read_text() == "base"

    def test_cross_layer_last_wins_when_home_absent(self, tmp_path):
        """Two layers both shipping foo.txt -> later layer wins (home didn't have it)."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {"foo.txt": "base version"})
        agent = self._layer(tmp_path, "agent", {"foo.txt": "agent version"})

        apply_template_layers(home, [base, agent])

        # Later layer overrides the earlier layer's file written THIS pass.
        assert (home / "foo.txt").read_text() == "agent version"

    def test_new_layer_files_land_in_nonempty_home(self, tmp_path):
        """Files unique to a layer are copied into an existing non-empty home."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "user-edited.txt").write_text("user changes")
        base = self._layer(tmp_path, "base", {
            "base-only.txt": "base",
            "nested/deep.txt": "deep",
        })

        apply_template_layers(home, [base])

        # Pre-existing user file untouched.
        assert (home / "user-edited.txt").read_text() == "user changes"
        # New unique files (including nested) land.
        assert (home / "base-only.txt").read_text() == "base"
        assert (home / "nested" / "deep.txt").read_text() == "deep"

    def test_existing_nested_file_not_clobbered(self, tmp_path):
        """A pre-existing file in a nested home subdir is preserved against a layer."""
        home = tmp_path / "home"
        nested = home / ".claude"
        nested.mkdir(parents=True)
        (nested / "settings.json").write_text("user settings")
        agent = self._layer(tmp_path, "agent", {".claude/settings.json": "shipped"})

        apply_template_layers(home, [agent])

        assert (home / ".claude" / "settings.json").read_text() == "user settings"

    def test_none_and_missing_layers_skipped(self, tmp_path):
        """A None layer or a layer dir that doesn't exist contributes nothing."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {"base-only.txt": "base"})
        missing = tmp_path / "does-not-exist"

        apply_template_layers(home, [base, None, missing])

        assert (home / "base-only.txt").read_text() == "base"
        assert sorted(p.name for p in home.iterdir()) == ["base-only.txt"]

    def test_standalone_omits_workset_layer(self, tmp_path):
        """STANDALONE boxes seed base + agent only (workset layer is None)."""
        home = tmp_path / "home"
        home.mkdir()
        base = self._layer(tmp_path, "base", {"shared.txt": "base version"})
        agent = self._layer(tmp_path, "agent", {"shared.txt": "agent version"})

        # Standalone: workset_template_dir() -> None.
        apply_template_layers(home, [base, agent, None])

        assert (home / "shared.txt").read_text() == "agent version"

    def test_nested_directories(self, tmp_path):
        """Layers with nested directory structure are copied correctly."""
        home = tmp_path / "home"
        home.mkdir()
        agent = tmp_path / "agent"
        nested = agent / ".claude"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("# Instructions")

        apply_template_layers(home, [agent])

        assert (home / ".claude" / "CLAUDE.md").read_text() == "# Instructions"

    def test_no_layers_is_noop(self, tmp_path):
        """No layers -> home is untouched."""
        home = tmp_path / "home"
        home.mkdir()
        (home / "existing.txt").write_text("untouched")

        apply_template_layers(home, [])

        assert (home / "existing.txt").read_text() == "untouched"
        assert sorted(p.name for p in home.iterdir()) == ["existing.txt"]


from kanibako.templates import mark_seeded, needs_seed, seed_marker_path  # noqa: E402


class TestSeedOnceGate:
    """The seed-once sentinel gates seeding on first-start, not ``is_new`` (BUG-D).

    A box made via the two-step ``box create`` then ``start`` flow has its
    metadata dir created at CREATE time, so ``is_new`` is False at START — yet it
    must still be seeded exactly once.  The ``.seeded`` sentinel under the
    metadata dir, not ``is_new``, decides this.
    """

    def test_fresh_box_needs_seed(self, tmp_path):
        """is_new True -> always needs seeding (the start-in-fresh-dir path)."""
        meta = tmp_path / "meta"
        meta.mkdir()
        assert needs_seed(meta, is_new=True) is True

    def test_created_then_started_box_needs_seed(self, tmp_path):
        """A box created earlier (is_new False) but never seeded still needs it.

        This is the BUG-D case: ``box create`` made the metadata dir, so the
        later ``start`` sees is_new False — but with no ``.seeded`` marker the box
        has NO curated content yet and must be seeded.
        """
        meta = tmp_path / "meta"
        meta.mkdir()
        assert seed_marker_path(meta).exists() is False
        assert needs_seed(meta, is_new=False) is True

    def test_already_seeded_box_does_not_reseed(self, tmp_path):
        """Once the sentinel is written, a later start never re-seeds (D-B6)."""
        meta = tmp_path / "meta"
        meta.mkdir()
        mark_seeded(meta)
        assert seed_marker_path(meta).exists() is True
        assert needs_seed(meta, is_new=False) is False

    def test_mark_seeded_creates_metadata_dir(self, tmp_path):
        """mark_seeded is robust when the metadata dir does not yet exist."""
        meta = tmp_path / "meta"  # not created
        mark_seeded(meta)
        assert seed_marker_path(meta).exists() is True

    def test_create_then_start_seeds_once_end_to_end(self, tmp_path):
        """Simulate create -> start -> start: seed exactly once.

        Models the gate `start` applies: on the first start the box (created
        earlier, is_new False) is seeded and marked; a second start with a
        user-edited file must NOT clobber it.
        """
        meta = tmp_path / "meta"
        meta.mkdir()
        home = tmp_path / "home"
        home.mkdir()
        base = tmp_path / "base"
        base.mkdir()
        (base / "INSTRUCTIONS.md").write_text("curated")

        # First start (post-create, is_new False).
        assert needs_seed(meta, is_new=False) is True
        apply_template_layers(home, [base])
        mark_seeded(meta)
        assert (home / "INSTRUCTIONS.md").read_text() == "curated"

        # User edits the seeded file inside the box.
        (home / "INSTRUCTIONS.md").write_text("user edits")

        # Second start: must not re-seed.
        assert needs_seed(meta, is_new=False) is False
        assert (home / "INSTRUCTIONS.md").read_text() == "user edits"


# ---------------------------------------------------------------------------
# Layered-template path resolution (Phase 7a) — pure derivations, no apply.
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


class TestBaseTemplateDir:
    def test_is_flat_base_template_root(self, std):
        """Layer 1 reads @system.base_template FLAT (no general/ subdir)."""
        assert base_template_dir(std) == std.base_template


class TestAgentTemplateDir:
    def test_derived_under_agents_store(self, std):
        """Layer 2 = @system.agents/<agent>/template."""
        assert agent_template_dir(std, "claude") == std.agents / "claude" / "template"

    def test_agent_name_varies(self, std):
        assert agent_template_dir(std, "goose") == std.agents / "goose" / "template"


class TestWorksetTemplateDir:
    def test_primary_roots_at_primary_workset(self, primary_proj, std):
        assert (
            workset_template_dir(primary_proj, std)
            == std.primary_workset / "template"
        )

    def test_named_roots_at_workset_root(self, named_proj, std):
        assert (
            workset_template_dir(named_proj, std)
            == named_proj.group.root / "template"
        )

    def test_standalone_is_none(self, standalone_proj, std):
        assert workset_template_dir(standalone_proj, std) is None


# ---------------------------------------------------------------------------
# Packaged curated-template install + fresh-box seeding (Phase 9c).
# ---------------------------------------------------------------------------

from kanibako.templates import install_packaged_templates  # noqa: E402


class TestInstallPackagedTemplates:
    def test_base_instructions_landed(self, std):
        """The packaged base INSTRUCTIONS.md is copied to @system.base_template."""
        install_packaged_templates(std, ["claude", "goose", "codex"])
        assert (std.base_template / "INSTRUCTIONS.md").is_file()

    def test_claude_template_landed(self, std):
        """The claude agent template (.claude.json stub + settings) is copied."""
        install_packaged_templates(std, ["claude"])
        dest = agent_template_dir(std, "claude")
        assert (dest / ".claude.json").is_file()
        assert (dest / ".claude" / "settings.json").is_file()
        # The onboarding stub marks onboarding complete.
        import json
        data = json.loads((dest / ".claude.json").read_text())
        assert data.get("hasCompletedOnboarding") is True

    def test_goose_and_codex_templates_landed(self, std):
        install_packaged_templates(std, ["goose", "codex"])
        assert (
            agent_template_dir(std, "goose") / ".config" / "goose" / "config.yaml"
        ).is_file()
        assert (
            agent_template_dir(std, "codex") / ".codex" / "config.toml"
        ).is_file()

    def test_unknown_agent_is_skipped(self, std):
        """An agent with no packaged template (e.g. no_agent) is a no-op."""
        install_packaged_templates(std, ["no_agent"])
        assert not (agent_template_dir(std, "no_agent")).exists()

    def test_create_if_absent_does_not_clobber(self, std):
        """A user-edited template file survives a re-install (create-if-absent)."""
        install_packaged_templates(std, ["claude"])
        instr = std.base_template / "INSTRUCTIONS.md"
        instr.write_text("MY EDITS")
        install_packaged_templates(std, ["claude"])
        assert instr.read_text() == "MY EDITS"

    def test_fresh_box_seeds_base_and_agent(self, std, tmp_path):
        """End-to-end: install packaged templates, then the layered seed-once
        apply lands the base INSTRUCTIONS.md + the agent files into a box home."""
        install_packaged_templates(std, ["claude"])
        home = tmp_path / "box-home"
        layers = [
            base_template_dir(std),
            agent_template_dir(std, "claude"),
            None,  # standalone: no workset layer
        ]
        apply_template_layers(home, layers)
        # Base layer seeded.
        assert (home / "INSTRUCTIONS.md").is_file()
        # Agent layer seeded.
        assert (home / ".claude.json").is_file()
        assert (home / ".claude" / "settings.json").is_file()
