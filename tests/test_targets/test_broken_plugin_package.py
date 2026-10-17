"""A plugin PACKAGE that raises at import costs its own agent, never the CLI.

An installed plugin lands in ``kanibako/plugins/<name>/`` and is reached twice per
discovery: once through its entry point, and again by the ``kanibako.plugins.*``
module scan. The scan walks with ``pkgutil.walk_packages``, which imports every
package to recurse into it. Without an ``onerror`` hook that walk lets anything
but ``ImportError`` escape, so a published ``kanibako-agent-goose`` whose
``__init__`` raised ``SettingsError`` (an old ``$GUEST_HOME`` spelling) printed
its SKIPPED warning and then killed every command anyway.

⚑ The package is REAL, not a ``side_effect``: it sits on ``kanibako.plugins``'s
``__path__`` where an install puts it, so the scan meets it the way it meets the
published wheel.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock, patch

import pytest

import kanibako.plugins as plugins_pkg
from kanibako.targets import discover_targets

_SETTINGS_ERROR_BODY = (
    "from kanibako.settings.settings_resolve import SettingsError\n"
    "raise SettingsError(\n"
    "    \"{name}-defaults.yaml: Old reference spelling '$GUEST_HOME' at: \"\n"
    "    \"'$GUEST_HOME/.local/bin/{name}' — references are braced now: \"\n"
    "    \"write '{{$GUEST_HOME}}'.\"\n"
    ")\n"
)
_GENERIC_BODY = "raise RuntimeError('{name} exploded at import')\n"

_CASES = [
    pytest.param("brokensettings", _SETTINGS_ERROR_BODY, "{$GUEST_HOME}", id="SettingsError"),
    pytest.param("brokengeneric", _GENERIC_BODY, "RuntimeError", id="Exception"),
]


@pytest.fixture(autouse=True)
def _clear_warn_dedupe():
    from kanibako.targets import _EP_LOAD_FAILED

    _EP_LOAD_FAILED.clear()
    yield
    _EP_LOAD_FAILED.clear()


def _install_broken_package(tmp_path, monkeypatch, name: str, body: str) -> MagicMock:
    """Put a raising ``kanibako.plugins.<name>`` package where an install would, and
    return the entry point that names it."""
    root = tmp_path / "site-plugins"
    pkg = root / name
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(body.format(name=name))
    monkeypatch.setattr(plugins_pkg, "__path__", [*plugins_pkg.__path__, str(root)])
    monkeypatch.delitem(sys.modules, f"kanibako.plugins.{name}", raising=False)

    ep = MagicMock()
    ep.name = name
    ep.dist.name = f"kanibako-agent-{name}"
    ep.load.side_effect = lambda: importlib.import_module(f"kanibako.plugins.{name}")
    return ep


def _run(argv: list[str]) -> int:
    from kanibako.cli import main

    try:
        main(argv)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)
    return 0


@pytest.mark.parametrize("name,body,detail", _CASES)
def test_discovery_returns_and_skips_only_the_broken_package(
    tmp_path, monkeypatch, capsys, name, body, detail,
):
    ep = _install_broken_package(tmp_path, monkeypatch, name, body)
    with patch("kanibako.targets.entry_points", return_value=[ep]):
        targets = discover_targets()
    err = capsys.readouterr().err

    assert name not in targets
    assert "shell" in targets
    assert f"kanibako-agent-{name}" in err, err
    assert "SKIPPED" in err, err
    assert detail in err, err


@pytest.mark.parametrize("name,body,detail", _CASES)
def test_a_command_still_succeeds_and_the_warning_names_the_plugin(
    tmp_home, monkeypatch, capsys, name, body, detail,
):
    ep = _install_broken_package(tmp_home, monkeypatch, name, body)
    with patch("kanibako.targets.entry_points", return_value=[ep]):
        rc = _run(["system", "defaults"])
    captured = capsys.readouterr()

    assert rc == 0, captured.err
    assert "DECLARED KEYS" in captured.out
    assert f"kanibako-agent-{name}" in captured.err, captured.err
    assert "SKIPPED" in captured.err, captured.err
    assert detail in captured.err, captured.err
    # The CLI's own failure line, which is what the escaped exception printed.
    assert not any(
        line.startswith("Error:") for line in captured.err.splitlines()
    ), captured.err
