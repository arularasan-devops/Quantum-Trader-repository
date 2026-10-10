"use client";
import { Indicators } from "@/lib/types";
import { themeFor } from "@/lib/theme";
import { Signal } from "@/lib/types";

export default function AiEngine({
  ind,
  signal,
  votes,
}: {
  ind: Indicators;
  signal: Signal;
  votes: number;
}) {
  const t = themeFor(signal);
  return (
    <div className="card h-full flex flex-col overflow-hidden">
      <div className="card-title">AI Engine</div>
      <div className="flex-1 grid place-items-center relative min-h-0">
        <div className="relative w-28 h-28 grid place-items-center">
          <div
            className="absolute inset-0 rounded-full spin-slow"
            style={{
              background: `conic-gradient(from 0deg, transparent, ${t.accent}, transparent 60%)`,
              maskImage: "radial-gradient(circle, transparent 62%, black 64%)",
              WebkitMaskImage: "radial-gradient(circle, transparent 62%, black 64%)",
            }}
          />
          <div className="absolute inset-3 rounded-full border border-white/10" />
          <div
            className="w-14 h-14 rounded-full grid place-items-center glow-pulse"
            style={{ background: t.accentDim, border: `1px solid ${t.accent}` }}
          >
            <span className="text-xl font-black" style={{ color: t.accent }}>AI</span>
          </div>
        </div>
      </div>
      <div className="px-4 pb-4 space-y-1 text-[11px]">
        <Row label="Signals analysed" value={`${votes}`} />
        <Row label="Trend" value={ind.trend ?? "—"} />
        <Row label="Structure" value={ind.market_structure ?? "—"} />
        <Row
          label="Market flow"
          value={ind.chart_pattern ? ind.chart_pattern.replace(/_/g, " ") : "—"}
          color={
            ind.chart_pattern_bias === "BULLISH"
              ? "#22e06b"
              : ind.chart_pattern_bias === "BEARISH"
              ? "#ff5470"
              : undefined
          }
        />
        <Row label="RSI" value={ind.rsi != null ? ind.rsi.toFixed(0) : "—"} />
        <Row label="ADX" value={ind.adx != null ? ind.adx.toFixed(0) : "—"} />
        <Row label="PCR" value={ind.pcr != null ? ind.pcr.toFixed(2) : "—"} />
      </div>
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="flex justify-between">
      <span className="text-white/45">{label}</span>
      <span className="font-semibold" style={{ color: color ?? "rgba(255,255,255,0.9)" }}>
        {value}
      </span>
    </div>
  );
}
