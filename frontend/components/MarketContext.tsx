"use client";
import { useEffect, useState } from "react";
import { getEconomicEvents, getFundamentals } from "@/lib/api";
import { EconomicEvent, EventGuard, Fundamentals } from "@/lib/types";

const IMPACT_COLOR: Record<string, string> = {
  HIGH: "#ff6b6b",
  MEDIUM: "#f4c025",
  LOW: "#8b97a5",
};

function fmt(ts: number): string {
  return new Date(ts * 1000).toLocaleString("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function crore(n: number | null | undefined): string {
  if (n == null) return "—";
  const sign = n >= 0 ? "+" : "";
  return `${sign}₹${n.toFixed(0)} Cr`;
}

export default function MarketContext({ instrument }: { instrument: string }) {
  const [events, setEvents] = useState<EconomicEvent[]>([]);
  const [guard, setGuard] = useState<EventGuard | null>(null);
  const [fund, setFund] = useState<Fundamentals | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      getEconomicEvents()
        .then((d) => {
          if (!alive) return;
          setEvents(d.events || []);
          setGuard(d.guard || null);
        })
        .catch(() => {});
      getFundamentals(instrument)
        .then((d) => alive && setFund(d))
        .catch(() => {});
    };
    load();
    const t = setInterval(load, 60000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [instrument]);

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center justify-between">
        <span>Market Context · Events &amp; Fundamentals</span>
        <span className="text-[10px] font-normal normal-case text-white/40">
          official / free feeds
        </span>
      </div>

      <div className="px-3 pb-3 pt-1 flex-1 overflow-y-auto space-y-3">
        {guard?.active && (
          <div
            className="text-[11px] font-bold rounded px-2 py-1.5"
            style={{ background: "rgba(255,107,107,0.12)", color: "#ff6b6b" }}
          >
            ⚠ {guard.event} in ~{guard.minutes_to}m — high-impact event, prefer WAIT.
          </div>
        )}

        {/* Economic events */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
            Upcoming events
          </div>
          {events.length === 0 && (
            <div className="text-xs text-white/40">No scheduled events in range.</div>
          )}
          <div className="space-y-1">
            {events.slice(0, 6).map((e, i) => (
              <div key={i} className="flex items-center justify-between text-xs border-b border-white/5 pb-1">
                <div className="min-w-0">
                  <span
                    className="text-[9px] font-bold uppercase mr-1.5 px-1 py-0.5 rounded"
                    style={{ color: IMPACT_COLOR[e.impact], border: `1px solid ${IMPACT_COLOR[e.impact]}55` }}
                  >
                    {e.impact}
                  </span>
                  <span className="text-white/85">{e.name}</span>
                </div>
                <span className="text-white/45 shrink-0 ml-2 tabular-nums">{fmt(e.ts)}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Stock fundamentals */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
            {fund?.stock.symbol || instrument} fundamentals
          </div>
          {fund?.stock.available ? (
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Stat label="Delivery %" value={fund.stock.delivery_pct != null ? `${fund.stock.delivery_pct}%` : "—"} />
              <Stat label="Results" value={fund.stock.results_date || "—"} />
            </div>
          ) : (
            <div className="text-xs text-white/40">{fund?.stock.note || "Loading…"}</div>
          )}
        </div>

        {/* FII / DII flows */}
        <div>
          <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
            FII / DII cash {fund?.flows.date ? `· ${fund.flows.date}` : ""}
          </div>
          {fund?.flows.available ? (
            <div className="grid grid-cols-2 gap-2 text-xs">
              <Stat label="FII net" value={crore(fund.flows.fii_net)} tone={fund.flows.fii_net} />
              <Stat label="DII net" value={crore(fund.flows.dii_net)} tone={fund.flows.dii_net} />
            </div>
          ) : (
            <div className="text-xs text-white/40">{fund?.flows.note || "Loading…"}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: number | null }) {
  const color = tone == null ? "#e6edf3" : tone >= 0 ? "#19e57f" : "#ff6b6b";
  return (
    <div className="bg-white/5 rounded px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wide text-white/35">{label}</div>
      <div className="font-bold tabular-nums" style={{ color }}>
        {value}
      </div>
    </div>
  );
}
