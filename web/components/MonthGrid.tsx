"use client";

import { CalendarCell, formatMoney } from "@/lib/api";

const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function monthKey(iso: string): string {
  return iso.slice(0, 7);
}

function monthLabel(key: string): string {
  const [year, month] = key.split("-").map(Number);
  return new Date(year, month - 1, 1).toLocaleDateString("en-GB", {
    month: "long",
    year: "numeric",
  });
}

/** Monday-indexed weekday, so weekends sit together at the end of the row. */
function weekdayIndex(date: Date): number {
  return (date.getDay() + 6) % 7;
}

function heatColor(t: number): string {
  // Green through yellow to red. Prices read fastest on a ramp people already
  // associate with cheap-to-expensive.
  const hue = Math.round(150 * (1 - t));
  return `hsl(${hue} 62% 42%)`;
}

interface Props {
  cells: CalendarCell[];
  currency: string;
  selected: string | null;
  onSelect: (date: string) => void;
}

export default function MonthGrid({ cells, currency, selected, onSelect }: Props) {
  if (cells.length === 0) return null;

  const prices = cells.map((c) => Number(c.price));
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const span = max - min || 1;

  const byDate = new Map(cells.map((c) => [c.depart_date, c]));
  const months = [...new Set(cells.map((c) => monthKey(c.depart_date)))].sort();

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>Cheapest {formatMoney(min, currency)}</span>
        <div className="flex h-2 w-32 overflow-hidden rounded-full">
          {Array.from({ length: 24 }, (_, i) => (
            <div
              key={i}
              className="flex-1"
              style={{ background: heatColor(i / 23) }}
            />
          ))}
        </div>
        <span>Priciest {formatMoney(max, currency)}</span>
      </div>

      {months.map((key) => {
        const [year, month] = key.split("-").map(Number);
        const daysInMonth = new Date(year, month, 0).getDate();
        const leading = weekdayIndex(new Date(year, month - 1, 1));

        return (
          <section key={key}>
            <h3 className="mb-2 text-sm font-medium text-muted">{monthLabel(key)}</h3>
            <div className="grid grid-cols-7 gap-1">
              {WEEKDAYS.map((day) => (
                <div
                  key={day}
                  className="pb-1 text-center text-[10px] font-medium uppercase tracking-wide text-muted"
                >
                  {day}
                </div>
              ))}

              {Array.from({ length: leading }, (_, i) => (
                <div key={`pad-${i}`} />
              ))}

              {Array.from({ length: daysInMonth }, (_, i) => {
                const day = i + 1;
                const iso = `${key}-${String(day).padStart(2, "0")}`;
                const cell = byDate.get(iso);
                const isSelected = selected === iso;

                if (!cell) {
                  return (
                    <div
                      key={iso}
                      className="rounded-md border border-dashed border-border-subtle/60 p-2 text-right text-[11px] text-muted/40"
                      title="No cached fare for this date"
                    >
                      {day}
                    </div>
                  );
                }

                const price = Number(cell.price);
                const t = (price - min) / span;

                return (
                  <button
                    key={iso}
                    type="button"
                    onClick={() => onSelect(iso)}
                    style={{ background: heatColor(t) }}
                    className={`group rounded-md p-2 text-left transition ${
                      isSelected
                        ? "ring-2 ring-white"
                        : "ring-1 ring-black/20 hover:ring-white/60"
                    }`}
                    title={`${cell.stops} stop${cell.stops === 1 ? "" : "s"}${
                      cell.airline_name ? ` · ${cell.airline_name}` : ""
                    }`}
                  >
                    <div className="text-[10px] text-white/70">{day}</div>
                    <div className="text-xs font-semibold text-white">
                      {formatMoney(price, currency)}
                    </div>
                    <div className="truncate text-[10px] text-white/70">
                      {cell.stops === 0 ? "nonstop" : `${cell.stops} stop`}
                      {cell.airline ? ` · ${cell.airline}` : ""}
                    </div>
                  </button>
                );
              })}
            </div>
          </section>
        );
      })}
    </div>
  );
}
