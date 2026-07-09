"""Contract test for the meta package's kanibako-cli dependency pin.

The `kanibako` meta package (``packages/meta/pyproject.toml``) depends on
``kanibako-cli``.  PyPI's simple index propagates the meta and cli packages
INDEPENDENTLY, so a range dependency lets ``pip install kanibako==1.7.0rcN``
resolve meta rcN + cli rcN-1 during the propagation window — a silent stale
pair.  The release workflow therefore stamps the dep to ``kanibako-cli==<VER>``
at build time (both the pre-release ``dev`` job and the prod ``promote`` job),
while the in-tree line stays a RANGE so a source checkout / dev flow never
breaks.

These tests pin the STAMP-ANCHOR CONTRACT: the exact regex the workflow ``sed``
matches must hit the kanibako-cli dep line (and ONLY it), and the in-tree line
must stay a range.  If someone reformats the dep line so the workflow anchor no
longer matches, this fails loudly instead of the workflow silently shipping an
unpinned meta.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# The EXACT anchor the release workflow's `sed -i -E` uses, translated to a
# Python regex:
#     sed -E "s|^([[:space:]]*)\"kanibako-cli[^\"]*\"|\1\"kanibako-cli==$VER\"|"
# POSIX ERE `[[:space:]]` on a line-leading indent is `[ \t]*` here.
_WORKFLOW_ANCHOR = re.compile(r'^([ \t]*)"kanibako-cli[^"]*"', re.MULTILINE)

_META_PYPROJECT = (
    Path(__file__).resolve().parents[1] / "packages" / "meta" / "pyproject.toml"
)


@pytest.fixture
def meta_text() -> str:
    return _META_PYPROJECT.read_text(encoding="utf-8")


def test_anchor_matches_exactly_one_line(meta_text: str) -> None:
    """The workflow anchor must match the kanibako-cli dep line and ONLY it.

    In particular it must NOT catch a ``kanibako-agent-*`` dep or the top-level
    ``version = "..."`` line — a second match would make the stamp ambiguous.
    """
    matches = _WORKFLOW_ANCHOR.findall(meta_text)
    assert len(matches) == 1, (
        f"expected exactly one kanibako-cli dep line matching the workflow "
        f"sed anchor, found {len(matches)}"
    )


def test_in_tree_dep_is_a_range_not_pinned(meta_text: str) -> None:
    """The committed line stays a RANGE so source/dev installs never break.

    The ``==`` pin is applied only at build time by the release workflow.
    """
    match = _WORKFLOW_ANCHOR.search(meta_text)
    assert match is not None
    line = match.group(0)
    assert ">=" in line, "in-tree kanibako-cli dep must remain a range (>=)"
    assert "==" not in line, (
        "in-tree kanibako-cli dep must NOT be hard-pinned (== is stamped only "
        "at build time; a committed pin would break source checkouts)"
    )


def test_stamp_produces_exact_pin_and_spares_agent_deps(meta_text: str) -> None:
    """Simulate the workflow sed: it must yield ``kanibako-cli==<VER>`` and
    leave the agent deps (``kanibako-agent-*``) untouched."""
    ver = "1.7.0rc9"
    stamped = _WORKFLOW_ANCHOR.sub(rf'\1"kanibako-cli=={ver}"', meta_text)
    assert f'"kanibako-cli=={ver}"' in stamped
    # Exactly one pinned line; the range is gone.
    assert stamped.count(f'"kanibako-cli=={ver}"') == 1
    assert ">=1.5.0.dev0" not in re.search(
        r'"kanibako-cli[^"]*"', stamped,
    ).group(0)
    # The independently-versioned agent deps keep their own floors.
    assert '"kanibako-agent-claude>=' in stamped
    assert '"kanibako-agent-goose>=' in stamped
    assert '"kanibako-agent-codex>=' in stamped
