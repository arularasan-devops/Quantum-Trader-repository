"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import type { Snapshot } from "@/lib/types";

interface Row {
  symbol: string; strike: number; type: "CE" | "PE"; ltp: number;
  bid: number | null; ask: number | null; bid_size: number | null; ask_size: number | null;
  spread: number | null; spread_pct: number | null; book_age_sec: number | null;
  executable: boolean; cost_per_lot_at_ask: number | null;
  iv: number; delta: number; gamma: number; theta: number; vega: number;
  oi: number; oi_change: number; volume: number;
}
interface Board {
  instrument: string; exchange: string | null; spot: number; lot_size: number | null;
  days_to_expiry: number | null; is_real_feed: boolean; trade_mode: string; feed_mode: string;
  feed: { live: boolean; underlying_age_sec: number | null; option_age_sec: number | null; stale_after_sec: number } | null;
  greeks_source: string;
  pcr: { available: boolean; pcr: number | null; bias: string | null };
  max_pain: { available: boolean; strike: number | null };
  max_oi_call_strike: number | null; max_oi_put_strike: number | null; expected_move: number | null;
  signal: { signal: string; confidence: number; recommended_option: string | null; entry_trigger: string | null;
    regime: string | null; current_premium: number | null; stop_loss: number | null;
    target1: number | null; target2: number | null; target3: number | null };
  auto_entry_rule: string;
  position: { option_symbol: string | null; entry_premium: number | null; current_premium: number | null;
    quantity_lots: number; total_costs: number; net_pnl: number };
  executable_count: number; rows: Row[];
}

