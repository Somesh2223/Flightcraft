"""Fixtures here are trimmed copies of real Ignav responses, not invented shapes."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from api.domain import BodyType
from api.providers.ignav import IgnavClient, IgnavError, resolve_dates
from api.reference import aircraft

BASE = "https://ignav.com/api"


@pytest.fixture
def client():
    return IgnavClient(api_key="test-key", base_url=BASE)


ONE_WAY_BODY = {
    "origin": "DEL",
    "destination": "DXB",
    "departure_date": "2026-10-14",
    "itineraries": [
        {
            "price": {"amount": 16287.0, "currency": "INR", "status": "verified"},
            "outbound": {
                "carrier": "Gulf Air",
                "duration_minutes": 1150,
                "segments": [
                    {
                        "marketing_carrier_code": "GF",
                        "flight_number": "131",
                        "operating_carrier_name": "Gulf Air",
                        "departure_airport": "DEL",
                        "departure_time_local": "2026-10-14T04:55:00",
                        "departure_timezone": "Asia/Kolkata",
                        "arrival_airport": "BAH",
                        "arrival_time_local": "2026-10-14T06:50:00",
                        "duration_minutes": 265,
                        "aircraft": "Airbus A321neo",
                    },
                    {
                        "marketing_carrier_code": "GF",
                        "flight_number": "512",
                        "operating_carrier_name": "Gulf Air",
                        "departure_airport": "BAH",
                        "departure_time_local": "2026-10-14T20:05:00",
                        "arrival_airport": "DXB",
                        "arrival_time_local": "2026-10-14T22:35:00",
                        "duration_minutes": 90,
                        "aircraft": "Airbus A321neo",
                    },
                ],
            },
            "cabin_class": "economy",
            "bags": {"carry_on": 1, "checked": 1},
            "requires_self_transfer": False,
            "ignav_id": "a13ff66fcb574bbe907f4038998be6c3",
        },
        {
            "price": {"amount": 16803.0, "currency": "INR", "status": "verified"},
            "outbound": {
                "carrier": "IndiGo",
                "duration_minutes": 225,
                "segments": [
                    {
                        "marketing_carrier_code": "6E",
                        "flight_number": "1463",
                        "operating_carrier_name": "IndiGo",
                        "departure_airport": "DEL",
                        "departure_time_local": "2026-10-14T19:10:00",
                        "arrival_airport": "DXB",
                        "arrival_time_local": "2026-10-14T21:25:00",
                        "duration_minutes": 225,
                        "aircraft": "Airbus A321neo",
                    }
                ],
            },
            "cabin_class": "economy",
            "bags": {"carry_on": 1, "checked": 0},
            "requires_self_transfer": False,
            "ignav_id": "b24aa77dcb685ccfa18e5149aa9cf7d4",
        },
    ],
}


class TestParsing:
    @respx.mock
    async def test_reads_carrier_aircraft_and_bags(self, client):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=ONE_WAY_BODY)
        )

        async with client as c:
            rows = await c.one_way("DEL", "DXB", date(2026, 10, 14))

        cheapest = rows[0]
        assert cheapest.price == Decimal("16287.0")
        assert cheapest.currency == "inr"
        assert cheapest.airline == "GF"
        assert cheapest.flight_number == "GF131"
        assert cheapest.stops == 1
        assert cheapest.aircraft_types == ["Airbus A321neo", "Airbus A321neo"]
        assert cheapest.checked_bags == 1
        assert cheapest.is_live_quote

    @respx.mock
    async def test_results_are_ordered_by_price(self, client):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=ONE_WAY_BODY)
        )

        async with client as c:
            rows = await c.one_way("DEL", "DXB", date(2026, 10, 14))

        assert [r.price for r in rows] == sorted(r.price for r in rows)

    @respx.mock
    async def test_nonstop_itinerary_has_no_stops(self, client):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(200, json=ONE_WAY_BODY)
        )

        async with client as c:
            rows = await c.one_way("DEL", "DXB", date(2026, 10, 14))

        indigo = next(r for r in rows if r.airline == "6E")
        assert indigo.stops == 0
        assert indigo.checked_bags == 0

    @respx.mock
    async def test_two_leg_search_keeps_both_directions(self, client):
        respx.post(f"{BASE}/fares/search").mock(
            return_value=httpx.Response(
                200,
                json={
                    "itineraries": [
                        {
                            "price": {"amount": 37668.0, "currency": "INR"},
                            "legs": [
                                {
                                    "duration_minutes": 225,
                                    "segments": [
                                        {
                                            "marketing_carrier_code": "6E",
                                            "flight_number": "1463",
                                            "departure_airport": "DEL",
                                            "departure_time_local": "2026-10-14T19:10:00",
                                            "arrival_airport": "DXB",
                                            "aircraft": "Airbus A321neo",
                                        }
                                    ],
                                },
                                {
                                    "duration_minutes": 385,
                                    "segments": [
                                        {
                                            "marketing_carrier_code": "GF",
                                            "flight_number": "505",
                                            "departure_airport": "DXB",
                                            "departure_time_local": "2026-12-07T12:50:00",
                                            "arrival_airport": "BAH",
                                            "aircraft": "Airbus A321neo",
                                        }
                                    ],
                                },
                            ],
                            "ignav_id": "rt1",
                        }
                    ]
                },
            )
        )

        async with client as c:
            rows = await c.round_trip(
                "DEL", "DXB", date(2026, 10, 14), date(2026, 12, 7)
            )

        row = rows[0]
        assert row.is_round_trip
        assert row.return_date == date(2026, 12, 7)
        assert row.airline == "6E"
        assert [s.marketing_carrier for s in row.inbound_segments] == ["GF"]
        assert row.duration_minutes == 610

    @respx.mock
    async def test_itinerary_without_segments_is_skipped(self, client):
        respx.post(f"{BASE}/fares/one-way").mock(
            return_value=httpx.Response(
                200,
                json={
                    "itineraries": [
                        {"price": {"amount": 100.0, "currency": "INR"}},
                        {"outbound": {"segments": []}},
                    ]
                },
            )
        )

        async with client as c:
            assert await c.one_way("DEL", "DXB", date(2026, 10, 14)) == []


class TestResilience:
    @respx.mock
    async def test_transient_424_is_retried(self, client):
        """Observed live: the same request 424s then succeeds. Retries are free,
        since only HTTP 200s are billable."""
        route = respx.post(f"{BASE}/fares/one-way")
        route.side_effect = [
            httpx.Response(424, text="upstream hiccup"),
            httpx.Response(200, json=ONE_WAY_BODY),
        ]

        async with client as c:
            rows = await c.one_way("DEL", "DXB", date(2026, 10, 14))

        assert len(rows) == 2
        assert route.call_count == 2

    @respx.mock
    async def test_only_successful_calls_are_counted(self, client):
        route = respx.post(f"{BASE}/fares/one-way")
        route.side_effect = [
            httpx.Response(424, text="hiccup"),
            httpx.Response(200, json=ONE_WAY_BODY),
        ]

        async with client as c:
            await c.one_way("DEL", "DXB", date(2026, 10, 14))
            assert c.requests_made == 1

    @respx.mock
    async def test_a_bad_request_is_not_retried(self, client):
        route = respx.post(f"{BASE}/fares/one-way")
        route.mock(return_value=httpx.Response(400, text="bad origin"))

        with pytest.raises(IgnavError, match="400"):
            async with client as c:
                await c.one_way("XXX", "DXB", date(2026, 10, 14))

        assert route.call_count == 1

    @respx.mock
    async def test_one_failed_date_does_not_lose_the_others(self, client):
        route = respx.post(f"{BASE}/fares/one-way")
        route.side_effect = [
            httpx.Response(200, json=ONE_WAY_BODY),
            httpx.Response(400, text="nope"),
            httpx.Response(200, json=ONE_WAY_BODY),
        ]

        async with client as c:
            rows, warnings = await resolve_dates(
                c, "DEL", "DXB", [date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
            )

        assert len(rows) == 4
        assert len(warnings) == 1
        assert "2026-10-15" in warnings[0]

    async def test_missing_key_fails_with_a_useful_message(self):
        with pytest.raises(IgnavError, match="IGNAV_API_KEY"):
            async with IgnavClient(api_key="", base_url=BASE) as c:
                await c.one_way("DEL", "DXB", date(2026, 10, 14))


class TestBookingLinks:
    @respx.mock
    async def test_links_are_returned_cheapest_first(self, client):
        respx.post(f"{BASE}/fares/booking-links").mock(
            return_value=httpx.Response(
                200,
                json={
                    "booking_options": [
                        {
                            "legs": ["outbound"],
                            "links": [
                                {
                                    "provider_name": "Adam Vacations",
                                    "provider_type": "third_party",
                                    "price": {"amount": 16287.0, "currency": "INR"},
                                    "url": "https://adamvacations.com/x",
                                },
                                {
                                    "provider_name": "Gulf Air",
                                    "provider_type": "airline",
                                    "price": {"amount": 15990.0, "currency": "INR"},
                                    "url": "https://gulfair.com/y",
                                },
                            ],
                        }
                    ]
                },
            )
        )

        async with client as c:
            links = await c.booking_links("abc123")

        assert [l.provider_name for l in links] == ["Gulf Air", "Adam Vacations"]
        assert links[0].price == Decimal("15990.0")

    @respx.mock
    async def test_ignav_id_lookup_sends_no_market_field(self, client):
        """The API rejects market or passenger fields alongside an ignav_id."""
        route = respx.post(f"{BASE}/fares/booking-links").mock(
            return_value=httpx.Response(200, json={"booking_options": []})
        )

        async with client as c:
            await c.booking_links("abc123")

        import json

        sent = json.loads(route.calls[0].request.content)
        assert sent == {"ignav_id": "abc123"}


class TestAircraftClassification:
    def test_widebody_and_retiring_types_are_recognised(self):
        assert aircraft.classify("Airbus A380-800").body is BodyType.WIDEBODY
        assert aircraft.classify("Airbus A380-800").retiring
        assert aircraft.classify("Boeing 747-8").engines == 4

    def test_narrowbody_is_not_mistaken_for_widebody(self):
        info = aircraft.classify("Airbus A321neo")
        assert info.body is BodyType.NARROWBODY
        assert info.family == "A320"
        assert not info.retiring

    def test_current_widebodies_are_not_flagged_retiring(self):
        for name in ("Boeing 787-9", "Airbus A350-1000", "Boeing 777-300ER"):
            info = aircraft.classify(name)
            assert info.body is BodyType.WIDEBODY, name
            assert not info.retiring, name

    def test_turboprop_and_regional_are_separated(self):
        assert aircraft.classify("ATR 72-600").body is BodyType.TURBOPROP
        assert aircraft.classify("Embraer E190").body is BodyType.REGIONAL

    def test_unknown_aircraft_degrades_quietly(self):
        info = aircraft.classify("Some New Type")
        assert info.body is BodyType.UNKNOWN
        assert info.family is None

    def test_empty_input_is_safe(self):
        assert aircraft.classify(None).body is BodyType.UNKNOWN
