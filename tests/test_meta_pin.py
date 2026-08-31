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

The file also pins the OTHER cross-package version contracts, which have the same
shape — one fact written in two files that nothing forced to agree:

* Each agent plugin carries its version in ``packages/agent-<name>/pyproject.toml``
  AND in ``.../src/kanibako/plugins/<name>/__init__.py``.  ``.bumpversion.cfg``
  stamps claude's pair only; goose and codex have neither stamped, so a bump that
  edits one file and forgets the other ships a wheel whose ``__version__`` lies.
  ``scripts/check-publish-collisions.py`` structurally cannot catch it — an
  unpublished version short-circuits before any content comparison.
* The meta package's floor for an independently-versioned plugin must not sit
  below that plugin's own version, or ``pip install kanibako`` can resolve a wheel
  older than the one this release needs.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

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
    assert Version("1.8.0rc1") > Version("1.8.0.dev98")
    assert max(
        map(Version, ["1.8.0.dev95", "1.8.0.dev98", "1.8.0rc1"]),
    ) == Version("1.8.0rc1")


# ---------------------------------------------------------------------------
# The two-file version pair every agent plugin carries.
# ---------------------------------------------------------------------------

# DISCOVERED, never listed: a fourth plugin is covered the day its directory
# lands, and a renamed one fails here rather than dropping silently out of the
# corpus.  `test_agent_plugin_packages_are_discovered` keeps that from emptying.
_AGENT_PYPROJECTS = sorted((REPO_ROOT / "packages").glob("agent-*/pyproject.toml"))


def _package_id(pyproject: Path) -> str:
    return pyproject.parent.name


def _project_table(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]


def _dunder_version(init_py: Path) -> str:
    """Read ``__version__`` out of a file by PARSING it — never by importing it.

    ``import kanibako.plugins.<name>`` resolves through ``sys.path``, where an
    editable or site-packages install of the same plugin can shadow the copy
    under ``packages/``.  The test would then read a DIFFERENT file than the one
    it names and pass while the repo is broken — the exact silence this test
    exists to remove.  Parsing addresses the file by path, so it cannot.
    """
    for node in ast.parse(init_py.read_text(encoding="utf-8"), str(init_py)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{init_py} declares no module-level __version__")


def _plugin_init(pyproject: Path) -> Path:
    """The one ``kanibako/plugins/<name>/__init__.py`` a distribution ships."""
    plugins = pyproject.parent / "src" / "kanibako" / "plugins"
    inits = sorted(plugins.glob("*/__init__.py"))
    assert len(inits) == 1, (
        f"{_package_id(pyproject)} ships {len(inits)} plugin packages under "
        f"{plugins}; the version pair assumes exactly one"
    )
    return inits[0]


def test_agent_plugin_packages_are_discovered() -> None:
    """The glob must find the plugins, or every test below passes vacuously.

    A parametrized test over an empty list collects nothing and reports green.
    This is the guard that makes the emptiness itself RED.
    """
    assert _AGENT_PYPROJECTS, (
        f"no packages/agent-*/pyproject.toml found under {REPO_ROOT / 'packages'} — "
        f"the version-pair tests would silently cover nothing"
    )


@pytest.mark.parametrize("pyproject", _AGENT_PYPROJECTS, ids=_package_id)
def test_plugin_version_pair_agrees(pyproject: Path) -> None:
    """A plugin's distribution version and its ``__version__`` must be identical.

    Nothing else forces them together: ``.bumpversion.cfg`` stamps claude's pair
    and neither goose's nor codex's, and the publish-collision check compares
    content only for a version that is ALREADY on PyPI — so a fresh number with a
    stale ``__version__`` sails straight through it.
    """
    init_py = _plugin_init(pyproject)
    declared = _project_table(pyproject)["version"]
    dunder = _dunder_version(init_py)
    assert declared == dunder, (
        f"{_package_id(pyproject)} version pair disagrees: "
        f"{pyproject.relative_to(REPO_ROOT)} says {declared!r} but "
        f"{init_py.relative_to(REPO_ROOT)} says {dunder!r} — bump both"
    )


# Plugins the release train does NOT stamp: their floor in the meta package is
# hand-maintained, which is what makes it drift.  Derived from `_STAMPED` rather
# than relisted, so the rule and not an inventory decides membership.
_INDEPENDENT_PYPROJECTS = [
    pyproject
    for pyproject in _AGENT_PYPROJECTS
    if _project_table(pyproject)["name"] not in _STAMPED
]


def _meta_floor(dist: str) -> Version:
    """The lower bound the meta package declares for ``dist``."""
    deps = _project_table(_META_PYPROJECT)["dependencies"]
    for raw in deps:
        req = Requirement(raw)
        if canonicalize_name(req.name) != canonicalize_name(dist):
            continue
        floors = [
            Version(spec.version) for spec in req.specifier if spec.operator == ">="
        ]
        assert len(floors) == 1, f"{dist} declares {len(floors)} lower bounds in meta"
        return floors[0]
    raise AssertionError(f"the meta package declares no dependency on {dist}")


def test_independent_plugins_are_discovered() -> None:
    """Same vacuity guard, for the filtered corpus below."""
    assert _INDEPENDENT_PYPROJECTS, (
        f"every packages/agent-* distribution is in _STAMPED {_STAMPED}; the meta "
        f"floor test below would cover nothing"
    )


@pytest.mark.parametrize("pyproject", _INDEPENDENT_PYPROJECTS, ids=_package_id)
def test_meta_floor_is_not_below_an_independent_plugins_own_version(
    pyproject: Path,
) -> None:
    """The meta must not floor an unstamped plugin below the version in tree.

    These are published on their own cadence, so their floors are typed by
    hand.  A floor left behind a bump lets ``pip install kanibako`` resolve the
    PREVIOUS wheel — content this release has already moved past — and the user
    gets a plugin that fails to import, which is the failure the meta package
    exists to prevent.

    ⚑ Deliberately one-sided.  A floor ABOVE the in-tree version is also wrong,
    but it announces itself at the first resolve; a floor below is silent.  The
    stamped train members are excluded because their in-tree floor is a ``.dev0``
    range ON PURPOSE — the workflow replaces it with ``==<VER>`` at build time.
    """
    dist = _project_table(pyproject)["name"]
    version = Version(_project_table(pyproject)["version"])
    floor = _meta_floor(dist)
    assert floor >= version, (
        f"{_META_PYPROJECT.relative_to(REPO_ROOT)} floors {dist} at {floor}, "
        f"below its own {version} — bump the floor with the package"
    )
