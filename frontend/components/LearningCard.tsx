"use client";
import clsx from "clsx";
import { LearningStats } from "@/lib/types";

function Cell({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <div className="text-[11px] text-white/45">{label}</div>
      <div className={clsx("text-base font-bold", tone ?? "text-white")}>{value}</div>
    </div>
  );
}

export default function LearningCard({ stats }: { stats: LearningStats | null }) {
  if (!stats || stats.total_trades === 0) {
    return (
      <div className="card h-full flex flex-col">
        <div className="card-title flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-white/30" /> Learning Engine
        </div>
        <div className="flex-1 grid place-items-center px-4 text-center text-white/40 text-sm">
          No closed trades yet — win rate, profit factor and expectancy appear after your first exit.
        </div>
      </div>
    );
  }
  const pfTone = stats.profit_factor >= 1 ? "text-[#19e57f]" : "text-[#ff6b6b]";
  const wrTone = stats.win_rate >= 50 ? "text-[#19e57f]" : "text-[#ff6b6b]";
  const expTone = stats.expectancy >= 0 ? "text-[#19e57f]" : "text-[#ff6b6b]";

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-[var(--accent)] pulse" /> Learning Engine
      </div>
      <div className="px-4 pb-3 pt-2 grid grid-cols-3 gap-y-3 gap-x-3">
        <Cell label="Win Rate" value={`${stats.win_rate.toFixed(0)}%`} tone={wrTone} />
        <Cell label="Profit Factor" value={stats.profit_factor.toFixed(2)} tone={pfTone} />
        <Cell label="Expectancy" value={`₹${stats.expectancy.toFixed(0)}`} tone={expTone} />
        <Cell label="Trades" value={`${stats.total_trades}`} />
        <Cell label="Avg Win" value={`₹${stats.avg_win.toFixed(0)}`} tone="text-[#19e57f]" />
        <Cell label="Avg Loss" value={`₹${stats.avg_loss.toFixed(0)}`} tone="text-[#ff6b6b]" />
      </div>
      <div className="px-4 pb-3 mt-auto text-[10px] text-white/40">{stats.note}</div>
    </div>
  );
}
