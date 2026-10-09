"""Tests for kanibako.utils: cp_if_newer, confirm_prompt, project_hash, short_hash, path encoding, container naming."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from kanibako.errors import UserCanceled
from kanibako.utils import (
    confirm_prompt,
    container_name_for,
    container_name_for_box_name,
    container_name_segments,
    cp_if_newer,
    legacy_container_names,
    name_segment,
    project_hash,
    render_container_name,
    render_socket_identity,
    renders_no_name,
    short_hash,
    unrenderable_box_name_refusal,
    workset_segment,
)


# ---------------------------------------------------------------------------
# cp_if_newer
# ---------------------------------------------------------------------------

class TestCpIfNewer:
    def test_copies_when_dst_missing(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("hello")
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "hello"

    def test_copies_when_src_newer(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        dst.write_text("old")
        # Ensure distinct mtime
        src.write_text("new")
        os.utime(dst, (0, 0))
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "new"

    def test_skips_when_dst_newer(self, tmp_path):
        src = tmp_path / "src.txt"
        dst = tmp_path / "dst.txt"
        src.write_text("old")
        os.utime(src, (0, 0))
        dst.write_text("new")
        assert cp_if_newer(src, dst) is False
        assert dst.read_text() == "new"

    def test_skips_when_src_missing(self, tmp_path):
        dst = tmp_path / "dst.txt"
        assert cp_if_newer(tmp_path / "nope.txt", dst) is False
        assert not dst.exists()

    def test_creates_parent_dirs(self, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data")
        dst = tmp_path / "a" / "b" / "dst.txt"
        assert cp_if_newer(src, dst) is True
        assert dst.read_text() == "data"


# ---------------------------------------------------------------------------
# confirm_prompt
# ---------------------------------------------------------------------------

class TestConfirmPrompt:
    def test_yes_passes(self):
        with patch("builtins.input", return_value="yes"):
            confirm_prompt("ok? ")  # Should not raise

    def test_no_raises(self):
        with patch("builtins.input", return_value="no"):
            with pytest.raises(UserCanceled):
                confirm_prompt("ok? ")

    def test_empty_raises(self):
        with patch("builtins.input", return_value=""):
            with pytest.raises(UserCanceled):
                confirm_prompt("ok? ")

    def test_eof_raises(self):
        with patch("builtins.input", side_effect=EOFError):
            with pytest.raises(UserCanceled):
                confirm_prompt("ok? ")

    def test_keyboard_interrupt_raises(self):
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with pytest.raises(UserCanceled):
                confirm_prompt("ok? ")

    def test_whitespace_yes_passes(self):
        with patch("builtins.input", return_value="  yes  "):
            confirm_prompt("ok? ")  # Should not raise


# ---------------------------------------------------------------------------
# project_hash
# ---------------------------------------------------------------------------

class TestProjectHash:
    def test_deterministic(self):
        h1 = project_hash("/some/path")
        h2 = project_hash("/some/path")
        assert h1 == h2

    def test_different_paths(self):
        h1 = project_hash("/path/a")
        h2 = project_hash("/path/b")
        assert h1 != h2

    def test_matches_sha256(self):
        path = "/my/project"
        expected = hashlib.sha256(path.encode()).hexdigest()
        assert project_hash(path) == expected


# ---------------------------------------------------------------------------
# short_hash
# ---------------------------------------------------------------------------

class TestShortHash:
    def test_default_length(self):
        h = "abcdef1234567890"
        assert short_hash(h) == "abcdef12"

    def test_custom_length(self):
        h = "abcdef1234567890"
        assert short_hash(h, 4) == "abcd"


# ---------------------------------------------------------------------------
# container_name_for
# ---------------------------------------------------------------------------

def _mock_proj(*, mode="primary", name="", project_path="/home/user/proj",
               metadata_path=None, group=None,
               project_hash="abcdef1234567890abcdef1234567890"):
    """Create a duck-typed ProjectPaths-like object for testing."""
    mode_ns = SimpleNamespace(value=mode)
    return SimpleNamespace(
        mode=mode_ns,
        name=name,
        group=SimpleNamespace(name=group) if group is not None else None,
        project_path=Path(project_path),
        metadata_path=Path(metadata_path if metadata_path is not None else project_path),
        project_hash=project_hash,
    )


class TestContainerNameFor:
    def test_primary_with_name(self):
        proj = _mock_proj(name="myapp")
        assert container_name_for(proj) == "kb-primary-myapp"

    def test_primary_without_name_fallback(self):
        proj = _mock_proj(name="")
        assert container_name_for(proj) == f"kb-primary-{short_hash(proj.project_hash)}"

    def test_named_carries_the_workset_name(self):
        proj = _mock_proj(mode="named", name="myapp", group="kento")
        assert container_name_for(proj) == "kb-kento-myapp"

    def test_named_uses_hash_fallback(self):
        proj = _mock_proj(mode="named", name="", group="kento")
        assert container_name_for(proj) == f"kb-kento-{short_hash(proj.project_hash)}"

    def test_standalone_carries_the_standalone_segment(self):
        proj = _mock_proj(
            mode="standalone",
            name="7xk9q_ws",
            metadata_path="/home/user/my-project",
            project_path="/home/user/my-project/workspace",
        )
        assert container_name_for(proj) == "kb-standalone-7xk9q_ws"

    def test_named_without_a_workset_name_refuses(self):
        """A NAMED box with no workset has no ``<W>`` to render."""
        proj = _mock_proj(mode="named", name="myapp", group=None)
        with pytest.raises(ValueError, match="workset name"):
            container_name_for(proj)

    def test_local_name_with_number_suffix(self):
        proj = _mock_proj(name="myapp2")
        assert container_name_for(proj) == "kb-primary-myapp2"


class TestNameSegment:
    def test_a_dash_is_written_double(self):
        assert name_segment("a-b") == "a--b"

    def test_no_dash_is_unchanged(self):
        assert name_segment("droste") == "droste"

    def test_empty_segment_renders_empty(self):
        assert name_segment("") == ""

    def test_a_name_of_only_dashes(self):
        assert name_segment("---") == "------"

    def test_non_ascii_is_untouched(self):
        assert name_segment("\u30c9\u30ed") == "\u30c9\u30ed"


class TestWorksetSegment:
    def test_primary(self):
        assert workset_segment("primary", None) == "primary"

    def test_standalone(self):
        assert workset_segment("standalone", "ignored") == "standalone"

    def test_named_is_the_workset_name(self):
        assert workset_segment("named", "kento") == "kento"

    def test_named_without_a_name_refuses(self):
        with pytest.raises(ValueError, match="workset name"):
            workset_segment("named", None)


class TestRenderContainerName:
    def test_a_plain_pair(self):
        assert render_container_name("primary", "droste") == "kb-primary-droste"

    def test_a_helper_carries_its_index(self):
        assert render_container_name("kento", "droste", 3) == "kb-kento-droste-helper-3"

    def test_helper_zero_is_not_the_absent_helper(self):
        assert render_container_name("kento", "droste", 0) == "kb-kento-droste-helper-0"

    def test_escaping_happens_per_segment(self):
        assert render_container_name("a-b", "c") == "kb-a--b-c"
        assert render_container_name("a", "b-c") == "kb-a-b--c"


class TestCollisions:
    """Each pair is two DISTINCT boxes the render must not spell alike."""

    def test_the_dash_boundary_is_not_a_segment_boundary(self):
        assert render_container_name("a-b", "c") != render_container_name("a", "b-c")

    def test_primary_and_named_boxes_of_one_name(self):
        assert render_container_name("primary", "droste") != (
            render_container_name("kento", "droste")
        )

    def test_a_box_named_like_a_helper_suffix(self):
        assert render_container_name("primary", "x-helper-1") != (
            render_container_name("primary", "x", 1)
        )

    def test_a_helper_of_a_box_named_like_a_helper_suffix(self):
        assert render_container_name("primary", "x-helper-1", 1) != (
            render_container_name("primary", "x-helper-1")
        )

    def test_two_worksets_whose_escapes_overlap(self):
        assert render_container_name("a", "b") == "kb-a-b"
        assert render_container_name("a-b", "") is None


class TestInjectivity:
    """No two distinct RENDERABLE triples may spell one name.

    A ``<B>`` two boxes could share renders no name at all
    (:class:`TestRendersNoName`), so it is not in the population swept here.
    """

    _ADVERSARIAL = (
        "", "-", "--", "---", "a", "a-b", "a--b", "-a", "a-", "b-c", "a-b-c",
        "a--b--c", "\u30c9\u30ed", "\u0440\u043e", "x-helper-1", "helper-1",
        "1", "0", "0-helper-0", "primary", "standalone", "kento", " " * 3,
        "a b", "A", "aA", "\u00e9", "\u00e9-", "-e\u0301", "tab\tsep",
    )

    def test_no_two_renderable_triples_collide(self):
        boxes = [s for s in self._ADVERSARIAL if s and not s.startswith("-")]
        seen: dict[str, tuple[str, str, object]] = {}
        for workset in self._ADVERSARIAL:
            for box in boxes:
                for helper in (None, 0, 1, 7):
                    name = render_container_name(workset, box, helper)
                    key = (workset, box, helper)
                    assert seen.get(name, key) == key, (
                        f"{name!r} is spelled by both {seen[name]!r} and {key!r}"
                    )
                    seen[name] = key

    def test_the_same_triple_is_stable(self):
        assert render_container_name("a-b", "c", 2) == render_container_name("a-b", "c", 2)

    def test_a_200_byte_name_still_renders_uniquely(self):
        long_a = "a" * 100 + "-" + "b" * 99
        long_b = "a" * 100 + "--" + "b" * 98
        assert render_container_name(long_a, "x") != render_container_name(long_b, "x")


class TestRendersNoName:
    """A ``<B>`` two boxes could render alike yields NO NAME — a value, never a raise."""

    def test_an_empty_box_name_renders_no_name(self):
        assert render_container_name("primary", "") is None

    def test_a_dash_leading_box_name_renders_no_name(self):
        assert render_container_name("primary", "-droste") is None

    def test_the_predicate_names_the_two_shapes(self):
        assert renders_no_name("") is True
        assert renders_no_name("-droste") is True
        assert renders_no_name("droste") is False

    def test_a_helper_number_does_not_rescue_the_box_name(self):
        assert render_container_name("primary", "-droste", 3) is None

    def test_only_one_side_of_the_colliding_pair_renders(self):
        """``kb-primary---b`` has two readings; the one needing a leading ``-`` is absent."""
        assert render_container_name("primary-", "b") == "kb-primary---b"
        assert render_container_name("primary", "-b") is None

    def test_the_workset_segment_is_not_the_judged_one(self):
        assert render_container_name("-", "b") == "kb----b"

    def test_only_a_name_is_judged(self):
        """A non-``str`` is not a name, so the render proceeds and fails in its own way."""
        with pytest.raises(AttributeError):
            render_container_name("primary", object())

    def test_a_conforming_name_renders_exactly_the_same_string(self):
        assert render_container_name("primary", "droste") == "kb-primary-droste"
        assert render_container_name("kento", "droste", 3) == "kb-kento-droste-helper-3"
        assert render_container_name("kento", "droste", 0) == "kb-kento-droste-helper-0"
        assert render_container_name("a-b", "c") == "kb-a--b-c"
        assert render_container_name("a", "b-c") == "kb-a-b--c"
        assert container_name_for_box_name("droste", "kento") == "kb-kento-droste"

    def test_a_nameless_primary_box_falls_back_to_its_hash(self):
        """The hash is a conforming name, so a nameless box still renders."""
        assert container_name_for(_mock_proj(name="")).startswith("kb-primary-")


class TestStartUnrenderableBoxNameRefusal:
    """The shared text names the rule and the command that gives a valid name.

    ⚑ ONE carrier in ``utils`` for every door that reports it — ``start`` and ``stop``.
    """

    RULE = "box name must not start with '-' (collides with CLI flags)"
    PATH = "/home/user/myproj"

    @staticmethod
    def _text(mode: str = "primary") -> str:
        return unrenderable_box_name_refusal(
            "-droste", mode, TestStartUnrenderableBoxNameRefusal.PATH,
        )

    def test_it_names_the_box_name_rule(self):
        assert self.RULE in self._text()

    def test_a_primary_box_is_cured_by_moving_it(self):
        assert f"kanibako box move {self.PATH} <new-path> --name <new-name>" in (
            self._text("primary")
        )

    def test_a_named_box_is_cured_by_moving_it(self):
        assert f"kanibako box move {self.PATH} <new-path> --name <new-name>" in (
            self._text("named")
        )

    def test_a_standalone_box_is_cured_by_moving_its_directory(self):
        text = self._text("standalone")
        assert text.endswith(f"\n  kanibako box move {self.PATH} <new-path>")
        assert "--name" not in text

    def test_the_cure_never_addresses_the_box_by_the_refused_name(self):
        """A leading ``-`` is read as a flag, so naming the box would not reach it."""
        for mode in ("primary", "named", "standalone"):
            text = self._text(mode)
            assert "kanibako box move -droste" not in text
            assert "kanibako box convert -droste" not in text

    def test_a_box_with_no_project_path_is_cured_from_inside_itself(self):
        text = unrenderable_box_name_refusal("-droste", "primary", None)
        assert "kanibako box move <new-path> --name <new-name>" in text
        assert "None" not in text

    def test_it_never_names_a_rename_command(self):
        assert "rename" not in self._text("primary")
        assert "rename" not in self._text("standalone")


class TestRenderSocketIdentity:
    def test_the_two_segments_lead_with_the_box(self):
        assert render_socket_identity("droste", "primary") == "droste-primary"

    def test_the_dash_boundary_is_not_a_segment_boundary(self):
        assert render_socket_identity("a-b", "c") != render_socket_identity("a", "b-c")

    def test_a_dash_leading_box_renders_no_identity(self):
        assert render_socket_identity("-", "primary") is None

    def test_a_conforming_pair_renders_exactly_the_same_string(self):
        assert render_socket_identity("a-b", "c") == "a--b-c"
        assert render_socket_identity("droste", "kento") == "droste-kento"


class TestContainerNameSegments:
    def test_primary(self):
        assert container_name_segments(_mock_proj(name="myapp")) == ("primary", "myapp")

    def test_named(self):
        segs = container_name_segments(_mock_proj(mode="named", name="myapp", group="kento"))
        assert segs == ("kento", "myapp")

    def test_the_pair_renders_to_the_name(self):
        proj = _mock_proj(mode="named", name="myapp", group="kento")
        assert render_container_name(*container_name_segments(proj)) == (
            container_name_for(proj)
        )


class TestContainerNameForBoxName:
    def test_the_workset_is_part_of_the_name(self):
        assert container_name_for_box_name("droste", "kento") == "kb-kento-droste"

    def test_the_same_name_in_two_worksets(self):
        assert container_name_for_box_name("droste", "kento") != (
            container_name_for_box_name("droste", "juno")
        )


class TestLegacyContainerNames:
    """The pre-``kb-`` spellings, one carrier, for the upgrade guard alone."""

    def test_a_primary_box(self):
        assert legacy_container_names(_mock_proj(name="myapp")) == ("kanibako-myapp",)

    def test_a_named_box_carried_no_workset(self):
        assert legacy_container_names(
            _mock_proj(mode="named", name="myapp", group="kento")
        ) == ("kanibako-myapp",)

    def test_a_nameless_box_fell_back_to_its_hash(self):
        proj = _mock_proj(name="")
        assert legacy_container_names(proj) == (f"kanibako-{short_hash(proj.project_hash)}",)

    def test_a_standalone_box_keyed_off_its_root(self):
        proj = _mock_proj(
            mode="standalone", name="7xk9q_ws",
            metadata_path="/home/user/my-project",
            project_path="/home/user/my-project/workspace",
        )
        assert legacy_container_names(proj) == ("kanibako-ronin-home-user-my-.project",)
