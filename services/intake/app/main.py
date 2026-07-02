"""Intake service.

Accepts a submission, acknowledges immediately with a tracking id (never blocks on
processing — F1/M8), de-duplicates accidental re-submissions (F3/M10), and publishes
`invoice.submitted` for the Orchestrator to pick up.
"""

import uuid
from datetime import UTC, datetime

from approvalflow_common import create_app, get_correlation_id, get_logger
from approvalflow_common.dapr_client import get_state, publish_event, save_state
from approvalflow_common.schemas import InvoiceSubmission, InvoiceSubmittedEvent
from fastapi import HTTPException

SERVICE = "intake"
TOPIC = "invoice.submitted"

app = create_app(SERVICE)
log = get_logger(SERVICE)


@app.post("/invoices", status_code=202)
def submit_invoice(invoice: InvoiceSubmission) -> dict:
    """Return 202 Accepted with a tracking id, then process asynchronously."""
    cid = get_correlation_id()
    idem_key = f"idem:{invoice.idempotency_key()}"

    existing = get_state(idem_key)
    if existing:
        # Accidental double-send: same tracking id, no second event, no second pay.
        log.info(
            "duplicate submission short-circuited",
            extra={"event": "idempotent_hit", "trackingId": existing["trackingId"]},
        )
        return {
            "trackingId": existing["trackingId"],
            "status": "accepted",
            "duplicate": True,
        }

    tracking_id = uuid.uuid4().hex
    save_state(idem_key, {"trackingId": tracking_id, "correlationId": cid})
    save_state(
        f"status:{tracking_id}",
        {"status": "received", "reason": "Submitted; awaiting an automated decision."},
    )

    event = InvoiceSubmittedEvent(
        trackingId=tracking_id,
        correlationId=cid,
        submittedAt=datetime.now(UTC).isoformat(),
        invoice=invoice,
    )
    publish_event(TOPIC, event.model_dump(by_alias=True))

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
    """Plain-language status for the submitter (F2)."""
    record = get_state(f"status:{tracking_id}")
    if not record:
        raise HTTPException(status_code=404, detail="unknown tracking id")
    return {"trackingId": tracking_id, **record}
