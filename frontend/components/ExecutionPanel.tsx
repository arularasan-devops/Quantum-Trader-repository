"use client";
import { Decision } from "@/lib/types";

const ACTION_LABEL: Record<string, string> = {
  ENTER_NOW: "ENTER NOW",
  WAIT_FOR_RETRACEMENT: "WAIT FOR RETRACEMENT",
  CHANGE_STRIKE: "CHANGE STRIKE",
  SKIP_TRADE: "SKIP TRADE",
};

const ACTION_COLOR: Record<string, string> = {
  ENTER_NOW: "#19e57f",
  WAIT_FOR_RETRACEMENT: "#f5b301",
  CHANGE_STRIKE: "#3aa0ff",
  SKIP_TRADE: "#ff4d4d",
};

function scoreColor(v: number | null | undefined): string {
  if (v == null) return "#8b93a7";
  if (v >= 80) return "#19e57f";
  if (v >= 60) return "#f5b301";
  return "#ff6b6b";
}

function Metric({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="text-center">
      <div className="text-[10px] uppercase tracking-wider text-white/45">{label}</div>
      <div className="text-xl font-black mt-0.5" style={{ color: color ?? "#fff" }}>{value}</div>
    </div>
  );
}

export default function ExecutionPanel({ decision }: { decision: Decision }) {
  const ei = decision.execution_intelligence;
  if (!ei || !ei.enabled) return null;

  const action = ei.recommended_action;
  const acolor = ACTION_COLOR[action] ?? "#fff";

  return (
    <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-bold tracking-wide text-white/80">
          Execution Intelligence <span className="text-white/35 font-normal">· Phase 3.6 · advisory</span>
        </div>
        <div
          className="rounded-md px-3 py-1 text-sm font-black"
          style={{ color: acolor, border: `1px solid ${acolor}66`, background: `${acolor}12` }}
        >
          {ACTION_LABEL[action] ?? action}
        </div>
      </div>

      <div className="grid grid-cols-5 divide-x divide-white/10 py-2">
        <Metric label="Grade" value={ei.grade} color={ei.grade.startsWith("A") ? "#19e57f" : ei.grade === "B" ? "#f5b301" : "#ff6b6b"} />
        <Metric label="Entry Timing" value={ei.entry_timing} />
        <Metric label="Strike Quality" value={ei.strike_quality != null ? String(Math.round(ei.strike_quality)) : "—"} color={scoreColor(ei.strike_quality)} />
        <Metric label="Trade Survival" value={ei.trade_survival != null ? String(Math.round(ei.trade_survival)) : "—"} color={scoreColor(ei.trade_survival)} />
        <Metric label="Pullback Prob" value={ei.pullback_probability != null ? `${Math.round(ei.pullback_probability)}%` : "—"} />
      </div>

      {ei.reasons.length > 0 && (
        <div className="mt-2 rounded-lg bg-white/5 px-3 py-2 text-[12px] text-white/75">
          {ei.reasons.map((r, i) => (
            <div key={i}>• {r}</div>
          ))}
          <div className="mt-1 text-[11px] text-white/40">{ei.baseline_note}</div>
        </div>
      )}

      {/* Strike suggestion */}
      {ei.strike?.changed && ei.strike.recommended_symbol && (
        <div className="mt-2 text-[12px] text-[#3aa0ff]">
          Suggested strike: <span className="font-bold">{ei.strike.recommended_symbol}</span>
          <span className="text-white/50"> (vs {ei.strike.current_symbol}) — {ei.strike.reason}</span>
        </div>
      )}
      {ei.entry?.action === "WAIT" && ei.entry.better_entry != null && (
        <div className="mt-1 text-[12px] text-[#f5b301]">
          Estimated better entry ≈ ₹{ei.entry.better_entry.toFixed(1)}
          {ei.entry.wait_candles != null && ` within ${ei.entry.wait_candles} candle(s)`}
        </div>
      )}

      {/* Survival breakdown */}
      {ei.survival && ei.survival.components.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
          {ei.survival.components.map((c, i) => (
            <span key={i} title={c.detail} className="text-white/55">
              {c.name}:{" "}
              <span style={{ color: c.available ? scoreColor(c.score) : "#8b93a7" }}>
                {c.available ? Math.round(c.score) : "n/a"}
              </span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
