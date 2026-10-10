"use client";
import clsx from "clsx";
import { RiskStatus } from "@/lib/types";

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] text-white/45">{label}</div>
      <div className={clsx("text-base font-bold", tone ?? "text-white")}>{value}</div>
    </div>
  );
}

export default function RiskCard({ risk }: { risk: RiskStatus | null }) {
  if (!risk) {
    return (
      <div className="card h-full grid place-items-center text-white/40 text-sm">Risk engine…</div>
    );
  }
  const dayTone = risk.day_pnl > 0 ? "text-[#19e57f]" : risk.day_pnl < 0 ? "text-[#ff6b6b]" : "text-white/70";
  const barColor = risk.loss_used_pct >= 80 ? "#ff4d4d" : risk.loss_used_pct >= 50 ? "#f4c025" : "#19e57f";

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center justify-between">
        <span className="flex items-center gap-2">
          <span
            className={clsx("w-2 h-2 rounded-full", risk.can_trade ? "bg-[#19e57f] pulse" : "bg-[#ff4d4d]")}
          />
          Risk & Sizing
        </span>
        <span
          className="text-[10px] font-bold uppercase tracking-wider rounded-full px-2 py-0.5"
          style={{
            color: risk.can_trade ? "#19e57f" : "#ff4d4d",
            border: `1px solid ${risk.can_trade ? "rgba(25,229,127,0.5)" : "rgba(255,77,77,0.6)"}`,
          }}
        >
          {risk.can_trade ? "TRADING OK" : "HALTED"}
        </span>
      </div>

      <div className="px-4 pb-3 pt-2 grid grid-cols-2 gap-y-3 gap-x-3">
        <Cell label="Day P&L" value={`${risk.day_pnl >= 0 ? "+" : ""}₹${risk.day_pnl.toFixed(0)}`} tone={dayTone} />
        <Cell label="Suggested Size" value={`${risk.suggested_lots} lot${risk.suggested_lots > 1 ? "s" : ""}`} tone="text-[var(--accent)]" />
        <Cell label="Trades Left" value={`${risk.trades_left}/${risk.trades_today + risk.trades_left}`} />
        <Cell
          label="Loss Streak"
          value={`${risk.consecutive_losses}`}
          tone={risk.consecutive_losses >= 2 ? "text-[#ff6b6b]" : undefined}
        />
      </div>

      <div className="px-4 pb-3 mt-auto">
        <div className="flex justify-between text-[10px] text-white/45 mb-1">
          <span>Daily loss budget</span>
          <span>{risk.loss_used_pct.toFixed(0)}% of ₹{risk.loss_limit.toFixed(0)}</span>
        </div>
        <div className="h-1.5 rounded-full bg-white/10 overflow-hidden">
          <div className="h-full rounded-full" style={{ width: `${risk.loss_used_pct}%`, background: barColor }} />
        </div>
        <div className="text-[10px] text-white/40 mt-2">
          {risk.block_reason ? <span className="text-[#ff6b6b]">{risk.block_reason}</span> : risk.squareoff_note}
        </div>
      </div>
    </div>
  );
}
