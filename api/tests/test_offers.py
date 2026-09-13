from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from api.engines.offers import Offer, active, best_offer, discount_for, expired

TODAY = date(2026, 9, 13)


def percent_offer(**kwargs) -> Offer:
    base = dict(
        id="hdfc",
        label="HDFC Infinia on MakeMyTrip",
        percent=Decimal(12),
        max_discount=Decimal(3_000),
    )
    return Offer(**{**base, **kwargs})


class TestDiscountRules:
    def test_percent_is_capped(self):
        offer = percent_offer()

        assert discount_for(offer, Decimal(50_000), None, TODAY) == Decimal(3_000)

    def test_percent_below_the_cap_is_paid_in_full(self):
        offer = percent_offer()

        assert discount_for(offer, Decimal(10_000), None, TODAY) == Decimal(1_200)

    def test_a_flat_discount_needs_no_percent(self):
        offer = Offer(id="amex", label="Amex", flat_discount=Decimal(1_500))

        assert discount_for(offer, Decimal(20_000), None, TODAY) == Decimal(1_500)

    def test_minimum_spend_is_enforced(self):
        offer = percent_offer(min_spend=Decimal(15_000))

        assert discount_for(offer, Decimal(14_999), None, TODAY) is None
        assert discount_for(offer, Decimal(15_000), None, TODAY) is not None

    def test_a_discount_never_exceeds_the_fare(self):
        offer = Offer(id="big", label="Big", flat_discount=Decimal(50_000))

        assert discount_for(offer, Decimal(9_000), None, TODAY) == Decimal(9_000)

    def test_an_offer_needs_some_kind_of_discount(self):
        with pytest.raises(ValueError, match="percent or a flat"):
            Offer(id="empty", label="Nothing")


class TestExpiry:
    """A stale offer is worse than none: the traveller books expecting money off
    and simply does not get it."""

    def test_an_expired_offer_does_not_apply(self):
        offer = percent_offer(valid_until=date(2026, 9, 12))

        assert discount_for(offer, Decimal(20_000), None, TODAY) is None

    def test_an_offer_expiring_today_still_applies(self):
        offer = percent_offer(valid_until=TODAY)

        assert discount_for(offer, Decimal(20_000), None, TODAY) == Decimal(2_400)

    def test_imminent_expiry_is_flagged(self):
        offer = percent_offer(valid_until=date(2026, 9, 16))

        result = best_offer([offer], Decimal(20_000), today=TODAY)

        assert result.expiring_soon
        assert result.days_left == 3
        assert "expires in 3 days" in result.note

    def test_a_distant_expiry_is_not_flagged(self):
        offer = percent_offer(valid_until=date(2026, 12, 31))

        assert not best_offer([offer], Decimal(20_000), today=TODAY).expiring_soon

    def test_active_and_expired_are_separable_for_the_ui(self):
        live = percent_offer(id="live", valid_until=date(2026, 12, 1))
        dead = percent_offer(id="dead", valid_until=date(2026, 1, 1))

        assert [o.id for o in active([live, dead], TODAY)] == ["live"]
        assert [o.id for o in expired([live, dead], TODAY)] == ["dead"]


class TestSellerScoping:
    def test_an_unscoped_offer_applies_anywhere(self):
        offer = percent_offer(sellers=[])

        assert discount_for(offer, Decimal(20_000), "Cleartrip", TODAY) is not None

    def test_a_scoped_offer_matches_its_seller_loosely(self):
        offer = percent_offer(sellers=["cleartrip"])

        assert discount_for(offer, Decimal(20_000), "Cleartrip.com", TODAY) is not None

    def test_a_scoped_offer_ignores_other_sellers(self):
        offer = percent_offer(sellers=["cleartrip"])

        assert discount_for(offer, Decimal(20_000), "Travomint", TODAY) is None

    def test_a_scoped_offer_is_withheld_when_the_seller_is_unknown(self):
        """Claiming it applies would overstate a saving the traveller may not get."""
        offer = percent_offer(sellers=["cleartrip"])

        assert discount_for(offer, Decimal(20_000), None, TODAY) is None


class TestBestOffer:
    def test_the_largest_discount_wins_and_they_do_not_stack(self):
        small = Offer(id="a", label="A", flat_discount=Decimal(1_000))
        large = Offer(id="b", label="B", flat_discount=Decimal(2_500))

        result = best_offer([small, large], Decimal(20_000), today=TODAY)

        assert result.offer_id == "b"
        assert result.discount == Decimal(2_500)
        assert result.effective_price == Decimal(17_500)

    def test_no_applicable_offer_yields_nothing(self):
        offer = percent_offer(min_spend=Decimal(99_000))

        assert best_offer([offer], Decimal(20_000), today=TODAY) is None

    def test_an_empty_list_is_safe(self):
        assert best_offer([], Decimal(20_000), today=TODAY) is None

    def test_the_promo_code_is_carried_through(self):
        offer = percent_offer(promo_code="FLYHDFC")

        assert "code FLYHDFC" in best_offer([offer], Decimal(20_000), today=TODAY).note


class TestRerankingIsThePoint:
    """An offer can make a pricier fare the cheaper one, which is the whole
    reason this is not just a calculator."""

    def test_a_discount_can_beat_a_lower_headline_fare(self):
        offer = percent_offer()
        cheap_headline = Decimal(16_287)
        pricey_headline = Decimal(18_000)

        discounted = best_offer([offer], pricey_headline, today=TODAY)

        assert discounted.effective_price == Decimal(15_840)
        assert discounted.effective_price < cheap_headline

    def test_a_minimum_spend_can_invert_the_ranking(self):
        """Observed live on DEL-DXB: with 3,000 off above 16,500, the 16,803
        fare pays 13,803 while the 16,287 one misses the threshold entirely and
        pays in full. The dearer ticket is genuinely the cheaper one."""
        offer = Offer(
            id="axis",
            label="Axis Atlas",
            flat_discount=Decimal(3_000),
            min_spend=Decimal(16_500),
        )
        cheaper_headline = Decimal(16_287)
        dearer_headline = Decimal(16_803)

        on_cheaper = best_offer([offer], cheaper_headline, today=TODAY)
        on_dearer = best_offer([offer], dearer_headline, today=TODAY)

        assert on_cheaper is None
        assert on_dearer.effective_price == Decimal(13_803)
        assert on_dearer.effective_price < cheaper_headline

    def test_the_cap_stops_a_big_fare_from_always_winning(self):
        offer = percent_offer()

        assert best_offer([offer], Decimal(60_000), today=TODAY).discount == Decimal(
            3_000
        )
