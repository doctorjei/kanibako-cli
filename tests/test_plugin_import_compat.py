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

⚑ When the removal gate in ``MIGRATION.md`` §3.1 fires (all three plugins
published against the new paths), delete the shim AND its row here — do not
weaken this file to keep it passing.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import warnings
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_legacy_path_warns(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """Importing the OLD path emits a user-visible ``FutureWarning``.

    Stage 1 of the two-stage retirement (Jei, 2026-08-01): v1.8.0 keeps the
    aliases but says so; the next release deletes them and makes plugin
    discovery refuse an old plugin by name.

    ``FutureWarning``, not ``DeprecationWarning``: the latter is hidden by
    default outside ``__main__``, which would make the notice invisible to
    exactly the people it is for.

    ``importlib.reload`` re-executes the module body -- the module is already in
    ``sys.modules`` from the tests above, so a plain import would emit nothing.
    Reload keeps the same module object, so the identity assertions elsewhere in
    this file are unaffected.
    """
    mod = importlib.import_module(legacy)
    with pytest.warns(FutureWarning) as record:
        importlib.reload(mod)
    messages = [str(w.message) for w in record]
    assert any(legacy in m for m in messages), messages
    assert any(new in m for m in messages), messages
    # The notice has to be ACTIONABLE, not merely present.
    assert any("REMOVED in the next release" in m for m in messages), messages
    assert any("kanibako-agent-" in m for m in messages), messages


@pytest.mark.parametrize("legacy,new,names", _SHIMS, ids=_IDS)
def test_new_path_does_not_warn(legacy: str, new: str, names: tuple[str, ...]) -> None:
    """The REAL module is silent.

    The warning belongs to the shim alone.  If it leaked into the new module,
    every in-repo caller and every correctly-updated plugin would be nagged for
    doing the right thing -- and the notice would stop meaning anything.
    """
    mod = importlib.import_module(new)
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        importlib.reload(mod)  # raises if the new module emits a FutureWarning


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
