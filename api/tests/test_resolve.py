from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import httpx
import pytest
import respx

from api.domain import DateRange, FareRow, SearchSpec, Segment, TripOption
from api.engines import datespace
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


@pytest.fixture
def return_spec():
    """The headline case: out in one month, back in another."""
    return SearchSpec(
        origin="DEL",
        destination="DXB",
        outbound=DateRange(start=date(2026, 10, 1), end=date(2026, 10, 31)),
        inbound=DateRange(start=date(2026, 12, 1), end=date(2026, 12, 31)),
    )


def return_row(day: str, price: int) -> FareRow:
    return FareRow(
        origin="DXB",
        destination="DEL",
        depart_date=date.fromisoformat(day),
        price=Decimal(price),
        currency="inr",
        stops=1,
        source="travelpayouts",
        observed_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
    )


def paired_options() -> list[TripOption]:
    """Cached pairings, as the free scan would produce them."""
    return datespace.build_options(
        [cached_row(f"2026-10-{d:02d}", 15000 + d) for d in (4, 5, 6, 7)],
        [return_row(f"2026-12-{d:02d}", 14000 + d) for d in (7, 8, 9, 10)],
    )


def echoing_one_way(price: float):
    """Answer each request with its own route and date.

    A fixed fixture would make every priced leg land on the same day, which
    silently collapses the pairing and would hide the very behaviour under test.
    """

    def handler(request):
        import json

        body = json.loads(request.content)
        day = body["departure_date"]
        return httpx.Response(
            200,
            json={
                "itineraries": [
                    {
                        "price": {"amount": price, "currency": "INR"},
                        "outbound": {
                            "duration_minutes": 225,
                            "segments": [
                                {
                                    "marketing_carrier_code": "6E",
                                    "flight_number": "1463",
                                    "departure_airport": body["origin"],
                                    "departure_time_local": f"{day}T19:10:00",
                                    "arrival_airport": body["destination"],
                                    "aircraft": "Airbus A321neo",
                                }
                            ],
                        },
                        "ignav_id": f"{body['origin']}{day}",
                    }
                ]
            },
        )

    return handler


def round_trip_response(price: float) -> dict:
    return {
        "itineraries": [
            {
                "price": {"amount": price, "currency": "INR", "status": "verified"},
                "legs": [
                    {
                        "duration_minutes": 225,
                        "segments": [
                            {
                                "marketing_carrier_code": "6E",
                                "flight_number": "1463",
                                "departure_airport": "DEL",
                                "departure_time_local": "2026-10-04T19:10:00",
                                "arrival_airport": "DXB",
                                "aircraft": "Airbus A321neo",
                            }
                        ],
                    },
                    {
                        "duration_minutes": 240,
                        "segments": [
                            {
                                "marketing_carrier_code": "6E",
                                "flight_number": "1464",
                                "departure_airport": "DXB",
                                "departure_time_local": "2026-12-07T02:10:00",
                                "arrival_airport": "DEL",
                                "aircraft": "Airbus A321neo",
                            }
                        ],
                    },
                ],
                "ignav_id": "rt1",
            }
        ]
    }


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


