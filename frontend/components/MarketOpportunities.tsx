"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getMarketOpportunities,
  type MarketOpportunities as Opps,
  type OpportunityRow,
} from "@/lib/api";

/** LIVE MARKET OPPORTUNITIES — research-only fast scan.
 *
 * Answers "where is the market actually moving right now, on data that is
 * actually current", before the expensive engine runs. It is NOT a trade list:
 * the vocabulary is OPPORTUNITY / WATCH / NO_OPPORTUNITY, never BUY, and the
 * scanner cannot reach a gate, a stop, a target or an order.
 *
 * Two things are deliberately visible rather than tidied away: the data age of
 * every row, and the instruments that were EXCLUDED because their feed was
 * stale or their exchange was shut. A mover list that silently ranks a frozen
 * price is worse than no list at all.
 */
const VIEWS: { key: string; label: string }[] = [
  { key: "all", label: "ALL" },
  { key: "movers", label: "TOP MOVERS" },
  { key: "volatility", label: "HIGH VOLATILITY" },
  { key: "fresh", label: "FRESH MOMENTUM" },
  { key: "active", label: "ACTIVE" },
  { key: "extended", label: "EXTENDED" },
  { key: "exhausted", label: "EXHAUSTED" },
];

function pct(v: number | null): string {
  if (v === null || v === undefined) return "—";
  const s = v >= 0 ? "+" : "";
  return `${s}${v.toFixed(2)}%`;
}

function num(v: number | null, dp = 1): string {
  return v === null || v === undefined ? "—" : v.toFixed(dp);
}

function moveColor(v: number | null): string {
  if (v === null || v === undefined) return "text-white/40";
  if (v > 0) return "text-[#19e57f]";
  if (v < 0) return "text-[#ff5c5c]";
  return "text-white/60";
}

