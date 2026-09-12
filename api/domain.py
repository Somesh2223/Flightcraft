"""Canonical types every provider normalises into and every filter reads from.

The whole application is one pipeline over these:

    SearchSpec -> [FareRow] -> [TripOption] -> filter -> rank

A single-date search and a two-month scan differ only in the size of the
DateRange, so they share one code path.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Iterator, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class CarrierClass(str, Enum):
    LOW_COST = "low_cost"
    FULL_SERVICE = "full_service"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"


class BodyType(str, Enum):
    REGIONAL = "regional"
    NARROWBODY = "narrowbody"
    WIDEBODY = "widebody"
    TURBOPROP = "turboprop"
    UNKNOWN = "unknown"


class TripClass(int, Enum):
    ECONOMY = 0
    BUSINESS = 1
    FIRST = 2


class DateRange(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> DateRange:
        if self.end < self.start:
            raise ValueError("DateRange.end must not precede DateRange.start")
        return self

    @classmethod
    def for_month(cls, anchor: date) -> DateRange:
        first = anchor.replace(day=1)
        next_first = (first + timedelta(days=32)).replace(day=1)
        return cls(start=first, end=next_first - timedelta(days=1))

    @classmethod
    def single(cls, day: date) -> DateRange:
        return cls(start=day, end=day)

    def __len__(self) -> int:
        return (self.end - self.start).days + 1

    def dates(self) -> Iterator[date]:
        for offset in range(len(self)):
            yield self.start + timedelta(days=offset)

    def months(self) -> list[date]:
        """First-of-month for every calendar month this range touches."""
        out: list[date] = []
        cursor = self.start.replace(day=1)
        last = self.end.replace(day=1)
        while cursor <= last:
            out.append(cursor)
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        return out

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


class SearchSpec(BaseModel):
    """What the user is asking for, independent of how we go and get it."""

    origin: str
    destination: str
    outbound: DateRange
    inbound: DateRange | None = None
    min_nights: int | None = Field(default=None, ge=0)
    max_nights: int | None = Field(default=None, ge=0)
    currency: str = "inr"
    trip_class: TripClass = TripClass.ECONOMY
    passengers: int = Field(default=1, ge=1, le=9)

    @field_validator("origin", "destination")
    @classmethod
    def _iata(cls, v: str) -> str:
        v = v.strip().upper()
        if len(v) != 3 or not v.isalpha():
            raise ValueError("expected a 3-letter IATA airport or city code")
        return v

    @field_validator("currency")
    @classmethod
    def _currency(cls, v: str) -> str:
        return v.strip().lower()

    @model_validator(mode="after")
    def _coherent(self) -> SearchSpec:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.min_nights is not None and self.max_nights is not None:
            if self.min_nights > self.max_nights:
                raise ValueError("min_nights must not exceed max_nights")
        if self.inbound is None and (self.min_nights or self.max_nights):
            raise ValueError("trip length constraints require an inbound range")
        return self

    @property
    def is_return(self) -> bool:
        return self.inbound is not None


class Segment(BaseModel):
    """One flight, as a live-quoting provider describes it.

    Only providers that quote real itineraries can fill these in; the cached
    aggregate feeds know nothing below the level of a price and a date.
    """

    marketing_carrier: str
    operating_carrier_name: str | None = None
    flight_number: str
    origin: str
    destination: str
    departure_local: datetime | None = None
    arrival_local: datetime | None = None
    departure_timezone: str | None = None
    arrival_timezone: str | None = None
    duration_minutes: int | None = None
    aircraft: str | None = None

    @property
    def designator(self) -> str:
        return f"{self.marketing_carrier}{self.flight_number}"


class BookingLink(BaseModel):
    provider_name: str
    provider_type: str | None = None
    price: Decimal | None = None
    currency: str | None = None
    url: str


class FareRow(BaseModel):
    """One observed price for one dated journey, as a provider reported it.

    `return_date` set means the provider priced this as a round trip; the row
    then represents the whole trip rather than a single leg.

    Fields below `segments` are only populated by live-quoting providers. The
    cached feeds leave them empty, and the pipeline is built to work either way
    — a filter that needs a segment simply cannot judge a row without one.
    """

    origin: str
    destination: str
    depart_date: date
    return_date: date | None = None
    price: Decimal
    currency: str
    stops: int = 0
    airline: str | None = None
    flight_number: str | None = None
    departure_at: datetime | None = None
    return_at: datetime | None = None
    duration_minutes: int | None = None
    distance_km: int | None = None
    source: str
    observed_at: datetime
    is_actual: bool = True

    outbound_segments: list[Segment] = Field(default_factory=list)
    inbound_segments: list[Segment] = Field(default_factory=list)
    cabin_class: str | None = None
    checked_bags: int | None = None
    carry_on_bags: int | None = None
    requires_self_transfer: bool = False
    provider_ref: str | None = None
    booking_links: list[BookingLink] = Field(default_factory=list)

    @property
    def segments(self) -> list[Segment]:
        return self.outbound_segments + self.inbound_segments

    @property
    def is_live_quote(self) -> bool:
        """Whether this came from a provider that priced a real itinerary."""
        return bool(self.outbound_segments)

    @property
    def aircraft_types(self) -> list[str]:
        return [s.aircraft for s in self.segments if s.aircraft]

    @field_validator("observed_at", "departure_at", "return_at")
    @classmethod
    def _utc_aware(cls, v: datetime | None) -> datetime | None:
        # Providers mix offset-bearing and bare timestamps; leaving both around
        # makes every later comparison a possible TypeError.
        if v is None:
            return None
        return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v

    @property
    def is_round_trip(self) -> bool:
        return self.return_date is not None

    @property
    def nights(self) -> int | None:
        if self.return_date is None:
            return None
        return (self.return_date - self.depart_date).days

    def age(self, now: datetime | None = None) -> timedelta:
        now = now or datetime.now(timezone.utc)
        observed = self.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return now - observed


TripKind = Literal["one_way", "round_trip", "combined_one_ways"]


class TripOption(BaseModel):
    """A complete, bookable-shaped answer: what the traveller would actually do.

    `combined_one_ways` is the interesting case — two independently priced
    one-way fares paired under the trip-length constraint. It is what makes
    "cheapest outbound in November, cheapest return in January" expressible,
    and it is frequently cheaper than any single round-trip fare.
    """

    kind: TripKind
    outbound: FareRow
    inbound: FareRow | None = None
    total_price: Decimal
    currency: str

    @model_validator(mode="after")
    def _coherent(self) -> TripOption:
        if self.kind == "combined_one_ways" and self.inbound is None:
            raise ValueError("combined_one_ways requires an inbound fare")
        if self.kind != "combined_one_ways" and self.inbound is not None:
            raise ValueError(f"{self.kind} must not carry a separate inbound fare")
        return self

    @classmethod
    def from_fare(cls, fare: FareRow) -> TripOption:
        return cls(
            kind="round_trip" if fare.is_round_trip else "one_way",
            outbound=fare,
            total_price=fare.price,
            currency=fare.currency,
        )

    @classmethod
    def combine(cls, outbound: FareRow, inbound: FareRow) -> TripOption:
        if outbound.currency != inbound.currency:
            raise ValueError("cannot combine fares priced in different currencies")
        return cls(
            kind="combined_one_ways",
            outbound=outbound,
            inbound=inbound,
            total_price=outbound.price + inbound.price,
            currency=outbound.currency,
        )

    @property
    def depart_date(self) -> date:
        return self.outbound.depart_date

    @property
    def return_date(self) -> date | None:
        if self.inbound is not None:
            return self.inbound.depart_date
        return self.outbound.return_date

    @property
    def nights(self) -> int | None:
        ret = self.return_date
        if ret is None:
            return None
        return (ret - self.depart_date).days

    @property
    def legs(self) -> list[FareRow]:
        return [self.outbound] if self.inbound is None else [self.outbound, self.inbound]

    @property
    def max_stops(self) -> int:
        return max(leg.stops for leg in self.legs)

    @property
    def airlines(self) -> set[str]:
        return {leg.airline for leg in self.legs if leg.airline}

    @property
    def observed_at(self) -> datetime:
        """The oldest observation backing this option — its weakest link."""
        return min(leg.observed_at for leg in self.legs)

    @property
    def duration_minutes(self) -> int | None:
        durations = [leg.duration_minutes for leg in self.legs]
        return sum(durations) if all(d is not None for d in durations) else None
