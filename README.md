# Nestly Home Voice Support Agent (demo)

A voice-based customer support agent for a fictional smart-home company
(thermostats, cameras, locks, sensors + cloud storage subscription). See
[ARCHITECTURE.md](./ARCHITECTURE.md) for the design.

## Layout

```
agent/
  main.py       LiveKit voice agent (STT -> LLM -> TTS) — the Orchestrator persona
  experts.py    Customer/Commerce/Device expert sub-agents the Orchestrator
                delegates to (each its own Claude tool-use loop)
  mcp_client.py One-off MCP calls used by the Orchestrator's own tools
mcp_server/     Standalone MCP server exposing customer/commerce/device/handoff
                tools, backed by mock JSON data
```

## Setup

1. `python -m venv .venv && source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. `cp .env.example .env` and fill in:
   - A LiveKit Cloud project's `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`
     (free tier at [cloud.livekit.io](https://cloud.livekit.io))
   - `ANTHROPIC_API_KEY`, `DEEPGRAM_API_KEY`, `CARTESIA_API_KEY`
   - `TAVILY_API_KEY` is optional — without it, device web-search
     troubleshooting just reports no results found.

## Run

Two processes, in separate terminals:

```bash
# 1. MCP tool server
python -m mcp_server.server --http --port 8089

# 2. Voice agent worker (dev mode auto-connects to LiveKit Agents Playground)
python -m agent.main dev
```

Then open the [LiveKit Agents Playground](https://agents-playground.livekit.io),
connect to your project, and join the room the agent is listening on.

## Demo script

Try asking (after providing a phone/account number — see
`mcp_server/data/customers.json` for valid test accounts, e.g.
`+15551234567` / `ACC-10234`):

- "What's the status of my most recent order?"
- "Is my thermostat on?" / "What's the temperature in my living room?"
- "My garage sensor keeps going offline, can you help?"
- "Can you transfer me to a person?"

## Testing the MCP server standalone

Since `mcp_server/` is a normal MCP server, it can be pointed at directly
from an MCP inspector (`npx @modelcontextprotocol/inspector`) or a Claude
Desktop config, independent of the voice pipeline — useful for demoing or
debugging the tools on their own.
