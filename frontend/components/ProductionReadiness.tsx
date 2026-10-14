"use client";

import { useEffect, useState } from "react";
import {
  getFuturesPaper,
  getReadiness,
  type Calibration,
  type FuturesPaperBoard,
  type ReadinessBoard,
  type ReadinessRow,
} from "@/lib/api";

/** Production Readiness — one screen answering "is anything close?".
 *
 * Three strategies, three rows: Option A+, Futures A+ and CAS. Each states its
 * sample, its chronological holdout, net expectancy, PF, drawdown, capture rate,
 * the date it was last validated, and — the part that matters — the explicit
 * reasons it is not ready.
 *
 * Deliberate properties of this screen:
 *   - it is read-only. There is no control on it that activates, sizes or
 *     promotes anything, and no endpoint behind it that could;
 *   - PRODUCTION_CANDIDATE is the top status and still means "a human should now
 *     review this", never "this is now live";
 *   - capture is read before performance. A gate that passes on 40% capture is
 *     measuring 40% of the trades that happened, so capture is a blocker in its
 *     own right;
 *   - the T1 probability stays hidden until the calibration verdict is
 *     CALIBRATED. Until then the tool shows the T1 score and rank band, which is
 *     what it can actually defend.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const BLUE = "#60a5fa";
const MUTED = "rgba(255,255,255,0.45)";

const STATUS_COLOUR: Record<string, string> = {
  NOT_READY: RED,
  RESEARCH: MUTED,
  PAPER: AMBER,
  VALIDATED: BLUE,
  PRODUCTION_CANDIDATE: GREEN,
};

function n(v: number | null | undefined, dp = 2): string {
  return typeof v === "number" && Number.isFinite(v) ? v.toFixed(dp) : "—";
}

function signed(v: number | null | undefined, dp = 2): string {
  return typeof v === "number" && Number.isFinite(v)
    ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}`
    : "—";
}

function pct(v: number | null | undefined): string {
  return typeof v === "number" && Number.isFinite(v) ? `${v.toFixed(1)}%` : "—";
}

function txt(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

function Field({ label, value, colour, sub }: {
  label: string;
  value: string;
  colour?: string;
  sub?: string;
}) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
        {label}
      </div>
      <div className="text-sm font-semibold" style={{ color: colour || "#fff" }}>
        {value}
      </div>
      {sub ? (
        <div className="text-[10px]" style={{ color: MUTED }}>{sub}</div>
      ) : null}
    </div>
  );
}

function StrategyRow({ row }: { row: ReadinessRow }) {
  const colour = STATUS_COLOUR[row.status] || MUTED;
  const captureShort =
    typeof row.capture_pct === "number" && row.capture_pct < row.capture_target_pct;
  return (
    <div className="card p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-semibold">{txt(row.strategy)}</div>
        <span className="text-[10px]" style={{ color: MUTED }}>{txt(row.vehicle)}</span>
        <span
          className="text-[11px] px-1.5 py-0.5 rounded font-semibold"
          style={{ background: "rgba(255,255,255,0.06)", color: colour }}
        >
          {txt(row.status)}
        </span>
        <span
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
        >
          PAPER ONLY
        </span>
        {row.window ? (
          <span className="text-[10px]" style={{ color: MUTED }}>{row.window}</span>
        ) : null}
      </div>

      <div className="grid grid-cols-4 md:grid-cols-8 gap-3">
        <Field label="sample" value={String(row.sample_size)} />
        <Field
          label="holdout"
          value={String(row.holdout_size)}
          sub="chronological"
        />
        <Field
          label="net expectancy"
          value={signed(row.net_expectancy_r)}
          colour={
            typeof row.net_expectancy_r === "number"
              ? row.net_expectancy_r > 0 ? GREEN : RED
              : undefined
          }
          sub="R, holdout, after costs"
        />
        <Field
          label="profit factor"
          value={n(row.profit_factor)}
          colour={
            typeof row.profit_factor === "number"
              ? row.profit_factor > 1 ? GREEN : RED
              : undefined
          }
        />
        <Field label="max drawdown" value={signed(row.max_drawdown_r)} sub="R" />
        <Field label="T1 rate" value={pct(row.t1_pct)} />
        <Field
          label="data capture"
          value={pct(row.capture_pct)}
          colour={captureShort ? RED : typeof row.capture_pct === "number" ? GREEN : undefined}
          sub={`target ${row.capture_target_pct.toFixed(0)}%`}
        />
        <Field label="last validated" value={txt(row.last_validation)} />
      </div>

      {typeof row.valid_plans === "number" ? (
        <div className="text-[10px]" style={{ color: MUTED }}>
          valid plans {row.valid_plans} · paper entries {txt(row.paper_entries)} ·
          {" "}resolved {txt(row.resolved)}
        </div>
      ) : null}

      {/* The blocking reasons are the point of the row. An empty list here is the
          only thing that can make a strategy reviewable. */}
      <div>
        <div className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
          blocking reasons
        </div>
        {row.blockers.length === 0 ? (
          <div className="text-xs" style={{ color: GREEN }}>
            none — the evidence gate is met. A human still has to approve this.
          </div>
        ) : (
          <ul className="text-xs mt-1 flex flex-col gap-0.5">
            {row.blockers.map((b) => (
              <li key={b} style={{ color: "rgba(255,255,255,0.8)" }}>· {b}</li>
            ))}
          </ul>
        )}
      </div>

      {row.note ? (
        <div className="text-[10px]" style={{ color: MUTED }}>{row.note}</div>
      ) : null}
    </div>
  );
}

