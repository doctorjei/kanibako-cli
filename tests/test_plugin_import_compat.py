"""LEGACY PLUGIN IMPORT PATHS — the CLEAN-BREAK contract.

The package-ification pass (v1.8.0) moved four core modules that the first-party
agent plugins import.  For a while each old flat path kept a re-export shim, and
this file pinned the opposite of what it pins now: that the legacy paths still
imported, re-exported the same objects, and warned.

**v1.8.0 is a deliberate clean break — no aliases, no deprecation window — and a
re-export shim IS a deprecation window.**  The four shims are DELETED (Jei,
2026-08-17: *"I would prefer we delete them. We have the old versions in our
source."*).  Their code is preserved in git history; nothing ships to hold their
place.  This file now pins the break:

* the legacy dotted path raises ``ModuleNotFoundError`` — it is gone, not
  hollowed out, not aliased, and no file re-appears at the old location;
* the NEW path imports and is silent (no warning leaked out of the retirement);
* nothing in the shipped tree still imports a legacy path;
* and the composition that makes the break SURVIVABLE: an installed plugin too
  old for this core fails inside ``ep.load()``, and plugin discovery degrades to
  a NAMED warning with a working CLI rather than a raw traceback.

⚑ **Why that last one is the load-bearing test.** A deleted module's own error
(``No module named 'kanibako.agent_defaults'``) names the missing path but NOT
the plugin that reached for it, and the user did not type that import — a
package they installed did.  ``kanibako.targets.discover_targets`` is the only
place that knows both, so the quality of the whole break rests there.

⚑ **HOUSE RULE, learned the hard way: never ``importlib.reload()`` a real module
in-process — use a fresh subprocess.**  Reload rebinds a module's attributes to
NEW objects while every module that already did ``from X import Y`` keeps the
OLD ones, so ``is`` comparisons start failing ACROSS FILES.  An earlier version
of this file reloaded ``kanibako.settings.settings_resolve`` to observe an import
warning; that rebound ``UNSET`` (an identity-compared sentinel) and
``SettingsError`` (module-scope-imported by ~8 modules) and broke **49 tests in
``tests/test_settings/``** in a single-process run.  The per-file capped runner
is blind to this by construction; CI's one-process ``pytest tests/`` is not.
Detector:

    ~/.venv/bin/pytest tests/test_plugin_import_compat.py tests/test_settings/ -q

⚑ **Do not "fix" a failure here by re-adding a shim.**  A red test in this file
means either a legacy path came back or an in-tree caller regressed onto one;
both are the bug, not the pin.  The user-facing half is ``MIGRATION.md``,
*"Core module paths moved (package-ification)"*.
"""

from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.support.repo import REPO_ROOT

# (legacy module, new module, names the PUBLISHED plugin wheels import from it)
#
# The name lists are transcribed from the v1.8.0-rc1 plugin sources, i.e. the code
# inside the wheels that are already on PyPI.  They no longer pin a re-export
# surface — nothing is re-exported — but they are kept because they record WHICH
# published wheels break and on WHAT, which is exactly what MIGRATION.md has to
# tell a user.  Deleting them would throw away the measurement.
_SHIMS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "kanibako.vscode_config",
        "kanibako.vscode.vscode_config",
        (
            "CodexModelProvider",
            "seed_session_start_hook",
            "seed_codex_approval",
            "seed_codex_config",
            "seed_goose_mode",
        ),
    ),
    (
        "kanibako.settings_resolve",
        "kanibako.settings.settings_resolve",
        (
            # claude/target.py:187 and codex/target.py:331 in the published wheels.
            "GUEST_HOME",
        ),
    ),
    (
        # ⚑ The one that breaks the most installs: all three published plugins
        # import it at MODULE SCOPE (claude:13, codex:51, goose:9), so an old
        # wheel fails before it can define its Target at all.
        "kanibako.agent_defaults",
        "kanibako.settings.agent_defaults",
        (
            "load_category_binds",
            "load_descriptor",
            "load_common",
        ),
    ),
    (
        "kanibako.agent_config",
        "kanibako.settings.agent_config",
        (
            # TYPE_CHECKING import + the generate_agent_config body, all three.
            "AgentConfig",
        ),
    ),
]

_IDS = [legacy for legacy, _, _ in _SHIMS]


