"""Fanning a SearchSpec out across provider endpoints.

No single Travelpayouts endpoint gives both month-wide breadth and airline
attribution, so scanning happens in tiers and the caller chooses how much
quota to spend:

    QUICK     1 call per month per direction. One airline-attributed fare per
              day. Enough to draw the month grid instantly.
    STANDARD  + month-matrix, which adds every stop-count variant per day but
              without airline attribution. The default.
    DEEP      + one call per date, which attributes an airline to each stop
              count. Needed before an airline or aircraft filter can be
              honoured, and the reason a scan can cost ~60 calls.

Depth is a cost dial, not a quality setting: QUICK results are not wrong, they
are just narrower.
"""
from __future__ import annotations

import asyncio
from datetime import date
from enum import Enum

from pydantic import BaseModel, Field

from api.domain import DateRange, FareRow, SearchSpec
from api.providers.travelpayouts import TravelpayoutsClient


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
    if not span.contains(row.depart_date):
        return False
    # Endpoints keyed by destination can echo back neighbours; keep our pair only.
    if row.origin and row.origin != origin:
        return False
    if row.destination and row.destination != destination:
        return False
    return True


async def _gather_leg(
    client: TravelpayoutsClient,
    origin: str,
    destination: str,
    span: DateRange,
    currency: str,
    depth: ScanDepth,
) -> tuple[list[FareRow], int, list[str]]:
    months = span.months()
    tasks: list = [
        client.calendar(origin, destination, month, currency=currency)
        for month in months
    ]

    if depth in (ScanDepth.STANDARD, ScanDepth.DEEP):
        tasks += [
            client.month_matrix(origin, destination, month, currency=currency)
            for month in months
        ]

    if depth is ScanDepth.DEEP:
        tasks += [
            client.cheap(origin, destination, day, currency=currency)
            for day in span.dates()
        ]

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

    return _dedupe(rows), len(tasks), warnings


async def scan(
    spec: SearchSpec,
    client: TravelpayoutsClient,
    depth: ScanDepth = ScanDepth.STANDARD,
) -> ScanResult:
    legs = [(spec.origin, spec.destination, spec.outbound)]
    if spec.inbound is not None:
        legs.append((spec.destination, spec.origin, spec.inbound))

    results = await asyncio.gather(
        *(
            _gather_leg(client, origin, dest, span, spec.currency, depth)
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
    """Provider calls a scan will cost, so the UI can warn before a deep scan."""
    spans: list[DateRange] = [spec.outbound]
    if spec.inbound is not None:
        spans.append(spec.inbound)

    total = 0
    for span in spans:
        months = len(span.months())
        total += months
        if depth in (ScanDepth.STANDARD, ScanDepth.DEEP):
            total += months
        if depth is ScanDepth.DEEP:
            total += len(span)
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
