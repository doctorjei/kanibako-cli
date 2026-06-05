"""Tests for kanibako.rig_resolve (pure rig-name resolution)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kanibako.config import KanibakoConfig
from kanibako.rig_resolve import RigResolution, resolve_rig


def _runtime(has: list[str] | None = None) -> MagicMock:
    """A ContainerRuntime-like mock whose image_exists is controllable."""
    have = set(has or [])
    rt = MagicMock()
    rt.image_exists.side_effect = lambda ref: ref in have
    return rt


def _std(tmp_path: Path) -> MagicMock:
    """A StandardPaths-like object with a tmp data_path."""
    std = MagicMock()
    std.data_path = tmp_path
    return std


def _merged() -> KanibakoConfig:
    return KanibakoConfig()


def _write_template(tmp_path: Path, name: str) -> Path:
    """Drop a user-override template Containerfile and return its path."""
    containers = tmp_path / "containers"
    containers.mkdir(parents=True, exist_ok=True)
    cf = containers / f"Containerfile.template-{name}"
    cf.write_text(f"# kanibako-template: {name}\nFROM scratch\n")
    return cf


class TestDiscoveredTemplate:
    def test_bundled_template_builds(self, tmp_path):
        """jvm is a bundled template -> kind=template, prep_action=build."""
        res = resolve_rig("jvm", _runtime(), _std(tmp_path), _merged())
        assert res.kind == "template"
        assert res.prep_action == "build"
        assert res.image == "kanibako-template-jvm"
        assert res.containerfile is not None
        assert res.containerfile.name == "Containerfile.template-jvm"
        assert res.containerfile.is_file()

    def test_user_override_template_builds(self, tmp_path):
        """A user-dropped template is discovered and points at its file."""
        cf = _write_template(tmp_path, "mytools")
        res = resolve_rig("mytools", _runtime(), _std(tmp_path), _merged())
        assert res.kind == "template"
        assert res.prep_action == "build"
        assert res.image == "kanibako-template-mytools"
        assert res.containerfile == cf

    def test_returns_rigresolution(self, tmp_path):
        res = resolve_rig("jvm", _runtime(), _std(tmp_path), _merged())
        assert isinstance(res, RigResolution)
        assert res.name == "jvm"


class TestAlreadyPreppedLocal:
    def test_local_template_image_none(self, tmp_path):
        """An existing kanibako-template-<name> -> kind=template, prep none."""
        rt = _runtime(has=["kanibako-template-jvm"])
        res = resolve_rig("jvm", rt, _std(tmp_path), _merged())
        assert res.kind == "template"
        assert res.prep_action == "none"
        assert res.image == "kanibako-template-jvm"
        assert res.containerfile is None

    def test_local_extended_image_none(self, tmp_path):
        """An existing kanibako-rig-<name> -> kind=extended, prep none."""
        rt = _runtime(has=["kanibako-rig-foo"])
        res = resolve_rig("foo", rt, _std(tmp_path), _merged())
        assert res.kind == "extended"
        assert res.prep_action == "none"
        assert res.image == "kanibako-rig-foo"

    def test_local_image_wins_over_discovered_template(self, tmp_path):
        """A prepped template image short-circuits the build path."""
        _write_template(tmp_path, "jvm")
        rt = _runtime(has=["kanibako-template-jvm"])
        res = resolve_rig("jvm", rt, _std(tmp_path), _merged())
        assert res.prep_action == "none"
        assert res.containerfile is None


class TestPrefab:
    def test_known_suffix_pull_when_missing(self, tmp_path):
        """oci is a known suffix; not local -> prefab, pull."""
        res = resolve_rig("oci", _runtime(), _std(tmp_path), _merged())
        assert res.kind == "prefab"
        assert res.prep_action == "pull"
        assert res.image == "ghcr.io/doctorjei/kanibako-oci:latest"
        assert res.source_ref == "oci"

    def test_known_suffix_none_when_local(self, tmp_path):
        """oci present locally -> prefab, prep none."""
        rt = _runtime(has=["kanibako-oci:latest"])
        res = resolve_rig("oci", rt, _std(tmp_path), _merged())
        assert res.kind == "prefab"
        assert res.prep_action == "none"
        assert res.image == "kanibako-oci:latest"

    def test_qualified_reference(self, tmp_path):
        """A fully qualified reference passes through as a prefab."""
        ref = "ghcr.io/other/thing:v2"
        res = resolve_rig(ref, _runtime(), _std(tmp_path), _merged())
        assert res.kind == "prefab"
        assert res.image == ref
        assert res.prep_action == "pull"

    def test_qualified_reference_local(self, tmp_path):
        ref = "ghcr.io/other/thing:v2"
        rt = _runtime(has=[ref])
        res = resolve_rig(ref, rt, _std(tmp_path), _merged())
        assert res.prep_action == "none"


class TestInvalidNamesSkipLocalStep:
    def test_slash_name_does_not_crash(self, tmp_path):
        """A name with '/' can't be a local template image; resolves as prefab."""
        res = resolve_rig("foo/bar", _runtime(), _std(tmp_path), _merged())
        assert res.kind == "prefab"
        # 'foo/bar' already qualified -> passed through unchanged.
        assert res.image == "foo/bar"

    def test_colon_name_does_not_crash(self, tmp_path):
        """A tagged bare name skips the template/extended local check."""
        res = resolve_rig("busybox:latest", _runtime(), _std(tmp_path), _merged())
        assert res.kind == "prefab"
        assert res.image == "busybox:latest"


