"""The test-tier cleanup of PROTECTED canon skeletons (``tests/support/protected_trees``).

⚑⚑ THIS FILE GUARDS A BUG THAT CANNOT BE REPRODUCED ON THE DEV BOX.

A box's canon skeleton is chowned to a foreign SUBUID by ``podman unshare``, after
which the host user cannot chmod or unlink it.  ``tempfile.TemporaryDirectory``'s
finalizer chmods on its way in (``_resetperms``), so a fixture that drives the real
create path into one dies with ``PermissionError`` — on CI, where user namespaces
work, and NOT here, where a broken ``newuidmap`` leaves every skeleton agent-owned.
``KANI_TEST_SIM_UNSHARE`` does not close the gap either: it reproduces the MODES,
not the OWNERSHIP, and ownership is what defeats the deleter.

So these tests cannot assert the real failure.  What they CAN do — and what makes
them worth having — is pin the two things whose absence caused it:

1. the reap ROUTES THROUGH the product's escalating deleter (``remove_box_tree``),
   not a hand-rolled ``rmtree`` that would fail the same way; and
2. ``start_mocks``'s finalizer actually CALLS it, before the naive cleanup.

Both are mutation-provable locally: bypass the deleter, or drop the fixture's reap,
and these redden — which is exactly the regression that shipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support.protected_trees import reap_box_stores, reap_tree


def _make_box_store(root: Path, name: str = "demo") -> Path:
    """Build a directory with the skeleton's on-disk signature (``home/canon``)."""
    store = root / "primary_workset" / "boxes" / name
    (store / "home" / "canon" / "bible").mkdir(parents=True)
    (store / "home" / "canon" / "COLLECTION.md").touch()
    (store / "canon" / "handbook").mkdir(parents=True)
    return store


class TestReapRoutesThroughTheProductDeleter:
    """⚑ THE LOAD-BEARING ASSERTION. A plain ``shutil.rmtree`` here would fail on CI
    in exactly the way the fixture already did; only ``container.remove_box_tree``
    escalates to ``podman unshare rm -rf``. Pinning the ROUTE is what stops someone
    'simplifying' it back into the bug."""

    def test_reap_tree_delegates_to_remove_box_tree(self, tmp_path, monkeypatch):
        seen: list[Path] = []
        monkeypatch.setattr(
            "kanibako.container.remove_box_tree",
            lambda p: seen.append(Path(p)) or True,
        )
        target = tmp_path / "boxdir"
        target.mkdir()
        assert reap_tree(target) is True
        assert seen == [target]

    def test_reap_box_stores_delegates_for_every_store(self, tmp_path, monkeypatch):
        seen: list[Path] = []
        monkeypatch.setattr(
            "kanibako.container.remove_box_tree",
            lambda p: seen.append(Path(p)) or True,
        )
        a = _make_box_store(tmp_path, "a")
        b = _make_box_store(tmp_path, "b")
        reaped = reap_box_stores(tmp_path)
        assert set(seen) == {a, b}, seen
        assert set(reaped) == {a, b}


class TestReapIsSurgicalAndSafe:
    def test_only_box_stores_are_reaped(self, tmp_path):
        """A temp dir usually holds ordinary files the caller still owns; the reap
        must take the box stores and leave the rest to the normal cleanup."""
        store = _make_box_store(tmp_path)
        keep = tmp_path / "unrelated" / "notes.md"
        keep.parent.mkdir(parents=True)
        keep.write_text("mine")

        reap_box_stores(tmp_path)
        assert not store.exists()
        assert keep.read_text() == "mine"

    def test_absent_and_empty_roots_are_tolerated(self, tmp_path):
        assert reap_box_stores(tmp_path / "nope") == []
        assert reap_box_stores(None) == []
        assert reap_box_stores(tmp_path) == []
        assert reap_tree(None) is True
        assert reap_tree(tmp_path / "nope") is True

    def test_a_failing_deleter_never_raises(self, tmp_path, monkeypatch):
        """Cleanup must not turn a passing test red: a leaked temp dir is a far
        smaller problem than a false failure."""
        def _boom(_p):
            raise OSError("nope")

        monkeypatch.setattr("kanibako.container.remove_box_tree", _boom)
        _make_box_store(tmp_path)
        assert reap_box_stores(tmp_path) == []
        assert reap_tree(tmp_path) is False


class TestStartMocksReapsItsBoxStores:
    """⚑ THE FIXTURE-LEVEL PIN. ``start_mocks`` creates a REAL box home inside a
    ``TemporaryDirectory`` and drives the REAL protect pass (it patches
    ``commands.start.ContainerRuntime``, but ``_protect_canon_skeleton`` imports from
    ``kanibako.container`` inside the function, so the real runtime runs). Its
    finalizer must therefore reap before ``TemporaryDirectory`` cleans up.

    Drop the reap from the fixture and this reddens.
    """

    def test_the_fixture_finalizer_reaps_through_the_deleter(
        self, start_mocks, monkeypatch,
    ):
        seen: list[Path] = []
        monkeypatch.setattr(
            "kanibako.container.remove_box_tree",
            lambda p: seen.append(Path(p)) or True,
        )
        with start_mocks() as m:
            store_root = Path(m.store_tmp.name)
            # The fixture's own box home (``<pw>/boxes/testproject/home``) already
            # carries the signature once a canon dir exists; create it explicitly so
            # the pin does not depend on which code path ran inside the block.
            (store_root / "primary_workset" / "boxes" / "testproject" / "home"
             / "canon").mkdir(parents=True, exist_ok=True)
        assert any(
            p.name == "testproject" for p in seen
        ), f"start_mocks did not reap its box store through the deleter: {seen}"

    def test_the_fixture_exposes_its_temp_root(self, start_mocks):
        """``store_tmp`` is part of the fixture's contract — the reap and any test
        that needs the real box paths both read it."""
        with start_mocks() as m:
            assert Path(m.store_tmp.name).is_dir()


@pytest.mark.parametrize("factory", [reap_tree, reap_box_stores])
def test_helpers_accept_str_paths(tmp_path, factory):
    _make_box_store(tmp_path)
    factory(str(tmp_path))  # must not raise on a str
