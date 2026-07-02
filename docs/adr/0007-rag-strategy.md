# ADR 0007 — RAG Strategy: Hybrid Structural + Lexical Retrieval (No Vector DB)

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Raz Dahan

## Context

- The system should provide the LLM only with the relevant policy rules instead of the entire policy document.
- Since the policy consists of a small, structured markdown file with a limited number of stable rules, a lightweight retrieval approach is sufficient.

## Decision

- I implemented a **hybrid retriever** (`approvalflow_common.policy_rag`) that combines two retrieval methods:
  - **Structural retrieval** selects rules based on invoice attributes such as category, missing receipts, unknown vendors, and other predefined conditions.
  - **Lexical retrieval** uses keyword matching to find additional relevant rules that may not be selected by the structural logic.
- The policy file is mounted as a volume, so it can be updated without rebuilding the application.

## Consequences

- **Positive:** The retrieval process is simple, explainable, easy to test, and keeps prompts small without requiring additional infrastructure.
- **Negative:** Keyword matching is less effective than embeddings when dealing with paraphrased text, but this limitation is acceptable given the small size of the policy.
- **Alternative rejected:** Using embeddings with a vector database would add unnecessary complexity and dependencies while providing little benefit for such a small and structured policy.