"use client";
import { Decision } from "@/lib/types";

const STAGES = [
  { n: 0, label: "No Setup" },
  { n: 1, label: "Momentum Building" },
  { n: 2, label: "Structure Confirmed" },
  { n: 3, label: "Early Buy" },
  { n: 4, label: "Confirmation Buy" },
];

const STAGE_COLOR: Record<number, string> = {
  0: "#8b93a7",
  1: "#f5b301",
  2: "#3aa0ff",
  3: "#19e57f",
  4: "#8b5cf6",
};

function scoreColor(v: number | null | undefined): string {
  if (v == null) return "#8b93a7";
  if (v >= 80) return "#19e57f";
  if (v >= 60) return "#f5b301";
  return "#ff6b6b";
}

export default function EarlyMomentumPanel({ decision }: { decision: Decision }) {
  const em = decision.early_momentum;
  const sd = decision.signal_delay;
  if (!em || !em.enabled) return null;

  const stageColor = STAGE_COLOR[em.stage_num] ?? "#fff";
  const stageLabel = STAGES.find((s) => s.n === em.stage_num)?.label ?? em.stage;

  return (
    <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-sm font-bold tracking-wide text-white/80">
          Early Momentum <span className="text-white/35 font-normal">· advisory · parallel to frozen engine</span>
        </div>
        <div className="rounded-md px-3 py-1 text-sm font-black"
          style={{ color: stageColor, border: `1px solid ${stageColor}66`, background: `${stageColor}12` }}>
          STAGE {em.stage_num} · {stageLabel.toUpperCase()} {em.side ?? ""}
        </div>
      </div>

      {/* stage ladder */}
      <div className="flex items-center gap-1 mb-3">
        {STAGES.map((s) => {
          const reached = em.stage_num >= s.n;
          const c = STAGE_COLOR[s.n];
          return (
            <div key={s.n} className="flex-1 text-center">
              <div className="h-1.5 rounded-full" style={{ background: reached ? c : "#ffffff12" }} />
              <div className="text-[9px] mt-1 leading-tight" style={{ color: em.stage_num === s.n ? c : "#8b93a7" }}>
                {s.n}. {s.label}
              </div>
            </div>
          );
        })}
      </div>

      {em.active && (
        <div className="mb-2 rounded-lg px-3 py-2 text-[12px]"
          style={{ background: "#19e57f10", border: "1px solid #19e57f33", color: "#c7f9df" }}>
          Early entry into a <b>forming</b> trend — <b>higher risk</b> than the confirmation BUY.
          Not the exact bottom; advisory only, engine unchanged.
        </div>
      )}

      <div className="grid grid-cols-4 divide-x divide-white/10 py-2">
        <div className="text-center">
          <div className="text-[10px] uppercase tracking-wider text-white/45">Adaptive Score</div>
          <div className="text-xl font-black mt-0.5" style={{ color: scoreColor(em.score) }}>{Math.round(em.score)}</div>
        </div>
        <div className="text-center">
          <div className="text-[10px] uppercase tracking-wider text-white/45">Regime</div>
          <div className="text-sm font-black mt-1.5">{em.regime}</div>
        </div>
        <div className="text-center">
          <div className="text-[10px] uppercase tracking-wider text-white/45">ATR from VWAP</div>
          <div className="text-xl font-black mt-0.5">{em.atr_from_vwap != null ? em.atr_from_vwap.toFixed(1) : "—"}</div>
        </div>
        <div className="text-center">
          <div className="text-[10px] uppercase tracking-wider text-white/45">Entry ≈</div>
          <div className="text-xl font-black mt-0.5">{em.entry_hint != null ? `₹${em.entry_hint.toFixed(1)}` : "—"}</div>
        </div>
      </div>

      {/* adaptive weighting note */}
      <div className="mt-1 text-[11px] text-white/40">Adaptive weighting: {em.weight_note}</div>

      {/* evidence checklist with adaptive weights */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
        {em.checks.map((c, i) => (
          <span key={i} title={`${c.detail} · weight ${c.weight}`} className="text-white/55">
            {c.name}{" "}
            <span className="text-white/35">({c.weight})</span>:{" "}
            <span style={{ color: !c.available ? "#8b93a7" : c.passed ? "#19e57f" : "#ff6b6b" }}>
              {!c.available ? "n/a" : c.passed ? "✓" : "✕"}
            </span>
          </span>
        ))}
      </div>

      {(em.reasons.length > 0 || em.note) && (
        <div className="mt-2 rounded-lg bg-white/5 px-3 py-2 text-[12px] text-white/75">
          {em.reasons.map((r, i) => <div key={i}>• {r}</div>)}
          {em.note && <div className="text-white/45">{em.note}</div>}
        </div>
      )}

      {sd && sd.available && (
        <div className="mt-3 rounded-lg bg-white/5 px-3 py-2" data-testid="signal-delay">
          <div className="text-[11px] uppercase tracking-wider text-white/45 mb-2">
            Signal Delay — confirmation vs early entry
          </div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-[12px]">
            <Metric label="Confirmation Entry"
              value={sd.confirmation_entry_premium != null ? `₹${sd.confirmation_entry_premium.toFixed(1)}` : "—"} />
            <Metric label="Early Entry"
              value={sd.early_entry_premium != null ? `₹${sd.early_entry_premium.toFixed(1)}` : "—"} />
            <Metric label="Difference"
              value={
                sd.premium_diff != null && sd.premium_diff_pct != null
                  ? `₹${sd.premium_diff.toFixed(1)} (${sd.premium_diff_pct.toFixed(1)}%)`
                  : "—"
              }
              color={sd.premium_diff != null ? (sd.premium_diff > 0 ? "#19e57f" : "#ff6b6b") : undefined} />
            <Metric label="Missed Move"
              value={sd.missed_move_pct != null ? `${sd.missed_move_pct}%` : "—"}
              color={sd.late ? "#ff6b6b" : "#f5b301"} />
            <Metric label="Remaining Move"
              value={sd.remaining_move_pct != null ? `${sd.remaining_move_pct}%` : "—"} />
            <Metric label="Candles Early→Confirm"
              value={sd.candles_between != null ? `${sd.candles_between}` : "—"} />
          </div>
          {sd.note && <div className="mt-2 text-[11px] text-white/45">{sd.note}</div>}
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className="text-sm font-bold mt-0.5" style={color ? { color } : undefined}>{value}</div>
    </div>
  );
}
