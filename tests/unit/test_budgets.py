"""Unit tests for the budget ledger — the saga's money guarantees, provable offline.

InMemoryStateBackend implements the exact ETag CAS contract of the Dapr store, so the
INV-1014 concurrency scenario and every idempotency claim are tested with no Redis.
"""

from approvalflow_common.budgets import BudgetLedger
from approvalflow_common.state import InMemoryStateBackend


def make_ledger(marketing_total=1000.0):
    ledger = BudgetLedger(InMemoryStateBackend())
    ledger.seed({"marketing-2026Q2": marketing_total})
    return ledger


def available(ledger, department="marketing-2026Q2"):
    row = ledger.snapshot([department])[0]
    return row["available"]


def test_reserve_and_snapshot():
    ledger = make_ledger()
    assert ledger.reserve("A", "marketing-2026Q2", 600.0).ok
    assert available(ledger) == 400.0


def test_reserve_is_idempotent():
    ledger = make_ledger()
    assert ledger.reserve("A", "marketing-2026Q2", 600.0).ok
    assert ledger.reserve("A", "marketing-2026Q2", 600.0).ok  # replay: same outcome
    assert available(ledger) == 400.0  # ...and exactly ONE effect (M10)


def test_budget_never_goes_negative():
    # INV-1014: two $600 items against a $1,000 budget — exactly one wins.
    ledger = make_ledger(1000.0)
    first = ledger.reserve("A", "marketing-2026Q2", 600.0)
    second = ledger.reserve("B", "marketing-2026Q2", 600.0)
    assert first.ok is True
    assert second.ok is False and second.reason == "insufficient_budget"
    assert available(ledger) == 400.0


def test_concurrent_cas_conflict_is_retried_and_stays_safe():
    # Simulate true interleaving: B reads the SAME etag as A (both see a full budget),
    # A commits first, B's CAS must fail and its retry must see there is no money left.
    ledger = make_ledger(1000.0)
    backend = ledger.backend

    original_get = backend.get
    stale = {}

    def racing_get(key):
        value, etag = original_get(key)
        if key == "budget:marketing-2026Q2" and "etag" in stale:
            return value, stale.pop("etag")  # hand B the stale etag once
        return value, etag

    _, first_etag = backend.get("budget:marketing-2026Q2")
    assert ledger.reserve("A", "marketing-2026Q2", 600.0).ok  # A commits
    stale["etag"] = first_etag  # B raced A: it read before A committed
    backend.get = racing_get

    second = ledger.reserve("B", "marketing-2026Q2", 600.0)
    assert second.ok is False and second.reason == "insufficient_budget"
    assert available(ledger) == 400.0  # never negative, no double effect


def test_unknown_department_is_denied():
    ledger = make_ledger()
    result = ledger.reserve("X", "no-such-dept", 10.0)
    assert result.ok is False and result.reason == "unknown_department"


def test_payment_execution_and_idempotency():
    ledger = make_ledger()
    assert ledger.execute_payment("A", 42.0, scenario=None).ok
    assert ledger.execute_payment("A", 42.0, scenario=None).ok  # retried payment: one effect


def test_injected_failure_is_deterministic_and_sticky():
    ledger = make_ledger()
    first = ledger.execute_payment("D", 9500.0, scenario="payment-failure:journey-D")
    replay = ledger.execute_payment("D", 9500.0, scenario="payment-failure:journey-D")
    assert first.ok is False and first.reason == "injected_failure"
    assert replay.ok is False  # replay returns the recorded outcome, no flip


def test_journey_d_compensation_leaves_no_orphan():
    # reserve -> injected payment failure -> release: budget must be whole again.
    ledger = BudgetLedger(InMemoryStateBackend())
    ledger.seed({"engineering-2026Q2": 50_000.0})
    assert ledger.reserve("D", "engineering-2026Q2", 9500.0).ok
    assert ledger.execute_payment("D", 9500.0, "payment-failure:journey-D").ok is False
    assert ledger.release("D").ok
    assert available(ledger, "engineering-2026Q2") == 50_000.0  # no orphaned reservation (M9)
    assert ledger.release("D").reason == "already_released"  # compensation replay: no-op
