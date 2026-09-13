from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from api.domain import DateRange, FareRow, SearchSpec, Segment, TripOption
from api.engines import datespace


class TestDateRange:
    def test_months_spans_boundary(self):
        span = DateRange(start=date(2026, 11, 20), end=date(2027, 1, 5))
        assert span.months() == [date(2026, 11, 1), date(2026, 12, 1), date(2027, 1, 1)]

    def test_single_month_is_one_entry(self):
        assert DateRange.for_month(date(2026, 11, 14)).months() == [date(2026, 11, 1)]

    def test_for_month_covers_whole_month(self):
        span = DateRange.for_month(date(2026, 2, 9))
        assert (span.start, span.end) == (date(2026, 2, 1), date(2026, 2, 28))
        assert len(span) == 28

    def test_end_before_start_rejected(self):
        with pytest.raises(ValueError):
            DateRange(start=date(2026, 11, 5), end=date(2026, 11, 1))


class TestPairing:
    def test_pairs_every_viable_cell(self, fare):
        out = [fare("2026-11-01", 20000), fare("2026-11-02", 21000)]
        back = [fare("2026-11-10", 18000, origin="LHR", destination="DEL")]

        options = datespace.pair_one_ways(out, back)

        assert len(options) == 2
        assert options[0].total_price == Decimal("38000")
        assert options[0].nights == 9

    def test_return_before_departure_is_never_paired(self, fare):
        out = [fare("2026-11-20", 20000)]
        back = [fare("2026-11-10", 5000, origin="LHR", destination="DEL")]

        assert datespace.pair_one_ways(out, back) == []

    def test_min_and_max_nights_bound_the_window(self, fare):
        out = [fare("2026-11-01", 20000)]
        back = [
            fare("2026-11-03", 9000, origin="LHR", destination="DEL"),
            fare("2026-11-08", 9000, origin="LHR", destination="DEL"),
            fare("2026-11-20", 9000, origin="LHR", destination="DEL"),
        ]

        options = datespace.pair_one_ways(out, back, min_nights=5, max_nights=10)

        assert [o.return_date for o in options] == [date(2026, 11, 8)]

    def test_same_day_return_allowed_at_zero_nights(self, fare):
        out = [fare("2026-11-01", 20000)]
        back = [fare("2026-11-01", 9000, origin="LHR", destination="DEL")]

        options = datespace.pair_one_ways(out, back, min_nights=0)

        assert len(options) == 1
        assert options[0].nights == 0

    def test_keeps_only_cheapest_fare_per_date(self, fare):
        out = [fare("2026-11-01", 30000), fare("2026-11-01", 20000, stops=1)]
        back = [fare("2026-11-05", 9000, origin="LHR", destination="DEL")]

        options = datespace.pair_one_ways(out, back)

        assert len(options) == 1
        assert options[0].total_price == Decimal("29000")

    def test_independent_ranges_across_different_months(self, fare):
        """The headline case: outbound in November, return in January."""
        out = [fare("2026-11-04", 22000), fare("2026-11-18", 19000)]
        back = [
            fare("2027-01-08", 17000, origin="LHR", destination="DEL"),
            fare("2027-01-22", 15000, origin="LHR", destination="DEL"),
        ]

        options = datespace.pair_one_ways(out, back, min_nights=40, max_nights=70)

        assert options[0].depart_date == date(2026, 11, 18)
        assert options[0].return_date == date(2027, 1, 22)
        assert options[0].total_price == Decimal("34000")


class TestBookableFirst:
    """Cached estimates are systematically optimistic, so price order alone
    puts unbookable fares at the top."""

    def _live(self, day: str, price: int):
        return TripOption.from_fare(
            FareRow(
                origin="DEL",
                destination="DXB",
                depart_date=date.fromisoformat(day),
                price=Decimal(price),
                currency="inr",
                stops=0,
                airline="6E",
                source="ignav",
                observed_at=datetime(2026, 9, 13, tzinfo=timezone.utc),
                outbound_segments=[
                    Segment(
                        marketing_carrier="6E",
                        flight_number="1463",
                        origin="DEL",
                        destination="DXB",
                    )
                ],
            )
        )

    def _estimate(self, day: str, price: int):
        return TripOption.from_fare(
            FareRow(
                origin="DEL",
                destination="DXB",
                depart_date=date.fromisoformat(day),
                price=Decimal(price),
                currency="inr",
                stops=1,
                source="travelpayouts",
                observed_at=datetime(2026, 9, 10, tzinfo=timezone.utc),
            )
        )

    def test_a_verified_fare_outranks_a_cheaper_estimate(self):
        options = [
            self._estimate("2026-10-14", 16068),
            self._live("2026-10-04", 16803),
        ]

        ranked = datespace.bookable_first(options)

        assert ranked[0].is_live_quote
        assert ranked[0].total_price == Decimal(16803)

    def test_verified_fares_are_still_ordered_by_price(self):
        options = [
            self._live("2026-10-04", 19000),
            self._live("2026-10-05", 16803),
            self._estimate("2026-10-14", 15000),
        ]

        ranked = datespace.bookable_first(options)

        assert [o.total_price for o in ranked] == [
            Decimal(16803),
            Decimal(19000),
            Decimal(15000),
        ]

    def test_estimates_are_kept_not_discarded(self):
        options = [self._estimate("2026-10-14", 16068), self._live("2026-10-04", 16803)]

        assert len(datespace.bookable_first(options)) == 2