# --------------------------------------------------------------------------- #
# The break itself                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_legacy_path_raises_module_not_found(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """The old dotted path is GONE — importing it raises, naming the path."""
    with pytest.raises(ModuleNotFoundError) as excinfo:
        importlib.import_module(legacy)
    assert legacy in str(excinfo.value), str(excinfo.value)


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_the_package_itself_still_imports(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """Anti-vacuity: the MODULE is missing, not the package holding it.

    Without this, a broken ``kanibako`` package would satisfy the test above for
    entirely the wrong reason — every import would raise and the suite would read
    the catastrophe as a clean retirement.
    """
    package, _, _leaf = legacy.rpartition(".")
    assert importlib.import_module(package) is not None


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_no_file_remains_at_the_legacy_location(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """No stub, no hollow module, no ``__init__`` package — the file is deleted.

    ``import`` failing is the behaviour; an absent FILE is the state that
    guarantees it.  Checked separately because a stray ``.py`` at the old path
    that merely re-raises would pass the import test while re-opening exactly the
    deprecation window this release refused to ship.
    """
    relative = Path(*legacy.split(".")).with_suffix(".py")
    stale = REPO_ROOT / "src" / relative
    assert not stale.exists(), (
        f"{stale} came back — v1.8.0 deletes the flat shims outright, and a "
        f"replacement stub at the old path is the deprecation window this "
        f"release deliberately does not ship."
    )


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_new_path_imports_and_carries_the_names(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """The replacement module exists and still exposes what the wheels reach for.

    The names are the published plugins' import list.  They must live SOMEWHERE
    after the move, or the migration instruction (*"switch to the new path"*)
    would be advice that does not work.
    """
    mod = importlib.import_module(new)
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, (
        f"{new} does not provide {missing} — MIGRATION.md tells plugin authors to "
        f"switch to this module, so the names have to be here."
    )


# --------------------------------------------------------------------------- #
# Import-time WARNING behaviour — measured in FRESH SUBPROCESSES               #
# --------------------------------------------------------------------------- #
#
# ⚑ NEVER `importlib.reload()` A REAL MODULE IN-PROCESS.  See the module
# docstring: reloading `kanibako.settings.settings_resolve` rebinds `UNSET` (a
# sentinel compared with `is`) and `SettingsError` (a class imported at module
# scope by ~8 modules), which poisoned 49 tests across `tests/test_settings/` in
# a single-process run.  Import-time behaviour is measured in a fresh subprocess;
# nothing here mutates the parent interpreter's module state.


def _import_in_subprocess(module: str, warning_flag: str) -> subprocess.CompletedProcess:
    """Import *module* in a virgin interpreter under ``-W`` *warning_flag*."""
    return subprocess.run(
        [sys.executable, "-W", warning_flag, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        timeout=60,
    )


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_new_path_does_not_warn(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """The replacement module is SILENT.

    The retired shims warned on import.  Nothing of that may have leaked into the
    real module: correctly-updated code must not be nagged for doing the right
    thing.  ``-W error::FutureWarning`` turns any leak into a nonzero exit.
    """
    proc = _import_in_subprocess(new, "error::FutureWarning")
    assert proc.returncode == 0, (
        f"importing {new} raised under -W error::FutureWarning — a retirement "
        f"warning has leaked into the real module:\n{proc.stderr}"
    )


# --------------------------------------------------------------------------- #
# Nothing in the shipped tree still reaches for a legacy path                  #
# --------------------------------------------------------------------------- #


def _shipped_sources() -> list[Path]:
    """Every shipped python file: the core package plus each plugin's own src tree.

    ``build/`` holds stale wheel-build copies and ``.claude/`` may hold another
    agent's live worktree — neither is shipped source and neither is ours.
    """
    files = list((REPO_ROOT / "src" / "kanibako").rglob("*.py"))
    for pkg in sorted((REPO_ROOT / "packages").glob("*/src")):
        files.extend(pkg.rglob("*.py"))
    return [f for f in files if "build" not in f.parts and ".claude" not in f.parts]


def test_the_source_scan_is_not_vacuous() -> None:
    """Guard the guard: an empty file list would make the scan below pass for free."""
    files = _shipped_sources()
    assert len(files) > 50, len(files)
    assert {"cli.py", "errors.py"} <= {f.name for f in files}


def test_no_shipped_module_imports_a_legacy_path() -> None:
    """The core and the in-repo plugins use the NEW paths, everywhere.

    An AST walk over ``import``/``from … import`` rather than a text grep, so a
    mention in a docstring or a comment (this file's own table, for one) cannot
    produce a phantom hit.
    """
    legacy = {name for name, _, _ in _SHIMS}
    offenders: list[str] = []
    for path in _shipped_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                hit = [a.name for a in node.names if a.name in legacy]
            elif isinstance(node, ast.ImportFrom):
                hit = [node.module] if node.module in legacy else []
            else:
                continue
            for name in hit:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {name}")
    assert not offenders, (
        "shipped source still imports a DELETED flat module path:\n  "
        + "\n  ".join(offenders)
        + "\nThe replacements are in kanibako.settings.* / kanibako.vscode.*."
    )


# --------------------------------------------------------------------------- #
# The composition that makes the break survivable                             #
# --------------------------------------------------------------------------- #


class TestAnOldPluginDegradesInsteadOfBricking:
    """An installed plugin too old for this core must cost the user that ONE agent.

    This is the end-to-end half of the clean break, and the only place the two
    mechanisms meet: the DELETION produces the ``ModuleNotFoundError``, and the
    ``ep.load()`` guard (``625e342``) turns it into a named, actionable warning
    instead of a raw traceback out of ``discover_targets``.  Neither is sufficient
    alone — the exception names the missing MODULE but not the PLUGIN, and the
    guard is only as useful as what it is handed.

    ⚑ The failure here is REAL, not simulated: ``load`` genuinely imports the
    deleted path.  A ``side_effect=ModuleNotFoundError(...)`` would keep passing
    if someone restored a shim, which is precisely the regression worth catching.
    """

    @pytest.fixture(autouse=True)
    def _clear_warn_dedupe(self):
        # The warning is once-per-process, so reset the memo or test ORDER decides
        # whether this test sees the message at all.
        from kanibako.targets import _EP_LOAD_FAILED

        _EP_LOAD_FAILED.clear()
        yield
        _EP_LOAD_FAILED.clear()

    @staticmethod
    def _stale_plugin(agent: str, legacy_module: str) -> MagicMock:
        """An entry point whose load() really imports a module this release deleted."""
        ep = MagicMock()
        ep.name = agent
        ep.dist.name = f"kanibako-agent-{agent}"
        ep.load.side_effect = lambda: importlib.import_module(legacy_module)
        return ep

    @staticmethod
    def _healthy_plugin(agent: str) -> MagicMock:
        ep = MagicMock()
        ep.name = agent
        ep.dist.name = f"kanibako-agent-{agent}"
        ep.load.return_value = object
        return ep

    @pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
    def test_discovery_survives_and_the_other_agents_live(
        self, legacy: str, new: str, names: tuple[str, ...], capsys
    ) -> None:
        """Discovery returns, the stale agent is absent, every other agent works."""
        from kanibako.targets import discover_targets

        stale = self._stale_plugin("goose", legacy)
        healthy = self._healthy_plugin("claude")
        with patch("kanibako.targets.entry_points", return_value=[stale, healthy]):
            targets = discover_targets()
        capsys.readouterr()
        assert "goose" not in targets
        assert "claude" in targets, "a healthy agent must not be taken down with the stale one"

    @pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
    def test_the_warning_names_the_plugin_the_module_and_a_way_forward(
        self, legacy: str, new: str, names: tuple[str, ...], capsys
    ) -> None:
        """What the user actually sees has to be enough to act on.

        Four things, each of which someone needs: WHO failed (the distribution —
        the module name alone does not tell you what to uninstall), WHAT failed
        (the deleted path, so the report is diagnosable), that the rest of the CLI
        including ``kanibako setup`` still works, and where the cure is written
        down now that no shim carries it.
        """
        from kanibako.targets import discover_targets

        stale = self._stale_plugin("goose", legacy)
        with patch("kanibako.targets.entry_points", return_value=[stale]):
            discover_targets()
        err = capsys.readouterr().err

        assert "kanibako-agent-goose" in err, err  # WHO
        assert "ModuleNotFoundError" in err, err  # WHAT
        assert legacy in err, err  # which path
        assert "SKIPPED" in err, err
        assert "kanibako setup" in err, err  # what still works
        # The cure lives in the migration guide now that no shim carries it, so
        # the notice has to hand over a term that finds it.
        assert "MIGRATION.md" in err, err

    def test_a_stale_plugin_does_not_stop_the_cli_from_reporting_agents(self) -> None:
        """The regression in one line: discovery RETURNS rather than raising."""
        from kanibako.targets import discover_targets

        stale = self._stale_plugin("goose", "kanibako.agent_defaults")
        with patch("kanibako.targets.entry_points", return_value=[stale]):
            targets = discover_targets()
        assert isinstance(targets, dict)
