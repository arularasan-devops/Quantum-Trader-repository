"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { API_BASE } from "@/lib/api";

type SignalRow = {
  trade_status?: string | null;
  trade_net_pnl?: number | null;
  trade_exit_reason?: string | null;
  auto_eligible?: boolean;
  event_id: string;
  ts_iso: string;
  instrument: string;
  mode: string | null;
  outcome: string;
  reason: string | null;
  setup: string | null;
  regime: string | null;
  htf_trend: string | null;
  confidence: number | null;
  option_symbol: string | null;
  bid: number | null;
  ask: number | null;
  expected_entry: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  trade_id: string | null;
  explanation: string | null;
};

type Summary = {
  total: number;
  first_ts_iso: string | null;
  last_ts_iso: string | null;
  by_outcome: Record<string, number>;
  by_setup: Record<string, number>;
  by_regime: Record<string, number>;
  top_skip_reasons: { reason: string; count: number }[];
};

const OUTCOMES = ["EXECUTED", "SKIPPED", "REJECTED", "NOT_EXECUTABLE"] as const;
const OUTCOME_COLOR: Record<string, string> = {
  EXECUTED: "#22c55e",
  SKIPPED: "#f59e0b",
  REJECTED: "#ef4444",
  NOT_EXECUTABLE: "#94a3b8",
};
const RANGES: { label: string; hours: number | null }[] = [
  { label: "Today", hours: 0 },
  { label: "24h", hours: 24 },
  { label: "7d", hours: 24 * 7 },
  { label: "30d", hours: 24 * 30 },
  { label: "All", hours: null },
];
const PAGE_SIZE = 100;

const fmt = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));
const when = (iso: string | null | undefined) => (iso ? iso.replace("T", " ").slice(0, 19) : "—");

function sinceEpoch(hours: number | null): number | null {
  if (hours === null) return null;
  if (hours === 0) {
    const d = new Date();
    d.setHours(0, 0, 0, 0);
    return Math.floor(d.getTime() / 1000);
  }
  return Math.floor(Date.now() / 1000) - hours * 3600;
}

/** Reports tab: every BUY signal the engine produced, persisted in journal v2
 *  (PostgreSQL), whether it was executed or skipped and why. */
