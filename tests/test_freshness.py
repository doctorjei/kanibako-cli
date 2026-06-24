"""Tests for kanibako.freshness: two-prong (version-first, dates) staleness.

The check warns ONLY when it can prove the remote ``:latest`` is newer than
local, and stays silent on every uncertainty. All registry/runtime calls are
mocked.
"""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

from kanibako.freshness import (
    _cached_remote_created,
    _cached_remote_digests,
    _cached_remote_tags,
    _cached_tag_digest,
    _parse_ts,
    check_image_freshness,
)


def _runtime(
    *,
    local_digests=("sha256:localchild", "sha256:localindex"),
    platform="linux/amd64",
    label=None,
    tags=None,
    created=None,
):
    rt = MagicMock()
    rt.get_local_digests.return_value = list(local_digests)
    rt.get_local_platform.return_value = platform
    rt.get_local_label.return_value = label
    rt.get_local_tags.return_value = list(tags) if tags is not None else []
    rt.get_local_created.return_value = created
    return rt


def _err(capsys):
    return capsys.readouterr().err


# ---------------------------------------------------------------------------
# Prong 1: semver
# ---------------------------------------------------------------------------


class TestProngVersion:
    def test_remote_newer_warns(self, tmp_path, capsys):
        """Local 1.5.3 label, remote :latest == 1.6.0 tag -> warn."""
        rt = _runtime(label="1.5.3")
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.3", "1.6.0", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                side_effect=lambda img, tag, cp: {
                    "1.6.0": "sha256:latest",
                    "1.5.3": "sha256:old",
                }.get(tag),
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        err = _err(capsys)
        assert "newer version" in err
        # Banner points at the new 'rig update' verb, not 'rig prep --force'.
        assert "kanibako rig update" in err
        assert "prep --force" not in err

    def test_remote_older_silent_dev_case(self, tmp_path, capsys):
        """Our dev25 local vs prod 1.5.x remote :latest -> silent (remote<local)."""
        rt = _runtime(label="1.6.0.dev25")
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:prodlatest"},
            ),
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.2", "1.5.3", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                side_effect=lambda img, tag, cp: {
                    "1.5.3": "sha256:prodlatest",
                    "1.5.2": "sha256:older",
                }.get(tag),
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_remote_equal_silent(self, tmp_path, capsys):
        """Local version == remote :latest version -> silent."""
        rt = _runtime(label="1.6.0")
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.6.0", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                side_effect=lambda img, tag, cp: {"1.6.0": "sha256:latest"}.get(tag),
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_local_version_from_cotag(self, tmp_path, capsys):
        """No label; local version resolved from a PEP440 RepoTag co-tag."""
        rt = _runtime(label=None, tags=["img:latest", "img:1.5.3"])
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.3", "1.6.0", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                side_effect=lambda img, tag, cp: {
                    "1.6.0": "sha256:latest",
                }.get(tag),
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" in _err(capsys)

    def test_local_version_from_local_digest_match(self, tmp_path, capsys):
        """No label/co-tag; local version = remote version-tag matching our digest."""
        rt = _runtime(local_digests=["sha256:mine"], label=None, tags=["img:latest"])
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.3", "1.6.0", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                side_effect=lambda img, tag, cp: {
                    "1.6.0": "sha256:latest",
                    "1.5.3": "sha256:mine",
                }.get(tag),
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        # local resolves to 1.5.3, remote :latest is 1.6.0 -> warn
        assert "newer version" in _err(capsys)


# ---------------------------------------------------------------------------
# Prong 2: dates (fallback when a version can't resolve on either side)
# ---------------------------------------------------------------------------


