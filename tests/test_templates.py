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
