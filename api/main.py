"""Fareloom HTTP API."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from datetime import datetime, timezone
from decimal import Decimal

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from api import config
from api.domain import DateRange, FareRow, SearchSpec, TripOption
from api.engines import datespace, history
from api.jobs import popular
from api.pipeline import filters, resolve
from api.pipeline.filters import FilterSet
from api.pipeline.scan import ScanDepth, estimate_calls, scan
from api.providers.demo import DemoClient
from api.providers.ignav import IgnavClient, IgnavError
from api.providers.travelpayouts import TravelpayoutsClient, TravelpayoutsError
from api.reference import carriers
from api.schemas import (
    BookingLinksRequest,
    CalendarCell,
    ResolveDateRequest,
    SearchRequest,
    SearchResponse,
    SplitTicketSaving,
    TripOptionOut,
)
from api.storage import db

@asynccontextmanager
async def lifespan(_: FastAPI):
    await db.init_db()

    scheduler = None
    if config.ENABLE_BACKGROUND_SCANS and not config.DEMO_MODE:
        scheduler = AsyncIOScheduler()
        popular.schedule(scheduler)
        scheduler.start()

    yield

    if scheduler is not None:
        scheduler.shutdown(wait=False)
    await db.dispose()


app = FastAPI(title="Fareloom", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "demo_mode": config.DEMO_MODE,
        "travelpayouts_configured": bool(config.TRAVELPAYOUTS_TOKEN),
        "marker_configured": bool(config.TRAVELPAYOUTS_MARKER),
        "live_pricing_configured": bool(config.IGNAV_API_KEY),
        "default_currency": config.DEFAULT_CURRENCY,
    }


@app.get("/api/carriers")
async def list_carriers() -> dict:
    return {
        "carriers": [
            {
                "code": c.code,
                "name": c.name,
                "class": c.carrier_class.value,
                "alliance": c.alliance,
                "country": c.country,
            }
            for c in carriers.all_carriers()
        ]
    }


@app.post("/api/search", response_model=SearchResponse)
async def search(request: SearchRequest) -> SearchResponse:
    try:
        spec = SearchSpec(
            origin=request.origin,
            destination=request.destination,
            outbound=request.outbound,
            inbound=request.inbound,
            min_nights=request.min_nights,
            max_nights=request.max_nights,
            currency=request.currency,
            trip_class=request.trip_class,
            passengers=request.passengers,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def assemble(outbound: list, inbound: list | None) -> list:
        return datespace.build_options(
            outbound, inbound, spec.min_nights, spec.max_nights, spec.inbound
        )

    filter_set = request.to_filters()

    try:
        # Stage one: the free cached landscape. Wide, cheap, and vague — it maps
        # where the cheap dates are without naming a single airline.
        async with (DemoClient() if config.DEMO_MODE else TravelpayoutsClient()) as client:
            result = await scan(spec, client, request.depth)
            inbound_rows = result.inbound if spec.is_return else None
            unfiltered = datespace.collapse_to_best_per_cell(
                assemble(result.outbound, inbound_rows)
            )
    except TravelpayoutsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"upstream provider error: {exc}"
        ) from exc

    # Stage two: buy exact answers for the best cells. Every date the user asked
    # about still appears; the ones we paid for become real, bookable offers.
    live_options: list = []
    resolved_cells: list = []
    live_requests = 0
    live_cache_hits = 0
    if config.IGNAV_API_KEY and not config.DEMO_MODE:
        async with IgnavClient() as ignav:
            resolution = await resolve.resolve(
                ignav, spec, unfiltered, request.depth, filter_set
            )
        # Live legs are paired by exactly the same rules as the cached ones, so
        # a two-month trip built from two real one-ways competes directly with
        # the single round-trip tickets probed alongside it.
        # Collapsed per kind, not per cell: a round-trip ticket and a pair of
        # one-ways covering the same dates are different products, and keeping
        # only the cheaper would erase the comparison between them.
        live_options = datespace.collapse_to_best_per_cell(
            assemble(
                resolution.outbound_rows,
                resolution.inbound_rows if spec.is_return else None,
            )
        ) + datespace.collapse_to_best_per_cell(
            [TripOption.from_fare(r) for r in resolution.round_trip_rows]
        )
        resolved_cells = resolve.cells_of(live_options)
        live_requests = resolution.requests
        live_cache_hits = resolution.cached_cells
        result.provider_calls += resolution.requests
        result.warnings.extend(resolution.warnings)
        unfiltered = resolve.merge(unfiltered, live_options, resolved_cells)
    elif filter_set.needs_aircraft:
        result.warnings.append(
            "Aircraft filters need live pricing, which is not configured. Set "
            "IGNAV_API_KEY to use them."
        )

    if not spec.is_return and result.outbound and not unfiltered:
        result.warnings.append(
            "Only round-trip fares are cached for this route and window. A "
            "round-trip price is not an answer to a one-way search, so none are "
            "shown — add a return range to use them."
        )

    # Filter the individual fares before pairing them, so a date is judged by
    # the best fare that actually satisfies the filters rather than by its
    # cheapest fare overall.
    kept_outbound, removed = filters.apply_to_rows(result.outbound, filter_set)
    if inbound_rows is not None:
        kept_inbound, removed_inbound = filters.apply_to_rows(inbound_rows, filter_set)
        removed = {
            reason: removed.get(reason, 0) + removed_inbound.get(reason, 0)
            for reason in set(removed) | set(removed_inbound)
        }
    else:
        kept_inbound = None

    cached_options = datespace.collapse_to_best_per_cell(
        assemble(kept_outbound, kept_inbound)
    )
    outcome = filters.apply(
        resolve.merge(cached_options, live_options, resolved_cells), filter_set
    )
    # Measured before collapsing, because collapsing keeps only the cheapest
    # option per cell — which deletes the round-trip ticket exactly when the
    # split beats it, leaving nothing to compare against.
    saving = _split_ticket_saving(outcome.kept)

    kept = datespace.bookable_first(
        datespace.collapse_to_best_per_cell(outcome.kept)
    )
    removed = {
        reason: removed.get(reason, 0) + outcome.removed.get(reason, 0)
        for reason in set(removed) | set(outcome.removed)
    }

    calendar = [
        CalendarCell(
            depart_date=option.depart_date,
            price=option.total_price,
            stops=option.max_stops,
            airline=option.outbound.airline,
            airline_name=(
                carriers.display_name(option.outbound.airline)
                if option.outbound.airline
                else None
            ),
            return_date=option.return_date,
        )
        for option in _cheapest_per_depart_date(kept)
    ]

    shown = kept[: request.limit]

    async with db.session() as store:
        recorded = await history.record(store, result.outbound + result.inbound)
        contexts = await _price_contexts(store, spec, shown)

    results = [
        TripOptionOut.of(o, spec.passengers, contexts.get(id(o))) for o in shown
    ]

    return SearchResponse(
        origin=spec.origin,
        destination=spec.destination,
        currency=spec.currency,
        depth=result.depth,
        provider_calls=result.provider_calls,
        cheapest=results[0] if results else None,
        results=results,
        calendar=calendar,
        total_before_filters=len(unfiltered),
        filtered_out=removed,
        # Airline and aircraft filters can only judge a date that has been priced
        # for real, so a deeper scan — which buys more dates — genuinely fixes it.
        needs_deep_scan=(
            removed.get("unknown_airline", 0) + removed.get("unknown_aircraft", 0) > 0
            and request.depth is not ScanDepth.DEEP
        ),
        demo_mode=config.DEMO_MODE,
        live_requests=live_requests,
        live_cache_hits=live_cache_hits,
        split_ticket_saving=saving,
        observations_recorded=recorded,
        warnings=result.warnings,
    )


@app.post("/api/resolve-date", response_model=list[TripOptionOut])
async def resolve_date(request: ResolveDateRequest) -> list[TripOptionOut]:
    """Turn one estimated date into real, bookable itineraries."""
    if not config.IGNAV_API_KEY:
        raise HTTPException(status_code=503, detail="live pricing is not configured")

    filters: dict = {"adults": request.passengers, "market": config.DEFAULT_MARKET}
    if request.max_stops is not None:
        filters["max_stops"] = request.max_stops
    if request.include_airlines:
        filters["airlines_include"] = sorted(c.upper() for c in request.include_airlines)

    try:
        async with IgnavClient() as client:
            # A cell with a return date is priced as one journey: that is the
            # pair the traveller pointed at, and one request answers it.
            if request.return_date is not None:
                rows = await client.round_trip(
                    request.origin,
                    request.destination,
                    request.depart_date,
                    request.return_date,
                    request.currency,
                    **filters,
                )
            else:
                rows = await client.one_way(
                    request.origin,
                    request.destination,
                    request.depart_date,
                    request.currency,
                    **filters,
                )
    except IgnavError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    options = sorted(
        (TripOption.from_fare(row) for row in rows), key=lambda o: o.total_price
    )
    return [TripOptionOut.of(o, request.passengers) for o in options]


@app.post("/api/booking-links")
async def booking_links(request: BookingLinksRequest) -> dict:
    """Where to buy one itinerary, and what each seller charges for it.

    Called on demand rather than during search: the search price and the price
    at checkout genuinely differ, and this is the one that can be paid.
    """
    if not config.IGNAV_API_KEY:
        raise HTTPException(status_code=503, detail="live pricing is not configured")

    try:
        async with IgnavClient() as client:
            links = await client.booking_links(request.provider_ref)
    except IgnavError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "links": [
            {
                "provider_name": link.provider_name,
                "provider_type": link.provider_type,
                "price": link.price,
                "currency": link.currency,
                "url": link.url,
            }
            for link in links
        ]
    }


@app.post("/api/search/estimate")
async def search_estimate(request: SearchRequest) -> dict:
    spec = SearchSpec(
        origin=request.origin,
        destination=request.destination,
        outbound=request.outbound,
        inbound=request.inbound,
        min_nights=request.min_nights,
        max_nights=request.max_nights,
        currency=request.currency,
    )
    return {
        depth.value: estimate_calls(spec, depth) for depth in ScanDepth
    }


async def _price_contexts(store, spec, options: list) -> dict[int, object]:
    """Price context per option, judged on its outbound leg.

    A paired trip's total was never observed as a single price, so the leg we
    actually have a record for is the honest thing to compare. One query covers
    every option rather than one per row.
    """
    if not options:
        return {}

    grouped: dict[bool, list] = {True: [], False: []}
    for option in options:
        grouped[option.outbound.is_round_trip].append(option)

    contexts: dict[int, object] = {}
    for round_trip, group in grouped.items():
        if not group:
            continue
        pools = await history.history_by_date(
            store,
            spec.origin,
            spec.destination,
            [o.outbound.depart_date for o in group],
            spec.currency,
            round_trip=round_trip,
        )
        for option in group:
            prices, distinct_days = pools.get(option.outbound.depart_date, ([], 0))
            contexts[id(option)] = history.summarise(
                prices, option.outbound.price, distinct_days
            )

    return contexts


def _split_ticket_saving(options: list) -> SplitTicketSaving | None:
    """Compare the best pair of one-ways against the best single return ticket.

    Airlines price a return as one product and two singles as another, and on
    long gaps the singles often win by a wide margin — the case this app exists
    to surface. Both sides must be verified fares: measuring a real price against
    a cached estimate would report a saving that might not survive checkout.
    """
    verified = [o for o in options if o.is_live_quote and o.return_date is not None]
    split = [o for o in verified if o.kind == "combined_one_ways"]
    single = [o for o in verified if o.kind == "round_trip"]
    if not split or not single:
        return None

    best_split = min(split, key=lambda o: o.total_price)
    best_single = min(single, key=lambda o: o.total_price)
    if best_split.total_price >= best_single.total_price:
        return None

    return SplitTicketSaving(
        saving=best_single.total_price - best_split.total_price,
        two_one_ways=best_split.total_price,
        round_trip=best_single.total_price,
        depart_date=best_split.depart_date,
        return_date=best_split.return_date,
    )


def _cheapest_per_depart_date(options: list) -> list:
    best: dict = {}
    for option in options:
        current = best.get(option.depart_date)
        if current is None or option.total_price < current.total_price:
            best[option.depart_date] = option
    return sorted(best.values(), key=lambda o: o.depart_date)
