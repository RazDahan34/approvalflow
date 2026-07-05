"""API Gateway.

The single external entry point (M6): validates requests, applies rate-limiting,
and forwards to internal services via Dapr service invocation. JWT auth + roles (N1)
are layered on top of this in a later phase.
"""

import httpx
from approvalflow_common import create_app, get_logger, get_settings
from approvalflow_common.dapr_client import invoke_service
from approvalflow_common.schemas import InvoiceSubmission
from approvalflow_common.security import ROLES, AuthError, issue_token, verify_token
from fastapi import Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
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


# ── authN/authZ (N1): self-signed JWT with roles, enforced at the single entry point ──


class TokenRequest(BaseModel):
    subject: str
    role: str


@app.post("/auth/token")
def auth_token(request_body: TokenRequest) -> dict:
    """Dev-mode identity provider: issue a signed token for a subject+role.
    In production this endpoint is replaced by a real IdP; services don't change."""
    if request_body.role not in ROLES:
        raise HTTPException(status_code=400, detail=f"role must be one of {ROLES}")
    return {"token": issue_token(request_body.subject, request_body.role), "role": request_body.role}


def _claims(request: Request) -> dict:
    """Bearer header first; `access_token` query as the SSE fallback (EventSource
    cannot set headers)."""
    header = request.headers.get("Authorization", "")
    token = header.removeprefix("Bearer ").strip() if header.startswith("Bearer ") else ""
    token = token or request.query_params.get("access_token", "")
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        return verify_token(token)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def require(*allowed_roles: str):
    """FastAPI dependency: authenticate, then authorize by role."""

    def dependency(request: Request) -> dict:
        claims = _claims(request)
        if claims["role"] not in allowed_roles:
            raise HTTPException(
                status_code=403,
                detail=f"role '{claims['role']}' may not access this resource",
            )
        return claims

    return dependency


any_user = require("submitter", "approver", "admin")
approvers = require("approver", "admin")
admins = require("admin")


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
def submit_invoice(invoice: InvoiceSubmission, request: Request, user: dict = Depends(any_user)):
    """Validate at the edge, then forward to intake via Dapr service invocation."""
    return _proxy("intake", "invoices", "POST", invoice.model_dump(by_alias=True))


@app.get("/invoices/{tracking_id}/status")
def invoice_status(tracking_id: str, request: Request, user: dict = Depends(any_user)):
    return _proxy("intake", f"invoices/{tracking_id}/status")


@app.post("/invoices/{tracking_id}/reply")
async def invoice_reply(tracking_id: str, request: Request, user: dict = Depends(any_user)):
    """The submitter's answer to a request_info — resumes the paused workflow (F5)."""
    return _proxy("orchestrator", f"submissions/{tracking_id}/reply", "POST", await request.json())


@app.get("/invoices/{tracking_id}/events")
async def invoice_events(tracking_id: str, request: Request, user: dict = Depends(any_user)):
    """Live status stream (SSE) for the submitter (M8/F2).

    Streams straight over HTTP rather than Dapr invocation: SSE is a long-lived
    streaming session, and the invocation pipeline is built for request/response.
    App-plane traffic everywhere else stays on Dapr.
    """

    async def stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET", f"http://notification:8000/events/{tracking_id}"
                ) as upstream:
                    async for chunk in upstream.aiter_raw():
                        yield chunk
        except httpx.HTTPError:
            yield b'data: {"status": "stream_unavailable"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


# ── approver surface ──


@app.get("/approvals")
def approvals_queue(request: Request, user: dict = Depends(approvers)):
    """Only the escalated items, each with the agent's recommendation (F4)."""
    return _proxy("orchestrator", "escalations")


@app.post("/approvals/{tracking_id}/decision")
async def approvals_decision(tracking_id: str, request: Request, user: dict = Depends(approvers)):
    """approve | reject | request_info — one action resumes the workflow (F5).

    The approver identity is taken from the VERIFIED token, never from the payload —
    the F9/F10 audit trail cannot be spoofed by a client."""
    body = await request.json()
    body["approver"] = user["sub"]
    return _proxy("orchestrator", f"escalations/{tracking_id}/decision", "POST", body)


# ── controller / auditor surface ──


@app.get("/budgets")
def budgets(request: Request, user: dict = Depends(admins)):
    return _proxy("payment", "budgets")


@app.get("/dashboard/metrics")
def dashboard_metrics(request: Request, user: dict = Depends(admins)):
    """Throughput, auto-vs-human rates and the money split (F8)."""
    return _proxy("audit", "metrics/summary")


@app.get("/audit/trail/{any_id}")
def audit_trail(any_id: str, request: Request, user: dict = Depends(admins)):
    """Complete decision trail by correlation OR tracking id (F9)."""
    return _proxy("audit", f"trail/{any_id}")


@app.get("/audit/autonomy-proof")
def autonomy_proof(request: Request, user: dict = Depends(admins)):
    """Evidence that no auto-approval ever exceeded the ceiling (F10)."""
    return _proxy("audit", "autonomy/proof")


@app.get("/config/posture")
def config_posture(request: Request, user: dict = Depends(admins)):
    """The live autonomy posture the router is enforcing right now (F7/M13)."""
    return _proxy("orchestrator", "policy/posture")
