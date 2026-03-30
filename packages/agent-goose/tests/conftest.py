"""Shared fixtures for plugin-goose tests."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def fake_host(tmp_path: Path) -> Path:
    """Create a fake home directory with .config/goose/ structure."""
    host = tmp_path / "fake_host"
    (host / ".config" / "goose").mkdir(parents=True)
    return host


@pytest.fixture()
def project_home(tmp_path: Path) -> Path:
    """Create a project home directory."""
    home = tmp_path / "home"
    home.mkdir()
    return home
