"use client";
import { useEffect, useRef } from "react";
import { createChart, IChartApi, ISeriesApi, IPriceLine, UTCTimestamp, LineStyle } from "lightweight-charts";
import { Candle, Decision } from "@/lib/types";
import { themeFor } from "@/lib/theme";

export default function MiniCharts({
  candles,
  decision,
  symbol,
}: {
  candles: Candle[];
  decision: Decision;
  symbol: string | null;
}) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Area"> | null>(null);
  const priceLines = useRef<IPriceLine[]>([]);
  const t = themeFor(decision.signal);

  useEffect(() => {
    if (!el.current) return;
    const c = createChart(el.current, {
      layout: { background: { color: "transparent" }, textColor: "#5b6672" },
      grid: { vertLines: { visible: false }, horzLines: { color: "rgba(255,255,255,0.04)" } },
      rightPriceScale: { borderVisible: false },
      timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
      autoSize: true,
      handleScale: false,
      handleScroll: false,
    });
    const s = c.addAreaSeries({ lineColor: t.accent, topColor: t.accentDim, bottomColor: "transparent", lineWidth: 2 });
    chart.current = c;
    series.current = s;
    return () => c.remove();
  }, [t.accent, t.accentDim]);

  useEffect(() => {
    const s = series.current;
    if (!s) return;
    s.applyOptions({ lineColor: t.accent, topColor: t.accentDim });
    s.setData(candles.map((c) => ({ time: c.time as UTCTimestamp, value: c.close })));
    priceLines.current.forEach((line) => s.removePriceLine(line));
    priceLines.current = [];
    const add = (p: number | null, color: string, title: string) => {
      if (p != null) {
        priceLines.current.push(
          s.createPriceLine({
            price: p,
            color,
            lineWidth: 1,
            lineStyle: LineStyle.Dashed,
            axisLabelVisible: true,
            title,
          })
        );
      }
    };
    add(decision.target1, "#19e57f", "T1");
    add(decision.stop_loss, "#ff4d4d", "SL");
  }, [candles, decision, t.accent, t.accentDim]);

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex justify-between">
        <span>Live Charts</span>
        <span className="text-white/40 font-normal">{symbol ?? "—"}</span>
      </div>
      <div className="flex-1 px-2 pb-2 min-h-0">
        <div ref={el} className="w-full h-full" />
      </div>
    </div>
  );
}
