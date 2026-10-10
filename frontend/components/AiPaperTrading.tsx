"use client";

import { useCallback, useEffect, useState } from "react";
import {
  type AiComparison,
  type AiDecisionRow,
  type AiPaperTrade,
  type AiStatus,
  closeAiPaperTrade,
  getAiComparison,
  getAiDecision,
  getAiPaperPositions,
  getAiStatus,
  runAiCycle,
} from "@/lib/api";

/** AI ENGINE — LIVE PAPER TRADING (Phase 6).
 *
 * The AI engine runs BESIDE the production baseline; it does not modify it and
 * cannot place a real order — the kill switch is a source constant, not a
 * setting, and this panel shows its state rather than hiding it.
 *
 * Three things are deliberately shown even when they are unflattering: the
 * reason the AI did NOT trade, the baseline's verdict on the same bar, and how
 * far the paper book is from the sample size needed to judge either.
 */
const DECISION_CLS: Record<string, string> = {
  BUY_NOW: "bg-[#19e57f]/15 text-[#19e57f] border-[#19e57f]/40",
  WAIT_PULLBACK: "bg-[#f4c025]/15 text-[#f4c025] border-[#f4c025]/40",
  WATCH: "bg-white/10 text-white/70 border-white/20",
  NO_TRADE: "bg-[#ff5c5c]/10 text-[#ff5c5c] border-[#ff5c5c]/30",
  EXIT: "bg-[#ff5c5c]/15 text-[#ff5c5c] border-[#ff5c5c]/40",
};

const FEED_CLS: Record<string, string> = {
  FRESH: "text-[#19e57f]",
  AGING: "text-[#f4c025]",
  STALE: "text-[#ff5c5c]",
  DEAD: "text-[#ff5c5c]",
  NO_DATA: "text-white/40",
};

function pct(v: number | null | undefined, dp = 1): string {
  return v === null || v === undefined ? "—" : `${(v * 100).toFixed(dp)}%`;
}

function num(v: number | null | undefined, dp = 2): string {
  return v === null || v === undefined ? "—" : v.toFixed(dp);
}

function money(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const s = v >= 0 ? "+" : "−";
  return `${s}₹${Math.abs(v).toLocaleString("en-IN", { maximumFractionDigits: 0 })}`;
}

function pnlCls(v: number | null | undefined): string {
  if (v === null || v === undefined || v === 0) return "text-white/60";
  return v > 0 ? "text-[#19e57f]" : "text-[#ff5c5c]";
}

function ago(ts: number | null | undefined): string {
  if (!ts) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
}

function Bar({ value, label }: { value: number | null; label: string }) {
  const v = value === null ? 0 : Math.max(0, Math.min(100, value));
  return (
    <div className="flex items-center gap-2">
      <div className="text-[10px] text-white/50 w-[86px] shrink-0">{label}</div>
      <div className="h-1.5 flex-1 bg-white/10 rounded overflow-hidden">
        <div className="h-full bg-[#3aa0ff]" style={{ width: `${v}%` }} />
      </div>
      <div className="text-[10px] text-white/70 w-[38px] text-right">
        {value === null ? "—" : `${v.toFixed(0)}`}
      </div>
    </div>
  );
}

