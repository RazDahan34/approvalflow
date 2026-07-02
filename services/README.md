# Services

Seven microservices, decomposed by **volatility** (IDesign) rather than by function. Each one is independently
deployable, owns its own data, and talks to the others **only through Dapr** — pub/sub for events,
service-invocation for synchronous calls.

| Service | IDesign layer | Responsibility |
|---|---|---|
| `gateway` | Client | Single external entry point; rate-limiting; JWT auth + roles; routing. |
| `intake` | Client / Manager | Async submission → immediate tracking id; duplicate / idempotency check; outbox → `invoice.submitted`. |
| `orchestrator` | Manager | The workflow brain: deterministic **router** (final decision), payment **saga**, durable **HITL** pause/resume. |
| `ai-decision` | Engine | The agent: extract → classify → confidence → cite rules → fraud signals. RAG over policy; swappable LLM; MCP tools. |
| `payment` | Engine + ResourceAccess | Executes payment; reserves / releases budget (compensation); idempotent; injectable failure. |
| `notification` | Engine | Delivers the final outcome to the submitter, asynchronously. |
| `audit` | Engine + ResourceAccess | Append-only decision trail by correlation id; ceiling-proof ledger; dashboard / reporting data. |
| `mcp-server` | Engine (tool plane) | The agent's tools (`fetch_policy`, `lookup_vendor`, `get_autonomy_thresholds`) over the Model Context Protocol (B2). |

> **The agent only _recommends_; the orchestrator's router _decides_.** That separation is what makes the
> autonomy ceiling provable (M12 / F10).
