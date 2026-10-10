"use client";
import { Intelligence } from "@/lib/types";

const GREEN = "#19e57f";
const RED = "#ff4d4d";
const AMBER = "#f5b301";
const MUTED = "#8b97a5";

function biasColor(bias: string | null | undefined): string {
  const b = (bias || "").toUpperCase();
  if (b.includes("BULL")) return GREEN;
  if (b.includes("BEAR")) return RED;
  return MUTED;
}

function gradeColor(grade: string): string {
  switch (grade) {
    case "A+":
    case "EXCELLENT":
      return GREEN;
    case "GOOD":
      return "#8fe36b";
    case "AVERAGE":
      return AMBER;
    default:
      return MUTED;
  }
}

function vixColor(level: string | null): string {
  switch (level) {
    case "MODERATE":
      return GREEN;
    case "LOW":
    case "ELEVATED":
      return AMBER;
    case "HIGH":
    case "EXTREME":
      return RED;
    default:
      return MUTED;
  }
}

function Cell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2 flex flex-col gap-0.5 min-w-0">
      <div className="text-[9px] font-bold uppercase tracking-wider text-white/35">{label}</div>
      {children}
    </div>
  );
}

function scoreColor(s: number): string {
  if (s >= 75) return GREEN;
  if (s >= 55) return AMBER;
  return RED;
}

export default function IntelligencePanel({ intel }: { intel: Intelligence | null | undefined }) {
  if (!intel) {
    return (
      <div className="text-[12px] text-white/40">Intelligence layer initialising…</div>
    );
  }
  const { india_vix: vix, breadth, max_pain: mp, pcr, trade_quality: tq } = intel;
  const comps = tq.components.filter((c) => c.available);

  return (
    <div className="flex flex-col gap-3">
      {/* Trade Quality headline */}
      <div className="rounded-xl border-2 p-3 flex items-center gap-4" style={{ borderColor: `${gradeColor(tq.grade)}55`, background: `${gradeColor(tq.grade)}0d` }}>
        <div className="text-center shrink-0">
          <div className="text-3xl font-black tabular-nums leading-none" style={{ color: gradeColor(tq.grade) }}>
            {Math.round(tq.score)}
          </div>
          <div className="text-[9px] uppercase tracking-wider text-white/40 mt-1">/ 100</div>
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-lg font-black leading-none" style={{ color: gradeColor(tq.grade) }}>
            {tq.grade === "A+" ? "A+ SETUP" : tq.grade}
          </div>
          <div className="text-[11px] text-white/55 mt-1 leading-snug">{tq.note}</div>
        </div>
      </div>

      {/* Quality components */}
      <div className="grid grid-cols-3 gap-1.5">
        {comps.map((c) => (
          <div key={c.name} className="rounded-md border border-white/6 bg-white/[0.02] px-2 py-1.5">
            <div className="flex items-center justify-between">
              <span className="text-[10px] text-white/55 truncate pr-1">{c.name}</span>
              <span className="text-[11px] font-bold tabular-nums" style={{ color: scoreColor(c.score) }}>
                {Math.round(c.score)}
              </span>
            </div>
            <div className="h-1 rounded-full bg-white/8 mt-1 overflow-hidden">
              <div className="h-full rounded-full" style={{ width: `${Math.max(3, Math.min(100, c.score))}%`, background: scoreColor(c.score) }} />
            </div>
            <div className="text-[9px] text-white/30 mt-0.5 truncate" title={c.detail}>{c.detail}</div>
          </div>
        ))}
      </div>

      {/* Market context row: VIX / Breadth / Max Pain / PCR */}
      <div className="grid grid-cols-4 gap-2">
        <Cell label="India VIX">
          {vix.available && vix.last !== null ? (
            <div className="flex items-baseline gap-1.5">
              <span className="text-base font-black tabular-nums" style={{ color: vixColor(vix.level) }}>{vix.last}</span>
              <span className="text-[10px] font-bold" style={{ color: (vix.change_pct ?? 0) >= 0 ? RED : GREEN }}>
                {(vix.change_pct ?? 0) >= 0 ? "▲" : "▼"} {Math.abs(vix.change_pct ?? 0).toFixed(1)}%
              </span>
              <span className="text-[9px] text-white/40 ml-auto">{vix.level}</span>
            </div>
          ) : (
            <span className="text-[10px] text-white/30">{vix.note ?? "Unavailable"}</span>
          )}
        </Cell>

        <Cell label="Market Breadth">
          {breadth.available ? (
            <div className="flex items-baseline gap-1.5">
              <span className="text-[12px] font-black tabular-nums" style={{ color: GREEN }}>{breadth.advances}</span>
              <span className="text-white/25 text-[11px]">/</span>
              <span className="text-[12px] font-black tabular-nums" style={{ color: RED }}>{breadth.declines}</span>
              <span className="text-[9px] font-bold ml-auto" style={{ color: biasColor(breadth.bias) }}>{breadth.bias}</span>
            </div>
          ) : (
            <span className="text-[10px] text-white/30">{breadth.note ?? "Unavailable"}</span>
          )}
        </Cell>

        <Cell label="Max Pain">
          {mp.available && mp.strike !== null ? (
            <div className="flex flex-col">
              <span className="text-base font-black tabular-nums text-white/90">{mp.strike}</span>
              {mp.distance_pct !== null && (
                <span className="text-[9px] text-white/40">{mp.distance_pct > 0 ? "+" : ""}{mp.distance_pct}% vs spot</span>
              )}
            </div>
          ) : (
            <span className="text-[10px] text-white/30">{mp.note ?? "Unavailable"}</span>
          )}
        </Cell>

        <Cell label="PCR (OI)">
          {pcr.available && pcr.pcr !== null ? (
            <div className="flex items-baseline gap-1.5">
              <span className="text-base font-black tabular-nums text-white/90">{pcr.pcr}</span>
              <span className="text-[9px] font-bold ml-auto" style={{ color: biasColor(pcr.bias) }}>{pcr.bias}</span>
            </div>
          ) : (
            <span className="text-[10px] text-white/30">{pcr.note ?? "Unavailable"}</span>
          )}
        </Cell>
      </div>

      {mp.bias && (
        <div className="text-[10px] text-white/40 leading-snug">Max pain: {mp.bias}</div>
      )}
      <div className="text-[9px] text-white/25 leading-snug">
        Advisory market context (Phase 3.1). Read-only — does not change the frozen engine&apos;s BUY/WAIT decision. VIX &amp; breadth are equity-market wide (NSE, best-effort).
      </div>
    </div>
  );
}
