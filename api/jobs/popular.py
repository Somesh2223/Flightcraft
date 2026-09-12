"""Daily background scan of popular routes, to accumulate price history.

History that only grows when someone happens to search is a poor dataset: the
routes with the most observations would be the ones already looked at, and any
route would take months to become useful. A daily sweep fixes both.

It is deliberately cheap. At QUICK depth one call returns roughly 140 dated
one-way fares for a route, so the whole list costs about one call per route per
day — a few dozen requests for a few thousand observations.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

from api import config
from api.domain import DateRange, SearchSpec
from api.engines import history
from api.pipeline.scan import ScanDepth, scan
from api.providers.travelpayouts import TravelpayoutsClient
from api.storage import db

log = logging.getLogger(__name__)

# India-first, weighted towards routes where a month of flexibility actually
# changes the price. Domestic trunk routes, Gulf hubs, and the long-haul
# destinations Indian travellers search most.
POPULAR_ROUTES: list[tuple[str, str]] = [
    ("DEL", "DXB"), ("BOM", "DXB"), ("BLR", "DXB"), ("MAA", "DXB"),
    ("DEL", "LON"), ("BOM", "LON"), ("DEL", "SIN"), ("BLR", "SIN"),
    ("DEL", "BKK"), ("BOM", "BKK"), ("DEL", "KTM"), ("DEL", "CMB"),
    ("DEL", "NYC"), ("BOM", "NYC"), ("DEL", "DOH"), ("DEL", "AUH"),
    ("DEL", "BOM"), ("DEL", "BLR"), ("DEL", "MAA"), ("DEL", "CCU"),
    ("BOM", "BLR"), ("DEL", "GOI"), ("DEL", "HYD"), ("BOM", "GOI"),
]

HORIZON_DAYS = 300


async def sweep(routes: list[tuple[str, str]] | None = None) -> int:
    """Scan every popular route once and record what comes back."""
    routes = routes or POPULAR_ROUTES
    today = date.today()
    span = DateRange(start=today + timedelta(days=1), end=today + timedelta(days=HORIZON_DAYS))

    recorded = 0
    async with TravelpayoutsClient() as client:
        for origin, destination in routes:
            try:
                spec = SearchSpec(
                    origin=origin,
                    destination=destination,
                    outbound=span,
                    currency=config.DEFAULT_CURRENCY,
                )
                result = await scan(spec, client, ScanDepth.QUICK)
                async with db.session() as store:
                    recorded += await history.record(store, result.outbound)
            except Exception:
                # One bad route must not end the sweep; the next run retries it.
                log.exception("sweep failed for %s-%s", origin, destination)

    log.info("popular-route sweep recorded %d new observations", recorded)
    return recorded


def schedule(scheduler) -> None:
    """Register the daily sweep.

    Deliberately not run at startup: a dev server restarting repeatedly would
    otherwise hammer the provider for data the previous run already has.
    """
    scheduler.add_job(
        sweep,
        "cron",
        hour=config.SWEEP_HOUR,
        minute=0,
        id="popular-route-sweep",
        replace_existing=True,
        misfire_grace_time=3600,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(sweep())
