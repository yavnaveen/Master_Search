"""Rotating logger for sync operations."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOGGER = None


def get_sync_logger():
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER

    logger = logging.getLogger('local_register_search.sync')
    logger.setLevel(logging.DEBUG)

    # Determine log path in project root/logs/sync.log
    project_root = Path(__file__).resolve().parent.parent
    logs_dir = project_root / 'logs'
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / 'sync.log'

    handler = RotatingFileHandler(
        str(log_file),
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)

    # Clear existing handlers to prevent duplication
    logger.handlers.clear()
    logger.addHandler(handler)
    _LOGGER = logger
    return _LOGGER


def enable_console_logging():
    logger = get_sync_logger()
    import sys
    for h in logger.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler):
            return
    console = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console.setFormatter(formatter)
    console.setLevel(logging.DEBUG)
    logger.addHandler(console)


def log_info(msg):
    get_sync_logger().info(msg)


def log_warning(msg):
    get_sync_logger().warning(msg)


def log_error(msg, exc_info=False):
    get_sync_logger().error(msg, exc_info=exc_info)


def log_debug(msg):
    get_sync_logger().debug(msg)
