# ADR 0009 — Data Layer: Per-Service Dapr State Stores; Postgres Behind Audit

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Raz Dahan

## Context

- A core microservices principle is that each service should own its own data.
- Since the system already uses the Dapr State API, I needed to decide how each service's state would be stored.

## Decision

- I gave each service its own Dapr state component, ensuring that only the owning service can access its data.
- Most services use Redis as the state backend, while the **Audit** service uses **PostgreSQL**.
- The services continue using the same Dapr State API, so changing the storage backend only requires updating the Dapr component configuration.
- Data is shared between services through events rather than direct database access.

## Consequences

- **Positive:** Each service has clear ownership of its data, storage backends can be changed without modifying the application code, and services remain loosely coupled.
- **Negative:** Using the Dapr State API means services cannot perform SQL joins across databases, so reporting relies on event-driven projections instead.
- **Alternative rejected:** I rejected using a shared database because it increases coupling between services, and direct SQL access because it bypasses Dapr and reduces flexibility.