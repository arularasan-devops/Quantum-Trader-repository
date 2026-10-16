"use client";

import { useEffect, useState } from "react";
import {
  getTradeJournal,
  tradeJournalCsvUrl,
  type JournalBook,
  type JournalOpenPosition,
  type JournalTrade,
} from "@/lib/api";

const BOOKS: { key: JournalBook; label: string }[] = [
  { key: "all", label: "All" },
  { key: "options", label: "Options" },
  { key: "futures", label: "Futures" },
];

const GREEN = "#22c55e";
const RED = "#ef4444";
const MUTED = "rgba(255,255,255,0.4)";

function fmt(n: number | null, d = 1): string {
  return n == null ? "—" : n.toFixed(d);
}

function when(ts: number): string {
  if (!ts) return "—";
  const dt = new Date(ts * 1000);
  return dt.toLocaleString();
}

export default function JournalBoard() {
  const [rows, setRows] = useState<JournalTrade[]>([]);
  const [summary, setSummary] = useState({ count: 0, wins: 0, losses: 0, net_pnl: 0 });
  // The two books are different instruments with different risk models, so a
  // combined win rate is close to meaningless — score them apart.
  const [book, setBook] = useState<JournalBook>("all");
  const [extra, setExtra] = useState({ charges: 0, gross: 0, pf: null as number | null, unrealized: 0 });
  const [openPos, setOpenPos] = useState<JournalOpenPosition[]>([]);

  useEffect(() => {
    let alive = true;
    const load = () =>
      getTradeJournal(500, book)
        .then((r) => {
          if (!alive) return;
          setRows(r.trades || []);
          setSummary({ count: r.count, wins: r.wins, losses: r.losses, net_pnl: r.net_pnl });
          setExtra({
            charges: r.charges ?? 0, gross: r.gross_pnl ?? r.net_pnl,
            pf: r.profit_factor ?? null, unrealized: r.unrealized_pnl ?? 0,
          });
          setOpenPos(r.open_positions || []);
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [book]);

  const winPct = summary.count ? Math.round((summary.wins / summary.count) * 100) : 0;

  return (
    <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-3">
      <div className="card px-4 py-3 flex items-center justify-between gap-3 shrink-0 flex-wrap">
        <div className="flex items-baseline gap-3 flex-wrap min-w-0">
          <div className="text-lg font-black tracking-tight">TRADE JOURNAL</div>
          <span className="text-[10px] uppercase tracking-widest rounded-full px-2 py-0.5 font-bold"
            style={{ color: MUTED, border: `1px solid ${MUTED}55` }}>
            Signal + Auto-Buy · paper + live · saved locally
          </span>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-white/15 p-0.5">
          {BOOKS.map((b) => (
            <button
              key={b.key}
              onClick={() => setBook(b.key)}
              className={`text-xs font-semibold px-3 py-1 rounded-md ${
                book === b.key ? "bg-white/15 text-white" : "text-white/50 hover:bg-white/5"
              }`}
            >
              {b.label}
              {b.key === "futures" && (
                <span className="ml-1 text-[9px] text-amber-300/80">PAPER</span>
              )}
            </button>
          ))}
        </div>
        <a
          href={tradeJournalCsvUrl(5000, book)}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5"
        >
          ⬇ Download CSV{book === "all" ? "" : ` (${book})`}
        </a>
      </div>

      <div className="grid grid-cols-4 lg:grid-cols-8 gap-3 shrink-0">
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Trades</div>
          <div className="text-2xl font-black">{summary.count}</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Win rate</div>
          <div className="text-2xl font-black">{winPct}%</div>
          <div className="text-[11px] text-white/40">{summary.wins}W / {summary.losses}L</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Net P&amp;L (₹)</div>
          <div className="text-2xl font-black tabular-nums"
            style={{ color: summary.net_pnl >= 0 ? GREEN : RED }}>
            {summary.net_pnl >= 0 ? "+" : ""}{summary.net_pnl.toLocaleString()}
          </div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Gross (₹)</div>
          <div className="text-xl font-black tabular-nums">{extra.gross.toLocaleString()}</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Charges (₹)</div>
          <div className="text-xl font-black tabular-nums">{extra.charges.toLocaleString()}</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Profit factor</div>
          <div className="text-xl font-black tabular-nums">{extra.pf === null ? "—" : extra.pf.toFixed(2)}</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Unrealized (₹)</div>
          <div className="text-xl font-black tabular-nums"
            style={{ color: extra.unrealized >= 0 ? GREEN : RED }}>
            {extra.unrealized >= 0 ? "+" : ""}{extra.unrealized.toLocaleString()}
          </div>
          <div className="text-[11px] text-white/40">{openPos.length} open · not in totals</div>
        </div>
        <div className="card p-4">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Source</div>
          <div className="text-sm font-semibold mt-1">Local database</div>
          <div className="text-[11px] text-white/40">all closed trades, not just this page</div>
        </div>
      </div>

      {openPos.length > 0 && (
        <div className="card px-4 py-2 shrink-0 text-xs flex flex-wrap gap-x-5 gap-y-1">
          <span className="text-white/40 uppercase tracking-widest text-[10px]">Open</span>
          {openPos.map((o) => (
            <span key={o.option} className="tabular-nums">
              {o.instrument} {o.option} ×{o.lots} @ {o.entry ?? "—"} → {o.current ?? "—"}
              {o.stop != null ? ` · SL ${o.stop}` : ""}{" "}
              <b style={{ color: o.unrealized_pnl >= 0 ? GREEN : RED }}>
                {o.unrealized_pnl >= 0 ? "+" : ""}{o.unrealized_pnl.toLocaleString()}
              </b>
            </span>
          ))}
        </div>
      )}


      <div className="card p-0 overflow-hidden shrink-0">
        {rows.length === 0 ? (
          <div className="p-6 text-sm text-white/50">
            {book === "futures"
              ? "No futures paper trades closed yet. Turn the Futures tool on from the Futures tab; every trade it closes is recorded here."
              : "No trades recorded yet."}
            {book === "futures" ? "" : " "} Every paper and live trade that closes is logged
            here automatically with its exit reason, confidence and P&amp;L.
          </div>
        ) : (
          <div className="overflow-auto">
            <table className="w-full text-xs">
              <thead className="text-white/40 uppercase text-[10px] tracking-widest">
                <tr className="border-b border-white/10">
                  <th className="text-left px-3 py-2">Time</th>
                  <th className="text-left px-3 py-2">Instrument</th>
                  <th className="text-left px-3 py-2">Option</th>
                  <th className="text-right px-3 py-2">Entry→Exit</th>
                  <th className="text-right px-3 py-2">Lots</th>
                  <th className="text-right px-3 py-2">P&amp;L ₹</th>
                  {/* Premium paid on an option, margin blocked on a future — and the
                      net measured against it, so trades of different size compare. */}
                  <th className="text-right px-3 py-2">Capital used</th>
                  <th className="text-right px-3 py-2">Return</th>
                  <th className="text-left px-3 py-2">Mode</th>
                  <th className="text-left px-3 py-2">Trigger</th>
                  <th className="text-left px-3 py-2">Analyst note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-b border-white/5 hover:bg-white/5">
                    <td className="px-3 py-2 whitespace-nowrap text-white/60">{when(r.ts)}</td>
                    <td className="px-3 py-2">{r.instrument}</td>
                    <td className="px-3 py-2">{r.option ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{fmt(r.entry)}→{fmt(r.exit)}</td>
                    <td className="px-3 py-2 text-right tabular-nums">{r.lots ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular-nums font-semibold"
                      style={{ color: (r.net_pnl ?? 0) >= 0 ? GREEN : RED }}>
                      {(r.net_pnl ?? 0) >= 0 ? "+" : ""}{(r.net_pnl ?? 0).toLocaleString()}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums text-white/60">
                      {r.capital_used == null
                        ? "—"
                        : Math.round(r.capital_used).toLocaleString("en-IN")}
                    </td>
                    <td className="px-3 py-2 text-right tabular-nums"
                      style={{ color: (r.return_on_capital_pct ?? 0) >= 0 ? GREEN : RED }}>
                      {r.return_on_capital_pct == null
                        ? "—"
                        : `${r.return_on_capital_pct > 0 ? "+" : ""}${r.return_on_capital_pct}%`}
                    </td>
                    <td className="px-3 py-2">
                      <span className="uppercase text-[10px] font-bold"
                        style={{ color: r.mode === "live" ? RED : MUTED }}>
                        {r.mode}{r.auto ? " · bot" : ""}
                      </span>
                    </td>
                    <td className="px-3 py-2">
                      <span className="text-[10px] font-bold uppercase text-white/60">
                        {r.entry_trigger ?? "—"}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-white/70 max-w-md">{r.note ?? "—"}</td>
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
