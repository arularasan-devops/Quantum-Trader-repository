"use client";

import { useEffect, useState } from "react";
import {
  getSignalBoardHistory,
  signalBoardReportCsvUrl,
  type SignalBoardRow,
} from "@/lib/api";

export default function ReportsBoard() {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [instrument, setInstrument] = useState("");
  const [rows, setRows] = useState<SignalBoardRow[]>([]);
  const [count, setCount] = useState(0);
  const [recording, setRecording] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const result = await getSignalBoardHistory(300, {
        date_from: from || undefined,
        date_to: to || undefined,
        instrument: instrument || undefined,
      });
      setRows(result.rows || []);
      setCount(result.count || 0);
      setRecording(result.recording?.verdict || "Recording status unavailable");
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const csvUrl = signalBoardReportCsvUrl({
    date_from: from || undefined,
    date_to: to || undefined,
    instrument: instrument || undefined,
  });
  const box = "rounded-xl border border-white/10 bg-white/[0.03] p-4";

  return (
    <section className="space-y-4 text-white">
      <div>
        <h2 className="text-xl font-bold">Signal Reports</h2>
        <p className="text-sm text-white/50">Historical signal journal. Read-only; CSV export does not place orders.</p>
      </div>

      <div className={`${box} flex flex-wrap items-end gap-3`}>
        <label className="text-xs text-white/60">From date
          <input type="date" value={from} onChange={e => setFrom(e.target.value)}
            className="mt-1 block rounded-lg border border-white/15 bg-black p-2 text-white" />
        </label>
        <label className="text-xs text-white/60">To date
          <input type="date" value={to} onChange={e => setTo(e.target.value)}
            className="mt-1 block rounded-lg border border-white/15 bg-black p-2 text-white" />
        </label>
        <label className="text-xs text-white/60">Instrument
          <input value={instrument} onChange={e => setInstrument(e.target.value.toUpperCase())}
            placeholder="All instruments"
            className="mt-1 block w-40 rounded-lg border border-white/15 bg-black p-2 text-white" />
        </label>
        <button onClick={() => void load()} disabled={loading}
          className="rounded-lg border border-white/15 px-4 py-2 text-sm disabled:opacity-50">
          {loading ? "Loading…" : "Apply filters"}
        </button>
        <a href={csvUrl} className="rounded-lg border border-sky-400/30 px-4 py-2 text-sm text-sky-300">
          Download CSV
        </a>
      </div>

      {error && <p className="text-red-300">{error}</p>}
      <div className={box}>
        <div className="font-semibold">{count} records returned</div>
        <p className="mt-1 text-xs text-white/50">Journal status: {recording || "Loading…"}</p>
      </div>

      <div className={`${box} overflow-x-auto`}>
        <table className="w-full text-left text-xs">
          <thead className="text-white/50">
            <tr>
              <th className="p-2">Recorded</th><th className="p-2">Instrument</th>
              <th className="p-2">Action</th><th className="p-2">Vehicle</th>
              <th className="p-2">Score</th><th className="p-2">Status</th>
              <th className="p-2">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={`${r.signal_id}-${i}`} className="border-t border-white/10">
                <td className="p-2">{r.time_ist || r.recorded_at || "—"}</td>
                <td className="p-2">{r.instrument || "—"}</td>
                <td className="p-2">{r.board_action || "—"}</td>
                <td className="p-2">{r.signal_vehicle || r.vehicle || "—"}</td>
                <td className="p-2">{r.signal_score ?? "—"}</td>
                <td className="p-2">{r.signal_status || "—"}</td>
                <td className="p-2">{r.outcome_state || "Pending"}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!loading && rows.length === 0 && <p className="mt-3 text-sm text-white/50">No records for these filters. Check the journal status above.</p>}
      </div>
    </section>
  );
}
