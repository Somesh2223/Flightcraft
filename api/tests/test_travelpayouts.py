from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from api.providers.travelpayouts import (
    TravelpayoutsClient,
    TravelpayoutsError,
    booking_link,
)

BASE = "https://api.travelpayouts.com"


@pytest.fixture
def client():
    return TravelpayoutsClient(token="test-token", base_url=BASE)


class TestBookingLink:
    def test_round_trip_encodes_both_dates_and_passengers(self):
        link = booking_link(
            "DEL", "LHR", date(2026, 11, 20), date(2026, 12, 5), 2, marker="12345"
        )
        assert link == "https://www.aviasales.com/search/DEL2011LHR05122?marker=12345"

    def test_one_way_omits_the_return_segment(self):
        link = booking_link("DEL", "BOM", date(2026, 1, 9), marker="12345")
        assert link == "https://www.aviasales.com/search/DEL0901BOM1?marker=12345"

    def test_marker_omitted_when_unset(self):
        link = booking_link("DEL", "BOM", date(2026, 1, 9), marker="")
        assert link == "https://www.aviasales.com/search/DEL0901BOM1"


class TestParsing:
    # respx.mock must decorate each test, not the class: as a class decorator it
    # replaces the class object and pytest stops collecting it entirely, so the
    # suite passes by running nothing.
    @respx.mock
    async def test_month_matrix_reads_stops_and_found_at(self, client):
        respx.get(f"{BASE}/v2/prices/month-matrix").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "origin": "DEL",
                            "destination": "LHR",
                            "depart_date": "2026-11-03",
                            "return_date": "",
                            "number_of_changes": 1,
                            "value": 42150,
                            "found_at": "2026-09-10T04:12:00+04:00",
                            "distance": 6704,
                            "actual": True,
                        }
                    ],
                },
            )
        )

        async with client as c:
            rows = await c.month_matrix("DEL", "LHR", date(2026, 11, 1))

        assert len(rows) == 1
        row = rows[0]
        assert row.price == Decimal("42150")
        assert row.stops == 1
        assert row.depart_date == date(2026, 11, 3)
        assert row.return_date is None
        assert row.airline is None
        assert row.observed_at.isoformat() == "2026-09-10T04:12:00+04:00"

    @respx.mock
    async def test_calendar_reads_airline_and_flight_number(self, client):
        respx.get(f"{BASE}/v1/prices/calendar").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "2026-11-03": {
                            "origin": "DEL",
                            "destination": "LHR",
                            "price": 39900,
                            "transfers": 0,
                            "airline": "AI",
                            "flight_number": 111,
                            "departure_at": "2026-11-03T13:30:00Z",
                            "return_at": "",
                            "expires_at": "2026-09-13T12:00:00Z",
                        }
                    },
                },
            )
        )

        async with client as c:
            rows = await c.calendar("DEL", "LHR", date(2026, 11, 1))

        row = rows[0]
        assert row.airline == "AI"
        assert row.flight_number == "AI111"
        assert row.stops == 0
        assert row.return_date is None

    @respx.mock
    async def test_cheap_takes_stop_count_from_the_grouping_key(self, client):
        respx.get(f"{BASE}/v1/prices/cheap").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "LHR": {
                            "0": {
                                "price": 51000,
                                "airline": "AI",
                                "flight_number": 111,
                                "departure_at": "2026-11-03T13:30:00Z",
                            },
                            "1": {
                                "price": 38000,
                                "airline": "EK",
                                "flight_number": 511,
                                "departure_at": "2026-11-03T04:30:00Z",
                            },
                        }
                    },
                },
            )
        )

        async with client as c:
            rows = await c.cheap("DEL", "LHR", date(2026, 11, 3))

        by_stops = {row.stops: row for row in rows}
        assert by_stops[0].airline == "AI"
        assert by_stops[1].airline == "EK"
        assert by_stops[1].price == Decimal("38000")

    @respx.mock
    async def test_duration_is_captured_from_matrix_rows(self, client):
        respx.get(f"{BASE}/v2/prices/month-matrix").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "origin": "DEL",
                            "destination": "DXB",
                            "depart_date": "2026-10-04",
                            "return_date": "",
                            "number_of_changes": 1,
                            "value": 15463,
                            "duration": 750,
                            "distance": 2184,
                            "found_at": "2026-09-06T15:43:32Z",
                        }
                    ],
                },
            )
        )

        async with client as c:
            rows = await c.month_matrix("DEL", "DXB", date(2026, 10, 1))

        assert rows[0].duration_minutes == 750
        assert rows[0].distance_km == 2184

    @respx.mock
    async def test_cheap_prefers_outbound_duration_over_round_trip_total(self, client):
        """`duration` covers the whole round trip and would overstate one leg."""
        respx.get(f"{BASE}/v1/prices/cheap").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": {
                        "DXB": {
                            "1": {
                                "price": 26707,
                                "airline": "AI",
                                "flight_number": 2555,
                                "departure_at": "2026-10-03T07:10:00+05:30",
                                "return_at": "2026-10-31T22:05:00+04:00",
                                "duration": 2415,
                                "duration_to": 455,
                                "duration_back": 450,
                            }
                        }
                    },
                },
            )
        )

        async with client as c:
            rows = await c.cheap("DEL", "DXB", date(2026, 10, 3))

        assert rows[0].duration_minutes == 455
        assert rows[0].is_round_trip

    @respx.mock
    async def test_round_trip_rows_keep_their_return_date(self, client):
        respx.get(f"{BASE}/v2/prices/month-matrix").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "origin": "DEL",
                            "destination": "LHR",
                            "depart_date": "2026-11-03",
                            "return_date": "2026-11-17",
                            "number_of_changes": 0,
                            "value": 71000,
                            "found_at": "2026-09-10T04:12:00+04:00",
                            "actual": True,
                        }
                    ],
                },
            )
        )

        async with client as c:
            rows = await c.month_matrix("DEL", "LHR", date(2026, 11, 1))

        assert rows[0].is_round_trip
        assert rows[0].nights == 14

    @respx.mock
    async def test_malformed_entries_are_skipped_not_fatal(self, client):
        respx.get(f"{BASE}/v2/prices/month-matrix").mock(
            return_value=httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {"origin": "DEL", "depart_date": "not-a-date", "value": 100},
                        {"origin": "DEL", "destination": "LHR", "value": 500},
                        None,
                        {
                            "origin": "DEL",
                            "destination": "LHR",
                            "depart_date": "2026-11-03",
                            "value": 42150,
                            "number_of_changes": 0,
                        },
                    ],
                },
            )
        )

        async with client as c:
            rows = await c.month_matrix("DEL", "LHR", date(2026, 11, 1))

        assert len(rows) == 1

    @respx.mock
    async def test_month_matrix_range_clips_to_the_requested_window(self, client):
        from api.domain import DateRange

        def payload(request):
            month = request.url.params["month"]
            day = "2026-11-30" if month.startswith("2026-11") else "2026-12-02"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": [
                        {
                            "origin": "DEL",
                            "destination": "LHR",
                            "depart_date": day,
                            "value": 40000,
                            "number_of_changes": 0,
                        },
                        {
                            "origin": "DEL",
                            "destination": "LHR",
                            "depart_date": "2026-11-05",
                            "value": 39000,
                            "number_of_changes": 0,
                        },
                    ],
                },
            )

        respx.get(f"{BASE}/v2/prices/month-matrix").mock(side_effect=payload)

        span = DateRange(start=date(2026, 11, 28), end=date(2026, 12, 3))
        async with client as c:
            rows = await c.month_matrix_range("DEL", "LHR", span)

        assert sorted(r.depart_date for r in rows) == [
            date(2026, 11, 30),
            date(2026, 12, 2),
        ]


class TestErrors:
    @respx.mock
    async def test_api_level_failure_becomes_a_typed_error(self, client):
        respx.get(f"{BASE}/v1/prices/calendar").mock(
            return_value=httpx.Response(
                200, json={"success": False, "error": "invalid origin"}
            )
        )

        with pytest.raises(TravelpayoutsError, match="invalid origin"):
            async with client as c:
                await c.calendar("XXX", "LHR", date(2026, 11, 1))

    @respx.mock
    async def test_missing_token_fails_with_a_useful_message(self):
        with pytest.raises(TravelpayoutsError, match="TRAVELPAYOUTS_TOKEN"):
            async with TravelpayoutsClient(token="", base_url=BASE) as c:
                await c.calendar("DEL", "LHR", date(2026, 11, 1))
