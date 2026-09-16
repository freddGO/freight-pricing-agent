# Freight Pricing Agent — Kaggle "5-Day Agents" Capstone

[![CI](https://github.com/freddGO/freight-pricing-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/freddGO/freight-pricing-agent/actions/workflows/ci.yml)

A freight-logistics pricing agent built with **Google ADK** + **Gemini**,
used as a wrap-up project for Kaggle's *5-Day Agents* course. Ask it for a
freight quote (origin, destination, cargo volume in m3) and it returns a
priced, itemized quote computed from synthetic freight-economics data.

Each course day maps to a specific, runnable piece of this repo:

| Day | Topic | Where |
|---|---|---|
| 1 | Agent introduction | `pricing_agent/agent.py`, `pricing_agent/prompts.py` — a single scoped `LlmAgent` |
| 2 | Tools & MCP interoperability | `mcp_server/` (the pricing tool exposed over MCP) + `McpToolset` wiring in `agent.py` |
| 3 | Context engineering — sessions & memory | `client/cli_client.py` (`--demo` mode), `load_memory` tool in `agent.py`, `client/persistence.py` for `$0` durable storage |
| 4 | **Agentic quality** | `pricing_agent/callbacks.py` (guardrails) + `eval/` (deterministic tests + LLM eval set) |
| 5 | Prototype → production | `pricing_agent/config.py`, `deployment/` (Dockerfile, Cloud Run) |

## Why an MCP server instead of plain Python tools?

The pricing logic (`mcp_server/pricing_data.py`) is exposed as MCP tools
(`mcp_server/freight_pricing_server.py`) rather than as in-process ADK
`FunctionTool`s. The ADK agent then consumes it through `McpToolset` over
stdio. This is the interoperability point of Day 2: the same pricing server
could be called by any other MCP-compatible client (Claude Desktop, a
different agent framework, etc.) without change — the tool isn't
Google-ADK-specific.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # set GOOGLE_API_KEY (from https://aistudio.google.com/apikey)

# deterministic tests (no API key needed): pricing math + guardrail logic
pytest

# scripted demo proving BOTH session state and cross-session memory work
python -m client.cli_client --demo

# interactive REPL
python -m client.cli_client

# add --persist to either mode for $0 SQLite-backed sessions/memory that
# survive across separate runs, instead of resetting every process (see
# client/README.md's persistence section, incl. a real retrieval-quality
# limitation this exposes)
python -m client.cli_client --persist --demo

# ADK's own dev UI for the same agent
adk web pricing_agent

# LLM-in-the-loop eval set (tool trajectory + response quality)
python -m eval.run_evals
```

## Project layout

Each subfolder has its own `README.md` with a technical walkthrough of
that piece **and** an "AI Engineer interview" study section for the
concepts it demonstrates — worth reading even if you're not touching the
code, since together they cover the full syllabus of this course.

```
mcp_server/    (README) synthetic pricing data + pricing exposed as MCP tools
  pricing_data.py            synthetic LatAm city catalog + pure pricing math
  freight_pricing_server.py  MCP server (FastMCP, stdio) exposing pricing as tools
pricing_agent/ (README) the LlmAgent + prompt + guardrails + config
  agent.py                   root_agent: LlmAgent + McpToolset + guardrail callbacks
  prompts.py                 scoped system instruction
  callbacks.py                guardrails: prompt-injection + tool-arg validation
  config.py                   env-driven settings (model, app name, log level)
client/        (README) sessions & memory
  cli_client.py               owns SessionService/MemoryService, drives the Runner
  persistence.py              $0 SQLite-backed durable alternative (--persist)
eval/          (README) agentic quality: unit tests + LLM eval set
  quality_tests.py            pytest: pricing math + guardrail unit tests (no LLM)
  pricing_agent.evalset.json  ADK eval set: tool-trajectory + response-quality cases
  test_config.json            eval thresholds/match rules
  run_evals.py                runs the eval set against the live agent
deployment/    (README + DEPLOY.md) prototype to production
  Dockerfile                  containerizes `adk api_server`
  DEPLOY.md                   local -> CI gates -> container -> Cloud Run
```

## Pricing model (synthetic, not real freight rates)

`mcp_server/pricing_data.py` prices a shipment from: great-circle distance
between two supported Latin America cities, cargo volume, a
volumetric-weight overage charge, a per-hub-tier handling surcharge, a fuel
surcharge, and a service-level multiplier (`standard` / `express` /
`economy`). It is deterministic and fully unit-tested in
`eval/quality_tests.py` — the numbers are synthetic but internally
consistent (e.g. express is always pricier and faster than economy for the
same route).

## Agentic quality (Day 4) — the emphasis of this capstone

Two independent layers, both logged for observability:

1. **`before_model_callback`** (`callbacks.py::block_unsafe_input`) — a
   deterministic prompt-injection / scope-abuse check that runs *before*
   the LLM is called at all, short-circuiting with a canned refusal.
2. **`before_tool_callback`** (`callbacks.py::validate_pricing_tool_call`) —
   validates tool arguments (volume/weight bounds, known service levels,
   origin ≠ destination) before they reach the MCP server, on the
   principle that a tool boundary shouldn't trust its caller either — even
   though the MCP server (`pricing_data.py`) also validates independently.

Quality is verified two ways:

- `eval/quality_tests.py` (pytest, deterministic, no API key) — regression
  tests for the pricing math and for both guardrails' pure decision logic.
- `eval/pricing_agent.evalset.json` (ADK eval, needs API key) — exercises
  the real agent end-to-end: happy-path quoting, a multi-turn follow-up
  that depends on session state, an unsupported-city case (must not
  fabricate a price), and a prompt-injection case (must refuse without
  calling any tool). `eval/test_config.json` grades on
  `tool_trajectory_avg_score` with `ignore_args: true` and
  `match_type: in_order` — i.e. it pins *which tools get called, in what
  order*, not exact argument values or exact response wording. That's a
  deliberate choice verified against a real run: a small/fast model
  (`gemini-3.5-flash-lite`, chosen for its friendlier free-tier quota)
  occasionally phrases city names differently ("Sao Paulo" vs "sao_paulo")
  or issues one redundant duplicate tool call — neither is a correctness
  bug, so grading on exact-string response match or exact call count would
  produce noisy, non-actionable failures. Grading on tool trajectory instead
  catches the failures that actually matter for a pricing agent: wrong tool,
  wrong order, or no tool call at all (i.e. a fabricated price).

## Author

**Alfredo Guillen - Solaria Nexus**

## License

MIT — see [LICENSE](LICENSE). Contributions welcome, see
[CONTRIBUTING.md](CONTRIBUTING.md).
