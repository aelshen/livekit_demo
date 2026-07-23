"""Structured tracing for the multi-agent call flow and its latency.

Two kinds of events land in the same stream, so they can be read as one
growing timeline of a call:

- Our own orchestration events (`orchestrator.*`, `expert.*`): the
  Orchestrator's delegation decisions and each expert sub-agent's own tool
  calls, which happen in plain async Python (agent/experts.py,
  agent/mcp_client.py) outside LiveKit's realtime pipeline, so nothing
  shows them by default.
- LiveKit's own per-component latency metrics (`metrics.stt/llm/tts/eou`),
  forwarded from `AgentSession`'s `metrics_collected` event (see
  agent/main.py) — how long STT/LLM/TTS actually took for the realtime
  voice loop.

Every call to `trace()` appends one JSON line to logs/trace.jsonl (and
echoes a short line to the console) so a call's full "who was asked what,
which tools they called, what came back, how long each step took" trail can
be inspected live or replayed afterward. Use scripts/view_trace.py to read
it back.
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


def elapsed_ms(started: float) -> int:
    """Milliseconds since a `time.monotonic()` timestamp — for timing a step before tracing it."""
    return round((time.monotonic() - started) * 1000)


# --- Span model --------------------------------------------------------------
# A timeline bar is now two events, not one: a `timeline.span_start` when an
# operation begins and a `timeline.span_end` when it finishes, correlated by a
# monotonic integer span id. The frontend opens a bar on start and GROWS it
# live against the wall clock every animation frame, then pins it + freezes its
# final duration label on end. This is what lets bars advance in real time
# instead of popping into existence fully-formed only once the work is done.
#
# Single event loop / single process, so a plain module-level counter is a
# fine span-id source (no locking needed).
_span_counter = 0


def span_start(lane: str, label: str | None = None, **fields: Any) -> int:
    """Open a timeline span and return its id. `lane` must be one of the
    frontend's timeline lane categories: "llm-orch", "tts", "llm-expert",
    "tool". Pair every call with a `span_end(id, ...)`."""
    global _span_counter
    _span_counter += 1
    span_id = _span_counter
    trace("timeline.span_start", span_id=span_id, lane=lane, label=label, **fields)
    return span_id


def span_end(span_id: int, duration_ms: int | None = None) -> None:
    """Close a previously opened timeline span. `duration_ms`, when provided,
    is the authoritative measured duration the frontend pins as the final
    label (on-screen growth uses the browser's own receipt clock)."""
    trace("timeline.span_end", span_id=span_id, duration_ms=duration_ms)
