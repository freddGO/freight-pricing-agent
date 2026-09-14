"""Freight Pricing MCP server.

Exposes the pricing business logic (mcp_server/pricing_data.py) as standard
MCP tools over stdio, using the official `mcp` Python SDK (FastMCP).

This is the "Day 2" piece of the capstone: instead of writing pricing
functions directly as ADK FunctionTools, we expose them behind the Model
Context Protocol so ANY MCP-compatible client/agent framework (not just
Google ADK) can call the same freight-pricing capability. The ADK agent in
pricing_agent/agent.py then consumes this server through ADK's MCPToolset,
demonstrating interoperability rather than framework lock-in.

Run standalone for manual testing (must be run as a module so the
`mcp_server.pricing_data` absolute import resolves; ADK's McpToolset in
pricing_agent/agent.py launches it the same way, with cwd set to the repo
root):
    python -m mcp_server.freight_pricing_server
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from mcp_server.pricing_data import PricingError, calculate_quote, list_cities

mcp = FastMCP("freight-pricing")


@mcp.tool()
def get_supported_cities() -> list[dict]:
    """Return the list of cities this freight network currently services.

    Each entry has: code, name, lat, lon, hub_tier (1=major hub with the
    lowest handling surcharge, 3=smaller terminal with a higher surcharge).
    Call this before quoting if you are unsure a city is supported.
    """
    return list_cities()


@mcp.tool()
def get_freight_quote(
    origin: str,
    destination: str,
    volume_m3: float,
    weight_kg: float | None = None,
    service_level: str = "standard",
) -> dict:
    """Calculate a freight shipping price quote between two supported cities.

    Args:
        origin: Origin city name or code (e.g. "cdmx", "Bogotá").
        destination: Destination city name or code.
        volume_m3: Cargo volume in cubic meters. Must be > 0.
        weight_kg: Optional actual cargo weight in kg. If omitted, a
            volumetric-equivalent weight is assumed (250 kg/m3).
        service_level: One of "standard", "express", "economy".

    Returns:
        A dict with total_price, currency, distance_km, estimated_transit_days,
        and a cost breakdown. On invalid input, returns {"error": "..."}
        instead of raising, so the calling agent can relay a clean message.
    """
    try:
        quote = calculate_quote(
            origin=origin,
            destination=destination,
            volume_m3=volume_m3,
            weight_kg=weight_kg,
            service_level=service_level,
        )
    except PricingError as exc:
        return {"error": str(exc)}
    return quote.to_dict()


if __name__ == "__main__":
    mcp.run(transport="stdio")
