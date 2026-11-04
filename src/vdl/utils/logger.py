"""Logging configuration with Rich console integration."""

from __future__ import annotations

import logging
from rich.console import Console
from rich.logging import RichHandler

console = Console()
stderr_console = Console(stderr=True)


def setup_logger(verbose: bool = False, quiet: bool = False) -> logging.Logger:
    """Initializes the application logger with Rich formatting."""
    if quiet and not verbose:
        level = logging.ERROR
    else:
        level = logging.DEBUG if verbose else logging.INFO
    logger = logging.getLogger("vdl")
    logger.setLevel(level)

    if not logger.handlers:
        handler = RichHandler(
            console=stderr_console if quiet else console,
            show_time=verbose,
            show_path=verbose,
            rich_tracebacks=True,
            markup=True,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    else:
        for handler in logger.handlers:
            handler.setLevel(level)

    logger.propagate = False
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Returns a child logger under the 'vdl' namespace."""
    base = logging.getLogger("vdl")
    if not base.handlers:
        setup_logger(False)
    if name:
        return base.getChild(name)
    return base

