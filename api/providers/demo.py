"""Synthetic fares, so the app is usable before a Travelpayouts token exists.

Deterministic: the same route and date always produce the same price, so the
month grid looks stable across reloads and filters behave believably. The shape
of the data is realistic (weekend and festive premiums, low-cost carriers
cheaper but with more stops) but the numbers are invented.

It also mirrors the real API's *limitations*, not just its shape, because a demo
that is more capable than production teaches the wrong thing:

  * `latest` and `month_matrix` return one-way fares with NO airline.
  * `cheap` is the only source of an airline, and it prices a ROUND TRIP.
  * Coverage is patchy — not every date has a cached fare.

Every row is stamped `source="demo"` and the API flags demo mode in its
response, because a plausible-looking fake price is worse than no price if
anyone mistakes it for real.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from api.domain import FareRow, TripClass

SOURCE = "demo"

# Carriers a demo route can plausibly be served by, with a rough price index
# and typical stop count.
_FLEET = [
    ("6E", 0.82, 0),
    ("AI", 1.00, 0),
    ("IX", 0.78, 1),
    ("QP", 0.85, 0),
    ("EK", 1.12, 1),
    ("QR", 1.08, 1),
    ("EY", 1.02, 1),
    ("SQ", 1.18, 1),
    ("TK", 0.95, 1),
    ("BA", 1.15, 0),
    ("LH", 1.10, 1),
    ("FZ", 0.80, 1),
]


def _noise(*parts: object) -> float:
    """Stable pseudo-random value in [0, 1) derived from the arguments."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode()).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def _base_price(origin: str, destination: str) -> float:
    return 6000 + _noise(origin, destination) * 46000


def _day_factor(day: date) -> float:
    weekend = 1.18 if day.weekday() in (4, 6) else 1.0
    festive = (
        1.25
        if (day.month == 12 and day.day >= 15) or (day.month == 1 and day.day <= 5)
        else 1.0
    )
    return weekend * festive


def _price(origin: str, destination: str, day: date, index: float, salt: str) -> Decimal:
    raw = (
        _base_price(origin, destination)
        * _day_factor(day)
        * index
        * (0.82 + 0.36 * _noise(origin, destination, day, salt))
    )
    return Decimal(int(round(raw, -1)))


def _has_fare(origin: str, destination: str, day: date) -> bool:
    """Coverage thins out further from today, as the real cache does."""
    horizon = (day - date.today()).days
    threshold = 0.08 if horizon < 90 else 0.35
    return _noise(origin, destination, day, "coverage") > threshold


def _fleet_for(origin: str, destination: str) -> list[tuple[str, float, int]]:
    """A stable subset of carriers, so a route isn't served by all twelve."""
    picked = [e for e in _FLEET if _noise(origin, destination, e[0]) > 0.35]
    return picked or _FLEET[:3]


def _one_way(
    origin: str, destination: str, day: date, currency: str, salt: str
) -> FareRow:
    stops = 0 if _noise(origin, destination, day, "stops") > 0.45 else 1
    index = 1.0 - 0.14 * stops
    return FareRow(
        origin=origin,
        destination=destination,
        depart_date=day,
        price=_price(origin, destination, day, index, salt),
        currency=currency,
        stops=stops,
        duration_minutes=220 + stops * 300 + int(_noise(day, salt) * 240),
        distance_km=2184,
        source=SOURCE,
        observed_at=datetime.now(timezone.utc) - timedelta(hours=int(_noise(day) * 40)),
    )


class DemoClient:
    """Duck-types TravelpayoutsClient for the endpoints the scanner uses."""

    async def __aenter__(self) -> DemoClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def latest(
        self,
        origin: str,
        destination: str | None = None,
        beginning_of_period: date | None = None,
        period_type: str = "month",
        one_way: bool = False,
        limit: int = 1000,
        currency: str = "inr",
        trip_class: TripClass = TripClass.ECONOMY,
    ) -> list[FareRow]:
        destination = destination or "XXX"
        start = beginning_of_period or date.today()
        horizon = 365 if period_type == "year" else 31
        return [
            _one_way(origin, destination, day, currency, "latest")
            for offset in range(horizon)
            if _has_fare(origin, destination, (day := start + timedelta(days=offset)))
        ][:limit]

    async def month_matrix(
        self,
        origin: str,
        destination: str,
        month: date,
        currency: str = "inr",
    ) -> list[FareRow]:
        rows = []
        day = month.replace(day=1)
        while day.month == month.month:
            if _has_fare(origin, destination, day):
                rows.append(_one_way(origin, destination, day, currency, "matrix"))
            day += timedelta(days=1)
        return rows

    async def cheap(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None = None,
        currency: str = "inr",
    ) -> list[FareRow]:
        """Round-trip fares with an airline, mirroring the real endpoint."""
        ret = return_date or depart_date + timedelta(days=7)
        rows = []
        for stops in (0, 1):
            candidates = [e for e in _fleet_for(origin, destination) if e[2] <= stops]
            if not candidates:
                continue
            airline, index, _ = min(
                candidates,
                key=lambda e: _price(origin, destination, depart_date, e[1], f"{e[0]}{stops}"),
            )
            outbound = _price(origin, destination, depart_date, index, f"{airline}o{stops}")
            inbound = _price(destination, origin, ret, index, f"{airline}i{stops}")
            rows.append(
                FareRow(
                    origin=origin,
                    destination=destination,
                    depart_date=depart_date,
                    return_date=ret,
                    price=outbound + inbound,
                    currency=currency,
                    stops=stops,
                    airline=airline,
                    flight_number=f"{airline}{100 + int(_noise(airline, depart_date) * 800)}",
                    departure_at=datetime.combine(
                        depart_date, datetime.min.time()
                    ).replace(
                        hour=int(_noise(origin, depart_date, airline) * 20) + 2,
                        tzinfo=timezone.utc,
                    ),
                    return_at=datetime.combine(ret, datetime.min.time()).replace(
                        hour=12, tzinfo=timezone.utc
                    ),
                    duration_minutes=220 + stops * 300,
                    source=SOURCE,
                    observed_at=datetime.now(timezone.utc)
                    - timedelta(hours=int(_noise(depart_date) * 20)),
                )
            )
        return rows
