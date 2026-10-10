"use client";
import { NewsItem } from "@/lib/types";

const TONE: Record<string, string> = {
  BULLISH: "#19e57f",
  BEARISH: "#ff6b6b",
  NEUTRAL: "#8b97a5",
};

export default function NewsFeed({
  news,
  sentiment,
  score,
}: {
  news: NewsItem[];
  sentiment: string;
  score: number;
}) {
  const uniq = dedupe(news).slice(0, 8);
  const tone = score > 0.1 ? "#19e57f" : score < -0.1 ? "#ff6b6b" : "#8b97a5";
  return (
    <div className="card h-full flex flex-col">
      <div className="card-title flex items-center justify-between">
        <span className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full pulse" style={{ background: tone }} /> News &amp; Geopolitical Feed
        </span>
        <span className="font-normal normal-case" style={{ color: tone }}>
          {sentiment} · {score >= 0 ? "+" : ""}{score.toFixed(2)}
        </span>
      </div>
      <div className="px-3 pb-3 pt-1 flex-1 overflow-y-auto space-y-1.5">
        {uniq.length === 0 && <div className="text-xs text-white/40 py-2">No headlines yet.</div>}
        {uniq.map((n, i) => (
          <div key={i} className="flex items-start gap-2 text-xs border-b border-white/5 pb-1.5">
            <span
              className="mt-0.5 shrink-0 text-[9px] font-bold uppercase tracking-wide px-1.5 py-0.5 rounded"
              style={{ color: TONE[n.sentiment], border: `1px solid ${TONE[n.sentiment]}55` }}
            >
              {n.sentiment}
            </span>
            <div className="min-w-0">
              <span className="text-white/40 uppercase text-[10px] mr-1">{n.source}</span>
              <span className="text-white/85">{n.headline}</span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function dedupe(news: NewsItem[]): NewsItem[] {
  const seen = new Set<string>();
  const out: NewsItem[] = [];
  for (const n of news) {
    const k = `${n.source}|${n.headline}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push(n);
    }
  }
  return out;
}
