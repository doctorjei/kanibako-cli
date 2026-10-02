"""Tests for :class:`kanibako.settings.config.BootstrapConfig`'s frozen-by-construction field.

⚑ WHY THIS FILE EXISTS SEPARATELY: it is one new test module about ONE class, kept out of
``test_config.py`` so it stays disjoint from that file's other edits.
"""

from __future__ import annotations

from types import MappingProxyType

import pytest

from kanibako.settings.config import (
    BootstrapConfig,
    load_config,
    write_global_config,
)
from kanibako.settings.config_io import write_nested_key
from kanibako.settings.paths import resolve_system_paths


def _written_config(tmp_path, *, agents: str = "/custom/agents"):
    """A Layer-1 file written exactly the way the existing readers write one."""
    path = tmp_path / "config.cfg"
    write_global_config(path)
    write_nested_key(path, ("config",), "agents", agents)
    return path


class TestConfigPathsIsReadOnly:
    def test_item_assignment_raises_type_error(self):
        """MUTATION: drop the ``__post_init__`` proxy and this passes again.

        ``frozen=True`` blocked only the REBIND; the field itself stayed a live ``dict``,
        so a frozen instance was mutated from outside (P8, "copy OUT at the boundary").
        """
        b = BootstrapConfig(config_paths={"config.agents": "/a"})

        with pytest.raises(TypeError):
            b.config_paths["config.agents"] = "/evil"  # type: ignore[index]

    def test_item_deletion_raises_type_error(self):
        """Deleting a key is a write too, and the proxy refuses it as well."""
        b = BootstrapConfig(config_paths={"config.agents": "/a"})

        with pytest.raises(TypeError):
            del b.config_paths["config.agents"]

    def test_constructor_does_not_alias_the_caller_dict(self):
        """MUTATION: keep the caller's dict (no ``dict(...)`` copy) and this passes again.

        The copy is what breaks the alias: the ``MappingProxyType`` alone would freeze a
        LIVE dict the caller still holds a reference to.
        """
        source = {"config.agents": "/a"}
        b = BootstrapConfig(config_paths=source)

        source["config.agents"] = "/aliased"

        assert b.config_paths["config.agents"] == "/a"

    def test_rebinding_the_field_is_still_refused(self):
        """The pre-existing ``frozen`` guarantee is NOT traded away for the proxy."""
        b = BootstrapConfig(config_paths={"config.agents": "/a"})

        with pytest.raises(Exception) as exc:  # FrozenInstanceError
            b.config_paths = {}  # type: ignore[misc]
        assert type(exc.value).__name__ == "FrozenInstanceError"

    def test_the_stored_mapping_is_a_proxy_over_a_private_copy(self):
        """It is a copy wrapped read-only — not the caller's own dict handed back."""
        source = {"config.agents": "/a"}
        b = BootstrapConfig(config_paths=source)

        assert isinstance(b.config_paths, MappingProxyType)
        assert b.config_paths is not source


class TestLoadConfigStillReads:
    def test_a_written_global_config_loads_empty(self, tmp_path):
        """A ``write_global_config`` file still loads as the EMPTY foundation it writes."""
        path = tmp_path / "config.cfg"
        write_global_config(path)

        cfg = load_config(path)

        assert isinstance(cfg, BootstrapConfig)
        assert cfg.config_paths == {}

    def test_a_populated_file_still_reads_back(self, tmp_path):
        """The read the existing readers rely on (``[...]`` and ``.get``) is unchanged."""
        path = _written_config(tmp_path)

        cfg = load_config(path)

        assert cfg.config_paths["config.agents"] == "/custom/agents"
        assert cfg.config_paths.get("config.agents") == "/custom/agents"
        assert cfg.config_paths.get("config.nope") is None
        assert cfg.config_paths == {"config.agents": "/custom/agents"}

    def test_the_mapping_feeds_resolve_system_paths(self, tmp_path):
        """``tests/conftest.py`` hands this field straight to ``resolve_system_paths``.

        Its signature takes ``Mapping[str, str]`` and it copies before resolving, so a
        read-only proxy is a legal argument. This pins that from the producer's side.
        """
        cfg = load_config(_written_config(tmp_path))

        resolved = resolve_system_paths(
            cfg.config_paths, data_home=tmp_path / "data", home=tmp_path / "home",
        )

        assert resolved["config.agents"].as_posix() == "/custom/agents"


class TestEqualityAndRepr:
    def test_two_empty_instances_are_equal(self):
        """``BootstrapConfig() == BootstrapConfig()`` — the dataclass ``__eq__`` survives.

        ``MappingProxyType`` compares equal to the ``dict`` it wraps, so no ``__eq__``
        override is needed; pinning it here means a future change that breaks it (or one
        that "fixes" it by dropping ``frozen``) has to face this test.
        """
        assert BootstrapConfig() == BootstrapConfig()

    def test_two_equal_populated_instances_are_equal(self):
        a = BootstrapConfig(config_paths={"config.agents": "/a"})
        b = BootstrapConfig(config_paths={"config.agents": "/a"})

        assert a == b

    def test_differing_values_are_not_equal(self):
        a = BootstrapConfig(config_paths={"config.agents": "/a"})
        b = BootstrapConfig(config_paths={"config.agents": "/b"})

        assert a != b

    def test_the_default_is_an_empty_mapping(self):
        """``BootstrapConfig()`` still works, and it is an empty (read-only) mapping."""
        b = BootstrapConfig()

        assert dict(b.config_paths) == {}
        assert b == BootstrapConfig(config_paths={})

    def test_repr_stays_usable(self):
        """``repr`` renders the key and its value — it names the value, not just the type."""
        text = repr(BootstrapConfig(config_paths={"config.agents": "/a"}))

        assert "BootstrapConfig" in text
        assert "config.agents" in text
        assert "/a" in text
