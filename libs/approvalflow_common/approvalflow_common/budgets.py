"""Budget reservations + payment execution — the saga's money steps (M9/M10).

Every operation is idempotent (keyed by tracking id, create-only records) and budget
math uses ETag compare-and-swap, so two concurrent reservations can never overspend:
the loser's CAS fails, it re-reads fresh state, and sees there is no money left.
"""

from pydantic import BaseModel

from .logging import get_logger
from .state import StateBackend

log = get_logger("budgets")

MAX_CAS_RETRIES = 5


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
        existing, _ = self.backend.get(f"reservation:{tracking_id}")
        if existing:  # redelivery / retry -> exactly one effect (M10)
            return OperationResult(ok=existing["state"] == "reserved", reason=existing.get("reason", "replayed"))

        for _ in range(MAX_CAS_RETRIES):
            budget, etag = self.backend.get(f"budget:{department}")
            if budget is None:
                return self._record_denied(tracking_id, department, amount, "unknown_department")
            if budget["total"] - budget["reserved"] < amount:
                return self._record_denied(tracking_id, department, amount, "insufficient_budget")
            budget["reserved"] = round(budget["reserved"] + amount, 2)
            if self.backend.save_cas(f"budget:{department}", budget, etag):
                self.backend.try_create(
                    f"reservation:{tracking_id}",
                    {"state": "reserved", "department": department, "amount": amount},
                )
                log.info(
                    "budget reserved",
                    extra={"trackingId": tracking_id, "department": department, "amount": amount},
                )
                return OperationResult(ok=True, reason="reserved")
            # CAS lost: someone reserved concurrently -> re-read and re-check (INV-1014).
        return OperationResult(ok=False, reason="conflict_retry_exhausted")

    def _record_denied(self, tracking_id: str, department: str, amount: float, reason: str) -> OperationResult:
        self.backend.try_create(
            f"reservation:{tracking_id}",
            {"state": "denied", "department": department, "amount": amount, "reason": reason},
        )
        log.info("reservation denied", extra={"trackingId": tracking_id, "reason": reason})
        return OperationResult(ok=False, reason=reason)

    # ── saga step 2: execute payment (idempotent; scenario hook fails deterministically) ──
    def execute_payment(self, tracking_id: str, amount: float, scenario: str | None) -> OperationResult:
        existing, _ = self.backend.get(f"payment:{tracking_id}")
        if existing:  # retried payment -> no double pay (F3/M10)
            return OperationResult(ok=existing["state"] == "paid", reason=existing.get("reason", "replayed"))

        if scenario and "payment-failure" in scenario:
            self.backend.try_create(
                f"payment:{tracking_id}", {"state": "failed", "amount": amount, "reason": "injected_failure"}
            )
            log.warning("payment failed (injected)", extra={"trackingId": tracking_id})
            return OperationResult(ok=False, reason="injected_failure")

        self.backend.try_create(f"payment:{tracking_id}", {"state": "paid", "amount": amount})
        log.info("payment executed", extra={"trackingId": tracking_id, "amount": amount})
        return OperationResult(ok=True, reason="paid")

    # ── compensation: release the reservation (no orphans, M9) ──
    def release(self, tracking_id: str) -> OperationResult:
        reservation, res_etag = self.backend.get(f"reservation:{tracking_id}")
        if reservation is None:
            return OperationResult(ok=True, reason="nothing_to_release")
        if reservation["state"] != "reserved":
            return OperationResult(ok=True, reason="already_" + reservation["state"])

        for _ in range(MAX_CAS_RETRIES):
            budget, etag = self.backend.get(f"budget:{reservation['department']}")
            if budget is None:
                return OperationResult(ok=False, reason="unknown_department")
            budget["reserved"] = round(budget["reserved"] - reservation["amount"], 2)
            if self.backend.save_cas(f"budget:{reservation['department']}", budget, etag):
                reservation["state"] = "released"
                self.backend.save_cas(f"reservation:{tracking_id}", reservation, res_etag)
                log.info("reservation released", extra={"trackingId": tracking_id})
                return OperationResult(ok=True, reason="released")
        return OperationResult(ok=False, reason="conflict_retry_exhausted")
