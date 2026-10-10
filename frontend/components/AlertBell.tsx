"use client";
import { useEffect, useState } from "react";
import {
  AlertState,
  setAlertGate,
  setDecisionSound,
  setFillSound,
  setMomentumSound,
  setSignalSound,
  startAlerts,
  subscribeAlerts,
  testAlert,
} from "@/lib/alerts";

const GREEN = "#19e57f";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.55)";

/** Shared hook over the single alert engine, so every mount shows one truth. */
export function useAlerts(): AlertState {
  const [s, setS] = useState<AlertState | null>(null);
  useEffect(() => {
    startAlerts();
    return subscribeAlerts(setS);
  }, []);
  return (
    s ?? {
      signalSound: true,
      fillSound: true,
      decisionSound: true,
      momentumSound: true,
      gate: 80,
      blocked: false,
      soundScope: false,
      last: null,
      events: [],
    }
  );
}

/**
 * Alert controls, one switch per FACT with its own tone: a new BUY signal, a real
 * bot fill, a decision state change, and the Early Momentum engine.
 *
 * They are separate because they are separate facts — the board can show a BUY
 * that the risk governor then refuses, and Early Momentum is a different engine
 * entirely — so one shared "sound on" toggle with one shared tone made a signal,
 * a purchase and an advisory indistinguishable by ear.
 */
export default function AlertBell({ compact = false }: { compact?: boolean }) {
  const a = useAlerts();
  return (
    <div className="flex items-center gap-1.5">
      <button
        onClick={() => setSignalSound(!a.signalSound)}
        title={a.signalSound ? "Two rising notes on a new BUY signal — click to mute" : "New-BUY sound muted"}
        className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 hover:bg-white/5"
        style={{ color: a.signalSound ? GREEN : MUTED }}
      >
        {a.signalSound ? "🔊" : "🔇"} Signal
      </button>
      <button
        onClick={() => setFillSound(!a.fillSound)}
        title={
          a.fillSound
            ? "Three rising notes when the bot actually BUYS (a real fill) — click to mute"
            : "Bot-fill sound muted"
        }
        className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 hover:bg-white/5"
        style={{ color: a.fillSound ? GREEN : MUTED }}
      >
        {a.fillSound ? "🔊" : "🔇"} Bought
      </button>
      <button
        onClick={() => setDecisionSound(!a.decisionSound)}
        title={
          a.decisionSound
            ? "Low double knock when the decision changes state (EXIT / HOLD / AVOID) — click to mute"
            : "Decision state-change sound muted"
        }
        className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 hover:bg-white/5"
        style={{ color: a.decisionSound ? GREEN : MUTED }}
      >
        {a.decisionSound ? "🔊" : "🔇"} Decision
      </button>
      <button
        onClick={() => setMomentumSound(!a.momentumSound)}
        title={
          a.momentumSound
            ? "Fast high triple for the Early Momentum engine (advisory) — click to mute"
            : "Early Momentum sound muted"
        }
        className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 hover:bg-white/5"
        style={{ color: a.momentumSound ? GREEN : MUTED }}
      >
        {a.momentumSound ? "🔊" : "🔇"} Momentum
      </button>
      {!compact && (
        <>
          <div className="flex items-center gap-1">
            <span className="text-[9px] uppercase tracking-wide text-white/35 mr-1">Alert ≥</span>
            {[80, 85, 90, 95].map((g) => (
              <button
                key={g}
                onClick={() => setAlertGate(g)}
                className="px-2 py-1 rounded text-[11px] font-bold leading-none"
                style={
                  a.gate === g
                    ? { background: GREEN, color: "#07120c" }
                    : { background: "rgba(255,255,255,0.06)", color: MUTED }
                }
              >
                {g}
              </button>
            ))}
          </div>
        </>
      )}
      {/* The only way to confirm the browser will let sound through, and to learn
          the four tones apart. Plays all four in order. */}
      <button
        onClick={testAlert}
        title="Play all four tones in order: Signal, Bought, Decision, Momentum"
        className="px-2 py-1 rounded text-[11px] font-bold leading-none border border-white/15 text-white/60 hover:bg-white/5"
      >
        ♫ Tones
      </button>
      {a.blocked && (
        <span className="text-[10px] font-bold" style={{ color: AMBER }} title="Browsers block audio until you interact with the page">
          click the page once to enable sound
        </span>
      )}
    </div>
  );
}

/** Floating toast so an alert is visible as well as audible, on any tab. */
export function AlertToast() {
  const a = useAlerts();
  const [show, setShow] = useState(false);
  useEffect(() => {
    if (!a.last) return;
    setShow(true);
    const id = setTimeout(() => setShow(false), 9000);
    return () => clearTimeout(id);
  }, [a.last]);
  if (!a.last || !show) return null;
  const fill = a.last.kind === "fill";
  return (
    <div
      className="fixed bottom-5 right-5 z-[60] rounded-xl border px-4 py-3 shadow-2xl"
      style={{
        borderColor: fill ? GREEN : "rgba(255,255,255,0.18)",
        background: fill ? "rgba(25,229,127,0.12)" : "rgba(12,16,22,0.95)",
        backdropFilter: "blur(6px)",
      }}
    >
      <div className="text-sm font-black" style={{ color: fill ? GREEN : "#fff" }}>
        {fill ? "🟢 BOT BOUGHT" : "⚡ NEW BUY SIGNAL"}
      </div>
      <div className="text-xs font-bold text-white/85 mt-0.5">{a.last.label}</div>
      <div className="text-[11px] text-white/50">{a.last.detail}</div>
    </div>
  );
}
