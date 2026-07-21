"""Customer Expert tools — identity, profile, subscription, registered devices."""

from mcp_server.data import store


def register(mcp):
    @mcp.tool()
    def identify_customer(phone_number: str | None = None, account_number: str | None = None) -> dict:
        """Look up a customer by phone number or account number.

        Returns the customer's name, account number, and subscription
        tier/status, or {"found": False} if no match. Call this first, before
        any other tool, once the caller has provided their phone number or
        account number.
        """
        customer = store.find_customer(phone_number=phone_number, account_number=account_number)
        if not customer:
            return {"found": False}
        return {
            "found": True,
            "account_number": customer["account_number"],
            "name": customer["name"],
            "subscription_tier": customer["subscription_tier"],
            "subscription_status": customer["subscription_status"],
        }

    @mcp.tool()
    def get_account_profile(account_number: str) -> dict:
        """Get the full account profile: name, email, member since, subscription."""
        customer = store.find_customer(account_number=account_number)
        if not customer:
            return {"found": False}
        return {
            "found": True,
            "account_number": customer["account_number"],
            "name": customer["name"],
            "email": customer["email"],
            "member_since": customer["member_since"],
            "subscription_tier": customer["subscription_tier"],
            "subscription_status": customer["subscription_status"],
        }

    @mcp.tool()
    def get_subscription_status(account_number: str) -> dict:
        """Get just the subscription tier and status (active/past_due/etc.) for an account."""
        customer = store.find_customer(account_number=account_number)
        if not customer:
            return {"found": False}
        return {
            "found": True,
            "subscription_tier": customer["subscription_tier"],
            "subscription_status": customer["subscription_status"],
        }

    @mcp.tool()
    def get_registered_devices(account_number: str) -> dict:
        """List the devices registered on an account (id, type, name, room, status).

        Does not include live telemetry — use the device expert's
        get_device_status tool for that.
        """
        devices = store.get_devices(account_number)
        return {
            "devices": [
                {
                    "device_id": d["device_id"],
                    "type": d["type"],
                    "name": d["name"],
                    "room": d["room"],
                    "status": d["status"],
                }
                for d in devices
            ]
        }
