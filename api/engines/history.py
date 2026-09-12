"""Turning accumulated observations into a book-now-or-wait signal.

The statistics are pure functions over a list of prices; only the thin wrappers
at the bottom touch the database. That split matters because the judgement calls
here — how many samples before we are willing to say anything, what counts as an
error fare — are exactly what needs testing, and none of it should require a
database to exercise.

The honest constraint: this feature has nothing to say on day one. A verdict is
withheld until there is enough history to support it, and the confidence level
is reported alongside so the UI never implies more certainty than exists.
"""
from __future__ import annotations

import statistics
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Sequence

from pydantic import BaseModel
from sqlalchemy import func, select

from api.domain import FareRow
from api.storage.models import FareObservation, fingerprint_for

# Below this many observations, any percentile is noise dressed up as insight.
MIN_SAMPLES = 8
# Samples must also be spread over time: eight rows from one scan say nothing
# about whether today's price is unusual.
MIN_DISTINCT_DAYS = 3
HISTORY_WINDOW_DAYS = 90

# A fare this far under the median is more likely a mistake or a flash sale than
# a normal fluctuation, and is worth flagging loudly.
ERROR_FARE_RATIO = Decimal("0.6")


class Verdict(str, Enum):
    EXCEPTIONAL = "exceptional"
    GOOD = "good"
    TYPICAL = "typical"
    HIGH = "high"
    UNKNOWN = "unknown"


class Confidence(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class PriceContext(BaseModel):
    verdict: Verdict
    confidence: Confidence
    samples: int
    distinct_days: int
    percentile: float | None = None
    median: Decimal | None = None
    cheapest_seen: Decimal | None = None
    note: str


def _confidence(samples: int, distinct_days: int) -> Confidence:
    if samples < MIN_SAMPLES or distinct_days < MIN_DISTINCT_DAYS:
        return Confidence.NONE
    if distinct_days >= 21 and samples >= 40:
        return Confidence.HIGH
    if distinct_days >= 7 and samples >= 20:
        return Confidence.MEDIUM
    return Confidence.LOW


def percentile_of(prices: Sequence[Decimal], current: Decimal) -> float:
    """Percent of observations at or below `current`. Low means cheap."""
    if not prices:
        return 0.0
    below = sum(1 for p in prices if p < current)
    equal = sum(1 for p in prices if p == current)
    return round(100 * (below + 0.5 * equal) / len(prices), 1)


def summarise(
    prices: Sequence[Decimal], current: Decimal, distinct_days: int
) -> PriceContext:
    samples = len(prices)
    confidence = _confidence(samples, distinct_days)

    if confidence is Confidence.NONE:
        return PriceContext(
            verdict=Verdict.UNKNOWN,
            confidence=confidence,
            samples=samples,
            distinct_days=distinct_days,
            cheapest_seen=min(prices) if prices else None,
            note=(
                "Not enough history yet to judge this price. The signal builds "
                "over the next few weeks of scanning."
            ),
        )

    median = Decimal(str(statistics.median(prices)))
    pct = percentile_of(prices, current)
    cheapest = min(prices)

    if pct <= 10 and median > 0 and current <= median * ERROR_FARE_RATIO:
        verdict = Verdict.EXCEPTIONAL
        note = (
            f"{_pct_below(current, median)}% below the {_days(distinct_days)} median. "
            "Fares this far out of line are often short-lived — check it now."
        )
    elif pct <= 25:
        verdict = Verdict.GOOD
        note = f"Cheaper than {round(100 - pct)}% of prices seen for this date."
    elif pct >= 75:
        verdict = Verdict.HIGH
        note = (
            f"Pricier than {round(pct)}% of prices seen for this date. "
            f"It has been as low as {cheapest}."
        )
    else:
        verdict = Verdict.TYPICAL
        note = "About the usual price for this date."

    return PriceContext(
        verdict=verdict,
        confidence=confidence,
        samples=samples,
        distinct_days=distinct_days,
        percentile=pct,
        median=median,
        cheapest_seen=cheapest,
        note=note,
    )


def _pct_below(current: Decimal, median: Decimal) -> int:
    return int(round(100 * (1 - current / median)))


def _days(count: int) -> str:
    return f"{count}-day" if count < 90 else "90-day"


# --- database-backed wrappers ------------------------------------------------


def _dialect_insert(bind_url: str):
    if bind_url.startswith("postgresql"):
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


async def record(session, rows: Sequence[FareRow]) -> int:
    """Append observations, ignoring ones already stored.

    Scans overlap heavily — the same fare comes back on every re-scan until the
    provider refreshes it — so conflicts are the normal case, not an error.
    """
    if not rows:
        return 0

    insert = _dialect_insert(str(session.bind.url) if session.bind else "sqlite")
    payload = [
        {
            "fingerprint": fingerprint_for(
                row.origin,
                row.destination,
                row.depart_date,
                row.return_date,
                row.stops,
                row.price,
                row.currency,
                row.observed_at,
            ),
            "origin": row.origin,
            "destination": row.destination,
            "depart_date": row.depart_date,
            "return_date": row.return_date,
            "price": row.price,
            "currency": row.currency,
            "stops": row.stops,
            "airline": row.airline,
            "duration_minutes": row.duration_minutes,
            "source": row.source,
            "observed_at": row.observed_at,
        }
        for row in rows
    ]

    # A single scan can return the same fare from two endpoints, so the batch
    # is deduplicated before it reaches a constraint that only sees one row.
    unique = {entry["fingerprint"]: entry for entry in payload}

    result = await session.execute(
        insert(FareObservation)
        .on_conflict_do_nothing(index_elements=["fingerprint"])
        .values(list(unique.values()))
    )
    await session.commit()
    return result.rowcount or 0


async def history_by_date(
    session,
    origin: str,
    destination: str,
    depart_dates: Sequence[date],
    currency: str,
    round_trip: bool,
    window_days: int = HISTORY_WINDOW_DAYS,
) -> dict[date, tuple[list[Decimal], int]]:
    """Prices seen for each departure date, and how many days they span.

    One query for the whole result set rather than one per row.

    `round_trip` partitions the pool deliberately: a return fare and a one-way
    fare for the same date are different quantities, and mixing them would
    compare a trip total against single legs.
    """
    if not depart_dates:
        return {}

    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    return_clause = (
        FareObservation.return_date.isnot(None)
        if round_trip
        else FareObservation.return_date.is_(None)
    )

    rows = (
        await session.execute(
            select(
                FareObservation.depart_date,
                FareObservation.price,
                FareObservation.recorded_at,
            ).where(
                FareObservation.origin == origin,
                FareObservation.destination == destination,
                FareObservation.depart_date.in_(list(depart_dates)),
                FareObservation.currency == currency,
                FareObservation.recorded_at >= cutoff,
                return_clause,
            )
        )
    ).all()

    prices: dict[date, list[Decimal]] = {}
    days: dict[date, set[date]] = {}
    for row in rows:
        prices.setdefault(row.depart_date, []).append(Decimal(str(row.price)))
        days.setdefault(row.depart_date, set()).add(row.recorded_at.date())

    return {day: (values, len(days[day])) for day, values in prices.items()}


async def route_sample_size(session, origin: str, destination: str) -> int:
    return (
        await session.execute(
            select(func.count(FareObservation.id)).where(
                FareObservation.origin == origin,
                FareObservation.destination == destination,
            )
        )
    ).scalar_one()
