"use client";
import { useCallback, useEffect, useState } from "react";
import {
  AccountSummary,
  LiveAccount,
  accountReportUrl,
  getAccount,
  getLiveAccount,
} from "@/lib/api";

function inr(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const sign = v < 0 ? "-" : "";
  return `${sign}₹${Math.abs(v).toLocaleString("en-IN", {
    maximumFractionDigits: 0,
  })}`;
}

function EquityCurve({ points }: { points: number[] }) {
  if (!points || points.length < 2) {
    return <div className="text-white/40 text-xs">Not enough trades to draw an equity curve yet.</div>;
  }
  const w = 640;
  const h = 140;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const pts = points
    .map((v, i) => {
      const x = (i / (points.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const up = points[points.length - 1] >= points[0];
  const color = up ? "#19e57f" : "#ff4d4d";
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-[140px]">
      <polyline points={pts} fill="none" stroke={color} strokeWidth={2} />
    </svg>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.02] px-4 py-3">
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className="text-lg font-bold" style={{ color: color || "#fff" }}>{value}</div>
    </div>
  );
}

export default function AccountDashboard() {
  const [data, setData] = useState<AccountSummary | null>(null);
  const [live, setLive] = useState<LiveAccount | null>(null);
  const [loading, setLoading] = useState(true);
  const [month, setMonth] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [d, l] = await Promise.all([getAccount(), getLiveAccount().catch(() => null)]);
      setData(d);
      setLive(l);
      if (d.months_available.length && !month) setMonth(d.months_available[0]);
    } finally {
      setLoading(false);
    }
  }, [month]);

  useEffect(() => {
    load();
    const id = setInterval(load, 15000);
    return () => clearInterval(id);
  }, [load]);

  if (loading && !data) {
    return <div className="p-6 text-white/50">Loading account…</div>;
  }
  if (!data) {
    return <div className="p-6 text-white/50">Account data unavailable.</div>;
  }

  const pnlColor = data.realised_pnl >= 0 ? "#19e57f" : "#ff4d4d";
  const equityCurve = data.by_day.map((d) => d.equity);
  const dl = month ? accountReportUrl(month) : "";

  return (
    <div className="p-4 space-y-4">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h2 className="text-xl font-bold">Paper Account</h2>
          <p className="text-[11px] text-white/45 max-w-2xl">{data.note}</p>
        </div>
        <span
          className={`text-[10px] font-semibold px-2 py-1 rounded-md border ${
            data.is_real_feed
              ? "border-[#19e57f]/40 bg-[#19e57f]/10 text-[#19e57f]"
              : "border-white/15 text-white/50"
          }`}
        >
          {data.is_real_feed ? "REAL FEED" : "SIMULATED FEED"} · PAPER
        </span>
      </div>

      {live && (
        <div className="rounded-lg border border-[#19e57f]/30 bg-[#19e57f]/[0.04] p-4 space-y-3">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="text-sm font-bold text-[#19e57f]">
              Live Angel Account (read-only)
            </div>
            <span className="text-[10px] font-semibold px-2 py-1 rounded-md border border-white/15 text-white/50">
              READ-ONLY · no orders placed
            </span>
          </div>
          {!live.enabled || live.ok === false ? (
            <div className="text-white/50 text-xs">
              {live.reason || "Live balance unavailable."}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <Stat label="Available Cash" value={inr(live.totals?.available_cash)} />
                <Stat label="Used Margin" value={inr(live.totals?.used_margin)} />
                <Stat label="Net Balance" value={inr(live.totals?.net_balance)} />
                <Stat
                  label="Open P&L"
                  value={inr(live.totals?.open_pnl)}
                  color={(live.totals?.open_pnl ?? 0) >= 0 ? "#19e57f" : "#ff4d4d"}
                />
                <Stat
                  label="Realised P&L (today)"
                  value={inr(live.totals?.realised_pnl_today)}
                  color={(live.totals?.realised_pnl_today ?? 0) >= 0 ? "#19e57f" : "#ff4d4d"}
                />
              </div>
              {live.positions && live.positions.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead className="text-white/40 text-left">
                      <tr>
                        <th className="py-1">Symbol</th>
                        <th>Product</th>
                        <th>Qty</th>
                        <th>Buy Avg</th>
                        <th>Sell Avg</th>
                        <th>LTP</th>
                        <th className="text-right">Open P&L</th>
                        <th className="text-right">Realised</th>
                      </tr>
                    </thead>
                    <tbody>
                      {live.positions.map((p, i) => (
                        <tr key={i} className="border-t border-white/5">
                          <td className="py-1.5 whitespace-nowrap">{p.symbol || "—"}</td>
                          <td>{p.product || "—"}</td>
                          <td>{p.net_qty ?? "—"}</td>
                          <td>{p.buy_avg != null ? p.buy_avg.toFixed(2) : "—"}</td>
                          <td>{p.sell_avg != null ? p.sell_avg.toFixed(2) : "—"}</td>
                          <td>{p.ltp ?? "—"}</td>
                          <td className="text-right font-semibold" style={{ color: p.unrealised >= 0 ? "#19e57f" : "#ff4d4d" }}>
                            {inr(p.unrealised)}
                          </td>
                          <td className="text-right" style={{ color: p.realised >= 0 ? "#19e57f" : "#ff4d4d" }}>
                            {inr(p.realised)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-white/40 text-xs">No open positions in your Angel account.</div>
              )}
            </>
          )}
        </div>
      )}

      <div className="text-xs font-semibold text-white/60 pt-1">Paper Ledger (simulated trades)</div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <Stat label="Starting Capital" value={inr(data.starting_capital)} />
        <Stat label="Equity (Balance)" value={inr(data.equity)} color={pnlColor} />
        <Stat label="Realised P&L" value={inr(data.realised_pnl)} color={pnlColor} />
        <Stat label="Return %" value={`${data.return_pct}%`} color={pnlColor} />
        <Stat label="Total Trades" value={String(data.overall.trades)} />
        <Stat label="Win Rate" value={`${data.overall.win_rate}%`} />
        <Stat
          label="Wins / Losses"
          value={`${data.overall.wins} / ${data.overall.losses}`}
        />
        <Stat
          label="Profit Factor"
          value={data.overall.profit_factor === null ? "—" : String(data.overall.profit_factor)}
        />
      </div>

      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="text-xs font-semibold text-white/70 mb-2">Equity Curve (by day)</div>
        <EquityCurve points={equityCurve} />
      </div>

      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-4 space-y-3">
        <div className="flex items-center justify-between flex-wrap gap-2">
          <div className="text-xs font-semibold text-white/70">Monthly Report</div>
          <div className="flex items-center gap-2">
            <select
              value={month}
              onChange={(e) => setMonth(e.target.value)}
              className="bg-black/40 border border-white/15 rounded-md px-2 py-1 text-xs"
            >
              {data.months_available.length === 0 && <option value="">No data yet</option>}
              {data.months_available.map((m) => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
            <a
              href={dl || undefined}
              onClick={(e) => { if (!month) e.preventDefault(); }}
              className={`text-xs font-semibold px-3 py-1.5 rounded-md ${
                month ? "bg-[#19e57f] text-black" : "bg-white/10 text-white/40 cursor-not-allowed"
              }`}
            >
              ↓ Download CSV
            </a>
          </div>
        </div>
        {data.by_month.length === 0 ? (
          <div className="text-white/40 text-xs">No trades recorded yet.</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-white/40 text-left">
              <tr>
                <th className="py-1">Month</th>
                <th>Trades</th>
                <th>Win %</th>
                <th className="text-right">Net P&L</th>
              </tr>
            </thead>
            <tbody>
              {data.by_month.map((m) => (
                <tr key={m.month} className="border-t border-white/5">
                  <td className="py-1.5">{m.month}</td>
                  <td>{m.trades}</td>
                  <td>{m.win_rate}%</td>
                  <td className="text-right font-semibold" style={{ color: m.net_pnl >= 0 ? "#19e57f" : "#ff4d4d" }}>
                    {inr(m.net_pnl)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="rounded-lg border border-white/10 bg-white/[0.02] p-4">
        <div className="text-xs font-semibold text-white/70 mb-2">Recent Trades</div>
        {data.recent_trades.length === 0 ? (
          <div className="text-white/40 text-xs">
            No paper trades yet. Trades appear here after you close a paper position (BUY → SELL).
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="text-white/40 text-left">
                <tr>
                  <th className="py-1">Date</th>
                  <th>Instrument</th>
                  <th>Option</th>
                  <th>Lots</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Hold</th>
                  <th className="text-right">Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_trades.map((t, i) => (
                  <tr key={i} className="border-t border-white/5">
                    <td className="py-1.5 whitespace-nowrap">{t.date}</td>
                    <td>{t.instrument}</td>
                    <td className="whitespace-nowrap">{t.option || "—"}</td>
                    <td>{t.lots ?? "—"}</td>
                    <td>{t.entry ?? "—"}</td>
                    <td>{t.exit ?? "—"}</td>
                    <td>{t.holding_minutes != null ? `${t.holding_minutes}m` : "—"}</td>
                    <td className="text-right font-semibold" style={{ color: t.win ? "#19e57f" : "#ff4d4d" }}>
                      {inr(t.net_pnl)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
