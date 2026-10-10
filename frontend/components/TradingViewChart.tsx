"use client";
import { useEffect, useRef } from "react";

/* Minimal typing for the TradingView embed global (loaded from tv.js). */
interface TVWidgetConfig {
  symbol: string;
  interval: string;
  container_id: string;
  autosize: boolean;
  theme: string;
  style: string;
  timezone: string;
  locale: string;
  hide_side_toolbar: boolean;
  allow_symbol_change: boolean;
  studies: string[];
}
interface TVGlobal {
  widget: new (config: TVWidgetConfig) => unknown;
}
declare global {
  interface Window {
    TradingView?: TVGlobal;
  }
}

const TV_SRC = "https://s3.tradingview.com/tv.js";

/* Map our internal TradingView symbol to a symbol the public widget resolves.
   MCX continuous futures need the "1!" suffix; NSE/BSE symbols pass through. */
function toWidgetSymbol(raw: string | null | undefined): string {
  const s = (raw || "NSE:NIFTY").trim();
  if (s.startsWith("MCX:") && !s.endsWith("1!")) return `${s}1!`;
  return s;
}

let scriptPromise: Promise<void> | null = null;
function loadTvScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.TradingView) return Promise.resolve();
  if (scriptPromise) return scriptPromise;
  scriptPromise = new Promise<void>((resolve, reject) => {
    const el = document.createElement("script");
    el.src = TV_SRC;
    el.async = true;
    el.onload = () => resolve();
    el.onerror = () => reject(new Error("failed to load TradingView"));
    document.head.appendChild(el);
  });
  return scriptPromise;
}

export default function TradingViewChart({
  symbol,
  interval = "5",
}: {
  symbol: string | null | undefined;
  interval?: string;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const idRef = useRef(`tv_${Math.random().toString(36).slice(2)}`);

  useEffect(() => {
    let cancelled = false;
    const containerId = idRef.current;
    loadTvScript()
      .then(() => {
        if (cancelled || !ref.current || !window.TradingView) return;
        ref.current.innerHTML = "";
        const mount = document.createElement("div");
        mount.id = containerId;
        mount.style.height = "100%";
        mount.style.width = "100%";
        ref.current.appendChild(mount);
        new window.TradingView.widget({
          symbol: toWidgetSymbol(symbol),
          interval,
          container_id: containerId,
          autosize: true,
          theme: "dark",
          style: "1",
          timezone: "Asia/Kolkata",
          locale: "en",
          hide_side_toolbar: false,
          allow_symbol_change: true,
          studies: ["STD;EMA", "STD;RSI"],
        });
      })
      .catch(() => {
        if (!cancelled && ref.current) {
          ref.current.innerHTML =
            '<div style="display:flex;height:100%;align-items:center;justify-content:center;color:#8b97a5;font-size:12px;text-align:center;padding:12px">TradingView chart could not load (needs internet access to tradingview.com).</div>';
        }
      });
    return () => {
      cancelled = true;
    };
  }, [symbol, interval]);

  return <div ref={ref} className="h-full w-full" />;
}
