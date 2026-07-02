"""Unit tests for the hybrid policy retriever (N5)."""

from pathlib import Path

from approvalflow_common.policy_rag import PolicyIndex
from approvalflow_common.schemas import InvoiceSubmission

POLICY = Path(__file__).resolve().parents[2] / "policy.md"
INDEX = PolicyIndex.from_file(POLICY)


def inv(**over):
    base = {
        "submitter": "a@b.example",
        "department": "engineering-2026Q2",
        "vendor": "Known Co",
        "vendorKnown": True,
        "invoiceNumber": "X-1",
        "currency": "USD",
        "category": "meals",
        "attendees": 2,
        "lineItems": [{"description": "Team lunch", "quantity": 1, "unitPrice": 42.0}],
        "taxAmount": 0.0,
        "total": 42.0,
        "receiptPresent": True,
        "date": "2026-05-12",
    }
    base.update(over)
    return InvoiceSubmission.model_validate(base)


def ids(chunks):
    return [c.rule_id for c in chunks]


def test_parses_the_real_policy():
    got = {r.rule_id for r in INDEX.rules}
    expected = {
        "MEAL-01", "MEAL-02", "MEAL-03",
        "TRAVEL-01", "TRAVEL-02", "TRAVEL-03",
        "SAAS-01", "HW-01", "HW-02",
        "GLOBAL-RECEIPT", "GLOBAL-VENDOR", "GLOBAL-FX", "GLOBAL-DUP", "GLOBAL-MATH", "GLOBAL-FRAUD",
    }  # §6 autonomy keys must NOT be indexed — they belong to the router, not the agent
    assert expected <= got
    assert "AUTONOMY-CEILING" not in got


def test_meals_invoice_gets_meal_rules_not_travel():
    retrieved = ids(INDEX.retrieve(inv()))
    assert "MEAL-01" in retrieved
    assert "GLOBAL-MATH" in retrieved and "GLOBAL-FRAUD" in retrieved
    assert "TRAVEL-02" not in retrieved
    assert "GLOBAL-VENDOR" not in retrieved  # vendor is known
    assert "GLOBAL-FX" not in retrieved  # USD


def test_attribute_flags_pull_matching_globals():
    retrieved = ids(INDEX.retrieve(inv(vendorKnown=False, currency="EUR", receiptPresent=False)))
    assert "GLOBAL-VENDOR" in retrieved
    assert "GLOBAL-FX" in retrieved
    assert "GLOBAL-RECEIPT" in retrieved


def test_lexical_match_crosses_categories():
    # An alcohol-only bar tab misfiled under `other` must still retrieve MEAL-03.
    sneaky = inv(
        category="other",
        attendees=None,
        notes="Alcohol-only bar tab from the offsite.",
        lineItems=[{"description": "Bar tab, alcohol only", "quantity": 1, "unitPrice": 42.0}],
    )
    assert "MEAL-03" in ids(INDEX.retrieve(sneaky))


def test_duplicate_rule_is_never_retrieved():
    # GLOBAL-DUP is intake's deterministic job; the model must not reason about it.
    assert "GLOBAL-DUP" not in ids(INDEX.retrieve(inv()))


def test_k_caps_the_result():
    assert len(INDEX.retrieve(inv(vendorKnown=False, currency="EUR", receiptPresent=False), k=4)) == 4
