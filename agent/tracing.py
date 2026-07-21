"""Structured tracing for the multi-agent call flow.

This is deliberately separate from LiveKit's own session telemetry (STT/LLM/
TTS/VAD latency metrics), which only covers the realtime voice pipeline.
The Orchestrator's delegation decisions and each expert sub-agent's own tool
calls happen in plain async Python (agent/experts.py, agent/mcp_client.py),
outside that pipeline, so nothing shows them by default. Every call to
`trace()` appends one JSON line to logs/trace.jsonl (and echoes a short line
to the console) so a call's full "who was asked what, which tools they
called, what came back" trail can be inspected live or replayed afterward.
Use scripts/view_trace.py to read it back.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
TRACE_FILE = LOG_DIR / "trace.jsonl"

logger = logging.getLogger("nestly.trace")


def trace(event: str, **fields: Any) -> None:
    """Record one step of the call flow: an orchestrator decision, a
    delegation to an expert, or a tool call an expert made."""
    record = {"ts": time.time(), "event": event, **fields}

    with open(TRACE_FILE, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")

    summary = " ".join(f"{k}={_truncate(v)}" for k, v in fields.items())
    logger.info("[%s] %s", event, summary)


def _truncate(value: Any, limit: int = 120) -> str:
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"
