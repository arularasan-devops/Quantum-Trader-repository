"use client";
import { Decision, Position } from "@/lib/types";
import { themeFor } from "@/lib/theme";

function fmt(n: number | null | undefined, dp = 1): string {
  return n == null ? "—" : n.toFixed(dp);
}

function Field({
  label,
  value,
  tone,
  sub,
}: {
  label: string;
  value: string;
  tone?: string;
  sub?: string;
}) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-white/40">{label}</span>
      <span className={`text-2xl font-black tabular-nums ${tone ?? "text-white"}`}>{value}</span>
      {sub && <span className="text-[10px] text-white/35">{sub}</span>}
    </div>
  );
}

export default function TradePlanCard({
  decision,
  position,
}: {
  decision: Decision;
  position: Position;
}) {
  const t = themeFor(decision.signal);
  const d = decision;
  const inPos = !!position.option_symbol;
  const premium = inPos ? position.current_premium : d.current_premium;
  const entry = d.entry_range;
  const hold = d.expected_holding_minutes;
  const rec = d.recovery_probability;
  const recMin = d.expected_recovery_minutes;

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex justify-between">
        <span>Trade Plan</span>
        {d.option_type && (
          <span
            className="text-xs font-bold normal-case tracking-normal px-2 py-0.5 rounded-full"
            style={{ color: t.accent, border: `1px solid ${t.accent}66` }}
          >
            {d.option_type}
          </span>
        )}
      </div>

      {/* recommended option + premium */}
      <div className="flex items-end justify-between border-b border-white/8 pb-2">
        <div className="flex flex-col">
          <span className="text-[10px] uppercase tracking-wider text-white/40">
            Recommended Option
          </span>
          <span className="text-base font-bold text-white truncate max-w-[220px]">
            {d.recommended_option || "—"}
          </span>
        </div>
        <div className="text-right">
          <span className="text-[10px] uppercase tracking-wider text-white/40">Premium</span>
          <div className="text-2xl font-black tabular-nums" style={{ color: t.accent }}>
            {premium != null ? `₹${fmt(premium)}` : "—"}
          </div>
        </div>
      </div>

      {/* entry / stop */}
      <div className="grid grid-cols-2 gap-3 py-2 border-b border-white/8">
        <Field
          label="Entry Zone"
          value={entry ? `${fmt(entry[0])} – ${fmt(entry[1])}` : "—"}
        />
        <Field label="Stop Loss" value={fmt(d.stop_loss)} tone="text-[#ff6b6b]" />
      </div>

      {/* targets */}
      <div className="grid grid-cols-3 gap-3 py-2 border-b border-white/8">
        <Field label="Target 1" value={fmt(d.target1)} tone="text-[#19e57f]" />
        <Field label="Target 2" value={fmt(d.target2)} tone="text-[#19e57f]" />
        <Field label="Target 3" value={fmt(d.target3)} tone="text-[#19e57f]" />
      </div>

      {/* timing + recovery */}
      <div className="grid grid-cols-3 gap-3 py-2 mt-auto">
        <Field
          label="Hold Time"
          value={hold != null ? `~${hold}m` : "—"}
        />
        <Field
          label="Recovery Prob"
          value={rec != null ? `${Math.round(rec * 100)}%` : "—"}
          tone={rec != null && rec >= 0.5 ? "text-[#19e57f]" : "text-white/70"}
        />
        <Field
          label="Recovery Time"
          value={recMin != null ? `~${recMin}m` : "—"}
        />
      </div>
    </div>
  );
}
