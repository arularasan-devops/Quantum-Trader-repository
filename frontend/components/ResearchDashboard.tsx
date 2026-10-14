"use client";
import { useCallback, useEffect, useState } from "react";
import AiPaperTrading from "@/components/AiPaperTrading";
import HurdleShadowPanel from "@/components/HurdleShadowPanel";
import CapturedOptionStudyPanel from "@/components/CapturedOptionStudyPanel";
import ExitStudyPanel from "@/components/ExitStudyPanel";
import FiveYearDiscoveryPanel from "@/components/FiveYearDiscoveryPanel";
import HigherTimeframePanel from "@/components/HigherTimeframePanel";
import MultiDayPanel from "@/components/MultiDayPanel";
import MarketOpportunities from "@/components/MarketOpportunities";
import SignalDiagnostics from "@/components/SignalDiagnostics";
import {
  DataQuality,
  GroupStat,
  ResearchReport,
  ResearchStatus,
  SetupLibrary,
  ShadowSummary,
  getDataQuality,
  getResearchReport,
  getResearchStatus,
  getSetupLibrary,
  getShadowSummary,
  runCapture,
  runReplay,
  runShadow,
} from "@/lib/api";

const PERF_METRICS: [string, string, boolean][] = [
  ["win_rate", "Win Rate", true],
  ["loss_rate", "Loss Rate", true],
  ["profit_factor", "Profit Factor", false],
  ["expectancy", "Expectancy", false],
  ["total_net_pnl", "Net P&L", false],
  ["avg_win", "Avg Win", false],
  ["avg_loss", "Avg Loss", false],
  ["avg_risk_reward", "Avg R:R", false],
  ["sharpe", "Sharpe", false],
  ["max_drawdown", "Max Drawdown", false],
  ["avg_hold_minutes", "Avg Hold (min)", false],
  ["longest_win_streak", "Win Streak", false],
  ["longest_loss_streak", "Loss Streak", false],
  ["trades", "Trades", false],
];

function num(v: number | number[] | null | undefined): number | null {
  return typeof v === "number" ? v : null;
}

