"""Thin helper for one-off calls to the smart-home-support MCP server.

Used directly by the Orchestrator for its own tools (identify_customer,
log_handoff_summary). Domain expert sub-agents (agent/experts.py) manage
their own longer-lived MCP session instead, since they may call several
tools across a multi-turn reasoning loop.
"""

import json
import os

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8089/mcp")


async def call_tool(name: str, arguments: dict) -> dict:
    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(name, arguments)
            text = "".join(block.text for block in result.content if block.type == "text")
            return json.loads(text) if text else {}
