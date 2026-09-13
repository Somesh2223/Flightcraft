"""Request and response shapes for the HTTP API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from api.domain import (
    BodyType,
    CarrierClass,
    DateRange,
    FareRow,
    Segment,
    TripClass,
    TripOption,
)
from api.reference import aircraft
from api.engines.history import PriceContext
from api.engines.offers import Application, Offer
from api.engines.points import AwardQuote, Eligibility, Wallet, eligible_programs
from api.pipeline.filters import FilterSet
from api.pipeline.scan import ScanDepth
from api.providers.travelpayouts import booking_link
from api.reference import carriers


class SearchRequest(BaseModel):
    origin: str
    destination: str
    outbound: DateRange
    inbound: DateRange | None = None
    min_nights: int | None = Field(default=None, ge=0)
    max_nights: int | None = Field(default=None, ge=0)

    max_stops: int | None = Field(default=None, ge=0)
    include_airlines: set[str] | None = None
    exclude_airlines: set[str] = Field(default_factory=set)
    carrier_classes: set[CarrierClass] | None = None
    alliances: set[str] | None = None
    max_price: Decimal | None = None

    aircraft_families: set[str] | None = None
    body_types: set[BodyType] | None = None
    retiring_only: bool = False

    depth: ScanDepth = ScanDepth.STANDARD
    currency: str = "inr"
    passengers: int = Field(default=1, ge=1, le=9)
    trip_class: TripClass = TripClass.ECONOMY
    limit: int = Field(default=40, ge=1, le=200)
    # Optional. When present, each result says which of these programmes could
    # book it — free to compute, since it needs no provider call.
    wallet: Wallet | None = None
    # Card and bank offers the traveller holds. Results are ranked on the price
    # after these apply, because a discount can change which date is cheapest.
    offers: list[Offer] = Field(default_factory=list)

    def to_filters(self) -> FilterSet:
        return FilterSet(
            max_stops=self.max_stops,
            include_airlines=self.include_airlines,
            exclude_airlines=self.exclude_airlines,
            carrier_classes=self.carrier_classes,
            alliances=self.alliances,
            max_price=self.max_price,
            aircraft_families=self.aircraft_families,
            body_types=self.body_types,
            retiring_only=self.retiring_only,
        )


class ResolveDateRequest(BaseModel):
    """Price one estimated cell for real, on demand.

    The estimates a scan shows are leads: cheap-looking dates nobody has checked.
    This turns one of them into an offer for a single request, so a traveller can
    chase a promising date without the search paying to price all sixty.
    """

    origin: str
    destination: str
    depart_date: date
    return_date: date | None = None
    max_stops: int | None = Field(default=None, ge=0)
    include_airlines: set[str] | None = None
    currency: str = "inr"
    passengers: int = Field(default=1, ge=1, le=9)


class PointsAssessRequest(BaseModel):
    """Value an award the traveller is looking at against the cash scan.

    `best_cash_alternative` is what makes this worth doing: comparing an award
    only against the same date's fare is how people talk themselves into poor
    redemptions.
    """

    wallet: Wallet
    quote: AwardQuote
    cash_price: Decimal
    best_cash_alternative: Decimal | None = None
    best_cash_date: str | None = None


class BookingLinksRequest(BaseModel):
    """Where to actually buy a specific live-quoted itinerary.

    Deliberately a separate call: a booking lookup costs a provider request, and
    fetching one for every result would double the cost of a search to answer a
    question most results are never asked.
    """

    provider_ref: str


def carriers_on(option: TripOption) -> list[str]:
    """Every marketing carrier the traveller would actually fly.

    Segment codes are used where a live quote provides them, since a one-stop
    itinerary can change airline midway and an award has to cover both.
    """
    codes: list[str] = []
    for leg in option.legs:
        if leg.segments:
            codes.extend(s.marketing_carrier for s in leg.segments)
        elif leg.airline:
            codes.append(leg.airline)
    return codes


class SegmentOut(BaseModel):
    carrier: str
    carrier_name: str | None
    flight_number: str
    origin: str
    destination: str
    departure_local: datetime | None
    arrival_local: datetime | None
    duration_minutes: int | None
    aircraft: str | None
    aircraft_family: str | None
    aircraft_body: BodyType

    @classmethod
    def of(cls, segment: Segment) -> SegmentOut:
        info = aircraft.classify(segment.aircraft)
        return cls(
            carrier=segment.marketing_carrier,
            carrier_name=(
                segment.operating_carrier_name
                or carriers.display_name(segment.marketing_carrier)
            ),
            flight_number=segment.designator,
            origin=segment.origin,
            destination=segment.destination,
            departure_local=segment.departure_local,
            arrival_local=segment.arrival_local,
            duration_minutes=segment.duration_minutes,
            aircraft=segment.aircraft,
            aircraft_family=info.family,
            aircraft_body=info.body,
        )


class LegOut(BaseModel):
    origin: str
    destination: str
    depart_date: date
    price: Decimal
    stops: int
    airline: str | None
    airline_name: str | None
    carrier_class: CarrierClass
    alliance: str | None
    flight_number: str | None
    departure_at: datetime | None
    duration_minutes: int | None
    observed_at: datetime
    segments: list[SegmentOut] = Field(default_factory=list)

    @classmethod
    def of(cls, row: FareRow, segments: list[Segment] | None = None) -> LegOut:
        """One leg of a trip.

        When `segments` are passed explicitly the leg is described by them, not
        by the row. A round trip priced as a single fare stores both directions
        on one row whose own fields describe the *outbound*, so reading the row
        for the return leg reported the wrong date, route, stop count and
        duration — an inbound leg labelled "Wed 14 Oct DEL→DXB" above a segment
        list that plainly said DXB→DEL.
        """
        if segments is not None and segments:
            first, last = segments[0], segments[-1]
            return cls(
                origin=first.origin,
                destination=last.destination,
                depart_date=(
                    first.departure_local.date()
                    if first.departure_local
                    else (row.return_date or row.depart_date)
                ),
                # The fare covers the whole journey and is reported on the trip,
                # so attributing it to one direction would double-count it.
                price=Decimal(0),
                stops=max(len(segments) - 1, 0),
                airline=first.marketing_carrier,
                airline_name=(
                    first.operating_carrier_name
                    or carriers.display_name(first.marketing_carrier)
                ),
                carrier_class=carriers.carrier_class(first.marketing_carrier),
                alliance=carriers.alliance(first.marketing_carrier),
                flight_number=first.designator,
                departure_at=first.departure_local,
                duration_minutes=(
                    sum(s.duration_minutes or 0 for s in segments) or None
                ),
                observed_at=row.observed_at,
                segments=[SegmentOut.of(s) for s in segments],
            )

        return cls(
            origin=row.origin,
            destination=row.destination,
            depart_date=row.depart_date,
            price=row.price,
            stops=row.stops,
            airline=row.airline,
            airline_name=carriers.display_name(row.airline) if row.airline else None,
            carrier_class=carriers.carrier_class(row.airline),
            alliance=carriers.alliance(row.airline),
            flight_number=row.flight_number,
            departure_at=row.departure_at,
            duration_minutes=row.duration_minutes,
            observed_at=row.observed_at,
            segments=[SegmentOut.of(s) for s in row.outbound_segments],
        )


class TripOptionOut(BaseModel):
    kind: str
    depart_date: date
    return_date: date | None
    nights: int | None
    total_price: Decimal
    currency: str
    max_stops: int
    outbound: LegOut
    inbound: LegOut | None
    booking_link: str
    observed_at: datetime
    # Judged on the outbound leg against its own history: for a paired trip the
    # total has no comparable record, since each leg is priced separately.
    price_context: PriceContext | None = None
    # A live quote names its flights and can be priced to a checkout page. An
    # estimate is a cached observation — roughly right, possibly no longer real.
    is_live_quote: bool = False
    provider_ref: str | None = None
    duration_minutes: int | None = None
    # Programmes from the caller's wallet that could book this itinerary.
    # Eligibility only — whether an award seat exists is a different question,
    # and no free data source answers it.
    points_options: list[Eligibility] = Field(default_factory=list)
    # The best card offer that applies, and what the fare costs once it does.
    applied_offer: Application | None = None

    @property
    def effective_price(self) -> Decimal:
        return (
            self.applied_offer.effective_price
            if self.applied_offer
            else self.total_price
        )

    @classmethod
    def of(
        cls,
        option: TripOption,
        passengers: int = 1,
        price_context: PriceContext | None = None,
        wallet: Wallet | None = None,
        applied_offer: Application | None = None,
    ) -> TripOptionOut:
        # A round trip priced as one fare carries both directions on a single
        # row, so its return segments belong to the inbound leg rather than the
        # outbound one they are stored on.
        native_return = option.kind == "round_trip" and option.outbound.inbound_segments
        return cls(
            price_context=price_context,
            kind=option.kind,
            depart_date=option.depart_date,
            return_date=option.return_date,
            nights=option.nights,
            total_price=option.total_price,
            currency=option.currency,
            max_stops=option.max_stops,
            outbound=LegOut.of(option.outbound),
            inbound=(
                LegOut.of(option.inbound)
                if option.inbound
                else (
                    LegOut.of(option.outbound, option.outbound.inbound_segments)
                    if native_return
                    else None
                )
            ),
            booking_link=booking_link(
                option.outbound.origin,
                option.outbound.destination,
                option.depart_date,
                option.return_date,
                passengers,
            ),
            observed_at=option.observed_at,
            is_live_quote=option.is_live_quote,
            provider_ref=option.provider_ref,
            duration_minutes=option.duration_minutes,
            points_options=(
                eligible_programs(wallet, carriers_on(option)) if wallet else []
            ),
            applied_offer=applied_offer,
        )


class MatrixCell(BaseModel):
    """One (departure, return) pair for the two-axis heatmap.

    Deliberately lean. A month against a month is around nine hundred cells, so
    anything carried here is carried nine hundred times; the full itinerary is
    fetched from the results list once a cell is picked.
    """

    depart_date: date
    return_date: date
    nights: int
    price: Decimal
    effective_price: Decimal
    stops: int
    airline: str | None
    is_live_quote: bool = False


class SplitTicketSaving(BaseModel):
    """What booking two one-ways saves over the cheapest single return ticket.

    Only reported when both were priced for real, since comparing a verified
    fare against an estimate would invent a saving that may not exist.
    """

    saving: Decimal
    two_one_ways: Decimal
    round_trip: Decimal
    depart_date: date
    return_date: date


class CalendarCell(BaseModel):
    """One day of the month grid."""

    depart_date: date
    price: Decimal
    stops: int
    airline: str | None
    airline_name: str | None
    return_date: date | None
    # What the day costs once a card offer applies. The grid colours by this,
    # since it is the number the traveller actually pays.
    effective_price: Decimal | None = None
    offer_label: str | None = None


class SearchResponse(BaseModel):
    origin: str
    destination: str
    currency: str
    depth: ScanDepth
    provider_calls: int

    cheapest: TripOptionOut | None
    results: list[TripOptionOut]
    calendar: list[CalendarCell]
    # Populated only for a return search: every viable departure/return pair,
    # which is the surface the calendar can only show one row of.
    matrix: list[MatrixCell] = Field(default_factory=list)

    total_before_filters: int
    filtered_out: dict[str, int]
    needs_deep_scan: bool
    demo_mode: bool
    # Cells priced for real this search, and cells answered from the short-lived
    # live-quote cache. Surfaced because they are what the search actually costs.
    live_requests: int = 0
    live_cache_hits: int = 0
    split_ticket_saving: SplitTicketSaving | None = None
    # New rows added to the price history by this scan. Visible so the user can
    # see the dataset growing, since the timing signal is worthless until it has.
    observations_recorded: int = 0
    warnings: list[str]
