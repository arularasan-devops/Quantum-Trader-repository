"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { getScreener, ScreenerRow } from "../lib/api";
import { playMomentumChime } from "../lib/useBuySound";

const GREEN = "#19e57f";
const ORANGE = "#ff8a3d";
const AMBER = "#ffb020";
const BLUE = "#3aa0ff";
const MUTED = "#8b97a5";

function inr(v: number | null | undefined, d = 1): string {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toLocaleString("en-IN", { minimumFractionDigits: d, maximumFractionDigits: d });
}

/**
 * Momentum screener — scans EVERY watched instrument and lists the ones the
 * (parallel, advisory) Early Momentum engine currently rates at EARLY BUY
 * (Stage 3), plus the ones one step away (Stage 2 · structure forming). This is
 * the momentum counterpart to the frozen-confirmation Signals screen. Read-only
 * — advisory, you place every trade manually; the frozen engine is untouched.
 */
export default function MomentumScreener({ onPick }: { onPick: (symbol: string) => void }) {
  const [rows, setRows] = useState<ScreenerRow[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [soundOn, setSoundOn] = useState(true);
  const audioRef = useRef<AudioContext | null>(null);
  // symbols already chimed for, so the distinct EARLY sound fires only on a NEW
  // early buy — never on every poll.
  const seenRef = useRef<Set<string>>(new Set());
  const primedRef = useRef(false);

  useEffect(() => {
    let alive = true;
    const load = async () => {
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

  const enabled = useMemo(() => rows.filter((r) => r.em_enabled), [rows]);
  const early = useMemo(
    () => enabled.filter((r) => r.em_active).sort((a, b) => b.em_score - a.em_score),
    [enabled]
  );
  const forming = useMemo(
    () => enabled.filter((r) => !r.em_active && r.em_stage === 2).sort((a, b) => b.em_score - a.em_score),
    [enabled]
  );
  const engineOff = loaded && enabled.length === 0;

  // Distinct EARLY-BUY alert when a NEW instrument reaches Stage 3 — a different
  // sound from the frozen BUY chime so the two are never confused by ear.
  useEffect(() => {
    const current = new Set(early.map((r) => `${r.symbol}:${r.em_side}:${r.em_option ?? ""}`));
    let isNew = false;
    current.forEach((k) => {
      if (!seenRef.current.has(k)) isNew = true;
    });
    seenRef.current = current;
    if (!primedRef.current) {
      primedRef.current = true; // first fill — remember without chiming
      return;
    }
    if (isNew && soundOn) {
      try {
        if (audioRef.current === null) audioRef.current = new AudioContext();
        const ctx = audioRef.current;
        if (ctx.state === "suspended") void ctx.resume();
        playMomentumChime(ctx);
      } catch {
        /* audio blocked/unavailable — ignore */
      }
    }
  }, [early, soundOn]);

  const sideColor = (s: string | null) => (s === "PE" ? ORANGE : GREEN);

  return (
    <div className="flex-1 min-h-0 overflow-auto rounded-2xl border border-white/10 bg-[#0b0f14] p-4">
      <div className="mb-4 flex items-start justify-between gap-3">
        <div>
          <div className="text-lg font-black tracking-wide text-white">⚡ EARLY MOMENTUM — ALL WATCHED INSTRUMENTS</div>
          <div className="text-[11px] text-white/45">
            Every watched instrument the Early Momentum engine rates at <b className="text-white/70">EARLY BUY</b> (Stage 3),
            plus ones forming (Stage 2). Advisory · you place trades manually. Click a row to open it in a new tab.
          </div>
        </div>
        <button
          onClick={() => setSoundOn((s) => !s)}
          title={soundOn ? "EARLY BUY sound ON (distinct from frozen) — click to mute" : "EARLY BUY sound OFF — click to unmute"}
          className="shrink-0 px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 hover:bg-white/5"
          style={{ color: soundOn ? GREEN : MUTED }}
        >
          {soundOn ? "🔊 EARLY" : "🔇 EARLY"}
        </button>
      </div>

      {engineOff && (
        <div className="rounded-xl border border-white/10 bg-white/[0.02] p-6 text-center text-white/60 text-sm">
          Early Momentum engine is off. Set <code className="text-white/80">QT_EARLY_MOMENTUM_ENABLED=true</code> and restart
          the backend to scan for early setups here.
        </div>
      )}

      {!engineOff && (
        <>
          <div className="flex items-center gap-6 mb-3 text-sm">
            <div>
              <span className="text-3xl font-black tabular-nums" style={{ color: early.length ? GREEN : MUTED }}>
                {early.length}
              </span>
              <span className="text-white/45 ml-2 text-xs uppercase tracking-wide">EARLY BUY now</span>
            </div>
            <div className="text-white/40 text-xs">
              <span className="font-bold text-white/70">{forming.length}</span> forming · scanned{" "}
              <span className="font-bold text-white/70">{enabled.length}</span> watched
              {!loaded && " · loading…"}
            </div>
          </div>

          {early.length === 0 ? (
            <div className="rounded-xl border border-white/10 bg-white/[0.02] p-8 text-center">
              <div className="text-2xl font-black text-white/70">No EARLY BUY right now</div>
              <div className="text-sm text-white/40 mt-2">
                No watched instrument is at Stage 3 yet. Forming setups (Stage 2) are listed below as they build.
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="text-[10px] uppercase tracking-wide text-white/40 border-b border-white/10">
                    <th className="text-left py-2 px-2">Instrument</th>
                    <th className="text-left py-2 px-2">Side</th>
                    <th className="text-right py-2 px-2">Premium</th>
                    <th className="text-right py-2 px-2">Entry Zone</th>
                    <th className="text-right py-2 px-2">Stop</th>
                    <th className="text-right py-2 px-2">T1</th>
                    <th className="text-right py-2 px-2">T2</th>
                    <th className="text-right py-2 px-2">T3</th>
                    <th className="text-right py-2 px-2">Score</th>
                    <th className="text-right py-2 px-2"></th>
                  </tr>
                </thead>
                <tbody>
                  {early.map((r) => {
                    const c = sideColor(r.em_side);
                    return (
                      <tr
                        key={r.symbol}
                        onClick={() => onPick(r.symbol)}
                        className="border-b border-white/5 hover:bg-white/[0.04] cursor-pointer"
                      >
                        <td className="py-3 px-2">
                          <div className="text-base font-black text-white leading-tight">{r.display}</div>
                          {r.em_option && <div className="text-[11px] font-bold text-white/45">{r.em_option}</div>}
                        </td>
                        <td className="py-3 px-2">
                          <span className="text-sm font-black px-2 py-1 rounded" style={{ background: `${c}1a`, color: c }}>
                            {r.em_side === "PE" ? "🐻 EARLY PE" : "🐂 EARLY CE"}
                          </span>
                        </td>
                        <td className="py-3 px-2 text-right">
                          <span className="text-lg font-black tabular-nums" style={{ color: AMBER }}>₹{inr(r.em_entry)}</span>
                        </td>
                        <td className="py-3 px-2 text-right">
                          <span className="text-base font-black tabular-nums" style={{ color: GREEN }}>
                            {inr(r.em_entry_low)}–{inr(r.em_entry_high)}
                          </span>
                        </td>
                        <td className="py-3 px-2 text-right">
                          <span className="text-base font-black tabular-nums" style={{ color: "#ff5c5c" }}>{inr(r.em_stop)}</span>
                        </td>
                        <td className="py-3 px-2 text-right"><span className="text-base font-black tabular-nums" style={{ color: GREEN }}>{inr(r.em_target1)}</span></td>
                        <td className="py-3 px-2 text-right"><span className="text-base font-black tabular-nums text-white/90">{inr(r.em_target2)}</span></td>
                        <td className="py-3 px-2 text-right"><span className="text-base font-black tabular-nums text-white/90">{inr(r.em_target3)}</span></td>
                        <td className="py-3 px-2 text-right">
                          <span className="text-2xl font-black tabular-nums" style={{ color: r.em_score >= 80 ? GREEN : AMBER }}>
                            {Math.round(r.em_score)}
                          </span>
                        </td>
                        <td className="py-3 px-2 text-right">
                          <span
                            className="text-[11px] font-black px-2.5 py-1 rounded whitespace-nowrap"
                            style={{ background: `${GREEN}22`, color: GREEN, border: `1px solid ${GREEN}55` }}
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

          {forming.length > 0 && (
            <div className="mt-5">
              <div className="text-[11px] uppercase tracking-wide text-white/40 mb-2">Forming — Stage 2 · structure confirmed</div>
              <div className="flex flex-wrap gap-2">
                {forming.map((r) => (
                  <button
                    key={r.symbol}
                    onClick={() => onPick(r.symbol)}
                    className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-left hover:bg-white/[0.06]"
                  >
                    <div className="text-sm font-bold text-white/85">{r.display}</div>
                    <div className="text-[11px]" style={{ color: r.em_side === "PE" ? ORANGE : BLUE }}>
                      {r.em_side ?? "—"} · score {Math.round(r.em_score)} · waiting for Early Buy
                    </div>
                  </button>
                ))}
              </div>
            </div>
          )}
        </>
      )}

      <div className="mt-4 text-[11px] text-white/35">
        Early Momentum is a separate, higher-risk advisory engine. It never changes the frozen Decision engine and no order
        is ever placed automatically.
      </div>
    </div>
  );
}
