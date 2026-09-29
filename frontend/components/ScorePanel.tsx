"use client";
import clsx from "clsx";
import { Decision, ScoreBreakdown } from "@/lib/types";

function Bar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div>
      <div className="flex justify-between text-[10px] text-muted uppercase tracking-wider">
        <span>{label}</span>
        <span>{Math.round(value)}</span>
      </div>
      <div className="h-1.5 bg-line rounded-full overflow-hidden">
        <div className={clsx("h-full", color)} style={{ width: `${Math.min(100, value)}%` }} />
      </div>
    </div>
  );
}

export default function ScorePanel({ decision, breakdown }: { decision: Decision; breakdown: ScoreBreakdown[] }) {
  const sorted = [...breakdown].sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));
  return (
    <div className="panel">
      <div className="panel-title">Decision Scores & Weighting</div>
      <div className="px-3 pb-3 space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <Bar label="Buy" value={decision.buy_score} color="bg-buy" />
          <Bar label="Sell/Bearish" value={decision.sell_score} color="bg-exit" />
          <Bar label="Wait" value={decision.wait_score} color="bg-wait" />
          <Bar label="Exit" value={decision.exit_score} color="bg-hold" />
        </div>
        <div className="pt-1 space-y-0.5 max-h-48 overflow-y-auto">
          {sorted.map((b, i) => (
            <div key={i} className="flex items-center justify-between text-[11px]">
              <span className="text-ink">{b.name}</span>
              <span
                className={clsx(
                  "tabular-nums font-semibold",
                  b.signal === "BULLISH" ? "text-buy" : b.signal === "BEARISH" ? "text-exit" : "text-muted"
                )}
              >
                {b.contribution > 0 ? "+" : ""}
                {b.contribution}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
