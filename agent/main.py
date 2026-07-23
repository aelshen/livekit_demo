"""LiveKit voice agent entrypoint — Nestly Home smart-support demo.

Wires an AgentSession (STT, LLM, TTS, Silero VAD, LiveKit's turn detector) to
a single voice-facing Orchestrator Agent. STT/LLM/TTS each prefer their
"real" provider (Deepgram / Anthropic / Cartesia) but fall back to OpenAI
when that provider's key isn't set, so the demo still runs end-to-end with
only an OPENAI_API_KEY configured (see _select_stt/_select_llm/_select_tts
below). agent/experts.py does the same fallback for the expert sub-agents.

The Orchestrator does NOT expose the MCP server's tools directly to its own
LLM — that would just be one agent with a flat tool list. Instead:

- `identify_customer` / `log_handoff_summary` are thin Orchestrator-owned
  tools that make a single direct call to the MCP server (see
  agent/mcp_client.py) — orchestration-level bookkeeping, not domain
  expertise.
- `ask_customer_expert` / `ask_commerce_expert` / `ask_device_expert`
  delegate a natural-language question to an independent Claude sub-agent
  (see agent/experts.py), each with its own system prompt and its own
  scoped subset of MCP tools, and return that sub-agent's synthesized
  answer. This is the actual multi-agent orchestrator-workers pattern: the
  Orchestrator's LLM never sees the experts' tools, only their answers.

Run the MCP server first, in a separate process:
    python -m mcp_server.server --http --port 8089

Then run this worker in dev mode (connects to LiveKit Agents Playground):
    python -m agent.main dev
"""

import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.agents.voice.events import MetricsCollectedEvent
from livekit.plugins import anthropic, cartesia, deepgram, openai, silero
from livekit.plugins.turn_detector.multilingual import MultilingualModel

from agent import mcp_client, tracing
from agent.experts import COMMERCE_EXPERT, CUSTOMER_EXPERT, DEVICE_EXPERT, run_expert
from agent.tracing import elapsed_ms, trace

load_dotenv()

PROMPTS_DIR = Path(__file__).parent / "prompts"

# frontend/server.py names rooms "support-{account_number}-{8 hex chars}"
# when the demo console's customer picker starts a call (account_number
# itself may contain hyphens, e.g. "ACC-10234", hence anchoring on the fixed
# -length hex suffix rather than splitting on the first "-"), so the caller
# can be auto-identified instead of having to say their account number aloud.
ROOM_ACCOUNT_RE = re.compile(r"^support-(?P<account_number>.+)-[0-9a-f]{8}$")


def _account_number_from_room_name(name: str) -> str | None:
    match = ROOM_ACCOUNT_RE.match(name)
    return match.group("account_number") if match else None


def _select_stt():
    if os.environ.get("DEEPGRAM_API_KEY"):
        return deepgram.STT(model="nova-3", language="en")
    return openai.STT(model="gpt-4o-transcribe")


def _select_llm():
    if os.environ.get("ANTHROPIC_API_KEY"):
        return anthropic.LLM(model="claude-sonnet-5")
    return openai.LLM(model="gpt-4o-mini")


def _select_tts():
    if os.environ.get("CARTESIA_API_KEY"):
        return cartesia.TTS()
    return openai.TTS(voice="alloy")


@dataclass
class CallState:
    account_number: str | None = None
    customer_info: dict | None = None