class TestRegistryParamAccepted:
    def test_registry_none_is_fine(self, tmp_path):
        res = resolve_rig("jvm", _runtime(), _std(tmp_path), _merged(), registry=None)
        assert res.kind == "template"

    def test_registry_without_name_falls_through(self, tmp_path):
        """A registry that doesn't contain the name doesn't change resolution."""
        from kanibako.rig_registry import RigRecord

        registry = {"other": RigRecord(name="other", kind="prefab")}
        res = resolve_rig(
            "jvm", _runtime(), _std(tmp_path), _merged(), registry=registry
        )
        assert res.kind == "template"


class TestRegistryConsultation:
    def _reg(self, *records):
        return {r.name: r for r in records}

    def test_prefab_image_present_is_none(self, tmp_path):
        """A prefab row with an image that exists locally -> prep none."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="corp/base", kind="prefab", image="corp/base:1.0")
        rt = _runtime(has=["corp/base:1.0"])
        res = resolve_rig(
            "corp/base", rt, _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "prefab"
        assert res.prep_action == "none"
        assert res.image == "corp/base:1.0"

    def test_prefab_image_absent_pulls(self, tmp_path):
        """A prefab row whose image is absent -> prep pull."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="corp/base", kind="prefab", image="corp/base:1.0")
        res = resolve_rig(
            "corp/base", _runtime(), _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "prefab"
        assert res.prep_action == "pull"
        assert res.image == "corp/base:1.0"

    def test_prefab_no_image_resolves_source_ref(self, tmp_path):
        """A prefab row with only a source ref resolves it via the reference path."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(
            name="mybox", kind="prefab",
            source="ghcr.io/corp/base:1.0", source_type="ref",
        )
        res = resolve_rig(
            "mybox", _runtime(), _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "prefab"
        assert res.prep_action == "pull"
        # 'ghcr.io/corp/base:1.0' is already qualified -> passes through.
        assert res.image == "ghcr.io/corp/base:1.0"
        assert res.source_ref == "ghcr.io/corp/base:1.0"

    def test_extended_image_present_is_none(self, tmp_path):
        """An extended row whose image exists -> extended/none."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="imported", kind="extended", image="kanibako-rig-imported")
        rt = _runtime(has=["kanibako-rig-imported"])
        res = resolve_rig(
            "imported", rt, _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "extended"
        assert res.prep_action == "none"
        assert res.image == "kanibako-rig-imported"

    def test_extended_image_absent_is_missing(self, tmp_path):
        """An extended row whose image is absent -> extended/missing."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="imported", kind="extended", image="kanibako-rig-imported")
        res = resolve_rig(
            "imported", _runtime(), _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "extended"
        assert res.prep_action == "missing"

    def test_extended_no_image_derives_default(self, tmp_path):
        """An extended row without an image falls back to kanibako-rig-<name>."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="imported", kind="extended")
        rt = _runtime(has=["kanibako-rig-imported"])
        res = resolve_rig(
            "imported", rt, _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "extended"
        assert res.prep_action == "none"
        assert res.image == "kanibako-rig-imported"

    def test_extended_no_image_invalid_name_does_not_raise(self, tmp_path):
        """A malformed extended row (no image, name invalid for the default
        kanibako-rig-<name>) must resolve without raising, not crash."""
        from kanibako.rig_registry import RigRecord

        # 'corp/base:1.0' is a valid registry key but NOT a valid short name,
        # so the default rig_image_name() would raise -- the resolver must
        # fall back instead of propagating that ValueError.
        rec = RigRecord(name="corp/base:1.0", kind="extended")
        res = resolve_rig(
            "corp/base:1.0", _runtime(), _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        assert res.kind == "extended"
        assert res.prep_action == "missing"
        assert res.image == "corp/base:1.0"

    def test_local_image_wins_over_registry(self, tmp_path):
        """An already-prepped local template short-circuits before the registry."""
        from kanibako.rig_registry import RigRecord

        rec = RigRecord(name="jvm", kind="prefab", image="corp/base:1.0")
        rt = _runtime(has=["kanibako-template-jvm"])
        res = resolve_rig(
            "jvm", rt, _std(tmp_path), _merged(),
            registry=self._reg(rec),
        )
        # Local prepped template wins; registry row is never consulted.
        assert res.kind == "template"
        assert res.prep_action == "none"
        assert res.image == "kanibako-template-jvm"


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
