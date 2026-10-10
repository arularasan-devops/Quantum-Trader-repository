"use client";

import { useCallback, useEffect, useState } from "react";
import { getSignalDiagnostics, type SignalDiagnostics as Diag } from "@/lib/api";

/** Why the engine refused, and what the refused setup then did.
 *
 * The dashboard has always been able to say WAIT; it has never been able to say
 * WHICH gate said it. This panel names the gate for every live instrument, and
 * scores each gate on the outcome of the setups it refused.
 *
 * Deliberately data-gated: with nothing logged it says so instead of drawing an
 * empty league table, because a blocker table with two rows in it invites a
 * change to a gate on the strength of two trades.
 */
export default function SignalDiagnostics() {
  const [d, setD] = useState<Diag | null>(null);

  const refresh = useCallback(async () => {
    try {
      setD(await getSignalDiagnostics());
    } catch {
      /* transient: the panel simply keeps the last reading */
    }
  }, []);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 20000);
    return () => clearInterval(t);
  }, [refresh]);

  if (!d) return null;

  return (
    <div className="card p-3 flex flex-col gap-3 shrink-0">
      <div className="flex items-baseline gap-2">
        <div className="text-sm font-bold text-white/90">Signal diagnostics</div>
        <div className="text-[11px] text-white/50">
          which gate refused the trade · {d.total} refusals followed for {d.window_minutes} min
        </div>
      </div>

      {/* live: the named blocker for each instrument being watched right now */}
      {d.live.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {d.live.map((l) => (
            <div
              key={l.instrument}
              className="rounded-md border border-white/10 px-2 py-1 text-[11px]"
            >
              <span className="text-white/80 font-semibold">{l.instrument}</span>{" "}
              <span className="text-white/50">{l.signal ?? "—"}</span>
              {l.primary_blocker ? (
                <span className="ml-1 text-[#f4c025]">{l.primary_blocker}</span>
              ) : (
                <span className="ml-1 text-[#19e57f]">all gates clear</span>
              )}
              {l.secondary_blocker && (
                <span className="ml-1 text-white/40">+{l.secondary_blocker}</span>
              )}
              {l.confidence != null && l.confidence_gate != null && (
                <span className="ml-1 text-white/40">
                  {l.confidence}% / {l.confidence_gate}%
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {d.gates.length === 0 ? (
        <div className="text-[11px] text-white/50">
          No refusals recorded yet — the table fills in as the engine turns setups down and
          the {d.window_minutes}-minute window on each one closes. Nothing is estimated.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[11px]">
            <thead className="text-white/40">
              <tr className="text-left">
                <th className="py-1 pr-3">Gate</th>
                <th className="py-1 pr-3 text-right">Refused</th>
                <th className="py-1 pr-3 text-right">≥5%</th>
                <th className="py-1 pr-3 text-right">≥10%</th>
                <th className="py-1 pr-3 text-right">≥20%</th>
                <th className="py-1 pr-3 text-right">Avg move</th>
                <th className="py-1 text-right">Best</th>
              </tr>
            </thead>
            <tbody>
              {d.gates.map((g) => (
                <tr key={g.gate} className="border-t border-white/5">
                  <td className="py-1 pr-3 text-white/80 font-semibold">{g.gate}</td>
                  <td className="py-1 pr-3 text-right text-white/70">{g.refusals}</td>
                  <td className="py-1 pr-3 text-right text-white/60">{g.over_5pct}</td>
                  <td className="py-1 pr-3 text-right text-[#f4c025]">{g.over_10pct}</td>
                  <td className="py-1 pr-3 text-right text-[#ff5c5c]">{g.over_20pct}</td>
                  <td className="py-1 pr-3 text-right text-white/70">{g.avg_mfe_pct}%</td>
                  <td className="py-1 text-right text-white/50">
                    {g.best_mfe_pct == null ? "—" : `${g.best_mfe_pct}%`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* the other half: a BUY that never became a position */}
      {d.execution && (
        <div className="flex flex-col gap-2 border-t border-white/5 pt-2">
          <div className="flex items-baseline gap-2">
            <div className="text-[12px] font-bold text-white/80">Execution funnel</div>
            <div className="text-[11px] text-white/50">
              {d.execution.reached.SIGNAL ?? 0} actionable BUYs ·{" "}
              <span className="text-[#19e57f]">{d.execution.executable_pct}% filled</span>
            </div>
          </div>

          <div className="flex flex-wrap gap-1">
            {d.execution.stages.map((s) => (
              <div
                key={s}
                className="rounded border border-white/10 px-2 py-0.5 text-[10px] text-white/60"
              >
                {s} <span className="text-white/80">{d.execution.reached[s] ?? 0}</span>
              </div>
            ))}
          </div>

          {d.execution.blocked.length === 0 ? (
            <div className="text-[11px] text-white/50">
              Nothing has been blocked between signal and fill since the backend started.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[11px]">
                <thead className="text-white/40">
                  <tr className="text-left">
                    <th className="py-1 pr-3">Stage</th>
                    <th className="py-1 pr-3">Blocker</th>
                    <th className="py-1 text-right">Blocked</th>
                  </tr>
                </thead>
                <tbody>
                  {d.execution.blocked.map((b) => (
                    <tr key={`${b.stage}-${b.blocker}`} className="border-t border-white/5">
                      <td className="py-1 pr-3 text-white/50">{b.stage}</td>
                      <td className="py-1 pr-3 text-white/80 font-semibold">{b.blocker}</td>
                      <td className="py-1 text-right text-[#f4c025]">{b.count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* the most recent refusals, with the value that failed and where */}
          {d.execution.recent.slice(0, 5).map((e, i) => (
            <div key={`${e.ts}-${i}`} className="text-[10px] text-white/50">
              <span className="text-white/70">{e.instrument}</span> {e.primary_blocker}
              {e.value != null && e.threshold != null && (
                <span className="text-white/40">
                  {" "}
                  ({String(e.value)} vs {String(e.threshold)})
                </span>
              )}{" "}
              — {e.reason} <span className="text-white/30">{e.where}</span>
            </div>
          ))}
        </div>
      )}

      <div className="text-[10px] text-white/40 leading-relaxed">{d.note}</div>
    </div>
  );
}
