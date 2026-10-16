"use client";

import { Fragment, useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

type Trade = {
  trade_id: string; instrument: string; mode: string; setup?: string; regime?: string;
  option_symbol?: string; lot_size?: number; lots?: number; capital_used?: number;
  entry_ts_iso?: string; entry_price?: number; initial_stop?: number;
  target1?: number; target2?: number; target3?: number; status: string;
  exit_ts_iso?: string; exit_price?: number; exit_reason?: string;
  gross_pnl?: number; costs?: number; net_pnl?: number; holding_minutes?: number;
  entry_explanation?: string;
};

const fmt = (v?: number | null, d = 1) => (v == null ? "—" : v.toFixed(d));
const inr = (v?: number | null) =>
  v == null ? "—" : `₹${Math.round(v).toLocaleString("en-IN")}`;
const ts = (s?: string) => (s ? s.replace("T", " ").slice(0, 19) : "—");

// Journal = only trades the auto-buy engine took (DB-backed journal v2).
export default function JournalBoard() {
  const [rows, setRows] = useState<Trade[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const pageSize = 50;

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/journal/v2/trades?auto=true&page=${page}&page_size=${pageSize}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (alive) { setRows(j.items || []); setTotal(j.total || 0); setErr(null); }
      } catch (e) {
        if (alive) setErr(String(e));
      }
    };
    load();
    const id = setInterval(load, 5000);
    return () => { alive = false; clearInterval(id); };
  }, [page]);

  const closed = rows.filter((r) => r.status === "CLOSED");
  const net = closed.reduce((a, r) => a + (r.net_pnl || 0), 0);
  const wins = closed.filter((r) => (r.net_pnl || 0) > 0).length;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  return (
    <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-3">
      <div className="flex items-center gap-3 flex-wrap">
        <div className="text-sm font-semibold">Auto-bought trades ({total})</div>
        <div className="text-xs text-white/60">
          this page: {closed.length} closed · {wins} wins · net {inr(net)}
        </div>
        <a href={`${API_BASE}/api/journal/v2/trades.csv?auto=true`}
           className="ml-auto text-xs px-2 py-1 rounded bg-white/10 hover:bg-white/15">
          ⬇ Download CSV
        </a>
        <div className="text-xs text-white/40">PostgreSQL · journal v2</div>
      </div>
      {err && <div className="text-xs text-red-400">Journal unavailable: {err}</div>}
      <div className="overflow-auto">
        <table className="w-full text-xs">
          <thead className="text-white/50">
            <tr>
              {["Entry time", "Instrument", "Option", "Setup / regime", "Lots", "Entry", "Stop",
                "T1 / T2 / T3", "Exit time", "Exit", "Exit reason", "Hold", "Net P&L", "Status"].map((h) => (
                <th key={h} className="text-left px-2 py-1.5 whitespace-nowrap">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.trade_id}>
                <tr className="border-t border-white/5 hover:bg-white/5 cursor-pointer"
                    onClick={() => setOpen(open === r.trade_id ? null : r.trade_id)}>
                  <td className="px-2 py-1.5 whitespace-nowrap">{ts(r.entry_ts_iso)}</td>
                  <td className="px-2 py-1.5">{r.instrument}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{r.option_symbol || "—"}</td>
                  <td className="px-2 py-1.5">{r.setup || "—"} / {r.regime || "—"}</td>
                  <td className="px-2 py-1.5">{r.lots ?? "—"}×{r.lot_size ?? "—"}</td>
                  <td className="px-2 py-1.5">{fmt(r.entry_price)}</td>
                  <td className="px-2 py-1.5">{fmt(r.initial_stop)}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{fmt(r.target1)} / {fmt(r.target2)} / {fmt(r.target3)}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap">{ts(r.exit_ts_iso)}</td>
                  <td className="px-2 py-1.5">{fmt(r.exit_price)}</td>
                  <td className="px-2 py-1.5">{r.exit_reason || "—"}</td>
                  <td className="px-2 py-1.5">{r.holding_minutes != null ? `${r.holding_minutes}m` : "—"}</td>
                  <td className={`px-2 py-1.5 ${(r.net_pnl || 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                    {inr(r.net_pnl)}
                  </td>
                  <td className="px-2 py-1.5">{r.status} · {r.mode}</td>
                </tr>
                {open === r.trade_id && (
                  <tr className="bg-white/5">
                    <td colSpan={14} className="px-3 py-2 text-white/70">
                      <div><b>Entry reason:</b> {r.entry_explanation || "—"}</div>
                      <div>
                        <b>Capital used:</b> {inr(r.capital_used)} · <b>Gross:</b> {inr(r.gross_pnl)} ·{" "}
                        <b>Costs:</b> {inr(r.costs)} · <b>Trade ID:</b> {r.trade_id}
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {rows.length === 0 && !err && (
              <tr><td colSpan={14} className="px-2 py-4 text-white/40">No auto-bought trades yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {pages > 1 && (
        <div className="flex gap-2 items-center text-xs">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="px-2 py-1 rounded bg-white/10 disabled:opacity-30">Prev</button>
          <span>{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="px-2 py-1 rounded bg-white/10 disabled:opacity-30">Next</button>
        </div>
      )}
    </div>
  );
}
