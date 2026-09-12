"""Airport-to-city resolution, from Travelpayouts' own reference data.

The fare endpoints answer at city level: ask for LHR and rows come back stamped
LON. So a row's codes cannot be compared to the requested ones directly — but
they cannot be ignored either, because a DXB search also returns the occasional
SHJ (Sharjah) fare, which is a genuinely different city and not an answer to the
question asked.

Resolving both sides to a city code separates the two cases. The dataset is
~2.5 MB, fetched once and cached on disk.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from api import config

AIRPORTS_URL = "https://api.travelpayouts.com/data/en/airports.json"
CACHE_PATH: Path = config.DATA_DIR / "airports.json"

_city_by_code: dict[str, str] | None = None


def _build(records: list) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in records:
        if not isinstance(entry, dict):
            continue
        code, city = entry.get("code"), entry.get("city_code")
        if code and city:
            out[str(code).upper()] = str(city).upper()
    return out


def _load_cache() -> bool:
    global _city_by_code
    if _city_by_code is not None:
        return True
    if not CACHE_PATH.exists():
        return False
    try:
        _city_by_code = _build(json.loads(CACHE_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return False
    return True


async def ensure_loaded(client: httpx.AsyncClient | None = None) -> bool:
    """Load the mapping, downloading and caching it on first use.

    Best-effort: a failure leaves resolution permissive rather than breaking a
    search, and callers can check `is_loaded()` to say so.
    """
    if _load_cache():
        return True

    owns = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        response = await client.get(AIRPORTS_URL)
        response.raise_for_status()
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_bytes(response.content)
    except (httpx.HTTPError, OSError):
        return False
    finally:
        if owns:
            await client.aclose()

    return _load_cache()


def is_loaded() -> bool:
    return _city_by_code is not None


def city_code(code: str | None) -> str | None:
    """The city a code belongs to; the code itself if it is already a city."""
    if not code:
        return None
    code = code.upper()
    if _city_by_code is None:
        return code
    return _city_by_code.get(code, code)


def same_place(a: str | None, b: str | None) -> bool:
    """Whether two codes name the same city. Permissive if the map is missing."""
    if not a or not b:
        return True
    if a.upper() == b.upper():
        return True
    if _city_by_code is None:
        return True
    return city_code(a) == city_code(b)


def _reset_for_tests(mapping: dict[str, str] | None) -> None:
    global _city_by_code
    _city_by_code = mapping
