"""Synthetic data and pure pricing logic for the Freight Pricing MCP server.

Everything here is deterministic and has no dependency on an LLM, so it can be
unit tested directly (see eval/quality_tests.py). This is the "ground truth"
business logic that tools expose to the agent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Synthetic city catalog (Latin America). Coordinates are approximate city
# centers, used only to derive a synthetic great-circle distance for pricing.
# ---------------------------------------------------------------------------

CITIES: dict[str, dict] = {
    "cdmx": {"name": "Ciudad de México, MX", "lat": 19.4326, "lon": -99.1332, "hub_tier": 1},
    "guadalajara": {"name": "Guadalajara, MX", "lat": 20.6597, "lon": -103.3496, "hub_tier": 2},
    "monterrey": {"name": "Monterrey, MX", "lat": 25.6866, "lon": -100.3161, "hub_tier": 2},
    "tijuana": {"name": "Tijuana, MX", "lat": 32.5149, "lon": -117.0382, "hub_tier": 3},
    "merida": {"name": "Mérida, MX", "lat": 20.9674, "lon": -89.5926, "hub_tier": 3},
    "bogota": {"name": "Bogotá, CO", "lat": 4.7110, "lon": -74.0721, "hub_tier": 1},
    "medellin": {"name": "Medellín, CO", "lat": 6.2442, "lon": -75.5812, "hub_tier": 2},
    "lima": {"name": "Lima, PE", "lat": -12.0464, "lon": -77.0428, "hub_tier": 1},
    "santiago": {"name": "Santiago, CL", "lat": -33.4489, "lon": -70.6693, "hub_tier": 1},
    "buenos_aires": {"name": "Buenos Aires, AR", "lat": -34.6037, "lon": -58.3816, "hub_tier": 1},
    "sao_paulo": {"name": "São Paulo, BR", "lat": -23.5505, "lon": -46.6333, "hub_tier": 1},
    "rio_de_janeiro": {"name": "Rio de Janeiro, BR", "lat": -22.9068, "lon": -43.1729, "hub_tier": 2},
    "quito": {"name": "Quito, EC", "lat": -0.1807, "lon": -78.4678, "hub_tier": 3},
    "montevideo": {"name": "Montevideo, UY", "lat": -34.9011, "lon": -56.1645, "hub_tier": 3},
    "panama_city": {"name": "Panamá, PA", "lat": 8.9824, "lon": -79.5199, "hub_tier": 2},
}

# Hub tiers 1-3 model port/terminal density: lower tier = better infrastructure
# = smaller handling surcharge.
HUB_SURCHARGE_PER_TIER = 15.00  # USD, applied per (tier - 1) on each end

# Base freight economics (synthetic but internally consistent).
RATE_PER_KM = 0.85          # USD per km
RATE_PER_M3 = 22.50         # USD per cubic meter of cargo volume
RATE_PER_KG_OVERWEIGHT = 0.40  # USD per kg beyond the volumetric-equivalent weight
FUEL_SURCHARGE_PCT = 0.12   # 12% fuel surcharge on (distance + volume) subtotal
MIN_CHARGE_USD = 60.00      # minimum billable shipment
VOLUMETRIC_DENSITY_KG_PER_M3 = 250  # used to decide if a shipment is "overweight"

SERVICE_LEVELS = {
    "standard": {"multiplier": 1.00, "transit_days_per_1000km": 1.4},
    "express": {"multiplier": 1.45, "transit_days_per_1000km": 0.7},
    "economy": {"multiplier": 0.82, "transit_days_per_1000km": 2.2},
}

MAX_VOLUME_M3 = 120.0   # sanity ceiling for a single freight booking
MAX_WEIGHT_KG = 30_000.0


class PricingError(ValueError):
    """Raised for invalid/out-of-range pricing inputs (used as a guardrail signal)."""


def _normalize_city(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("é", "e").replace("á", "a")


def list_cities() -> list[dict]:
    return [{"code": code, **info} for code, info in CITIES.items()]


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Quote:
    origin: str
    destination: str
    distance_km: float
    volume_m3: float
    weight_kg: float
    service_level: str
    distance_cost: float
    volume_cost: float
    overweight_cost: float
    hub_surcharge: float
    fuel_surcharge: float
    subtotal: float
    total_price: float
    currency: str
    estimated_transit_days: float
    breakdown: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "destination": self.destination,
            "distance_km": round(self.distance_km, 1),
            "volume_m3": self.volume_m3,
            "weight_kg": self.weight_kg,
            "service_level": self.service_level,
            "currency": self.currency,
            "total_price": round(self.total_price, 2),
            "estimated_transit_days": round(self.estimated_transit_days, 1),
            "breakdown": self.breakdown,
        }


def resolve_city(value: str) -> tuple[str, dict]:
    code = _normalize_city(value)
    if code not in CITIES:
        known = ", ".join(sorted(CITIES))
        raise PricingError(f"Unknown city '{value}'. Supported cities: {known}")
    return code, CITIES[code]


def calculate_quote(
    origin: str,
    destination: str,
    volume_m3: float,
    weight_kg: float | None = None,
    service_level: str = "standard",
) -> Quote:
    """Pure, deterministic freight pricing calculation.

    Raises PricingError for invalid input so callers (tool layer, guardrail
    callbacks) can turn it into a clean rejection instead of a stack trace.
    """
    if volume_m3 is None or volume_m3 <= 0:
        raise PricingError("volume_m3 must be a positive number.")
    if volume_m3 > MAX_VOLUME_M3:
        raise PricingError(f"volume_m3 exceeds the {MAX_VOLUME_M3} m3 single-booking limit.")

    service_level = (service_level or "standard").strip().lower()
    if service_level not in SERVICE_LEVELS:
        allowed = ", ".join(SERVICE_LEVELS)
        raise PricingError(f"Unknown service_level '{service_level}'. Choose one of: {allowed}")

    origin_code, origin_info = resolve_city(origin)
    dest_code, dest_info = resolve_city(destination)
    if origin_code == dest_code:
        raise PricingError("origin and destination must be different cities.")

    volumetric_weight = volume_m3 * VOLUMETRIC_DENSITY_KG_PER_M3
    weight_kg = volumetric_weight if weight_kg is None else float(weight_kg)
    if weight_kg < 0:
        raise PricingError("weight_kg cannot be negative.")
    if weight_kg > MAX_WEIGHT_KG:
        raise PricingError(f"weight_kg exceeds the {MAX_WEIGHT_KG} kg single-booking limit.")

    distance_km = _haversine_km(
        origin_info["lat"], origin_info["lon"], dest_info["lat"], dest_info["lon"]
    )

    distance_cost = distance_km * RATE_PER_KM
    volume_cost = volume_m3 * RATE_PER_M3
    overweight_kg = max(0.0, weight_kg - volumetric_weight)
    overweight_cost = overweight_kg * RATE_PER_KG_OVERWEIGHT
    hub_surcharge = (
        (origin_info["hub_tier"] - 1) + (dest_info["hub_tier"] - 1)
    ) * HUB_SURCHARGE_PER_TIER

    pre_fuel_subtotal = distance_cost + volume_cost + overweight_cost + hub_surcharge
    fuel_surcharge = pre_fuel_subtotal * FUEL_SURCHARGE_PCT
    subtotal = pre_fuel_subtotal + fuel_surcharge

    level = SERVICE_LEVELS[service_level]
    total_price = max(subtotal * level["multiplier"], MIN_CHARGE_USD)
    transit_days = (distance_km / 1000.0) * level["transit_days_per_1000km"]

    breakdown = {
        "distance_cost_usd": round(distance_cost, 2),
        "volume_cost_usd": round(volume_cost, 2),
        "overweight_cost_usd": round(overweight_cost, 2),
        "hub_surcharge_usd": round(hub_surcharge, 2),
        "fuel_surcharge_usd": round(fuel_surcharge, 2),
        "service_level_multiplier": level["multiplier"],
        "minimum_charge_applied": subtotal * level["multiplier"] < MIN_CHARGE_USD,
    }

    return Quote(
        origin=origin_info["name"],
        destination=dest_info["name"],
        distance_km=distance_km,
        volume_m3=volume_m3,
        weight_kg=weight_kg,
        service_level=service_level,
        distance_cost=distance_cost,
        volume_cost=volume_cost,
        overweight_cost=overweight_cost,
        hub_surcharge=hub_surcharge,
        fuel_surcharge=fuel_surcharge,
        subtotal=subtotal,
        total_price=total_price,
        currency="USD",
        estimated_transit_days=transit_days,
        breakdown=breakdown,
    )
