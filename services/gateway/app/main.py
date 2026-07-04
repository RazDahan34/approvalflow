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


def _proxy(app_id: str, method: str, verb: str = "GET", body: dict | None = None) -> JSONResponse:
    """Forward to an internal service via Dapr invocation; fail cleanly, never silently (M15)."""
    try:
        resp = invoke_service(app_id, method, http_verb=verb, json_body=body)
    except Exception as exc:
        log.error("upstream invocation failed", extra={"appId": app_id, "method": method, "error": str(exc)})
        return JSONResponse(status_code=502, content={"detail": "upstream unavailable"})
    return JSONResponse(status_code=resp.status_code, content=resp.json())


# ── submitter surface ──


@app.post("/invoices", status_code=202)
def submit_invoice(invoice: InvoiceSubmission, request: Request):
    """Validate at the edge, then forward to intake via Dapr service invocation."""
    return _proxy("intake", "invoices", "POST", invoice.model_dump(by_alias=True))


@app.get("/invoices/{tracking_id}/status")
def invoice_status(tracking_id: str, request: Request):
    return _proxy("intake", f"invoices/{tracking_id}/status")


@app.post("/invoices/{tracking_id}/reply")
async def invoice_reply(tracking_id: str, request: Request):
    """The submitter's answer to a request_info — resumes the paused workflow (F5)."""
    return _proxy("orchestrator", f"submissions/{tracking_id}/reply", "POST", await request.json())


# ── approver surface ──


@app.get("/approvals")
def approvals_queue(request: Request):
    """Only the escalated items, each with the agent's recommendation (F4)."""
    return _proxy("orchestrator", "escalations")


@app.post("/approvals/{tracking_id}/decision")
async def approvals_decision(tracking_id: str, request: Request):
    """approve | reject | request_info — one action resumes the workflow (F5)."""
    return _proxy("orchestrator", f"escalations/{tracking_id}/decision", "POST", await request.json())


# ── controller surface ──


@app.get("/budgets")
def budgets(request: Request):
    return _proxy("payment", "budgets")
