"use client";
import clsx from "clsx";
import { Recovery } from "@/lib/types";

/** Recovery Engine — shown when a position is under water. */
export default function RecoveryPanel({ recovery }: { recovery: Recovery }) {
  if (!recovery.in_position) {
    return (
      <div className="panel px-3 py-4 text-xs text-muted">Recovery Engine · idle (no open position)</div>
    );
  }
  const prob = Math.round((recovery.recovery_probability ?? 0) * 100);
  const hold = recovery.recommended_action === "HOLD";
  return (
    <div className="panel">
      <div className="panel-title">Recovery Engine</div>
      <div className="px-3 pb-3 grid grid-cols-2 gap-2 text-xs">
        <div>
          <div className="text-muted">Unrealized</div>
          <div className={clsx("text-lg font-bold", (recovery.unrealized_pct ?? 0) >= 0 ? "text-buy" : "text-exit")}>
            {recovery.unrealized_pct}%
          </div>
        </div>
        <div>
          <div className="text-muted">Recovery Prob</div>
          <div className="text-lg font-bold text-hold">{prob}%</div>
        </div>
        <div>
          <div className="text-muted">Est. Recovery</div>
          <div className="font-semibold">{recovery.expected_recovery_minutes}m</div>
        </div>
        <div>
          <div className="text-muted">Action</div>
          <div className={clsx("font-bold", hold ? "text-hold" : "text-exit")}>{recovery.recommended_action}</div>
        </div>
      </div>
    </div>
  );
}
