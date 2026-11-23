"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

type OpenPos = { instrument: string; option: string; lots: number; entry: number; mark: number | null; stop: number; unrealized: number };
type BookRow = {
  book: string; strategy: string; capital: number; balance: number; available: number; used_margin: number;
  realized_pnl: number; unrealized_pnl: number; trades: number; win_rate: number | null; profit_factor: number | null;
  max_drawdown: number; today_equity: number; locked: boolean; open: OpenPos[];
};
type Summary = { enabled: boolean; risk_pct: number; total_capital: number; books: BookRow[]; last_skip: Record<string, string> };
type Trade = Record<string, string | number | null>;

const inr = (v: number | null | undefined) => (v == null ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`);
const pnl = (v: number | null | undefined) => (v == null ? "text-white/50" : v >= 0 ? "text-emerald-400" : "text-rose-400");
const ts = (v: unknown) => (v ? String(v).replace("T", " ").slice(0, 19) : "—");

export default function StrategyBooksBoard() {
  const [sum, setSum] = useState<Summary | null>(null);
  const [sel, setSel] = useState("S-PULLBACK");
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [s, t] = await Promise.all([
          fetch(`${API_BASE}/api/strategy-books`).then((r) => r.json()),
          fetch(`${API_BASE}/api/strategy-books/trades?book=${encodeURIComponent(sel)}&page_size=200`).then((r) => r.json()),
        ]);
        if (!alive) return;
        setSum(s); setTrades(t.rows || []); setTotal(t.total || 0); setErr(null);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [sel]);

  const b = sum?.books.find((x) => x.book === sel);
  const stat = (label: string, value: string, cls = "") => (
    <div className="rounded bg-white/5 px-3 py-2"><div className="text-[10px] text-white/50">{label}</div><div className={`text-sm font-semibold ${cls}`}>{value}</div></div>
  );

  return (
    <div className="w-full space-y-3 p-3">
      <div className="card p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="text-lg font-bold">Strategy Books <span className="text-[10px] text-amber-300/80">PAPER · no orders · PostgreSQL</span></div>
          {sum && (
            <div className="text-xs text-white/60">
              {sum.enabled ? <span className="text-emerald-400">ON</span> : <span className="text-amber-300">OFF (QT_STRATEGY_BOOKS_ENABLED=false)</span>}
              {" "}· {sum.books.length} books, {inr(sum.books[0]?.capital)} each, never shared · total {inr(sum.total_capital)} · risk {sum.risk_pct}% of capital per trade
            </div>
          )}
        </div>
        {err && <div className="mt-2 text-xs text-rose-400">{err}</div>}
        <div className="mt-3 flex flex-wrap gap-1">
          {(sum?.books || []).map((x) => (
            <button key={x.book} onClick={() => setSel(x.book)}
              className={`rounded px-3 py-1 text-xs ${sel === x.book ? "bg-white/15 text-white" : "bg-white/5 text-white/60 hover:bg-white/10"}`}>
              {x.strategy} <span className={pnl(x.realized_pnl + x.unrealized_pnl)}>{inr(x.realized_pnl + x.unrealized_pnl)}</span>
            </button>
          ))}
        </div>
      </div>

      {b && (
        <div className="card p-4">
          <div className="mb-2 font-bold">{b.strategy}{b.locked && <span className="ml-2 text-xs text-amber-300">DAY LOCKED</span>}</div>
          <div className="grid grid-cols-2 gap-2 md:grid-cols-5">
            {stat("Starting capital", inr(b.capital))}
            {stat("Balance", inr(b.balance), pnl(b.balance - b.capital))}
            {stat("Available", inr(b.available))}
            {stat("Used margin", inr(b.used_margin))}
            {stat("Realized P&L (net)", inr(b.realized_pnl), pnl(b.realized_pnl))}
            {stat("Unrealized P&L", inr(b.unrealized_pnl), pnl(b.unrealized_pnl))}
            {stat("Trades", String(b.trades))}
            {stat("Win %", b.win_rate == null ? "—" : `${b.win_rate}%`)}
            {stat("Profit factor", b.profit_factor == null ? "—" : String(b.profit_factor))}
            {stat("Max drawdown", inr(b.max_drawdown), "text-rose-300")}
          </div>
          <div className="mt-3 text-xs font-semibold text-white/70">Open positions</div>
          {b.open.length === 0 ? <div className="text-xs text-white/40">none</div> : b.open.map((p) => (
            <div key={p.option} className="text-xs tabular-nums">{p.instrument} {p.option} ×{p.lots} · entry {p.entry} · mark {p.mark ?? "—"} · stop {p.stop} · <span className={pnl(p.unrealized)}>{inr(p.unrealized)}</span></div>
          ))}
        </div>
      )}

      <div className="card p-4">
        <div className="font-bold">Buys and exits <span className="text-xs font-normal text-white/50">{total} trades</span></div>
        <div className="mt-2 max-h-[60vh] overflow-auto">
          <table className="w-full text-xs tabular-nums">
            <thead className="sticky top-0 bg-black/80 text-white/50">
              <tr className="text-left">
                {["Buy time", "Instrument", "Option", "Lots", "Buy @ask", "Stop", "T1", "Exit time", "Exit @bid", "Exit reason", "Charges", "Net P&L", "Status"].map((h) => (
                  <th key={h} className="px-2 py-1 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={String(t.trade_id)} className="border-t border-white/5">
                  <td className="px-2 py-1 whitespace-nowrap">{ts(t.entry_ts_iso)}</td>
                  <td className="px-2 py-1">{t.instrument}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{t.option_symbol}</td>
                  <td className="px-2 py-1">{t.lots}</td>
                  <td className="px-2 py-1">{t.entry_price}</td>
                  <td className="px-2 py-1">{t.initial_stop}</td>
                  <td className="px-2 py-1">{t.target1 ?? "—"}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{ts(t.exit_ts_iso)}</td>
                  <td className="px-2 py-1">{t.exit_price ?? "—"}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{t.exit_reason ?? "—"}</td>
                  <td className="px-2 py-1">{inr(t.costs as number | null)}</td>
                  <td className={`px-2 py-1 font-semibold ${pnl(t.net_pnl as number | null)}`}>{inr(t.net_pnl as number | null)}</td>
                  <td className="px-2 py-1">{t.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
