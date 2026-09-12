"""Central configuration loaded from .env. Never hardcode API keys elsewhere."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
REPO_DIR = BASE_DIR.parent
DATA_DIR = Path(os.getenv("DATA_DIR", REPO_DIR / "data")).resolve()
DATASETS_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# --- Travelpayouts / Aviasales Data API ---
TRAVELPAYOUTS_TOKEN = os.getenv("TRAVELPAYOUTS_TOKEN", "")
TRAVELPAYOUTS_MARKER = os.getenv("TRAVELPAYOUTS_MARKER", "")
TRAVELPAYOUTS_BASE_URL = os.getenv(
    "TRAVELPAYOUTS_BASE_URL", "https://api.travelpayouts.com"
)

# --- Aircraft enrichment ---
AERODATABOX_RAPIDAPI_KEY = os.getenv("AERODATABOX_RAPIDAPI_KEY", "")
OPENSKY_CLIENT_ID = os.getenv("OPENSKY_CLIENT_ID", "")
OPENSKY_CLIENT_SECRET = os.getenv("OPENSKY_CLIENT_SECRET", "")

# --- Storage ---
DATABASE_URL = os.getenv("DATABASE_URL", "") or f"sqlite+aiosqlite:///{DATA_DIR / 'fareloom.db'}"

# --- Defaults ---
DEFAULT_CURRENCY = os.getenv("DEFAULT_CURRENCY", "inr").lower()
DEFAULT_MARKET = os.getenv("DEFAULT_MARKET", "IN").upper()
FARE_CACHE_TTL_HOURS = int(os.getenv("FARE_CACHE_TTL_HOURS", "6"))

# Travelpayouts publishes no documented rate limit, so this is a self-imposed
# ceiling to stay a good citizen — the per-date scan depth can otherwise fan
# out to ~60 calls for a two-month search.
PROVIDER_MAX_CONCURRENCY = int(os.getenv("PROVIDER_MAX_CONCURRENCY", "8"))
PROVIDER_TIMEOUT_SECONDS = float(os.getenv("PROVIDER_TIMEOUT_SECONDS", "20"))

# Daily sweep of popular routes, which is what makes the price history grow
# whether or not anyone searches. Off without a token, since there is nothing
# real to record.
ENABLE_BACKGROUND_SCANS = os.getenv(
    "ENABLE_BACKGROUND_SCANS", "true"
).strip().lower() in ("1", "true", "yes")
SWEEP_HOUR = int(os.getenv("SWEEP_HOUR", "3"))

# Synthetic fares, so the app runs before a Travelpayouts token exists. Falls
# back automatically rather than erroring, but every response says so.
_DEMO_FLAG = os.getenv("FARELOOM_DEMO", "").strip().lower()
DEMO_MODE = _DEMO_FLAG in ("1", "true", "yes") or (
    _DEMO_FLAG not in ("0", "false", "no") and not TRAVELPAYOUTS_TOKEN
)

HOST = os.getenv("HOST", "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
