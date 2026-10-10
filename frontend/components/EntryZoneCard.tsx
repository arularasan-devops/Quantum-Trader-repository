"use client";
import { Decision } from "@/lib/types";

export default function EntryZoneCard({ decision }: { decision: Decision }) {
  const r = decision.entry_range;
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Entry Zone</div>
      <div className="px-4 pb-4 pt-1 flex-1 flex flex-col justify-center gap-2">
        <div className="text-center text-[#19e57f] text-lg font-bold">
          {r ? "Buy between" : "No active entry"}
        </div>
        <div
          className="rounded-xl py-3 text-center text-2xl font-black tabular-nums"
          style={{ background: "rgba(25,229,127,0.12)", border: "1px solid rgba(25,229,127,0.4)", color: "#19e57f" }}
        >
          {r ? `${r[0]} to ${r[1]}` : "—"}
        </div>
        <div
          className="rounded-xl py-2 text-center text-sm font-bold"
          style={{ background: "rgba(255,77,77,0.12)", border: "1px solid rgba(255,77,77,0.4)", color: "#ff6b6b" }}
        >
          {decision.do_not_buy_above ? `DO NOT BUY ABOVE ${decision.do_not_buy_above}` : "—"}
        </div>
      </div>
    </div>
  );
}
