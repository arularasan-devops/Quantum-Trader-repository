"use client";

import { useEffect, useState } from "react";
import { getDailyBest, type DailyBestReport } from "@/lib/api";

export default function DailyBestBoard() {
  const [data, setData] = useState<DailyBestReport | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  async function load() {
    setLoading(true);
    try {
      setData(await getDailyBest(120));
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const box = "rounded-xl border border-white/10 bg-white/[0.03] p-4";
  const pct = (v: number | null | undefined) =>
    v == null ? "N/A" : `${v.toFixed(1)}%`;

  return (
    <section className="space-y-4 text-white">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-xl font-bold">One a Day — Historical Evidence</h2>
          <p className="text-sm text-white/50">
            Read-only results. R values are gross of costs, not live performance.
          </p>
        </div>
        <button onClick={() => void load()} className="rounded-lg border border-white/15 px-3 py-2 text-sm">
          Refresh
        </button>
      </div>

      {loading && <p className="text-white/60">Loading daily-best report…</p>}
      {error && <p className="text-red-300">Unable to load report: {error}</p>}

      {data && (
        <>
          <div className={box}>
            <div className="text-sm text-white/50">Ranking verdict</div>
            <div className="mt-1 font-semibold">{data.ranking_verdict || "Not available"}</div>
            <div className="mt-2 text-xs text-amber-300">
              {data.ranking_validated ? "Ranking marked validated by API" : "Ranking not validated"}
              {" · "}
              {data.research_only ? "Research only" : "Research status not confirmed"}
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              ["Sessions", data.every_signal.sessions],
              ["Graded signals", data.every_signal.graded],
              ["Reached T1", data.every_signal.reached_t1],
              ["T1 rate", pct(data.every_signal.reached_t1_pct)],
            ].map(([label, value]) => (
              <div className={box} key={String(label)}>
                <div className="text-xs text-white/50">{label}</div>
                <div className="mt-2 text-xl font-bold">{value}</div>
              </div>
            ))}
          </div>

          {Object.entries(data.policies || {}).map(([name, policy]) => (
            <div className={box} key={name}>
              <h3 className="mb-3 font-bold">{name}</h3>
              <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <div><div className="text-xs text-white/50">Sessions</div><div>{policy.summary.sessions}</div></div>
                <div><div className="text-xs text-white/50">T1 reached</div><div>{pct(policy.summary.reached_t1_pct)}</div></div>
                <div><div className="text-xs text-white/50">Mean gross R</div><div>{policy.summary.mean_r_gross ?? "N/A"}</div></div>
                <div><div className="text-xs text-white/50">Evidence</div><div className="text-sm">{policy.summary.evidence || "N/A"}</div></div>
              </div>

              <div className="mt-4 overflow-x-auto">
                <table className="w-full text-left text-xs">
                  <thead className="text-white/50">
                    <tr>
                      <th className="p-2">Session</th><th className="p-2">Instrument</th>
                      <th className="p-2">Direction</th><th className="p-2">Outcome</th>
                      <th className="p-2">T1</th><th className="p-2">Gross R</th>
                    </tr>
                  </thead>
                  <tbody>
                    {policy.history.map((r, i) => (
                      <tr key={`${r.signal_id || r.session || name}-${i}`} className="border-t border-white/10">
                        <td className="p-2">{r.session || "—"}</td>
                        <td className="p-2">{r.instrument || "—"}</td>
                        <td className="p-2">{r.direction || "—"}</td>
                        <td className="p-2">{r.outcome || "PENDING"}</td>
                        <td className="p-2">{r.reached_t1 ? "Yes" : "No"}</td>
                        <td className="p-2">{r.realized_r ?? "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ))}

          {data.notes?.length > 0 && (
            <div className={box}>
              <h3 className="mb-2 font-bold">Notes and limitations</h3>
              {data.notes.map((n, i) => <p className="text-sm text-white/60" key={i}>{n}</p>)}
            </div>
          )}
        </>
      )}
    </section>
  );
}
