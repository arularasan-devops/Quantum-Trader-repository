// Audible alerts for new BUY signals and for real bot fills.
//
// Deliberately a single module-level engine rather than a hook inside the
// Signals screen, for three reasons the old in-screen chime got wrong:
//
//  * The screener poll it rode on was skipped while the browser tab was hidden,
//    which is exactly when a sound matters. This engine keeps polling.
//  * A row on the board is an opinion, not a trade. So a FILL (the bot actually
//    bought) gets its own, distinct alert sourced from /api/fills, because a
//    surfaced BUY can still be refused by the risk governor, the premium floor
//    or the concurrency cap.
//
// Browsers keep audio suspended until the page has had a real user gesture, so
// `blocked` is exposed and shown in the UI instead of the alert silently failing.
import { API_BASE, ScreenerRow } from "@/lib/api";

export type AlertKind = "signal" | "fill" | "decision" | "momentum";

export interface AlertEvent {
  kind: AlertKind;
  key: string;
  label: string;
  detail: string;
  at: number;
}

export interface AlertState {
  signalSound: boolean;
  fillSound: boolean;
  decisionSound: boolean;
  momentumSound: boolean;
  gate: number;
  blocked: boolean;
  /** True while the Signal dashboard is on screen — the only screen that sounds. */
  soundScope: boolean;
  last: AlertEvent | null;
  events: AlertEvent[];
}

const STORE_KEY = "qt_alerts_v2";
const POLL_MS = 4000;

let state: AlertState = {
  signalSound: true,
  fillSound: true,
  decisionSound: true,
  momentumSound: true,
  gate: 80,
  blocked: false,
  soundScope: false,
  last: null,
  events: [],
};

const listeners = new Set<(s: AlertState) => void>();
let started = false;
let timer: ReturnType<typeof setInterval> | null = null;
let ctx: AudioContext | null = null;
// Signals/fills already alerted on, so a poll every 4s cannot re-announce the
// same one. Primed on the first response: whatever is already open or already
// on the board when the page loads is history, not news.
const seenSignals = new Set<string>();
const seenFills = new Set<string>();
let primedSignals = false;
let primedFills = false;

function emit(): void {
  listeners.forEach((fn) => fn(state));
}

function patch(next: Partial<AlertState>): void {
  state = { ...state, ...next };
  emit();
}

function persist(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(
      STORE_KEY,
      JSON.stringify({
        signalSound: state.signalSound,
        fillSound: state.fillSound,
        decisionSound: state.decisionSound,
        momentumSound: state.momentumSound,
        gate: state.gate,
      }),
    );
  } catch {
    /* storage unavailable (private mode) — settings just won't persist */
  }
}

function restore(): void {
  if (typeof window === "undefined") return;
  try {
    const raw = window.localStorage.getItem(STORE_KEY);
    if (!raw) return;
    const saved = JSON.parse(raw) as Partial<AlertState>;
    state = {
      ...state,
      signalSound: saved.signalSound ?? true,
      fillSound: saved.fillSound ?? true,
      decisionSound: saved.decisionSound ?? true,
      momentumSound: saved.momentumSound ?? true,
      gate: typeof saved.gate === "number" ? saved.gate : 80,
    };
  } catch {
    /* ignore corrupt settings */
  }
}

function audio(): AudioContext | null {
  if (typeof window === "undefined") return null;
  try {
    if (!ctx) {
      const Ctor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctor) return null;
      ctx = new Ctor();
    }
    if (ctx.state === "suspended") void ctx.resume();
    if (state.blocked !== (ctx.state !== "running")) patch({ blocked: ctx.state !== "running" });
    return ctx;
  } catch {
    return null;
  }
}

function tone(freq: number, start: number, dur: number, gain: number): void {
  const ac = audio();
  if (!ac) return;
  const osc = ac.createOscillator();
  const g = ac.createGain();
  osc.type = "sine";
  osc.frequency.setValueAtTime(freq, ac.currentTime + start);
  g.gain.setValueAtTime(0.0001, ac.currentTime + start);
  g.gain.exponentialRampToValueAtTime(gain, ac.currentTime + start + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + start + dur);
  osc.connect(g);
  g.connect(ac.destination);
  osc.start(ac.currentTime + start);
  osc.stop(ac.currentTime + start + dur + 0.02);
}

// Four deliberately unmistakable voices. They are different facts, so telling
// them apart by ear — without looking at the screen — is the whole point.

