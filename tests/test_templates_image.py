"""Tests for kanibako.templates_image."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kanibako.templates_image import (
    BundledTemplate,
    _bundled_containers_dir,
    delete_template,
    list_bundled_templates,
    list_templates,
    read_template_checks,
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

    def test_bundled_dir_includes_all_five(self):
        names = sorted(t.name for t in list_bundled_templates())
        assert names == ["android", "dotnet", "js", "jvm", "systems"]


class TestListBundledTemplatesWithOverride:
    def test_override_adds_user_template(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "Containerfile.template-foo").write_text(
            "# kanibako-template: Foo desc\nFROM scratch\n"
        )
        override = tmp_path / "override"
        override.mkdir()
        (override / "Containerfile.template-custom").write_text(
            "# kanibako-template: Custom desc\nFROM scratch\n"
        )

        result = list_bundled_templates(bundled, override_dir=override)

        assert result == [
            BundledTemplate(
                name="custom", description="Custom desc", source="user"
            ),
            BundledTemplate(name="foo", description="Foo desc", source="bundled"),
        ]

    def test_user_template_overrides_bundled_same_name(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "Containerfile.template-foo").write_text(
            "# kanibako-template: Bundled foo\nFROM scratch\n"
        )
        override = tmp_path / "override"
        override.mkdir()
        (override / "Containerfile.template-foo").write_text(
            "# kanibako-template: User foo\nFROM scratch\n"
        )

        result = list_bundled_templates(bundled, override_dir=override)

        assert result == [
            BundledTemplate(
                name="foo", description="User foo", source="user"
            ),
        ]

    def test_no_override_dir_identical_to_bundled_only(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "Containerfile.template-foo").write_text("FROM scratch\n")

        assert list_bundled_templates(bundled) == list_bundled_templates(
            bundled, override_dir=None
        )
        assert list_bundled_templates(bundled, override_dir=None) == [
            BundledTemplate(
                name="foo", description="foo template", source="bundled"
            ),
        ]

    def test_empty_override_dir_identical_to_bundled_only(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "Containerfile.template-foo").write_text("FROM scratch\n")
        empty_override = tmp_path / "override"
        empty_override.mkdir()

        assert list_bundled_templates(
            bundled, override_dir=empty_override
        ) == list_bundled_templates(bundled)

    def test_missing_override_dir_ignored(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        (bundled / "Containerfile.template-foo").write_text("FROM scratch\n")

        assert list_bundled_templates(
            bundled, override_dir=tmp_path / "nope"
        ) == list_bundled_templates(bundled)

    def test_invalid_named_override_file_skipped(self, tmp_path):
        bundled = tmp_path / "bundled"
        bundled.mkdir()
        override = tmp_path / "override"
        override.mkdir()
        (override / "Containerfile.template-Bad").write_text("FROM scratch\n")
        (override / "Containerfile.template-good").write_text("FROM scratch\n")

        result = list_bundled_templates(bundled, override_dir=override)

        assert [t.name for t in result] == ["good"]
        assert result[0].source == "user"

    def test_default_source_is_bundled(self):
        assert BundledTemplate(name="x", description="y").source == "bundled"


class TestReadTemplateChecks:
    def test_parses_single_check(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "# kanibako-template: Foo desc\n"
            "# kanibako-template-check: java -version\n"
            "ARG BASE_IMAGE=kanibako-oci:latest\n"
            "FROM $BASE_IMAGE\n"
        )
        assert read_template_checks(cf) == ("java -version",)

    def test_parses_multiple_checks_in_order(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "# kanibako-template: Foo desc\n"
            "# kanibako-template-check: java -version\n"
            "# kanibako-template-check: kotlin -version\n"
            "# kanibako-template-check: mvn -version\n"
            "FROM scratch\n"
        )
        assert read_template_checks(cf) == (
            "java -version",
            "kotlin -version",
            "mvn -version",
        )

    def test_ignores_description_header(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "# kanibako-template: Foo desc\n"
            "FROM scratch\n"
        )
        assert read_template_checks(cf) == ()

    def test_returns_empty_when_no_checks(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text("FROM scratch\n")
        assert read_template_checks(cf) == ()

    def test_stops_at_first_directive_line(self, tmp_path):
        # A check header appearing AFTER a FROM/ARG line must NOT be picked up.
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "# kanibako-template-check: java -version\n"
            "ARG BASE_IMAGE=kanibako-oci:latest\n"
            "FROM $BASE_IMAGE\n"
            "# kanibako-template-check: should-not-appear\n"
            "RUN echo hi\n"
        )
        assert read_template_checks(cf) == ("java -version",)

    def test_allows_blank_lines_in_leading_block(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "# kanibako-template: Foo desc\n"
            "\n"
            "# kanibako-template-check: java -version\n"
            "\n"
            "# kanibako-template-check: mvn -version\n"
            "FROM scratch\n"
        )
        assert read_template_checks(cf) == (
            "java -version",
            "mvn -version",
        )

    def test_strips_surrounding_whitespace(self, tmp_path):
        cf = tmp_path / "Containerfile.template-foo"
        cf.write_text(
            "#   kanibako-template-check:    java -version   \n"
            "FROM scratch\n"
        )
        assert read_template_checks(cf) == ("java -version",)

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_template_checks(tmp_path / "nope") == ()


class TestBundledTemplatesDeclareChecks:
    @pytest.mark.parametrize(
        "template",
        list_bundled_templates(),
        ids=lambda t: t.name,
    )
    def test_every_bundled_template_declares_at_least_one_check(self, template):
        containers_dir = _bundled_containers_dir()
        assert containers_dir is not None
        cf = containers_dir / f"Containerfile.template-{template.name}"
        checks = read_template_checks(cf)
        assert len(checks) >= 1, (
            f"bundled template {template.name!r} declares no "
            "# kanibako-template-check: header"
        )


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
