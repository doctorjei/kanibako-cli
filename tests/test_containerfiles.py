"""Tests for kanibako.containerfiles: bundled resolution, user overrides, listing."""

from __future__ import annotations



from kanibako.containerfiles import get_containerfile, list_containerfile_suffixes


class TestGetContainerfile:
    def test_bundled_resolution(self):
        """Should find the unified Containerfile bundled with the package."""
        result = get_containerfile("kanibako")
        assert result is not None
        assert result.name == "Containerfile.kanibako"
        assert result.is_file()

    def test_user_override_takes_priority(self, tmp_path):
        """User-override dir should win over bundled files."""
        override = tmp_path / "Containerfile.kanibako"
        override.write_text("FROM custom\n")

        result = get_containerfile("kanibako", tmp_path)
        assert result is not None
        assert result == override

    def test_falls_back_to_bundled(self, tmp_path):
        """When override dir exists but has no matching file, fall back to bundled."""
        result = get_containerfile("kanibako", tmp_path)
        assert result is not None
        assert result.name == "Containerfile.kanibako"
        # Should NOT be inside tmp_path
        assert not str(result).startswith(str(tmp_path))

    def test_not_found_returns_none(self, tmp_path):
        """Unknown suffix returns None."""
        result = get_containerfile("nonexistent", tmp_path)
        assert result is None

    def test_no_override_dir(self):
        """Works when data_containers_dir is None (bundled only)."""
        result = get_containerfile("kanibako", None)
        assert result is not None
        assert result.name == "Containerfile.kanibako"


class TestListContainerfileSuffixes:
    def test_lists_bundled(self):
        """Should list the unified kanibako Containerfile."""
        suffixes = list_containerfile_suffixes()
        assert "kanibako" in suffixes
        assert suffixes == sorted(suffixes)

    def test_merges_with_user_overrides(self, tmp_path):
        """User-override dir adds suffixes to the bundled set."""
        (tmp_path / "Containerfile.custom").write_text("FROM custom\n")
        suffixes = list_containerfile_suffixes(tmp_path)
        assert "custom" in suffixes
        assert "kanibako" in suffixes

    def test_deduplicates(self, tmp_path):
        """Same suffix in both bundled and override appears once."""
        (tmp_path / "Containerfile.kanibako").write_text("FROM custom\n")
        suffixes = list_containerfile_suffixes(tmp_path)
        assert suffixes.count("kanibako") == 1

    def test_empty_override_dir(self, tmp_path):
        """Empty override dir just returns bundled."""
        suffixes = list_containerfile_suffixes(tmp_path)
        assert "kanibako" in suffixes

    def test_nonexistent_override_dir(self, tmp_path):
        """Non-existent override dir is handled gracefully."""
        suffixes = list_containerfile_suffixes(tmp_path / "nope")
        assert "kanibako" in suffixes
