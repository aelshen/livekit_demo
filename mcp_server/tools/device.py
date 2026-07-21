"""Device Expert tools — telemetry, troubleshooting KB, web search fallback, ticket history."""

import os

from mcp_server.data import store


def register(mcp):
    @mcp.tool()
    def get_device_status(account_number: str, device_id: str) -> dict:
        """Get live status/telemetry for one registered device.

        E.g. thermostat mode/target/current temp, lock state, camera recording
        state, sensor battery level.
        """
        device = store.get_device(account_number, device_id)
        if not device:
            return {"found": False}
        return {"found": True, **device}

    @mcp.tool()
    def search_troubleshooting_kb(device_type: str, issue: str) -> dict:
        """Search internal troubleshooting docs for a device type.

        device_type is one of: thermostat, camera, lock, sensor. Try this
        before web_search_troubleshooting — it's faster and covers the most
        common issues for this product line.
        """
        result = store.search_device_docs(device_type, issue)
        if not result:
            return {"found": False}
        return {"found": True, "guidance": result}

    @mcp.tool()
    def web_search_troubleshooting(query: str) -> dict:
        """Fall back to a live web search when the internal KB has no guidance.

        Requires TAVILY_API_KEY to be configured; otherwise returns
        found=False so the caller can fall back to offering a human transfer.
        """
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return {
                "found": False,
                "reason": "No web search provider configured (set TAVILY_API_KEY).",
            }

        # TODO: wire up a real search call once a provider key is available, e.g.:
        #   import requests
        #   resp = requests.post(
        #       "https://api.tavily.com/search",
        #       json={"api_key": api_key, "query": query, "max_results": 3},
        #       timeout=10,
        #   )
        #   return {"found": True, "results": resp.json()["results"]}
        return {"found": False, "reason": "web search not yet implemented"}

    @mcp.tool()
    def search_support_tickets(account_number: str, device_id: str | None = None) -> dict:
        """Look up prior support tickets for this account, optionally scoped to one device."""
        tickets = store.get_tickets(account_number, device_id=device_id)
        return {"tickets": tickets}
