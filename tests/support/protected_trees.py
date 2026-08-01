"""Cleanup for test trees that contain a PROTECTED canon skeleton.

⚑⚑ WHY THIS EXISTS — the failure it prevents is INVISIBLE ON THE DEV BOX.

Since J-7, ``core_defaults.materialize_canon_skeleton`` chowns a box's canon books
to a mapped SUBUID via ``podman unshare``.  The host user then cannot chmod, unlink
or rmdir them.  Any test that drives the real create/skeleton path and then lets an
ORDINARY deleter clean up afterwards dies with ``PermissionError`` — and
``tempfile.TemporaryDirectory`` is the sharpest case, because its finalizer runs
``_resetperms`` → ``os.chmod(path, 0o700)`` on the way in, so it fails on the
DIRECTORY ITSELF before it ever tries to recurse.

The trap is that this is **environment-conditional**:

* on a host with a broken ``newuidmap`` (this project's dev box) ``podman unshare``
  fails, the protect pass short-circuits, every skeleton is left agent-owned, and
  the naive cleanup succeeds — so the bug does not exist locally;
* on CI runners and bifrost, user namespaces WORK, the skeleton is genuinely
  foreign-owned, and the same code reddens.

⚑ The ``KANI_TEST_SIM_UNSHARE`` simulation CANNOT reproduce it either: the sim
chmods as the OWNER, so it reproduces the MODES (555/444) but not the OWNERSHIP,
and it is foreign ownership that defeats the deleter.  A local green therefore
proves nothing about this class — the same lesson memory
``local-gate-blind-to-protected-canon`` records, arriving a third time (test-body
rmtree → e2e ``/tmp`` creep → this).

⚑ ONE ESCALATION, ONE PLACE.  Everything here delegates to
:func:`kanibako.container.remove_box_tree`, the SAME deleter the product's own
lifecycle verbs use — it tries ``shutil.rmtree`` first and falls back to
``podman unshare rm -rf``.  A second hand-rolled escalation in the test tree would
be free to drift from the one that ships.
"""

from __future__ import annotations

from pathlib import Path

#: The skeleton's on-disk SIGNATURE, relative to a box store root.  ``home/canon``
#: is created by (and only by) ``materialize_canon_skeleton``, so its presence is
#: what distinguishes a box store worth escalating over from an ordinary temp dir.
_BOX_STORE_MARKER = ("home", "canon")


def reap_tree(path: Path | str | None) -> bool:
    """Delete *path* through the escalating deleter; never raise.

    The WHOLE-TREE form, for a caller that owns everything under *path* (the e2e
    tier, where a test's ``tmp_path`` holds an entire isolated kanibako install).
    Returns True if *path* is gone afterwards.
    """
    if path is None:
        return True
    target = Path(path)
    if not target.exists():
        return True
    try:
        from kanibako.container import remove_box_tree

        return remove_box_tree(target)
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        return False


def reap_box_stores(root: Path | str | None) -> list[Path]:
    """Delete every BOX STORE under *root* through the escalating deleter.

    The SURGICAL form, for a caller whose temp dir holds protected box stores among
    ordinary files it would rather leave to the normal cleanup (the unit tier).
    Finds each store by its ``home/canon`` signature and removes the store ROOT, so
    the protected skeleton is gone before any ordinary deleter reaches it.

    ⚑ CALL IT BEFORE the naive cleanup, not instead of it: this removes the box
    stores, and whatever created the temp dir still owns the rest.

    Returns the store roots actually removed (empty is the normal case — most tests
    create no box at all, and on a host without working user namespaces nothing is
    ever protected).  Never raises: a cleanup failure must not turn a passing test
    red, and a leaked temp dir is a far smaller problem than a false red.
    """
    if root is None:
        return []
    base = Path(root)
    if not base.is_dir():
        return []
    reaped: list[Path] = []
    try:
        for canon in sorted(base.rglob("/".join(_BOX_STORE_MARKER))):
            store = canon.parent.parent
            if reap_tree(store):
                reaped.append(store)
    except Exception:  # noqa: BLE001 - cleanup must never fail a test
        return reaped
    return reaped
