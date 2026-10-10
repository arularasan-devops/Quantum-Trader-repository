"use client";
import { useEffect, useRef } from "react";

/**
 * Plays a short, upbeat, celebratory chime the moment the decision flips INTO a
 * BUY (an ascending C-major arpeggio with a bright bell timbre). It fires only
 * on the transition into BUY — and re-fires if a *new* BUY is recommended for a
 * different option — so it never nags on every tick.
 *
 * Synthesised with the Web Audio API (no audio file to ship). Browsers block
 * audio until the user has interacted with the page; the mute toggle button in
 * the top bar provides that gesture, after which chimes play normally.
 */
export function useBuySound(signal: string, optionSymbol: string | null, enabled: boolean) {
  const ctxRef = useRef<AudioContext | null>(null);
  // Remember what we last chimed for so we don't repeat on every snapshot tick.
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    const isBuy = signal === "BUY";
    const key = isBuy ? `BUY:${optionSymbol || ""}` : signal;

    if (!isBuy) {
      // Reset when we leave BUY so the next BUY chimes again.
      lastKeyRef.current = key;
      return;
    }
    if (!enabled) {
      lastKeyRef.current = key;
      return;
    }
    if (key === lastKeyRef.current) return; // already chimed for this BUY
    lastKeyRef.current = key;

    try {
      if (ctxRef.current === null) ctxRef.current = new AudioContext();
      const ctx = ctxRef.current;
      if (ctx.state === "suspended") void ctx.resume();
      playChime(ctx);
    } catch {
      /* audio unavailable / blocked — silently ignore, never break the UI */
    }
  }, [signal, optionSymbol, enabled]);
}

/**
 * Distinct EARLY-BUY alert for the *Early Momentum* engine — deliberately
 * different from the frozen BUY chime (which is a bright C-major bell
 * arpeggio) so the two are never confused by ear. This one is a punchy,
 * urgent two-pulse "radar" motif: a low sawtooth thump followed by two
 * rising square-wave blips. It signals "an early setup is forming — act
 * fast", not the calm confirmation of the frozen engine.
 */
export function useMomentumSound(active: boolean, optionSymbol: string | null, enabled: boolean) {
  const ctxRef = useRef<AudioContext | null>(null);
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    const key = active ? `EARLY:${optionSymbol || ""}` : "IDLE";
    if (!active) {
      lastKeyRef.current = key;
      return;
    }
    if (!enabled) {
      lastKeyRef.current = key;
      return;
    }
    if (key === lastKeyRef.current) return;
    lastKeyRef.current = key;
    try {
      if (ctxRef.current === null) ctxRef.current = new AudioContext();
      const ctx = ctxRef.current;
      if (ctx.state === "suspended") void ctx.resume();
      playMomentumChime(ctx);
    } catch {
      /* audio unavailable / blocked — silently ignore */
    }
  }, [active, optionSymbol, enabled]);
}

/**
 * URGENT EXIT alarm for the Quick Scalp Engine — deliberately the OPPOSITE of a
 * buy chime: a harsh, descending two-tone "klaxon" (falling square waves) that
 * unmistakably means "GET OUT — the move is rolling over". Fires once when a
 * running scalp flips into EXIT-NOW (not on every tick).
 */
export function useScalpExitSound(exitNow: boolean, optionSymbol: string | null, enabled: boolean) {
  const ctxRef = useRef<AudioContext | null>(null);
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    const key = exitNow ? `EXIT:${optionSymbol || ""}` : "IDLE";
    if (!exitNow) {
      lastKeyRef.current = key;
      return;
    }
    if (!enabled) {
      lastKeyRef.current = key;
      return;
    }
    if (key === lastKeyRef.current) return;
    lastKeyRef.current = key;
    try {
      if (ctxRef.current === null) ctxRef.current = new AudioContext();
      const ctx = ctxRef.current;
      if (ctx.state === "suspended") void ctx.resume();
      playExitAlarm(ctx);
    } catch {
      /* audio unavailable / blocked — silently ignore */
    }
  }, [exitNow, optionSymbol, enabled]);
}

/**
 * Flow Engine (Candle-Flow) sounds — a SINGLE hook driving both alerts so the
 * Flow tab is audible on its own:
 *   * BUY / SWITCH → a bright, quick two-blip "go" chime (distinct from the
 *     frozen bell and the momentum radar) meaning "green flow — ride it".
 *   * EXIT         → the same urgent descending klaxon used for scalp exits
 *     ("get out — the flow rolled over").
 * Fires only on a genuine state transition (keyed by state+side+leg), never on
 * every tick.
 */
export function useFlowSound(
  state: string,
  side: string | null,
  optionSymbol: string | null,
  enabled: boolean,
) {
  const ctxRef = useRef<AudioContext | null>(null);
  const lastKeyRef = useRef<string>("");

  useEffect(() => {
    const actionable = state === "BUY" || state === "SWITCH" || state === "EXIT";
    const key = actionable ? `${state}:${side || ""}:${optionSymbol || ""}` : `IDLE:${state}`;
    if (!actionable || !enabled) {
      lastKeyRef.current = key;
      return;
    }
    if (key === lastKeyRef.current) return;
    lastKeyRef.current = key;
    try {
      if (ctxRef.current === null) ctxRef.current = new AudioContext();
      const ctx = ctxRef.current;
      if (ctx.state === "suspended") void ctx.resume();
      if (state === "EXIT") playExitAlarm(ctx);
      else playFlowBuy(ctx);
    } catch {
      /* audio unavailable / blocked — silently ignore */
    }
  }, [state, side, optionSymbol, enabled]);
}