class TestProngDates:
    def _no_version(self):
        return (
            patch("kanibako.freshness._cached_remote_tags", return_value=[]),
            patch("kanibako.freshness._cached_tag_digest", return_value=None),
        )

    def test_remote_date_newer_warns(self, tmp_path, capsys):
        rt = _runtime(label=None, created="2026-01-01T00:00:00Z")
        t1, t2 = self._no_version()
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            t1,
            t2,
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-06-01T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" in _err(capsys)

    def test_remote_date_older_silent(self, tmp_path, capsys):
        rt = _runtime(label=None, created="2026-06-01T00:00:00Z")
        t1, t2 = self._no_version()
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            t1,
            t2,
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-01-01T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_remote_date_equal_silent(self, tmp_path, capsys):
        rt = _runtime(label=None, created="2026-06-01T00:00:00Z")
        t1, t2 = self._no_version()
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            t1,
            t2,
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-06-01T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_zeroed_reproducible_dates_silent(self, tmp_path, capsys):
        """Epoch-zero reproducible-build stamps are treated as absent -> silent."""
        rt = _runtime(label=None, created="1970-01-01T00:00:00Z")
        t1, t2 = self._no_version()
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            t1,
            t2,
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-06-01T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)


# ---------------------------------------------------------------------------
# Indeterminate / safety
# ---------------------------------------------------------------------------


class TestIndeterminateSilent:
    def test_no_version_no_dates_silent(self, tmp_path, capsys):
        rt = _runtime(label=None, created=None)
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch("kanibako.freshness._cached_remote_tags", return_value=[]),
            patch("kanibako.freshness._cached_tag_digest", return_value=None),
            patch("kanibako.freshness._cached_remote_created", return_value=None),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_remote_unfetchable_silent(self, tmp_path, capsys):
        """Empty remote digest set (can't reach registry) -> silent."""
        rt = _runtime()
        with patch(
            "kanibako.freshness._cached_remote_digests", return_value=set()
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_custom_image_unresolvable_silent(self, tmp_path, capsys):
        """A custom local image (no version, no remote tags) never nags."""
        rt = _runtime(label=None, tags=["mycustom:dev"], created=None)
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            patch("kanibako.freshness._cached_remote_tags", return_value=[]),
            patch("kanibako.freshness._cached_tag_digest", return_value=None),
            patch("kanibako.freshness._cached_remote_created", return_value=None),
        ):
            check_image_freshness(rt, "mycustom:dev", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_exception_swallowed(self, tmp_path, capsys):
        rt = MagicMock()
        rt.get_local_digests.side_effect = RuntimeError("boom")
        check_image_freshness(rt, "img:latest", tmp_path)
        assert _err(capsys) == ""

    def test_only_local_version_resolves_stays_silent(self, tmp_path, capsys):
        """Local version known, remote version unresolved -> silent (no dates).

        Dates are NOT consulted once either side has a version: a resolvable
        local version that we can't *prove* is behind is treated as up to date,
        so a newer remote ``created`` stamp must not nag.
        """
        rt = _runtime(label="1.5.0", created="2026-01-01T00:00:00Z")
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:latest"},
            ),
            # remote :latest digest matches no version tag -> no remote version
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.0", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                return_value="sha256:unrelated",
            ),
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-06-01T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)

    def test_local_dev_retag_vs_rebuilt_prod_latest_silent(self, tmp_path, capsys):
        """Real false-positive: a locally-built/retagged dev image vs a remote
        prod ``:latest`` that was rebuilt more recently (same prod version).

        The local image carries a higher dev version label but is NOT remote
        ``:latest`` (digests disjoint), and remote ``:latest``'s digest matches
        no probed version tag -> remote version unresolved. The local build is
        older by *date* than the freshly-rebuilt remote prod image. This must
        stay silent: 1.6.0.dev25 is not behind 1.5.3, and dates must not lie.
        """
        rt = _runtime(
            local_digests=["sha256:devbuild"],
            label="1.6.0.dev25",
            tags=["img:latest"],
            created="2026-05-01T00:00:00Z",  # local built earlier
        )
        with (
            patch(
                "kanibako.freshness._cached_remote_digests",
                return_value={"sha256:prodlatest"},
            ),
            # remote :latest digest matches none of the probed version tags
            patch(
                "kanibako.freshness._cached_remote_tags",
                return_value=["1.5.2", "1.5.3", "latest"],
            ),
            patch(
                "kanibako.freshness._cached_tag_digest",
                return_value="sha256:unrelated",
            ),
            # remote prod :latest rebuilt more recently than the local build
            patch(
                "kanibako.freshness._cached_remote_created",
                return_value="2026-06-20T00:00:00Z",
            ),
        ):
            check_image_freshness(rt, "img:latest", tmp_path)
        assert "newer version" not in _err(capsys)


