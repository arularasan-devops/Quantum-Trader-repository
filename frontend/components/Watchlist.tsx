"use client";
import { OptionRecommendation } from "@/lib/types";
import { buyOption } from "@/lib/api";

export default function Watchlist({ items }: { items: OptionRecommendation[] }) {
  const rank = ["Top Pick", "2nd Best", "3rd Best"];
  return (
    <div className="panel">
      <div className="panel-title">Watchlist · Recommended Options</div>
      <div className="px-2 pb-2 space-y-1">
        {items.map((o, i) => (
          <button
            key={o.option_symbol}
            onClick={() => buyOption(o.option_symbol)}
            className="w-full flex items-center justify-between bg-panel2 hover:bg-line rounded-lg px-3 py-2 text-left"
          >
            <div>
              <div className="text-[10px] uppercase tracking-wider text-muted">{rank[i] ?? `#${i + 1}`}</div>
              <div className="text-sm font-semibold">{o.option_symbol}</div>
              <div className="text-xs text-muted">₹{o.premium}</div>
            </div>
            <div className="text-right">
              <div className="text-lg font-bold tabular-nums text-buy">{Math.round(o.confidence)}%</div>
              <div className="text-[10px] text-muted">confidence</div>
            </div>
          </button>
        ))}
        {items.length === 0 && <div className="text-xs text-muted px-3 py-4">No candidates</div>}
      </div>
    </div>
  );
}
