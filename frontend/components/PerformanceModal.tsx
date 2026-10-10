"use client";
import { useEffect, useState } from "react";
import { getPerformance } from "@/lib/api";
import { PerformanceReport } from "@/lib/types";

const METRIC_LABELS: [string, string][] = [
  ["win_rate", "Win Rate"],
  ["profit_factor", "Profit Factor"],
  ["expectancy", "Expectancy"],
  ["max_drawdown", "Max Drawdown"],
  ["sharpe_ratio", "Sharpe Ratio"],
  ["avg_winner", "Average Winner"],
  ["avg_loser", "Average Loser"],
  ["total_net_pnl", "Total Net P&L"],
  ["avg_hold_minutes", "Avg Hold (min)"],
  ["buy_accuracy", "BUY Accuracy"],
  ["wait_accuracy", "WAIT Accuracy"],
  ["exit_accuracy", "EXIT Accuracy"],
  ["trap_detection_accuracy", "Trap Detection Acc."],
  ["recovery_accuracy", "Recovery Acc."],
  ["premium_behaviour_accuracy", "Premium Behaviour Acc."],
];

const PCT_KEYS = new Set([
  "win_rate",
  "buy_accuracy",
  "wait_accuracy",
  "exit_accuracy",
  "trap_detection_accuracy",
  "recovery_accuracy",
  "premium_behaviour_accuracy",
]);

function fmtVal(key: string, v: number | null): string {
  if (v == null) return "Insufficient Real Data";
  if (PCT_KEYS.has(key)) return `${v}%`;
  return `${v}`;
}

export default function PerformanceModal({
  instrument,
  onClose,
}: {
  instrument: string;
  onClose: () => void;
}) {
  const [rep, setRep] = useState<PerformanceReport | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    getPerformance(instrument)
      .then(setRep)
      .finally(() => setLoading(false));
  }, [instrument]);

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-black/70" onClick={onClose}>
      <div
        className="card card-scroll p-6 w-[640px] max-h-[85vh] space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-widest text-[var(--accent)] font-bold">
            Performance Report · {instrument}
          </div>
          <button onClick={onClose} className="text-white/50 text-sm hover:text-white">
            ✕
          </button>
        </div>

        {loading || !rep ? (
          <div className="h-32 grid place-items-center text-white/50">Loading…</div>
        ) : (
          <>
            {!rep.data_ready && (
              <div className="rounded-lg border border-[#f4c025]/40 bg-[#f4c025]/10 px-4 py-3">
                <div className="text-sm font-bold text-[#f4c025]">
                  {rep.message}
                </div>
                <div className="text-[11px] text-white/60 mt-1">
                  {rep.reason} Metrics will populate automatically once real
                  market data has been collected ({rep.trades_recorded}/
                  {rep.trades_required} real trades so far). No synthetic numbers
                  are shown.
                </div>
              </div>
            )}

            <div className="grid grid-cols-3 gap-2">
              {METRIC_LABELS.map(([key, label]) => {
                const v = rep.metrics[key];
                const missing = v == null;
                return (
                  <div
                    key={key}
                    className="rounded-lg border border-white/10 px-3 py-2"
                  >
                    <div className="text-[10px] text-white/45">{label}</div>
                    <div
                      className={
                        missing
                          ? "text-[10px] font-semibold text-white/35 mt-1"
                          : "text-base font-bold text-white"
                      }
                    >
                      {fmtVal(key, v)}
                    </div>
                  </div>
                );
              })}
            </div>

            {rep.data_ready && Object.keys(rep.trade_distribution).length > 0 && (
              <div className="border-t border-white/10 pt-3">
                <div className="text-[11px] text-white/45 mb-2">
                  Trade distribution by market regime
                </div>
                <div className="space-y-1">
                  {Object.entries(rep.trade_distribution).map(([regime, d]) => (
                    <div key={regime} className="flex items-center gap-2 text-[11px]">
                      <span className="w-28 text-white/70 truncate capitalize">
                        {regime.replace(/_/g, " ").toLowerCase()}
                      </span>
                      <span className="text-white/50">{d.trades} trades</span>
                      <span className="text-white/50">
                        {d.trades ? Math.round((100 * d.wins) / d.trades) : 0}% win
                      </span>
                      <span
                        className={
                          "ml-auto tabular-nums " +
                          (d.net_pnl >= 0 ? "text-[#19e57f]" : "text-[#ff6b6b]")
                        }
                      >
                        ₹{d.net_pnl}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="text-[10px] text-white/40 leading-relaxed border-t border-white/10 pt-3">
              Validation Mode records every closed trade with its full decision
              context (entry, exit, P&amp;L, hold time, trade score, opportunity,
              risk, reasons, regime, premium behaviour, OI, volume, news). Real
              performance metrics are computed only from trades taken on a real
              broker feed — never fabricated from the simulated demo. Past
              performance does not guarantee future results.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
