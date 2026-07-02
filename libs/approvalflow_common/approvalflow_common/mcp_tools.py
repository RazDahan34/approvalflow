"""MCP client used by the agent's tool loop (B2).

Tools are discovered dynamically from the MCP server (that is the point of the
protocol — the client hard-codes nothing), converted to OpenAI function schemas, and
executed over streamable HTTP. Any failure raises loudly; the caller's fail-closed
behavior (escalate to a human) does the rest.
"""

import asyncio
import json


class MCPToolClient:
    """Sync facade over the async MCP SDK — a handful of calls per invoice at most."""

    def __init__(self, url: str) -> None:
        self.url = url
        self._schemas: list[dict] | None = None

    # ── OpenAI-format schemas, discovered from the server and cached ──
    def tool_schemas(self) -> list[dict]:
        if self._schemas is None:
            tools = asyncio.run(self._list_tools())
            self._schemas = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.inputSchema,
                    },
                }
                for t in tools
            ]
        return self._schemas

    def call(self, name: str, arguments: dict) -> str:
        return asyncio.run(self._call(name, arguments))

    async def _session(self):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(self.url), ClientSession

    async def _list_tools(self):
        transport, session_cls = await self._session()
        async with transport as (read, write, _):
            async with session_cls(read, write) as session:
                await session.initialize()
                return (await session.list_tools()).tools

    async def _call(self, name: str, arguments: dict) -> str:
        transport, session_cls = await self._session()
        async with transport as (read, write, _):
            async with session_cls(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                texts = [c.text for c in result.content if getattr(c, "text", None)]
                return "\n".join(texts) if texts else json.dumps({"error": "empty tool result"})
