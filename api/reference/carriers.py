"""Carrier reference lookups, loaded once from api/data/carriers.yaml."""
from __future__ import annotations

from functools import lru_cache

import yaml
from pydantic import BaseModel

from api import config
from api.domain import CarrierClass

_CARRIERS_FILE = config.DATASETS_DIR / "carriers.yaml"


class Carrier(BaseModel):
    code: str
    name: str
    carrier_class: CarrierClass = CarrierClass.UNKNOWN
    alliance: str | None = None
    country: str | None = None


@lru_cache(maxsize=1)
def _registry() -> dict[str, Carrier]:
    raw = yaml.safe_load(_CARRIERS_FILE.read_text(encoding="utf-8")) or {}
    out: dict[str, Carrier] = {}
    for code, entry in (raw.get("carriers") or {}).items():
        code = str(code).upper()
        out[code] = Carrier(
            code=code,
            name=entry.get("name", code),
            carrier_class=CarrierClass(entry.get("class", CarrierClass.UNKNOWN.value)),
            alliance=entry.get("alliance"),
            country=entry.get("country"),
        )
    return out


def get(code: str | None) -> Carrier | None:
    if not code:
        return None
    return _registry().get(code.upper())


def display_name(code: str | None) -> str:
    carrier = get(code)
    return carrier.name if carrier else (code or "Unknown")


def carrier_class(code: str | None) -> CarrierClass:
    carrier = get(code)
    return carrier.carrier_class if carrier else CarrierClass.UNKNOWN


def alliance(code: str | None) -> str | None:
    carrier = get(code)
    return carrier.alliance if carrier else None


def codes_in_class(target: CarrierClass) -> set[str]:
    return {c.code for c in _registry().values() if c.carrier_class is target}


def all_carriers() -> list[Carrier]:
    return sorted(_registry().values(), key=lambda c: c.name)