export function playFlowBuy(ctx: AudioContext) {
  const now = ctx.currentTime;
  const master = ctx.createGain();
  master.gain.value = 0.85;
  master.connect(ctx.destination);

  // Two quick rising sine blips (E5 → B5) with a bright triangle tick on top —
  // a crisp "green flow, go" motif distinct from the other engines.
  const blips = [659.25, 987.77];
  blips.forEach((freq, i) => {
    const t0 = now + i * 0.13;
    const dur = 0.18;
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, t0);
    osc.frequency.exponentialRampToValueAtTime(freq * 1.18, t0 + dur * 0.8);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.4, t0 + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    const tick = ctx.createOscillator();
    tick.type = "triangle";
    tick.frequency.setValueAtTime(freq * 2, t0);
    const tg = ctx.createGain();
    tg.gain.setValueAtTime(0.0001, t0);
    tg.gain.exponentialRampToValueAtTime(0.12, t0 + 0.01);
    tg.gain.exponentialRampToValueAtTime(0.0001, t0 + dur * 0.6);
    osc.connect(g).connect(master);
    tick.connect(tg).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.03);
    tick.start(t0);
    tick.stop(t0 + dur + 0.03);
  });
}

export function playExitAlarm(ctx: AudioContext) {
  const now = ctx.currentTime;
  const master = ctx.createGain();
  master.gain.value = 0.9;
  master.connect(ctx.destination);

  // Two descending klaxon blasts (high → low) — an unmistakable "danger" motif.
  const blasts = [
    { hi: 880, lo: 440 },
    { hi: 740, lo: 330 },
  ];
  blasts.forEach((b, i) => {
    const t0 = now + i * 0.34;
    const dur = 0.3;
    const osc = ctx.createOscillator();
    osc.type = "square";
    osc.frequency.setValueAtTime(b.hi, t0);
    osc.frequency.exponentialRampToValueAtTime(b.lo, t0 + dur);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.5, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.03);
  });
}

export function playMomentumChime(ctx: AudioContext) {
  const now = ctx.currentTime;
  const master = ctx.createGain();
  master.gain.value = 0.85;
  master.connect(ctx.destination);

  // 1) A short low "thump" (sawtooth) that sweeps up — an urgent lead-in.
  const sweep = ctx.createOscillator();
  sweep.type = "sawtooth";
  sweep.frequency.setValueAtTime(180, now);
  sweep.frequency.exponentialRampToValueAtTime(360, now + 0.14);
  const sg = ctx.createGain();
  sg.gain.setValueAtTime(0.0001, now);
  sg.gain.exponentialRampToValueAtTime(0.45, now + 0.02);
  sg.gain.exponentialRampToValueAtTime(0.0001, now + 0.18);
  sweep.connect(sg).connect(master);
  sweep.start(now);
  sweep.stop(now + 0.2);

  // 2) Two rising square-wave blips — the distinctive "momentum" signature.
  const blips = [440, 587.33]; // A4 → D5, a rising call
  blips.forEach((freq, i) => {
    const t0 = now + 0.18 + i * 0.16;
    const dur = 0.2;
    const osc = ctx.createOscillator();
    osc.type = "square";
    osc.frequency.setValueAtTime(freq, t0);
    // a quick pitch bump gives each blip a lively "chirp".
    osc.frequency.exponentialRampToValueAtTime(freq * 1.25, t0 + dur * 0.8);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.3, t0 + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    osc.connect(g).connect(master);
    osc.start(t0);
    osc.stop(t0 + dur + 0.03);
  });
}

export function playChime(ctx: AudioContext) {
  const now = ctx.currentTime;
  const master = ctx.createGain();
  master.gain.value = 0.9; // loud
  master.connect(ctx.destination);

  // Ascending C-major arpeggio: C5, E5, G5, C6 — bright and positive.
  const notes = [523.25, 659.25, 783.99, 1046.5];
  notes.forEach((freq, i) => {
    const t0 = now + i * 0.11;
    const dur = 0.55;

    const osc = ctx.createOscillator();
    osc.type = "triangle";
    osc.frequency.setValueAtTime(freq, t0);

    // A second detuned sine an octave up adds a bell-like sparkle.
    const shimmer = ctx.createOscillator();
    shimmer.type = "sine";
    shimmer.frequency.setValueAtTime(freq * 2, t0);

    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.6, t0 + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);

    const gs = ctx.createGain();
    gs.gain.setValueAtTime(0.0001, t0);
    gs.gain.exponentialRampToValueAtTime(0.18, t0 + 0.02);
    gs.gain.exponentialRampToValueAtTime(0.0001, t0 + dur * 0.7);

    osc.connect(g).connect(master);
    shimmer.connect(gs).connect(master);

    osc.start(t0);
    osc.stop(t0 + dur + 0.05);
    shimmer.start(t0);
    shimmer.stop(t0 + dur + 0.05);
  });

  // Final triumphant chord (C major) rings out under the arpeggio's last note.
  const chordAt = now + notes.length * 0.11;
  [523.25, 659.25, 783.99].forEach((freq) => {
    const osc = ctx.createOscillator();
    osc.type = "sine";
    osc.frequency.setValueAtTime(freq, chordAt);
    const g = ctx.createGain();
    g.gain.setValueAtTime(0.0001, chordAt);
    g.gain.exponentialRampToValueAtTime(0.35, chordAt + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, chordAt + 0.9);
    osc.connect(g).connect(master);
    osc.start(chordAt);
    osc.stop(chordAt + 1.0);
  });
}
