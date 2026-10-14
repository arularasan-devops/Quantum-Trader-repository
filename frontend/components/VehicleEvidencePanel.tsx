"use client";

import { useEffect, useState } from "react";
import {
  getP17Evidence,
  getP17Summary,
  type P17EvidenceRow,
  type P17Evidence,
  type P17Side,
  type P17Summary,
} from "@/lib/api";

/** VEHICLE EVIDENCE — what the option actually cost, on every eligible candidate.
 *
 * RESEARCH ONLY. This is the feed the A+ label sits on top of, not the other way
 * round: every eligible candidate is recorded, A+ or not, BUY or refused, so the
 * vehicle questions (does the spread eat the edge, CE or PE, which strikes can
 * pay) are answerable in weeks rather than in months of A+ rows.
 *
 * Both sides are quoted at the SAME instant. That is the only reason the
 * "would the opposite side have won" column means anything — a CE from one
 * timestamp against a PE from another answers nothing.
 *
 * Every row carries its own data_quality. A row that reads STALE or MISSING has
 * no book behind it and is excluded from every measured table; it is shown here
 * rather than hidden, because a silently-dropped row is how a dataset becomes
 * 98% unusable without anyone noticing.
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

function txt(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

function time(ts: number | null): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
    timeZone: "Asia/Kolkata",
  });
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

function SideCell({ side, selected }: { side: P17Side; selected: boolean }) {
  return (
    <td className="pr-3 align-top">
      <div className="flex items-center gap-1">
        <span className={selected ? "font-semibold" : ""}>{txt(side.vehicle)}</span>
        <span className="text-[10px]" style={{ color: MUTED }}>
          {txt(side.strike)} {txt(side.moneyness)}
        </span>
        {selected ? (
          <span className="text-[9px] px-1 rounded"
                style={{ background: "rgba(255,255,255,0.1)", color: MUTED }}>
            taken
          </span>
        ) : null}
      </div>
      <div className="text-[10px]" style={{ color: MUTED }}>
        prem {n(side.premium)} · bid {n(side.bid)} / ask {n(side.ask)}
      </div>
      <div className="text-[10px]">
        <span style={{ color: MUTED }}>spread </span>
        {n(side.spread)} ({pct(side.spread_pct)})
        <span style={{ color: vehicleColour(side.vehicle_class) }}>
          {" "}· {txt(side.vehicle_class)}
        </span>
      </div>
      <div className="text-[10px]" style={{ color: MUTED }}>
        OI {txt(side.oi)} · vol {txt(side.volume)} · IV {n(side.iv)} · Δ{n(side.delta)}
      </div>
      <div className="text-[10px]" style={{ color: qualityColour(side.data_quality) }}>
        {txt(side.data_quality)}
      </div>
    </td>
  );
}

function EvidenceRow({ row }: { row: P17EvidenceRow }) {
  return (
    <tr className="border-t border-white/5">
      <td className="py-1.5 pr-3 align-top">
        <div className="font-semibold">{row.instrument}</div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          {time(row.signal_ts)} · {txt(row.candidate_class)}
        </div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          {txt(row.direction)} · {txt(row.regime)} · {txt(row.htf_alignment)}
        </div>
      </td>
      <SideCell side={row.ce_pe.selected}
                selected={row.selected_vehicle === row.ce_pe.selected.vehicle} />
      <SideCell side={row.ce_pe.opposite} selected={false} />
      <td className="pr-3 align-top">
        <div>{txt(row.room.t1_rank)}</div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          score {n(row.room.t1_score, 1)} — not a probability
        </div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          needs {n(row.room.underlying_move_required)} pts of underlying
        </div>
        <div className="text-[10px]" style={{ color: MUTED }}>
          room after spread {n(row.room.room_after_spread)}
        </div>
      </td>
      <td className="pr-3 align-top">{txt(row.entry_quality)}</td>
      <td className="pr-3 align-top">
        {n(row.a_plus_score, 1)}
        <div className="text-[10px]" style={{ color: MUTED }}>
          {txt(row.a_plus_label)}
        </div>
      </td>
      <td className="pr-3 align-top" style={{ color: row.both_sides ? GREEN : RED }}>
        {row.both_sides ? "both" : "one"}
      </td>
      <td className="align-top text-[10px]" style={{ color: MUTED }}>
        {txt(row.tracking)}
      </td>
    </tr>
  );
}

export default function VehicleEvidencePanel() {
  const [ev, setEv] = useState<P17Evidence | null>(null);
  const [summary, setSummary] = useState<P17Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The candidate table and the daily summary load separately: the summary is
  // derived from the evidence files and is the slower of the two, and waiting
  // for it used to leave this whole tab on "Loading…".
  useEffect(() => {
    let alive = true;
    const load = () => {
      const fail = (err: unknown) => {
        if (alive) setError(err instanceof Error ? err.message : "request failed");
      };
      getP17Evidence(80).then((v) => { if (alive) { setEv(v); setError(null); } }, fail);
      getP17Summary().then((v) => { if (alive) setSummary(v); }, fail);
    };
    load();
    const t = setInterval(load, 20_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (!ev) {
    return (
      <div className="card p-4 text-sm" style={{ color: error ? RED : MUTED }}>
        {error ?? "Loading…"}
      </div>
    );
  }

  const c = ev.counts;
  const met = c.meets_target === true;
  const classes = summary?.vehicle_classes || {};

  return (
    <div className="flex flex-col gap-3">
      <div className="card p-3">
        <div className="flex items-center gap-2">
          <div className="text-sm font-semibold">Vehicle Evidence</div>
          <span className="text-[10px] px-1.5 py-0.5 rounded"
                style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}>
            RESEARCH ONLY
          </span>
        </div>
        <div className="text-[11px] mt-1" style={{ color: MUTED }}>{ev.note}</div>
        {error ? (
          <div className="text-[11px] mt-1" style={{ color: RED }}>
            last refresh failed: {error} — panels below may be stale
          </div>
        ) : null}
      </div>

      <div className="grid grid-cols-6 gap-3">
        <Stat label="EXACT match rate" value={pct(c.exact_match_rate_pct)}
              colour={met ? GREEN : RED}
              sub={`target ${pct(c.target_pct)} · EXACT means within ${c.tolerances_ms.exact / 1000}s`} />
        <Stat label="unmatched" value={`${c.unmatched} (${pct(c.unmatched_pct)})`}
              colour={c.unmatched > 0 ? AMBER : GREEN}
              sub="no book at the decision instant" />
        <Stat label="both sides quoted" value={pct(summary?.both_side_pct)}
              sub="CE and PE at the same timestamp" />
        <Stat label="GREEN / YELLOW / RED"
              value={`${classes.GREEN ?? 0} / ${classes.YELLOW ?? 0} / ${classes.RED ?? 0}`}
              sub={`${classes.UNKNOWN ?? 0} not gradeable`} />
        <Stat label="wrong market" value={pct(summary?.wrong_market_pct)}
              sub="direction was wrong" />
        <Stat label="wrong vehicle" value={pct(summary?.wrong_vehicle_pct)}
              colour={AMBER} sub="direction right, the option still lost" />
      </div>

      <div className="card p-3">
        <div className="flex items-baseline justify-between">
          <div className="text-sm font-semibold">Candidates today</div>
          <div className="text-[10px]" style={{ color: MUTED }}>
            {ev.rows.length} shown · every eligible candidate, A+ or not
          </div>
        </div>
        {ev.rows.length === 0 ? (
          <div className="text-xs mt-2" style={{ color: MUTED }}>
            Nothing captured yet in this session. Capture only exists while the
            app is up, and each report states how many sessions it covers so the
            dataset cannot quietly become a record of the days it was running.
          </div>
        ) : (
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
                  <th className="text-left pb-1 pr-3">candidate</th>
                  <th className="text-left pb-1 pr-3">selected side</th>
                  <th className="text-left pb-1 pr-3">opposite side</th>
                  <th className="text-left pb-1 pr-3">T1 reachability</th>
                  <th className="text-left pb-1 pr-3">entry</th>
                  <th className="text-left pb-1 pr-3">A+</th>
                  <th className="text-left pb-1 pr-3">sides</th>
                  <th className="text-left pb-1">tracking</th>
                </tr>
              </thead>
              <tbody>
                {ev.rows.map((r) => (
                  <EvidenceRow key={r.observation_id} row={r} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card p-3">
        <div className="text-sm font-semibold">The 25 daily questions</div>
        {summary === null ? (
          <div className="text-[11px] mt-1" style={{ color: MUTED }}>
            Still loading — the answers are derived from the evidence files.
          </div>
        ) : (
        <>
        <div className="text-[11px] mt-1" style={{ color: MUTED }}>
          {summary.questions.requires_more_data} of {summary.questions.total}
          {" "}still read REQUIRES_MORE_DATA. That is the honest state of the
          evidence, not a failure of the report.
        </div>
        <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1">
          {summary.questions_detail.map((q) => {
            const a = typeof q.answer === "string" ? q.answer : JSON.stringify(q.answer);
            const thin = a.includes("REQUIRES_MORE_DATA");
            return (
              <div key={q.question} className="text-[11px]">
                <span style={{ color: MUTED }}>{q.question}</span>{" "}
                <span style={{ color: thin ? AMBER : "#fff" }}>{a}</span>
              </div>
            );
          })}
        </div>
        </>
        )}
      </div>

      <div className="card p-3 text-[11px]" style={{ color: MUTED }}>
        Historical five-year context behind these rows is
        {" "}<span style={{ color: AMBER }}>UNDERLYING_ONLY</span> and gross: it
        can say whether a market setup moved, never whether an option would have
        paid. Option profitability on this screen comes only from the recorded
        book above.
      </div>
    </div>
  );
}
