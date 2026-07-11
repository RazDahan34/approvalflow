"""The deterministic decision router — the heart of the autonomy guarantee.

The AI agent only *recommends*. This module takes that recommendation together with the
invoice and the (externally configurable) policy thresholds and computes the **binding**
route in pure, deterministic code. Because the ceiling and the hard stops are re-derived
here from the amount and the config — never trusted from the agent — the system is
provably incapable of auto-approving above the ceiling (M12/F10), even when the agent is
forced to recommend approval.
"""

from pydantic import BaseModel, Field

from .schemas import Category, InvoiceSubmission, Route

# Submission-date FX rates (from sample-invoices.json). In production these come from a
# rates service / config; kept explicit here so conversion is visible and testable.
FX_RATES: dict[str, float] = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27}


class PolicyConfig(BaseModel):
    """Everything the router enforces. Tuning the dilemma == changing these values."""

    # The absolute autonomy ENVELOPE: no category's ceiling may exceed it — the router
    # clamps with min(envelope, category). One number an auditor can hold on to (F10):
    # nothing above $500 is ever machine-signed, whatever the category or config says.
    ceiling_usd: float = 500.0
    confidence_threshold: float = 0.80
    # The per-category posture (the dilemma's answer), explicit for every category.
    # Autonomy follows structure: categories with strong deterministic guards get more
    # (travel: economy-only + the $1,500 manager line; hardware: the $1,000 capital
    # line), the unstructured `other` gets the least. saas aligns with the SAAS-01 cap
    # (defence in depth: two independent knobs). Values above the envelope are clamped.
    category_ceilings: dict[Category, float] = Field(
        default_factory=lambda: {
            Category.meals: 250.0,
            Category.travel: 500.0,
            Category.saas: 200.0,
            Category.hardware: 350.0,
            Category.other: 100.0,
        }
    )
    saas_monthly_cap: float = 200.0
    hardware_cap: float = 1000.0
    meal_per_attendee_cap: float = 75.0
    travel_single_cap: float = 1500.0
    receipt_required_over: float = 25.0
    client_entertainment_threshold: float = 500.0
    fx_hard_stop_over: float = 1000.0


class AgentRecommendation(BaseModel):
    """What the AI agent proposes — advisory only; the router is free to override it."""

    proposed_route: Route = Route.human_review
    confidence: float = 0.0
    category: Category = Category.other
    policy_violations: list[str] = Field(default_factory=list)
    fraud_signals: list[str] = Field(default_factory=list)
    reason: str = ""


class RouterDecision(BaseModel):
    route: Route
    autonomous: bool = False
    rule_ids: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    amount_usd: float = 0.0
    plain_reason: str = ""
    # The ceiling actually enforced for THIS decision (min of envelope and category
    # tier) — recorded on the decided event so the F10 audit evidence reflects the
    # config at decision time, not whatever it is when someone asks.
    enforced_ceiling_usd: float = 0.0


def to_usd(invoice: InvoiceSubmission) -> float:
    return round(invoice.total * FX_RATES.get(invoice.currency.upper(), 1.0), 2)


def line_items_total(invoice: InvoiceSubmission) -> float:
    return round(sum(li.quantity * li.unit_price for li in invoice.line_items), 2)


