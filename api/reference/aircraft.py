"""Classifying aircraft from the display names live fares carry.

Ignav reports aircraft as human strings — "Airbus A321neo", "Boeing 737" —
rather than ICAO codes, so the avgeek filters need to turn those into something
structured: family, body width, engine count, and whether the type is on its way
out.

Matching is by substring against an ordered table, longest-first, because
"A350-1000" must not be caught by a rule written for "A350" only after a rule
for "A35K" — and "737 MAX" must not be mistaken for a classic 737 when the
distinction matters.
"""
from __future__ import annotations

import re
from functools import lru_cache

from pydantic import BaseModel

from api.domain import BodyType


class AircraftInfo(BaseModel):
    raw: str
    family: str | None = None
    manufacturer: str | None = None
    body: BodyType = BodyType.UNKNOWN
    engines: int | None = None
    # Types being retired from most fleets — the "fly it before it's gone" filter.
    retiring: bool = False


# (pattern, family, manufacturer, body, engines, retiring)
_RULES: list[tuple[str, str, str, BodyType, int, bool]] = [
    (r"a380", "A380", "Airbus", BodyType.WIDEBODY, 4, True),
    (r"a340", "A340", "Airbus", BodyType.WIDEBODY, 4, True),
    (r"a350", "A350", "Airbus", BodyType.WIDEBODY, 2, False),
    (r"a330", "A330", "Airbus", BodyType.WIDEBODY, 2, False),
    # neo and ceo share a family: someone filtering "A320 family" wants both,
    # and anyone who cares about the variant can match on `raw`.
    (r"a318|a319|a320|a321", "A320", "Airbus", BodyType.NARROWBODY, 2, False),
    (r"a220", "A220", "Airbus", BodyType.NARROWBODY, 2, False),
    (r"747", "747", "Boeing", BodyType.WIDEBODY, 4, True),
    (r"787|dreamliner", "787", "Boeing", BodyType.WIDEBODY, 2, False),
    (r"777", "777", "Boeing", BodyType.WIDEBODY, 2, False),
    (r"767", "767", "Boeing", BodyType.WIDEBODY, 2, True),
    (r"757", "757", "Boeing", BodyType.NARROWBODY, 2, True),
    (r"737", "737", "Boeing", BodyType.NARROWBODY, 2, False),
    (r"md-?1[01]|dc-?10", "MD-11", "McDonnell Douglas", BodyType.WIDEBODY, 3, True),
    (r"embraer|e1[79]0|e19[05]|erj", "Embraer E-Jet", "Embraer", BodyType.REGIONAL, 2, False),
    (r"crj", "CRJ", "Bombardier", BodyType.REGIONAL, 2, False),
    (r"atr", "ATR", "ATR", BodyType.TURBOPROP, 2, False),
    (r"dash 8|q400", "Dash 8", "De Havilland", BodyType.TURBOPROP, 2, False),
]

_COMPILED = [(re.compile(p, re.I), *rest) for p, *rest in _RULES]


@lru_cache(maxsize=512)
def classify(raw: str | None) -> AircraftInfo:
    if not raw:
        return AircraftInfo(raw="")

    for pattern, family, manufacturer, body, engines, retiring in _COMPILED:
        if pattern.search(raw):
            return AircraftInfo(
                raw=raw,
                family=family,
                manufacturer=manufacturer,
                body=body,
                engines=engines,
                retiring=retiring,
            )

    return AircraftInfo(raw=raw)


def is_widebody(raw: str | None) -> bool:
    return classify(raw).body is BodyType.WIDEBODY
