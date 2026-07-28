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

import asyncio
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from livekit.agents import Agent, AgentSession, JobContext, RunContext, WorkerOptions, cli, function_tool
from livekit.agents.voice.events import AgentFalseInterruptionEvent, AgentStateChangedEvent, MetricsCollectedEvent
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
    # gpt-4o-mini-tts's default pace reads as noticeably slow (confirmed by
    # measuring synthesis duration directly, not just by ear) — speed=1.0 gave
    # a ~9-word test phrase at ~190 wpm of *generated audio*, but the perceived
    # slowness was about cadence, not pitch, so the API's own speed knob is the
    # right lever. Effect is sub-linear for this model (1.4 measured ~75% of
    # baseline duration, not the ~71% a literal 1.4x would imply), so this is
    # picked from direct measurement, not the nominal value.
    return openai.TTS(voice="alloy", speed=1.35)


@dataclass
class CallState:
    account_number: str | None = None
    customer_info: dict | None = None
    # Set by log_handoff_summary once a human handoff has been logged. The
    # session isn't torn down here (the farewell hasn't been spoken yet at that
    # point — see the agent_state_changed handler in entrypoint); this just arms
    # the hang-up so it fires once the farewell finishes playing.
    should_end_call: bool = False


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
        # try/finally (matching llm_node above) so the span closes exactly once
        # no matter how the generator exits: normal exhaustion, an exception, or
        # cancellation via GeneratorExit/CancelledError during async-generator
        # cleanup — e.g. a genuine user barge-in interrupting the agent before
        # the first audio frame is produced. Without this, a cancelled TTS
        # request leaves the span open forever and the frontend bar grows to
        # infinity. finally in an async generator runs during aclose()/GC
        # cleanup (PEP 525), so this is the standard, safe pattern.
        try:
            async for frame in Agent.default.tts_node(self, text, model_settings):
                if first:
                    first = False
                    tracing.span_end(span, duration_ms=elapsed_ms(started))
                yield frame
        finally:
            if first:  # never reached the first frame; close the span once here
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
        # Arm the hang-up. We deliberately do NOT call session.shutdown() here:
        # this tool runs BEFORE the LLM has generated (let alone spoken) the
        # farewell, so shutting down now could resolve before any speech is in
        # flight and cut the call off silently. The agent_state_changed handler
        # in entrypoint() waits for the farewell to finish, then closes.
        context.userdata.should_end_call = True
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


def _on_false_interruption(ev: AgentFalseInterruptionEvent) -> None:
    """Direct evidence of the pause-then-resume behavior, instead of inferring
    it from LLM/TTS timing gaps after the fact. Fires whenever VAD (mis)fired
    a START_OF_SPEECH that never turned into real, confirmed speech — shows up
    in the Live Trace panel like any other narrative event (not a duration, so
    it isn't in TIMELINE_ONLY_EVENTS)."""
    trace("agent.false_interruption", resumed=ev.resumed)


# Upper bound (seconds) on how long we'll wait for the post-handoff farewell to
# start + finish before ending the call anyway. Generous on purpose: the normal
# path (farewell spoken, then a speaking->non-speaking transition) almost always
# fires well within this, so the timeout only bites when the follow-up reply
# never arrives at all (see below). Big enough not to clip a slow-but-real
# farewell; small enough that a stuck call still ends on its own.
_FAREWELL_FALLBACK_TIMEOUT_S = 14.0


