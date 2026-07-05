"""Unit tests for the lifecycle journal module (``kanibako.journal``, J1).

The journal is a write-ahead log of in-flight box-lifecycle ops keyed by box
host-side path.  At rest it is normally empty; an entry is the rare in-flight /
crashed op.  These tests cover the module in isolation (real files, no mocks):
read / write-ahead / clear / pending, atomicity, and the empty-file at-rest case.
"""

from __future__ import annotations

from pathlib import Path

from kanibako import journal


class TestReadJournal:
    def test_absent_file_is_empty(self, tmp_path: Path) -> None:
        assert journal.read_journal(tmp_path / "journal.yaml") == {}

    def test_empty_file_is_empty(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        jp.write_text("")
        assert journal.read_journal(jp) == {}

    def test_malformed_entries_is_empty(self, tmp_path: Path) -> None:
        """A non-mapping ``entries`` value yields an empty dict (defensive)."""
        jp = tmp_path / "journal.yaml"
        jp.write_text("entries: not-a-mapping\n")
        assert journal.read_journal(jp) == {}


class TestWriteEntry:
    def test_write_then_read_round_trip(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(
            jp, "/box/foo", op="create", name="foo", mode="primary",
        )
        entries = journal.read_journal(jp)
        assert set(entries) == {"/box/foo"}
        entry = entries["/box/foo"]
        assert entry["op"] == "create"
        assert entry["name"] == "foo"
        assert entry["mode"] == "primary"
        # Stamped fields present.
        assert "started_at" in entry
        assert "host" in entry
        # No workset key when not supplied.
        assert "workset" not in entry

    def test_workset_recorded_when_supplied(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(
            jp, "/box/foo", op="create", name="foo", mode="named",
            workset="myws",
        )
        assert journal.read_journal(jp)["/box/foo"]["workset"] == "myws"

    def test_workspace_recorded_when_supplied(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(
            jp, "/box/foo", op="create", name="foo", mode="primary",
            workspace="/ws/foo",
        )
        entry = journal.read_journal(jp)["/box/foo"]
        assert entry["workspace"] == "/ws/foo"

    def test_workspace_absent_when_not_supplied(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(
            jp, "/box/foo", op="create", name="foo", mode="primary",
        )
        assert "workspace" not in journal.read_journal(jp)["/box/foo"]

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """The journal parent (e.g. ``global/``) is created on first write."""
        jp = tmp_path / "global" / "journal.yaml"
        assert not jp.parent.exists()
        journal.write_entry(
            jp, "/box/foo", op="create", name="foo", mode="primary",
        )
        assert jp.is_file()

    def test_multiple_entries_coexist(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="primary")
        journal.write_entry(jp, "/box/b", op="import", name="b", mode="standalone")
        entries = journal.read_journal(jp)
        assert set(entries) == {"/box/a", "/box/b"}
        assert entries["/box/a"]["op"] == "create"
        assert entries["/box/b"]["op"] == "import"

    def test_rewrite_same_key_overwrites(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="primary")
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="standalone")
        assert journal.read_journal(jp)["/box/a"]["mode"] == "standalone"

    def test_path_key_accepts_pathlib(self, tmp_path: Path) -> None:
        """A ``Path`` box key is normalized to the same string key as ``str``."""
        jp = tmp_path / "journal.yaml"
        box = Path("/box/foo")
        journal.write_entry(jp, box, op="create", name="foo", mode="primary")
        assert journal.pending_entry(jp, "/box/foo") is not None
        assert journal.pending_entry(jp, box) is not None


class TestClearEntry:
    def test_clear_removes_key_keeps_doc(self, tmp_path: Path) -> None:
        """JC-J1-3 lean: clearing the last entry keeps the doc (``entries: {}``)."""
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/foo", op="create", name="foo", mode="primary")
        journal.clear_entry(jp, "/box/foo")
        assert journal.read_journal(jp) == {}
        assert jp.is_file()  # document persists, not deleted.

    def test_clear_one_of_many(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="primary")
        journal.write_entry(jp, "/box/b", op="create", name="b", mode="primary")
        journal.clear_entry(jp, "/box/a")
        assert set(journal.read_journal(jp)) == {"/box/b"}

    def test_clear_absent_key_is_noop(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="primary")
        journal.clear_entry(jp, "/box/missing")  # must not raise.
        assert set(journal.read_journal(jp)) == {"/box/a"}

    def test_clear_absent_file_is_noop(self, tmp_path: Path) -> None:
        journal.clear_entry(tmp_path / "journal.yaml", "/box/x")  # no raise.


class TestPending:
    def test_pending_entry_returns_entry(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/foo", op="create", name="foo", mode="primary")
        entry = journal.pending_entry(jp, "/box/foo")
        assert entry is not None and entry["name"] == "foo"

    def test_pending_entry_none_when_absent(self, tmp_path: Path) -> None:
        assert journal.pending_entry(tmp_path / "journal.yaml", "/box/x") is None

    def test_pending_create_only_for_create_op(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/c", op="create", name="c", mode="primary")
        journal.write_entry(jp, "/box/i", op="import", name="i", mode="primary")
        assert journal.pending_create(jp, "/box/c") is not None
        # A non-create op is NOT a pending create (no re-seed for import/connect).
        assert journal.pending_create(jp, "/box/i") is None
        assert journal.pending_create(jp, "/box/missing") is None


class TestPendingCreateForWorkspace:
    def test_finds_create_by_workspace(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        ws = tmp_path / "ws"
        ws.mkdir()
        journal.write_entry(
            jp, "/box/c", op="create", name="c", mode="primary",
            workspace=str(ws),
        )
        entry = journal.pending_create_for_workspace(jp, ws)
        assert entry is not None and entry["name"] == "c"

    def test_resolves_both_sides(self, tmp_path: Path) -> None:
        # A trailing-slash / unresolved query still matches a stored resolved path.
        jp = tmp_path / "journal.yaml"
        ws = tmp_path / "ws"
        ws.mkdir()
        journal.write_entry(
            jp, "/box/c", op="create", name="c", mode="primary",
            workspace=str(ws.resolve()),
        )
        assert journal.pending_create_for_workspace(jp, Path(str(ws) + "/")) is not None

    def test_ignores_non_create_ops(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        ws = tmp_path / "ws"
        ws.mkdir()
        journal.write_entry(
            jp, "/box/i", op="import", name="i", mode="primary",
            workspace=str(ws),
        )
        # A non-create op with a matching workspace is NOT returned.
        assert journal.pending_create_for_workspace(jp, ws) is None

    def test_none_when_workspace_mismatch(self, tmp_path: Path) -> None:
        jp = tmp_path / "journal.yaml"
        journal.write_entry(
            jp, "/box/c", op="create", name="c", mode="primary",
            workspace=str(tmp_path / "ws-a"),
        )
        assert journal.pending_create_for_workspace(jp, tmp_path / "ws-b") is None

    def test_none_when_no_workspace_field(self, tmp_path: Path) -> None:
        # A legacy entry without a workspace field never matches (no crash).
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/c", op="create", name="c", mode="primary")
        assert journal.pending_create_for_workspace(jp, tmp_path / "ws") is None


class TestAtomicity:
    def test_write_is_atomic_temp_rename(self, tmp_path: Path) -> None:
        """``dump_doc`` writes atomically (temp + os.replace) — no torn doc, and
        no stray temp files left in the journal dir after a successful write."""
        jp = tmp_path / "journal.yaml"
        journal.write_entry(jp, "/box/a", op="create", name="a", mode="primary")
        # The directory holds exactly the journal file (the atomic temp file is
        # renamed away on success — none linger).
        assert sorted(p.name for p in tmp_path.iterdir()) == ["journal.yaml"]
        # And the document round-trips cleanly (not torn).
        assert journal.read_journal(jp)["/box/a"]["name"] == "a"
