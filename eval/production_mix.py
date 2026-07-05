"""Production-mix simulation — the "boring majority" question.

The shipped fixtures are deliberately edge-heavy (they exist to exercise every decision
path), so their auto/human ratio says nothing about production behaviour. This module
synthesizes a *plausible enterprise expense mix* — mostly small routine items, a tail of
big-ticket and messy ones — and measures what share the posture signs autonomously.

Deterministic (seeded), offline (stub agent + the real router), runs in CI.
The mix assumptions are visible below and stated in the report; tune them to argue a
different traffic profile.
"""

import random
import uuid
from collections import Counter

from approvalflow_common.agent import AgentProvider
from approvalflow_common.decision import PolicyConfig, route_decision
from approvalflow_common.schemas import InvoiceSubmission

KNOWN_VENDORS = {
    "meals": ["Bistro 19", "Trattoria Verde", "The Rooftop Grill", "City Deli"],
    "saas": ["Atlassian", "DataDog", "PixelForge", "Slack"],
    "travel": ["City Cabs", "Lufthansa", "Hotel Adler", "Metro Transit"],
    "hardware": ["Logitech", "Dell", "Office Depot"],
    "other": ["Lakeside Venue", "ExpoWorks", "PrintWorks"],
}


def _invoice(rng: random.Random, category: str, total: float, **overrides) -> InvoiceSubmission:
    body = {
        "submitter": "mix@northwind.example",
        "department": rng.choice(["engineering-2026Q2", "sales-2026Q2", "marketing-2026Q2"]),
        "vendor": rng.choice(KNOWN_VENDORS[category]),
        "vendorKnown": True,
        "invoiceNumber": f"MIX-{uuid.UUID(int=rng.getrandbits(128)).hex[:10]}",
        "currency": "USD",
        "category": category,
        "lineItems": [{"description": f"{category} expense", "quantity": 1, "unitPrice": round(total, 2)}],
        "taxAmount": 0.0,
        "total": round(total, 2),
        "receiptPresent": True,
        "date": "2026-05-20",
    }
    body.update(overrides)
    return InvoiceSubmission.model_validate(body)


def generate_mix(count: int = 1000, seed: int = 42) -> list[InvoiceSubmission]:
    """A defensible enterprise mix: expense traffic is dominated by small routine
    items (meals, rides, subscriptions), with a real tail of big-ticket and messy ones."""
    rng = random.Random(seed)
    invoices: list[InvoiceSubmission] = []
    for _ in range(count):
        roll = rng.random()
        if roll < 0.40:  # meals — team lunches and coffees
            attendees = rng.choice([1, 1, 1, 2, 2, 3, 4, 6])
            per_head = rng.uniform(9, 55)
            inv = _invoice(rng, "meals", per_head * attendees, attendees=attendees)
        elif roll < 0.60:  # saas — monthly tools
            inv = _invoice(rng, "saas", rng.uniform(15, 240))
        elif roll < 0.80:  # travel — mostly rides, some hotels/flights
            if rng.random() < 0.75:
                inv = _invoice(rng, "travel", rng.uniform(9, 80))  # taxis/transit
            else:
                inv = _invoice(rng, "travel", rng.uniform(150, 1900))  # hotels/flights
        elif roll < 0.92:  # hardware — overwhelmingly peripherals, occasional big item
            if rng.random() < 0.85:
                inv = _invoice(rng, "hardware", rng.uniform(15, 320))  # cables/keyboards/docks
            else:
                inv = _invoice(rng, "hardware", rng.uniform(320, 1400))  # monitors/laptops
        else:  # other — the junk drawer (mostly small odds and ends)
            inv = _invoice(rng, "other", rng.uniform(20, 500))

        # Real-world mess, injected independently of category:
        mess = rng.random()
        data = inv.model_dump(by_alias=True)
        if mess < 0.03:
            data["receiptPresent"] = False  # forgot the receipt
        elif mess < 0.05:
            data["vendorKnown"] = False  # brand-new vendor
        elif mess < 0.06:
            data["total"] = float(rng.choice([1000, 2000, 5000]))  # suspicious round number
            data["lineItems"] = [{"description": "services", "quantity": 1, "unitPrice": data["total"]}]
        invoices.append(InvoiceSubmission.model_validate(data))
    return invoices


def simulate(provider: AgentProvider, config: PolicyConfig, count: int = 1000, seed: int = 42) -> dict:
    routes: Counter[str] = Counter()
    autonomous_usd = 0.0
    human_usd = 0.0
    for invoice in generate_mix(count, seed):
        decision = route_decision(invoice, provider.recommend(invoice), config)
        routes[decision.route.value] += 1
        if decision.autonomous:
            autonomous_usd += decision.amount_usd
        else:
            human_usd += decision.amount_usd
    total = sum(routes.values())
    return {
        "count": total,
        "routes": dict(routes),
        "auto_rate": routes["auto_approve"] / total,
        "human_rate": routes["human_review"] / total,
        "reject_rate": routes.get("reject", 0) / total,
        "autonomous_usd": round(autonomous_usd, 2),
        "human_usd": round(human_usd, 2),
    }
