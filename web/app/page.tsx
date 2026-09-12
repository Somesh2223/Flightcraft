"use client";

import { useMemo, useState } from "react";

import MonthGrid from "@/components/MonthGrid";
import ResultsList from "@/components/ResultsList";
import {
  CarrierClass,
  REJECTION_LABELS,
  ScanDepth,
  SearchRequest,
  SearchResponse,
  search,
} from "@/lib/api";

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

const DEPTHS: { value: ScanDepth; label: string; hint: string }[] = [
  { value: "quick", label: "Quick", hint: "1 call per month" },
  { value: "standard", label: "Standard", hint: "adds every stop count" },
  { value: "deep", label: "Deep", hint: "adds airline per date, ~1 call per day" },
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
  const [depth, setDepth] = useState<ScanDepth>("standard");

  const [data, setData] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  function toggleClass(value: CarrierClass) {
    setClasses((current) =>
      current.includes(value)
        ? current.filter((c) => c !== value)
        : [...current, value],
    );
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
      depth: activeDepth,
      limit: 40,
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

  const visible = useMemo(() => {
    if (!data) return [];
    if (!selectedDate) return data.results;
    return data.results.filter((o) => o.depart_date === selectedDate);
  }, [data, selectedDate]);

  const removed = data ? Object.entries(data.filtered_out) : [];

  return (
    <main className="mx-auto max-w-6xl px-6 py-10">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold tracking-tight">Fareloom</h1>
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
          </Field>
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
            <span>{data.provider_calls} provider calls</span>
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

          {data.needs_deep_scan && (
            <div className="mb-6 flex flex-wrap items-center gap-3 rounded-lg border border-accent/40 bg-accent/10 p-4 text-sm">
              <span>
                Some fares were hidden because this scan depth does not identify
                the airline.
              </span>
              <button
                type="button"
                onClick={() => runSearch("deep")}
                className="rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-black"
              >
                Re-scan deeply
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
                {selectedDate ? `Options on ${selectedDate}` : "Best options"}
              </h2>
              <ResultsList options={visible} currency={data.currency} />
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
