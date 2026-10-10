"use client";
import clsx from "clsx";
import { Decision, Indicators } from "@/lib/types";

function trendLabel(trend: string | null, strength: number): { text: string; tone: string } {
  const strong = strength >= 55;
  if (trend === "UP") return { text: strong ? "Strong Bullish" : "Bullish", tone: "text-[#19e57f]" };
  if (trend === "DOWN") return { text: strong ? "Strong Bearish" : "Bearish", tone: "text-[#ff6b6b]" };
  return { text: "Neutral", tone: "text-white/70" };
}

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] text-white/45">{label}</div>
      <div className={clsx("text-base font-bold", tone ?? "text-white")}>{value}</div>
    </div>
  );
}

export default function AiStatusCard({ ind, decision }: { ind: Indicators; decision: Decision }) {
  const trend = trendLabel(ind.trend, decision.signal_strength);
  const mom = ind.momentum ?? 0;
  const momText = Math.abs(mom) >= 0.15 ? "High" : Math.abs(mom) >= 0.05 ? "Moderate" : "Low";
  const inst = ind.institutional;
  const instText = `${ind.institutional_label ?? "—"}`;
  const instTone = inst === "BULLISH" ? "text-[#19e57f]" : inst === "BEARISH" ? "text-[#ff6b6b]" : "text-white/70";
  const instLabelName = inst === "BEARISH" ? "Institutional Selling" : "Institutional Buying";

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-[#19e57f] pulse" /> Live AI Status
      </div>
      <div className="px-4 pb-4 pt-2 grid grid-cols-2 gap-y-4 gap-x-3">
        <Cell label="Current Trend" value={trend.text} tone={trend.tone} />
        <Cell label="Momentum" value={momText} />
        <Cell label="Volume" value={ind.volume_level ?? "—"} tone={ind.volume_level === "Very High" ? "text-[#19e57f]" : undefined} />
        <Cell label={instLabelName} value={instText} tone={instTone} />
      </div>
    </div>
  );
}
