"use client";
import { useEffect, useRef, useState } from "react";
import { QuantumSignalView, SignalScan } from "@/lib/types";
import { fetchSignal, fetchSignalScan } from "@/lib/api";
import FuturesChart from "@/components/FuturesChart";
import AutoBotPanel from "@/components/AutoBotPanel";
import { playBuySound, playExitSound } from "@/lib/sound";

/**
 * Quantum Signal — the single-page decision tool. One job: show whether there
 * is a CLEAN, tradeable move right now, or tell you to stay out. It reads the
 * read-only /api/signal view (frozen engine + regime/volatility gate) and never
 * places a trade. Honest by design: fabricates no probabilities.
 */

const COLORS: Record<string, string> = {
  green: "#19e57f",
  amber: "#f4c025",
  grey: "#8b93a7",
  red: "#ff4d4d",
};

function actionTheme(action: string, actionable: boolean): { color: string; glow: string } {
  if (action.startsWith("BUY") && actionable) return { color: "#19e57f", glow: "rgba(25,229,127,0.18)" };
  if (action === "EXIT") return { color: "#ff4d4d", glow: "rgba(255,77,77,0.18)" };
  if (action === "HOLD") return { color: "#3aa0ff", glow: "rgba(58,160,255,0.16)" };
  return { color: "#8b93a7", glow: "rgba(139,147,167,0.10)" }; // WAIT
}

function fmt(n: number | null | undefined, d = 1): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("en-IN", { maximumFractionDigits: d });
}

function Dot({ color }: { color: string }) {
  return <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: COLORS[color] || color }} />;
}

