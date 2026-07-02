"""Integration: the agent + router reproduce the labeled ground truth (offline).

Parametrized so each fixture is its own test case in the CI report.
"""

from pathlib import Path

import pytest
from approvalflow_common.agent import get_provider
from approvalflow_common.decision import PolicyConfig
from approvalflow_common.evaluation import evaluate_fixtures, load_fixtures

FIXTURES = Path(__file__).resolve().parents[2] / "sample-invoices.json"
_RESULTS = evaluate_fixtures(load_fixtures(FIXTURES), get_provider("stub"), PolicyConfig())
_DECIDED = [r for r in _RESULTS if not r.pipeline]


@pytest.mark.parametrize("result", _DECIDED, ids=[r.id for r in _DECIDED])
def test_fixture_routes_as_labeled(result):
    assert result.predicted == result.expected, (
        f"{result.id}: expected {result.expected}, got {result.predicted} (rules: {result.rule_ids})"
    )


def test_at_least_two_auto_approves_shipped():
    autos = [r for r in _DECIDED if r.expected == "auto_approve" and r.passed]
    assert len(autos) >= 2, "the posture must still auto-approve at least 2 fixtures (anti-cheese)"
