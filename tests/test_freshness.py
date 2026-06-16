"""Tests for kanibako.freshness: per-arch digest intersection, caching, swallowing."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch


from kanibako.freshness import check_image_freshness, _cached_remote_digests


def _runtime(digests, platform="linux/amd64"):
    rt = MagicMock()
    rt.get_local_digests.return_value = list(digests)
    rt.get_local_platform.return_value = platform
    return rt


class TestCheckImageFreshness:
    def test_real_observed_data_no_warning(self, tmp_path, capsys):
        """Regression guard: local {3de8,4f49}, remote {3de8,4f49} -> no warn."""
        runtime = _runtime(["sha256:3de8", "sha256:4f49"])
        with patch(
            "kanibako.freshness._cached_remote_digests",
            return_value={"sha256:3de8", "sha256:4f49"},
        ):
            check_image_freshness(runtime, "img:latest", tmp_path)
        assert "newer version" not in capsys.readouterr().err

    def test_local_only_platform_child_no_warning(self, tmp_path, capsys):
        """local {3de8}, remote {4f49,3de8} -> intersection on 3de8 -> no warn."""
        runtime = _runtime(["sha256:3de8"])
        with patch(
            "kanibako.freshness._cached_remote_digests",
            return_value={"sha256:4f49", "sha256:3de8"},
        ):
            check_image_freshness(runtime, "img:latest", tmp_path)
        assert "newer version" not in capsys.readouterr().err

    def test_local_only_index_no_warning(self, tmp_path, capsys):
        """local {4f49}, remote {4f49,3de8} -> intersection on 4f49 -> no warn."""
        runtime = _runtime(["sha256:4f49"])
        with patch(
            "kanibako.freshness._cached_remote_digests",
            return_value={"sha256:4f49", "sha256:3de8"},
        ):
            check_image_freshness(runtime, "img:latest", tmp_path)
        assert "newer version" not in capsys.readouterr().err

    def test_genuinely_stale_warns(self, tmp_path, capsys):
        """Disjoint local/remote -> warning on stderr."""
        runtime = _runtime(["sha256:old3de8", "sha256:old4f49"])
        with patch(
            "kanibako.freshness._cached_remote_digests",
            return_value={"sha256:new4f49", "sha256:new3de8"},
        ):
            check_image_freshness(runtime, "img:latest", tmp_path)
        assert "newer version" in capsys.readouterr().err

    def test_empty_remote_silent_skip(self, tmp_path, capsys):
        """Empty remote set -> no crash, no warning."""
        runtime = _runtime(["sha256:3de8"])
        with patch("kanibako.freshness._cached_remote_digests", return_value=set()):
            check_image_freshness(runtime, "img:latest", tmp_path)
        assert "newer version" not in capsys.readouterr().err

    def test_no_local_digests_skips(self, tmp_path, capsys):
        """No local digests -> no remote query, no warning."""
        runtime = _runtime([])
        with patch("kanibako.freshness._cached_remote_digests") as m:
            check_image_freshness(runtime, "img:latest", tmp_path)
            m.assert_not_called()
        assert "newer version" not in capsys.readouterr().err

    def test_exception_swallowed(self, tmp_path, capsys):
        """Any exception is silently swallowed."""
        runtime = MagicMock()
        runtime.get_local_digests.side_effect = RuntimeError("boom")
        check_image_freshness(runtime, "img:latest", tmp_path)
        assert capsys.readouterr().err == ""


class TestCachedRemoteDigests:
    def test_cache_miss(self, tmp_path):
        """First call fetches from registry and writes cache."""
        with patch(
            "kanibako.freshness.get_remote_digests",
            return_value={"sha256:3de8", "sha256:4f49"},
        ) as m:
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:3de8", "sha256:4f49"}
        m.assert_called_once()

        cache = json.loads((tmp_path / "digest-cache.json").read_text())
        key = "img:latest\tlinux/amd64"
        assert sorted(cache[key]["digests"]) == ["sha256:3de8", "sha256:4f49"]

    def test_cache_hit(self, tmp_path):
        """Within TTL, returns cached value without network call."""
        cache = {
            "img:latest\tlinux/amd64": {
                "digests": ["sha256:cached"], "ts": time.time(),
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch("kanibako.freshness.get_remote_digests") as m:
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:cached"}
        m.assert_not_called()

    def test_cache_keyed_by_platform(self, tmp_path):
        """A different platform is a cache miss even for the same image."""
        cache = {
            "img:latest\tlinux/amd64": {
                "digests": ["sha256:amd"], "ts": time.time(),
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:arm"},
        ) as m:
            result = _cached_remote_digests("img:latest", "linux/arm64", tmp_path)
        assert result == {"sha256:arm"}
        m.assert_called_once()

    def test_cache_expired(self, tmp_path):
        """Expired cache triggers a new fetch."""
        cache = {
            "img:latest\tlinux/amd64": {
                "digests": ["sha256:old"], "ts": time.time() - 100000,
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:fresh"},
        ) as m:
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:fresh"}
        m.assert_called_once()

    def test_corrupt_cache_file(self, tmp_path):
        """Corrupt cache file is handled gracefully."""
        (tmp_path / "digest-cache.json").write_text("not json!")
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:ok"},
        ):
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:ok"}

    def test_incompatible_old_entry_shape_refetched(self, tmp_path):
        """A legacy single-digest entry shape is treated as a miss and overwritten."""
        # Old format used {"digest": "...", "ts": ...} with a different key.
        cache = {
            "img:latest\tlinux/amd64": {
                "digest": "sha256:legacy", "ts": time.time(),
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:new"},
        ) as m:
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:new"}
        m.assert_called_once()
        # Cache overwritten with the new list shape.
        written = json.loads((tmp_path / "digest-cache.json").read_text())
        assert written["img:latest\tlinux/amd64"]["digests"] == ["sha256:new"]
