"""Shared fixtures for plugin-codex tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def fake_host(tmp_path: Path) -> Path:
    """Create a fake home directory with a .codex/ structure."""
    host = tmp_path / "fake_host"
    (host / ".codex").mkdir(parents=True)
    return host


@pytest.fixture()
def project_home(tmp_path: Path) -> Path:
    """Create a project home directory."""
    home = tmp_path / "home"
    home.mkdir()
    return home


def make_vendored_tree(
    npm_root: Path,
    *,
    suffix: str = "codex-linux-x64",
    triple: str = "x86_64-unknown-linux-musl",
    nested: bool = False,
) -> Path:
    """Materialize a fake npm global tree with a vendored codex binary.

    When *nested* is False the platform package is HOISTED to the top level
    (``<root>/@openai/<suffix>``); when True it is nested under the shim
    (``<root>/@openai/codex/node_modules/@openai/<suffix>``).  Returns the path
    to the created vendored binary file.
    """
    if nested:
        pkg = npm_root / "@openai" / "codex" / "node_modules" / "@openai" / suffix
    else:
        pkg = npm_root / "@openai" / suffix
    binary = pkg / "vendor" / triple / "bin" / "codex"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x7fELF fake native codex\n")
    binary.chmod(0o755)
    return binary
