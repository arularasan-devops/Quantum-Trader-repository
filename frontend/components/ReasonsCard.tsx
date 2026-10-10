"use client";
import { Decision } from "@/lib/types";
import { themeFor } from "@/lib/theme";

function Score({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="text-center">
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className={`text-2xl font-black ${tone ?? "text-white"}`}>{value}</div>
    </div>
  );
}

export default function ReasonsCard({ decision }: { decision: Decision }) {
  const t = themeFor(decision.signal);
  const isBuy = decision.signal === "BUY" || decision.signal === "HOLD";
  const reasons = decision.reasons ?? [];
  const riskTone =
    decision.risk_level === "HIGH"
      ? "text-[#ff6b6b]"
      : decision.risk_level === "LOW"
      ? "text-[#19e57f]"
      : "text-[#f4c025]";

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex justify-between">
        <span>Why This Call</span>
        <span className="text-white/40 font-normal normal-case">
          {isBuy ? "reasons to trade" : "why not now"}
        </span>
      </div>

      <div className="grid grid-cols-4 divide-x divide-white/10 border-b border-white/10 py-2 px-1">
        <Score label="Trade" value={`${Math.round(decision.trade_score)}`} tone="text-white" />
        <Score label="Opportunity" value={`${Math.round(decision.opportunity_score)}`} tone="text-white" />
        <Score label="Risk" value={`${Math.round(decision.risk_score)}`} tone={riskTone} />
        <Score label="Confidence" value={`${Math.round(decision.confidence)}%`} tone="" />
      </div>

      <div className="flex-1 overflow-auto px-4 py-2 space-y-1.5">
        {reasons.length === 0 && (
          <div className="text-xs text-white/40">No dominant factors yet.</div>
        )}
        {reasons.map((r, i) => (
          <div key={i} className="flex items-start gap-2 text-sm leading-snug">
            <span style={{ color: isBuy ? t.accent : "#8b97a5" }}>{isBuy ? "✔" : "•"}</span>
            <span className="text-white/85">{r}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
