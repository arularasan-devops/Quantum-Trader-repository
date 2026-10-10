"use client";
import { Decision } from "@/lib/types";

function Target({ n, value }: { n: number; value: number | null }) {
  return (
    <div className="flex items-center gap-2">
      <div className="text-[10px] text-white/45 w-12">Target {n}</div>
      <div
        className="flex-1 rounded-lg py-1.5 text-center text-lg font-bold tabular-nums"
        style={{ background: "rgba(25,229,127,0.10)", border: "1px solid rgba(25,229,127,0.35)", color: "#19e57f" }}
      >
        {value ?? "—"}
      </div>
    </div>
  );
}

export default function TargetsCard({ decision }: { decision: Decision }) {
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Targets</div>
      <div className="px-4 pb-4 pt-1 flex-1 flex flex-col justify-center gap-2">
        <Target n={1} value={decision.target1} />
        <Target n={2} value={decision.target2} />
        <Target n={3} value={decision.target3} />
      </div>
    </div>
  );
}
