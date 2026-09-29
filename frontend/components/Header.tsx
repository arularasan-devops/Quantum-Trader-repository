"use client";
import clsx from "clsx";

const STATUS_TONE: Record<string, string> = {
  TRENDING: "text-buy border-buy",
  RANGING: "text-muted border-line",
  VOLATILE: "text-notrade border-notrade",
  LOW_VOLUME: "text-muted border-line",
  NEWS_MODE: "text-wait border-wait",
  REVERSAL_MODE: "text-hold border-hold",
  BREAKOUT_MODE: "text-buy border-buy",
};

export default function Header({
  price,
  change,
  changePct,
  marketStatus,
  connected,
  underlying,
}: {
  price: number;
  change: number;
  changePct: number;
  marketStatus: string;
  connected: boolean;
  underlying: string;
}) {
  const posChg = change >= 0;
  return (
    <div className="panel flex items-center justify-between px-4 py-2">
      <div className="flex items-center gap-4">
        <div className="text-lg font-black tracking-tight">
          QUANTUM<span className="text-hold">TRADER</span>
        </div>
        <div className="text-xs text-muted">{underlying}</div>
      </div>
      <div className="flex items-center gap-6">
        <div className="text-right">
          <span className="text-2xl font-bold tabular-nums">{price?.toFixed(1)}</span>
          <span className={clsx("ml-2 text-sm font-semibold", posChg ? "text-buy" : "text-exit")}>
            {posChg ? "+" : ""}
            {change?.toFixed(1)} ({changePct?.toFixed(2)}%)
          </span>
        </div>
        <span className={clsx("text-xs font-semibold uppercase tracking-wider border rounded-full px-3 py-1", STATUS_TONE[marketStatus] ?? "text-muted border-line")}>
          {marketStatus?.replace(/_/g, " ")}
        </span>
        <span className="flex items-center gap-1.5 text-xs text-muted">
          <span className={clsx("w-2 h-2 rounded-full", connected ? "bg-buy" : "bg-exit")} />
          {connected ? "LIVE" : "OFFLINE"}
        </span>
      </div>
    </div>
  );
}
