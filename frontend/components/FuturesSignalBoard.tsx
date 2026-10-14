"use client";

import { useEffect, useState } from "react";
import { getFuturesSignal } from "@/lib/api";
import type { InstrumentOption } from "@/lib/types";

type Props = {
  instrument: string;
  instruments: InstrumentOption[];
  onInstrument: (instrument: string) => void;
};

export default function FuturesSignalBoard({ instrument, instruments, onInstrument }: Props) {
  const [data, setData] = useState<Awaited<ReturnType<typeof getFuturesSignal>> | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      setData(await getFuturesSignal(instrument));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, [instrument]);

  const card = data?.card;
  const fields = card ? Object.entries(card as unknown as Record<string, unknown>)
    .filter(([key, value]) => !["checks"].includes(key) && value !== null && value !== undefined && typeof value !== "object") : [];

  return (
    <section className="space-y-4 text-white">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Futures Signal Research</h2>
          <p className="text-sm text-white/50">Point-based risk and contract research. This screen has no order route.</p>
        </div>
        <select value={instrument} onChange={e => onInstrument(e.target.value)}
          className="rounded-lg border border-white/15 bg-black p-2 text-white">
          {instruments.map((item, i) => {
            const raw = item as unknown as Record<string, unknown>;
            const value = String(raw.value ?? raw.symbol ?? raw.instrument ?? raw.code ?? "");
            const label = String(raw.label ?? raw.name ?? value);
            return <option key={`${value}-${i}`} value={value}>{label || value}</option>;
          })}
        </select>
      </div>

      <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.04] p-3 text-sm text-amber-200">
        RESEARCH ONLY · {String(data?.executable ?? false)} executable flag · No paper or live order submission from this board.
      </div>

      {loading && <p className="text-white/60">Loading futures research…</p>}
      {error && <p className="text-red-300">{error}</p>}
      {data?.note && <p className="text-sm text-white/60">{data.note}</p>}

      {!loading && !card && (
        <div className="rounded-xl border border-white/10 p-5">
          <h3 className="font-semibold">No futures plan available</h3>
          <p className="mt-2 text-sm text-white/50">The API did not return a valid research card for {instrument}. No plan has been fabricated.</p>
          <button onClick={() => void load()} className="mt-3 rounded-lg border border-white/15 px-3 py-2 text-sm">Retry</button>
        </div>
      )}

      {card && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ["Signal", String(card.signal ?? "N/A")],
              ["Status", String(card.status ?? "N/A")],
              ["Entry", card.entry ?? "N/A"],
              ["Stop", card.stop ?? "N/A"],
              ["Target 1", card.target1 ?? "N/A"],
              ["Target 2", card.target2 ?? "N/A"],
              ["Risk points", card.risk_points ?? "N/A"],
              ["Risk per lot (₹)", card.risk_rupees_per_lot ?? "N/A"],
            ].map(([label, value]) => (
              <div key={String(label)} className="rounded-xl border border-white/10 bg-white/[0.03] p-4">
                <div className="text-xs text-white/50">{label}</div>
                <div className="mt-2 break-words text-lg font-bold">{String(value)}</div>
              </div>
            ))}
          </div>

          <div className="rounded-xl border border-white/10 p-4">
            <h3 className="mb-3 font-semibold">Plan details</h3>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {fields.map(([key, value]) => (
                <div key={key} className="border-b border-white/10 pb-2">
                  <div className="text-xs text-white/45">{key.replaceAll("_", " ")}</div>
                  <div className="mt-1 break-words text-sm">{String(value)}</div>
                </div>
              ))}
            </div>
            {card.checks?.length > 0 && (
              <div className="mt-4">
                <h4 className="mb-2 text-sm font-semibold">Validation checks</h4>
                {card.checks.map((check, i) => (
                  <div key={`${check.name}-${i}`} className="border-t border-white/10 py-2 text-sm">
                    <span className={check.passed ? "text-emerald-300" : "text-amber-300"}>
                      {check.passed ? "PASS" : "BLOCK"}
                    </span>
                    {" · "}{check.name}: {check.detail || "No additional detail"}
                  </div>
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
