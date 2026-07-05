"""The invoice lifecycle as a durable Dapr Workflow (M9 + M11).

The workflow function is pure control flow — every side effect lives in an activity,
so a crash at any point replays the recorded history and resumes exactly where it
stopped. The human pause is `wait_for_external_event`: durable by construction.

    decide ──▶ reject? ──▶ finalize(rejected)
        │
        ├─▶ human_review? ──▶ [pending ⇄ request_info/reply loop] ──▶ reject? ─▶ finalize
        │                                                             approve ─┐
        └─▶ auto_approve ──────────────────────────────────────────────────────┤
                                                                                ▼
                                              reserve budget ──▶ execute payment
                                               │ insufficient        │ failure
                                               ▼                     ▼
                                         finalize(rejected)   COMPENSATE: release
                                                                      ▼
                                                            finalize(payment_failed)
"""

import dapr.ext.workflow as wf
from approvalflow_common import (
    AgentRecommendation,
    get_correlation_id,
    get_logger,
    get_settings,
    route_decision,
    set_correlation_id,
)
from approvalflow_common.dapr_client import invoke_service, publish_event
from approvalflow_common.policy_source import PolicySource
from approvalflow_common.schemas import InvoiceSubmission, Route
from approvalflow_common.state import DaprStateBackend
from approvalflow_common.topics import INVOICE_DECIDED, INVOICE_STATUS_CHANGED

log = get_logger("orchestrator.workflow")
settings = get_settings()

wfr = wf.WorkflowRuntime()

ESCALATION_INDEX_KEY = "escalations:index"

# Live autonomy posture (M13/F7) — TTL-cached reads from the Dapr config store.
policy_source = PolicySource()


def _backend() -> DaprStateBackend:
    return DaprStateBackend(settings.statestore_name)


# ─────────────────────────────── workflow ───────────────────────────────


@wfr.workflow(name="invoice_lifecycle")
def invoice_lifecycle(ctx: wf.DaprWorkflowContext, payload: dict):
    decision = yield ctx.call_activity(decide, input=payload)
    route = decision["route"]

    if route == Route.reject.value:
        yield ctx.call_activity(
            finalize, input={**payload, "status": "rejected", "reason": decision["plainReason"]}
        )
        return decision

    if route == Route.human_review.value:
        while True:
            yield ctx.call_activity(enqueue_escalation, input={**payload, "decision": decision})
            # Durable pause (M11): survives restarts until an approver acts.
            event = yield ctx.wait_for_external_event("approver_decision")
            action = event.get("action")
            if action == "request_info":
                yield ctx.call_activity(
                    request_info, input={**payload, "question": event.get("note", "")}
                )
                reply = yield ctx.wait_for_external_event("submitter_reply")
                payload["submitterReply"] = reply.get("message", "")
                continue  # back to the approver queue, reply attached (F5 full loop)
            break

        yield ctx.call_activity(dequeue_escalation, input=payload)
        if action == "reject":
            yield ctx.call_activity(
                finalize,
                input={
                    **payload,
                    "status": "rejected",
                    "reason": "Rejected by an approver."
                    + (f' Note: {event.get("note")}' if event.get("note") else ""),
                    "decidedBy": event.get("approver", "approver"),
                },
            )
            return {**decision, "finalStatus": "rejected", "decidedBy": event.get("approver")}
        decision["decidedBy"] = event.get("approver", "approver")

    # ── payment saga (M9): reserve -> execute; compensation on failure ──
    money = {
        "trackingId": payload["trackingId"],
        "correlationId": payload["correlationId"],
        "department": payload["invoice"]["department"],
        "amountUsd": decision["amountUsd"],
        "scenario": payload["invoice"].get("scenario"),
    }
    reserved = yield ctx.call_activity(reserve_budget, input=money)
    if not reserved["ok"]:
        yield ctx.call_activity(
            finalize,
            input={
                **payload,
                "status": "rejected",
                "reason": f"Approved, but the department budget cannot fund it ({reserved['reason']}).",
            },
        )
        return {**decision, "finalStatus": "rejected_insufficient_budget"}

    paid = yield ctx.call_activity(execute_payment, input=money)
    if not paid["ok"]:
        # COMPENSATION: undo the reservation — no orphans, no partial payments.
        yield ctx.call_activity(release_budget, input=money)
        yield ctx.call_activity(
            finalize,
            input={
                **payload,
                "status": "payment_failed",
                "reason": "Payment failed; the budget reservation was rolled back.",
            },
        )
        return {**decision, "finalStatus": "payment_failed"}

    yield ctx.call_activity(
        finalize,
        input={
            **payload,
            "status": "paid",
            "reason": decision["plainReason"] + " Payment completed.",
            "autonomous": decision["autonomous"],
            "decidedBy": decision.get("decidedBy"),
        },
    )
    return {**decision, "finalStatus": "paid"}


