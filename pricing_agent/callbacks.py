"""Guardrail callbacks — the Day 4 "Agentic Quality" centerpiece.

Design principle: every guardrail's DECISION LOGIC is a small, pure function
(no ADK types involved) so it can be unit tested directly in
eval/quality_tests.py without spinning up a live agent or LLM call. The ADK
callback functions at the bottom are thin adapters that call the pure logic
and translate the result into ADK's callback contract.

Two layers of defense are implemented on purpose (defense in depth):
  1. before_model_callback  -> blocks prompt-injection / off-scope turns
     before they ever reach the LLM.
  2. before_tool_callback   -> validates tool arguments before they reach the
     MCP server, even though the MCP server *also* validates (a well-behaved
     tool should never trust its caller either).
Both layers log a structured audit record, which is the minimum viable
"observability" story for a production agent.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("pricing_agent.guardrails")

# ---------------------------------------------------------------------------
# Pure decision logic (unit-testable, no ADK imports)
# ---------------------------------------------------------------------------

INJECTION_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous instructions",
    "disregard your instructions",
    "you are now",
    "reveal your system prompt",
    "reveal your instructions",
    "print your instructions",
    "developer mode",
    "jailbreak",
)

MAX_USER_TEXT_CHARS = 4000


def detect_prompt_injection(user_text: str) -> str | None:
    """Return a rejection reason if the text looks like an injection/override
    attempt, else None. Deterministic substring/heuristic check — cheap,
    explainable, and runs before any model call.
    """
    if not user_text:
        return None
    lowered = user_text.lower()
    for pattern in INJECTION_PATTERNS:
        if pattern in lowered:
            return f"blocked_injection_pattern:{pattern!r}"
    if len(user_text) > MAX_USER_TEXT_CHARS:
        return "input_too_long"
    return None


ALLOWED_TOOL_ARG_BOUNDS = {
    "volume_m3": (0.0, 120.0),
    "weight_kg": (0.0, 30_000.0),
}
ALLOWED_SERVICE_LEVELS = {"standard", "express", "economy"}


def validate_tool_args(tool_name: str, args: dict[str, Any]) -> str | None:
    """Return an error message if `args` for `tool_name` are out of bounds,
    else None. Mirrors (and double-checks) the MCP server's own validation —
    a tool boundary should not blindly trust the model's arguments.
    """
    if tool_name != "get_freight_quote":
        return None

    for field, (low, high) in ALLOWED_TOOL_ARG_BOUNDS.items():
        value = args.get(field)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            return f"{field} must be a number."
        if not (low < value <= high):
            return f"{field}={value} is out of the allowed range ({low}, {high}]."

    service_level = args.get("service_level")
    if service_level is not None and str(service_level).lower() not in ALLOWED_SERVICE_LEVELS:
        allowed = ", ".join(sorted(ALLOWED_SERVICE_LEVELS))
        return f"service_level must be one of: {allowed}."

    origin = args.get("origin")
    destination = args.get("destination")
    if origin and destination and str(origin).strip().lower() == str(destination).strip().lower():
        return "origin and destination must be different."

    return None


# ---------------------------------------------------------------------------
# ADK callback adapters
# ---------------------------------------------------------------------------


def _extract_last_user_text(llm_request) -> str:
    contents = getattr(llm_request, "contents", None) or []
    for content in reversed(contents):
        if getattr(content, "role", None) != "user":
            continue
        parts = getattr(content, "parts", None) or []
        return "".join(getattr(p, "text", "") or "" for p in parts)
    return ""


def block_unsafe_input(ctx, llm_request):
    """before_model_callback: short-circuits the LLM call for injection
    attempts or scope-abuse, returning a canned refusal instead.
    """
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    user_text = _extract_last_user_text(llm_request)
    reason = detect_prompt_injection(user_text)
    if reason is None:
        return None

    logger.warning(
        "guardrail_blocked stage=before_model user_id=%s reason=%s",
        getattr(ctx, "user_id", "?"),
        reason,
    )
    refusal = (
        "I can only help with freight price quotes for this network "
        "(origin, destination, cargo volume, service level). "
        "I can't follow instructions that try to change how I operate."
    )
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=refusal)]))


def validate_pricing_tool_call(tool, args, ctx):
    """before_tool_callback: rejects out-of-bounds arguments before they
    reach the MCP tool, returning the same {"error": ...} shape the MCP
    server itself would return so the agent's error-handling path is uniform.
    """
    error = validate_tool_args(getattr(tool, "name", ""), args)
    if error is None:
        return None

    logger.warning(
        "guardrail_blocked stage=before_tool tool=%s user_id=%s reason=%s args=%s",
        getattr(tool, "name", "?"),
        getattr(ctx, "user_id", "?"),
        error,
        args,
    )
    return {"error": error}


def log_tool_result(tool, args, ctx, tool_response):
    """after_tool_callback: structured audit log of every completed tool
    call — the minimum viable observability trail for a pricing agent whose
    output has real business/financial consequences.
    """
    logger.info(
        "tool_call tool=%s user_id=%s args=%s result=%s",
        getattr(tool, "name", "?"),
        getattr(ctx, "user_id", "?"),
        args,
        tool_response,
    )
    return None