function EquityCurve({ curve }: { curve: number[] }) {
  if (!curve || curve.length < 2) {
    return <div className="text-white/40 text-xs">No equity curve yet.</div>;
  }
  const w = 560;
  const h = 120;
  const min = Math.min(...curve);
  const max = Math.max(...curve);
  const span = max - min || 1;
  const pts = curve
    .map((v, i) => {
      const x = (i / (curve.length - 1)) * w;
      const y = h - ((v - min) / span) * h;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  const last = curve[curve.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-28">
      <polyline
        fill="none"
        stroke={last >= 0 ? "#19e57f" : "#ff6b6b"}
        strokeWidth="1.5"
        points={pts}
      />
      <line x1="0" y1={h - ((0 - min) / span) * h} x2={w}
        y2={h - ((0 - min) / span) * h} stroke="#ffffff22" strokeDasharray="3 3" />
    </svg>
  );
}

function GroupBars({ title, data }: { title: string; data: Record<string, GroupStat> }) {
  const entries = Object.entries(data).sort((a, b) => a[0].localeCompare(b[0]));
  const maxN = Math.max(1, ...entries.map(([, s]) => s.trades));
  return (
    <div className="card p-3">
      <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">{title}</div>
      {entries.length === 0 ? (
        <div className="text-white/40 text-xs">No trades.</div>
      ) : (
        <div className="space-y-1">
          {entries.map(([k, s]) => (
            <div key={k} className="flex items-center gap-2 text-[11px]">
              <span className="w-24 truncate text-white/70 capitalize">
                {k.replace(/_/g, " ").toLowerCase()}
              </span>
              <div className="flex-1 h-3 bg-white/5 rounded overflow-hidden">
                <div
                  className="h-full bg-[var(--accent)]/60"
                  style={{ width: `${(s.trades / maxN) * 100}%` }}
                />
              </div>
              <span className="w-10 text-right text-white/60">{s.win_rate}%</span>
              <span
                className={
                  "w-16 text-right tabular-nums " +
                  (s.net_pnl >= 0 ? "text-[#19e57f]" : "text-[#ff6b6b]")
                }
              >
                ₹{s.net_pnl}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Buckets({ title, data }: { title: string; data: Record<string, number> }) {
  const entries = Object.entries(data);
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div>
      <div className="text-[10px] text-white/45 mb-1">{title}</div>
      <div className="flex items-end gap-1 h-16">
        {entries.map(([k, v]) => (
          <div key={k} className="flex-1 flex flex-col items-center justify-end gap-1">
            <div className="w-full bg-[var(--accent)]/50 rounded-t"
              style={{ height: `${(v / max) * 100}%` }} />
            <span className="text-[8px] text-white/40">{k}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function fmtTs(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString([], {
    month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

function SetupLibraryPanel({ instrument }: { instrument: string }) {
  const [lib, setLib] = useState<SetupLibrary | null>(null);
  const [outcome, setOutcome] = useState("all");

  useEffect(() => {
    getSetupLibrary(instrument, { outcome }).then(setLib).catch(() => setLib(null));
  }, [instrument, outcome]);

  const rows = lib?.setups ?? [];
  return (
    <div className="card p-3 shrink-0">
      <div className="flex items-center justify-between mb-2">
        <div className="text-[11px] uppercase tracking-widest text-white/45">
          Setup Library — completed paper trades
          {lib ? ` (${lib.matched}/${lib.total})` : ""}
        </div>
        <div className="flex rounded-lg border border-white/15 overflow-hidden text-[10px] font-semibold">
          {["all", "win", "loss"].map((o) => (
            <button
              key={o}
              onClick={() => setOutcome(o)}
              className={
                (outcome === o ? "bg-white/10 text-white " : "text-white/50 hover:bg-white/5 ") +
                "px-2.5 py-1 capitalize"
              }
            >
              {o}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="text-white/40 text-xs">
          {lib?.note ??
            "No completed paper trades recorded yet. Close a paper trade and it is captured here with full context, MFE and MAE."}
        </div>
      ) : (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-[11px]">
            <thead className="text-white/40 text-[10px] uppercase">
              <tr className="text-left">
                <th className="py-1 pr-2">Time</th>
                <th className="pr-2">Option</th>
                <th className="pr-2 text-right">Entry</th>
                <th className="pr-2 text-right">Exit</th>
                <th className="pr-2 text-right">P&amp;L</th>
                <th className="pr-2 text-right">MFE</th>
                <th className="pr-2 text-right">MAE</th>
                <th className="pr-2 text-right">Hold</th>
                <th className="pr-2">Regime</th>
                <th className="pr-2 text-right">Conf</th>
                <th className="pr-2 text-right">Score</th>
                <th className="pr-2">Exit</th>
              </tr>
            </thead>
            <tbody className="text-white/70">
              {rows.map((r, i) => (
                <tr key={r.id ?? i} className="border-t border-white/5">
                  <td className="py-1 pr-2 whitespace-nowrap text-white/50">{fmtTs(r.exit_ts ?? r.entry_ts)}</td>
                  <td className="pr-2 whitespace-nowrap">{r.option_symbol ?? "—"}</td>
                  <td className="pr-2 text-right tabular-nums">{r.entry ?? "—"}</td>
                  <td className="pr-2 text-right tabular-nums">{r.exit ?? "—"}</td>
                  <td className={"pr-2 text-right tabular-nums " + ((r.pnl ?? 0) >= 0 ? "text-[#19e57f]" : "text-[#ff6b6b]")}>
                    {r.pnl == null ? "—" : `₹${r.pnl}`}
                  </td>
                  <td className="pr-2 text-right tabular-nums text-[#19e57f]/80">{r.mfe == null ? "—" : r.mfe}</td>
                  <td className="pr-2 text-right tabular-nums text-[#ff6b6b]/80">{r.mae == null ? "—" : r.mae}</td>
                  <td className="pr-2 text-right tabular-nums">{r.duration_min == null ? "—" : `${Math.round(r.duration_min)}m`}</td>
                  <td className="pr-2 whitespace-nowrap text-white/50">{r.market_regime ?? "—"}</td>
                  <td className="pr-2 text-right tabular-nums">{r.confidence == null ? "—" : r.confidence}</td>
                  <td className="pr-2 text-right tabular-nums">{r.trade_score == null ? "—" : r.trade_score}</td>
                  <td className="pr-2 whitespace-nowrap text-white/50">{r.exit_reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function ResearchDashboard({ instrument }: { instrument: string }) {
  const [status, setStatus] = useState<ResearchStatus | null>(null);
  const [report, setReport] = useState<ResearchReport | null>(null);
  const [quality, setQuality] = useState<DataQuality | null>(null);
  const [shadow, setShadow] = useState<ShadowSummary | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [s, r, q, sh] = await Promise.all([
      getResearchStatus(instrument),
      getResearchReport(instrument),
      getDataQuality(instrument),
      getShadowSummary(instrument),
    ]);
    setStatus(s);
    setReport(r);
    setQuality(q);
    setShadow(sh);
  }, [instrument]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const doAction = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name);
    try {
      await fn();
      await refresh();
    } finally {
      setBusy(null);
    }
  };

  const perf = report?.performance ?? {};
  const curve = (perf["equity_curve"] as number[] | undefined) ?? [];

  return (
    // No scroll container of its own: the page owns the single research
    // scrollbar. Nesting one here clipped every panel below the first viewport
    // and swallowed the wheel over inner tables.
    <div className="flex flex-col gap-3 pb-6">
      {/* honesty banner */}
      {report && (
        <div
          className={
            "rounded-lg px-4 py-2 border " +
            (report.is_real_data
              ? "border-[#19e57f]/40 bg-[#19e57f]/10"
              : "border-[#f4c025]/40 bg-[#f4c025]/10")
          }
        >
          <div
            className={
              "text-xs font-bold " +
              (report.is_real_data ? "text-[#19e57f]" : "text-[#f4c025]")
            }
          >
            {report.is_real_data
              ? "REAL DATA — metrics computed from real recorded trades"
              : "SIMULATED DATA — logic-validation only, NOT real performance"}
          </div>
          <div className="text-[11px] text-white/60 mt-0.5">{report.note}</div>
        </div>
      )}

      {/* AI engine, live paper book and baseline-vs-AI. Paper only, by design. */}
      <AiPaperTrading instrument={instrument} />

      {/* Where the market is moving right now — scanned before the engine runs.
          Research only: it ranks opportunity, it cannot emit a signal. */}
      <MarketOpportunities />

      {/* Which gate refused each setup, and what the refused setup then did. */}
      <SignalDiagnostics />

      {/* Setup Library — every completed paper trade captured with MFE/MAE */}
      <SetupLibraryPanel instrument={instrument} />

      {/* Phase 23 — break-even hurdle PAPER SHADOW. Record-only: it observes
          auto BUY opportunities with the measured book and scores three arms on
          the engine's own trades. No threshold reaches the live order path. */}
      <HurdleShadowPanel />

      {/* Phase 24 — the five-year discovery study, read-only. Separate from the
          Phase 23 shadow: it reads artefacts a CLI run wrote and can neither
          start a study nor promote anything. */}
      <FiveYearDiscoveryPanel />

      {/* Phase 25 — the captured-window CE/PE study, read-only. Its own
          artefacts: entry at the stored ask, exit at the stored bid, and a
          verdict that can never exceed RESEARCH_LEAD. */}
      <CapturedOptionStudyPanel />

      {/* Phase 26 — exit variants on the same captured books, plus the measured
          tradability advisory. Advisory only: no row here is wired to a live
          refusal, and the ceiling is still RESEARCH_LEAD. */}
      <ExitStudyPanel />

      {/* Phase 27 — the final futures branch on 5-minute and 15-minute bars,
          read-only. Option strategy logic and the option-book capture are
          untouched by it, and the ceiling is still RESEARCH_LEAD. */}
      <HigherTimeframePanel />

      {/* Phase 28 — the first study that holds overnight: multi-day futures and
          cash-equity delivery on daily bars, benchmarked against buy-and-hold.
          A stock with no five-year history reads INSUFFICIENT_HISTORY, which is
          unanswered rather than failed. Read-only, ceiling RESEARCH_LEAD. */}
      <MultiDayPanel />

      {/* controls */}
      <div className="card p-3 flex flex-wrap items-center gap-2">
        <div className="text-[11px] text-white/50 mr-2">
          Store: <span className="text-white/80">{status?.backend ?? "…"}</span> ·
          futures bars <span className="text-white/80">{status?.futures_rows ?? 0}</span> ·
          signals <span className="text-white/80">{status?.signals_recorded ?? 0}</span> ·
          replay trades <span className="text-white/80">{status?.replay_trades ?? 0}</span>
        </div>
        {status?.auto_capture_active ? (
          <span className="text-[10px] font-semibold px-2 py-1 rounded-md border border-[#19e57f]/40 bg-[#19e57f]/10 text-[#19e57f]">
            ● AUTO-CAPTURE ON — recording real feed
          </span>
        ) : (
          <span className="text-[10px] font-semibold px-2 py-1 rounded-md border border-white/15 text-white/50">
            AUTO-CAPTURE OFF (needs real Angel feed)
          </span>
        )}
        <div className="flex-1" />
        <button
          disabled={!!busy}
          onClick={() => doAction("capture", () => runCapture(instrument, 400))}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white/80 border border-white/15 hover:bg-white/5 disabled:opacity-40"
        >
          {busy === "capture" ? "Capturing…" : "Capture (sim)"}
        </button>
        <button
          disabled={!!busy}
          onClick={() => doAction("replay", () => runReplay(instrument, 100))}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white/80 border border-white/15 hover:bg-white/5 disabled:opacity-40"
        >
          {busy === "replay" ? "Replaying…" : "Run Replay"}
        </button>
        <button
          disabled={!!busy}
          onClick={() => doAction("shadow", () => runShadow(instrument))}
          className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white/80 border border-white/15 hover:bg-white/5 disabled:opacity-40"
        >
          {busy === "shadow" ? "Running…" : "Run Shadow"}
        </button>
      </div>

      {/* performance metrics */}
      <div className="card p-3">
        <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
          Performance ({report?.trades_recorded ?? 0} trades)
        </div>
        <div className="grid grid-cols-7 gap-2">
          {PERF_METRICS.map(([key, label, pct]) => {
            const v = num(perf[key]);
            return (
              <div key={key} className="rounded-lg border border-white/10 px-2 py-1.5">
                <div className="text-[9px] text-white/45">{label}</div>
                <div className="text-sm font-bold text-white">
                  {v == null ? "—" : pct ? `${v}%` : v}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* equity + distributions */}
      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-7 card p-3">
          <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
            Equity Curve
          </div>
          <EquityCurve curve={curve} />
        </div>
        <div className="col-span-5 card p-3 space-y-2">
          <div className="text-[11px] uppercase tracking-widest text-white/45">
            Distributions
          </div>
          {report && (
            <>
              <Buckets title="Signal" data={
                Object.fromEntries(Object.entries(report.distributions.signal)) as Record<string, number>
              } />
              <Buckets title="Confidence" data={report.distributions.confidence} />
              <Buckets title="Trade Score" data={report.distributions.trade_score} />
            </>
          )}
        </div>
      </div>

      {/* regime + hour */}
      <div className="grid grid-cols-2 gap-3">
        {report && <GroupBars title="Performance by Regime" data={report.by_regime} />}
        {report && <GroupBars title="Performance by Hour" data={report.by_hour} />}
      </div>

      {/* rule impact */}
      {report && (
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
            Rule Impact (observational — frozen engine, conditional performance)
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-1">
            {Object.entries(report.rule_impact).map(([rule, split]) => (
              <div key={rule} className="text-[11px]">
                <div className="text-white/70 capitalize mb-0.5">
                  {rule.replace(/_/g, " ")}
                </div>
                {Object.entries(split).map(([label, s]) => (
                  <div key={label} className="flex justify-between text-white/50 pl-2">
                    <span className="capitalize">{label.replace(/_/g, " ")}</span>
                    <span>
                      {s.win_rate == null ? "—" : `${s.win_rate}%`} · {s.trades}t ·{" "}
                      {s.net_pnl == null ? "—" : `₹${s.net_pnl}`}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* shadow + data quality */}
      <div className="grid grid-cols-2 gap-3">
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
            Shadow Mode (recommendation vs. actual)
          </div>
          {shadow && shadow.resolved > 0 ? (
            <>
              <div className="text-2xl font-bold text-white">
                {shadow.accuracy}%<span className="text-xs text-white/40 ml-2">
                  overall accuracy · {shadow.resolved} resolved
                </span>
              </div>
              <div className="mt-2 space-y-1">
                {Object.entries(shadow.by_signal).map(([sig, d]) => (
                  <div key={sig} className="flex justify-between text-[11px] text-white/60">
                    <span>{sig}</span>
                    <span>{d.accuracy}% ({d.n})</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="text-white/40 text-xs">
              No resolved shadow recommendations yet. Run Shadow to log
              recommendations and score them against the actual market move.
            </div>
          )}
        </div>

        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
            Data Quality
          </div>
          {quality ? (
            <div className="text-[11px] text-white/60 space-y-1">
              <div>
                Rows checked: <span className="text-white/80">{quality.rows_checked}</span>
              </div>
              <div>
                Issues:{" "}
                <span className={quality.clean ? "text-[#19e57f]" : "text-[#f4c025]"}>
                  {quality.issues} {quality.clean ? "(clean)" : ""}
                </span>
              </div>
              {Object.entries(quality.by_kind).map(([k, n]) => (
                <div key={k} className="flex justify-between pl-2">
                  <span className="capitalize">{k.replace(/_/g, " ")}</span>
                  <span>{n}</span>
                </div>
              ))}
              {quality.span && (
                <div className="text-white/40 pt-1">
                  {quality.span.from.slice(0, 16).replace("T", " ")} →{" "}
                  {quality.span.to.slice(0, 16).replace("T", " ")}
                </div>
              )}
            </div>
          ) : (
            <div className="text-white/40 text-xs">…</div>
          )}
        </div>
      </div>

      {/* trade explanations */}
      {report && report.trade_explanations.length > 0 && (
        <div className="card p-3">
          <div className="text-[11px] uppercase tracking-widest text-white/45 mb-2">
            Trade Explanations (most recent)
          </div>
          <div className="space-y-1 max-h-52 overflow-auto">
            {report.trade_explanations
              .slice()
              .reverse()
              .map((t, i) => (
                <div key={i} className="text-[11px] border-b border-white/5 pb-1">
                  <div className="flex justify-between">
                    <span className={t.win ? "text-[#19e57f]" : "text-[#ff6b6b]"}>
                      {t.why}
                    </span>
                    <span className="text-white/40">{t.option}</span>
                  </div>
                  {t.factors.length > 0 && (
                    <div className="text-white/45">{t.factors.join(" · ")}</div>
                  )}
                </div>
              ))}
          </div>
        </div>
      )}

      <div className="text-[10px] text-white/40 leading-relaxed">
        The Phase 1.5 signal engine is frozen. This research platform only
        collects data, replays the engine with no lookahead, and measures it.
        Simulated-feed numbers validate the logic + pipeline and are never
        presented as real trading performance — real metrics require a real
        broker feed and collected market history. Past performance does not
        guarantee future results.
      </div>
    </div>
  );
}
