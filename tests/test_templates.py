"""Tests for kanibako.templates."""

from __future__ import annotations

from kanibako.templates import apply_shell_template, resolve_template


class TestResolveTemplate:
    def test_agent_specific(self, tmp_path):
        """Agent-specific template takes priority over general."""
        (tmp_path / "claude" / "standard").mkdir(parents=True)
        (tmp_path / "general" / "standard").mkdir(parents=True)

        result = resolve_template(tmp_path, "claude")
        assert result == tmp_path / "claude" / "standard"

    def test_falls_back_to_general(self, tmp_path):
        """Falls back to general when agent-specific dir is absent."""
        (tmp_path / "general" / "standard").mkdir(parents=True)

        result = resolve_template(tmp_path, "claude")
        assert result == tmp_path / "general" / "standard"

    def test_empty_when_no_dirs(self, tmp_path):
        """Returns None when no template dirs exist."""
        result = resolve_template(tmp_path, "claude")
        assert result is None

    def test_custom_template_name(self, tmp_path):
        """Resolves a custom template variant."""
        (tmp_path / "claude" / "minimal").mkdir(parents=True)

        result = resolve_template(tmp_path, "claude", "minimal")
        assert result == tmp_path / "claude" / "minimal"

    def test_general_agent_name(self, tmp_path):
        """Resolves general/standard for agent_name='general'."""
        (tmp_path / "general" / "standard").mkdir(parents=True)

        result = resolve_template(tmp_path, "general")
        assert result == tmp_path / "general" / "standard"

    def test_empty_sentinel_returns_none(self, tmp_path):
        """Explicit 'empty' returns None even when a matching directory exists."""
        (tmp_path / "general" / "empty").mkdir(parents=True)
        (tmp_path / "claude" / "empty").mkdir(parents=True)

        result = resolve_template(tmp_path, "claude", "empty")
        assert result is None


class TestApplyShellTemplate:
    def test_copies_base_then_overlay(self, tmp_path):
        """Base files are applied first, then agent template overlays."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()

        # Set up general/base with a file
        base_dir = templates / "general" / "base"
        base_dir.mkdir(parents=True)
        (base_dir / "base-file.txt").write_text("from base")
        (base_dir / "shared.txt").write_text("base version")

        # Set up claude/standard with an overlay file
        agent_dir = templates / "claude" / "standard"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent-file.txt").write_text("from agent")
        (agent_dir / "shared.txt").write_text("agent version")

        apply_shell_template(shell, templates, "claude")

        assert (shell / "base-file.txt").read_text() == "from base"
        assert (shell / "agent-file.txt").read_text() == "from agent"
        # Overlay wins on conflict
        assert (shell / "shared.txt").read_text() == "agent version"

    def test_base_only_when_no_agent_template(self, tmp_path):
        """When only general/standard exists, base + general/standard are applied."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()

        base_dir = templates / "general" / "base"
        base_dir.mkdir(parents=True)
        (base_dir / "base-file.txt").write_text("from base")

        general_dir = templates / "general" / "standard"
        general_dir.mkdir(parents=True)
        (general_dir / "general-file.txt").write_text("from general")

        apply_shell_template(shell, templates, "claude")

        assert (shell / "base-file.txt").read_text() == "from base"
        assert (shell / "general-file.txt").read_text() == "from general"

    def test_empty_is_noop(self, tmp_path):
        """No template dirs at all — shell is unchanged."""
        templates = tmp_path / "templates"
        templates.mkdir()
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / "existing.txt").write_text("untouched")

        apply_shell_template(shell, templates, "claude")

        assert (shell / "existing.txt").read_text() == "untouched"
        # No new files created
        assert sorted(p.name for p in shell.iterdir()) == ["existing.txt"]

    def test_empty_sentinel_is_noop(self, tmp_path):
        """Explicit 'empty' template doesn't copy anything, even with dirs on disk."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / "existing.txt").write_text("untouched")

        # Create base and empty dirs that would match
        (templates / "general" / "base").mkdir(parents=True)
        (templates / "general" / "base" / "base-file.txt").write_text("from base")
        (templates / "general" / "empty").mkdir(parents=True)
        (templates / "general" / "empty" / "tmpl-file.txt").write_text("from empty")

        apply_shell_template(shell, templates, "claude", "empty")

        assert (shell / "existing.txt").read_text() == "untouched"
        assert sorted(p.name for p in shell.iterdir()) == ["existing.txt"]

    def test_nested_directories(self, tmp_path):
        """Template with nested directory structure is copied correctly."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()

        agent_dir = templates / "claude" / "standard"
        nested = agent_dir / ".claude"
        nested.mkdir(parents=True)
        (nested / "CLAUDE.md").write_text("# Instructions")

        apply_shell_template(shell, templates, "claude")

        assert (shell / ".claude" / "CLAUDE.md").read_text() == "# Instructions"

    def test_preserves_existing_shell_files(self, tmp_path):
        """Existing files in shell_path that don't conflict are preserved."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()
        (shell / ".bashrc").write_text("existing bashrc")

        agent_dir = templates / "claude" / "standard"
        agent_dir.mkdir(parents=True)
        (agent_dir / "new-file.txt").write_text("new content")

        apply_shell_template(shell, templates, "claude")

        assert (shell / ".bashrc").read_text() == "existing bashrc"
        assert (shell / "new-file.txt").read_text() == "new content"

    def test_no_base_dir(self, tmp_path):
        """Works when general/base doesn't exist (only agent template applied)."""
        templates = tmp_path / "templates"
        shell = tmp_path / "shell"
        shell.mkdir()

        agent_dir = templates / "claude" / "standard"
        agent_dir.mkdir(parents=True)
        (agent_dir / "agent-only.txt").write_text("agent content")

        apply_shell_template(shell, templates, "claude")

        assert (shell / "agent-only.txt").read_text() == "agent content"
