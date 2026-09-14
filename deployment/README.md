# `deployment/` — Prototype to Production

Day 5 of the capstone. For step-by-step commands see **`DEPLOY.md`** in
this same folder — this file is the "why," aimed at understanding the
concepts well enough to discuss them in an interview, not just run them.

## Files

| File | Role |
|---|---|
| `Dockerfile` | Containerizes `adk api_server` running the exact same `pricing_agent` package used locally. |
| `DEPLOY.md` | The practical path: local → CI gates → container → Cloud Run, plus scaling notes. |

## Technical implementation

### The core principle: the agent code never branches on environment

Nothing in `pricing_agent/` checks `if ENVIRONMENT == "prod"`. What differs
between your laptop and Cloud Run is entirely **outside** the code:

- Model/app/log config comes from env vars (`pricing_agent/config.py`) —
  see `pricing_agent/README.md`'s "config as an environment boundary."
- The *process* hosting the agent differs — `python -m client.cli_client`
  locally, `adk web` for the dev UI, `adk api_server` in the container —
  but all three load the identical `root_agent` object from
  `pricing_agent/agent.py`.

```dockerfile
COPY mcp_server/ mcp_server/
COPY pricing_agent/ pricing_agent/
CMD ["sh", "-c", "adk api_server --host 0.0.0.0 --port ${PORT} pricing_agent"]
```

Note what's *not* copied or rewritten: the MCP server launch command in
`agent.py` (`sys.executable -m mcp_server.freight_pricing_server`, `cwd`
set to the repo root) works unchanged inside the container because the
container's filesystem layout mirrors the repo layout — no path rewriting,
no environment-specific branch.

### Why a container at all, when `adk deploy cloud_run` can build from source

`DEPLOY.md` documents both paths deliberately: `adk deploy cloud_run`
builds and deploys straight from source (fastest path, good for iterating),
while the Dockerfile gives you an artifact you can build once, scan, sign,
and promote through environments unchanged — the standard "build once,
deploy everywhere" pattern most orgs require once a prototype becomes a
real service.

### Stateful pieces called out explicitly for swapping

`DEPLOY.md`'s "scaling beyond this capstone" section is not filler — it's
naming the exact two places this project currently uses in-memory,
single-process state that **will not work correctly** the moment you run
more than one server replica:

- `InMemorySessionService` / `InMemoryMemoryService` (owned by
  `client/cli_client.py`) — swap for `DatabaseSessionService` /
  `VertexAiMemoryBankService` so session/memory state is shared across
  replicas and survives a restart.
- The local stdio `McpToolset` (`pricing_agent/agent.py`) — swap
  `StdioConnectionParams` for `SseConnectionParams` or
  `StreamableHTTPConnectionParams` pointing at a separately-deployed MCP
  server, so the pricing tool isn't a subprocess tied to one agent
  instance's lifecycle.

Both swaps are interface-level changes only (see `client/README.md`'s note
on `BaseSessionService`) — this is intentional, and worth being able to
explain as a concrete instance of programming to an interface.

## Concepts to study for an AI Engineer interview

**The "12-factor app" applied to LLM agents.** Config in the environment
(covered in `pricing_agent/README.md`), and here specifically: treat the
agent process as stateless and disposable, keep state in attached backing
services (session DB, memory store), and make the build artifact
identical across environments. Be ready to map each of these back to a
concrete file in this repo, not just recite the principle.

**Stateless vs. stateful services, and why it matters for scaling.** A
stateless service (this agent, once session/memory are externalized) can
be scaled horizontally by just adding replicas behind a load balancer —
any replica can handle any request. A stateful service (this project's
current `InMemorySessionService`) can't: a user's second request might hit
a different replica with no memory of the first. This is the single
biggest architectural question interviewers ask about deploying
conversational AI at scale, and this project gives you a real before/after
to describe.

**Serverless containers (Cloud Run) vs. alternatives.** Know the shape of
the decision space: Cloud Run (pay-per-request, scales to zero, simplest
ops, cold-start latency to consider) vs. GKE/Kubernetes (more control,
always-on cost, needed for complex networking/sidecars) vs. a managed
agent platform (e.g. Vertex AI Agent Engine — `adk deploy agent_engine`
exists precisely for this) which trades control for far less
infrastructure to own. Be able to argue which you'd pick for "a low-traffic
internal tool" vs. "a high-QPS customer-facing agent" vs. "a team that
doesn't want to run any infrastructure at all."

**Cold starts and subprocess overhead — specific to MCP-based agents.**
Every new agent instance in this project spawns the MCP server as a fresh
subprocess (`McpToolset` launches it lazily on first tool use). In a
scale-to-zero serverless environment, that means the *first* request after
a cold start pays for both the LLM's first-call latency **and** MCP
subprocess startup. This is a genuine, non-obvious cost of the
tool-server-as-subprocess pattern that's worth naming if asked about
latency in agentic systems — the mitigation is exactly the
SSE/StreamableHTTP swap mentioned above (a long-lived, separately-scaled
MCP server that many agent instances share, instead of one subprocess per
instance).

**Observability: logs vs. traces vs. metrics.** `DEPLOY.md` calls out
structured logs (already implemented, see `pricing_agent/callbacks.py`)
and distributed tracing (`--trace_to_cloud`, not yet wired up locally) as
two different observability layers. Know the distinction:  logs answer
"what happened," traces answer "where did the time go across a single
request's model calls + tool calls," and metrics (not present here, but
the natural next step — e.g. a counter of blocked-injection events, a
histogram of quote latency) answer "how is the system behaving in
aggregate, over time, well enough to alert on." A mature answer to "how
would you monitor this in prod" names all three, not just logging.

**Security posture of a locally-run dev server.** `adk api_server`'s own
docs (surfaced in `DEPLOY.md`) state its endpoints are unauthenticated by
default — appropriate for local development, not for anything reachable
from an untrusted network. Be ready to name the layer you'd add in front
of it in production (API gateway with auth, IAP, a reverse proxy enforcing
authn/authz) — "the framework's dev server has no auth" is a completely
normal starting point, and knowing you must add a layer rather than assume
the framework handles it is the actual signal an interviewer is checking
for.
