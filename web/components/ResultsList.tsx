"use client";

import { useState } from "react";

import {
  BookingLink,
  Leg,
  POINTS_VERDICT_STYLE,
  PointsAssessment,
  PriceContext,
  Segment,
  TripOption,
  VERDICT_STYLE,
  Wallet,
  assessPoints,
  bookingLinks,
  formatDuration,
  formatMoney,
  formatRate,
  relativeAge,
} from "@/lib/api";

const KIND_LABEL: Record<TripOption["kind"], string> = {
  one_way: "One way",
  round_trip: "Return fare",
  combined_one_ways: "Two one-ways",
};

const CLASS_LABEL: Record<string, string> = {
  low_cost: "budget",
  full_service: "full service",
  hybrid: "hybrid",
  unknown: "",
};

function shortDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    weekday: "short",
  });
}

function clockTime(iso: string | null): string | null {
  if (!iso) return null;
  return new Date(iso).toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

function SegmentRow({ segment }: { segment: Segment }) {
  const depart = clockTime(segment.departure_local);
  const arrive = clockTime(segment.arrival_local);

  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-xs text-muted">
      <span className="font-mono text-foreground">{segment.flight_number}</span>
      <span>
        {segment.origin}→{segment.destination}
      </span>
      {depart && arrive && (
        <span>
          {depart}–{arrive}
        </span>
      )}
      {segment.aircraft && (
        <span
          className="rounded bg-surface-raised px-1.5 py-0.5 text-[10px]"
          title={`${segment.aircraft_family ?? "Unclassified"} · ${segment.aircraft_body}`}
        >
          {segment.aircraft}
        </span>
      )}
      {segment.carrier_name && <span className="truncate">{segment.carrier_name}</span>}
    </div>
  );
}

function LegRow({ leg, label }: { leg: Leg; label: string }) {
  const duration = formatDuration(leg.duration_minutes);

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-baseline gap-2 text-sm">
        <span className="w-12 shrink-0 text-xs uppercase tracking-wide text-muted">
          {label}
        </span>
        <span className="font-medium">{shortDate(leg.depart_date)}</span>
        <span className="text-muted">
          {leg.origin}→{leg.destination}
        </span>
        <span className="text-muted">
          {leg.stops === 0
            ? "nonstop"
            : `${leg.stops} stop${leg.stops === 1 ? "" : "s"}`}
        </span>
        {duration && <span className="text-muted">{duration}</span>}
        {leg.segments.length === 0 && leg.airline_name && (
          <span className="truncate">
            {leg.airline_name}
            {CLASS_LABEL[leg.carrier_class] && (
              <span className="text-muted"> · {CLASS_LABEL[leg.carrier_class]}</span>
            )}
          </span>
        )}
      </div>
      {leg.segments.length > 0 && (
        <div className="ml-12 space-y-0.5 border-l border-border-subtle pl-3">
          {leg.segments.map((segment, i) => (
            <SegmentRow key={`${segment.flight_number}-${i}`} segment={segment} />
          ))}
        </div>
      )}
    </div>
  );
}

function PriceSignal({ context }: { context: PriceContext }) {
  // A verdict with no history behind it would be a guess dressed as advice, so
  // the unknown case shows how far off a real signal is instead.
  const style = VERDICT_STYLE[context.verdict];
  const detail =
    context.verdict === "unknown"
      ? `${context.samples} observation${context.samples === 1 ? "" : "s"} so far`
      : context.note;

  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <span
        className={`rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${style.className}`}
      >
        {style.label}
      </span>
      <span className="text-[11px] text-muted">{detail}</span>
      {context.confidence !== "none" && context.confidence !== "high" && (
        <span className="text-[11px] text-muted/70">
          ({context.confidence} confidence)
        </span>
      )}
    </div>
  );
}

