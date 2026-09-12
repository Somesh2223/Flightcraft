# Fareloom

A flight search engine built around scanning **date spaces** rather than dates.

Skyscanner and Google Flights assume you roughly know when you're travelling.
They offer a cheapest-in-a-month calendar, but you cannot put constraints on it —
ask for *"the cheapest day in November, no more than one stop, on a widebody,
excluding budget carriers"* and you're back to searching one date at a time.

Fareloom inverts that. Scanning a range and then filtering it is the core
primitive, so every feature is the same pipeline with a different filter:

```
SearchSpec ──▶ FareScan ──▶ Enrich ──▶ Filter ──▶ Rank ──▶ Results
```

A single-date search is just a range of length one.

## What works today

- **Whole-month (or multi-month) fare scanning** with a price heatmap calendar.
- **Independent outbound and return ranges.** Fly out any day in October, come
  back any day in January, bounded by trip length. The two ranges are priced
  separately and paired afterwards, which is frequently cheaper than any single
  return fare — and is not expressible on any mainstream site.
- **Filters that survive the month scan**: max stops, specific airlines,
  carrier type (budget / full service / hybrid), alliance, price cap.
- **Booking deep-links** to a live search, so the real current price is always
  one click away.

## The data, and its honest limits

Amadeus shut down its free Self-Service API on 17 July 2026. Kiwi's Tequila is
invite-only, and Skyscanner and Duffel need commercial agreements. The one
remaining self-serve, no-cost source of real fare data is the Travelpayouts /
Aviasales Data API, free with affiliate registration at
[travelpayouts.com](https://travelpayouts.com).

Its prices are **cached** — real fares from real recent searches, refreshed
continuously, but not live quotes. Three consequences, stated plainly:

1. **This is the right tool for month scanning.** A live-quote API physically
   cannot price two months of dates; that's thousands of priced queries.
2. **Every price is labelled with when it was observed**, and every result
   deep-links to a live search where the exact fare is confirmed.
3. **We accumulate our own price history** as a side effect of scanning, which
   is what will make "book now or wait" and error-fare detection possible.

### Scan depth is a cost dial

| Depth | Provider calls | What you get |
|---|---|---|
| `quick` | 1 per direction | `latest` with `period_type=year` — dated one-way fares across a whole year |
| `standard` | + 1 per month per direction | Adds `month-matrix`: fresher, and covers some dates `latest` misses |
| `deep` | + up to 20 | Tries to name airlines for the best date pairs |

### What the live API actually does

Measured against the real service, not the docs — several things differ, and
each one changed the design:

- **Prices are city-level.** A search for `LHR` returns rows stamped `LON`.
  Comparing codes exactly made DEL–LHR look like a route with no fares at all;
  comparing *city* codes fixes it while still excluding the neighbouring-city
  fares the endpoint mixes in (a DXB search also returns Sharjah).
- **`calendar` ignores the month it is given.** October, December, or anything
  else returns the identical 26 rows spanning five months. It is not used.
- **One-way and round-trip fares are mixed together** in the same responses, and
  are not comparable. A one-way search must therefore discard round-trip fares
  rather than show a roughly doubled price.
- **Airline attribution is, in practice, unavailable.** The only endpoint that
  names an airline (`cheap`) prices round trips, refuses any trip longer than
  30 nights, and returns nothing at all for most date pairs. So the airline,
  carrier-type and alliance filters work only on the rare attributed fare.
  This is a property of the data source, not a gap in the code — see the
  roadmap.

Filters that need an airline reject unattributed fares rather than guess, and
the count is reported separately so the UI can explain itself.

## Running it

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
npm --prefix web install
```

Copy `.env.example` to `.env`. Without a `TRAVELPAYOUTS_TOKEN` the app starts in
**demo mode** with clearly-labelled synthetic fares, so you can use the whole
interface before registering.

```bash
.venv/Scripts/python.exe -m uvicorn api.main:app --reload --port 8000
npm --prefix web run dev
```

Then open http://localhost:3000.

```bash
.venv/Scripts/python.exe -m pytest
```

## Layout

```
api/
  domain.py              canonical types: SearchSpec, DateRange, FareRow, TripOption
  providers/
    travelpayouts.py     the real data source, normalised into FareRow
    demo.py              deterministic synthetic fares for running without a token
  pipeline/
    scan.py              tiered provider fan-out
    filters.py           composable filters, and an account of what they removed
  engines/
    datespace.py         pairing independent date ranges into trip options
  reference/carriers.py  carrier class and alliance lookups
  data/carriers.yaml     curated carrier reference data, India-first
web/                     Next.js frontend
```

Two ordering rules in the pipeline are load-bearing and easy to get wrong:

- **Filter fares before pairing them.** Options keep the cheapest fare per date,
  so filtering afterwards judges a date by a fare the traveller already excluded
  — asking for one stop would delete a date whose *cheapest* fare has two, even
  when a one-stop fare exists that day.
- **Price is a trip-level filter**, never a per-leg one.

## Roadmap

### Open question: airline attribution

The airline and aircraft filters both need to know *which carrier* a given
cached price belongs to, and Travelpayouts will not say. Three ways forward,
none free and obvious:

1. **A schedules source** (AeroDataBox, OAG) to learn which airlines fly a route,
   then present airline choice as "who flies this" rather than "what each one
   charges". Cheap, but it filters routes, not prices.
2. **A second fare provider** for attributed prices on the shortlist only —
   Ignav is self-serve at roughly $2 per 1,000 requests after 1,000 free, which
   would cover a shortlist re-price at negligible cost.
3. **Scope the feature down** to what the data supports: filter by stops, price,
   duration and date, and drop per-airline filtering.

Worth deciding before building Phase 3, since aircraft filtering inherits the
same problem.

Ordered roughly by value per unit of effort.

- **Aircraft filters** — exact type, family, or category (widebody, four-engine,
  "fly it before it's gone"), from a route→equipment database built via
  AeroDataBox and OpenSky. Labelled with confidence, since equipment swaps.
  Blocked on the attribution question above.
- **True-cost pricing** — baggage, seat and meal fees folded in, so a ₹4,000
  budget fare plus a checked bag can correctly lose to a ₹5,200 full-service one.
- **Layover intelligence** — connection times, terminal changes, self-transfer
  risk, and transit-visa checks for the Indian passport.
- **Price history** — percentile versus the last 90 days, a book-now-or-wait
  signal, and error-fare detection. Needs weeks of accumulated scans to be useful.
- **Miles and points** — the emphasis is airline and alliance redemptions, not
  card points. For one flight the engine compares every program the traveller
  holds (own-airline, alliance partner, non-alliance bilateral, or via a card
  transfer) and says which to spend — and when not to redeem at all. Award seat
  availability cannot be checked by any free API, so the UI must always say "if a
  saver seat is available" and link out to the program's own search.
- **Discovery** — "anywhere under ₹15,000 in December", crossed with visa data
  for visa-free destinations, plus a holiday-aware calendar that spots
  long-weekend arbitrage.

## Scope

Search, filtering and insight. There is no booking or payment flow: results
hand off to the airline or OTA. We do not scrape Skyscanner or Google Flights.
