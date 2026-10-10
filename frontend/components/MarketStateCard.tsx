"use client";
import { Decision } from "@/lib/types";
import { themeFor } from "@/lib/theme";

export default function MarketStateCard({ decision, marketStatus }: { decision: Decision; marketStatus: string }) {
  const t = themeFor(decision.signal);
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex justify-between">
        <span>Market State</span>
        <span className="text-white/40 font-normal normal-case">{marketStatus?.replace(/_/g, " ")}</span>
      </div>
      <div className="flex-1 grid place-items-center">
        <div className="text-center">
          <div className="text-[11px] text-white/45 mb-1">Slowly pulses</div>
          <div className="text-4xl font-black neon pulse">{t.state}</div>
        </div>
      </div>
    </div>
  );
}
