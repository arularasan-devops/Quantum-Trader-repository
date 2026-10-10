"use client";
import { Candle } from "@/lib/types";

/**
 * Dependency-free SVG candlestick chart for the 5-min futures series with a
 * VWAP overlay. Display-only — draws exactly the candles the engine already
 * has, no new data or indicators.
 */
export default function FuturesChart({ candles, vwap }: { candles: Candle[]; vwap: number | null }) {
  const W = 600;
  const H = 200;
  const padL = 4;
  const padR = 52;
  const padY = 8;

  if (!candles.length) {
    return <div className="w-full h-full grid place-items-center text-white/30 text-xs">No candles yet</div>;
  }

  const data = candles.slice(-90);
  const highs = data.map((c) => c.high);
  const lows = data.map((c) => c.low);
  let max = Math.max(...highs);
  let min = Math.min(...lows);
  if (vwap != null) {
    max = Math.max(max, vwap);
    min = Math.min(min, vwap);
  }
  const range = max - min || 1;
  const plotW = W - padL - padR;
  const plotH = H - padY * 2;
  const y = (p: number) => padY + (1 - (p - min) / range) * plotH;
  const step = plotW / data.length;
  const bodyW = Math.max(1.2, step * 0.6);

  const gridVals = [max, min + range * 0.75, min + range * 0.5, min + range * 0.25, min];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="w-full h-full">
      {gridVals.map((g, i) => (
        <g key={i}>
          <line x1={padL} x2={padL + plotW} y1={y(g)} y2={y(g)} stroke="rgba(255,255,255,0.05)" strokeWidth={1} />
          <text x={padL + plotW + 4} y={y(g) + 3} fill="#5b6672" fontSize={9}>
            {g.toLocaleString("en-IN", { maximumFractionDigits: 2 })}
          </text>
        </g>
      ))}
      {data.map((c, i) => {
        const cx = padL + i * step + step / 2;
        const up = c.close >= c.open;
        const col = up ? "#19e57f" : "#ff4d4d";
        const yO = y(c.open);
        const yC = y(c.close);
        const top = Math.min(yO, yC);
        const hgt = Math.max(1, Math.abs(yO - yC));
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={y(c.high)} y2={y(c.low)} stroke={col} strokeWidth={1} />
            <rect x={cx - bodyW / 2} y={top} width={bodyW} height={hgt} fill={col} />
          </g>
        );
      })}
      {vwap != null && (
        <g>
          <line x1={padL} x2={padL + plotW} y1={y(vwap)} y2={y(vwap)} stroke="#f5b301" strokeWidth={1.2} strokeDasharray="4 3" />
          <text x={padL + plotW + 4} y={y(vwap) + 3} fill="#f5b301" fontSize={9}>
            VWAP
          </text>
        </g>
      )}
    </svg>
  );
}
