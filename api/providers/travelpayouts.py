"""Travelpayouts (Aviasales) Data API client, normalised into FareRow.

Endpoint capabilities differ in ways that drive the whole scan strategy, so
they are worth stating plainly:

    month-matrix     1 call/month, many rows per day split by stop count,
                     but NO airline attribution.
    calendar         1 call/month, exactly one (cheapest) row per day,
                     WITH airline and flight number.
    cheap            1 call/date, up to one row per stop count,
                     WITH airline and flight number.

So no single endpoint gives both breadth and airline attribution, which is why
`scan` fans out in tiers rather than calling one thing. See pipeline/scan.py.

All prices here are cached observations, not live quotes. Every row carries the
timestamp the fare was found so the UI can say how stale it is.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import httpx

from api import config
from api.domain import DateRange, FareRow, TripClass

SOURCE = "travelpayouts"


class TravelpayoutsError(RuntimeError):
    pass


def _parse_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_date(raw: Any) -> date | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _flight_number(airline: Any, number: Any) -> str | None:
    if not airline or number in (None, ""):
        return None
    return f"{airline}{number}"


def booking_link(
    origin: str,
    destination: str,
    depart: date,
    return_date: date | None = None,
    passengers: int = 1,
    marker: str | None = None,
) -> str:
    """Aviasales deep-link: ORIGIN + DDMM + DEST + [DDMM] + passenger count.

    Built locally rather than fetched, so it costs nothing and always points at
    a live search where the real, current price is confirmed.
    """
    segment = f"{origin}{depart:%d%m}{destination}"
    if return_date is not None:
        segment += f"{return_date:%d%m}"
    url = f"https://www.aviasales.com/search/{segment}{passengers}"
    marker = marker if marker is not None else config.TRAVELPAYOUTS_MARKER
    return f"{url}?marker={marker}" if marker else url


class TravelpayoutsClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.token = token if token is not None else config.TRAVELPAYOUTS_TOKEN
        self.base_url = (base_url or config.TRAVELPAYOUTS_BASE_URL).rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.PROVIDER_MAX_CONCURRENCY)

    async def __aenter__(self) -> TravelpayoutsClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=config.PROVIDER_TIMEOUT_SECONDS,
                headers={"Accept-Encoding": "gzip, deflate"},
            )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        if not self.token:
            raise TravelpayoutsError(
                "TRAVELPAYOUTS_TOKEN is not set — register at "
                "https://www.travelpayouts.com/developers/api to get one"
            )
        if self._client is None:
            raise TravelpayoutsError("client used outside an async context manager")

        query = {k: v for k, v in params.items() if v is not None}
        async with self._semaphore:
            response = await self._client.get(
                f"{self.base_url}{path}",
                params=query,
                headers={"X-Access-Token": self.token},
            )
        response.raise_for_status()
        payload = response.json()

        # nearest-places-matrix answers with a bare object; the rest wrap in
        # {success, data, error}.
        if isinstance(payload, dict) and "success" in payload:
            if not payload.get("success"):
                raise TravelpayoutsError(str(payload.get("error") or "request failed"))
            return payload.get("data")
        return payload

    # ---- endpoints -------------------------------------------------------

    async def month_matrix(
        self,
        origin: str,
        destination: str,
        month: date,
        currency: str = "inr",
    ) -> list[FareRow]:
        """Every cached price for a month, split out by number of stops."""
        data = await self._get(
            "/v2/prices/month-matrix",
            {
                "origin": origin,
                "destination": destination,
                "month": f"{month:%Y-%m-01}",
                "currency": currency,
                "show_to_affiliates": "true",
            },
        )
        fetched_at = datetime.now(timezone.utc)
        return [
            row
            for entry in (data or [])
            if (row := self._row_from_matrix(entry, currency, fetched_at)) is not None
        ]

    async def calendar(
        self,
        origin: str,
        destination: str,
        depart_month: date,
        return_date: date | None = None,
        currency: str = "inr",
    ) -> list[FareRow]:
        """Cheapest fare per day for a month, with airline attribution."""
        data = await self._get(
            "/v1/prices/calendar",
            {
                "origin": origin,
                "destination": destination,
                "depart_date": f"{depart_month:%Y-%m}",
                "return_date": f"{return_date:%Y-%m-%d}" if return_date else None,
                "calendar_type": "departure_date",
                "currency": currency,
            },
        )
        fetched_at = datetime.now(timezone.utc)
        rows: list[FareRow] = []
        for day, entry in (data or {}).items():
            row = self._row_from_keyed(
                entry, currency, fetched_at, depart_date=_parse_date(day)
            )
            if row is not None:
                rows.append(row)
        return rows

    async def cheap(
        self,
        origin: str,
        destination: str,
        depart_date: date,
        return_date: date | None = None,
        currency: str = "inr",
    ) -> list[FareRow]:
        """Cheapest fare per stop count for one date, with airline attribution."""
        data = await self._get(
            "/v1/prices/cheap",
            {
                "origin": origin,
                "destination": destination,
                "depart_date": f"{depart_date:%Y-%m-%d}",
                "return_date": f"{return_date:%Y-%m-%d}" if return_date else None,
                "currency": currency,
            },
        )
        fetched_at = datetime.now(timezone.utc)
        rows: list[FareRow] = []
        for dest, by_stops in (data or {}).items():
            if not isinstance(by_stops, dict):
                continue
            for key, entry in by_stops.items():
                row = self._row_from_keyed(
                    entry,
                    currency,
                    fetched_at,
                    depart_date=depart_date,
                    destination=dest,
                    stops=int(key) if str(key).isdigit() else None,
                )
                if row is not None:
                    rows.append(row)
        return rows

    async def city_directions(
        self, origin: str, currency: str = "inr"
    ) -> list[FareRow]:
        """Cheapest fare to every destination reachable from an origin."""
        data = await self._get(
            "/v1/city-directions", {"origin": origin, "currency": currency}
        )
        fetched_at = datetime.now(timezone.utc)
        rows: list[FareRow] = []
        for dest, entry in (data or {}).items():
            row = self._row_from_keyed(
                entry, currency, fetched_at, origin=origin, destination=dest
            )
            if row is not None:
                rows.append(row)
        return rows

    async def latest(
        self,
        origin: str,
        destination: str | None = None,
        beginning_of_period: date | None = None,
        period_type: str = "month",
        one_way: bool = False,
        limit: int = 1000,
        currency: str = "inr",
        trip_class: TripClass = TripClass.ECONOMY,
    ) -> list[FareRow]:
        """Fares found in the last 48 hours — the freshest view available."""
        data = await self._get(
            "/v2/prices/latest",
            {
                "origin": origin,
                "destination": destination,
                "beginning_of_period": (
                    f"{beginning_of_period:%Y-%m-01}" if beginning_of_period else None
                ),
                "period_type": period_type,
                "one_way": "true" if one_way else "false",
                "limit": min(limit, 1000),
                "sorting": "price",
                "currency": currency,
                "trip_class": int(trip_class),
                "show_to_affiliates": "true",
            },
        )
        fetched_at = datetime.now(timezone.utc)
        return [
            row
            for entry in (data or [])
            if (row := self._row_from_matrix(entry, currency, fetched_at)) is not None
        ]

    async def month_matrix_range(
        self, origin: str, destination: str, span: DateRange, currency: str = "inr"
    ) -> list[FareRow]:
        """month_matrix across every month a range touches, clipped to the range."""
        results = await asyncio.gather(
            *(
                self.month_matrix(origin, destination, month, currency)
                for month in span.months()
            )
        )
        return [row for batch in results for row in batch if span.contains(row.depart_date)]

    # ---- normalisation ---------------------------------------------------

    def _row_from_matrix(
        self, entry: Any, currency: str, fetched_at: datetime
    ) -> FareRow | None:
        """Shape used by month-matrix, latest and nearest-places-matrix."""
        if not isinstance(entry, dict):
            return None
        depart = _parse_date(entry.get("depart_date"))
        value = entry.get("value")
        if depart is None or value in (None, ""):
            return None
        return FareRow(
            origin=str(entry.get("origin", "")).upper(),
            destination=str(entry.get("destination", "")).upper(),
            depart_date=depart,
            return_date=_parse_date(entry.get("return_date")),
            price=Decimal(str(value)),
            currency=currency,
            stops=int(entry.get("number_of_changes") or 0),
            distance_km=entry.get("distance"),
            source=SOURCE,
            observed_at=_parse_dt(entry.get("found_at")) or fetched_at,
            is_actual=bool(entry.get("actual", True)),
        )

    def _row_from_keyed(
        self,
        entry: Any,
        currency: str,
        fetched_at: datetime,
        depart_date: date | None = None,
        origin: str | None = None,
        destination: str | None = None,
        stops: int | None = None,
    ) -> FareRow | None:
        """Shape used by calendar, cheap, direct and city-directions."""
        if not isinstance(entry, dict):
            return None
        price = entry.get("price")
        if price in (None, ""):
            return None

        departure_at = _parse_dt(entry.get("departure_at"))
        depart = depart_date or (departure_at.date() if departure_at else None)
        if depart is None:
            return None

        return_at = _parse_dt(entry.get("return_at"))
        airline = entry.get("airline")
        resolved_stops = stops if stops is not None else entry.get("transfers")

        return FareRow(
            origin=str(origin or entry.get("origin") or "").upper(),
            destination=str(destination or entry.get("destination") or "").upper(),
            depart_date=depart,
            return_date=return_at.date() if return_at else None,
            price=Decimal(str(price)),
            currency=currency,
            stops=int(resolved_stops or 0),
            airline=str(airline).upper() if airline else None,
            flight_number=_flight_number(airline, entry.get("flight_number")),
            departure_at=departure_at,
            return_at=return_at,
            source=SOURCE,
            # These endpoints report no found_at, only an expiry, so the fetch
            # time is the best honest bound on freshness.
            observed_at=fetched_at,
        )
