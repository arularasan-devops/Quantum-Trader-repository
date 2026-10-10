"use client";
import { Snapshot } from "@/lib/types";

/**
 * Display-only "Buy Confidence Checklist".
 *
 * It READS existing snapshot fields produced by the frozen Phase 1.5 engine and
 * ticks/crosses the conditions a disciplined buyer should confirm before
 * entering, then prints one clear suggestion. It adds NO new indicators,
 * thresholds or decision rules, changes nothing in the decision, places no
 * orders, and is excluded from validation / backtest / performance.
 */
export interface Check {
  label: string;
  ok: boolean;
  detail: string;
  critical: boolean;
}

export function buildChecks(snap: Snapshot): Check[] {
  const d = snap.decision;
  const ind = snap.indicators;
  const risk = snap.risk;

  const bd = d.trade_score_breakdown ?? {};
  const trend = bd["trend"] ?? 50;
  const premium = bd["premium"] ?? 50;
  const oi = bd["oi"] ?? 50;

  const wantCall = d.option_type === "CE";
  const trapAgainst = wantCall ? d.buy_trap_prob : d.sell_trap_prob;
  const fakeAgainst = wantCall ? d.fake_breakout_prob : d.fake_breakdown_prob;
  const worstTrap = Math.max(trapAgainst ?? 0, fakeAgainst ?? 0);

  const entry = d.current_premium ?? (d.entry_range ? (d.entry_range[0] + d.entry_range[1]) / 2 : null);
  let rr: number | null = null;
  if (entry != null && d.target1 != null && d.stop_loss != null && entry - d.stop_loss > 0) {
    rr = (d.target1 - entry) / (entry - d.stop_loss);
  }
  const inZone =
    entry != null && d.entry_range != null
      ? entry >= d.entry_range[0] && entry <= d.entry_range[1]
      : false;
  const pullback = /PULLBACK|RETEST/i.test(d.entry_trigger ?? "");
  const volLevel = (ind.volume_level ?? "").toLowerCase();

  // Critical first, then supporting.
  return [
    {
      label: "Signal is BUY & locked",
      ok: d.signal === "BUY" && d.locked,
      detail: `${d.signal}${d.locked ? " · locked" : ""}`,
      critical: true,
    },
    {
      label: "Confidence ≥ 80%",
      ok: d.confidence >= 80,
      detail: `${Math.round(d.confidence)}%`,
      critical: true,
    },
    {
      label: "Trend + Premium + OI agree",
      ok: trend >= 60 && premium >= 60 && oi >= 60,
      detail: `T${Math.round(trend)} P${Math.round(premium)} OI${Math.round(oi)}`,
      critical: true,
    },
    {
      label: "No trap / fake-break",
      ok: worstTrap <= 30,
      detail: `${Math.round(worstTrap)}%`,
      critical: true,
    },
    {
      label: "Capital / risk allows it",
      ok: risk?.can_trade ?? false,
      detail: risk ? (risk.can_trade ? `${risk.suggested_lots} lot(s)` : "blocked") : "—",
      critical: true,
    },
    {
      label: "Risk LOW & Score ≥ 85",
      ok: d.risk_level === "LOW" && d.trade_score >= 85,
      detail: `${d.risk_level ?? "—"} · ${Math.round(d.trade_score)}`,
      critical: false,
    },
    {
      label: "Premium healthy",
      ok: (d.premium_quality ?? 0) >= 50 && d.premium_health !== "DANGEROUS",
      detail: `${d.premium_quality != null ? Math.round(d.premium_quality) : "—"}`,
      critical: false,
    },
    {
      label: "Pullback & in entry zone",
      ok: pullback && inZone,
      detail: inZone ? "in zone" : "out of zone",
      critical: false,
    },
    {
      label: "Volume not LOW",
      ok: volLevel !== "" && volLevel !== "low",
      detail: ind.volume_level ?? "—",
      critical: false,
    },
    {
      label: "Reward:Risk ≥ 1.5",
      ok: rr != null && rr >= 1.5,
      detail: rr != null ? `${rr.toFixed(1)}:1` : "—",
      critical: false,
    },
  ];
}

