"""LEGACY PLUGIN IMPORT PATHS — the shim contract.

The package-ification pass (v1.8.0) moved four core modules that the first-party
agent plugins import.  The plugins depend on ``kanibako-cli`` with **no upper
bound** (``packages/agent-*/pyproject.toml``), and ``kanibako-agent-goose``
0.3.0 / ``kanibako-agent-codex`` 0.3.0 are FINAL on PyPI — so an already-published
plugin WILL be installed beside the new base.  If a legacy path stops resolving,
agent detection raises at import and the box launches with no agent.

Each moved module therefore keeps a re-export shim at its old flat path.  This
file is the machine-checked half of that contract (``MIGRATION.md`` §3.1 is the
human half):

* the legacy dotted path still imports;
* every name the shim re-exports is the SAME OBJECT as the one in the new
  module — a copy would drift, and an ``isinstance``/identity check somewhere in
  the plugin would then fail in a way no import test would catch;
* the specific names the PUBLISHED plugin wheels import are present by name, so
  narrowing a shim's surface fails here rather than at a user's launch.

* importing a legacy path emits an actionable ``FutureWarning``, and importing
  the REAL module emits nothing (stage 1 of the two-stage retirement).

⚑ **HOUSE RULE, learned the hard way: never ``importlib.reload()`` a real module
in-process — use a fresh subprocess.** Reload rebinds a module's attributes to
NEW objects while every module that already did ``from X import Y`` keeps the
OLD ones, so ``is`` comparisons start failing ACROSS FILES. An earlier version of
this file reloaded ``kanibako.settings.settings_resolve`` to observe its import
warning; that rebound ``UNSET`` (an identity-compared sentinel) and
``SettingsError`` (module-scope-imported by ~8 modules) and broke **49 tests in
``tests/test_settings/``** in a single-process run. The per-file capped runner is
blind to this by construction; CI's one-process ``pytest tests/`` is not. See the
block above ``_import_in_subprocess`` for the detector command.

⚑ When the removal gate in ``MIGRATION.md`` §3.1 fires (all three plugins
published against the new paths), delete the shim AND its row here — do not
weaken this file to keep it passing.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support.repo import REPO_ROOT

# (legacy module, new module, names the PUBLISHED plugin wheels import from it)
#
# The name lists are transcribed from the v1.8.0-rc1 plugin sources, i.e. the
# code inside the wheels that are already on PyPI — not from the in-repo
# plugins, which this pass updated to the new paths.
_SHIMS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "kanibako.vscode_config",
        "kanibako.vscode.vscode_config",
        (
            "CodexModelProvider",
            "clear_claude_bypass_permissions",
            "seed_claude_bypass_permissions",
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
        # ⚑ The one that matters most: all three published plugins import this
        # at MODULE SCOPE (claude:13, codex:51, goose:9).  Break it and NO agent
        # plugin imports at all.
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


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_legacy_path_imports(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """The old dotted path still resolves to a module."""
    assert importlib.import_module(legacy) is not None
    assert importlib.import_module(new) is not None


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_published_plugin_names_are_present(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """Every name a published plugin wheel imports from the legacy path exists."""
    mod = importlib.import_module(legacy)
    missing = [n for n in names if not hasattr(mod, n)]
    assert not missing, (
        f"{legacy} no longer re-exports {missing} — a published plugin importing "
        f"them will fail at agent detection.  See MIGRATION.md §3.1."
    )


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_shim_re_exports_are_the_same_objects(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """The shim re-exports, it does not re-implement.

    Identity, not equality: a shim that defined its own copy of a function or
    enum would pass an existence check and still break an ``is``/``isinstance``
    comparison inside the plugin or core.
    """
    legacy_mod = importlib.import_module(legacy)
    new_mod = importlib.import_module(new)
    for name in names:
        assert getattr(legacy_mod, name) is getattr(new_mod, name), (
            f"{legacy}.{name} is not the same object as {new}.{name} — the shim "
            f"must re-export, never re-implement."
        )


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_shim_covers_the_whole_public_surface(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """The shim re-exports EVERY public name of the new module.

    The published first-party plugins use a known subset, but a third-party
    plugin may use any of it.  Covering the whole surface makes the shim's
    correctness independent of what anyone happens to import.
    """
    legacy_mod = importlib.import_module(legacy)
    public = _defined_public_names(new)
    assert public, f"{new} exposes no public names — the filter is wrong, not the shim"
    missing = sorted(n for n in public if not hasattr(legacy_mod, n))
    assert not missing, f"{legacy} does not re-export {missing} from {new}"


# --------------------------------------------------------------------------- #
# Import-time WARNING behaviour — measured in FRESH SUBPROCESSES
# --------------------------------------------------------------------------- #
#
# ⚑ NEVER `importlib.reload()` A REAL MODULE IN-PROCESS.  A warning only fires
# on the FIRST import, so testing it needs a virgin interpreter state — and
# `reload` is the wrong way to get one.  Reload re-executes the module body and
# REBINDS its module-level attributes to NEW objects, while every other module
# that already did `from X import Y` keeps holding the OLD ones.  The two then
# fail `is` comparisons.  That is not hypothetical and it is not confined to
# this file: reloading `kanibako.settings.settings_resolve` rebinds `UNSET` (a
# sentinel compared with `is`) and `SettingsError` (a class imported at module
# scope by ~8 modules), which poisoned 49 tests across `tests/test_settings/`
# in a single-process run.
#
# ⚑ AND THE PER-FILE CAPPED RUNNER CANNOT SEE IT.  It runs one pytest per file,
# so cross-file contamination is invisible BY CONSTRUCTION — a green run of the
# standard gate says nothing about this class of bug.  CI runs `pytest tests/`
# in ONE process, which is where it surfaced.  The detector is a bounded
# single-process run:
#
#     ~/.venv/bin/pytest tests/test_plugin_import_compat.py tests/test_settings/ -q
#
# The rule this file now follows: **import-time behaviour is measured in a fresh
# subprocess; nothing here mutates the parent interpreter's module state.**


def _import_in_subprocess(module: str, warning_flag: str) -> subprocess.CompletedProcess:
    """Import *module* in a virgin interpreter under ``-W`` *warning_flag*.

    A subprocess is the only honest way to observe a FIRST import: the parent
    process has already imported most of the tree, and every in-process trick
    for undoing that (reload, `sys.modules` surgery) corrupts state other tests
    depend on.
    """
    return subprocess.run(
        [sys.executable, "-W", warning_flag, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        timeout=60,
    )


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_legacy_path_warns(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """Importing the OLD path emits a user-visible, ACTIONABLE ``FutureWarning``.

    Stage 1 of the two-stage retirement (2026-08-01): v1.8.0 keeps the aliases
    but says so; the next release deletes them and makes plugin discovery refuse
    an old plugin by name.

    ``FutureWarning``, not ``DeprecationWarning``: the latter is hidden by
    default outside ``__main__``, which would make the notice invisible to
    exactly the people it is for.
    """
    proc = _import_in_subprocess(legacy, "always::FutureWarning")
    assert proc.returncode == 0, proc.stderr
    err = proc.stderr
    assert "FutureWarning" in err, err
    assert legacy in err, err
    assert new in err, err
    # The notice has to be ACTIONABLE, not merely present.
    assert "REMOVED in the next release" in err, err
    assert "kanibako-agent-" in err, err


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_legacy_path_warning_is_a_FutureWarning(
    legacy: str, new: str, names: tuple[str, ...]
) -> None:
    """The category is really ``FutureWarning`` — proven by making it fatal.

    Asserting the string ``"FutureWarning"`` appears in stderr would also pass
    if the message merely mentioned the word.  Under ``-W error::FutureWarning``
    the import must actually FAIL, which only a real ``FutureWarning`` does.
    """
    proc = _import_in_subprocess(legacy, "error::FutureWarning")
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "FutureWarning" in proc.stderr, proc.stderr


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_new_path_does_not_warn(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """The REAL module is silent.

    The warning belongs to the shim alone.  If it leaked into the new module,
    every in-repo caller and every correctly-updated plugin would be nagged for
    doing the right thing -- and the notice would stop meaning anything.

    ``-W error::FutureWarning`` makes any such leak a nonzero exit.
    """
    proc = _import_in_subprocess(new, "error::FutureWarning")
    assert proc.returncode == 0, (
        f"importing {new} raised under -W error::FutureWarning — the shim's "
        f"warning has leaked into the real module:\n{proc.stderr}"
    )


def _defined_public_names(module: str) -> set[str]:
    """Public names the module DEFINES at top level, read from its source.

    Deliberately AST-based rather than ``vars(mod)``.  A module's runtime
    namespace also holds everything it imported — ``Path``, ``Callable``,
    ``dataclass``, ``annotations`` — and re-exporting stdlib names through a
    compatibility shim would be noise, not compatibility.  What a shim owes the
    old path is the module's OWN surface.

    (Names a module re-imports from a kanibako sibling were also reachable at
    the old path.  Those are few and are carried EXPLICITLY in the shim, with a
    comment saying why — see ``CANONICAL_SEP`` in
    ``src/kanibako/settings_resolve.py``.)
    """
    spec = importlib.util.find_spec(module)
    assert spec is not None and spec.origin, f"cannot locate source for {module}"
    tree = ast.parse(Path(spec.origin).read_text())
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not node.name.startswith("_"):
                names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not target.id.startswith("_"):
                    names.add(target.id)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and not node.target.id.startswith("_"):
                names.add(node.target.id)
    return names
