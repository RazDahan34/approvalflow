"""Shared building blocks for every ApprovalFlow service."""

from .app import create_app
from .config import Settings, get_settings
from .correlation import (
    CORRELATION_ID_HEADER,
    ensure_correlation_id,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)
from .decision import (
    AgentRecommendation,
    PolicyConfig,
    RouterDecision,
    route_decision,
    to_usd,
)
from .logging import configure_logging, get_logger
from .schemas import (
    Category,
    InvoiceSubmission,
    InvoiceSubmittedEvent,
    LineItem,
    Route,
)

__all__ = [
    "create_app",
    "Settings",
    "get_settings",
    "CORRELATION_ID_HEADER",
    "ensure_correlation_id",
    "get_correlation_id",
    "new_correlation_id",
    "set_correlation_id",
    "configure_logging",
    "get_logger",
    "Category",
    "InvoiceSubmission",
    "InvoiceSubmittedEvent",
    "LineItem",
    "Route",
    "AgentRecommendation",
    "PolicyConfig",
    "RouterDecision",
    "route_decision",
    "to_usd",
]
