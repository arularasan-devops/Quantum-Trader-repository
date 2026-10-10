"use client";
import { Snapshot } from "@/lib/types";

/**
 * Display-only "Expiry Opportunity" (zero-to-hero) panel.
 *
 * It REUSES the frozen engine's existing directional read (CE/PE) and
 * confidence to suggest a cheap far-OTM option for an expiry-day lottery play.
 * It adds NO new indicators or decision rules, and is explicitly excluded from
 * validation / backtest / performance. Clearly labelled High Risk / Speculative.
 */
const MONTHS: Record<string, number> = {
  JAN: 0, FEB: 1, MAR: 2, APR: 3, MAY: 4, JUN: 5,
  JUL: 6, AUG: 7, SEP: 8, OCT: 9, NOV: 10, DEC: 11,
};

function parseExpiry(symbol: string | null): Date | null {
  if (!symbol) return null;
  const m = symbol.toUpperCase().match(/(\d{1,2})([A-Z]{3})(\d{2})/);
  if (!m) return null;
  const day = parseInt(m[1], 10);
  const mon = MONTHS[m[2]];
  if (mon === undefined) return null;
  const year = 2000 + parseInt(m[3], 10);
  return new Date(year, mon, day);
}

function inferStep(strikes: number[]): number | null {
  const uniq = Array.from(new Set(strikes)).sort((a, b) => a - b);
  let step = Infinity;
  for (let i = 1; i < uniq.length; i++) step = Math.min(step, uniq[i] - uniq[i - 1]);
  return Number.isFinite(step) && step > 0 ? step : null;
}

export default function ExpiryOpportunityPanel({ snap }: { snap: Snapshot }) {
  const d = snap.decision;

  // Direction strictly from the frozen engine's existing outputs.
  let bullish: boolean | null = null;
  if (d.option_type === "CE") bullish = true;
  else if (d.option_type === "PE") bullish = false;
  else if (d.htf_trend && /UP|BULL/i.test(d.htf_trend)) bullish = true;
  else if (d.htf_trend && /DOWN|BEAR/i.test(d.htf_trend)) bullish = false;
  else if (d.buy_score !== d.sell_score) bullish = d.buy_score > d.sell_score;

  const side = bullish === null ? null : bullish ? "CE" : "PE";
  const atm = d.atm_strike;
  const step = inferStep(snap.watchlist.map((w) => w.strike));
  const otmStrike =
    atm != null && step != null && bullish !== null
      ? bullish ? atm + 3 * step : atm - 3 * step
      : null;

  const expiry = parseExpiry(d.recommended_option);
  let dte: number | null = null;
  if (expiry) {
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    dte = Math.round((expiry.getTime() - today.getTime()) / 86400000);
  }
  const isExpiryToday = dte === 0;

  const sideColor = side === "CE" ? "#19e57f" : side === "PE" ? "#ff4d4d" : "#8b97a5";

  const dteText = expiry
    ? isExpiryToday
      ? "EXPIRY TODAY — zero-to-hero window"
      : dte != null && dte > 0
        ? `${dte} day${dte === 1 ? "" : "s"} to expiry`
        : "Expiry passed / next contract"
    : "Expiry unknown";

  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.05] p-3 flex items-center gap-5">
      <div className="flex items-center gap-3 shrink-0">
        <div className="text-5xl font-extrabold leading-none" style={{ color: sideColor }}>{side ?? "—"}</div>
        <div>
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-bold text-amber-300">⚡ Expiry Opportunity · Zero-to-Hero</span>
            <span className="text-[9px] font-bold px-2 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/40">
              HIGH RISK · SPECULATIVE
            </span>
          </div>
          <div className="text-[11px] font-semibold mt-0.5" style={{ color: isExpiryToday ? "#f5b301" : "rgba(255,255,255,0.5)" }}>{dteText}</div>
        </div>
      </div>

      <div className="text-[12px] text-white/70 leading-tight flex-1 min-w-0 border-l border-white/8 pl-5">
        {side
          ? <>Buy a cheap OTM <b style={{ color: sideColor }}>{side}</b>{otmStrike != null ? <> near <b style={{ color: sideColor }}>{otmStrike}</b></> : null} for an expiry-day lottery play.</>
          : "Engine has no clear side right now — no expiry play."}
        <div className="text-white/40 mt-0.5">Confidence {Math.round(d.confidence)}% · direction taken from the live frozen engine.</div>
      </div>

      <div className="flex items-center gap-2 shrink-0 text-[11px]">
        <div className="rounded-lg border border-white/8 px-3 py-1.5 text-center">
          <div className="text-white/40 text-[9px] uppercase">ATM</div>
          <div className="text-white/85 font-bold tabular-nums">{atm ?? "—"}</div>
        </div>
        <div className="rounded-lg border border-white/8 px-3 py-1.5 text-center">
          <div className="text-white/40 text-[9px] uppercase">Suggested OTM</div>
          <div className="font-bold tabular-nums" style={{ color: sideColor }}>{otmStrike ?? "—"}</div>
        </div>
      </div>

      <div className="text-[10px] text-white/40 leading-snug max-w-[300px] shrink-0 border-l border-white/8 pl-5">
        Reuses the engine&apos;s direction only — no new indicators. Far-OTM expiry options usually expire worthless; risk only what you can lose. <b>Excluded from validation, backtest &amp; performance.</b>
      </div>
    </div>
  );
}
