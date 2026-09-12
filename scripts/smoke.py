"""Check a real Travelpayouts token and verify the response contract.

Run manually, never in CI — it spends live quota and depends on the provider
being up. Unit tests cover parsing against recorded fixtures instead.

    python scripts/smoke.py            # defaults to DEL-DXB next month
    python scripts/smoke.py BOM LHR    # any route
    python scripts/smoke.py DEL DXB 2027-01

Three assumptions in the client are guesses from the published docs rather than
observed behaviour, and each one silently degrades a feature if wrong. This
script names them and reports a verdict on each:

  1. /v1/prices/cheap groups by NUMBER OF STOPS. If the key means something
     else, the stop filter is wrong at deep scan depth.
  2. month-matrix returns one-way rows (empty return_date) as well as round
     trips. Without them, independent outbound/return ranges cannot be paired.
  3. Flight numbers appear often enough to key aircraft lookups. If not, the
     aircraft filter has to fall back to matching on route and time of day.
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from datetime import date, timedelta

from api import config
from api.providers.travelpayouts import TravelpayoutsClient


def _next_month() -> date:
    today = date.today()
    return (today.replace(day=1) + timedelta(days=32)).replace(day=1)


def _pct(part: int, whole: int) -> str:
    return f"{(100 * part / whole):.0f}%" if whole else "n/a"


def _verdict(label: str, ok: bool, detail: str) -> None:
    print(f"  [{'PASS' if ok else 'CHECK'}] {label}: {detail}")


async def main() -> int:
    origin = (sys.argv[1] if len(sys.argv) > 1 else "DEL").upper()
    destination = (sys.argv[2] if len(sys.argv) > 2 else "DXB").upper()
    month = (
        date.fromisoformat(f"{sys.argv[3]}-01") if len(sys.argv) > 3 else _next_month()
    )

    if not config.TRAVELPAYOUTS_TOKEN:
        print(
            "No TRAVELPAYOUTS_TOKEN in .env.\n\n"
            "  1. Sign up free at https://travelpayouts.com ('Get Started')\n"
            "  2. Open https://app.travelpayouts.com -> Profile -> API token\n"
            "  3. Paste it into .env as TRAVELPAYOUTS_TOKEN=...\n"
        )
        return 1

    print(f"{origin} -> {destination}, {month:%B %Y}\n")

    async with TravelpayoutsClient() as client:
        # --- calendar: airline attribution ---
        calendar = await client.calendar(origin, destination, month, currency="inr")
        with_airline = sum(1 for r in calendar if r.airline)
        with_flight_no = sum(1 for r in calendar if r.flight_number)
        print(f"calendar        {len(calendar):>4} rows")

        # --- month-matrix: stop breakdown and one-way availability ---
        matrix = await client.month_matrix(origin, destination, month, currency="inr")
        one_way = sum(1 for r in matrix if not r.is_round_trip)
        stop_counts = Counter(r.stops for r in matrix)
        print(f"month-matrix    {len(matrix):>4} rows")

        # --- cheap: the grouping-key assumption, checked against raw JSON ---
        probe_day = max(month, date.today() + timedelta(days=21))
        raw_cheap = await client._get(
            "/v1/prices/cheap",
            {
                "origin": origin,
                "destination": destination,
                "depart_date": f"{probe_day:%Y-%m-%d}",
                "currency": "inr",
            },
        )
        cheap_keys: list[str] = []
        sample_entry: dict | None = None
        for by_stops in (raw_cheap or {}).values():
            if isinstance(by_stops, dict):
                cheap_keys.extend(str(k) for k in by_stops)
                sample_entry = sample_entry or next(iter(by_stops.values()), None)
        print(f"cheap           {len(cheap_keys):>4} rows on {probe_day}\n")

    print("Contract checks")
    _verdict(
        "cheap groups by stop count",
        bool(cheap_keys) and all(k.isdigit() for k in cheap_keys),
        f"keys seen: {sorted(set(cheap_keys)) or 'none'}",
    )
    _verdict(
        "month-matrix yields one-way rows",
        one_way > 0,
        f"{one_way}/{len(matrix)} one-way, stops seen: {dict(sorted(stop_counts.items()))}",
    )
    _verdict(
        "flight numbers present",
        len(calendar) > 0 and with_flight_no / len(calendar) > 0.5,
        f"airline on {_pct(with_airline, len(calendar))}, "
        f"flight number on {_pct(with_flight_no, len(calendar))} of calendar rows",
    )

    if sample_entry:
        print("\nRaw 'cheap' entry, for reference:")
        print(json.dumps(sample_entry, indent=2)[:400])

    cheapest = min(
        (r for r in calendar + matrix), key=lambda r: r.price, default=None
    )
    if cheapest:
        print(
            f"\nCheapest observed: INR {cheapest.price} on {cheapest.depart_date} "
            f"({cheapest.stops} stop, {cheapest.airline or 'airline unattributed'}), "
            f"found {cheapest.observed_at:%Y-%m-%d %H:%M}"
        )
    else:
        print("\nNo fares returned. Thin route, or the cache has nothing for that month.")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
