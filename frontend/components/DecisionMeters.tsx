"use client";
import { Decision } from "@/lib/types";

/* ------------------------------------------------------------------ helpers */

const OPP_COLOR: Record<string, string> = {
  VERY_HIGH: "#19e57f",
  HIGH: "#54d68a",
  MEDIUM: "#f4c025",
  LOW: "#f0883e",
  AVOID: "#8b93a7",
};

const RISK_COLOR: Record<string, string> = {
  VERY_LOW: "#19e57f",
  LOW: "#54d68a",
  MEDIUM: "#f4c025",
  HIGH: "#f0883e",
  EXTREME: "#ff4d4d",
};

const HEALTH_COLOR: Record<string, string> = {
  HEALTHY: "#19e57f",
  NEUTRAL: "#f4c025",
  DANGEROUS: "#ff4d4d",
};

export function pretty(s: string | null | undefined): string {
  if (!s) return "—";
  return s.replace(/_/g, " ");
}

function scoreColor(v: number): string {
  if (v >= 70) return "#19e57f";
  if (v >= 55) return "#54d68a";
  if (v >= 45) return "#f4c025";
  if (v >= 30) return "#f0883e";
  return "#ff4d4d";
}

/* A labelled segmented meter (5 buckets) with the active bucket highlighted. */
function SegMeter({
  label,
  value,
  buckets,
  color,
  hint,
}: {
  label: string;
  value: string;
  buckets: string[];
  color: string;
  hint?: string;
}) {
  const activeIdx = buckets.indexOf(value);
  return (
    <div className="flex flex-col gap-1.5" title={hint}>
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] uppercase tracking-wider text-white/45">{label}</span>
        <span className="text-base font-black" style={{ color }}>
          {pretty(value)}
        </span>
      </div>
      <div className="flex gap-1">
        {buckets.map((b, i) => (
          <div
            key={b}
            className="h-1.5 flex-1 rounded-full"
            style={{
              background: i <= activeIdx && activeIdx >= 0 ? color : "rgba(255,255,255,0.08)",
            }}
          />
        ))}
      </div>
    </div>
  );
}

/* A horizontal 0..100 bar. */
function Bar({ label, value, color }: { label: string; value: number; color?: string }) {
  const c = color ?? scoreColor(value);
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium text-white/60 w-16 shrink-0 capitalize">{label}</span>
      <div className="flex-1 h-2 rounded-full bg-white/8 overflow-hidden">
        <div
          className="h-full rounded-full"
          style={{ width: `${Math.max(0, Math.min(100, value))}%`, background: c, transition: "width .5s ease" }}
        />
      </div>
      <span className="text-sm font-black tabular-nums w-8 text-right" style={{ color: c }}>
        {Math.round(value)}
      </span>
    </div>
  );
}

/* ------------------------------------------------------ Opportunity + Risk */

