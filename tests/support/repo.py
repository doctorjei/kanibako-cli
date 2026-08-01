"""The repo root, resolved once.

``tests/support/`` sits two levels under the repo root, so :data:`REPO_ROOT` is
correct no matter how deep the IMPORTING test file lives — which is the point:
test files move between directories (the package-ification pass mirrors the
source tree into ``tests/test_<pkg>/``), this file does not.

Before this existed, five tests each re-derived the root by counting directory
levels (``Path(__file__).resolve().parents[1]``).  That is one rule copied five
times, and every copy silently encodes the copier's own depth.
"""

from __future__ import annotations

from pathlib import Path

#: The repository root — the directory holding ``src/``, ``tests/``, ``packages/``
#: and ``pyproject.toml``.
REPO_ROOT = Path(__file__).resolve().parents[2]