export default function ConfidenceChecklist({ snap }: { snap: Snapshot }) {
  const d = snap.decision;
  const checks = buildChecks(snap);
  const critical = checks.filter((c) => c.critical);
  const support = checks.filter((c) => !c.critical);
  const met = checks.filter((c) => c.ok).length;
  const total = checks.length;
  const failedCritical = critical.filter((c) => !c.ok);

  let verdict: string;
  let color: string;
  let suggestion: string;
  if (d.signal !== "BUY") {
    verdict = "NO BUY";
    color = "#8b97a5";
    suggestion = `Stand aside — engine says ${d.signal}, not a buy setup.`;
  } else if (failedCritical.length > 0) {
    verdict = "NOT READY";
    color = "#ff4d4d";
    suggestion = `Don't buy yet — ${failedCritical.length} must-pass check${failedCritical.length === 1 ? "" : "s"} failing: ${failedCritical.map((c) => c.label).join(", ")}.`;
  } else if (met >= total - 1) {
    verdict = "BUY-READY";
    color = "#19e57f";
    suggestion =
      d.entry_range != null
        ? `Strong setup — buy ${d.option_type ?? ""} on a pullback into ₹${d.entry_range[0]}–${d.entry_range[1]}, stop ₹${d.stop_loss ?? "—"}.`
        : `Strong setup — all must-pass checks green.`;
  } else {
    verdict = "OK — CONFIRM";
    color = "#f5b301";
    suggestion = "Buyable but not perfect — wait for the amber supporting checks to turn green.";
  }

  const Row = ({ c, big }: { c: Check; big?: boolean }) => (
    <div className="flex items-center gap-2">
      <span
        className={`shrink-0 grid place-items-center rounded-full font-bold ${big ? "w-6 h-6 text-[13px]" : "w-4 h-4 text-[10px]"}`}
        style={{ background: c.ok ? "#19e57f22" : "#ff4d4d1a", color: c.ok ? "#19e57f" : "#ff6b6b" }}
      >
        {c.ok ? "✓" : "✕"}
      </span>
      <span
        className={big ? "font-semibold" : ""}
        style={{ color: c.ok ? (big ? "#e7ecf2" : "#b9c2cc") : big ? "#ff9a9a" : "#7d8894", fontSize: big ? 15 : 11 }}
      >
        {c.label}
      </span>
      <span className="ml-auto text-white/45 tabular-nums font-semibold" style={{ fontSize: big ? 13 : 10 }}>
        {c.detail}
      </span>
    </div>
  );

  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] p-4 h-full flex flex-col">
      <div className="flex items-center justify-between">
        <div className="text-base font-bold text-white/90">Buy Confidence Checklist</div>
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-white/55">{met}/{total}</span>
          <span
            className="text-sm font-extrabold px-3 py-1 rounded-md border"
            style={{ color, borderColor: `${color}66`, background: `${color}18` }}
          >
            {verdict}
          </span>
        </div>
      </div>

      {/* Clear one-line suggestion */}
      <div
        className="mt-2 rounded-lg px-3 py-2.5 text-base font-bold leading-snug"
        style={{ color, background: `${color}12`, border: `1px solid ${color}33` }}
      >
        {suggestion}
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-2 mt-3 flex-1 min-h-0 overflow-auto content-start">
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wide text-white/35 mb-1.5">
            Must pass
          </div>
          <div className="flex flex-col gap-1.5">
            {critical.map((c) => (
              <Row key={c.label} c={c} big />
            ))}
          </div>
        </div>
        <div>
          <div className="text-[10px] font-bold uppercase tracking-wide text-white/35 mb-1.5">
            Supporting
          </div>
          <div className="flex flex-col gap-1.5">
            {support.map((c) => (
              <Row key={c.label} c={c} />
            ))}
          </div>
        </div>
      </div>

      <div className="mt-auto pt-2 text-[10px] text-white/35 leading-relaxed">
        Display-only — reads the frozen engine&apos;s existing outputs, changes no rule and places no
        orders. Not part of validation / backtest / performance.
      </div>
    </div>
  );
}
