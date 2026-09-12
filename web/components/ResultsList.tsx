"use client";

import {
  Leg,
  PriceContext,
  TripOption,
  VERDICT_STYLE,
  formatMoney,
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

function LegRow({ leg, label }: { leg: Leg; label: string }) {
  return (
    <div className="flex items-baseline gap-2 text-sm">
      <span className="w-14 shrink-0 text-xs uppercase tracking-wide text-muted">
        {label}
      </span>
      <span className="font-medium">{shortDate(leg.depart_date)}</span>
      <span className="text-muted">
        {leg.origin}→{leg.destination}
      </span>
      <span className="text-muted">
        {leg.stops === 0 ? "nonstop" : `${leg.stops} stop${leg.stops === 1 ? "" : "s"}`}
      </span>
      {leg.airline_name && (
        <span className="truncate">
          {leg.airline_name}
          {CLASS_LABEL[leg.carrier_class] && (
            <span className="text-muted"> · {CLASS_LABEL[leg.carrier_class]}</span>
          )}
        </span>
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

export default function ResultsList({
  options,
  currency,
}: {
  options: TripOption[];
  currency: string;
}) {
  if (options.length === 0) {
    return (
      <p className="rounded-lg border border-border-subtle bg-surface p-6 text-sm text-muted">
        Nothing matches these filters yet. Loosening the stop limit or widening the
        date range is usually the quickest fix.
      </p>
    );
  }

  return (
    <ul className="space-y-2">
      {options.map((option, index) => (
        <li
          key={`${option.depart_date}-${option.return_date ?? "ow"}-${index}`}
          className="rounded-lg border border-border-subtle bg-surface p-4 transition hover:border-accent/50"
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="min-w-0 flex-1 space-y-1">
              <div className="flex items-center gap-2">
                <span className="rounded bg-surface-raised px-2 py-0.5 text-[10px] uppercase tracking-wide text-muted">
                  {KIND_LABEL[option.kind]}
                </span>
                {option.nights !== null && (
                  <span className="text-xs text-muted">{option.nights} nights</span>
                )}
                {index === 0 && (
                  <span className="rounded bg-emerald-500/15 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-emerald-300">
                    Cheapest
                  </span>
                )}
              </div>

              <LegRow leg={option.outbound} label="Out" />
              {option.inbound && <LegRow leg={option.inbound} label="Back" />}
              {option.price_context && (
                <PriceSignal context={option.price_context} />
              )}
            </div>

            <div className="text-right">
              <div className="text-lg font-semibold">
                {formatMoney(option.total_price, currency)}
              </div>
              <div className="text-[11px] text-muted">
                seen {relativeAge(option.observed_at)}
              </div>
              <a
                href={option.booking_link}
                target="_blank"
                rel="noopener noreferrer"
                className="mt-2 inline-block rounded-md bg-accent px-3 py-1.5 text-xs font-medium text-black transition hover:brightness-110"
              >
                Check live price
              </a>
            </div>
          </div>
        </li>
      ))}
    </ul>
  );
}
