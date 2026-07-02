# ADR 0005 — Redis as the Dapr Pub/Sub Broker and State Store

- **Status:** Accepted
- **Date:** 2026-06-30
- **Deciders:** Raz Dahan

## Context

- The system requires both asynchronous messaging and durable state storage.
- It also needs to run completely with a single `docker compose up` command.
- I wanted a lightweight solution that works well with Dapr and keeps the infrastructure simple.

## Decision

- I chose **Redis** as both the Dapr pub/sub broker and the default Dapr state store (the Audit service stores its data in PostgreSQL — see ADR 0009).
- A single Redis container supports both Dapr building blocks, making the overall architecture simpler and easier to manage.

## Consequences

- **Positive:** The solution keeps the Docker Compose setup small, provides good performance, and makes local development straightforward.
- **Negative:** Dapr's Redis pub/sub runs on Redis Streams, which gives consumer groups and at-least-once delivery, but fewer messaging features than dedicated brokers, such as advanced routing and dead-letter queues. At-least-once delivery also means consumers must deduplicate redelivered events, which the audit projections do.
- **Alternative rejected:** RabbitMQ provides more advanced messaging capabilities, but it would introduce another service to maintain while still requiring a separate state store such as Redis.
