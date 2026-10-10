"use client";
import { useEffect, useState } from "react";
import { EarlyEarly, ScalpSignal, Snapshot } from "@/lib/types";
import {
  getMomentumOutcomes,
  MomentumInstrumentStat,
  MomentumOutcome,
} from "@/lib/api";
import { useMomentumSound, useScalpExitSound } from "@/lib/useBuySound";

const GREEN = "#19e57f";
const RED = "#ff5c5c";
const AMBER = "#f5b301";
const BLUE = "#3aa0ff";
const MUTED = "#8b93a7";

// This board deliberately shows ONLY the early lifecycle (0..3). The old
// "Stage 4 · Confirmation Buy" belongs to the frozen Decision engine, so it is
// not a rung here — when the frozen engine confirms we just note it.
const STAGES = [
  { n: 0, label: "No Setup", color: MUTED },
  { n: 1, label: "Building", color: AMBER },
  { n: 2, label: "Structure", color: BLUE },
  { n: 3, label: "Early Buy", color: GREEN },
];

function money(n: number | null | undefined, p = 1): string {
  return n == null ? "—" : `₹${n.toFixed(p)}`;
}

/* Horizontal 180° speedometer gauge for the evidence score (0..100). The
   needle + coloured fill move up live from the very first stage, so the user
   can watch momentum build. Pure geometry (top semicircle, centre at bottom
   middle) so it always renders as a clean half-circle. */
function ScoreGauge({ score, side }: { score: number; side: string }) {
  const v = Math.max(0, Math.min(100, score));
  const col = v >= 80 ? GREEN : v >= 60 ? AMBER : v >= 35 ? BLUE : MUTED;

  const cx = 100;
  const cy = 96;
  const R = 80;

  // Point on the TOP semicircle. frac 0 = left end, 1 = right end.
  const pt = (frac: number, radius = R) => {
    const theta = Math.PI * (1 - frac); // π (left) .. 0 (right)
    return [cx + radius * Math.cos(theta), cy - radius * Math.sin(theta)];
  };

  const [lx, ly] = pt(0);
  const [rx, ry] = pt(1);
  const [vx, vy] = pt(v / 100);
  const [nx, ny] = pt(v / 100, R - 16); // needle tip

  return (
    <svg viewBox="0 0 200 116" className="w-[200px] h-[116px]">
      {/* track */}
      <path
        d={`M ${lx} ${ly} A ${R} ${R} 0 0 1 ${rx} ${ry}`}
        fill="none"
        stroke="rgba(255,255,255,0.10)"
        strokeWidth={12}
        strokeLinecap="round"
      />
      {/* filled portion up to the score */}
      {v > 0 && (
        <path
          d={`M ${lx} ${ly} A ${R} ${R} 0 0 1 ${vx} ${vy}`}
          fill="none"
          stroke={col}
          strokeWidth={12}
          strokeLinecap="round"
        />
      )}
      {/* needle */}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke={col} strokeWidth={3.5} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={6} fill={col} />
      <text x={cx} y={cy - 26} textAnchor="middle" className="fill-white" style={{ fontSize: 30, fontWeight: 900 }}>
        {Math.round(v)}
      </text>
      <text x={cx} y={cy - 9} textAnchor="middle" style={{ fontSize: 10, fill: MUTED, letterSpacing: 1 }}>
        EVIDENCE {side}
      </text>
    </svg>
  );
}

