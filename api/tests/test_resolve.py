from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from api.domain import DateRange, FareRow, SearchSpec, Segment, TripOption
from api.pipeline import resolve
from api.pipeline.filters import FilterSet
from api.pipeline.scan import ScanDepth
from api.providers.ignav import IgnavClient

BASE = "https://ignav.com/api"


@pytest.fixture(autouse=True)
def clean_cache():
    resolve.clear_cache()
    yield
    resolve.clear_cache()


@pytest.fixture
def spec():
    return SearchSpec(
        origin="DEL",
        destination="DXB",
        outbound=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 31)),
    )


def cached_row(day: str, price: int) -> FareRow:
    return FareRow(
        origin="DEL",
        destination="DXB",
        depart_date=date.fromisoformat(day),
        price=Decimal(price),
        currency="inr",
        stops=1,
        source="travelpayouts",
        observed_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )


def live_row(day: str, price: int, carrier: str = "6E") -> FareRow:
    return FareRow(
        origin="DEL",
        destination="DXB",
        depart_date=date.fromisoformat(day),
        price=Decimal(price),
        currency="inr",
        stops=0,
        airline=carrier,
        source="ignav",
        observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        outbound_segments=[
            Segment(
                marketing_carrier=carrier,
                flight_number="1463",
                origin="DEL",
                destination="DXB",
                aircraft="Airbus A321neo",
            )
        ],
    )


def response_for(price: float, carrier: str = "6E") -> dict:
    return {
        "itineraries": [
            {
                "price": {"amount": price, "currency": "INR", "status": "verified"},
                "outbound": {
                    "duration_minutes": 225,
                    "segments": [
                        {
                            "marketing_carrier_code": carrier,
                            "flight_number": "1463",
                            "operating_carrier_name": "IndiGo",
                            "departure_airport": "DEL",
                            "departure_time_local": "2026-10-04T19:10:00",
                            "arrival_airport": "DXB",
                            "aircraft": "Airbus A321neo",
                        }
                    ],
                },
                "ignav_id": "abc",
            }
        ]
    }


class TestCandidateSelection:
    def test_spends_the_budget_on_the_cheapest_cells(self):
        options = [
            TripOption.from_fare(cached_row("2026-10-04", 15000)),
            TripOption.from_fare(cached_row("2026-10-05", 16000)),
            TripOption.from_fare(cached_row("2026-10-06", 17000)),
        ]

        cells = resolve.candidate_cells(options, limit=2)

        assert cells == [(date(2026, 10, 4), None), (date(2026, 10, 5), None)]

    def test_a_repeated_cell_is_not_paid_for_twice(self):
        options = [
            TripOption.from_fare(cached_row("2026-10-04", 15000)),
            TripOption.from_fare(cached_row("2026-10-04", 15500)),
            TripOption.from_fare(cached_row("2026-10-05", 16000)),
        ]

        assert len(resolve.candidate_cells(options, limit=3)) == 2


class TestMerge:
    """A cached price that is cheaper but unbookable is worse than none."""

    def test_live_quote_replaces_the_cached_estimate_for_its_cell(self):
        cached = [
            TripOption.from_fare(cached_row("2026-10-04", 15000)),
            TripOption.from_fare(cached_row("2026-10-05", 16000)),
        ]
        live = [TripOption.from_fare(live_row("2026-10-04", 16800))]

        merged = resolve.merge(cached, live, [(date(2026, 10, 4), None)])

        by_date = {o.depart_date: o for o in merged}
        assert by_date[date(2026, 10, 4)].total_price == Decimal(16800)
        assert by_date[date(2026, 10, 4)].is_live_quote

    def test_unresolved_cells_keep_their_estimate(self):
        cached = [
            TripOption.from_fare(cached_row("2026-10-04", 15000)),
            TripOption.from_fare(cached_row("2026-10-05", 16000)),
        ]
        live = [TripOption.from_fare(live_row("2026-10-04", 16800))]

        merged = resolve.merge(cached, live, [(date(2026, 10, 4), None)])

        estimate = next(o for o in merged if o.depart_date == date(2026, 10, 5))
        assert not estimate.is_live_quote
        assert estimate.total_price == Decimal(16000)

    def test_merged_options_stay_price_ordered(self):
        cached = [TripOption.from_fare(cached_row(f"2026-10-{d:02d}", 20000 - d * 100)) for d in range(5, 12)]
        live = [TripOption.from_fare(live_row("2026-10-20", 12000))]

        merged = resolve.merge(cached, live, [(date(2026, 10, 20), None)])

        assert [o.total_price for o in merged] == sorted(o.total_price for o in merged)


class TestResolve:
    async def test_quick_depth_spends_nothing(self, spec):
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(client, spec, options, ScanDepth.QUICK)

        assert result.requests == 0
        assert result.rows == []

    @respx.mock
    async def test_standard_depth_prices_the_shortlist(self, spec):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(16803.0))
        )
        options = [
            TripOption.from_fare(cached_row("2026-10-04", 15000)),
            TripOption.from_fare(cached_row("2026-10-05", 16000)),
        ]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, spec, options, ScanDepth.STANDARD, limit=2
            )

        assert result.requests == 2
        assert len(result.cells) == 2
        assert all(r.is_live_quote for r in result.rows)

    @respx.mock
    async def test_repeating_a_search_reuses_quotes_instead_of_paying_again(self, spec):
        route = respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(16803.0))
        )
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            first = await resolve.resolve(client, spec, options, ScanDepth.STANDARD, limit=1)
            second = await resolve.resolve(client, spec, options, ScanDepth.STANDARD, limit=1)

        assert (first.requests, second.requests) == (1, 0)
        assert second.cached_cells == 1
        assert route.call_count == 1
        assert len(second.rows) == len(first.rows)

    @respx.mock
    async def test_different_filters_are_priced_separately(self, spec):
        route = respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(16803.0))
        )
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            await resolve.resolve(client, spec, options, ScanDepth.STANDARD, limit=1)
            await resolve.resolve(
                client,
                spec,
                options,
                ScanDepth.STANDARD,
                FilterSet(max_stops=0),
                limit=1,
            )

        assert route.call_count == 2

    @respx.mock
    async def test_a_failed_cell_keeps_its_cached_estimate(self, spec):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(400, text="nope")
        )
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, spec, options, ScanDepth.STANDARD, limit=1
            )

        assert result.cells == []
        assert result.rows == []
        assert any("live pricing failed" in w for w in result.warnings)

    @respx.mock
    async def test_an_empty_answer_does_not_blank_the_date(self, spec):
        """No flights found must leave the estimate standing, not erase the day."""
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json={"itineraries": []})
        )
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, spec, options, ScanDepth.STANDARD, limit=1
            )

        assert result.cells == []
        merged = resolve.merge(options, [], result.cells)
        assert len(merged) == 1

    @respx.mock
    async def test_user_filters_are_pushed_to_the_provider(self, spec):
        route = respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(16803.0))
        )
        options = [TripOption.from_fare(cached_row("2026-10-04", 15000))]

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            await resolve.resolve(
                client,
                spec,
                options,
                ScanDepth.STANDARD,
                FilterSet(max_stops=0, include_airlines={"6e"}),
                limit=1,
            )

        import json

        sent = json.loads(route.calls[0].request.content)
        assert sent["max_stops"] == 0
        assert sent["airlines_include"] == ["6E"]
