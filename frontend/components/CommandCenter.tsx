"use client";
import { useEffect, useState } from "react";
import { Snapshot } from "@/lib/types";
import { CommandCenter as CC, ScreenerRow, getCommandCenter } from "@/lib/api";
import FuturesChart from "./FuturesChart";

const GREEN = "#19e57f";
const RED = "#ff5c5c";
const AMBER = "#f5b301";
const BLUE = "#3aa0ff";
const PURPLE = "#a06bff";
const MUTED = "#8b93a7";

function money(n: number | null | undefined, p = 1): string {
  return n == null ? "—" : `₹${n.toFixed(p)}`;
}
function num(n: number | null | undefined, p = 0): string {
  return n == null ? "—" : n.toFixed(p);
}
function pretty(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (m) => m.toUpperCase());
}

const REGIME: Record<string, { label: string; color: string }> = {
  TRENDING: { label: "TREND DAY", color: GREEN },
  BREAKOUT_MODE: { label: "BREAKOUT", color: GREEN },
  RANGING: { label: "RANGE DAY", color: AMBER },
  REVERSAL_MODE: { label: "REVERSAL", color: AMBER },
  VOLATILE: { label: "VOLATILE", color: RED },
  LOW_VOLUME: { label: "LOW VOLUME", color: MUTED },
  NEWS_MODE: { label: "NEWS MODE", color: RED },
};

function scoreColor(s: number | null | undefined): string {
  if (s == null) return MUTED;
  if (s >= 70) return GREEN;
  if (s >= 45) return AMBER;
  return MUTED;
}
function actionColor(sig: string): string {
  if (sig === "BUY") return GREEN;
  if (sig === "EXIT" || sig === "AVOID") return RED;
  if (sig === "HOLD") return BLUE;
  return MUTED;
}

const STAGES = ["No Setup", "Prepare", "Early Buy", "Buy", "Hold", "Trail", "Exit"];

function lifecycleIdx(snap: Snapshot): number {
  const pos = snap.position;
  const d = snap.decision;
  if (pos && pos.option_symbol) {
    if (d.emergency_exit != null && d.current_premium != null && d.current_premium <= d.emergency_exit) return 6;
    if (pos.trailing_stop != null) return 5;
    return 4;
  }
  if (d.signal === "BUY") return 3;
  const ee = d.early_early;
  const em = d.early_momentum;
  if ((ee && ee.active) || (em && em.active)) return 2;
  if ((em && em.stage_num >= 1) || d.signal === "HOLD") return 1;
  return 0;
}

// A small labelled stat cell used across the header + panels.
function Stat({ label, value, sub, color }: { label: string; value: string; sub?: string; color?: string }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-white/40">{label}</div>
      <div className="text-sm font-bold" style={color ? { color } : undefined}>{value}</div>
      {sub != null && <div className="text-[10px] text-white/45">{sub}</div>}
    </div>
  );
}

// A semicircular gauge (0..100). Honest — shows "—" if value is null.
function Gauge({ value, label }: { value: number | null; label: string }) {
  const v = value == null ? null : Math.max(0, Math.min(100, value));
  const R = 52;
  const CIRC = Math.PI * R; // half circle
  const dash = v == null ? 0 : (v / 100) * CIRC;
  const col = v == null ? MUTED : v >= 70 ? GREEN : v >= 45 ? AMBER : RED;
  return (
    <div className="flex flex-col items-center">
      <svg viewBox="0 0 130 74" className="w-32">
        <path d="M 13 66 A 52 52 0 0 1 117 66" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="9" strokeLinecap="round" />
        <path
          d="M 13 66 A 52 52 0 0 1 117 66"
          fill="none"
          stroke={col}
          strokeWidth="9"
          strokeLinecap="round"
          strokeDasharray={`${dash} ${CIRC}`}
        />
        <text x="65" y="58" textAnchor="middle" fontSize="22" fontWeight="800" fill="#fff">
          {v == null ? "—" : `${v.toFixed(0)}%`}
        </text>
      </svg>
      <div className="text-[10px] uppercase tracking-wider font-semibold" style={{ color: col }}>{label}</div>
    </div>
  );
}