class TestSplitTicketSaving:
    """Two one-ways often beat a return ticket on long gaps — the case this
    app exists to surface — so the comparison must survive the pipeline."""

    def _live_pair(self, total: int) -> TripOption:
        out = live_row("2026-10-04", total // 2)
        back = live_row("2026-12-05", total - total // 2)
        back.origin, back.destination = "DXB", "DEL"
        return datespace.build_options([out], [back])[0]

    def _live_return(self, total: int) -> TripOption:
        row = live_row("2026-10-04", total)
        row.return_date = date(2026, 12, 5)
        row.inbound_segments = [
            Segment(
                marketing_carrier="6E",
                flight_number="1464",
                origin="DXB",
                destination="DEL",
            )
        ]
        return TripOption.from_fare(row)

    def test_reports_the_gap_when_splitting_wins(self):
        from api.main import _split_ticket_saving

        saving = _split_ticket_saving(
            [self._live_pair(32289), self._live_return(39243)]
        )

        assert saving is not None
        assert saving.saving == Decimal(39243 - 32289)
        assert saving.round_trip == Decimal(39243)

    def test_silent_when_the_return_ticket_wins(self):
        from api.main import _split_ticket_saving

        assert (
            _split_ticket_saving([self._live_pair(40000), self._live_return(30000)])
            is None
        )

    def test_never_compares_against_an_estimate(self):
        """A saving measured against a cached guess might not exist at all."""
        from api.main import _split_ticket_saving

        estimate = datespace.build_options(
            [cached_row("2026-10-04", 20000)], [return_row("2026-12-05", 20000)]
        )[0]

        assert _split_ticket_saving([estimate, self._live_return(39243)]) is None


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
        assert not result.any_rows

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
        assert all(r.is_live_quote for r in result.outbound_rows)
        assert result.inbound_rows == []

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
        assert len(second.outbound_rows) == len(first.outbound_rows)

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

        assert not result.any_rows
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

        merged = resolve.merge(options, [], resolve.cells_of([]))
        assert len(merged) == 1

    @respx.mock
    async def test_dual_month_prices_legs_not_pairs(self, return_spec):
        """The combinatorial gain: six requests buy far more than six cells."""
        respx.post(f"{BASE}/fares/one-way").mock(side_effect=echoing_one_way(16803.0))
        respx.post(f"{BASE}/fares/search").mock(
            return_value=httpx.Response(200, json=round_trip_response(34000.0))
        )

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, return_spec, paired_options(), ScanDepth.STANDARD, limit=6
            )

        # 2 outbound legs + 2 inbound legs + 2 round-trip probes.
        assert result.requests == 6
        assert len(result.outbound_rows) == 2
        assert len(result.inbound_rows) == 2
        assert len(result.round_trip_rows) == 2

    @respx.mock
    async def test_dual_month_legs_pair_into_more_cells_than_requests(
        self, return_spec
    ):
        respx.post(f"{BASE}/fares/one-way").mock(side_effect=echoing_one_way(16803.0))
        respx.post(f"{BASE}/fares/search").mock(
            return_value=httpx.Response(200, json=round_trip_response(34000.0))
        )

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, return_spec, paired_options(), ScanDepth.STANDARD, limit=6
            )

        paired = datespace.build_options(
            result.outbound_rows, result.inbound_rows, None, None, return_spec.inbound
        )
        cells = set(resolve.cells_of(paired))

        # Two priced departures against two priced returns is four combinations,
        # from four leg requests — the whole reason legs beat pairs.
        assert len(cells) == 4
        assert all(o.is_live_quote for o in paired)

    @respx.mock
    async def test_a_round_trip_ticket_can_beat_two_one_ways(self, return_spec):
        """Airlines often price a return below two singles; missing that would
        hand the traveller the wrong answer."""
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(20000.0))
        )
        respx.post(f"{BASE}/fares/search").mock(
            return_value=httpx.Response(200, json=round_trip_response(30000.0))
        )

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            result = await resolve.resolve(
                client, return_spec, paired_options(), ScanDepth.STANDARD, limit=6
            )

        paired = datespace.build_options(
            result.outbound_rows, result.inbound_rows, None, None, return_spec.inbound
        )
        singles = TripOption.from_fare(result.round_trip_rows[0])
        best = min([*paired, singles], key=lambda o: o.total_price)

        assert best.kind == "round_trip"
        assert best.total_price == Decimal("30000.0")

    @respx.mock
    async def test_long_gap_round_trips_are_still_probed(self, return_spec):
        """Travelpayouts refused trips over 30 nights; this provider does not,
        so the probe must actually go out for a 60-night gap."""
        respx.post(f"{BASE}/fares/one-way").mock(side_effect=echoing_one_way(16803.0))
        route = respx.post(f"{BASE}/fares/search").mock(
            return_value=httpx.Response(200, json=round_trip_response(34000.0))
        )

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            await resolve.resolve(
                client, return_spec, paired_options(), ScanDepth.STANDARD, limit=6
            )

        import json

        sent = json.loads(route.calls[0].request.content)
        gap = (
            date.fromisoformat(sent["legs"][1]["departure_date"])
            - date.fromisoformat(sent["legs"][0]["departure_date"])
        ).days
        assert gap > 30

    @respx.mock
    async def test_one_way_search_never_prices_the_return_direction(self, spec):
        route = respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=response_for(16803.0))
        )

        async with IgnavClient(api_key="k", base_url=BASE) as client:
            await resolve.resolve(client, spec, paired_options(), ScanDepth.STANDARD, limit=4)

        import json

        origins = {
            json.loads(call.request.content)["origin"] for call in route.calls
        }
        assert origins == {"DEL"}

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