function BookingPanel({
  option,
  currency,
}: {
  option: TripOption;
  currency: string;
}) {
  const [links, setLinks] = useState<BookingLink[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    if (!option.provider_ref) return;
    setLoading(true);
    setError(null);
    try {
      const response = await bookingLinks(option.provider_ref);
      setLinks(response.links);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load sellers");
    } finally {
      setLoading(false);
    }
  }

  if (links === null) {
    return (
      <div className="mt-2 text-right">
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-black transition hover:brightness-110 disabled:opacity-60"
        >
          {loading ? "Checking sellers…" : "Where to book"}
        </button>
        {error && <p className="mt-1 text-[11px] text-amber-300">{error}</p>}
      </div>
    );
  }

  if (links.length === 0) {
    return (
      <p className="mt-2 text-right text-[11px] text-muted">
        No seller is listing this fare right now.
      </p>
    );
  }

  const searchPrice = Number(option.total_price);

  return (
    <div className="mt-2 space-y-1">
      {links.map((link) => {
        const price = link.price === null ? null : Number(link.price);
        // The checkout price and the search price genuinely differ, so the gap
        // is shown rather than hidden behind the more flattering number.
        const delta = price === null ? null : price - searchPrice;
        return (
          <a
            key={link.url}
            href={link.url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-baseline justify-end gap-2 text-xs transition hover:text-accent"
          >
            <span className="truncate text-muted">{link.provider_name}</span>
            {price !== null && (
              <span className="font-medium">{formatMoney(price, currency)}</span>
            )}
            {delta !== null && Math.abs(delta) >= 1 && (
              <span
                className={delta > 0 ? "text-amber-300" : "text-emerald-300"}
              >
                {delta > 0 ? "+" : ""}
                {Math.round(delta).toLocaleString()}
              </span>
            )}
          </a>
        );
      })}
    </div>
  );
}

function PointsPanel({
  option,
  wallet,
  currency,
  bestCashAlternative,
  bestCashDate,
}: {
  option: TripOption;
  wallet: Wallet;
  currency: string;
  bestCashAlternative?: number | null;
  bestCashDate?: string | null;
}) {
  // Deliberately not seeded from props. This component mounts on the first
  // render — before any wallet exists, when points_options is still empty — and
  // useState only runs its initialiser once, so a seeded value would stay ""
  // forever and every click would return early. The select looked correct
  // regardless, because a browser shows the first option when React's value
  // matches none of them.
  const [program, setProgram] = useState("");
  const selected = program || option.points_options[0]?.program || "";
  const [points, setPoints] = useState("");
  const [taxes, setTaxes] = useState("");
  const [result, setResult] = useState<PointsAssessment | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (option.points_options.length === 0) return null;

  async function value() {
    const pts = Number(points);
    if (!selected || !Number.isFinite(pts) || pts <= 0) return;
    setBusy(true);
    setError(null);
    try {
      setResult(
        await assessPoints({
          wallet,
          quote: {
            program: selected,
            points: Math.round(pts),
            cash_component: Number(taxes) || 0,
          },
          cash_price: Number(option.total_price),
          best_cash_alternative: bestCashAlternative ?? null,
          best_cash_date: bestCashDate ?? null,
        }),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not value that award");
    } finally {
      setBusy(false);
    }
  }

  const style = result ? POINTS_VERDICT_STYLE[result.verdict] : null;

  return (
    <div className="mt-3 rounded-md border border-border-subtle bg-surface-raised/50 p-3">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted">Bookable with</span>
        {option.points_options.map((e) => (
          <span
            key={e.program}
            className="rounded-full border border-border-subtle px-2 py-0.5"
            title={e.note}
          >
            {e.label} · {e.balance.toLocaleString()}
          </span>
        ))}
      </div>

      <div className="mt-2 flex flex-wrap items-end gap-2">
        {option.points_options.length > 1 && (
          <select
            value={selected}
            onChange={(e) => setProgram(e.target.value)}
            className="input max-w-[180px] py-1 text-xs"
          >
            {option.points_options.map((e) => (
              <option key={e.program} value={e.program}>
                {e.label}
              </option>
            ))}
          </select>
        )}
        <input
          type="number"
          min={0}
          value={points}
          onChange={(e) => setPoints(e.target.value)}
          placeholder="Points quoted"
          className="input w-32 py-1 text-xs"
        />
        <input
          type="number"
          min={0}
          value={taxes}
          onChange={(e) => setTaxes(e.target.value)}
          placeholder="Taxes"
          className="input w-24 py-1 text-xs"
        />
        <button
          type="button"
          onClick={value}
          disabled={busy}
          className="rounded-md border border-accent/50 px-3 py-1 text-xs text-accent transition hover:bg-accent/10 disabled:opacity-60"
        >
          {busy ? "Valuing…" : "Is it worth it?"}
        </button>
      </div>

      {error && <p className="mt-2 text-[11px] text-amber-300">{error}</p>}

      {result && style && (
        <div className="mt-2 space-y-1">
          <span
            className={`inline-block rounded px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide ${style.className}`}
          >
            {style.label}
          </span>
          <p className="text-[11px] leading-relaxed text-muted">{result.note}</p>
          {result.value_per_point_flexible !== null && (
            <p className="text-[11px] text-muted/70">
              {formatRate(result.value_per_point, currency)} per point against this
              date ·{" "}
              {formatRate(result.value_per_point_flexible, currency)} against the
              cheapest date found
            </p>
          )}
        </div>
      )}
    </div>
  );
}

export default function ResultsList({
  options,
  currency,
  onPriceDate,
  pricingDate,
  wallet,
  bestCashAlternative,
  bestCashDate,
}: {
  options: TripOption[];
  currency: string;
  onPriceDate?: (option: TripOption) => void;
  pricingDate?: string | null;
  wallet?: Wallet;
  bestCashAlternative?: number | null;
  bestCashDate?: string | null;
}) {
  if (options.length === 0) {
    return (
      <p className="rounded-lg border border-border-subtle bg-surface p-6 text-sm text-muted">
        Nothing matches these filters yet. Loosening the stop limit or widening the
        date range is usually the quickest fix.
      </p>
    );
  }

  const firstEstimate = options.findIndex((o) => !o.is_live_quote);

  return (
    <ul className="space-y-2">
      {options.map((option, index) => {
        const cellKey = `${option.depart_date}-${option.return_date ?? "ow"}`;
        return (
          <li key={`${cellKey}-${index}`}>
            {index === firstEstimate && firstEstimate > 0 && (
              <p className="mb-2 mt-4 text-xs text-muted">
                Below: dates that looked cheap in the scan but have not been priced
                for real yet. They may not still be available.
              </p>
            )}
            <div className="rounded-lg border border-border-subtle bg-surface p-4 transition hover:border-accent/50">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 flex-1 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-surface-raised px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                      {KIND_LABEL[option.kind]}
                    </span>
                    {option.is_live_quote ? (
                      <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300">
                        Verified fare
                      </span>
                    ) : (
                      <span className="rounded bg-white/5 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                        Estimate
                      </span>
                    )}
                    {option.nights !== null && (
                      <span className="text-xs text-muted">
                        {option.nights} nights
                      </span>
                    )}
                    {index === 0 && option.is_live_quote && (
                      <span className="rounded bg-accent/20 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-accent">
                        Cheapest bookable
                      </span>
                    )}
                  </div>

                  <LegRow leg={option.outbound} label="Out" />
                  {option.inbound && <LegRow leg={option.inbound} label="Back" />}
                  {option.price_context && (
                    <PriceSignal context={option.price_context} />
                  )}
                  {wallet && (
                    <PointsPanel
                      option={option}
                      wallet={wallet}
                      currency={currency}
                      bestCashAlternative={bestCashAlternative}
                      bestCashDate={bestCashDate}
                    />
                  )}
                </div>

                <div className="text-right">
                  <div className="text-lg font-semibold">
                    {formatMoney(option.total_price, currency)}
                  </div>
                  <div className="text-[11px] text-muted">
                    {option.is_live_quote
                      ? "live quote"
                      : `seen ${relativeAge(option.observed_at)}`}
                  </div>

                  {option.is_live_quote && option.provider_ref ? (
                    <BookingPanel option={option} currency={currency} />
                  ) : (
                    <div className="mt-2 space-y-1">
                      {onPriceDate && (
                        <button
                          type="button"
                          onClick={() => onPriceDate(option)}
                          disabled={pricingDate === cellKey}
                          className="rounded-md border border-accent/50 px-3 py-1.5 text-xs font-medium text-accent transition hover:bg-accent/10 disabled:opacity-60"
                        >
                          {pricingDate === cellKey ? "Pricing…" : "Price this date"}
                        </button>
                      )}
                      <div>
                        <a
                          href={option.booking_link}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[11px] text-muted underline underline-offset-2 hover:text-accent"
                        >
                          Search manually
                        </a>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
