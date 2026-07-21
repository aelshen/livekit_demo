"""In-memory mock data access layer backing the MCP tools.

Loads customers.json / orders.json / tickets.json / device_docs/*.md once at
import time and exposes small query helpers. This stands in for a real
CRM/order/ticketing system for the demo.
"""

import json
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent
DEVICE_DOCS_DIR = DATA_DIR / "device_docs"

with open(DATA_DIR / "customers.json") as f:
    _CUSTOMERS = json.load(f)

with open(DATA_DIR / "orders.json") as f:
    _ORDERS = json.load(f)

with open(DATA_DIR / "tickets.json") as f:
    _TICKETS = json.load(f)

_CUSTOMERS_BY_ACCOUNT = {c["account_number"]: c for c in _CUSTOMERS}
_CUSTOMERS_BY_PHONE = {c["phone_number"]: c for c in _CUSTOMERS}


def find_customer(phone_number: Optional[str] = None, account_number: Optional[str] = None) -> Optional[dict]:
    """Look up a customer record by phone number or account number."""
    if account_number and account_number in _CUSTOMERS_BY_ACCOUNT:
        return _CUSTOMERS_BY_ACCOUNT[account_number]
    if phone_number and phone_number in _CUSTOMERS_BY_PHONE:
        return _CUSTOMERS_BY_PHONE[phone_number]
    return None


def get_devices(account_number: str) -> list[dict]:
    customer = _CUSTOMERS_BY_ACCOUNT.get(account_number)
    return customer["devices"] if customer else []


def get_device(account_number: str, device_id: str) -> Optional[dict]:
    for device in get_devices(account_number):
        if device["device_id"] == device_id:
            return device
    return None


def get_orders(account_number: str) -> list[dict]:
    return _ORDERS.get(account_number, [])


def get_order(account_number: str, order_id: str) -> Optional[dict]:
    for order in get_orders(account_number):
        if order["order_id"] == order_id:
            return order
    return None


def get_tickets(account_number: str, device_id: Optional[str] = None) -> list[dict]:
    tickets = _TICKETS.get(account_number, [])
    if device_id:
        tickets = [t for t in tickets if t.get("device_id") == device_id]
    return tickets


def search_device_docs(device_type: str, query: str) -> Optional[str]:
    """Keyword search over the local troubleshooting KB for one device type.

    Small enough corpus (one markdown file per device type) that a simple
    substring/keyword match is enough — no embeddings needed for this demo.
    """
    doc_path = DEVICE_DOCS_DIR / f"{device_type}.md"
    if not doc_path.exists():
        return None

    text = doc_path.read_text()
    query_terms = [t.lower() for t in query.split() if len(t) > 2]

    sections = text.split("\n## ")
    sections = [sections[0]] + [f"## {s}" for s in sections[1:]]
    matches = [s for s in sections if any(term in s.lower() for term in query_terms)]
    if not matches:
        return None

    return "\n\n".join(matches)
