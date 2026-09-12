from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.domain import FareRow
from api.engines import history
from api.engines.history import (
    Confidence,
    Verdict,
    percentile_of,
    summarise,
)
from api.storage.models import Base


def prices(*values: int) -> list[Decimal]:
    return [Decimal(v) for v in values]


class TestPercentile:
    def test_cheapest_price_sits_at_the_bottom(self):
        assert percentile_of(prices(100, 200, 300, 400), Decimal(50)) == 0.0

    def test_priciest_sits_at_the_top(self):
        assert percentile_of(prices(100, 200, 300, 400), Decimal(500)) == 100.0

    def test_ties_count_as_half(self):
        assert percentile_of(prices(100, 200, 300, 400), Decimal(200)) == 37.5

    def test_empty_history_is_not_a_crash(self):
        assert percentile_of([], Decimal(100)) == 0.0


class TestConfidenceGating:
    """The feature must stay silent until it has grounds to speak."""

    def test_too_few_samples_yields_no_verdict(self):
        result = summarise(prices(100, 110, 120), Decimal(90), distinct_days=3)

        assert result.verdict is Verdict.UNKNOWN
        assert result.confidence is Confidence.NONE
        assert "builds over the next few weeks" in result.note

    def test_many_samples_from_one_day_still_yields_no_verdict(self):
        """Fifty rows from a single scan say nothing about price movement."""
        result = summarise(prices(*([100] * 50)), Decimal(60), distinct_days=1)

        assert result.verdict is Verdict.UNKNOWN
        assert result.confidence is Confidence.NONE

    def test_cheapest_seen_is_reported_even_without_a_verdict(self):
        result = summarise(prices(300, 250, 400), Decimal(280), distinct_days=1)

        assert result.cheapest_seen == Decimal(250)

    def test_confidence_rises_with_spread_and_volume(self):
        low = summarise(prices(*range(100, 112)), Decimal(105), distinct_days=4)
        medium = summarise(prices(*range(100, 130)), Decimal(105), distinct_days=10)
        high = summarise(prices(*range(100, 160)), Decimal(105), distinct_days=30)

        assert low.confidence is Confidence.LOW
        assert medium.confidence is Confidence.MEDIUM
        assert high.confidence is Confidence.HIGH


class TestVerdicts:
    history = prices(*range(10000, 10000 + 100 * 20, 100))  # 20 evenly spread

    def test_low_price_reads_as_good(self):
        result = summarise(self.history, Decimal(10200), distinct_days=8)

        assert result.verdict is Verdict.GOOD
        assert result.percentile is not None and result.percentile <= 25

    def test_high_price_reads_as_high_and_names_the_floor(self):
        result = summarise(self.history, Decimal(11800), distinct_days=8)

        assert result.verdict is Verdict.HIGH
        assert "10000" in result.note

    def test_middling_price_reads_as_typical(self):
        result = summarise(self.history, Decimal(10950), distinct_days=8)

        assert result.verdict is Verdict.TYPICAL

    def test_far_below_median_is_flagged_as_exceptional(self):
        result = summarise(self.history, Decimal(4000), distinct_days=8)

        assert result.verdict is Verdict.EXCEPTIONAL
        assert "below" in result.note

    def test_cheap_but_not_absurd_is_good_rather_than_exceptional(self):
        """The error-fare flag needs both a low rank and a big gap to the median."""
        result = summarise(self.history, Decimal(9900), distinct_days=8)

        assert result.verdict is Verdict.GOOD


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def observation(depart: str, price: int, return_date: str | None = None) -> FareRow:
    return FareRow(
        origin="DEL",
        destination="DXB",
        depart_date=date.fromisoformat(depart),
        return_date=date.fromisoformat(return_date) if return_date else None,
        price=Decimal(price),
        currency="inr",
        stops=0,
        source="test",
        observed_at=datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc),
    )


class TestRecording:
    async def test_rescanning_does_not_duplicate_one_way_fares(self, store):
        """Regression: NULL != NULL in SQL, so a unique constraint over
        return_date silently failed to dedupe every one-way fare, and each
        re-scan inflated the history that feeds every percentile."""
        rows = [observation("2026-10-04", 15463), observation("2026-10-05", 16000)]

        first = await history.record(store, rows)
        second = await history.record(store, rows)

        assert (first, second) == (2, 0)

    async def test_duplicates_within_one_batch_are_collapsed(self, store):
        row = observation("2026-10-04", 15463)

        assert await history.record(store, [row, row, row]) == 1

    async def test_a_changed_price_is_a_new_observation(self, store):
        await history.record(store, [observation("2026-10-04", 15463)])

        assert await history.record(store, [observation("2026-10-04", 14000)]) == 1

    async def test_round_trip_and_one_way_are_kept_apart(self, store):
        await history.record(
            store,
            [
                observation("2026-10-04", 15463),
                observation("2026-10-04", 31000, return_date="2026-10-18"),
            ],
        )

        one_way = await history.history_by_date(
            store, "DEL", "DXB", [date(2026, 10, 4)], "inr", round_trip=False
        )
        returns = await history.history_by_date(
            store, "DEL", "DXB", [date(2026, 10, 4)], "inr", round_trip=True
        )

        assert one_way[date(2026, 10, 4)][0] == [Decimal(15463)]
        assert returns[date(2026, 10, 4)][0] == [Decimal(31000)]

    async def test_empty_batch_is_a_no_op(self, store):
        assert await history.record(store, []) == 0
