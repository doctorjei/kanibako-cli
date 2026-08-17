"""Contract test for the meta package's STAMPED-TRAIN dependency pins.

The `kanibako` meta package (``packages/meta/pyproject.toml``) depends on
``kanibako-cli`` and ``kanibako-agent-claude``, both of which the release
workflow stamps to the SAME build version.  PyPI's simple index propagates
packages INDEPENDENTLY, so a range dependency lets ``pip install
kanibako==1.7.0rcN`` resolve meta rcN + cli rcN-1 during the propagation window
— a silent stale pair.  The release workflow therefore stamps those deps to
``<pkg>==<VER>`` at build time (both the pre-release ``dev`` job and the prod
``promote`` job), while the in-tree lines stay RANGES so a source checkout / dev
flow never breaks.

⚑⚑ AGENT-CLAUDE IS IN THIS SET, and this test file previously asserted the
opposite.  Pinning only the cli left agent-claude a range, and PEP 440 sorts a
release candidate ABOVE a dev release (``1.8.0rc1`` > ``1.8.0.dev98``) — so a
meta stamped at ``.devN`` resolved agent-claude to the older PUBLISHED
``1.8.0rc1`` and installed a plugin built against a different cli.  Measured
2026-08-17 on a real install: ``ImportError: cannot import name 'BindDefault'``
straight out of a clean ``uv tool install``.  The propagation-window argument was
always the same argument for agent-claude; it simply had never been applied to it.

``kanibako-agent-goose`` and ``kanibako-agent-codex`` are NOT in this set: they
version independently of the train and are never stamped, so their floors must
survive the stamp untouched.

These tests pin the STAMP-ANCHOR CONTRACT: the exact regex the workflow ``sed``
matches must hit each stamped dep line (and ONLY it), and the in-tree lines must
stay ranges.  If someone reformats a dep line so the workflow anchor no longer
matches, this fails loudly instead of the workflow silently shipping an unpinned
meta.
"""

from __future__ import annotations

import re

import pytest

from tests.support.repo import REPO_ROOT

# The EXACT anchor the release workflow's `sed -i -E` uses, translated to a
# Python regex.  The workflow loops this over each stamped package:
#     sed -E "s|^([[:space:]]*)\"${pkg}[^\"]*\"|\1\"${pkg}==$VER\"|"
# POSIX ERE `[[:space:]]` on a line-leading indent is `[ \t]*` here.
def _anchor(pkg: str) -> re.Pattern[str]:
    return re.compile(rf'^([ \t]*)"{re.escape(pkg)}[^"]*"', re.MULTILINE)


# Stamped to the build version by the release workflow — must be PINNED at build
# time.  Keep in sync with the workflow's `for pkg in ...` loops.
_STAMPED = ("kanibako-cli", "kanibako-agent-claude")

# Versioned independently of the train — never stamped; floors must survive.
_INDEPENDENT = ("kanibako-agent-goose", "kanibako-agent-codex")

_META_PYPROJECT = REPO_ROOT / "packages" / "meta" / "pyproject.toml"


@pytest.fixture
def meta_text() -> str:
    return _META_PYPROJECT.read_text(encoding="utf-8")


@pytest.mark.parametrize("pkg", _STAMPED)
def test_anchor_matches_exactly_one_line(meta_text: str, pkg: str) -> None:
    """Each stamped package's anchor must match its dep line and ONLY it.

    In particular ``kanibako-cli``'s anchor must not catch a ``kanibako-agent-*``
    dep, and ``kanibako-agent-claude``'s must not catch goose or codex — a second
    match would make the stamp ambiguous.
    """
    matches = _anchor(pkg).findall(meta_text)
    assert len(matches) == 1, (
        f"expected exactly one {pkg} dep line matching the workflow sed anchor, "
        f"found {len(matches)}"
    )


@pytest.mark.parametrize("pkg", _STAMPED)
def test_in_tree_dep_is_a_range_not_pinned(meta_text: str, pkg: str) -> None:
    """The committed lines stay RANGES so source/dev installs never break.

    The ``==`` pin is applied only at build time by the release workflow.
    """
    match = _anchor(pkg).search(meta_text)
    assert match is not None
    line = match.group(0)
    assert ">=" in line, f"in-tree {pkg} dep must remain a range (>=)"
    assert "==" not in line, (
        f"in-tree {pkg} dep must NOT be hard-pinned (== is stamped only at "
        f"build time; a committed pin would break source checkouts)"
    )


def test_stamp_pins_the_whole_train_and_spares_the_independent_agents(
    meta_text: str,
) -> None:
    """Simulate the workflow's loop over the stamped packages.

    Both train members come out pinned to the build version; goose and codex keep
    their own floors, because they are released on their own cadence and pinning
    them to a cli version would be a lie.
    """
    ver = "1.8.0.dev98"
    stamped = meta_text
    for pkg in _STAMPED:
        stamped = _anchor(pkg).sub(rf'\1"{pkg}=={ver}"', stamped)

    for pkg in _STAMPED:
        assert f'"{pkg}=={ver}"' in stamped, f"{pkg} was not pinned by the stamp"
        assert stamped.count(f'"{pkg}=={ver}"') == 1
        # The range is gone for that package.
        line = re.search(rf'"{re.escape(pkg)}[^"]*"', stamped).group(0)
        assert ">=" not in line, f"{pkg} still carries a range after stamping"

    for pkg in _INDEPENDENT:
        assert f'"{pkg}>=' in stamped, (
            f"{pkg} versions independently and must keep its floor through the "
            f"stamp — pinning it to a cli version would be false"
        )


def test_an_rc_outranks_a_dev_which_is_why_the_pin_is_required() -> None:
    """The ordering fact that makes a RANGE unsafe for a stamped package.

    Guards the reasoning itself: if this ever stopped being true, the pin could
    be relaxed.  While it holds, a ``.devN`` meta with a ranged agent-claude
    resolves to any published rc — which is exactly the 2026-08-17 failure.
    """
    from packaging.version import Version

    assert Version("1.8.0rc1") > Version("1.8.0.dev98")
    assert max(
        map(Version, ["1.8.0.dev95", "1.8.0.dev98", "1.8.0rc1"]),
    ) == Version("1.8.0rc1")
