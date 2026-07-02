"""Unit tests for the OpenAI-compatible LLM provider.

httpx.MockTransport lets us test the full request/retry/parse path with no network and
no real model — the same technique CI uses to stay deterministic.
"""

import json

import httpx
import pytest
from approvalflow_common.llm import LLMProvider, ProviderError
from approvalflow_common.schemas import InvoiceSubmission, Route

INVOICE = InvoiceSubmission.model_validate(
    {
        "submitter": "a@b.example",
        "department": "engineering-2026Q2",
        "vendor": "Bistro 19",
        "vendorKnown": True,
        "invoiceNumber": "X-1",
        "currency": "USD",
        "category": "meals",
        "attendees": 1,
        "lineItems": [{"description": "Team lunch", "quantity": 1, "unitPrice": 42.0}],
        "taxAmount": 0.0,
        "total": 42.0,
        "receiptPresent": True,
        "date": "2026-05-12",
    }
)

GOOD_RECOMMENDATION = {
    "category": "meals",
    "confidence": 0.93,
    "proposed_route": "auto_approve",
    "policy_violations": [],
    "fraud_signals": [],
    "reason": "In-policy solo lunch.",
}


def completion_response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def make_provider(handler, max_retries: int = 2) -> LLMProvider:
    return LLMProvider(
        name="testvendor",
        model="test-model",
        api_key="test-key",
        base_url="https://llm.example/v1",
        max_retries=max_retries,
        backoff_base=0,  # no sleeping in tests
        transport=httpx.MockTransport(handler),
    )


def test_happy_path_parses_structured_output():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        captured["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json=completion_response(json.dumps(GOOD_RECOMMENDATION)))

    rec = make_provider(handler).recommend(INVOICE)
    assert rec.proposed_route is Route.auto_approve
    assert rec.confidence == 0.93
    # The request is a real chat-completions call: deterministic, JSON-mode, authed.
    assert captured["payload"]["temperature"] == 0
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["model"] == "test-model"
    assert captured["auth"] == "Bearer test-key"
    # The invoice travels as the user message, in the wire (camelCase) format.
    assert "invoiceNumber" in captured["payload"]["messages"][1]["content"]


def test_unparsable_content_raises_provider_error():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion_response("sorry, I cannot help with that"))

    with pytest.raises(ProviderError, match="unparsable"):
        make_provider(handler).recommend(INVOICE)


def test_retries_on_429_then_succeeds():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=completion_response(json.dumps(GOOD_RECOMMENDATION)))

    rec = make_provider(handler).recommend(INVOICE)
    assert rec.category.value == "meals"
    assert calls["n"] == 2


def test_bad_api_key_fails_fast_without_retry():
    calls = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": "invalid key"})

    with pytest.raises(ProviderError, match="401"):
        make_provider(handler).recommend(INVOICE)
    assert calls["n"] == 1  # non-retryable: exactly one attempt


def test_exhausted_retries_raise():
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    with pytest.raises(ProviderError, match="unavailable after 3 attempts"):
        make_provider(handler, max_retries=2).recommend(INVOICE)


def test_missing_api_key_fails_at_construction():
    with pytest.raises(ProviderError, match="LLM_API_KEY"):
        LLMProvider(name="x", model="m", api_key="", base_url="https://llm.example")


def test_rag_clauses_replace_the_static_rule_list():
    # With a retriever attached, the system prompt carries ONLY the retrieved clauses
    # for this invoice (N5) — not the static fallback list.
    from pathlib import Path

    from approvalflow_common.policy_rag import PolicyIndex

    index = PolicyIndex.from_file(Path(__file__).resolve().parents[2] / "policy.md")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["system"] = json.loads(request.content)["messages"][0]["content"]
        return httpx.Response(200, json=completion_response(json.dumps(GOOD_RECOMMENDATION)))

    provider = make_provider(handler)
    provider.retriever = index
    provider.recommend(INVOICE)

    assert "MEAL-01" in captured["system"]  # retrieved for a meals invoice
    assert "TRAVEL-02" not in captured["system"]  # irrelevant clause stays out


def test_agent_may_not_claim_duplicate():
    # Duplicate detection is intake's deterministic job; a model saying "duplicate"
    # is coerced to human_review, never trusted.
    payload = {**GOOD_RECOMMENDATION, "proposed_route": "duplicate"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion_response(json.dumps(payload)))

    rec = make_provider(handler).recommend(INVOICE)
    assert rec.proposed_route is Route.human_review
