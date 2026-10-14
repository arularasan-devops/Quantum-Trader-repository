"use client";

import { useEffect, useState } from "react";
import {
  getP28Coverage,
  getP28Economics,
  getP28Funnel,
  getP28Strategies,
  getP28Summary,
  type P28Coverage,
  type P28Economics,
  type P28Strategy,
  type P28Summary,
  type P28VehicleBlock,
} from "@/lib/api";

/** MULTI-DAY FUTURES & CASH EQUITY — Phase 28, read only.
 *
 * The first study in this programme that holds a position overnight, so the
 * panel has to carry three things the intraday panels never had to:
 *
 *   - the cash-equity half can be UNANSWERED. A machine with no five-year
 *     stock history must render INSUFFICIENT_HISTORY, not an empty strategy
 *     table that reads as "stocks were tested and failed";
 *   - a long-only rule on a rising instrument earns drift it did not create,
 *     so buy-and-hold sits in the row itself, not in a footnote;
 *   - the daily cost/risk column belongs next to Phase 27's intraday one,
 *     because "the longer hold fixed the cost" and "the longer hold found an
 *     edge" are separate claims and only the first one is true here.
 *
 * The verdict comes from the backend gate. Nothing on this panel can promote
 * a cohort, and the ceiling stays RESEARCH_LEAD.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.45)";
const LINE = "rgba(255,255,255,0.08)";

const STATUS_COLOUR: Record<string, string> = {
  VALIDATED: GREEN,
  RESEARCH_LEAD: GREEN,
  RESEARCH_ONLY: AMBER,
  REJECTED: RED,
  REQUIRES_MORE_DATA: MUTED,
  INSUFFICIENT_HISTORY: MUTED,
  NO_RUN: MUTED,
};

const FUNNEL: [string, string][] = [
  ["cohorts_ranked", "RANKED"],
  ["positive_development", "DEV +"],
  ["positive_validation", "VAL +"],
  ["positive_holdout", "HOLDOUT +"],
  ["positive_all_three_windows", "ALL THREE +"],
  ["holdout_sample_adequate", "SAMPLE OK"],
  ["walk_forward_stable", "EVERY FOLD +"],
  ["survives_every_stress_variant", "STRESS OK"],
  ["beats_buy_and_hold_in_holdout", "BEATS PASSIVE"],
  ["fdr_survivors", "FDR OK"],
  ["research_leads", "LEADS"],
];

function num(v: number | null | undefined, suffix = ""): string {
  return typeof v === "number" ? `${v}${suffix}` : "—";
}

function signColour(v: number | null | undefined): string {
  if (typeof v !== "number" || v === 0) return MUTED;
  return v > 0 ? GREEN : RED;
}

export default function MultiDayPanel() {
  const [sum, setSum] = useState<P28Summary | null>(null);
  const [cov, setCov] = useState<P28Coverage | null>(null);
  const [econ, setEcon] = useState<P28Economics | null>(null);
  const [blocks, setBlocks] = useState<P28VehicleBlock[]>([]);
  const [rows, setRows] = useState<P28Strategy[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, c, f, e, r] = await Promise.all([
          getP28Summary(),
          getP28Coverage(),
          getP28Funnel(),
          getP28Economics(),
          getP28Strategies(25),
        ]);
        if (!alive) return;
        setSum(s);
        setCov(c);
        setBlocks(Array.isArray(f?.vehicles) ? f.vehicles : []);
        setEcon(e);
        setRows(Array.isArray(r?.rows) ? r.rows : []);
      } catch {
        if (alive) {
          setErr("Phase 28 artefacts are not readable from this backend.");
        }
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const concl = sum?.conclusion;
  const verdict = concl?.verdict ?? (sum?.available ? "RESEARCH_ONLY" : "NO_RUN");
  const equity = sum?.equity_history ?? cov?.equity_history;
  const hold = econ?.holding_period_comparison;

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[12px] font-semibold tracking-wide">
          MULTI-DAY FUTURES &amp; CASH EQUITY{" "}
          <span style={{ color: MUTED }}>(RESEARCH · PAPER ONLY)</span>
        </div>
        <span
          className="text-[10px] font-semibold px-2 py-[2px] rounded-md"
          style={{
            color: STATUS_COLOUR[verdict] ?? MUTED,
            border: `1px solid ${STATUS_COLOUR[verdict] ?? MUTED}55`,
          }}
        >
          {verdict}
        </span>
        {concl?.stop_rule_triggered ? (
          <span className="text-[10px]" style={{ color: AMBER }}>
            pre-committed stopping rule triggered
          </span>
        ) : null}
        {sum?.quick_mode ? (
          <span className="text-[10px]" style={{ color: AMBER }}>
            QUICK MODE — reduced grid, not the authoritative run
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

      {concl?.headline ? (
        <div
          className="rounded-md p-2 mb-3 text-[11px]"
          style={{ border: `1px solid ${LINE}` }}
        >
          <div
            className="font-semibold"
            style={{ color: STATUS_COLOUR[verdict] ?? MUTED }}
          >
            {concl.headline}
          </div>
          {concl.statement ? (
            <div className="mt-1" style={{ color: MUTED }}>
              {concl.statement}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* The stock half is a data status, never a strategy result. */}
      {equity ? (
        <div
          className="rounded-md p-2 mb-3 text-[11px]"
          style={{ border: `1px solid ${LINE}` }}
        >
          <span
            className="text-[10px] font-semibold px-2 py-[2px] rounded-md mr-2"
            style={{
              color: STATUS_COLOUR[equity.status] ?? MUTED,
              border: `1px solid ${STATUS_COLOUR[equity.status] ?? MUTED}55`,
            }}
          >
            CASH EQUITY · {equity.status}
          </span>
          <span style={{ color: MUTED }}>
            {equity.status === "INSUFFICIENT_HISTORY"
              ? equity.note ??
                "no cash-equity name has five years of daily history here, so the stock half is unanswered rather than answered negatively"
              : `${(equity.tested ?? []).length} name(s) tested, ${
                  (equity.missing ?? []).length
                } without five years of history`}
          </span>
          {equity.how_to_answer_it ? (
            <div className="mt-1 text-[10px]" style={{ color: MUTED }}>
              {equity.how_to_answer_it}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* Per-vehicle counts. Independent tallies, not a funnel. */}
      {blocks.length ? (
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">VEHICLE</th>
                <th className="py-1 pr-2">DEV GATE</th>
                {FUNNEL.map(([k, label]) => (
                  <th key={k} className="py-1 pr-2">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {blocks.map((b) => (
                <tr key={b.vehicle} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2 font-semibold">{b.vehicle}</td>
                  <td className="py-1 pr-2">
                    {num(b.candidates_surviving_development)}
                  </td>
                  {FUNNEL.map(([k]) => (
                    <td
                      key={k}
                      className="py-1 pr-2"
                      style={{
                        color:
                          k === "research_leads" && (b.diagnostics?.[k] ?? 0) > 0
                            ? GREEN
                            : undefined,
                      }}
                    >
                      {num(b.diagnostics?.[k])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* Ranked cohorts. The failed clause and the passive comparison are
          the point, not the score. */}
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">VEHICLE</th>
                <th className="py-1 pr-2">INSTRUMENT</th>
                <th className="py-1 pr-2">SIDE</th>
                <th className="py-1 pr-2">STOP</th>
                <th className="py-1 pr-2">HOLD</th>
                <th className="py-1 pr-2">CONDITIONS</th>
                <th className="py-1 pr-2">DEV R</th>
                <th className="py-1 pr-2">VAL R</th>
                <th className="py-1 pr-2">HOLDOUT R</th>
                <th className="py-1 pr-2">HOLDOUT N</th>
                <th className="py-1 pr-2">PF</th>
                <th className="py-1 pr-2">WF</th>
                <th className="py-1 pr-2">VS PASSIVE</th>
                <th className="py-1 pr-2">FDR</th>
                <th className="py-1 pr-2">STATUS</th>
                <th className="py-1 pr-2">WHY REFUSED</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy_id} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">{r.vehicle}</td>
                  <td className="py-1 pr-2">{r.instrument}</td>
                  <td className="py-1 pr-2">{r.side}</td>
                  <td className="py-1 pr-2">{num(r.stop_band_atr, "×ATR")}</td>
                  <td className="py-1 pr-2">
                    {num(r.horizon_trading_days, "d")}
                  </td>
                  <td className="py-1 pr-2" style={{ maxWidth: 240 }}>
                    {(r.conditions ?? []).join(" + ")}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: signColour(r.development?.avg_net_r) }}
                  >
                    {num(r.development?.avg_net_r)}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: signColour(r.validation?.avg_net_r) }}
                  >
                    {num(r.validation?.avg_net_r)}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: signColour(r.holdout?.avg_net_r) }}
                  >
                    {num(r.holdout?.avg_net_r)}
                  </td>
                  <td className="py-1 pr-2">{num(r.holdout?.trades)}</td>
                  <td className="py-1 pr-2">{num(r.holdout?.profit_factor)}</td>
                  <td className="py-1 pr-2">
                    {typeof r.walk_forward?.folds_positive === "number"
                      ? `${r.walk_forward.folds_positive}/${
                          r.walk_forward.folds_scored ?? "—"
                        }`
                      : "—"}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{
                      color: r.benchmark_holdout?.beats_benchmark
                        ? GREEN
                        : MUTED,
                    }}
                  >
                    {r.benchmark_holdout
                      ? `${
                          r.benchmark_holdout.beats_benchmark ? "beats" : "loses to"
                        } ${r.benchmark_holdout.benchmark ?? "passive"}`
                      : "—"}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: r.fdr_survivor ? GREEN : MUTED }}
                  >
                    {r.fdr_survivor ? "pass" : "fail"}
                    {typeof r.fdr_denominator === "number"
                      ? ` /${r.fdr_denominator}`
                      : ""}
                  </td>
                  <td
                    className="py-1 pr-2 font-semibold"
                    style={{ color: STATUS_COLOUR[r.status] ?? MUTED }}
                  >
                    {r.status}
                  </td>
                  <td className="py-1 pr-2" style={{ color: MUTED, maxWidth: 300 }}>
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
          {/* Did the longer hold fix the cost arithmetic? Separate question. */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              HOLDING PERIOD — 5m / 15m (Phase 27) vs daily horizons
            </div>
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">HOLD</th>
                  <th className="py-1 pr-2">INST</th>
                  <th className="py-1 pr-2">STOP</th>
                  <th className="py-1 pr-2">RISK PTS</th>
                  <th className="py-1 pr-2">COST PTS</th>
                  <th className="py-1 pr-2">COST/RISK</th>
                  <th className="py-1 pr-2">AVG R</th>
                </tr>
              </thead>
              <tbody>
                {(hold?.rows ?? []).map((g, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">{g.holding_period}</td>
                    <td className="py-1 pr-2">{g.instrument}</td>
                    <td className="py-1 pr-2">{num(g.stop_band_atr, "×")}</td>
                    <td className="py-1 pr-2">{num(g.median_risk_points)}</td>
                    <td className="py-1 pr-2">{num(g.median_cost_points)}</td>
                    <td className="py-1 pr-2">
                      {num(g.cost_as_fraction_of_risk)}
                    </td>
                    <td
                      className="py-1 pr-2"
                      style={{ color: signColour(g.avg_net_r) }}
                    >
                      {num(g.avg_net_r)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="text-[10px] mt-2" style={{ color: MUTED }}>
              median cost/risk intraday{" "}
              {num(hold?.median_cost_over_risk_intraday)} vs daily{" "}
              {num(hold?.median_cost_over_risk_daily)} — cheaper to hold, still
              not directional.
            </div>
          </div>

          {/* Where the daily bars came from, and who was excluded. */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              DAILY BARS — source, span and thin sessions
            </div>
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">INST</th>
                  <th className="py-1 pr-2">VEHICLE</th>
                  <th className="py-1 pr-2">SOURCE</th>
                  <th className="py-1 pr-2">BARS</th>
                  <th className="py-1 pr-2">YEARS</th>
                  <th className="py-1 pr-2">THIN</th>
                </tr>
              </thead>
              <tbody>
                {(cov?.usable ?? []).map((a) => (
                  <tr key={a.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">{a.instrument}</td>
                    <td className="py-1 pr-2">{a.vehicle}</td>
                    <td className="py-1 pr-2">{a.source}</td>
                    <td className="py-1 pr-2">
                      {a.daily_bars.toLocaleString()}
                    </td>
                    <td className="py-1 pr-2">{num(a.span_years)}</td>
                    <td className="py-1 pr-2">{num(a.thin_sessions)}</td>
                  </tr>
                ))}
                {(cov?.excluded ?? []).slice(0, 12).map((x) => (
                  <tr key={x.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {x.instrument}
                    </td>
                    <td className="py-1 pr-2" colSpan={5} style={{ color: MUTED }}>
                      {x.status}: {x.reason}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {(cov?.excluded ?? []).length > 12 ? (
              <div className="text-[10px] mt-1" style={{ color: MUTED }}>
                +{(cov?.excluded ?? []).length - 12} more excluded on history —
                a data status, never a strategy result.
              </div>
            ) : null}
          </div>
        </div>
      ) : null}

      {concl?.what_this_does_not_say?.length ? (
        <ul className="text-[10px] mt-2 list-disc pl-4" style={{ color: MUTED }}>
          {concl.what_this_does_not_say.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      ) : null}

      <div className="text-[9px] mt-2" style={{ color: MUTED }}>
        {sum?.buy_and_hold_claim ??
          "a long-only rule on a rising instrument earns drift it did not create, so buy-and-hold over the same window is the bar it has to clear."}{" "}
        {sum?.gap_claim ??
          "a daily stop is not a limit order: every bar after entry is checked at its open first, and a gap through the level fills at the open."}{" "}
        {sum?.rollover_claim ??
          "a futures hold across expiry is charged one extra round trip per crossing."}{" "}
        The ceiling here is {sum?.research_ceiling ?? "RESEARCH_LEAD"}: nothing on
        this panel can place, size or refuse an order, option strategy logic is
        frozen and the option-book capture is untouched.
      </div>
    </div>
  );
}
