"""Orchestrator service (Manager).

Subscribes to `invoice.submitted`, obtains the agent's *advisory* recommendation via
Dapr service invocation (ai-decision), then runs the DETERMINISTIC ROUTER for the
binding decision, persists the decision record, and publishes `invoice.decided`.

The agent recommends; this service decides (M12/F10). The payment saga and the durable
HITL pause/resume are layered on in Phase 3.
"""

from datetime import UTC, datetime

from approvalflow_common import (
    AgentRecommendation,
    PolicyConfig,
    create_app,
    get_correlation_id,
    get_logger,
    get_settings,
    route_decision,
    set_correlation_id,
)
from approvalflow_common.dapr_client import get_state, invoke_service, publish_event, save_state
from approvalflow_common.schemas import InvoiceSubmission, Route
from dapr.ext.fastapi import DaprApp
from fastapi import Request

SERVICE = "orchestrator"
DECIDED_TOPIC = "invoice.decided"

settings = get_settings()
app = create_app(SERVICE)
dapr_app = DaprApp(app)
log = get_logger(SERVICE)

# Plain-language status per route (F2): submitters see words, not enum values.
STATUS_BY_ROUTE = {
    Route.auto_approve: "auto_approved",
    Route.human_review: "pending_approval",
    Route.reject: "rejected",
    Route.duplicate: "duplicate",
}


def _get_recommendation(invoice: InvoiceSubmission, tracking_id: str) -> AgentRecommendation:
    """Ask the agent. On ANY failure return a zero-confidence advisory -> the router
    escalates to a human (fail closed). Loud logs; never a silent fallback (M15)."""
    try:
        response = invoke_service(
            "ai-decision", "recommend", "POST", invoice.model_dump(by_alias=True), timeout=60
        )
        if response.status_code == 200:
            return AgentRecommendation.model_validate(response.json())
        log.error(
            "agent returned an error status",
            extra={"status": response.status_code, "trackingId": tracking_id},
        )
    except Exception as exc:
        log.error("agent invocation failed", extra={"error": str(exc), "trackingId": tracking_id})
    return AgentRecommendation(
        proposed_route=Route.human_review,
        confidence=0.0,
        category=invoice.category,
        reason="The automated analyst was unavailable, so a person will review this item.",
    )


@dapr_app.subscribe(pubsub="pubsub", topic="invoice.submitted")
async def on_invoice_submitted(request: Request) -> dict:
    envelope = await request.json()
    data = envelope.get("data", envelope)
    set_correlation_id(data.get("correlationId", ""))
    tracking_id = data.get("trackingId", "")

    # Redelivered event? The decision record makes reprocessing a no-op (M10).
    if get_state(f"decision:{tracking_id}"):
        log.info("duplicate delivery ignored", extra={"trackingId": tracking_id})
        return {"success": True}

    try:
        invoice = InvoiceSubmission.model_validate(data["invoice"])
    except Exception as exc:
        log.error("malformed invoice event", extra={"trackingId": tracking_id, "error": str(exc)})
        save_state(
            f"status:{tracking_id}",
            {"status": "error", "reason": "The submission event was malformed; contact support."},
        )
        return {"success": True}

    recommendation = _get_recommendation(invoice, tracking_id)

    config = PolicyConfig(
        ceiling_usd=settings.autonomy_ceiling_usd,
        confidence_threshold=settings.autonomy_confidence,
    )
    decision = route_decision(invoice, recommendation, config)

    decided_at = datetime.now(UTC).isoformat()
    save_state(
        f"decision:{tracking_id}",
        {
            "trackingId": tracking_id,
            "correlationId": get_correlation_id(),
            "decidedAt": decided_at,
            "route": decision.route.value,
            "autonomous": decision.autonomous,
            "amountUsd": decision.amount_usd,
            "ruleIds": decision.rule_ids,
            "reasons": decision.reasons,
            "agent": recommendation.model_dump(),
        },
    )
    save_state(
        f"status:{tracking_id}",
        {"status": STATUS_BY_ROUTE[decision.route], "reason": decision.plain_reason},
    )
    publish_event(
        DECIDED_TOPIC,
        {
            "trackingId": tracking_id,
            "correlationId": get_correlation_id(),
            "decidedAt": decided_at,
            "route": decision.route.value,
            "autonomous": decision.autonomous,
            "amountUsd": decision.amount_usd,
            "ruleIds": decision.rule_ids,
            "reason": decision.plain_reason,
            "vendor": invoice.vendor,
            "department": invoice.department,
        },
    )

    log.info(
        "decision routed",
        extra={
            "event": "decided",
            "trackingId": tracking_id,
            "route": decision.route.value,
            "autonomous": decision.autonomous,
            "amountUsd": decision.amount_usd,
            "ruleIds": decision.rule_ids,
            "agentProposed": recommendation.proposed_route.value,
            "agentConfidence": recommendation.confidence,
        },
    )
    return {"success": True}
