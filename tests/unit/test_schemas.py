"""Unit tests for the shared domain schemas (the wire contract)."""

import pytest
from approvalflow_common.schemas import Category, InvoiceSubmission
from pydantic import ValidationError

# A real shipped fixture (INV-1001), camelCase exactly as it arrives on the wire.
INV_1001 = {
    "id": "INV-1001",
    "submitter": "dana.cohen@northwind.example",
    "department": "engineering-2026Q2",
    "vendor": "Bistro 19",
    "vendorKnown": True,
    "invoiceNumber": "NW-INV-7781",
    "currency": "USD",
    "category": "meals",
    "attendees": 1,
    "lineItems": [{"description": "Team lunch", "quantity": 1, "unitPrice": 38.89}],
    "taxAmount": 3.11,
    "total": 42.0,
    "receiptPresent": True,
    "date": "2026-05-12",
    "notes": "Solo working lunch.",
}


def test_parses_camelcase_wire_format():
    inv = InvoiceSubmission.model_validate(INV_1001)
    assert inv.vendor == "Bistro 19"
    assert inv.vendor_known is True
    assert inv.invoice_number == "NW-INV-7781"
    assert inv.category is Category.meals
    assert inv.line_items[0].unit_price == 38.89
    assert inv.tax_amount == 3.11


def test_round_trips_back_to_camelcase():
    inv = InvoiceSubmission.model_validate(INV_1001)
    dumped = inv.model_dump(by_alias=True)
    assert dumped["vendorKnown"] is True
    assert dumped["invoiceNumber"] == "NW-INV-7781"
    assert dumped["lineItems"][0]["unitPrice"] == 38.89


def test_idempotency_key_is_stable_and_specific():
    inv = InvoiceSubmission.model_validate(INV_1001)
    again = InvoiceSubmission.model_validate(INV_1001)
    # Same vendor + invoiceNumber + total => same key (GLOBAL-DUP / F3).
    assert inv.idempotency_key() == again.idempotency_key()
    # A different total => a different key.
    changed = {**INV_1001, "total": 43.0}
    assert InvoiceSubmission.model_validate(changed).idempotency_key() != inv.idempotency_key()


MINIMAL = {
    "submitter": "x@y.z",
    "department": "d",
    "vendor": "v",
    "invoiceNumber": "n",
    "category": "other",
    "total": 10.0,
    "date": "2026-01-01",
}


def test_defaults_for_optional_fields():
    inv = InvoiceSubmission.model_validate(MINIMAL)
    assert inv.vendor_known is False
    assert inv.receipt_present is False
    assert inv.tax_amount == 0.0
    assert inv.line_items == []


def test_rejects_nonpositive_total():
    # A zero/negative "expense" is either garbage or a refund — never a payment to auto-approve.
    for bad in (0, -42.0):
        with pytest.raises(ValidationError):
            InvoiceSubmission.model_validate({**MINIMAL, "total": bad})


def test_rejects_zero_attendees():
    # attendees=0 would silently skip the per-attendee meal check; the edge rejects it.
    with pytest.raises(ValidationError):
        InvoiceSubmission.model_validate({**MINIMAL, "category": "meals", "attendees": 0})
