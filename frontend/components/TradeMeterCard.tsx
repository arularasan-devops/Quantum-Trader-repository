"use client";
import { Decision } from "@/lib/types";
import { themeFor } from "@/lib/theme";

export default function TradeMeterCard({ decision }: { decision: Decision }) {
  const t = themeFor(decision.signal);
  const score = Math.round(decision.opportunity_score);
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const r = 52;
  const circ = Math.PI * r; // semicircle length
  const dash = circ * pct;

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Trade Meter</div>
      <div className="flex-1 grid place-items-center pb-2">
        <div className="relative">
          <svg width="150" height="90" viewBox="0 0 150 90">
            <path d="M 15 82 A 60 60 0 0 1 135 82" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="10" strokeLinecap="round" />
            <path
              d="M 15 82 A 60 60 0 0 1 135 82"
              fill="none"
              stroke={t.accent}
              strokeWidth="10"
              strokeLinecap="round"
              strokeDasharray={`${(dash / circ) * 188.5} 188.5`}
              style={{ filter: `drop-shadow(0 0 6px ${t.accent})`, transition: "stroke-dasharray 0.6s ease" }}
            />
          </svg>
          <div className="absolute inset-0 grid place-items-center pt-4">
            <span className="text-3xl font-black" style={{ color: t.accent }}>{score}</span>
          </div>
        </div>
        <div className="text-[11px] text-white/45 -mt-1">Opportunity Score</div>
      </div>
    </div>
  );
}