export default function QuantumSignal({
  instrument,
  onInstrument,
}: {
  instrument: string;
  onInstrument: (s: string) => void;
}) {
  const [view, setView] = useState<QuantumSignalView | null>(null);
  const [scan, setScan] = useState<SignalScan | null>(null);
  const [auto, setAuto] = useState(true);
  const [stale, setStale] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const autoRef = useRef(auto);
  autoRef.current = auto;
  // Sound on bot entry/exit: fire a chime whenever the bot posts a NEW action
  // whose text is a BUY (entry) or EXIT. Tracks the last-action timestamp so we
  // only play once per event and never on the first render.
  const lastSoundTs = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    setView(null);
    const load = () =>
      fetchSignal(instrument)
        .then((d) => {
          if (!alive) return;
          setView(d);
          setStale(false);
        })
        .catch(() => alive && setStale(true));
    load();
    timer.current = setInterval(load, 3000);
    return () => {
      alive = false;
      if (timer.current) clearInterval(timer.current);
    };
  }, [instrument]);

  // Auto-scan: poll every instrument and jump the page to the best actionable
  // BUY so the user never has to pick one. Turned off the moment they toggle
  // Auto off or click a row manually.
  useEffect(() => {
    let alive = true;
    const scanLoad = () =>
      fetchSignalScan()
        .then((s) => {
          if (!alive) return;
          setScan(s);
          if (autoRef.current && s.best_actionable && s.best_actionable !== instrument) {
            onInstrument(s.best_actionable);
          }
        })
        .catch(() => {});
    scanLoad();
    const id = setInterval(scanLoad, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [instrument, onInstrument]);

  // Play a chime on each new bot BUY / EXIT action.
  const act = view?.auto_trade?.last_action ?? null;
  const actTs = view?.auto_trade?.last_action_time ?? null;
  useEffect(() => {
    if (actTs == null || act == null) return;
    if (lastSoundTs.current === null) {
      // First observation after mount/instrument switch — arm, don't play.
      lastSoundTs.current = actTs;
      return;
    }
    if (actTs === lastSoundTs.current) return;
    lastSoundTs.current = actTs;
    const a = act.toUpperCase();
    if (a.includes("EXIT")) playExitSound();
    else if (a.includes("BUY")) playBuySound();
  }, [act, actTs]);

  if (!view) {
    return (
      <div className="h-[70vh] grid place-items-center text-white/40">
        Loading Quantum Signal…
      </div>
    );
  }

  const th = actionTheme(view.action, view.actionable);
  const up = view.change_pct >= 0;
  const pos = view.position;
  const inPos = !!pos.option_symbol;
  const p = view.plan;
  const at = view.auto_trade;
  const ent = at?.entry ?? null;
  const history = at?.history ?? [];

  return (
    <div className="max-w-6xl mx-auto px-3 pb-16">
      {/* header: instrument picker + live price */}
      <div className="flex flex-wrap items-center gap-3 py-4">
        <div className="text-lg font-bold tracking-tight text-white">Quantum&nbsp;Signal</div>
        <select
          value={instrument}
          onChange={(e) => {
            // Manually picking an instrument must STICK — turn Auto off so the
            // auto-scan can't jump the page back to the best actionable BUY
            // (e.g. snapping from Natural Gas back to a crude signal).
            setAuto(false);
            onInstrument(e.target.value);
          }}
          className="bg-white/5 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white outline-none"
        >
          {view.instruments.map((i) => (
            <option key={i.symbol} value={i.symbol} className="bg-[#0c1016]">
              {i.display}
            </option>
          ))}
        </select>
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-white tabular-nums">{fmt(view.spot, 2)}</span>
          <span className="text-sm font-semibold tabular-nums" style={{ color: up ? COLORS.green : COLORS.red }}>
            {up ? "▲" : "▼"} {fmt(Math.abs(view.change_pct), 2)}%
          </span>
        </div>
        <button
          onClick={() => setAuto((a) => !a)}
          className="ml-auto flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-bold border"
          style={{
            borderColor: auto ? "#19e57f66" : "rgba(255,255,255,0.15)",
            color: auto ? "#19e57f" : "#8b93a7",
            background: auto ? "rgba(25,229,127,0.10)" : "transparent",
          }}
          title="Auto: the dashboard scans every instrument and jumps to the best live trade"
        >
          <span className={`w-2 h-2 rounded-full ${auto ? "bg-emerald-400 animate-pulse" : "bg-white/30"}`} />
          AUTO {auto ? "ON" : "OFF"}
        </button>
        <div className="flex items-center gap-2 text-xs text-white/40">
          <span className={`w-2 h-2 rounded-full ${stale ? "bg-amber-400" : "bg-emerald-400"}`} />
          {view.market_open ? "Live" : "Market closed"}
        </div>
      </div>

      {/* auto-scan strip — every watched instrument, best actionable first */}
      {scan && scan.rows.length > 0 && (
        <div className="mb-4">
          <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">
            {auto ? "Live scan — auto-showing the best trade" : "Live scan — click to open"}
            {scan.best_actionable ? "" : " · nothing clean right now"}
          </div>
          <div className="flex gap-2 overflow-x-auto pb-1">
            {scan.rows.map((r) => {
              const active = r.instrument === view.instrument;
              const buy = r.actionable && r.action.startsWith("BUY");
              const col = buy ? "#19e57f" : "#8b93a7";
              return (
                <button
                  key={r.instrument}
                  onClick={() => {
                    setAuto(false);
                    onInstrument(r.instrument);
                  }}
                  className="shrink-0 rounded-lg border px-3 py-1.5 text-left"
                  style={{
                    borderColor: active ? `${col}` : "rgba(255,255,255,0.10)",
                    background: buy ? "rgba(25,229,127,0.08)" : "rgba(255,255,255,0.02)",
                  }}
                >
                  <div className="text-xs font-semibold text-white flex items-center gap-1.5">
                    <span className="w-1.5 h-1.5 rounded-full" style={{ background: col }} />
                    {r.instrument_name}
                  </div>
                  <div className="text-[11px] font-bold" style={{ color: col }}>
                    {r.action}
                  </div>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* THE signal card */}
      <div
        className="rounded-2xl border p-6 mb-4"
        style={{ borderColor: `${th.color}55`, background: `radial-gradient(120% 140% at 0% 0%, ${th.glow}, transparent 60%)` }}
      >
        <div className="flex flex-wrap items-center gap-4">
          <div className="text-5xl font-black tracking-tight" style={{ color: th.color }}>
            {view.action}
          </div>
          {view.actionable && view.side && (
            <div className="px-3 py-1 rounded-full text-sm font-bold" style={{ background: `${th.color}22`, color: th.color }}>
              {view.side === "CE" ? "CALL" : "PUT"}
            </div>
          )}
          <div className="ml-auto text-right">
            <div className="text-xs text-white/40 uppercase tracking-wide">Confidence</div>
            <div className="text-2xl font-bold text-white tabular-nums">{fmt(view.confidence, 0)}%</div>
          </div>
        </div>

        {/* reasons */}
        <ul className="mt-4 space-y-1.5">
          {view.reasons.map((r, i) => (
            <li key={i} className="text-sm text-white/70 flex gap-2">
              <span style={{ color: th.color }}>•</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>

        {/* the plan — only when actionable */}
        {view.actionable && p.option_symbol && (
          <div className="mt-5 grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat label="Option" value={p.option_symbol} mono />
            <Stat
              label="Entry zone"
              value={p.entry_range ? `${fmt(p.entry_range[0])}–${fmt(p.entry_range[1])}` : fmt(p.current_premium)}
            />
            <Stat label="Stop loss" value={fmt(p.stop_loss)} color={COLORS.red} />
            <Stat label="Target 1 / 2 / 3" value={`${fmt(p.target1)} / ${fmt(p.target2)} / ${fmt(p.target3)}`} color={COLORS.green} />
            <Stat label="Strike" value={`${fmt(p.strike, 0)} ${p.moneyness || ""}`} />
            <Stat label="Hold (est.)" value={p.expected_holding_minutes ? `${p.expected_holding_minutes} min` : "—"} />
            <Stat label="Trade quality" value={view.trade_quality || "—"} />
            <Stat label="Risk" value={view.risk_level} />
          </div>
        )}

        {/* honest win-probability note */}
        <div className="mt-4 text-[11px] text-white/35">
          Win probability: <span className="text-amber-300/80">calibrating</span> — {view.win_probability_note}
        </div>
      </div>

      {/* tipster-style CALL CARD — same format as a broadcast call, but backed
          by the measured backtest instead of an unaccountable tip */}
      {view.actionable && p.option_symbol && <CallCard view={view} />}

      {/* hybrid pipeline gate strip: 15m bias → 5m trend → regime → volatility */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
        <GateCard
          title="15-min bias"
          color={view.bias15.trend === "UP" ? "green" : view.bias15.trend === "DOWN" ? "red" : "grey"}
          big={view.bias15.trend.toLowerCase()}
          sub="market direction (top of pipeline)"
        />
        <GateCard
          title="5-min trend"
          color={view.htf.trend === "UP" ? "green" : view.htf.trend === "DOWN" ? "red" : "grey"}
          big={view.htf.trend || "flat"}
          sub={`expected move ${fmt(view.volatility.expected_move_points)} pts`}
        />
        <GateCard title="Regime" color={view.regime.color} big={view.regime.label} sub={`market: ${view.regime.raw.toLowerCase()}`} />
        <GateCard
          title="Move likely?"
          color={view.volatility.color}
          big={view.volatility.state}
          sub={`ADX ${fmt(view.volatility.adx, 0)} (need ${fmt(view.volatility.adx_min, 0)}) · ATR ${fmt(view.volatility.atr_points)}`}
        />
      </div>

      {/* chart */}
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-3 mb-4">
        <div className="text-xs text-white/40 mb-1">{view.instrument_name} · recent candles</div>
        <div className="h-52">
          <FuturesChart candles={view.candles} vwap={null} />
        </div>
      </div>

      {/* zero-to-hero expiry punt (speculative) */}
      {view.zero_to_hero?.active ? (
        <div className="rounded-2xl border border-fuchsia-500/30 bg-fuchsia-500/[0.05] p-4">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs uppercase tracking-wide text-fuchsia-300 font-bold">Zero-to-Hero call</span>
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-fuchsia-500/20 text-fuchsia-200 font-bold">EXPIRY · HIGH RISK</span>
          </div>
          <div className="font-mono text-sm text-white/90 leading-6">
            <div>
              BUY <span className="font-bold text-fuchsia-200">{view.zero_to_hero.side}</span> {view.zero_to_hero.option_symbol}
            </div>
            <div className="text-white/70">
              Premium ₹{fmt(view.zero_to_hero.premium ?? 0, 1)} · Lots {view.zero_to_hero.lots} · Cost ₹{fmt(view.zero_to_hero.cost ?? 0, 0)} (max loss = cost)
            </div>
            <div className="text-white/40 text-[11px]">Budget ₹{fmt(view.zero_to_hero.budget, 0)}/day · walled off from core capital</div>
          </div>
          <div className="mt-2 text-[10px] text-fuchsia-200/70 leading-4">{view.zero_to_hero.note}</div>
        </div>
      ) : null}

      {/* auto-buy bot controls (paper) */}
      <AutoBotPanel instruments={view.instruments} />

      {/* open position tracker */}
      <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
        <div className="flex items-center gap-2 mb-2">
          <span className="text-xs text-white/40 uppercase tracking-wide">Open position</span>
          {at?.mode === "live" ? (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-red-500/20 text-red-300 font-bold">LIVE · REAL ORDERS</span>
          ) : at?.enabled ? (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-amber-400/15 text-amber-300 font-bold">PAPER · auto-buy on</span>
          ) : (
            <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/10 text-white/50 font-bold">PAPER · bot off</span>
          )}
        </div>
        {inPos ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
              <Stat label="Leg" value={pos.option_symbol || "—"} mono />
              <Stat label="Entry" value={fmt(pos.entry_premium)} />
              <Stat label="Now" value={fmt(pos.current_premium)} />
              <Stat
                label="P&L"
                value={`${pos.pnl >= 0 ? "+" : ""}${fmt(pos.pnl, 0)} (${fmt(pos.pnl_pct, 1)}%)`}
                color={pos.pnl >= 0 ? COLORS.green : COLORS.red}
              />
            </div>
            {ent ? (
              <div className="mt-3 rounded-xl border border-white/10 bg-white/[0.02] p-3">
                <div className="text-[10px] uppercase tracking-wide text-white/35 mb-2">
                  Bot entry — kept visible after the BUY card clears
                </div>
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                  <Stat label="Side / Strike" value={`${ent.side || "—"} ${ent.strike ?? ""}`} />
                  <Stat label="Entry / Lots" value={`${fmt(ent.entry_premium)} × ${ent.lots ?? "—"}`} />
                  <Stat label="Stop" value={fmt(ent.stop_loss)} color={COLORS.red} />
                  <Stat
                    label="Locked floor"
                    value={ent.locked_floor != null ? fmt(ent.locked_floor) : "— (pre-T1)"}
                    color={ent.locked_floor != null ? COLORS.green : COLORS.grey}
                  />
                  <Stat label="Target 1" value={fmt(ent.target1)} color={COLORS.green} />
                  <Stat label="Target 2" value={fmt(ent.target2)} color={COLORS.green} />
                  <Stat label="Target 3" value={fmt(ent.target3)} color={COLORS.green} />
                  <Stat label="Entered" value={ent.entry_time ? new Date(ent.entry_time * 1000).toLocaleTimeString("en-IN") : "—"} />
                </div>
                <div className="mt-2 text-[10px] text-white/35 leading-4">
                  Ratchet exit: hard stop until T1, then the floor steps up to each target reached. Above T3 it keeps
                  riding for higher highs and exits only if it falls back to T3 — a hit target is never given back.
                </div>
              </div>
            ) : null}
          </>
        ) : (
          <div className="text-sm text-white/40">
            {at?.mode === "live"
              ? "Flat — no open position. Bot is LIVE-armed and will place a REAL order on the next qualified signal."
              : at?.enabled
                ? "Flat — no open position. Bot is ON (paper); it will simulate a buy on the next qualified signal."
                : "Flat — no open position. Auto-buy bot is off."}
          </div>
        )}
        {at?.last_action ? (
          <div className="mt-2 text-[11px] text-white/45">Last bot action: {at.last_action}</div>
        ) : null}
      </div>

      {/* bot trade history */}
      {history.length > 0 ? (
        <div className="rounded-2xl border border-white/10 bg-white/[0.02] p-4">
          <div className="text-xs text-white/40 uppercase tracking-wide mb-2">Bot trade history</div>
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-white/35 text-left">
                  <th className="py-1 pr-3 font-medium">Time</th>
                  <th className="py-1 pr-3 font-medium">Instrument</th>
                  <th className="py-1 pr-3 font-medium">Leg</th>
                  <th className="py-1 pr-3 font-medium text-right">Entry</th>
                  <th className="py-1 pr-3 font-medium text-right">Exit</th>
                  <th className="py-1 pr-3 font-medium text-right">Lots</th>
                  <th className="py-1 pr-3 font-medium text-right">P&L</th>
                  <th className="py-1 pr-3 font-medium">Hold</th>
                  <th className="py-1 pr-3 font-medium">Reason</th>
                  <th className="py-1 pr-3 font-medium">Mode</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h, i) => (
                  <tr key={i} className="border-t border-white/5 text-white/70">
                    <td className="py-1 pr-3">{h.time ? new Date(h.time * 1000).toLocaleTimeString("en-IN") : "—"}</td>
                    <td className="py-1 pr-3">{h.instrument || "—"}</td>
                    <td className="py-1 pr-3 font-mono">{h.option || "—"}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{fmt(h.entry)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{fmt(h.exit)}</td>
                    <td className="py-1 pr-3 text-right tabular-nums">{h.lots ?? "—"}</td>
                    <td
                      className="py-1 pr-3 text-right tabular-nums font-semibold"
                      style={{ color: (h.net_pnl ?? 0) >= 0 ? COLORS.green : COLORS.red }}
                    >
                      {h.net_pnl != null ? `${h.net_pnl >= 0 ? "+" : ""}${fmt(h.net_pnl, 0)}` : "—"}
                    </td>
                    <td className="py-1 pr-3">{h.holding_minutes != null ? `${h.holding_minutes}m` : "—"}</td>
                    <td className="py-1 pr-3">{h.exit_reason || "—"}</td>
                    <td className="py-1 pr-3">{h.mode || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function Stat({ label, value, color, mono }: { label: string; value: string; color?: string; mono?: boolean }) {
  return (
    <div className="rounded-lg bg-white/[0.03] border border-white/5 px-3 py-2">
      <div className="text-[10px] uppercase tracking-wide text-white/35">{label}</div>
      <div className={`text-sm font-semibold ${mono ? "font-mono text-xs" : ""}`} style={{ color: color || "#fff" }}>
        {value}
      </div>
    </div>
  );
}

function buildCallText(view: QuantumSignalView): string {
  const p = view.plan;
  const side = view.side === "CE" ? "CALL (CE)" : "PUT (PE)";
  const entry = p.entry_range ? `${fmt(p.entry_range[0])}–${fmt(p.entry_range[1])}` : fmt(p.current_premium);
  const lines = [
    `${view.instrument_name} — BUY ${side}`,
    `Option : ${p.option_symbol}`,
    `Entry  : ₹${entry}`,
    `SL     : ₹${fmt(p.stop_loss)}`,
    `T1/T2/T3: ₹${fmt(p.target1)} / ₹${fmt(p.target2)} / ₹${fmt(p.target3)}`,
    p.expected_holding_minutes ? `Hold   : ~${p.expected_holding_minutes} min` : "",
    view.sizing.suggested_lots
      ? `Lots   : ${view.sizing.suggested_lots} (safest for ₹${fmt(view.sizing.capital, 0)} @ ${view.sizing.risk_per_trade_pct}% risk)`
      : "",
    `Spot   : ${fmt(view.spot, 2)}`,
    view.measured
      ? `Edge   : measured ${view.measured.win_rate_pct}% win, PF ${view.measured.profit_factor} (${view.measured.window})`
      : `Edge   : not yet backtested for this instrument`,
    `Paper/advisory only — not financial advice. Manage your own risk.`,
  ];
  return lines.filter(Boolean).join("\n");
}

function CallCard({ view }: { view: QuantumSignalView }) {
  const [copied, setCopied] = useState(false);
  const p = view.plan;
  const side = view.side === "CE" ? "CALL (CE)" : "PUT (PE)";
  const entry = p.entry_range ? `${fmt(p.entry_range[0])}–${fmt(p.entry_range[1])}` : fmt(p.current_premium);
  const m = view.measured;

  const copy = () => {
    navigator.clipboard.writeText(buildCallText(view)).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1600);
      },
      () => {},
    );
  };

  return (
    <div className="rounded-2xl border border-emerald-400/30 bg-emerald-400/[0.04] p-5 mb-4">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-[10px] uppercase tracking-wide text-emerald-300/80 font-bold">The Call</span>
        <span className="text-[10px] text-white/35">tipster-style, backed by the measured backtest</span>
        <button
          onClick={copy}
          className="ml-auto px-3 py-1 rounded-lg text-xs font-bold border border-emerald-400/40 text-emerald-300 hover:bg-emerald-400/10"
        >
          {copied ? "Copied ✓" : "Copy call"}
        </button>
      </div>
      <div className="font-mono text-sm leading-6 text-white/85 whitespace-pre-wrap">
        <div>
          <span className="text-emerald-300 font-bold">BUY {side}</span> · {view.instrument_name}
        </div>
        <div>Option : {p.option_symbol}</div>
        <div>Entry  : ₹{entry}</div>
        <div>
          SL     : <span style={{ color: COLORS.red }}>₹{fmt(p.stop_loss)}</span>
        </div>
        <div>
          T1/T2/T3: <span style={{ color: COLORS.green }}>₹{fmt(p.target1)} / ₹{fmt(p.target2)} / ₹{fmt(p.target3)}</span>
        </div>
        {p.expected_holding_minutes ? <div>Hold   : ~{p.expected_holding_minutes} min</div> : null}
        {view.sizing.suggested_lots ? (
          <div>
            Lots   : <span className="text-emerald-300 font-bold">{view.sizing.suggested_lots}</span>{" "}
            <span className="text-white/40">
              (safest for ₹{fmt(view.sizing.capital, 0)} @ {view.sizing.risk_per_trade_pct}% risk
              {view.sizing.est_risk ? ` · ~₹${fmt(view.sizing.est_risk, 0)} at stop` : ""})
            </span>
          </div>
        ) : null}
      </div>
      <div className="mt-3 text-[11px]">
        {m ? (
          <span className="text-white/50">
            Historical edge (measured):{" "}
            <span className="text-emerald-300/90 font-semibold">
              {m.win_rate_pct}% win · PF {m.profit_factor} · avg +{m.avg_points} pts
            </span>{" "}
            over {m.trades.toLocaleString("en-IN")} trades — {m.window}.
          </span>
        ) : (
          <span className="text-amber-300/70">No measured backtest for this instrument yet — treat the call as unvalidated.</span>
        )}
      </div>
      <div className="mt-1 text-[10px] text-white/30">
        Paper/advisory only. Not financial advice. Underlying-edge figure excludes option theta &amp; spread.
      </div>
    </div>
  );
}

function GateCard({ title, color, big, sub }: { title: string; color: string; big: string; sub: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.02] px-4 py-3">
      <div className="text-[10px] uppercase tracking-wide text-white/35 mb-1">{title}</div>
      <div className="flex items-center gap-2">
        <Dot color={color} />
        <span className="text-base font-bold text-white capitalize">{big}</span>
      </div>
      <div className="text-[11px] text-white/40 mt-1">{sub}</div>
    </div>
  );
}
