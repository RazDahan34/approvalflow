"""Intake service.

Accepts a submission, acknowledges immediately with a tracking id (never blocks on
processing — F1/M8), de-duplicates re-submissions atomically (F3/M10), and publishes
`invoice.submitted` for the Orchestrator.

Intake also OWNS the submitter-facing status projection (F2): the Orchestrator
publishes `invoice.status-changed` events and intake materializes them into its own
store — services never touch each other's data (database-per-service).
"""

import uuid
from datetime import UTC, datetime

from approvalflow_common import create_app, get_correlation_id, get_logger, get_settings, set_correlation_id
from approvalflow_common.correlation import get_traceparent
from approvalflow_common.dapr_client import publish_event
from approvalflow_common.schemas import InvoiceSubmission, InvoiceSubmittedEvent
from approvalflow_common.state import DaprStateBackend
from approvalflow_common.topics import INVOICE_STATUS_CHANGED, INVOICE_SUBMITTED
from dapr.ext.fastapi import DaprApp
from fastapi import HTTPException, Request

SERVICE = "intake"

settings = get_settings()
app = create_app(SERVICE)
dapr_app = DaprApp(app)
log = get_logger(SERVICE)


def _backend() -> DaprStateBackend:
    return DaprStateBackend(settings.statestore_name)


@app.post("/invoices", status_code=202)
def submit_invoice(invoice: InvoiceSubmission) -> dict:
    """Return 202 Accepted with a tracking id, then process asynchronously."""
    cid = get_correlation_id()
    backend = _backend()
    idem_key = f"idem:{invoice.idempotency_key()}"
    tracking_id = uuid.uuid4().hex

    # Atomic create-only claim: under two concurrent identical submissions exactly ONE
    # wins the key; the loser reads the winner's record. No TOCTOU window (M10).
    if not backend.try_create(idem_key, {"trackingId": tracking_id, "correlationId": cid}):
        existing, _ = backend.get(idem_key)
        log.info(
            "duplicate submission short-circuited",
            extra={"event": "idempotent_hit", "trackingId": existing["trackingId"]},
        )
        return {"trackingId": existing["trackingId"], "status": "accepted", "duplicate": True}

    backend.try_create(
        f"status:{tracking_id}",
        {"status": "received", "reason": "Submitted; awaiting an automated decision."},
    )

    event = InvoiceSubmittedEvent(
        trackingId=tracking_id,
        correlationId=cid,
        submittedAt=datetime.now(UTC).isoformat(),
        traceparent=get_traceparent() or None,
        invoice=invoice,
    )
    publish_event(INVOICE_SUBMITTED, event.model_dump(by_alias=True))

    log.info(
        "invoice accepted",
        extra={
            "event": "accepted",
            "trackingId": tracking_id,
            "vendor": invoice.vendor,
            "total": invoice.total,
            "currency": invoice.currency,
        },
    )
    return {"trackingId": tracking_id, "status": "accepted"}


@app.get("/invoices/{tracking_id}/status")
def get_invoice_status(tracking_id: str) -> dict:
    """Plain-language status for the submitter (F2), served from intake's projection."""
    record, _ = _backend().get(f"status:{tracking_id}")
    if not record:
        raise HTTPException(status_code=404, detail="unknown tracking id")
    return {"trackingId": tracking_id, **record}


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_STATUS_CHANGED)
async def on_status_changed(request: Request) -> dict:
    """Materialize the Orchestrator's status events into intake's own projection."""
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")

    backend = _backend()
    projection = {"status": data.get("status"), "reason": data.get("reason")}
    if data.get("decidedBy"):
        projection["decidedBy"] = data["decidedBy"]
    current, etag = backend.get(f"status:{tracking_id}")
    if current is None:
        backend.try_create(f"status:{tracking_id}", projection)
    else:
        backend.save_cas(f"status:{tracking_id}", projection, etag)

    log.info(
        "status projected",
        extra={"event": "status_projected", "trackingId": tracking_id, "status": data.get("status")},
    )
    return {"success": True}
