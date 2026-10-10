"use client";
import { useEffect, useState } from "react";
import dynamic from "next/dynamic";
import { Snapshot } from "@/lib/types";
import { API_BASE, getSignalStats, SignalStats, setAutoTrade, getEngineSettings, setEntryMode, EntryMode } from "@/lib/api";
import { buildChecks } from "@/components/ConfidenceChecklist";
import ExpiryOpportunityPanel from "@/components/ExpiryOpportunityPanel";
import IntelligencePanel from "@/components/IntelligencePanel";
import ExecutionPanel from "@/components/ExecutionPanel";
import EarlyMomentumPanel from "@/components/EarlyMomentumPanel";
import { premiumHiLoToday, sessionOHLCToday } from "@/lib/premiumRange";

const FuturesChart = dynamic(() => import("@/components/FuturesChart"), { ssr: false });
const TradingViewChart = dynamic(() => import("@/components/TradingViewChart"), { ssr: false });

/* ------------------------------------------------------------------ helpers */

const GREEN = "#19e57f";
const RED = "#ff4d4d";
const AMBER = "#f5b301";
const MUTED = "#8b97a5";

function inr(n: number | null | undefined, dp = 0): string {
  if (n == null || !isFinite(n)) return "—";
  return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function num(n: number | null | undefined, dp = 2): string {
  if (n == null || !isFinite(n)) return "—";
  return n.toLocaleString("en-IN", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}
function compact(n: number | null | undefined): string {
  if (n == null || !isFinite(n)) return "—";
  if (n >= 1e9) return (n / 1e9).toFixed(2) + "B";
  if (n >= 1e7) return (n / 1e7).toFixed(2) + "Cr";
  if (n >= 1e5) return (n / 1e5).toFixed(2) + "L";
  if (n >= 1e3) return (n / 1e3).toFixed(2) + "K";
  return Math.round(n).toString();
}
function pretty(s: string | null | undefined): string {
  return s ? s.replace(/_/g, " ") : "—";
}

/* ---- panel; expandable ones pop out (chart & calculator only) ---- */
function Panel({
  title,
  right,
  children,
  className,
  expandHeight,
  expandable = false,
}: {
  title?: string;
  right?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  expandHeight?: string;
  expandable?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <div
        onClick={expandable ? () => setOpen(true) : undefined}
        className={`rounded-xl border border-white/8 bg-[#0e141b] p-3.5 flex flex-col min-w-0 relative transition ${expandable ? "cursor-zoom-in hover:border-white/25 hover:bg-[#111923]" : ""} ${className ?? ""}`}
      >
        {title && (
          <div className="flex items-center justify-between mb-2.5">
            <div className="text-[11px] font-bold uppercase tracking-wider text-white/45">{title}</div>
            <div className="flex items-center gap-2">
              {right}
              {expandable && <span className="text-white/25 text-xs leading-none" title="Click to expand">⤢</span>}
            </div>
          </div>
        )}
        <div className="flex-1 min-h-0 flex flex-col">{children}</div>
      </div>

      {expandable && open && (
        <div className="fixed inset-0 z-[80] grid place-items-center bg-black/75 p-6" onClick={() => setOpen(false)}>
          <div
            className="rounded-2xl border border-white/12 bg-[#0e141b] p-6 w-[min(1150px,94vw)] max-h-[90vh] overflow-auto flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-4">
              <div className="text-sm font-bold uppercase tracking-wider text-white/60">{title}</div>
              <button onClick={() => setOpen(false)} className="text-white/50 hover:text-white text-xl leading-none">✕</button>
            </div>
            <div className="flex-1 flex flex-col min-h-0 text-[15px]" style={expandHeight ? { height: expandHeight } : undefined}>
              {children}
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Chip({ label, color }: { label: string; color: string }) {
  return (
    <span className="text-[10px] font-bold px-2 py-0.5 rounded-md" style={{ color, background: `${color}1a`, border: `1px solid ${color}44` }}>
      {label}
    </span>
  );
}

function MiniBar({ value, color }: { value: number; color: string }) {
  return (
    <div className="h-1.5 w-20 rounded-full bg-white/8 overflow-hidden">
      <div className="h-full rounded-full" style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: color }} />
    </div>
  );
}

function CheckLine({ label, ok }: { label: string; ok: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className="grid place-items-center w-4 h-4 rounded-full text-[9px] font-bold shrink-0"
        style={{ background: ok ? `${GREEN}22` : `${RED}1a`, color: ok ? GREEN : RED }}>
        {ok ? "✓" : "✕"}
      </span>
      <span className="text-white/70">{label}</span>
    </div>
  );
}

/* ---- semicircular gauge meter ---- */
function Gauge({ value, color }: { value: number; color: string }) {
  const v = Math.max(0, Math.min(100, value));
  const r = 68;
  const cx = 90;
  const cy = 90;
  const ang = Math.PI - (v / 100) * Math.PI;
  const x = cx + r * Math.cos(ang);
  const y = cy - r * Math.sin(ang);
  const bgPath = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const valPath = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${x} ${y}`;
  return (
    <svg viewBox="0 0 180 108" className="w-full max-w-[240px]">
      <path d={bgPath} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={14} strokeLinecap="round" />
      <path d={valPath} fill="none" stroke={color} strokeWidth={14} strokeLinecap="round" />
      <text x={cx} y={82} textAnchor="middle" fontSize={40} fontWeight={800} fill={color}>{Math.round(v)}</text>
      <text x={cx} y={100} textAnchor="middle" fontSize={11} fill="#8b97a5">/ 100</text>
    </svg>
  );
}

function statusColor(s: string): string {
  return s === "TARGET" ? GREEN : s === "STOP" ? RED : s === "OPEN" ? AMBER : MUTED;
}

/* ---- daily signal scoreboard: how many signals, targets hit, stops hit ---- */
function SignalScoreboard({ stats }: { stats: SignalStats | null }) {
  const t = stats?.today;
  const tiles = [
    { l: "Signals Today", v: `${t?.signals ?? 0}`, c: "#e7ecf2" },
    { l: "Targets Hit", v: `${t?.target ?? 0}`, c: GREEN },
    { l: "Stop Loss Hit", v: `${t?.stop ?? 0}`, c: RED },
    { l: "Still Open", v: `${t?.open ?? 0}`, c: AMBER },
    { l: "Win Rate", v: t?.win_rate != null ? `${t.win_rate}%` : "—", c: (t?.win_rate ?? 0) >= 50 ? GREEN : AMBER },
  ];
  const days = stats?.days ?? [];
  const recent = stats?.recent ?? [];
  const buckets = stats?.buckets ?? [];
  const timing = stats?.timing;
  const tl = timing?.levels ?? [];
  const holdTiles = [
    ...tl.map((lv) => ({
      l: `Reach ${lv.level}`,
      v: lv.reach_pct != null ? `${lv.reach_pct}%` : "—",
      sub: lv.avg_mins != null ? `~${lv.avg_mins} min` : "—",
      c: GREEN,
    })),
    {
      l: "Stop First",
      v: timing?.stop_first_pct != null ? `${timing.stop_first_pct}%` : "—",
      sub: timing?.avg_mins_to_stop != null ? `~${timing.avg_mins_to_stop} min` : "—",
      c: RED,
    },
  ];
  // The export is only worth analysing if the tracker is still writing to it, so
  // the newest logged day is shown next to the download rather than left implicit.
  const newestDay = days.length ? days[0].date : null;
  const today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  const stale = newestDay != null && newestDay !== today;
  return (
    <Panel
      title="Signal Performance (auto-tracked — engine frozen, no orders)"
      right={
        <div className="flex items-center gap-2">
          <span
            className="text-[9px] tabular-nums"
            style={{ color: stale ? AMBER : "rgba(255,255,255,0.3)" }}
          >
            {newestDay == null
              ? "nothing logged yet"
              : stale
                ? `newest signal ${newestDay} — not today`
                : `logging · newest ${newestDay}`}
          </span>
          <a
            href={`${API_BASE}/api/signal-log`}
            className="text-[10px] px-2 py-1 rounded-lg border border-white/15 text-white/60 hover:text-white"
          >
            ⬇ Signals JSON
          </a>
        </div>
      }
    >
      {/* today's numbers */}
      <div className="grid grid-cols-5 gap-2 auto-rows-fr mb-3">
        {tiles.map((x) => (
          <div key={x.l} className="rounded-lg border border-white/8 bg-white/[0.02] px-2 py-3 text-center flex flex-col justify-center">
            <div className="text-3xl font-black tabular-nums leading-none" style={{ color: x.c }}>{x.v}</div>
            <div className="text-[9px] uppercase tracking-wide text-white/40 mt-1">{x.l}</div>
          </div>
        ))}
      </div>

      {/* target-reach % + how long to hold (avg minutes to each target / stop) */}
      <div className="mb-1 flex items-center justify-between">
        <div className="text-[9px] uppercase tracking-wide text-white/40">
          Which Target It Reaches & How Long To Hold
        </div>
        <div className="text-[9px] text-white/30">
          {timing?.finalized ? `${timing.finalized} finalized` : "fills in as signals close"}
        </div>
      </div>
      <div className="grid grid-cols-4 gap-2 auto-rows-fr mb-3">
        {holdTiles.map((x) => (
          <div key={x.l} className="rounded-lg border border-white/8 bg-white/[0.02] px-2 py-2 text-center flex flex-col justify-center">
            <div className="text-2xl font-black tabular-nums leading-none" style={{ color: x.c }}>{x.v}</div>
            <div className="text-[10px] font-bold text-white/55 mt-0.5">{x.sub}</div>
            <div className="text-[9px] uppercase tracking-wide text-white/40 mt-0.5">{x.l}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-12 gap-3">
        {/* confidence-band breakdown */}
        <div className="col-span-5 rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2">
          <div className="text-[9px] uppercase tracking-wide text-white/40 mb-1">Win / Stop by Confidence Band</div>
          <div className="grid grid-cols-6 text-[9px] uppercase text-white/30 pb-1 border-b border-white/8">
            <span className="col-span-2">Band</span><span className="text-right">Sig</span>
            <span className="text-right" style={{ color: GREEN }}>Win</span>
            <span className="text-right" style={{ color: RED }}>Stop</span>
            <span className="text-right">R:R</span>
          </div>
          {buckets.map((b) => (
            <div key={b.band} className="grid grid-cols-6 text-[11px] items-center border-b border-white/5 last:border-0 py-1">
              <span className="col-span-2 font-bold text-white/70">{b.band}%</span>
              <span className="text-right text-white/55 tabular-nums">{b.signals}</span>
              <span className="text-right font-bold tabular-nums" style={{ color: GREEN }}>{b.win_rate != null ? `${b.win_rate}%` : "—"}</span>
              <span className="text-right font-bold tabular-nums" style={{ color: RED }}>{b.stop_rate != null ? `${b.stop_rate}%` : "—"}</span>
              <span className="text-right text-white/45 tabular-nums">{b.target}/{b.stop}</span>
            </div>
          ))}
          {buckets.every((b) => b.signals === 0) && (
            <div className="text-[10px] text-white/35 py-2 text-center">Fills in as signals resolve.</div>
          )}
        </div>

        {/* last 7 days */}
        <div className="col-span-3 rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2">
          <div className="text-[9px] uppercase tracking-wide text-white/40 mb-1">Last 7 Days</div>
          {days.length === 0 ? (
            <div className="text-[11px] text-white/35 py-3 text-center">No signals logged yet.</div>
          ) : (
            <div className="flex flex-col gap-0.5">
              {days.map((dd) => (
                <div key={dd.date} className="flex items-center justify-between text-[11px] border-b border-white/5 last:border-0 py-0.5">
                  <span className="text-white/55">{dd.date.slice(5)}</span>
                  <span className="text-white/40">{dd.signals} sig</span>
                  <span style={{ color: GREEN }}>{dd.target}T</span>
                  <span style={{ color: RED }}>{dd.stop}S</span>
                  <span className="font-bold" style={{ color: (dd.win_rate ?? 0) >= 50 ? GREEN : AMBER }}>{dd.win_rate != null ? `${dd.win_rate}%` : "—"}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* recent signals */}
        <div className="col-span-4 rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2">
          <div className="text-[9px] uppercase tracking-wide text-white/40 mb-1">Recent Signals</div>
          {recent.length === 0 ? (
            <div className="text-[11px] text-white/35 py-3 text-center">Signals appear here as the engine gives them.</div>
          ) : (
            <div className="flex flex-col gap-0.5 max-h-28 overflow-auto">
              {recent.map((r, i) => {
                const mins = r.t3_mins ?? r.t2_mins ?? r.t1_mins;
                const hit = r.first_hit ?? (r.status === "OPEN" ? "…" : "—");
                return (
                <div key={i} className="flex items-center justify-between text-[11px] border-b border-white/5 last:border-0 py-0.5">
                  <span className="text-white/45 w-9">{r.time ?? "—"}</span>
                  <span className="font-bold w-16" style={{ color: r.option_type === "PE" ? RED : GREEN }}>{r.strike ?? "—"} {r.option_type ?? ""}</span>
                  <span className="text-white/55 tabular-nums w-12 text-right">{inr(r.entry, 1)}</span>
                  <span className="w-14 text-right tabular-nums" style={{ color: hit === "STOP" ? RED : hit.startsWith("T") ? GREEN : MUTED }}>{hit}{mins != null ? ` ${mins}m` : ""}</span>
                  <span className="font-bold w-12 text-right" style={{ color: statusColor(r.status) }}>{r.status}</span>
                </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}

/* ==================================================================== main */

export default function ProDashboard({ snap, onCalculate }: { snap: Snapshot; onCalculate: () => void }) {
  const d = snap.decision;
  const ind = snap.indicators;
  const risk = snap.risk;
  const [lots, setLots] = useState(1);

  /* ---- high-confidence gate (display-only; engine stays frozen) ---- */
  // Default 80% = the frozen 75–80% quality floor. At 95% almost nothing
  // qualifies, so the dashboard would show WAIT all day even on valid BUYs.
  const [gate, setGate] = useState(80);

  /* ---- chart source toggle: native lightweight chart vs embedded TradingView ---- */
  const [chartSrc, setChartSrc] = useState<"native" | "tv">("native");

  /* ---- entry mode: pullback (safe) | momentum (trend) | pattern (candle) ---- */
  const [entryMode, setEntryModeState] = useState<EntryMode>("pullback");
  const [modeBusy, setModeBusy] = useState(false);
  useEffect(() => {
    getEngineSettings()
      .then((s) =>
        setEntryModeState(
          s.entry_mode === "momentum" ||
            s.entry_mode === "pattern" ||
            s.entry_mode === "auto"
            ? s.entry_mode
            : "pullback",
        ),
      )
      .catch(() => {});
  }, []);
  const flipMode = (m: EntryMode) => {
    setModeBusy(true);
    setEntryModeState(m);
    setEntryMode(m).catch(() => {}).finally(() => setModeBusy(false));
  };

  /* ---- paper auto-trader (simulated fills only; never live orders) ---- */
  const auto = snap.auto_trade;
  const [autoBusy, setAutoBusy] = useState(false);
  const pushAuto = (opts: { enabled?: boolean; min_confidence?: number; max_lots?: number }) => {
    setAutoBusy(true);
    setAutoTrade(opts).catch(() => {}).finally(() => setAutoBusy(false));
  };

  /* ---- signal scoreboard (auto-logged outcomes, polled) ---- */
  const [sigStats, setSigStats] = useState<SignalStats | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => {
      if (typeof document !== "undefined" && document.hidden) return;
      getSignalStats(snap.instrument, 7)
        .then((s) => { if (alive) setSigStats(s); })
        .catch(() => {});
    };
    load();
    const id = setInterval(load, 15000);
    return () => { alive = false; clearInterval(id); };
  }, [snap.instrument]);

  /* ---- signal colours & words ---- */
  const engineBuy = d.signal === "BUY" || d.signal === "HOLD";
  const gateBlocked = engineBuy && d.confidence < gate;
  const isBuy = engineBuy && !gateBlocked;
  const isExit = d.signal === "EXIT";
  // Don't-chase guard (display-only, engine unchanged): a live BUY whose premium
  // has already run ABOVE the entry zone means you'd be buying the top. Warn and
  // paint it amber so you wait for the pullback back into the zone.
  const zoneHi = d.entry_range ? d.entry_range[1] : null;
  const liveP = d.current_premium ?? null;
  // today's high/low of the option premium — prefer the broker's OFFICIAL day
  // H/L (matches the broker app); fall back to candles+live price otherwise.
  const premFallback = premiumHiLoToday(snap.selected_option_candles, liveP);
  const premHi = snap.option_day_high ?? premFallback.high;
  const premLo = snap.option_day_low ?? premFallback.low;
  // No-live-data guard (display-only): a BUY with no valid live option premium
  // (common on illiquid stock options that barely trade on Angel) is NOT
  // actionable — show STAND ASIDE instead of a phantom call.
  const noLiveData = isBuy && (liveP == null || liveP <= 0);
  const chasing = isBuy && !noLiveData && zoneHi != null && liveP != null && liveP > zoneHi * 1.002;
  const sigColor = noLiveData ? AMBER : isBuy ? (chasing ? AMBER : GREEN) : isExit ? RED : (d.signal === "WAIT" || gateBlocked) ? AMBER : MUTED;
  const bigWord = noLiveData ? "WAIT" : isBuy && d.option_type ? `BUY ${d.option_type}` : gateBlocked ? "WAIT" : pretty(d.signal);
  const bull = /UP|BULL/i.test(d.htf_trend ?? "");
  const bear = /DOWN|BEAR/i.test(d.htf_trend ?? "");
  const subtitle = noLiveData
    ? "NO LIVE OPTION DATA — STAND ASIDE (illiquid / no quotes)"
    : chasing
    ? "ABOVE ENTRY ZONE — DON'T CHASE · WAIT FOR PULLBACK"
    : gateBlocked
    ? `BUY BLOCKED · CONVICTION ${Math.round(d.confidence)}% < ${gate}% GATE`
    : bull
      ? (d.htf_strength ?? 0) >= 40 ? "STRONG BULLISH SETUP" : "BULLISH BIAS"
      : bear
        ? (d.htf_strength ?? 0) >= 40 ? "STRONG BEARISH SETUP" : "BEARISH BIAS"
        : "NO CLEAR TREND — STAND ASIDE";

  /* ---- derived numbers ---- */
  const entry = d.current_premium ?? (d.entry_range ? (d.entry_range[0] + d.entry_range[1]) / 2 : null);
  const stop = d.stop_loss;
  const stopPts = entry != null && stop != null ? entry - stop : null;
  let rr: number | null = null;
  if (entry != null && d.target1 != null && stop != null && entry - stop > 0) rr = (d.target1 - entry) / (entry - stop);

  const trapAll = [d.buy_trap_prob, d.sell_trap_prob, d.fake_breakout_prob, d.fake_breakdown_prob];
  const trapOverall = Math.max(...trapAll);
  const trapLabel = trapOverall >= 60 ? "HIGH" : trapOverall >= 35 ? "MEDIUM" : "LOW";
  const trapColor = trapOverall >= 60 ? RED : trapOverall >= 35 ? AMBER : GREEN;


  /* ---- market overview: prefer the broker's OFFICIAL day OHLC (so H/L match
     the broker app exactly); fall back to collected candles only when the feed
     can't supply it (e.g. simulated). ---- */
  const fc = snap.futures_candles;
  // Fallback is restricted to TODAY's IST session so a 48h candle window can't
  // produce a misleading 2-session High/Low that won't match the broker app.
  const sessFallback = sessionOHLCToday(fc, snap.futures_price ?? null);
  const dayOpen = snap.futures_day_open ?? sessFallback.open;
  const dayHigh = snap.futures_day_high ?? sessFallback.high;
  const dayLow = snap.futures_day_low ?? sessFallback.low;
  const dayVol = fc.length ? fc.reduce((a, c) => a + c.volume, 0) : null;

  /* ---- position sizing (inline) ---- */
  const lotSize = risk?.lot_size ?? 1;
  const capital = risk?.capital ?? 0;
  const riskPct = risk?.risk_per_trade_pct ?? 0;
  const riskAmt = (capital * riskPct) / 100;
  const premium = entry ?? 0;
  const costPerLot = premium * lotSize;
  const investment = costPerLot * lots;

  /* ---- checklist ---- */
  const checks = buildChecks(snap);
  const mustPass = checks.filter((c) => c.critical);
  const support = checks.filter((c) => !c.critical);
  const allCritical = mustPass.every((c) => c.ok);

  /* ---- premium gauge ---- */
  const pq = d.premium_quality ?? 0;
  const pqColor = pq >= 60 ? GREEN : pq >= 40 ? AMBER : RED;

  const planColor = isBuy ? GREEN : isExit ? RED : AMBER;

  return (
    <div className="h-full overflow-y-auto overflow-x-hidden pr-1 flex flex-col gap-3">
      {/* ===== EARLY BUY alert (advisory) — active Stage-3 setup, CE or PE ===== */}
      {d.early_momentum?.enabled && d.early_momentum.active && d.early_momentum.side && (
        <div
          className="flex items-center justify-between rounded-xl px-4 py-2.5 border"
          style={{ background: "#19e57f14", borderColor: "#19e57f55" }}
        >
          <div className="flex items-center gap-3">
            <span className="rounded-md px-2.5 py-1 text-sm font-black" style={{ background: GREEN, color: "#07120c" }}>
              ⚡ EARLY BUY {d.early_momentum.side}
            </span>
            <span className="text-[13px] text-white/80">
              New {d.early_momentum.side === "CE" ? "up" : "down"}-trend forming · score <b>{Math.round(d.early_momentum.score)}</b>
              {d.early_momentum.entry_hint != null && <> · entry ≈ <b>₹{d.early_momentum.entry_hint.toFixed(1)}</b></>}
              {d.early_momentum.option_symbol && <> · <b>{d.early_momentum.option_symbol}</b></>}
            </span>
          </div>
          <span className="text-[11px] text-white/45">advisory · higher risk · you place it manually</span>
        </div>
      )}

      {/* ============================ TOP ROW ============================ */}
      <div className="grid grid-cols-12 gap-3 items-stretch">
        {/* -------- left column -------- */}
        <div className="col-span-3 min-w-0 flex flex-col gap-3">
          <Panel title="Market Overview" className="flex-[1.3]">
            <div className="flex items-start justify-between">
              <div>
                <div className="text-[10px] uppercase text-white/40">Spot Price</div>
                <div className="text-3xl font-black leading-tight" style={{ color: snap.futures_change >= 0 ? GREEN : RED }}>{num(snap.futures_price, 2)}</div>
              </div>
              <div className="text-right">
                <div className="text-[10px] uppercase text-white/40">Change</div>
                <div className="text-base font-bold" style={{ color: snap.futures_change >= 0 ? GREEN : RED }}>
                  {snap.futures_change >= 0 ? "+" : ""}{num(snap.futures_change, 2)} ({num(snap.futures_change_pct, 2)}%)
                </div>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-px mt-3 flex-1 rounded-lg overflow-hidden bg-white/[0.06]">
              {([
                ["Open", num(dayOpen, 2), "#e7ecf2"],
                ["High", num(dayHigh, 2), GREEN],
                ["Low", num(dayLow, 2), RED],
                ["Volume", compact(dayVol), "#e7ecf2"],
                ["VWAP", num(ind.vwap, 2), "#e7ecf2"],
                ["ATR (14)", num(ind.atr, 2), "#e7ecf2"],
                ["PCR", ind.pcr != null ? ind.pcr.toFixed(2) : "—", AMBER],
                ["OI Bias", pretty(ind.oi_bias), ind.oi_bias === "BULLISH" ? GREEN : ind.oi_bias === "BEARISH" ? RED : MUTED],
                ["Trend", pretty(ind.trend), bull ? GREEN : bear ? RED : MUTED],
              ] as [string, string, string][]).map(([l, v, c]) => (
                <div key={l} className="bg-[#0e141b] flex flex-col justify-center items-center py-2">
                  <div className="text-[9px] uppercase text-white/35">{l}</div>
                  <div className="text-sm font-bold tabular-nums" style={{ color: c }}>{v}</div>
                </div>
              ))}
            </div>
          </Panel>

          <Panel title="Institutional Activity" className="flex-1">
            <div className="flex-1 flex flex-col justify-between gap-1">
              {[
                { l: "OI Build-up", bar: ind.oi_bias === "BULLISH" ? 75 : ind.oi_bias === "BEARISH" ? 30 : 50, t: pretty(ind.oi_bias), c: ind.oi_bias === "BULLISH" ? GREEN : ind.oi_bias === "BEARISH" ? RED : MUTED },
                { l: "Volume", bar: ind.volume_level === "High" || ind.volume_level === "Very High" ? 80 : ind.volume_level === "Low" ? 30 : 55, t: ind.volume_level ?? "—", c: ind.volume_level === "Low" ? AMBER : GREEN },
                { l: "Smart Money", bar: ind.smart_money === "BULLISH" ? 75 : ind.smart_money === "BEARISH" ? 30 : 50, t: pretty(ind.smart_money_label ?? ind.smart_money), c: ind.smart_money === "BULLISH" ? GREEN : ind.smart_money === "BEARISH" ? RED : MUTED },
                { l: "PCR (Vol)", bar: Math.min(100, (ind.pcr ?? 1) * 60), t: ind.pcr != null ? ind.pcr.toFixed(2) : "—", c: AMBER },
              ].map((r) => (
                <div key={r.l} className="flex items-center justify-between flex-1 border-b border-white/5 last:border-0">
                  <span className="text-[12px] text-white/60">{r.l}</span>
                  <div className="flex items-center gap-2 shrink-0">
                    <MiniBar value={r.bar} color={r.c} />
                    <span className="text-[11px] font-bold w-16 text-right truncate" style={{ color: r.c }}>{r.t}</span>
                  </div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        {/* -------- center: live signal + trade plan -------- */}
        <div className="col-span-6 min-w-0 flex flex-col gap-3">
          <Panel
            title="Live Trading Signal"
            right={
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 justify-end">
                <div className="flex items-center gap-1" title="Pullback = only buy dips (safest). Momentum = also follow strong trends that don't pull back. Pattern = confirmation-candle entries (engulfing/star/hammer in the trend). Auto = all three together (most coverage) — the signal shows which trigger fired.">
                  <span className="text-[9px] uppercase tracking-wide text-white/35 mr-1">Entry</span>
                  {(["pullback", "momentum", "pattern", "auto"] as const).map((m) => (
                    <button
                      key={m}
                      disabled={modeBusy}
                      onClick={() => flipMode(m)}
                      className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-none capitalize"
                      style={
                        entryMode === m
                          ? { background: AMBER, color: "#07120c" }
                          : { background: "rgba(255,255,255,0.06)", color: "#8b97a5" }
                      }
                    >
                      {m}
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[9px] uppercase tracking-wide text-white/35 mr-1">Conf. Gate</span>
                  {[80, 85, 90, 95].map((g) => (
                    <button
                      key={g}
                      onClick={() => setGate(g)}
                      className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-none"
                      style={
                        gate === g
                          ? { background: GREEN, color: "#07120c" }
                          : { background: "rgba(255,255,255,0.06)", color: "#8b97a5" }
                      }
                    >
                      {g}
                    </button>
                  ))}
                </div>
              </div>
            }
          >
            {/* persistent live option price — keeps running even after entry/buy */}
            <div className="flex items-center justify-center gap-3 pb-2 mb-1 border-b border-white/8">
              <span className="flex items-center gap-1.5 text-[11px] font-bold text-white/60">
                <span className="w-2 h-2 rounded-full animate-pulse" style={{ background: GREEN }} /> LIVE
              </span>
              <span className="text-[11px] uppercase tracking-wide text-white/40">Option LTP</span>
              <span className="text-2xl font-black tabular-nums" style={{ color: AMBER }}>{inr(d.current_premium, 2)}</span>
              <span className="flex items-center gap-2 pl-3 ml-1 border-l border-white/10">
                <span className="text-[10px] uppercase tracking-wide text-white/40">Day</span>
                <span className="text-sm font-black tabular-nums" style={{ color: GREEN }} title="Today's premium high">
                  H {num(premHi, 2)}
                </span>
                <span className="text-sm font-black tabular-nums" style={{ color: RED }} title="Today's premium low">
                  L {num(premLo, 2)}
                </span>
              </span>
            </div>

            {snap.event_guard?.active && (
              <div
                className="text-[11px] font-bold text-center rounded px-2 py-1 mb-1"
                style={{ background: "rgba(255,107,107,0.12)", color: "#ff6b6b" }}
              >
                ⚠ {snap.event_guard.event} in ~{snap.event_guard.minutes_to}m — high-impact event, prefer WAIT
              </div>
            )}

            <div className="text-center py-2">
              <div className="text-7xl xl:text-8xl font-black tracking-tight leading-none" style={{ color: sigColor, textShadow: `0 0 40px ${sigColor}55` }}>
                {bigWord}
              </div>
              <div className="text-base font-bold tracking-[0.2em] mt-2" style={{ color: sigColor }}>{subtitle}</div>
              {chasing && (
                <div
                  className="mx-auto mt-2 inline-block rounded-md px-3 py-1 text-xs font-black tracking-wide"
                  style={{ background: `${AMBER}1a`, border: `1px solid ${AMBER}66`, color: AMBER }}
                >
                  ⚠ PRICE {inr(liveP, 2)} IS ABOVE ENTRY ZONE {d.entry_range ? `${inr(d.entry_range[0], 2)}–${inr(d.entry_range[1], 2)}` : ""} — DON&apos;T CHASE
                </div>
              )}
            </div>

            {/* -------- paper auto-trader (simulated fills, no real orders) -------- */}
            <div
              className="rounded-xl border-2 px-3 py-2 mt-2"
              style={{
                borderColor: auto?.enabled ? `${GREEN}66` : "rgba(255,255,255,0.10)",
                background: auto?.enabled ? `${GREEN}0d` : "rgba(255,255,255,0.03)",
              }}
            >
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-black tracking-wide" style={{ color: auto?.enabled ? GREEN : "#8b97a5" }}>
                    ⚡ AUTO-TRADE
                  </span>
                  <span className="text-[9px] font-bold uppercase px-1.5 py-0.5 rounded" style={{ background: "rgba(255,255,255,0.08)", color: AMBER }}>
                    PAPER · SIMULATED
                  </span>
                </div>
                <button
                  disabled={autoBusy}
                  onClick={() => pushAuto({ enabled: !auto?.enabled })}
                  className="px-3 py-1 rounded-md text-[11px] font-black leading-none"
                  style={auto?.enabled ? { background: GREEN, color: "#07120c" } : { background: "rgba(255,255,255,0.10)", color: "#c4ccd6" }}
                >
                  {auto?.enabled ? "ON" : "OFF"}
                </button>
              </div>
              <div className="flex items-center justify-between gap-2 mt-2">
                <div className="flex items-center gap-1">
                  <span className="text-[9px] uppercase tracking-wide text-white/35 mr-1">Buy ≥</span>
                  {[85, 90, 95].map((g) => (
                    <button
                      key={g}
                      disabled={autoBusy}
                      onClick={() => pushAuto({ min_confidence: g })}
                      className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-none"
                      style={
                        Math.round(auto?.min_confidence ?? 90) === g
                          ? { background: GREEN, color: "#07120c" }
                          : { background: "rgba(255,255,255,0.06)", color: "#8b97a5" }
                      }
                    >
                      {g}%
                    </button>
                  ))}
                </div>
                <div className="flex items-center gap-3 text-[10px] text-white/45">
                  <span>Entries: <b className="text-white/75">{auto?.entries_today ?? 0}</b></span>
                  <span>Day P&L: <b style={{ color: (auto?.day_pnl ?? 0) >= 0 ? GREEN : RED }}>{inr(auto?.day_pnl ?? 0, 0)}</b></span>
                </div>
              </div>
              <div className="text-[10px] mt-1.5 text-center" style={{ color: auto?.blocked_reason ? RED : "#6b7684" }}>
                {auto?.blocked_reason
                  ? `Blocked: ${auto.blocked_reason}`
                  : auto?.last_action
                    ? auto.last_action
                    : auto?.enabled
                      ? "Armed — waiting for a qualifying BUY; auto-exits at Target 1 or stop."
                      : "Off — turn ON to auto-enter (paper) on a BUY and auto-exit at T1/stop."}
              </div>
            </div>

            <div className="grid grid-cols-5 gap-2 mt-3 rounded-xl border-2 p-3 items-center text-center" style={{ borderColor: `${sigColor}66`, background: `${sigColor}0d` }}>
              <div>
                <div className="text-[10px] uppercase text-white/40">Action</div>
                <div className="text-3xl leading-tight">{isBuy && !noLiveData ? (d.option_type === "PE" ? "🐻" : "🐂") : isExit ? "🚪" : "⏸"}</div>
                <div className="text-base font-black" style={{ color: sigColor }}>{isBuy && !noLiveData ? (chasing ? "WAIT" : "BUY") : gateBlocked || noLiveData ? "WAIT" : pretty(d.signal)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-white/40">Instrument</div>
                <div className="text-sm font-bold text-white/70 leading-tight">{snap.instrument_name}</div>
                <div className="text-2xl font-black leading-tight" style={{ color: sigColor }}>
                  {d.strike != null ? d.strike : "—"}
                  {d.option_type ? <span className="ml-1">{d.option_type}</span> : null}
                </div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-white/40">{d.entry_range ? "Entry Zone" : "Entry Price"}</div>
                {d.entry_range ? (
                  <div className="leading-tight">
                    <div className="text-2xl font-black tabular-nums" style={{ color: GREEN }}>{inr(d.entry_range[0], 2)}</div>
                    <div className="text-2xl font-black tabular-nums" style={{ color: GREEN }}>– {inr(d.entry_range[1], 2)}</div>
                  </div>
                ) : (
                  <div className="text-3xl font-black tabular-nums" style={{ color: GREEN }}>{inr(entry, 2)}</div>
                )}
              </div>
              <div>
                <div className="text-[10px] uppercase text-white/40">Stop Loss</div>
                <div className="text-3xl font-black tabular-nums" style={{ color: RED }}>{inr(stop, 2)}</div>
              </div>
              <div>
                <div className="text-[10px] uppercase text-white/40 mb-1">Targets</div>
                <div className="flex flex-col gap-0.5 items-center">
                  {([["T1", d.target1], ["T2", d.target2], ["T3", d.target3]] as [string, number | null][]).map(([l, v]) => (
                    <div key={l} className="flex justify-center gap-2 text-[13px]"><span className="text-white/45 font-bold">{l}</span><span className="font-black tabular-nums" style={{ color: GREEN }}>{inr(v, 2)}</span></div>
                  ))}
                </div>
              </div>
            </div>
          </Panel>

          {/* -------- Trade Plan: only non-duplicated plan context -------- */}
          <Panel title="Trade Plan" className="flex-1">
            <div className="grid grid-cols-3 gap-2 flex-1 auto-rows-fr">
              {[
                { l: "Setup", v: pretty(d.entry_trigger), c: planColor },
                { l: "Reward : Risk", v: rr != null ? `${rr.toFixed(2)} : 1` : "—", c: rr != null && rr >= 1.5 ? GREEN : AMBER },
                { l: "Expected Move", v: d.expected_move_points != null ? `${d.expected_move_points >= 0 ? "+" : ""}${num(d.expected_move_points, 1)} pts` : "—", c: GREEN },
                { l: "Max Risk", v: inr(riskAmt), c: RED },
                { l: "Hold Time", v: d.expected_holding_minutes != null ? `${d.expected_holding_minutes} min` : "—", c: "#e7ecf2" },
                { l: "Lot Size", v: `${lotSize} qty`, c: "#e7ecf2" },
              ].map((x) => (
                <div key={x.l} className="rounded-lg border border-white/8 bg-white/[0.02] px-3 py-2.5 text-center flex flex-col justify-center items-center">
                  <div className="text-[10px] uppercase tracking-wide text-white/40">{x.l}</div>
                  <div className="text-2xl font-black tabular-nums leading-tight mt-0.5" style={{ color: x.c }}>{x.v}</div>
                </div>
              ))}
            </div>
          </Panel>
        </div>

        {/* -------- right column -------- */}
        <div className="col-span-3 min-w-0 flex flex-col gap-3">
          <Panel title="Trade Score" className="flex-1 justify-between" right={<Chip label={`${Math.round(d.trade_score)}/100`} color={d.trade_score >= 70 ? GREEN : d.trade_score >= 40 ? AMBER : RED} />}>
            <div className="grid place-items-center flex-1">
              <Gauge value={d.trade_score} color={d.trade_score >= 70 ? GREEN : d.trade_score >= 40 ? AMBER : RED} />
            </div>
            <div className="grid grid-cols-3 gap-1 text-center border-t border-white/6 pt-2 text-[12px]">
              <div><div className="text-white/40 text-[9px]">Conviction</div><div className="font-black text-lg" style={{ color: d.confidence >= 80 ? GREEN : d.confidence >= 65 ? AMBER : RED }}>{Math.round(d.confidence)}%</div></div>
              <div><div className="text-white/40 text-[9px]">Opportunity</div><div className="font-black text-lg" style={{ color: /HIGH/.test(d.opportunity_label) ? GREEN : AMBER }}>{pretty(d.opportunity_label)}</div></div>
              <div><div className="text-white/40 text-[9px]">Risk</div><div className="font-black text-lg" style={{ color: d.risk_level === "LOW" ? GREEN : d.risk_level === "HIGH" ? RED : AMBER }}>{pretty(d.risk_level)}</div></div>
            </div>
          </Panel>

          <Panel title="Premium Analysis" className="flex-1 justify-between" right={<Chip label={pretty(d.premium_health)} color={d.premium_health === "HEALTHY" ? GREEN : d.premium_health === "DANGEROUS" ? RED : AMBER} />}>
            <div className="grid place-items-center flex-1">
              <Gauge value={pq} color={pqColor} />
            </div>
            <div className="grid grid-cols-3 gap-1 text-center border-t border-white/6 pt-2 text-[12px]">
              <div><div className="text-white/40 text-[9px]">Momentum</div><div className="font-black text-lg" style={{ color: (d.premium_momentum ?? 0) >= 0 ? GREEN : RED }}>{d.premium_momentum != null ? Math.round(d.premium_momentum) : "—"}</div></div>
              <div><div className="text-white/40 text-[9px]">Speed</div><div className="font-black text-lg text-white/85">{num(d.premium_velocity, 2)}</div></div>
              <div><div className="text-white/40 text-[9px]">Accel</div><div className="font-black text-lg text-white/85">{num(d.premium_acceleration, 2)}</div></div>
            </div>
          </Panel>

          <Panel title="Trap Detector" className="flex-1 justify-center">
            <div className="flex flex-col gap-2 flex-1 justify-center">
              {[["Bull Trap", d.buy_trap_prob], ["Bear Trap", d.sell_trap_prob], ["Fake Breakout", d.fake_breakout_prob], ["Whipsaw", d.fake_breakdown_prob]].map(([l, v]) => {
                const val = v as number;
                const c = val >= 60 ? RED : val >= 35 ? AMBER : GREEN;
                return (
                  <div key={l as string} className="flex items-center justify-between">
                    <span className="text-[12px] text-white/60">{l}</span>
                    <div className="flex items-center gap-2"><MiniBar value={val} color={c} /><span className="text-[11px] font-bold w-8 text-right" style={{ color: c }}>{Math.round(val)}%</span></div>
                  </div>
                );
              })}
            </div>
            <div className="flex items-center justify-between border-t border-white/6 pt-2 mt-2">
              <span className="text-[11px] text-white/60">Overall Trap Risk</span>
              <span className="text-lg font-black" style={{ color: trapColor }}>{trapLabel}</span>
            </div>
          </Panel>
        </div>
      </div>

      {/* ===================== CHART + POSITION SIZE ===================== */}
      <div className="grid grid-cols-12 gap-3 items-stretch">
        <Panel
          title={`${snap.instrument_name} 5M Chart · click to expand`}
          className="col-span-8 h-64"
          expandHeight="78vh"
          expandable
          right={
            <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
              {(["native", "tv"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setChartSrc(s)}
                  className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-none"
                  style={
                    chartSrc === s
                      ? { background: GREEN, color: "#07120c" }
                      : { background: "rgba(255,255,255,0.06)", color: "#8b97a5" }
                  }
                >
                  {s === "native" ? "Native" : "TradingView"}
                </button>
              ))}
            </div>
          }
        >
          <div className="flex-1 min-h-0">
            {chartSrc === "native" ? (
              <FuturesChart candles={fc} vwap={ind.vwap} />
            ) : (
              <TradingViewChart symbol={snap.tradingview_symbol} interval="5" />
            )}
          </div>
        </Panel>

        <Panel title="Position Size Calculator" className="col-span-4 h-64">
          <div className="flex flex-col gap-1 text-[12px]">
            <Row l="Capital" v={inr(capital)} />
            <Row l="Risk / Trade" v={`${num(riskPct, 2)}%`} />
            <Row l="Stop (pts)" v={stopPts != null ? num(stopPts, 2) : "—"} />
            <Row
              l="Suggested Lots"
              v={risk?.suggested_lots != null ? `${risk.suggested_lots} lot${risk.suggested_lots === 1 ? "" : "s"}` : "—"}
              vc={GREEN}
            />
            <div className="flex items-center justify-between">
              <span className="text-white/45">Lots</span>
              <div className="flex items-center gap-1.5">
                <button onClick={(e) => { e.stopPropagation(); setLots((n) => Math.max(1, n - 1)); }} className="w-6 h-6 rounded bg-white/8 text-white/70 leading-none">−</button>
                <span className="w-6 text-center font-bold text-white">{lots}</span>
                <button onClick={(e) => { e.stopPropagation(); setLots((n) => n + 1); }} className="w-6 h-6 rounded bg-white/8 text-white/70 leading-none">+</button>
              </div>
            </div>
            <Row l="Cost / Lot" v={inr(costPerLot)} />
            <Row l="Total Invest" v={inr(investment)} vc={GREEN} />
          </div>
          <button onClick={(e) => { e.stopPropagation(); onCalculate(); }} className="mt-auto w-full text-[12px] font-bold py-2 rounded-lg" style={{ background: GREEN, color: "#07120c" }}>
            FULL CALCULATOR
          </button>
        </Panel>
      </div>

      {/* ===================== SIGNAL SCOREBOARD (daily) ===================== */}
      <SignalScoreboard stats={sigStats} />

      {/* ======================= EXPIRY OPPORTUNITY ======================= */}
      <ExpiryOpportunityPanel snap={snap} />

      {/* ======================= INTELLIGENCE LAYER ======================= */}
      <div className="grid grid-cols-12 gap-3 items-stretch">
        <Panel
          title="Intelligence Layer · Trade Quality (advisory)"
          className="col-span-12"
          right={<Chip label="Phase 3.1 · engine frozen" color={MUTED} />}
        >
          <IntelligencePanel intel={snap.intelligence} />
        </Panel>
      </div>

      {/* ===================== EXECUTION INTELLIGENCE ===================== */}
      {d.execution_intelligence?.enabled && (
        <div className="grid grid-cols-12 gap-3 items-stretch">
          <div className="col-span-12">
            <ExecutionPanel decision={d} />
          </div>
        </div>
      )}

      {/* ===================== EARLY MOMENTUM (advisory, parallel) ===================== */}
      {d.early_momentum?.enabled && (
        <div className="grid grid-cols-12 gap-3 items-stretch">
          <div className="col-span-12">
            <EarlyMomentumPanel decision={d} />
          </div>
        </div>
      )}

      {/* ============================ BOTTOM ROW ============================ */}
      <div className="grid grid-cols-12 gap-3 items-stretch">
        <Panel title="Why This Is A Good Setup" className="col-span-6">
          <div className="grid grid-cols-2 gap-x-4 gap-y-2">
            {(d.reasons.length ? d.reasons : ["No dominant factors yet."]).map((r, i) => (
              <div key={i} className="flex items-start gap-2 text-[12px]">
                <span className="grid place-items-center w-4 h-4 rounded-full text-[9px] font-bold shrink-0 mt-0.5"
                  style={{ background: isBuy ? `${GREEN}22` : `${MUTED}22`, color: isBuy ? GREEN : MUTED }}>
                  {isBuy ? "✓" : "•"}
                </span>
                <span className="text-white/75 leading-snug">{r}</span>
              </div>
            ))}
          </div>
        </Panel>

        <Panel title="Confidence Checklist" className="col-span-6">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            <div>
              <div className="text-[10px] font-bold uppercase text-white/35 mb-1">Must Pass</div>
              <div className="flex flex-col gap-1">{mustPass.map((c) => <CheckLine key={c.label} label={c.label} ok={c.ok} />)}</div>
            </div>
            <div>
              <div className="text-[10px] font-bold uppercase text-white/35 mb-1">Supporting</div>
              <div className="flex flex-col gap-1">{support.map((c) => <CheckLine key={c.label} label={c.label} ok={c.ok} />)}</div>
            </div>
          </div>
          <div className="mt-2 text-[11px] font-bold" style={{ color: allCritical ? GREEN : RED }}>
            {allCritical ? "● ALL CRITICAL CHECKS PASSED ✅" : "● CRITICAL CHECKS FAILING — STAND ASIDE"}
          </div>
        </Panel>
      </div>

      {/* ============================ STATUS BAR ============================ */}
      <div className="grid grid-cols-6 gap-3 rounded-xl border border-white/8 bg-[#0e141b] px-4 py-2 text-[11px]">
        <StatusItem l="Data Source" v={snap.data_source === "angelone" ? "Angel One" : "Simulated"} c={snap.data_source === "angelone" ? GREEN : AMBER} />
        <StatusItem l="Connection" v={snap.market_open ? "Live" : "Idle"} c={snap.market_open ? GREEN : MUTED} />
        <StatusItem l="Data Quality" v={fc.length > 100 ? "Excellent" : fc.length > 20 ? "Good" : "Low"} c={fc.length > 100 ? GREEN : AMBER} />
        <FeedStatus snap={snap} />
        <StatusItem l="Trade Mode" v={snap.trade_mode.toUpperCase()} c={snap.trade_mode === "paper" ? GREEN : RED} />
        <StatusItem l="Shadow Mode" v="Off" c={MUTED} />
      </div>
    </div>
  );
}

function Row({ l, v, vc }: { l: string; v: string; vc?: string }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-white/45">{l}</span>
      <span className="font-bold tabular-nums" style={{ color: vc ?? "#e7ecf2" }}>{v}</span>
    </div>
  );
}

/* Feed freshness. Shows the AGE of the last tick, not just the mode: an
   instrument that has silently dropped to the 3s REST poll — or an illiquid
   strike that has stopped ticking altogether — looks identical to a live one
   on price alone, which is how a frozen premium became a tradeable signal. */
function FeedStatus({ snap }: { snap: Snapshot }) {
  const h = snap.feed_health;
  const mode = h?.mode ?? snap.feed_mode;
  if (!h) {
    return (
      <StatusItem
        l="Feed"
        v={mode === "push" ? "Live Push" : mode === "rest" ? "REST 3s" : "Simulated"}
        c={mode === "push" ? GREEN : mode === "rest" ? AMBER : MUTED}
      />
    );
  }
  // The option leg is what we actually trade, so the slower of the two ages is
  // the honest one to show.
  const age = Math.max(h.underlying_age_sec ?? 0, h.option_age_sec ?? 0);
  const stale = age > h.stale_after_sec;
  const down = h.source === "UNAVAILABLE" || h.state === "DISCONNECTED";
  const label = down ? "DISCONNECTED" : mode === "simulated" ? "Simulated" : h.live && !stale ? "LIVE" : "DELAYED";
  const color = down ? RED : mode === "simulated" ? MUTED : h.live && !stale ? GREEN : AMBER;
  const detail = down || mode === "simulated" ? "" : ` ${age.toFixed(0)}s`;
  const title =
    h.note +
    (h.push_pct_today !== null ? ` · ${h.push_pct_today}% of today on push` : "") +
    (h.fallbacks_today ? ` · ${h.fallbacks_today} fallback(s) today` : "");
  return (
    <div className="flex items-center gap-2" title={title}>
      <span className="text-white/35 uppercase tracking-wide">Feed</span>
      <span className="font-bold tabular-nums" style={{ color }}>
        ● {label}
        {detail}
      </span>
    </div>
  );
}

function StatusItem({ l, v, c }: { l: string; v: string; c: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-white/35 uppercase tracking-wide">{l}</span>
      <span className="font-bold" style={{ color: c }}>● {v}</span>
    </div>
  );
}
