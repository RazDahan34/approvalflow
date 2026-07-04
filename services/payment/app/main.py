"""Payment service (Engine + ResourceAccess).

Owns budgets, reservations and payments. Called synchronously by the Orchestrator as
saga steps: reserve -> execute -> (release on failure). Every operation is idempotent;
budget math is ETag CAS so concurrent approvals can never overspend (INV-1014).
"""

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

from approvalflow_common import create_app, get_logger, get_settings
from approvalflow_common.budgets import BudgetLedger
from approvalflow_common.state import DaprStateBackend
from fastapi import FastAPI
from pydantic import BaseModel

SERVICE = "payment"

settings = get_settings()
log = get_logger(SERVICE)

BUDGETS = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "budgets.json").read_text(encoding="utf-8")
)["budgets"]

ledger = BudgetLedger(DaprStateBackend(settings.statestore_name))


def _seed_with_retry() -> None:
    """Seed budgets once the sidecar is reachable; create-only, so restarts never
    reset live balances. Gives up loudly after ~60s (fail fast, M15)."""
    for attempt in range(30):
        try:
            ledger.seed(BUDGETS)
            log.info("budgets ready", extra={"departments": list(BUDGETS.keys())})
            return
        except Exception as exc:
            log.warning(
                "budget seeding retry (sidecar warming up?)",
                extra={"attempt": attempt, "error": str(exc)[:120]},
            )
            time.sleep(2)
    raise RuntimeError("budget seeding failed: Dapr state store unavailable")


@asynccontextmanager
async def payment_lifespan(_: FastAPI):
    await asyncio.to_thread(_seed_with_retry)
    yield


app = create_app(SERVICE, lifespan_extra=payment_lifespan)


class ReserveRequest(BaseModel):
    trackingId: str
    department: str
    amountUsd: float


class ExecuteRequest(BaseModel):
    trackingId: str
    amountUsd: float
    scenario: str | None = None


class ReleaseRequest(BaseModel):
    trackingId: str


@app.post("/reserve")
def reserve(request: ReserveRequest) -> dict:
    result = ledger.reserve(request.trackingId, request.department, request.amountUsd)
    return result.model_dump()


@app.post("/execute")
def execute(request: ExecuteRequest) -> dict:
    result = ledger.execute_payment(request.trackingId, request.amountUsd, request.scenario)
    return result.model_dump()


@app.post("/release")
def release(request: ReleaseRequest) -> dict:
    result = ledger.release(request.trackingId)
    return result.model_dump()


@app.get("/budgets")
def budgets() -> dict:
    """Current budget snapshot (feeds the controller dashboard, F8)."""
    return {"budgets": ledger.snapshot(list(BUDGETS.keys()))}
