"""
V2 Structured Logger Factory.

Returns a standard logging.Logger whose handlers emit JSON-formatted
lines — forward-compatible with Loki, Datadog, and CloudWatch.

Usage:
    from v2.core.logging import get_logger
    logger = get_logger("v2.scanner_service")
    logger.info("Signal generated", extra={"coin": "BTC", "score": 87})
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class _JsonFormatter(logging.Formatter):
    """Format every log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":      self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":   record.levelname,
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields supplied via logger.info(..., extra={...})
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
                "taskName",
            }:
                payload[key] = val
        return json.dumps(payload, default=str)


_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(_JsonFormatter())

_configured: set[str] = set()


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Return a logger named *name* configured with the JSON handler.

    Safe to call multiple times with the same name — only installs the
    handler once.
    """
    logger = logging.getLogger(name)
    if name not in _configured:
        logger.setLevel(level)
        logger.addHandler(_handler)
        logger.propagate = False
        _configured.add(name)
    return logger
