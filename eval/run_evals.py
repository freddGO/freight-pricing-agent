"""Run the ADK eval set against the live agent (LLM-in-the-loop quality gate).

This exercises the real model + real MCP tool calls + real guardrails, unlike
eval/quality_tests.py which is pure/deterministic. Needs GOOGLE_API_KEY set.

Usage:
    python -m eval.run_evals

Why this doesn't just call `AgentEvaluator.evaluate(...)` directly: ADK's
`AgentEvaluator` runs eval cases within a set concurrently, at a default
`parallelism` of 4 (see `google.adk.evaluation.base_eval_service.InferenceConfig`).
Each eval case's agent independently spawns its own MCP subprocess
(`pricing_agent/agent.py`'s `McpToolset` launches `mcp_server/freight_pricing_server.py`
over stdio), so 4-way parallelism means up to 4 concurrent subprocess spawns
plus 4 concurrent Gemini calls competing for CPU on whatever machine runs
this. That's comfortably fine on a dev laptop, but GitHub Actions' shared
2-vCPU runners are noisier and slower under that same contention — this
project hit a real `TimeoutError` connecting to the MCP server in CI purely
from that contention, not from any actual bug in the agent or the eval
cases. Running eval cases one at a time here removes the contention at its
source (never more than one MCP subprocess alive at once) instead of just
papering over it with a bigger timeout — see `pricing_agent/agent.py`'s
`StdioConnectionParams(timeout=...)` history for the "just raise the
timeout" version of that same fix, applied previously to the exact same
symptom on a plain local run with no CI involved.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from google.adk.evaluation.agent_evaluator import AgentEvaluator
from google.adk.evaluation.eval_set import EvalSet

EVAL_DIR = Path(__file__).resolve().parent
EVAL_SET_PATH = EVAL_DIR / "pricing_agent.evalset.json"


async def main() -> None:
    full_eval_set = EvalSet.model_validate_json(EVAL_SET_PATH.read_text())
    eval_config = AgentEvaluator.find_config_for_test_file(str(EVAL_SET_PATH))

    failures: list[str] = []
    for eval_case in full_eval_set.eval_cases:
        single_case_set = full_eval_set.model_copy(
            update={"eval_cases": [eval_case]}
        )
        try:
            await AgentEvaluator.evaluate_eval_set(
                agent_module="pricing_agent",
                eval_set=single_case_set,
                eval_config=eval_config,
                num_runs=1,
            )
        except AssertionError as exc:
            failures.append(f"[{eval_case.eval_id}] {exc}")

    if failures:
        raise AssertionError(
            f"{len(failures)}/{len(full_eval_set.eval_cases)} eval case(s) failed:\n\n"
            + "\n\n".join(failures)
        )
    print(f"All {len(full_eval_set.eval_cases)} eval cases passed.")


if __name__ == "__main__":
    asyncio.run(main())
