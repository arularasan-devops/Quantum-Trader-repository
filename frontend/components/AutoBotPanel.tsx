"use client";
import { useCallback, useEffect, useState } from "react";
import { AccountRiskStatus, AutoBuyStatus, AutoTradeConfig, EntryGateSettings, engageKillSwitch, getKillSwitch, KillSwitchState, releaseKillSwitch, getEngineSettings, getEntryGates, getRiskGuard, setAutoBuy, setAutoTrade, setEntryGates, setPointExits } from "@/lib/api";
import { InstrumentOption } from "@/lib/types";

/**
 * Auto-Buy Bot control — configures the PAPER auto-trader (simulated fills only;
 * never places a live order). Enters on the validated gated signal, sizes lots
 * from capital + risk%, and rides the winner with a ratcheting trailing stop.
 * A daily profit goal / loss cap halt new trades. Honest by design: it cannot
 * guarantee a daily profit — losing days are normal.
 */
export default function AutoBotPanel({ instruments = [] }: { instruments?: InstrumentOption[] }) {
  const [cfg, setCfg] = useState<AutoTradeConfig | null>(null);
  const [saving, setSaving] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [autoBuy, setAutoBuyState] = useState<AutoBuyStatus | null>(null);
  const [gates, setGates] = useState<EntryGateSettings | null>(null);
  const [guard, setGuard] = useState<AccountRiskStatus | null>(null);

  useEffect(() => {
    getEngineSettings()
      .then((s) => setCfg(s.auto_trade))
      .catch(() => {});
    getEntryGates()
      .then(setGates)
      .catch(() => {});
    const pullGuard = () => getRiskGuard().then(setGuard).catch(() => {});
    pullGuard();
    const t = setInterval(pullGuard, 15000);
    return () => clearInterval(t);
  }, []);

  const toggleIgnition = async () => {
    if (!gates) return;
    if (!gates.ignition_entry_enabled) {
      const ok = window.confirm(
        "Turn ON the IGNITION entry?\n\nIt enters on the FIRST strong expansion candle instead of waiting for momentum to build, so it buys earlier in the move.\n\nBUT the 60,000-candle replay measured it at profit factor 0.75-0.81 on CRUDEOIL and an unstable 0.92-1.07 on NIFTY - it LOST money in testing and made the overall system worse.\n\nRecommended for PAPER observation only. Entries it creates are tagged IGNITION on the board so you can compare them."
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await setEntryGates({
        ignition_entry_enabled: !gates.ignition_entry_enabled,
      });
      setGates(next);
    } finally {
      setSaving(false);
    }
  };

  const toggleReversal = async () => {
    if (!gates) return;
    if (!gates.reversal_entry_enabled) {
      const ok = window.confirm(
        "Turn ON the REVERSAL (bottom/top) entry?\n\nIt buys the TURN at a support or resistance extreme - hammer/engulfing at the level, stretched RSI, close to the swing low - instead of waiting for the trend to confirm.\n\nBUT the replay measured it at profit factor 0.90 on NIFTY - it LOST money in testing. Buying a bottom means the move has not confirmed yet, and you pay option decay while waiting to be proven right.\n\nRecommended for PAPER observation only. Entries it creates are tagged REVERSAL on the board."
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await setEntryGates({
        reversal_entry_enabled: !gates.reversal_entry_enabled,
      });
      setGates(next);
    } finally {
      setSaving(false);
    }
  };

  const toggleSupertrend = async () => {
    if (!gates) return;
    if (!gates.supertrend_entry_enabled) {
      const ok = window.confirm(
        "Turn ON the SUPERTREND FLIP entry?\n\nThis is the BUY/SELL marker from a TradingView Supertrend chart - it fires on the candle the ATR trailing band flips.\n\nTwo measured findings you should know first:\n\n1. It LOST money out-of-sample: profit factor 0.79-0.85 on NIFTY and 0.83-0.85 on CRUDEOIL, and it pulled the whole system down.\n\n2. It is NOT earlier than your normal entry. Measured from the swing low, the flip captured only 20-26% of the move, versus 30-32% for the pullback entry - because price must travel ~3x ATR before the band can flip at all.\n\nRecommended for PAPER observation only. Entries are tagged SUPERTREND on the board."
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const next = await setEntryGates({
        supertrend_entry_enabled: !gates.supertrend_entry_enabled,
      });
      setGates(next);
    } finally {
      setSaving(false);
    }
  };

  const patchGates = async (opts: Partial<EntryGateSettings>) => {
    setSaving(true);
    try {
      setGates(await setEntryGates(opts));
    } finally {
      setSaving(false);
    }
  };

  const patch = async (opts: Parameters<typeof setAutoTrade>[0]) => {
    setSaving(true);
    try {
      const next = await setAutoTrade(opts);
      setCfg(next);
    } finally {
      setSaving(false);
    }
  };

  const toggleAutoBuy = async (turnOn: boolean) => {
    if (turnOn) {
      const ok = window.confirm(
        "Turn ON LIVE Auto-Buy?\n\nThe bot will place REAL orders on your Angel account, sizing each trade from your available balance (bigger size on higher-confidence signals). Real money, real losses. One order can never exceed your available cash.\n\nRequires QT_TRADE_MODE=live + a connected Angel session."
      );
      if (!ok) return;
    }
    setSaving(true);
    try {
      const st = await setAutoBuy(turnOn);
      setAutoBuyState(st);
      const s = await getEngineSettings();
      setCfg(s.auto_trade);
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return <KillSwitchControl />;

  const liveArmed = cfg.mode === "live";
  const liveFeed = cfg.trade_mode?.toLowerCase() === "live";
  const oneClickOn = cfg.enabled && cfg.allow_live && !!cfg.use_live_balance;

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4 mb-4">
      <KillSwitchControl />
      {/* ── ONE-CLICK LIVE AUTO-BUY ─────────────────────────────────────── */}
      <div
        className="rounded-xl border p-4 mb-3"
        style={{
          borderColor: oneClickOn ? "#19e57f55" : "rgba(255,255,255,0.12)",
          background: oneClickOn ? "rgba(25,229,127,0.06)" : "rgba(255,255,255,0.02)",
        }}
      >
        <div className="flex items-center gap-3">
          <div className="min-w-0">
            <div className="text-sm font-bold text-white">Live Auto-Buy</div>
            <div className="text-[11px] text-white/45 leading-4 mt-0.5">
              One switch. The signal picks the trade &amp; confidence; the bot sizes it from your
              balance and buys automatically. You decide nothing else.
            </div>
          </div>
          <button
            onClick={() => toggleAutoBuy(!oneClickOn)}
            disabled={saving}
            className="ml-auto shrink-0 px-5 py-2.5 rounded-xl text-sm font-extrabold border"
            style={{
              borderColor: oneClickOn ? "#19e57f" : "rgba(255,255,255,0.2)",
              color: oneClickOn ? "#04110a" : "#e6e9f0",
              background: oneClickOn ? "#19e57f" : "rgba(255,255,255,0.04)",
            }}
          >
            {oneClickOn ? "AUTO-BUY ON" : "AUTO-BUY OFF"}
          </button>
        </div>
        {(oneClickOn || autoBuy) && (
          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px]">
            {autoBuy?.available_cash != null && (
              <span className="text-white/60">
                Balance <span className="font-bold text-white">₹{autoBuy.available_cash.toLocaleString("en-IN")}</span>
              </span>
            )}
            <span className="text-white/45">Sizing: confidence-scaled · buys first signal ≥ {cfg.min_confidence}%</span>
            {oneClickOn && !liveArmed && (
              <span className="text-amber-300 font-semibold">Not live yet — set QT_TRADE_MODE=live (paper for now)</span>
            )}
            {oneClickOn && liveArmed && (
              <span className="text-red-300 font-bold">LIVE — placing REAL orders</span>
            )}
          </div>
        )}
        {autoBuy?.note && <div className="mt-1 text-[10px] text-white/35">{autoBuy.note}</div>}
      </div>

      <button
        onClick={() => setShowAdvanced((s) => !s)}
        className="text-[11px] text-white/40 hover:text-white/70 mb-2"
      >
        {showAdvanced ? "▾ Hide advanced settings" : "▸ Advanced settings (optional)"}
      </button>

      {!showAdvanced ? null : (
      <>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-xs uppercase tracking-wide text-white/45 font-bold">Auto-Buy Bot</span>
        {liveArmed ? (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 font-bold">LIVE · REAL ORDERS</span>
        ) : (
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-400/15 text-amber-300 font-bold">PAPER · no live orders</span>
        )}
        <button
          onClick={() => patch({ enabled: !cfg.enabled })}
          disabled={saving}
          className="ml-auto px-3 py-1.5 rounded-lg text-xs font-bold border"
          style={{
            borderColor: cfg.enabled ? "#19e57f66" : "rgba(255,255,255,0.15)",
            color: cfg.enabled ? "#19e57f" : "#8b93a7",
            background: cfg.enabled ? "rgba(25,229,127,0.10)" : "transparent",
          }}
        >
          {cfg.enabled ? "BOT ON" : "BOT OFF"}
        </button>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <NumField label="Capital (₹)" value={cfg.capital} step={5000} min={1000} onCommit={(v) => patch({ capital: v })} />
        <NumField label="Risk / trade (%)" value={cfg.risk_per_trade_pct} step={0.5} min={0.1} onCommit={(v) => patch({ risk_per_trade_pct: v })} />
        <NumField label="Daily goal (₹, 0=off)" value={cfg.daily_profit_target} step={1000} min={0} onCommit={(v) => patch({ daily_profit_target: v })} />
        <NumField label="Daily loss cap (₹)" value={cfg.max_daily_loss} step={1000} min={0} onCommit={(v) => patch({ max_daily_loss: v })} />
        <NumField label="Trail stop (%)" value={cfg.trail_pct} step={1} min={2} onCommit={(v) => patch({ trail_pct: v })} />
        <NumField label="Max lots cap" value={cfg.max_lots} step={1} min={1} onCommit={(v) => patch({ max_lots: v })} />
        <NumField label="Max concurrent instruments" value={cfg.max_concurrent} step={1} min={1} onCommit={(v) => patch({ max_concurrent: v })} />
      </div>

      <div className="flex flex-wrap gap-4 mt-3">
        <Toggle label="Safe sizing (risk-based lots)" on={cfg.safe_sizing} onClick={() => patch({ safe_sizing: !cfg.safe_sizing })} />
        <Toggle label="Ride winner: T1/T2/T3 target-lock (never give back a hit target)" on={cfg.ride_trail} onClick={() => patch({ ride_trail: !cfg.ride_trail })} />
        <Toggle label="Momentum-trigger entry (enter as momentum turns up)" on={cfg.momentum_entry} onClick={() => patch({ momentum_entry: !cfg.momentum_entry })} />
        <Toggle label="No-chase (skip already-extended moves)" on={cfg.no_chase} onClick={() => patch({ no_chase: !cfg.no_chase })} />
        <Toggle label="Pre-T1 peak trail (exit the high if it rolls over before T1)" on={cfg.pre_t1_trail} onClick={() => patch({ pre_t1_trail: !cfg.pre_t1_trail })} />
      </div>

      {/* IGNITION entry — enters on the first expansion candle rather than after
          momentum has accumulated. Measured PF 0.75-1.07 (it LOST in replay), so
          it is presented as unproven rather than as an improvement. */}
      {gates && (
        <div className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <div className="flex items-center gap-3">
            <Toggle
              label="Ignition entry — buy the FIRST strong candle (not the peak)"
              on={gates.ignition_entry_enabled}
              onClick={toggleIgnition}
            />
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-bold">
              UNPROVEN · LOST IN BACKTEST
            </span>
          </div>
          <div className="text-[11px] text-white/55 mt-2 leading-relaxed">
            Enters on the first expansion candle (body ≥{gates.ignition_body_factor}× the
            recent average + a {gates.ignition_volume_factor}× volume surge, breaking out of a
            compressed base, agreeing with the 15m bias) instead of waiting for momentum to
            build near the top of the run.{" "}
            <span className="text-amber-300/90">
              Replay on 60,000 candles: PF 0.75–0.81 on CRUDEOIL, 0.92–1.07 on NIFTY, and it
              dragged the whole system down (NIFTY 1.081 → 1.043).
            </span>{" "}
            Use it for paper observation — its entries are tagged{" "}
            <code className="text-white/70">IGNITION</code> on the board.
          </div>
        </div>
      )}

      {/* REVERSAL entry — buys the bottom/top at a level instead of waiting for
          the trend to confirm. Measured NIFTY PF ~0.90 (it LOST in replay), so it
          is presented as unproven rather than as an improvement. */}
      {gates && (
        <div className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <div className="flex items-center gap-3">
            <Toggle
              label="Reversal entry — buy the BOTTOM at a level (counter-trend)"
              on={gates.reversal_entry_enabled}
              onClick={toggleReversal}
            />
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-bold">
              UNPROVEN · LOST IN BACKTEST
            </span>
          </div>
          <div className="text-[11px] text-white/55 mt-2 leading-relaxed">
            Buys the turn at a support/resistance extreme — a hammer or engulfing candle
            at the level, RSI at/below {gates.reversal_rsi_oversold}, price within{" "}
            {gates.reversal_max_atr_from_swing}× ATR of the swing low — instead of waiting
            for the trend to confirm.{" "}
            <span className="text-amber-300/90">
              Replay measured NIFTY PF ~0.90 — it lost money.
            </span>{" "}
            At a real bottom nothing has confirmed yet, and option decay runs against you
            while the level is retested. Paper observation only — entries are tagged{" "}
            <code className="text-white/70">REVERSAL</code> on the board.
          </div>
        </div>
      )}

      {/* SUPERTREND FLIP entry — the TradingView BUY/SELL marker. Measured
          out-of-sample PF 0.79-0.85 (LOSING) and, against the whole point of it,
          it entered LATER off the swing low than the pullback entry. Shown as
          unproven, not as an improvement. */}
      {gates && (
        <div className="mt-3 rounded-lg border border-amber-400/25 bg-amber-400/5 p-3">
          <div className="flex items-center gap-3">
            <Toggle
              label="Supertrend flip entry — the TradingView BUY/SELL marker"
              on={gates.supertrend_entry_enabled}
              onClick={toggleSupertrend}
            />
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-rose-500/20 text-rose-300 font-bold">
              UNPROVEN · LOST IN BACKTEST
            </span>
          </div>
          <div className="text-[11px] text-white/55 mt-2 leading-relaxed">
            Fires on the candle the ATR trailing band flips ({gates.supertrend_period}-period,{" "}
            {gates.supertrend_multiplier}× ATR) — the arrow you see on a Supertrend chart.{" "}
            <span className="text-amber-300/90">
              Walk-forward measured PF 0.79–0.85 on NIFTY and 0.83–0.85 on CRUDEOIL — it lost
              money, and it dragged the overall system down.
            </span>{" "}
            It is also <b>not earlier</b> than your normal entry: measured from the swing low it
            captured only 20–26% of the move versus 30–32% for the pullback entry, because price
            has to travel about 3× ATR before the band can flip at all. Paper observation only —
            entries are tagged <code className="text-white/70">SUPERTREND</code> on the board.
          </div>
        </div>
      )}

      {/* Past-T3 ratchet — lets a runner keep running instead of being capped at T3. */}
      {gates && (
        <div className="mt-3 rounded-lg border border-emerald-400/25 bg-emerald-400/5 p-3">
          <div className="flex items-center gap-3">
            <Toggle
              label="Keep riding past Target 3 (ratchet the lock upward)"
              on={gates.beyond_t3_ratchet}
              onClick={() => patchGates({ beyond_t3_ratchet: !gates.beyond_t3_ratchet })}
            />
            <NumField
              label="Giveback % of premium"
              value={gates.beyond_t3_step_pct}
              step={0.5}
              min={0.5}
              onCommit={(v) => patchGates({ beyond_t3_step_pct: v })}
            />
          </div>
          <div className="text-[11px] text-white/55 mt-2 leading-relaxed">
            Applies <b>past Target 3 only</b> — T1, T2 and the pre-T1 trail are unchanged. Once T3
            is reached the locked floor keeps climbing with the move and the exit fires on the first
            tick that gives back {gates.beyond_t3_step_pct}% of the premium, so a big run is no
            longer capped at T3. The step is a <b>percentage</b>, not fixed points, because a fixed
            5 points is inside the noise on a ₹300 option and far too loose on a ₹40 one.{" "}
            <span className="text-white/40">
              No trail can exit exactly at the top — one step is always given back. A smaller % keeps
              more of the peak but is stopped out by smaller dips.
            </span>
          </div>
        </div>
      )}

      {/* Per-instrument capital + lots overrides */}
      <PerInstrument
        instruments={instruments}
        capital={cfg.instrument_capital || {}}
        lots={cfg.instrument_lots || {}}
        globalCapital={cfg.capital}
        globalLots={cfg.max_lots}
        onCommit={(instrument_capital, instrument_lots) => patch({ instrument_capital, instrument_lots })}
      />

      {/* Per-instrument fixed point exits (SL + profit target) */}
      <PointExits
        instruments={instruments}
        stopPoints={cfg.stop_points || {}}
        targetPoints={cfg.target_points || {}}
        targetPct={cfg.target_pct || {}}
        onCommit={async (stop_points, target_points, target_pct) => {
          setSaving(true);
          try {
            await setPointExits({ stop_points, target_points, target_pct });
            const s = await getEngineSettings();
            setCfg(s.auto_trade);
          } finally {
            setSaving(false);
          }
        }}
      />

      {guard && <AccountRiskStrip guard={guard} />}

      {/* Quality gates found from the journal: an inverted reward:risk, a
          premium too cheap to stop, and re-entry laddering the same move. */}
      <QualityGates
        minTargetR={cfg.min_target_r ?? 1}
        minPremium={cfg.min_premium ?? 20}
        cooldownSec={cfg.reentry_cooldown_sec ?? 300}
        postStopSec={cfg.post_stop_cooldown_sec ?? 1800}
        openMinutes={cfg.no_entry_open_minutes ?? 15}
        maxConfidence={cfg.max_confidence ?? 100}
        onCommit={async (v) => {
          setSaving(true);
          try {
            await setPointExits(v);
            const s = await getEngineSettings();
            setCfg(s.auto_trade);
          } finally {
            setSaving(false);
          }
        }}
      />

      {/* How far T1/T2/T3 sit and how tight the stop is allowed to be. */}
      {gates && (
        <TargetShape
          targetScale={gates.target_scale ?? 1}
          minRewardRisk={gates.min_reward_risk ?? 1.2}
          minStopPct={gates.min_stop_pct_of_premium ?? 8}
          armPct={gates.pre_t1_arm_profit_pct ?? 6}
          givebackPct={gates.pre_t1_giveback_min_pct ?? 0.8}
          onTrail={(pre_t1_arm_profit_pct, pre_t1_giveback_min_pct) =>
            patchGates({ pre_t1_arm_profit_pct, pre_t1_giveback_min_pct })
          }
          balanced={cfg.risk_balanced_lots ?? false}
          balancedMaxLots={cfg.balanced_max_lots ?? 10}
          onShape={(target_scale, min_reward_risk, min_stop_pct_of_premium) =>
            patchGates({ target_scale, min_reward_risk, min_stop_pct_of_premium })
          }
          onSizing={async (risk_balanced_lots, balanced_max_lots) => {
            setSaving(true);
            try {
              await setPointExits({ risk_balanced_lots, balanced_max_lots });
              const s = await getEngineSettings();
              setCfg(s.auto_trade);
            } finally {
              setSaving(false);
            }
          }}
        />
      )}

      {/* LIVE arm — real money. Only meaningful when the feed is live. */}
      <div className="mt-4 rounded-xl border border-red-500/25 bg-red-500/[0.04] p-3">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[11px] font-bold text-red-300 uppercase tracking-wide">Live orders (real money)</span>
          <button
            onClick={() => {
              if (cfg.allow_live) return patch({ allow_live: false });
              if (window.confirm("Arm the bot to place REAL orders on your Angel account? Real money, real losses. It only fires when the feed is also live.")) {
                patch({ allow_live: true });
              }
            }}
            disabled={saving}
            className="ml-auto px-3 py-1 rounded-lg text-xs font-bold border"
            style={{
              borderColor: cfg.allow_live ? "#ff5a5a66" : "rgba(255,255,255,0.15)",
              color: cfg.allow_live ? "#ff6b6b" : "#8b93a7",
              background: cfg.allow_live ? "rgba(255,90,90,0.10)" : "transparent",
            }}
          >
            {cfg.allow_live ? "LIVE ARMED" : "ARM LIVE"}
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Live max lots" value={cfg.live_max_lots} step={1} min={1} onCommit={(v) => patch({ live_max_lots: v })} />
          <NumField label="Live max order (₹)" value={cfg.live_max_order_value} step={5000} min={0} onCommit={(v) => patch({ live_max_order_value: v })} />
        </div>
        <div className="mt-2 text-[10px] leading-4 text-white/40">
          {liveArmed
            ? "ARMED FOR LIVE — the bot is placing REAL orders on your Angel account."
            : cfg.allow_live && !liveFeed
              ? "Live armed, but the data feed is not live (QT_TRADE_MODE=live + Angel creds needed). Currently still paper."
              : "Off — no real orders. Needs BOTH this armed AND QT_TRADE_MODE=live with your Angel creds."}
        </div>
      </div>

      {/* Zero-to-Hero expiry sleeve — speculative, isolated budget. */}
      <div className="mt-3 rounded-xl border border-fuchsia-500/25 bg-fuchsia-500/[0.04] p-3">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-[11px] font-bold text-fuchsia-300 uppercase tracking-wide">Zero-to-Hero (expiry punt)</span>
          <button
            onClick={() => patch({ zero_to_hero_enabled: !cfg.zero_to_hero_enabled })}
            disabled={saving}
            className="ml-auto px-3 py-1 rounded-lg text-xs font-bold border"
            style={{
              borderColor: cfg.zero_to_hero_enabled ? "#e879f966" : "rgba(255,255,255,0.15)",
              color: cfg.zero_to_hero_enabled ? "#e879f9" : "#8b93a7",
              background: cfg.zero_to_hero_enabled ? "rgba(232,121,249,0.10)" : "transparent",
            }}
          >
            {cfg.zero_to_hero_enabled ? "SLEEVE ON" : "SLEEVE OFF"}
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <NumField label="Sleeve budget (₹/day)" value={cfg.zero_to_hero_budget} step={1000} min={0} onCommit={(v) => patch({ zero_to_hero_budget: v })} />
        </div>
        <div className="mt-2 text-[10px] leading-4 text-white/40">
          Fires ONLY on expiry day, buys a cheap far-OTM leg in the day&apos;s bias direction. It can multiply — but
          usually expires <span className="text-fuchsia-300">worthless</span>. No backtest, no edge. Walled off: it never
          loses more than the budget/day, and never uses your core capital.
        </div>
      </div>
      </>
      )}

      <div className="mt-3 text-[10px] text-white/30 leading-4">
        The bot cannot guarantee a daily profit; losing days are normal and it stays flat when there is no clean setup.
        The measured edge is thin (PF ~1.05) and validated on underlying moves only — real option costs (theta/spread)
        may reduce it. Real orders fire only when explicitly armed above AND the feed is live.
      </div>
    </div>
  );
}

function NumField({
  label, value, step, min, onCommit,
}: { label: string; value: number; step: number; min: number; onCommit: (v: number) => void }) {
  const [v, setV] = useState(String(value));
  useEffect(() => setV(String(value)), [value]);
  return (
    <label className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2 block">
      <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">{label}</div>
      <input
        type="number"
        value={v}
        step={step}
        min={min}
        onChange={(e) => setV(e.target.value)}
        onBlur={() => {
          const n = parseFloat(v);
          if (!Number.isNaN(n) && n >= min) onCommit(n);
        }}
        className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums"
      />
    </label>
  );
}

/**
 * Per-instrument capital + lots overrides. Each instrument can carry its own
 * capital budget and a hard fixed lot count. Blank = use the global values
 * shown as the placeholder. Live max-lots / max-order-value caps still apply.
 */
function PerInstrument({
  instruments, capital, lots, globalCapital, globalLots, onCommit,
}: {
  instruments: InstrumentOption[];
  capital: Record<string, number>;
  lots: Record<string, number>;
  globalCapital: number;
  globalLots: number;
  onCommit: (capital: Record<string, number>, lots: Record<string, number>) => void;
}) {
  const [capEdit, setCapEdit] = useState<Record<string, string>>({});
  const [lotEdit, setLotEdit] = useState<Record<string, string>>({});

  useEffect(() => {
    const c: Record<string, string> = {};
    const l: Record<string, string> = {};
    for (const k of Object.keys(capital)) c[k] = String(capital[k]);
    for (const k of Object.keys(lots)) l[k] = String(lots[k]);
    setCapEdit(c);
    setLotEdit(l);
  }, [capital, lots]);

  if (!instruments.length) return null;

  const commit = (nextCap: Record<string, string>, nextLot: Record<string, string>) => {
    const outCap: Record<string, number> = {};
    const outLot: Record<string, number> = {};
    for (const k of Object.keys(nextCap)) {
      const n = parseFloat(nextCap[k]);
      if (!Number.isNaN(n) && n > 0) outCap[k] = n;
    }
    for (const k of Object.keys(nextLot)) {
      const n = parseInt(nextLot[k], 10);
      if (!Number.isNaN(n) && n >= 1) outLot[k] = n;
    }
    onCommit(outCap, outLot);
  };

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold text-white/55 uppercase tracking-wide">
          Per-instrument capital &amp; lots
        </span>
        <span className="text-[10px] text-white/35">— each instrument is independent</span>
      </div>
      <div className="grid grid-cols-[1fr_1fr_1fr] gap-2 items-center mb-1">
        <div className="text-[9px] uppercase tracking-wide text-white/30">Instrument</div>
        <div className="text-[9px] uppercase tracking-wide text-white/30">Capital (₹)</div>
        <div className="text-[9px] uppercase tracking-wide text-white/30">Lots (fixed)</div>
      </div>
      <div className="space-y-2">
        {instruments.map((inst) => {
          const key = inst.symbol.toUpperCase();
          const hasCustom = (capEdit[key] ?? "") !== "" || (lotEdit[key] ?? "") !== "";
          return (
            <div
              key={inst.symbol}
              className="grid grid-cols-[1fr_1fr_1fr] gap-2 items-center rounded-lg px-1 py-0.5"
              style={{ background: hasCustom ? "rgba(25,229,127,0.06)" : "transparent" }}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-xs text-white/70 font-semibold truncate">{inst.display}</span>
                {hasCustom ? (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-[#19e57f]/15 text-[#19e57f] font-bold shrink-0">CUSTOM</span>
                ) : (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-white/5 text-white/30 font-bold shrink-0">GLOBAL</span>
                )}
              </div>
              <label
                className="rounded-lg bg-white/[0.03] px-2 py-1 block border"
                style={{ borderColor: (capEdit[key] ?? "") !== "" ? "#19e57f66" : "rgba(255,255,255,0.05)" }}
              >
                <input
                  type="number"
                  inputMode="numeric"
                  value={capEdit[key] ?? ""}
                  placeholder={`global ${globalCapital}`}
                  onChange={(e) => setCapEdit((prev) => ({ ...prev, [key]: e.target.value }))}
                  onBlur={() => commit(capEdit, lotEdit)}
                  className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums placeholder:text-white/25 placeholder:font-normal"
                />
              </label>
              <label
                className="rounded-lg bg-white/[0.03] px-2 py-1 block border"
                style={{ borderColor: (lotEdit[key] ?? "") !== "" ? "#19e57f66" : "rgba(255,255,255,0.05)" }}
              >
                <input
                  type="number"
                  inputMode="numeric"
                  value={lotEdit[key] ?? ""}
                  placeholder={`global ${globalLots}`}
                  onChange={(e) => setLotEdit((prev) => ({ ...prev, [key]: e.target.value }))}
                  onBlur={() => commit(capEdit, lotEdit)}
                  className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums placeholder:text-white/25 placeholder:font-normal"
                />
              </label>
            </div>
          );
        })}
      </div>
      <div className="mt-2 text-[10px] leading-4 text-white/35">
        Each instrument keeps its <span className="text-white/55">own</span> capital &amp; lots. A grey
        <span className="text-white/45"> GLOBAL</span> row is empty and falls back to the global values above
        (shown faint as <span className="text-white/45">&quot;global …&quot;</span>). Type a value and it becomes a
        <span className="text-[#19e57f]"> CUSTOM</span> row for that instrument only. Lots here is a hard fixed size;
        live max-lots / max-order-value caps still apply on top.
      </div>
    </div>
  );
}

/**
 * Per-instrument FIXED point exits. For each instrument you can set a stop-loss
 * and a profit target in OPTION-PREMIUM POINTS. Blank = fall back to the default:
 * SL → % / engine stop; target → the T1/T2/T3 ride model. When set, the bot
 * exits at exactly entry−SL or entry+Target for a predictable rupee outcome.
 */
function PointExits({
  instruments, stopPoints, targetPoints, targetPct, onCommit,
}: {
  instruments: InstrumentOption[];
  stopPoints: Record<string, number>;
  targetPoints: Record<string, number>;
  targetPct: Record<string, number>;
  onCommit: (
    stop: Record<string, number>,
    target: Record<string, number>,
    target_pct: Record<string, number>,
  ) => void;
}) {
  const [slEdit, setSlEdit] = useState<Record<string, string>>({});
  const [tgtEdit, setTgtEdit] = useState<Record<string, string>>({});
  const [pctEdit, setPctEdit] = useState<Record<string, string>>({});

  useEffect(() => {
    const s: Record<string, string> = {};
    const t: Record<string, string> = {};
    const p: Record<string, string> = {};
    for (const k of Object.keys(stopPoints)) s[k] = String(stopPoints[k]);
    for (const k of Object.keys(targetPoints)) t[k] = String(targetPoints[k]);
    for (const k of Object.keys(targetPct)) p[k] = String(targetPct[k]);
    setSlEdit(s);
    setTgtEdit(t);
    setPctEdit(p);
  }, [stopPoints, targetPoints, targetPct]);

  if (!instruments.length) return null;

  const commit = (
    nextSl: Record<string, string>,
    nextTgt: Record<string, string>,
    nextPct: Record<string, string>,
  ) => {
    const outSl: Record<string, number> = {};
    const outTgt: Record<string, number> = {};
    const outPct: Record<string, number> = {};
    for (const k of Object.keys(nextSl)) {
      const n = parseFloat(nextSl[k]);
      outSl[k] = !Number.isNaN(n) && n > 0 ? n : 0; // 0 clears on the backend
    }
    for (const k of Object.keys(nextTgt)) {
      const n = parseFloat(nextTgt[k]);
      outTgt[k] = !Number.isNaN(n) && n > 0 ? n : 0;
    }
    for (const k of Object.keys(nextPct)) {
      const n = parseFloat(nextPct[k]);
      outPct[k] = !Number.isNaN(n) && n > 0 ? n : 0;
    }
    onCommit(outSl, outTgt, outPct);
  };

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold text-white/55 uppercase tracking-wide">
          Per-instrument exit points
        </span>
        <span className="text-[10px] text-white/35">— stop-loss &amp; profit-lock in premium points</span>
      </div>
      <div className="grid grid-cols-[1fr_1fr_1fr_1fr] gap-2 items-center mb-1">
        <div className="text-[9px] uppercase tracking-wide text-white/30">Instrument</div>
        <div className="text-[9px] uppercase tracking-wide text-white/30">Stop-loss (pts)</div>
        <div className="text-[9px] uppercase tracking-wide text-white/30">Target (pts)</div>
        <div className="text-[9px] uppercase tracking-wide text-[#19e57f]/60">Target (% prem)</div>
      </div>
      <div className="space-y-2">
        {instruments.map((inst) => {
          const key = inst.symbol.toUpperCase();
          const hasCustom =
            (slEdit[key] ?? "") !== "" ||
            (tgtEdit[key] ?? "") !== "" ||
            (pctEdit[key] ?? "") !== "";
          return (
            <div
              key={inst.symbol}
              className="grid grid-cols-[1fr_1fr_1fr_1fr] gap-2 items-center rounded-lg px-1 py-0.5"
              style={{ background: hasCustom ? "rgba(25,229,127,0.06)" : "transparent" }}
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <span className="text-xs text-white/70 font-semibold truncate">{inst.display}</span>
                {hasCustom ? (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-[#19e57f]/15 text-[#19e57f] font-bold shrink-0">POINTS</span>
                ) : (
                  <span className="text-[8px] px-1 py-0.5 rounded bg-white/5 text-white/30 font-bold shrink-0">T1/T2/T3</span>
                )}
              </div>
              <label
                className="rounded-lg bg-white/[0.03] px-2 py-1 block border"
                style={{ borderColor: (slEdit[key] ?? "") !== "" ? "#ff5a5a66" : "rgba(255,255,255,0.05)" }}
              >
                <input
                  type="number"
                  inputMode="decimal"
                  value={slEdit[key] ?? ""}
                  placeholder="% / engine"
                  onChange={(e) => setSlEdit((prev) => ({ ...prev, [key]: e.target.value }))}
                  onBlur={() => commit(slEdit, tgtEdit, pctEdit)}
                  className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums placeholder:text-white/25 placeholder:font-normal"
                />
              </label>
              <label
                className="rounded-lg bg-white/[0.03] px-2 py-1 block border"
                style={{ borderColor: (tgtEdit[key] ?? "") !== "" ? "#19e57f66" : "rgba(255,255,255,0.05)" }}
              >
                <input
                  type="number"
                  inputMode="decimal"
                  value={tgtEdit[key] ?? ""}
                  placeholder="T1/T2/T3"
                  onChange={(e) => setTgtEdit((prev) => ({ ...prev, [key]: e.target.value }))}
                  onBlur={() => commit(slEdit, tgtEdit, pctEdit)}
                  className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums placeholder:text-white/25 placeholder:font-normal"
                />
              </label>
              <label
                className="rounded-lg bg-white/[0.03] px-2 py-1 block border"
                style={{ borderColor: (pctEdit[key] ?? "") !== "" ? "#19e57f66" : "rgba(255,255,255,0.05)" }}
              >
                <input
                  type="number"
                  inputMode="decimal"
                  value={pctEdit[key] ?? ""}
                  placeholder="% of premium"
                  onChange={(e) => setPctEdit((prev) => ({ ...prev, [key]: e.target.value }))}
                  onBlur={() => commit(slEdit, tgtEdit, pctEdit)}
                  className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums placeholder:text-white/25 placeholder:font-normal"
                />
              </label>
            </div>
          );
        })}
      </div>
      <div className="mt-2 text-[10px] leading-4 text-white/35">
        Points are <span className="text-white/55">option-premium points</span> from entry (rupee = points × lot size × lots).
        <span className="text-[#ff6b6b]"> Stop-loss</span>: exit at entry − pts (blank → % / engine stop).
        <span className="text-[#19e57f]"> Target</span>: once it&apos;s up by your pts and then <span className="text-white/55">ticks down (struggling before T1)</span>
        it banks the profit there; if it keeps climbing to T1 it rides T1/T2/T3 (blank → pure T1/T2/T3 ride).
        <div className="mt-1 text-[#19e57f]/70">
          Prefer <span className="font-semibold">Target (% prem)</span> — it wins over the points
          column where both are set. A flat 2 points is 0.7% on a ₹262 option and 9% on a ₹25 one,
          which is how a 67% win rate still lost money: +0.7% banked against an 8% stop. Either way
          the early exit can no longer fire tighter than the min-R floor.
        </div>
      </div>
    </div>
  );
}

/**
 * Entry/exit quality gates derived from the trade journal:
 *  - min target R: the early profit-lock may not fire tighter than this multiple
 *    of the stop, so it cannot be configured into a losing reward:risk;
 *  - min premium: a contract too cheap to stop out sanely is refused;
 *  - re-entry cooldown: stops the bot laddering the same move rung by rung.
 */
/**
 * Live state of the ACCOUNT-WIDE risk governor.
 *
 * This is on screen because its absence was the expensive part: on 13 Aug the
 * ₹5,000 daily cap was counted per instrument and held in memory, so the book
 * lost ₹16,834 with the cap "on" and nothing on the dashboard could show that
 * the cap was doing nothing. An engine that is refusing to trade and an engine
 * with a broken cap look identical from outside unless the number is displayed.
 */
function AccountRiskStrip({ guard }: { guard: AccountRiskStatus }) {
  const lossPct =
    guard.daily_loss_cap > 0
      ? Math.min(100, Math.max(0, (-guard.day_pnl / guard.daily_loss_cap) * 100))
      : 0;
  const cooled = Object.entries(guard.cooldowns).filter(([, c]) => c.blocked);
  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold text-white/55 uppercase tracking-wide">
          Account risk today
        </span>
        <span className="text-[10px] text-white/35">
          — one pot for every instrument, kept on disk across restarts
        </span>
        {guard.blocked && (
          <span className="ml-auto px-2 py-0.5 rounded-md text-[10px] font-bold text-red-300 bg-red-500/10 border border-red-500/25">
            HALTED
          </span>
        )}
      </div>
      <div className="grid grid-cols-4 gap-2 text-center">
        {[
          {
            label: "Day P&L",
            value: `₹${Math.round(guard.day_pnl).toLocaleString("en-IN")}`,
            tint: guard.day_pnl >= 0 ? "#4ade80" : "#ff6b6b",
          },
          {
            label: "Loss cap used",
            value: `${lossPct.toFixed(0)}%`,
            tint: lossPct >= 100 ? "#ff6b6b" : lossPct >= 60 ? "#fbbf24" : "#8b93a7",
          },
          {
            label: "Trades",
            value: `${guard.trades_today}${guard.max_trades_per_day > 0 ? ` / ${guard.max_trades_per_day}` : ""}`,
            tint: "#8b93a7",
          },
          {
            label: "Loss streak",
            value:
              guard.stand_down_sec_left > 0
                ? `${guard.consecutive_losses} · ${Math.ceil(guard.stand_down_sec_left / 60)}m`
                : String(guard.consecutive_losses),
            tint: guard.stand_down_sec_left > 0 ? "#fbbf24" : "#8b93a7",
          },
        ].map((c) => (
          <div key={c.label} className="rounded-lg bg-white/[0.03] border border-white/5 px-2 py-1">
            <div className="text-[9px] uppercase tracking-wide text-white/30">{c.label}</div>
            <div className="text-sm font-semibold tabular-nums" style={{ color: c.tint }}>
              {c.value}
            </div>
          </div>
        ))}
      </div>
      {guard.blocked && (
        <div className="mt-2 text-[10px] leading-4 text-red-300/80">{guard.blocked}</div>
      )}
      <div className="mt-2 text-[10px] leading-4 text-white/40">
        No entry for the first {guard.opening_window_minutes} minutes after the bell ·{" "}
        {Math.round(guard.reentry_cooldown_sec / 60)}m cooldown after an
        exit, {Math.round(guard.post_stop_cooldown_sec / 60)}m after a stop.
      </div>
      {cooled.length > 0 && (
        <div className="mt-1 text-[10px] leading-4 text-white/35">
          Cooling: {cooled.map(([inst]) => inst).join(", ")}
        </div>
      )}
    </div>
  );
}

function QualityGates({
  minTargetR, minPremium, cooldownSec, postStopSec, openMinutes, maxConfidence, onCommit,
}: {
  minTargetR: number;
  minPremium: number;
  cooldownSec: number;
  postStopSec: number;
  openMinutes: number;
  maxConfidence: number;
  onCommit: (v: {
    min_target_r: number;
    min_premium: number;
    reentry_cooldown_sec: number;
    post_stop_cooldown_sec: number;
    no_entry_open_minutes: number;
    max_confidence: number;
  }) => void;
}) {
  const [r, setR] = useState(String(minTargetR));
  const [prem, setPrem] = useState(String(minPremium));
  const [cool, setCool] = useState(String(Math.round(cooldownSec / 60)));
  const [stopCool, setStopCool] = useState(String(Math.round(postStopSec / 60)));
  const [openMin, setOpenMin] = useState(String(openMinutes));
  const [conf, setConf] = useState(String(maxConfidence));

  useEffect(() => {
    setR(String(minTargetR));
    setPrem(String(minPremium));
    setCool(String(Math.round(cooldownSec / 60)));
    setStopCool(String(Math.round(postStopSec / 60)));
    setOpenMin(String(openMinutes));
    setConf(String(maxConfidence));
  }, [minTargetR, minPremium, cooldownSec, postStopSec, openMinutes, maxConfidence]);

  const num = (raw: string, fallback: number) => {
    const v = parseFloat(raw);
    return Number.isNaN(v) ? fallback : v;
  };

  const commit = () => {
    onCommit({
      min_target_r: num(r, minTargetR),
      min_premium: num(prem, minPremium),
      reentry_cooldown_sec: num(cool, cooldownSec / 60) * 60,
      post_stop_cooldown_sec: num(stopCool, postStopSec / 60) * 60,
      no_entry_open_minutes: num(openMin, openMinutes),
      max_confidence: num(conf, maxConfidence),
    });
  };

  const field = (
    label: string,
    hint: string,
    value: string,
    set: (v: string) => void,
  ) => (
    <label className="block">
      <div className="text-[9px] uppercase tracking-wide text-white/30">{label}</div>
      <div className="rounded-lg bg-white/[0.03] px-2 py-1 border border-white/5">
        <input
          type="number"
          inputMode="decimal"
          value={value}
          onChange={(e) => set(e.target.value)}
          onBlur={commit}
          className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums"
        />
      </div>
      <div className="mt-0.5 text-[9px] text-white/30 leading-3">{hint}</div>
    </label>
  );

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold text-white/55 uppercase tracking-wide">
          Quality gates
        </span>
        <span className="text-[10px] text-white/35">— what the bot refuses to trade</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {field("Min target (R)", "early exit never tighter than this × the stop", r, setR)}
        {field("Min premium (₹)", "too cheap to stop out sanely", prem, setPrem)}
        {field("Re-entry cooldown (min)", "per instrument, after an exit", cool, setCool)}
        {field("After a STOP (min)", "longer: the view was just proven wrong", stopCool, setStopCool)}
        {field("No entry after bell (min)", "premiums gap through a stop here", openMin, setOpenMin)}
        {field("Max confidence (%)", "the ≥95% band lost the most — cap it", conf, setConf)}
      </div>
    </div>
  );
}

/**
 * Where the targets sit and how tight the stop may be.
 *
 * Measured on the signal history: T1 was priced ~11% above the premium while the
 * median signal only travelled ~2% and just 12% of tradeable signals ever reached
 * 11% — T1 was further away than the move usually goes. `Target scale` pulls
 * T1/T2/T3 in without touching the stop, so it raises the hit rate at the cost of
 * reward:risk; `Min reward:risk` is the floor that refuses the setup once the
 * shortened target no longer pays for the stop, which is why lowering the scale
 * turns some signals into WAIT rather than into small winners.
 */
function TargetShape({
  targetScale, minRewardRisk, minStopPct, armPct, givebackPct,
  balanced, balancedMaxLots, onShape, onTrail, onSizing,
}: {
  targetScale: number;
  minRewardRisk: number;
  minStopPct: number;
  armPct: number;
  givebackPct: number;
  balanced: boolean;
  balancedMaxLots: number;
  onShape: (scale: number, rr: number, stopPct: number) => void;
  onTrail: (armPct: number, givebackPct: number) => void;
  onSizing: (balanced: boolean, maxLots: number) => void;
}) {
  const [scale, setScale] = useState(String(targetScale));
  const [rr, setRr] = useState(String(minRewardRisk));
  const [stop, setStop] = useState(String(minStopPct));
  const [arm, setArm] = useState(String(armPct));
  const [give, setGive] = useState(String(givebackPct));
  const [lots, setLots] = useState(String(balancedMaxLots));

  useEffect(() => {
    setScale(String(targetScale));
    setRr(String(minRewardRisk));
    setStop(String(minStopPct));
    setArm(String(armPct));
    setGive(String(givebackPct));
    setLots(String(balancedMaxLots));
  }, [targetScale, minRewardRisk, minStopPct, armPct, givebackPct, balancedMaxLots]);

  const commitTrail = () => {
    const na = parseFloat(arm);
    const ng = parseFloat(give);
    onTrail(Number.isNaN(na) ? armPct : na, Number.isNaN(ng) ? givebackPct : ng);
  };

  const commitShape = () => {
    const ns = parseFloat(scale);
    const nr = parseFloat(rr);
    const np = parseFloat(stop);
    onShape(
      Number.isNaN(ns) ? targetScale : ns,
      Number.isNaN(nr) ? minRewardRisk : nr,
      Number.isNaN(np) ? minStopPct : np,
    );
  };

  const field = (
    label: string,
    hint: string,
    value: string,
    set: (v: string) => void,
    commit: () => void,
  ) => (
    <label className="block">
      <div className="text-[9px] uppercase tracking-wide text-white/30">{label}</div>
      <div className="rounded-lg bg-white/[0.03] px-2 py-1 border border-white/5">
        <input
          type="number"
          inputMode="decimal"
          value={value}
          onChange={(e) => set(e.target.value)}
          onBlur={commit}
          className="w-full bg-transparent text-sm font-semibold text-white outline-none tabular-nums"
        />
      </div>
      <div className="mt-0.5 text-[9px] text-white/30 leading-3">{hint}</div>
    </label>
  );

  const approxT1 = Math.round(11 * (parseFloat(scale) || 1) * 10) / 10;

  return (
    <div className="mt-4 rounded-xl border border-white/10 bg-white/[0.02] p-3">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] font-bold text-white/55 uppercase tracking-wide">
          Target &amp; stop shape
        </span>
        <span className="text-[10px] text-white/35">— how far T1 sits, how tight the stop may be</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        {field("Target scale (×)", `T1 lands near ${approxT1}% of premium`, scale, setScale, commitShape)}
        {field("Min reward:risk", "refuses the setup below this", rr, setRr, commitShape)}
        {field("Min stop (% prem)", "the stop can never be tighter", stop, setStop, commitShape)}
      </div>
      <div className="mt-2 text-[10px] leading-4 text-white/40">
        Measured on the signal history: only 12% of tradeable signals reached the
        current ~11% T1, while the median one travelled ~2%. Pulling the targets in
        raises the hit rate but shrinks reward:risk — the min reward:risk floor then
        turns the weakest of those setups into WAIT instead of small winners. This is
        a trade-off, not a free improvement, and one session is not enough evidence to
        call an optimum.
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3">
        {field("Pre-T1 arm (% above entry)", "lock profit once it runs this far", arm, setArm, commitTrail)}
        {field("Min give-back (% prem)", "exit this far off the peak; scales with the premium", give, setGive, commitTrail)}
      </div>
      <div className="mt-2 text-[10px] leading-4 text-white/40">
        The give-back floor used to be in premium points — the same unit mistake the
        target field had. 2 points is 0.8% of a ₹255 CRUDEOIL option but 13% of a ₹15
        SBIN one, wider than the whole stop, so on a cheap contract the trail could
        never fire and the trade drifted into the hard stop. It is a % now.
      </div>
      <div className="mt-3 flex items-center gap-3 flex-wrap">
        <Toggle
          label="Risk-balanced lots (same ₹ risk per instrument)"
          on={balanced}
          onClick={() => onSizing(!balanced, balancedMaxLots)}
        />
        <label className="flex items-center gap-2 text-[10px] text-white/35">
          cap
          <input
            type="number"
            value={lots}
            onChange={(e) => setLots(e.target.value)}
            onBlur={() => {
              const n = parseInt(lots, 10);
              onSizing(balanced, Number.isNaN(n) ? balancedMaxLots : n);
            }}
            className="w-14 rounded-lg bg-white/[0.03] border border-white/5 px-2 py-1 text-white tabular-nums outline-none"
          />
          lots
        </label>
      </div>
      <div className="mt-1 text-[10px] leading-4 text-white/40">
        Off by default. On, a cheap contract is bought in more lots so its stop costs
        the same rupees as an expensive one&apos;s — which fixes small wins against
        large losses, but it does raise the lot count, so paper it first.
      </div>
    </div>
  );
}

