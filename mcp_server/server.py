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
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.tools import commerce, customer, device, handoff

mcp = FastMCP("smart-home-support")

for _module in (customer, commerce, device, handoff):
    _module.register(mcp)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", action="store_true", help="Serve over streamable HTTP instead of stdio")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--host", default="0.0.0.0", help="Bind address for --http (default: 0.0.0.0, reachable from other containers)")
    args = parser.parse_args()

    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        # FastMCP's default DNS-rebinding protection only allows Host headers
        # of localhost/127.0.0.1/::1 — that rejects requests from other
        # docker-compose containers (Host: mcp-server:8089). This server is
        # only ever called by our own agent/frontend processes server-side,
        # never reached directly from a browser, so that protection (aimed at
        # browser-based DNS-rebinding attacks) doesn't apply here.
        mcp.settings.transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
