"use client";

import { useEffect, useMemo, useState } from "react";

import MonthGrid from "@/components/MonthGrid";
import OffersPanel, { loadOffers } from "@/components/OffersPanel";
import PointsWallet, { loadWallet } from "@/components/PointsWallet";
import ResultsList from "@/components/ResultsList";
import {
  BODY_LABEL,
  BodyType,
  CarrierClass,
  REJECTION_LABELS,
  ScanDepth,
  SearchRequest,
  SearchResponse,
  Offer,
  SearchEstimate,
  TripOption,
  Wallet,
  estimateSearch,
  formatMoney,
  resolveDate,
  search,
} from "@/lib/api";

function cellKey(option: TripOption): string {
  return `${option.depart_date}-${option.return_date ?? "ow"}`;
}

/** Mirrors the server's ranking: a fare you can buy beats a cheaper guess. */
function bookableFirst(options: TripOption[]): TripOption[] {
  return [...options].sort((a, b) => {
    if (a.is_live_quote !== b.is_live_quote) return a.is_live_quote ? -1 : 1;
    return Number(a.total_price) - Number(b.total_price);
  });
}

function iso(date: Date): string {
  // Local components, not toISOString — east of UTC that rolls midnight back a day.
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function monthBounds(offset: number): { start: string; end: string } {
  const now = new Date();
  const first = new Date(now.getFullYear(), now.getMonth() + offset, 1);
  const last = new Date(now.getFullYear(), now.getMonth() + offset + 1, 0);
  return { start: iso(first), end: iso(last) };
}

const CARRIER_CLASSES: { value: CarrierClass; label: string }[] = [
  { value: "full_service", label: "Full service" },
  { value: "low_cost", label: "Budget" },
  { value: "hybrid", label: "Hybrid" },
];

const BODY_TYPES: BodyType[] = ["widebody", "narrowbody", "regional", "turboprop"];

const DEPTHS: { value: ScanDepth; label: string; hint: string }[] = [
  { value: "quick", label: "Quick", hint: "estimates only, free" },
  { value: "standard", label: "Standard", hint: "prices the 10 best dates for real" },
  { value: "deep", label: "Deep", hint: "prices up to 30 dates for real" },
];

export default function Home() {
  const nextMonth = useMemo(() => monthBounds(1), []);
  const monthAfter = useMemo(() => monthBounds(2), []);

  const [origin, setOrigin] = useState("DEL");
  const [destination, setDestination] = useState("LHR");
  const [outStart, setOutStart] = useState(nextMonth.start);
  const [outEnd, setOutEnd] = useState(nextMonth.end);
  const [wantsReturn, setWantsReturn] = useState(false);
  const [inStart, setInStart] = useState(monthAfter.start);
  const [inEnd, setInEnd] = useState(monthAfter.end);
  const [minNights, setMinNights] = useState("");
  const [maxNights, setMaxNights] = useState("");

  const [maxStops, setMaxStops] = useState("");
  const [classes, setClasses] = useState<CarrierClass[]>([]);
  const [airlines, setAirlines] = useState("");
  const [bodies, setBodies] = useState<BodyType[]>([]);
  const [retiringOnly, setRetiringOnly] = useState(false);
  const [depth, setDepth] = useState<ScanDepth>("standard");

  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [pricingCell, setPricingCell] = useState<string | null>(null);
  // Read after mount, not during render: localStorage does not exist on the
  // server and reading it in the initial state would break hydration.
  const [wallet, setWallet] = useState<Wallet>({ holdings: [] });
  const [offers, setOffers] = useState<Offer[]>([]);
  const [estimate, setEstimate] = useState<SearchEstimate | null>(null);

  useEffect(() => {
    setWallet(loadWallet());
    setOffers(loadOffers());
  }, []);

  // Live pricing is the only part that costs money, so the count is shown
  // before the search rather than after it.
  useEffect(() => {
    let stale = false;
    estimateSearch({
      origin: origin.trim().toUpperCase() || "DEL",
      destination: destination.trim().toUpperCase() || "BOM",
      outbound: { start: outStart, end: outEnd },
      inbound: wantsReturn ? { start: inStart, end: inEnd } : null,
    })
      .then((e) => !stale && setEstimate(e))
      .catch(() => !stale && setEstimate(null));
    return () => {
      stale = true;
    };
  }, [origin, destination, outStart, outEnd, wantsReturn, inStart, inEnd]);

  function toggleClass(value: CarrierClass) {
    setClasses((current) =>
      current.includes(value)
        ? current.filter((c) => c !== value)
        : [...current, value],
    );
  }

  function toggleBody(value: BodyType) {
    setBodies((current) =>
      current.includes(value)
        ? current.filter((b) => b !== value)
        : [...current, value],
    );
  }

  /** Buy a real price for one estimated date, and swap it into the list. */
  async function priceDate(option: TripOption) {
    if (!data) return;
    const key = cellKey(option);
    setPricingCell(key);
    setError(null);
    try {
      const priced = await resolveDate({
        origin: data.origin,
        destination: data.destination,
        depart_date: option.depart_date,
        return_date: option.return_date,
        max_stops: maxStops === "" ? null : Number(maxStops),
      });
      if (priced.length === 0) {
        setError(`No flights are being sold for ${option.depart_date}.`);
        return;
      }
      const real = priced[0];
      setData((current) =>
        current
          ? {
              ...current,
              results: bookableFirst([
                real,
                ...current.results.filter((o) => cellKey(o) !== key),
              ]),
              // The grid has to move too. Leaving the old estimate on the
              // calendar would keep advertising a price we just disproved.
              calendar: current.calendar.map((cell) =>
                cell.depart_date === real.depart_date
                  ? {
                      ...cell,
                      price: real.total_price,
                      stops: real.max_stops,
                      airline: real.outbound.airline,
                      airline_name: real.outbound.airline_name,
                    }
                  : cell,
              ),
              live_requests: current.live_requests + 1,
            }
          : current,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not price that date");
    } finally {
      setPricingCell(null);
    }
  }

  async function runSearch(overrideDepth?: ScanDepth) {
    const activeDepth = overrideDepth ?? depth;
    if (overrideDepth) setDepth(overrideDepth);

    setLoading(true);
    setError(null);

    const codes = airlines
      .split(/[,\s]+/)
      .map((c) => c.trim().toUpperCase())
      .filter(Boolean);

    const body: SearchRequest = {
      origin: origin.trim().toUpperCase(),
      destination: destination.trim().toUpperCase(),
      outbound: { start: outStart, end: outEnd },
      inbound: wantsReturn ? { start: inStart, end: inEnd } : null,
      min_nights: wantsReturn && minNights ? Number(minNights) : null,
      max_nights: wantsReturn && maxNights ? Number(maxNights) : null,
      max_stops: maxStops === "" ? null : Number(maxStops),
      include_airlines: codes.length ? codes : null,
      carrier_classes: classes.length ? classes : null,
      body_types: bodies.length ? bodies : null,
      retiring_only: retiringOnly,
      depth: activeDepth,
      limit: 40,
      wallet: wallet.holdings.length ? wallet : null,
      offers,
    };

    try {
      const result = await search(body);
      setData(result);
      setSelectedDate(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }

  /** The cheapest fare anywhere in the scanned window — the real alternative to
   *  spending points, for anyone whose dates are flexible. */
  const cheapestCash = useMemo(() => {
    if (!data || data.calendar.length === 0) return null;
    // After any card offer, since that is what paying cash would actually cost —
    // and so what the points are really being weighed against.
    const payable = (c: (typeof data.calendar)[number]) =>
      Number(c.effective_price ?? c.price);
    const best = data.calendar.reduce((a, b) => (payable(a) <= payable(b) ? a : b));
    return { price: payable(best), date: best.depart_date };
  }, [data]);

  const visible = useMemo(() => {
    if (!data) return [];
    // Picking a day shows every return that pairs with it; the default view
    // stays a shortlist, since the payload now carries several returns per day
    // and listing them all unprompted would bury the ranking.
    if (selectedDate) {
      return data.results.filter((o) => o.depart_date === selectedDate);
    }
    return data.results.slice(0, 20);
  }, [data, selectedDate]);

  const removed = data ? Object.entries(data.filtered_out) : [];

  /** Filters that can only be judged on a date priced for real. */
  const airlineFilterActive =
    airlines.trim() !== "" ||
    classes.length > 0 ||
    bodies.length > 0 ||
    retiringOnly;

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Flightcraft</h1>
        <p className="mt-1 text-sm text-muted">
          Scan whole months of fares, then filter. Outbound and return ranges are
          independent, so they can sit in different months.
        </p>
      </header>

      {data?.demo_mode && (
        <div className="mb-6 rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm">
          <strong className="font-semibold text-amber-300">Demo data.</strong>{" "}
          <span className="text-amber-100/80">
            These prices are synthetic. For real fares, get a free token from{" "}
            <a
              href="https://app.travelpayouts.com"
              target="_blank"
              rel="noopener noreferrer"
              className="underline underline-offset-2"
            >
              app.travelpayouts.com
            </a>{" "}
            (Profile → API token) and set <code>TRAVELPAYOUTS_TOKEN</code> in{" "}
            <code>.env</code>.
          </span>
        </div>
      )}

      <PointsWallet wallet={wallet} onChange={setWallet} />
      <OffersPanel offers={offers} onChange={setOffers} currency="inr" />

      <section className="mb-6 rounded-xl border border-border-subtle bg-surface p-5">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="From">
            <input
              value={origin}
              onChange={(e) => setOrigin(e.target.value)}
              maxLength={3}
              className="input uppercase"
            />
          </Field>
          <Field label="To">
            <input
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
              maxLength={3}
              className="input uppercase"
            />
          </Field>
          <Field label="Outbound from">
            <input
              type="date"
              value={outStart}
              onChange={(e) => setOutStart(e.target.value)}
              className="input"
            />
          </Field>
          <Field label="Outbound until">
            <input
              type="date"
              value={outEnd}
              onChange={(e) => setOutEnd(e.target.value)}
              className="input"
            />
          </Field>
        </div>

        <label className="mt-4 flex w-fit cursor-pointer items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={wantsReturn}
            onChange={(e) => setWantsReturn(e.target.checked)}
            className="h-4 w-4 accent-[var(--accent)]"
          />
          Add a return, priced from its own date range
        </label>

        {wantsReturn && (
          <div className="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <Field label="Return from">
              <input
                type="date"
                value={inStart}
                onChange={(e) => setInStart(e.target.value)}
                className="input"
              />
            </Field>
            <Field label="Return until">
              <input
                type="date"
                value={inEnd}
                onChange={(e) => setInEnd(e.target.value)}
                className="input"
              />
            </Field>
            <Field label="Min nights">
              <input
                type="number"
                min={0}
                value={minNights}
                onChange={(e) => setMinNights(e.target.value)}
                placeholder="any"
                className="input"
              />
            </Field>
            <Field label="Max nights">
              <input
                type="number"
                min={0}
                value={maxNights}
                onChange={(e) => setMaxNights(e.target.value)}
                placeholder="any"
                className="input"
              />
            </Field>
          </div>
        )}

        <div className="mt-5 grid gap-4 border-t border-border-subtle pt-5 sm:grid-cols-2 lg:grid-cols-4">
          <Field label="Max stops">
            <select
              value={maxStops}
              onChange={(e) => setMaxStops(e.target.value)}
              className="input"
            >
              <option value="">Any</option>
              <option value="0">Nonstop only</option>
              <option value="1">1 stop or fewer</option>
              <option value="2">2 stops or fewer</option>
            </select>
          </Field>

          <Field label="Airlines (IATA codes)">
            <input
              value={airlines}
              onChange={(e) => setAirlines(e.target.value)}
              placeholder="e.g. 6E, AI"
              className="input uppercase"
            />
          </Field>

          <Field label="Carrier type">
            <div className="flex flex-wrap gap-1.5 pt-1">
              {CARRIER_CLASSES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => toggleClass(value)}
                  className={`rounded-full border px-3 py-1 text-xs transition ${
                    classes.includes(value)
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-border-subtle text-muted hover:border-muted"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </Field>

          <Field label="Scan depth">
            <select
              value={depth}
              onChange={(e) => setDepth(e.target.value as ScanDepth)}
              className="input"
            >
              {DEPTHS.map((d) => (
                <option key={d.value} value={d.value}>
                  {d.label} — {d.hint}
                </option>
              ))}
            </select>
            {estimate && (
              <span className="mt-1 block text-xs text-muted">
                {estimate.depths[depth].live_requests === 0
                  ? `Estimates only across ${estimate.dates_in_window} dates — no live pricing.`
                  : `Prices ${estimate.depths[depth].live_requests} of ${estimate.dates_in_window} dates for real.`}
                {airlineFilterActive &&
                  estimate.depths[depth].live_requests <
                    estimate.dates_in_window && (
                    <span className="text-amber-300">
                      {" "}
                      Airline and aircraft filters only match priced dates — Deep
                      covers more of the month.
                    </span>
                  )}
              </span>
            )}
          </Field>
        </div>

        <div className="mt-5 grid gap-4 border-t border-border-subtle pt-5 sm:grid-cols-2">
          <Field label="Aircraft">
            <div className="flex flex-wrap gap-1.5 pt-1">
              {BODY_TYPES.map((value) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => toggleBody(value)}
                  className={`rounded-full border px-3 py-1 text-xs transition ${
                    bodies.includes(value)
                      ? "border-accent bg-accent/15 text-accent"
                      : "border-border-subtle text-muted hover:border-muted"
                  }`}
                >
                  {BODY_LABEL[value]}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setRetiringOnly((v) => !v)}
                title="A380, 747, A340, 767 — types most fleets are retiring"
                className={`rounded-full border px-3 py-1 text-xs transition ${
                  retiringOnly
                    ? "border-accent bg-accent/15 text-accent"
                    : "border-border-subtle text-muted hover:border-muted"
                }`}
              >
                Fly it before it&apos;s gone
              </button>
            </div>
          </Field>
          <p className="self-end text-xs text-muted">
            Aircraft filters need a real quote, so they only match dates that have
            been priced. Unpriced dates are hidden while one is active.
          </p>
        </div>

        <button
          type="button"
          onClick={() => runSearch()}
          disabled={loading}
          className="mt-5 rounded-lg bg-accent px-5 py-2.5 text-sm font-semibold text-black transition hover:brightness-110 disabled:opacity-50"
        >
          {loading ? "Scanning…" : "Scan fares"}
        </button>
      </section>

      {error && (
        <div className="mb-6 rounded-lg border border-rose-500/40 bg-rose-500/10 p-4 text-sm text-rose-200">
          {error}
        </div>
      )}

      {data && (
        <>
          <div className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
            <span>
              {data.origin} → {data.destination}
            </span>
            <span>
              {visible.length} of {data.total_before_filters} dated options
            </span>
            <span>
              {data.live_requests > 0
                ? `${data.live_requests} date${data.live_requests === 1 ? "" : "s"} priced live`
                : "no live pricing this search"}
              {data.live_cache_hits > 0 && ` · ${data.live_cache_hits} reused`}
            </span>
            {removed.length > 0 && (
              <span>
                hidden:{" "}
                {removed
                  .map(([k, v]) => `${v} ${REJECTION_LABELS[k] ?? k}`)
                  .join(", ")}
              </span>
            )}
            {selectedDate && (
              <button
                type="button"
                onClick={() => setSelectedDate(null)}
                className="rounded border border-border-subtle px-2 py-0.5 hover:border-muted"
              >
                clear {selectedDate}
              </button>
            )}
          </div>

          {data.split_ticket_saving && (
            <div className="mb-6 rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4 text-sm">
              <strong className="font-semibold text-emerald-300">
                Two one-way tickets save{" "}
                {formatMoney(data.split_ticket_saving.saving, data.currency)}.
              </strong>{" "}
              <span className="text-emerald-100/80">
                Booking {data.split_ticket_saving.depart_date} out and{" "}
                {data.split_ticket_saving.return_date} back separately costs{" "}
                {formatMoney(data.split_ticket_saving.two_one_ways, data.currency)},
                against{" "}
                {formatMoney(data.split_ticket_saving.round_trip, data.currency)} for
                the cheapest single return ticket. Both prices are verified.
              </span>
            </div>
          )}

          {data.needs_deep_scan && (
            <div className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent/10 p-4 text-sm">
              <span>
                Dates that have not been priced for real were hidden, because the
                airline and aircraft are unknown until they are.
              </span>
              <button
                type="button"
                onClick={() => runSearch("deep")}
                className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-black"
              >
                Price more dates
              </button>
            </div>
          )}

          {data.warnings.map((warning) => (
            <p
              key={warning}
              className="mb-3 rounded-lg border border-border-subtle bg-surface p-3 text-xs text-muted"
            >
              {warning}
            </p>
          ))}

          <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)]">
            <div>
              <MonthGrid
                cells={data.calendar}
                currency={data.currency}
                selected={selectedDate}
                onSelect={(date) =>
                  setSelectedDate((current) => (current === date ? null : date))
                }
              />
            </div>
            <div>
              <h2 className="mb-3 text-sm font-medium text-muted">
                {selectedDate
                  ? wantsReturn
                    ? `Returns for ${selectedDate} — ${visible.length} option${visible.length === 1 ? "" : "s"}`
                    : `Options on ${selectedDate}`
                  : "Best options"}
              </h2>
              <ResultsList
                options={visible}
                currency={data.currency}
                onPriceDate={priceDate}
                pricingDate={pricingCell}
                wallet={wallet.holdings.length ? wallet : undefined}
                bestCashAlternative={cheapestCash?.price}
                bestCashDate={cheapestCash?.date}
              />
            </div>
          </div>
        </>
      )}
    </main>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </span>
      {children}
    </label>
  );
}
