"use client";
import { useEffect, useState } from "react";
import { runBacktest, getLearning, FactorWeight } from "@/lib/api";
import { BacktestResult } from "@/lib/types";

function Stat({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg border border-white/10 px-3 py-2">
      <div className="text-[11px] text-white/45">{label}</div>
      <div className={`text-lg font-bold ${tone ?? "text-white"}`}>{value}</div>
    </div>
  );
}

function Sparkline({ curve }: { curve: number[] }) {
  if (curve.length < 2) return null;
  const w = 520;
  const h = 90;
  const min = Math.min(...curve);
  const max = Math.max(...curve);
  const span = max - min || 1;
  const pts = curve
    .map((v, i) => `${(i / (curve.length - 1)) * w},${h - ((v - min) / span) * h}`)
    .join(" ");
  const last = curve[curve.length - 1];
  const color = last >= 0 ? "#19e57f" : "#ff4d4d";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-[90px]">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  );
}

export default function BacktestModal({ instrument, onClose }: { instrument: string; onClose: () => void }) {
  const [res, setRes] = useState<BacktestResult | null>(null);
  const [factors, setFactors] = useState<FactorWeight[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    runBacktest(instrument)
      .then(setRes)
      .finally(() => setLoading(false));
    getLearning(instrument)
      .then((d) => setFactors(d.factors))
      .catch(() => setFactors([]));
  }, [instrument]);

  return (
    <div className="fixed inset-0 z-[70] grid place-items-center bg-black/70" onClick={onClose}>
      <div className="card p-6 w-[600px] space-y-4" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between">
          <div className="text-xs uppercase tracking-widest text-[var(--accent)] font-bold">
            Backtest · {instrument}
          </div>
          <button onClick={onClose} className="text-white/50 text-sm hover:text-white">
            ✕
          </button>
        </div>

        {loading || !res ? (
          <div className="h-40 grid place-items-center text-white/50">Replaying history through the engine…</div>
        ) : res.trades === 0 ? (
          <div className="h-40 grid place-items-center text-white/50 text-center text-sm">
            Not enough history yet to backtest. Let the feed run a little longer and try again.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-4 gap-2">
              <Stat label="Trades" value={`${res.trades}`} />
              <Stat label="Win Rate" value={`${res.win_rate.toFixed(0)}%`} tone={res.win_rate >= 50 ? "text-[#19e57f]" : "text-[#ff6b6b]"} />
              <Stat label="Net P&L" value={`₹${res.net_pnl.toFixed(0)}`} tone={res.net_pnl >= 0 ? "text-[#19e57f]" : "text-[#ff6b6b]"} />
              <Stat label="Profit Factor" value={res.profit_factor.toFixed(2)} tone={res.profit_factor >= 1 ? "text-[#19e57f]" : "text-[#ff6b6b]"} />
              <Stat label="Max Drawdown" value={`₹${res.max_drawdown.toFixed(0)}`} tone="text-[#ff6b6b]" />
              <Stat label="Avg Hold" value={`${res.avg_hold_minutes.toFixed(0)}m`} />
              <Stat label="Wins / Losses" value={`${res.wins}/${res.losses}`} />
              <Stat label="Bars Tested" value={`${res.candles_tested}`} />
            </div>
            <div>
              <div className="text-[11px] text-white/45 mb-1">Equity curve (₹)</div>
              <Sparkline curve={res.equity_curve} />
            </div>
          </>
        )}

        {factors.length > 0 && (
          <div className="border-t border-white/10 pt-3">
            <div className="text-[11px] text-white/45 mb-2">
              Self-learned factor weights (tuned from your closed trades)
            </div>
            <div className="space-y-1">
              {factors.slice(0, 6).map((f) => (
                <div key={f.name} className="flex items-center gap-2 text-[11px]">
                  <span className="w-32 text-white/70 truncate">{f.name}</span>
                  <div className="flex-1 h-1.5 rounded bg-white/10 overflow-hidden">
                    <div
                      className="h-full"
                      style={{
                        width: `${Math.min(100, (f.multiplier / 1.6) * 100)}%`,
                        background: f.multiplier >= 1 ? "#19e57f" : "#ff6b6b",
                      }}
                    />
                  </div>
                  <span className="w-10 text-right text-white/80">{f.multiplier.toFixed(2)}×</span>
                  <span className="w-16 text-right text-white/45">{f.trades} trd</span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="text-[10px] text-white/40 leading-relaxed border-t border-white/10 pt-3">
          Walk-forward replay of the live signal logic. Option P&L is modelled from the underlying move
          via a nominal ATM delta × lot size — it validates the directional edge and stop/target
          discipline, not exact premium ticks. Past performance does not guarantee future results.
        </div>
      </div>
    </div>
  );
}
