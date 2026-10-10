"use client";
import { useEffect, useState } from "react";
import { Alert } from "@/lib/types";

const SEV: Record<string, string> = {
  critical: "#ff4d4d",
  warning: "#f4c025",
  info: "#3aa0ff",
};

/** Floating toast popups (BUY NOW / EXIT / TARGET / STOP / VOLATILITY / NEWS / CRASH). */
export default function Alerts({ alerts }: { alerts: Alert[] }) {
  const [toasts, setToasts] = useState<Alert[]>([]);
  const [lastTs, setLastTs] = useState(0);

  useEffect(() => {
    const fresh = alerts.filter((a) => a.time > lastTs);
    if (fresh.length) {
      setLastTs(Math.max(...alerts.map((a) => a.time)));
      setToasts((t) => {
        const seen = new Set(t.map((x) => x.message));
        const add = fresh.filter((f) => !seen.has(f.message));
        return [...add, ...t].slice(0, 3);
      });
      fresh.forEach((f) => setTimeout(() => setToasts((t) => t.filter((x) => x !== f)), 5500));
    }
  }, [alerts, lastTs]);

  return (
    <div className="fixed bottom-4 right-4 z-50 space-y-2 w-72 pointer-events-none">
      {toasts.map((a, i) => (
        <div
          key={`${a.time}-${i}`}
          className="card rise px-3 py-2 shadow-2xl"
          style={{ borderLeft: `3px solid ${SEV[a.severity] ?? SEV.info}` }}
        >
          <div className="text-[10px] uppercase tracking-wider" style={{ color: SEV[a.severity] ?? SEV.info }}>
            {a.kind.replace(/_/g, " ")}
          </div>
          <div className="text-xs text-white/85">{a.message}</div>
        </div>
      ))}
    </div>
  );
}
