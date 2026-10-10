"use client";
import { useEffect, useRef, useState } from "react";
import { Snapshot } from "@/lib/types";
import { useFlowSound } from "@/lib/useBuySound";
import { getFlowReport, setFlowSettings, getEngineSettings, FlowSummary } from "@/lib/api";

const GREEN = "#19e57f";
const RED = "#ff5c5c";
const AMBER = "#f5b301";
const MUTED = "#8b93a7";

// How long the red EXIT / amber SWITCH banner stays latched on screen after it
// first fires, so there's time to actually close instead of a one-candle flash.
const EXIT_LATCH_MS = 30_000;

function money(n: number | null | undefined, p = 1): string {
  return n == null ? "—" : `₹${n.toFixed(p)}`;
}

function pts(n: number | null | undefined): string {
  if (n == null) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(0)} pts`;
}

function rupees(n: number | null | undefined): string {
  if (n == null) return "—";
  const s = Math.abs(n) >= 1000 ? n.toLocaleString("en-IN") : n.toFixed(0);
  return `${n >= 0 ? "+" : "-"}₹${s.replace("-", "")}`;
}

function hhmm(sec: number | null | undefined): string {
  if (sec == null || sec <= 0) return "—";
  const m = Math.floor(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m}m`;
}

function clock(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

const STATE_STYLE: Record<string, { color: string; label: string; sub: string }> = {
  // While in a leg we always show BUY (no separate HOLD) — one clear "you're in".
  BUY: { color: GREEN, label: "BUY", sub: "in the trade — ride it" },
  HOLD: { color: GREEN, label: "BUY", sub: "in the trade — ride it" },
  EXIT: { color: RED, label: "EXIT NOW", sub: "rolled over — get out" },
  SWITCH: { color: AMBER, label: "SWITCH", sub: "flow flipped side" },
  WAIT: { color: MUTED, label: "WAIT", sub: "no clear flow yet" },
};

function pretty(s: string): string {
  return s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (m) => m.toUpperCase());
}

