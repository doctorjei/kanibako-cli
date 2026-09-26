"""Tests for kanibako.log."""

from __future__ import annotations

import logging

import pytest

from kanibako.log import get_logger, setup_logging


@pytest.fixture(autouse=True)
def _restore_kanibako_logger():
    """Every test here reconfigures the shared ``kanibako`` logger; put it back so no
    later test inherits a handler from this module."""
    logger = logging.getLogger("kanibako")
    handlers, level = logger.handlers[:], logger.level
    yield
    logger.handlers[:] = handlers
    logger.setLevel(level)  # setLevel, not assignment: it clears the level cache


class TestSetupLogging:
    def test_default_level_is_warning(self):
        setup_logging(verbose=False)
        logger = logging.getLogger("kanibako")
        assert logger.level == logging.WARNING

    def test_verbose_level_is_debug(self):
        setup_logging(verbose=True)
        logger = logging.getLogger("kanibako")
        assert logger.level == logging.DEBUG

    def test_handler_attached(self):
        setup_logging(verbose=False)
        logger = logging.getLogger("kanibako")
        assert len(logger.handlers) == 1

    def test_verbose_format(self):
        setup_logging(verbose=True)
        logger = logging.getLogger("kanibako")
        handler = logger.handlers[0]
        assert handler.formatter is not None
        assert "kanibako" in handler.formatter._fmt

    def test_repeated_calls_clear_handlers(self):
        setup_logging(verbose=False)
        setup_logging(verbose=True)
        logger = logging.getLogger("kanibako")
        assert len(logger.handlers) == 1

    def test_normal_mode_no_formatter(self):
        setup_logging(verbose=False)
        logger = logging.getLogger("kanibako")
        handler = logger.handlers[0]
        # Default handler has no explicit formatter set by us
        assert handler.formatter is None or "kanibako" not in handler.formatter._fmt

    def test_timestamps_prefix_time_and_level(self):
        """The detached creds watcher's stderr is a file read later, so each record
        carries its time and level; the message itself is unchanged."""
        setup_logging(timestamps=True)
        handler = logging.getLogger("kanibako").handlers[0]
        record = logging.LogRecord("kanibako.x", logging.WARNING, "", 0, "hi", None, None)
        record.created = 0.0
        line = handler.format(record)
        assert line.endswith(" WARNING hi")
        assert line.startswith(handler.formatter.formatTime(record))


class TestGetLogger:
    def test_child_logger_name(self):
        logger = get_logger("foo")
        assert logger.name == "kanibako.foo"

    def test_nested_child_logger(self):
        logger = get_logger("targets.claude")
        assert logger.name == "kanibako.targets.claude"

    def test_child_inherits_level(self):
        setup_logging(verbose=True)
        child = get_logger("test")
        assert child.getEffectiveLevel() == logging.DEBUG

    def test_child_inherits_warning_level(self):
        setup_logging(verbose=False)
        child = get_logger("test")
        assert child.getEffectiveLevel() == logging.WARNING