export default function ReportsBoard() {
  const [outcome, setOutcome] = useState("");
  const [instrument, setInstrument] = useState("");
  const [setup, setSetup] = useState("");
  const [regime, setRegime] = useState("");
  const [range, setRange] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const query = useMemo(() => {
    const q = new URLSearchParams({ signal: "BUY" });
    if (outcome) q.set("outcome", outcome);
    if (instrument.trim()) q.set("instrument", instrument.trim().toUpperCase());
    if (setup) q.set("setup", setup);
    if (regime) q.set("regime", regime);
    const since = sinceEpoch(range);
    if (since !== null) q.set("since", String(since));
    return q.toString();
  }, [outcome, instrument, setup, regime, range]);

  useEffect(() => setPage(1), [query]);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const base = `${API_BASE}/api/journal/v2/signals`;
        const [l, s] = await Promise.all([
          fetch(`${base}?${query}&page=${page}&page_size=${PAGE_SIZE}`),
          fetch(`${base}/summary?${query}`),
        ]);
        if (!l.ok) throw new Error(`signals HTTP ${l.status}`);
        if (!s.ok) throw new Error(`summary HTTP ${s.status}`);
        const lj = await l.json();
        const sj = await s.json();
        if (!alive) return;
        setRows(lj.items ?? []);
        setTotal(lj.total ?? 0);
        setSummary(sj);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [query, page]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const executed = summary?.by_outcome.EXECUTED ?? 0;
  const setups = Object.keys(summary?.by_setup ?? {}).filter((k) => k !== "—");
  const regimes = Object.keys(summary?.by_regime ?? {}).filter((k) => k !== "—");
  const select = "bg-black/30 border border-white/15 rounded-md px-2 py-1 text-xs";

  return (
    <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-3">
      <div className="card px-4 py-3 flex items-center justify-between gap-3 shrink-0 flex-wrap">
        <div className="flex items-baseline gap-3 flex-wrap">
          <div className="text-lg font-black tracking-tight">BUY SIGNAL REPORT</div>
          <span className="text-xs text-white/60">
            Every BUY signal stored in the database, executed or not, with the reason.
            Auto-entry gate: PULLBACK in TRENDING at ≥80% confidence (PAPER).
          </span>
        </div>
        <a
          href={`${API_BASE}/api/journal/v2/signals.csv?${query}`}
          target="_blank"
          rel="noreferrer"
          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5"
        >
          Download CSV
        </a>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-3 shrink-0">
        <div className="card p-3">
          <div className="text-[10px] uppercase tracking-widest text-white/50">Buy signals</div>
          <div className="text-2xl font-bold tabular-nums">{summary?.total ?? "—"}</div>
          <div className="text-[10px] text-white/40">
            {when(summary?.first_ts_iso)} → {when(summary?.last_ts_iso)}
          </div>
        </div>
        {OUTCOMES.map((o) => (
          <button
            key={o}
            onClick={() => setOutcome(outcome === o ? "" : o)}
            className={`card p-3 text-left ${outcome === o ? "ring-1 ring-white/40" : ""}`}
            title="Click to filter"
          >
            <div className="text-[10px] uppercase tracking-widest" style={{ color: OUTCOME_COLOR[o] }}>
              {o.replace("_", " ")}
            </div>
            <div className="text-2xl font-bold tabular-nums">{summary?.by_outcome[o] ?? 0}</div>
          </button>
        ))}
        <div className="card p-3">
          <div className="text-[10px] uppercase tracking-widest text-white/50">Taken rate</div>
          <div className="text-2xl font-bold tabular-nums">
            {summary && summary.total ? `${((executed / summary.total) * 100).toFixed(1)}%` : "—"}
          </div>
        </div>
      </div>

      {summary && summary.top_skip_reasons.length > 0 && (
        <div className="card p-3 shrink-0">
          <div className="text-[10px] uppercase tracking-widest text-white/50 mb-2">
            Why signals were not taken
          </div>
          <div className="flex flex-wrap gap-2">
            {summary.top_skip_reasons.map((r) => (
              <span key={r.reason} className="text-xs rounded-md border border-white/15 px-2 py-1">
                <span className="font-bold tabular-nums mr-1">{r.count}</span>
                {r.reason}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="card p-3 flex flex-col min-h-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap mb-2 text-xs">
          <div className="flex rounded-md border border-white/15 overflow-hidden">
            {RANGES.map((r) => (
              <button
                key={r.label}
                onClick={() => setRange(r.hours)}
                className={range === r.hours ? "px-2 py-1 bg-white/10" : "px-2 py-1 text-white/60 hover:bg-white/5"}
              >
                {r.label}
              </button>
            ))}
          </div>
          <select className={select} value={outcome} onChange={(e) => setOutcome(e.target.value)}>
            <option value="">All outcomes</option>
            {OUTCOMES.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <select className={select} value={setup} onChange={(e) => setSetup(e.target.value)}>
            <option value="">All setups</option>
            {(setup && !setups.includes(setup) ? [...setups, setup] : setups).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <select className={select} value={regime} onChange={(e) => setRegime(e.target.value)}>
            <option value="">All regimes</option>
            {(regime && !regimes.includes(regime) ? [...regimes, regime] : regimes).map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <input
            className={select}
            placeholder="Instrument (e.g. NIFTY)"
            value={instrument}
            onChange={(e) => setInstrument(e.target.value)}
          />
          <span className="ml-auto flex items-center gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}
                    className="px-2 py-1 rounded border border-white/15 disabled:opacity-30">Prev</button>
            <span className="tabular-nums">page {page}/{pages} · {total} rows</span>
            <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages}
                    className="px-2 py-1 rounded border border-white/15 disabled:opacity-30">Next</button>
          </span>
        </div>
        {error && <div className="text-xs text-red-400 mb-2">Report unavailable: {error}</div>}
        <div className="overflow-auto min-h-0 flex-1">
          <table className="w-full text-xs border-collapse">
            <thead className="sticky top-0 bg-[#0b1220]">
              <tr className="text-left text-white/50">
                <th className="py-1 pr-2">Time (IST)</th><th className="pr-2">Instrument</th>
                <th className="pr-2">Option</th><th className="pr-2">Setup</th>
                <th className="pr-2">Regime</th><th className="pr-2">HTF</th>
                <th className="pr-2 text-right">Conf</th><th className="pr-2 text-right">Bid/Ask</th>
                <th className="pr-2 text-right">Entry</th><th className="pr-2 text-right">Stop</th>
                <th className="pr-2 text-right">T1/T2/T3</th><th className="pr-2">Outcome</th>
                <th className="pr-2">Reason</th><th>Mode</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <Fragment key={s.event_id}>
                  <tr
                    onClick={() => setOpen(open === s.event_id ? null : s.event_id)}
                    className="border-t border-white/10 hover:bg-white/5 cursor-pointer"
                  >
                    <td className="py-1 pr-2 tabular-nums whitespace-nowrap">{when(s.ts_iso)}</td>
                    <td className="pr-2">{s.instrument}</td>
                    <td className="pr-2">{s.option_symbol ?? "—"}</td>
                    <td className="pr-2">{s.setup ?? "—"}</td>
                    <td className="pr-2">{s.regime ?? "—"}</td>
                    <td className="pr-2">{s.htf_trend ?? "—"}</td>
                    <td className="pr-2 text-right tabular-nums">{fmt(s.confidence, 1)}</td>
                    <td className="pr-2 text-right tabular-nums whitespace-nowrap">
                      {s.bid == null && s.ask == null ? "—" : `${fmt(s.bid)} / ${fmt(s.ask)}`}
                    </td>
                    <td className="pr-2 text-right tabular-nums">{fmt(s.expected_entry)}</td>
                    <td className="pr-2 text-right tabular-nums">{fmt(s.stop)}</td>
                    <td className="pr-2 text-right tabular-nums whitespace-nowrap">
                      {fmt(s.target1)} / {fmt(s.target2)} / {fmt(s.target3)}
                    </td>
                    <td className="pr-2 font-semibold" style={{ color: OUTCOME_COLOR[s.outcome] ?? "inherit" }}>
                      {s.outcome}
                    </td>
                    <td className="pr-2">{s.reason ?? ""}</td>
                    <td className="uppercase">{s.mode ?? "—"}</td>
                  </tr>
                  {open === s.event_id && (
                    <tr className="bg-white/5">
                      <td colSpan={14} className="px-2 py-2 text-white/70">
                        <div><b>Why the engine signalled:</b> {s.explanation ?? "—"}</div>
                        {s.trade_id && <div><b>Trade:</b> {s.trade_id} (see Journal tab)</div>}
                        {s.trade_id && s.trade_status && (
                          <div><b>Trade result:</b> {s.trade_status}
                            {s.trade_net_pnl != null ? ` · net ₹${Number(s.trade_net_pnl).toLocaleString()}` : ""}
                            {s.trade_exit_reason ? ` · ${s.trade_exit_reason}` : ""}</div>
                        )}
                        <div><b>Auto-buy eligible setup:</b> {s.auto_eligible ? "yes (PULLBACK + TRENDING)" : "no"}</div>
                        <div className="text-white/40">event {s.event_id}</div>
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
              {!rows.length && !error && (
                <tr><td colSpan={14} className="py-3 text-white/50">No buy signals recorded for these filters yet.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
