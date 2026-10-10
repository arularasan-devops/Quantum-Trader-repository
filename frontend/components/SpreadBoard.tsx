"use client";

import type { Snapshot } from "@/lib/types";

/** Premium-SELLING board — defined-risk credit spreads, PAPER ONLY.
 *
 * Deliberately leads with the honest numbers rather than the headline win rate:
 * the measured backtest is 81% frictionless but ~66% once the bid-ask is paid,
 * and it LOSES if carried overnight, which is why the engine is intraday-only.
 */
export default function SpreadBoard({ snap }: { snap: Snapshot | null }) {
  const cs = snap?.decision?.credit_spread ?? null;

  if (!cs || !cs.enabled) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="max-w-xl text-center space-y-3">
          <div className="text-lg font-semibold text-white/80">
            Premium-Selling engine is OFF
          </div>
          <p className="text-sm text-white/50 leading-relaxed">
            Sells defined-risk credit spreads instead of buying options, so time
            decay works for you. Enable with{" "}
            <code className="text-white/70">QT_CREDIT_SPREAD_ENABLED=true</code>.
          </p>
          <p className="text-xs text-rose-300/90 leading-relaxed">
            RESEARCH ONLY. Measured on 1,243 NIFTY sessions: 81.4% win
            frictionless, but 66% once the bid-ask is paid on all four legs — and
            it LOSES (PF 0.65) if held overnight. Prices are modelled, never
            validated against a real fill. Do not trade these proposals.
          </p>
        </div>
      </div>
    );
  }

  const stateColor =
    cs.state === "PROPOSE"
      ? "text-emerald-400"
      : cs.state === "CLOSE"
      ? "text-rose-400"
      : cs.state === "BLOCKED"
      ? "text-amber-400"
      : "text-white/50";

  return (
    <div className="h-full overflow-y-auto space-y-3">
      <div className="rounded-xl border-2 border-rose-500/50 bg-rose-500/10 p-3">
        <div className="text-sm font-bold text-rose-300">
          RESEARCH ONLY — DO NOT PLACE THESE TRADES
        </div>
        <div className="text-[11px] text-white/70 mt-1.5 leading-relaxed">
          This is <span className="font-semibold text-white/90">not a buy suggestion.</span>{" "}
          It shows what a premium-selling strategy <em>would</em> propose, so you can
          watch it build a track record. Do not place these legs manually with real
          money.
        </div>
        <ul className="text-[11px] text-white/60 mt-2 space-y-1 list-disc list-inside leading-relaxed">
          <li>
            Every premium is <span className="text-white/80">MODELLED</span>{" "}
            (Black-Scholes on realised volatility) — not a live option quote. It has
            never been tested against a real fill.
          </li>
          <li>
            Measured backtest: 81% win frictionless, but only{" "}
            <span className="text-white/80">66% (PF 1.19)</span> once the bid-ask is
            paid — thin enough that slightly worse real spreads make it{" "}
            <span className="text-rose-300">lose money</span>.
          </li>
          <li>
            Held overnight it LOSES (<span className="text-rose-300">PF 0.65</span>).
            Negative skew: many small wins, then one day takes back 10–20 of them.
          </li>
          <li>
            A high win rate here comes from the short strike&apos;s delta, not from a
            prediction. Judge it on profit factor, never on win rate.
          </li>
        </ul>
      </div>

      <div className="rounded-xl border border-white/10 bg-white/5 p-4">
        <div className="flex items-baseline justify-between">
          <div className={`text-2xl font-bold ${stateColor}`}>
            {cs.state === "PROPOSE" ? "WOULD OPEN" : cs.state}
            <span className="ml-2 text-xs font-normal text-white/40">
              (simulated — no order placed)
            </span>
          </div>
          <div className="text-sm text-white/70">
            {cs.structure?.replaceAll("_", " ") ?? "—"}
          </div>
        </div>
        {cs.reason && (
          <div className="text-xs text-white/60 mt-2">{cs.reason}</div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
        <Stat label="Credit collected" value={cs.net_credit} tone="good" />
        <Stat label="Max loss (capped)" value={cs.max_loss} tone="bad" />
        <Stat
          label="Win probability"
          value={cs.win_probability}
          suffix="%"
        />
        <Stat label="Credit ÷ risk" value={cs.reward_risk} />
        <Stat label="Breakeven low" value={cs.breakeven_low} />
        <Stat label="Breakeven high" value={cs.breakeven_high} />
        <Stat label="Stop at loss of" value={cs.stop_at} tone="bad" />
        <Stat
          label="Minutes to close"
          value={cs.minutes_to_close}
          suffix="m"
        />
      </div>

      {cs.legs.length > 0 && (
        <div className="rounded-xl border border-white/10 bg-white/5 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-white/5 text-white/50 text-xs">
              <tr>
                <th className="text-left px-3 py-2">Action</th>
                <th className="text-left px-3 py-2">Type</th>
                <th className="text-right px-3 py-2">Strike</th>
                <th className="text-right px-3 py-2">Premium</th>
                <th className="text-right px-3 py-2">Delta</th>
              </tr>
            </thead>
            <tbody>
              {cs.legs.map((lg, i) => (
                <tr key={i} className="border-t border-white/5">
                  <td
                    className={`px-3 py-2 font-semibold ${
                      lg.action === "SELL" ? "text-emerald-400" : "text-sky-400"
                    }`}
                  >
                    {lg.action}
                    {lg.action === "BUY" && (
                      <span className="ml-2 text-[10px] text-white/40">
                        hedge
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-white/70">{lg.option_type}</td>
                  <td className="px-3 py-2 text-right">{lg.strike}</td>
                  <td className="px-3 py-2 text-right">{lg.premium}</td>
                  <td className="px-3 py-2 text-right text-white/60">
                    {lg.delta}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="px-3 py-2 text-[11px] text-white/40 border-t border-white/5">
            Every short leg is hedged by a further-OTM long leg — the worst case is
            known before entry and can never exceed the max loss above.
            {cs.implied_vol != null && ` Priced at ${cs.implied_vol}% vol.`}
          </div>
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  suffix,
  tone,
}: {
  label: string;
  value: number | null | undefined;
  suffix?: string;
  tone?: "good" | "bad";
}) {
  const color =
    tone === "good" ? "text-emerald-400" : tone === "bad" ? "text-rose-400" : "text-white";
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/40">
        {label}
      </div>
      <div className={`text-lg font-semibold ${color}`}>
        {value == null ? "—" : value}
        {value != null && suffix ? suffix : ""}
      </div>
    </div>
  );
}
