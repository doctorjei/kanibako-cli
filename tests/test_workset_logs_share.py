"""Keyspec § 0 "Per-owner resources" — two working sets on ONE ``workset.logs`` directory.

  > "Two instances whose own values name one per-owner resource (two worksets'
  > ``workset.logs`` on one directory) share it too: refused by name unless ``--force``,
  > and a forced share's destructive verb removes only its own instance's part."

Two consequences are tested here, both read off the SPEC rather than the implementation:

* **The door refuses by name.**  Aiming a second working set at a log directory some
  working set already resolves to is refused — naming the key, the value and the other
  working set — unless ``--force``.  The comparison is RESOLVED, never lexical:
  ``@meta.workset.path/../shared`` is a different string in every workset's own file and
  ONE directory on disk, and a raw-value comparison would report no collision while both
  worksets wrote into the same files.
* **A forced share's destructive verb takes only its own part.**  A per-box log is named
  by BOX NAME ALONE, so once the share is forced, a same-named box in the other working
  set maps to the SAME file.  That file is not attributable: the verb KEEPS it and
  REPORTS it, exactly as it keeps any path outside the box's own root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanibako.project.workset import (
    add_project, create_workset, logs_share_refusal, purge_box_logs,
)
from kanibako.settings.config import (
    WORKSET_META_FILE, load_config, system_settings_path,
)
from kanibako.settings.config_io import dump_doc
from kanibako.settings.paths import box_log_files, load_std_paths


# --------------------------------------------------------------------------- helpers

def set_own_logs(root: Path, value: "str | None") -> None:
    """Record a workset's OWN ``workset.logs`` value, as ``workset set`` writes it.

    ``None`` is the ``--null`` write: the workset keeps nothing of its own.
    """
    dump_doc(root / WORKSET_META_FILE, {"workset": {"logs": value}})


def two_worksets(std, tmp_home):
    """Two registered sibling working sets, neither yet pointing anywhere of its own."""
    a = create_workset("A", tmp_home / "wsA", std)
    b = create_workset("B", tmp_home / "wsB", std)
    return a, b


def seed_log(logs_dir: Path, box: str) -> "dict[Path, str]":
    """Write the per-box files a running box would have left; return them by path."""
    written = {
        logs_dir / f"{box}.jsonl": "helper line\n",
        logs_dir / f"{box}.creds-watcher.log": "watcher line\n",
    }
    logs_dir.mkdir(parents=True, exist_ok=True)
    for path, text in written.items():
        path.write_text(text)
    return written


def forced_share(std, tmp_home, a, b):
    """Put A and B on one log directory, the way ``--force`` lets them get there."""
    shared = tmp_home / "shared_logs"
    set_own_logs(a.root, str(shared))
    set_own_logs(b.root, str(shared))
    return shared


# --------------------------------------------------------------------------- the door

class TestTheShareIsRefusedByName:
    """A write that would put two working sets on one log directory does not happen."""

    def test_aiming_a_second_workset_at_the_ones_log_dir_is_refused(self, std, tmp_home):
        a, b = two_worksets(std, tmp_home)
        shared = str(tmp_home / "shared_logs")
        set_own_logs(a.root, shared)

        msg = logs_share_refusal("workset.logs", shared, std, force=False,
                                scope="workset", target_name="B", target_root=b.root)

        assert msg is not None, "two worksets on one log dir must not be writable"

    def test_the_refusal_names_the_key_the_value_and_the_other_workset(self, std, tmp_home):
        a, b = two_worksets(std, tmp_home)
        shared = str(tmp_home / "shared_logs")
        set_own_logs(a.root, shared)

        msg = logs_share_refusal("workset.logs", shared, std, force=False,
                                scope="workset", target_name="B", target_root=b.root)

        assert "workset.logs" in msg
        assert repr(shared) in msg, "the refused value must be quoted back"
        assert "'A'" in msg, "the OTHER working set must be named"

    def test_force_writes_it_anyway(self, std, tmp_home):
        """The share is the user's to take; ``--force`` is how they say so."""
        a, b = two_worksets(std, tmp_home)
        shared = str(tmp_home / "shared_logs")
        set_own_logs(a.root, shared)

        assert logs_share_refusal("workset.logs", shared, std, force=True,
                                 scope="workset", target_name="B",
                                 target_root=b.root) is None

    def test_two_values_that_only_resolve_alike_still_collide(self, std, tmp_home):
        """⚑ RESOLVED, not lexical: the dotdot form differs per workset, lands as one dir."""
        a, b = two_worksets(std, tmp_home)
        dotdot = "@meta.workset.path/../shared_logs"
        set_own_logs(a.root, dotdot)

        assert a.root.joinpath("..", "shared_logs").resolve() == b.root.joinpath(
            "..", "shared_logs").resolve()
        msg = logs_share_refusal("workset.logs", dotdot, std, force=False,
                                scope="workset", target_name="B", target_root=b.root)
        assert msg is not None

    def test_a_workset_aimed_at_a_directory_of_its_own_is_not_a_share(self, std, tmp_home):
        a, b = two_worksets(std, tmp_home)
        set_own_logs(a.root, str(tmp_home / "logs_a"))

        assert logs_share_refusal("workset.logs", str(tmp_home / "logs_b"), std,
                                 force=False, scope="workset",
                                 target_name="B", target_root=b.root) is None

    def test_a_third_workset_on_its_own_is_not_a_share_of_the_other_two(self, std, tmp_home):
        """A write that moves only its own target cannot be refused for others' sharing."""
        a, b = two_worksets(std, tmp_home)
        forced_share(std, tmp_home, a, b)
        c = create_workset("C", tmp_home / "wsC", std)

        assert logs_share_refusal("workset.logs", str(tmp_home / "logs_c"), std,
                                 force=False, scope="workset",
                                 target_name="C", target_root=c.root) is None

    def test_the_system_tier_can_collide_too_and_is_refused(self, std, tmp_home):
        """A SYSTEM value that reaches identity still lands every workset on one dir."""
        two_worksets(std, tmp_home)

        msg = logs_share_refusal("workset.logs", "@meta.workset.path/../shared_logs", std,
                                force=False, scope="system")
        assert msg is not None

    def test_the_system_tier_share_is_forced_the_same_way(self, std, tmp_home):
        two_worksets(std, tmp_home)

        assert logs_share_refusal("workset.logs", "@meta.workset.path/../shared_logs", std,
                                 force=True, scope="system") is None

    def test_a_null_write_that_falls_through_to_a_colliding_system_value_is_refused(
            self, std, tmp_home, config_file):
        """``--null`` is a write too: the target keeps nothing and reads the tier below."""
        two_worksets(std, tmp_home)
        # The FORCED system-tier value the null falls through TO, on disk, then re-read
        # so the early tier carries it the way a live process would.
        dump_doc(system_settings_path(),
                {"workset": {"logs": "@meta.workset.path/../shared_logs"}})
        std_with_tier = load_std_paths(load_config(config_file))

        msg = logs_share_refusal("workset.logs", None, std_with_tier, force=False,
                                scope="workset", target_name="A")
        assert msg is not None, "nulling onto a tier that collides is still a share"

    def test_another_per_owner_key_is_not_policed_by_this_guard(self, std, tmp_home):
        """Only ``workset.logs`` is in scope; a sibling key at the same door is untouched."""
        a, b = two_worksets(std, tmp_home)
        set_own_logs(a.root, str(tmp_home / "shared_logs"))

        assert logs_share_refusal("workset.registry", str(tmp_home / "shared_logs"), std,
                                 force=False, scope="workset",
                                 target_name="B", target_root=b.root) is None


