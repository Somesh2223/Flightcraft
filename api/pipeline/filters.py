"""Composable filters over trip options, with an account of what they removed.

One deliberate decision runs through this file: **an airline-dependent filter
rejects options whose airline we could not identify.** The alternative — letting
unattributed fares through — would answer "cheapest IndiGo flight in December"
with fares that may not be IndiGo at all.

That rejection is counted separately as `unknown_airline` rather than lumped in
with genuine mismatches, because it is fixable: the cheap month scan carries no
airline attribution, and re-running at deep scan depth resolves it. The UI is
expected to offer exactly that when this count is non-zero.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from pydantic import BaseModel, Field

from api.domain import CarrierClass, FareRow, TripOption
from api.reference import carriers


class FilterSet(BaseModel):
    max_stops: int | None = Field(default=None, ge=0)
    include_airlines: set[str] | None = None
    exclude_airlines: set[str] = Field(default_factory=set)
    carrier_classes: set[CarrierClass] | None = None
    alliances: set[str] | None = None
    max_price: Decimal | None = None
    max_age_hours: int | None = Field(default=None, ge=0)

    def model_post_init(self, _context: object) -> None:
        if self.include_airlines:
            self.include_airlines = {c.upper() for c in self.include_airlines}
        self.exclude_airlines = {c.upper() for c in self.exclude_airlines}

    @property
    def needs_airline(self) -> bool:
        """Whether any active filter can only be judged with an airline code."""
        return bool(self.include_airlines or self.carrier_classes or self.alliances)

    @property
    def is_empty(self) -> bool:
        return not (
            self.max_stops is not None
            or self.include_airlines
            or self.exclude_airlines
            or self.carrier_classes
            or self.alliances
            or self.max_price is not None
            or self.max_age_hours is not None
        )


class FilterOutcome(BaseModel):
    kept: list[TripOption]
    removed: dict[str, int] = Field(default_factory=dict)

    @property
    def unknown_airline_count(self) -> int:
        return self.removed.get("unknown_airline", 0)


def _leg_reason(
    airlines: list[str | None],
    max_stops_seen: int,
    oldest_observation: datetime,
    spec: FilterSet,
    now: datetime,
) -> str | None:
    """Everything judgeable from the legs alone, shared by both entry points."""
    if spec.max_stops is not None and max_stops_seen > spec.max_stops:
        return "stops"

    if spec.max_age_hours is not None:
        if oldest_observation < now - timedelta(hours=spec.max_age_hours):
            return "stale"

    if spec.exclude_airlines and any(a and a in spec.exclude_airlines for a in airlines):
        return "airline_excluded"

    if spec.needs_airline and not all(airlines):
        return "unknown_airline"

    if spec.include_airlines and not all(a in spec.include_airlines for a in airlines):
        return "airline_not_in_list"

    if spec.carrier_classes and not all(
        carriers.carrier_class(a) in spec.carrier_classes for a in airlines
    ):
        return "carrier_class"

    if spec.alliances and not all(
        (carriers.alliance(a) or "") in spec.alliances for a in airlines
    ):
        return "alliance"

    return None


def _reject_reason(option: TripOption, spec: FilterSet, now: datetime) -> str | None:
    if spec.max_price is not None and option.total_price > spec.max_price:
        return "price"

    return _leg_reason(
        [leg.airline for leg in option.legs],
        option.max_stops,
        option.observed_at,
        spec,
        now,
    )


def apply_to_rows(
    rows: list[FareRow], spec: FilterSet, now: datetime | None = None
) -> tuple[list[FareRow], dict[str, int]]:
    """Filter individual fares before they are combined into trip options.

    This has to happen first. Options are built by keeping the cheapest fare per
    date, so filtering afterwards would judge a date by a fare the traveller
    already ruled out — asking for "at most one stop" would delete a date whose
    cheapest fare has two stops, even when a one-stop fare exists on that day.

    Price is not evaluated here: it is a budget for the whole trip, so it can
    only be judged once both legs are paired.
    """
    if spec.is_empty:
        return list(rows), {}

    now = now or datetime.now(timezone.utc)
    kept: list[FareRow] = []
    removed: Counter[str] = Counter()

    for row in rows:
        reason = _leg_reason([row.airline], row.stops, row.observed_at, spec, now)
        if reason is None:
            kept.append(row)
        else:
            removed[reason] += 1

    return kept, dict(removed)


def apply(
    options: list[TripOption], spec: FilterSet, now: datetime | None = None
) -> FilterOutcome:
    if spec.is_empty:
        return FilterOutcome(kept=list(options))

    now = now or datetime.now(timezone.utc)
    kept: list[TripOption] = []
    removed: Counter[str] = Counter()

    for option in options:
        reason = _reject_reason(option, spec, now)
        if reason is None:
            kept.append(option)
        else:
            removed[reason] += 1

    return FilterOutcome(kept=kept, removed=dict(removed))
