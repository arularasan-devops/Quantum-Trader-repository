import { Candle } from "./types";

const IST_OFFSET = 19800; // +5:30 in seconds

function istDay(unixSec: number): number {
  return Math.floor((unixSec + IST_OFFSET) / 86400);
}

/**
 * Today's High / Low of the option premium, derived from the option candle
 * series AND the current LIVE premium. "Today" = the IST calendar day of the
 * most recent candle. Folding in the live price makes the range expand in real
 * time as the price moves, instead of lagging behind the 1-minute candles.
 */
export function premiumHiLoToday(
  candles: Candle[] | null | undefined,
  live?: number | null
): { high: number | null; low: number | null } {
  let hi = -Infinity;
  let lo = Infinity;
  if (candles && candles.length > 0) {
    const lastDay = istDay(candles[candles.length - 1].time);
    for (const c of candles) {
      if (istDay(c.time) !== lastDay) continue;
      if (c.high > hi) hi = c.high;
      if (c.low < lo) lo = c.low;
    }
  }
  if (live != null && live > 0) {
    if (live > hi) hi = live;
    if (live < lo) lo = live;
  }
  return {
    high: hi === -Infinity ? null : hi,
    low: lo === Infinity ? null : lo,
  };
}

/**
 * Today's session Open / High / Low from a candle series, restricted to the IST
 * calendar day of the most recent candle. Used ONLY as a fallback when the
 * broker-authoritative day OHLC isn't in the snapshot yet — without the
 * today-only filter a 48h candle window yields a misleading 2-session High/Low
 * that won't match the broker app.
 */
export function sessionOHLCToday(
  candles: Candle[] | null | undefined,
  live?: number | null
): { open: number | null; high: number | null; low: number | null } {
  let open: number | null = null;
  let hi = -Infinity;
  let lo = Infinity;
  if (candles && candles.length > 0) {
    const lastDay = istDay(candles[candles.length - 1].time);
    for (const c of candles) {
      if (istDay(c.time) !== lastDay) continue;
      if (open === null) open = c.open;
      if (c.high > hi) hi = c.high;
      if (c.low < lo) lo = c.low;
    }
  }
  if (live != null && live > 0) {
    if (hi === -Infinity || live > hi) hi = live;
    if (lo === Infinity || live < lo) lo = live;
  }
  return {
    open,
    high: hi === -Infinity ? null : hi,
    low: lo === Infinity ? null : lo,
  };
}
