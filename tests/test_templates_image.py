"""Tests for kanibako.templates_image."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kanibako.templates_image import (
    BundledTemplate,
    delete_template,
    list_bundled_templates,
    list_templates,
    template_image_name,
    validate_template_name,
)


class TestValidateTemplateName:
    def test_accepts_simple(self):
        validate_template_name("jvm")

    def test_accepts_dashes(self):
        validate_template_name("my-tools")

    def test_accepts_underscores(self):
        validate_template_name("my_tools")

    def test_accepts_digits(self):
        validate_template_name("3d-tools")

    def test_rejects_uppercase(self):
        with pytest.raises(ValueError, match="Invalid template name"):
            validate_template_name("MyTools")

    def test_rejects_spaces(self):
        with pytest.raises(ValueError, match="Invalid template name"):
            validate_template_name("my tools")

    def test_rejects_slashes(self):
        with pytest.raises(ValueError, match="Invalid template name"):
            validate_template_name("../etc")

    def test_rejects_leading_dash(self):
        with pytest.raises(ValueError, match="Invalid template name"):
            validate_template_name("-bad")

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="Invalid template name"):
            validate_template_name("")


class TestTemplateImageName:
    def test_simple_name(self):
        assert template_image_name("jvm") == "kanibako-template-jvm"

    def test_preserves_dashes(self):
        assert template_image_name("my-tools") == "kanibako-template-my-tools"

    def test_rejects_invalid_name(self):
        with pytest.raises(ValueError):
            template_image_name("Bad Name!")


class TestListTemplates:
    def test_empty_when_no_images(self):
        runtime = MagicMock()
        runtime.list_local_images.return_value = []
        assert list_templates(runtime) == []

    def test_filters_template_images(self):
        runtime = MagicMock()
        runtime.list_local_images.return_value = [
            ("kanibako-template-jvm", "1.2 GB"),
            ("kanibako-oci", "900 MB"),
            ("kanibako-template-systems", "2.1 GB"),
            ("ubuntu:latest", "80 MB"),
        ]
        result = list_templates(runtime)
        assert result == [
            ("jvm", "kanibako-template-jvm", "1.2 GB"),
            ("systems", "kanibako-template-systems", "2.1 GB"),
        ]

    def test_handles_tagged_images(self):
        runtime = MagicMock()
        runtime.list_local_images.return_value = [
            ("kanibako-template-jvm:latest", "1.2 GB"),
        ]
        result = list_templates(runtime)
        assert result == [("jvm", "kanibako-template-jvm", "1.2 GB")]


class TestListBundledTemplates:
    def test_discovers_matching_files_with_descriptions(self, tmp_path):
        (tmp_path / "Containerfile.template-foo").write_text(
            "# kanibako-template: Foo desc\nARG BASE_IMAGE=kanibako-oci:latest\n"
        )
        (tmp_path / "Containerfile.template-bar").write_text(
            "ARG BASE_IMAGE=kanibako-oci:latest\n"
        )
        (tmp_path / "Containerfile.kanibako").write_text("FROM scratch\n")
        (tmp_path / "Containerfile.notatemplate").write_text("FROM scratch\n")

        result = list_bundled_templates(tmp_path)

        assert result == [
            BundledTemplate(name="bar", description="bar template"),
            BundledTemplate(name="foo", description="Foo desc"),
        ]

    def test_skips_archive_subdir(self, tmp_path):
        archive = tmp_path / "archive"
        archive.mkdir()
        (archive / "Containerfile.template-old").write_text("FROM scratch\n")
        (tmp_path / "Containerfile.template-foo").write_text("FROM scratch\n")

        result = list_bundled_templates(tmp_path)

        assert [t.name for t in result] == ["foo"]

    def test_empty_when_dir_missing(self, tmp_path):
        assert list_bundled_templates(tmp_path / "nope") == []

    def test_bundled_dir_includes_jvm_and_systems(self):
        names = {t.name for t in list_bundled_templates()}
        assert {"jvm", "systems"} <= names


class TestDeleteTemplate:
    def test_removes_image(self):
        runtime = MagicMock()
        delete_template(runtime, "jvm")
        runtime.remove_image.assert_called_once_with("kanibako-template-jvm")

    def test_raises_on_unknown(self):
        runtime = MagicMock()
        runtime.remove_image.side_effect = Exception("no such image")
        with pytest.raises(Exception, match="no such image"):
            delete_template(runtime, "jvm")
