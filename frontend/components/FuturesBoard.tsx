"use client";

import { useCallback, useEffect, useState } from "react";

import {
  getFuturesReport,
  setFuturesSettings,
  type FuturesOpenPosition,
  type FuturesReport,
  type FuturesToolSettings,
  type FuturesTradeRow,
} from "@/lib/api";
import type { Snapshot } from "@/lib/types";

/** FUTURES paper tool — a SEPARATE self-driving bot, run alongside the option
 * engine.
 *
 * Everything here is in INDEX POINTS: one point is `lot_size` rupees, there is no
 * theta and no IV, and a bearish view is a SHORT rather than a long put. It has
 * its own capital pot, sizes itself from the stop distance (a futures loss is
 * unbounded, so the stop IS the risk control), squares off before the bell, and
 * charges itself slippage and brokerage.
 *
 * It is hard-locked to paper and has no live order route. The board leads with
 * that and with profit factor, because a leveraged bot can win most of its
 * trades and still lose money.
 */
export default function FuturesBoard({ snap }: { snap: Snapshot | null }) {
  const fut = snap?.decision?.futures ?? null;
  const [cfg, setCfg] = useState<FuturesToolSettings | null>(null);
  const [rep, setRep] = useState<FuturesReport | null>(null);
  const [rows, setRows] = useState<FuturesTradeRow[]>([]);
  const [open, setOpen] = useState<FuturesOpenPosition[]>([]);
  const [cap, setCap] = useState<{ pot: number; deployed_margin: number; free: number } | null>(
    null
  );
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const r = await getFuturesReport(200);
      setCfg(r.settings);
      setRep(r.report);
      setRows(r.trades ?? []);
      setOpen(r.open_positions ?? []);
      setCap(r.capital ?? null);
    } catch {
      /* board still renders from the live snapshot */
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [load]);

  const patch = async (opts: Partial<FuturesToolSettings>) => {
    setSaving(true);
    try {
      const next = await setFuturesSettings(opts);
      setCfg(next.futures);
    } finally {
      setSaving(false);
    }
  };

  const toggle = async () => {
    if (!cfg) return;
    if (!cfg.enabled) {
      const ok = window.confirm(
        "Start the FUTURES paper bot?\n\n" +
          "It runs on its own and decides everything itself: LONG or SHORT, its own " +
          "stop and targets in index points, its own lot size from its own capital pot.\n\n" +
          "Three things to know before you switch it on:\n\n" +
          "1. PAPER ONLY. It has no live order route at all - it cannot place an order " +
          "even if you wanted it to. Nothing it does has been validated against a real " +
          "futures fill.\n\n" +
          "2. UNPROVEN. It has no track record yet. Judge it on profit factor after " +
          "20-30 trades, not on the first few.\n\n" +
          "3. A futures loss is UNBOUNDED, unlike an option premium. That is why it " +
          "sizes from the stop and will refuse a trade whose single lot risks more than " +
          "its budget - including refusing NIFTY entirely on a small pot.\n\n" +
          "Its trades appear in the Journal tagged FUT so you can compare them against " +
          "your option trades from the same market."
      );
      if (!ok) return;
    }
    await patch({ enabled: !cfg.enabled });
  };

  const stateColor =
    fut?.state === "ENTERED"
      ? "text-emerald-400"
      : fut?.state === "HOLDING"
      ? "text-sky-400"
      : fut?.state === "CLOSED"
      ? "text-amber-300"
      : fut?.state === "BLOCKED"
      ? "text-amber-400"
      : "text-white/50";

  const pf = rep?.profit_factor ?? null;

  return (
    <div className="h-full overflow-y-auto space-y-3">
      {/* --- honest header ------------------------------------------------- */}
      <div className="rounded-xl border-2 border-amber-500/40 bg-amber-500/10 p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-bold text-amber-200">
            FUTURES PAPER BOT — PAPER ONLY, NO LIVE ORDER ROUTE
          </div>
          <button
            onClick={toggle}
            disabled={saving || !cfg}
            className={`px-3 py-1.5 rounded-lg text-xs font-semibold shrink-0 ${
              cfg?.enabled
                ? "bg-emerald-500/20 text-emerald-300 border border-emerald-500/40"
                : "bg-white/10 text-white/70 border border-white/20"
            } disabled:opacity-50`}
          >
            {cfg?.enabled ? "RUNNING · click to stop" : "STOPPED · click to start"}
          </button>
        </div>
        <div className="text-[11px] text-white/70 mt-1.5 leading-relaxed">
          A <span className="font-semibold text-white/90">separate tool</span> from
          your options engine — its own capital, its own rules, its own scoreboard.
          It trades the futures contract in{" "}
          <span className="font-semibold text-white/90">index points</span>, so there
          is no premium, no theta and no IV crush, and a downtrend is traded as a
          SHORT instead of through a decaying put.
        </div>
        <ul className="text-[11px] text-white/60 mt-2 space-y-1 list-disc list-inside leading-relaxed">
          <li>
            It <span className="text-white/80">cannot place a real order</span> —
            there is no live route into this tool, by design. It exists to build a
            track record you can judge.
          </li>
          <li>
            A futures loss is <span className="text-rose-300">unbounded</span>, unlike
            the premium you pay for an option. It therefore sizes from the stop
            distance and refuses any trade whose single lot risks more than{" "}
            {cfg ? `${cfg.risk_per_trade_pct}%` : "its budget"} of its pot.
          </li>
          <li>
            Fills are modelled at the candle close plus slippage, and brokerage is
            charged on every round trip. Real fills will differ.
          </li>
          <li>
            Judge it on <span className="text-white/80">profit factor</span>, not win
            rate — on a leveraged instrument a high win rate with a few large losses
            is the normal way to lose money.
          </li>
        </ul>
      </div>

      {/* --- live board --------------------------------------------------- */}
      <div className="rounded-xl border border-white/10 bg-white/5 p-4">
        <div className="flex items-baseline justify-between">
          <div className={`text-2xl font-bold ${stateColor}`}>
            {fut?.state === "ENTERED"
              ? `OPENED ${fut.side}`
              : fut?.state === "HOLDING"
              ? `HOLDING ${fut.side}`
              : fut?.state ?? "OFF"}
            <span className="ml-2 text-xs font-normal text-white/40">
              (paper — no order placed)
            </span>
          </div>
          <div className="text-sm text-white/60">
            {snap?.instrument ?? ""} FUT
            {fut?.lot_size ? ` · 1 lot = ${fut.lot_size} × 1 pt` : ""}
          </div>
        </div>
        {fut?.reason && (
          <div className="text-xs text-white/60 mt-2">{fut.reason}</div>
        )}
        {fut?.sizing_note && (
          <div className="text-[11px] text-white/40 mt-1">{fut.sizing_note}</div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Stat label="Entry" value={fut?.entry} />
        <Stat label="Price now" value={fut?.price} />
        <Stat label="Stop (points)" value={fut?.stop} tone="bad" />
        <Stat label="Risk from here" value={fut?.risk_rupees} prefix="₹" tone="bad" />
        <Stat label="Target 1" value={fut?.target1} tone="good" />
        <Stat label="Target 2" value={fut?.target2} tone="good" />
        <Stat label="Target 3" value={fut?.target3} tone="good" />
        <Stat label="Locked floor" value={fut?.locked_floor} />
        <Stat label="Lots" value={fut?.lots} />
        <Stat label="Levels hit" text={fut?.levels_hit ?? "—"} />
        <Stat
          label="Open points"
          value={fut?.open_points}
          tone={(fut?.open_points ?? 0) >= 0 ? "good" : "bad"}
        />
        <Stat
          label="Open P&L"
          value={fut?.open_rupees}
          prefix="₹"
          tone={(fut?.open_rupees ?? 0) >= 0 ? "good" : "bad"}
        />
      </div>

      <div className="text-[11px] text-white/40 px-1">
        Once Target 1 is paid the floor locks at{" "}
        {cfg ? `${cfg.t1_lock_r}R plus the full round-trip cost` : "a fraction of R plus costs"} —
        not at your entry price, because exiting at entry on futures still pays
        brokerage, STT/CTT and slippage and is therefore a guaranteed loss. Floor moves
        again at Target 2, and past Target 3 it keeps ratcheting so a trend is not
        capped — it exits after the move rolls back one step. It cannot exit at the
        exact top; one step is always given back. The position is squared off before
        the bell and never held overnight, because a gap on a leveraged position cannot
        be managed by a stop.
      </div>

      {/* --- risk governor -------------------------------------------------- */}
      {rep?.guard && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-4">
          <div className="text-sm font-semibold text-white/80 mb-2">
            Risk governor — what it is refusing to trade right now
            <span className="ml-2 text-[10px] font-normal text-white/40">
              survives a backend restart · resets on a new trading day
            </span>
          </div>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            <Stat
              label="Day P&L (futures)"
              value={rep.guard.day_pnl}
              prefix="₹"
              tone={rep.guard.day_pnl >= 0 ? "good" : "bad"}
            />
            <Stat
              label="Daily loss cap"
              value={rep.guard.daily_loss_cap}
              prefix="₹"
            />
            <Stat
              label="Trades today"
              text={
                Object.keys(rep.guard.trades_today).length === 0
                  ? "none"
                  : Object.entries(rep.guard.trades_today)
                      .map(([k, v]) => `${k} ${v}`)
                      .join(" · ")
              }
            />
            <Stat
              label="Standing down"
              text={
                Object.keys(rep.guard.standing_down).length === 0
                  ? "nothing"
                  : Object.entries(rep.guard.standing_down)
                      .map(([k, m]) => `${k} ${m}m`)
                      .join(" · ")
              }
              tone={
                Object.keys(rep.guard.standing_down).length === 0 ? undefined : "bad"
              }
            />
          </div>
          <div className="text-[11px] text-white/40 mt-2 leading-relaxed">
            After an exit it waits{" "}
            {cfg ? Math.round(cfg.reentry_cooldown_sec / 60) : "—"} min before touching
            the same instrument, will not flip to the opposite side for{" "}
            {cfg ? Math.round(cfg.flip_block_sec / 60) : "—"} min after being stopped,
            and stands down for {cfg ? Math.round(cfg.stand_down_sec / 60) : "—"} min
            after {cfg?.max_consecutive_losses ?? "—"} losses in a row. These exist
            because the measured session showed six stop-outs in two hours, flipping
            side each time, paying the round-trip cost every lap.
          </div>
        </div>
      )}

      {/* --- what it is holding right now, across instruments -------------- */}
      {cfg?.enabled && (
        <div className="rounded-xl border border-white/10 bg-white/5 p-4">
          <div className="flex items-baseline justify-between">
            <div className="text-sm font-semibold text-white/80">
              Holding right now
              <span className="ml-2 text-[10px] font-normal text-white/40">
                all instruments — not just the one selected above
              </span>
            </div>
            {cap && (
              <div className="text-[11px] text-white/50">
                pot ₹{cap.pot.toLocaleString("en-IN")} · margin used ₹
                {cap.deployed_margin.toLocaleString("en-IN")} · free ₹
                {cap.free.toLocaleString("en-IN")}
              </div>
            )}
          </div>
          {open.length === 0 ? (
            <div className="text-xs text-white/50 mt-2">
              Nothing open. It only enters when its own filters agree — a trend, enough
              ATR to place a stop outside the noise, and a lot it can both risk and
              margin.
            </div>
          ) : (
            <table className="w-full text-sm mt-2">
              <thead className="text-white/50 text-xs">
                <tr>
                  <th className="text-left py-1">Instrument</th>
                  <th className="text-left py-1">Side</th>
                  <th className="text-right py-1">Entry</th>
                  <th className="text-right py-1">Stop</th>
                  <th className="text-right py-1">T1 / T2 / T3 (1R/2R/3R)</th>
                  <th className="text-right py-1">Lots</th>
                  <th className="text-right py-1">Risk from here</th>
                  <th className="text-left py-1 pl-4">Hit</th>
                </tr>
              </thead>
              <tbody>
                {open.map((p) => (
                  <tr key={p.instrument} className="border-t border-white/5">
                    <td className="py-1.5 text-white/80">{p.instrument} FUT</td>
                    <td
                      className={`py-1.5 font-semibold ${
                        p.side === "LONG" ? "text-emerald-400" : "text-rose-400"
                      }`}
                    >
                      {p.side}
                    </td>
                    <td className="py-1.5 text-right">{p.entry}</td>
                    <td className="py-1.5 text-right text-rose-300">
                      {p.locked_floor ?? p.stop}
                      {p.locked_floor != null && (
                        <span className="ml-1 text-[9px] text-white/40">locked</span>
                      )}
                    </td>
                    <td className="py-1.5 text-right text-emerald-300/80 text-xs">
                      {p.target1} / {p.target2} / {p.target3}
                    </td>
                    <td className="py-1.5 text-right">{p.lots}</td>
                    <td className="py-1.5 text-right text-rose-300">
                      ₹{p.risk_rupees.toLocaleString("en-IN")}
                    </td>
                    <td className="py-1.5 pl-4 text-white/60">{p.levels_hit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* --- its own scoreboard ------------------------------------------- */}
      <div className="rounded-xl border border-white/10 bg-white/5 p-4">
        <div className="text-sm font-semibold text-white/80 mb-2">
          Its own track record
          <span className="ml-2 text-[10px] font-normal text-white/40">
            paper fills · separate from your options scoreboard
          </span>
        </div>
        {!rep || rep.trades === 0 ? (
          <div className="text-xs text-white/50">
            No trades yet — the numbers stay empty until it has actually traded.
            Nothing here is estimated or back-filled.
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              <Stat label="Trades" value={rep.trades} />
              <Stat label="Win rate" value={rep.win_rate} suffix="%" />
              <Stat
                label="Profit factor"
                value={pf}
                tone={pf == null ? undefined : pf >= 1 ? "good" : "bad"}
              />
              <Stat
                label="Net points"
                value={rep.net_points}
                tone={rep.net_points >= 0 ? "good" : "bad"}
              />
              <Stat
                label="Net P&L"
                value={rep.net_rupees}
                prefix="₹"
                tone={rep.net_rupees >= 0 ? "good" : "bad"}
              />
            </div>
            <div className="text-[11px] text-white/40 mt-2">
              {rep.trades < 20
                ? `Only ${rep.trades} trade(s) — too few to mean anything. Wait for 20–30 before judging it.`
                : pf != null && pf < 1
                ? "Profit factor below 1.00 — on this evidence the tool loses money."
                : "Profit factor above 1.00 on paper fills. Real fills will be worse than modelled ones."}
            </div>
          </>
        )}
      </div>

      {rows.length > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/5 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-white/50 text-xs">
              <tr>
                <th className="text-left px-3 py-2">Instrument</th>
                <th className="text-left px-3 py-2">Side</th>
                <th className="text-right px-3 py-2">Entry</th>
                <th className="text-right px-3 py-2">Exit</th>
                <th className="text-right px-3 py-2">Lots</th>
                <th className="text-right px-3 py-2">Net pts</th>
                <th className="text-right px-3 py-2">R</th>
                <th className="text-right px-3 py-2">Net ₹</th>
                {/* What the trade actually tied up, and what it returned on that
                    — a bare rupee figure says nothing about the money used. */}
                <th className="text-right px-3 py-2">Capital used</th>
                <th className="text-right px-3 py-2">Risk ₹</th>
                <th className="text-right px-3 py-2">Costs</th>
                <th className="text-right px-3 py-2">Return</th>
                <th className="text-left px-3 py-2">Why it closed</th>
              </tr>
            </thead>
            <tbody>
              {rows.slice(0, 50).map((r, i) => (
                <tr key={i} className="border-t border-white/5">
                  <td className="px-3 py-2 text-white/70">{r.instrument}</td>
                  <td
                    className={`px-3 py-2 font-semibold ${
                      r.side === "LONG" ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {r.side}
                  </td>
                  <td className="px-3 py-2 text-right">{r.entry}</td>
                  <td className="px-3 py-2 text-right">{r.exit}</td>
                  <td className="px-3 py-2 text-right text-white/60">
                    {r.lots}
                    <span className="text-white/25"> × {r.lot_size}</span>
                  </td>
                  <td
                    className={`px-3 py-2 text-right ${
                      r.net_points >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {r.net_points > 0 ? "+" : ""}
                    {r.net_points}
                  </td>
                  <td className="px-3 py-2 text-right text-white/60">
                    {r.r_multiple == null ? "—" : `${r.r_multiple}R`}
                  </td>
                  <td
                    className={`px-3 py-2 text-right ${
                      r.net_rupees >= 0 ? "text-emerald-400" : "text-rose-400"
                    }`}
                  >
                    {r.net_rupees > 0 ? "+" : ""}
                    {r.net_rupees}
                  </td>
                  <td className="px-3 py-2 text-right text-white/60 tabular-nums">
                    {r.capital_used == null
                      ? "—"
                      : `₹${Math.round(r.capital_used).toLocaleString("en-IN")}`}
                  </td>
                  <td className="px-3 py-2 text-right text-white/50 tabular-nums">
                    {r.risk_rupees == null
                      ? "—"
                      : `₹${Math.round(r.risk_rupees).toLocaleString("en-IN")}`}
                  </td>
                  <td className="px-3 py-2 text-right text-white/50 tabular-nums">
                    {r.cost_rupees == null
                      ? "—"
                      : `₹${Math.round(r.cost_rupees).toLocaleString("en-IN")}`}
                  </td>
                  <td
                    className={`px-3 py-2 text-right tabular-nums ${
                      (r.return_on_capital_pct ?? 0) >= 0
                        ? "text-emerald-400/80"
                        : "text-rose-400/80"
                    }`}
                  >
                    {r.return_on_capital_pct == null
                      ? "—"
                      : `${r.return_on_capital_pct > 0 ? "+" : ""}${r.return_on_capital_pct}%`}
                  </td>
                  <td className="px-3 py-2 text-white/50 text-xs">{r.exit_reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-2 text-[11px] text-white/40 border-t border-white/5">
            Net points are after slippage and brokerage. <b>Capital used is the MARGIN
            blocked</b>, not the contract&apos;s notional value — a Crude lot is ~₹7.9L of oil
            carried on ~₹95k — and <b>Return</b> is that net measured on the margin, which is
            what makes a ₹1,626 win comparable across instruments. The same trades are
            mirrored into the Journal tagged FUT, and the CSV download carries these columns
            too. Rows closed before this was recorded show —.
          </div>
        </div>
      )}

      {/* --- controls ----------------------------------------------------- */}
      {cfg && (
        <details className="rounded-xl border border-white/10 bg-white/5 p-3">
          <summary className="text-sm font-semibold text-white/70 cursor-pointer">
            How it decides &amp; sizes (editable, applied at runtime)
          </summary>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            <Num
              label="Its own capital (₹)"
              value={cfg.capital}
              step={10000}
              onChange={(v) => patch({ capital: v })}
              hint="Separate pot — never competes with the options bot."
            />
            <Num
              label="Risk per trade (%)"
              value={cfg.risk_per_trade_pct}
              step={0.25}
              onChange={(v) => patch({ risk_per_trade_pct: v })}
              hint="What the stop costs if hit. Lots are derived from this."
            />
            <Num
              label="Max lots"
              value={cfg.paper_lots}
              step={1}
              onChange={(v) => patch({ paper_lots: v })}
              hint="Ceiling only. The risk budget decides the lots below this; 0 = no ceiling."
            />
            <Num
              label="Max open instruments"
              value={cfg.max_concurrent}
              step={1}
              onChange={(v) => patch({ max_concurrent: v })}
            />
            <Num
              label="Stop = ATR ×"
              value={cfg.stop_atr}
              step={0.1}
              onChange={(v) => patch({ stop_atr: v })}
              hint="Stop distance in points, also anchored to structure."
            />
            <Num
              label="Target 1 (R)"
              value={cfg.t1_r}
              step={0.25}
              onChange={(v) => patch({ t1_r: v })}
            />
            <Num
              label="Target 2 (R)"
              value={cfg.t2_r}
              step={0.25}
              onChange={(v) => patch({ t2_r: v })}
            />
            <Num
              label="Target 3 (R)"
              value={cfg.t3_r}
              step={0.5}
              onChange={(v) => patch({ t3_r: v })}
            />
            <Num
              label="T1 lock (R)"
              value={cfg.t1_lock_r}
              step={0.1}
              onChange={(v) => patch({ t1_lock_r: v })}
              hint="Locked in once T1 pays, plus costs. 0 = the old break-even, which loses the cost."
            />
            <Num
              label="Max cost ÷ risk (%)"
              value={cfg.max_cost_to_risk_pct}
              step={1}
              onChange={(v) => patch({ max_cost_to_risk_pct: v })}
              hint="Refuses the trade above this. A stop that costs a fifth of R cannot be won back."
            />
            <Num
              label="Re-entry cooldown (min)"
              value={Math.round(cfg.reentry_cooldown_sec / 60)}
              step={1}
              onChange={(v) => patch({ reentry_cooldown_sec: v * 60 })}
              hint="Same instrument, after any exit."
            />
            <Num
              label="No side-flip for (min)"
              value={Math.round(cfg.flip_block_sec / 60)}
              step={1}
              onChange={(v) => patch({ flip_block_sec: v * 60 })}
              hint="After a stop it will not immediately take the opposite side."
            />
            <Num
              label="Losses before stand-down"
              value={cfg.max_consecutive_losses}
              step={1}
              onChange={(v) => patch({ max_consecutive_losses: v })}
              hint="Consecutive losses on one instrument."
            />
            <Num
              label="Stand-down (min)"
              value={Math.round(cfg.stand_down_sec / 60)}
              step={5}
              onChange={(v) => patch({ stand_down_sec: v * 60 })}
            />
            <Num
              label="Daily loss cap (₹)"
              value={cfg.max_daily_loss}
              step={1000}
              onChange={(v) => patch({ max_daily_loss: v })}
              hint="Stops the whole futures book for the day."
            />
            <Num
              label="Max trades/day/instrument"
              value={cfg.max_trades_per_day}
              step={1}
              onChange={(v) => patch({ max_trades_per_day: v })}
              hint="21 of 27 trades were one instrument in the measured session."
            />
            <Num
              label="Past-T3 step (R)"
              value={cfg.beyond_t3_step_atr}
              step={0.1}
              onChange={(v) => patch({ beyond_t3_step_atr: v })}
              hint="Giveback before it exits a runner. Smaller = exits sooner."
            />
            <Num
              label="Min ATR (% of price)"
              value={cfg.min_atr_pct}
              step={0.01}
              onChange={(v) => patch({ min_atr_pct: v })}
              hint="Dead-market filter. A % works on every contract; the old points version passed Crude and refused every stock future."
            />
            <Num
              label="Min ATR (points, legacy)"
              value={cfg.min_atr_points}
              step={1}
              onChange={(v) => patch({ min_atr_points: v })}
              hint="Absolute override. 0 = off, which is the default — it means a different thing on every instrument."
            />
            <Num
              label="Post-T1 trail (R)"
              value={cfg.post_t1_trail_r}
              step={0.1}
              onChange={(v) => patch({ post_t1_trail_r: v })}
              hint="Past T1 the floor follows the peak by this much. 0 = park it at the T1 lock, which booked every winner at ~0.5R."
            />
            <Num
              label="Min ADX"
              value={cfg.min_adx}
              step={1}
              onChange={(v) => patch({ min_adx: v })}
              hint="Needs a trend; it does not trade chop."
            />
            <Num
              label="Slippage (points/side)"
              value={cfg.slippage_points}
              step={0.5}
              onChange={(v) => patch({ slippage_points: v })}
              hint="Charged on entry and exit. Do not set this to 0."
            />
          </div>
          {/* Sizing mode. Kept as an explicit switch with the consequence spelled
              out, because it is the one control here that raises risk per trade. */}
          <label className="mt-3 flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={cfg.size_by_margin}
              onChange={(e) => patch({ size_by_margin: e.target.checked })}
              className="mt-0.5"
            />
            <span className="text-[11px] leading-4 text-white/55">
              Size to the free MARGIN instead of the risk budget
              <span className="block text-white/35">
                Off (recommended): lots come from{" "}
                {cfg.risk_per_trade_pct}% of ₹{cfg.capital.toLocaleString("en-IN")}, so the
                stop costs that much whatever it takes. On: it buys every lot the margin
                can carry — on Crude that is ~5 lots against the 2 the risk rule allows,
                i.e. roughly 5× the rupee loss when the stop hits. Same profit per point
                either way; you are choosing the bet size.
              </span>
            </span>
          </label>
          <div className="text-[11px] text-white/40 mt-3 leading-relaxed">
            Changes apply immediately, no restart. They are lost on a backend
            restart — set <code className="text-white/60">QT_FUTURES_PAPER_ENABLED</code>{" "}
            in the backend <code className="text-white/60">.env</code> only if you want
            the tool to start by itself every time. There is deliberately no setting
            anywhere that gives this tool a live order route.
          </div>
        </details>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  text,
  prefix,
  suffix,
  tone,
}: {
  label: string;
  value?: number | null;
  text?: string;
  prefix?: string;
  suffix?: string;
  tone?: "good" | "bad";
}) {
  const color =
    tone === "good" ? "text-emerald-400" : tone === "bad" ? "text-rose-400" : "text-white";
  const shown =
    text != null ? text : value == null ? "—" : `${prefix ?? ""}${value}${suffix ?? ""}`;
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <div className={`text-lg font-semibold ${color}`}>{shown}</div>
    </div>
  );
}

function Num({
  label,
  value,
  step,
  hint,
  onChange,
}: {
  label: string;
  value: number;
  step: number;
  hint?: string;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => setDraft(String(value)), [value]);
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide text-white/40">{label}</div>
      <input
        type="number"
        step={step}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const v = Number(draft);
          if (Number.isFinite(v) && v !== value) onChange(v);
          else setDraft(String(value));
        }}
        className="mt-1 w-full rounded-lg bg-black/30 border border-white/15 px-2 py-1 text-sm text-white"
      />
      {hint && <div className="text-[10px] text-white/35 mt-1">{hint}</div>}
    </div>
  );
}
