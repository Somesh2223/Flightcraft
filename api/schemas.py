"""Request and response shapes for the HTTP API."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from api.domain import CarrierClass, DateRange, FareRow, TripClass, TripOption
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

    depth: ScanDepth = ScanDepth.STANDARD
    currency: str = "inr"
    passengers: int = Field(default=1, ge=1, le=9)
    trip_class: TripClass = TripClass.ECONOMY
    limit: int = Field(default=40, ge=1, le=200)

    def to_filters(self) -> FilterSet:
        return FilterSet(
            max_stops=self.max_stops,
            include_airlines=self.include_airlines,
            exclude_airlines=self.exclude_airlines,
            carrier_classes=self.carrier_classes,
            alliances=self.alliances,
            max_price=self.max_price,
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
    observed_at: datetime

    @classmethod
    def of(cls, row: FareRow) -> LegOut:
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
            observed_at=row.observed_at,
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

    @classmethod
    def of(cls, option: TripOption, passengers: int = 1) -> TripOptionOut:
        return cls(
            kind=option.kind,
            depart_date=option.depart_date,
            return_date=option.return_date,
            nights=option.nights,
            total_price=option.total_price,
            currency=option.currency,
            max_stops=option.max_stops,
            outbound=LegOut.of(option.outbound),
            inbound=LegOut.of(option.inbound) if option.inbound else None,
            booking_link=booking_link(
                option.outbound.origin,
                option.outbound.destination,
                option.depart_date,
                option.return_date,
                passengers,
            ),
            observed_at=option.observed_at,
        )


class CalendarCell(BaseModel):
    """One day of the month grid."""

    depart_date: date
    price: Decimal
    stops: int
    airline: str | None
    airline_name: str | None
    return_date: date | None


class SearchResponse(BaseModel):
    origin: str
    destination: str
    currency: str
    depth: ScanDepth
    provider_calls: int

    cheapest: TripOptionOut | None
    results: list[TripOptionOut]
    calendar: list[CalendarCell]

    total_before_filters: int
    filtered_out: dict[str, int]
    needs_deep_scan: bool
    demo_mode: bool
    warnings: list[str]
