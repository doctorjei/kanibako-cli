"""Tests for kanibako.containerfiles: bundled resolution, user overrides, listing."""

from __future__ import annotations



from kanibako.containerfiles import get_containerfile, list_containerfile_suffixes


class TestGetContainerfile:
    def test_bundled_resolution(self):
        """Should find a bundled template Containerfile (base no longer ships)."""
        result = get_containerfile("template-jvm")
        assert result is not None
        assert result.name == "Containerfile.template-jvm"
        assert result.is_file()

    def test_user_override_takes_priority(self, tmp_path):
        """User-override dir should win over bundled files."""
        override = tmp_path / "Containerfile.template-jvm"
        override.write_text("FROM custom\n")

        result = get_containerfile("template-jvm", tmp_path)
        assert result is not None
        assert result == override

    def test_falls_back_to_bundled(self, tmp_path):
        """When override dir exists but has no matching file, fall back to bundled."""
        result = get_containerfile("template-jvm", tmp_path)
        assert result is not None
        assert result.name == "Containerfile.template-jvm"
        # Should NOT be inside tmp_path
        assert not str(result).startswith(str(tmp_path))

    def test_base_no_longer_bundled(self):
        """The base Containerfile.kanibako is no longer shipped (pull-only)."""
        assert get_containerfile("kanibako") is None

    def test_user_override_for_arbitrary_suffix(self, tmp_path):
        """A user can still drop any Containerfile.<suffix> override."""
        override = tmp_path / "Containerfile.kanibako"
        override.write_text("FROM custom\n")
        result = get_containerfile("kanibako", tmp_path)
        assert result == override

    def test_not_found_returns_none(self, tmp_path):
        """Unknown suffix returns None."""
        result = get_containerfile("nonexistent", tmp_path)
        assert result is None

    def test_no_override_dir(self):
        """Works when data_containers_dir is None (bundled only)."""
        result = get_containerfile("template-jvm", None)
        assert result is not None
        assert result.name == "Containerfile.template-jvm"


class TestListContainerfileSuffixes:
    def test_lists_bundled(self):
        """Should list the bundled template Containerfiles."""
        suffixes = list_containerfile_suffixes()
        assert "template-jvm" in suffixes
        assert suffixes == sorted(suffixes)

    def test_merges_with_user_overrides(self, tmp_path):
        """User-override dir adds suffixes to the bundled set."""
        (tmp_path / "Containerfile.custom").write_text("FROM custom\n")
        suffixes = list_containerfile_suffixes(tmp_path)
        assert "custom" in suffixes
        assert "template-jvm" in suffixes

    def test_deduplicates(self, tmp_path):
        """Same suffix in both bundled and override appears once."""
        (tmp_path / "Containerfile.template-jvm").write_text("FROM custom\n")
        suffixes = list_containerfile_suffixes(tmp_path)
        assert suffixes.count("template-jvm") == 1

    def test_empty_override_dir(self, tmp_path):
        """Empty override dir just returns bundled."""
        suffixes = list_containerfile_suffixes(tmp_path)
        assert "template-jvm" in suffixes

    def test_nonexistent_override_dir(self, tmp_path):
        """Non-existent override dir is handled gracefully."""
        suffixes = list_containerfile_suffixes(tmp_path / "nope")
        assert "template-jvm" in suffixes
