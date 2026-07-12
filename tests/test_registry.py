"""Tests for kanibako.registry: OCI digest client (all network mocked)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.registry import (
    _fetch_manifest_digest,
    _get_anonymous_token,
    _parse_image_ref,
    get_remote_created,
    get_remote_digests,
    get_remote_tag_digest,
    list_remote_tags,
)


def _resp(headers=None, body=None):
    """Build a context-manager mock urlopen response."""
    r = MagicMock()
    r.headers = headers or {}
    if body is not None:
        r.read.return_value = json.dumps(body).encode()
    r.__enter__ = MagicMock(return_value=r)
    r.__exit__ = MagicMock(return_value=False)
    return r


_OCI_INDEX = "application/vnd.oci.image.index.v1+json"
_OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


class TestParseImageRef:
    def test_standard(self):
        reg, repo, tag = _parse_image_ref("ghcr.io/doctorjei/kanibako-oci:latest")
        assert reg == "ghcr.io"
        assert repo == "doctorjei/kanibako-oci"
        assert tag == "latest"

    def test_no_tag(self):
        reg, repo, tag = _parse_image_ref("ghcr.io/owner/repo")
        assert tag == "latest"

    def test_custom_tag(self):
        reg, repo, tag = _parse_image_ref("ghcr.io/owner/repo:v2.0")
        assert tag == "v2.0"

    def test_too_few_parts(self):
        with pytest.raises(ValueError, match="Cannot parse"):
            _parse_image_ref("localimage:latest")

    def test_nested_repo(self):
        reg, repo, tag = _parse_image_ref("ghcr.io/org/sub/repo:v1")
        assert reg == "ghcr.io"
        assert repo == "org/sub/repo"
        assert tag == "v1"


class TestGetAnonymousToken:
    def test_ghcr_returns_token(self):
        resp_data = {"token": "test-token-123"}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(resp_data).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.registry.urllib.request.urlopen", return_value=mock_resp):
            token = _get_anonymous_token("ghcr.io", "owner/repo")
        assert token == "test-token-123"

    def test_non_ghcr_returns_none(self):
        assert _get_anonymous_token("docker.io", "library/ubuntu") is None

    def test_url_construction(self):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"token": "t"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.registry.urllib.request.urlopen", return_value=mock_resp) as m:
            _get_anonymous_token("ghcr.io", "doctorjei/kanibako-oci")
            req = m.call_args[0][0]
            assert "scope=repository:doctorjei/kanibako-oci:pull" in req.full_url


class TestFetchManifestDigest:
    def test_returns_digest_header(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Docker-Content-Digest": "sha256:abc123"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.registry.urllib.request.urlopen", return_value=mock_resp):
            digest = _fetch_manifest_digest("ghcr.io", "owner/repo", "latest", "token")
        assert digest == "sha256:abc123"

    def test_no_token(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Docker-Content-Digest": "sha256:def456"}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.registry.urllib.request.urlopen", return_value=mock_resp) as m:
            digest = _fetch_manifest_digest("reg.io", "o/r", "v1", None)
            req = m.call_args[0][0]
            assert "Authorization" not in req.headers
        assert digest == "sha256:def456"

    def test_missing_header_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.headers = {}
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("kanibako.registry.urllib.request.urlopen", return_value=mock_resp):
            digest = _fetch_manifest_digest("ghcr.io", "o/r", "latest", "tok")
        assert digest is None


class TestGetRemoteDigests:
    """get_remote_digests resolves the acceptable per-arch digest set."""

    def test_index_returns_tag_and_matching_child(self):
        """Index tag -> {index digest, matching-arch child}; attestation skipped."""
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:4f49", "Content-Type": _OCI_INDEX},
        )
        index_body = {
            "manifests": [
                {"digest": "sha256:3de8",
                 "platform": {"os": "linux", "architecture": "amd64"}},
                {"digest": "sha256:arm",
                 "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": "sha256:attest",
                 "platform": {"os": "unknown", "architecture": "unknown"}},
            ]
        }
        get_body = _resp(body=index_body)

        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry.urllib.request.urlopen",
                  side_effect=[head, get_body]) as m,
        ):
            result = get_remote_digests("ghcr.io/o/r:latest", "linux/amd64")
        assert result == {"sha256:4f49", "sha256:3de8"}

        # Broadened Accept header on both the HEAD and the GET.
        for call in m.call_args_list:
            req = call[0][0]
            accept = req.headers["Accept"]
            assert "oci.image.index.v1+json" in accept
            assert "oci.image.manifest.v1+json" in accept
            assert "docker.distribution.manifest.list.v2+json" in accept
            assert "docker.distribution.manifest.v2+json" in accept

    def test_plain_manifest_returns_tag_only(self):
        """Plain manifest tag -> {tag digest}; no index GET performed."""
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:plain",
                     "Content-Type": _OCI_MANIFEST},
        )
        with (
            patch("kanibako.registry._get_anonymous_token", return_value=None),
            patch("kanibako.registry.urllib.request.urlopen",
                  side_effect=[head]) as m,
        ):
            result = get_remote_digests("ghcr.io/o/r:latest", "linux/amd64")
        assert result == {"sha256:plain"}
        # Only the HEAD request was made (no index GET).
        assert m.call_count == 1

    def test_index_no_matching_child_returns_tag_only(self):
        """Arch with no matching child -> {tag digest} only, no crash."""
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:4f49", "Content-Type": _OCI_INDEX},
        )
        index_body = {
            "manifests": [
                {"digest": "sha256:arm",
                 "platform": {"os": "linux", "architecture": "arm64"}},
            ]
        }
        get_body = _resp(body=index_body)
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry.urllib.request.urlopen",
                  side_effect=[head, get_body]),
        ):
            result = get_remote_digests("ghcr.io/o/r:latest", "linux/amd64")
        assert result == {"sha256:4f49"}

    def test_index_no_platform_skips_child_resolution(self):
        """Unknown local platform -> only the tag/index digest, no GET."""
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:4f49", "Content-Type": _OCI_INDEX},
        )
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry.urllib.request.urlopen",
                  side_effect=[head]) as m,
        ):
            result = get_remote_digests("ghcr.io/o/r:latest", None)
        assert result == {"sha256:4f49"}
        assert m.call_count == 1

    def test_failure_returns_empty_set(self):
        with patch("kanibako.registry._parse_image_ref", side_effect=OSError("boom")):
            assert get_remote_digests("ghcr.io/o/r:latest", "linux/amd64") == set()


class TestListRemoteTags:
    def test_returns_tag_list(self):
        body = _resp(body={"name": "o/r", "tags": ["1.0", "1.1", "latest"]})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry.urllib.request.urlopen", return_value=body) as m,
        ):
            tags = list_remote_tags("ghcr.io/o/r:latest")
        assert tags == ["1.0", "1.1", "latest"]
        # URL targets the tags/list endpoint on the repo (tag ignored).
        assert m.call_args[0][0].full_url == "https://ghcr.io/v2/o/r/tags/list"

    def test_filters_non_strings(self):
        body = _resp(body={"tags": ["1.0", 5, None, "latest"]})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value=None),
            patch("kanibako.registry.urllib.request.urlopen", return_value=body),
        ):
            assert list_remote_tags("ghcr.io/o/r:latest") == ["1.0", "latest"]

    def test_missing_tags_key(self):
        body = _resp(body={"name": "o/r"})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value=None),
            patch("kanibako.registry.urllib.request.urlopen", return_value=body),
        ):
            assert list_remote_tags("ghcr.io/o/r:latest") == []

    def test_network_error_returns_empty(self):
        with patch("kanibako.registry._parse_image_ref", side_effect=OSError("x")):
            assert list_remote_tags("ghcr.io/o/r:latest") == []

    def test_parse_error_returns_empty(self):
        assert list_remote_tags("localimage") == []


class TestGetRemoteTagDigest:
    def test_overrides_tag(self):
        head = _resp(headers={"Docker-Content-Digest": "sha256:v16"})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry.urllib.request.urlopen", return_value=head) as m,
        ):
            digest = get_remote_tag_digest("ghcr.io/o/r:latest", "1.6.0")
        assert digest == "sha256:v16"
        # The provided tag, not the ref's :latest, is fetched.
        assert m.call_args[0][0].full_url == "https://ghcr.io/v2/o/r/manifests/1.6.0"

    def test_failure_returns_none(self):
        with patch("kanibako.registry._parse_image_ref", side_effect=OSError("x")):
            assert get_remote_tag_digest("ghcr.io/o/r:latest", "1.6.0") is None


class TestGetRemoteCreated:
    def test_index_resolves_child_then_config(self):
        """Index tag -> child manifest -> config blob -> created."""
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:idx", "Content-Type": _OCI_INDEX},
        )
        index_body = _resp(body={
            "manifests": [
                {"digest": "sha256:child",
                 "platform": {"os": "linux", "architecture": "amd64"}},
            ]
        })
        manifest_body = _resp(body={"config": {"digest": "sha256:cfg"}})
        config_body = _resp(body={"created": "2026-06-01T00:00:00Z"})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch(
                "kanibako.registry.urllib.request.urlopen",
                side_effect=[head, index_body, manifest_body, config_body],
            ),
        ):
            created = get_remote_created("ghcr.io/o/r:latest", "linux/amd64")
        assert created == "2026-06-01T00:00:00Z"

    def test_plain_manifest_config(self):
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:m", "Content-Type": _OCI_MANIFEST},
        )
        manifest_body = _resp(body={"config": {"digest": "sha256:cfg"}})
        config_body = _resp(body={"created": "2026-01-02T03:04:05Z"})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value=None),
            patch(
                "kanibako.registry.urllib.request.urlopen",
                side_effect=[head, manifest_body, config_body],
            ),
        ):
            created = get_remote_created("ghcr.io/o/r:latest", "linux/amd64")
        assert created == "2026-01-02T03:04:05Z"

    def test_index_no_child_returns_none(self):
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:idx", "Content-Type": _OCI_INDEX},
        )
        index_body = _resp(body={"manifests": [
            {"digest": "sha256:arm",
             "platform": {"os": "linux", "architecture": "arm64"}},
        ]})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch(
                "kanibako.registry.urllib.request.urlopen",
                side_effect=[head, index_body],
            ),
        ):
            assert get_remote_created("ghcr.io/o/r:latest", "linux/amd64") is None

    def test_missing_created_returns_none(self):
        head = _resp(
            headers={"Docker-Content-Digest": "sha256:m", "Content-Type": _OCI_MANIFEST},
        )
        manifest_body = _resp(body={"config": {"digest": "sha256:cfg"}})
        config_body = _resp(body={"architecture": "amd64"})
        with (
            patch("kanibako.registry._get_anonymous_token", return_value=None),
            patch(
                "kanibako.registry.urllib.request.urlopen",
                side_effect=[head, manifest_body, config_body],
            ),
        ):
            assert get_remote_created("ghcr.io/o/r:latest", "linux/amd64") is None

    def test_failure_returns_none(self):
        with patch("kanibako.registry._parse_image_ref", side_effect=OSError("x")):
            assert get_remote_created("ghcr.io/o/r:latest", "linux/amd64") is None
