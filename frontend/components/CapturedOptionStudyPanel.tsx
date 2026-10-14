"use client";

import { useEffect, useState } from "react";
import {
  getP25Coverage,
  getP25Cohorts,
  getP25Summary,
  type P25Cohort,
  type P25CohortRow,
  type P25Coverage,
  type P25Summary,
} from "@/lib/api";

/** CAPTURED OPTION CE/PE (RESEARCH) — Phase 25.
 *
 * A read-only view of the captured-window option study. There is no BUY, no
 * arm and no promote here, and the backend behind it can only read artefacts a
 * CLI run wrote.
 *
 * What this panel is built to keep honest:
 *   - the execution model is printed, because it is the whole claim: entry at
 *     the stored ask, exit at the stored bid, real brokerage and statutory
 *     charges, no modelled premium and no mid-price anywhere;
 *   - an instrument whose capture cannot support a chronological split is
 *     REQUIRES_MORE_DATA with its real snapshot counts, never a negative;
 *   - the best possible verdict is RESEARCH_LEAD over a window of weeks —
 *     VALIDATED does not exist in this study and is not rendered;
 *   - every non-lead row shows the exact clause it failed;
 *   - CE and PE are shown side by side, since which vehicle to buy is the
 *     question the five-year futures study could not answer.
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
  NO_RUN: MUTED,
};

function num(v: number | null | undefined, suffix = ""): string {
  return typeof v === "number" ? `${v}${suffix}` : "—";
}

/** Profit factor is a number, or a sentence when a cohort had no loser. */
function pf(v: number | string | null | undefined): string {
  if (typeof v === "number") return String(v);
  return typeof v === "string" ? "no loser" : "—";
}

function signColour(v: number | null | undefined): string {
  if (typeof v !== "number" || v === 0) return MUTED;
  return v > 0 ? GREEN : RED;
}

function cohortCell(c: P25Cohort | undefined): string {
  if (!c || !c.trades) return "—";
  return `${c.trades} tr · T1 ${num(c.t1_before_sl_pct, "%")} · ${num(
    c.avg_net_r,
  )}R · PF ${pf(c.profit_factor)}`;
}