# ─────────────────────────────── activities ───────────────────────────────


@wfr.activity(name="decide")
def decide(ctx: wf.WorkflowActivityContext, payload: dict) -> dict:
    set_correlation_id(payload.get("correlationId", ""))
    tracking_id = payload["trackingId"]
    invoice = InvoiceSubmission.model_validate(payload["invoice"])

    recommendation = _get_recommendation(invoice, tracking_id)
    # Live posture from the config store (M13/F7): tuning a threshold is a redis SET,
    # not a redeploy. Falls back loudly to env defaults if the store is unreachable.
    config = policy_source.current()
    decision = route_decision(invoice, recommendation, config)

    record = {
        "trackingId": tracking_id,
        "correlationId": get_correlation_id(),
        "route": decision.route.value,
        "autonomous": decision.autonomous,
        "amountUsd": decision.amount_usd,
        "ruleIds": decision.rule_ids,
        "reasons": decision.reasons,
        "plainReason": decision.plain_reason,
        "agent": recommendation.model_dump(),
    }
    _backend().try_create(f"decision:{tracking_id}", record)
    publish_event(INVOICE_DECIDED, record)

    status = "auto_approved" if decision.route is Route.auto_approve else (
        "pending_approval" if decision.route is Route.human_review else "rejected"
    )
    _publish_status(payload, status, decision.plain_reason)
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
    return record


