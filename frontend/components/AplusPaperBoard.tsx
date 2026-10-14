"use client";

import { useEffect, useState } from "react";
import {
  getP17Board,
  getP17Health,
  getP17Paper,
  getP17Summary,
  type P17Board,
  type P17BoardRow,
  type P17Health,
  type P17Paper,
  type P17Summary,
} from "@/lib/api";

/** A+ PAPER — the research board, and the costed paper book behind it.
 *
 * PAPER ONLY. Nothing on this screen has an order path: the rows are ranked by a
 * research score that the production Signal never reads, and the fills below are
 * simulated against the recorded book (entered at the ASK, exited at the BID).
 *
 * Four things here are deliberate and are not decoration:
 *   - the EXACT-match rate is printed first, because every economic number on
 *     this screen is only as real as the book it was measured against. The
 *     previous attempt matched 48 of 3,448 legs; below the target the tables are
 *     descriptive, not evidence;
 *   - GROSS, COST and NET are always shown together. A gross-only paper P&L is
 *     the specific way this tool has misled before — the measured round trip ran
 *     several times the gross travel;
 *   - the T1 column is a RANK, not a probability, and says so. It becomes a
 *     probability only after out-of-sample calibration, which has not happened;
 *   - an EMPTY board is a valid day and reads as such. No row is promoted to
 *     fill a slot.
 */
const GREEN = "#22c55e";
const RED = "#ef4444";
const AMBER = "#f4c025";
const MUTED = "rgba(255,255,255,0.45)";

function n(v: number | null | undefined, dp = 2): string {
  return typeof v === "number" ? v.toFixed(dp) : "—";
}

function pct(v: number | null | undefined): string {
  return typeof v === "number" ? `${v.toFixed(1)}%` : "—";
}