export function OpportunityRiskMeters({ decision }: { decision: Decision }) {
  return (
    <div className="card h-full flex flex-col gap-3 justify-center">
      <div className="card-title">Opportunity &amp; Risk</div>
      <SegMeter
        label="Opportunity Meter"
        value={decision.opportunity_label}
        buckets={["AVOID", "LOW", "MEDIUM", "HIGH", "VERY_HIGH"]}
        color={OPP_COLOR[decision.opportunity_label] ?? "#8b93a7"}
        hint="How attractive the current setup is for a fresh entry."
      />
      <SegMeter
        label="Risk Meter"
        value={decision.risk_meter}
        buckets={["VERY_LOW", "LOW", "MEDIUM", "HIGH", "EXTREME"]}
        color={RISK_COLOR[decision.risk_meter] ?? "#f4c025"}
        hint="Estimated risk of the current conditions (volatility, disagreement, conflict)."
      />
      <div className="flex items-center justify-between pt-1 border-t border-white/8">
        <span className="text-[11px] uppercase tracking-wider text-white/45">Signal Strength</span>
        <span className="text-xl font-black tabular-nums text-white/90">
          {Math.round(decision.signal_strength)}
        </span>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------- Trade Score */

export function TradeScoreCard({ decision }: { decision: Decision }) {
  const score = Math.round(decision.trade_score);
  const c = scoreColor(score);
  const bd = decision.trade_score_breakdown ?? {};
  const cats: [string, string][] = [
    ["trend", "Trend"],
    ["premium", "Premium"],
    ["oi", "OI"],
    ["volume", "Volume"],
    ["news", "News"],
  ];
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Trade Score</div>
      <div className="flex items-center gap-4 py-1">
        <div className="text-5xl font-black tabular-nums" style={{ color: c }}>
          {score}
          <span className="text-lg text-white/30 font-bold">/100</span>
        </div>
        <div className="text-[11px] text-white/45 leading-tight">
          composite quality of the
          <br />
          current setup
        </div>
      </div>
      <div className="flex flex-col gap-1.5 mt-1">
        {cats.map(([k, label]) => (
          <Bar key={k} label={label} value={bd[k] ?? 50} />
        ))}
      </div>
    </div>
  );
}

/* --------------------------------------------------------- Premium Quality */

export function PremiumQualityCard({ decision }: { decision: Decision }) {
  const q = decision.premium_quality;
  const health = decision.premium_health;
  const hc = health ? HEALTH_COLOR[health] ?? "#f4c025" : "#8b93a7";
  const mom = decision.premium_momentum;
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex justify-between">
        <span>Premium Quality</span>
        <span
          className="text-[10px] font-bold normal-case tracking-normal px-2 py-0.5 rounded-full"
          style={{ color: hc, border: `1px solid ${hc}66` }}
        >
          {pretty(health)}
        </span>
      </div>
      <div className="flex items-center gap-3 py-1">
        <div className="text-4xl font-black tabular-nums" style={{ color: hc }}>
          {q != null ? Math.round(q) : "—"}
        </div>
        <div className="text-[11px] text-white/45 leading-tight">
          is the option we&apos;d buy
          <br />
          behaving healthily?
        </div>
      </div>
      <div className="flex flex-col gap-1 mt-1 text-xs">
        <div className="flex justify-between">
          <span className="text-white/50">Momentum</span>
          <span className="tabular-nums" style={{ color: (mom ?? 0) >= 0 ? "#19e57f" : "#ff6b6b" }}>
            {mom != null ? (mom > 0 ? "+" : "") + Math.round(mom) : "—"}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-white/50">Speed (%/bar)</span>
          <span className="tabular-nums text-white/80">
            {decision.premium_velocity != null ? decision.premium_velocity.toFixed(2) : "—"}
          </span>
        </div>
        <div className="flex justify-between">
          <span className="text-white/50">Acceleration</span>
          <span className="tabular-nums text-white/80">
            {decision.premium_acceleration != null ? decision.premium_acceleration.toFixed(2) : "—"}
          </span>
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------- Trap Detector */

export function TrapDetectorCard({ decision }: { decision: Decision }) {
  const rows: [string, number][] = [
    ["Buy Trap", decision.buy_trap_prob],
    ["Sell Trap", decision.sell_trap_prob],
    ["Fake Breakout", decision.fake_breakout_prob],
    ["Fake Breakdown", decision.fake_breakdown_prob],
  ];
  const trapColor = (v: number) => (v >= 60 ? "#ff4d4d" : v >= 35 ? "#f0883e" : "#19e57f");
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title">Trap Detector</div>
      <div className="flex flex-col gap-2 mt-1">
        {rows.map(([label, v]) => (
          <Bar key={label} label={label} value={v} color={trapColor(v)} />
        ))}
      </div>
      <div className="text-[10px] text-white/35 mt-auto pt-2 leading-snug">
        Probability the current move is a trap against a buyer — from detected
        fake-breaks &amp; liquidity sweeps. Higher = more caution.
      </div>
    </div>
  );
}

/* ----------------------------------------------------------- Smart Money */

export function SmartMoneyCard({ decision }: { decision: Decision }) {
  const sm = decision.smart_money;
  const c = sm === "BULLISH" ? "#19e57f" : sm === "BEARISH" ? "#ff4d4d" : "#8b93a7";
  return (
    <div className="card h-full flex flex-col justify-center">
      <div className="card-title">Estimated Smart Money</div>
      <div className="text-2xl font-black" style={{ color: c }}>
        {pretty(sm)}
      </div>
      <div className="text-[10px] text-white/40 mt-1 leading-snug">
        {decision.smart_money_label ||
          "Estimate from volume + OI + price movement — NOT real order-flow."}
      </div>
    </div>
  );
}
