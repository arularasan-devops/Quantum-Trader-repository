"use client";

import { useEffect, useState } from "react";
import {
  getP23Rows,
  getP23Summary,
  type P23Metrics,
  type P23Row,
  type P23Summary,
} from "@/lib/api";

/** HURDLE SHADOW (PAPER) — Phase 23.
 *
 * A record-only experiment sitting beside the live auto-trader. Every auto BUY
 * opportunity is written down with the book that was actually quoted at that
 * tick, and three arms are then scored on the engine's own closed trades:
 * the unchanged engine, and the two shadow books that would have declined an
 * option whose break-even hurdle was above 5% or above 3%.
 *
 * Properties this screen is built to keep:
 *   - the hurdle is measured, never modelled. A leg with no usable two-sided
 *     book is UNMEASURED, every arm abstains on it, and it is reported as
 *     coverage rather than costed at a family-median spread;
 *   - MONEY MISSED is shown next to MONEY SAVED, always, and next to the same
 *     figure with the single largest refused trade removed;
 *   - no threshold is live. Nothing on this page or behind it can refuse an
 *     order; promotion is a separate, explicitly approved change.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.45)";
const LINE = "rgba(255,255,255,0.08)";

const VERDICT_COLOUR: Record<string, string> = {
  READY_FOR_HUMAN_REVIEW: GREEN,
  NOT_PROVEN: AMBER,
  INSUFFICIENT_DATA: MUTED,
  VALIDATED_NEGATIVE: RED,
};

const ARMS: { key: string; label: string }[] = [
  { key: "EXISTING", label: "EXISTING (live, unchanged)" },
  { key: "SHADOW_LE_5", label: "SHADOW ≤ 5%" },
  { key: "SHADOW_LE_3", label: "SHADOW ≤ 3%" },
];

function money(v: number | null | undefined): string {
  if (typeof v !== "number") return "—";
  return `${v < 0 ? "-" : ""}₹${Math.abs(Math.round(v)).toLocaleString("en-IN")}`;
}

function num(v: number | null | undefined, suffix = ""): string {
  return typeof v === "number" ? `${v}${suffix}` : "—";
}

function signColour(v: number | null | undefined): string {
  if (typeof v !== "number" || v === 0) return MUTED;
  return v > 0 ? GREEN : RED;
}

function ArmCard({ label, m }: { label: string; m?: P23Metrics }) {
  const att = m?.attribution;
  return (
    <div className="card p-3" style={{ borderColor: LINE }}>
      <div className="text-[11px] uppercase tracking-widest text-white/45">
        {label}
      </div>
      <div className="text-lg font-bold" style={{ color: signColour(m?.net_pnl) }}>
        {money(m?.net_pnl)}
      </div>
      <div className="text-[11px] text-white/60 mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5">
        <span>trades {m?.trades ?? 0}</span>
        <span>rejected {m?.rejected ?? 0}</span>
        <span>win {num(m?.win_pct, "%")}</span>
        <span>PF {num(m?.profit_factor)}</span>
        <span>T1-before-SL {num(m?.t1_before_sl_pct, "%")}</span>
        <span>avg R {num(m?.avg_net_r)}</span>
        <span>med R {num(m?.median_net_r)}</span>
        <span>max DD {money(m?.max_drawdown)}</span>
        <span>lose streak {m?.max_losing_streak ?? 0}</span>
        <span>avg hurdle {num(m?.avg_hurdle_pct, "%")}</span>
        <span>med spread {num(m?.median_measured_spread_pct, "%")}</span>
        <span>med hold {num(m?.median_hold_minutes, "m")}</span>
      </div>
      {att && (
        <div className="mt-2 pt-2 border-t text-[11px]" style={{ borderColor: LINE }}>
          <div className="flex justify-between">
            <span className="text-white/45">MONEY SAVED by refusing</span>
            <span style={{ color: GREEN }}>{money(att.money_saved_by_refusing_high_hurdle_trades)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-white/45">MONEY MISSED by refusing</span>
            <span style={{ color: RED }}>{money(att.money_missed_by_refusing_them)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-white/45">net effect</span>
            <span style={{ color: signColour(att.net_effect) }}>{money(att.net_effect)}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-white/45">net effect excl. largest refused</span>
            <span style={{ color: signColour(att.net_effect_excluding_largest) }}>
              {money(att.net_effect_excluding_largest)}
            </span>
          </div>
          <div className="text-white/35 mt-0.5">
            abstained (no measured book): {att.abstained_unmeasured}
          </div>
        </div>
      )}
    </div>
  );
}

export default function HurdleShadowPanel() {
  const [summary, setSummary] = useState<P23Summary | null>(null);
  const [rows, setRows] = useState<P23Row[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const load = async () => {
      try {
        const [s, r] = await Promise.all([getP23Summary(), getP23Rows(100)]);
        if (!live) return;
        setSummary(s);
        setRows(r.rows ?? []);
        setError(null);
      } catch {
        if (live) setError("hurdle shadow unavailable");
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => {
      live = false;
      clearInterval(t);
    };
  }, []);

  if (error) {
    return (
      <div className="card p-3 text-[11px] text-white/45">
        HURDLE SHADOW (PAPER) — {error}
      </div>
    );
  }

  const rep = summary?.report;
  const cov = rep?.coverage;
  const verdict = summary?.verdict;

  return (
    <div className="flex flex-col gap-3">
      <div className="card p-3" style={{ borderColor: LINE }}>
        <div className="flex items-baseline justify-between flex-wrap gap-2">
          <div>
            <div className="text-xs font-bold text-white/80">
              HURDLE SHADOW (PAPER) — options only, record only
            </div>
            <div className="text-[11px] text-white/50 mt-0.5">
              Break-even hurdle from the measured bid/ask at the tick. The live
              auto-buy path is unchanged and no threshold is applied to it.
            </div>
          </div>
          <div className="text-right">
            <div
              className="text-xs font-bold"
              style={{ color: VERDICT_COLOUR[verdict?.status ?? ""] ?? MUTED }}
            >
              {verdict?.status ?? "—"}
            </div>
            <div className="text-[11px] text-white/45">
              discovered threshold{" "}
              {verdict?.discovered_threshold_pct != null
                ? `${verdict.discovered_threshold_pct}%`
                : "—"}{" "}
              · {verdict?.measured_resolved_trades ?? 0} measured resolved ·{" "}
              {verdict?.sessions ?? 0} session(s)
            </div>
          </div>
        </div>
        <div className="text-[11px] text-white/55 mt-2 grid grid-cols-2 md:grid-cols-4 gap-x-3 gap-y-0.5">
          <span>opportunities {cov?.opportunities ?? 0}</span>
          <span>measured book {cov?.measured_book ?? 0}</span>
          <span>unmeasured {cov?.unmeasured_book ?? 0}</span>
          <span>measured {num(cov?.measured_pct, "%")}</span>
          <span>engine bought {cov?.engine_would_buy ?? 0}</span>
          <span>engine refused {cov?.engine_refused ?? 0}</span>
          <span>resolved {cov?.resolved_trades ?? 0}</span>
          <span>unresolved {cov?.open_or_unresolved ?? 0}</span>
          <span>rows on file {cov?.rows_on_file ?? 0}</span>
          <span>simulated-feed excluded {cov?.simulated_feed_rows_excluded ?? 0}</span>
        </div>
        <div className="text-[10px] text-white/35 mt-1">{rep?.futures_note}</div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        {ARMS.map((a) => (
          <ArmCard key={a.key} label={a.label} m={rep?.cumulative?.[a.key]} />
        ))}
      </div>

      <div className="card p-3" style={{ borderColor: LINE }}>
        <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
          Threshold sweep 3% → 5% (nothing here is a recommendation)
        </div>
        <div className="overflow-auto">
          <table className="w-full text-[11px]">
            <thead className="text-white/40">
              <tr>
                <th className="text-left py-1">cutoff</th>
                <th className="text-right">accepted</th>
                <th className="text-right">rejected</th>
                <th className="text-right">net P&amp;L</th>
                <th className="text-right">vs live</th>
                <th className="text-right">PF</th>
                <th className="text-right">win</th>
                <th className="text-right">saved</th>
                <th className="text-right">missed</th>
                <th className="text-right">net effect</th>
                <th className="text-right">excl. largest</th>
              </tr>
            </thead>
            <tbody>
              {(rep?.sweep ?? []).map((s) => (
                <tr key={s.threshold_pct} className="border-t" style={{ borderColor: LINE }}>
                  <td className="py-1">{s.threshold_pct}%</td>
                  <td className="text-right">{s.accepted}</td>
                  <td className="text-right">{s.rejected}</td>
                  <td className="text-right">{money(s.net_pnl)}</td>
                  <td className="text-right" style={{ color: signColour(s.vs_existing_net_pnl) }}>
                    {money(s.vs_existing_net_pnl)}
                  </td>
                  <td className="text-right">{num(s.profit_factor)}</td>
                  <td className="text-right">{num(s.win_pct, "%")}</td>
                  <td className="text-right" style={{ color: GREEN }}>{money(s.money_saved)}</td>
                  <td className="text-right" style={{ color: RED }}>{money(s.money_missed)}</td>
                  <td className="text-right" style={{ color: signColour(s.net_effect) }}>
                    {money(s.net_effect)}
                  </td>
                  <td
                    className="text-right"
                    style={{ color: signColour(s.net_effect_excluding_largest) }}
                  >
                    {money(s.net_effect_excluding_largest)}
                  </td>
                </tr>
              ))}
              {(rep?.sweep ?? []).length === 0 && (
                <tr>
                  <td colSpan={11} className="text-white/40 py-2">
                    No opportunities recorded yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card p-3" style={{ borderColor: LINE }}>
        <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
          Recorded opportunities (newest first)
        </div>
        <div className="overflow-auto max-h-72">
          <table className="w-full text-[11px]">
            <thead className="text-white/40">
              <tr>
                <th className="text-left py-1">when</th>
                <th className="text-left">instrument</th>
                <th className="text-left">option</th>
                <th className="text-right">premium</th>
                <th className="text-right">bid</th>
                <th className="text-right">ask</th>
                <th className="text-right">spread</th>
                <th className="text-right">cost</th>
                <th className="text-right">hurdle</th>
                <th className="text-left">engine</th>
                <th className="text-left">≤3%</th>
                <th className="text-left">≤5%</th>
                <th className="text-left">outcome</th>
                <th className="text-right">net P&amp;L</th>
                <th className="text-right">net R</th>
                <th className="text-right">hold</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} className="border-t" style={{ borderColor: LINE }}>
                  <td className="py-1 whitespace-nowrap">
                    {r.date ?? "—"} {r.time_bucket}
                  </td>
                  <td>{r.instrument ?? "—"}</td>
                  <td className="whitespace-nowrap">
                    {r.option_type ?? "—"} {r.strike ?? ""}
                  </td>
                  <td className="text-right">{num(r.premium)}</td>
                  <td className="text-right">{num(r.bid)}</td>
                  <td className="text-right">{num(r.ask)}</td>
                  <td className="text-right">{num(r.measured_spread_pct, "%")}</td>
                  <td className="text-right">{money(r.round_trip_cost_rupees)}</td>
                  <td
                    className="text-right"
                    style={{
                      color:
                        r.hurdle_status === "MEASURED"
                          ? (r.hurdle_pct ?? 0) <= 3
                            ? GREEN
                            : (r.hurdle_pct ?? 0) <= 5
                              ? AMBER
                              : RED
                          : MUTED,
                    }}
                  >
                    {r.hurdle_status === "MEASURED"
                      ? num(r.hurdle_pct, "%")
                      : "UNMEASURED"}
                  </td>
                  <td>{r.engine_would_buy ? "BUY" : "no"}</td>
                  <td>{r.gate_3_allows ?? "—"}</td>
                  <td>{r.gate_5_allows ?? "—"}</td>
                  <td className="whitespace-nowrap">
                    {r.real_feed === false
                      ? "SIMULATED"
                      : r.resolved
                        ? (r.outcome ?? "—")
                        : "OPEN"}
                  </td>
                  <td className="text-right" style={{ color: signColour(r.net_pnl) }}>
                    {r.resolved ? money(r.net_pnl) : "—"}
                  </td>
                  <td className="text-right">{r.resolved ? num(r.net_r) : "—"}</td>
                  <td className="text-right">{num(r.hold_minutes, "m")}</td>
                </tr>
              ))}
              {rows.length === 0 && (
                <tr>
                  <td colSpan={16} className="text-white/40 py-2">
                    No auto BUY opportunity has been observed yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="text-[10px] text-white/40 leading-relaxed">
        Paper shadow only. The three arms are scored on the same closed trades the
        live engine took, so the difference between them is only the trades a
        cutoff declined. Rows without a two-sided book are UNMEASURED and are not
        counted for or against any cutoff. Futures are deliberately outside this
        experiment. Promotion of any threshold requires human review.
      </div>
    </div>
  );
}
