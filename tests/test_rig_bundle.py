"""Tests for kanibako.rig_bundle (.rig.tgz export bundle)."""

from __future__ import annotations

import io
import tarfile

import pytest

from kanibako.rig_bundle import (
    BUNDLE_SUFFIX,
    pack_bundle,
    read_bundle_meta,
    unpack_bundle,
)
from kanibako.rig_meta import RigMeta, write_rig_meta


def _make_meta_file(tmp_path, name="myhack"):
    meta = RigMeta(name=name, parent="kanibako-oci:latest")
    path = tmp_path / "rig.yaml"
    write_rig_meta(meta, path)
    return path, meta


def _make_image_tar(tmp_path, content=b"dummy-image-bytes"):
    path = tmp_path / "image.tar"
    path.write_bytes(content)
    return path


def test_bundle_suffix():
    assert BUNDLE_SUFFIX == ".rig.tgz"


def test_pack_members_minimal(tmp_path):
    rig_yaml, _ = _make_meta_file(tmp_path)
    image_tar = _make_image_tar(tmp_path)
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"

    pack_bundle(out, rig_yaml, image_tar)

    assert out.is_file()
    with tarfile.open(out, "r:gz") as tar:
        assert set(tar.getnames()) == {"rig.yaml", "image.tar"}


def test_pack_members_with_containerfile(tmp_path):
    rig_yaml, _ = _make_meta_file(tmp_path)
    image_tar = _make_image_tar(tmp_path)
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM kanibako-oci:latest\n")
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"

    pack_bundle(out, rig_yaml, image_tar, containerfile)

    with tarfile.open(out, "r:gz") as tar:
        assert set(tar.getnames()) == {
            "rig.yaml",
            "image.tar",
            "Containerfile",
        }


def test_pack_missing_image_tar_raises(tmp_path):
    rig_yaml, _ = _make_meta_file(tmp_path)
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"
    with pytest.raises(FileNotFoundError):
        pack_bundle(out, rig_yaml, tmp_path / "nope.tar")


def test_pack_missing_rig_yaml_raises(tmp_path):
    image_tar = _make_image_tar(tmp_path)
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"
    with pytest.raises(FileNotFoundError):
        pack_bundle(out, tmp_path / "nope.yaml", image_tar)


def test_unpack_round_trip(tmp_path):
    rig_yaml, meta = _make_meta_file(tmp_path)
    image_tar = _make_image_tar(tmp_path, b"hello-image")
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"
    pack_bundle(out, rig_yaml, image_tar)

    dest = tmp_path / "unpacked"
    result = unpack_bundle(out, dest)

    assert result["rig_yaml"] == dest / "rig.yaml"
    assert result["image_tar"] == dest / "image.tar"
    assert "containerfile" not in result
    assert result["rig_yaml"].is_file()
    assert result["image_tar"].read_bytes() == b"hello-image"


def test_unpack_includes_containerfile_when_present(tmp_path):
    rig_yaml, _ = _make_meta_file(tmp_path)
    image_tar = _make_image_tar(tmp_path)
    containerfile = tmp_path / "Containerfile"
    containerfile.write_text("FROM base\n")
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"
    pack_bundle(out, rig_yaml, image_tar, containerfile)

    dest = tmp_path / "unpacked"
    result = unpack_bundle(out, dest)

    assert result["containerfile"] == dest / "Containerfile"
    assert result["containerfile"].read_text() == "FROM base\n"


def test_unpack_missing_image_tar_raises(tmp_path):
    # Hand-build a bundle that only has rig.yaml.
    rig_yaml, _ = _make_meta_file(tmp_path)
    out = tmp_path / f"broken{BUNDLE_SUFFIX}"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(rig_yaml, arcname="rig.yaml")

    with pytest.raises(ValueError):
        unpack_bundle(out, tmp_path / "unpacked")


def test_unpack_missing_rig_yaml_raises(tmp_path):
    image_tar = _make_image_tar(tmp_path)
    out = tmp_path / f"broken{BUNDLE_SUFFIX}"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(image_tar, arcname="image.tar")

    with pytest.raises(ValueError):
        unpack_bundle(out, tmp_path / "unpacked")


def test_read_bundle_meta(tmp_path):
    rig_yaml, meta = _make_meta_file(tmp_path)
    image_tar = _make_image_tar(tmp_path)
    out = tmp_path / f"myhack{BUNDLE_SUFFIX}"
    pack_bundle(out, rig_yaml, image_tar)

    loaded = read_bundle_meta(out)
    assert loaded == meta


def test_read_bundle_meta_missing_rig_yaml_raises(tmp_path):
    image_tar = _make_image_tar(tmp_path)
    out = tmp_path / f"broken{BUNDLE_SUFFIX}"
    with tarfile.open(out, "w:gz") as tar:
        tar.add(image_tar, arcname="image.tar")

    with pytest.raises(ValueError):
        read_bundle_meta(out)


def _add_bytes(tar, arcname, data):
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def test_unpack_rejects_dotdot_traversal(tmp_path):
    out = tmp_path / f"evil{BUNDLE_SUFFIX}"
    with tarfile.open(out, "w:gz") as tar:
        _add_bytes(tar, "rig.yaml", b"name: x\nkind: extended\n")
        _add_bytes(tar, "image.tar", b"img")
        _add_bytes(tar, "../evil", b"pwned")

    with pytest.raises(ValueError):
        unpack_bundle(out, tmp_path / "unpacked")


def test_unpack_rejects_absolute_member(tmp_path):
    out = tmp_path / f"evil{BUNDLE_SUFFIX}"
    with tarfile.open(out, "w:gz") as tar:
        _add_bytes(tar, "rig.yaml", b"name: x\nkind: extended\n")
        _add_bytes(tar, "image.tar", b"img")
        _add_bytes(tar, "/etc/evil", b"pwned")

    with pytest.raises(ValueError):
        unpack_bundle(out, tmp_path / "unpacked")