function pts(v: number | null | undefined): string {
  return typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(2)}` : "—";
}

function txt(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

function qualityColour(q: string | null | undefined): string {
  if (q === "EXACT") return GREEN;
  if (q === "GOOD") return "#86efac";
  if (q === "DEGRADED") return AMBER;
  return RED;
}

function vehicleColour(c: string | null | undefined): string {
  if (c === "GREEN") return GREEN;
  if (c === "YELLOW") return AMBER;
  if (c === "RED") return RED;
  return MUTED;
}

function entryColour(q: string | null | undefined): string {
  if (q === "IDEAL" || q === "GOOD") return GREEN;
  if (q === "ACCEPTABLE") return AMBER;
  if (q === "CHASED" || q === "SEVERELY_CHASED") return RED;
  return MUTED;
}

function Stat({ label, value, colour, sub }: {
  label: string;
  value: string;
  colour?: string;
  sub?: string;
}) {
  return (
    <div className="card p-3">
      <div className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
        {label}
      </div>
      <div className="text-lg font-semibold" style={{ color: colour || "#fff" }}>
        {value}
      </div>
      {sub ? (
        <div className="text-[10px] mt-0.5" style={{ color: MUTED }}>{sub}</div>
      ) : null}
    </div>
  );
}

function BoardRow({ row }: { row: P17BoardRow }) {
  return (
    <tr className="border-t border-white/5">
      <td className="py-1.5 pr-3">
        <div className="font-semibold">{row.instrument}</div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          {txt(row.direction)} · {txt(row.symbol)}
        </div>
      </td>
      <td className="pr-3">{txt(row.vehicle)}</td>
      <td className="pr-3">{n(row.premium)}</td>
      <td className="pr-3">
        {n(row.entry)}
        <div className="text-[10px]" style={{ color: MUTED }}>
          ask {n(row.ask)} / bid {n(row.bid)}
        </div>
      </td>
      <td className="pr-3" style={{ color: RED }}>{n(row.stop)}</td>
      <td className="pr-3">
        {n(row.target1)} · {n(row.target2)} · {n(row.target3)}
      </td>
      <td className="pr-3 font-semibold">{n(row.a_plus_score, 1)}</td>
      <td className="pr-3">
        {txt(row.t1_rank)}
        <div className="text-[10px]" style={{ color: MUTED }}>rank, not a probability</div>
        {row.t1_basis && row.t1_basis !== "ENGINE_TARGET" ? (
          <div className="text-[10px]" style={{ color: AMBER }}>
            derived T1 · research only, never promoted
          </div>
        ) : null}
      </td>
      <td className="pr-3" style={{ color: vehicleColour(row.vehicle_class) }}>
        {txt(row.vehicle_class)}
      </td>
      <td className="pr-3">
        {n(row.spread)}
        <div className="text-[10px]" style={{ color: MUTED }}>{pct(row.spread_pct)}</div>
      </td>
      <td className="pr-3" style={{ color: entryColour(row.entry_quality) }}>
        {txt(row.entry_quality)}
      </td>
      <td className="pr-3">
        {row.expected_hold_minutes === null ? "—" : `${row.expected_hold_minutes}m`}
      </td>
      <td className="pr-3" style={{ color: qualityColour(row.data_quality) }}>
        {txt(row.data_quality)}
      </td>
      <td className="text-[10px]" style={{ color: MUTED }}>
        {(row.reasons || []).slice(0, 2).join(" · ") || "—"}
      </td>
    </tr>
  );
}

function BoardTable({ title, rows, note }: {
  title: string;
  rows: P17BoardRow[];
  note: string;
}) {
  return (
    <div className="card p-3">
      <div className="flex items-baseline justify-between">
        <div className="text-sm font-semibold">{title}</div>
        <div className="text-[10px]" style={{ color: MUTED }}>{note}</div>
      </div>
      {rows.length === 0 ? (
        <div className="text-xs mt-2" style={{ color: MUTED }}>
          None. An empty board is a valid result — no candidate is promoted to
          fill a slot.
        </div>
      ) : (
        <div className="overflow-x-auto mt-2">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
                <th className="text-left pb-1 pr-3">candidate</th>
                <th className="text-left pb-1 pr-3">CE/PE</th>
                <th className="text-left pb-1 pr-3">premium</th>
                <th className="text-left pb-1 pr-3">entry</th>
                <th className="text-left pb-1 pr-3">stop</th>
                <th className="text-left pb-1 pr-3">T1 · T2 · T3</th>
                <th className="text-left pb-1 pr-3">A+ score</th>
                <th className="text-left pb-1 pr-3">T1 rank</th>
                <th className="text-left pb-1 pr-3">vehicle</th>
                <th className="text-left pb-1 pr-3">spread</th>
                <th className="text-left pb-1 pr-3">entry quality</th>
                <th className="text-left pb-1 pr-3">hold</th>
                <th className="text-left pb-1 pr-3">data</th>
                <th className="text-left pb-1">why</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => <BoardRow key={r.observation_id} row={r} />)}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export default function AplusPaperBoard() {
  const [board, setBoard] = useState<P17Board | null>(null);
  const [health, setHealth] = useState<P17Health | null>(null);
  const [paper, setPaper] = useState<P17Paper | null>(null);
  const [summary, setSummary] = useState<P17Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Each panel loads on its own. The board and health answer in milliseconds
  // while the paper book and the daily summary re-derive the day from the
  // evidence files, so waiting for all four let one slow read blank the whole
  // screen — which is exactly how this tab appeared broken.
  useEffect(() => {
    let alive = true;
    const load = () => {
      const fail = (e: unknown) => {
        if (alive) setError(e instanceof Error ? e.message : "request failed");
      };
      getP17Board().then((v) => { if (alive) { setBoard(v); setError(null); } }, fail);
      getP17Health().then((v) => { if (alive) setHealth(v); }, fail);
      getP17Paper().then((v) => { if (alive) setPaper(v); }, fail);
      getP17Summary().then((v) => { if (alive) setSummary(v); }, fail);
    };
    load();
    const t = setInterval(load, 20_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!board || !health) {
    return (
      <div className="card p-4 text-sm" style={{ color: error ? RED : MUTED }}>
        {error ?? "Loading…"}
      </div>
    );
  }

  const exact = summary?.exact_match_rate_pct;
  const met = summary?.meets_target === true;
  const open = paper?.open ?? [];
  const resolved = (paper?.resolved ?? []).slice(-25).reverse();

  return (
    <div className="flex flex-col gap-3">
      <div className="card p-3">
        <div className="flex items-center gap-2">
          <div className="text-sm font-semibold">A+ Paper</div>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
          >
            PAPER ONLY · NO REAL ORDER
          </span>
          <span className="text-[10px]" style={{ color: MUTED }}>
            research ranking — the production Signal does not read it
          </span>
        </div>
        <div className="text-[11px] mt-1" style={{ color: MUTED }}>
          {board.note}
        </div>
        {error ? (
          <div className="text-[11px] mt-1" style={{ color: RED }}>
            last refresh failed: {error} — panels below may be stale
          </div>
        ) : null}
      </div>

      {summary === null ? (
        <div className="card p-3 text-xs" style={{ color: MUTED }}>
          Daily summary still loading — it is derived from the evidence files,
          so it arrives after the board.
        </div>
      ) : (
      <div className="grid grid-cols-6 gap-3">
        <Stat
          label="EXACT match rate"
          value={pct(exact)}
          colour={met ? GREEN : RED}
          sub={met ? "at or above the 90% target" : "below target — tables are descriptive only"}
        />
        <Stat label="candidates today" value={txt(health.today_rows)}
              sub={`${txt(summary.candidates)} recorded in total`} />
        <Stat label="A+ found" value={txt(board.a_plus_found)}
              colour={board.a_plus_found > 0 ? GREEN : MUTED}
              sub={`${board.watch_found} on watch · zero is valid`} />
        <Stat label="paper gross" value={pts(
          typeof summary.paper_net_points === "number" &&
          typeof summary.paper_cost_points === "number"
            ? summary.paper_net_points + summary.paper_cost_points : null)}
              sub="before cost" />
        <Stat label="paper cost" value={pts(summary.paper_cost_points)}
              colour={AMBER} sub="spread + brokerage + taxes + slippage" />
        <Stat label="paper NET" value={pts(summary.paper_net_points)}
              colour={(summary.paper_net_points ?? 0) >= 0 ? GREEN : RED}
              sub={`expectancy ${n(summary.paper_expectancy_r)}R`} />
      </div>
      )}

      <BoardTable title="Preferred" rows={board.preferred}
                  note="at most 5 · ranked by the research A+ score" />
      <BoardTable title="Watch" rows={board.watch}
                  note="at most 5 · not tradable as they stand" />

      <div className="card p-3">
        <div className="flex items-baseline justify-between">
          <div className="text-sm font-semibold">Paper book</div>
          <div className="text-[10px]" style={{ color: MUTED }}>
            {paper === null ? "loading…" : (
              <>
                entered at the ASK · exited at the BID · {open.length} open ·
                {" "}{paper.resolved.length} resolved
              </>
            )}
          </div>
        </div>
        {paper === null ? (
          <div className="text-xs mt-2" style={{ color: MUTED }}>
            Reading the costed book…
          </div>
        ) : resolved.length === 0 && open.length === 0 ? (
          <div className="text-xs mt-2" style={{ color: MUTED }}>
            No paper episode yet. An episode is only opened on a candidate whose
            book is two-sided and fresh, so a thin or stale chain produces none.
          </div>
        ) : (
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
                  <th className="text-left pb-1 pr-3">episode</th>
                  <th className="text-left pb-1 pr-3">entry</th>
                  <th className="text-left pb-1 pr-3">exit</th>
                  <th className="text-left pb-1 pr-3">gross</th>
                  <th className="text-left pb-1 pr-3">cost</th>
                  <th className="text-left pb-1 pr-3">net</th>
                  <th className="text-left pb-1 pr-3">net R</th>
                  <th className="text-left pb-1 pr-3">MFE / MAE</th>
                  <th className="text-left pb-1 pr-3">hold</th>
                  <th className="text-left pb-1 pr-3">reason</th>
                  <th className="text-left pb-1">cost basis</th>
                </tr>
              </thead>
              <tbody>
                {[...open, ...resolved].map((p) => (
                  <tr key={p.episode_id} className="border-t border-white/5">
                    <td className="py-1.5 pr-3">
                      <div>{p.instrument} {txt(p.vehicle)}</div>
                      <div className="text-[10px]" style={{ color: MUTED }}>
                        {txt(p.symbol)}
                      </div>
                    </td>
                    <td className="pr-3">
                      {n(p.entry_price)}
                      <div className="text-[10px]" style={{ color: MUTED }}>
                        {txt(p.entry_side)}
                      </div>
                    </td>
                    <td className="pr-3">
                      {n(p.exit_price)}
                      <div className="text-[10px]" style={{ color: MUTED }}>
                        {p.exit_price === null ? "open" : txt(p.exit_side)}
                      </div>
                    </td>
                    <td className="pr-3">{pts(p.gross_points)}</td>
                    <td className="pr-3" style={{ color: AMBER }}>
                      {pts(p.cost_points)}
                    </td>
                    <td className="pr-3 font-semibold"
                        style={{ color: (p.net_points ?? 0) >= 0 ? GREEN : RED }}>
                      {pts(p.net_points)}
                    </td>
                    <td className="pr-3">{n(p.net_r)}</td>
                    <td className="pr-3">
                      {pts(p.mfe_points)} / {pts(p.mae_points)}
                    </td>
                    <td className="pr-3">
                      {p.hold_minutes === null ? "—" : `${n(p.hold_minutes, 1)}m`}
                    </td>
                    <td className="pr-3">{txt(p.exit_reason)}</td>
                    <td style={{ color: p.cost_status === "MEASURED" ? GREEN : AMBER }}>
                      {txt(p.cost_status)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card p-3">
        <div className="text-sm font-semibold">Promotion status</div>
        {summary === null ? (
          <div className="text-xs mt-1" style={{ color: MUTED }}>loading…</div>
        ) : (
        <div className="text-xs mt-1">
          <span style={{ color: summary.promotion.verdict === "PRODUCTION_CANDIDATE" ? GREEN : AMBER }}>
            {txt(summary.promotion.status)} · {txt(summary.promotion.verdict)}
          </span>
          <span style={{ color: MUTED }}>
            {" "}— {summary.questions.requires_more_data} of{" "}
            {summary.questions.total} daily questions still read
            REQUIRES_MORE_DATA.
          </span>
        </div>
        )}
        <div className="text-[11px] mt-1" style={{ color: MUTED }}>
          Promotion needs 100 development and 100 chronological holdout outcomes
          with a real book, positive holdout net expectancy, PF above 1 and
          fold stability. Until then nothing here changes production, and this
          phase changes no production logic regardless of its verdict.
        </div>
        {health.failures > 0 ? (
          <div className="text-[11px] mt-1" style={{ color: RED }}>
            capture failures: {health.failures} — last: {txt(health.last_error)}
          </div>
        ) : null}
        {health.tracker.dropped > 0 ? (
          <div className="text-[11px] mt-1" style={{ color: AMBER }}>
            {health.tracker.dropped} leg(s) dropped by the tracking budget
            ({health.tracker.budget}) and recorded as TRACKING_DROPPED rather
            than silently missing.
          </div>
        ) : null}
      </div>
    </div>
  );
}
