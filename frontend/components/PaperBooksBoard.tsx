"use client";

import { useEffect, useState } from "react";
import DateRangeDownload from "./DateRangeDownload";
import { API_BASE } from "@/lib/api";

type OpenPos = { instrument: string; option: string; lots: number; entry: number; mark: number | null; stop: number; floor: number | null; unrealized: number };
type BookRow = {
  book: string; capital: number; sizing: string; trades: number; wins: number; win_rate: number | null;
  net_pnl: number; costs: number; profit_factor: number | null; max_drawdown: number; worst_trade: number | null;
  best_trade: number | null; return_pct: number; today_realized: number; today_equity: number; day_peak: number;
  lock_level: number | null; locked: boolean; deployed: number; open: OpenPos[];
};
type Summary = { enabled: boolean; risk_pct: number; lock_keep_pct: number; profit_target: number; books: BookRow[]; last_skip: Record<string, string> };
type Trade = Record<string, string | number | null>;

const inr = (v: number | null | undefined) => (v == null ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`);
const pnl = (v: number | null | undefined) => (v == null ? "text-white/50" : v >= 0 ? "text-emerald-400" : "text-rose-400");
const ts = (v: unknown) => (v ? String(v).replace("T", " ").slice(0, 19) : "—");

export default function PaperBooksBoard() {
  const [sum, setSum] = useState<Summary | null>(null);
  const [book, setBook] = useState("");
  const [trades, setTrades] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [range, setRange] = useState<{ since: number | null; until: number | null }>({ since: null, until: null });

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const q = new URLSearchParams({ page_size: "200" });
        if (book) q.set("book", book);
        if (range.since !== null) q.set("since", String(range.since));
        if (range.until !== null) q.set("until", String(range.until));
        const [s, t] = await Promise.all([
          fetch(`${API_BASE}/api/paper-books`).then((r) => r.json()),
          fetch(`${API_BASE}/api/paper-books/trades?${q}`).then((r) => r.json()),
        ]);
        if (!alive) return;
        setSum(s);
        setTrades(t.rows || []);
        setTotal(t.total || 0);
        setErr(null);
      } catch (e) {
        if (alive) setErr(String(e));
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [book, range.since, range.until]);

  return (
    <div className="w-full space-y-3 p-3">
      <div className="card p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="text-lg font-bold">Paper Books <span className="text-[10px] text-amber-300/80">PAPER · PostgreSQL</span></div>
          {sum && (
            <div className="text-xs text-white/60">
              Same signals for every book · RISK = {sum.risk_pct}% of capital per trade · day lock keeps {sum.lock_keep_pct}% of the day high after {inr(sum.profit_target)}
            </div>
          )}
        </div>
        {err && <div className="mt-2 text-xs text-rose-400">{err}</div>}
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead className="text-white/50">
              <tr className="text-left">
                {["Book", "Capital", "Trades", "Win %", "Net P&L", "Return", "PF", "Max DD", "Worst", "Best", "Today", "Day high", "Lock", "Open"].map((h) => (
                  <th key={h} className="px-2 py-1 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(sum?.books || []).map((b) => (
                <tr key={b.book} onClick={() => setBook(book === b.book ? "" : b.book)}
                  className={`cursor-pointer border-t border-white/5 hover:bg-white/5 ${book === b.book ? "bg-white/10" : ""}`}>
                  <td className="px-2 py-1 font-semibold whitespace-nowrap">{b.book}</td>
                  <td className="px-2 py-1">{inr(b.capital)}</td>
                  <td className="px-2 py-1">{b.trades}</td>
                  <td className="px-2 py-1">{b.win_rate ?? "—"}</td>
                  <td className={`px-2 py-1 font-semibold ${pnl(b.net_pnl)}`}>{inr(b.net_pnl)}</td>
                  <td className={`px-2 py-1 ${pnl(b.return_pct)}`}>{b.return_pct}%</td>
                  <td className="px-2 py-1">{b.profit_factor ?? "—"}</td>
                  <td className="px-2 py-1 text-rose-300">{inr(b.max_drawdown)}</td>
                  <td className={`px-2 py-1 ${pnl(b.worst_trade)}`}>{inr(b.worst_trade)}</td>
                  <td className={`px-2 py-1 ${pnl(b.best_trade)}`}>{inr(b.best_trade)}</td>
                  <td className={`px-2 py-1 ${pnl(b.today_equity)}`}>{inr(b.today_equity)}</td>
                  <td className="px-2 py-1">{inr(b.day_peak)}</td>
                  <td className="px-2 py-1 whitespace-nowrap">
                    {b.locked ? <span className="text-amber-300">LOCKED</span> : b.lock_level != null ? inr(b.lock_level) : "—"}
                  </td>
                  <td className="px-2 py-1 whitespace-nowrap">
                    {b.open.length === 0 ? "—" : b.open.map((p) => (
                      <div key={p.option}>{p.option} ×{p.lots} <span className={pnl(p.unrealized)}>{inr(p.unrealized)}</span></div>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="card p-4">
        <div className="flex flex-wrap items-center gap-2">
          <div className="font-bold">Trades</div>
          <select value={book} onChange={(e) => setBook(e.target.value)} className="rounded bg-white/10 px-2 py-1 text-xs">
            <option value="">All books</option>
            {(sum?.books || []).map((b) => <option key={b.book} value={b.book}>{b.book}</option>)}
          </select>
          <span className="text-xs text-white/50">{total} trades</span>
          <span className="ml-auto">
            <DateRangeDownload href={`${API_BASE}/api/paper-books/trades.csv${book ? `?book=${encodeURIComponent(book)}` : ""}`}
              onRange={(since, until) => setRange({ since, until })} />
          </span>
        </div>
        <div className="mt-2 max-h-[60vh] overflow-auto">
          <table className="w-full text-xs tabular-nums">
            <thead className="sticky top-0 bg-black/80 text-white/50">
              <tr className="text-left">
                {["Book", "Entry time", "Instrument", "Option", "Lots", "Entry", "Stop", "Exit", "Reason", "Costs", "Net", "Status", "Depth"].map((h) => (
                  <th key={h} className="px-2 py-1 whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={String(t.trade_id)} className="border-t border-white/5">
                  <td className="px-2 py-1 whitespace-nowrap">{t.book}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{ts(t.entry_ts_iso)}</td>
                  <td className="px-2 py-1">{t.instrument}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{t.option_symbol}</td>
                  <td className="px-2 py-1">{t.lots}</td>
                  <td className="px-2 py-1">{t.entry_price}</td>
                  <td className="px-2 py-1">{t.initial_stop}</td>
                  <td className="px-2 py-1">{t.exit_price ?? "—"}</td>
                  <td className="px-2 py-1 whitespace-nowrap">{t.exit_reason ?? "—"}</td>
                  <td className="px-2 py-1">{inr(t.costs as number | null)}</td>
                  <td className={`px-2 py-1 font-semibold ${pnl(t.net_pnl as number | null)}`}>{inr(t.net_pnl as number | null)}</td>
                  <td className="px-2 py-1">{t.status}</td>
                  <td className="px-2 py-1 whitespace-nowrap text-white/50">{t.depth_note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