@wfr.activity(name="enqueue_escalation")
def enqueue_escalation(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    """Add the item to the approver queue (F4) with the agent's full rationale."""
    set_correlation_id(payload.get("correlationId", ""))
    tracking_id = payload["trackingId"]
    invoice = payload["invoice"]
    decision = payload["decision"]
    entry = {
        "trackingId": tracking_id,
        "vendor": invoice["vendor"],
        "department": invoice["department"],
        "amountUsd": decision["amountUsd"],
        "category": invoice.get("category"),
        "notes": invoice.get("notes"),
        "submitterReply": payload.get("submitterReply"),
        "agent": decision["agent"],
        "ruleIds": decision["ruleIds"],
        "reasons": decision["reasons"],
    }
    backend = _backend()
    backend.try_create(f"escalation:{tracking_id}", entry) or _overwrite(backend, f"escalation:{tracking_id}", entry)
    _index_update(backend, add=tracking_id)
    _publish_status(payload, "pending_approval", "Waiting for a human approver.")


@wfr.activity(name="dequeue_escalation")
def dequeue_escalation(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    _index_update(_backend(), remove=payload["trackingId"])


@wfr.activity(name="request_info")
def request_info(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    set_correlation_id(payload.get("correlationId", ""))
    _index_update(_backend(), remove=payload["trackingId"])
    _publish_status(
        payload,
        "info_requested",
        payload.get("question") or "The approver needs more information; please reply.",
    )


@wfr.activity(name="reserve_budget")
def reserve_budget(ctx: wf.WorkflowActivityContext, money: dict) -> dict:
    set_correlation_id(money.get("correlationId", ""))
    return _call_payment("reserve", {k: money[k] for k in ("trackingId", "department", "amountUsd")})


@wfr.activity(name="execute_payment")
def execute_payment(ctx: wf.WorkflowActivityContext, money: dict) -> dict:
    set_correlation_id(money.get("correlationId", ""))
    return _call_payment(
        "execute",
        {"trackingId": money["trackingId"], "amountUsd": money["amountUsd"], "scenario": money.get("scenario")},
    )


@wfr.activity(name="release_budget")
def release_budget(ctx: wf.WorkflowActivityContext, money: dict) -> dict:
    set_correlation_id(money.get("correlationId", ""))
    return _call_payment("release", {"trackingId": money["trackingId"]})


@wfr.activity(name="finalize")
def finalize(ctx: wf.WorkflowActivityContext, payload: dict) -> None:
    set_correlation_id(payload.get("correlationId", ""))
    _publish_status(payload, payload["status"], payload["reason"], decided_by=payload.get("decidedBy"))
    log.info(
        "workflow finalized",
        extra={"event": "finalized", "trackingId": payload["trackingId"], "status": payload["status"]},
    )


# ─────────────────────────────── helpers ───────────────────────────────


def _get_recommendation(invoice: InvoiceSubmission, tracking_id: str) -> AgentRecommendation:
    """Fail CLOSED: on any agent failure return zero confidence -> the router escalates."""
    try:
        response = invoke_service(
            "ai-decision", "recommend", "POST", invoice.model_dump(by_alias=True), timeout=60
        )
        if response.status_code == 200:
            return AgentRecommendation.model_validate(response.json())
        log.error("agent returned an error status", extra={"status": response.status_code, "trackingId": tracking_id})
    except Exception as exc:
        log.error("agent invocation failed", extra={"error": str(exc), "trackingId": tracking_id})
    return AgentRecommendation(
        proposed_route=Route.human_review,
        confidence=0.0,
        category=invoice.category,
        reason="The automated analyst was unavailable, so a person will review this item.",
    )


def _call_payment(method: str, body: dict) -> dict:
    try:
        response = invoke_service("payment", method, "POST", body, timeout=30)
        if response.status_code == 200:
            return response.json()
        log.error("payment call failed", extra={"method": method, "status": response.status_code})
        return {"ok": False, "reason": f"payment_{response.status_code}"}
    except Exception as exc:
        log.error("payment invocation error", extra={"method": method, "error": str(exc)})
        return {"ok": False, "reason": "payment_unreachable"}


def _publish_status(payload: dict, status: str, reason: str, decided_by: str | None = None) -> None:
    event = {
        "trackingId": payload["trackingId"],
        "correlationId": payload.get("correlationId", get_correlation_id()),
        "status": status,
        "reason": reason,
    }
    if decided_by:
        event["decidedBy"] = decided_by
    publish_event(INVOICE_STATUS_CHANGED, event)


def _overwrite(backend: DaprStateBackend, key: str, value: dict) -> bool:
    current, etag = backend.get(key)
    return backend.save_cas(key, value, etag) if current is not None else backend.try_create(key, value)


def _index_update(backend: DaprStateBackend, add: str | None = None, remove: str | None = None) -> None:
    """CAS loop on the escalation index — safe under concurrent workflows."""
    for _ in range(8):
        current, etag = backend.get(ESCALATION_INDEX_KEY)
        if current is None:
            if backend.try_create(ESCALATION_INDEX_KEY, {"ids": [add] if add else []}):
                return
            continue
        ids = set(current.get("ids", []))
        if add:
            ids.add(add)
        if remove:
            ids.discard(remove)
        if backend.save_cas(ESCALATION_INDEX_KEY, {"ids": sorted(ids)}, etag):
            return
    log.error("escalation index update failed after retries", extra={"add": add, "remove": remove})
