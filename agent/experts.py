"""Domain expert sub-agents delegated to by the Orchestrator.

Each expert is its own bounded tool-use loop, with its own system prompt and
its own scoped subset of the MCP server's tools (fetched fresh from the
server and filtered by name, not hand-copied — so adding a tool to
mcp_server/tools/device.py automatically becomes available to the Device
Expert without touching this file). This is what makes the system a genuine
orchestrator-workers multi-agent design rather than one LLM with a flat tool
list: the Orchestrator delegates a natural-language question to a worker,
the worker reasons and calls tools independently, and only the worker's
final synthesized answer crosses back to the Orchestrator.

Workers use a smaller/cheaper model than the Orchestrator — they do narrow,
bounded lookups, not sustained conversation, so latency and cost matter more
than conversational range here.

Provider: Anthropic (Claude Haiku) if ANTHROPIC_API_KEY is set, else falls
back to OpenAI (gpt-4o-mini) so the demo still runs end-to-end with only an
OPENAI_API_KEY. See agent/main.py for the same fallback on STT/LLM/TTS.
"""

import json
import os
from dataclasses import dataclass

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from agent.mcp_client import MCP_SERVER_URL
from agent.tracing import trace

PROVIDER = "anthropic" if os.environ.get("ANTHROPIC_API_KEY") else "openai"
EXPERT_MODEL = os.environ.get("EXPERT_MODEL") or (
    "claude-haiku-4-5-20251001" if PROVIDER == "anthropic" else "gpt-4o-mini"
)
MAX_TOOL_ITERATIONS = 4

_anthropic_client = None
_openai_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic

        _anthropic_client = anthropic.AsyncAnthropic()
    return _anthropic_client


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        import openai

        _openai_client = openai.AsyncOpenAI()
    return _openai_client


@dataclass
class ExpertConfig:
    name: str
    system_prompt: str
    allowed_tools: list[str]


CUSTOMER_EXPERT = ExpertConfig(
    name="Customer Expert",
    system_prompt=(
        "You are the Customer Expert, a backend specialist consulted by a voice "
        "support Orchestrator. Answer questions about account profile, "
        "subscription status, and registered devices using your tools. Be "
        "factual and concise - your answer will be spoken aloud by another "
        "agent, so avoid markdown or lists. If a tool reports not found, say so "
        "plainly rather than guessing."
    ),
    allowed_tools=["get_account_profile", "get_subscription_status", "get_registered_devices"],
)

COMMERCE_EXPERT = ExpertConfig(
    name="Commerce Expert",
    system_prompt=(
        "You are the Commerce Expert, a backend specialist consulted by a voice "
        "support Orchestrator. Answer questions about order history and order "
        "status using your tools. Be factual and concise - your answer will be "
        "spoken aloud by another agent, so avoid markdown or lists. If a tool "
        "reports not found, say so plainly rather than guessing."
    ),
    allowed_tools=["get_order_history", "get_order_status"],
)

DEVICE_EXPERT = ExpertConfig(
    name="Device Expert",
    system_prompt=(
        "You are the Device Expert, a backend specialist consulted by a voice "
        "support Orchestrator. Answer questions about live device status and "
        "troubleshooting using your tools. get_device_status needs an exact "
        "device_id, not a name — if you don't already have it, call "
        "get_registered_devices first and match the customer's description "
        "(e.g. \"garage motion sensor\") to its device_id and type. For "
        "troubleshooting, try search_troubleshooting_kb before "
        "web_search_troubleshooting. Check search_support_tickets if the "
        "customer references a past issue. Be factual and concise - your "
        "answer will be spoken aloud by another agent, so avoid markdown or "
        "lists. If a tool reports not found, say so plainly rather than "
        "guessing."
    ),
    allowed_tools=[
        "get_registered_devices",
        "get_device_status",
        "search_troubleshooting_kb",
        "web_search_troubleshooting",
        "search_support_tickets",
    ],
)


async def run_expert(config: ExpertConfig, question: str, account_number: str | None) -> str:
    """Run one bounded tool-use loop for a domain expert and return its final answer."""

    prompt = f"Question: {question}"
    if account_number:
        prompt += f"\nThe caller's account number is {account_number}."

    trace("expert.start", expert=config.name, question=question, account_number=account_number, provider=PROVIDER, model=EXPERT_MODEL)

    async with streamablehttp_client(MCP_SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            all_tools = [t for t in (await session.list_tools()).tools if t.name in config.allowed_tools]

            if PROVIDER == "anthropic":
                answer = await _run_anthropic_loop(config, prompt, all_tools, session)
            else:
                answer = await _run_openai_loop(config, prompt, all_tools, session)

    answer = answer or f"The {config.name} couldn't reach a conclusive answer in time."
    trace("expert.answer", expert=config.name, answer=answer)
    return answer


async def _run_anthropic_loop(config: ExpertConfig, prompt: str, tools, session: ClientSession) -> str | None:
    client = _get_anthropic_client()
    claude_tools = [{"name": t.name, "description": t.description, "input_schema": t.inputSchema} for t in tools]
    messages = [{"role": "user", "content": prompt}]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.messages.create(
            model=EXPERT_MODEL,
            max_tokens=1024,
            system=config.system_prompt,
            tools=claude_tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(block.text for block in response.content if block.type == "text")

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result = await session.call_tool(block.name, block.input)
            result_text = "".join(c.text for c in result.content if c.type == "text")
            trace("expert.tool_call", expert=config.name, tool=block.name, args=block.input, result=result_text)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result_text})
        messages.append({"role": "user", "content": tool_results})

    return None


async def _run_openai_loop(config: ExpertConfig, prompt: str, tools, session: ClientSession) -> str | None:
    client = _get_openai_client()
    openai_tools = [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.inputSchema},
        }
        for t in tools
    ]
    messages = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": prompt},
    ]

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.chat.completions.create(
            model=EXPERT_MODEL,
            messages=messages,
            tools=openai_tools,
        )
        message = response.choices[0].message

        if not message.tool_calls:
            return message.content

        messages.append(message.model_dump())

        for tool_call in message.tool_calls:
            args = json.loads(tool_call.function.arguments or "{}")
            result = await session.call_tool(tool_call.function.name, args)
            result_text = "".join(c.text for c in result.content if c.type == "text")
            trace("expert.tool_call", expert=config.name, tool=tool_call.function.name, args=args, result=result_text)
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})

    return None
