from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from api.domain import FareRow, Segment, TripOption
from api.schemas import TripOptionOut


def seg(carrier: str, number: str, origin: str, dest: str, when: str) -> Segment:
    return Segment(
        marketing_carrier=carrier,
        operating_carrier_name=None,
        flight_number=number,
        origin=origin,
        destination=dest,
        departure_local=datetime.fromisoformat(when),
        duration_minutes=180,
        aircraft="Airbus A321neo",
    )


def native_round_trip() -> TripOption:
    """One fare covering both directions, as a live two-leg search returns it."""
    row = FareRow(
        origin="DEL",
        destination="DXB",
        depart_date=date(2026, 10, 14),
        return_date=date(2026, 11, 28),
        price=Decimal(45272),
        currency="inr",
        stops=0,
        airline="6E",
        source="ignav",
        observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        outbound_segments=[seg("6E", "1463", "DEL", "DXB", "2026-10-14T19:10:00")],
        inbound_segments=[seg("SG", "6", "DXB", "DEL", "2026-11-28T11:10:00")],
    )
    return TripOption.from_fare(row)


class TestReturnLegOfANativeRoundTrip:
    """A round trip priced as one fare stores both directions on a single row
    whose own fields describe the outbound. Reading the row for the return leg
    labelled it with the outbound's date and route."""

    def test_the_return_leg_flies_the_other_way(self):
        out = TripOptionOut.of(native_round_trip())

        assert out.inbound is not None
        assert (out.inbound.origin, out.inbound.destination) == ("DXB", "DEL")

    def test_the_return_leg_departs_on_the_return_date(self):
        out = TripOptionOut.of(native_round_trip())

        assert out.inbound.depart_date == date(2026, 11, 28)

    def test_the_return_leg_reports_its_own_carrier(self):
        out = TripOptionOut.of(native_round_trip())

        assert out.inbound.airline == "SG"
        assert out.inbound.flight_number == "SG6"

    def test_the_outbound_leg_is_untouched(self):
        out = TripOptionOut.of(native_round_trip())

        assert (out.outbound.origin, out.outbound.destination) == ("DEL", "DXB")
        assert out.outbound.depart_date == date(2026, 10, 14)
        assert out.outbound.airline == "6E"

    def test_the_fare_is_not_counted_against_both_directions(self):
        out = TripOptionOut.of(native_round_trip())

        assert out.total_price == Decimal(45272)
        assert out.inbound.price == Decimal(0)

    def test_paired_one_ways_keep_their_own_leg_prices(self):
        """The synthetic-leg rule must not leak into genuinely separate fares."""
        out_row = FareRow(
            origin="DEL",
            destination="DXB",
            depart_date=date(2026, 10, 14),
            price=Decimal(16000),
            currency="inr",
            stops=0,
            source="travelpayouts",
            observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )
        back_row = FareRow(
            origin="DXB",
            destination="DEL",
            depart_date=date(2026, 11, 28),
            price=Decimal(12000),
            currency="inr",
            stops=1,
            source="travelpayouts",
            observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
        )

        out = TripOptionOut.of(
            TripOption(
                legs=[out_row, back_row],
                kind="combined_one_ways",
                outbound=out_row,
                inbound=back_row,
                total_price=Decimal(28000),
                currency="inr",
            )
        )

        assert out.inbound.price == Decimal(12000)
        assert (out.inbound.origin, out.inbound.destination) == ("DXB", "DEL")
        assert out.inbound.depart_date == date(2026, 11, 28)
