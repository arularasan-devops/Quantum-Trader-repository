"use client";
import { Decision, Position } from "@/lib/types";

export default function StopLossCard({ decision, position }: { decision: Decision; position: Position }) {
  const trailing = position.trailing_stop ?? decision.stop_loss;
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Stop Loss</div>
      <div className="px-4 pb-4 pt-1 flex-1 flex flex-col justify-center gap-3">
        <div className="flex justify-between">
          <div>
            <div className="text-[11px] text-white/45">Hard Stop</div>
            <div className="text-xl font-bold tabular-nums">{decision.stop_loss ?? "—"}</div>
          </div>
          <div className="text-right">
            <div className="text-[11px] text-white/45">Trailing Stop</div>
            <div className="text-xl font-bold tabular-nums">{trailing ?? "—"}</div>
          </div>
        </div>
        {(decision.underlying_stop != null || decision.atr_points != null) && (
          <div className="flex justify-between text-[11px] text-white/50">
            <span title="Invalidation level on the FUTURES price (ATR + structure based)">
              Futures stop:{" "}
              <span className="text-white/80 font-semibold tabular-nums">
                {decision.underlying_stop ?? "—"}
              </span>
            </span>
            <span title="Volatility (1× ATR) that sizes the dynamic stop & targets">
              ATR:{" "}
              <span className="text-white/80 font-semibold tabular-nums">
                {decision.atr_points ?? "—"}
              </span>
            </span>
          </div>
        )}
        <div
          className="rounded-xl py-2 text-center"
          style={{ background: "rgba(255,77,77,0.12)", border: "1px solid rgba(255,77,77,0.4)" }}
        >
          <div className="text-[#ff6b6b] font-bold">Emergency Exit</div>
          <div className="text-[11px] text-white/55">
            {decision.emergency_exit ? `If price closes below ${decision.emergency_exit}` : "—"}
          </div>
        </div>
      </div>
    </div>
  );
}