function Toggle({ label, on, onClick }: { label: string; on: boolean; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex items-center gap-2 text-xs text-white/60">
      <span
        className="w-9 h-5 rounded-full p-0.5 transition-colors"
        style={{ background: on ? "#19e57f" : "rgba(255,255,255,0.15)" }}
      >
        <span className="block w-4 h-4 rounded-full bg-white transition-transform" style={{ transform: on ? "translateX(16px)" : "none" }} />
      </span>
      {label}
    </button>
  );
}

/**
 * Kill switch. Engage needs no token and blocks all new entries at once.
 * Release needs the live-arm token, typed into a password field, sent once in
 * a request header and cleared — never stored, never in a URL. This control
 * cannot enable live trading.
 */
function KillSwitchControl() {
  const [ks, setKs] = useState<KillSwitchState | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [releasing, setReleasing] = useState(false);
  const [token, setToken] = useState("");

  const pull = useCallback(async () => {
    try {
      setKs(await getKillSwitch());
      setErr(null);
    } catch (e) {
      setKs(null);
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    pull();
    const t = setInterval(pull, 5000);
    return () => clearInterval(t);
  }, [pull]);

  const engage = async () => {
    const ok = window.confirm(
      "ENGAGE the kill switch?\n\nAll new entries are blocked immediately and the auto-trader is switched off. Exits keep working. Releasing it later needs the live-arm token."
    );
    if (!ok) return;
    setBusy(true);
    try {
      setKs(await engageKillSwitch("engaged from dashboard"));
      setErr(null);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const release = async () => {
    if (!token) {
      setErr("Enter the live-arm token to release the kill switch.");
      return;
    }
    setBusy(true);
    try {
      setKs(await releaseKillSwitch(token, "released from dashboard"));
      setErr(null);
      setReleasing(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setToken("");
      setBusy(false);
    }
  };

  const state = ks === null ? "UNKNOWN" : ks.engaged ? "ENGAGED" : "RELEASED";
  const color = state === "ENGAGED" ? "#ff4d4d" : state === "RELEASED" ? "#19e57f" : "#f5b301";

  return (
    <div
      className="rounded-xl border p-3 mb-3"
      style={{ borderColor: `${color}55`, background: "rgba(255,255,255,0.02)" }}
    >
      <div className="flex flex-wrap items-center gap-3">
        <div className="min-w-0">
          <div className="text-sm font-bold text-white">
            Kill switch <span style={{ color }}>● {state}</span>
          </div>
          <div className="text-[11px] text-white/45 leading-4 mt-0.5">
            {state === "ENGAGED"
              ? `New entries blocked${ks?.reason ? ` — ${ks.reason}` : ""}${ks?.ts ? ` (since ${new Date(ks.ts * 1000).toLocaleString()})` : ""}. Exits still work.`
              : state === "RELEASED"
                ? "New entries allowed (subject to the usual gates)."
                : "Status unavailable — treat trading as unsafe until it loads."}
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {state !== "ENGAGED" && (
            <button
              onClick={engage}
              disabled={busy}
              className="px-4 py-2 rounded-lg text-xs font-extrabold border"
              style={{ borderColor: "#ff4d4d", color: "#ff4d4d", background: "rgba(255,77,77,0.08)" }}
            >
              ENGAGE KILL SWITCH
            </button>
          )}
          {state === "ENGAGED" && !releasing && (
            <button
              onClick={() => {
                setErr(null);
                setReleasing(true);
              }}
              disabled={busy}
              className="px-4 py-2 rounded-lg text-xs font-bold border border-white/20 text-white/80"
            >
              Release…
            </button>
          )}
        </div>
      </div>
      {state === "ENGAGED" && releasing && (
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <input
            type="password"
            autoComplete="off"
            spellCheck={false}
            aria-label="Live-arm token"
            placeholder="Live-arm token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") release();
            }}
            className="px-2 py-1.5 rounded-md bg-black/40 border border-white/15 text-xs text-white w-56"
          />
          <button
            onClick={release}
            disabled={busy || !token}
            className="px-3 py-1.5 rounded-md text-xs font-bold border border-white/25 text-white"
          >
            Release
          </button>
          <button
            onClick={() => {
              setToken("");
              setReleasing(false);
            }}
            disabled={busy}
            className="px-3 py-1.5 rounded-md text-xs text-white/60"
          >
            Cancel
          </button>
        </div>
      )}
      {err && <div className="mt-2 text-[11px] font-semibold text-[#ff4d4d]">{err}</div>}
    </div>
  );
}