class Orchestrator(Agent):
    """The one voice-facing agent for the whole call — see prompts/orchestrator.md."""

    def __init__(self) -> None:
        super().__init__(instructions=(PROMPTS_DIR / "orchestrator.md").read_text())

    # --- Live latency spans for the realtime voice pipeline -----------------
    # The metrics system only reports LLM/TTS latency AFTER the fact (via
    # metrics_collected), which can't drive a live-growing bar. These node
    # overrides give us the START signal: a span opens the moment TTS/LLM work
    # begins and the frontend grows its bar every frame until the matching end.
    #
    # CRITICAL: both must fully re-yield the default node's async iterable —
    # swallowing or short-circuiting an item makes the agent go mute. The
    # `async for ...: yield` pattern below re-yields every frame/chunk.
    async def tts_node(self, text, model_settings):
        # User ask: measure request -> FIRST audio byte (TTFB), grown live.
        # While waiting for that first frame the bar grows; on the first frame
        # it pins to the measured TTFB. Remaining frames still stream through.
        span = tracing.span_start("tts", label="TTS")
        started = time.monotonic()
        first = True
        async for frame in Agent.default.tts_node(self, text, model_settings):
            if first:
                first = False
                tracing.span_end(span, duration_ms=elapsed_ms(started))
            yield frame
        if first:  # no frames produced; close the span so it doesn't hang open
            tracing.span_end(span, duration_ms=elapsed_ms(started))

    async def llm_node(self, chat_ctx, tools, model_settings):
        # Fires once per orchestrator turn, and again for each tool-call round
        # trip — each invocation is its own span, covering entry -> generator
        # exhaustion (the full generation for that step).
        span = tracing.span_start("llm-orch", label="LLM (orchestrator)")
        started = time.monotonic()
        try:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                yield chunk
        finally:
            tracing.span_end(span, duration_ms=elapsed_ms(started))

    @function_tool()
    async def identify_customer(
        self,
        context: RunContext[CallState],
        phone_number: str | None = None,
        account_number: str | None = None,
    ) -> dict:
        """Look up the caller by phone number or account number — not by name.

        Call this first, before any expert tool, once the caller provides a
        phone or account number. If they've only given their name, ask for
        one of those instead of guessing. Already identified this call?
        Don't call this again — you already have their info.
        """
        if context.userdata.account_number:
            trace("orchestrator.identify_customer", note="already identified, skipped re-lookup", account_number=context.userdata.account_number)
            return context.userdata.customer_info

        result = await mcp_client.call_tool(
            "identify_customer", {"phone_number": phone_number, "account_number": account_number}
        )
        if result.get("found"):
            context.userdata.account_number = result["account_number"]
            context.userdata.customer_info = result
        trace("orchestrator.identify_customer", phone_number=phone_number, account_number=account_number, result=result)
        return result

    @function_tool()
    async def ask_customer_expert(self, context: RunContext[CallState], question: str) -> str:
        """Delegate an account profile, subscription status, or registered-devices question to the Customer Expert sub-agent."""
        trace("orchestrator.delegate", expert="customer", question=question, account_number=context.userdata.account_number)
        started = time.monotonic()
        answer = await run_expert(CUSTOMER_EXPERT, question, context.userdata.account_number)
        trace("orchestrator.delegate_result", expert="customer", answer=answer, duration_ms=elapsed_ms(started))
        return answer

    @function_tool()
    async def ask_commerce_expert(self, context: RunContext[CallState], question: str) -> str:
        """Delegate an order history or order status question to the Commerce Expert sub-agent."""
        trace("orchestrator.delegate", expert="commerce", question=question, account_number=context.userdata.account_number)
        started = time.monotonic()
        answer = await run_expert(COMMERCE_EXPERT, question, context.userdata.account_number)
        trace("orchestrator.delegate_result", expert="commerce", answer=answer, duration_ms=elapsed_ms(started))
        return answer

    @function_tool()
    async def ask_device_expert(self, context: RunContext[CallState], question: str) -> str:
        """Delegate a device status or troubleshooting question to the Device Expert sub-agent."""
        trace("orchestrator.delegate", expert="device", question=question, account_number=context.userdata.account_number)
        started = time.monotonic()
        answer = await run_expert(DEVICE_EXPERT, question, context.userdata.account_number)
        trace("orchestrator.delegate_result", expert="device", answer=answer, duration_ms=elapsed_ms(started))
        return answer

    @function_tool()
    async def log_handoff_summary(self, context: RunContext[CallState], reason: str, summary: str) -> dict:
        """Persist a call summary before transferring to a human agent.

        Summarize the call yourself first (what the customer needed, what
        you found, what's unresolved) and pass that as `summary`.
        """
        result = await mcp_client.call_tool(
            "log_handoff_summary",
            {
                "account_number": context.userdata.account_number or "unknown",
                "reason": reason,
                "summary": summary,
            },
        )
        trace("orchestrator.handoff", reason=reason, summary=summary, account_number=context.userdata.account_number)
        return result


def _log_metrics(ev: MetricsCollectedEvent) -> None:
    """Forward the LiveKit metrics that aren't already covered by live spans.

    LLM and TTS latency now come from the Orchestrator's llm_node/tts_node
    overrides as live-growing `timeline.span_*` events, so we deliberately no
    longer forward `llm_metrics`/`tts_metrics` here (that would double-draw the
    same bar, once live and once after the fact). STT stays as the timeline's
    open-mic heartbeat baseline and EOU stays as the turn-taking marker."""
    m = ev.metrics
    if m.type == "stt_metrics":
        trace("metrics.stt", duration_ms=round(m.duration * 1000), audio_duration_ms=round(m.audio_duration * 1000))
    elif m.type == "eou_metrics":
        trace("metrics.eou", end_of_utterance_delay_ms=round(m.end_of_utterance_delay * 1000))


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession[CallState](
        userdata=CallState(),
        stt=_select_stt(),
        llm=_select_llm(),
        tts=_select_tts(),
        vad=silero.VAD.load(),
        turn_handling={
            "turn_detection": MultilingualModel(),
            # Defense against acoustic echo: without headphones, the agent's
            # own TTS played through speakers can leak into the mic and get
            # misread as the caller talking — the round trip is way longer
            # than browser-native echo cancellation is designed to track, so
            # it can't cancel it. This doesn't fix that (headphones do); it
            # makes a stray echo blip less likely to register as a real
            # interruption, and recovers cleanly when one slips through.
            "interruption": {
                "mode": "adaptive",  # ML-based, not raw VAD energy
                "min_words": 2,  # STT must have actually heard words
                "resume_false_interruption": True,  # already the default; explicit since it's exactly this scenario
                "false_interruption_timeout": 2.0,
            },
        },
    )
    session.on("metrics_collected", _log_metrics)

    await session.start(agent=Orchestrator(), room=ctx.room)

    # If the demo console's customer picker started this call, the account is
    # already known — skip the identification step and greet by name.
    account_number = _account_number_from_room_name(ctx.room.name)
    if account_number:
        result = await mcp_client.call_tool("identify_customer", {"account_number": account_number})
        trace("orchestrator.identify_customer", account_number=account_number, result=result, source="room_name")
        if result.get("found"):
            session.userdata.account_number = result["account_number"]
            session.userdata.customer_info = result
            await session.generate_reply(
                instructions=(
                    f"Greet the caller warmly by name ({result['name']}) as Alex from Nestly "
                    "Home support. They're already identified via the demo console, so don't "
                    "ask for a phone or account number — just ask how you can help."
                )
            )
            return

    # Greet first — a support call shouldn't require the customer to speak first.
    await session.generate_reply(
        instructions=(
            "Greet the caller warmly as Alex from Nestly Home support, "
            "and ask for their phone number or account number."
        )
    )


if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
