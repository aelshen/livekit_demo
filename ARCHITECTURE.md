# Architecture — Nestly Home Voice Support Agent

## Overview

A cascading voice agent (STT → LLM → TTS) for a smart-home support line. One
voice-facing **Orchestrator** persona (Claude Sonnet) handles the call and
speaks every reply, but does not have direct access to account/order/device
data. Instead it delegates domain questions to three independent **expert
sub-agents** — Customer, Commerce, Device — each its own bounded Claude
tool-use loop (Claude Haiku) with its own system prompt and its own scoped
subset of tools. The Orchestrator only ever sees a natural-language question
in and a synthesized natural-language answer out; it never sees an expert's
tool schemas or intermediate tool calls. This is the orchestrator-workers
multi-agent pattern, not one LLM with a flat tool list.

All experts (and the Orchestrator's own two lightweight tools) pull from a
single MCP server, which is what makes the "customer expert / commerce
expert / device expert" tool groupings swappable and independently testable
without hard-wiring backend logic into the voice pipeline.

## Diagram

```
                    ┌───────────────────────────────┐
                    │  Web client                     │
                    │  (LiveKit Agents Playground)      │
                    └────────────────┬───────────────────┘
                                     │ WebRTC (LiveKit room)
                    ┌────────────────▼───────────────────┐
                    │  Agent Worker (Python)                │
                    │  livekit-agents AgentSession             │
                    │                                            │
                    │  VAD: Silero                                │
                    │  Turn detection: LiveKit turn detector         │
                    │  STT: Deepgram (streaming)                       │
                    │  TTS: Cartesia (streaming)                         │
                    │                                                      │
                    │  ┌────────────────────────────────────────────────┐  │
                    │  │  Orchestrator (Claude Sonnet)                    │  │
                    │  │  - greets caller, calls identify_customer          │  │
                    │  │  - delegates domain questions to expert sub-agents   │
                    │  │  - speaks each expert's synthesized answer             │  │
                    │  │  - summarizes + calls log_handoff_summary on transfer    │
                    │  └───┬──────────────┬──────────────┬───────────────┬────────┘  │
                    │      │one-off calls │              │               │             │
                    │      │(mcp_client)  │  ask_*_expert(question) delegation           │
                    │      ▼              ▼              ▼               ▼               │
                    │ identify_customer ┌────────┐  ┌──────────┐   ┌───────────┐          │
                    │ log_handoff_      │Customer│  │ Commerce │   │  Device   │          │
                    │   summary         │ Expert │  │  Expert  │   │  Expert   │          │
                    │                   │(Haiku) │  │ (Haiku)  │   │ (Haiku)   │          │
                    │                   └───┬────┘  └────┬─────┘   └─────┬─────┘          │
                    │                       │            │                │                │
                    │        each expert runs its OWN bounded Claude tool-use loop,          │
                    │        scoped to its OWN filtered subset of MCP tools                    │
                    └───────────────────────┼────────────┼────────────────┼────────────────────┘
                                            │            │                │
                    ┌───────────────────────▼────────────▼────────────────▼────────────────┐
                    │  smart-home-support MCP server (Python, FastMCP)                        │
                    │                                                                            │
                    │  customer.*   commerce.*   device.*   handoff.*                              │
                    │  identify_customer          get_order_history   get_device_status              │
                    │  get_account_profile         get_order_status    search_troubleshooting_kb       │
                    │  get_subscription_status                         web_search_troubleshooting       │
                    │  get_registered_devices                          search_support_tickets             │
                    │                                                   log_handoff_summary                │
                    │                                                                                        │
                    │  backed by mock data: customers/orders/tickets JSON + device_docs/*.md                 │
                    └────────────────────────────────────────────────────────────────────────────────────────┘
```

## Components

**Orchestrator** (`agent/main.py`) — the only voice/persona on the call.
- System prompt (`agent/prompts/orchestrator.md`): support-rep persona,
  greeting + identification flow, delegate-don't-guess policy, and when to
  offer a human transfer.
- Owns two thin tools that make a single direct MCP call each
  (`agent/mcp_client.py`): `identify_customer` and `log_handoff_summary`.
  These are orchestration-level bookkeeping, not domain expertise, so they
  don't need a sub-agent.
- Owns three delegation tools — `ask_customer_expert`, `ask_commerce_expert`,
  `ask_device_expert` — each of which hands a plain-language question to the
  matching expert sub-agent and returns its answer as the tool result. The
  Orchestrator's own LLM never sees the underlying data tools.

**Expert sub-agents** (`agent/experts.py`) — three independent, on-demand
Claude tool-use loops (model: Haiku, since these are narrow bounded lookups
rather than sustained conversation):
- *Customer Expert* — account profile, subscription status, registered
  devices. Tools: `get_account_profile`, `get_subscription_status`,
  `get_registered_devices`.
- *Commerce Expert* — order history/status. Tools: `get_order_history`,
  `get_order_status`.
- *Device Expert* — live telemetry, troubleshooting KB, web-search fallback,
  prior tickets. Tools: `get_device_status`, `search_troubleshooting_kb`,
  `web_search_troubleshooting`, `search_support_tickets`.

Each expert fetches its own tool list from the MCP server at call time and
filters it down to its `allowed_tools`, runs its own bounded loop (ask
Claude → execute any tool calls → feed results back → repeat, up to 4 turns),
and returns only the final synthesized text. The Orchestrator never sees the
expert's intermediate reasoning or raw tool output.

**smart-home-support MCP server** (`mcp_server/`) — one basic MCP server
exposing all of the above tools, backed by mock data
(`mcp_server/data/*.json`, `device_docs/*.md`) loaded into memory at start.
Being a standalone server, it can also be pointed at directly from an MCP
inspector or Claude Desktop, independent of the voice pipeline.

**Transfer flow** — the Orchestrator has the full transcript itself, so it
summarizes the call, calls `log_handoff_summary` to persist the record
(stand-in for "what a human agent's screen would show"), then says a
goodbye/transfer line and leaves the room — simulating a warm transfer
without real telephony.

## Why this shape

- **Real delegation, not a flat tool list.** The Orchestrator's LLM only
  ever sees `ask_customer_expert("is my thermostat on?")` and gets back a
  sentence — it has no idea `get_device_status` or
  `search_troubleshooting_kb` exist. That boundary is what makes this a
  multi-agent system: each expert independently decides which of its own
  tools to call and how to synthesize the result, not the Orchestrator.
- **One voice, not a relay of personas.** LiveKit's Agents SDK also supports
  full agent-to-agent handoff (swapping the active persona/voice mid-call),
  which is the right tool when a specialist needs to talk to the caller
  directly across multiple turns. Here, the caller should only ever hear
  one consistent voice; the experts work behind the scenes, so
  delegate-and-return is the better fit than a persona swap.
- **MCP as the shared tool boundary.** Both the Orchestrator's direct tools
  and every expert's scoped toolset are filtered views onto one MCP server,
  so the mock CRM/order/ticket/KB logic lives in exactly one place and can
  be tested independently of the voice pipeline (MCP inspector, Claude
  Desktop) or of any given expert.
- **Web interface over phone/SIP.** No telephony provider to provision —
  fastest path to a working, demoable call.