const f = (v: number | null | undefined, d = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "—" : v.toLocaleString("en-IN", { maximumFractionDigits: d });

/** OPTIONS board — chain with the executable book, liquidity, OI and model Greeks.
 * Read-only: shows what the paper auto-trader can and cannot act on; no order path. */
export default function OptionsBoard({ snap }: { snap: Snapshot | null }) {
  const inst = snap?.instrument ?? "";
  const [b, setB] = useState<Board | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [side, setSide] = useState<"ALL" | "CE" | "PE">("ALL");
  const [onlyExec, setOnlyExec] = useState(false);

  useEffect(() => {
    let alive = true;
    const load = () =>
      fetch(`${API_BASE}/api/options-board?instrument=${encodeURIComponent(inst)}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
        .then((j) => { if (alive) { setB(j); setErr(null); } })
        .catch((e) => alive && setErr(String(e.message || e)));
    load();
    const t = setInterval(load, 5000);
    return () => { alive = false; clearInterval(t); };
  }, [inst]);

  if (!b) return <div className="p-6 text-sm text-white/50">{err ? `Options board unavailable: ${err}` : "Loading option chain…"}</div>;

  const ages = [b.feed?.underlying_age_sec, b.feed?.option_age_sec].filter((a): a is number => typeof a === "number");
  const stale = b.is_real_feed && (!ages.length || Math.max(...ages) > (b.feed?.stale_after_sec ?? 10));
  const rows = b.rows.filter((r) => (side === "ALL" || r.type === side) && (!onlyExec || r.executable));
  const s = b.signal;
  const p = b.position;

  return (
    <div className="h-full overflow-auto p-4 space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-lg font-semibold">{b.instrument} options</div>
        <span className="text-xs text-white/50">{b.exchange} · lot {f(b.lot_size, 0)} · {b.days_to_expiry ? `${f(b.days_to_expiry, 0)} days to expiry` : "expiry unknown"}</span>
        <span className="text-xs px-2 py-0.5 rounded bg-sky-500/15 text-sky-300">{b.trade_mode.toUpperCase()}</span>
        <span className={`text-xs px-2 py-0.5 rounded ${b.is_real_feed ? "bg-emerald-500/15 text-emerald-300" : "bg-white/10 text-white/60"}`}>
          {b.is_real_feed ? `LIVE feed (${b.feed_mode})` : "SIMULATED feed"}
        </span>
        {stale && <span className="text-xs px-2 py-0.5 rounded bg-rose-500/20 text-rose-300">Feed stale: auto-entry blocked</span>}
        {!b.is_real_feed && <span className="text-xs text-amber-300/80">Simulated chain has no bid/ask: prices are synthetic, not executable.</span>}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-6 gap-2">
        {[
          ["Spot / futures", f(b.spot)],
          ["PCR", b.pcr.available ? `${f(b.pcr.pcr)} ${b.pcr.bias ?? ""}` : "—"],
          ["Max pain", f(b.max_pain.strike, 0)],
          ["Max OI CE / PE", `${f(b.max_oi_call_strike, 0)} / ${f(b.max_oi_put_strike, 0)}`],
          ["Expected move", f(b.expected_move)],
          ["Executable quotes", `${b.executable_count} / ${b.rows.length}`],
        ].map(([k, v]) => (
          <div key={k} className="rounded bg-white/5 px-3 py-2">
            <div className="text-[10px] uppercase text-white/40">{k}</div>
            <div className="font-mono">{v}</div>
          </div>
        ))}
      </div>

      <div className="grid md:grid-cols-2 gap-2">
        <div className="rounded bg-white/5 px-3 py-2 space-y-1">
          <div className="text-[10px] uppercase text-white/40">Engine call</div>
          <div className="font-mono">
            {s.signal} · {f(s.confidence, 1)}% · {s.entry_trigger ?? "—"} in {s.regime ?? "—"} · {s.recommended_option ?? "no leg"}
          </div>
          <div className="font-mono text-xs text-white/60">
            entry {f(s.current_premium)} · stop {f(s.stop_loss)} · T1 {f(s.target1)} · T2 {f(s.target2)} · T3 {f(s.target3)}
          </div>
          <div className="text-[11px] text-white/40">{b.auto_entry_rule}</div>
        </div>
        <div className="rounded bg-white/5 px-3 py-2 space-y-1">
          <div className="text-[10px] uppercase text-white/40">Paper position</div>
          {p.option_symbol ? (
            <div className="font-mono">
              {p.option_symbol} × {p.quantity_lots} lots · entry {f(p.entry_premium)} · now {f(p.current_premium)} · costs ₹{f(p.total_costs, 0)} ·{" "}
              <span className={p.net_pnl >= 0 ? "text-emerald-400" : "text-rose-400"}>net ₹{f(p.net_pnl, 0)}</span>
            </div>
          ) : (
            <div className="text-white/50">Flat: no open paper option position.</div>
          )}
          <div className="text-[11px] text-white/40">{b.greeks_source}</div>
        </div>
      </div>

      <div className="flex items-center gap-2 text-xs">
        {(["ALL", "CE", "PE"] as const).map((k) => (
          <button key={k} onClick={() => setSide(k)} className={`px-2 py-1 rounded ${side === k ? "bg-white/15" : "bg-white/5 text-white/60"}`}>{k}</button>
        ))}
        <label className="flex items-center gap-1 ml-3 text-white/60">
          <input type="checkbox" checked={onlyExec} onChange={(e) => setOnlyExec(e.target.checked)} /> executable only
        </label>
        {err && <span className="text-rose-300 ml-auto">refresh failed: {err}</span>}
      </div>

      <table className="w-full text-xs font-mono">
        <thead className="text-white/40 text-[10px] uppercase">
          <tr className="text-right">
            <th className="text-left">Contract</th><th>Strike</th><th>LTP</th><th>Bid</th><th>Ask</th><th>Spread %</th>
            <th>Book age</th><th>₹ / lot @ask</th><th>IV</th><th>Δ</th><th>Γ</th><th>Θ</th><th>Vega</th><th>OI</th><th>ΔOI</th><th>Vol</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.symbol} className={`text-right border-t border-white/5 ${r.symbol === s.recommended_option ? "bg-sky-500/10" : ""} ${r.executable ? "" : "text-white/40"}`}>
              <td className="text-left">{r.symbol} <span className={r.type === "CE" ? "text-emerald-400" : "text-rose-400"}>{r.type}</span></td>
              <td>{f(r.strike, 0)}</td><td>{f(r.ltp)}</td><td>{f(r.bid)}</td><td>{f(r.ask)}</td>
              <td className={r.spread_pct !== null && r.spread_pct > 5 ? "text-amber-300" : ""}>{r.executable ? f(r.spread_pct) : "no book"}</td>
              <td>{r.book_age_sec === null ? "—" : `${f(r.book_age_sec, 1)}s`}</td>
              <td>{f(r.cost_per_lot_at_ask, 0)}</td>
              <td>{f(r.iv * 100, 1)}%</td><td>{f(r.delta)}</td><td>{f(r.gamma, 4)}</td><td>{f(r.theta)}</td><td>{f(r.vega)}</td>
              <td>{f(r.oi, 0)}</td><td>{f(r.oi_change, 0)}</td><td>{f(r.volume, 0)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
