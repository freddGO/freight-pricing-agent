# `mcp_server/` — Freight Pricing MCP Server

Day 2 of the capstone: **Tools & Model Context Protocol (MCP) interoperability**.

## Files

| File | Role |
|---|---|
| `pricing_data.py` | Pure Python: synthetic city catalog + deterministic pricing math. Zero framework dependencies. |
| `freight_pricing_server.py` | Wraps `pricing_data.py` as MCP tools using `FastMCP`, served over stdio. |

## Technical implementation

### 1. Business logic is framework-agnostic (`pricing_data.py`)

`calculate_quote()` takes plain Python types in, returns a `Quote` dataclass
out, and raises a custom `PricingError` for bad input. It imports nothing
from `mcp` or `google.adk`. This is deliberate: the pricing algorithm is
the asset; the protocol that exposes it is a swappable detail. Because it's
pure, `eval/quality_tests.py` unit-tests it directly with plain `pytest`,
no agent, no LLM, no subprocess.

The pricing formula composes five independently-tunable line items:

```
distance_cost   = haversine_km(origin, dest) * RATE_PER_KM
volume_cost     = volume_m3 * RATE_PER_M3
overweight_cost = max(0, actual_weight - volumetric_weight) * RATE_PER_KG_OVERWEIGHT
hub_surcharge   = (origin.hub_tier - 1 + dest.hub_tier - 1) * HUB_SURCHARGE_PER_TIER
fuel_surcharge  = (sum of the above) * FUEL_SURCHARGE_PCT

total = max(subtotal * service_level_multiplier, MIN_CHARGE_USD)
```

This mirrors how real freight/ride-pricing systems are structured: a base
distance-and-volume rate, a volumetric-weight rule (carriers bill whichever
of actual-weight or volume-equivalent-weight is higher — this is a real
industry practice, not invented for this project), a surcharge for
lower-tier hubs, and a fuel surcharge applied to the subtotal rather than
the base rate (also realistic — carriers surcharge the loaded cost, not
just distance).

### 2. Exposing it over MCP (`freight_pricing_server.py`)

```python
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("freight-pricing")

@mcp.tool()
def get_freight_quote(origin: str, destination: str, volume_m3: float, ...) -> dict:
    """<this docstring is what the LLM reads to decide how/when to call the tool>"""
    ...

mcp.run(transport="stdio")
```

Three things matter here:

- **The docstring is the interface contract with the model.** `FastMCP`
  introspects the function signature (via type hints) and docstring to build
  the JSON Schema the LLM sees. A vague docstring produces a model that
  calls the tool with wrong or missing arguments — this is the MCP
  equivalent of writing a bad API doc, except the "caller" is a
  probabilistic model that can't ask a human for clarification.
- **Errors are returned, not raised.** `get_freight_quote` catches
  `PricingError` and returns `{"error": "..."}` instead of letting an
  exception kill the MCP session. An LLM can read and react to a
  structured error in a tool *result*; it cannot recover from a transport-
  level crash.
- **`transport="stdio"`** means this server is a subprocess communicating
  over stdin/stdout using JSON-RPC 2.0 — no network port, no auth needed
  for local use. `pricing_agent/agent.py` launches it as
  `python -m mcp_server.freight_pricing_server` with `cwd` set to the repo
  root (required so the `mcp_server.pricing_data` absolute import
  resolves — running the script by path instead of by module would put the
  wrong directory on `sys.path`).

### 3. Manual testing

```bash
python -m mcp_server.freight_pricing_server   # runs and waits on stdin — Ctrl+C to exit
```
To actually exercise it, drive it with an MCP client session (see the
project root README's test commands) rather than typing JSON-RPC by hand.

## Concepts to study for an AI Engineer interview

**What is MCP and why does it exist?**
Model Context Protocol is an open, JSON-RPC-based protocol (from Anthropic)
that standardizes how an LLM application discovers and calls external
tools/resources, independent of which model or agent framework is on the
other end. Before MCP, every framework (LangChain, ADK, custom code) had
its own bespoke "tool" abstraction, so a tool written for one framework
couldn't be reused in another. MCP separates **tool provider** (the server,
e.g. this folder) from **tool consumer** (the agent/client), the same way
LSP separated "language smarts" from "editor" in developer tooling. Be
ready to explain this analogy — interviewers ask for it directly.

**MCP vs. "native" function calling / FunctionTool.**
Native function calling (e.g. an in-process Python function decorated as an
ADK `FunctionTool`, or an OpenAI `tools=[...]` schema) is faster (no IPC)
and simpler, but couples the tool to that one process/framework. MCP adds a
process boundary (here: stdio, but MCP also supports SSE/HTTP for remote
servers) in exchange for reusability — any MCP-aware client (Claude
Desktop, a different agent framework, another team's service) can call the
exact same tool with zero code changes. Know the trade-off: **coupling vs.
reuse**, and **latency/complexity vs. interoperability**.

**Tool discovery: `tools/list` and `tools/call`.**
MCP defines a small set of JSON-RPC methods. A client calls `initialize`,
then `tools/list` to get each tool's name, description, and JSON Schema for
its arguments (this is exactly what `session.list_tools()` does in the
verification script referenced in the root README), then `tools/call` with
a tool name + arguments to invoke it. Understanding this request/response
shape is the kind of detail interviewers use to check you've actually built
something with MCP, not just read the tagline.

**Why validate at the tool boundary even though the caller is "trusted."**
`calculate_quote()` raises on bad input (unknown city, negative volume,
mismatched origin/destination) even though the only caller is an LLM that
was explicitly instructed on the valid ranges. This is the **defense in
depth** principle applied to agents: never trust an upstream caller's
correctness, even a cooperative one, because prompts are not type systems
— a model can and will occasionally pass malformed or out-of-range
arguments. Pair this with `pricing_agent/callbacks.py`'s
`before_tool_callback`, which validates the *same* constraints a second
time before the call ever reaches this server — two independent layers
checking the same invariant is a legitimate production pattern, not
redundant code.

**Stateless vs. stateful tool servers.**
This server is stateless — every call is a pure function of its arguments.
That's what makes it trivially safe to run as a short-lived subprocess per
agent session. Be ready to discuss what changes if a tool needs state
(e.g. a shopping cart): you'd need session-scoped identifiers passed as
tool arguments, or a stateful backing store the tool queries, since the MCP
server process itself shouldn't be assumed to persist or to be a singleton.

**Synthetic data / test fixtures vs. real integrations.**
The city catalog and rate constants in `pricing_data.py` are
intentionally synthetic and fully deterministic (no network calls, no
external pricing API). This is a common, correct choice when prototyping
an agent: it lets you build and eval the *agent behavior* (does it call
the right tool, handle errors, respect guardrails) completely decoupled
from the reliability/cost/rate-limits of a real backend. Know how to
articulate when you'd swap this for a real integration (once agent
behavior is validated) and what would need to change (the tool's I/O
contract stays identical — only the implementation behind
`get_freight_quote` changes, which is the whole point of the tool
abstraction).
