"use client";
import { useEffect, useRef } from "react";

/**
 * Left panel — MCX Crude Oil futures via the TradingView Advanced Chart widget.
 * EMA 9 / 20 / 50, VWAP and volume are pre-loaded as studies. TradingView is
 * the *display* layer only; all decision logic runs on the backend feed.
 */
export default function FuturesChart({ symbol }: { symbol: string }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    ref.current.innerHTML = "";
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.innerHTML = JSON.stringify({
      autosize: true,
      symbol,
      interval: "1",
      timezone: "Asia/Kolkata",
      theme: "dark",
      style: "1",
      locale: "en",
      backgroundColor: "#0d1117",
      gridColor: "rgba(30,39,51,0.6)",
      hide_side_toolbar: false,
      allow_symbol_change: true,
      studies: [
        "MASimple@tv-basicstudies",
        "VWAP@tv-basicstudies",
        "Volume@tv-basicstudies",
      ],
      support_host: "https://www.tradingview.com",
    });
    ref.current.appendChild(script);
  }, [symbol]);

  return (
    <div className="panel h-full flex flex-col">
      <div className="panel-title flex justify-between">
        <span>Left · MCX Crude Futures</span>
        <span className="text-muted normal-case tracking-normal">{symbol}</span>
      </div>
      <div className="flex-1 px-2 pb-2 min-h-0">
        <div ref={ref} className="w-full h-full rounded-lg overflow-hidden" />
      </div>
    </div>
  );
}