/** New BUY signal: two rising notes. The only tradeable call. */
export function signalTone(): void {
  tone(660, 0, 0.14, 0.18);
  tone(990, 0.13, 0.2, 0.18);
}

/** The bot actually bought: three rising notes, louder — money moved. */
export function fillTone(): void {
  tone(660, 0, 0.12, 0.24);
  tone(880, 0.12, 0.12, 0.24);
  tone(1320, 0.24, 0.26, 0.24);
}

/** Decision state change (EXIT / HOLD / AVOID flip): low double knock. */
export function decisionTone(): void {
  tone(392, 0, 0.11, 0.16);
  tone(392, 0.16, 0.16, 0.16);
}

/** Early Momentum engine: fast high triple — advisory, not the production call. */
export function momentumTone(): void {
  tone(1245, 0, 0.07, 0.13);
  tone(1245, 0.09, 0.07, 0.13);
  tone(1660, 0.18, 0.14, 0.13);
}

function announce(ev: AlertEvent, play: () => void, enabled: boolean): void {
  // Audio only while the Signal dashboard is on screen; the event log still
  // records everything, so switching tabs loses the chime, not the history.
  if (enabled && state.soundScope) play();
  patch({ last: ev, events: [ev, ...state.events].slice(0, 20) });
}

/**
 * Mark the Signal dashboard as on/off screen. Called by that screen on mount and
 * unmount so no other tab makes noise.
 */
export function setSoundScope(active: boolean): void {
  if (state.soundScope === active) return;
  patch({ soundScope: active });
  if (active) audio();
}

// Last signal seen for the instrument shown on the MAIN dashboard, so the
// WAIT/AVOID -> BUY flip of the call card you are actually looking at sounds
// too. The screener poll above covers the whole board; this covers the one
// screen the user sits on, where a card can turn BUY between polls (and, for a
// name below the alert gate, without appearing in the board's BUY list at all).
const lastDashSignal = new Map<string, string>();
const lastDashPosition = new Map<string, string>();
const lastMomentum = new Map<string, string>();

/**
 * Report the main dashboard's current call. Sounds only on a real transition
 * INTO BUY — never on the first reading for an instrument (that would fire on
 * every page load and every instrument switch) and never twice for the same
 * unchanged BUY.
 */
export function noteDashboardSignal(
  instrument: string,
  display: string,
  signal: string,
  detail: string,
): void {
  const previous = lastDashSignal.get(instrument);
  lastDashSignal.set(instrument, signal);
  if (previous === undefined || previous === signal) return;
  if (signal !== "BUY") return;
  announce(
    {
      kind: "signal",
      key: `dash:${instrument}:${Date.now()}`,
      label: `BUY ${display}`,
      detail,
      at: Date.now(),
    },
    signalTone,
    state.signalSound,
  );
}

/**
 * Report the production engine's position-side state (HOLD / EXIT / AVOID / WAIT).
 * Sounds the DECISION tone on a real transition only, and never for BUY — a BUY
 * is the signal tone's job, so the two can never be confused by ear.
 */
export function noteDecisionState(
  instrument: string,
  display: string,
  signal: string,
  detail: string,
): void {
  const previous = lastDashPosition.get(instrument);
  lastDashPosition.set(instrument, signal);
  if (previous === undefined || previous === signal) return;
  if (signal === "BUY") return;
  announce(
    {
      kind: "decision",
      key: `state:${instrument}:${signal}:${Date.now()}`,
      label: `${signal} ${display}`,
      detail,
      at: Date.now(),
    },
    decisionTone,
    state.decisionSound,
  );
}

/**
 * Report the Early Momentum engine's call. Advisory engine, its own tone, and
 * only on a transition into an EARLY BUY.
 */
export function noteMomentumSignal(
  instrument: string,
  display: string,
  signal: string,
  detail: string,
): void {
  const previous = lastMomentum.get(instrument);
  lastMomentum.set(instrument, signal);
  if (previous === undefined || previous === signal) return;
  if (!signal.toUpperCase().includes("BUY")) return;
  announce(
    {
      kind: "momentum",
      key: `mom:${instrument}:${Date.now()}`,
      label: `MOMENTUM ${signal} ${display}`,
      detail,
      at: Date.now(),
    },
    momentumTone,
    state.momentumSound,
  );
}

