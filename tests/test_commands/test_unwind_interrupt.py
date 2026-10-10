"""A second Ctrl-C inside the unwind must not skip the remaining compensating actions."""

from __future__ import annotations

import pytest

from kanibako.commands.box._lifecycle import _Unwind


def _record(ran: list[str], name: str, exc: BaseException | None = None):
    def action() -> None:
        ran.append(name)
        if exc is not None:
            raise exc
    return action


class TestRunHoldsInterrupt:
    def test_interrupted_action_does_not_skip_the_rest(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "first"))
        unwind.push(_record(ran, "middle", KeyboardInterrupt()))
        unwind.push(_record(ran, "last"))
        with pytest.raises(KeyboardInterrupt):
            unwind.run()
        assert ran == ["last", "middle", "first"]
        assert unwind.actions == []

    def test_first_interrupt_is_the_one_reraised(self):
        first, second = KeyboardInterrupt("first"), KeyboardInterrupt("second")
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "a", second))
        unwind.push(_record(ran, "b", first))
        with pytest.raises(KeyboardInterrupt) as info:
            unwind.run()
        assert info.value is first
        assert ran == ["b", "a"]

    def test_ordinary_failure_still_swallowed(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.push(_record(ran, "a"))
        unwind.push(_record(ran, "b", OSError("boom")))
        unwind.run()
        assert ran == ["b", "a"]


class TestNoteInterruptedHoldsInterrupt:
    def test_interrupted_note_does_not_drop_the_rest(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.on_success(lambda: None, interrupted=_record(ran, "one"))
        unwind.on_success(
            lambda: None, interrupted=_record(ran, "two", KeyboardInterrupt()))
        unwind.on_success(lambda: None, interrupted=_record(ran, "three"))
        with pytest.raises(KeyboardInterrupt):
            unwind.note_interrupted()
        assert ran == ["one", "two", "three"]

    def test_only_unfinished_cleanups_are_named(self):
        ran: list[str] = []
        unwind = _Unwind()
        unwind.on_success(
            lambda: None, interrupted=_record(ran, "done", KeyboardInterrupt()))
        unwind.on_success(lambda: None, interrupted=_record(ran, "pending"))
        unwind.finished = 1
        unwind.note_interrupted()
        assert ran == ["pending"]