def _make_end_call_handler(session: "AgentSession[CallState]"):
    """Build the agent_state_changed handler that hangs up after a handoff.

    log_handoff_summary only sets userdata.should_end_call; nothing else ever
    closed the session, so the orchestrator kept getting re-invoked on every
    ambient-noise "turn" and regenerating a goodbye forever (the looping bug).

    Two-phase gate. The signal that the farewell finished playing is a
    transition OUT of "speaking" — BUT only if it belongs to the farewell.
    Firing on any speaking-exit after the flag is set is wrong: a stray/residual
    transition (e.g. the tail of a sentence that was interrupted right as the
    handoff tool ran) can look identical and cut the call off before the
    farewell is ever spoken (the "cuts off the farewell" bug). So we first wait
    to positively observe the farewell *starting* — a transition INTO "speaking"
    that happens after the flag is set — and only then treat the next exit from
    "speaking" as "the farewell is done." A one-shot guard fires shutdown once.

    Safety net. If the follow-up reply never generates at all — which really
    happens: when a user barge-in leaves the pipeline interrupted at the moment
    the handoff tool returns, LiveKit skips the post-tool-call reply (the
    interrupted speech handle returns before producing any LLM/TTS), so no
    "->speaking" transition ever arrives and the two-phase gate would otherwise
    wait forever — a delayed fallback task ends the call after
    _FAREWELL_FALLBACK_TIMEOUT_S. Both paths share the `ended` guard so whichever
    fires first wins and the other no-ops; the fallback is armed exactly once,
    the first time we see the flag set.

    `session.on` requires a plain sync callback (it rejects coroutine
    functions), matching _log_metrics; session.shutdown() is itself sync and
    schedules the close, draining any in-flight speech first (drain=True
    default)."""
    ended = False
    farewell_started = False
    fallback_armed = False

    def _end_call(reason: str) -> None:
        nonlocal ended
        if ended:
            return
        ended = True
        trace("orchestrator.call_ended", account_number=session.userdata.account_number, reason=reason)
        session.shutdown()

    async def _fallback() -> None:
        await asyncio.sleep(_FAREWELL_FALLBACK_TIMEOUT_S)
        # No-ops cleanly if the normal path already ended the call.
        if not ended:
            _end_call("fallback_timeout")

    def _on_agent_state_changed(ev: AgentStateChangedEvent) -> None:
        nonlocal farewell_started, fallback_armed
        if ended or not session.userdata.should_end_call:
            return
        # Arm the safety net once, the first time we observe the flag set, so a
        # farewell that never arrives still degrades to "the call ends anyway".
        if not fallback_armed:
            fallback_armed = True
            asyncio.create_task(_fallback())
        # Phase 1: wait until we've seen the farewell actually start playing.
        if not farewell_started:
            if ev.new_state == "speaking":
                farewell_started = True
            return
        # Phase 2: the first exit from "speaking" after that is the farewell end.
        if ev.old_state == "speaking" and ev.new_state != "speaking":
            _end_call("farewell_finished")

    return _on_agent_state_changed


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    session = AgentSession[CallState](
        userdata=CallState(),
        stt=_select_stt(),
        llm=_select_llm(),
        tts=_select_tts(),
        # activation_threshold raised from the library default (0.5): confirmed
        # by reading livekit-agents' own audio_recognition.py that a raw VAD
        # START_OF_SPEECH event alone is what pauses the agent's speech, before
        # any "is this real" classification happens downstream. Reproduced live
        # with the mic hardware-muted (so acoustic echo is ruled out) — VAD was
        # still firing on the residual noise floor, causing the agent to pause
        # mid-sentence and then resume in place a few seconds later once
        # nothing coherent followed (the false_interruption_timeout window
        # below). Raising this is the actual fix; the interruption-policy
        # tuning below only ever handled what happens *after* VAD already
        # (mis)fired, not the firing itself. If this ever makes the agent slow
        # to notice genuine soft/quiet speech, come back down a bit — this is
        # a real sensitivity/false-positive tradeoff, not a fixed constant.
        vad=silero.VAD.load(activation_threshold=0.7),
        turn_handling={
            "turn_detection": MultilingualModel(),
            # Defense against a stray VAD misfire getting treated as a real
            # interruption (a stronger mic-noise floor than expected, a breath,
            # brief background noise — acoustic echo without headphones was one
            # cause we found, but not the only one; see the VAD activation
            # threshold above for the more fundamental fix). This doesn't
            # prevent VAD from firing; it makes a misfire less likely to be
            # treated as a real interruption, and recovers cleanly when one
            # slips through anyway.
            "interruption": {
                "mode": "adaptive",  # ML-based, not raw VAD energy
                "min_words": 2,  # STT must have actually heard words
                "resume_false_interruption": True,  # already the default; explicit since it's exactly this scenario
                # How long to wait, after something gets flagged as a possible
                # interruption (an echo blip, a breath, background noise), before
                # giving up on real speech following and having the agent resume
                # with something like "sorry about that, can you tell me...".
                # The library default (2.0s) is tuned for quick back-and-forth,
                # not for a caller pausing to think or go find an account/order
                # number — that legitimately takes longer than 2s, and was
                # getting misread as "nothing's coming" every time. Widened to
                # give real thinking pauses room; the cost is a longer silent
                # gap in the rarer case where it truly was just noise and the
                # caller says nothing at all.
                "false_interruption_timeout": 6.0,
            },
        },
    )
    session.on("metrics_collected", _log_metrics)
    session.on("agent_false_interruption", _on_false_interruption)

    await session.start(agent=Orchestrator(), room=ctx.room)

    # End the call after a human handoff has been logged AND the farewell has
    # actually finished being spoken (see _make_end_call_handler).
    session.on("agent_state_changed", _make_end_call_handler(session))

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
