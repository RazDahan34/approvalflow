# ADR 0002 — Orchestration via Dapr Workflow

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** Raz Dahan

## Context

- The payment process must support a durable and compensable saga, while human approvals should be able to pause and continue even after a service restart.
- I considered two options: using Dapr Workflow or implementing my own state machine that stores progress and reacts to pub/sub events.

## Decision

- I chose to use **Dapr Workflow** inside the Orchestrator.
- Each saga step and its compensation are implemented as workflow activities.
- Human approval is handled using `wait_for_external_event`, allowing the workflow to resume once the approver submits a decision.

## Consequences

- **Positive:** Dapr Workflow provides durability, replay, and pause/resume capabilities out of the box. It also offers a clean compensation model and integrates well with the Dapr ecosystem.
- **Negative:** The orchestration logic becomes dependent on the Dapr Workflow programming model, requires deterministic workflows, and has a learning curve.
- **Alternative rejected:** A custom saga implementation would provide more flexibility, but it would require building durability, replay, and resume logic from scratch, adding unnecessary complexity to the project.
