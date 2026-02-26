"""Logging configuration for the application."""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .config import get_config


def setup_logger(
    name: str,
    log_file: Optional[str] = None,
    level: Optional[str] = None
) -> logging.Logger:
    """
    Setup a logger with console and optional file handlers.

    Args:
        name: Logger name
        log_file: Optional log file path
        level: Optional log level (overrides config)

    Returns:
        Configured logger instance
    """
    config = get_config()

    # Create logger
    logger = logging.getLogger(name)
    log_level = level or config.logging.level
    logger.setLevel(getattr(logging, log_level.upper()))

    # Prevent duplicate handlers
    if logger.handlers:
        return logger

    # Create formatters
    formatter = logging.Formatter(config.logging.format)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler — skipped in production (Railway has ephemeral filesystem;
    # stdout is captured automatically by the platform).
    if log_file and not config.is_production:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=config.logging.max_bytes,
            backupCount=config.logging.backup_count
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_sync_logger() -> logging.Logger:
    """Get logger for sync operations."""
    config = get_config()
    return setup_logger("sync", config.logging.files.sync)


def get_webhook_logger() -> logging.Logger:
    """Get logger for webhook operations."""
    config = get_config()
    return setup_logger("webhook", config.logging.files.webhook)


def get_error_logger() -> logging.Logger:
    """Get logger for error tracking."""
    config = get_config()
    return setup_logger("error", config.logging.files.error, "ERROR")


def get_api_logger() -> logging.Logger:
    """Get logger for API operations."""
    return setup_logger("api")


def get_scheduler_logger() -> logging.Logger:
    """Get logger for APScheduler internals.

    Without this, APScheduler exceptions in background threads are
    silently swallowed and never appear in Railway logs.
    """
    return setup_logger("apscheduler")
