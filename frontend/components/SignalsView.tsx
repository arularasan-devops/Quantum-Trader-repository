"use client";
import { useState } from "react";
import SignalsScreener from "@/components/SignalsScreener";
import MomentumScreener from "@/components/MomentumScreener";
import OpportunityBoard from "@/components/OpportunityBoard";
import AlertBell, { useAlerts } from "@/components/AlertBell";

/**
 * Signals tab shell with two segregated pages:
 *  - "Frozen" → the confirmation-BUY screener (existing behaviour).
 *  - "Momentum" → the Early Momentum EARLY-BUY screener (parallel engine).
 * Kept as two clearly separate sub-tabs so the two engines are never confused.
 */
export default function SignalsView({ onPick }: { onPick: (symbol: string) => void }) {
  const [sub, setSub] = useState<"frozen" | "momentum" | "opportunity">("frozen");
  const alerts = useAlerts();
  return (
    <div className="flex-1 min-h-0 flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <div className="flex rounded-lg border border-white/15 overflow-hidden text-xs font-bold">
          <button
            onClick={() => setSub("frozen")}
            className={sub === "frozen" ? "px-4 py-1.5 bg-white/10 text-white" : "px-4 py-1.5 text-white/55 hover:bg-white/5"}
          >
            Frozen · Confirmation BUY
          </button>
          <button
            onClick={() => setSub("momentum")}
            className={sub === "momentum" ? "px-4 py-1.5 bg-white/10 text-white" : "px-4 py-1.5 text-white/55 hover:bg-white/5"}
          >
            Momentum · Early BUY
          </button>
          <button
            onClick={() => setSub("opportunity")}
            className={sub === "opportunity" ? "px-4 py-1.5 bg-white/10 text-white" : "px-4 py-1.5 text-white/55 hover:bg-white/5"}
          >
            Opportunity · Where the move is
          </button>
        </div>
        {/* The alert controls belong to the whole Signals tab, not one sub-tab:
            the sound fires from the page-level engine regardless of which of the
            three is open. */}
        <div className="ml-auto flex items-center gap-2">
          {alerts.last && (
            <span className="text-[10px] text-white/40">
              last: {alerts.last.kind === "fill" ? "bought" : "signal"} · {alerts.last.label}
            </span>
          )}
          <AlertBell compact />
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[11px] text-white/35">
          {sub === "frozen"
            ? "Validated confirmation engine — safer, later entries."
            : sub === "momentum"
            ? "Advisory early engine — earlier, higher-risk entries."
            : "Every watched instrument ranked on net-of-cost premium movement, with the reason any BUY was withheld."}
        </span>
      </div>
      {sub === "frozen" ? (
        <SignalsScreener onPick={onPick} />
      ) : sub === "momentum" ? (
        <MomentumScreener onPick={onPick} />
      ) : (
        <OpportunityBoard onPick={onPick} />
      )}
    </div>
  );
}
