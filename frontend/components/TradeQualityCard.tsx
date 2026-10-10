"use client";
import { Decision } from "@/lib/types";

export default function TradeQualityCard({ decision }: { decision: Decision }) {
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Trade Quality</div>
      <div className="flex-1 grid place-items-center">
        <div className="text-6xl font-black neon">{decision.trade_quality}</div>
      </div>
    </div>
  );
}
