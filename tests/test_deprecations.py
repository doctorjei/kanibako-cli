"""Tests for kanibako.deprecation: registry, decorator, and the CI gate (W7).

The CI gate (``test_no_overdue_deprecations``) reads the REAL registry +
``__version__`` and fails the build once any registered deprecation reaches its
``remove_at``.  The unit tests below exercise the mechanism against an ISOLATED
registry (snapshot/restore fixture) so their example records never leak into the
real registry and trip the gate.
"""

from __future__ import annotations

import warnings

import pytest

from kanibako import __version__
from kanibako import deprecation
from kanibako.deprecation import (
    DeprecationRecord,
    deprecated,
    overdue_deprecations,
    register,
    registered_deprecations,
)


@pytest.fixture
def isolated_registry():
    """Snapshot the real registry, give the test a clean one, then restore.

    Guarantees example/temp records registered by a unit test cannot leak into
    the process-wide registry that the real gate test inspects.
    """
    saved = dict(deprecation._REGISTRY)
    deprecation._REGISTRY.clear()
    try:
        yield deprecation._REGISTRY
    finally:
        deprecation._REGISTRY.clear()
        deprecation._REGISTRY.update(saved)


# --------------------------------------------------------------------------- #
# The CI gate
# --------------------------------------------------------------------------- #
def test_no_overdue_deprecations():
    """Gate: no registered deprecation may be overdue at the current version.

    Passes trivially while the real registry is empty.  When this fails, the
    listed symbol(s) have reached their ``remove_at`` version and MUST be
    removed from the codebase (and their registry entry deleted).
    """
    overdue = overdue_deprecations(__version__)
    assert not overdue, (
        "These deprecations are overdue for removal at version "
        f"{__version__} -- delete the symbol(s) and their registry entries:\n"
        + deprecation.format_overdue(overdue)
    )


def test_real_registry_starts_empty():
    """W7 ships an empty real registry (W2 already removed all deprecations)."""
    assert registered_deprecations() == []


# --------------------------------------------------------------------------- #
# @deprecated decorator
# --------------------------------------------------------------------------- #
def test_decorator_registers_on_decoration(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0", replacement="new_fn")
    def old_fn(x):
        return x * 2

    records = registered_deprecations()
    assert len(records) == 1
    rec = records[0]
    assert rec.deprecated_in == "2.1.0"
    assert rec.remove_at == "3.0.0"
    assert rec.replacement == "new_fn"
    assert rec.kind == "function"
    assert "old_fn" in rec.name


def test_decorator_warns_on_call_and_returns_value(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0", replacement="new_fn")
    def old_fn(x):
        return x * 2

    with pytest.warns(DeprecationWarning, match="3.0.0"):
        result = old_fn(21)
    assert result == 42


def test_decorator_warning_mentions_symbol_and_replacement(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0", replacement="shiny()")
    def old_fn():
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_fn()
    assert len(caught) == 1
    text = str(caught[0].message)
    assert "old_fn" in text
    assert "3.0.0" in text
    assert "shiny()" in text


def test_decorator_preserves_metadata(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0")
    def old_fn(x):
        """Original docstring."""
        return x

    assert old_fn.__name__ == "old_fn"
    assert old_fn.__doc__ == "Original docstring."


def test_decorator_explicit_name(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0", name="public.api.thing")
    def _impl():
        return None

    assert registered_deprecations()[0].name == "public.api.thing"


def test_decorator_no_replacement_omits_use_clause(isolated_registry):
    @deprecated(deprecated_in="2.1.0", remove_at="3.0.0")
    def old_fn():
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_fn()
    assert "Use " not in str(caught[0].message)


# --------------------------------------------------------------------------- #
# Declarative register()
# --------------------------------------------------------------------------- #
def test_register_adds_record_and_enumerates(isolated_registry):
    register(
        "legacy.config.key",
        deprecated_in="2.1.0",
        remove_at="3.0.0",
        replacement="new.config.key",
        kind="config-key",
    )
    records = registered_deprecations()
    assert len(records) == 1
    assert records[0].name == "legacy.config.key"
    assert records[0].kind == "config-key"


def test_register_is_idempotent_on_key(isolated_registry):
    for _ in range(3):
        register("--legacy-flag", deprecated_in="2.1.0", remove_at="3.0.0")
    assert len(registered_deprecations()) == 1


def test_register_different_remove_at_is_distinct(isolated_registry):
    register("thing", deprecated_in="2.1.0", remove_at="3.0.0")
    register("thing", deprecated_in="2.5.0", remove_at="4.0.0")
    assert len(registered_deprecations()) == 2


# --------------------------------------------------------------------------- #
# overdue_deprecations (PEP 440 comparison)
# --------------------------------------------------------------------------- #
def test_overdue_flags_record_at_or_past_remove_at(isolated_registry):
    register("old", deprecated_in="2.1.0", remove_at="3.0.0")
    assert [r.name for r in overdue_deprecations("3.0.0")] == ["old"]
    assert [r.name for r in overdue_deprecations("3.1.0")] == ["old"]


def test_overdue_ignores_record_before_remove_at(isolated_registry):
    register("future", deprecated_in="2.1.0", remove_at="3.0.0")
    assert overdue_deprecations("2.9.0") == []
    assert overdue_deprecations("2.1.0") == []


def test_overdue_pep440_prerelease_below_major(isolated_registry):
    """A pre-release of the remove_at major is NOT yet at the deadline."""
    register("x", deprecated_in="2.1.0", remove_at="3.0.0")
    # 3.0.0rc1 < 3.0.0 under PEP 440 -> not overdue.
    assert overdue_deprecations("3.0.0rc1") == []
    # ...but 3.0.0 final and beyond ARE.
    assert len(overdue_deprecations("3.0.0")) == 1


def test_overdue_mixed_registry(isolated_registry):
    register("gone", deprecated_in="1.0.0", remove_at="2.0.0")
    register("safe", deprecated_in="1.5.0", remove_at="9.0.0")
    overdue = overdue_deprecations("2.0.0")
    assert [r.name for r in overdue] == ["gone"]


# --------------------------------------------------------------------------- #
# DeprecationRecord
# --------------------------------------------------------------------------- #
def test_record_key():
    rec = DeprecationRecord(name="n", deprecated_in="1.0.0", remove_at="2.0.0")
    assert rec.key == ("n", "2.0.0")
