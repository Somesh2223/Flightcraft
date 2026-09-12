"""Fareloom HTTP API."""
from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from api import config
from api.domain import SearchSpec
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from api.engines import datespace, history
from api.jobs import popular
from api.pipeline import filters
from api.storage import db
from api.pipeline.scan import ScanDepth, attribute_cells, estimate_calls, scan
from api.providers.demo import DemoClient
from api.providers.travelpayouts import TravelpayoutsClient, TravelpayoutsError
from api.reference import carriers
from api.schemas import CalendarCell, SearchRequest, SearchResponse, TripOptionOut

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

    try:
        async with (DemoClient() if config.DEMO_MODE else TravelpayoutsClient()) as client:
            result = await scan(spec, client, request.depth)
            inbound_rows = result.inbound if spec.is_return else None
            unfiltered = datespace.collapse_to_best_per_cell(
                assemble(result.outbound, inbound_rows)
            )

            if request.depth is ScanDepth.DEEP and spec.is_return:
                cells = [
                    (o.depart_date, o.return_date)
                    for o in unfiltered
                    if o.return_date is not None
                ]
                extra, calls, warnings = await attribute_cells(client, spec, cells)
                if extra:
                    result.outbound.extend(extra)
                    unfiltered = datespace.collapse_to_best_per_cell(
                        assemble(result.outbound, inbound_rows)
                    )
                result.provider_calls += calls
                result.warnings.extend(warnings)
    except TravelpayoutsError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"upstream provider error: {exc}"
        ) from exc

    filter_set = request.to_filters()

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

    outcome = filters.apply(assemble(kept_outbound, kept_inbound), filter_set)
    kept = datespace.collapse_to_best_per_cell(outcome.kept)
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
        needs_deep_scan=(
            removed.get("unknown_airline", 0) > 0 and request.depth is not ScanDepth.DEEP
        ),
        demo_mode=config.DEMO_MODE,
        observations_recorded=recorded,
        warnings=result.warnings,
    )


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


def _cheapest_per_depart_date(options: list) -> list:
    best: dict = {}
    for option in options:
        current = best.get(option.depart_date)
        if current is None or option.total_price < current.total_price:
            best[option.depart_date] = option
    return sorted(best.values(), key=lambda o: o.depart_date)
