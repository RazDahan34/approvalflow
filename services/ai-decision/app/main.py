"""AI-Decision service (Engine).

Wraps the agent: extract -> classify -> confidence -> cite rules -> flag fraud.
This service only RECOMMENDS; the orchestrator's deterministic router decides.
The LLM provider is swappable by configuration — gemini | groq | openrouter | stub (M15).
"""

from pathlib import Path

from approvalflow_common import create_app, get_correlation_id, get_logger, get_settings
from approvalflow_common.agent import get_provider
from approvalflow_common.llm import ProviderError
from approvalflow_common.policy_rag import PolicyIndex
from approvalflow_common.schemas import InvoiceSubmission
from fastapi import HTTPException

SERVICE = "ai-decision"

settings = get_settings()
app = create_app(SERVICE)
log = get_logger(SERVICE)

# RAG index over the policy document (N5): the file is volume-mounted, so editing the
# policy needs a restart, not a rebuild (F7). Missing file -> fail fast at boot (M15).
policy_index: PolicyIndex | None = None
if Path(settings.policy_path).exists():
    policy_index = PolicyIndex.from_file(settings.policy_path)
    log.info("policy indexed", extra={"rules": len(policy_index.rules), "path": settings.policy_path})
elif settings.llm_provider != "stub":
    raise RuntimeError(f"policy file not found at '{settings.policy_path}' — required for RAG")
else:
    log.warning("policy file not found; stub provider runs without RAG", extra={"path": settings.policy_path})

# The agent's tools over MCP (B2): a real LLM provider consumes fetch_policy /
# lookup_vendor / get_autonomy_thresholds remotely; the stub stays self-contained.
tool_client = None
if settings.mcp_server_url and settings.llm_provider != "stub":
    from approvalflow_common.mcp_tools import MCPToolClient

    tool_client = MCPToolClient(settings.mcp_server_url)
    log.info("MCP tool client configured", extra={"url": settings.mcp_server_url})

# Built at startup: a misconfigured provider (e.g. missing API key) crashes the boot
# with a clear message instead of failing silently on the first request (M15).
provider = get_provider(settings.llm_provider, retriever=policy_index, tool_client=tool_client)
log.info("agent provider ready", extra={"provider": provider.name})

# App-level OTel spans (N4): the sidecar traces the hop TO us; this traces what the
# agent does INSIDE — retrieval, the model call, MCP tools — in the same trace.
from .telemetry import get_tracer, setup_tracing  # noqa: E402

setup_tracing(app)


@app.post("/recommend")
def recommend(invoice: InvoiceSubmission) -> dict:
    """Return the agent's advisory recommendation for one invoice."""
    tracer = get_tracer()
    try:
        with tracer.start_as_current_span("agent.recommend") as span:
            span.set_attribute("approvalflow.correlation_id", get_correlation_id())
            span.set_attribute("invoice.category", invoice.category.value)
            span.set_attribute("invoice.total", invoice.total)
            span.set_attribute("agent.provider", provider.name)
            recommendation = provider.recommend(invoice)
            span.set_attribute("agent.proposed_route", recommendation.proposed_route.value)
            span.set_attribute("agent.confidence", recommendation.confidence)
    except ProviderError as exc:
        # Fail cleanly, never silently (M15): loud structured log + explicit 502.
        # The orchestrator treats this as "agent unavailable" and escalates to a human.
        log.error("agent provider failed", extra={"provider": provider.name, "error": str(exc)})
        raise HTTPException(status_code=502, detail=f"agent provider error: {exc}") from exc

    log.info(
        "recommendation produced",
        extra={
            "provider": provider.name,
            "category": recommendation.category.value,
            "proposed_route": recommendation.proposed_route.value,
            "confidence": recommendation.confidence,
            "violations": recommendation.policy_violations,
            "fraud_signals": recommendation.fraud_signals,
        },
    )
    return recommendation.model_dump()


@app.get("/policy/rules")
def list_indexed_rules() -> dict:
    """Expose the RAG index for inspection (demo / audit aid)."""
    if policy_index is None:
        return {"rules": [], "note": "no policy file loaded"}
    return {"rules": [{"rule_id": r.rule_id, "section": r.section} for r in policy_index.rules]}
