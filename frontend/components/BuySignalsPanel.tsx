"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";

type SignalRow = {
  event_id: string;
  ts_iso: string;
  instrument: string;
  option_symbol: string | null;
  outcome: string;
  reason: string | null;
  setup: string | null;
  regime: string | null;
  confidence: number | null;
  mode?: string | null;
};

const OUTCOME_COLOR: Record<string, string> = {
  EXECUTED: "#22c55e",
  SKIPPED: "#f59e0b",
  REJECTED: "#ef4444",
  NOT_EXECUTABLE: "#94a3b8",
  FAILED_CONDITIONS: "#94a3b8",
};

/** Every BUY signal from journal v2 (PostgreSQL), executed or not, with the reason. */
export default function BuySignalsPanel() {
  const [rows, setRows] = useState<SignalRow[]>([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const pageSize = 100;
  const pages = Math.max(1, Math.ceil(total / pageSize));

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = await fetch(`${API_BASE}/api/journal/v2/signals?signal=BUY&page=${page}&page_size=${pageSize}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const j = await r.json();
        if (!alive) return;
        setRows(j.items ?? []);
        setTotal(j.total ?? 0);
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
  }, [page]);

  return (
    <section style={{ marginTop: 24, flexShrink: 0, maxHeight: "45vh", display: "flex", flexDirection: "column" }}>
      <h3 style={{ fontSize: 14, fontWeight: 600, marginBottom: 8 }}>
        Buy signals ({total}) · strategy: PULLBACK in TRENDING
        <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page <= 1}
                style={{ marginLeft: 12, fontSize: 12 }}>Prev</button>
        <span style={{ margin: "0 6px", fontSize: 12 }}>page {page}/{pages}</span>
        <button onClick={() => setPage((p) => Math.min(pages, p + 1))} disabled={page >= pages}
                style={{ fontSize: 12 }}>Next</button>
        <a href={`${API_BASE}/api/journal/v2/trades.csv`}
           style={{ marginLeft: 12, fontSize: 12 }} target="_blank" rel="noreferrer">Trades CSV</a>
      </h3>
      {error && <div style={{ color: "#ef4444", fontSize: 12 }}>Signals unavailable: {error}</div>}
      <div style={{ overflow: "auto", minHeight: 0, flex: 1 }}>
        <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ textAlign: "left", opacity: 0.7 }}>
              <th>Time</th><th>Instrument</th><th>Option</th><th>Setup</th>
              <th>Regime</th><th>Conf</th><th>Outcome</th><th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.event_id} style={{ borderTop: "1px solid rgba(148,163,184,0.2)" }}>
                <td>{s.ts_iso?.replace("T", " ").slice(0, 19)}</td>
                <td>{s.instrument}</td>
                <td>{s.option_symbol ?? "—"}</td>
                <td>{s.setup ?? "—"}</td>
                <td>{s.regime ?? "—"}</td>
                <td>{s.confidence != null ? s.confidence.toFixed(1) : "—"}</td>
                <td style={{ color: OUTCOME_COLOR[s.outcome] ?? "inherit" }}>{s.outcome}</td>
                <td>{s.reason ?? ""}</td>
              </tr>
            ))}
            {!rows.length && !error && (
              <tr><td colSpan={8} style={{ opacity: 0.6 }}>No buy signals recorded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