export default function AiPaperTrading({ instrument }: { instrument: string }) {
  const [status, setStatus] = useState<AiStatus | null>(null);
  const [decision, setDecision] = useState<AiDecisionRow | null>(null);
  const [open, setOpen] = useState<AiPaperTrade[]>([]);
  const [recent, setRecent] = useState<AiPaperTrade[]>([]);
  const [pnl, setPnl] = useState<Record<string, number | boolean> | null>(null);
  const [cmp, setCmp] = useState<AiComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [st, dec, pos] = await Promise.all([
        getAiStatus(),
        getAiDecision(instrument),
        getAiPaperPositions(),
      ]);
      setStatus(st);
      setDecision(dec);
      setOpen(pos.open ?? []);
      setRecent((pos.recent ?? []).filter((t) => t.status !== "OPEN").slice(0, 8));
      setPnl(pos.pnl as unknown as Record<string, number | boolean>);
      setErr(null);
    } catch {
      setErr("AI endpoints unreachable");
    }
  }, [instrument]);

  useEffect(() => {
    load();
    const id = setInterval(load, 5000);
    return () => clearInterval(id);
  }, [load]);

  const loadComparison = useCallback(async () => {
    try {
      setCmp(await getAiComparison());
    } catch {
      setErr("comparison unavailable");
    }
  }, []);

  const cycleNow = async () => {
    setBusy(true);
    try {
      await runAiCycle();
      await load();
    } catch {
      setErr("cycle failed");
    } finally {
      setBusy(false);
    }
  };

  const closeTrade = async (id: string) => {
    setBusy(true);
    try {
      await closeAiPaperTrade(id, "AI_EXIT");
      await load();
    } catch {
      setErr("close failed");
    } finally {
      setBusy(false);
    }
  };

  const regime = decision?.regime as Record<string, unknown> | undefined;
  const dirn = decision?.direction as Record<string, unknown> | undefined;
  const eq = decision?.entry_quality as Record<string, unknown> | undefined;
  const dec = decision?.decision ?? "—";

  return (
    <div className="card p-3 shrink-0">
      {/* header + the safety statement, which is never collapsed away */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <div className="text-[12px] font-semibold tracking-wide text-white/80">
            AI ENGINE — LIVE PAPER TRADING
          </div>
          <div className="text-[10px] text-white/45">
            Runs beside the production engine. Paper only — it has no real-order path.
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] px-2 py-0.5 rounded border border-[#19e57f]/40 bg-[#19e57f]/10 text-[#19e57f]">
            REAL MONEY: {status?.safety?.real_money_execution ?? "DISABLED"}
          </span>
          <span
            className={`text-[10px] px-2 py-0.5 rounded border ${
              status?.enabled
                ? "border-[#3aa0ff]/40 bg-[#3aa0ff]/10 text-[#3aa0ff]"
                : "border-white/20 text-white/50"
            }`}
          >
            LOOP {status?.enabled ? "ON" : "OFF"}
          </span>
          <span
            className={`text-[10px] px-2 py-0.5 rounded border ${
              status?.paper_enabled
                ? "border-[#f4c025]/40 bg-[#f4c025]/10 text-[#f4c025]"
                : "border-white/20 text-white/50"
            }`}
          >
            PAPER ENTRIES {status?.paper_enabled ? "ARMED" : "OFF"}
          </span>
          <button
            className="btn text-[10px] px-2 py-1"
            onClick={cycleNow}
            disabled={busy}
          >
            RUN CYCLE
          </button>
        </div>
      </div>

      {err && <div className="mt-2 text-[10px] text-[#ff5c5c]">{err}</div>}

      {/* model status — an absent or weak model is stated, not hidden */}
      <div className="mt-2 rounded border border-white/10 bg-white/[0.02] p-2">
        <div className="text-[10px] text-white/50">
          PROBABILITY MODEL:{" "}
          {status?.model?.available ? (
            <span className="text-white/80">
              {status.model.model_version ?? "loaded"} · OOS AUC{" "}
              {num(
                (
                  (status.model.metrics as
                    | { oos?: { auc?: number } }
                    | undefined)?.oos?.auc ?? null
                ),
                3,
              )}
            </span>
          ) : (
            <span className="text-[#f4c025]">
              NONE LOADED — {status?.model?.reason ?? "no artefact"}. Without a
              calibrated model the AI cannot reach BUY_NOW; it stays at WATCH.
            </span>
          )}
        </div>
      </div>

      {/* the live decision, and the baseline's verdict on the same bar */}
      <div className="mt-2 grid grid-cols-1 lg:grid-cols-2 gap-2">
        <div className="rounded border border-white/10 p-2">
          <div className="flex items-center justify-between">
            <div className="text-[10px] text-white/50">
              {decision?.instrument ?? instrument} · AI
            </div>
            <div className={`text-[10px] ${FEED_CLS[decision?.feed_state ?? ""] ?? "text-white/40"}`}>
              FEED {decision?.feed_state ?? "—"}
              {decision?.data_age_ms !== null && decision?.data_age_ms !== undefined
                ? ` · ${Math.round(decision.data_age_ms)}ms`
                : ""}
            </div>
          </div>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <span
              className={`text-[12px] font-semibold px-2 py-0.5 rounded border ${
                DECISION_CLS[dec] ?? "border-white/20 text-white/70"
              }`}
            >
              {dec}
            </span>
            {decision?.side && (
              <span className="text-[11px] text-white/70">{decision.side}</span>
            )}
            <span className="text-[11px] text-white/50">
              P(target before stop) {pct(decision?.probability ?? null)}
            </span>
            {decision?.blocked_by && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-[#ff5c5c]/10 text-[#ff5c5c]">
                BLOCKED: {decision.blocked_by}
              </span>
            )}
          </div>
          <div className="mt-2 space-y-1">
            <Bar
              label="REGIME CONF"
              value={typeof regime?.confidence === "number" ? regime.confidence : null}
            />
            <Bar
              label="ENTRY QUALITY"
              value={typeof eq?.score === "number" ? eq.score : null}
            />
          </div>
          <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
            <div className="text-white/50">
              REGIME <span className="text-white/80">{String(regime?.state ?? "—")}</span>
            </div>
            <div className="text-white/50">
              BIAS{" "}
              <span className="text-white/80">
                {String(regime?.direction_bias ?? "—")}
              </span>
            </div>
            <div className="text-white/50">
              ENTRY{" "}
              <span className="text-white/80">{String(eq?.verdict ?? "—")}</span>
            </div>
            <div className="text-white/50">
              EXTENSION{" "}
              <span className="text-white/80">
                {num(typeof eq?.extension_atr === "number" ? eq.extension_atr : null, 1)} ATR
              </span>
            </div>
            <div className="text-white/50">
              P(CE){" "}
              <span className="text-white/80">
                {pct(typeof dirn?.p_ce === "number" ? dirn.p_ce : null)}
              </span>
            </div>
            <div className="text-white/50">
              P(PE){" "}
              <span className="text-white/80">
                {pct(typeof dirn?.p_pe === "number" ? dirn.p_pe : null)}
              </span>
            </div>
          </div>
          {(decision?.reasons ?? []).length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {(decision?.reasons ?? []).slice(0, 5).map((r, i) => (
                <li key={i} className="text-[10px] text-white/55">
                  · {r}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="rounded border border-white/10 p-2">
          <div className="text-[10px] text-white/50">BASELINE (production engine)</div>
          <div className="mt-1 flex items-center gap-2 flex-wrap">
            <span className="text-[12px] font-semibold text-white/80">
              {decision?.baseline?.decision ?? "—"}
            </span>
            <span className="text-[11px] text-white/60">
              {decision?.baseline?.side ?? ""}
            </span>
            <span className="text-[11px] text-white/50">
              conf {num(decision?.baseline?.confidence ?? null, 0)}
            </span>
          </div>
          <div className="mt-1 text-[10px] text-white/40">
            Read from the cached snapshot — the AI never recomputes or alters it.
          </div>

          <div className="mt-2 text-[10px] text-white/50">PAPER BOOK</div>
          <div className="mt-1 grid grid-cols-3 gap-2">
            <div>
              <div className="text-[9px] text-white/40">REALISED</div>
              <div className={`text-[12px] ${pnlCls(Number(pnl?.realized_pnl ?? 0))}`}>
                {money(Number(pnl?.realized_pnl ?? 0))}
              </div>
            </div>
            <div>
              <div className="text-[9px] text-white/40">OPEN</div>
              <div className={`text-[12px] ${pnlCls(Number(pnl?.unrealized_pnl ?? 0))}`}>
                {money(Number(pnl?.unrealized_pnl ?? 0))}
              </div>
            </div>
            <div>
              <div className="text-[9px] text-white/40">CLOSED TRADES</div>
              <div className="text-[12px] text-white/80">
                {String(pnl?.closed_trades ?? 0)}
              </div>
            </div>
          </div>
          <div className="mt-1 text-[10px] text-white/40">
            Costs charged {money(Number(pnl?.costs_charged ?? 0))} · cycles{" "}
            {status?.cycles ?? 0} · last {ago(status?.last_cycle_ts)}
          </div>
        </div>
      </div>

      {/* open paper positions */}
      <div className="mt-3">
        <div className="text-[10px] text-white/50 mb-1">
          OPEN PAPER POSITIONS ({open.length})
        </div>
        {open.length === 0 ? (
          <div className="text-[10px] text-white/35">
            No open paper positions.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead className="text-white/40">
                <tr>
                  <th className="text-left font-normal py-1">SYMBOL</th>
                  <th className="text-right font-normal">ENTRY</th>
                  <th className="text-right font-normal">MARK</th>
                  <th className="text-right font-normal">STOP</th>
                  <th className="text-right font-normal">T1</th>
                  <th className="text-right font-normal">MFE</th>
                  <th className="text-right font-normal">MAE</th>
                  <th className="text-right font-normal">P&amp;L</th>
                  <th className="text-right font-normal">P</th>
                  <th className="text-right font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {open.map((t) => (
                  <tr key={t.id} className="border-t border-white/5">
                    <td className="py-1 text-white/80">
                      {t.symbol}
                      <span className="text-white/40"> · {t.side} × {t.lots}</span>
                    </td>
                    <td className="text-right text-white/70">{num(t.entry_premium)}</td>
                    <td className="text-right text-white/70">
                      {num(t.current_premium)}
                    </td>
                    <td className="text-right text-[#ff5c5c]/80">{num(t.stop)}</td>
                    <td className="text-right text-[#19e57f]/80">{num(t.target1)}</td>
                    <td className="text-right text-white/60">{num(t.mfe)}</td>
                    <td className="text-right text-white/60">{num(t.mae)}</td>
                    <td className={`text-right ${pnlCls(t.unrealized_pnl)}`}>
                      {money(t.unrealized_pnl)}
                    </td>
                    <td className="text-right text-white/60">{pct(t.probability, 0)}</td>
                    <td className="text-right">
                      <button
                        className="btn text-[9px] px-1.5 py-0.5"
                        onClick={() => closeTrade(t.id)}
                        disabled={busy}
                      >
                        CLOSE
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* closed paper trades — with the exit reason, which is the useful column */}
      {recent.length > 0 && (
        <div className="mt-3">
          <div className="text-[10px] text-white/50 mb-1">RECENT PAPER EXITS</div>
          <div className="overflow-x-auto">
            <table className="w-full text-[10px]">
              <thead className="text-white/40">
                <tr>
                  <th className="text-left font-normal py-1">SYMBOL</th>
                  <th className="text-left font-normal">REASON</th>
                  <th className="text-right font-normal">R</th>
                  <th className="text-right font-normal">P&amp;L</th>
                  <th className="text-right font-normal">HELD</th>
                  <th className="text-left font-normal">REGIME</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((t) => (
                  <tr key={t.id} className="border-t border-white/5">
                    <td className="py-1 text-white/75">{t.symbol}</td>
                    <td className="text-white/60">{t.exit_reason ?? "—"}</td>
                    <td className={`text-right ${pnlCls(t.r_multiple)}`}>
                      {num(t.r_multiple)}
                    </td>
                    <td className={`text-right ${pnlCls(t.realized_pnl)}`}>
                      {money(t.realized_pnl)}
                    </td>
                    <td className="text-right text-white/50">
                      {t.hold_sec === null ? "—" : `${Math.round(t.hold_sec / 60)}m`}
                    </td>
                    <td className="text-white/50">{t.regime ?? "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* baseline vs AI — with the sample-size warning attached to the numbers */}
      <div className="mt-3 rounded border border-white/10 p-2">
        <div className="flex items-center justify-between">
          <div className="text-[10px] text-white/50">BASELINE vs AI</div>
          <button
            className="btn text-[9px] px-2 py-0.5"
            onClick={loadComparison}
            disabled={busy}
          >
            COMPUTE
          </button>
        </div>
        {!cmp ? (
          <div className="mt-1 text-[10px] text-white/35">
            Not computed yet. It compares both engines on identical bars and levels.
          </div>
        ) : (
          <>
            <div className="mt-1 grid grid-cols-2 sm:grid-cols-4 gap-2 text-[10px]">
              <div>
                <div className="text-white/40">RESOLVED DECISIONS</div>
                <div className="text-white/80">{cmp.resolved_decisions}</div>
              </div>
              <div>
                <div className="text-white/40">BASELINE E[R]</div>
                <div className={pnlCls(cmp.baseline?.expectancy_r ?? null)}>
                  {num(cmp.baseline?.expectancy_r ?? null, 4)}
                </div>
              </div>
              <div>
                <div className="text-white/40">AI E[R]</div>
                <div className={pnlCls(cmp.ai?.expectancy_r ?? null)}>
                  {num(cmp.ai?.expectancy_r ?? null, 4)}
                </div>
              </div>
              <div>
                <div className="text-white/40">DIFFERENCE</div>
                <div className={pnlCls(cmp.expectancy_difference_r)}>
                  {num(cmp.expectancy_difference_r, 4)}
                </div>
              </div>
            </div>
            <div
              className={`mt-1 text-[10px] ${
                cmp.verdict_allowed ? "text-white/60" : "text-[#f4c025]"
              }`}
            >
              {cmp.verdict_allowed
                ? cmp.warning
                : `NOT A VERDICT — ${cmp.resolved_decisions} of ~${cmp.min_paired_for_verdict} paired observations needed to detect a 0.10R effect.`}
            </div>
          </>
        )}
      </div>

      <div className="mt-2 text-[9px] text-white/35 leading-relaxed">
        Outcomes are measured on the UNDERLYING; no real historical option chain
        exists in this archive, so premium behaviour, IV, OI and spread remain
        unproven. Nothing here is a profitability forecast, and real-money
        execution stays disabled in source.
      </div>
    </div>
  );
}
