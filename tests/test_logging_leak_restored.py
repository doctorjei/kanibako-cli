"""Guard: a logging handler installed by one test must not outlive it.

⚑ WHY THIS IS A PAIR AND NOT ONE TEST.
The defect is ORDER-DEPENDENT: a handler bound to a stream that later closes, left on
the process-wide ``kanibako`` logger by a test that called ``cli.main()`` (which
calls ``log.setup_logging`` → ``StreamHandler(sys.stderr)`` against pytest's captured
stderr).  When pytest closes that capture the handler stays attached to a closed file,
and the NEXT test's WARNING prints ``--- Logging error --- ValueError: I/O operation
on closed file`` into ITS captured output.

So the guard has to be two tests in a fixed order: one that leaks, one that proves the
leak was cleaned up.  ``conftest._restore_kanibako_logging`` is the fix; remove it
and the second test here reddens.
"""

from __future__ import annotations

import io
import logging


def _closed_stream_handlers() -> "list[logging.Handler]":
    """Handlers on the ``kanibako`` logger whose stream is closed."""
    logger = logging.getLogger("kanibako")
    return [
        h for h in logger.handlers
        if getattr(h, "stream", None) is not None and h.stream.closed
    ]


class TestALeakedHandlerDoesNotOutliveItsTest:
    def test_first_a_handler_is_installed_on_a_stream_we_then_close(self):
        """Recreate the leak shape exactly: a handler bound to a stream that closes.

        This is what ``setup_logging`` does to pytest's captured ``sys.stderr`` when a
        test calls ``main()`` in-process.  Asserted present, so the next test has
        something it must have cleaned up.
        """
        logger = logging.getLogger("kanibako")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger.addHandler(handler)
        stream.close()

        assert handler in logger.handlers
        assert _closed_stream_handlers() == [handler]
        # Teardown of THIS test must remove it; see the next test.

    def test_the_previous_test_left_no_handler_on_a_closed_stream(self):
        """The autouse restore fixture must have removed the leaked handler.

        Without it, any WARNING this test triggered would hit the closed stream and
        print a ``Logging error`` block into this test's own captured output — the
        exact failure that showed up in ``test_create_recovery.py``.
        """
        assert _closed_stream_handlers() == []

    def test_a_warning_here_does_not_raise_or_print_a_logging_error(self, capsys):
        """Emitting here must be clean: no error text, no ``Logging error`` block."""
        logging.getLogger("kanibako").warning("probe after a prior test's leak")
        cap = capsys.readouterr()
        assert "Logging error" not in cap.err
        assert "I/O operation on closed file" not in cap.err