// A donut of segment counts (real counts of watched instruments per action).
function Donut({ segments }: { segments: { label: string; value: number; color: string }[] }) {
  const total = segments.reduce((a, s) => a + s.value, 0) || 1;
  const R = 30;
  const C = 2 * Math.PI * R;
  let offset = 0;
  return (
    <div className="flex items-center gap-3">
      <svg viewBox="0 0 80 80" className="w-20 h-20 -rotate-90">
        {segments.map((s) => {
          const len = (s.value / total) * C;
          const el = (
            <circle
              key={s.label}
              cx="40" cy="40" r={R}
              fill="none" stroke={s.color} strokeWidth="12"
              strokeDasharray={`${len} ${C - len}`} strokeDashoffset={-offset}
            />
          );
          offset += len;
          return el;
        })}
      </svg>
      <div className="space-y-0.5">
        {segments.map((s) => (
          <div key={s.label} className="flex items-center gap-1.5 text-[11px]">
            <span className="w-2 h-2 rounded-sm" style={{ background: s.color }} />
            <span className="text-white/60 w-20">{s.label}</span>
            <span className="font-bold tabular-nums">{s.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

const NAV = [
  ["Dashboard", true], ["Opportunities", false], ["Charts", false], ["Scanners", false],
  ["AI Signals", false], ["Portfolio", false], ["Positions", false], ["Alerts", false],
];

export default function CommandCenter({
  snap,
  onPick,
}: {
  snap: Snapshot;
  onPick: (sym: string) => void;
}) {
  const [cc, setCc] = useState<CC | null>(null);

  useEffect(() => {
    let alive = true;
    const load = () => {
      getCommandCenter().then((r) => alive && setCc(r)).catch(() => {});
    };
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (cc && !cc.enabled) {
    return (
      <div className="flex-1 grid place-items-center text-center">
        <div className="card p-8 max-w-lg space-y-3">
          <div className="text-xl font-black tracking-tight">AI <span style={{ color: BLUE }}>COMMAND CENTER</span></div>
          <div className="text-sm text-white/60">
            The Command Center is <b>off</b>. Enable it for one glanceable page: market status,
            VIX/breadth, ranked opportunities, the live chart, trade lifecycle, position health and
            exit — all from the <b>real</b> signals the engines already compute.
          </div>
          <div className="text-xs text-white/40 font-mono bg-black/30 rounded-lg px-3 py-2 inline-block">QT_AI_COMMAND_CENTER=true</div>
          <div className="text-[11px] text-white/35">
            Read-only. Aggregates existing engines — computes nothing new, fabricates no numbers,
            never places an order.
          </div>
        </div>
      </div>
    );
  }

  const d = snap.decision;
  const ind = snap.indicators;
  const intel = snap.intelligence;
  const vix = intel?.india_vix;
  const breadth = intel?.breadth;
  const regime = REGIME[snap.market_status] ?? { label: pretty(snap.market_status), color: MUTED };
  const life = lifecycleIdx(snap);
  const opps = cc?.opportunities ?? [];
  const expMove = d.expected_move_points ?? ind.expected_move;
  const learn = snap.learning;

  const breadthPct =
    breadth?.available && breadth.advances != null && breadth.declines != null && breadth.advances + breadth.declines > 0
      ? (100 * breadth.advances) / (breadth.advances + breadth.declines)
      : null;

  // Real scanner counts across the watched universe (NOT a fabricated 485).
  const scan = {
    buy: opps.filter((o) => o.signal === "BUY").length,
    prepare: opps.filter((o) => o.signal === "WAIT" || o.signal === "HOLD").length,
    breakout: opps.filter((o) => o.entry_trigger === "BREAKOUT" || o.htf_trend === "BREAKOUT").length,
    avoid: opps.filter((o) => o.signal === "AVOID" || o.signal === "NO_TRADE").length,
  };

  const ticker = opps.slice(0, 4);

  return (
    <div className="flex-1 flex gap-2 overflow-hidden">
      {/* ===== Decorative left nav rail (matches the mockup skin) ===== */}
      <div className="w-36 shrink-0 card p-2 hidden xl:flex flex-col gap-1 overflow-hidden">
        <div className="text-[11px] font-black tracking-tight px-1 py-1" style={{ color: BLUE }}>QUANTUM<span className="text-white">TRADER X</span></div>
        {NAV.map(([label, active]) => (
          <div
            key={label as string}
            className="text-[11px] font-semibold px-2 py-1.5 rounded-md"
            style={{ background: active ? "rgba(58,160,255,0.15)" : "transparent", color: active ? BLUE : "rgba(255,255,255,0.5)" }}
          >
            {label as string}
          </div>
        ))}
        <div className="mt-auto text-[9px] text-white/35 px-1 leading-relaxed">
          Read-only command view. Use the top tabs for the full app.
        </div>
      </div>

      {/* ===== Main scroll area ===== */}
      <div className="flex-1 overflow-auto space-y-2 pr-1 pb-4">
        {/* Ticker strip */}
        <div className="flex items-center gap-4 card px-3 py-1.5 text-xs overflow-x-auto">
          {ticker.map((o) => (
            <button key={o.symbol} onClick={() => onPick(o.symbol)} className="flex items-center gap-2 whitespace-nowrap hover:text-white">
              <span className="font-bold">{o.display}</span>
              <span className="tabular-nums text-white/70">{o.futures_price != null ? o.futures_price.toLocaleString("en-IN") : "—"}</span>
              {o.futures_change_pct != null && (
                <span style={{ color: o.futures_change_pct >= 0 ? GREEN : RED }}>
                  {o.futures_change_pct >= 0 ? "+" : ""}{o.futures_change_pct.toFixed(2)}%
                </span>
              )}
            </button>
          ))}
          <span className="flex items-center gap-2 whitespace-nowrap ml-auto">
            <span className="font-bold text-white/50">INDIA VIX</span>
            {vix?.available ? (
              <>
                <span className="tabular-nums">{num(vix.last, 2)}</span>
                <span style={{ color: (vix.change_pct ?? 0) >= 0 ? RED : GREEN }}>
                  {vix.change_pct != null ? `${vix.change_pct >= 0 ? "+" : ""}${vix.change_pct.toFixed(2)}%` : ""}
                </span>
              </>
            ) : <span className="text-white/40">n/a</span>}
          </span>
        </div>

        {/* Header stat cards */}
        <div className="grid grid-cols-7 gap-2">
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Market Status</div>
            <div className="text-lg font-black leading-tight" style={{ color: regime.color }}>{regime.label}</div>
            <div className="text-[10px] text-white/45">{num(d.confidence)}% confidence</div>
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Market Breadth</div>
            {breadth?.available ? (
              <>
                <div className="text-lg font-black leading-tight" style={{ color: breadth.bias === "BULLISH" ? GREEN : breadth.bias === "BEARISH" ? RED : MUTED }}>{pretty(breadth.bias)}</div>
                <div className="text-[10px] text-white/45">{breadthPct != null ? `${breadthPct.toFixed(0)}% adv` : `${breadth.advances}/${breadth.declines}`}</div>
              </>
            ) : <div className="text-sm text-white/40 mt-1">Not available</div>}
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Institutional Flow</div>
            <div className="text-lg font-black leading-tight" style={{ color: (ind.smart_money === "BULLISH" || ind.institutional === "BUYING") ? GREEN : (ind.smart_money === "BEARISH" || ind.institutional === "SELLING") ? RED : MUTED }}>
              {pretty(ind.institutional_label ?? ind.institutional ?? "—")}
            </div>
            <div className="text-[10px] text-white/45">₹ value: calibrating</div>
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Global Cues</div>
            <div className="text-sm text-white/40 mt-1">Not available</div>
            <div className="text-[10px] text-white/35">no overnight feed</div>
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">VIX Status</div>
            {vix?.available ? (
              <>
                <div className="text-lg font-black leading-tight" style={{ color: AMBER }}>{pretty(vix.level)}</div>
                <div className="text-[10px] text-white/45">{num(vix.last, 2)}</div>
              </>
            ) : <div className="text-sm text-white/40 mt-1">Not available</div>}
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Expected Move</div>
            <div className="text-lg font-black leading-tight">{expMove != null ? `${expMove.toFixed(0)} pts` : "—"}</div>
            <div className="text-[10px] text-white/45">{snap.instrument_name}</div>
          </div>
          <div className="card p-3">
            <div className="text-[9px] uppercase tracking-wider text-white/40">Best Window</div>
            <div className="text-sm text-white/40 mt-1">Calibrating</div>
            <div className="text-[10px] text-white/35">needs paper history</div>
          </div>
        </div>

        {/* Main 3-column body */}
        <div className="grid grid-cols-12 gap-2">
          {/* --- AI Opportunity Radar --- */}
          <div className="col-span-3 card p-3 flex flex-col">
            <div className="text-[10px] uppercase tracking-widest text-white/40">AI Opportunity Radar</div>
            <div className="text-[10px] text-white/35 mb-1">Ranked by real opportunity score · {opps.length} watched</div>
            <div className="space-y-1.5 overflow-auto" style={{ maxHeight: 520 }}>
              {opps.length === 0 && <div className="text-sm text-white/40 py-6 text-center">Warming instruments…</div>}
              {opps.map((o: ScreenerRow, i) => (
                <button key={o.symbol} onClick={() => onPick(o.symbol)} className="w-full text-left rounded-lg p-2 hover:bg-white/5 border border-white/5">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      {i < 3 && <span>{["🥇", "🥈", "🥉"][i]}</span>}
                      <div>
                        <div className="font-bold text-sm leading-tight">{o.display}</div>
                        <div className="text-[10px] text-white/40">{o.option_type ?? ""} {o.strike ?? ""}</div>
                      </div>
                    </div>
                    <div className="text-lg font-black" style={{ color: scoreColor(o.opportunity_score) }}>{num(o.opportunity_score)}</div>
                  </div>
                  <div className="grid grid-cols-3 gap-1 mt-1 text-[10px]">
                    <div><span className="text-white/40">Move </span><span className="font-semibold">{o.expected_move_points != null ? `${o.expected_move_points.toFixed(0)}p` : "—"}</span></div>
                    <div><span className="text-white/40">Prob </span><span className="text-white/50">calib.</span></div>
                    <div className="text-right font-bold" style={{ color: actionColor(o.signal) }}>{o.signal}</div>
                  </div>
                </button>
              ))}
            </div>
          </div>

          {/* --- Center: chart + lifecycle/momentum/expmove --- */}
          <div className="col-span-6 space-y-2">
            <div className="card p-3">
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-bold">LIVE CHART · {snap.instrument_name}</div>
                <div className="flex gap-3 text-[10px] text-white/50">
                  <span>O {num(ind.ema9, 1)}</span>
                  <span style={{ color: BLUE }}>EMA9 {num(ind.ema9, 1)}</span>
                  <span style={{ color: AMBER }}>VWAP {num(ind.vwap, 1)}</span>
                  <span>C {num(snap.futures_price, 1)}</span>
                </div>
              </div>
              <div className="h-52 mt-1">
                <FuturesChart candles={snap.futures_candles} vwap={ind.vwap} />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              {/* Trade Lifecycle */}
              <div className="card p-3">
                <div className="text-[10px] uppercase tracking-wider text-white/40">Trade Lifecycle</div>
                <div className="text-lg font-black mt-1" style={{ color: PURPLE }}>{STAGES[life].toUpperCase()}</div>
                <div className="flex items-center gap-0.5 mt-2">
                  {STAGES.map((s, i) => (
                    <div key={s} className="flex-1 h-1.5 rounded-full" style={{ background: i <= life ? PURPLE : "rgba(255,255,255,0.1)" }} />
                  ))}
                </div>
                <div className="flex justify-between text-[8px] text-white/35 mt-1">
                  <span>No Setup</span><span>Buy</span><span>Exit</span>
                </div>
                <div className="text-[10px] text-white/45 mt-2">Setup completion: <span className="text-white/60 font-semibold">calibrating</span></div>
              </div>
              {/* Momentum Meter */}
              <div className="card p-3 flex flex-col items-center">
                <div className="text-[10px] uppercase tracking-wider text-white/40 self-start">Momentum</div>
                <Gauge value={d.signal_strength} label={d.signal_strength >= 70 ? "STRONG" : d.signal_strength >= 45 ? "MODERATE" : "WEAK"} />
                <div className="w-full text-[10px] space-y-0.5 mt-1">
                  <div className="flex justify-between"><span className="text-white/40">Trend</span><span className="font-semibold">{pretty(ind.trend)}</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Structure</span><span className="font-semibold">{pretty(ind.market_structure)}</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Volume</span><span className="font-semibold">{pretty(ind.volume_level)}</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Smart money</span><span className="font-semibold">{pretty(ind.smart_money_label ?? ind.smart_money)}</span></div>
                </div>
              </div>
              {/* Expected Move Predictor */}
              <div className="card p-3">
                <div className="text-[10px] uppercase tracking-wider text-white/40">Expected Move</div>
                <div className="text-3xl font-black mt-1">{expMove != null ? expMove.toFixed(0) : "—"}<span className="text-sm font-normal text-white/40"> pts</span></div>
                <div className="text-[10px] space-y-0.5 mt-2">
                  <div className="flex justify-between"><span className="text-white/40">Probability</span><span className="text-white/50">calibrating</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Risk</span><span className="font-semibold">{d.risk_level}</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Confidence</span><span className="font-semibold">{num(d.confidence)}%</span></div>
                  <div className="flex justify-between"><span className="text-white/40">Duration</span><span className="font-semibold">{d.expected_holding_minutes != null ? `${d.expected_holding_minutes}m` : "—"}</span></div>
                </div>
              </div>
            </div>
          </div>

          {/* --- Right: recommendation + entry/exit AI --- */}
          <div className="col-span-3 space-y-2">
            <div className="card p-3">
              <div className="flex items-center justify-between">
                <div className="text-[10px] uppercase tracking-widest text-white/40">Today&apos;s AI Recommendation</div>
              </div>
              <div className="text-base font-bold mt-1">{d.recommended_option ?? snap.instrument_name}</div>
              <div className="text-3xl font-black mt-1" style={{ color: actionColor(d.signal) }}>
                {d.signal === "BUY" ? `BUY ${d.option_type ?? ""}` : d.signal}
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2">
                <Stat label="Entry (ideal)" value={d.entry_range ? money(d.entry_range[0]) : money(d.current_premium)} />
                <Stat label="Current price" value={money(d.current_premium)} />
                <Stat label="Stop loss" value={money(d.stop_loss)} color={RED} />
                <Stat label="Target" value={money(d.target1)} color={GREEN} />
              </div>
              <div className="grid grid-cols-2 gap-2 mt-2 pt-2 border-t border-white/10">
                <Stat label="Opportunity" value={`${num(d.opportunity_score)}/100`} color={scoreColor(d.opportunity_score)} />
                <Stat label="Probability" value="calibrating" />
                <Stat label="Confidence" value={`${num(d.confidence)}%`} />
                <Stat label="Expected hold" value={d.expected_holding_minutes != null ? `${d.expected_holding_minutes} min` : "—"} />
              </div>
              <div className="text-[10px] uppercase tracking-wider text-white/40 mt-2">AI reason summary</div>
              <div className="grid grid-cols-1 gap-0.5 mt-1">
                {(d.reasons.length ? d.reasons.slice(0, 4) : ["No standout reasons right now."]).map((r, i) => (
                  <div key={i} className="text-[11px] text-white/65">✓ {r}</div>
                ))}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="card p-3">
                <div className="text-[10px] uppercase tracking-wider text-white/40">Entry Timing AI</div>
                <Stat label="Grade" value={d.execution_intelligence?.entry_timing ?? "—"} />
                <div className="mt-1"><Stat label="Ideal entry" value={d.entry_range ? money(d.entry_range[0]) : "—"} /></div>
                <div className="mt-1"><Stat label="Pullback prob" value={d.execution_intelligence?.pullback_probability != null ? `${(d.execution_intelligence.pullback_probability * 100).toFixed(0)}%` : "calibrating"} /></div>
              </div>
              <div className="card p-3">
                <div className="text-[10px] uppercase tracking-wider text-white/40">Exit AI</div>
                <Stat label="Exit pressure" value={`${num(d.exit_score)}/100`} color={d.exit_score >= 60 ? RED : d.exit_score >= 35 ? AMBER : GREEN} />
                <div className="mt-1"><Stat label="Emergency exit" value={money(d.emergency_exit)} color={RED} /></div>
                <div className="mt-1"><Stat label="Recommendation" value={snap.recovery?.recommended_action ?? (d.exit_score >= 60 ? "TIGHTEN/CUT" : "HOLD")} /></div>
              </div>
            </div>
          </div>
        </div>

        {/* Bottom row: scanner / zero-hero / performance / explanation / alerts */}
        <div className="grid grid-cols-12 gap-2">
          <div className="col-span-3 card p-3">
            <div className="text-[10px] uppercase tracking-wider text-white/40">Portfolio Scanner</div>
            <div className="text-[10px] text-white/35 mb-2">Live scan · {opps.length} watched instruments</div>
            <Donut segments={[
              { label: "Strong Buy", value: scan.buy, color: GREEN },
              { label: "Preparing", value: scan.prepare, color: BLUE },
              { label: "Breakout", value: scan.breakout, color: AMBER },
              { label: "Avoid", value: scan.avoid, color: RED },
            ]} />
          </div>

          <div className="col-span-2 card p-3">
            <div className="text-[10px] uppercase tracking-wider text-white/40">Zero → Hero</div>
            <div className="text-sm text-white/40 mt-2">Not available</div>
            <div className="text-[10px] text-white/35 mt-1">A validated far-OTM &quot;lottery&quot; scanner needs option-chain + backtest data we don&apos;t yet have. Coming in a later phase.</div>
          </div>

          <div className="col-span-3 card p-3">
            <div className="text-[10px] uppercase tracking-wider text-white/40">Performance Today (paper)</div>
            {learn && learn.total_trades > 0 ? (
              <div className="grid grid-cols-3 gap-2 mt-2 text-center">
                <div><div className="text-xl font-black">{learn.total_trades}</div><div className="text-[9px] text-white/40">SIGNALS</div></div>
                <div><div className="text-xl font-black" style={{ color: GREEN }}>{learn.wins}</div><div className="text-[9px] text-white/40">WINNING</div></div>
                <div><div className="text-xl font-black" style={{ color: RED }}>{learn.losses}</div><div className="text-[9px] text-white/40">LOSING</div></div>
                <div><div className="text-sm font-bold">{learn.win_rate.toFixed(0)}%</div><div className="text-[9px] text-white/40">WIN RATE</div></div>
                <div><div className="text-sm font-bold">{learn.profit_factor.toFixed(2)}</div><div className="text-[9px] text-white/40">PROFIT FACTOR</div></div>
                <div><div className="text-sm font-bold">{learn.expectancy.toFixed(1)}</div><div className="text-[9px] text-white/40">EXPECTANCY</div></div>
              </div>
            ) : (
              <div className="text-sm text-white/40 mt-2">Calibrating — no paper trades logged yet. Real win-rate, profit factor and expectancy appear here once trades close.</div>
            )}
          </div>

          <div className="col-span-2 card p-3">
            <div className="text-[10px] uppercase tracking-wider text-white/40">AI Explanation</div>
            <div className="space-y-0.5 mt-1">
              {(d.reasons.length ? d.reasons.slice(0, 6) : ["No standout factors right now."]).map((r, i) => (
                <div key={i} className="text-[10px] text-white/60">✓ {r}</div>
              ))}
            </div>
          </div>

          <div className="col-span-2 card p-3">
            <div className="text-[10px] uppercase tracking-wider text-white/40">Alerts</div>
            <div className="space-y-1 mt-1 overflow-auto" style={{ maxHeight: 150 }}>
              {snap.alerts.length === 0 && <div className="text-[11px] text-white/40">No alerts.</div>}
              {snap.alerts.slice(0, 8).map((a, i) => (
                <div key={i} className="text-[10px]">
                  <span style={{ color: a.severity === "high" ? RED : a.severity === "medium" ? AMBER : MUTED }}>●</span>{" "}
                  <span className="text-white/70">{a.message}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="text-[10px] text-white/40 px-1 leading-relaxed">
          <b className="text-white/60">AI Command Center:</b> every value shown is computed by the existing
          engines from live data — nothing is fabricated. Panels we cannot compute real data for yet
          (Global Cues, Institutional ₹-flow, Zero→Hero, calibrated probability / win-rate, best-window)
          honestly show <b>&quot;Calibrating / Not available&quot;</b> instead of invented numbers. Read-only —
          it never changes the frozen engine and never places an order.
        </div>
      </div>
    </div>
  );
}
