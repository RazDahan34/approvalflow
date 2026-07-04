"""Shared domain models.

The wire format is camelCase (matching sample-invoices.json); in Python we use
snake_case. Pydantic aliases bridge the two, so the contract stays stable while
the code stays idiomatic.
"""

import hashlib
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Category(StrEnum):
    meals = "meals"
    travel = "travel"
    saas = "saas"
    hardware = "hardware"
    other = "other"


class Route(StrEnum):
    auto_approve = "auto_approve"
    human_review = "human_review"
    reject = "reject"
    duplicate = "duplicate"


class LineItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    description: str
    quantity: float = Field(default=1, gt=0)
    unit_price: float = Field(alias="unitPrice", ge=0)


class InvoiceSubmission(BaseModel):
    """An invoice/expense as submitted by an employee or vendor."""

    model_config = ConfigDict(populate_by_name=True)

    external_id: str | None = Field(default=None, alias="id")
    submitter: str
    department: str
    vendor: str
    vendor_known: bool = Field(default=False, alias="vendorKnown")
    invoice_number: str = Field(alias="invoiceNumber")
    currency: str = "USD"
    category: Category = Category.other
    attendees: int | None = Field(default=None, ge=1)
    line_items: list[LineItem] = Field(default_factory=list, alias="lineItems")
    tax_amount: float = Field(default=0.0, alias="taxAmount", ge=0)
    total: float = Field(gt=0)
    receipt_present: bool = Field(default=False, alias="receiptPresent")
    date: str
    notes: str | None = None
    # Chaos/testing hook carried by the shipped fixtures (e.g. "payment-failure:journey-D"):
    # the payment service fails deterministically when it says so. Documented, never
    # consulted by the router — it cannot change a decision, only simulate downstream faults.
    scenario: str | None = None

    def idempotency_key(self) -> str:
        """Same vendor + invoiceNumber + total => the same submission (GLOBAL-DUP)."""
        raw = f"{self.vendor}|{self.invoice_number}|{self.total}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class InvoiceSubmittedEvent(BaseModel):
    """Published on the bus when a new invoice is accepted."""

    model_config = ConfigDict(populate_by_name=True)

    tracking_id: str = Field(alias="trackingId")
    correlation_id: str = Field(alias="correlationId")
    submitted_at: str = Field(alias="submittedAt")
    invoice: InvoiceSubmission
