"use client";
import clsx from "clsx";
import { Indicators, ScoreBreakdown } from "@/lib/types";

function tone(bias: string | null | undefined): string {
  if (bias === "BULLISH") return "text-[#19e57f]";
  if (bias === "BEARISH") return "text-[#ff6b6b]";
  return "text-white/60";
}

function pretty(v: string | null | undefined): string {
  if (!v || v === "NONE") return "—";
  return v.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}

function Row({
  level,
  name,
  value,
  bias,
  note,
}: {
  level: string;
  name: string;
  value: string;
  bias?: string | null;
  note?: string;
}) {
  return (
    <div className="flex items-baseline gap-2 py-1 border-b border-white/5 last:border-0">
      <span className="text-[10px] font-bold text-white/30 w-6 shrink-0">{level}</span>
      <span className="text-[11px] text-white/50 w-[92px] shrink-0">{name}</span>
      <span className={clsx("text-[12px] font-semibold flex-1", tone(bias))}>{value}</span>
      {note && <span className="text-[10px] text-white/30">{note}</span>}
    </div>
  );
}

export default function DecisionStack({
  ind,
  breakdown,
}: {
  ind: Indicators;
  breakdown: ScoreBreakdown[];
}) {
  // Level 1 headline: strongest price-action read (trap > structure)
  const trap =
    ind.fake_signal && ind.fake_signal !== "NONE"
      ? pretty(ind.fake_signal)
      : ind.liquidity_sweep && ind.liquidity_sweep !== "NONE"
      ? pretty(ind.liquidity_sweep)
      : ind.pullback && ind.pullback !== "NONE"
      ? pretty(ind.pullback)
      : ind.breakout && ind.breakout !== "NONE"
      ? pretty(ind.breakout)
      : pretty(ind.market_structure);
  const paBias =
    ind.market_structure === "HH_HL"
      ? "BULLISH"
      : ind.market_structure === "LH_LL"
      ? "BEARISH"
      : null;

  // Level 7 — do lagging indicators confirm? (agreement of confirm-only votes)
  const confirmVotes = breakdown.filter((b) => b.confirm_only);
  const cBull = confirmVotes.filter((b) => b.signal === "BULLISH").length;
  const cBear = confirmVotes.filter((b) => b.signal === "BEARISH").length;
  const confirmText =
    confirmVotes.length === 0
      ? "silent"
      : cBull > cBear
      ? `confirm ↑ (${cBull}/${confirmVotes.length})`
      : cBear > cBull
      ? `confirm ↓ (${cBear}/${confirmVotes.length})`
      : "mixed";

  const newsText = ind.news_material
    ? `Material · ${Math.round(ind.news_bull_pct ?? 50)}% bull / ${Math.round(
        ind.news_bear_pct ?? 50
      )}% bear`
    : "No high-impact news";

  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-[var(--accent)] pulse" /> Decision Stack
        <span className="ml-auto text-[10px] font-normal text-white/30 normal-case tracking-normal">
          price action → premium → OI → indicators confirm
        </span>
      </div>
      <div className="px-4 pb-3 pt-1 flex flex-col justify-between flex-1">
        <Row level="L1" name="Price Action" value={trap} bias={paBias} note="highest weight" />
        <Row
          level="L2"
          name="Premium Beh."
          value={`${pretty(ind.premium_call_state)} CE · ${pretty(ind.premium_put_state)} PE`}
          bias={ind.premium_bias}
        />
        <Row
          level="L3"
          name="Open Interest"
          value={`${pretty(ind.oi_state)}${
            ind.oi_writing && ind.oi_writing !== "NONE" ? " · " + pretty(ind.oi_writing) : ""
          }`}
          bias={ind.oi_bias}
          note={ind.oi_change_pct != null ? `${ind.oi_change_pct > 0 ? "+" : ""}${ind.oi_change_pct}% OI` : undefined}
        />
        <Row level="L4" name="Volume" value={ind.volume_level ?? "—"} bias={ind.volume_spike ? "BULLISH" : null} />
        {ind.poc != null && (
          <Row
            level="VP"
            name="Volume Profile"
            value={`POC ${ind.poc} · ${pretty(ind.value_area_position)}`}
            bias={
              ind.value_area_position === "ABOVE_VALUE"
                ? "BULLISH"
                : ind.value_area_position === "BELOW_VALUE"
                ? "BEARISH"
                : null
            }
            note={ind.val != null && ind.vah != null ? `VA ${ind.val}–${ind.vah}` : undefined}
          />
        )}
        <Row
          level="L5"
          name="Smart Money"
          value={pretty(ind.smart_money)}
          bias={ind.smart_money}
          note="estimated"
        />
        <Row level="L6" name="News" value={newsText} bias={ind.news_material ? undefined : null} />
        <Row level="L7" name="Indicators" value={confirmText} note="confirm only" />
        <div className="text-[9.5px] text-white/25 pt-1 leading-tight">
          {ind.smart_money_label ?? "Estimated (OI+volume proxy — not order-flow)"}
        </div>
      </div>
    </div>
  );
}
