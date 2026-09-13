"use client";

import { useMemo } from "react";

import { MatrixCell, formatMoney } from "@/lib/api";

/** Departure down the side, return across the top.
 *
 *  A month against a month is around nine hundred pairs, far too many to label
 *  individually — no price fits in an eighteen-pixel square. So the grid shows
 *  the *shape* of the trade-off: where the cheap region sits, how it slants with
 *  trip length, which weeks to avoid. Picking a cell drills into the itinerary.
 *  The calendar can only ever show one row of this. */
function heatColor(t: number): string {
  const hue = Math.round(150 * (1 - t));
  return `hsl(${hue} 62% 42%)`;
}

function shortDay(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
  });
}

function fullDate(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    weekday: "short",
  });
}

function monthOf(iso: string): string {
  return new Date(`${iso}T00:00:00`).toLocaleDateString("en-GB", {
    month: "short",
  });
}

function isWeekend(iso: string): boolean {
  const day = new Date(`${iso}T00:00:00`).getDay();
  return day === 0 || day === 6;
}

interface Props {
  cells: MatrixCell[];
  currency: string;
  selected: { depart: string; ret: string } | null;
  onSelect: (pair: { depart: string; ret: string }) => void;
}

export default function TripMatrix({
  cells,
  currency,
  selected,
  onSelect,
}: Props) {
  const model = useMemo(() => {
    const departs = [...new Set(cells.map((c) => c.depart_date))].sort();
    const returns = [...new Set(cells.map((c) => c.return_date))].sort();
    const byPair = new Map(
      cells.map((c) => [`${c.depart_date}|${c.return_date}`, c]),
    );
    const prices = cells.map((c) => Number(c.effective_price));
    const min = Math.min(...prices);
    const max = Math.max(...prices);
    const best = cells.reduce((a, b) =>
      Number(a.effective_price) <= Number(b.effective_price) ? a : b,
    );
    return { departs, returns, byPair, min, max, span: max - min || 1, best };
  }, [cells]);

  if (cells.length === 0) return null;

  const { departs, returns, byPair, min, max, span, best } = model;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>Cheapest {formatMoney(min, currency)}</span>
        <div className="flex h-2 w-28 overflow-hidden rounded-full">
          {Array.from({ length: 24 }, (_, i) => (
            <div key={i} className="flex-1" style={{ background: heatColor(i / 23) }} />
          ))}
        </div>
        <span>Priciest {formatMoney(max, currency)}</span>
        <span className="text-muted/70">
          {cells.length.toLocaleString()} date pairs
        </span>
      </div>

      <p className="text-xs text-muted">
        Departure down the side, return across the top. Cheapest is{" "}
        <button
          type="button"
          onClick={() =>
            onSelect({ depart: best.depart_date, ret: best.return_date })
          }
          className="underline underline-offset-2 hover:text-accent"
        >
          {fullDate(best.depart_date)} → {fullDate(best.return_date)}
        </button>{" "}
        at {formatMoney(best.effective_price, currency)} ({best.nights} nights).
      </p>

      <div className="overflow-x-auto pb-2">
        <table className="border-separate border-spacing-[2px]">
          <thead>
            <tr>
              <th className="sticky left-0 z-10 bg-surface" />
              {returns.map((ret, i) => {
                const newMonth = i === 0 || monthOf(ret) !== monthOf(returns[i - 1]);
                return (
                  <th
                    key={ret}
                    className={`w-5 min-w-5 pb-1 text-center text-[9px] font-normal ${
                      isWeekend(ret) ? "text-white/70" : "text-muted"
                    }`}
                    title={fullDate(ret)}
                  >
                    {newMonth ? (
                      <span className="text-accent">{monthOf(ret)}</span>
                    ) : (
                      shortDay(ret)
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {departs.map((depart) => (
              <tr key={depart}>
                <th
                  scope="row"
                  className={`sticky left-0 z-10 bg-surface pr-2 text-right text-[10px] font-normal whitespace-nowrap ${
                    isWeekend(depart) ? "text-white/80" : "text-muted"
                  }`}
                >
                  {fullDate(depart)}
                </th>
                {returns.map((ret) => {
                  const cell = byPair.get(`${depart}|${ret}`);
                  if (!cell) {
                    return (
                      <td
                        key={ret}
                        className="h-5 w-5 rounded-[2px] border border-dashed border-border-subtle/40"
                        title="Outside the trip length you asked for"
                      />
                    );
                  }
                  const price = Number(cell.effective_price);
                  const isBest = cell === best;
                  const isSelected =
                    selected?.depart === depart && selected?.ret === ret;
                  return (
                    <td key={ret} className="p-0">
                      <button
                        type="button"
                        onClick={() => onSelect({ depart, ret })}
                        style={{ background: heatColor((price - min) / span) }}
                        className={`h-5 w-5 rounded-[2px] transition ${
                          isSelected
                            ? "ring-2 ring-white"
                            : isBest
                              ? "ring-1 ring-white/90"
                              : "ring-1 ring-black/20 hover:ring-white/70"
                        }`}
                        title={`${fullDate(depart)} → ${fullDate(ret)} · ${cell.nights} nights · ${formatMoney(price, currency)} · ${
                          cell.stops === 0 ? "nonstop" : `${cell.stops} stop`
                        }${cell.airline ? ` · ${cell.airline}` : ""}${
                          cell.is_live_quote ? " · verified" : " · estimate"
                        }`}
                      />
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
