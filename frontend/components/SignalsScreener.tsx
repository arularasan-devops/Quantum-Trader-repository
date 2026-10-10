"use client";
import { useEffect, useMemo, useState } from "react";
import { getScreener, ScreenerRow } from "../lib/api";
import AlertBell from "@/components/AlertBell";
import { getAlertState, setAlertGate, testAlert } from "@/lib/alerts";

const GREEN = "#19e57f";
const AMBER = "#ffb020";
const RED = "#ff4d4d";
const MUTED = "#8b97a5";

function inr(v: number | null | undefined, d = 2): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

// Which entry trigger fired the signal — shown so the user knows whether a BUY
// came from a pullback, momentum, or candle pattern (esp. in Auto mode).
const TRIGGER_LABEL: Record<string, string> = {
  PULLBACK: "pullback",
  BREAKOUT_RETEST: "breakout retest",
  MOMENTUM: "momentum",
  PATTERN: "pattern",
};

/**
 * Live multi-instrument screener. Scans EVERY instrument and lists the ones
 * currently flashing a BUY at/above the chosen gate, with big bold numbers:
 * live premium, entry zone, stop, T1/T2/T3, confidence. Click a row to jump to
 * that instrument on the Decision tab. Read-only — never places a trade.
 */
export default function SignalsScreener({
  onPick,
}: {
  onPick: (symbol: string) => void;
}) {
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [gate, setGate] = useState<number>(80);
  const [loaded, setLoaded] = useState(false);

  // Adopt the saved alert gate, so what you see and what you hear stay the same
  // number across a reload.
  useEffect(() => {
    setGate(getAlertState().gate);
  }, []);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      // Skip polling while this browser tab is hidden — with several instrument
      // tabs open, background polling multiplied the load on the backend.
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const d = await getScreener();
        if (!alive) return;
        setRows(d.rows || []);
        setLoaded(true);
      } catch {
        /* keep last */
      }
    };
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const buys = useMemo(
    () =>
      rows
        .filter((r) => r.is_buy && r.confidence >= gate)
        .sort((a, b) => b.confidence - a.confidence),
    [rows, gate]
  );

  // The chime itself lives in the page-level alert engine (lib/alerts), not here:
  // a sound that only fires while this screen happens to be mounted, and whose
  // poll pauses when the browser tab is hidden, is silent exactly when it is
  // needed. This screen just hosts the controls.

  const total = rows.length;
  const dataLive = rows.some((r) => r.data_source && r.data_source.toLowerCase().includes("angel"));

  return (
    <div className="flex-1 min-h-0 overflow-auto rounded-2xl border border-white/10 bg-[#0b0f14] p-4">
      {/* header */}
      <div className="flex items-center justify-between flex-wrap gap-3 mb-4">
        <div>
          <div className="text-lg font-black tracking-wide text-white">⚡ LIVE SIGNALS — ALL INSTRUMENTS</div>
          <div className="text-[11px] text-white/45">
            Every instrument giving a BUY at/above your gate, in one screen. Click a row to open it in a new tab to trade.
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span
            className="text-[10px] font-bold uppercase px-2 py-1 rounded"
            style={{ background: "rgba(255,255,255,0.06)", color: dataLive ? GREEN : AMBER }}
          >
            {dataLive ? "● Angel One" : "○ Simulated"}
          </span>
          <AlertBell compact />
          <button
            onClick={testAlert}
            title="Play both alert tones now, to check your volume"
            className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 text-white/60 hover:bg-white/5"
          >
            Test
          </button>
          <div className="flex items-center gap-1">
            {/* One dial for both: the gate you watch is the gate you want to hear. */}
            <span className="text-[9px] uppercase tracking-wide text-white/35 mr-1">Gate</span>
            {[80, 85, 90, 95].map((g) => (
              <button
                key={g}
                onClick={() => {
                  setGate(g);
                  setAlertGate(g);
                }}
                className="px-2 py-1 rounded text-[11px] font-bold leading-none"
                style={
                  gate === g
                    ? { background: GREEN, color: "#07120c" }
                    : { background: "rgba(255,255,255,0.06)", color: MUTED }
                }
              >
                {g}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* summary strip */}
      <div className="flex items-center gap-6 mb-3 text-sm">
        <div>
          <span className="text-3xl font-black tabular-nums" style={{ color: buys.length ? GREEN : MUTED }}>
            {buys.length}
          </span>
          <span className="text-white/45 ml-2 text-xs uppercase tracking-wide">BUY signals ≥ {gate}%</span>
        </div>
        <div className="text-white/40 text-xs">
          Scanned <span className="font-bold text-white/70">{total}</span> instruments
          {!loaded && " · loading…"}
          {loaded && total < 20 && " · warming up, more appearing…"}
        </div>
      </div>

      {/* table */}
      {buys.length === 0 ? (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-8 text-center">
          <div className="text-2xl font-black text-white/70">No BUY signals ≥ {gate}% right now</div>
          <div className="text-sm text-white/40 mt-2">
            Standing aside is a valid call. Lower the gate to see closer setups, or wait for a clean BUY.
          </div>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-white/40 border-b border-white/10">
                <th className="text-left py-2 px-2">Instrument</th>
                <th className="text-left py-2 px-2">Side</th>
                <th className="text-right py-2 px-2">Live LTP</th>
                <th className="text-right py-2 px-2">Day H / L</th>
                <th className="text-right py-2 px-2">Entry Zone</th>
                <th className="text-right py-2 px-2">Stop</th>
                <th className="text-right py-2 px-2">T1</th>
                <th className="text-right py-2 px-2">T2</th>
                <th className="text-right py-2 px-2">T3</th>
                <th className="text-right py-2 px-2">Conf.</th>
                <th className="text-right py-2 px-2">Risk</th>
                <th className="text-right py-2 px-2">Hold</th>
                <th className="text-right py-2 px-2"></th>
              </tr>
            </thead>
            <tbody>
              {buys.map((r) => {
                const sideColor = r.option_type === "PE" ? RED : GREEN;
                const warn = r.chasing || r.no_live_data;
                return (
                  <tr
                    key={r.symbol}
                    onClick={() => onPick(r.symbol)}
                    className="border-b border-white/5 hover:bg-white/[0.04] cursor-pointer"
                  >
                    <td className="py-3 px-2">
                      <div className="text-lg font-black text-white leading-tight">{r.display}</div>
                      <div className="flex items-baseline gap-1.5">
                        <span className="text-base font-black tabular-nums text-white">{r.strike}</span>
                        <span className="text-sm font-black" style={{ color: sideColor }}>{r.option_type}</span>
                        {r.moneyness && <span className="text-[10px] font-bold text-white/40">· {r.moneyness}</span>}
                      </div>
                      {r.entry_trigger && TRIGGER_LABEL[r.entry_trigger] && (
                        <div className="text-[10px] font-bold uppercase tracking-wide text-white/45 mt-0.5">
                          via {TRIGGER_LABEL[r.entry_trigger]}
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-2">
                      <span
                        className="text-sm font-black px-2 py-1 rounded"
                        style={{ background: `${sideColor}1a`, color: sideColor }}
                      >
                        {r.option_type === "PE" ? "🐻 PE" : "🐂 CE"}
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span className="text-xl font-black tabular-nums" style={{ color: AMBER }}>
                        ₹{inr(r.current_premium)}
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right whitespace-nowrap">
                      <span className="text-base font-black tabular-nums" style={{ color: GREEN }}>{inr(r.premium_high)}</span>
                      <span className="text-white/30 font-bold mx-1">/</span>
                      <span className="text-base font-black tabular-nums" style={{ color: RED }}>{inr(r.premium_low)}</span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <div
                        className="inline-flex items-center gap-2 rounded-md px-2.5 py-1"
                        style={{ background: "rgba(25,229,127,0.10)", border: `1px solid rgba(25,229,127,0.30)` }}
                      >
                        <span className="text-lg font-black tabular-nums" style={{ color: GREEN }}>
                          {inr(r.entry_low)}
                        </span>
                        <span className="font-black text-sm" style={{ color: GREEN, opacity: 0.7 }}>→</span>
                        <span className="text-lg font-black tabular-nums" style={{ color: GREEN }}>
                          {inr(r.entry_high)}
                        </span>
                      </div>
                      <div className="text-[9px] font-bold tracking-wide text-white/35 mt-0.5">
                        BUY BETWEEN THESE
                      </div>
                      {warn && (
                        <div className="text-[9px] font-bold mt-0.5" style={{ color: AMBER }}>
                          {r.no_live_data ? "NO LIVE DATA" : "ABOVE ZONE — DON'T CHASE"}
                        </div>
                      )}
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span className="text-lg font-black tabular-nums" style={{ color: RED }}>
                        {inr(r.stop_loss)}
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span className="text-lg font-black tabular-nums" style={{ color: GREEN }}>
                        {inr(r.target1)}
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span className="text-lg font-black tabular-nums text-white/90">{inr(r.target2)}</span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span className="text-lg font-black tabular-nums text-white/90">{inr(r.target3)}</span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span
                        className="text-2xl font-black tabular-nums"
                        style={{ color: r.confidence >= 90 ? GREEN : AMBER }}
                      >
                        {inr(r.confidence, 0)}%
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span
                        className="text-xs font-bold"
                        style={{ color: r.risk_level === "LOW" ? GREEN : r.risk_level === "HIGH" ? RED : AMBER }}
                      >
                        {r.risk_level}
                      </span>
                    </td>
                    <td className="py-3 px-2 text-right text-xs font-bold text-white/60 tabular-nums">
                      {r.expected_holding_minutes != null ? `${r.expected_holding_minutes}m` : "—"}
                    </td>
                    <td className="py-3 px-2 text-right">
                      <span
                        className="text-[11px] font-black px-2.5 py-1 rounded whitespace-nowrap"
                        style={{ background: `${GREEN}22`, color: GREEN, border: `1px solid ${GREEN}55` }}
                        title="Opens this instrument in a new browser tab"
                      >
                        OPEN ↗
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="mt-4 text-[11px] text-white/35">
        Confidence is the engine&apos;s conviction, not a guaranteed win. Only enter inside the entry zone — never chase
        a premium already above it. Read-only screen; buying happens on the Decision tab.
      </div>
    </div>
  );
}
