from __future__ import annotations

from decimal import Decimal

from api.domain import CarrierClass, TripOption
from api.engines import datespace
from api.pipeline import filters


def opt(fare_row) -> TripOption:
    return TripOption.from_fare(fare_row)


class TestLegLevelFiltering:
    """Filtering must precede the collapse to one fare per date.

    Regression: options are built by keeping the cheapest fare per date, so
    filtering afterwards judged each date by a fare the traveller had already
    excluded — a date whose cheapest fare had two stops disappeared entirely
    even though a one-stop fare was available that day.
    """

    def test_date_survives_on_its_best_compliant_fare(self, fare):
        rows = [
            fare("2026-11-01", 8000, stops=2),
            fare("2026-11-01", 11000, stops=1),
        ]

        kept, removed = filters.apply_to_rows(rows, filters.FilterSet(max_stops=1))
        options = datespace.build_options(kept)

        assert removed == {"stops": 1}
        assert len(options) == 1
        assert options[0].total_price == Decimal("11000")

    def test_date_disappears_only_when_no_fare_complies(self, fare):
        rows = [fare("2026-11-01", 8000, stops=2), fare("2026-11-02", 9000, stops=1)]

        kept, _ = filters.apply_to_rows(rows, filters.FilterSet(max_stops=1))

        assert [r.depart_date.day for r in kept] == [2]

    def test_pairing_uses_the_cheapest_compliant_fare_on_each_side(self, fare):
        outbound = [
            fare("2026-11-01", 5000, stops=2),
            fare("2026-11-01", 9000, stops=0),
        ]
        inbound = [
            fare("2026-11-10", 4000, stops=2, origin="LHR", destination="DEL"),
            fare("2026-11-10", 7000, stops=1, origin="LHR", destination="DEL"),
        ]
        spec = filters.FilterSet(max_stops=1)

        kept_out, _ = filters.apply_to_rows(outbound, spec)
        kept_in, _ = filters.apply_to_rows(inbound, spec)
        options = datespace.build_options(kept_out, kept_in)

        assert len(options) == 1
        assert options[0].total_price == Decimal("16000")

    def test_price_is_not_judged_per_leg(self, fare):
        """A trip budget must not be applied to each leg separately."""
        rows = [fare("2026-11-01", 20000)]

        kept, removed = filters.apply_to_rows(
            rows, filters.FilterSet(max_price=Decimal("25000"))
        )

        assert len(kept) == 1
        assert removed == {}


class TestStops:
    def test_max_stops_removes_longer_itineraries(self, fare):
        options = [opt(fare("2026-11-01", 10000, stops=0)), opt(fare("2026-11-02", 8000, stops=2))]

        outcome = filters.apply(options, filters.FilterSet(max_stops=1))

        assert len(outcome.kept) == 1
        assert outcome.removed == {"stops": 1}

    def test_paired_trip_judged_on_its_worst_leg(self, fare):
        combined = TripOption.combine(
            fare("2026-11-01", 10000, stops=0),
            fare("2026-11-10", 9000, stops=2, origin="LHR", destination="DEL"),
        )

        outcome = filters.apply([combined], filters.FilterSet(max_stops=1))

        assert outcome.kept == []


class TestAirlineAttribution:
    def test_unattributed_fares_are_rejected_and_counted_separately(self, fare):
        """The distinction that lets the UI offer a deep scan instead of lying."""
        options = [opt(fare("2026-11-01", 10000, airline=None))]

        outcome = filters.apply(
            options, filters.FilterSet(include_airlines={"6E"})
        )

        assert outcome.kept == []
        assert outcome.unknown_airline_count == 1
        assert "airline_not_in_list" not in outcome.removed

    def test_attributed_mismatch_is_a_genuine_rejection(self, fare):
        options = [opt(fare("2026-11-01", 10000, airline="AI"))]

        outcome = filters.apply(options, filters.FilterSet(include_airlines={"6E"}))

        assert outcome.removed == {"airline_not_in_list": 1}

    def test_unattributed_fares_survive_filters_that_do_not_need_an_airline(self, fare):
        options = [opt(fare("2026-11-01", 10000, stops=0, airline=None))]

        outcome = filters.apply(options, filters.FilterSet(max_stops=1))

        assert len(outcome.kept) == 1

    def test_exclusion_applies_to_known_airlines_only(self, fare):
        options = [
            opt(fare("2026-11-01", 10000, airline="SG")),
            opt(fare("2026-11-02", 11000, airline=None)),
        ]

        outcome = filters.apply(options, filters.FilterSet(exclude_airlines={"SG"}))

        assert len(outcome.kept) == 1
        assert outcome.removed == {"airline_excluded": 1}


class TestCarrierClass:
    def test_full_service_only_drops_low_cost_carriers(self, fare):
        options = [
            opt(fare("2026-11-01", 9000, airline="6E")),
            opt(fare("2026-11-02", 15000, airline="AI")),
        ]

        outcome = filters.apply(
            options, filters.FilterSet(carrier_classes={CarrierClass.FULL_SERVICE})
        )

        assert [o.outbound.airline for o in outcome.kept] == ["AI"]

    def test_both_legs_must_satisfy_the_class(self, fare):
        combined = TripOption.combine(
            fare("2026-11-01", 9000, airline="AI"),
            fare("2026-11-10", 8000, airline="6E", origin="LHR", destination="DEL"),
        )

        outcome = filters.apply(
            [combined], filters.FilterSet(carrier_classes={CarrierClass.FULL_SERVICE})
        )

        assert outcome.kept == []


class TestAlliance:
    def test_filters_to_member_carriers(self, fare):
        options = [
            opt(fare("2026-11-01", 30000, airline="AI")),
            opt(fare("2026-11-02", 28000, airline="QR")),
        ]

        outcome = filters.apply(options, filters.FilterSet(alliances={"star_alliance"}))

        assert [o.outbound.airline for o in outcome.kept] == ["AI"]


class TestPriceAndPassthrough:
    def test_max_price_applies_to_the_trip_total(self, fare):
        combined = TripOption.combine(
            fare("2026-11-01", 20000),
            fare("2026-11-10", 20000, origin="LHR", destination="DEL"),
        )

        outcome = filters.apply(
            [combined], filters.FilterSet(max_price=Decimal("35000"))
        )

        assert outcome.removed == {"price": 1}

    def test_empty_filterset_is_a_passthrough(self, fare):
        options = [opt(fare("2026-11-01", 10000))]

        outcome = filters.apply(options, filters.FilterSet())

        assert outcome.kept == options
        assert outcome.removed == {}
