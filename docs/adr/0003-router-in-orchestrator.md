# ADR 0003 — The Deterministic Router Runs In-Process in the Orchestrator

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Raz Dahan

## Context

- The final approval decision must always be deterministic and validate all required limits before approving a request.
- I considered whether the router should be a separate microservice or part of the Orchestrator.
- According to the IDesign approach, a service should represent an independent area of change.

## Decision

- I decided to implement the router as a **pure function** in the shared library (`approvalflow_common.decision`).
- The Orchestrator executes the router directly during the workflow without calling another service.

## Consequences

- **Positive:** This avoids an extra network call on the critical path, reduces possible failure points, and ensures the same router code is used both in production and in automated tests. Since the router and workflow change together, keeping them in the same service makes sense.
- **Negative:** Changes to the router require redeploying the Orchestrator. If another service needs the same logic, it imports the shared library instead of calling an API, which is acceptable because only the Orchestrator is allowed to make approval decisions.
- **Alternative rejected:** A separate policy service would introduce additional network communication and deployment overhead without providing a clear independent responsibility, since only the policy thresholds are externalized while the routing logic remains in code.


