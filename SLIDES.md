# Slide content draft — Nestly Home voice support agent

Draft talking points for 4 slides, per the "Slide Requirements & Flow" brief
(introduction → context → architecture → demo, with freedom to jump back to
slides at any point). Written to be fed into a slide-generation tool, not as
final wording — trim to fit whatever the tool renders per slide.

---

## Slide 1 — Introduction

**Title:** Welcome / Introductions

**Talking points:**
- Introduce yourself: name, role, and one line on your background relevant
  to this build (voice AI / agent systems / whatever's true for you).
- State the purpose of the meeting plainly: "I'm going to walk through the
  voice support agent I designed and built for [Nestly Home / the smart-home
  support scenario], then show it running live."
- Hand off to the customer panel: "Before I dive in, I'd love for you all to
  quickly introduce yourselves — your name and role — so I know who I'm
  talking to."

**Suggested visual:** Plain title slide, your name/title, maybe the product
name ("Nestly Home Voice Support Agent"). Nothing busy — this slide is about
you talking, not the content on screen.

---

## Slide 2 — Context

**Title:** The situation

**Talking points:**
- Nestly Home is a smart-home company selling connected devices —
  thermostats, cameras, door locks, sensors — plus a subscription tier for
  cloud video storage and monitoring.
- Customers call in with account-specific questions: order status, whether
  a device is online, why a sensor keeps dropping off, general
  troubleshooting — and today that all goes through a live human agent.
- The ask: a voice-based agent that can look a caller up by phone or account
  number, answer real questions grounded in their actual account data
  (not generic FAQ answers), and hand off to a human cleanly when it can't
  help — with the human getting a summary instead of starting from zero.
- Frame the stakes simply: faster resolution for callers, less repetitive
  load on the human support team, and a smooth handoff so nothing is lost
  when it does need a person.

**Suggested visual:** 3-4 short bullets, maybe with small icons for
"devices," "orders," "subscription" to visually anchor what Nestly Home
sells. Keep this slide light on text — it's context-setting, not a spec doc.

---

## Slide 3 — Architecture

**Title:** How it's built

**Talking points:**
- One continuous voice — a single "Orchestrator" persona (Claude) handles
  the whole call: greets the caller, identifies their account, and speaks
  every reply. The caller never perceives a handoff between "agents."
- Real delegation, not a flat tool list — the Orchestrator doesn't have
  direct access to account/order/device data. It delegates a plain-language
  question to one of three independent expert sub-agents (Customer,
  Commerce, Device), each running its own reasoning loop with its own
  scoped tools. That's what makes this a genuine multi-agent
  orchestrator-workers system, not one LLM with a big tool belt.
- Shared tool backend — all data access (mock CRM, orders, tickets,
  troubleshooting docs) is exposed through one MCP server, so the tool
  surface is swappable and independently testable.
- Built-in observability — every delegation and every tool call is traced
  with latency, live, so you can see exactly what the agent is doing and
  where time is going as a call happens.

**Suggested visual:** The Mermaid diagram from `README.md` / `ARCHITECTURE.md`
— Browser → LiveKit Room → Orchestrator (STT → LLM → TTS) → three Expert
sub-agents → MCP server → mock data. Export it as an image if the slide tool
can't render Mermaid directly (e.g. `npx @mermaid-js/mermaid-cli`).

---

## Slide 4 — What you're about to see

**Title:** Demo roadmap

**Talking points:**
- Quick bullet list of what the live demo will cover, so the panel knows
  what to watch for:
  1. Greeting a caller by name (identified by phone/account number)
  2. Answering account/order/device questions grounded in real data
  3. Troubleshooting a device issue
  4. Transferring to a human with a generated call summary
  5. (if time) the live trace view showing the orchestration and latency
     in real time
- One-line transition: "Let's make this real — I'll switch to the demo now,
  and I'll come back to this diagram if useful while we talk through what
  you're seeing."

**Suggested visual:** A simple numbered list matching the demo beats above.
This slide is the bridge — keep it short, you'll be talking over it for
seconds, not minutes.
