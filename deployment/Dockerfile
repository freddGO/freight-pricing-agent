FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_server/ mcp_server/
COPY pricing_agent/ pricing_agent/

ENV PRICING_AGENT_APP_NAME=freight_pricing_app
ENV PORT=8080
EXPOSE 8080

# `adk api_server` hosts the agent (this package's root_agent) behind an
# HTTP API on $PORT, backed by the same MCPToolset + guardrails used
# locally — no code branching between dev and prod.
CMD ["sh", "-c", "adk api_server --host 0.0.0.0 --port ${PORT} pricing_agent"]
