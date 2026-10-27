"use client";
import { useEffect, useRef, useState } from "react";
import { Candle, Snapshot } from "./types";
import { API_BASE, WS_BASE } from "./api";

// `instrument` addresses a specific per-instrument stream on the server, so two
// tabs on different instruments stay independent. Changing it tears down the
// old socket and connects to the new instrument's stream.
// A "candles_tail" frame carries only the forming bars: fold them into the
// history already on screen (replace the same bar, append a newer one).
function mergeTail(prev: Candle[] | undefined, tail: Candle[] | undefined): Candle[] {
  if (!tail?.length) return prev ?? [];
  if (!prev?.length) return tail;
  const out = prev.slice();
  for (const c of tail) {
    const last = out[out.length - 1];
    if (c.time > last.time) {
      out.push(c);
      if (out.length > prev.length) out.shift();
      continue;
    }
    for (let j = out.length - 1; j >= 0 && out[j].time >= c.time; j--) {
      if (out[j].time === c.time) { out[j] = c; break; }
    }
  }
  return out;
}

export function useLiveData(instrument?: string) {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;
    const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";

    // clear the previous instrument's data while the new one loads
    setSnap(null);

    // seed with a one-shot snapshot so the UI paints immediately
    fetch(`${API_BASE}/api/snapshot${qs}`)
      .then((r) => r.json())
      .then((d) => !closed && setSnap(d))
      .catch(() => {});

    // A socket can stay "open" while no frames arrive (sleeping laptop, proxy,
    // half-dead TCP). Without a watchdog the UI then keeps showing a frozen LTP
    // as LIVE, so a silent socket is closed and re-opened with backoff.
    let attempt = 0;
    let lastMsgAt = Date.now();
    const STALL_MS = 6000;
    const watchdog = setInterval(() => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN && Date.now() - lastMsgAt > STALL_MS) {
        ws.close();
      }
    }, 2000);

    const connect = () => {
      if (closed) return;
      const ws = new WebSocket(`${WS_BASE}/ws${qs}`);
      wsRef.current = ws;
      ws.onopen = () => {
        attempt = 0;
        lastMsgAt = Date.now();
        setConnected(true);
      };
      ws.onmessage = (e) => {
        lastMsgAt = Date.now();
        try {
          const d = JSON.parse(e.data);
          setSnap((prev) => {
            if (prev && typeof prev.time === "number" && typeof d.time === "number"
              && d.instrument === prev.instrument && d.time < prev.time) return prev;
            if (d.candles_tail) {
              const same = prev && d.instrument === prev.instrument;
              d.futures_candles = mergeTail(same ? prev.futures_candles : undefined, d.futures_candles);
              d.selected_option_candles = mergeTail(
                same ? prev.selected_option_candles : undefined, d.selected_option_candles);
            }
            return d;
          });
        } catch {}
      };
      ws.onclose = () => {
        if (wsRef.current !== ws) return;
        setConnected(false);
        if (closed) return;
        const base = Math.min(15000, 1000 * 2 ** attempt);
        attempt = Math.min(attempt + 1, 4);
        retry = setTimeout(connect, base / 2 + Math.random() * (base / 2));
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(retry);
      clearInterval(watchdog);
      const ws = wsRef.current;
      wsRef.current = null;
      ws?.close();
    };
  }, [instrument]);

  return { snap, connected };
}
