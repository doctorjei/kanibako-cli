"""SEQUESTERED 2026-08-01 (Phase 0, Jei ruling) — deprecation-tracking mechanism (W7).

⚑ **DORMANT SOURCE, kept for a designed future purpose — NOT live code.** This
module is post-public deprecation-policy infrastructure: correct, complete, and
deliberately unused until the project reaches the post-public era it was built
for. It is **not shipped in the wheel** (outside ``src/``), **not imported** by
anything, **not linted, not type-checked, and not collected** by pytest. It was
moved here from ``src/kanibako/deprecation.py`` in the Phase-0 retirement sweep,
which found zero production consumers; Jei ruled SEQUESTER rather than delete.

**Reactivation** (when the first real deprecation is declared): move this module
back to ``src/kanibako/deprecation.py``, move ``salvage/test_deprecations.py``
back to ``tests/``, and re-point ``docs/architecture.md`` (its policy section
records that the machinery lives here in the meantime). Nothing else is needed —
the code is unmodified from its last live state.

See ``salvage/README.md``.

---



A central in-process **registry** of deprecation records plus a ``@deprecated``
decorator and declarative ``register(...)`` helper, backing a CI/test **gate**
that fails the build once the project version reaches a record's ``remove_at``.

This exists to make post-public deprecations *removable in a reasonable
fashion* under the major-only breaking-change rule: every deprecation declares
when it was deprecated, the (next-major) version at/after which it MUST be gone,
and what to use instead.  The gate (``tests/test_deprecations.py``) enumerates
the registry and fails if any record is overdue, forcing the symbol's removal at
the right release.

Two ways to record a deprecation:

* ``@deprecated(...)`` — for callables (functions/methods).  Registers a record
  at decoration time (so the gate sees it even if the callable is never called)
  and emits a runtime ``DeprecationWarning`` on each call.
* ``register(...)`` — declarative, for things that are not decoratable callables
  (config keys, CLI flags, env vars, ...).

The registry starts **empty**: W2 clean-break-removed everything currently
deprecated, so with the real registry empty the gate passes trivially.  Add the
first real entry only when something is deprecated-not-removed post-public.

Version comparison is PEP 440 (``packaging.version.Version``), consistent with
the rest of the codebase.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from packaging.version import Version

__all__ = [
    "DeprecationRecord",
    "deprecated",
    "register",
    "registered_deprecations",
    "overdue_deprecations",
    "clear_registry",
]

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class DeprecationRecord:
    """One tracked deprecation.

    * ``name`` — human/symbol identifier (e.g. ``"kanibako.foo.bar"`` or
      ``"--legacy-flag"``).
    * ``deprecated_in`` — version the deprecation was announced in (e.g.
      ``"2.1.0"``).
    * ``remove_at`` — version at/after which the symbol MUST be gone; by
      convention the *next major* (e.g. ``"3.0.0"``).  The gate fails once
      ``__version__ >= remove_at`` and the record is still present.
    * ``replacement`` — what to use instead (free text); ``""`` if none.
    * ``kind`` — optional clarity tag (``"function"`` / ``"method"`` /
      ``"config-key"`` / ``"cli-flag"`` / ...).
    """

    name: str
    deprecated_in: str
    remove_at: str
    replacement: str = ""
    kind: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """Idempotency key: a record is identified by ``(name, remove_at)``."""
        return (self.name, self.remove_at)


# The module-level registry, keyed by ``DeprecationRecord.key`` so that
# re-importing a module whose top-level ``@deprecated`` decorators re-run does
# NOT create duplicate entries.
_REGISTRY: dict[tuple[str, str], DeprecationRecord] = {}


def register(
    name: str,
    *,
    deprecated_in: str,
    remove_at: str,
    replacement: str = "",
    kind: str = "",
) -> DeprecationRecord:
    """Declaratively register a deprecation record (for non-callables).

    Idempotent on ``(name, remove_at)``: re-registering the same key replaces
    the existing record rather than adding a duplicate.  Returns the stored
    record.
    """
    record = DeprecationRecord(
        name=name,
        deprecated_in=deprecated_in,
        remove_at=remove_at,
        replacement=replacement,
        kind=kind,
    )
    _REGISTRY[record.key] = record
    return record


def deprecated(
    *,
    deprecated_in: str,
    remove_at: str,
    replacement: str = "",
    name: str | None = None,
) -> Callable[[F], F]:
    """Mark a callable deprecated: register a record + warn on call.

    On decoration the record is added to the registry (so the gate sees it even
    if the callable is never invoked).  On each call a ``DeprecationWarning`` is
    emitted (``stacklevel=2`` so it points at the caller) naming the symbol, the
    ``remove_at`` version, and the replacement.  ``functools.wraps`` preserves
    the wrapped callable's metadata.
    """

    def decorate(func: F) -> F:
        symbol = name or _qualified_name(func)
        register(
            symbol,
            deprecated_in=deprecated_in,
            remove_at=remove_at,
            replacement=replacement,
            kind="function",
        )
        message = _warning_message(symbol, remove_at, replacement)

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            warnings.warn(message, DeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorate


def registered_deprecations() -> list[DeprecationRecord]:
    """Return all registered deprecation records (the gate reads this)."""
    return list(_REGISTRY.values())


def overdue_deprecations(current_version: str) -> list[DeprecationRecord]:
    """Return records where ``Version(current_version) >= Version(remove_at)``.

    These are the deprecations whose removal deadline has been reached: the
    symbol should already be gone.  The gate fails the build on a non-empty
    result.
    """
    current = Version(current_version)
    return [r for r in _REGISTRY.values() if current >= Version(r.remove_at)]


def clear_registry() -> None:
    """Empty the registry.  Intended for test isolation only."""
    _REGISTRY.clear()


def _qualified_name(func: Callable[..., Any]) -> str:
    module = getattr(func, "__module__", "") or ""
    qual = getattr(func, "__qualname__", None) or getattr(func, "__name__", repr(func))
    return f"{module}.{qual}" if module else str(qual)


def _warning_message(symbol: str, remove_at: str, replacement: str) -> str:
    msg = f"{symbol} is deprecated and will be removed in version {remove_at}."
    if replacement:
        msg += f" Use {replacement} instead."
    return msg


def format_overdue(records: Iterable[DeprecationRecord]) -> str:
    """Human-readable listing of overdue records, for the gate's failure text."""
    lines = []
    for r in records:
        repl = f" -> use {r.replacement}" if r.replacement else ""
        lines.append(
            f"  - {r.name} (deprecated in {r.deprecated_in}, "
            f"remove at {r.remove_at}){repl}"
        )
    return "\n".join(lines)


