"""
Logger setup: outputs to stdout and a daily-rotating log file.
"""
import logging
import os
from logging.handlers import TimedRotatingFileHandler

_initialized = False


def setup_logger(level_str: str = "DEBUG", log_dir: str = "logs") -> logging.Logger:
    """Initialize the root 'smartcam' logger (idempotent)."""
    global _initialized
    if _initialized:
        return logging.getLogger("smartcam")

    os.makedirs(log_dir, exist_ok=True)
    level = getattr(logging, level_str.upper(), logging.DEBUG)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    logger = logging.getLogger("smartcam")
    logger.setLevel(level)

    ch = logging.StreamHandler()
    ch.setLevel(level)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    log_path = os.path.join(log_dir, "smartcam.log")
    fh = TimedRotatingFileHandler(
        log_path, when="midnight", interval=1, backupCount=7, encoding="utf-8"
    )
    fh.setLevel(level)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    _initialized = True
    logger.info(f"Logger initialized - level={level_str}, log_dir={log_dir}")
    return logger


def get_logger(name: str = "smartcam") -> logging.Logger:
    return logging.getLogger(name)