class TestParseTs:
    def test_z_suffix(self):
        dt = _parse_ts("2026-06-01T00:00:00Z")
        assert dt is not None and dt.year == 2026

    def test_offset(self):
        assert _parse_ts("2026-06-01T00:00:00+00:00") is not None

    def test_epoch_is_none(self):
        assert _parse_ts("1970-01-01T00:00:00Z") is None

    def test_empty_is_none(self):
        assert _parse_ts(None) is None
        assert _parse_ts("") is None

    def test_unparseable_is_none(self):
        assert _parse_ts("not a date") is None


# ---------------------------------------------------------------------------
# Cache (digest set) — preserved behavior
# ---------------------------------------------------------------------------


class TestCachedRemoteDigests:
    def test_cache_miss(self, tmp_path):
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
        (tmp_path / "digest-cache.json").write_text("not json!")
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:ok"},
        ):
            result = _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        assert result == {"sha256:ok"}

    def test_incompatible_old_entry_shape_refetched(self, tmp_path):
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
        written = json.loads((tmp_path / "digest-cache.json").read_text())
        assert written["img:latest\tlinux/amd64"]["digests"] == ["sha256:new"]

    def test_sibling_fields_preserved(self, tmp_path):
        """Refetching digests keeps sibling cache fields (tags/created) intact."""
        cache = {
            "img:latest\tlinux/amd64": {
                "created": {"value": "2026-06-01T00:00:00Z", "ts": time.time()},
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch(
            "kanibako.freshness.get_remote_digests", return_value={"sha256:x"},
        ):
            _cached_remote_digests("img:latest", "linux/amd64", tmp_path)
        written = json.loads((tmp_path / "digest-cache.json").read_text())
        entry = written["img:latest\tlinux/amd64"]
        assert entry["digests"] == ["sha256:x"]
        assert entry["created"]["value"] == "2026-06-01T00:00:00Z"


class TestCachedFieldHelpers:
    def test_tags_miss_then_hit(self, tmp_path):
        with patch(
            "kanibako.freshness.list_remote_tags", return_value=["1.0", "latest"],
        ) as m:
            assert _cached_remote_tags("img:latest", tmp_path) == ["1.0", "latest"]
            # Second call is served from cache, no refetch.
            assert _cached_remote_tags("img:latest", tmp_path) == ["1.0", "latest"]
        m.assert_called_once()

    def test_tag_digest_caches_none(self, tmp_path):
        """A None result is cached to avoid hammering a failing endpoint."""
        with patch(
            "kanibako.freshness.get_remote_tag_digest", return_value=None,
        ) as m:
            assert _cached_tag_digest("img:latest", "1.0", tmp_path) is None
            assert _cached_tag_digest("img:latest", "1.0", tmp_path) is None
        m.assert_called_once()

    def test_tag_digest_per_tag_keyed(self, tmp_path):
        with patch(
            "kanibako.freshness.get_remote_tag_digest",
            side_effect=lambda img, tag: f"sha256:{tag}",
        ) as m:
            assert _cached_tag_digest("img:latest", "1.0", tmp_path) == "sha256:1.0"
            assert _cached_tag_digest("img:latest", "2.0", tmp_path) == "sha256:2.0"
        assert m.call_count == 2

    def test_created_cached(self, tmp_path):
        with patch(
            "kanibako.freshness.get_remote_created",
            return_value="2026-06-01T00:00:00Z",
        ) as m:
            v = _cached_remote_created("img:latest", "linux/amd64", tmp_path)
            assert v == "2026-06-01T00:00:00Z"
            _cached_remote_created("img:latest", "linux/amd64", tmp_path)
        m.assert_called_once()

    def test_field_cache_expires(self, tmp_path):
        cache = {
            "img:latest\t": {
                "tags": {"value": ["old"], "ts": time.time() - 100000},
            }
        }
        (tmp_path / "digest-cache.json").write_text(json.dumps(cache))
        with patch(
            "kanibako.freshness.list_remote_tags", return_value=["new"],
        ) as m:
            assert _cached_remote_tags("img:latest", tmp_path) == ["new"]
        m.assert_called_once()
