"""Handoff tool — persists a call-summary record when transferring to a human."""

import json
from datetime import datetime, timezone
from pathlib import Path

HANDOFF_LOG = Path(__file__).parent.parent / "data" / "handoff_log.json"


def register(mcp):
    @mcp.tool()
    def log_handoff_summary(account_number: str, reason: str, summary: str) -> dict:
        """Persist a call summary for the human agent picking up the transfer.

        Stands in for pushing a screen-pop/summary to a real ticketing system.
        The caller (the Orchestrator) is responsible for generating the
        summary text from the conversation before calling this tool.
        """
        record = {
            "account_number": account_number,
            "reason": reason,
            "summary": summary,
            "logged_at": datetime.now(timezone.utc).isoformat(),
        }

        records = []
        if HANDOFF_LOG.exists():
            records = json.loads(HANDOFF_LOG.read_text())
        records.append(record)
        HANDOFF_LOG.write_text(json.dumps(records, indent=2))

        return {"logged": True}
