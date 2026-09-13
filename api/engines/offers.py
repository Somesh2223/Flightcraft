"""Card and bank offers, and what they do to the ranking.

The arithmetic is trivial. What is not trivial, and what nothing else does, is
that an offer changes *which date is cheapest*. Twelve percent off a 18,000
rupee fare beats a 16,287 fare with nothing on it, so a month grid that ignores
offers quietly points at the wrong day. Everything here exists to feed that
re-ranking.

No offers ship with the app. They expire in weeks, vary by card, seller, payment
method and promo code, and no machine-readable source lists them. A stale offer
is worse than no offer at all: the traveller books expecting a discount and
simply does not get it. So the terms come from the person holding the card, who
can read them off their own bank's page, and expiry is enforced rather than
hoped for.

Offers are assumed not to stack. Banks almost never allow two on one
transaction, so the best single offer wins rather than the sum of them.
"""
from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel, Field, model_validator

# An offer expiring within this many days is still applied, but flagged: it is
# the difference between "book today" and "this will not be there on Friday".
EXPIRING_SOON_DAYS = 7


class Offer(BaseModel):
    """Terms as the traveller read them off their own card's offer page."""

    id: str
    label: str
    # Seller names this applies to, matched case-insensitively as substrings so
    # "cleartrip" catches "Cleartrip.com". Empty means any seller.
    sellers: list[str] = Field(default_factory=list)

    percent: Decimal | None = Field(default=None, ge=0, le=100)
    max_discount: Decimal | None = Field(default=None, ge=0)
    flat_discount: Decimal | None = Field(default=None, ge=0)
    min_spend: Decimal | None = Field(default=None, ge=0)

    valid_until: date | None = None
    promo_code: str | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _needs_a_discount(self) -> Offer:
        if self.percent is None and self.flat_discount is None:
            raise ValueError("an offer needs either a percent or a flat discount")
        return self

    def applies_to_seller(self, seller: str | None) -> bool:
        if not self.sellers:
            return True
        if seller is None:
            # Scoped to particular sellers, and we do not know which one this
            # would be bought from. Claiming it applies would overstate the
            # saving on a price the traveller might not be able to get.
            return False
        lowered = seller.lower()
        return any(s.lower() in lowered for s in self.sellers)

    def expired_on(self, today: date) -> bool:
        return self.valid_until is not None and self.valid_until < today

    def days_left(self, today: date) -> int | None:
        if self.valid_until is None:
            return None
        return (self.valid_until - today).days


class Application(BaseModel):
    offer_id: str
    label: str
    discount: Decimal
    effective_price: Decimal
    promo_code: str | None = None
    days_left: int | None = None
    expiring_soon: bool = False
    note: str


def _round(value: Decimal) -> Decimal:
    return value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def discount_for(
    offer: Offer, price: Decimal, seller: str | None, today: date
) -> Decimal | None:
    """What this offer takes off, or None if it does not apply."""
    if offer.expired_on(today):
        return None
    if not offer.applies_to_seller(seller):
        return None
    if offer.min_spend is not None and price < offer.min_spend:
        return None

    amounts: list[Decimal] = []
    if offer.percent is not None:
        pct = price * offer.percent / Decimal(100)
        if offer.max_discount is not None:
            pct = min(pct, offer.max_discount)
        amounts.append(pct)
    if offer.flat_discount is not None:
        amounts.append(offer.flat_discount)

    if not amounts:
        return None

    # A cap and a flat amount on the same offer are alternative wordings of the
    # same benefit, not two benefits; the better of them is what the bank pays.
    discount = _round(min(max(amounts), price))
    return discount if discount > 0 else None


def best_offer(
    offers: list[Offer],
    price: Decimal,
    seller: str | None = None,
    today: date | None = None,
) -> Application | None:
    """The single best applicable offer. Offers do not stack."""
    today = today or date.today()

    best: Application | None = None
    for offer in offers:
        discount = discount_for(offer, price, seller, today)
        if discount is None:
            continue
        if best is not None and discount <= best.discount:
            continue

        left = offer.days_left(today)
        soon = left is not None and left <= EXPIRING_SOON_DAYS
        best = Application(
            offer_id=offer.id,
            label=offer.label,
            discount=discount,
            effective_price=price - discount,
            promo_code=offer.promo_code,
            days_left=left,
            expiring_soon=soon,
            note=_describe(offer, discount, left, soon),
        )

    return best


def _describe(
    offer: Offer, discount: Decimal, days_left: int | None, soon: bool
) -> str:
    parts = [f"{discount:,.0f} off with {offer.label}"]

    if offer.percent is not None and offer.max_discount is not None:
        parts.append(f"{offer.percent:g}% capped at {offer.max_discount:,.0f}")
    elif offer.percent is not None:
        parts.append(f"{offer.percent:g}%")

    if offer.promo_code:
        parts.append(f"code {offer.promo_code}")

    if soon and days_left is not None:
        parts.append(
            "expires today" if days_left == 0 else f"expires in {days_left} days"
        )

    if offer.note:
        parts.append(offer.note)

    return " · ".join(parts)


def active(offers: list[Offer], today: date | None = None) -> list[Offer]:
    today = today or date.today()
    return [o for o in offers if not o.expired_on(today)]


def expired(offers: list[Offer], today: date | None = None) -> list[Offer]:
    today = today or date.today()
    return [o for o in offers if o.expired_on(today)]
