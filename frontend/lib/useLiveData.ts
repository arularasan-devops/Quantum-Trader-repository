"use client";
import { useEffect, useRef, useState } from "react";
import { Snapshot } from "./types";
import { API_BASE, WS_BASE } from "./api";

export function useLiveData() {
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let closed = false;
    let retry: ReturnType<typeof setTimeout>;

    // seed with a one-shot snapshot so the UI paints immediately
    fetch(`${API_BASE}/api/snapshot`)
      .then((r) => r.json())
      .then((d) => !closed && setSnap(d))
      .catch(() => {});

    const connect = () => {
      const ws = new WebSocket(`${WS_BASE}/ws`);
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
  }, []);

  return { snap, connected };
}
