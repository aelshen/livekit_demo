"""Smart-home support MCP server.

Exposes customer/commerce/device/handoff tools backed by mock data
(mcp_server/data) over MCP, so the LiveKit voice agent (agent/main.py) can
consume them as a single external tool source, and so the same tools can be
exercised standalone via an MCP inspector or Claude Desktop.

Run for the LiveKit agent to connect to over HTTP:
    python -m mcp_server.server --http --port 8089

Run over stdio (e.g. from an MCP inspector or a Claude Desktop config):
    python -m mcp_server.server
"""

import argparse

from mcp.server.fastmcp import FastMCP

from mcp_server.tools import commerce, customer, device, handoff

mcp = FastMCP("smart-home-support")

for _module in (customer, commerce, device, handoff):
    _module.register(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()

    if args.http:
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
