"""Structured JSON logging with the correlation id on every line (M14).

Emitting JSON to stdout lets the compose/k8s log collector and any APM tool parse
each line and stitch a single request together by `correlation_id`.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from .correlation import get_correlation_id

# Standard LogRecord attributes we do NOT want to duplicate into the JSON payload;
# anything else attached via `extra={...}` is treated as a structured field.
_RESERVED = set(logging.makeLogRecord({}).__dict__.keys()) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "service": self.service_name,
            "correlation_id": get_correlation_id(),
            "msg": record.getMessage(),
        }
        # Promote any structured `extra={...}` fields to top-level keys.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(service_name))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # Align uvicorn's loggers with our handler so access logs are JSON too.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
