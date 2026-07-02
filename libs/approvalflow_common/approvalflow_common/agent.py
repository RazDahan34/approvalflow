"""The AI agent's classification + a deterministic offline stub provider.

The agent EXTRACTS/classifies an invoice and emits an advisory recommendation (category,
confidence, cited policy rules, fraud signals). The real LLM provider is swapped in by
configuration (M15); the stub here is fully deterministic — used for CI and the eval
harness so tests never depend on a network or a model's mood.
"""

from typing import Protocol

from .decision import AgentRecommendation
from .schemas import Category, InvoiceSubmission, Route


class AgentProvider(Protocol):
    name: str

    def recommend(self, invoice: InvoiceSubmission) -> AgentRecommendation: ...


class StubProvider:
    """Deterministic heuristics that stand in for the LLM offline."""

    name = "stub"

    def recommend(self, invoice: InvoiceSubmission) -> AgentRecommendation:
        parts = [invoice.notes or ""] + [li.description for li in invoice.line_items]
        text = " ".join(parts).lower()

        violations: list[str] = []
        fraud: list[str] = []
        confidence = 0.95

        # Alcohol-only receipts are not reimbursable.
        if invoice.category is Category.meals and ("alcohol" in text or "bar tab" in text):
            violations.append("MEAL-03")
        # Client entertainment over $500 needs a client name + justification.
        if invoice.category is Category.meals and invoice.total > 500:
            violations.append("MEAL-02")
        # First/business-class travel always needs approval.
        if invoice.category is Category.travel and ("business class" in text or "first class" in text):
            violations.append("TRAVEL-03")
        # Fraud pattern: a round-number total to a brand-new vendor.
        if invoice.total >= 1000 and invoice.total % 1000 == 0 and not invoice.vendor_known:
            fraud.append("round-number total to a new vendor")
        # Ambiguous / bundled expense -> low confidence.
        if any(w in text for w in ("bundled", "bundle", "mixes", "mixed", "ambiguous")):
            confidence = 0.5

        if "MEAL-03" in violations:
            proposed = Route.reject
        elif violations or fraud or confidence < 0.80:
            proposed = Route.human_review
        else:
            proposed = Route.auto_approve

        return AgentRecommendation(
            proposed_route=proposed,
            confidence=confidence,
            category=invoice.category,
            policy_violations=violations,
            fraud_signals=fraud,
            reason="stub heuristic classification",
        )


def get_provider(provider_name: str, retriever=None) -> AgentProvider:
    """Factory — the LLM provider is selected purely by configuration (M15).

    `stub` is the deterministic offline provider (CI / eval); anything else resolves to
    an OpenAI-compatible endpoint (gemini / groq / openrouter, or a custom LLM_BASE_URL).
    A `retriever` (PolicyIndex) enables RAG: only the relevant clauses enter the prompt.
    """
    if provider_name == "stub":
        return StubProvider()
    from .llm import build_llm_provider  # local import: keep the stub path dependency-light

    return build_llm_provider(provider_name, retriever=retriever)
