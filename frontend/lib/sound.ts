// Lightweight alert tones for bot entry/exit — generated with the Web Audio
// API so no audio asset/network fetch is needed. Safe no-op on the server or
// when the browser blocks audio before a user gesture.

let ctx: AudioContext | null = null;

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
    return ctx;
  } catch {
    return null;
  }
}

function tone(freq: number, start: number, dur: number, gain = 0.18): void {
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

// Rising two-note chime for an ENTRY (bought).
export function playBuySound(): void {
  tone(660, 0, 0.14);
  tone(990, 0.13, 0.2);
}

// Falling two-note chime for an EXIT (sold/closed).
export function playExitSound(): void {
  tone(780, 0, 0.14);
  tone(520, 0.13, 0.24);
}

// Decision-state and Early Momentum tones live in lib/alerts.ts with the rest of
// the alert engine, so the scope gate ("Signal dashboard only") and the
// once-per-transition de-duplication apply to them too. Adding copies here would
// be a second, ungated way to make noise.
