"""Flightcraft HTTP API."""
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
from api.engines import datespace, history, offers, points
from api.jobs import popular
from api.pipeline import filters, resolve
from api.pipeline.filters import FilterSet
from api.pipeline.scan import ScanDepth, dates_in_window, estimate_calls, scan
from api.providers.demo import DemoClient
from api.providers.ignav import IgnavClient, IgnavError
from api.providers.travelpayouts import TravelpayoutsClient, TravelpayoutsError
from api.reference import carriers, loyalty
from api.schemas import (
    BookingLinksRequest,
    CalendarCell,
    MatrixCell,
    PointsAssessRequest,
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


# How many return dates each departure day carries into the response, so that
# opening a day on the grid is a genuine choice rather than a single suggestion.
RETURNS_PER_DATE = 6

# A month against a month is roughly 900 pairs, which draws fine. Beyond this a
# heatmap stops being readable long before it stops being renderable.
MATRIX_CELL_LIMIT = 2_500

app = FastAPI(title="Flightcraft", version="0.1.0", lifespan=lifespan)

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
    resolution: resolve.ResolveResult | None = None
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
        live_options = _live_options(resolution, spec, assemble)
        # Cells we paid to price, taken before any filtering. A date whose only
        # matching fare was filtered out must not fall back to its cached
        # estimate: we know what flies that day, and the estimate does not.
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

    # Live fares get the same row-level filtering, and for the same reason. The
    # collapse above keeps one option per date, so filtering after it would
    # judge a date by a fare the traveller already ruled out — a budget-only
    # search on a day where Gulf Air is cheapest and IndiGo also flies would
    # drop the day entirely rather than offer the IndiGo seat.
    filtered_live = live_options
    if resolution is not None and not filter_set.is_empty:
        live_out, live_removed = filters.apply_to_rows(
            resolution.outbound_rows, filter_set
        )
        live_in, live_in_removed = (
            filters.apply_to_rows(resolution.inbound_rows, filter_set)
            if spec.is_return
            else ([], {})
        )
        live_rt, live_rt_removed = filters.apply_to_rows(
            resolution.round_trip_rows, filter_set
        )
        filtered_live = _assemble_live(live_out, live_in, live_rt, spec, assemble)
        for counts in (live_removed, live_in_removed, live_rt_removed):
            for reason, count in counts.items():
                removed[reason] = removed.get(reason, 0) + count

    outcome = filters.apply(
        resolve.merge(cached_options, filtered_live, resolved_cells), filter_set
    )
    # Measured before collapsing, because collapsing keeps only the cheapest
    # option per cell — which deletes the round-trip ticket exactly when the
    # split beats it, leaving nothing to compare against.
    saving = _split_ticket_saving(outcome.kept)

    # Offers are applied before ranking, not after. Twelve percent off an 18,000
    # fare beats a 16,287 one, so ranking on the headline price would point at
    # the wrong day. No seller is known at this stage, so seller-scoped offers
    # deliberately stay out until a booking link names one.
    live_offers = offers.active(request.offers)
    applied: dict[int, offers.Application] = {}
    if live_offers:
        for option in outcome.kept:
            best = offers.best_offer(live_offers, option.total_price)
            if best is not None:
                applied[id(option)] = best

    def payable(option) -> Decimal:
        found = applied.get(id(option))
        return found.effective_price if found else option.total_price

    kept = datespace.bookable_first(
        datespace.collapse_to_best_per_cell(outcome.kept), payable
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
            effective_price=payable(option),
            offer_label=applied[id(option)].label if id(option) in applied else None,
        )
        for option in _cheapest_per_depart_date(kept, payable)
    ]

    matrix: list[MatrixCell] = []
    if spec.is_return:
        pairs = [o for o in kept if o.return_date is not None]
        if len(pairs) > MATRIX_CELL_LIMIT:
            result.warnings.append(
                f"The date grid covers {len(pairs):,} departure/return pairs, too "
                f"many to draw at once. Narrowing the ranges or the trip length "
                "brings it back."
            )
        else:
            matrix = [
                MatrixCell(
                    depart_date=o.depart_date,
                    return_date=o.return_date,
                    nights=o.nights if o.nights is not None else 0,
                    price=o.total_price,
                    effective_price=payable(o),
                    stops=o.max_stops,
                    airline=o.outbound.airline,
                    is_live_quote=o.is_live_quote,
                )
                for o in pairs
            ]

    # Every day drawn on the calendar has to be openable, and opening one has to
    # show a real choice of return dates rather than the single cheapest. The
    # results list is capped globally, so without this a day whose best option
    # ranks below the cap opens to an empty list, and a day that scrapes in
    # opens to exactly one option — neither of which is choosing a return.
    shown = kept[: request.limit]
    already = {id(o) for o in shown}
    shown = datespace.bookable_first(
        shown
        + [
            o
            for o in _best_per_depart_date(kept, payable, RETURNS_PER_DATE)
            if id(o) not in already
        ],
        payable,
    )

    async with db.session() as store:
        recorded = await history.record(store, result.outbound + result.inbound)
        contexts = await _price_contexts(store, spec, shown)

    results = [
        TripOptionOut.of(
            o,
            spec.passengers,
            contexts.get(id(o)),
            request.wallet,
            applied.get(id(o)),
        )
        for o in shown
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
        matrix=matrix,
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


@app.get("/api/loyalty/programs")
async def list_programs() -> dict:
    return {
        "programs": [
            {
                "code": p.code,
                "name": p.name,
                "label": p.label,
                "airline": p.airline,
                "books_alliance": p.books_alliance,
                "currency_pool": p.currency_pool,
                "books_award_seats": p.books_award_seats,
            }
            for p in loyalty.all_programs()
        ]
    }


@app.post("/api/points/assess", response_model=points.Assessment)
async def assess_points(request: PointsAssessRequest) -> points.Assessment:
    """Value a redemption the traveller is looking at, in the context of the scan.

    The award price comes from the traveller because no free source publishes
    one, and a guessed number here would send someone to spend points they
    cannot get back.
    """
    return points.assess(
        request.quote,
        request.wallet,
        cash_price=request.cash_price,
        best_cash_alternative=request.best_cash_alternative,
        best_cash_date=request.best_cash_date,
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
    # Live requests are the only part that costs money, so they are reported
    # separately rather than folded into a single opaque number.
    return {
        "dates_in_window": dates_in_window(spec),
        "depths": {
            depth.value: {
                "cached_calls": estimate_calls(spec, depth),
                "live_requests": min(
                    resolve.RESOLVE_LIMITS.get(depth, 0), dates_in_window(spec)
                ),
            }
            for depth in ScanDepth
        },
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


def _assemble_live(outbound, inbound, round_trips, spec, assemble) -> list:
    """Options from live rows, collapsed per kind rather than per cell."""
    return datespace.collapse_to_best_per_cell(
        assemble(outbound, inbound if spec.is_return else None)
    ) + datespace.collapse_to_best_per_cell(
        [TripOption.from_fare(r) for r in round_trips]
    )


def _live_options(resolution, spec, assemble) -> list:
    return _assemble_live(
        resolution.outbound_rows,
        resolution.inbound_rows,
        resolution.round_trip_rows,
        spec,
        assemble,
    )


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


def _cheapest_per_depart_date(options: list, price_of=None) -> list:
    price = price_of or (lambda o: o.total_price)
    best: dict = {}
    for option in options:
        current = best.get(option.depart_date)
        if current is None or price(option) < price(current):
            best[option.depart_date] = option
    return sorted(best.values(), key=lambda o: o.depart_date)


def _best_per_depart_date(options: list, price_of=None, per_date: int = 1) -> list:
    """The cheapest few options for each departure date.

    More than one, because on a two-range search the return date is the thing
    being chosen; offering a single cheapest return per day is a recommendation,
    not a choice.
    """
    price = price_of or (lambda o: o.total_price)
    grouped: dict = {}
    for option in sorted(options, key=price):
        bucket = grouped.setdefault(option.depart_date, [])
        if len(bucket) < per_date:
            bucket.append(option)
    return [o for day in sorted(grouped) for o in grouped[day]]
