"""Run the ADK eval set against the live agent (LLM-in-the-loop quality gate).

This exercises the real model + real MCP tool calls + real guardrails, unlike
eval/quality_tests.py which is pure/deterministic. Needs GOOGLE_API_KEY set.

Usage:
    python -m eval.run_evals
    # equivalent to the ADK CLI form:
    adk eval pricing_agent eval/pricing_agent.evalset.json --config_file_path eval/test_config.json
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from google.adk.evaluation.agent_evaluator import AgentEvaluator

EVAL_DIR = Path(__file__).resolve().parent


async def main() -> None:
    await AgentEvaluator.evaluate(
        agent_module="pricing_agent",
        eval_dataset_file_path_or_dir=str(EVAL_DIR / "pricing_agent.evalset.json"),
        num_runs=1,
    )


if __name__ == "__main__":
    asyncio.run(main())
