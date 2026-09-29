"use client";
import { useEffect, useState } from "react";
import clsx from "clsx";
import { Alert } from "@/lib/types";

const SEV: Record<string, string> = {
  critical: "border-exit text-exit",
  warning: "border-wait text-wait",
  info: "border-hold text-hold",
};

/** Toast-style popup alerts (BUY NOW / EXIT / TARGET / STOP / VOLATILITY / NEWS / CRASH). */
export default function Alerts({ alerts }: { alerts: Alert[] }) {
  const [toasts, setToasts] = useState<Alert[]>([]);
  const [lastTs, setLastTs] = useState(0);

  useEffect(() => {
    const fresh = alerts.filter((a) => a.time > lastTs);
    if (fresh.length) {
      setLastTs(Math.max(...alerts.map((a) => a.time)));
      setToasts((t) => [...fresh, ...t].slice(0, 4));
      fresh.forEach((f) => setTimeout(() => setToasts((t) => t.filter((x) => x !== f)), 6000));
    }
  }, [alerts, lastTs]);

  return (
    <>
      <div className="fixed top-4 right-4 z-50 space-y-2 w-80">
        {toasts.map((a, i) => (
          <div
            key={`${a.time}-${i}`}
            className={clsx("panel border-l-4 px-3 py-2 shadow-xl bg-panel2", SEV[a.severity] ?? SEV.info)}
          >
            <div className="text-[10px] uppercase tracking-wider">{a.kind.replace(/_/g, " ")}</div>
            <div className="text-sm text-ink">{a.message}</div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="panel-title">Alerts Feed</div>
        <div className="px-2 pb-2 space-y-1 max-h-40 overflow-y-auto">
          {alerts.slice(0, 20).map((a, i) => (
            <div key={i} className="flex items-start gap-2 text-xs">
              <span className={clsx("mt-1 w-1.5 h-1.5 rounded-full shrink-0", {
                "bg-exit": a.severity === "critical",
                "bg-wait": a.severity === "warning",
                "bg-hold": a.severity === "info",
              })} />
              <span className="text-muted">{a.kind.replace(/_/g, " ")}:</span>
              <span className="text-ink">{a.message}</span>
            </div>
          ))}
          {alerts.length === 0 && <div className="text-xs text-muted px-2 py-3">No alerts</div>}
        </div>
      </div>
    </>
  );
}
