"""Budget reservations + payment execution — the saga's money steps (M9/M10).

Exactly-once is enforced by a **claim gate**: the reservation record is created
(create-only) BEFORE any budget math, so exactly one caller ever mutates the budget
for a given tracking id. Concurrent duplicates — a retried invocation racing the
original — never touch the budget: they wait briefly for the owner's outcome and
replay it. Budget math itself is ETag compare-and-swap, so two *different* invoices
can never overspend a department either (INV-1014).

Residual window, documented: if the claim owner crashes between claiming and
finalizing, the record stays pending and duplicates fail loudly (the workflow retries
with backoff). Production would add a lease/takeover on stale claims; money is never
moved twice either way.
"""

import time

from pydantic import BaseModel

from .logging import get_logger
from .state import StateBackend

log = get_logger("budgets")

MAX_CAS_RETRIES = 5
AWAIT_OUTCOME_SECONDS = 5.0
_IN_FLIGHT_STATES = ("pending", "releasing")


class OperationResult(BaseModel):
    ok: bool
    reason: str = ""


class BudgetLedger:
    def __init__(self, backend: StateBackend) -> None:
        self.backend = backend

    # ── seeding ──
    def seed(self, budgets: dict[str, float]) -> None:
        for department, total in budgets.items():
            created = self.backend.try_create(
                f"budget:{department}", {"department": department, "total": total, "reserved": 0.0}
            )
            if created:
                log.info("budget seeded", extra={"department": department, "total": total})

    def snapshot(self, departments: list[str]) -> list[dict]:
        rows = []
        for department in departments:
            value, _ = self.backend.get(f"budget:{department}")
            if value:
                value["available"] = round(value["total"] - value["reserved"], 2)
                rows.append(value)
        return rows

    # ── saga step 1: reserve (compensation: release) ──
    def reserve(self, tracking_id: str, department: str, amount: float) -> OperationResult:
        # CLAIM GATE (M10): whoever creates the record owns the budget mutation.
        # A concurrent duplicate loses the create and never touches the budget.
        claimed = self.backend.try_create(
            f"reservation:{tracking_id}",
            {"state": "pending", "department": department, "amount": amount},
        )
        if not claimed:
            return self._await_outcome(tracking_id)

        for _ in range(MAX_CAS_RETRIES):
            budget, etag = self.backend.get(f"budget:{department}")
            if budget is None:
                return self._finalize(tracking_id, "denied", "unknown_department")
            if budget["total"] - budget["reserved"] < amount:
                return self._finalize(tracking_id, "denied", "insufficient_budget")
            budget["reserved"] = round(budget["reserved"] + amount, 2)
            if self.backend.save_cas(f"budget:{department}", budget, etag):
                log.info(
                    "budget reserved",
                    extra={"trackingId": tracking_id, "department": department, "amount": amount},
                )
                return self._finalize(tracking_id, "reserved", "reserved")
            # CAS lost to a DIFFERENT invoice -> re-read and re-check (INV-1014).
        return self._finalize(tracking_id, "denied", "conflict_retry_exhausted")

    # ── saga step 2: execute payment (idempotent; scenario hook fails deterministically) ──
    def execute_payment(self, tracking_id: str, amount: float, scenario: str | None) -> OperationResult:
        existing, _ = self.backend.get(f"payment:{tracking_id}")
        if existing:  # retried payment -> no double pay (F3/M10)
            return OperationResult(ok=existing["state"] == "paid", reason=existing.get("reason", "replayed"))

        # NB: with a real PSP the create-only record would be written BEFORE calling
        # the provider, carrying the same key as the PSP idempotency key. Here the
        # record IS the payment side effect, so whoever wins the create defines the
        # outcome; a concurrent loser reads and returns the winner's record.
        if scenario and "payment-failure" in scenario:
            outcome = {"state": "failed", "amount": amount, "reason": "injected_failure"}
        else:
            outcome = {"state": "paid", "amount": amount, "reason": "paid"}

        if not self.backend.try_create(f"payment:{tracking_id}", outcome):
            recorded, _ = self.backend.get(f"payment:{tracking_id}")
            outcome = recorded or outcome
        paid = outcome["state"] == "paid"
        log_fn = log.info if paid else log.warning
        log_fn("payment executed" if paid else "payment failed (injected)",
               extra={"trackingId": tracking_id, "amount": amount})
        return OperationResult(ok=paid, reason=outcome.get("reason", ""))

    # ── compensation: release the reservation (no orphans, M9) ──
    def release(self, tracking_id: str) -> OperationResult:
        reservation, res_etag = self.backend.get(f"reservation:{tracking_id}")
        if reservation is None:
            return OperationResult(ok=True, reason="nothing_to_release")
        if reservation["state"] in _IN_FLIGHT_STATES:
            return self._await_outcome(tracking_id)
        if reservation["state"] != "reserved":
            return OperationResult(ok=True, reason="already_" + reservation["state"])

        # CLAIM GATE for the compensation: CAS reserved -> releasing. Exactly one
        # caller wins and decrements the budget; a concurrent duplicate loses the CAS
        # and waits for the outcome instead of freeing the money twice.
        reservation["state"] = "releasing"
        if not self.backend.save_cas(f"reservation:{tracking_id}", reservation, res_etag):
            return self._await_outcome(tracking_id)

        for _ in range(MAX_CAS_RETRIES):
            budget, etag = self.backend.get(f"budget:{reservation['department']}")
            if budget is None:
                return self._finalize(tracking_id, "release_failed", "unknown_department", ok=False)
            budget["reserved"] = round(budget["reserved"] - reservation["amount"], 2)
            if self.backend.save_cas(f"budget:{reservation['department']}", budget, etag):
                log.info("reservation released", extra={"trackingId": tracking_id})
                return self._finalize(tracking_id, "released", "released")
        return self._finalize(tracking_id, "release_failed", "conflict_retry_exhausted", ok=False)

    # ── internals ──

    def _finalize(self, tracking_id: str, state: str, reason: str, ok: bool | None = None) -> OperationResult:
        """Record the claim's outcome so duplicates can replay it."""
        for _ in range(MAX_CAS_RETRIES):
            record, etag = self.backend.get(f"reservation:{tracking_id}")
            if record is None:
                break
            record["state"] = state
            record["reason"] = reason
            if self.backend.save_cas(f"reservation:{tracking_id}", record, etag):
                break
        if ok is None:
            ok = state in ("reserved", "released")
        return OperationResult(ok=ok, reason=reason)

    def _await_outcome(self, tracking_id: str, timeout_seconds: float | None = None) -> OperationResult:
        """A duplicate call: never touch the budget — wait for the owner's outcome."""
        if timeout_seconds is None:
            timeout_seconds = AWAIT_OUTCOME_SECONDS  # module constant: patchable in tests
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            record, _ = self.backend.get(f"reservation:{tracking_id}")
            if record and record["state"] not in _IN_FLIGHT_STATES:
                ok = record["state"] in ("reserved", "released")
                if record["state"].startswith("already_"):
                    ok = True
                return OperationResult(ok=ok, reason=record.get("reason", "replayed"))
            time.sleep(0.1)
        # Owner crashed mid-flight (rare): fail LOUDLY, money untouched. The workflow
        # retries with backoff; production would add a lease/takeover on stale claims.
        raise RuntimeError(f"reservation {tracking_id} stuck in-flight; refusing to double-apply")
