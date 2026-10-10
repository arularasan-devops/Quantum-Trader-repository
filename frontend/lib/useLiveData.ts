"use client";
import { useEffect, useRef, useState } from "react";
import { Snapshot } from "./types";
import { API_BASE, WS_BASE } from "./api";

// `instrument` addresses a specific per-instrument stream on the server, so two
// tabs on different instruments stay independent. Changing it tears down the
// old socket and connects to the new instrument's stream.
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

    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/ws${qs}`);
      wsRef.current = ws;
      ws.onopen = () => setConnected(true);
      ws.onmessage = (e) => {
        try {
          setSnap(JSON.parse(e.data));
        } catch {}
      };
      ws.onclose = () => {
        setConnected(false);
        if (!closed) retry = setTimeout(connect, 1500);
      };
      ws.onerror = () => ws.close();
    };
    connect();

    return () => {
      closed = true;
      clearTimeout(retry);
      wsRef.current?.close();
    };
  }, [instrument]);

  return { snap, connected };
}
