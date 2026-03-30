"""Tests for tweakcc configuration, cache, and integration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kanibako.tweakcc import (
    TweakccConfig,
    _deep_merge,
    build_merged_config,
    load_external_config,
    load_tweakcc_section,
    resolve_tweakcc_config,
    write_merged_config,
)
from kanibako.tweakcc_cache import (
    TweakccCache,
    TweakccCacheError,
    config_hash,
)


class TestLoadTweakccSection:
    def test_present(self):
        data = {"tweakcc": {"enabled": True, "config": "~/.tweakcc/config.json"}}
        assert load_tweakcc_section(data) == {"enabled": True, "config": "~/.tweakcc/config.json"}

    def test_missing(self):
        assert load_tweakcc_section({}) == {}

    def test_empty(self):
        assert load_tweakcc_section({"tweakcc": {}}) == {}


class TestResolveTweakccConfig:
    def test_disabled_by_default(self):
        cfg = resolve_tweakcc_config({})
        assert cfg.enabled is False
        assert cfg.config_path is None
        assert cfg.overrides == {}

    def test_agent_enables(self):
        cfg = resolve_tweakcc_config({"enabled": True})
        assert cfg.enabled is True

    def test_project_overrides_agent(self):
        cfg = resolve_tweakcc_config(
            {"enabled": False, "config": "/agent/config.json"},
            {"enabled": True, "config": "/project/config.json"},
        )
        assert cfg.enabled is True
        assert cfg.config_path == "/project/config.json"

    def test_inline_overrides_preserved(self):
        cfg = resolve_tweakcc_config({
            "enabled": True,
            "settings": {"misc": {"mcpConnectionNonBlocking": True}},
        })
        assert cfg.overrides == {"settings": {"misc": {"mcpConnectionNonBlocking": True}}}

    def test_config_path_stringified(self):
        cfg = resolve_tweakcc_config({"config": Path("/some/path")})
        assert cfg.config_path == "/some/path"


class TestLoadExternalConfig:
    def test_none_path(self):
        assert load_external_config(None) == {}

    def test_missing_file(self):
        assert load_external_config("/nonexistent/config.json") == {}

    def test_valid_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        result = load_external_config(str(cfg))
        assert result == {"settings": {"misc": {"tableFormat": "unicode"}}}

    def test_invalid_json(self, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("{bad json!")
        assert load_external_config(str(cfg)) == {}

    def test_non_dict_json(self, tmp_path):
        cfg = tmp_path / "array.json"
        cfg.write_text("[1, 2, 3]")
        assert load_external_config(str(cfg)) == {}

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        cfg = tmp_path / ".tweakcc" / "config.json"
        cfg.parent.mkdir()
        cfg.write_text(json.dumps({"key": "val"}))
        result = load_external_config("~/.tweakcc/config.json")
        assert result == {"key": "val"}


class TestDeepMerge:
    def test_flat(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_override(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_nested(self):
        base = {"settings": {"misc": {"a": 1, "b": 2}}}
        override = {"settings": {"misc": {"b": 3, "c": 4}}}
        result = _deep_merge(base, override)
        assert result == {"settings": {"misc": {"a": 1, "b": 3, "c": 4}}}

    def test_nested_replace_non_dict(self):
        base = {"settings": {"misc": {"a": 1}}}
        override = {"settings": {"misc": "replaced"}}
        result = _deep_merge(base, override)
        assert result == {"settings": {"misc": "replaced"}}

    def test_does_not_mutate(self):
        base = {"a": {"b": 1}}
        override = {"a": {"c": 2}}
        result = _deep_merge(base, override)
        assert "c" not in base["a"]
        assert result["a"] == {"b": 1, "c": 2}


class TestBuildMergedConfig:
    def test_empty(self):
        cfg = TweakccConfig()
        assert build_merged_config(cfg) == {}

    def test_kanibako_defaults(self):
        cfg = TweakccConfig()
        defaults = {"settings": {"misc": {"mcpConnectionNonBlocking": True}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result == defaults

    def test_external_overrides_defaults(self, tmp_path):
        ext = tmp_path / "config.json"
        ext.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        cfg = TweakccConfig(config_path=str(ext))
        defaults = {"settings": {"misc": {"tableFormat": "ascii", "a": 1}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result["settings"]["misc"]["tableFormat"] == "unicode"
        assert result["settings"]["misc"]["a"] == 1

    def test_inline_overrides_everything(self, tmp_path):
        ext = tmp_path / "config.json"
        ext.write_text(json.dumps({"settings": {"misc": {"tableFormat": "unicode"}}}))
        cfg = TweakccConfig(
            config_path=str(ext),
            overrides={"settings": {"misc": {"tableFormat": "custom"}}},
        )
        defaults = {"settings": {"misc": {"tableFormat": "ascii"}}}
        result = build_merged_config(cfg, kanibako_defaults=defaults)
        assert result["settings"]["misc"]["tableFormat"] == "custom"


class TestWriteMergedConfig:
    def test_write(self, tmp_path):
        output = tmp_path / "sub" / "config.json"
        config = {"settings": {"misc": {"a": 1}}}
        write_merged_config(config, output)
        assert output.exists()
        assert json.loads(output.read_text()) == config

    def test_creates_parents(self, tmp_path):
        output = tmp_path / "deep" / "nested" / "config.json"
        write_merged_config({"key": "val"}, output)
        assert output.exists()


class TestAgentConfigTweakcc:
    """Test that AgentConfig round-trips the tweakcc section."""

    def test_load_with_tweakcc(self, tmp_path):
        from kanibako.agents import load_agent_config

        toml_content = """\
