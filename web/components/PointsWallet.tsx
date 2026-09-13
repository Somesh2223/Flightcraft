"use client";

import { useEffect, useState } from "react";

import { Holding, LoyaltyProgram, Wallet, listPrograms } from "@/lib/api";

const STORAGE_KEY = "flightcraft.wallet";

/** Balances stay in this browser. They are personal, and the server has no
 *  account to attach them to — it only ever sees them for the length of a
 *  search, to work out which programmes could book the flights it found. */
export function loadWallet(): Wallet {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw) as Wallet;
  } catch {
    // Private windows and blocked site data both land here; an empty wallet is
    // the right answer either way.
  }
  return { holdings: [] };
}

function saveWallet(wallet: Wallet) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(wallet));
  } catch {
    // Nothing depends on persistence; the wallet still works for this session.
  }
}

export default function PointsWallet({
  wallet,
  onChange,
}: {
  wallet: Wallet;
  onChange: (wallet: Wallet) => void;
}) {
  const [programs, setPrograms] = useState<LoyaltyProgram[]>([]);
  const [open, setOpen] = useState(false);
  const [program, setProgram] = useState("");
  const [balance, setBalance] = useState("");

  useEffect(() => {
    listPrograms()
      .then((r) => setPrograms(r.programs.filter((p) => p.books_award_seats)))
      .catch(() => setPrograms([]));
  }, []);

  function update(next: Wallet) {
    saveWallet(next);
    onChange(next);
  }

  function add() {
    const amount = Number(balance);
    if (!program || !Number.isFinite(amount) || amount <= 0) return;
    const holdings: Holding[] = [
      ...wallet.holdings.filter((h) => h.program !== program),
      { program, balance: Math.round(amount) },
    ];
    update({ ...wallet, holdings });
    setProgram("");
    setBalance("");
  }

  function remove(code: string) {
    update({
      ...wallet,
      holdings: wallet.holdings.filter((h) => h.program !== code),
    });
  }

  const labelFor = (code: string) =>
    programs.find((p) => p.code === code)?.label ?? code;

  return (
    <section className="mb-6 rounded-xl border border-border-subtle bg-surface p-5">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between text-left"
      >
        <span className="text-sm font-medium">
          Your points
          {wallet.holdings.length > 0 && (
            <span className="ml-2 text-xs text-muted">
              {wallet.holdings.length} programme
              {wallet.holdings.length === 1 ? "" : "s"}
            </span>
          )}
        </span>
        <span className="text-xs text-muted">{open ? "Hide" : "Edit"}</span>
      </button>

      {wallet.holdings.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-2">
          {wallet.holdings.map((h) => (
            <li
              key={h.program}
              className="flex items-center gap-2 rounded-full border border-border-subtle px-3 py-1 text-xs"
            >
              <span>{labelFor(h.program)}</span>
              <span className="text-muted">{h.balance.toLocaleString()}</span>
              <button
                type="button"
                onClick={() => remove(h.program)}
                className="text-muted hover:text-rose-300"
                aria-label={`Remove ${labelFor(h.program)}`}
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      )}

      {open && (
        <div className="mt-4 space-y-4">
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_140px_auto]">
            <select
              value={program}
              onChange={(e) => setProgram(e.target.value)}
              className="input"
            >
              <option value="">Choose a programme…</option>
              {programs.map((p) => (
                <option key={p.code} value={p.code}>
                  {p.label}
                </option>
              ))}
            </select>
            <input
              type="number"
              min={0}
              value={balance}
              onChange={(e) => setBalance(e.target.value)}
              placeholder="Balance"
              className="input"
            />
            <button
              type="button"
              onClick={add}
              className="rounded-lg border border-accent/50 px-4 py-2 text-sm text-accent transition hover:bg-accent/10"
            >
              Add
            </button>
          </div>

          <label className="block">
            <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-muted">
              What a point is normally worth to you
            </span>
            <input
              type="number"
              step="0.01"
              min={0}
              value={wallet.baseline_per_point ?? ""}
              onChange={(e) =>
                update({
                  ...wallet,
                  baseline_per_point:
                    e.target.value === "" ? null : Number(e.target.value),
                })
              }
              placeholder="e.g. 0.50 — leave blank for no verdict"
              className="input max-w-xs"
            />
            <span className="mt-1 block text-xs text-muted">
              Optional. Without it you get the rupees-per-point figure but no
              judgement, because what counts as good value is yours to decide.
            </span>
          </label>

          <p className="rounded-lg border border-border-subtle bg-surface-raised p-3 text-xs text-muted">
            Balances are kept in this browser only. Flightcraft shows which
            programmes <em>could</em> book a flight — whether an award seat is
            actually free is a separate question no free data source answers, so
            check the airline before counting on it.
          </p>
        </div>
      )}
    </section>
  );
}
