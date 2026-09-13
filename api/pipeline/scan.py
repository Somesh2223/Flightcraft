"""Stage one: the free cached landscape.

This maps where the cheap dates are across whole months, for nothing. What it
cannot do is name an airline, an aircraft, or a price anyone can actually pay —
that is stage two's job, in pipeline/resolve.py.

    QUICK     1 call per direction. `latest` with period_type="year" returns
              dated one-way fares across a whole year, so even a two-month
              dual-range search costs two calls.
    STANDARD  + month-matrix per month per direction. Overlaps QUICK heavily
              but is fresher and covers some dates `latest` misses.

Depth also decides how many cells stage two buys outright, which is where the
money goes; the calls here are free either way.
"""
from __future__ import annotations

import asyncio
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from api.domain import DateRange, FareRow, SearchSpec
from api.providers.travelpayouts import TravelpayoutsClient
from api.reference import places


class ScanDepth(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"


class ScanResult(BaseModel):
    outbound: list[FareRow] = Field(default_factory=list)
    inbound: list[FareRow] = Field(default_factory=list)
    depth: ScanDepth
    provider_calls: int = 0
    warnings: list[str] = Field(default_factory=list)

    @property
    def has_airline_attribution(self) -> bool:
        rows = self.outbound + self.inbound
        return any(row.airline for row in rows)


def _dedupe(rows: list[FareRow]) -> list[FareRow]:
    seen: set[tuple] = set()
    out: list[FareRow] = []
    for row in rows:
        key = (
            row.depart_date,
            row.return_date,
            row.stops,
            row.airline,
            row.price,
            row.origin,
            row.destination,
        )
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


def _relevant(row: FareRow, origin: str, destination: str, span: DateRange) -> bool:
    """Date window, plus a city-level check on both endpoints.

    Codes are compared by city rather than exactly, because the provider answers
    at city level: a search for LHR comes back stamped LON, and demanding an
    exact match discarded every row on that route. Comparing city codes still
    excludes the genuinely different destinations the endpoint mixes in — a DXB
    search also returns the occasional SHJ (Sharjah) fare.
    """
    return (
        span.contains(row.depart_date)
        and places.same_place(row.origin, origin)
        and places.same_place(row.destination, destination)
    )


async def _gather_leg(
    client: TravelpayoutsClient,
    origin: str,
    destination: str,
    span: DateRange,
    currency: str,
    depth: ScanDepth,
    wants_round_trip: bool,
) -> tuple[list[FareRow], int, list[str]]:
    tasks: list = [
        client.latest(
            origin,
            destination,
            beginning_of_period=span.start,
            period_type="year",
            one_way=True,
            currency=currency,
        )
    ]

    if depth in (ScanDepth.STANDARD, ScanDepth.DEEP):
        tasks += [
            client.month_matrix(origin, destination, month, currency=currency)
            for month in span.months()
        ]

    # `cheap` is deliberately not fanned out over every date here. It prices
    # round trips with a provider-chosen return, so a blanket sweep yields
    # fares that almost never land in the requested inbound range. Airline
    # attribution is instead fetched for specific date pairs by attribute_cells.

    batches = await asyncio.gather(*tasks, return_exceptions=True)

    rows: list[FareRow] = []
    warnings: list[str] = []
    for batch in batches:
        if isinstance(batch, BaseException):
            warnings.append(f"{origin}-{destination}: {type(batch).__name__}: {batch}")
            continue
        rows.extend(
            row for row in batch if _relevant(row, origin, destination, span)
        )

    rows = _dedupe(rows)

    served = {(r.origin, r.destination) for r in rows if r.origin and r.destination}
    if served and (origin, destination) not in served:
        pairs = ", ".join(f"{o}-{d}" for o, d in sorted(served))
        warnings.append(
            f"{origin}-{destination} is priced at city level as {pairs}. Fares may "
            "be for any airport in that city, so check the arrival airport before booking."
        )

    return rows, len(tasks), warnings


async def scan(
    spec: SearchSpec,
    client: TravelpayoutsClient,
    depth: ScanDepth = ScanDepth.STANDARD,
) -> ScanResult:
    await places.ensure_loaded()

    legs = [(spec.origin, spec.destination, spec.outbound)]
    if spec.inbound is not None:
        legs.append((spec.destination, spec.origin, spec.inbound))

    results = await asyncio.gather(
        *(
            _gather_leg(
                client, origin, dest, span, spec.currency, depth, spec.is_return
            )
            for origin, dest, span in legs
        )
    )

    outbound_rows, outbound_calls, warnings = results[0]
    inbound_rows: list[FareRow] = []
    calls = outbound_calls

    if len(results) > 1:
        inbound_rows, inbound_calls, inbound_warnings = results[1]
        calls += inbound_calls
        warnings = warnings + inbound_warnings

    result = ScanResult(
        outbound=outbound_rows,
        inbound=inbound_rows,
        depth=depth,
        provider_calls=calls,
        warnings=warnings,
    )

    if not result.outbound:
        result.warnings.append(
            f"No cached fares for {spec.origin}-{spec.destination} in this window. "
            "Thin routes and far-future dates are often simply not in the cache yet."
        )

    return result


def estimate_calls(spec: SearchSpec, depth: ScanDepth) -> int:
    """Free cached calls a scan will cost. Paid resolution is counted separately."""
    spans: list[DateRange] = [spec.outbound]
    if spec.inbound is not None:
        spans.append(spec.inbound)

    total = 0
    for span in spans:
        total += 1
        if depth in (ScanDepth.STANDARD, ScanDepth.DEEP):
            total += len(span.months())
    return total


def dates_in_window(spec: SearchSpec) -> int:
    """Distinct departure dates a full sweep would have to price."""
    total = len(spec.outbound)
    if spec.inbound is not None:
        total += len(spec.inbound)
    return total


def widest_span(*spans: DateRange | None) -> DateRange | None:
    present = [s for s in spans if s is not None]
    if not present:
        return None
    return DateRange(
        start=min(s.start for s in present), end=max(s.end for s in present)
    )


def month_of(day: date) -> date:
    return day.replace(day=1)
