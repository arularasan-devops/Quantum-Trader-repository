"use client";
import { useEffect, useState } from "react";
import { getAutoPick, AutoPick } from "../lib/api";

/**
 * Today's Top Movers — the daily auto-pick, as ONE unified watchlist. Both the
 * always-on core (Crude/Nifty/Sensex) and the broker's biggest F&O movers are
 * shown in the same table with a type tag, % move, direction and LTP. Click a
 * row to open that instrument in a new Decision tab. Refreshes itself and has a
 * manual "Re-pick now" button.
 */
export default function TopMovers({ onPick }: { onPick: (sym: string) => void }) {
  const [data, setData] = useState<AutoPick | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async (refresh = false) => {
    if (typeof document !== "undefined" && document.hidden && !refresh) return;
    setLoading(true);
    try {
      const d = await getAutoPick(refresh);
      setData(d);
    } catch {
      /* keep last */
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const id = setInterval(() => load(false), 15000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = data?.rows || [];
  const movers = rows.filter((r) => r.type === "MOVER");

  return (
    <div className="flex-1 min-h-0 overflow-auto p-5">
      <div className="flex items-center justify-between mb-4">
        <div>
          <div className="text-xl font-bold tracking-tight">Today&apos;s Watchlist</div>
          <div className="text-xs text-white/45 mt-0.5">
            Always-on core + the biggest F&amp;O movers the broker reported today — auto-picked.
          </div>
        </div>
        <button
          onClick={() => load(true)}
          disabled={loading}
          className="text-xs font-semibold px-3.5 py-2 rounded-lg text-white/85 border border-white/15 hover:bg-white/5 disabled:opacity-40"
        >
          {loading ? "Refreshing…" : "Re-pick now"}
        </button>
      </div>

      {data && !data.enabled && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-200/90">
          Auto-pick is <b>off</b>. Enable it in <code>.env</code> with{" "}
          <code>QT_AUTO_PICK_ENABLED=true</code> (and leave <code>QT_INSTRUMENTS</code> blank),
          then restart. It needs the live Angel feed to fetch today&apos;s movers.
        </div>
      )}

      {data && data.enabled && (
        <>
          <div className="text-[11px] uppercase tracking-widest text-white/40 mb-2">
            {rows.length} instruments · {movers.length} movers · source: {data.source || "—"}
          </div>

          <table className="w-full text-sm border-separate" style={{ borderSpacing: "0 6px" }}>
            <thead>
              <tr className="text-[11px] uppercase tracking-widest text-white/40 text-left">
                <th className="px-3 py-1">Instrument</th>
                <th className="px-3 py-1">Type</th>
                <th className="px-3 py-1 text-right">% Change</th>
                <th className="px-3 py-1 text-center">Dir</th>
                <th className="px-3 py-1 text-right">Price / LTP</th>
                <th className="px-3 py-1"></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const hasPct = r.percent_change != null;
                const up = (r.direction || "").toUpperCase() === "UP";
                const isCore = r.type === "CORE";
                return (
                  <tr key={r.symbol} className="bg-white/[0.03] hover:bg-white/[0.07]">
                    <td className="px-3 py-3 font-bold rounded-l-lg">{r.display}</td>
                    <td className="px-3 py-3">
                      <span
                        className={`text-[10px] font-bold px-2 py-0.5 rounded uppercase tracking-wide ${
                          isCore
                            ? "bg-sky-400/15 text-sky-300"
                            : "bg-violet-400/15 text-violet-300"
                        }`}
                      >
                        {isCore ? "Core" : "Mover"}
                      </span>
                    </td>
                    <td
                      className={`px-3 py-3 text-right font-bold tabular-nums text-base ${
                        !hasPct ? "text-white/35" : up ? "text-[#19e57f]" : "text-[#ff6b6b]"
                      }`}
                    >
                      {hasPct
                        ? `${r.percent_change! > 0 ? "+" : ""}${r.percent_change!.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="px-3 py-3 text-center text-base">
                      {hasPct ? (
                        <span className={up ? "text-[#19e57f]" : "text-[#ff6b6b]"}>
                          {up ? "▲" : "▼"}
                        </span>
                      ) : (
                        <span className="text-white/25">—</span>
                      )}
                    </td>
                    <td className="px-3 py-3 text-right tabular-nums font-semibold">
                      {r.ltp ? r.ltp.toLocaleString(undefined, { maximumFractionDigits: 1 }) : "—"}
                    </td>
                    <td className="px-3 py-3 text-right rounded-r-lg">
                      <button
                        onClick={() => onPick(r.symbol)}
                        className="text-xs font-semibold px-3 py-1.5 rounded border border-white/15 hover:bg-white/10"
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {movers.length === 0 && (
            <div className="mt-3 text-xs text-white/50">
              No live movers yet — this needs the Angel feed during market hours. Core instruments
              always show above.
            </div>
          )}

          <div className="text-[11px] text-white/35 mt-6 max-w-2xl">
            A big mover is a good <b>watchlist</b> filter, not a trade signal — the engine still
            decides BUY / stand-aside on each name. Core (Crude/Nifty/Sensex) is always kept; movers
            refresh automatically each trading day. Core % / price come from the live feed.
          </div>
        </>
      )}
    </div>
  );
}
