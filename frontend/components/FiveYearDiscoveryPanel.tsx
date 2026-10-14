"use client";

import { useEffect, useState } from "react";
import {
  getP24Coverage,
  getP24Strategies,
  getP24Summary,
  type P24Coverage,
  type P24Strategy,
  type P24Summary,
} from "@/lib/api";

/** 5-YEAR DISCOVERY (RESEARCH) — Phase 24.
 *
 * A read-only view of a historical study. It is deliberately not a trading
 * screen: there is no BUY, no arm, no promote, and the backend behind it can
 * only read artefacts a CLI run wrote.
 *
 * What this panel is built to keep honest:
 *   - instruments without a five-year series are listed as INSUFFICIENT_HISTORY
 *     with their real bar counts, not evaluated and reported as a negative;
 *   - CE and PE stay REQUIRES_MORE_DATA because no historical option book
 *     exists to buy at the ask and sell at the bid against;
 *   - a strategy shows VALIDATED only if the backend gate said so, and every
 *     other row shows the exact clause it failed;
 *   - the unconditional geometry table is shown next to the discovered rules so
 *     a reader can tell a failed search from geometry that never paid.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.45)";
const LINE = "rgba(255,255,255,0.08)";

const STATUS_COLOUR: Record<string, string> = {
  VALIDATED: GREEN,
  RESEARCH_ONLY: AMBER,
  REJECTED: RED,
  INSUFFICIENT_HISTORY: MUTED,
  REQUIRES_MORE_DATA: MUTED,
};

function num(v: number | null | undefined, suffix = ""): string {
  return typeof v === "number" ? `${v}${suffix}` : "—";
}

function signColour(v: number | null | undefined): string {
  if (typeof v !== "number" || v === 0) return MUTED;
  return v > 0 ? GREEN : RED;
}

export default function FiveYearDiscoveryPanel() {
  const [cov, setCov] = useState<P24Coverage | null>(null);
  const [sum, setSum] = useState<P24Summary | null>(null);
  const [rows, setRows] = useState<P24Strategy[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [c, s, r] = await Promise.all([
          getP24Coverage(),
          getP24Summary(),
          getP24Strategies(25),
        ]);
        if (!alive) return;
        setCov(c);
        setSum(s);
        setRows(Array.isArray(r?.rows) ? r.rows : []);
      } catch {
        if (alive) setErr("Phase 24 artefacts are not readable from this backend.");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const concl = sum?.conclusion;
  const status = concl?.STATUS ?? (sum?.available ? "RESEARCH_ONLY" : "NO_RUN");
  const closest =
    concl?.CLOSEST_CANDIDATE && typeof concl.CLOSEST_CANDIDATE === "object"
      ? concl.CLOSEST_CANDIDATE
      : null;

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[12px] font-semibold tracking-wide">
          5-YEAR DISCOVERY <span style={{ color: MUTED }}>(RESEARCH · PAPER ONLY)</span>
        </div>
        <span
          className="text-[10px] font-semibold px-2 py-[2px] rounded-md"
          style={{
            color: STATUS_COLOUR[status] ?? MUTED,
            border: `1px solid ${STATUS_COLOUR[status] ?? MUTED}55`,
          }}
        >
          {status}
        </span>
        {sum?.hypotheses_evaluated ? (
          <span className="text-[10px]" style={{ color: MUTED }}>
            {sum.hypotheses_evaluated.toLocaleString()} hypotheses evaluated ·{" "}
            {rows.length} cohorts shown
          </span>
        ) : null}
        <button
          className="text-[10px] ml-auto px-2 py-[2px] rounded-md border"
          style={{ borderColor: LINE, color: MUTED }}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? "hide detail" : "show detail"}
        </button>
      </div>

      {err ? (
        <div className="text-[11px]" style={{ color: AMBER }}>
          {err}
        </div>
      ) : null}

      {sum && !sum.available ? (
        <div className="text-[11px]" style={{ color: MUTED }}>
          {sum.note}
        </div>
      ) : null}

      {/* The conclusion, in the exact fields the study is required to answer. */}
      {concl ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-3">
          {[
            ["BEST STRATEGY", concl.BEST_VALIDATED_STRATEGY ?? "NONE"],
            ["T1 BEFORE SL", num(concl.EXPECTED_T1_BEFORE_SL, "%")],
            ["NET EXPECTANCY", num(concl.NET_EXPECTANCY_R, "R")],
            ["PROFIT FACTOR", num(concl.PROFIT_FACTOR)],
            ["FINAL HOLDOUT", concl.FINAL_HOLDOUT ?? "—"],
            ["TRADES / WEEK", num(concl.TRADES_PER_WEEK)],
            ["STATUS", concl.STATUS],
            ["VEHICLES", "FUTURES measured · CE/PE REQUIRES_MORE_DATA"],
          ].map(([k, v]) => (
            <div key={String(k)} className="rounded-md p-2" style={{ border: `1px solid ${LINE}` }}>
              <div className="text-[9px] tracking-wide" style={{ color: MUTED }}>
                {k}
              </div>
              <div className="text-[11px] font-semibold">{String(v)}</div>
            </div>
          ))}
        </div>
      ) : null}

      {/* No survivor is not a gap in the report: the closest candidate and the
          exact clauses it failed are shown instead of a hopeful number. */}
      {closest ? (
        <div
          className="rounded-md p-2 mb-3 text-[10px]"
          style={{ border: `1px solid ${LINE}`, color: MUTED }}
        >
          <span style={{ color: AMBER }}>CLOSEST CANDIDATE</span>{" "}
          {closest.instrument} {closest.side} stop {closest.stop_band_atr}×ATR ·{" "}
          {(closest.conditions ?? []).join(" AND ")} — failed:{" "}
          {(closest.why_it_failed ?? []).join("; ")}
        </div>
      ) : null}

      {/* Ranked cohorts. Nothing here is tradable; the failed clause is the point. */}
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">INSTRUMENT</th>
                <th className="py-1 pr-2">SIDE</th>
                <th className="py-1 pr-2">STOP</th>
                <th className="py-1 pr-2">CONDITIONS</th>
                <th className="py-1 pr-2">DEV T1%</th>
                <th className="py-1 pr-2">DEV R</th>
                <th className="py-1 pr-2">HOLDOUT T1%</th>
                <th className="py-1 pr-2">HOLDOUT R</th>
                <th className="py-1 pr-2">PF</th>
                <th className="py-1 pr-2">DD (R)</th>
                <th className="py-1 pr-2">WF</th>
                <th className="py-1 pr-2">T/WK</th>
                <th className="py-1 pr-2">STATUS</th>
                <th className="py-1 pr-2">WHY REFUSED</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy_id} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">{r.instrument}</td>
                  <td className="py-1 pr-2">{r.side}</td>
                  <td className="py-1 pr-2">{num(r.stop_band_atr, "×ATR")}</td>
                  <td className="py-1 pr-2" style={{ maxWidth: 260 }}>
                    {(r.conditions ?? []).join(" + ")}
                  </td>
                  <td className="py-1 pr-2">{num(r.dev_t1_pct, "%")}</td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.dev_avg_net_r) }}>
                    {num(r.dev_avg_net_r)}
                  </td>
                  <td className="py-1 pr-2">{num(r.holdout_t1_pct, "%")}</td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.holdout_avg_net_r) }}>
                    {num(r.holdout_avg_net_r)}
                  </td>
                  <td className="py-1 pr-2">{num(r.holdout_profit_factor)}</td>
                  <td className="py-1 pr-2">{num(r.holdout_max_drawdown_r)}</td>
                  <td className="py-1 pr-2">
                    {typeof r.folds_positive === "number"
                      ? `${r.folds_positive}/${r.folds_scored ?? "—"}`
                      : "—"}
                  </td>
                  <td className="py-1 pr-2">{num(r.trades_per_week)}</td>
                  <td
                    className="py-1 pr-2 font-semibold"
                    style={{ color: STATUS_COLOUR[r.status] ?? MUTED }}
                  >
                    {r.status}
                  </td>
                  <td className="py-1 pr-2" style={{ color: MUTED, maxWidth: 320 }}>
                    {(r.failed_clauses ?? []).join("; ") || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {open ? (
        <div className="mt-3 grid md:grid-cols-2 gap-3">
          {/* Geometry before any condition: is the failure in the search or the maths? */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              UNCONDITIONAL GEOMETRY — every candidate, no rule applied
            </div>
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">INST</th>
                  <th className="py-1 pr-2">STOP</th>
                  <th className="py-1 pr-2">T1</th>
                  <th className="py-1 pr-2">TRADES</th>
                  <th className="py-1 pr-2">T1%</th>
                  <th className="py-1 pr-2">NEEDS</th>
                  <th className="py-1 pr-2">AVG R</th>
                  <th className="py-1 pr-2">COST/RISK</th>
                </tr>
              </thead>
              <tbody>
                {(sum?.geometry_sweep ?? []).map((g, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">{g.instrument}</td>
                    <td className="py-1 pr-2">{g.stop_band_atr}×</td>
                    <td className="py-1 pr-2">{g.t1_r}R</td>
                    <td className="py-1 pr-2">{g.trades.toLocaleString()}</td>
                    <td className="py-1 pr-2">{g.t1_before_sl_pct}%</td>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {g.breakeven_t1_pct_before_costs}%
                    </td>
                    <td className="py-1 pr-2" style={{ color: signColour(g.avg_net_r) }}>
                      {g.avg_net_r}
                    </td>
                    <td className="py-1 pr-2">{g.cost_as_fraction_of_risk}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Coverage: what could be studied, and what could not. */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              DATA COVERAGE — what a five-year claim can honestly rest on
            </div>
            <table className="w-full text-[10px]">
              <tbody>
                {(cov?.series?.usable ?? []).map((u) => (
                  <tr key={u.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2" style={{ color: GREEN }}>
                      USABLE
                    </td>
                    <td className="py-1 pr-2">{u.instrument}</td>
                    <td className="py-1 pr-2">{u.bars.toLocaleString()} bars</td>
                    <td className="py-1 pr-2">{u.sessions ?? "—"} sessions</td>
                  </tr>
                ))}
                {(cov?.series?.excluded ?? []).map((x) => (
                  <tr key={x.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      INSUFFICIENT
                    </td>
                    <td className="py-1 pr-2">{x.instrument}</td>
                    <td className="py-1 pr-2">{x.bars.toLocaleString()} bars</td>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {x.reason ?? ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {cov?.option_books ? (
              <div className="text-[10px] mt-2" style={{ color: MUTED }}>
                Option books: {cov.option_books.snapshots.toLocaleString()} snapshots ·{" "}
                {cov.option_books.real_broker_snapshots.toLocaleString()} from the real
                broker ·{" "}
                {cov.option_books.snapshots_with_two_sided_book.toLocaleString()} with a
                two-sided book. {cov.option_books.note}
              </div>
            ) : null}
            {cov?.series?.equity_note ? (
              <div className="text-[10px] mt-1" style={{ color: MUTED }}>
                {cov.series.equity_note}
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="text-[9px] mt-2" style={{ color: MUTED }}>
        Research only. Futures results include per-order brokerage, statutory charges and
        configured slippage, but no historical two-sided futures spread exists, so they are
        optimistic by one unmeasured spread. Nothing on this panel can place, size or
        refuse an order, and no strategy is promoted to production from here.
      </div>
    </div>
  );
}
