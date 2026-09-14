"""Root agent definition — wires together all 5 course days:

  Day 1 (Agent intro):      LlmAgent with a scoped instruction (prompts.py).
  Day 2 (Tools & MCP):      pricing tools are consumed via MCPToolset, i.e.
                             through the Model Context Protocol rather than
                             as in-process Python functions.
  Day 3 (Context eng.):     the built-in `load_memory` tool lets the agent
                             recall facts (past routes/quotes) that were
                             written into long-term memory in earlier
                             sessions (see client/cli_client.py).
  Day 4 (Agentic quality):  before_model / before_tool / after_tool
                             guardrail callbacks (callbacks.py) + the eval
                             suite in eval/.
  Day 5 (Proto -> prod):    model/config are environment-driven (see
                             pricing_agent/config.py), so the same code runs
                             locally, in `adk web`, or in the Cloud Run
                             container in deployment/.
"""

from __future__ import annotations

import sys
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import load_memory
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StdioConnectionParams
from mcp import StdioServerParameters

from pricing_agent import callbacks
from pricing_agent.config import settings
from pricing_agent.prompts import ROOT_AGENT_INSTRUCTION

PROJECT_ROOT = Path(__file__).resolve().parent.parent

freight_pricing_toolset = McpToolset(
    connection_params=StdioConnectionParams(
        server_params=StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.freight_pricing_server"],
            cwd=str(PROJECT_ROOT),
        ),
        timeout=30.0,
    ),
)

root_agent = LlmAgent(
    name="freight_pricing_agent",
    model=settings.model_name,
    description=(
        "Quotes freight shipping prices between supported Latin America "
        "cities given origin, destination, and cargo volume."
    ),
    instruction=ROOT_AGENT_INSTRUCTION,
    tools=[freight_pricing_toolset, load_memory],
    before_model_callback=callbacks.block_unsafe_input,
    before_tool_callback=callbacks.validate_pricing_tool_call,
    after_tool_callback=callbacks.log_tool_result,
)
