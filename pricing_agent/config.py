"""Environment-driven configuration (Day 5: prototype -> production).

Nothing environment-specific is hardcoded in agent.py/client code: the model
name, logging level, and app/session naming all come from env vars so the
exact same code runs locally, under `adk web`, in eval, and in the Cloud Run
container in deployment/Dockerfile — only the .env / environment differs.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    model_name: str = os.getenv("PRICING_AGENT_MODEL", "gemini-3.5-flash-lite")
    app_name: str = os.getenv("PRICING_AGENT_APP_NAME", "freight_pricing_app")
    log_level: str = os.getenv("PRICING_AGENT_LOG_LEVEL", "INFO")


settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
