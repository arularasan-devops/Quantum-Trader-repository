"use client";

import { useEffect, useState } from "react";
import {
  getP28Summary,
  getP28Coverage,
  getP28Funnel,
  getP28Economics,
  getP28Strategies,
} from "@/lib/api";

type ResearchData = {
  summary: Awaited<ReturnType<typeof getP28Summary>> | null;
  coverage: Awaited<ReturnType<typeof getP28Coverage>> | null;
  funnel: Awaited<ReturnType<typeof getP28Funnel>> | null;
  economics: Awaited<ReturnType<typeof getP28Economics>> | null;
  strategies: Awaited<ReturnType<typeof getP28Strategies>> | null;
};

const initial: ResearchData = {
  summary: null, coverage: null, funnel: null, economics: null, strategies: null,
};

export default function CoreAplusPaper() {
  const [data, setData] = useState<ResearchData>(initial);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    setLoading(true);
    try {
      const [summary, coverage, funnel, economics, strategies] = await Promise.all([
        getP28Summary(), getP28Coverage(), getP28Funnel(), getP28Economics(), getP28Strategies(50),
      ]);
      setData({ summary, coverage, funnel, economics, strategies });
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const box = "rounded-xl border border-white/10 bg-white/[0.03] p-4";
  const pretty = (v: unknown) => v == null ? "Not available" : typeof v === "string" ? v : JSON.stringify(v, null, 2);

  return (
    <section className="space-y-4 text-white">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">Core A+ — Research & Validation</h2>
          <p className="text-sm text-white/50">Phase 28 research evidence. Paper/research only; no orders or strategy activation.</p>
        </div>
        <button onClick={() => void load()} disabled={loading}
          className="rounded-lg border border-white/15 px-3 py-2 text-sm disabled:opacity-50">
          {loading ? "Loading…" : "Refresh research"}
        </button>
      </div>

      <div className="rounded-xl border border-amber-400/20 bg-amber-400/[0.04] p-3 text-sm text-amber-200">
        PAPER / RESEARCH ONLY. A displayed strategy or score is not approval to trade.
      </div>
      {loading && <p className="text-white/60">Loading Phase 28 research endpoints…</p>}
      {error && <p className="text-red-300">Unable to load all research data: {error}</p>}

      {data.summary && (
        <div className={box}>
          <h3 className="mb-2 font-bold">Study conclusion</h3>
          <div className="text-lg font-semibold">{data.summary.conclusion?.headline || "No conclusion headline available"}</div>
          <p className="mt-2 text-sm text-white/60">{data.summary.conclusion?.statement || data.summary.note || "No additional statement supplied by the API."}</p>
          <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
            <div><div className="text-xs text-white/50">Availability</div><div>{data.summary.available ? "Available" : "Unavailable"}</div></div>
            <div><div className="text-xs text-white/50">Version</div><div>{data.summary.version || "—"}</div></div>
            <div><div className="text-xs text-white/50">Data status</div><div>{data.summary.data_status || "—"}</div></div>
            <div><div className="text-xs text-white/50">Runtime</div><div>{data.summary.runtime_seconds ?? "—"} s</div></div>
          </div>
          {data.summary.stop_rule && <p className="mt-3 text-sm text-amber-200">Stop rule: {data.summary.stop_rule}</p>}
          {data.summary.equity_history && (
            <p className="mt-2 text-sm text-white/60">
              Equity-history status: {data.summary.equity_history.status}. Missing: {data.summary.equity_history.missing?.join(", ") || "none reported"}.
            </p>
          )}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className={box}>
          <h3 className="mb-2 font-bold">Data coverage</h3>
          <p className="text-sm text-white/50">Usable: {data.coverage?.usable?.length ?? "—"} · Excluded: {data.coverage?.excluded?.length ?? "—"}</p>
          <pre className="mt-3 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-white/65">{pretty(data.coverage)}</pre>
        </div>
        <div className={box}>
          <h3 className="mb-2 font-bold">Candidate funnel</h3>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-white/65">{pretty(data.funnel)}</pre>
        </div>
        <div className={box}>
          <h3 className="mb-2 font-bold">Trading economics</h3>
          <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs text-white/65">{pretty(data.economics)}</pre>
        </div>
      </div>

      <div className={box}>
        <h3 className="mb-3 font-bold">Candidate strategies ({data.strategies?.count ?? 0})</h3>
        {data.strategies?.stop_rule && <p className="mb-3 text-xs text-amber-200">Stop rule: {data.strategies.stop_rule}</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-white/50">
              <tr><th className="p-2">Strategy</th><th className="p-2">Instrument</th><th className="p-2">Vehicle</th><th className="p-2">Status</th><th className="p-2">Gate verdict</th><th className="p-2">Holdout avg net R</th></tr>
            </thead>
            <tbody>
              {data.strategies?.rows?.map((r) => (
                <tr key={r.strategy_id} className="border-t border-white/10">
                  <td className="p-2">{r.strategy_id}</td>
                  <td className="p-2">{r.instrument}</td>
                  <td className="p-2">{r.vehicle}</td>
                  <td className="p-2">{r.status}</td>
                  <td className="p-2">{r.gate_verdict || "—"}</td>
                  <td className="p-2">{r.holdout?.avg_net_r ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!loading && data.strategies?.rows?.length === 0 && <p className="mt-3 text-sm text-white/50">No strategy rows returned by the API.</p>}
      </div>
    </section>
  );
}
