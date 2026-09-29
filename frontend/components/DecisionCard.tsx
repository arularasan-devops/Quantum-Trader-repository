"use client";
import clsx from "clsx";
import { Decision } from "@/lib/types";
import { buyOption, sellPosition } from "@/lib/api";

const SIGNAL_STYLE: Record<string, { bg: string; label: string; ring: string }> = {
  BUY: { bg: "bg-buy", label: "BUY NOW", ring: "shadow-[0_0_60px_-10px_#16c784]" },
  WAIT: { bg: "bg-wait", label: "WAIT", ring: "shadow-[0_0_60px_-10px_#f4c025]" },
  HOLD: { bg: "bg-hold", label: "HOLD", ring: "shadow-[0_0_60px_-10px_#2f81f7]" },
  EXIT: { bg: "bg-exit", label: "EXIT", ring: "shadow-[0_0_60px_-10px_#f85149]" },
  NO_TRADE: { bg: "bg-notrade", label: "NO TRADE", ring: "shadow-[0_0_60px_-10px_#f0883e]" },
};

function Metric({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="bg-black/25 rounded-lg px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-white/60">{label}</div>
      <div className={clsx("text-lg font-semibold", accent)}>{value}</div>
    </div>
  );
}

export default function DecisionCard({ decision, inPosition }: { decision: Decision; inPosition: boolean }) {
  const s = SIGNAL_STYLE[decision.signal] ?? SIGNAL_STYLE.NO_TRADE;
  const conf = Math.round(decision.confidence);

  return (
    <div className={clsx("rounded-2xl p-5 text-black decision-pulse", s.bg, s.ring)}>
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.25em] text-black/60">Decision Engine</div>
          <div className="text-6xl font-black leading-none mt-1">{s.label}</div>
          <div className="mt-2 text-sm font-semibold text-black/70">
            {decision.recommended_option ?? "—"} · Quality {decision.trade_quality}
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-wider text-black/60">Confidence</div>
          <div className="text-5xl font-black tabular-nums">{conf}%</div>
          <div className="mt-1 w-28 h-2 bg-black/20 rounded-full overflow-hidden ml-auto">
            <div className="h-full bg-black/70" style={{ width: `${decision.signal_strength}%` }} />
          </div>
          <div className="text-[10px] text-black/60 mt-0.5">Strength {Math.round(decision.signal_strength)}%</div>
        </div>
      </div>

      <div className="grid grid-cols-4 gap-2 mt-4">
        <Metric label="Premium" value={decision.current_premium ?? "—"} />
        <Metric
          label="Entry Range"
          value={decision.entry_range ? `${decision.entry_range[0]}–${decision.entry_range[1]}` : "—"}
        />
        <Metric label="Stop Loss" value={decision.stop_loss ?? "—"} accent="text-red-900" />
        <Metric label="Hold Time" value={decision.expected_holding_minutes ? `${decision.expected_holding_minutes}m` : "—"} />
        <Metric label="Target 1" value={decision.target1 ?? "—"} accent="text-green-900" />
        <Metric label="Target 2" value={decision.target2 ?? "—"} accent="text-green-900" />
        <Metric label="Target 3" value={decision.target3 ?? "—"} accent="text-green-900" />
        <Metric
          label="Recovery Prob"
          value={decision.recovery_probability != null ? `${Math.round(decision.recovery_probability * 100)}%` : "—"}
        />
      </div>

      <div className="mt-4 bg-black/20 rounded-lg px-3 py-2">
        <div className="text-[10px] uppercase tracking-wider text-black/60 mb-1">Why</div>
        <ul className="text-[13px] font-medium space-y-0.5">
          {decision.reasons.slice(0, 5).map((r, i) => (
            <li key={i}>• {r}</li>
          ))}
        </ul>
      </div>

      <div className="mt-4 flex gap-2">
        <button
          disabled={!decision.recommended_option || inPosition}
          onClick={() => decision.recommended_option && buyOption(decision.recommended_option)}
          className="flex-1 bg-black/80 text-white font-bold py-2 rounded-lg disabled:opacity-40 hover:bg-black"
        >
          BUY {decision.recommended_option ?? ""}
        </button>
        <button
          disabled={!inPosition}
          onClick={() => sellPosition()}
          className="flex-1 bg-white/90 text-black font-bold py-2 rounded-lg disabled:opacity-40 hover:bg-white"
        >
          SELL / EXIT
        </button>
      </div>
    </div>
  );
}