export default function MomentumBoard({ snap }: { snap: Snapshot }) {
  const em = snap.decision.early_momentum;
  const sd = snap.decision.signal_delay;
  const ee = snap.decision.early_early;
  const sc = snap.decision.scalp;

  const [stats, setStats] = useState<MomentumInstrumentStat[]>([]);
  const [recent, setRecent] = useState<MomentumOutcome[]>([]);
  const [totalOutcomes, setTotalOutcomes] = useState(0);
  const [soundOn, setSoundOn] = useState(true);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const d = await getMomentumOutcomes();
        if (!alive) return;
        setStats(d.instruments || []);
        setRecent(d.recent || []);
        setTotalOutcomes(d.total || 0);
      } catch {
        /* keep last */
      }
    };
    load();
    const id = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // The board is a permanent tab; when the engine flag is off we still render
  // the frame with a clear, non-alarming banner instead of hiding everything.
  const engineOff = !em || !em.enabled;

  const plan = em?.plan ?? null;
  const side = em?.side ?? "";
  const active = !!em?.active && !!plan;
  const stageNum = em?.stage_num ?? 0;
  const frozenConfirmed = stageNum === 4;
  // Only a genuine EARLY BUY (Stage 3, active) highlights the "Early Buy" rung.
  // At Stage 4 the frozen engine has taken over — the early-momentum window is
  // gone, so we do NOT light "Early Buy ◄ now" (that made it look like this
  // engine was giving an early buy when it wasn't).
  const ladderFill = Math.min(stageNum, 3);
  const currentRung = frozenConfirmed ? -1 : ladderFill;
  const sideColor = side === "CE" ? GREEN : side === "PE" ? "#ff8a3d" : MUTED;

  const headline = active
    ? `EARLY BUY ${side}`
    : frozenConfirmed
    ? "EARLY WINDOW PASSED"
    : stageNum === 2
    ? "WAIT — structure forming"
    : stageNum === 1
    ? "WAIT — momentum building"
    : "NO SETUP";
  const headColor = active ? sideColor : frozenConfirmed ? "#c4b5fd" : stageNum >= 2 ? AMBER : MUTED;
  const spot = snap.decision.spot_price ?? snap.futures_price ?? null;

  // Distinct EARLY-BUY alert — a different sound from the frozen BUY chime so
  // the two engines are never confused by ear. Fires once when this instrument
  // flips INTO a genuine Stage-3 EARLY BUY (not on every tick).
  useMomentumSound(active, em?.option_symbol ?? null, soundOn);
  // URGENT klaxon the moment a running Quick Scalp flips into EXIT-NOW.
  useScalpExitSound(!!sc?.exit_now, sc?.option_symbol ?? null, soundOn);

  return (
    <div className="flex-1 overflow-y-auto pr-1 flex flex-col gap-3">
      {engineOff ? (
        <div className="rounded-xl border border-white/10 bg-[#11151c] p-4 text-[13px] text-white/70">
          <b className="text-white/85">Early Momentum engine is off.</b> Add{" "}
          <code className="text-white/85">QT_EARLY_MOMENTUM_ENABLED=true</code> to your{" "}
          <code className="text-white/85">.env</code> and restart the backend to see live EARLY BUY setups here. This is a
          separate advisory engine — it never changes the frozen Decision engine.
        </div>
      ) : (
        <>
          {/* ===================== HERO (Decision-style) ===================== */}
          <div
            className="rounded-2xl border p-4 grid grid-cols-12 gap-4 items-center"
            style={{ borderColor: `${headColor}55`, background: `${headColor}0f` }}
          >
            {/* left: signal + instrument + option */}
            <div className="col-span-5">
              <div className="flex items-center gap-2">
                <div className="text-[11px] uppercase tracking-widest text-white/45">
                  {snap.instrument_name} · Momentum Engine · advisory
                </div>
                <button
                  onClick={() => setSoundOn((s) => !s)}
                  title={soundOn ? "EARLY BUY sound ON (distinct from frozen) — click to mute" : "EARLY BUY sound OFF — click to unmute"}
                  className="px-1.5 py-0.5 rounded text-[10px] font-bold leading-none border border-white/15 hover:bg-white/5"
                  style={{ color: soundOn ? GREEN : MUTED }}
                >
                  {soundOn ? "🔊 EARLY" : "🔇 EARLY"}
                </button>
              </div>
              <div className="text-4xl font-black mt-1 leading-none" style={{ color: headColor }}>
                {headline}
              </div>
              <div className="mt-3 flex items-center gap-2 flex-wrap">
                {side && (
                  <span
                    className="rounded-md px-2.5 py-1 text-sm font-black"
                    style={{ background: `${sideColor}1f`, color: sideColor, border: `1px solid ${sideColor}66` }}
                  >
                    {side === "PE" ? "🐻 PE" : "🐂 CE"}
                  </span>
                )}
                {em?.option_symbol && (
                  <span className="text-sm font-bold text-white/85">{em.option_symbol}</span>
                )}
              </div>
              {frozenConfirmed && (
                <div className="mt-2 inline-block rounded-md px-2 py-1 text-[11px] font-bold"
                  style={{ color: "#c4b5fd", background: "#8b5cf618", border: "1px solid #8b5cf655" }}>
                  Frozen Decision engine already confirmed — not an early entry
                </div>
              )}
            </div>

            {/* middle: gauge */}
            <div className="col-span-3 flex justify-center">
              <ScoreGauge score={em?.score ?? 0} side={side || "—"} />
            </div>

            {/* right: live CE/PE numbers */}
            <div className="col-span-4 grid grid-cols-3 gap-2">
              <Num label="Spot" value={money(spot)} />
              <Num label="Premium" value={money(em?.entry_hint)} color={AMBER} />
              <Num label="Regime" value={em?.regime ?? "—"} small />
              <Num label="Stage" value={frozenConfirmed ? "done" : `${stageNum}/3`} color={STAGES[ladderFill].color} />
              <Num label="ATR·VWAP" value={em?.atr_from_vwap != null ? em.atr_from_vwap.toFixed(1) : "—"} small />
              <Num label="Risk" value={active ? plan?.risk_level ?? "HIGH" : "—"} color={active ? AMBER : MUTED} small />
            </div>
          </div>

          {/* ===================== STAGE LADDER (0..3) ===================== */}
          <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
            <div className="text-[11px] uppercase tracking-wider text-white/45 mb-3">
              Momentum lifecycle — act at <b className="text-white/70">Early Buy</b>
            </div>
            <div className="flex items-center gap-2">
              {STAGES.map((s, i) => {
                const reached = ladderFill >= s.n;
                const isNow = currentRung === s.n;
                return (
                  <div key={s.n} className="flex-1">
                    <div
                      className="h-2.5 rounded-full transition-all"
                      style={{ background: reached ? s.color : "rgba(255,255,255,0.08)" }}
                    />
                    <div
                      className="mt-1.5 text-center text-[11px] font-bold"
                      style={{ color: isNow ? s.color : reached ? "rgba(255,255,255,0.6)" : MUTED }}
                    >
                      {s.n}. {s.label}
                      {isNow && i > 0 && <span className="ml-1">◄ now</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ============ EARLY-EARLY (Stage 2.5) — separate add-on ============ */}
          {ee && ee.enabled && <EarlyEarlyPanel ee={ee} />}

          {/* ============ QUICK SCALP — separate add-on (10–20 pt) ============ */}
          {sc && sc.enabled && <ScalpPanel sc={sc} />}

          {/* ===================== TRADE PLAN (big CE/PE numbers) ===================== */}
          {active && plan ? (
            <div className="grid grid-cols-4 gap-3">
              <PlanCell label="Entry Zone" value={`${money(plan.entry_low)} – ${money(plan.entry_high)}`} color="#e7ecf2" />
              <PlanCell
                label="Stop-Loss"
                value={money(plan.stop_loss)}
                color={RED}
                sub={plan.underlying_stop != null ? `underlying ${plan.underlying_stop.toFixed(1)}` : undefined}
              />
              <PlanCell label="Risk / Lot" value={money(plan.risk_per_lot)} color={AMBER} />
              <PlanCell
                label="Reward : Risk"
                value={plan.reward_risk != null ? `${plan.reward_risk.toFixed(1)}:1` : "—"}
                color="#e7ecf2"
              />
              <PlanCell label="Target 1 · 1R" value={money(plan.target1)} color={GREEN} />
              <PlanCell label="Target 2 · 2R" value={money(plan.target2)} color={GREEN} />
              <PlanCell label="Target 3 · 3R" value={money(plan.target3)} color={GREEN} />
              <PlanCell label="Hold" value="intraday" color="#e7ecf2" sub={plan.holding_time || undefined} smallValue />
            </div>
          ) : (
            <div className="rounded-xl border border-white/10 bg-[#11151c] p-4 text-[13px] text-white/60">
              No live EARLY BUY plan yet — the full entry / stop / targets appear the moment this reaches{" "}
              <b className="text-white/80">Stage 3 · Early Buy</b>. {em?.note}
            </div>
          )}

          {/* ===================== EXIT CONDITIONS ===================== */}
          {active && plan && plan.exit_conditions.length > 0 && (
            <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
              <div className="text-[11px] uppercase tracking-wider text-white/45 mb-2">Exit Conditions — when to get out</div>
              <ul className="space-y-1.5">
                {plan.exit_conditions.map((c, i) => (
                  <li key={i} className="flex gap-2 text-[13px] text-white/75">
                    <span style={{ color: RED }}>•</span>
                    <span>{c}</span>
                  </li>
                ))}
              </ul>
              {plan.note && <div className="mt-2 text-[11px] text-white/40">{plan.note}</div>}
            </div>
          )}

          {/* ===================== EVIDENCE + SIGNAL DELAY ===================== */}
          <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
            <div className="text-[11px] uppercase tracking-wider text-white/45 mb-2">
              Evidence · adaptive weighting <span className="text-white/30">({em?.weight_note})</span>
            </div>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
              {(em?.checks ?? []).map((c, i) => (
                <span key={i} title={`${c.detail} · weight ${c.weight}`} className="text-white/60">
                  {c.name} <span className="text-white/35">({c.weight})</span>:{" "}
                  <span style={{ color: !c.available ? MUTED : c.passed ? GREEN : RED }}>
                    {!c.available ? "n/a" : c.passed ? "✓" : "✕"}
                  </span>
                </span>
              ))}
            </div>

            {sd && sd.available && (
              <div className="mt-3 rounded-lg bg-white/5 px-3 py-2" data-testid="signal-delay">
                <div className="text-[11px] uppercase tracking-wider text-white/45 mb-2">
                  Signal Delay — confirmation vs early entry
                </div>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-x-6 gap-y-2 text-[12px]">
                  <Metric label="Confirmation Entry" value={money(sd.confirmation_entry_premium)} />
                  <Metric label="Early Entry" value={money(sd.early_entry_premium)} />
                  <Metric
                    label="Difference"
                    value={
                      sd.premium_diff != null && sd.premium_diff_pct != null
                        ? `₹${sd.premium_diff.toFixed(1)} (${sd.premium_diff_pct.toFixed(1)}%)`
                        : "—"
                    }
                    color={sd.premium_diff != null ? (sd.premium_diff > 0 ? GREEN : RED) : undefined}
                  />
                  <Metric label="Missed Move" value={sd.missed_move_pct != null ? `${sd.missed_move_pct}%` : "—"} color={sd.late ? RED : AMBER} />
                  <Metric label="Remaining Move" value={sd.remaining_move_pct != null ? `${sd.remaining_move_pct}%` : "—"} />
                  <Metric label="Candles Early→Confirm" value={sd.candles_between != null ? `${sd.candles_between}` : "—"} />
                </div>
              </div>
            )}
          </div>

          {/* ===================== PER-INSTRUMENT OUTCOMES (analysis) ===================== */}
          <div className="rounded-xl border border-white/10 bg-[#11151c] p-4">
            <div className="flex items-center justify-between mb-2">
              <div className="text-[11px] uppercase tracking-wider text-white/45">
                EARLY BUY outcomes by instrument — how far each reached
              </div>
              <span className="text-[10px] text-white/35">{totalOutcomes} logged</span>
            </div>
            {stats.length === 0 ? (
              <div className="text-[12px] text-white/45">
                No completed EARLY BUY setups logged yet. Each time an instrument hits Stage 3, its result (max move,
                targets/stop hit) is recorded here for you to review — no fabricated stats.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-[12px] border-collapse">
                  <thead>
                    <tr className="text-[10px] uppercase tracking-wide text-white/40 border-b border-white/10">
                      <th className="text-left py-1.5 px-2">Instrument</th>
                      <th className="text-right py-1.5 px-2">Signals</th>
                      <th className="text-right py-1.5 px-2">Avg Max</th>
                      <th className="text-right py-1.5 px-2">Best</th>
                      <th className="text-right py-1.5 px-2">T1</th>
                      <th className="text-right py-1.5 px-2">T2</th>
                      <th className="text-right py-1.5 px-2">T3</th>
                      <th className="text-right py-1.5 px-2">Stop</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.map((s) => (
                      <tr key={s.instrument} className="border-b border-white/5">
                        <td className="py-1.5 px-2 font-bold text-white/85">{s.instrument}</td>
                        <td className="py-1.5 px-2 text-right tabular-nums">{s.signals}</td>
                        <td className="py-1.5 px-2 text-right tabular-nums" style={{ color: GREEN }}>
                          {s.avg_max_pct != null ? `${s.avg_max_pct}%` : "—"}
                        </td>
                        <td className="py-1.5 px-2 text-right tabular-nums" style={{ color: GREEN }}>
                          {s.best_pct != null ? `${s.best_pct}%` : "—"}
                        </td>
                        <td className="py-1.5 px-2 text-right tabular-nums">{s.t1_rate}%</td>
                        <td className="py-1.5 px-2 text-right tabular-nums">{s.t2_rate}%</td>
                        <td className="py-1.5 px-2 text-right tabular-nums">{s.t3_rate}%</td>
                        <td className="py-1.5 px-2 text-right tabular-nums" style={{ color: RED }}>{s.stop_rate}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {recent.length > 0 && (
                  <div className="mt-2 text-[10px] text-white/35">
                    Latest: {recent.slice(0, 4).map((r) => `${r.instrument} ${r.side ?? ""} ${r.max_gain_pct ?? 0}% (${r.close_reason})`).join(" · ")}
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="text-[11px] text-white/35 px-1 pb-2">
            Advisory · higher risk than a confirmation BUY · you place every trade manually. AUTO-TRADE never acts on an
            EARLY BUY. This engine is fully separate from the frozen Decision engine.
          </div>
        </>
      )}
    </div>
  );
}

function Num({ label, value, color, small }: { label: string; value: string; color?: string; small?: boolean }) {
  return (
    <div className="rounded-lg bg-white/5 px-2 py-1.5 text-center">
      <div className="text-[9px] uppercase tracking-wider text-white/40">{label}</div>
      <div className={`font-black mt-0.5 tabular-nums ${small ? "text-sm" : "text-lg"}`} style={{ color: color ?? "#e7ecf2" }}>
        {value}
      </div>
    </div>
  );
}

function PlanCell({
  label,
  value,
  color,
  sub,
  smallValue,
}: {
  label: string;
  value: string;
  color: string;
  sub?: string;
  smallValue?: boolean;
}) {
  return (
    <div className="rounded-xl border border-white/10 bg-[#11151c] p-3">
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className={`font-black mt-1 tabular-nums ${smallValue ? "text-sm" : "text-xl"}`} style={{ color }}>
        {value}
      </div>
      {sub && <div className="text-[10px] text-white/40 mt-1">{sub}</div>}
    </div>
  );
}

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-white/40">{label}</div>
      <div className="text-sm font-bold mt-0.5" style={color ? { color } : undefined}>{value}</div>
    </div>
  );
}

// Recommended-action ladder → colour + plain label (increasing conviction).
const ACTION_META: Record<string, { label: string; color: string }> = {
  IGNORE: { label: "Ignore", color: MUTED },
  WATCH: { label: "Watch", color: BLUE },
  PREPARE: { label: "Prepare", color: AMBER },
  SMALL_POSITION: { label: "Small Position", color: "#7ee787" },
  NORMAL_ENTRY: { label: "Normal Entry", color: GREEN },
  CONSERVATIVE_ENTRY: { label: "Conservative Entry", color: GREEN },
};

function EarlyEarlyPanel({ ee }: { ee: EarlyEarly }) {
  const active = ee.active;
  const side = ee.side ?? "";
  const sideEmoji = side === "PE" ? "🐻 PE" : side === "CE" ? "🐂 CE" : "—";
  const act = ACTION_META[ee.recommended_action] ?? ACTION_META.IGNORE;
  const plan = ee.plan;
  const agreeColor =
    ee.agreement === "CONFIRMED" ? GREEN : ee.agreement === "REJECTED" ? RED : ee.agreement === "PENDING" ? AMBER : MUTED;

  return (
    <div
      className="rounded-2xl border p-4"
      style={{
        borderColor: active ? `${GREEN}88` : "rgba(255,255,255,0.12)",
        background: active ? `${GREEN}12` : "#11151c",
      }}
    >
      {/* header banner */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-black tracking-widest"
            style={{ background: `${GREEN}22`, color: GREEN, border: `1px solid ${GREEN}66` }}
          >
            STAGE 2.5
          </span>
          <span className="text-2xl font-black leading-none" style={{ color: active ? GREEN : MUTED }}>
            {active ? `⚡ EARLY-EARLY BUY ${side}` : "EARLY-EARLY · watching"}
          </span>
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-black"
            style={{ background: "#ff5c5c1f", color: RED, border: "1px solid #ff5c5c66" }}
          >
            ⚠ HIGHER RISK
          </span>
        </div>
        {active && side && (
          <span
            className="rounded-md px-2.5 py-1 text-sm font-black"
            style={{ background: `${GREEN}1f`, color: GREEN, border: `1px solid ${GREEN}66` }}
          >
            {sideEmoji} {ee.option_symbol ?? ""}
          </span>
        )}
      </div>
      <div className="mt-1 text-[11px] text-white/50">
        Earlier than a normal Early Buy — fires only when multiple evidence pieces align (never one green candle). Separate,
        advisory-only add-on; the frozen engine and normal Early Momentum are untouched.
      </div>

      {/* (1) Probability / Evidence panel */}
      <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
        <Num label="Probability" value={`${Math.round(ee.probability)}%`} color={active ? GREEN : AMBER} />
        <Num label="Evidence" value={`${ee.evidence_count}/6`} color={ee.evidence_count >= ee.evidence_required ? GREEN : AMBER} />
        <Num label="Risk Level" value={ee.risk_level} color={RED} small />
        <Num label="Expected Hold" value={active ? "2–8 candles" : "—"} small />
      </div>

      {/* (3) Recommended Action panel */}
      <div className="mt-2 rounded-xl border p-3" style={{ borderColor: `${act.color}55`, background: `${act.color}0f` }}>
        <div className="text-[10px] uppercase tracking-wider text-white/45">Recommended Action</div>
        <div className="flex items-center gap-2 flex-wrap mt-1">
          <span className="text-lg font-black" style={{ color: act.color }}>{act.label}</span>
          <span className="text-[12px] text-white/60">{ee.action_reason}</span>
        </div>
        <div className="mt-1 text-[10px] text-white/35">
          Ignore → Watch → Prepare → Small Position → Normal Entry → Conservative Entry
        </div>
      </div>

      {/* (4) Validation chips: R:R ≥ 2:1, room, liquidity */}
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        <Chip ok={ee.rr_ok} label={`R:R ${ee.reward_risk != null ? ee.reward_risk.toFixed(1) : "—"}:1`} need="≥ 2:1" />
        <Chip ok={ee.room_ok} label={`Room ${ee.room_atr != null ? ee.room_atr.toFixed(1) : "—"} ATR`} need="to target" />
        <Chip ok={ee.liquidity_ok} label="Liquidity" need={ee.spread_pct != null ? `spread ${ee.spread_pct}%` : "OI/vol"} />
      </div>

      {/* (2) Agreement tracker */}
      <div className="mt-2 rounded-lg bg-white/5 px-3 py-2 flex items-center gap-4 flex-wrap">
        <div className="text-[10px] uppercase tracking-wider text-white/45">Frozen Agreement</div>
        <span className="text-sm font-black" style={{ color: agreeColor }}>{ee.agreement}</span>
        {ee.confirm_delay_candles != null && (
          <span className="text-[12px] text-white/60">
            confirmed <b className="text-white/85">{ee.confirm_delay_candles}</b> candle
            {ee.confirm_delay_candles === 1 ? "" : "s"} after Early-Early
          </span>
        )}
        <span className="text-[10px] text-white/35">
          tracks whether the frozen engine later confirmed / rejected this side (logged for comparison)
        </span>
      </div>

      {/* evidence checks */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
        {ee.checks.map((c, i) => (
          <span key={i} title={c.detail} className="text-white/60">
            {c.name}:{" "}
            <span style={{ color: !c.available ? MUTED : c.passed ? GREEN : RED }}>
              {!c.available ? "n/a" : c.passed ? "✓" : "✕"}
            </span>
          </span>
        ))}
      </div>

      {/* plan (only when a valid Early-Early is live) */}
      {active && plan ? (
        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
          <Num label="Entry Zone" value={`${money(plan.entry_low)}–${money(plan.entry_high)}`} small />
          <Num label="Stop" value={money(plan.stop_loss)} color={RED} small />
          <Num label="Target 1 · 2R" value={money(plan.target1)} color={GREEN} small />
          <Num label="Target 2 · 3R" value={money(plan.target2)} color={GREEN} small />
        </div>
      ) : (
        <div className="mt-2 text-[12px] text-white/50">{ee.note}</div>
      )}
    </div>
  );
}

function Chip({ ok, label, need }: { ok: boolean; label: string; need: string }) {
  const c = ok ? GREEN : MUTED;
  return (
    <span
      className="rounded-md px-2 py-1 font-bold"
      style={{ background: `${c}14`, color: c, border: `1px solid ${c}44` }}
    >
      {ok ? "✓" : "•"} {label} <span className="text-white/35 font-normal">{need}</span>
    </span>
  );
}

// The Quick Scalp panel — a SEPARATE, advisory-only add-on for fast 10–20 point
// option scalps off exhaustion/bounce setups, with a dedicated fixed target,
// tight stop, breakeven and time-stop. Its headline feature is the LIVE EXIT
// alert: the moment a running scalp rolls over, a red EXIT NOW banner + klaxon.
function ScalpPanel({ sc }: { sc: ScalpSignal }) {
  const active = sc.active;
  const side = sc.side ?? "";
  const sideEmoji = side === "PE" ? "🐻 PE" : side === "CE" ? "🐂 CE" : "—";
  const act = ACTION_META[sc.recommended_action] ?? ACTION_META.IGNORE;
  const plan = sc.plan;
  const PURPLE = "#a78bfa";

  // The EXIT alert takes visual priority over everything else in the panel.
  const exitBar =
    sc.in_trade && (sc.exit_urgency === "EXIT" || sc.exit_urgency === "WATCH") ? (
      <div
        className="mt-3 rounded-xl border p-3"
        style={
          sc.exit_now
            ? { borderColor: RED, background: `${RED}22` }
            : { borderColor: `${AMBER}88`, background: `${AMBER}14` }
        }
      >
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className={`rounded-md px-2.5 py-1 text-sm font-black tracking-widest ${sc.exit_now ? "animate-pulse" : ""}`}
            style={
              sc.exit_now
                ? { background: RED, color: "#0b0e13" }
                : { background: `${AMBER}22`, color: AMBER, border: `1px solid ${AMBER}66` }
            }
          >
            {sc.exit_now ? "🚨 EXIT NOW" : "⚠ EXIT WATCH"}
          </span>
          <span className="text-[13px] font-bold" style={{ color: sc.exit_now ? RED : AMBER }}>
            {sc.exit_reason}
          </span>
        </div>
        <div className="mt-2 flex items-center gap-3 flex-wrap text-[12px]">
          {sc.live_premium != null && (
            <span className="text-white/70">
              Now <b className="text-white/90">{money(sc.live_premium)}</b>
            </span>
          )}
          {sc.live_points != null && (
            <span style={{ color: sc.live_points >= 0 ? GREEN : RED }}>
              {sc.live_points >= 0 ? "+" : ""}
              {sc.live_points.toFixed(1)} pts
            </span>
          )}
          {sc.exit_signals.map((s, i) => (
            <span key={i} className="rounded px-1.5 py-0.5 text-[11px]" style={{ background: "rgba(255,255,255,0.06)", color: "#e7ecf2" }}>
              {s}
            </span>
          ))}
        </div>
      </div>
    ) : null;

  return (
    <div
      className="rounded-2xl border p-4"
      style={{
        borderColor: sc.exit_now ? RED : active ? `${PURPLE}88` : "rgba(255,255,255,0.12)",
        background: active ? `${PURPLE}12` : "#11151c",
      }}
    >
      {/* header banner */}
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-2 flex-wrap">
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-black tracking-widest"
            style={{ background: `${PURPLE}22`, color: PURPLE, border: `1px solid ${PURPLE}66` }}
          >
            QUICK SCALP
          </span>
          <span className="text-2xl font-black leading-none" style={{ color: active ? PURPLE : MUTED }}>
            {active ? `⚡ SCALP ${side} · ${sc.setup}` : "QUICK SCALP · watching"}
          </span>
          <span
            className="rounded-md px-2 py-0.5 text-[10px] font-black"
            style={{ background: "#ff5c5c1f", color: RED, border: "1px solid #ff5c5c66" }}
          >
            ⚠ HIGHER RISK
          </span>
        </div>
        {active && side && (
          <span
            className="rounded-md px-2.5 py-1 text-sm font-black"
            style={{ background: `${PURPLE}1f`, color: PURPLE, border: `1px solid ${PURPLE}66` }}
          >
            {sideEmoji} {sc.option_symbol ?? ""}
          </span>
        )}
      </div>
      <div className="mt-1 text-[11px] text-white/50">
        Fast {sc.target_points != null ? Math.round(sc.target_points) : 10}-point option scalp off an exhaustion / bounce
        setup — fixed target, tight stop, breakeven-after-partial and a hard time-stop. Separate, advisory-only add-on;
        the frozen engine, Early Momentum and Early-Early are untouched. <b className="text-white/70">No automatic order.</b>
      </div>

      {/* LIVE EXIT alert (top priority once a scalp is running) */}
      {exitBar}

      {/* probability / conditions / risk / hold */}
      <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
        <Num label="Probability" value={`${Math.round(sc.probability)}%`} color={active ? PURPLE : AMBER} />
        <Num
          label="Conditions"
          value={`${sc.condition_count}/7`}
          color={sc.condition_count >= sc.condition_required ? GREEN : AMBER}
        />
        <Num label="Risk Level" value={sc.risk_level} color={RED} small />
        <Num label="Expected Hold" value={sc.hold_time || (active ? "quick (few candles)" : "—")} small />
      </div>

      {/* recommended action */}
      <div className="mt-2 rounded-xl border p-3" style={{ borderColor: `${act.color}55`, background: `${act.color}0f` }}>
        <div className="text-[10px] uppercase tracking-wider text-white/45">Recommended Action</div>
        <div className="flex items-center gap-2 flex-wrap mt-1">
          <span className="text-lg font-black" style={{ color: act.color }}>{act.label}</span>
          <span className="text-[12px] text-white/60">{sc.action_reason}</span>
        </div>
      </div>

      {/* validation chips */}
      <div className="mt-2 flex flex-wrap gap-2 text-[11px]">
        <Chip ok={sc.rr_ok} label={`R:R ${sc.reward_risk != null ? sc.reward_risk.toFixed(1) : "—"}:1`} need="tight stop" />
        <Chip ok={sc.liquidity_ok} label="Liquidity" need="OI/vol" />
      </div>

      {/* condition checks */}
      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[12px]">
        {sc.checks.map((c, i) => (
          <span key={i} title={c.detail} className="text-white/60">
            {c.name}:{" "}
            <span style={{ color: !c.available ? MUTED : c.passed ? GREEN : RED }}>
              {!c.available ? "n/a" : c.passed ? "✓" : "✕"}
            </span>
          </span>
        ))}
      </div>

      {/* scalp plan (fixed target + tight stop + breakeven + time-stop) */}
      {active && plan ? (
        <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-2">
          <Num label="Entry" value={money(sc.entry_hint)} small />
          <Num label="Stop" value={money(plan.stop_loss)} color={RED} small />
          <Num label={`Target · +${sc.target_points != null ? Math.round(sc.target_points) : 10}`} value={money(plan.target1)} color={GREEN} small />
          <Num label="Breakeven at" value={money(sc.breakeven_at)} color={AMBER} small />
          <Num label="Stretch T2" value={money(plan.target2)} color={GREEN} small />
          <Num label="Stretch T3" value={money(plan.target3)} color={GREEN} small />
          <Num label="Time-Stop" value={sc.time_stop_candles != null ? `${sc.time_stop_candles} candles` : "—"} small />
          <Num label="R:R" value={sc.reward_risk != null ? `${sc.reward_risk.toFixed(1)}:1` : "—"} small />
        </div>
      ) : (
        <div className="mt-2 text-[12px] text-white/50">{sc.note}</div>
      )}
    </div>
  );
}
