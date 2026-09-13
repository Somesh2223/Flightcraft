"""Stage two: turning the cheap, vague landscape into exact bookable answers.

The cached scan is free and wide — it can price a whole year in one call — but
it only ever says "something cost this much on this date". Ignav says which
airline, which aircraft, which flight number, and what you would actually pay,
at roughly $0.002 a request.

Spending a request on all sixty dates of a two-month search would be wasteful
and slow. So the free landscape is used as a targeting layer: it ranks the date
space, and only the best cells are bought outright. A search resolves ten cells
by default — about two rupees — and the user still sees every date, because the
unresolved ones keep their cached estimate and are labelled as such.

Live rows replace cached ones for the same cell rather than competing with them.
A cached price that is cheaper but unbookable is worse than no price at all:
that is exactly the gap between a search result and a checkout page.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field

from api import config
from api.domain import FareRow, SearchSpec, TripOption
from api.pipeline.filters import FilterSet
from api.pipeline.scan import ScanDepth
from api.providers.ignav import IgnavClient, IgnavError

# How many cells each depth buys. QUICK stays free and cached-only; DEEP covers
# a typical month.
RESOLVE_LIMITS: dict[ScanDepth, int] = {
    ScanDepth.QUICK: 0,
    ScanDepth.STANDARD: config.IGNAV_RESOLVE_LIMIT,
    ScanDepth.DEEP: 30,
}


class ResolveResult(BaseModel):
    """Live rows, kept per direction so the caller can pair them.

    Pairing is deliberately left to the caller rather than done here: which
    combinations are legal depends on the trip-length constraints, and the
    resolved cells then fall out of the pairing instead of being predicted.
    """

    outbound_rows: list[FareRow] = Field(default_factory=list)
    inbound_rows: list[FareRow] = Field(default_factory=list)
    round_trip_rows: list[FareRow] = Field(default_factory=list)
    requests: int = 0
    cached_cells: int = 0
    warnings: list[str] = Field(default_factory=list)

    @property
    def any_rows(self) -> bool:
        return bool(self.outbound_rows or self.inbound_rows or self.round_trip_rows)


# For a return search, a couple of requests go on pricing the best cells as a
# single round-trip ticket. Airlines often price a return far below two separate
# one-ways, and missing that would hand the traveller the wrong answer on
# exactly the long-gap trips this app is built for.
ROUND_TRIP_PROBES = 2


# Live quotes are cached briefly because every miss costs a paid request, and
# repeating a search — changing a filter, reloading the page, paging back — is
# the normal way people use a search tool. The window is deliberately short:
# Ignav advises against holding fares for more than a few hours, and a stale
# "live" price is the exact failure this whole stage exists to avoid.
_CACHE: dict[tuple, tuple[datetime, list[FareRow]]] = {}


def _cache_key(spec: SearchSpec, cell: tuple[date, date | None], filters: dict) -> tuple:
    return (
        spec.origin,
        spec.destination,
        cell,
        spec.currency,
        tuple(sorted((k, str(v)) for k, v in filters.items())),
    )


def _cached(key: tuple) -> list[FareRow] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    stored_at, rows = entry
    if datetime.now(timezone.utc) - stored_at > timedelta(
        minutes=config.LIVE_QUOTE_TTL_MINUTES
    ):
        del _CACHE[key]
        return None
    return rows


def clear_cache() -> None:
    _CACHE.clear()


def candidate_cells(
    options: list[TripOption], limit: int
) -> list[tuple[date, date | None]]:
    """The date cells worth paying to price exactly, cheapest first.

    Options arrive ranked by price, so taking from the front spends the budget
    where a traveller is most likely to look.
    """
    seen: set[tuple[date, date | None]] = set()
    cells: list[tuple[date, date | None]] = []
    for option in options:
        cell = (option.depart_date, option.return_date)
        if cell in seen:
            continue
        seen.add(cell)
        cells.append(cell)
        if len(cells) >= limit:
            break
    return cells


def spread_cells(
    options: list[TripOption], limit: int
) -> list[tuple[date, date | None]]:
    """Evenly spaced dates instead of the cheapest-looking ones.

    Cheapest-first targeting assumes the cached landscape predicts where the
    good fares are. That assumption breaks precisely when someone filters by
    airline or aircraft: the cheap days in the landscape are cheap because of
    whichever carrier undercuts the route, which is usually not the one being
    asked about. Spending the whole budget there would price ten days that share
    one airline and miss the rest of the month.
    """
    cells = sorted({(o.depart_date, o.return_date) for o in options})
    if limit <= 0 or not cells:
        return []
    if len(cells) <= limit:
        return cells
    step = len(cells) / limit
    return [cells[int(i * step)] for i in range(limit)]


def spread_dates(options: list[TripOption], limit: int) -> tuple[list[date], list[date]]:
    out = sorted({o.depart_date for o in options})
    back = sorted({o.return_date for o in options if o.return_date is not None})

    def pick(days: list[date]) -> list[date]:
        if limit <= 0 or not days:
            return []
        if len(days) <= limit:
            return days
        step = len(days) / limit
        return [days[int(i * step)] for i in range(limit)]

    return pick(out), pick(back)


def candidate_dates(
    options: list[TripOption], limit: int
) -> tuple[list[date], list[date]]:
    """Best departure and return dates, taken separately.

    Pricing legs rather than pairs is what makes a two-month search affordable.
    Ten requests spent on pairs buys ten cells; the same ten split across the two
    directions buys twenty-five, because every priced outbound can be matched
    against every priced return. That combinatorial gain is the whole reason the
    independent-months feature is practical at all.
    """
    out_seen: dict[date, None] = {}
    in_seen: dict[date, None] = {}

    for option in options:
        if len(out_seen) < limit and option.depart_date not in out_seen:
            out_seen[option.depart_date] = None
        if (
            option.return_date is not None
            and len(in_seen) < limit
            and option.return_date not in in_seen
        ):
            in_seen[option.return_date] = None
        if len(out_seen) >= limit and len(in_seen) >= limit:
            break

    return list(out_seen), list(in_seen)


def _provider_filters(spec: SearchSpec, filter_set: FilterSet | None) -> dict:
    """Push filters upstream so a paid request returns usable itineraries.

    Filtering server-side does not save requests, but it does stop a request
    being spent on twenty results that the user's own filters would discard.
    """
    filters: dict = {"adults": spec.passengers, "market": config.DEFAULT_MARKET}
    if filter_set is None:
        return filters

    if filter_set.max_stops is not None:
        filters["max_stops"] = filter_set.max_stops
    if filter_set.include_airlines:
        filters["airlines_include"] = sorted(filter_set.include_airlines)
    if filter_set.exclude_airlines:
        filters["airlines_exclude"] = sorted(filter_set.exclude_airlines)
    if filter_set.max_price is not None:
        filters["max_price"] = filter_set.max_price
    return filters


class _Job(BaseModel):
    """One paid lookup: a leg on a date, or a whole round trip."""

    model_config = {"arbitrary_types_allowed": True}

    origin: str
    destination: str
    depart: date
    ret: date | None = None
    lane: str  # "outbound", "inbound", or "round_trip"

    @property
    def key(self) -> tuple:
        return (self.origin, self.destination, self.depart, self.ret)


async def resolve(
    client: IgnavClient,
    spec: SearchSpec,
    options: list[TripOption],
    depth: ScanDepth,
    filter_set: FilterSet | None = None,
    limit: int | None = None,
) -> ResolveResult:
    """Price the best dates exactly. Failures degrade to the cached estimate."""
    budget = limit if limit is not None else RESOLVE_LIMITS.get(depth, 0)
    if budget <= 0 or not options:
        return ResolveResult()

    filters = _provider_filters(spec, filter_set)
    # An airline or aircraft filter makes the cached ranking an unreliable guide
    # to where that carrier is cheap, so the budget is spread across the window
    # instead of piled onto the dates that merely look cheapest overall.
    spread = filter_set is not None and (
        filter_set.needs_airline or filter_set.needs_aircraft
    )
    jobs = _plan(spec, options, budget, spread)
    if not jobs:
        return ResolveResult()

    lanes: dict[str, list[FareRow]] = {
        "outbound": [],
        "inbound": [],
        "round_trip": [],
    }
    warnings: list[str] = []
    reused = 0
    to_fetch: list[_Job] = []

    for job in jobs:
        hit = _cached(_cache_key(spec, job.key, filters))
        if hit is None:
            to_fetch.append(job)
        else:
            reused += 1
            lanes[job.lane].extend(hit)

    async def price(job: _Job) -> list[FareRow]:
        if job.ret is None:
            return await client.one_way(
                job.origin, job.destination, job.depart, spec.currency, **filters
            )
        return await client.round_trip(
            job.origin, job.destination, job.depart, job.ret, spec.currency, **filters
        )

    batches = await asyncio.gather(
        *(price(job) for job in to_fetch), return_exceptions=True
    )

    for job, batch in zip(to_fetch, batches):
        if isinstance(batch, BaseException):
            label = f"{job.origin}-{job.destination} {job.depart}"
            detail = str(batch) if isinstance(batch, IgnavError) else repr(batch)
            warnings.append(f"live pricing failed for {label}: {detail[:160]}")
            continue
        # An empty answer is cached too: the absence of flights is itself worth
        # knowing, and re-asking would spend a request to learn it again.
        _CACHE[_cache_key(spec, job.key, filters)] = (
            datetime.now(timezone.utc),
            batch,
        )
        lanes[job.lane].extend(batch)

    result = ResolveResult(
        outbound_rows=lanes["outbound"],
        inbound_rows=lanes["inbound"],
        round_trip_rows=lanes["round_trip"],
        requests=len(to_fetch),
        cached_cells=reused,
        warnings=warnings,
    )

    if warnings and not result.any_rows:
        result.warnings.append(
            "Live pricing is unavailable right now, so prices below are cached "
            "estimates and may not be bookable at these fares."
        )

    return result


def _plan(
    spec: SearchSpec,
    options: list[TripOption],
    budget: int,
    spread: bool = False,
) -> list[_Job]:
    """Decide what to spend the budget on."""
    if not spec.is_return:
        cells = (
            spread_cells(options, budget)
            if spread
            else candidate_cells(options, budget)
        )
        return [
            _Job(
                origin=spec.origin,
                destination=spec.destination,
                depart=depart,
                lane="outbound",
            )
            for depart, _ in cells
        ]

    probes = min(ROUND_TRIP_PROBES, max(budget - 2, 0))
    per_direction = max((budget - probes) // 2, 1)
    out_dates, in_dates = (
        spread_dates(options, per_direction)
        if spread
        else candidate_dates(options, per_direction)
    )

    jobs = [
        _Job(
            origin=spec.origin,
            destination=spec.destination,
            depart=day,
            lane="outbound",
        )
        for day in out_dates
    ] + [
        _Job(
            origin=spec.destination,
            destination=spec.origin,
            depart=day,
            lane="inbound",
        )
        for day in in_dates
    ]

    for depart, ret in candidate_cells(options, probes):
        if ret is not None:
            jobs.append(
                _Job(
                    origin=spec.origin,
                    destination=spec.destination,
                    depart=depart,
                    ret=ret,
                    lane="round_trip",
                )
            )

    return jobs


def cells_of(options: list[TripOption]) -> list[tuple[date, date | None]]:
    """Which cells a set of live options actually covers.

    Derived from the options rather than predicted before pairing, because the
    trip-length rules decide which combinations survive and only the pairing
    knows that.
    """
    return [(o.depart_date, o.return_date) for o in options]


def merge(
    cached: list[TripOption],
    live: list[TripOption],
    resolved_cells: list[tuple[date, date | None]],
) -> list[TripOption]:
    """Live quotes win their cell outright; cached options fill the rest.

    A cached fare for a cell we priced for real is not a second opinion, it is a
    stale guess — and the cheaper of the two is usually the one that no longer
    exists.
    """
    taken = set(resolved_cells)
    kept = [o for o in cached if (o.depart_date, o.return_date) not in taken]
    merged = live + kept
    merged.sort(key=lambda o: (o.total_price, o.depart_date))
    return merged
