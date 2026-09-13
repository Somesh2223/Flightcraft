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
    rows: list[FareRow] = Field(default_factory=list)
    cells: list[tuple[date, date | None]] = Field(default_factory=list)
    requests: int = 0
    cached_cells: int = 0
    warnings: list[str] = Field(default_factory=list)


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


async def resolve(
    client: IgnavClient,
    spec: SearchSpec,
    options: list[TripOption],
    depth: ScanDepth,
    filter_set: FilterSet | None = None,
    limit: int | None = None,
) -> ResolveResult:
    """Price the best cells exactly. Failures degrade to the cached estimate."""
    budget = limit if limit is not None else RESOLVE_LIMITS.get(depth, 0)
    if budget <= 0 or not options:
        return ResolveResult()

    cells = candidate_cells(options, budget)
    if not cells:
        return ResolveResult()

    filters = _provider_filters(spec, filter_set)
    rows: list[FareRow] = []
    warnings: list[str] = []
    resolved: list[tuple[date, date | None]] = []

    to_fetch: list[tuple[date, date | None]] = []
    reused = 0
    for cell in cells:
        hit = _cached(_cache_key(spec, cell, filters))
        if hit is None:
            to_fetch.append(cell)
        else:
            reused += 1
            if hit:
                resolved.append(cell)
                rows.extend(hit)

    async def price(cell: tuple[date, date | None]) -> list[FareRow]:
        depart, ret = cell
        if ret is None:
            return await client.one_way(
                spec.origin, spec.destination, depart, spec.currency, **filters
            )
        return await client.round_trip(
            spec.origin, spec.destination, depart, ret, spec.currency, **filters
        )

    batches = await asyncio.gather(
        *(price(cell) for cell in to_fetch), return_exceptions=True
    )

    for cell, batch in zip(to_fetch, batches):
        if isinstance(batch, BaseException):
            label = f"{cell[0]}" + (f" / {cell[1]}" if cell[1] else "")
            detail = str(batch) if isinstance(batch, IgnavError) else repr(batch)
            warnings.append(f"live pricing failed for {label}: {detail[:160]}")
            continue
        # An empty answer is cached too: the absence of flights is itself worth
        # knowing, and re-asking would spend a request to learn it again.
        _CACHE[_cache_key(spec, cell, filters)] = (datetime.now(timezone.utc), batch)
        if batch:
            # Only a cell that actually produced fares counts as resolved; an
            # empty answer must leave the cached estimate in place rather than
            # blanking the date.
            resolved.append(cell)
            rows.extend(batch)

    if warnings and not rows:
        warnings.append(
            "Live pricing is unavailable right now, so prices below are cached "
            "estimates and may not be bookable at these fares."
        )

    return ResolveResult(
        rows=rows,
        cells=resolved,
        requests=len(to_fetch),
        cached_cells=reused,
        warnings=warnings,
    )


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
