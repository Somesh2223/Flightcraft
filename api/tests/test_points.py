from __future__ import annotations

from decimal import Decimal

from api.engines.points import (
    AwardQuote,
    Holding,
    Verdict,
    Wallet,
    assess,
    eligible_programs,
)
from api.reference import loyalty


class TestEligibility:
    def test_a_programme_can_book_its_own_airline(self):
        wallet = Wallet(holdings=[Holding(program="skywards", balance=50_000)])

        assert [e.program for e in eligible_programs(wallet, ["EK"])] == ["skywards"]

    def test_an_alliance_programme_books_its_alliance(self):
        """KrisFlyer is Star, Air India is Star, so the miles reach the flight."""
        wallet = Wallet(holdings=[Holding(program="krisflyer", balance=60_000)])

        assert [e.program for e in eligible_programs(wallet, ["AI"])] == ["krisflyer"]

    def test_a_non_alliance_programme_does_not_reach_other_carriers(self):
        """Emirates has real bilateral partners, but they are not published
        anywhere reliable — suggesting one that does not exist sends the
        traveller hunting for an award they cannot book."""
        wallet = Wallet(holdings=[Holding(program="skywards", balance=50_000)])

        assert eligible_programs(wallet, ["AI"]) == []

    def test_every_carrier_on_the_itinerary_must_be_bookable(self):
        """An award covering half a journey is not an answer."""
        wallet = Wallet(holdings=[Holding(program="krisflyer", balance=60_000)])

        assert eligible_programs(wallet, ["AI", "EK"]) == []

    def test_avios_pool_across_programmes(self):
        """Avios in Qatar's account can be spent through British Airways."""
        wallet = Wallet(
            holdings=[
                Holding(program="privilege_club", balance=30_000),
                Holding(program="ba_club", balance=20_000),
            ]
        )

        found = eligible_programs(wallet, ["QR"])

        assert all(e.balance == 50_000 for e in found)

    def test_a_discount_scheme_is_not_an_award_programme(self):
        """6E Rewards pays out as money off a fare, never an award seat."""
        wallet = Wallet(holdings=[Holding(program="six_e_rewards", balance=9_000)])

        assert eligible_programs(wallet, ["6E"]) == []

    def test_unknown_programme_is_ignored_not_fatal(self):
        wallet = Wallet(holdings=[Holding(program="nonesuch", balance=1_000)])

        assert eligible_programs(wallet, ["AI"]) == []

    def test_no_carriers_yields_nothing(self):
        wallet = Wallet(holdings=[Holding(program="krisflyer", balance=60_000)])

        assert eligible_programs(wallet, []) == []


class TestValuation:
    quote = AwardQuote(
        program="krisflyer", points=25_000, cash_component=Decimal(5_000)
    )
    wallet = Wallet(holdings=[Holding(program="krisflyer", balance=60_000)])

    def test_value_is_the_cash_actually_avoided(self):
        """Taxes still payable are not saved, so they do not count."""
        result = assess(self.quote, self.wallet, cash_price=Decimal(30_000))

        assert result.value_per_point == Decimal("1.0000")

    def test_shortfall_is_reported_with_a_transfer_warning(self):
        thin = Wallet(holdings=[Holding(program="krisflyer", balance=18_000)])

        result = assess(self.quote, thin, cash_price=Decimal(30_000))

        assert result.verdict is Verdict.NOT_ENOUGH
        assert result.shortfall == 7_000
        assert "irreversible" in result.note

    def test_taxes_above_the_cheapest_fare_are_flagged_outright(self):
        """Objectively bad: the 'free' seat costs more than simply buying one."""
        pricey = AwardQuote(
            program="krisflyer", points=25_000, cash_component=Decimal(20_000)
        )

        result = assess(
            pricey,
            self.wallet,
            cash_price=Decimal(30_000),
            best_cash_alternative=Decimal(16_000),
        )

        assert result.verdict is Verdict.WORSE_THAN_CASH
        assert "more than" in result.note


