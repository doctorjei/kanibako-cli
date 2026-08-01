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

import importlib
from types import ModuleType

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
    new_mod = importlib.import_module(new)
    legacy_mod = importlib.import_module(legacy)
    public = set()
    for name, value in vars(new_mod).items():
        if name.startswith("_"):
            continue
        # A module bound in the namespace is the module's own `import json` etc.,
        # not part of its public surface.
        if isinstance(value, ModuleType):
            continue
        # Functions/classes carry __module__; a name defined ELSEWHERE and merely
        # imported here is not this module's surface either.  Plain constants
        # (str/Path/int) have no __module__ — those are defined here, so keep them.
        if getattr(value, "__module__", new) != new:
            continue
        public.add(name)
    assert public, f"{new} exposes no public names — the filter is wrong, not the shim"
    missing = sorted(n for n in public if not hasattr(legacy_mod, n))
    assert not missing, f"{legacy} does not re-export {missing} from {new}"
