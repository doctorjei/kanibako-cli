"""Tests for kanibako.instructions — layered instruction file merging."""

from __future__ import annotations

from kanibako.instructions import (
    _MARKER_BASE,
    _MARKER_PROJECT,
    _read_layer,
    merge_instruction_content,
    merge_instruction_files,
)


class TestReadLayer:
    def test_reads_file_content(self, tmp_path):
        f = tmp_path / "test.md"
        f.write_text("hello world\n")
        assert _read_layer(f) == "hello world"

    def test_returns_none_for_missing_file(self, tmp_path):
        f = tmp_path / "nonexistent.md"
        assert _read_layer(f) is None

    def test_returns_none_for_empty_file(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("")
        assert _read_layer(f) is None

    def test_returns_none_for_whitespace_only(self, tmp_path):
        f = tmp_path / "whitespace.md"
        f.write_text("   \n\n   \n")
        assert _read_layer(f) is None

    def test_strips_content(self, tmp_path):
        f = tmp_path / "padded.md"
        f.write_text("\n  content here  \n\n")
        assert _read_layer(f) == "content here"


class TestMergeInstructionContent:
    def test_all_three_layers(self):
        result = merge_instruction_content(
            base_content="base instructions",
            template_content="template instructions",
            template_name="standard",
            user_content="user instructions",
        )
        assert _MARKER_BASE in result
        assert "# --- template: standard ---" in result
        assert _MARKER_PROJECT in result
        assert "base instructions" in result
        assert "template instructions" in result
        assert "user instructions" in result

    def test_layer_order(self):
        """Base appears first, user appears last."""
        result = merge_instruction_content(
            base_content="AAA",
            template_content="BBB",
            template_name="test",
            user_content="CCC",
        )
        base_pos = result.index("AAA")
        tmpl_pos = result.index("BBB")
        user_pos = result.index("CCC")
        assert base_pos < tmpl_pos < user_pos

    def test_base_only(self):
        result = merge_instruction_content(base_content="base only")
        assert result is not None
        assert "base only" in result
        assert _MARKER_BASE in result

    def test_template_only(self):
        result = merge_instruction_content(
            template_content="template only",
            template_name="minimal",
        )
        assert result is not None
        assert "template only" in result
        assert "# --- template: minimal ---" in result

    def test_user_only(self):
        result = merge_instruction_content(user_content="user only")
        assert result is not None
        assert "user only" in result
        assert _MARKER_PROJECT in result

    def test_base_and_template(self):
        result = merge_instruction_content(
            base_content="base",
            template_content="template",
            template_name="std",
        )
        assert _MARKER_BASE in result
        assert "# --- template: std ---" in result
        assert _MARKER_PROJECT not in result

    def test_base_and_user(self):
        result = merge_instruction_content(
            base_content="base",
            user_content="user",
        )
        assert _MARKER_BASE in result
        assert _MARKER_PROJECT in result
        assert "template" not in result.lower().split("---")[0]  # no template marker

    def test_template_and_user(self):
        result = merge_instruction_content(
            template_content="template",
            template_name="test",
            user_content="user",
        )
        assert _MARKER_BASE not in result
        assert "# --- template: test ---" in result
        assert _MARKER_PROJECT in result

    def test_all_none_returns_none(self):
        result = merge_instruction_content()
        assert result is None

    def test_empty_strings_treated_as_missing(self):
        result = merge_instruction_content(
            base_content="",
            template_content="",
            user_content="",
        )
        assert result is None

    def test_default_template_name(self):
        result = merge_instruction_content(
            template_content="content",
        )
        assert "# --- template: default ---" in result

    def test_result_ends_with_newline(self):
        result = merge_instruction_content(
            base_content="base",
            user_content="user",
        )
        assert result.endswith("\n")


class TestMergeInstructionFiles:
    def _setup_dirs(self, tmp_path):
        """Set up shell_path and templates_base for testing."""
        shell = tmp_path / "shell"
        shell.mkdir()
        templates = tmp_path / "templates"
        templates.mkdir()
        return shell, templates

    def test_empty_instruction_files_is_noop(self, tmp_path):
        shell, templates = self._setup_dirs(tmp_path)
        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=[],
            templates_base=templates,
            agent_name="claude",
        )
        assert not (shell / ".claude").exists()

    def test_base_layer_only(self, tmp_path):
        """Only base layer has content; file is written without markers."""
        shell, templates = self._setup_dirs(tmp_path)

        # Create base layer
        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("# Base instructions\n")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        # Single layer — no markers
        assert _MARKER_BASE not in result
        assert "# Base instructions" in result

    def test_template_layer_only(self, tmp_path):
        """Only template layer has content; file is written without markers."""
        shell, templates = self._setup_dirs(tmp_path)

        # Create template layer
        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("# Template instructions\n")

        # apply_shell_template would have copied this to shell/.claude/CLAUDE.md
        # Simulate that:
        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("# Template instructions\n")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        # Single layer — no markers
        assert "# --- template" not in result
        assert "# Template instructions" in result

    def test_base_and_template_layers(self, tmp_path):
        """Base + template layers merged with markers."""
        shell, templates = self._setup_dirs(tmp_path)

        # Create base layer
        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("# Base\nBase content")

        # Create template layer
        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("# Template\nTemplate content")

        # Simulate apply_shell_template having copied template content
        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("# Template\nTemplate content")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert _MARKER_BASE in result
        assert "# --- template: standard ---" in result
        assert "Base content" in result
        assert "Template content" in result

    def test_all_three_layers(self, tmp_path):
        """Base + template + user layers all present."""
        shell, templates = self._setup_dirs(tmp_path)

        # Create base layer
        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("# Base\nBase content")

        # Create template layer
        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("# Template\nTemplate content")

        # User content already in shell (different from template)
        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("# My Project\nUser-specific instructions")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert _MARKER_BASE in result
        assert "# --- template: standard ---" in result
        assert _MARKER_PROJECT in result
        assert "Base content" in result
        assert "Template content" in result
        assert "User-specific instructions" in result

    def test_user_layer_order_is_last(self, tmp_path):
        """User content appears after base and template content."""
        shell, templates = self._setup_dirs(tmp_path)

        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("AAAA")

        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("BBBB")

        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("CCCC")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert result.index("AAAA") < result.index("BBBB") < result.index("CCCC")

    def test_user_content_same_as_template_not_duplicated(self, tmp_path):
        """When shell file is identical to template, it's not treated as user content."""
        shell, templates = self._setup_dirs(tmp_path)

        # Base layer
        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("Base content")

        # Template layer
        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("Template content")

        # Shell has exact same content as template (from apply_shell_template)
        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("Template content")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        # Template content should appear exactly once
        assert result.count("Template content") == 1

    def test_no_templates_base(self, tmp_path):
        """When templates_base is None, only user content is used."""
        shell = tmp_path / "shell"
        shell.mkdir()

        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("User content only")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=None,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert "User content only" in result

    def test_missing_all_layers_skips_file(self, tmp_path):
        """When no layers have content, file is not created."""
        shell, templates = self._setup_dirs(tmp_path)

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        assert not (shell / ".claude" / "CLAUDE.md").exists()

    def test_multiple_instruction_files(self, tmp_path):
        """Multiple instruction files are each processed independently."""
        shell, templates = self._setup_dirs(tmp_path)

        # Base layer with two files
        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("Base CLAUDE")
        (base_dir / "RULES.md").write_text("Base RULES")

        # Template layer with only one file
        tmpl_dir = templates / "claude" / "standard" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("Template CLAUDE")

        # Shell has template content for CLAUDE.md
        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("Template CLAUDE")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md", "RULES.md"],
            templates_base=templates,
            agent_name="claude",
        )

        claude_result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert "Base CLAUDE" in claude_result
        assert "Template CLAUDE" in claude_result

        rules_result = (shell / ".claude" / "RULES.md").read_text()
        assert "Base RULES" in rules_result

    def test_general_fallback_template(self, tmp_path):
        """Falls back to general/standard when agent-specific template is missing."""
        shell, templates = self._setup_dirs(tmp_path)

        # Base layer
        base_dir = templates / "general" / "base" / ".agent"
        base_dir.mkdir(parents=True)
        (base_dir / "instructions.md").write_text("Base")

        # General template (no agent-specific template exists)
        gen_dir = templates / "general" / "standard" / ".agent"
        gen_dir.mkdir(parents=True)
        (gen_dir / "instructions.md").write_text("General template")

        # Shell has general template content
        dest_dir = shell / ".agent"
        dest_dir.mkdir(parents=True)
        (dest_dir / "instructions.md").write_text("General template")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".agent",
            instruction_files=["instructions.md"],
            templates_base=templates,
            agent_name="some_agent",
        )

        result = (shell / ".agent" / "instructions.md").read_text()
        assert "Base" in result
        assert "General template" in result

    def test_creates_config_dir_if_needed(self, tmp_path):
        """Config directory is created if it doesn't exist."""
        shell, templates = self._setup_dirs(tmp_path)

        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("Base content")

        # .claude dir does NOT exist in shell yet
        assert not (shell / ".claude").exists()

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        assert (shell / ".claude" / "CLAUDE.md").exists()

    def test_custom_template_name(self, tmp_path):
        """Uses the correct template variant directory."""
        shell, templates = self._setup_dirs(tmp_path)

        tmpl_dir = templates / "claude" / "minimal" / ".claude"
        tmpl_dir.mkdir(parents=True)
        (tmpl_dir / "CLAUDE.md").write_text("Minimal template")

        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("Minimal template")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
            template_name="minimal",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert "Minimal template" in result

    def test_user_content_same_as_base_not_duplicated(self, tmp_path):
        """When shell file matches base content, it's not treated as user content."""
        shell, templates = self._setup_dirs(tmp_path)

        base_dir = templates / "general" / "base" / ".claude"
        base_dir.mkdir(parents=True)
        (base_dir / "CLAUDE.md").write_text("Same content")

        dest_dir = shell / ".claude"
        dest_dir.mkdir(parents=True)
        (dest_dir / "CLAUDE.md").write_text("Same content")

        merge_instruction_files(
            shell_path=shell,
            config_dir_name=".claude",
            instruction_files=["CLAUDE.md"],
            templates_base=templates,
            agent_name="claude",
        )

        result = (shell / ".claude" / "CLAUDE.md").read_text()
        assert result.count("Same content") == 1


class TestTargetInstructionFiles:
    """Test that target plugins return expected instruction_files()."""

    def test_claude_target_returns_claude_md(self):
        from kanibako.plugins.claude import ClaudeTarget

        t = ClaudeTarget()
        assert t.instruction_files() == ["CLAUDE.md"]

    def test_no_agent_target_returns_empty(self):
        from kanibako.targets.no_agent import NoAgentTarget

        t = NoAgentTarget()
        assert t.instruction_files() == []

    def test_base_target_default_returns_empty(self):
        """The default implementation on the ABC returns an empty list."""
        from kanibako.targets.base import Target

        # Create a minimal concrete subclass to test the default
        class MinimalTarget(Target):
            @property
            def name(self): return "test"
            @property
            def display_name(self): return "Test"
            def detect(self): return None
            def binary_mounts(self, install): return []
            def init_home(self, home, *, auth="shared"): pass
            def refresh_credentials(self, home): pass
            def writeback_credentials(self, home): pass
            def build_cli_args(self, **kwargs): return []

        t = MinimalTarget()
        assert t.instruction_files() == []