class TestFlexibleDateComparison:
    """The comparison a month-wide scan makes possible, and the point of all this."""

    quote = AwardQuote(
        program="krisflyer", points=25_000, cash_component=Decimal(2_000)
    )
    wallet = Wallet(holdings=[Holding(program="krisflyer", balance=60_000)])

    def test_a_cheaper_date_elsewhere_devalues_the_redemption(self):
        result = assess(
            self.quote,
            self.wallet,
            cash_price=Decimal(32_000),
            best_cash_alternative=Decimal(16_000),
            best_cash_date="2026-10-04",
        )

        assert result.verdict is Verdict.POOR
        assert result.value_per_point == Decimal("1.2000")
        assert result.value_per_point_flexible == Decimal("0.5600")
        assert "2026-10-04" in result.note

    def test_no_cheaper_alternative_leaves_the_redemption_standing(self):
        result = assess(
            self.quote,
            self.wallet,
            cash_price=Decimal(32_000),
            best_cash_alternative=Decimal(31_000),
            best_cash_date="2026-10-04",
        )

        assert result.verdict is not Verdict.POOR

    def test_verdict_is_withheld_without_a_personal_baseline(self):
        """'Good value' is a personal judgement; inventing a benchmark would
        dress an opinion up as a fact."""
        result = assess(self.quote, self.wallet, cash_price=Decimal(32_000))

        assert result.verdict is Verdict.UNKNOWN
        assert "Set what a point is normally worth" in result.note

    def test_baseline_turns_the_number_into_a_verdict(self):
        opinionated = Wallet(
            holdings=[Holding(program="krisflyer", balance=60_000)],
            baseline_per_point=Decimal("0.50"),
        )

        result = assess(self.quote, opinionated, cash_price=Decimal(32_000))

        assert result.verdict is Verdict.STRONG

    def test_a_redemption_below_the_baseline_is_called_poor(self):
        opinionated = Wallet(
            holdings=[Holding(program="krisflyer", balance=60_000)],
            baseline_per_point=Decimal("2.00"),
        )

        result = assess(self.quote, opinionated, cash_price=Decimal(32_000))

        assert result.verdict is Verdict.POOR
        assert "save the points" in result.note

    def test_the_flexible_figure_drives_the_baseline_comparison(self):
        """Judging against this date's fare would call a poor redemption strong."""
        opinionated = Wallet(
            holdings=[Holding(program="krisflyer", balance=60_000)],
            baseline_per_point=Decimal("1.00"),
        )

        result = assess(
            self.quote,
            opinionated,
            cash_price=Decimal(32_000),
            best_cash_alternative=Decimal(27_000),
        )

        assert result.value_per_point > Decimal("1.00")
        assert result.value_per_point_flexible == Decimal("1.0000")
        assert result.verdict is Verdict.FAIR


class TestRegistry:
    def test_every_programme_points_at_a_known_carrier(self):
        from api.reference import carriers

        for program in loyalty.all_programs():
            assert carriers.get(program.airline) is not None, program.code

    def test_alliance_claims_match_the_carrier_registry(self):
        """A programme cannot book an alliance its own airline is not in."""
        from api.reference import carriers

        for program in loyalty.all_programs():
            if program.books_alliance:
                assert carriers.alliance(program.airline) == program.books_alliance, (
                    program.code
                )

    def test_label_does_not_stutter_the_airline_name(self):
        assert loyalty.get("ba_club").label == "The British Airways Club"
        assert loyalty.get("krisflyer").label == "Singapore Airlines KrisFlyer"

    def test_avios_programmes_pool_together(self):
        ba = loyalty.get("ba_club")
        assert {p.code for p in loyalty.pool_members(ba)} >= {
            "ba_club",
            "privilege_club",
            "iberia_plus",
            "finnair_plus",
        }
