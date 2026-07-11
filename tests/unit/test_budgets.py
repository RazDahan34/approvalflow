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


def test_concurrent_duplicate_reserve_never_touches_budget_twice():
    # THE race a hostile review found in the old design: a duplicate call for the
    # SAME tracking id must never mutate the budget — the claim gate guarantees the
    # loser only waits for (and replays) the owner's outcome.
    import threading
    import time

    ledger = make_ledger(1000.0)
    # Simulate the duplicate arriving while the owner is mid-flight:
    assert ledger.backend.try_create(
        "reservation:A", {"state": "pending", "department": "marketing-2026Q2", "amount": 600.0}
    )

    def owner_finishes():
        time.sleep(0.3)
        record, etag = ledger.backend.get("reservation:A")
        record.update(state="reserved", reason="reserved")
        ledger.backend.save_cas("reservation:A", record, etag)

    thread = threading.Thread(target=owner_finishes)
    thread.start()
    duplicate = ledger.reserve("A", "marketing-2026Q2", 600.0)  # loses the claim, waits
    thread.join()

    assert duplicate.ok is True and duplicate.reason == "reserved"
    assert available(ledger) == 1000.0  # the duplicate NEVER touched the budget


def test_stuck_inflight_claim_fails_loudly_without_touching_money(monkeypatch):
    import pytest
    from approvalflow_common import budgets as budgets_module

    monkeypatch.setattr(budgets_module, "AWAIT_OUTCOME_SECONDS", 0.3)
    ledger = make_ledger(1000.0)
    ledger.backend.try_create(
        "reservation:A", {"state": "pending", "department": "marketing-2026Q2", "amount": 600.0}
    )
    with pytest.raises(RuntimeError, match="stuck in-flight"):
        ledger.reserve("A", "marketing-2026Q2", 600.0)
    assert available(ledger) == 1000.0


def test_concurrent_duplicate_release_frees_money_once():
    # Same claim-gate reasoning for the compensation: reserved -> releasing is a CAS,
    # so exactly one caller decrements the budget.
    import threading
    import time

    ledger = make_ledger(1000.0)
    assert ledger.reserve("A", "marketing-2026Q2", 600.0).ok
    record, etag = ledger.backend.get("reservation:A")
    record["state"] = "releasing"  # simulate an in-flight release owner
    ledger.backend.save_cas("reservation:A", record, etag)

    def owner_finishes():
        time.sleep(0.3)
        budget, be = ledger.backend.get("budget:marketing-2026Q2")
        budget["reserved"] = round(budget["reserved"] - 600.0, 2)
        ledger.backend.save_cas("budget:marketing-2026Q2", budget, be)
        rec, e = ledger.backend.get("reservation:A")
        rec.update(state="released", reason="released")
        ledger.backend.save_cas("reservation:A", rec, e)

    thread = threading.Thread(target=owner_finishes)
    thread.start()
    duplicate = ledger.release("A")  # must wait, not double-free
    thread.join()

    assert duplicate.ok is True
    assert available(ledger) == 1000.0  # freed exactly once, not twice


def test_journey_d_compensation_leaves_no_orphan():
    # reserve -> injected payment failure -> release: budget must be whole again.
    ledger = BudgetLedger(InMemoryStateBackend())
    ledger.seed({"engineering-2026Q2": 50_000.0})
    assert ledger.reserve("D", "engineering-2026Q2", 9500.0).ok
    assert ledger.execute_payment("D", 9500.0, "payment-failure:journey-D").ok is False
    assert ledger.release("D").ok
    assert available(ledger, "engineering-2026Q2") == 50_000.0  # no orphaned reservation (M9)
    assert ledger.release("D").reason == "already_released"  # compensation replay: no-op
