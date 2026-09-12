from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from api.domain import FareRow


@pytest.fixture
def fare():
    def _make(
        depart: str,
        price: str | int,
        *,
        origin: str = "DEL",
        destination: str = "LHR",
        stops: int = 0,
        airline: str | None = None,
        return_date: str | None = None,
        observed_at: datetime | None = None,
    ) -> FareRow:
        return FareRow(
            origin=origin,
            destination=destination,
            depart_date=date.fromisoformat(depart),
            return_date=date.fromisoformat(return_date) if return_date else None,
            price=Decimal(str(price)),
            currency="inr",
            stops=stops,
            airline=airline,
            source="test",
            observed_at=observed_at or datetime(2026, 9, 12, tzinfo=timezone.utc),
        )

    return _make
