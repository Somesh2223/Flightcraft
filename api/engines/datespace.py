"""Turning a space of dated fares into rankable trip options.

This is the piece that makes "cheapest outbound in November, cheapest return in
January" a first-class query. Metasearch sites tie the return calendar to the
outbound one; here the two ranges are independent and paired afterwards under a
trip-length constraint.

Pure functions over FareRow — no I/O, no provider knowledge.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable, Sequence

from api.domain import FareRow, TripOption


def one_way_rows(rows: Iterable[FareRow]) -> list[FareRow]:
    return [row for row in rows if not row.is_round_trip]


def round_trip_rows(rows: Iterable[FareRow]) -> list[FareRow]:
    return [row for row in rows if row.is_round_trip]


def cheapest_per_date(rows: Iterable[FareRow]) -> dict[date, FareRow]:
    """The single best fare for each departure date — the month-grid view."""
    best: dict[date, FareRow] = {}
    for row in rows:
        current = best.get(row.depart_date)
        if current is None or row.price < current.price:
            best[row.depart_date] = row
    return best


def _nights_ok(nights: int, min_nights: int | None, max_nights: int | None) -> bool:
    if nights < 0:
        return False
    if min_nights is not None and nights < min_nights:
        return False
    if max_nights is not None and nights > max_nights:
        return False
    return True


def pair_one_ways(
    outbound: Sequence[FareRow],
    inbound: Sequence[FareRow],
    min_nights: int | None = None,
    max_nights: int | None = None,
) -> list[TripOption]:
    """Cheapest combined trip for every viable (depart, return) date pair.

    Returns one option per cell rather than every combination, because the cells
    are what the two-axis heatmap renders and the global best is just the
    cheapest cell. Ordered by total price.

    A same-day return is allowed (0 nights); a return before departure is not.
    """
    best_out = cheapest_per_date(outbound)
    best_in = cheapest_per_date(inbound)
    if not best_out or not best_in:
        return []

    return_dates = sorted(best_in)
    options: list[TripOption] = []

    for depart in sorted(best_out):
        lower = depart + timedelta(days=min_nights) if min_nights is not None else depart
        upper = depart + timedelta(days=max_nights) if max_nights is not None else None
        for ret in return_dates:
            if ret < lower:
                continue
            if upper is not None and ret > upper:
                break
            if not _nights_ok((ret - depart).days, min_nights, max_nights):
                continue
            options.append(TripOption.combine(best_out[depart], best_in[ret]))

    options.sort(key=lambda opt: (opt.total_price, opt.depart_date))
    return options


def build_options(
    outbound: Sequence[FareRow],
    inbound: Sequence[FareRow] | None = None,
    min_nights: int | None = None,
    max_nights: int | None = None,
) -> list[TripOption]:
    """All trip options for a scan, from both pairing and native round trips.

    Providers price some journeys as a single round-trip fare and others as two
    one-ways, and neither is reliably cheaper. Both are produced here and left
    for the ranker to sort out, so the traveller sees whichever actually wins.
    """
    options: list[TripOption] = []

    if inbound is None:
        options.extend(TripOption.from_fare(row) for row in outbound)
    else:
        options.extend(
            pair_one_ways(
                one_way_rows(outbound), one_way_rows(inbound), min_nights, max_nights
            )
        )
        for row in round_trip_rows(outbound):
            nights = row.nights
            if nights is not None and _nights_ok(nights, min_nights, max_nights):
                options.append(TripOption.from_fare(row))

    options.sort(key=lambda opt: (opt.total_price, opt.depart_date))
    return options


def collapse_to_best_per_cell(options: Iterable[TripOption]) -> list[TripOption]:
    """Deduplicate to one option per (depart, return) pair, keeping the cheapest.

    Needed once round-trip fares and paired one-ways are mixed together, since
    both can land on the same cell.
    """
    best: dict[tuple[date, date | None], TripOption] = {}
    for option in options:
        key = (option.depart_date, option.return_date)
        current = best.get(key)
        if current is None or option.total_price < current.total_price:
            best[key] = option
    return sorted(best.values(), key=lambda opt: (opt.total_price, opt.depart_date))


def price_grid(options: Iterable[TripOption]) -> dict[date, dict[date | None, TripOption]]:
    """Nested depart -> return -> option, for rendering the heatmap."""
    grid: dict[date, dict[date | None, TripOption]] = defaultdict(dict)
    for option in collapse_to_best_per_cell(options):
        grid[option.depart_date][option.return_date] = option
    return dict(grid)
