# ADR 0008 — Agent Tools over a Standalone MCP Server

- **Status:** Accepted
- **Date:** 2026-07-03
- **Deciders:** Raz Dahan

## Context

- The project requires (B2) exposing the agent's tools through the Model Context Protocol (MCP) instead of calling them directly inside the AI service.
- I considered implementing the tools inside the `ai-decision` service or running them as a separate service.

## Decision

- I chose to implement a standalone **`mcp-server`** service using FastMCP.
- The AI service communicates with the MCP server as a client, discovers the available tools, executes them remotely, and uses the results to generate its final response.
- The tool implementations remain in a shared library (`agent_tools.py`), while the MCP server only exposes them through the protocol.
- The MCP server does not use a Dapr sidecar or expose a public port because it is intended only for internal communication between services.

## Consequences

- **Positive:** The solution demonstrates real MCP-based tool calling, supports dynamic tool discovery, and allows the same tools to be reused by other agents in the future.
- **Negative:** It introduces an additional service and another possible failure point, but tool failures are handled safely by escalating the request for human approval.
- **Alternative rejected:** An in-process implementation would be simpler, but it would not demonstrate the actual benefits of using the MCP protocol.