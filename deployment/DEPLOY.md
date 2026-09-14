# Day 5 — Prototype to Production

The agent code (`pricing_agent/`) never branches on environment. What
changes between "runs on my laptop" and "runs in the cloud" is only:
config (env vars, see `.env.example` / `pricing_agent/config.py`) and the
process that hosts the agent.

## 1. Local prototype

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in GOOGLE_API_KEY

python -m client.cli_client --demo   # scripted sessions+memory walkthrough
python -m client.cli_client          # interactive REPL
adk web pricing_agent                # ADK's own dev UI, same agent
```

## 2. Automated quality gates (run before every deploy)

```bash
pytest                               # deterministic: pricing math + guardrails
python -m eval.run_evals             # LLM-in-the-loop: tool-use + response quality
```

Wire both into CI (e.g. GitHub Actions) as a required check before merging
to main — `pytest` needs no API key, `eval.run_evals` does.

## 3. Containerize

```bash
docker build -f deployment/Dockerfile -t freight-pricing-agent .
docker run -p 8080:8080 --env-file .env freight-pricing-agent
```

This runs `adk api_server`, which hosts the same `root_agent` (MCP tools,
guardrail callbacks, and all) behind an HTTP API — no separate "prod agent".
Note the server is unauthenticated by default; put it behind your own auth
layer (API gateway, IAP, etc.) before exposing it beyond a trusted network.

## 4. Deploy to Cloud Run

`adk deploy` builds and deploys straight from source, without needing the
Dockerfile above:

```bash
adk deploy cloud_run \
  --project=YOUR_GCP_PROJECT \
  --region=us-central1 \
  pricing_agent
```

Set `GOOGLE_API_KEY` as a Cloud Run environment variable/secret (or switch
`GOOGLE_GENAI_USE_VERTEXAI=TRUE` and use the Cloud Run service account's
Vertex AI permissions instead of an API key).

## 5. Observability in production

- `pricing_agent/callbacks.py` already logs every guardrail rejection and
  every completed tool call as structured log lines
  (`guardrail_blocked ...`, `tool_call ...`) — these are what you'd ship to
  Cloud Logging / a log-based metric to alert on (e.g. spike in blocked
  injection attempts, or tool error rate).
- Add `--trace_to_cloud` (Cloud Run) to get distributed traces of each
  agent turn, including the MCP tool call latency.

## Scaling beyond this capstone

- Swap `InMemorySessionService` / `InMemoryMemoryService`
  (`client/cli_client.py`) for `DatabaseSessionService` /
  `VertexAiMemoryBankService` so sessions and memory survive a restart and
  are shared across server instances.
- Swap the local stdio `McpToolset` (`pricing_agent/agent.py`) for an
  `SseConnectionParams`/`StreamableHTTPConnectionParams` connection to a
  freight-pricing MCP server running as its own deployed service — the
  agent code doesn't change, only the connection params.