export default function CapturedOptionStudyPanel() {
  const [cov, setCov] = useState<P25Coverage | null>(null);
  const [sum, setSum] = useState<P25Summary | null>(null);
  const [rows, setRows] = useState<P25CohortRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [c, s, r] = await Promise.all([
          getP25Coverage(),
          getP25Summary(),
          getP25Cohorts(25),
        ]);
        if (!alive) return;
        setCov(c);
        setSum(s);
        setRows(Array.isArray(r?.rows) ? r.rows : []);
      } catch {
        if (alive) setErr("Phase 25 artefacts are not readable from this backend.");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const concl = sum?.conclusion;
  const status = concl?.verdict ?? (sum?.available ? "RESEARCH_ONLY" : "NO_RUN");
  const closest = concl?.closest_candidate ?? null;
  const geo = sum?.geometry;
  const instruments = sum?.instruments ?? [];

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[12px] font-semibold tracking-wide">
          CAPTURED OPTION CE/PE{" "}
          <span style={{ color: MUTED }}>(RESEARCH · PAPER ONLY)</span>
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

      {/* The window claim is the ceiling on every number below it. */}
      {sum?.window_claim ? (
        <div className="text-[10px] mb-2" style={{ color: AMBER }}>
          {sum.window_claim}
        </div>
      ) : null}

      {concl ? (
        <div className="text-[11px] mb-3">{concl.headline}</div>
      ) : null}

      {/* The execution model, printed because it is the claim being made. */}
      {geo ? (
        <div
          className="rounded-md p-2 mb-3 text-[10px]"
          style={{ border: `1px solid ${LINE}`, color: MUTED }}
        >
          {geo.entry} · {geo.exit} · slippage {geo.slippage_pct_per_side}% per side ·{" "}
          {geo.costs_charged} · stop bands{" "}
          {(geo.stop_pct_of_premium_bands ?? []).join("/")}% of premium · T1 {geo.t1_r}R ·{" "}
          {geo.split}
        </div>
      ) : null}

      {/* No lead is not a gap: the closest cohort and its failed clauses. */}
      {closest ? (
        <div
          className="rounded-md p-2 mb-3 text-[10px]"
          style={{ border: `1px solid ${LINE}`, color: MUTED }}
        >
          <span style={{ color: AMBER }}>CLOSEST COHORT</span> {closest.instrument}{" "}
          {closest.option_type} · {(closest.conditions ?? []).join(" AND ")} —{" "}
          {closest.status} — failed: {(closest.failed_clauses ?? []).join("; ")}
        </div>
      ) : null}

      {/* CE vs PE per instrument: the question futures history could not answer. */}
      {instruments.length ? (
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">INSTRUMENT</th>
                <th className="py-1 pr-2">STATUS</th>
                <th className="py-1 pr-2">CANDIDATES</th>
                <th className="py-1 pr-2">SESSIONS</th>
                <th className="py-1 pr-2">MED SPREAD</th>
                <th className="py-1 pr-2">MED HURDLE</th>
                <th className="py-1 pr-2">CE</th>
                <th className="py-1 pr-2">PE</th>
              </tr>
            </thead>
            <tbody>
              {instruments.map((r) => (
                <tr key={r.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">{r.instrument}</td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: STATUS_COLOUR[r.status] ?? MUTED }}
                  >
                    {r.status}
                  </td>
                  <td className="py-1 pr-2">
                    {r.pool ? r.pool.candidates.toLocaleString() : "—"}
                  </td>
                  <td className="py-1 pr-2">{r.pool ? r.pool.sessions : "—"}</td>
                  <td className="py-1 pr-2">
                    {num(r.pool?.median_measured_spread_pct, "%")}
                  </td>
                  <td className="py-1 pr-2">{num(r.pool?.median_hurdle_pct, "%")}</td>
                  <td className="py-1 pr-2">{cohortCell(r.sides?.CE)}</td>
                  <td className="py-1 pr-2">{cohortCell(r.sides?.PE)}</td>
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
                <th className="py-1 pr-2">INSTRUMENT</th>
                <th className="py-1 pr-2">CE/PE</th>
                <th className="py-1 pr-2">STOP</th>
                <th className="py-1 pr-2">CONDITIONS</th>
                <th className="py-1 pr-2">DEV T1%</th>
                <th className="py-1 pr-2">DEV R</th>
                <th className="py-1 pr-2">VAL R</th>
                <th className="py-1 pr-2">HOLD T1%</th>
                <th className="py-1 pr-2">HOLD R</th>
                <th className="py-1 pr-2">PF</th>
                <th className="py-1 pr-2">DD (R)</th>
                <th className="py-1 pr-2">WF</th>
                <th className="py-1 pr-2">TOP1%</th>
                <th className="py-1 pr-2">STATUS</th>
                <th className="py-1 pr-2">WHY REFUSED</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy_id} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">{r.instrument}</td>
                  <td className="py-1 pr-2">{r.option_type}</td>
                  <td className="py-1 pr-2">
                    {num(r.stop_band_pct_of_premium, "% of prem")}
                  </td>
                  <td className="py-1 pr-2" style={{ maxWidth: 260 }}>
                    {(r.conditions ?? []).join(" + ")}
                  </td>
                  <td className="py-1 pr-2">{num(r.dev_t1_pct, "%")}</td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.dev_avg_net_r) }}>
                    {num(r.dev_avg_net_r)}
                  </td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.val_avg_net_r) }}>
                    {num(r.val_avg_net_r)}
                  </td>
                  <td className="py-1 pr-2">{num(r.holdout_t1_pct, "%")}</td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: signColour(r.holdout_avg_net_r) }}
                  >
                    {num(r.holdout_avg_net_r)}
                  </td>
                  <td className="py-1 pr-2">{pf(r.holdout_profit_factor)}</td>
                  <td className="py-1 pr-2">{num(r.holdout_max_drawdown_r)}</td>
                  <td className="py-1 pr-2">
                    {typeof r.folds_positive === "number"
                      ? `${r.folds_positive}/${r.folds_measurable ?? "—"}`
                      : "—"}
                  </td>
                  <td className="py-1 pr-2">{num(r.outlier_top1_pct, "%")}</td>
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
        <div className="mt-3 grid gap-3">
          {/* Hurdle bands: the one lead the journal already produced, remeasured
              here on stored quotes instead of on an assumed spread. */}
          {instruments.map((r) =>
            r.hurdle_bands?.length ? (
              <div key={`h-${r.instrument}`}>
                <div className="text-[10px] mb-1" style={{ color: MUTED }}>
                  {r.instrument} — BREAK-EVEN HURDLE BANDS (measured bid/ask)
                </div>
                <table className="w-full text-[10px]">
                  <thead style={{ color: MUTED }}>
                    <tr className="text-left">
                      <th className="py-1 pr-2">BAND</th>
                      <th className="py-1 pr-2">TRADES</th>
                      <th className="py-1 pr-2">T1%</th>
                      <th className="py-1 pr-2">AVG NET R</th>
                      <th className="py-1 pr-2">TOTAL NET ₹</th>
                      <th className="py-1 pr-2">PF</th>
                      <th className="py-1 pr-2">STATUS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.hurdle_bands.map((b) => (
                      <tr
                        key={`${r.instrument}-${b.band}`}
                        style={{ borderTop: `1px solid ${LINE}` }}
                      >
                        <td className="py-1 pr-2">{b.band}</td>
                        <td className="py-1 pr-2">{b.trades}</td>
                        <td className="py-1 pr-2">{num(b.t1_before_sl_pct, "%")}</td>
                        <td className="py-1 pr-2" style={{ color: signColour(b.avg_net_r) }}>
                          {num(b.avg_net_r)}
                        </td>
                        <td className="py-1 pr-2">{num(b.avg_net_rupees)}</td>
                        <td className="py-1 pr-2">{pf(b.profit_factor)}</td>
                        <td className="py-1 pr-2" style={{ color: MUTED }}>
                          {b.status ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null,
          )}

          {/* Coverage last: which instruments were studied, and which were only
              reported, with the reason and their real counts. */}
          {cov ? (
            <div>
              <div className="text-[10px] mb-1" style={{ color: MUTED }}>
                CAPTURED BOOK COVERAGE — {cov.note}
              </div>
              {cov.unmeasured ? (
                <div className="text-[10px]" style={{ color: AMBER }}>
                  {cov.cause} — the store is unmeasured, not empty. Retry with the
                  engine stopped.
                </div>
              ) : (
                <table className="w-full text-[10px]">
                  <thead style={{ color: MUTED }}>
                    <tr className="text-left">
                      <th className="py-1 pr-2">INSTRUMENT</th>
                      <th className="py-1 pr-2">BOOKS</th>
                      <th className="py-1 pr-2">SESSIONS</th>
                      <th className="py-1 pr-2">PATHS</th>
                      <th className="py-1 pr-2">EXCLUDED</th>
                      <th className="py-1 pr-2">STATUS</th>
                      <th className="py-1 pr-2">REASON</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...cov.eligible, ...cov.reported_only].map((c) => (
                      <tr key={c.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                        <td className="py-1 pr-2">{c.instrument}</td>
                        <td className="py-1 pr-2">
                          {(c.snapshots ?? c.real_broker_snapshots ?? 0).toLocaleString()}
                        </td>
                        <td className="py-1 pr-2">{num(c.sessions)}</td>
                        <td className="py-1 pr-2">{num(c.contract_paths)}</td>
                        <td className="py-1 pr-2">
                          {`sim ${c.simulator_snapshots_excluded ?? 0} · one-sided ${
                            c.one_sided_legs_excluded ?? 0
                          }`}
                        </td>
                        <td
                          className="py-1 pr-2"
                          style={{ color: STATUS_COLOUR[c.status] ?? MUTED }}
                        >
                          {c.status}
                        </td>
                        <td className="py-1 pr-2" style={{ color: MUTED, maxWidth: 320 }}>
                          {(c.reasons ?? []).join("; ") || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ) : null}

          {sum?.answers?.length ? (
            <div>
              <div className="text-[10px] mb-1" style={{ color: MUTED }}>
                WHAT THE STUDY ANSWERS
              </div>
              {sum.answers.map((a) => (
                <div key={a.question} className="text-[10px] mb-1">
                  <span style={{ color: MUTED }}>{a.question}</span> {a.answer}
                </div>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
