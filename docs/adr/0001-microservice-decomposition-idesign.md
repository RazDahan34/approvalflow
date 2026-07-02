# ADR 0001 — Microservice Decomposition via IDesign Volatility Analysis

- **Status:** Accepted
- **Date:** 2026-06-29
- **Deciders:** Raz Dahan

## Context

- The project is an enterprise expense and invoice approval system that must support millions of users.
- The requirements include using at least three containerized services, Dapr, and running the entire system with a single `docker compose up`.
- Instead of using a monolith or splitting services only by functionality, I chose to define service boundaries based on parts of the system that can change independently.
- The architecture also needs to support different approval workflows, reliable payment processing, human approval when needed, and complete auditability.

## Decision

- I decided to decompose the system according to the IDesign volatility approach: each
  service wraps something that changes independently, so the AI model can be swapped
  without touching payment logic, and the workflow can evolve without touching intake.
- The services are:
  - **Orchestrator** – manages the workflow and routing logic (the single Manager).
  - **AI-Decision** – handles AI reasoning and model integration.
  - **MCP Server** – exposes the agent's tools over the Model Context Protocol.
  - **Payment** – manages payments and budgets.
  - **Intake** and **Notification** – handle incoming requests and user notifications.
  - **Audit / Reporting** – stores audit data and reporting information.
  - **Gateway** – manages authentication and security.
- Policy rules and thresholds are stored in external configuration, while the AI service only provides recommendations and the final decision is made by the routing logic.

## Consequences

- **Positive:** Clear service boundaries, independent deployment, and better alignment with the IDesign approach.
- **Negative:** More services increase infrastructure complexity and communication between components.
- **Mitigations:** Use Dapr for communication, a shared `libs` package for common code, and a single `docker compose` command to run the system.
- **Alternatives considered:** A five-service architecture, an internal router, and a modular monolith were considered but rejected because they provide weaker separation of responsibilities.