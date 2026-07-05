# ADR 0004 — LLM Provider: Swappable OpenAI-Compatible Abstraction, Gemini Live, Stub for CI

- **Status:** Accepted
- **Date:** 2026-07-05
- **Deciders:** Raz Dahan

## Context

- The project requires (M15) the LLM provider to be configurable without changing the code and to handle provider failures safely.
- It also requires using free-tier models and providing a stub implementation for CI and evaluation to avoid rate limits.

## Decision

- I implemented a single `LLMProvider` that uses the OpenAI Chat Completions API format with a configurable base URL.
- This allows providers such as Gemini, Groq, and OpenRouter to be switched by changing environment variables instead of modifying the code.
- **Gemini** (`gemini-2.5-flash`) is used as the live provider because it offers a strong free tier and was already available for the project.
- For CI and automated testing, I use a deterministic offline stub that implements the same interface.
- If the provider fails because of API errors, timeouts, or quota limits, the AI service returns an error, and the Orchestrator treats the result as zero confidence so the request is automatically escalated for human approval.

## Consequences

- **Positive:** Switching providers only requires configuration changes, CI remains deterministic without API limits, and provider failures always result in human review instead of automatic approval.
- **Negative:** Using the OpenAI-compatible interface limits access to provider-specific features, and the offline stub cannot fully reflect the behavior of a real language model.
- **Alternatives rejected:** Native SDKs for each provider would increase maintenance effort, while running a local model would require additional resources without providing significant benefits for this project.
