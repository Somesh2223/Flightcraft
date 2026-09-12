"""Synthetic fares, so the app is usable before a Travelpayouts token exists.

Deterministic: the same route and date always produce the same price, so the
month grid looks stable across reloads and filters behave believably. The shape
of the data is realistic (weekend and holiday premiums, low-cost carriers
cheaper but with more stops) but the numbers are invented.

Every row is stamped `source="demo"` and the API flags demo mode in its
response, because a plausible-looking fake price is worse than no price if
anyone mistakes it for real.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from api.domain import FareRow

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
    # A December peak, which makes the seasonality visible in a month grid.
    festive = 1.25 if (day.month == 12 and day.day >= 15) or day.month == 1 and day.day <= 5 else 1.0
    return weekend * festive


def _price(origin: str, destination: str, day: date, index: float, salt: str) -> Decimal:
    raw = (
        _base_price(origin, destination)
        * _day_factor(day)
        * index
        * (0.82 + 0.36 * _noise(origin, destination, day, salt))
    )
    return Decimal(int(round(raw, -1)))


def _row(
    origin: str,
    destination: str,
    day: date,
    airline: str,
    stops: int,
    price: Decimal,
    currency: str,
    with_airline: bool = True,
) -> FareRow:
    depart_hour = int(_noise(origin, destination, day, airline) * 20) + 2
    return FareRow(
        origin=origin,
        destination=destination,
        depart_date=day,
        price=price,
        currency=currency,
        stops=stops,
        airline=airline if with_airline else None,
        flight_number=(
            f"{airline}{100 + int(_noise(airline, day) * 800)}" if with_airline else None
        ),
        departure_at=datetime.combine(day, datetime.min.time()).replace(
            hour=depart_hour, tzinfo=timezone.utc
        ),
        source=SOURCE,
        observed_at=datetime.now(timezone.utc) - timedelta(hours=int(_noise(day) * 20)),
    )


def _fleet_for(origin: str, destination: str) -> list[tuple[str, float, int]]:
    """A stable subset of carriers, so a route isn't served by all twelve."""
    picked = [
        entry
        for entry in _FLEET
        if _noise(origin, destination, entry[0]) > 0.35
    ]
    return picked or _FLEET[:3]


class DemoClient:
    """Duck-types TravelpayoutsClient for the endpoints the scanner uses."""

    async def __aenter__(self) -> DemoClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def calendar(
        self,
        origin: str,
        destination: str,
        depart_month: date,
        return_date: date | None = None,
        currency: str = "inr",
    ) -> list[FareRow]:
        rows = []
        day = depart_month.replace(day=1)
        while day.month == depart_month.month:
            airline, index, stops = min(
                _fleet_for(origin, destination),
                key=lambda e: _price(origin, destination, day, e[1], e[0]),
            )
            rows.append(
                _row(
                    origin,
                    destination,
                    day,
                    airline,
                    stops,
                    _price(origin, destination, day, index, airline),
                    currency,
                )
            )
            day += timedelta(days=1)
        return rows

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
            for stops in (0, 1, 2):
                index = 1.25 - 0.18 * stops
                rows.append(
                    _row(
                        origin,
                        destination,
                        day,
                        "ZZ",
                        stops,
                        _price(origin, destination, day, index, f"matrix{stops}"),
                        currency,
                        with_airline=False,
                    )
                )
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
        rows = []
        for stops in (0, 1, 2):
            candidates = [e for e in _fleet_for(origin, destination) if e[2] <= stops]
            if not candidates:
                continue
            airline, index, _ = min(
                candidates,
                key=lambda e: _price(origin, destination, depart_date, e[1], f"{e[0]}{stops}"),
            )
            rows.append(
                _row(
                    origin,
                    destination,
                    depart_date,
                    airline,
                    stops,
                    _price(
                        origin,
                        destination,
                        depart_date,
                        index - 0.12 * stops,
                        f"{airline}{stops}",
                    ),
                    currency,
                )
            )
        return rows