class TestBuildOptions:
    def test_round_trip_fares_compete_with_paired_one_ways(self, fare):
        out = [
            fare("2026-11-01", 20000),
            fare("2026-11-01", 31000, return_date="2026-11-08"),
        ]
        back = [fare("2026-11-08", 18000, origin="LHR", destination="DEL")]

        options = datespace.build_options(out, back)
        best = datespace.collapse_to_best_per_cell(options)[0]

        assert best.kind == "round_trip"
        assert best.total_price == Decimal("31000")

    def test_round_trip_outside_night_bounds_is_dropped(self, fare):
        out = [fare("2026-11-01", 10000, return_date="2026-11-02")]

        options = datespace.build_options(out, [], min_nights=5)

        assert options == []

    def test_one_way_search_wraps_every_fare(self, fare):
        out = [fare("2026-11-01", 20000), fare("2026-11-02", 21000)]

        options = datespace.build_options(out)

        assert len(options) == 2
        assert all(o.kind == "one_way" for o in options)

    def test_one_way_search_ignores_round_trip_fares(self, fare):
        """The upstream cache mixes both, and a return price is roughly double."""
        out = [
            fare("2026-11-01", 20000),
            fare("2026-11-02", 34000, return_date="2026-11-09"),
        ]

        options = datespace.build_options(out)

        assert [o.depart_date for o in options] == [date(2026, 11, 1)]

    def test_round_trip_returning_outside_the_inbound_range_is_dropped(self, fare):
        """Trip length alone is not enough: the return must be when they asked."""
        out = [fare("2026-11-01", 30000, return_date="2026-11-11")]
        window = DateRange(start=date(2027, 1, 1), end=date(2027, 1, 31))

        options = datespace.build_options(
            out, [], min_nights=5, max_nights=15, inbound_range=window
        )

        assert options == []

    def test_round_trip_inside_the_inbound_range_is_kept(self, fare):
        out = [fare("2026-11-01", 30000, return_date="2027-01-10")]
        window = DateRange(start=date(2027, 1, 1), end=date(2027, 1, 31))

        options = datespace.build_options(out, [], inbound_range=window)

        assert len(options) == 1
        assert options[0].kind == "round_trip"

    def test_collapse_keeps_cheapest_per_cell(self, fare):
        out = [fare("2026-11-01", 20000, return_date="2026-11-05")]
        cheap = [fare("2026-11-01", 15000, return_date="2026-11-05")]

        collapsed = datespace.collapse_to_best_per_cell(
            datespace.build_options(out + cheap, [])
        )

        assert len(collapsed) == 1
        assert collapsed[0].total_price == Decimal("15000")


class TestSearchSpec:
    def test_night_bounds_require_an_inbound_range(self):
        with pytest.raises(ValueError, match="inbound"):
            SearchSpec(
                origin="DEL",
                destination="LHR",
                outbound=DateRange.for_month(date(2026, 11, 1)),
                max_nights=10,
            )

    def test_rejects_identical_origin_and_destination(self):
        with pytest.raises(ValueError, match="must differ"):
            SearchSpec(
                origin="DEL",
                destination="del",
                outbound=DateRange.for_month(date(2026, 11, 1)),
            )

    def test_normalises_codes_and_currency(self):
        spec = SearchSpec(
            origin="del",
            destination="lhr",
            outbound=DateRange.for_month(date(2026, 11, 1)),
            currency="INR",
        )
        assert (spec.origin, spec.destination, spec.currency) == ("DEL", "LHR", "inr")
