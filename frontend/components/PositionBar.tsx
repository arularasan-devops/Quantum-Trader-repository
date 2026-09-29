"use client";
import clsx from "clsx";
import { Position } from "@/lib/types";

function Cell({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="px-4 py-2 border-r border-line last:border-r-0">
      <div className="text-[10px] uppercase tracking-wider text-muted">{label}</div>
      <div className={clsx("text-sm font-semibold tabular-nums", accent)}>{value}</div>
    </div>
  );
}

export default function PositionBar({
  position,
  todayProfit,
  todayLoss,
}: {
  position: Position;
  todayProfit: number;
  todayLoss: number;
}) {
  const has = !!position.option_symbol;
  const pnlColor = position.net_pnl >= 0 ? "text-buy" : "text-exit";
  return (
    <div className="panel flex items-stretch overflow-x-auto">
      <Cell label="Position" value={has ? position.option_symbol : "FLAT"} />
      <Cell label="Buy Price" value={position.entry_premium ?? "—"} />
      <Cell label="Current" value={position.current_premium ?? "—"} />
      <Cell label="P&L" value={has ? position.pnl.toFixed(0) : "—"} accent={has ? pnlColor : ""} />
      <Cell label="P&L %" value={has ? `${position.pnl_pct.toFixed(1)}%` : "—"} accent={has ? pnlColor : ""} />
      <Cell label="Holding" value={has ? `${position.holding_minutes}m` : "—"} />
      <Cell label="Brokerage" value={has ? position.brokerage.toFixed(0) : "—"} />
      <Cell label="Net P&L" value={has ? position.net_pnl.toFixed(0) : "—"} accent={has ? pnlColor : ""} />
      <Cell label="Trailing SL" value={position.trailing_stop ?? "—"} />
      <Cell label="Today Profit" value={todayProfit.toFixed(0)} accent="text-buy" />
      <Cell label="Today Loss" value={todayLoss.toFixed(0)} accent="text-exit" />
    </div>
  );
}
