"""MCP server (B2) — the agent's tools over the Model Context Protocol.

A thin wrapper: the tool logic lives in `approvalflow_common.agent_tools` (pure and
unit-tested); this service exposes it over MCP streamable HTTP so the agent consumes
its tools remotely instead of in-process.
"""

import json
from pathlib import Path

from approvalflow_common import PolicyConfig, get_settings
from approvalflow_common.agent_tools import AgentToolbox
from approvalflow_common.logging import configure_logging, get_logger
from approvalflow_common.policy_rag import PolicyIndex
from mcp.server.fastmcp import FastMCP

SERVICE = "mcp-server"

settings = get_settings()
configure_logging(SERVICE, settings.log_level)
log = get_logger(SERVICE)

index = PolicyIndex.from_file(settings.policy_path)
vendors_path = Path(__file__).resolve().parent.parent / "data" / "vendors.json"
known_vendors = set(json.loads(vendors_path.read_text(encoding="utf-8"))["known_vendors"])
config = PolicyConfig(
    ceiling_usd=settings.autonomy_ceiling_usd,
    confidence_threshold=settings.autonomy_confidence,
)
toolbox = AgentToolbox(index, known_vendors, config)
log.info("toolbox ready", extra={"rules": len(index.rules), "vendors": len(known_vendors)})

mcp = FastMCP("approvalflow-tools", host="0.0.0.0", port=8000)


@mcp.tool()
def fetch_policy(category: str | None = None, query: str | None = None) -> str:
    """Fetch expense-policy clauses. Filter by category (meals|travel|saas|hardware)
    and/or a free-text query; with neither, returns the global rules."""
    return toolbox.fetch_policy(category, query)


@mcp.tool()
def lookup_vendor(name: str) -> str:
    """Check whether a vendor exists in the company's vendor master list."""
    return toolbox.lookup_vendor(name)


@mcp.tool()
def get_autonomy_thresholds() -> str:
    """Get the current autonomy posture (envelope, per-category ceilings, confidence)."""
    return toolbox.get_autonomy_thresholds()


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
