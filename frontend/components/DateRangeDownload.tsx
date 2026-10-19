"use client";
import { useState } from "react";

// From/To date pickers + a CSV link. Dates are the browser's local days
// (IST on the user's laptop); To is inclusive.
export default function DateRangeDownload({ href, label = "Download CSV" }: { href: string; label?: string }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const q: string[] = [];
  if (from) q.push(`since=${Math.floor(new Date(`${from}T00:00:00`).getTime() / 1000)}`);
  if (to) q.push(`until=${Math.floor(new Date(`${to}T00:00:00`).getTime() / 1000) + 86400}`);
  const url = q.length ? `${href}${href.includes("?") ? "&" : "?"}${q.join("&")}` : href;
  const input = "rounded bg-white/10 px-2 py-1 text-xs [color-scheme:dark]";
  return (
    <span className="inline-flex items-center gap-1.5 flex-wrap">
      <input type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={input} title="From date" />
      <span className="text-xs text-white/40">to</span>
      <input type="date" value={to} onChange={(e) => setTo(e.target.value)} className={input} title="To date (inclusive)" />
      <a href={url} target="_blank" rel="noreferrer"
         className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-white/15 hover:bg-white/5">
        {label}
      </a>
    </span>
  );
}
