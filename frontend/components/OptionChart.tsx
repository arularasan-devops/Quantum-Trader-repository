"use client";
import { useEffect, useRef } from "react";
import {
  createChart,
  IChartApi,
  ISeriesApi,
  CandlestickData,
  LineStyle,
  UTCTimestamp,
} from "lightweight-charts";
import { Candle, Decision } from "@/lib/types";

/**
 * Right panel — premium chart of the currently recommended / held option,
 * rendered from the backend feed with lightweight-charts. Overlays stop-loss
 * and the three targets as price lines.
 */
export default function OptionChart({
  candles,
  decision,
  symbol,
}: {
  candles: Candle[];
  decision: Decision;
  symbol: string | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);

  useEffect(() => {
    if (!container.current) return;
    const chart = createChart(container.current, {
      layout: { background: { color: "#0d1117" }, textColor: "#8b98a9" },
      grid: { vertLines: { color: "#131a24" }, horzLines: { color: "#131a24" } },
      rightPriceScale: { borderColor: "#1e2733" },
      timeScale: { borderColor: "#1e2733", timeVisible: true, secondsVisible: false },
      autoSize: true,
    });
    const series = chart.addCandlestickSeries({
      upColor: "#16c784",
      downColor: "#f85149",
      wickUpColor: "#16c784",
      wickDownColor: "#f85149",
      borderVisible: false,
    });
    chartRef.current = chart;
    seriesRef.current = series;
    return () => chart.remove();
  }, []);

  useEffect(() => {
    const series = seriesRef.current;
    if (!series) return;
    const data: CandlestickData[] = candles.map((c) => ({
      time: c.time as UTCTimestamp,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    series.setData(data);

    // refresh price lines
    // @ts-expect-error internal cache for cleanup
    (series._lines ?? []).forEach((l: any) => series.removePriceLine(l));
    const lines: any[] = [];
    const add = (price: number | null, color: string, title: string) => {
      if (price == null) return;
      lines.push(
        series.createPriceLine({ price, color, lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true, title })
      );
    };
    add(decision.stop_loss, "#f85149", "SL");
    add(decision.target1, "#16c784", "T1");
    add(decision.target2, "#16c784", "T2");
    add(decision.target3, "#16c784", "T3");
    // @ts-expect-error internal cache for cleanup
    series._lines = lines;
  }, [candles, decision]);

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>Right · Option Premium</span>
        <span className="text-muted normal-case tracking-normal">{symbol ?? "—"}</span>
      </div>
      <div className="flex-1 px-2 pb-2 min-h-0">
        <div ref={container} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
}