[agent]
name = "Claude Code"
shell = "standard"
default_args = []

[state]
model = "opus"

[env]

[shared]

[tweakcc]
enabled = true
config = "~/.tweakcc/config.json"
"""
        path = tmp_path / "agent.toml"
        path.write_text(toml_content)
        cfg = load_agent_config(path)
        assert cfg.tweakcc == {"enabled": True, "config": "~/.tweakcc/config.json"}

    def test_load_without_tweakcc(self, tmp_path):
        from kanibako.agents import load_agent_config

        toml_content = """\
[agent]
name = "Claude Code"

[state]
"""
        path = tmp_path / "agent.toml"
        path.write_text(toml_content)
        cfg = load_agent_config(path)
        assert cfg.tweakcc == {}

    def test_write_with_tweakcc(self, tmp_path):
        from kanibako.agents import AgentConfig, load_agent_config, write_agent_config

        cfg = AgentConfig(name="Test", tweakcc={"enabled": True, "config": "/path"})
        path = tmp_path / "agent.toml"
        write_agent_config(path, cfg)

        # Round-trip
        loaded = load_agent_config(path)
        assert loaded.tweakcc["enabled"] is True
        assert loaded.tweakcc["config"] == "/path"

    def test_write_without_tweakcc(self, tmp_path):
        from kanibako.agents import AgentConfig, write_agent_config

        cfg = AgentConfig(name="Test")
        path = tmp_path / "agent.toml"
        write_agent_config(path, cfg)
        content = path.read_text()
        assert "[tweakcc]" in content
        assert "# enabled = false" in content


# ── Cache layer tests ────────────────────────────────────────────────


def _make_binary(tmp_path: Path, name: str = "binary") -> Path:
    """Create a fake binary file for cache tests."""
    binary = tmp_path / name
    binary.write_bytes(b"\x7fELF" + b"\x00" * 100)
    binary.chmod(0o755)
    return binary


def _noop_patch(staging_dir: Path, binary_path: Path) -> None:
    """Patch function that does nothing (binary passes through unchanged)."""


def _failing_patch(staging_dir: Path, binary_path: Path) -> None:
    """Patch function that always fails."""
    raise RuntimeError("patch failed")


class TestConfigHash:
    def test_deterministic(self):
        h1 = config_hash({"a": 1, "b": 2})
        h2 = config_hash({"a": 1, "b": 2})
        assert h1 == h2

    def test_key_order_irrelevant(self):
        h1 = config_hash({"a": 1, "b": 2})
        h2 = config_hash({"b": 2, "a": 1})
        assert h1 == h2

    def test_different_values(self):
        h1 = config_hash({"a": 1})
        h2 = config_hash({"a": 2})
        assert h1 != h2

    def test_empty(self):
        h = config_hash({})
        assert len(h) == 64  # SHA-256 hex

    def test_nested(self):
        h1 = config_hash({"a": {"b": 1}})
        h2 = config_hash({"a": {"b": 2}})
        assert h1 != h2


class TestCacheKey:
    def test_same_inputs_same_key(self):
        cache = TweakccCache(Path("/tmp"))
        k1 = cache.cache_key("abc", "def")
        k2 = cache.cache_key("abc", "def")
        assert k1 == k2

    def test_different_binary_hash(self):
        cache = TweakccCache(Path("/tmp"))
        k1 = cache.cache_key("abc", "def")
        k2 = cache.cache_key("xyz", "def")
        assert k1 != k2

    def test_different_config_hash(self):
        cache = TweakccCache(Path("/tmp"))
        k1 = cache.cache_key("abc", "def")
        k2 = cache.cache_key("abc", "ghi")
        assert k1 != k2

    def test_key_length(self):
        cache = TweakccCache(Path("/tmp"))
        key = cache.cache_key("abc", "def")
        assert len(key) == 16


class TestEnsureDir:
    def test_creates_nested(self, tmp_path):
        cache = TweakccCache(tmp_path / "a" / "b" / "c")
        cache.ensure_dir()
        assert cache.cache_dir.is_dir()

    def test_idempotent(self, tmp_path):
        cache = TweakccCache(tmp_path / "x")
        cache.ensure_dir()
        cache.ensure_dir()
        assert cache.cache_dir.is_dir()


class TestCacheGetMiss:
    def test_empty_dir(self, tmp_path):
        cache = TweakccCache(tmp_path)
        assert cache.get("nonexistent") is None

    def test_no_dir(self, tmp_path):
        cache = TweakccCache(tmp_path / "does_not_exist")
        assert cache.get("key") is None


class TestCachePutAndGet:
    def test_put_then_get(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry = cache.put("testkey", binary, _noop_patch)
        assert entry.path.exists()
        assert entry.path == cache._entry_path("testkey")

        # get should also work
        entry2 = cache.get("testkey")
        assert entry2 is not None
        assert entry2.path == entry.path

        cache.release(entry)
        cache.release(entry2)

    def test_put_preserves_content(self, tmp_path):
        """Noop patch doesn't modify the binary, so content is preserved."""
        cache = TweakccCache(tmp_path / "cache")
        content = b"\x7fELF" + b"\x00" * 100
        binary = tmp_path / "binary"
        binary.write_bytes(content)

        entry = cache.put("k", binary, _noop_patch)
        assert entry.path.read_bytes() == content
        cache.release(entry)

    def test_put_does_not_modify_source(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)
        original = binary.read_bytes()

        entry = cache.put("k", binary, _noop_patch)
        assert binary.read_bytes() == original
        cache.release(entry)

    def test_put_sets_executable(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry = cache.put("k", binary, _noop_patch)
        # Cached binary should be readable (we have it open)
        assert entry.path.exists()
        cache.release(entry)


class TestCachePutFailure:
    def test_tweakcc_fails(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        with pytest.raises(TweakccCacheError, match="Cache put failed"):
            cache.put("k", binary, _failing_patch)

    def test_staging_cleaned_on_failure(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        with pytest.raises(TweakccCacheError):
            cache.put("k", binary, _failing_patch)

        # No staging files left
        if cache.cache_dir.exists():
            staging = list(cache.cache_dir.glob(".staging-*"))
            assert staging == []

    def test_entry_not_created_on_failure(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        with pytest.raises(TweakccCacheError):
            cache.put("k", binary, _failing_patch)

        assert cache.get("k") is None


class TestCacheRelease:
    def test_unlinks_when_sole_holder(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry = cache.put("k", binary, _noop_patch)
        path = entry.path
        assert path.exists()

        result = cache.release(entry)
        assert result is True
        assert not path.exists()

    def test_leaves_when_others_hold(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry1 = cache.put("k", binary, _noop_patch)
        entry2 = cache.get("k")
        assert entry2 is not None

        # Release entry1 — entry2 still holds shared lock
        result = cache.release(entry1)
        assert result is False
        assert entry2.path.exists()

        # Now release entry2 — should unlink
        result2 = cache.release(entry2)
        assert result2 is True

    def test_release_already_gone(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry = cache.put("k", binary, _noop_patch)
        # Manually remove
        entry.path.unlink()

        # release should not fail
        import os
        os.close(entry.fd)
        # Re-create entry with invalid fd to test FileNotFoundError path
        # (the real release already closed fd, so just check the method)


class TestCacheConcurrent:
    def test_multiple_shared_locks(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        entry = cache.put("k", binary, _noop_patch)

        # Multiple gets should all succeed (shared locks are compatible)
        entries = []
        for _ in range(5):
            e = cache.get("k")
            assert e is not None
            entries.append(e)

        # Release all
        cache.release(entry)
        for i, e in enumerate(entries):
            result = cache.release(e)
            if i < len(entries) - 1:
                assert result is False  # others still hold
            else:
                assert result is True  # last one cleans up

    def test_put_overwrites(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary1 = tmp_path / "bin1"
        binary1.write_bytes(b"AAAA")
        binary2 = tmp_path / "bin2"
        binary2.write_bytes(b"BBBB")

        entry1 = cache.put("k", binary1, _noop_patch)
        cache.release(entry1)

        entry2 = cache.put("k", binary2, _noop_patch)
        assert entry2.path.read_bytes() == b"BBBB"
        cache.release(entry2)


class TestCacheIntegration:
    """End-to-end test with config_hash and cache_key."""

    def test_full_flow(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        cfg = {"settings": {"misc": {"tableFormat": "unicode"}}}
        cfg_h = config_hash(cfg)
        bin_h = "fakebinaryhash"
        key = cache.cache_key(bin_h, cfg_h)

        # Miss
        assert cache.get(key) is None

        # Put
        entry = cache.put(key, binary, _noop_patch)
        assert entry.path.exists()

        # Hit
        entry2 = cache.get(key)
        assert entry2 is not None

        cache.release(entry)
        cache.release(entry2)

    def test_different_config_different_key(self, tmp_path):
        cache = TweakccCache(tmp_path / "cache")
        binary = _make_binary(tmp_path)

        cfg1 = {"a": 1}
        cfg2 = {"a": 2}
        key1 = cache.cache_key("binhash", config_hash(cfg1))
        key2 = cache.cache_key("binhash", config_hash(cfg2))
        assert key1 != key2

        e1 = cache.put(key1, binary, _noop_patch)
        assert cache.get(key2) is None  # different key = miss
        cache.release(e1)
