"""Tests for kanibako.registry: OCI digest client (all network mocked)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from kanibako.registry import (
    _fetch_manifest_digest,
    _get_anonymous_token,
    _parse_image_ref,
    get_remote_digest,
)


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


class TestGetRemoteDigest:
    def test_success(self):
        with (
            patch("kanibako.registry._parse_image_ref", return_value=("ghcr.io", "o/r", "latest")),
            patch("kanibako.registry._get_anonymous_token", return_value="tok"),
            patch("kanibako.registry._fetch_manifest_digest", return_value="sha256:abc"),
        ):
            assert get_remote_digest("ghcr.io/o/r:latest") == "sha256:abc"

    def test_network_error(self):
        with patch("kanibako.registry._parse_image_ref", side_effect=OSError("timeout")):
            assert get_remote_digest("ghcr.io/o/r:latest") is None

    def test_parse_error(self):
        assert get_remote_digest("localimage") is None