async function poll(): Promise<void> {
  // NOTE: intentionally polls while the browser tab is hidden. The whole point
  // of a sound is that you are not looking at the screen.
  try {
    const res = await fetch(`${API_BASE}/api/screener`);
    const data = (await res.json()) as { rows?: ScreenerRow[] };
    const buys = (data.rows ?? []).filter((r) => r.is_buy && r.confidence >= state.gate);
    const fresh: ScreenerRow[] = [];
    const current = new Set<string>();
    buys.forEach((r) => {
      const key = `${r.symbol}:${r.option_type}:${r.strike}`;
      current.add(key);
      if (!seenSignals.has(key)) fresh.push(r);
    });
    seenSignals.forEach((k) => {
      if (!current.has(k)) seenSignals.delete(k);
    });
    current.forEach((k) => seenSignals.add(k));
    if (!primedSignals) {
      primedSignals = true;
    } else if (fresh.length > 0) {
      const r = fresh[0];
      announce(
        {
          kind: "signal",
          key: `${r.symbol}:${r.option_type}:${r.strike}`,
          label: `BUY ${r.display} ${r.option_type ?? ""} ${r.strike ?? ""}`.trim(),
          detail:
            `${r.confidence.toFixed(0)}% confidence` +
            (r.current_premium ? ` · premium ₹${r.current_premium.toFixed(1)}` : "") +
            (fresh.length > 1 ? ` · +${fresh.length - 1} more` : ""),
          at: Date.now(),
        },
        signalTone,
        state.signalSound,
      );
    }
  } catch {
    /* engine unreachable — keep the last state, try again next tick */
  }

  try {
    const res = await fetch(`${API_BASE}/api/fills`);
    const data = (await res.json()) as {
      open?: {
        id: string;
        book: string;
        display: string;
        symbol: string;
        side: string;
        entry: number | null;
        lots: number;
      }[];
    };
    const open = data.open ?? [];
    const ids = new Set(open.map((o) => o.id));
    seenFills.forEach((id) => {
      if (!ids.has(id)) seenFills.delete(id);
    });
    const newly = open.filter((o) => !seenFills.has(o.id));
    ids.forEach((id) => seenFills.add(id));
    if (!primedFills) {
      primedFills = true;
    } else if (newly.length > 0) {
      const f = newly[0];
      announce(
        {
          kind: "fill",
          key: f.id,
          label: `BOUGHT ${f.symbol}`,
          detail:
            `${f.book} · ${f.side} · ${f.lots} lot(s)` +
            (f.entry ? ` at ₹${f.entry.toFixed(2)}` : "") +
            (newly.length > 1 ? ` · +${newly.length - 1} more` : ""),
          at: Date.now(),
        },
        fillTone,
        state.fillSound,
      );
    }
  } catch {
    /* ignore */
  }
}

/** Start the single shared alert loop (idempotent). */
export function startAlerts(): void {
  if (started || typeof window === "undefined") return;
  started = true;
  restore();
  // Any real click/keypress is the gesture browsers require before audio may
  // play, so the context is resumed then and `blocked` clears itself.
  const unlock = () => {
    audio();
  };
  window.addEventListener("pointerdown", unlock);
  window.addEventListener("keydown", unlock);
  patch({ blocked: true });
  void poll();
  timer = setInterval(() => void poll(), POLL_MS);
}

export function stopAlerts(): void {
  if (timer) clearInterval(timer);
  timer = null;
  started = false;
}

export function subscribeAlerts(fn: (s: AlertState) => void): () => void {
  listeners.add(fn);
  fn(state);
  return () => listeners.delete(fn);
}

export function getAlertState(): AlertState {
  return state;
}

export function setSignalSound(on: boolean): void {
  patch({ signalSound: on });
  persist();
  if (on) audio();
}

export function setFillSound(on: boolean): void {
  patch({ fillSound: on });
  persist();
  if (on) audio();
}

export function setDecisionSound(on: boolean): void {
  patch({ decisionSound: on });
  persist();
  if (on) audio();
}

export function setMomentumSound(on: boolean): void {
  patch({ momentumSound: on });
  persist();
  if (on) audio();
}

export function setAlertGate(gate: number): void {
  patch({ gate });
  persist();
}

/** Play all four tones in order so each one can be learned by ear. */
export function testAlert(): void {
  audio();
  signalTone();
  window.setTimeout(fillTone, 700);
  window.setTimeout(decisionTone, 1500);
  window.setTimeout(momentumTone, 2200);
}
