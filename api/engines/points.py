"""Judging whether points are worth spending on a particular flight.

The ordinary points calculator divides a fare by a number of miles and calls the
result a valuation. It is usually wrong, for a reason that has nothing to do with
arithmetic: it compares the award against the cash price *on that same date*. A
traveller with a month of flexibility is not choosing between points and that
fare. They are choosing between points and the cheapest fare in their window.

This app has just scanned that whole window, so it can make the honest
comparison — and the two answers often differ enough to reverse the decision.
Burning 50,000 miles to avoid a 32,000-rupee fare looks like a fine redemption
until you notice the same trip sells for 16,000 four days earlier, at which
point the miles are earning half what they appeared to.

No award prices are hardcoded anywhere. The traveller supplies the quote the
airline is actually showing them, and everything here is arithmetic on that.
See data/loyalty.yaml for why.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, Field

from api.reference import loyalty


class Verdict(str, Enum):
    STRONG = "strong"
    FAIR = "fair"
    POOR = "poor"
    WORSE_THAN_CASH = "worse_than_cash"
    NOT_ENOUGH = "not_enough"
    UNKNOWN = "unknown"


class Holding(BaseModel):
    program: str
    balance: int = Field(ge=0)


class Wallet(BaseModel):
    holdings: list[Holding] = Field(default_factory=list)
    # What the traveller reckons a point is worth to them elsewhere, in the
    # smallest currency unit (paise for INR). Optional: without it no verdict is
    # given, because "good value" is a personal judgement and inventing a
    # benchmark would dress an opinion up as a fact.
    baseline_per_point: Decimal | None = None

    def balance_of(self, program_code: str) -> int:
        """Balance available to a programme, pooling shared currencies.

        Avios sit in five programmes and move between them at 1:1, so a
        traveller holding them in Qatar's account can spend them through British
        Airways. Treating those as separate pots would understate what they have.
        """
        program = loyalty.get(program_code)
        if program is None:
            return 0
        pool = {p.code for p in loyalty.pool_members(program)}
        return sum(h.balance for h in self.holdings if h.program in pool)


class AwardQuote(BaseModel):
    """What the airline is asking for the seat, as the traveller sees it."""

    program: str
    points: int = Field(gt=0)
    # Taxes, carrier surcharges and fees still payable in cash on the award.
    cash_component: Decimal = Decimal(0)


class Eligibility(BaseModel):
    program: str
    label: str
    balance: int
    note: str


class Assessment(BaseModel):
    program: str
    label: str
    points: int
    cash_component: Decimal
    balance: int
    shortfall: int

    cash_price: Decimal
    best_cash_alternative: Decimal | None = None
    best_cash_date: str | None = None

    value_per_point: Decimal
    value_per_point_flexible: Decimal | None = None

    verdict: Verdict
    note: str


def eligible_programs(wallet: Wallet, carrier_codes: list[str]) -> list[Eligibility]:
    """Which of the traveller's programmes could plausibly book this itinerary.

    Every carrier on the itinerary must be bookable: an award covering only half
    the journey is not an answer to "can I use my points for this trip".
    """
    carriers_present = [c for c in carrier_codes if c]
    if not carriers_present:
        return []

    out: list[Eligibility] = []
    for holding in wallet.holdings:
        program = loyalty.get(holding.program)
        if program is None:
            continue
        if not all(loyalty.can_book(program, c) for c in carriers_present):
            continue
        balance = wallet.balance_of(program.code)
        note = (
            f"{program.label} can book {', '.join(sorted(set(carriers_present)))}"
        )
        out.append(
            Eligibility(
                program=program.code,
                label=program.label,
                balance=balance,
                note=note,
            )
        )

    out.sort(key=lambda e: e.balance, reverse=True)
    return out


def _per_point(cash_avoided: Decimal, points: int) -> Decimal:
    if points <= 0:
        return Decimal(0)
    return (cash_avoided / Decimal(points)).quantize(Decimal("0.0001"))


def assess(
    quote: AwardQuote,
    wallet: Wallet,
    cash_price: Decimal,
    best_cash_alternative: Decimal | None = None,
    best_cash_date: str | None = None,
) -> Assessment:
    """Value a redemption against both the fare on the day and the best fare found."""
    program = loyalty.get(quote.program)
    label = program.label if program else quote.program
    balance = wallet.balance_of(quote.program)
    shortfall = max(quote.points - balance, 0)

    cash_avoided = cash_price - quote.cash_component
    value = _per_point(cash_avoided, quote.points)

    flexible_value: Decimal | None = None
    if best_cash_alternative is not None:
        flexible_value = _per_point(
            best_cash_alternative - quote.cash_component, quote.points
        )

    verdict, note = _judge(
        quote, wallet, value, flexible_value, best_cash_alternative, best_cash_date, shortfall
    )

    return Assessment(
        program=quote.program,
        label=label,
        points=quote.points,
        cash_component=quote.cash_component,
        balance=balance,
        shortfall=shortfall,
        cash_price=cash_price,
        best_cash_alternative=best_cash_alternative,
        best_cash_date=best_cash_date,
        value_per_point=value,
        value_per_point_flexible=flexible_value,
        verdict=verdict,
        note=note,
    )


def _judge(
    quote: AwardQuote,
    wallet: Wallet,
    value: Decimal,
    flexible_value: Decimal | None,
    best_cash: Decimal | None,
    best_cash_date: str | None,
    shortfall: int,
) -> tuple[Verdict, str]:
    # Objectively bad regardless of anyone's valuation: the cash still payable on
    # the "free" seat exceeds what the whole trip costs to simply buy.
    if best_cash is not None and quote.cash_component >= best_cash:
        return (
            Verdict.WORSE_THAN_CASH,
            f"The taxes and surcharges on this award come to {quote.cash_component:,.0f}, "
            f"which is more than the {best_cash:,.0f} the trip costs in cash"
            + (f" on {best_cash_date}" if best_cash_date else "")
            + ". Paying cash costs less and keeps the points.",
        )

    if shortfall > 0:
        return (
            Verdict.NOT_ENOUGH,
            f"Short by {shortfall:,} points. Topping up by transfer is usually "
            "irreversible, so check the rate in your bank's portal before moving any.",
        )

    # The comparison the month scan makes possible, and the one that flips
    # decisions: flexible travellers are not really avoiding this date's fare.
    if flexible_value is not None and flexible_value < value * Decimal("0.75"):
        return (
            Verdict.POOR,
            f"Worth {value:,.2f} per point against this date's fare, but only "
            f"{flexible_value:,.2f} against the {best_cash:,.0f} fare"
            + (f" on {best_cash_date}" if best_cash_date else "")
            + ". If your dates are flexible, paying cash then and keeping the points is better value.",
        )

    baseline = wallet.baseline_per_point
    if baseline is None:
        return (
            Verdict.UNKNOWN,
            f"Worth {value:,.2f} per point here. Set what a point is normally "
            "worth to you to get a verdict rather than just a number.",
        )

    effective = flexible_value if flexible_value is not None else value
    if effective >= baseline * Decimal("1.25"):
        return (
            Verdict.STRONG,
            f"{effective:,.2f} per point, comfortably above your {baseline:,.2f} baseline.",
        )
    if effective >= baseline:
        return (
            Verdict.FAIR,
            f"{effective:,.2f} per point, a little above your {baseline:,.2f} baseline.",
        )
    return (
        Verdict.POOR,
        f"{effective:,.2f} per point, below your {baseline:,.2f} baseline. "
        "Cash is the better buy here; save the points for a redemption that beats it.",
    )
