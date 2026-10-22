"use client";
import { useEffect, useState } from "react";

// From/To date pickers + a CSV link. Dates are the browser's local days
// (IST on the user's laptop); To is inclusive. ``onRange`` also filters the
// table on screen with the same dates (null = open end).
export default function DateRangeDownload({ href, label = "Download CSV", onRange }: {
  href: string; label?: string; onRange?: (since: number | null, until: number | null) => void;
}) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const since = from ? Math.floor(new Date(`${from}T00:00:00`).getTime() / 1000) : null;
  const until = to ? Math.floor(new Date(`${to}T00:00:00`).getTime() / 1000) + 86400 : null;
  useEffect(() => { onRange?.(since, until); }, [since, until]); // eslint-disable-line react-hooks/exhaustive-deps
  const q: string[] = [];
  if (since !== null) q.push(`since=${since}`);
  if (until !== null) q.push(`until=${until}`);
  const url = q.length ? `${href}${href.includes("?") ? "&" : "?"}${q.join("&")}` : href;
  const input = "rounded bg-white/10 px-2 py-1 text-xs [color-scheme:dark]";
  return (
    <span className="inline-flex items-center gap-1.5 flex-wrap">
      <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={input} title="From date" />
      <span className="text-xs text-white/40">to</span>
      <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={input} title="To date (inclusive)" />
      {onRange && (from || to) && (
        <button onClick={() => { setFrom(""); setTo(""); }} className="text-xs text-white/50 hover:text-white" title="Show all dates">✕</button>
      )}
      <a href={url} target="_blank" rel="noreferrer"
         className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5">
        {label}
      </a>
    </span>
  );
}