function ageLabel(ms: number | null, state: string): { text: string; cls: string } {
  const cls =
    state === "FRESH"
      ? "text-[#19e57f]"
      : state === "AGING"
        ? "text-[#f4c025]"
        : "text-[#ff5c5c]";
  if (ms === null || ms === undefined) return { text: state, cls };
  return { text: ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`, cls };
}

const STATE_CLS: Record<string, string> = {
  FRESH_MOMENTUM: "text-[#19e57f]",
  ACTIVE: "text-[#4da3ff]",
  EXTENDED: "text-[#f4c025]",
  EXHAUSTED: "text-[#ff8f5c]",
  STALLED: "text-white/50",
  NO_DATA: "text-white/40",
};

function Row({ r, rank }: { r: OpportunityRow; rank: number }) {
  const age = ageLabel(r.data_age_ms, r.feed_state);
  return (
    <tr className="border-t border-white/5">
      <td className="py-1 pr-2 text-white/40">{rank}</td>
      <td className="py-1 pr-2 text-white/90 font-semibold">{r.display || r.instrument}</td>
      <td className="py-1 pr-2 text-white/60">{r.direction}</td>
      <td className={"py-1 pr-2 tabular-nums " + moveColor(r.move_1m_pct)}>{pct(r.move_1m_pct)}</td>
      <td className={"py-1 pr-2 tabular-nums " + moveColor(r.move_3m_pct)}>{pct(r.move_3m_pct)}</td>
      <td className={"py-1 pr-2 tabular-nums " + moveColor(r.move_5m_pct)}>{pct(r.move_5m_pct)}</td>
      <td className="py-1 pr-2 text-white/70">{r.volatility_state}</td>
      <td className="py-1 pr-2 text-white/70 tabular-nums">
        {r.relative_volume === null ? (
          <span className="text-white/35">UNKNOWN</span>
        ) : (
          `${r.relative_volume.toFixed(2)}x`
        )}
      </td>
      <td className="py-1 pr-2 text-white/70 tabular-nums">{num(r.momentum_score)}</td>
      <td className="py-1 pr-2 text-white/70 tabular-nums">{num(r.freshness_score)}</td>
      <td className={"py-1 pr-2 tabular-nums " + age.cls}>{age.text}</td>
      <td className="py-1 pr-2 text-white/90 font-bold tabular-nums">
        {num(r.opportunity_score)}
      </td>
      <td className={"py-1 " + (STATE_CLS[r.classification] ?? "text-white/60")}>
        {r.classification}
        {r.verdict === "OPPORTUNITY" && (
          <span className="ml-1 text-[10px] text-[#19e57f]/70">OPP</span>
        )}
      </td>
    </tr>
  );
}

export default function MarketOpportunities() {
  const [d, setD] = useState<Opps | null>(null);
  const [view, setView] = useState("all");

  const refresh = useCallback(async () => {
    try {
      setD(await getMarketOpportunities(15, view));
    } catch {
      /* transient: keep the last reading rather than blanking the panel */
    }
  }, [view]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  if (!d) return null;
  const h = d.market_health;

  // shrink-0: the Research column is a scrolling flex container, which would
  // otherwise squash this card to a single line once the page overflows.
  return (
    <div className="card p-3 flex flex-col gap-2 shrink-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <div className="text-sm font-bold text-white/90">LIVE MARKET OPPORTUNITIES</div>
        <div className="text-[11px] text-white/50">
          research scan · {h.ranked}/{h.instruments_scanned} ranked ·{" "}
          <span className="text-[#19e57f]">{h.opportunities} opportunity</span> ·{" "}
          <span className="text-[#f4c025]">{h.watch} watch</span>
          {h.stale_instruments ? (
            <>
              {" "}· <span className="text-[#ff5c5c]">{h.stale_instruments} stale/dead</span>
            </>
          ) : null}
        </div>
      </div>

      <div className="flex flex-wrap gap-1">
        {VIEWS.map((v) => (
          <button
            key={v.key}
            onClick={() => setView(v.key)}
            className={
              "rounded px-2 py-0.5 text-[10px] border " +
              (view === v.key
                ? "border-white/30 bg-white/10 text-white/90"
                : "border-white/10 text-white/50 hover:text-white/80")
            }
          >
            {v.label}
          </button>
        ))}
      </div>

      {d.rows.length === 0 ? (
        <div className="text-[11px] text-white/50">
          Nothing ranked in this view. With the markets shut or the feed cold this is the
          correct reading — the scanner will not rank an instrument on prices that are not
          current.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-white/40">
              <tr>
                <th className="text-left font-normal pr-2">#</th>
                <th className="text-left font-normal pr-2">instrument</th>
                <th className="text-left font-normal pr-2">dir</th>
                <th className="text-left font-normal pr-2">1m</th>
                <th className="text-left font-normal pr-2">3m</th>
                <th className="text-left font-normal pr-2">5m</th>
                <th className="text-left font-normal pr-2">vol</th>
                <th className="text-left font-normal pr-2">rel vol</th>
                <th className="text-left font-normal pr-2">mom</th>
                <th className="text-left font-normal pr-2">fresh</th>
                <th className="text-left font-normal pr-2">age</th>
                <th className="text-left font-normal pr-2">score</th>
                <th className="text-left font-normal">state</th>
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r, i) => (
                <Row key={r.instrument} r={r} rank={i + 1} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Excluded instruments are shown, not hidden: a name missing from a
          "top movers" list for a data reason is information. */}
      {d.excluded.length > 0 && (
        <div className="text-[10px] text-white/40">
          not ranked ({d.excluded.length}):{" "}
          {d.excluded.slice(0, 12).map((e) => (
            <span key={e.instrument} className="mr-2">
              <span className="text-white/60">{e.instrument}</span>{" "}
              <span className={e.feed_state === "FRESH" ? "" : "text-[#ff5c5c]/70"}>
                {e.reason}
              </span>
            </span>
          ))}
          {d.excluded.length > 12 && <span>+{d.excluded.length - 12} more</span>}
        </div>
      )}

      {/* Scanner rank against what the (unchanged) engine independently said. */}
      {d.handoff.length > 0 && (
        <div className="text-[10px] text-white/45">
          handoff:{" "}
          {d.handoff.slice(0, 8).map((x) => (
            <span key={x.instrument} className="mr-2">
              <span className="text-white/70">
                #{x.scanner_rank} {x.instrument}
              </span>{" "}
              {num(x.scanner_score)} →{" "}
              <span className="text-white/60">{x.final_signal ?? "—"}</span>
            </span>
          ))}
        </div>
      )}

      <div className="text-[10px] text-white/35">{d.note}</div>
    </div>
  );
}
