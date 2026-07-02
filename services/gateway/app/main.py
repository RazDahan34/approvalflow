"""API Gateway.

The single external entry point (M6): validates requests, applies rate-limiting,
and forwards to internal services via Dapr service invocation. JWT auth + roles (N1)
are layered on top of this in a later phase.
"""

from approvalflow_common import create_app, get_logger, get_settings
from approvalflow_common.dapr_client import invoke_service
from approvalflow_common.schemas import InvoiceSubmission
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

SERVICE = "gateway"
settings = get_settings()

app = create_app(SERVICE)
log = get_logger(SERVICE)

# Let the browser UI (served from a different origin) call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate-limiting (M6): shield the system from spikes / abuse. Per-client-IP window.
limiter = Limiter(key_func=get_remote_address, default_limits=[settings.gateway_rate_limit])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.post("/invoices", status_code=202)
def submit_invoice(invoice: InvoiceSubmission, request: Request):
    """Validate at the edge, then forward to intake via Dapr service invocation."""
    try:
        resp = invoke_service(
            "intake",
            "invoices",
            http_verb="POST",
            json_body=invoice.model_dump(by_alias=True),
        )
    except Exception as exc:  # fail cleanly, never silently (M15)
        log.error("intake invocation failed", extra={"error": str(exc)})
        return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


@app.get("/invoices/{tracking_id}/status")
def invoice_status(tracking_id: str, request: Request):
    try:
        resp = invoke_service("intake", f"invoices/{tracking_id}/status", http_verb="GET")
    except Exception as exc:
        log.error("intake invocation failed", extra={"error": str(exc)})
        return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())
