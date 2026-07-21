"""Commerce Expert tools — order history and order status."""

from mcp_server.data import store


def register(mcp):
    @mcp.tool()
    def get_order_history(account_number: str) -> dict:
        """List all orders for an account, most recent first."""
        orders = store.get_orders(account_number)
        return {"orders": sorted(orders, key=lambda o: o["order_date"], reverse=True)}

    @mcp.tool()
    def get_order_status(account_number: str, order_id: str | None = None) -> dict:
        """Get the status of a specific order, or the most recent order if order_id is omitted."""
        orders = store.get_orders(account_number)
        if not orders:
            return {"found": False}

        if order_id:
            order = store.get_order(account_number, order_id)
            return {"found": order is not None, **(order or {})}

        most_recent = sorted(orders, key=lambda o: o["order_date"], reverse=True)[0]
        return {"found": True, **most_recent}
