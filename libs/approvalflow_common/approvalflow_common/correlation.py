"""Correlation id: one id follows a request end-to-end across services (M14/F9).

Stored in a context variable so it is available to any code (handlers, loggers,
Dapr helpers) without threading it through every function signature.
"""

import uuid
from contextvars import ContextVar

CORRELATION_ID_HEADER = "X-Correlation-ID"

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def get_correlation_id() -> str:
    return _correlation_id.get()


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def ensure_correlation_id(value: str | None) -> str:
    """Use the incoming id if present, otherwise mint a new one."""
    cid = value or new_correlation_id()
    set_correlation_id(cid)
    return cid
