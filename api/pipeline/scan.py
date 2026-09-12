"""Fanning a SearchSpec out across provider endpoints.

Tiers, sized against what the endpoints actually return (see the provider
module for the measurements):

    QUICK     1 call per direction. `latest` with period_type="year" returns
              dated one-way fares across a whole year, so even a two-month
              dual-range search costs two calls. No airline attribution.
    STANDARD  + month-matrix per month per direction. Overlaps QUICK heavily
              but is fresher and sometimes covers dates `latest` misses.
              The default.
    DEEP      + one call per date, the only source of airline attribution.
              Those fares are priced as ROUND TRIPS, so they are requested only
              when the search actually wants a return — on a one-way search they
              could never be used, and DEEP is silently equivalent to STANDARD.

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


ATTRIBUTION_LIMIT = 20

# The cheap endpoint rejects any pair whose departure and return are further
# apart than this: "diff between max depart date and min return date exceeds
# supported maximum of 30".
ATTRIBUTION_MAX_NIGHTS = 30


async def attribute_cells(
    client: TravelpayoutsClient,
    spec: SearchSpec,
    cells: list[tuple[date, date]],
    limit: int = ATTRIBUTION_LIMIT,
) -> tuple[list[FareRow], int, list[str]]:
    """Fetch airline-attributed round-trip fares for specific date pairs.

    The only endpoint carrying an airline prices round trips, so attribution is
    only possible once both dates are known. Rather than sweeping the calendar,
    the shortlist of cheapest date pairs is priced exactly — bounded at `limit`
    calls instead of one per date, and the fares that come back are real,
    comparable round-trip options rather than a decoration on existing rows.

    Returns rows, not enrichments: a fare priced for these exact dates stands on
    its own and competes with the paired one-ways.
    """
    eligible = [
        (depart, ret)
        for depart, ret in cells
        if (ret - depart).days <= ATTRIBUTION_MAX_NIGHTS
    ]
    shortlist = eligible[:limit]

    if not shortlist:
        if cells:
            return [], 0, [
                f"Airlines could not be identified: the only endpoint that names "
                f"them refuses trips longer than {ATTRIBUTION_MAX_NIGHTS} nights, "
                f"and every option here is longer."
            ]
        return [], 0, []

    batches = await asyncio.gather(
        *(
            client.cheap(
                spec.origin,
                spec.destination,
                depart,
                return_date=ret,
                currency=spec.currency,
            )
            for depart, ret in shortlist
        ),
        return_exceptions=True,
    )

    rows: list[FareRow] = []
    warnings: list[str] = []
    wanted = set(shortlist)

    for batch in batches:
        if isinstance(batch, BaseException):
            warnings.append(f"airline lookup: {type(batch).__name__}: {batch}")
            continue
        for row in batch:
            # The provider can answer with its own dates; only keep fares that
            # actually price the pair we asked about.
            if row.return_date and (row.depart_date, row.return_date) in wanted:
                rows.append(row)

    rows = _dedupe(rows)

    if not rows and not warnings:
        warnings.append(
            f"No airline could be identified for any of the {len(shortlist)} best "
            "date pairs. The endpoint that names airlines has very thin coverage, "
            "so most fares stay unattributed however deep the scan."
        )

    return rows, len(shortlist), warnings


def estimate_calls(spec: SearchSpec, depth: ScanDepth) -> int:
    """Provider calls a scan will cost, so the UI can warn before a deep scan."""
    spans: list[DateRange] = [spec.outbound]
    if spec.inbound is not None:
        spans.append(spec.inbound)

    total = 0
    for span in spans:
        total += 1
        if depth in (ScanDepth.STANDARD, ScanDepth.DEEP):
            total += len(span.months())
    if depth is ScanDepth.DEEP and spec.is_return:
        total += ATTRIBUTION_LIMIT
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
