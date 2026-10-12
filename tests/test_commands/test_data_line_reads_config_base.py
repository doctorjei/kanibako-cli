"""``system info``'s Data line and ``system diagnose``'s Storage check read ``config.data`` as every
other reader does: the ``/etc`` config base < the user config file."""

from __future__ import annotations

import pytest

import kanibako.settings.config as cfg_mod
from kanibako.cli import main
from kanibako.commands import diagnose
from kanibako.settings.paths import load_std_paths, resolve_data_path


@pytest.fixture
def site_data(tmp_home, config_file, monkeypatch):
    """A config base that moves ``config.data``; the user config file leaves it unset."""
    base = tmp_home / "etc" / "base.cfg"
    base.parent.mkdir()
    data = tmp_home / "site-data"
    base.write_text(f"config:\n  data: {data}\n")
    monkeypatch.setattr(cfg_mod, "config_base_path", lambda: base)
    assert load_std_paths().data == data == resolve_data_path()
    return data


def _run(argv):
    try:
        main(argv)
    except SystemExit:
        pass


def test_system_info_data_line_honors_the_config_base(site_data, capsys):
    _run(["system", "info"])
    out = capsys.readouterr().out
    assert [line.split(None, 1)[1] for line in out.splitlines() if line.startswith("Data:")] == [
        str(site_data)]


def test_system_diagnose_storage_honors_the_config_base(site_data, monkeypatch):
    checked = []
    monkeypatch.setattr(diagnose, "_check_storage", lambda path: (checked.append(path), ("ok", ""))[1])
    _run(["system", "diagnose"])
    assert checked == [site_data]
