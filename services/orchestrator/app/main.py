"""Orchestrator service (Manager).

Hosts the durable invoice workflow (see workflow.py) and the approver API:
- `invoice.submitted` -> schedules a workflow instance (id = tracking id, so a
  redelivered event cannot start a second instance — M10);
- GET /escalations -> the approver queue with the agent's rationale (F4);
- POST /escalations/{id}/decision -> approve / reject / request_info resumes the
  durably-paused workflow exactly where it stopped (F5, M11);
- POST /submissions/{id}/reply -> the submitter's answer re-enters the loop.
"""

from contextlib import asynccontextmanager

from approvalflow_common import create_app, get_logger, get_settings, set_correlation_id
from approvalflow_common.state import DaprStateBackend
from approvalflow_common.topics import INVOICE_SUBMITTED
from dapr.ext.fastapi import DaprApp
from dapr.ext.workflow import DaprWorkflowClient
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from .workflow import ESCALATION_INDEX_KEY, invoice_lifecycle, wfr

SERVICE = "orchestrator"

settings = get_settings()
log = get_logger(SERVICE)


@asynccontextmanager
async def workflow_lifespan(_: FastAPI):
    wfr.start()
    log.info("workflow runtime started", extra={"event": "workflow_runtime_up"})
    yield
    wfr.shutdown()


app = create_app(SERVICE, lifespan_extra=workflow_lifespan)
dapr_app = DaprApp(app)


class ApproverDecision(BaseModel):
    action: str  # approve | reject | request_info
    note: str = ""
    approver: str = "approver"


class SubmitterReply(BaseModel):
    message: str


def _client() -> DaprWorkflowClient:
    return DaprWorkflowClient()


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_SUBMITTED)
async def on_invoice_submitted(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")
    try:
        _client().schedule_new_workflow(
            workflow=invoice_lifecycle, input=data, instance_id=tracking_id
        )
        log.info("workflow scheduled", extra={"event": "scheduled", "trackingId": tracking_id})
    except Exception as exc:
        detail = str(exc)
        if "already exists" in detail.lower():
            # Redelivered event; the instance id makes scheduling exactly-once (M10).
            log.info("duplicate delivery ignored", extra={"trackingId": tracking_id})
        else:
            # Real failure (e.g. infrastructure): never swallow it (M15) — ask Dapr to
            # redeliver so the invoice is not lost; scheduling stays idempotent.
            log.error(
                "workflow scheduling failed; requesting redelivery",
                extra={"trackingId": tracking_id, "error": detail[:200]},
            )
            return {"status": "RETRY"}
    return {"success": True}


@app.get("/escalations")
def list_escalations() -> dict:
    """The approver queue (F4): only escalated items, each with the agent's rationale."""
    backend = DaprStateBackend(settings.statestore_name)
    index, _ = backend.get(ESCALATION_INDEX_KEY)
    entries = []
    for tracking_id in (index or {}).get("ids", []):
        entry, _ = backend.get(f"escalation:{tracking_id}")
        if entry:
            entries.append(entry)
    return {"escalations": entries}


@app.post("/escalations/{tracking_id}/decision")
def approver_decision(tracking_id: str, decision: ApproverDecision) -> dict:
    if decision.action not in ("approve", "reject", "request_info"):
        raise HTTPException(status_code=422, detail="action must be approve | reject | request_info")
    if decision.action == "request_info" and not decision.note.strip():
        raise HTTPException(status_code=422, detail="request_info needs a note with the question")
    try:
        _client().raise_workflow_event(
            instance_id=tracking_id,
            event_name="approver_decision",
            data=decision.model_dump(),
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"no waiting workflow for '{tracking_id}'") from exc
    log.info(
        "approver decision raised",
        extra={"event": "approver_decision", "trackingId": tracking_id, "action": decision.action},
    )
    return {"trackingId": tracking_id, "action": decision.action, "accepted": True}


@app.post("/submissions/{tracking_id}/reply")
def submitter_reply(tracking_id: str, reply: SubmitterReply) -> dict:
    try:
        _client().raise_workflow_event(
            instance_id=tracking_id, event_name="submitter_reply", data=reply.model_dump()
        )
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"no waiting workflow for '{tracking_id}'") from exc
    log.info("submitter reply raised", extra={"event": "submitter_reply", "trackingId": tracking_id})
    return {"trackingId": tracking_id, "accepted": True}
