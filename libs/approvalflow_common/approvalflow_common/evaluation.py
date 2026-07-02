"""Shared evaluation core used by both the CLI harness (eval/) and the CI tests.

Runs each labeled fixture through an agent provider + the router and compares the
router's route to the ground-truth `expected.route`.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .agent import AgentProvider
from .decision import PolicyConfig, route_decision
from .schemas import InvoiceSubmission

# Fixtures whose final route is decided by a stateful pipeline stage, not the agent+router:
#   INV-1007 -> `duplicate` is short-circuited by intake idempotency before any routing.
PIPELINE_FIXTURES: set[str] = {"INV-1007"}


@dataclass
class FixtureResult:
    id: str
    expected: str
    predicted: str | None
    passed: bool
    pipeline: bool
    rule_ids: list[str] = field(default_factory=list)


def load_fixtures(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["fixtures"]


def evaluate_fixtures(
    fixtures: list[dict],
    provider: AgentProvider,
    config: PolicyConfig,
) -> list[FixtureResult]:
    results: list[FixtureResult] = []
    for fx in fixtures:
        fid = fx["id"]
        expected = fx["expected"]["route"]
        if fid in PIPELINE_FIXTURES:
            results.append(FixtureResult(fid, expected, None, True, True))
            continue
        invoice = InvoiceSubmission.model_validate(fx)
        recommendation = provider.recommend(invoice)
        decision = route_decision(invoice, recommendation, config)
        predicted = decision.route.value
        results.append(
            FixtureResult(fid, expected, predicted, predicted == expected, False, decision.rule_ids)
        )
    return results
