"use client";
import clsx from "clsx";
import { Indicators } from "@/lib/types";

function Item({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className="flex justify-between text-xs py-1 border-b border-line/60 last:border-0">
      <span className="text-muted">{label}</span>
      <span className={clsx("font-semibold tabular-nums", tone)}>{value ?? "—"}</span>
    </div>
  );
}

const up = "text-buy";
const down = "text-exit";

export default function IndicatorGrid({ ind }: { ind: Indicators }) {
  const trendTone = ind.trend === "UP" ? up : ind.trend === "DOWN" ? down : "text-muted";
  return (
    <div className="panel">
      <div className="panel-title">Live Analysis · 30+ metrics</div>
      <div className="grid grid-cols-2 gap-x-4 px-3 pb-3">
        <Item label="Trend" value={ind.trend} tone={trendTone} />
        <Item label="Structure" value={ind.market_structure} />
        <Item label="EMA 9" value={ind.ema9} />
        <Item label="EMA 20" value={ind.ema20} />
        <Item label="EMA 50" value={ind.ema50} />
        <Item label="VWAP" value={ind.vwap} />
        <Item label="RSI" value={ind.rsi} tone={ind.rsi ? (ind.rsi > 65 ? down : ind.rsi < 35 ? up : "") : ""} />
        <Item label="MACD hist" value={ind.macd_hist} tone={ind.macd_hist ? (ind.macd_hist > 0 ? up : down) : ""} />
        <Item label="ATR" value={ind.atr} />
        <Item label="ADX" value={ind.adx} tone={ind.adx && ind.adx >= 25 ? up : ""} />
        <Item label="SuperTrend" value={ind.supertrend_dir === 1 ? "UP" : ind.supertrend_dir === -1 ? "DOWN" : "—"} tone={ind.supertrend_dir === 1 ? up : down} />
        <Item label="Momentum" value={ind.momentum ? `${ind.momentum}%` : "—"} />
        <Item label="BB Upper" value={ind.bb_upper} />
        <Item label="BB Lower" value={ind.bb_lower} />
        <Item label="Support" value={ind.support} tone={up} />
        <Item label="Resistance" value={ind.resistance} tone={down} />
        <Item label="Breakout" value={ind.breakout} />
        <Item label="Candle" value={ind.candle_pattern} />
        <Item label="Volume spike" value={ind.volume_spike ? "YES" : "no"} tone={ind.volume_spike ? up : ""} />
        <Item label="PCR" value={ind.pcr} />
        <Item label="Max OI Call" value={ind.max_oi_call_strike} />
        <Item label="Max OI Put" value={ind.max_oi_put_strike} />
        <Item label="Expected move" value={ind.expected_move} />
      </div>
    </div>
  );
}