def route_decision(
    invoice: InvoiceSubmission,
    recommendation: AgentRecommendation,
    config: PolicyConfig,
) -> RouterDecision:
    """Compute the binding route. Auto-approve only when *nothing* blocks it."""
    amount = to_usd(invoice)
    # The effective ceiling is needed by the FX hard stop too, so derive it up front.
    category_ceiling = config.category_ceilings.get(invoice.category, config.ceiling_usd)
    effective_ceiling = min(config.ceiling_usd, category_ceiling)
    rule_ids: list[str] = []
    reasons: list[str] = []

    def block(rule: str, reason: str) -> None:
        rule_ids.append(rule)
        reasons.append(reason)

    # ── Reject-level: not reimbursable at all (agent-detected semantic rule) ──
    reject = "MEAL-03" in recommendation.policy_violations
    if reject:
        block("MEAL-03", "Alcohol-only receipts are not reimbursable.")

    # ── Deterministic hard stops — computed here, never trusted from the agent ──
    reconciled = line_items_total(invoice) + invoice.tax_amount
    if abs(reconciled - invoice.total) > 0.01:
        block("GLOBAL-MATH", f"Line items + tax ({reconciled:.2f}) do not match the total ({invoice.total:.2f}).")
    if invoice.total > config.receipt_required_over and not invoice.receipt_present:
        block("GLOBAL-RECEIPT", "A receipt is required for any expense over $25.")
    if not invoice.vendor_known:
        block("GLOBAL-VENDOR", "A new / unknown vendor is always reviewed by a human.")
    currency = invoice.currency.upper()
    if currency != "USD":
        if currency not in FX_RATES:
            # Fail closed: without a rate on file we cannot know the real USD value,
            # so the ceiling cannot be trusted — a person must look at it.
            block("GLOBAL-FX", f"No exchange rate on file for '{invoice.currency}'; cannot safely convert.")
        elif amount > effective_ceiling or amount > config.fx_hard_stop_over:
            block("GLOBAL-FX", f"Foreign-currency amount (~${amount:.2f}) is over the FX hard-stop.")
    if recommendation.fraud_signals:
        block("GLOBAL-FRAUD", "Fraud-pattern signal(s): " + ", ".join(recommendation.fraud_signals) + ".")

    # ── Per-category policy compliance ──
    _check_category(invoice, amount, recommendation, config, block)

    # ── Autonomy gate (the dilemma): the category's ceiling, clamped by the envelope ──
    if amount > effective_ceiling:
        block(
            "AUTONOMY-CEILING",
            f"${amount:.2f} is over the {invoice.category.value} autonomy ceiling of ${effective_ceiling:.0f}.",
        )
    if recommendation.confidence < config.confidence_threshold:
        block(
            "AUTONOMY-CONFIDENCE",
            f"Agent confidence {recommendation.confidence:.2f} is below {config.confidence_threshold:.2f}.",
        )

    # ── Final route: reject > human_review > auto_approve. The agent may escalate
    #    (ask for a human) but can never force an auto-approval. ──
    agent_wants_human = recommendation.proposed_route in (Route.human_review, Route.reject)
    if reject:
        route = Route.reject
        plain = "Rejected: this expense is not reimbursable under policy."
    elif rule_ids or agent_wants_human:
        route = Route.human_review
        if not rule_ids:
            reasons.append("The agent flagged this for human review.")
        plain = "Sent to a person for review — " + reasons[0]
    else:
        route = Route.auto_approve
        plain = "Automatically approved: in policy, under the autonomy limits, and no red flags."

    return RouterDecision(
        route=route,
        autonomous=route is Route.auto_approve,
        rule_ids=rule_ids,
        reasons=reasons,
        amount_usd=amount,
        plain_reason=plain,
        enforced_ceiling_usd=effective_ceiling,
    )


def _check_category(
    invoice: InvoiceSubmission,
    amount: float,
    rec: AgentRecommendation,
    config: PolicyConfig,
    block,
) -> None:
    cat = invoice.category
    if cat is Category.saas:
        if amount > config.saas_monthly_cap:
            block("SAAS-01", f"SaaS ${amount:.2f} exceeds the ${config.saas_monthly_cap:.0f}/month cap.")
    elif cat is Category.hardware:
        if amount > config.hardware_cap:
            block("HW-02", f"Hardware ${amount:.2f} over ${config.hardware_cap:.0f} is a capital expense.")
    elif cat is Category.meals:
        if invoice.attendees is None:
            block("MEAL-01", "Meals require an attendee count.")
        elif invoice.attendees > 0 and amount / invoice.attendees > config.meal_per_attendee_cap:
            block(
                "MEAL-01",
                f"Meals ${amount / invoice.attendees:.2f}/attendee exceed the ${config.meal_per_attendee_cap:.0f} cap.",
            )
        if amount > config.client_entertainment_threshold and "MEAL-02" in rec.policy_violations:
            block("MEAL-02", "Client entertainment over $500 needs a client name and justification.")
    elif cat is Category.travel:
        if amount > config.travel_single_cap:
            block(
                "TRAVEL-02",
                f"Single travel expense ${amount:.2f} over ${config.travel_single_cap:.0f} needs manager approval.",
            )
        # TRAVEL-03 is a hard stop in policy.md, so the router checks it BOTH ways:
        # a deterministic text sweep (a premium-cabin fare under the travel ceiling
        # must not slip through a blind model) plus whatever the agent flagged.
        travel_text = " ".join(
            [invoice.notes or ""] + [item.description for item in invoice.line_items]
        ).lower()
        premium_marker = any(
            marker in travel_text
            for marker in ("business class", "business-class", "first class", "first-class")
        )
        if premium_marker or "TRAVEL-03" in rec.policy_violations:
            block("TRAVEL-03", "First/business-class travel always requires approval.")
