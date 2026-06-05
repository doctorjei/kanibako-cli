"""Tests for kanibako.rig_source: source detection + name derivation."""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from kanibako.rig_source import derive_name, detect_source_kind, fetch_to_temp


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _make_image_tar(
    path: Path, *, marker: str = "manifest.json", repo_tags: list[str] | None = None
) -> Path:
    """Build a tiny tar marking it as an image archive.

    *marker* is the member name that flags it as an image. When *repo_tags* is
    given, a ``manifest.json`` member carrying those tags is written.
    """
    with tarfile.open(path, "w") as tar:
        if repo_tags is not None:
            payload = json.dumps([{"RepoTags": repo_tags}]).encode("utf-8")
            info = tarfile.TarInfo(name="manifest.json")
            info.size = len(payload)
            import io

            tar.addfile(info, io.BytesIO(payload))
        else:
            info = tarfile.TarInfo(name=marker)
            info.size = 0
            import io

            tar.addfile(info, io.BytesIO(b""))
    return path


# ---------------------------------------------------------------------------
# detect_source_kind
# ---------------------------------------------------------------------------


def test_template_file_with_from(tmp_path: Path) -> None:
    cf = _write(tmp_path / "Containerfile.myjvm", "FROM kanibako-oci\n")
    assert detect_source_kind(str(cf)) == "template"


def test_template_file_lowercase_from(tmp_path: Path) -> None:
    cf = _write(tmp_path / "Containerfile.x", "from busybox\n")
    assert detect_source_kind(str(cf)) == "template"


def test_template_via_header(tmp_path: Path) -> None:
    cf = _write(
        tmp_path / "some.txt",
        "# kanibako-template: foo\nFROM x\n",
    )
    assert detect_source_kind(str(cf)) == "template"


def test_template_via_check_header(tmp_path: Path) -> None:
    cf = _write(
        tmp_path / "some.txt",
        "# kanibako-template-check: java -version\n",
    )
    assert detect_source_kind(str(cf)) == "template"


def test_image_tar_manifest(tmp_path: Path) -> None:
    tar = _make_image_tar(tmp_path / "img.tar")
    assert detect_source_kind(str(tar)) == "image"


def test_image_tar_oci_layout(tmp_path: Path) -> None:
    tar = _make_image_tar(tmp_path / "img2.tar", marker="oci-layout")
    assert detect_source_kind(str(tar)) == "image"


def test_image_ref_with_host(tmp_path: Path) -> None:
    # Not an existing path.
    assert detect_source_kind("ghcr.io/corp/base:1.0") == "image"


def test_image_ref_bare(tmp_path: Path) -> None:
    assert detect_source_kind("busybox") == "image"


def test_image_ref_with_digest() -> None:
    assert detect_source_kind("ubuntu@sha256:abc123") == "image"


def test_ambiguous_text_file_raises(tmp_path: Path) -> None:
    f = _write(tmp_path / "notes.txt", "hello world\nno from here\n")
    with pytest.raises(ValueError, match="cannot classify"):
        detect_source_kind(str(f))


def test_url_raises_fetch_first() -> None:
    with pytest.raises(ValueError, match="fetch it first"):
        detect_source_kind("https://example.com/Containerfile")


def test_force_template_overrides(tmp_path: Path) -> None:
    # A file that would otherwise be ambiguous.
    f = _write(tmp_path / "notes.txt", "hello world\n")
    assert detect_source_kind(str(f), force="template") == "template"


def test_force_image_overrides() -> None:
    assert detect_source_kind("anything at all", force="image") == "image"


def test_force_invalid_raises() -> None:
    with pytest.raises(ValueError, match="invalid force"):
        detect_source_kind("busybox", force="bogus")


# ---------------------------------------------------------------------------
# derive_name
# ---------------------------------------------------------------------------


def test_derive_name_containerfile(tmp_path: Path) -> None:
    cf = _write(tmp_path / "Containerfile.myjvm", "FROM kanibako-oci\n")
    assert derive_name(str(cf), "template") == "myjvm"


def test_derive_name_strips_template_prefix() -> None:
    assert derive_name("Containerfile.template-foo", "template") == "foo"


def test_derive_name_dockerfile_basename() -> None:
    assert derive_name("./Dockerfile.bar", "template") == "bar"


def test_derive_name_bare_containerfile_none() -> None:
    assert derive_name("Containerfile", "template") is None


def test_derive_name_bare_dockerfile_none() -> None:
    assert derive_name("Dockerfile", "template") is None


def test_derive_name_image_ref_with_host() -> None:
    assert derive_name("ghcr.io/corp/base:1.0", "image") == "corp/base:1.0"


def test_derive_name_image_ref_no_host() -> None:
    assert derive_name("corp/base:1.0", "image") == "corp/base:1.0"


def test_derive_name_image_ref_localhost_port() -> None:
    assert derive_name("localhost:5000/foo:1", "image") == "foo:1"


def test_derive_name_bare_image() -> None:
    assert derive_name("busybox", "image") == "busybox"


def test_derive_name_image_tar_repotag(tmp_path: Path) -> None:
    tar = _make_image_tar(
        tmp_path / "img.tar", repo_tags=["myrepo/base:1.0"]
    )
    assert derive_name(str(tar), "image") == "myrepo/base:1.0"


def test_derive_name_image_tar_no_repotag_uses_stem(tmp_path: Path) -> None:
    tar = _make_image_tar(tmp_path / "saved-image.tar")
    assert derive_name(str(tar), "image") == "saved-image"


def test_derive_name_url_underivable() -> None:
    assert derive_name("https://example.com/blob", "image") is None


def test_derive_name_url_with_containerfile_basename() -> None:
    assert (
        derive_name("https://example.com/Containerfile.web", "template")
        == "web"
    )


# ---------------------------------------------------------------------------
# fetch_to_temp (monkeypatched -- no real network)
# ---------------------------------------------------------------------------


def test_fetch_to_temp_monkeypatched(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_urlretrieve(url: str, filename: str) -> tuple[str, object]:
        captured["url"] = url
        Path(filename).write_text("FROM busybox\n", encoding="utf-8")
        return filename, object()

    monkeypatch.setattr(
        "kanibako.rig_source.urllib.request.urlretrieve", fake_urlretrieve
    )

    result = fetch_to_temp("https://example.com/Containerfile.web")
    assert captured["url"] == "https://example.com/Containerfile.web"
    assert result.is_file()
    # The fetched file should classify as a template.
    assert detect_source_kind(str(result)) == "template"
    result.unlink()
