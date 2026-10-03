"""Tests for kanibako.runtime.baseline (image-baseline manifest loader/accessors)."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.errors import ConfigError
from kanibako.runtime import baseline


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
        """A list document REFUSES instead of reading ``{}``.

        Reading a non-mapping as ``{}`` drops the whole overlay without a word, and a
        baseline that silently installs nothing is a silent accept of the wrong answer
        (spec §0). ``_read_doc`` goes through ``load_doc``, the one entry point for a
        user's YAML, and is refused BY NAME. An EMPTY or MISSING file still reads ``{}``;
        only a document that is not a mapping refuses.
        """
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == (
            f"the config file {p} is a list, not a mapping of keys. "
            "Fix or remove the file, then retry."
        )

    def test_repeated_key_is_refused_naming_the_file(self, tmp_path) -> None:
        """A package listed twice used to keep the LAST list and lose the first in silence."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("mypkg: [aaa]\nmypkg: [bbb]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == (
            f"the config file {p} sets 'mypkg' twice (line 1 and line 2). "
            "Remove one of the two, then retry."
        )

    def test_invalid_yaml_is_refused_not_a_traceback(self, tmp_path) -> None:
        """An unterminated flow sequence used to raise a raw ``yaml`` error out of the CLI."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("mypkg: [aaa\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value).startswith(
            f"the config file {p} is not valid YAML: "
        )
        assert str(exc.value).endswith("Fix or remove the file, then retry.")

    def test_a_self_referential_anchor_is_refused_naming_the_file(self, tmp_path) -> None:
        """A package list that contains itself is a document hazard, not an executable list."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("mypkg: &a [x, *a]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert f"{p} refers to itself at 'mypkg[1]'" in str(exc.value)

    def test_values_still_normalize(self, tmp_path) -> None:
        """⚑ THE CONTROL: the reader's own normalization is unchanged by the reroute."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("bare: rg\nnulled:\nlisted: [a, 2]\n")

        assert baseline._read_doc(p) == {
            "bare": ["rg"], "nulled": [], "listed": ["a", "2"],
        }

    @staticmethod
    def _value_refusal(path: Path, pkg: str) -> str:
        """The one message a value that is not an executable name, nor a list of them, gets."""
        return (
            f"the config file {path} sets '{pkg}' to a value that is not an "
            "executable name or a list of them. Fix or remove that entry, "
            "then retry."
        )

    def test_scalar_value_is_refused_naming_the_package(self, tmp_path) -> None:
        """A package mapped to a number is REFUSED BY NAME rather than iterated."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: 5\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")

    def test_table_value_is_refused_naming_the_package(self, tmp_path) -> None:
        """A package mapped to a table is REFUSED: a table's KEYS are not executable names."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: {a: 1}\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")

    def test_nested_list_value_is_refused_naming_the_package(self, tmp_path) -> None:
        """A list holding a list is REFUSED: its inner value is not an executable name."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: [[x]]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")

    def test_null_item_in_a_list_is_refused_naming_the_package(self, tmp_path) -> None:
        """``git: [~, true]`` items are YAML keywords; ``str`` would invent "None"/"True"."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: [~, true]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")

    def test_yaml_yes_item_in_a_list_is_refused(self, tmp_path) -> None:
        """A bare ``yes`` is a YAML 1.1 boolean, so it never means the executable "yes"."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: [yes]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")

    def test_bare_true_item_alone_is_refused(self, tmp_path) -> None:
        """A single boolean item is refused on its own, not only beside a null one."""
        p = tmp_path / "image-baseline.yaml"
        p.write_text("git: [x, false]\n")

        with pytest.raises(ConfigError) as exc:
            baseline._read_doc(p)
        assert str(exc.value) == self._value_refusal(p, "git")
