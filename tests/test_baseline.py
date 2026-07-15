"""Tests for kanibako.baseline (image-baseline manifest loader/accessors)."""

from __future__ import annotations


import pytest

from kanibako import baseline


# Locked universal contract shipped as package data.
_SHIPPED = {
    "tmux": ["tmux"],
    "inotify-tools": ["inotifywait", "inotifywatch"],
    "ripgrep": ["rg"],
    "fd-find": ["fdfind"],
    "openssh-client": ["ssh"],
    "bubblewrap": ["bwrap"],
}


@pytest.fixture
def overlay_dirs(tmp_path, monkeypatch):
    """Point baseline overlays at temp 'etc' and 'config' dirs.

    Returns ``(etc_path, user_path)`` — the two overlay file paths (machine then
    user). Neither exists until a test writes it.
    """
    etc = tmp_path / "etc" / baseline.BASELINE_FILENAME
    user = tmp_path / "config" / baseline.BASELINE_FILENAME
    monkeypatch.setattr(baseline, "_overlay_paths", lambda: [etc, user])
    return etc, user


class TestShippedDefault:
    def test_load_shipped_default(self) -> None:
        """The bundled default matches the locked contract."""
        assert baseline.load_baseline() == _SHIPPED

    def test_packages_sorted(self) -> None:
        assert baseline.packages() == sorted(_SHIPPED)

    def test_executables_pairs(self) -> None:
        pairs = baseline.executables()
        assert ("tmux", "tmux") in pairs
        assert ("inotify-tools", "inotifywait") in pairs
        assert ("inotify-tools", "inotifywatch") in pairs
        assert ("ripgrep", "rg") in pairs
        assert ("fd-find", "fdfind") in pairs
        assert ("openssh-client", "ssh") in pairs
        assert ("bubblewrap", "bwrap") in pairs
        # Sorted by (package, executable).
        assert pairs == sorted(pairs)


class TestOverlayMerge:
    def test_no_overlays_is_default(self, overlay_dirs) -> None:
        assert baseline.load_baseline() == _SHIPPED

    def test_etc_adds_package(self, overlay_dirs) -> None:
        etc, _user = overlay_dirs
        etc.parent.mkdir(parents=True)
        etc.write_text("jq: [jq]\n")
        merged = baseline.load_baseline()
        assert merged["jq"] == ["jq"]
        # Original packages preserved.
        assert merged["tmux"] == ["tmux"]

    def test_user_overrides_executable_list(self, overlay_dirs) -> None:
        _etc, user = overlay_dirs
        user.parent.mkdir(parents=True)
        user.write_text("ripgrep: [rg, ripgrep]\n")
        merged = baseline.load_baseline()
        assert merged["ripgrep"] == ["rg", "ripgrep"]

    def test_user_overlay_wins_over_etc(self, overlay_dirs) -> None:
        etc, user = overlay_dirs
        etc.parent.mkdir(parents=True)
        user.parent.mkdir(parents=True)
        etc.write_text("tmux: [tmux-etc]\n")
        user.write_text("tmux: [tmux-user]\n")
        merged = baseline.load_baseline()
        assert merged["tmux"] == ["tmux-user"]

    def test_bare_string_value_normalized(self, overlay_dirs) -> None:
        etc, _user = overlay_dirs
        etc.parent.mkdir(parents=True)
        etc.write_text("htop: htop\n")
        merged = baseline.load_baseline()
        assert merged["htop"] == ["htop"]

    def test_missing_overlay_tolerated(self, overlay_dirs) -> None:
        # Neither file written — must not raise.
        assert baseline.load_baseline() == _SHIPPED


class TestVerify:
    def test_verify_all_present(self) -> None:
        missing = baseline.verify(lambda exe: True)
        assert missing == []

    def test_verify_all_missing(self) -> None:
        missing = baseline.verify(lambda exe: False)
        assert set(missing) == set(baseline.executables())

    def test_verify_partial(self) -> None:
        # Everything present except 'rg'.
        missing = baseline.verify(lambda exe: exe != "rg")
        assert missing == [("ripgrep", "rg")]


class TestInstallCommand:
    def test_install_command_shape(self) -> None:
        cmd = baseline.install_command(["tmux", "ripgrep"])
        assert cmd == [
            "apt-get", "install", "-y", "--no-install-recommends",
            "tmux", "ripgrep",
        ]

    def test_install_command_empty(self) -> None:
        cmd = baseline.install_command([])
        assert cmd == ["apt-get", "install", "-y", "--no-install-recommends"]


class TestReadDoc:
    def test_read_missing_file(self, tmp_path) -> None:
        assert baseline._read_doc(tmp_path / "nope.yaml") == {}

    def test_read_empty_file(self, tmp_path) -> None:
        p = tmp_path / "empty.yaml"
        p.write_text("")
        assert baseline._read_doc(p) == {}

    def test_read_non_dict(self, tmp_path) -> None:
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n")
        assert baseline._read_doc(p) == {}
