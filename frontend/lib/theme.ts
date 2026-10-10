import { Signal } from "./types";

export interface SignalTheme {
  accent: string;
  accentDim: string;
  word: string; // giant word in the hero
  state: string; // market-state verb
  label: string;
}

export const SIGNAL_THEME: Record<Signal, SignalTheme> = {
  BUY: { accent: "#19e57f", accentDim: "rgba(25,229,127,0.14)", word: "BUY", state: "BUY NOW", label: "BUY" },
  EXIT: { accent: "#ff4d4d", accentDim: "rgba(255,77,77,0.14)", word: "SELL", state: "EXIT NOW", label: "EXIT" },
  HOLD: { accent: "#3aa0ff", accentDim: "rgba(58,160,255,0.14)", word: "HOLD", state: "HOLD", label: "HOLD" },
  WAIT: { accent: "#f4c025", accentDim: "rgba(244,192,37,0.14)", word: "WAIT", state: "WAIT", label: "WAIT" },
  NO_TRADE: { accent: "#f0883e", accentDim: "rgba(240,136,62,0.14)", word: "NO TRADE", state: "STAND ASIDE", label: "NO TRADE" },
  AVOID: { accent: "#8b93a7", accentDim: "rgba(139,147,167,0.14)", word: "AVOID", state: "AVOID", label: "AVOID" },
};

export function themeFor(signal: Signal): SignalTheme {
  return SIGNAL_THEME[signal] ?? SIGNAL_THEME.NO_TRADE;
}
