"""Persistent fare observations — the asset the product accumulates.

Every scan writes here. Nothing else in the system needs a database, but this
does: the price-history features cannot be bought or fetched, only accrued, so
recording starts from the first search even though the signal takes weeks to
become meaningful.
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class FareObservation(Base):
    """One price, for one dated journey, as seen at one moment.

    `observed_at` is when the provider found the fare; `recorded_at` is when we
    saw it. They differ by up to days, and the distinction matters: staleness is
    judged on the former, while the booking curve is built from the latter.
    """

    __tablename__ = "fare_observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)

    # Identity digest, because a plain unique constraint cannot do the job here:
    # every one-way fare has return_date NULL, and SQL treats NULLs as distinct,
    # so the constraint never fires and re-scanning duplicates the whole route.
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True)

    origin: Mapped[str] = mapped_column(String(3))
    destination: Mapped[str] = mapped_column(String(3))
    depart_date: Mapped[date] = mapped_column(Date)
    return_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3))
    stops: Mapped[int] = mapped_column(Integer, default=0)
    airline: Mapped[str | None] = mapped_column(String(3), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    source: Mapped[str] = mapped_column(String(32))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

    __table_args__ = (
        Index("ix_route_departure", "origin", "destination", "depart_date"),
        Index("ix_route_recorded", "origin", "destination", "recorded_at"),
    )


def fingerprint_for(
    origin: str,
    destination: str,
    depart_date: date,
    return_date: date | None,
    stops: int,
    price: Decimal,
    currency: str,
    observed_at: datetime,
) -> str:
    """Identity of an observation: the same fare, seen at the same moment.

    Re-scanning a route returns the same rows until the provider refreshes them,
    so without this every scan would inflate the history and skew every
    percentile it feeds.
    """
    parts = [
        origin,
        destination,
        depart_date.isoformat(),
        return_date.isoformat() if return_date else "",
        str(stops),
        str(price),
        currency,
        observed_at.astimezone(timezone.utc).isoformat(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
