"""Technical logging to a rotating file plus the console."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from config import get_config

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_MAX_LOG_BYTES = 1_000_000
_LOG_BACKUP_COUNT = 3

_is_configured = False


def configure_logging(level: int = logging.INFO) -> None:
    """Attach the file and console handlers once per process.

    Calling this twice is harmless: duplicate handlers would duplicate every
    line in the log file, so the second call is ignored.
    """
    global _is_configured
    if _is_configured:
        return

    formatter = logging.Formatter(_LOG_FORMAT)

    file_handler = RotatingFileHandler(
        get_config().log_file,
        maxBytes=_MAX_LOG_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(console_handler)

    _is_configured = True
