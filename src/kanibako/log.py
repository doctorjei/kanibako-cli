"""Logging setup for kanibako."""

from __future__ import annotations

import logging
import sys


def setup_logging(verbose: bool = False, *, timestamps: bool = False) -> None:
    """Configure the ``kanibako`` root logger.

    Normal mode: WARNING+ to stderr.
    Verbose mode: DEBUG+ to stderr with ``kanibako: <message>`` format.
    *timestamps* prefixes each record with its time and level, for a stderr that is a
    file read later rather than a terminal read now (the detached creds watcher).
    """
    logger = logging.getLogger("kanibako")
    logger.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    fmt = None
    if verbose:
        logger.setLevel(logging.DEBUG)
        fmt = "kanibako: %(message)s"
    else:
        logger.setLevel(logging.WARNING)
    if timestamps:
        fmt = f"%(asctime)s %(levelname)s {fmt or '%(message)s'}"
    if fmt is not None:
        handler.setFormatter(logging.Formatter(fmt))

    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """Return a logger under ``kanibako``, from a short name or a module ``__name__``."""
    # ``__name__`` inside the package already carries the ``kanibako.`` prefix, so
    # prefixing it again would nest the real module under a phantom ``kanibako.``.
    # The dot is the boundary: ``kanibakox`` is a short name, not the package.
    if name == "kanibako" or name.startswith("kanibako."):
        return logging.getLogger(name)
    return logging.getLogger(f"kanibako.{name}")