# ------------------------------------------------- the forced share's destructive verb

class TestAForcedSharesPurgeTakesOnlyItsOwnPart:
    """Under a forced share the verb removes its own instance's part, and no more."""

    def test_a_differently_named_boxs_files_are_left_alone(self, std, tmp_home, capsys):
        a, b = two_worksets(std, tmp_home)
        shared = forced_share(std, tmp_home, a, b)
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        add_project(b, "beta", tmp_home / "src" / "beta")
        seed_log(shared, "alpha")
        seed_log(shared, "beta")

        removed = purge_box_logs(std, shared, "alpha", workset_root=a.root)

        assert sorted(p.name for p in removed) == ["alpha.creds-watcher.log", "alpha.jsonl"]
        assert (shared / "beta.jsonl").is_file(), "B's box's log is not this verb's to take"
        assert (shared / "beta.creds-watcher.log").is_file()

    def test_a_same_named_boxs_shared_file_is_kept(self, std, tmp_home):
        """Same name in both = ONE file = not attributable = KEEP."""
        a, b = two_worksets(std, tmp_home)
        shared = forced_share(std, tmp_home, a, b)
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        add_project(b, "alpha", tmp_home / "src2" / "alpha")
        written = seed_log(shared, "alpha")

        removed = purge_box_logs(std, shared, "alpha", workset_root=a.root)

        assert removed == [], "nothing may be deleted when the file has two owners"
        for path, text in written.items():
            assert path.read_text() == text, f"{path.name} must survive byte for byte"

    def test_the_kept_file_is_reported_and_never_reported_as_removed(self, std, tmp_home,
                                                                 capsys):
        a, b = two_worksets(std, tmp_home)
        shared = forced_share(std, tmp_home, a, b)
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        add_project(b, "alpha", tmp_home / "src2" / "alpha")
        seed_log(shared, "alpha")

        removed = purge_box_logs(std, shared, "alpha", workset_root=a.root)
        err = capsys.readouterr().err

        assert removed == []
        assert "kept" in err.lower()
        assert "'B'" in err, "the report must name the other working set"
        for path in box_log_files(shared, "alpha"):
            assert str(path) in err, f"every kept file is named in the report: {path.name}"

    def test_the_other_workset_purging_the_same_name_is_symmetric(self, std, tmp_home):
        """Neither side owns it — B's purge keeps it for the same reason A's did."""
        a, b = two_worksets(std, tmp_home)
        shared = forced_share(std, tmp_home, a, b)
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        add_project(b, "alpha", tmp_home / "src2" / "alpha")
        written = seed_log(shared, "alpha")

        assert purge_box_logs(std, shared, "alpha", workset_root=b.root) == []
        assert all(p.read_text() == t for p, t in written.items())

    def test_with_no_share_the_purge_deletes_normally(self, std, tmp_home, capsys):
        """The guard must not tax an ordinary purge: unshared, the file is simply the box's."""
        a, _b = two_worksets(std, tmp_home)
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        written = seed_log(a.logs_dir, "alpha")

        removed = purge_box_logs(std, a.logs_dir, "alpha", workset_root=a.root)

        assert sorted(p.name for p in removed) == ["alpha.creds-watcher.log", "alpha.jsonl"]
        assert not any(p.exists() for p in written)
        assert "kept" not in capsys.readouterr().err.lower()

    def test_a_null_log_directory_holds_nothing_to_delete(self, std, tmp_home):
        a, _b = two_worksets(std, tmp_home)

        assert purge_box_logs(std, None, "alpha", workset_root=a.root) == []

    def test_a_workset_that_is_not_in_the_walk_shares_with_whoever_is(self, std, tmp_home):
        """A STANDALONE box's degenerate workset is not walked, so it excludes nothing."""
        a, _b = two_worksets(std, tmp_home)
        shared = tmp_home / "shared_logs"
        set_own_logs(a.root, str(shared))
        add_project(a, "alpha", tmp_home / "src" / "alpha")
        seed_log(shared, "alpha")

        removed = purge_box_logs(std, shared, "alpha", workset_root=tmp_home / "elsewhere")

        assert removed == [], "A holds the same name, and A is not the one purging"


@pytest.mark.parametrize("value", [
    "/tmp/one_logs",
    "@meta.workset.path/../one_logs",
])
class TestBothCollidingValueShapesCloseTheSameDoor:
    """A literal and an anchored dotdot value are the same hazard and get the same answer."""

    def test_the_second_workset_is_refused_for_either_shape(self, std, tmp_home, value):
        a, b = two_worksets(std, tmp_home)
        set_own_logs(a.root, value)

        assert logs_share_refusal("workset.logs", value, std, force=False,
                                 scope="workset", target_name="B",
                                 target_root=b.root) is not None
