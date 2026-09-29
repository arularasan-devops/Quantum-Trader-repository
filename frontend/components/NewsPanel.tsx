"use client";
import clsx from "clsx";
import { NewsItem } from "@/lib/types";

const TONE: Record<string, string> = {
  BULLISH: "text-buy",
  BEARISH: "text-exit",
  NEUTRAL: "text-muted",
};

export default function NewsPanel({
  news,
  sentiment,
  score,
}: {
  news: NewsItem[];
  sentiment: string;
  score: number;
}) {
  return (
    <div className="panel">
      <div className="panel-title flex justify-between items-center">
        <span>News & Geopolitical Sentiment</span>
        <span className={clsx("font-bold", TONE[sentiment])}>
          {sentiment} {score >= 0 ? "+" : ""}{score}
        </span>
      </div>
      <div className="px-2 pb-2 space-y-1 max-h-56 overflow-y-auto">
        {news.map((n, i) => (
          <div key={i} className="bg-panel2 rounded-lg px-3 py-1.5">
            <div className="flex justify-between text-[10px] uppercase tracking-wider text-muted">
              <span>{n.source} · {n.category}</span>
              <span className={TONE[n.sentiment]}>{n.sentiment}</span>
            </div>
            <div className="text-xs text-ink">{n.headline}</div>
          </div>
        ))}
        {news.length === 0 && <div className="text-xs text-muted px-2 py-3">No headlines</div>}
      </div>
    </div>
  );
}
