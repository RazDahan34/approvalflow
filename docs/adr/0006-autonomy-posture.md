# ADR 0006 — Autonomy Posture: Category-Aware Ceiling

- **Status:** Accepted
- **Date:** 2026-07-01
- **Deciders:** Raz Dahan

## Context

- One of the main design decisions was determining which expenses the agent can approve without human involvement.
- Approving everything automatically creates financial risk, while escalating every request would defeat the purpose of the system.
- The policy approval limits and the autonomy limits represent different concepts: one defines when a manager must approve, while the other defines when the AI can make a decision on its own.

## Decision

- I chose category-based autonomy ceilings: **travel $500, hardware $350, meals $250, SaaS $200, and other $100**, with an overall maximum of **$500** and a minimum confidence score of **0.80**.
- Categories with stronger deterministic validation are allowed higher autonomy, while less structured categories receive lower limits.
- All thresholds are stored as external configuration and enforced by the deterministic router.

## Consequences

- **Positive:** The solution balances automation and safety, allows more low-risk requests to be approved automatically, keeps policy changes configurable, and provides a clear maximum autonomy limit of **$500**.
- **Negative:** The policy is more complex than using a single limit, and higher autonomy for some categories slightly increases the financial risk if the AI makes an incorrect recommendation.
- **Alternative rejected:** I rejected a zero-autonomy approach, a single fixed limit for all categories, and using the policy approval limits as autonomy limits because they do not provide the right balance between automation and risk.