"""Logging setup. Console shows INFO (or DEBUG with --debug); the log file at
~/.airthrow/airthrow.log always records full DEBUG detail for after-the-fact
debugging (gesture metrics, sends, receives, errors).
"""

from __future__ import annotations

import logging
import sys

from .config import CONFIG_DIR

LOG_PATH = CONFIG_DIR / "airthrow.log"
_LOGGER_NAME = "airthrow"


def setup_logging(debug: bool = False) -> logging.Logger:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(fmt)
    logger.addHandler(console)

    try:
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)  # full detail always on disk
        file_handler.setFormatter(fmt)
        logger.addHandler(file_handler)
    except OSError:
        pass  # a missing log file must never stop the app

    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
