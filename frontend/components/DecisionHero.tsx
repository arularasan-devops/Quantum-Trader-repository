"use client";
import { useEffect, useState } from "react";
import { Decision } from "@/lib/types";
import { themeFor } from "@/lib/theme";

function Stat({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <div className="text-center px-2">
      <div className="text-[11px] uppercase tracking-wider text-white/45">{label}</div>
      <div className="text-2xl font-black mt-1 whitespace-nowrap" style={{ color: color ?? "#ffffff" }}>{value}</div>
    </div>
  );
}

export default function DecisionHero({ decision }: { decision: Decision }) {
  const t = themeFor(decision.signal);
  const [countdown, setCountdown] = useState(decision.next_review_seconds);

  useEffect(() => {
    setCountdown(decision.next_review_seconds);
    const id = setInterval(() => setCountdown((c) => (c > 0 ? c - 1 : decision.next_review_seconds)), 1000);
    return () => clearInterval(id);
  }, [decision.next_review_seconds, decision.signal]);

  const mm = String(Math.floor(countdown / 60)).padStart(2, "0");
  const ss = String(countdown % 60).padStart(2, "0");
  const move = decision.expected_move_points;

  // Position-independent market scan (fresh-entry BUY / WAIT / NO_TRADE). Keep
  // it visible whenever it differs from the main signal — e.g. while holding
  // (main signal HOLD/EXIT) the BUY/WAIT call must NOT disappear.
  const marketSignal = decision.market_signal;
  const showMarket = marketSignal != null && marketSignal !== decision.signal;
  const mt = showMarket ? themeFor(marketSignal) : null;

  // Reversal: holding one side but the move flipped — flag the opposite side.
  const flipSide = decision.reversal_option_type === "PE" ? "PUT" : "CALL";

  // Higher-timeframe (5-min) trend that GATES direction, and the 1-min entry
  // trigger (pullback / retest / waiting). Surfaced so the user sees WHY.
  const htf = decision.htf_trend;
  const htfArrow = htf === "UP" ? "▲" : htf === "DOWN" ? "▼" : "▬";
  const htfColor = htf === "UP" ? "#19e57f" : htf === "DOWN" ? "#ff4d4d" : "#8b93a7";
  const triggerText: Record<string, string> = {
    PULLBACK: "via pullback",
    BREAKOUT_RETEST: "via breakout retest",
    MOMENTUM: "via momentum",
    PATTERN: "via pattern",
    WAIT_PULLBACK: "waiting for pullback",
    WAIT_PATTERN: "waiting for pattern",
    WAIT_SETUP: "waiting for setup",
    NONE: "no setup",
  };

  const actionable = ["BUY", "HOLD", "EXIT"].includes(decision.signal);
  // Show the recommended option (and its live price) at ALL times — including
  // while WAITing or before a buy — so the current option price is always
  // visible, not only once a BUY/HOLD/EXIT fires.
  const optionLabel =
    decision.strike != null && decision.option_type
      ? `${decision.strike} ${decision.option_type}`
      : decision.recommended_option ?? null;
  const strikeText = optionLabel
    ? actionable
      ? `${decision.signal} ${optionLabel}`
      : optionLabel
    : null;
  const showOption = strikeText != null;

  return (
    <div className="card card-accent h-full flex flex-col rise">
      <div className="flex items-center justify-between px-5 pt-4">
        <div className="text-sm font-semibold text-white/70">Huge AI Decision Card</div>
        <div className="flex items-center gap-2">
          {htf && (
            <span
              className="text-[10px] font-bold rounded-full px-2.5 py-0.5 uppercase tracking-wider"
              style={{ color: htfColor, border: `1px solid ${htfColor}`, background: "rgba(255,255,255,0.03)" }}
              title="5-min futures trend — this decides CE vs PE. The 1-min chart is only used to time the entry."
            >
              5m {htfArrow} {htf}
              {decision.htf_strength != null ? ` · ${Math.round(decision.htf_strength)}%` : ""}
            </span>
          )}
          {decision.entry_trigger && decision.entry_trigger !== "NONE" && (
            <span
              className="text-[10px] font-semibold rounded-full px-2.5 py-0.5 tracking-wider text-white/60"
              style={{ border: "1px solid rgba(255,255,255,0.15)", background: "rgba(255,255,255,0.03)" }}
              title="How the engine wants to time the entry on the 1-min chart (never chase the breakout candle)."
            >
              {triggerText[decision.entry_trigger] ?? decision.entry_trigger}
            </span>
          )}
          {showMarket && mt && (
            <span
              className="text-[10px] font-bold rounded-full px-2.5 py-0.5 uppercase tracking-wider"
              style={{ color: mt.accent, border: `1px solid ${mt.accent}`, background: "rgba(255,255,255,0.03)" }}
              title="What the engine would signal for a FRESH entry right now (independent of your open position)"
            >
              Market: {marketSignal}
              {decision.market_confidence != null ? ` · ${Math.round(decision.market_confidence)}%` : ""}
            </span>
          )}
          {decision.locked && (
            <span className="pill text-[10px] font-semibold rounded-full px-2 py-0.5 uppercase tracking-wider">
              Signal locked · {decision.signal_age_seconds}s
            </span>
          )}
          <div className="w-6 h-6 rounded-full border border-white/15 grid place-items-center text-white/40 text-xs">i</div>
        </div>
      </div>

      <div className="flex-1 grid place-items-center py-1 min-h-0">
        <div className="text-center">
          <div className={`${showOption ? "text-[5.5rem]" : "text-[7.5rem]"} leading-none font-black tracking-tight neon-outline glow-pulse`}>
            {t.word}
          </div>
          {showOption && (
            <div className="mt-2 flex flex-col items-center gap-1">
              <div
                className="inline-block rounded-lg px-4 py-1.5 text-2xl font-black tracking-tight"
                style={{ color: t.accent, border: `1px solid ${t.accent}`, background: "rgba(255,255,255,0.03)" }}
              >
                {!actionable && <span className="text-white/40 text-sm font-semibold uppercase tracking-wider">{"Watching "}</span>}
                {strikeText}
                {decision.current_premium != null && (
                  <span className="text-white/70 text-lg font-semibold">{`  @ ₹${decision.current_premium}`}</span>
                )}
                {decision.moneyness && (
                  <span className="text-white/50 text-sm font-semibold">{`  ${decision.moneyness}`}</span>
                )}
              </div>
              <div className="text-[12px] text-white/55 font-medium">
                {decision.spot_price != null && <span>Spot ₹{decision.spot_price}</span>}
                {decision.atm_strike != null && (
                  <span>{" · "}ATM {decision.atm_strike}{decision.option_type ? ` ${decision.option_type}` : ""}</span>
                )}
              </div>
              {decision.reversal && decision.reversal_option && (
                <div
                  className="mt-1 rounded-lg px-3 py-1 text-[12px] font-bold"
                  style={{ color: "#ffb020", border: "1px solid rgba(255,176,32,0.6)", background: "rgba(255,176,32,0.08)" }}
                  title="The move reversed against the option you hold — exit it and consider flipping to the opposite side"
                >
                  ⇄ FLIP → buy {flipSide} {decision.reversal_option}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {decision.trade_validation?.enabled && (
        <div
          className="mx-4 mt-1 mb-2 rounded-lg px-3 py-2 text-[12px]"
          style={{
            border: `1px solid ${decision.trade_validation.approved ? "rgba(25,229,127,0.5)" : "rgba(255,77,77,0.55)"}`,
            background: decision.trade_validation.approved ? "rgba(25,229,127,0.06)" : "rgba(255,77,77,0.07)",
          }}
        >
          <div className="flex items-center justify-between font-bold mb-1">
            <span style={{ color: decision.trade_validation.approved ? "#19e57f" : "#ff4d4d" }}>
              {decision.trade_validation.approved ? "✓ Risk v2: Trade validated" : "✕ Risk v2: Trade rejected"}
            </span>
            <span className="text-white/45 font-medium">
              {decision.trade_validation.reward_risk != null && `R:R ${decision.trade_validation.reward_risk.toFixed(2)}`}
              {decision.trade_validation.max_premium_loss_pct != null &&
                ` · max loss ${decision.trade_validation.max_premium_loss_pct.toFixed(0)}%`}
              {decision.trade_validation.refined_stop != null &&
                ` · stop ${decision.trade_validation.refined_stop.toFixed(1)} (${decision.trade_validation.refined_stop_source ?? ""})`}
            </span>
          </div>
          {!decision.trade_validation.approved &&
            decision.trade_validation.rejections.map((r, i) => (
              <div key={i} className="text-[#ff8a8a]">• {r}</div>
            ))}
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-white/55">
            {decision.trade_validation.checks.map((c, i) => (
              <span key={i} title={c.detail}>
                <span style={{ color: !c.available ? "#8b93a7" : c.passed ? "#19e57f" : "#ff4d4d" }}>
                  {!c.available ? "—" : c.passed ? "✓" : "✕"}
                </span>{" "}
                {c.name}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="grid grid-cols-6 divide-x divide-white/10 border-t border-white/10 py-3">
        <Stat
          label="Confidence"
          value={`${Math.round(decision.confidence)}%`}
          color={decision.confidence >= 80 ? "#19e57f" : decision.confidence >= 65 ? "#f5b301" : "#ff6b6b"}
        />
        <Stat
          label="Risk"
          value={<span className="capitalize">{decision.risk_level.toLowerCase()}</span>}
          color={decision.risk_level === "LOW" ? "#19e57f" : decision.risk_level === "HIGH" ? "#ff6b6b" : "#f5b301"}
        />
        <Stat label="Probability" value={decision.win_probability != null ? `${Math.round(decision.win_probability)}%` : "—"} />
        <Stat
          label="Suggested Lots"
          value={decision.suggested_lots != null ? String(decision.suggested_lots) : "—"}
          color={decision.suggested_lots != null ? "#19e57f" : undefined}
        />
        <Stat
          label="Expected Move"
          value={move != null ? <span style={{ color: t.accent }}>{`+${move} pts`}</span> : "—"}
        />
        <Stat label="Next Review" value={`${mm}:${ss}`} />
      </div>
    </div>
  );
}
