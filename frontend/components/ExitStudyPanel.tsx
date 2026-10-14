"use client";

import { useEffect, useState } from "react";
import {
  getP26Advisory,
  getP26Cohorts,
  getP26Summary,
  type P26Advisory,
  type P26AdvisoryRow,
  type P26CohortRow,
  type P26Summary,
  type P26VariantTotal,
} from "@/lib/api";

/** EXIT MANAGEMENT & TRADABILITY (RESEARCH) — Phase 26.
 *
 * A read-only view of the exit-variant study. There is no BUY, no arm, no
 * promote and no threshold here, and the backend behind it can only read
 * artefacts a CLI run wrote.
 *
 * What this panel is built to keep honest:
 *   - the baseline row is Phase 25's own geometry, always rendered, win or
 *     lose, so a variant is read as a difference and not as a result;
 *   - a variant with no fixed target reaches the horizon by construction, so
 *     the horizon rate and how much of it was in profit are shown separately
 *     from Phase 25's flat timeout;
 *   - the tradability table says on its face that it is advisory and is not
 *     wired to any live refusal, and it shows what a refusal would have given
 *     up as well as what it would have saved;
 *   - the best possible verdict is RESEARCH_LEAD over a window of weeks —
 *     VALIDATED does not exist in this study and is not rendered;
 *   - every non-lead row shows the exact clause it failed.
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
  TRADABLE_ECONOMICS: GREEN,
  MARGINAL_ECONOMICS: AMBER,
  AVOID_ECONOMICS: RED,
};

function num(v: number | null | undefined, suffix = ""): string {
  return typeof v === "number" ? `${v}${suffix}` : "—";
}

function pf(v: number | string | null | undefined): string {
  if (typeof v === "number") return String(v);
  return typeof v === "string" ? "no loser" : "—";
}

function signColour(v: number | null | undefined): string {
  if (typeof v !== "number" || v === 0) return MUTED;
  return v > 0 ? GREEN : RED;
}

function rupees(v: number | null | undefined): string {
  if (typeof v !== "number") return "—";
  return `₹${Math.round(v).toLocaleString()}`;
}

function mins(v: number | null | undefined): string {
  return typeof v === "number" ? `${Math.round(v / 60)}m` : "—";
}

export default function ExitStudyPanel() {
  const [sum, setSum] = useState<P26Summary | null>(null);
  const [adv, setAdv] = useState<P26Advisory | null>(null);
  const [rows, setRows] = useState<P26CohortRow[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const [s, a, c] = await Promise.all([
          getP26Summary(),
          getP26Advisory(),
          getP26Cohorts(25),
        ]);
        if (!alive) return;
        setSum(s);
        setAdv(a);
        setRows(Array.isArray(c?.rows) ? c.rows : []);
      } catch {
        if (alive) setErr("Phase 26 artefacts are not readable from this backend.");
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const concl = sum?.conclusion;
  const status = concl?.verdict ?? (sum?.available ? "RESEARCH_ONLY" : "NO_RUN");
  const totals: P26VariantTotal[] = sum?.variant_totals ?? [];
  const baseline = totals.find((t) => t.variant === concl?.baseline_variant);
  const advRows: P26AdvisoryRow[] = adv?.rows ?? [];

  return (
    <div className="card p-3">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <div className="text-[12px] font-semibold tracking-wide">
          EXIT MANAGEMENT &amp; TRADABILITY{" "}
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
        {sum?.event_hypotheses_evaluated ? (
          <span className="text-[10px]" style={{ color: MUTED }}>
            {sum.event_hypotheses_evaluated.toLocaleString()} event hypotheses ·{" "}
            {totals.length} exits · {rows.length} cohorts shown
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
          {sum.note ?? "no study has been run on this machine yet."}
        </div>
      ) : null}

      {concl ? (
        <div className="text-[11px] mb-2" style={{ color: "rgba(255,255,255,0.8)" }}>
          {concl.headline}
          <div className="text-[10px] mt-1" style={{ color: MUTED }}>
            {concl.window}
          </div>
        </div>
      ) : null}

      {/* ---------------- exit variants ---------------- */}
      {totals.length ? (
        <div className="overflow-x-auto mb-3">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">exit variant</th>
                <th className="py-1 pr-2">trades</th>
                <th className="py-1 pr-2">net R</th>
                <th className="py-1 pr-2">₹</th>
                <th className="py-1 pr-2">profit exit</th>
                <th className="py-1 pr-2">stopped</th>
                <th className="py-1 pr-2">flat at horizon</th>
                <th className="py-1 pr-2">horizon (in profit)</th>
                <th className="py-1 pr-2">hold</th>
                <th className="py-1 pr-2">beats baseline</th>
              </tr>
            </thead>
            <tbody>
              {totals.map((t) => {
                const isBase = t.variant === concl?.baseline_variant;
                const delta =
                  baseline && !isBase ? t.avg_net_r - baseline.avg_net_r : null;
                return (
                  <tr key={t.variant} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2">
                      <div className="font-semibold">
                        {t.variant}
                        {isBase ? (
                          <span style={{ color: MUTED }}> · baseline</span>
                        ) : null}
                      </div>
                      <div style={{ color: MUTED }}>{t.geometry.label}</div>
                    </td>
                    <td className="py-1 pr-2">{t.trades.toLocaleString()}</td>
                    <td className="py-1 pr-2" style={{ color: signColour(t.avg_net_r) }}>
                      {num(t.avg_net_r, "R")}
                      {delta !== null ? (
                        <span style={{ color: signColour(delta) }}>
                          {" "}
                          ({delta > 0 ? "+" : ""}
                          {delta.toFixed(4)})
                        </span>
                      ) : null}
                    </td>
                    <td
                      className="py-1 pr-2"
                      style={{ color: signColour(t.total_net_rupees) }}
                    >
                      {rupees(t.total_net_rupees)}
                    </td>
                    <td className="py-1 pr-2">
                      {num(t.profit_exit_before_sl_pct, "%")}
                    </td>
                    <td className="py-1 pr-2">{num(t.stop_out_pct, "%")}</td>
                    <td className="py-1 pr-2">{num(t.flat_timeout_pct, "%")}</td>
                    <td className="py-1 pr-2" style={{ color: MUTED }}>
                      {num(t.horizon_exit_pct, "%")} (
                      {num(t.horizon_exit_in_profit_pct, "%")})
                    </td>
                    <td className="py-1 pr-2">{mins(t.avg_hold_sec)}</td>
                    <td className="py-1 pr-2">
                      {isBase
                        ? "—"
                        : `${t.instruments_better_than_baseline.length} instr`}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="text-[10px] mt-1" style={{ color: MUTED }}>
            a variant with no fixed target reaches the horizon by construction, so
            the horizon column is shown separately from the flat timeout Phase 25
            measured.
          </div>
        </div>
      ) : null}

      {/* ---------------- tradability advisory ---------------- */}
      {advRows.length ? (
        <div className="mb-3">
          <div className="text-[11px] font-semibold mb-1">
            TRADABILITY — measured economics{" "}
            <span style={{ color: AMBER }}>advisory only, nothing is wired</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead style={{ color: MUTED }}>
                <tr className="text-left">
                  <th className="py-1 pr-2">instrument</th>
                  <th className="py-1 pr-2">spread</th>
                  <th className="py-1 pr-2">spread / risk</th>
                  <th className="py-1 pr-2">hurdle</th>
                  <th className="py-1 pr-2">premium</th>
                  <th className="py-1 pr-2">measured net R</th>
                  <th className="py-1 pr-2">economics</th>
                </tr>
              </thead>
              <tbody>
                {advRows.map((r) => (
                  <tr key={r.instrument} style={{ borderTop: `1px solid ${LINE}` }}>
                    <td className="py-1 pr-2 font-semibold">{r.instrument}</td>
                    <td className="py-1 pr-2">
                      {num(r.median_measured_spread_pct, "%")}
                    </td>
                    <td className="py-1 pr-2">
                      {num(r.spread_as_fraction_of_risk, "x")}
                    </td>
                    <td className="py-1 pr-2">
                      {num(r.median_break_even_hurdle_pct, "%")}
                    </td>
                    <td className="py-1 pr-2">{rupees(r.median_premium_ask)}</td>
                    <td
                      className="py-1 pr-2"
                      style={{ color: signColour(r.measured_avg_net_r) }}
                    >
                      {num(r.measured_avg_net_r, "R")}
                    </td>
                    <td
                      className="py-1 pr-2"
                      style={{ color: STATUS_COLOUR[r.verdict] ?? MUTED }}
                    >
                      {r.verdict}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {adv?.advisory_claim ? (
            <div className="text-[10px] mt-1" style={{ color: MUTED }}>
              {adv.advisory_claim}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ---------------- ranked event cohorts ---------------- */}
      {rows.length ? (
        <div className="overflow-x-auto">
          <table className="w-full text-[10px]">
            <thead style={{ color: MUTED }}>
              <tr className="text-left">
                <th className="py-1 pr-2">cohort</th>
                <th className="py-1 pr-2">exit</th>
                <th className="py-1 pr-2">dev</th>
                <th className="py-1 pr-2">validation</th>
                <th className="py-1 pr-2">holdout</th>
                <th className="py-1 pr-2">folds</th>
                <th className="py-1 pr-2">status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.strategy_id} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-1 pr-2">
                    <div className="font-semibold">
                      {r.instrument} {r.option_type}
                    </div>
                    <div style={{ color: MUTED }}>{r.conditions.join(" + ")}</div>
                  </td>
                  <td className="py-1 pr-2" style={{ color: MUTED }}>
                    {r.exit_variant}
                  </td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.dev_avg_net_r) }}>
                    {num(r.dev_trades)} tr · {num(r.dev_avg_net_r, "R")}
                  </td>
                  <td className="py-1 pr-2" style={{ color: signColour(r.val_avg_net_r) }}>
                    {num(r.val_trades)} tr · {num(r.val_avg_net_r, "R")}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: signColour(r.holdout_avg_net_r) }}
                  >
                    {num(r.holdout_trades)} tr · {num(r.holdout_avg_net_r, "R")} · PF{" "}
                    {pf(r.holdout_profit_factor)}
                  </td>
                  <td className="py-1 pr-2">
                    {num(r.folds_positive)}/{num(r.folds_measurable)}
                  </td>
                  <td
                    className="py-1 pr-2"
                    style={{ color: STATUS_COLOUR[r.status] ?? MUTED }}
                  >
                    <div>{r.status}</div>
                    {open && r.failed_clauses?.length ? (
                      <ul style={{ color: MUTED }}>
                        {r.failed_clauses.map((f) => (
                          <li key={f}>· {f}</li>
                        ))}
                      </ul>
                    ) : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {/* ---------------- detail ---------------- */}
      {open ? (
        <div className="mt-3 text-[10px]" style={{ color: MUTED }}>
          {sum?.execution
            ? Object.entries(sum.execution).map(([k, v]) => (
                <div key={k}>
                  <span style={{ color: "rgba(255,255,255,0.7)" }}>{k}:</span> {String(v)}
                </div>
              ))
            : null}
          {(sum?.instruments ?? []).map((i) => (
            <div key={i.instrument} className="mt-1">
              <span style={{ color: "rgba(255,255,255,0.7)" }}>{i.instrument}</span>{" "}
              {i.status} · {num(i.candidates)} candidates · {num(i.sessions)} sessions ·
              best on dev {i.best_variant_on_development ?? "—"}
              {i.reasons?.length ? ` · ${i.reasons.join("; ")}` : ""}
            </div>
          ))}
          {(sum?.answers ?? []).map((a) => (
            <div key={a.question} className="mt-1">
              <div style={{ color: "rgba(255,255,255,0.7)" }}>{a.question}</div>
              <div>{a.answer}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