export default function FlowBoard({ snap }: { snap: Snapshot }) {
  const fl = snap.decision.flow;
  const [soundOn, setSoundOn] = useState(true);
  const [report, setReport] = useState<FlowSummary | null>(null);
  const [paperLots, setPaperLots] = useState(1);
  const [lotSizes, setLotSizes] = useState<Record<string, number>>({});
  // Editable paper-config inputs (kept as strings while typing).
  const [lotsEdit, setLotsEdit] = useState("");
  const [gbEdit, setGbEdit] = useState("");
  const [giveback, setGiveback] = useState<number | null>(null);
  const [savedAt, setSavedAt] = useState(0);

  useFlowSound(fl?.state ?? "WAIT", fl?.side ?? null, fl?.option_symbol ?? null, soundOn);

  // Load current flow paper-config once so the inputs show live values.
  useEffect(() => {
    getEngineSettings()
      .then((s) => {
        if (s.flow) {
          setPaperLots(s.flow.paper_lots || 1);
          setGiveback(s.flow.giveback_points);
        }
      })
      .catch(() => {});
  }, []);

  const saveFlow = (patch: { paper_lots?: number; giveback_points?: number }) =>
    setFlowSettings(patch)
      .then((r) => {
        if (r.flow) {
          setPaperLots(r.flow.paper_lots || 1);
          setGiveback(r.flow.giveback_points);
          setLotsEdit("");
          setGbEdit("");
          setSavedAt(Date.now());
        }
      })
      .catch(() => {});

  // Poll the paper Flow history/performance log (separate from the live tick).
  useEffect(() => {
    let alive = true;
    const load = () =>
      getFlowReport(150)
        .then((r) => {
          if (!alive) return;
          setReport(r.flow);
          setPaperLots(r.paper_lots || 1);
          setLotSizes(r.lot_sizes || {});
        })
        .catch(() => {});
    load();
    const id = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Open-leg paper P&L (₹) = live points × lot size × paper lots. Advisory/paper.
  const openLotSize = lotSizes[snap.instrument] ?? null;
  const openRupees =
    fl?.in_trade && fl?.points != null && openLotSize != null
      ? fl.points * openLotSize * paperLots
      : null;

  // Latch EXIT / SWITCH so the red/amber banner stays on screen for a while
  // (EXIT_LATCH_MS) — enough time to actually close — instead of flashing for a
  // single candle. Cleared by the timer or the manual dismiss below.
  const [latch, setLatch] = useState<{
    kind: "EXIT" | "SWITCH";
    reason: string;
    signals: string[];
    side: "CE" | "PE" | null;
    prevSide: "CE" | "PE" | null;
    optionSymbol: string | null;
  } | null>(null);
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    if (!fl) return;
    const isEx = fl.state === "EXIT" || fl.exit_now;
    const isSw = fl.state === "SWITCH";
    if (!isEx && !isSw) {
      lastKeyRef.current = "";
      return;
    }
    const key = `${fl.state}|${fl.exit_reason}|${fl.option_symbol ?? ""}`;
    if (key === lastKeyRef.current) return;
    lastKeyRef.current = key;
    // For a plain EXIT, fl.side is the tracked leg we're closing. For a SWITCH,
    // fl.side is the NEW side and fl.prev_side is the leg being closed.
    setLatch({
      kind: isSw ? "SWITCH" : "EXIT",
      reason: fl.exit_reason,
      signals: fl.exit_signals ?? [],
      side: fl.side,
      prevSide: fl.prev_side,
      optionSymbol: fl.option_symbol,
    });
  }, [fl]);

  useEffect(() => {
    if (!latch) return;
    const t = setTimeout(() => setLatch(null), EXIT_LATCH_MS);
    return () => clearTimeout(t);
  }, [latch]);

  if (!fl || !fl.enabled) {
    return (
      <div className="flex-1 grid place-items-center text-center">
        <div className="card p-8 max-w-lg space-y-3">
          <div className="text-xl font-black tracking-tight">
            CANDLE-<span style={{ color: GREEN }}>FLOW</span>
          </div>
          <div className="text-sm text-white/60">
            The Flow engine is <b>off</b>. Enable it to ride the 1-minute candle:
            BUY while it prints green, EXIT the moment it turns red, and auto-switch
            CE↔PE with the move.
          </div>
          <button
            onClick={() => setFlowSettings({ enabled: true }).catch(() => {})}
            className="px-4 py-2 rounded-lg font-bold text-sm"
            style={{ background: GREEN, color: "#04160c" }}
          >
            Enable Candle-Flow (paper)
          </button>
          <div className="text-[11px] text-white/40">
            or set <span className="font-mono bg-black/30 rounded px-1.5 py-0.5">QT_FLOW_ENABLED=true</span> in
            the backend <span className="font-mono">.env</span> to have it on at startup.
          </div>
          <div className="text-[11px] text-white/35">
            Independent of the frozen engine, Momentum, Early-Early and Quick Scalp.
            Advisory only — no automatic order.
          </div>
        </div>
      </div>
    );
  }

  // Display state: a latched EXIT/SWITCH wins (stays up ~30s); otherwise while in
  // a leg (BUY or HOLD) we always show a single green BUY — no separate HOLD.
  const isExit = latch?.kind === "EXIT";
  const isSwitch = latch?.kind === "SWITCH";
  const inPosition = !latch && (fl.state === "BUY" || fl.state === "HOLD");
  const justEntered = inPosition && fl.just_entered;

  const st = isExit
    ? STATE_STYLE.EXIT
    : isSwitch
      ? STATE_STYLE.SWITCH
      : inPosition
        ? STATE_STYLE.BUY
        : STATE_STYLE[fl.state] ?? STATE_STYLE.WAIT;

  const sideLbl = (isSwitch ? latch?.side : fl.side) ?? "—";
  const showSide = inPosition || isSwitch; // EXIT embeds its side in the label
  const mainLabel = isExit
    ? latch?.side
      ? `EXIT ${latch.side} NOW`
      : "EXIT NOW"
    : st.label;
  const exitReason = latch?.reason ?? fl.exit_reason;
  const exitSignals = latch?.signals ?? fl.exit_signals;
  const exitSymbol = latch?.optionSymbol ?? fl.option_symbol;
  // While an EXIT is latched, the live leg panel may already show a DIFFERENT
  // (new) side forming — only then relabel it so it's not mistaken for the leg
  // to close. On a plain exit-to-cash the side is unchanged → keep normal label.
  const legIsNewSide = Boolean(isExit && latch?.side && fl.side && fl.side !== latch.side);
  const bannerSub = isExit
    ? exitReason || "Flow rolled over — get out"
    : isSwitch
      ? exitReason || "Flow flipped — switch side"
      : inPosition
        ? justEntered
          ? `just entered — candle ${fl.bars_in_trade}`
          : "in the trade — ride it"
        : fl.headline || st.sub;
  const candleCol =
    fl.candle_color === "GREEN" ? GREEN : fl.candle_color === "RED" ? RED : MUTED;
  const projCol =
    fl.projection === "LIKELY_GREEN" ? GREEN : fl.projection === "LIKELY_RED" ? RED : MUTED;

  return (
    <div className="flex-1 min-h-0 overflow-auto flex flex-col gap-3">
      {/* header */}
      <div className="card px-4 py-3 flex items-center justify-between gap-3 shrink-0">
        <div className="flex items-baseline gap-3 flex-wrap min-w-0">
          <div className="text-lg font-black tracking-tight">
            CANDLE-<span style={{ color: GREEN }}>FLOW</span>
          </div>
          <span className="text-xs font-semibold text-white/60">{snap.instrument_name}</span>
          <span className="text-[10px] uppercase tracking-widest rounded-full px-2 py-0.5 font-bold"
            style={{ color: AMBER, border: `1px solid ${AMBER}55` }}>
            Higher Risk
          </span>
          <span className="text-[10px] uppercase tracking-widest rounded-full px-2 py-0.5 font-bold"
            style={{ color: MUTED, border: `1px solid ${MUTED}55` }}>
            Advisory · No Auto Order
          </span>
        </div>
        <button
          onClick={() => setSoundOn((s) => !s)}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5"
          title="Toggle the Flow buy/exit sounds"
        >
          {soundOn ? "🔊 Sound on" : "🔇 Muted"}
        </button>
      </div>

      {/* main state banner */}
      <div
        className="card px-5 py-4 flex items-center justify-between gap-4 shrink-0 flex-wrap"
        style={{
          border: `2px solid ${st.color}`,
          background: `${st.color}14`,
          boxShadow: isExit ? `0 0 40px ${RED}55` : undefined,
        }}
      >
        <div className="flex items-center gap-5 min-w-0">
          <div className="min-w-0">
            <div className="text-4xl font-black leading-none break-words" style={{ color: st.color }}>
              {isExit ? "🚨 " : justEntered ? "⚡ " : ""}{mainLabel}
              {showSide && <span className="text-2xl"> {sideLbl}</span>}
            </div>
            <div className="text-sm font-semibold mt-1" style={{ color: st.color }}>
              {bannerSub}
            </div>
          </div>
          {(isExit || isSwitch) && (
            <button
              onClick={() => setLatch(null)}
              className="text-[11px] font-semibold px-2 py-1 rounded-md border border-white/20 text-white/60 hover:bg-white/10"
            >
              dismiss
            </button>
          )}
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest text-white/40">1-min candle</div>
          <div className="text-2xl font-black" style={{ color: candleCol }}>
            {fl.candle_color}
          </div>
          <div className="text-[11px] text-white/50">
            pattern: <span style={{ color: fl.pattern_bias === "BULLISH" ? GREEN : fl.pattern_bias === "BEARISH" ? RED : MUTED }}>
              {pretty(fl.candle_pattern)}
            </span>
          </div>
        </div>
      </div>

      {/* live leg + read row */}
      <div className="grid grid-cols-12 gap-3 shrink-0">
        {/* option leg + P&L */}
        <div className="col-span-5 card p-4 space-y-2">
          <div className="text-[10px] uppercase tracking-widest text-white/40">
            {legIsNewSide ? "New flow forming (not the leg to close)" : "Option leg"}
          </div>
          <div className="text-lg font-bold">{fl.option_symbol ?? "—"}</div>
          <div className="grid grid-cols-2 gap-2 text-sm mt-1">
            <div>
              <div className="text-[10px] text-white/40 uppercase">Entry</div>
              <div className="font-semibold tabular-nums">{money(fl.in_trade ? fl.entry_premium : fl.entry_hint)}</div>
            </div>
            <div>
              <div className="text-[10px] text-white/40 uppercase">Live</div>
              <div className="font-semibold tabular-nums">{money(fl.live_premium)}</div>
            </div>
            <div>
              <div className="text-[10px] text-white/40 uppercase">Points</div>
              <div className="font-bold tabular-nums" style={{ color: (fl.points ?? 0) >= 0 ? GREEN : RED }}>
                {pts(fl.points)}
              </div>
            </div>
            {fl.in_trade && (
              <div className="col-span-2">
                <div className="text-[10px] text-white/40 uppercase">
                  Open paper P&amp;L ({paperLots}×{openLotSize ?? "?"} qty)
                </div>
                <div className="font-bold tabular-nums text-lg" style={{ color: (openRupees ?? 0) >= 0 ? GREEN : RED }}>
                  {rupees(openRupees)}
                </div>
              </div>
            )}
            <div>
              <div className="text-[10px] text-white/40 uppercase">Peak / Giveback</div>
              <div className="font-semibold tabular-nums">
                {money(fl.peak_premium)} <span className="text-white/40">/</span>{" "}
                <span style={{ color: (fl.giveback ?? 0) > 0 ? RED : MUTED }}>{pts(fl.giveback == null ? null : -fl.giveback)}</span>
              </div>
            </div>
          </div>
        </div>

        {/* flow read */}
        <div className="col-span-4 card p-4 space-y-2">
          <div className="text-[10px] uppercase tracking-widest text-white/40">Flow read</div>
          <Row k="Side" v={sideLbl} vc={sideLbl === "CE" ? GREEN : sideLbl === "PE" ? RED : MUTED} />
          <Row k="With-flow streak" v={`${fl.green_streak} candle(s)`} />
          <Row k="Next candle" v={pretty(fl.projection)} vc={projCol} />
          <div>
            <div className="flex justify-between text-[11px] mb-1">
              <span className="text-white/40 uppercase">Flow strength</span>
              <span className="font-semibold">{Math.round(fl.strength)}</span>
            </div>
            <div className="h-2 rounded-full bg-white/10 overflow-hidden">
              <div className="h-full rounded-full" style={{ width: `${fl.strength}%`, background: st.color }} />
            </div>
          </div>
        </div>

        {/* exit / reasons */}
        <div className="col-span-3 card p-4 space-y-2"
          style={isExit ? { border: `1px solid ${RED}`, background: `${RED}10` } : undefined}>
          <div className="text-[10px] uppercase tracking-widest" style={{ color: isExit ? RED : "rgba(255,255,255,0.4)" }}>
            {isExit ? "🚨 Exit alert" : "Why"}
          </div>
          {isExit ? (
            <>
              <div className="text-sm font-semibold" style={{ color: RED }}>
                Close your {latch?.side ?? ""} leg{exitSymbol ? ` (${exitSymbol})` : ""}
              </div>
              <div className="text-xs text-white/70 mt-1">{exitReason}</div>
              <ul className="text-xs text-white/70 space-y-1 mt-1">
                {exitSignals.map((s, i) => (
                  <li key={i}>• {s}</li>
                ))}
              </ul>
            </>
          ) : (
            <ul className="text-xs text-white/70 space-y-1">
              {(fl.reasons.length ? fl.reasons : [fl.note]).map((s, i) => (
                <li key={i}>• {s}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      {isSwitch && (
        <div className="card px-4 py-2 text-sm font-semibold" style={{ border: `1px solid ${AMBER}`, color: AMBER }}>
          ↔ Flow flipped {latch?.prevSide ?? "—"} → {latch?.side ?? "—"}: exit the old leg and take the new side.
        </div>
      )}

      {/* paper performance summary + history */}
      {report && (
        <div className="card p-4 space-y-3 shrink-0">
          <div className="flex items-center justify-between flex-wrap gap-2">
            <div className="flex items-baseline gap-2">
              <span className="text-sm font-black tracking-tight">FLOW PAPER HISTORY</span>
              <span className="text-[10px] uppercase tracking-widest rounded-full px-2 py-0.5 font-bold"
                style={{ color: MUTED, border: `1px solid ${MUTED}55` }}>
                Paper only · no real orders
              </span>
            </div>
            <div className="text-[11px] text-white/40">
              ₹ P&amp;L shown at {paperLots} paper lot(s) × lot size — analysis only, never sizes a real order.
            </div>
          </div>

          {/* dashboard paper controls */}
          <div className="flex items-end gap-3 flex-wrap rounded-lg bg-black/20 px-3 py-2">
            <label className="flex flex-col gap-1">
              <span className="text-[9px] uppercase tracking-widest text-white/40">Paper lots</span>
              <input
                type="number"
                min={1}
                max={100}
                value={lotsEdit}
                placeholder={`${paperLots}`}
                onChange={(e) => setLotsEdit(e.target.value)}
                onBlur={() => {
                  const v = parseInt(lotsEdit, 10);
                  if (!Number.isNaN(v) && v !== paperLots) saveFlow({ paper_lots: v });
                  else setLotsEdit("");
                }}
                className="w-24 bg-black/40 rounded-md px-2 py-1 text-sm tabular-nums border border-white/10 focus:border-white/30 outline-none"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-[9px] uppercase tracking-widest text-white/40">Trail give-back (prem. pts)</span>
              <input
                type="number"
                min={1}
                max={50}
                step={0.5}
                value={gbEdit}
                placeholder={giveback == null ? "—" : `${giveback}`}
                onChange={(e) => setGbEdit(e.target.value)}
                onBlur={() => {
                  const v = parseFloat(gbEdit);
                  if (!Number.isNaN(v) && v !== giveback) saveFlow({ giveback_points: v });
                  else setGbEdit("");
                }}
                className="w-32 bg-black/40 rounded-md px-2 py-1 text-sm tabular-nums border border-white/10 focus:border-white/30 outline-none"
              />
            </label>
            <div className="text-[10px] text-white/40 leading-tight max-w-xs">
              The trail exits when the premium gives back this many points from its running peak
              (your 3–5 point ratcheting trail). Changes apply live; paper only.
              {savedAt > 0 && Date.now() - savedAt < 3000 && (
                <span className="ml-1 font-semibold" style={{ color: GREEN }}>saved ✓</span>
              )}
            </div>
          </div>

          {report.total === 0 ? (
            <div className="text-xs text-white/45">
              No completed flow legs logged yet. Once the engine takes and closes paper legs they appear
              here with entry/exit, points, ₹ P&amp;L, hold time and exit reason.
            </div>
          ) : (
            <>
              {/* totals */}
              <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
                <Stat k="Legs" v={`${report.total}`} />
                <Stat k="Win rate" v={`${report.win_rate}%`} vc={report.win_rate >= 50 ? GREEN : RED} />
                <Stat k="Profit factor" v={report.profit_factor == null ? "—" : report.profit_factor.toFixed(2)}
                  vc={(report.profit_factor ?? 0) >= 1 ? GREEN : RED} />
                <Stat k="Net paper ₹" v={rupees(report.net_rupees)} vc={report.net_rupees >= 0 ? GREEN : RED} />
                <Stat k="Avg hold" v={hhmm((report.avg_hold_min ?? 0) * 60)} />
                <Stat k="Wins / Losses" v={`${report.wins}/${report.losses}`} />
              </div>

              {/* per-instrument */}
              {report.instruments.length > 0 && (
                <div className="overflow-x-auto">
                  <table className="w-full text-[11px]">
                    <thead>
                      <tr className="text-white/40 uppercase text-[9px] tracking-widest text-left">
                        <th className="py-1 pr-2">Instrument</th>
                        <th className="py-1 px-2 text-right">Legs</th>
                        <th className="py-1 px-2 text-right">Win%</th>
                        <th className="py-1 px-2 text-right">Switch%</th>
                        <th className="py-1 px-2 text-right">Avg pts</th>
                        <th className="py-1 px-2 text-right">Net ₹</th>
                        <th className="py-1 px-2 text-right">Avg hold</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.instruments.map((s) => (
                        <tr key={s.instrument} className="border-t border-white/5">
                          <td className="py-1 pr-2 font-semibold">{s.instrument}</td>
                          <td className="py-1 px-2 text-right tabular-nums">{s.signals}</td>
                          <td className="py-1 px-2 text-right tabular-nums" style={{ color: s.win_rate >= 50 ? GREEN : RED }}>{s.win_rate}%</td>
                          <td className="py-1 px-2 text-right tabular-nums text-white/60">{s.switch_rate}%</td>
                          <td className="py-1 px-2 text-right tabular-nums" style={{ color: s.avg_final_pts >= 0 ? GREEN : RED }}>{s.avg_final_pts}</td>
                          <td className="py-1 px-2 text-right tabular-nums" style={{ color: s.net_rupees >= 0 ? GREEN : RED }}>{rupees(s.net_rupees)}</td>
                          <td className="py-1 px-2 text-right tabular-nums text-white/60">{hhmm(s.avg_hold_min * 60)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* recent legs */}
              <div className="overflow-x-auto">
                <table className="w-full text-[11px]">
                  <thead>
                    <tr className="text-white/40 uppercase text-[9px] tracking-widest text-left">
                      <th className="py-1 pr-2">In</th>
                      <th className="py-1 px-2">Out</th>
                      <th className="py-1 px-2">Instrument</th>
                      <th className="py-1 px-2">Side</th>
                      <th className="py-1 px-2">Leg</th>
                      <th className="py-1 px-2 text-right">Entry</th>
                      <th className="py-1 px-2 text-right">Exit</th>
                      <th className="py-1 px-2 text-right">Peak</th>
                      <th className="py-1 px-2 text-right">Pts</th>
                      <th className="py-1 px-2 text-right">₹ P&amp;L</th>
                      <th className="py-1 px-2 text-right">Hold</th>
                      <th className="py-1 px-2">Exit reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.recent.map((r, i) => (
                      <tr key={`${r.ts_open}-${i}`} className="border-t border-white/5">
                        <td className="py-1 pr-2 tabular-nums text-white/60">{clock(r.ts_open)}</td>
                        <td className="py-1 px-2 tabular-nums text-white/60">{clock(r.ts_close)}</td>
                        <td className="py-1 px-2 font-semibold">{r.instrument}</td>
                        <td className="py-1 px-2 font-bold" style={{ color: r.side === "CE" ? GREEN : r.side === "PE" ? RED : MUTED }}>{r.side ?? "—"}</td>
                        <td className="py-1 px-2 text-white/50">{r.option_symbol ?? "—"}</td>
                        <td className="py-1 px-2 text-right tabular-nums">{money(r.entry_premium)}</td>
                        <td className="py-1 px-2 text-right tabular-nums">{money(r.final_premium)}</td>
                        <td className="py-1 px-2 text-right tabular-nums text-white/50">{money(r.peak_premium)}</td>
                        <td className="py-1 px-2 text-right tabular-nums" style={{ color: (r.final_points ?? 0) >= 0 ? GREEN : RED }}>{pts(r.final_points)}</td>
                        <td className="py-1 px-2 text-right tabular-nums font-semibold" style={{ color: (r.final_rupees ?? 0) >= 0 ? GREEN : RED }}>{rupees(r.final_rupees)}</td>
                        <td className="py-1 px-2 text-right tabular-nums text-white/60">{hhmm(r.duration_sec)}</td>
                        <td className="py-1 px-2 text-white/50">{r.close_reason}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      )}

      {/* how it works */}
      <div className="card px-4 py-3 text-[11px] text-white/45 leading-relaxed">
        <b className="text-white/60">How Candle-Flow works:</b> it rides the raw 1-minute candle —
        <span style={{ color: GREEN }}> BUY</span> stays up the whole time you&apos;re in the trade (no separate HOLD),
        then <span style={{ color: RED }}> EXIT NOW</span> latches on screen (~30s) the instant it rolls red or the
        premium gives back ~{"{"}QT_FLOW_GIVEBACK_POINTS{"}"} points so you have time to close, and
        <span style={{ color: AMBER }}> SWITCH</span> CE↔PE when the flow flips. Candlestick patterns
        project the next candle&apos;s likely colour. No VWAP/score/room gates. It never touches the frozen
        engine and never places an order.
      </div>
    </div>
  );
}

function Row({ k, v, vc }: { k: string; v: string; vc?: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-white/40 text-[11px] uppercase">{k}</span>
      <span className="font-semibold" style={vc ? { color: vc } : undefined}>{v}</span>
    </div>
  );
}

function Stat({ k, v, vc }: { k: string; v: string; vc?: string }) {
  return (
    <div className="rounded-lg bg-black/30 px-3 py-2">
      <div className="text-[9px] uppercase tracking-widest text-white/40">{k}</div>
      <div className="text-base font-black tabular-nums" style={vc ? { color: vc } : undefined}>{v}</div>
    </div>
  );
}