function CalibrationPanel({ cal }: { cal: Calibration }) {
  const ok = cal.verdict === "CALIBRATED";
  return (
    <div className="card p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-semibold">T1 calibration</div>
        <span
          className="text-[11px] px-1.5 py-0.5 rounded font-semibold"
          style={{
            background: "rgba(255,255,255,0.06)",
            color: ok ? GREEN : cal.verdict === "NOT_CALIBRATED" ? RED : MUTED,
          }}
        >
          {txt(cal.verdict)}
        </span>
        <span className="text-[10px]" style={{ color: ok ? GREEN : AMBER }}>
          displaying {cal.display === "T1_PROBABILITY" ? "T1 PROBABILITY" : "T1 SCORE / RANK"}
        </span>
      </div>

      <div className="grid grid-cols-3 md:grid-cols-7 gap-3">
        <Field label="resolved OOS" value={String(cal.scored_holdout_rows)} sub={`of ${cal.holdout_rows}`} />
        <Field label="dev rows" value={String(cal.dev_rows)} />
        <Field label="base rate" value={pct(typeof cal.base_rate === "number" ? cal.base_rate * 100 : null)} />
        <Field label="Brier" value={n(cal.brier, 4)} sub={`base ${n(cal.brier_base_rate, 4)}`} />
        <Field
          label="Brier skill"
          value={n(cal.brier_skill, 4)}
          colour={
            typeof cal.brier_skill === "number"
              ? cal.brier_skill >= (cal.gates?.min_brier_skill ?? 0.02) ? GREEN : RED
              : undefined
          }
        />
        <Field
          label="ECE"
          value={n(cal.ece, 4)}
          colour={
            typeof cal.ece === "number"
              ? cal.ece <= (cal.gates?.max_ece ?? 0.1) ? GREEN : RED
              : undefined
          }
        />
        <Field label="monotone" value={cal.monotone === null ? "—" : cal.monotone ? "yes" : "no"} />
      </div>

      <div>
        <div className="text-[10px] uppercase tracking-wide mb-1" style={{ color: MUTED }}>
          reliability curve — predicted from development sessions, observed on later ones
        </div>
        <table className="w-full text-[11px]">
          <thead>
            <tr style={{ color: MUTED }}>
              <th className="text-left font-normal">score bin</th>
              <th className="text-right font-normal">predicted</th>
              <th className="text-right font-normal">observed</th>
              <th className="text-right font-normal">gap</th>
              <th className="text-right font-normal">n</th>
            </tr>
          </thead>
          <tbody>
            {cal.reliability_curve.map((b) => (
              <tr key={b.bin}>
                <td>{b.bin}</td>
                <td className="text-right">{pct(typeof b.predicted === "number" ? b.predicted * 100 : null)}</td>
                <td className="text-right">{pct(typeof b.observed === "number" ? b.observed * 100 : null)}</td>
                <td className="text-right">{n(b.gap, 3)}</td>
                <td className="text-right">{b.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {cal.blockers.length > 0 ? (
        <ul className="text-xs flex flex-col gap-0.5">
          {cal.blockers.map((b) => (
            <li key={b} style={{ color: "rgba(255,255,255,0.8)" }}>· {b}</li>
          ))}
        </ul>
      ) : null}
      <div className="text-[10px]" style={{ color: MUTED }}>{cal.note}</div>
    </div>
  );
}

function FuturesPaperPanel({ board }: { board: FuturesPaperBoard }) {
  const h = board.health;
  const ev = board.evaluation;
  const rows = board.resolved.slice(-25).reverse();
  return (
    <div className="card p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-semibold">Futures paper book</div>
        <span
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
        >
          {board.banner}
        </span>
        {h.enabled ? null : (
          <span className="text-[11px]" style={{ color: RED }}>DISABLED</span>
        )}
      </div>

      <div className="grid grid-cols-4 md:grid-cols-7 gap-3">
        <Field label="valid plans" value={String(h.plans_seen)} />
        <Field label="paper entries" value={String(h.entries)} />
        <Field label="paper exits" value={String(h.exits)} />
        <Field label="resolved" value={String(h.resolved)} />
        <Field label="open" value={String(h.open)} />
        <Field
          label="feed freshness"
          value={pct(board.freshness_pct)}
          sub={`entered within ${h.max_feed_age_sec.toFixed(0)}s`}
        />
        <Field
          label="holdout net expectancy"
          value={signed(
            typeof ev.holdout?.expectancy_r === "number" ? ev.holdout.expectancy_r : null
          )}
          sub={`n=${txt(ev.holdout?.trades)}`}
        />
      </div>

      {Object.keys(h.skipped).length > 0 ? (
        <div className="text-[10px]" style={{ color: MUTED }}>
          refused:{" "}
          {Object.entries(h.skipped)
            .map(([k, v]) => `${k} ${v}`)
            .join(" · ")}
        </div>
      ) : null}

      {board.open.length > 0 ? (
        <div className="text-[11px]" style={{ color: BLUE }}>
          {board.open.length} paper trade(s) open — stepped on every tick, flattened at the close.
        </div>
      ) : null}

      {rows.length === 0 ? (
        <div className="text-xs" style={{ color: MUTED }}>
          No resolved futures paper trades yet. A valid futures plan on a fresh feed
          enters here at an executable side and resolves to T1/T2/T3, a stop, a
          timeout or the close.
        </div>
      ) : (
        <div className="overflow-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr style={{ color: MUTED }}>
                <th className="text-left font-normal">session</th>
                <th className="text-left font-normal">instrument</th>
                <th className="text-left font-normal">dir</th>
                <th className="text-right font-normal">entry</th>
                <th className="text-right font-normal">exit</th>
                <th className="text-left font-normal">outcome</th>
                <th className="text-right font-normal">gross pts</th>
                <th className="text-right font-normal">cost pts</th>
                <th className="text-right font-normal">net pts</th>
                <th className="text-right font-normal">net R</th>
                <th className="text-right font-normal">MFE</th>
                <th className="text-right font-normal">MAE</th>
                <th className="text-right font-normal">giveback</th>
                <th className="text-right font-normal">to T1</th>
                <th className="text-right font-normal">hold</th>
                <th className="text-left font-normal">cost basis</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={`${r.session}-${r.instrument}-${r.entry_time_ist}-${i}`}>
                  <td>{txt(r.session)}</td>
                  <td>{txt(r.instrument)}</td>
                  <td style={{ color: r.direction === "LONG" ? GREEN : RED }}>
                    {txt(r.direction)}
                  </td>
                  <td className="text-right">{n(r.entry_price)}</td>
                  <td className="text-right">{n(r.exit_price)}</td>
                  <td
                    style={{
                      color: r.outcome === "STOP" ? RED
                        : r.outcome.startsWith("T") ? GREEN : MUTED,
                    }}
                  >
                    {txt(r.outcome)}
                  </td>
                  <td className="text-right">{signed(r.gross_points, 1)}</td>
                  <td className="text-right">{n(r.cost_points, 1)}</td>
                  <td className="text-right">{signed(r.net_points, 1)}</td>
                  <td
                    className="text-right"
                    style={{
                      color: typeof r.net_r === "number"
                        ? r.net_r > 0 ? GREEN : RED
                        : undefined,
                    }}
                  >
                    {signed(r.net_r)}
                  </td>
                  <td className="text-right">{signed(r.mfe_points, 1)}</td>
                  <td className="text-right">{signed(r.mae_points, 1)}</td>
                  <td className="text-right">{n(r.giveback_points, 1)}</td>
                  <td className="text-right">{n(r.minutes_to_t1, 1)}</td>
                  <td className="text-right">{txt(r.hold_bucket)}</td>
                  <td style={{ color: r.cost_status === "COST_MEASURED" ? GREEN : AMBER }}>
                    {txt(r.cost_status)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="text-[10px]" style={{ color: MUTED }}>
        {ev.note} · {ev.rows_with_measured_spread} of {ev.rows_considered} rows had a
        measured futures spread; the rest are charged a modelled crossing and say so.
      </div>
    </div>
  );
}

export default function ProductionReadiness() {
  const [board, setBoard] = useState<ReadinessBoard | null>(null);
  const [futures, setFutures] = useState<FuturesPaperBoard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [b, f] = await Promise.all([getReadiness(), getFuturesPaper()]);
        if (!alive) return;
        setBoard(b);
        setFutures(f);
        setError(null);
      } catch {
        if (alive) setError("readiness unavailable");
      }
    };
    load();
    const t = setInterval(load, 30_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  if (error) {
    return <div className="p-4 text-sm" style={{ color: RED }}>{error}</div>;
  }
  if (!board) {
    return <div className="p-4 text-sm" style={{ color: MUTED }}>loading…</div>;
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="card p-3 flex flex-col gap-1">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="text-sm font-semibold">Production Readiness</div>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded font-semibold"
            style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
          >
            {board.banner}
          </span>
          <span className="text-[11px]" style={{ color: MUTED }}>
            best status{" "}
            <span style={{ color: STATUS_COLOUR[board.best_status] || MUTED }}>
              {txt(board.best_status)}
            </span>
          </span>
        </div>
        <div className="text-[10px]" style={{ color: MUTED }}>{board.note}</div>
        <div className="grid grid-cols-3 gap-3 mt-1">
          <Field
            label="option capture (exact/near)"
            value={pct(board.capture.option_exact_near_pct)}
            colour={
              typeof board.capture.option_exact_near_pct === "number"
                ? board.capture.option_exact_near_pct >= board.capture.option_target_pct
                  ? GREEN : RED
                : undefined
            }
            sub={`target ${board.capture.option_target_pct.toFixed(0)}% — read this before any table below`}
          />
          <Field label="CAS capture" value={pct(board.capture.cas_match_pct)} />
          <Field label="futures feed freshness" value={pct(board.capture.futures_freshness_pct)} />
        </div>
      </div>

      {board.rows.map((r) => (
        <StrategyRow key={`${r.strategy}-${r.vehicle}`} row={r} />
      ))}

      <CalibrationPanel cal={board.calibration} />
      {futures ? <FuturesPaperPanel board={futures} /> : null}
    </div>
  );
}
