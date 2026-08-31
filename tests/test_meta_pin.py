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

* Every distribution that SHIPS CODE carries its version twice — the cli in
  ``pyproject.toml`` and ``src/kanibako/__init__.py``, each plugin in
  ``packages/agent-<name>/pyproject.toml`` and
  ``.../src/kanibako/plugins/<name>/__init__.py``.  ``.bumpversion.cfg`` stamps
  the cli's pair and claude's and neither goose's nor codex's, so a bump that
  edits one file and forgets the other ships a wheel whose ``__version__`` lies.
  ``scripts/check-publish-collisions.py`` structurally cannot catch it — an
  unpublished version short-circuits before any content comparison.
  ⚑ Being STAMPED is not being ASSERTED: agent-claude was stamped too, and its
  pair still needed this test.  A stamp is a convention a hand edit, a merge or a
  config change steps around in silence.
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
# The two-file version pair every distribution that ships code carries.
# ---------------------------------------------------------------------------

# DISCOVERED, never listed: a fourth plugin is covered the day its directory
# lands, and a renamed one fails here rather than dropping silently out of the
# corpus.  `test_agent_plugin_packages_are_discovered` keeps that from emptying.
_AGENT_PYPROJECTS = sorted((REPO_ROOT / "packages").glob("agent-*/pyproject.toml"))

# Every DISTRIBUTION in the tree, from the two places a pyproject can live: the
# repo root (the cli itself) and one directory per package.
_DISTRIBUTIONS = [
    REPO_ROOT / "pyproject.toml",
    *sorted((REPO_ROOT / "packages").glob("*/pyproject.toml")),
]

# ...of which the ones that SHIP CODE are the ones carrying a version twice.  The
# membership rule is "has a src/ tree", never a list of names: `packages/meta` is
# a dependency shell with no source, so it has no second copy to disagree with,
# and a distribution that grows one joins this corpus by EXISTING rather than by
# somebody remembering to add it here.
_VERSIONED_PYPROJECTS = [p for p in _DISTRIBUTIONS if (p.parent / "src").is_dir()]


def _dist_id(pyproject: Path) -> str:
    """The DECLARED distribution name — stable where a directory name is not.

    The ONE id spelling in this file, because the repo root's directory name is
    whatever the checkout was called: it cannot identify the cli the way
    ``packages/agent-claude`` identifies claude, and two id spellings would leave
    the next reader picking the one that breaks on the root.
    """
    return _project_table(pyproject)["name"]


def _project_table(pyproject: Path) -> dict:
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]


def _dunder_version(module: Path) -> str | None:
    """Read ``__version__`` out of a file by PARSING it — never by importing it.

    ``import kanibako.plugins.<name>`` resolves through ``sys.path``, where an
    editable or site-packages install of the same distribution can shadow the
    copy under ``packages/``.  The test would then read a DIFFERENT file than the
    one it names and pass while the repo is broken — the exact silence this test
    exists to remove.  Parsing addresses the file by path, so it cannot.

    ``None`` means the module declares none, which is how :func:`_version_module`
    tells a version carrier from any other ``__init__.py``.
    """
    for node in ast.parse(module.read_text(encoding="utf-8"), str(module)).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            return ast.literal_eval(node.value)
    return None


def _version_module(pyproject: Path) -> Path:
    """The one module under a distribution's ``src/`` that declares ``__version__``.

    Found by the DECLARATION rather than by a path shape, so the cli's
    ``src/kanibako/__init__.py`` and a plugin's
    ``src/kanibako/plugins/<name>/__init__.py`` are one rule instead of two.
    Exactly one: zero would leave that distribution's pair silently unasserted,
    and two make "the pair" ambiguous.
    """
    src = pyproject.parent / "src"
    carriers = sorted(
        p for p in src.rglob("__init__.py") if _dunder_version(p) is not None
    )
    assert len(carriers) == 1, (
        f"{_dist_id(pyproject)} declares __version__ in {len(carriers)} modules "
        f"under {src} ({[str(p.relative_to(src)) for p in carriers]}); the "
        f"version pair assumes exactly one"
    )
    return carriers[0]


def test_agent_plugin_packages_are_discovered() -> None:
    """The glob must find the plugins, or the corpora built from it cover nothing.

    A parametrized test over an empty list collects nothing and reports green.
    This is the guard that makes the emptiness itself RED.
    """
    assert _AGENT_PYPROJECTS, (
        f"no packages/agent-*/pyproject.toml found under {REPO_ROOT / 'packages'} — "
        f"the version-pair tests would silently cover nothing"
    )


def test_versioned_distributions_are_discovered() -> None:
    """The pair corpus must hold the cli AND every agent plugin.

    Same vacuity guard as above, plus the half a plain "non-empty" would miss: a
    corpus that quietly lost the repo-root distribution still collects three
    plugins and reports green over exactly the row this test was widened to add.
    """
    assert REPO_ROOT / "pyproject.toml" in _VERSIONED_PYPROJECTS, (
        f"the repo-root distribution is not in the version-pair corpus — no "
        f"{REPO_ROOT / 'src'} tree, so the cli's own pair is unasserted again"
    )
    missing = sorted(set(_AGENT_PYPROJECTS) - set(_VERSIONED_PYPROJECTS))
    assert not missing, (
        f"agent packages missing from the version-pair corpus: {missing} — each "
        f"ships code, so each must carry a src/ tree the corpus can find"
    )


@pytest.mark.parametrize("pyproject", _VERSIONED_PYPROJECTS, ids=_dist_id)
def test_version_pair_agrees(pyproject: Path) -> None:
    """A distribution's declared version and its ``__version__`` must be identical.

    Nothing else forces them together: ``.bumpversion.cfg`` stamps the cli's pair
    and claude's and neither goose's nor codex's, and the publish-collision check
    compares content only for a version that is ALREADY on PyPI — so a fresh
    number with a stale ``__version__`` sails straight through it.

    ⚑ The stamped pairs are in this corpus BECAUSE they are stamped, not despite
    it.  agent-claude was stamped too and drifted anyway, which is why this test
    exists at all: a stamp is a convention that a hand edit, a merge or an edit to
    ``.bumpversion.cfg`` itself steps around in silence.
    """
    module = _version_module(pyproject)
    declared = _project_table(pyproject)["version"]
    dunder = _dunder_version(module)
    assert declared == dunder, (
        f"{_dist_id(pyproject)} version pair disagrees: "
        f"{pyproject.relative_to(REPO_ROOT)} says {declared!r} but "
        f"{module.relative_to(REPO_ROOT)} says {dunder!r} — bump both"
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


@pytest.mark.parametrize("pyproject", _INDEPENDENT_PYPROJECTS, ids=_dist_id)
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
    dist = _dist_id(pyproject)
    version = Version(_project_table(pyproject)["version"])
    floor = _meta_floor(dist)
    assert floor >= version, (
        f"{_META_PYPROJECT.relative_to(REPO_ROOT)} floors {dist} at {floor}, "
        f"below its own {version} — bump the floor with the package"
    )
