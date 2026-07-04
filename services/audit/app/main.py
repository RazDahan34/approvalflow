"""Audit / Reporting service (Engine + ResourceAccess).

A pure event consumer (event-driven reporting): it materializes
- the complete decision trail per correlation id (F9),
- dashboard aggregates: throughput, auto-vs-human rates, money split (F8),
- the autonomy ledger proving no auto-approval ever exceeded the ceiling (F10).

Backed by its own Postgres-based Dapr state component (database-per-service): the
same state API as everyone else, a different database behind it (ADR-0009).
"""

from collections.abc import Callable
from datetime import UTC, datetime

from approvalflow_common import create_app, get_logger, get_settings, set_correlation_id
from approvalflow_common.state import DaprStateBackend
from approvalflow_common.topics import INVOICE_DECIDED, INVOICE_STATUS_CHANGED, INVOICE_SUBMITTED
from dapr.ext.fastapi import DaprApp
from fastapi import HTTPException, Request

SERVICE = "audit"

settings = get_settings()
app = create_app(SERVICE)
dapr_app = DaprApp(app)
log = get_logger(SERVICE)

METRICS_KEY = "metrics:summary"
LEDGER_KEY = "autonomy:ledger"

EMPTY_METRICS = {
    "submitted": 0,
    "decided": 0,
    "auto_approved": 0,
    "escalated": 0,
    "rejected_by_router": 0,
    "paid": 0,
    "payment_failed": 0,
    "money_auto_usd": 0.0,
    "money_human_usd": 0.0,
}
EMPTY_LEDGER = {"autonomous_count": 0, "max_autonomous_usd": 0.0, "violations": []}


def _backend() -> DaprStateBackend:
    return DaprStateBackend(settings.statestore_name)


def _update(key: str, mutate: Callable[[dict], dict], empty: dict) -> None:
    """CAS read-modify-write loop; concurrent events land safely."""
    backend = _backend()
    for _ in range(8):
        current, etag = backend.get(key)
        if current is None:
            if backend.try_create(key, mutate(dict(empty))):
                return
        elif backend.save_cas(key, mutate(current), etag):
            return
    log.error("audit update lost after retries", extra={"key": key})


def _append_trail(correlation_id: str, entry: dict) -> None:
    def mutate(trail: dict) -> dict:
        trail.setdefault("events", []).append(entry)
        return trail

    _update(f"trail:{correlation_id}", mutate, {"events": []})


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_SUBMITTED)
async def on_submitted(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(cid := data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")
    invoice = data.get("invoice", {})

    _backend().try_create(f"tmap:{tracking_id}", {"correlationId": cid})
    _append_trail(
        cid,
        {
            "at": data.get("submittedAt") or _now(),
            "type": "submitted",
            "trackingId": tracking_id,
            "vendor": invoice.get("vendor"),
            "department": invoice.get("department"),
            "total": invoice.get("total"),
            "currency": invoice.get("currency"),
        },
    )

    def mutate(metrics: dict) -> dict:
        metrics["submitted"] += 1
        return metrics

    _update(METRICS_KEY, mutate, EMPTY_METRICS)
    return {"success": True}


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_DECIDED)
async def on_decided(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(cid := data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")
    autonomous = bool(data.get("autonomous"))
    amount = float(data.get("amountUsd") or 0.0)
    route = data.get("route", "")

    # Keep the decision context for the money split at payment time.
    _backend().try_create(
        f"adecision:{tracking_id}", {"amountUsd": amount, "autonomous": autonomous, "route": route}
    )
    _append_trail(
        cid,
        {
            "at": data.get("decidedAt") or _now(),
            "type": "decided",
            "trackingId": tracking_id,
            "route": route,
            "autonomous": autonomous,
            "amountUsd": amount,
            "ruleIds": data.get("ruleIds", []),
            "agent": data.get("agent", {}),
        },
    )

    def mutate(metrics: dict) -> dict:
        metrics["decided"] += 1
        if route == "auto_approve":
            metrics["auto_approved"] += 1
        elif route == "human_review":
            metrics["escalated"] += 1
        elif route == "reject":
            metrics["rejected_by_router"] += 1
        return metrics

    _update(METRICS_KEY, mutate, EMPTY_METRICS)

    if autonomous:
        envelope_usd = settings.autonomy_ceiling_usd

        def ledger_mutate(ledger: dict) -> dict:
            ledger["autonomous_count"] += 1
            ledger["max_autonomous_usd"] = max(ledger["max_autonomous_usd"], amount)
            if amount > envelope_usd:  # must never happen — recorded as hard evidence (F10)
                ledger["violations"].append({"trackingId": tracking_id, "amountUsd": amount, "at": _now()})
            return ledger

        _update(LEDGER_KEY, ledger_mutate, EMPTY_LEDGER)
    return {"success": True}


@dapr_app.subscribe(pubsub="pubsub", topic=INVOICE_STATUS_CHANGED)
async def on_status_changed(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(cid := data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")
    status = data.get("status", "")

    entry = {"at": _now(), "type": "status", "trackingId": tracking_id, "status": status}
    if data.get("decidedBy"):
        entry["decidedBy"] = data["decidedBy"]
    if data.get("reason"):
        entry["reason"] = data["reason"]
    _append_trail(cid, entry)

    if status in ("paid", "payment_failed"):
        decision, _ = _backend().get(f"adecision:{tracking_id}")

        def mutate(metrics: dict) -> dict:
            metrics[status] += 1
            if status == "paid" and decision:
                bucket = "money_auto_usd" if decision.get("autonomous") else "money_human_usd"
                metrics[bucket] = round(metrics[bucket] + decision.get("amountUsd", 0.0), 2)
            return metrics

        _update(METRICS_KEY, mutate, EMPTY_METRICS)
    return {"success": True}


# ───────────────────────────── query surface ─────────────────────────────


@app.get("/trail/{any_id}")
def trail(any_id: str) -> dict:
    """The complete decision trail, by correlation id OR tracking id (F9)."""
    backend = _backend()
    mapping, _ = backend.get(f"tmap:{any_id}")
    correlation_id = mapping["correlationId"] if mapping else any_id
    record, _ = backend.get(f"trail:{correlation_id}")
    if record is None:
        raise HTTPException(status_code=404, detail="no trail for this id")
    events = sorted(record.get("events", []), key=lambda e: e.get("at", ""))
    return {"correlationId": correlation_id, "events": events}


@app.get("/metrics/summary")
def metrics_summary() -> dict:
    """Dashboard aggregates (F8)."""
    metrics, _ = _backend().get(METRICS_KEY)
    metrics = metrics or dict(EMPTY_METRICS)
    decided = metrics["decided"] or 1
    metrics["auto_rate"] = round(metrics["auto_approved"] / decided, 3)
    metrics["escalation_rate"] = round(metrics["escalated"] / decided, 3)
    return metrics


@app.get("/autonomy/proof")
def autonomy_proof() -> dict:
    """The auditor's F10 query: evidence that autonomy never exceeded the ceiling."""
    ledger, _ = _backend().get(LEDGER_KEY)
    ledger = ledger or dict(EMPTY_LEDGER)
    return {
        "envelopeUsd": settings.autonomy_ceiling_usd,
        **ledger,
        "ceilingRespected": not ledger["violations"],
    }
