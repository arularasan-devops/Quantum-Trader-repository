"use client";

import { useEffect, useState } from "react";
import {
  getP27Comparison,
  getP27Coverage,
  getP27Strategies,
  getP27Summary,
  type P27ComparisonRow,
  type P27Coverage,
  type P27Strategy,
  type P27Summary,
} from "@/lib/api";

/** HIGHER-TIMEFRAME FUTURES (5m / 15m) — Phase 27, read only.
 *
 * The final futures branch, shown the way it has to be read:
 *
 *   - the verdict comes from the backend gate, never from this file;
 *   - a cohort reaches RESEARCH_LEAD at most, so no row here is tradable and
 *     none of them can be promoted from a screen;
 *   - the funnel is shown as counts per clause, because "nothing survived" and
 *     "nothing was ever positive" are different claims;
 *   - the 1-minute row sits next to 5m and 15m so a reader can see whether the
 *     larger bar changed the cost arithmetic, the hit rate, or neither.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.45)";
const LINE = "rgba(255,255,255,0.08)";

const STATUS_COLOUR: Record<string, string> = {
  RESEARCH_LEAD: GREEN,
  RESEARCH_ONLY: AMBER,
  REJECTED: RED,
  REQUIRES_MORE_DATA: MUTED,
  INSUFFICIENT_HISTORY: MUTED,
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

export default function HigherTimeframePanel() {
  const [sum, setSum] = useState<P27Summary | null>(null);
  const [cov, setCov] = useState<P27Coverage | null>(null);
  const [cmp, setCmp] = useState<P27ComparisonRow[]>([]);
  const [rows, setRows] = useState<P27Strategy[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, c, k, r] = await Promise.all([
          getP27Summary(),
          getP27Coverage(),
          getP27Comparison(),
          getP27Strategies(25),
        ]);
        if (!alive) return;
        setSum(s);
        setCov(c);
        setCmp(Array.isArray(k?.rows) ? k.rows : []);
        setRows(Array.isArray(r?.rows) ? r.rows : []);
      } catch {
        if (alive) setErr("Phase 27 artefacts are not readable from this backend.");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const concl = sum?.conclusion;
  const verdict = concl?.verdict ?? (sum?.available ? "RESEARCH_ONLY" : "NO_RUN");

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[12px] font-semibold tracking-wide">
          HIGHER-TIMEFRAME FUTURES · 5m / 15m{" "}
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
            QUICK MODE — reduced geometry and stress grid, not the authoritative run
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

      {/* The funnel per timeframe: how far the search got, clause by clause. */}
      {(sum?.timeframe_results ?? []).length ? (
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">BARS</th>
                <th className="py-1 pr-2">HYPOTHESES</th>
                <th className="py-1 pr-2">DEV GATE</th>
                {FUNNEL.map(([k, label]) => (
                  <th key={k} className="py-1 pr-2">
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(sum?.timeframe_results ?? []).map((b) => (
                <tr
                  key={b.timeframe_minutes}
                  style={{ borderTop: `1px solid ${LINE}` }}
                >
                  <td className="py-1 pr-2 font-semibold">
                    {b.timeframe_minutes}m
                  </td>
                  <td className="py-1 pr-2">
                    {b.hypotheses_evaluated.toLocaleString()}
                  </td>
                  <td className="py-1 pr-2">
                    {b.candidates_surviving_development}
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

      {/* Ranked cohorts. The failed clause is the point, not the score. */}
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">BARS</th>
                <th className="py-1 pr-2">INSTRUMENT</th>
                <th className="py-1 pr-2">SIDE</th>
                <th className="py-1 pr-2">STOP</th>
                <th className="py-1 pr-2">CONDITIONS</th>
                <th className="py-1 pr-2">DEV R</th>
                <th className="py-1 pr-2">VAL R</th>
                <th className="py-1 pr-2">HOLDOUT R</th>
                <th className="py-1 pr-2">HOLDOUT N</th>
                <th className="py-1 pr-2">PF</th>
                <th className="py-1 pr-2">WF</th>
                <th className="py-1 pr-2">FDR</th>
                <th className="py-1 pr-2">STATUS</th>
                <th className="py-1 pr-2">WHY REFUSED</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy_id} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">{r.timeframe_minutes}m</td>
                  <td className="py-1 pr-2">{r.instrument}</td>
                  <td className="py-1 pr-2">{r.side}</td>
                  <td className="py-1 pr-2">{num(r.stop_band_atr, "×ATR")}</td>
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
          {/* 1m vs 5m vs 15m, unconditional: geometry before any search. */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              UNCONDITIONAL POOL ECONOMICS — 1m reference vs 5m vs 15m
            </div>
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">BARS</th>
                  <th className="py-1 pr-2">INST</th>
                  <th className="py-1 pr-2">STOP</th>
                  <th className="py-1 pr-2">TRADES</th>
                  <th className="py-1 pr-2">T1%</th>
                  <th className="py-1 pr-2">NEEDS</th>
                  <th className="py-1 pr-2">AVG R</th>
                  <th className="py-1 pr-2">COST/RISK</th>
                </tr>
              </thead>
              <tbody>
                {cmp.map((g, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">{g.timeframe_minutes}m</td>
                    <td className="py-1 pr-2">{g.instrument}</td>
                    <td className="py-1 pr-2">{num(g.stop_band_atr, "×")}</td>
                    <td className="py-1 pr-2">
                      {typeof g.trades === "number"
                        ? g.trades.toLocaleString()
                        : "—"}
                    </td>
                    <td className="py-1 pr-2">{num(g.t1_before_sl_pct, "%")}</td>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {num(g.breakeven_t1_pct_before_costs, "%")}
                    </td>
                    <td className="py-1 pr-2" style={{ color: signColour(g.avg_net_r) }}>
                      {num(g.avg_net_r)}
                    </td>
                    <td className="py-1 pr-2">
                      {num(g.cost_as_fraction_of_risk)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Where the bars came from, including the tail bars. */}
          <div>
            <div className="text-[10px] mb-1" style={{ color: MUTED }}>
              AGGREGATION — the same stored 1-minute series, bucketed causally
            </div>
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">INST</th>
                  <th className="py-1 pr-2">BARS</th>
                  <th className="py-1 pr-2">OUT</th>
                  <th className="py-1 pr-2">FULL</th>
                  <th className="py-1 pr-2">SHORT</th>
                  <th className="py-1 pr-2">SPANS SESSION</th>
                </tr>
              </thead>
              <tbody>
                {(cov?.resampled ?? []).map((a, i) => (
                  <tr key={i} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">{a.instrument}</td>
                    <td className="py-1 pr-2">{a.timeframe_minutes}m</td>
                    <td className="py-1 pr-2">{a.output_bars.toLocaleString()}</td>
                    <td className="py-1 pr-2">{a.full_bars.toLocaleString()}</td>
                    <td className="py-1 pr-2">
                      {a.short_bars.toLocaleString()} ({a.short_bar_pct}%)
                    </td>
                    <td
                      className="py-1 pr-2"
                      style={{ color: a.spans_a_session ? RED : GREEN }}
                    >
                      {a.spans_a_session ? "YES" : "no"}
                    </td>
                  </tr>
                ))}
                {(cov?.excluded ?? []).map((x) => (
                  <tr key={x.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {x.instrument}
                    </td>
                    <td className="py-1 pr-2" colSpan={4} style={{ color: MUTED }}>
                      {x.status}: {x.reason}
                    </td>
                    <td />
                  </tr>
                ))}
              </tbody>
            </table>
            {sum?.resample_claim ? (
              <div className="text-[10px] mt-2" style={{ color: MUTED }}>
                {sum.resample_claim}
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
        {sum?.stop_rule ??
          "If neither timeframe survives validation, the untouched holdout, " +
            "walk-forward, cost stress and the multiple-testing correction, the " +
            "search stops and no further strategy phase follows."}{" "}
        {sum?.spread_claim ??
          "No historical two-sided futures book exists, so these net numbers are optimistic by one unmeasured spread."}{" "}
        The ceiling here is {sum?.research_ceiling ?? "RESEARCH_LEAD"}: nothing on this
        panel can place, size or refuse an order, option strategy logic is frozen and
        the option-book capture is untouched.
      </div>
    </div>
  );
}
