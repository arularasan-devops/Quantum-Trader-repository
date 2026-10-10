"use client";
import { useEffect, useRef, useState } from "react";
import clsx from "clsx";
import { Position } from "@/lib/types";

function useHoldingClock(entryTime: number | null) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!entryTime) {
      setSecs(0);
      return;
    }
    const upd = () => setSecs(Math.max(0, Math.floor(Date.now() / 1000 - entryTime)));
    upd();
    const id = setInterval(upd, 1000);
    return () => clearInterval(id);
  }, [entryTime]);
  return secs;
}

function fmt(s: number) {
  const m = Math.floor(s / 60);
  const ss = String(s % 60).padStart(2, "0");
  return `${m}:${ss}`;
}

function useCountUp(target: number, ms = 600) {
  const [val, setVal] = useState(target);
  const from = useRef(target);
  useEffect(() => {
    const start = performance.now();
    const a = from.current;
    let raf = 0;
    const tick = (now: number) => {
      const p = Math.min(1, (now - start) / ms);
      setVal(a + (target - a) * (1 - Math.pow(1 - p, 3)));
      if (p < 1) raf = requestAnimationFrame(tick);
      else from.current = target;
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [target, ms]);
  return val;
}

export default function PositionCard({
  position,
  expectedHoldMinutes,
  averageNote,
}: {
  position: Position;
  expectedHoldMinutes: number | null;
  averageNote?: string | null;
}) {
  const has = !!position.option_symbol;
  const animated = useCountUp(position.net_pnl || 0);
  const up = (position.net_pnl || 0) >= 0;
  const held = useHoldingClock(has ? position.entry_time : null);
  const target = (expectedHoldMinutes ?? 0) * 60;
  const remain = has && target ? Math.max(0, target - held) : 0;

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Current Position</div>
      <div className="px-4 pb-4 pt-1 flex-1 flex flex-col">
        <div className="flex justify-between">
          <div>
            <div className="text-[11px] text-white/45">Bought @</div>
            <div className="text-2xl font-bold">{has ? position.entry_premium : "—"}</div>
          </div>
          <div className="text-right">
            <div className="text-[11px] text-white/45">Current Price</div>
            <div className="text-2xl font-bold">{has ? position.current_premium : "—"}</div>
          </div>
        </div>
        <div
          className="mt-3 rounded-xl px-3 py-3 text-center"
          style={{
            background: has ? (up ? "rgba(25,229,127,0.10)" : "rgba(255,77,77,0.10)") : "rgba(255,255,255,0.03)",
            border: `1px solid ${has ? (up ? "rgba(25,229,127,0.35)" : "rgba(255,77,77,0.35)") : "rgba(255,255,255,0.08)"}`,
          }}
        >
          <div className="text-[11px] text-white/45">Animated Profit</div>
          <div className={clsx("text-3xl font-black tabular-nums", has ? (up ? "text-[#19e57f]" : "text-[#ff4d4d]") : "text-white/40")}>
            {has ? `${up ? "↑" : "↓"} ₹${Math.abs(Math.round(animated)).toLocaleString("en-IN")}` : "FLAT"}
          </div>
          {has && <div className="text-[11px] text-white/45 mt-0.5">{position.pnl_pct.toFixed(1)}% · {position.quantity_lots} lot(s)</div>}
        </div>
        {has && (
          <div className="mt-2 grid grid-cols-2 gap-2 text-center">
            <div className="rounded-lg py-1.5" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-[10px] text-white/45">Holding Time</div>
              <div className="text-lg font-bold tabular-nums">{fmt(held)}</div>
            </div>
            <div className="rounded-lg py-1.5" style={{ background: "rgba(255,255,255,0.03)", border: "1px solid rgba(255,255,255,0.07)" }}>
              <div className="text-[10px] text-white/45">Hold ~ Remaining</div>
              <div className="text-lg font-bold tabular-nums" style={{ color: remain > 0 ? "#19e57f" : "#f4c025" }}>
                {target ? fmt(remain) : "—"}
              </div>
            </div>
          </div>
        )}
        {has && averageNote && (
          <div
            className="mt-2 rounded-lg px-2.5 py-1.5 text-[10.5px] leading-snug font-medium"
            style={{ color: "#7fc9ff", border: "1px solid rgba(58,160,255,0.4)", background: "rgba(58,160,255,0.08)" }}
            title="Optional averaging suggestion — not automated, not risk-free"
          >
            + Average: {averageNote}
          </div>
        )}
      </div>
    </div>
  );
}
