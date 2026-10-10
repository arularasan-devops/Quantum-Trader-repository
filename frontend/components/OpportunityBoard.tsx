"use client";
import { useEffect, useState } from "react";
import { getOpportunities, OpportunityRow } from "../lib/api";

const GREEN = "#19e57f";
const AMBER = "#ffb020";
const MUTED = "#8b97a5";

function num(v: number | null | undefined, d = 1, suffix = ""): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(d) + suffix;
}

/**
 * Where the opportunity is right now, across every watched instrument.
 *
 * Ranked on net-of-cost PREMIUM percent rather than underlying points or
 * "% gainers", because those are not comparable across a Rs 7,900 commodity
 * and a Rs 16 stock option — and because a 4% underlying move that the option
 * cannot follow is not an opportunity.
 *
 * Read-only. Where a moving instrument has no BUY, the blocking gate is printed
 * instead of the row being hidden: a missing signal you cannot explain is
 * indistinguishable from a broken one.
 */
export default function OpportunityBoard({ onPick }: { onPick: (symbol: string) => void }) {
  const [rows, setRows] = useState<OpportunityRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [minScore, setMinScore] = useState(0);
  const [onlyTradeable, setOnlyTradeable] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const d = await getOpportunities();
        if (!alive) return;
        setRows(d.rows || []);
        setMinScore(d.min_score);
        setLoaded(true);
      } catch {
        /* keep last */
      }
    };
    load();
    // 2s, matching the backend's scan cadence: the endpoint only reads cached
    // snapshots, so polling faster than the scan adds load without new data,
    // and polling slower makes a live BUY look late on screen.
    const t = setInterval(load, 2000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  const shown = onlyTradeable ? rows.filter((r) => r.tradeable) : rows;
  const buys = rows.filter((r) => r.actionable).length;
  const worth = rows.filter((r) => r.tradeable).length;

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-2">
      <div className="flex items-center gap-3 text-[11px]">
        <span className="font-bold text-white/80">
          {worth} of {rows.length} instruments are moving enough to pay · {buys} showing BUY
        </span>
        <label className="flex items-center gap-1.5 text-white/55">
          <input
            type="checkbox"
            checked={onlyTradeable}
            onChange={(e) => setOnlyTradeable(e.target.checked)}
          />
          worth trading only
        </label>
        <span className="text-white/30">score floor {minScore}</span>
      </div>

      <div className="flex-1 min-h-0 overflow-auto rounded-xl border border-white/8">
        <table className="w-full text-[11px]">
          <thead className="sticky top-0 bg-[#0e141b] text-white/40 uppercase tracking-wide">
            <tr>
              <th className="px-3 py-2 text-left">Instrument</th>
              <th className="px-3 py-2 text-right">Score</th>
              <th className="px-3 py-2 text-right">Move (net %prem)</th>
              <th className="px-3 py-2 text-right">ATR %</th>
              <th className="px-3 py-2 text-right">ADX</th>
              <th className="px-3 py-2 text-right">Vol</th>
              <th className="px-3 py-2 text-right">Premium</th>
              <th className="px-3 py-2 text-left">Feed</th>
              <th className="px-3 py-2 text-left">Call</th>
              <th className="px-3 py-2 text-left">Why</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => {
              const feedAge = r.feed
                ? Math.max(r.feed.underlying_age_sec ?? 0, r.feed.option_age_sec ?? 0)
                : null;
              const feedStale =
                r.feed != null && feedAge != null && feedAge > r.feed.stale_after_sec;
              return (
                <tr
                  key={r.instrument}
                  onClick={() => onPick(r.instrument)}
                  className="cursor-pointer border-t border-white/5 hover:bg-white/5"
                >
                  <td className="px-3 py-1.5 font-bold text-white/85">{r.instrument_name}</td>
                  <td
                    className="px-3 py-1.5 text-right font-bold tabular-nums"
                    style={{ color: r.tradeable ? GREEN : MUTED }}
                  >
                    {num(r.score, 0)}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {num(r.net_move_pct, 1, "%")}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{num(r.atr_pct, 2, "%")}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{num(r.adx, 0)}</td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {r.volume_surge == null ? "—" : `${r.volume_surge.toFixed(1)}x`}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">
                    {r.premium == null ? "—" : `₹${r.premium.toFixed(1)}`}
                  </td>
                  <td className="px-3 py-1.5">
                    {r.feed == null ? (
                      <span className="text-white/25">—</span>
                    ) : (
                      <span
                        className="font-bold tabular-nums"
                        style={{ color: r.feed.live && !feedStale ? GREEN : AMBER }}
                        title={r.feed.note}
                      >
                        {r.feed.live && !feedStale ? "LIVE" : "DELAYED"} {num(feedAge, 0, "s")}
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5">
                    <span
                      className="font-bold"
                      style={{ color: r.actionable ? GREEN : MUTED }}
                    >
                      {r.action}
                      {r.side ? ` ${r.side}` : ""}
                    </span>
                  </td>
                  {/* The blocking gate, verbatim: a BUY that never appeared is
                      only trustworthy if you can see what stopped it. */}
                  <td className="px-3 py-1.5 text-white/50">
                    {r.actionable ? r.reason : r.withheld_because || r.reason}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {loaded && shown.length === 0 && (
          <div className="p-6 text-center text-[12px] text-white/40">
            {rows.length === 0
              ? "Warming up — instruments appear as their 1-min candles load (Angel rate-limits the history API, so this is gradual)."
              : "Nothing is moving enough to cover costs right now."}
          </div>
        )}
        {!loaded && <div className="p-6 text-center text-[12px] text-white/40">Loading…</div>}
      </div>
    </div>
  );
}
