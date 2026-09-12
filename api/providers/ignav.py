"""Ignav client — live-quoted fares with the detail the cached feeds cannot give.

Where Travelpayouts answers "something cost ₹15,463 on the 4th", this answers
"IndiGo 6E1463, an A321neo, departing 19:10, ₹16,803 verified, bookable here".
That difference is the product: an airline filter, an aircraft filter, and a
true-cost comparison all need to know what the fare actually *is*.

Behaviour observed against the live API, not assumed:

  * Auth is `X-Api-Key`; the base path is /api and booking links live at
    /api/fares/booking-links (not /api/booking-links, which 404s).
  * One request per exact date. There is no month or flexible-date search, so
    breadth costs requests and the cached feed stays useful for targeting.
  * HTTP 424 happens and is transient — the same request succeeded on retry.
    Only 200s are billable, so retrying a 424 is free.
  * A booking-link lookup by `ignav_id` must not carry `market` or passenger
    fields; the API rejects the combination.
  * Ten parallel searches completed cleanly, so a shortlist can be resolved
    concurrently rather than one date at a time (each search takes ~10s).
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence

import httpx

from api import config
from api.domain import BookingLink, FareRow, Segment

SOURCE = "ignav"

# 424 is the provider signalling an upstream hiccup rather than a bad request.
RETRY_STATUSES = {424, 429, 502, 503, 504}
MAX_ATTEMPTS = 3


class IgnavError(RuntimeError):
    pass


def _parse_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _segment(entry: dict) -> Segment | None:
    carrier = entry.get("marketing_carrier_code")
    number = entry.get("flight_number")
    if not carrier or number in (None, ""):
        return None
    return Segment(
        marketing_carrier=str(carrier).upper(),
        operating_carrier_name=entry.get("operating_carrier_name"),
        flight_number=str(number),
        origin=str(entry.get("departure_airport", "")).upper(),
        destination=str(entry.get("arrival_airport", "")).upper(),
        departure_local=_parse_dt(entry.get("departure_time_local")),
        arrival_local=_parse_dt(entry.get("arrival_time_local")),
        departure_timezone=entry.get("departure_timezone"),
        arrival_timezone=entry.get("arrival_timezone"),
        duration_minutes=entry.get("duration_minutes"),
        aircraft=entry.get("aircraft"),
    )


def _segments(leg: Any) -> list[Segment]:
    if not isinstance(leg, dict):
        return []
    return [s for e in (leg.get("segments") or []) if (s := _segment(e)) is not None]


class IgnavClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else config.IGNAV_API_KEY
        self.base_url = (base_url or config.IGNAV_BASE_URL).rstrip("/")
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.IGNAV_MAX_CONCURRENCY)
        self.requests_made = 0

    async def __aenter__(self) -> IgnavClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=config.IGNAV_TIMEOUT_SECONDS)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post(self, path: str, body: dict) -> Any:
        if not self.api_key:
            raise IgnavError(
                "IGNAV_API_KEY is not set — get one free at https://ignav.com"
            )
        if self._client is None:
            raise IgnavError("client used outside an async context manager")

        payload = {k: v for k, v in body.items() if v is not None}
        last: httpx.Response | None = None

        for attempt in range(MAX_ATTEMPTS):
            async with self._semaphore:
                response = await self._client.post(
                    f"{self.base_url}{path}",
                    json=payload,
                    headers={"X-Api-Key": self.api_key},
                )
            last = response

            if response.status_code == 200:
                self.requests_made += 1
                return response.json()

            if response.status_code not in RETRY_STATUSES:
                break

            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(0.6 * (attempt + 1))

        assert last is not None
        detail = last.text[:300]
        raise IgnavError(f"{last.status_code} from {path}: {detail}")

    # ---- searches ---------------------------------------------------------

    def _filters(
        self,
        max_stops: int | None = None,
        airlines_include: Sequence[str] | None = None,
        airlines_exclude: Sequence[str] | None = None,
        cabin_class: str | None = None,
        max_price: Decimal | int | None = None,
        min_checked_bags: int | None = None,
        allow_self_transfer: bool | None = None,
        adults: int = 1,
        market: str | None = None,
    ) -> dict:
        return {
            "adults": adults,
            "market": market or config.DEFAULT_MARKET,
            "max_stops": max_stops,
            "airlines_include": list(airlines_include) if airlines_include else None,
            "airlines_exclude": list(airlines_exclude) if airlines_exclude else None,
            "cabin_class": cabin_class,
            "max_price": float(max_price) if max_price is not None else None,
            "min_checked_bags": min_checked_bags,
            "allow_self_transfer": allow_self_transfer,
        }

    async def one_way(
        self,
        origin: str,
        destination: str,
        depart: date,
        currency_hint: str = "inr",
        **filters: Any,
    ) -> list[FareRow]:
        data = await self._post(
            "/fares/one-way",
            {
                "origin": origin,
                "destination": destination,
                "departure_date": f"{depart:%Y-%m-%d}",
                **self._filters(**filters),
            },
        )
        return self._rows(data, origin, destination, depart, None, currency_hint)

    async def round_trip(
        self,
        origin: str,
        destination: str,
        depart: date,
        ret: date,
        currency_hint: str = "inr",
        **filters: Any,
    ) -> list[FareRow]:
        """Both legs priced as one journey, which is how airlines sell them."""
        data = await self._post(
            "/fares/search",
            {
                "legs": [
                    {
                        "origin": origin,
                        "destination": destination,
                        "departure_date": f"{depart:%Y-%m-%d}",
                    },
                    {
                        "origin": destination,
                        "destination": origin,
                        "departure_date": f"{ret:%Y-%m-%d}",
                    },
                ],
                **self._filters(**filters),
            },
        )
        return self._rows(data, origin, destination, depart, ret, currency_hint)

    async def booking_links(self, ignav_id: str) -> list[BookingLink]:
        """Where to actually buy it, with each provider's own price.

        Takes no market or passenger fields: the API rejects them alongside an
        ignav_id.
        """
        data = await self._post("/fares/booking-links", {"ignav_id": ignav_id})

        links: list[BookingLink] = []
        for option in (data or {}).get("booking_options") or []:
            for link in option.get("links") or []:
                url = link.get("url")
                if not url:
                    continue
                price = link.get("price") or {}
                links.append(
                    BookingLink(
                        provider_name=link.get("provider_name") or "Unknown",
                        provider_type=link.get("provider_type"),
                        price=(
                            Decimal(str(price["amount"]))
                            if price.get("amount") is not None
                            else None
                        ),
                        currency=price.get("currency"),
                        url=url,
                    )
                )

        links.sort(key=lambda l: (l.price is None, l.price))
        return links

    # ---- normalisation ----------------------------------------------------

    def _rows(
        self,
        data: Any,
        origin: str,
        destination: str,
        depart: date,
        ret: date | None,
        currency_hint: str,
    ) -> list[FareRow]:
        fetched_at = datetime.now(timezone.utc)
        rows: list[FareRow] = []

        for itinerary in (data or {}).get("itineraries") or []:
            row = self._row(
                itinerary, origin, destination, depart, ret, currency_hint, fetched_at
            )
            if row is not None:
                rows.append(row)

        rows.sort(key=lambda r: r.price)
        return rows

    def _row(
        self,
        itinerary: Any,
        origin: str,
        destination: str,
        depart: date,
        ret: date | None,
        currency_hint: str,
        fetched_at: datetime,
    ) -> FareRow | None:
        if not isinstance(itinerary, dict):
            return None

        price = (itinerary.get("price") or {}).get("amount")
        if price is None:
            return None

        # One-way answers carry `outbound`; multi-leg answers carry `legs`.
        legs: list[Any] = itinerary.get("legs") or []
        if not legs and itinerary.get("outbound"):
            legs = [itinerary["outbound"]]

        outbound = _segments(legs[0]) if legs else []
        inbound = _segments(legs[1]) if len(legs) > 1 else []
        if not outbound:
            return None

        bags = itinerary.get("bags") or {}
        duration = sum(
            leg.get("duration_minutes") or 0 for leg in legs if isinstance(leg, dict)
        )

        return FareRow(
            origin=origin,
            destination=destination,
            depart_date=outbound[0].departure_local.date()
            if outbound[0].departure_local
            else depart,
            return_date=ret,
            price=Decimal(str(price)),
            currency=((itinerary.get("price") or {}).get("currency") or currency_hint).lower(),
            # Stops are counted on the outbound leg, matching how the rest of the
            # pipeline reads `stops`; the inbound leg is judged on its own row.
            stops=max(len(outbound) - 1, 0),
            airline=outbound[0].marketing_carrier,
            flight_number=outbound[0].designator,
            departure_at=outbound[0].departure_local,
            duration_minutes=duration or None,
            source=SOURCE,
            observed_at=fetched_at,
            outbound_segments=outbound,
            inbound_segments=inbound,
            cabin_class=itinerary.get("cabin_class"),
            checked_bags=bags.get("checked"),
            carry_on_bags=bags.get("carry_on"),
            requires_self_transfer=bool(itinerary.get("requires_self_transfer")),
            provider_ref=itinerary.get("ignav_id"),
        )


async def resolve_dates(
    client: IgnavClient,
    origin: str,
    destination: str,
    dates: Iterable[date],
    **filters: Any,
) -> tuple[list[FareRow], list[str]]:
    """Price several dates concurrently, tolerating individual failures.

    Searches take roughly ten seconds each, so a shortlist has to go out in
    parallel; one date failing must not lose the rest.
    """
    dates = list(dates)
    if not dates:
        return [], []

    batches = await asyncio.gather(
        *(client.one_way(origin, destination, day, **filters) for day in dates),
        return_exceptions=True,
    )

    rows: list[FareRow] = []
    warnings: list[str] = []
    for day, batch in zip(dates, batches):
        if isinstance(batch, BaseException):
            warnings.append(f"live fares unavailable for {day}: {batch}")
        else:
            rows.extend(batch)

    return rows, warnings
