"use client";

import { useState } from "react";

import { Offer, formatMoney } from "@/lib/api";

const STORAGE_KEY = "fareloom.offers";

/** Offers live in this browser. They are personal to the cards someone holds,
 *  expire within weeks, and no source publishes them — so they are entered from
 *  the bank's own offer page rather than guessed at on the user's behalf. */
export function loadOffers(): Offer[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as Offer[];
  } catch {
    // Private windows and blocked site data both land here.
  }
  return [];
}

function saveOffers(offers: Offer[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(offers));
  } catch {
    // Nothing depends on persistence; offers still work for this session.
  }
}

function isExpired(offer: Offer, today: string): boolean {
  return !!offer.valid_until && offer.valid_until < today;
}

export default function OffersPanel({
  offers,
  onChange,
  currency,
}: {
  offers: Offer[];
  onChange: (offers: Offer[]) => void;
  currency: string;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [percent, setPercent] = useState("");
  const [cap, setCap] = useState("");
  const [flat, setFlat] = useState("");
  const [minSpend, setMinSpend] = useState("");
  const [sellers, setSellers] = useState("");
  const [validUntil, setValidUntil] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);

  const today = new Date().toISOString().slice(0, 10);
  const live = offers.filter((o) => !isExpired(o, today));
  const dead = offers.filter((o) => isExpired(o, today));

  function update(next: Offer[]) {
    saveOffers(next);
    onChange(next);
  }

  function add() {
    const pct = percent === "" ? null : Number(percent);
    const flatAmount = flat === "" ? null : Number(flat);
    if (!label.trim()) {
      setError("Give the offer a name you will recognise.");
      return;
    }
    if (pct === null && flatAmount === null) {
      setError("An offer needs either a percentage or a flat amount.");
      return;
    }
    setError(null);
    update([
      ...offers,
      {
        id: `${Date.now()}`,
        label: label.trim(),
        percent: pct,
        max_discount: cap === "" ? null : Number(cap),
        flat_discount: flatAmount,
        min_spend: minSpend === "" ? null : Number(minSpend),
        sellers: sellers
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        valid_until: validUntil || null,
        promo_code: code.trim() || null,
      },
    ]);
    setLabel("");
    setPercent("");
    setCap("");
    setFlat("");
    setMinSpend("");
    setSellers("");
    setValidUntil("");
    setCode("");
  }

  function remove(id: string) {
    update(offers.filter((o) => o.id !== id));
  }

  function describe(o: Offer): string {
    const bits: string[] = [];
    if (o.percent != null) {
      bits.push(
        o.max_discount != null
          ? `${o.percent}% up to ${formatMoney(o.max_discount, currency)}`
          : `${o.percent}%`,
      );
    }
    if (o.flat_discount != null) bits.push(formatMoney(o.flat_discount, currency));
    if (o.min_spend != null) bits.push(`min ${formatMoney(o.min_spend, currency)}`);
    if (o.sellers.length) bits.push(o.sellers.join("/"));
    if (o.valid_until) bits.push(`to ${o.valid_until}`);
    return bits.join(" · ");
  }

  return (
    <section className="mb-6 rounded-xl border border-border-subtle bg-surface p-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-sm font-medium">
          Card offers
          {live.length > 0 && (
            <span className="ml-2 text-xs text-muted">
              {live.length} active
              {dead.length > 0 && `, ${dead.length} expired`}
            </span>
          )}
        </span>
        <span className="text-xs text-muted">{open ? "Hide" : "Edit"}</span>
      </button>

      {offers.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {offers.map((o) => {
            const gone = isExpired(o, today);
            return (
              <li
                key={o.id}
                className={`flex items-baseline gap-2 text-xs ${gone ? "opacity-50" : ""}`}
              >
                <span className="font-medium">{o.label}</span>
                <span className="text-muted">{describe(o)}</span>
                {gone && (
                  <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-rose-300">
                    expired
                  </span>
                )}
                <button
                  type="button"
                  onClick={() => remove(o.id)}
                  className="ml-auto text-muted hover:text-rose-300"
                  aria-label={`Remove ${o.label}`}
                >
                  ×
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {open && (
        <div className="mt-4 space-y-3">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <input
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="HDFC Infinia on MakeMyTrip"
              className="input lg:col-span-2"
            />
            <input
              type="number"
              value={percent}
              onChange={(e) => setPercent(e.target.value)}
              placeholder="% off"
              className="input"
            />
            <input
              type="number"
              value={cap}
              onChange={(e) => setCap(e.target.value)}
              placeholder="Capped at"
              className="input"
            />
            <input
              type="number"
              value={flat}
              onChange={(e) => setFlat(e.target.value)}
              placeholder="or flat amount"
              className="input"
            />
            <input
              type="number"
              value={minSpend}
              onChange={(e) => setMinSpend(e.target.value)}
              placeholder="Min spend"
              className="input"
            />
            <input
              value={sellers}
              onChange={(e) => setSellers(e.target.value)}
              placeholder="Sellers (blank = any)"
              className="input"
            />
            <input
              type="date"
              value={validUntil}
              onChange={(e) => setValidUntil(e.target.value)}
              className="input"
            />
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Promo code"
              className="input"
            />
            <button
              type="button"
              onClick={add}
              className="rounded-lg border border-accent/50 px-4 py-2 text-sm text-accent transition hover:bg-accent/10"
            >
              Add offer
            </button>
          </div>

          {error && <p className="text-xs text-amber-300">{error}</p>}

          <p className="rounded-lg border border-border-subtle bg-surface-raised p-3 text-xs text-muted">
            Copy the terms from your bank&apos;s own offer page. Fareloom ranks
            dates by what you would actually pay, so an offer can move a pricier
            fare to the top — but it only knows what you tell it, offers are
            assumed not to stack, and an expiry date is enforced rather than
            trusted. Check the discount on the payment page before paying.
          </p>
        </div>
      )}
    </section>
  );
}
