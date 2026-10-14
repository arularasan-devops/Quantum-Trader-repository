"use client";

import { useEffect, useState } from "react";
import {
  getCasBoard,
  getCasHealth,
  getCasReconciliation,
  getCasReport,
  type CasBoard as CasBoardData,
  type CasCard,
  type CasHealth,
  type CasReconciliation,
  type CasReport,
} from "@/lib/api";

/** CAS — the closing auction session, 15:10-15:30 IST. RESEARCH / PAPER ONLY.
 *
 * Kept as its own screen rather than folded into the Option Signal, because CAS
 * is a different mechanism with its own window and its own evidence base, and
 * mixing the two would let a research number be read as a live call.
 *
 * Four deliberate choices on this screen:
 *   - there is no order control anywhere on it, and no endpoint behind it that
 *     could place one;
 *   - CAS began on 2025-08-03, so the sample is a handful of sessions. The
 *     expected move and the time-to-T1 estimate print their sample count beside
 *     them and are a measured range, not a forecast;
 *   - the CAS score is a score. It is not a probability and says so;
 *   - entries are priced at the ASK and exits at the BID. Where the book is
 *     missing the row reads UNKNOWN rather than falling back to a mid.
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

function signed(v: number | null | undefined, dp = 2): string {
  return typeof v === "number" ? `${v >= 0 ? "+" : ""}${v.toFixed(dp)}` : "—";
}

function txt(v: unknown): string {
  return v === null || v === undefined || v === "" ? "—" : String(v);
}

function secs(v: number | null | undefined): string {
  if (typeof v !== "number") return "—";
  if (v < 90) return `${Math.round(v)}s`;
  return `${Math.floor(v / 60)}m ${Math.round(v % 60)}s`;
}

function directionColour(d: string | null | undefined): string {
  if (d === "CAS_BULLISH") return GREEN;
  if (d === "CAS_BEARISH") return RED;
  return MUTED;
}

function signalColour(s: string | null | undefined): string {
  if (s === "CAS_BUY_CE") return GREEN;
  if (s === "CAS_BUY_PE") return RED;
  return MUTED;
}

function qualityColour(q: string | null | undefined): string {
  if (q === "EXACT") return GREEN;
  if (q === "NEAR_EXACT") return "#86efac";
  if (q === "DEGRADED") return AMBER;
  return RED;
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

function Card({ card }: { card: CasCard }) {
  const range = card.cas_move_range;
  return (
    <div className="card p-3 flex flex-col gap-3">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="text-sm font-semibold">{card.instrument}</div>
        <span
          className="text-[10px] px-1.5 py-0.5 rounded"
          style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
        >
          CAS PAPER ONLY
        </span>
        <span className="text-[11px]" style={{ color: signalColour(card.signal) }}>
          {txt(card.signal)}
        </span>
        <span className="text-[11px]" style={{ color: directionColour(card.direction) }}>
          {txt(card.direction)}
        </span>
        <span className="text-[10px]" style={{ color: MUTED }}>
          {txt(card.cas_state)} · {txt(card.sub_window)} · closes in {secs(card.remaining_seconds)}
        </span>
        <span className="text-[10px]" style={{ color: qualityColour(card.data_quality) }}>
          {txt(card.data_quality)}
        </span>
      </div>

      <div className="text-[11px]" style={{ color: MUTED }}>
        {txt(card.session)} · expiry {txt(card.expiry)} · DTE {txt(card.days_to_expiry)}
        {" · "}{txt(card.expiry_class)} · status {txt(card.status)}
      </div>

      {/* The underlying block: what the window itself is expected to do. */}
      <div className="grid grid-cols-5 gap-3">
        <Field label="current underlying" value={n(card.current_underlying)} />
        <Field
          label="CAS expected move (pts)"
          value={n(card.cas_expected_move_points, 1)}
          sub={`${txt(card.expectation_basis)} · n=${txt(card.expectation_samples)}`}
        />
        <Field
          label="CAS move range"
          value={
            range && (range.low !== null || range.high !== null)
              ? `${n(range.low, 1)} – ${n(range.high, 1)}`
              : "—"
          }
        />
        <Field label="move %" value={pct(card.move_pct)} />
        <Field label="move / ATR" value={n(card.move_atr)} />
      </div>
      {card.expectation_warning ? (
        <div className="text-[10px]" style={{ color: AMBER }}>
          {card.expectation_warning}
        </div>
      ) : null}

      {/* The vehicle block: what that move is worth on the option, at the book. */}
      <div className="grid grid-cols-5 gap-3">
        <Field
          label="current option premium"
          value={n(card.current_option_premium)}
          sub={`${txt(card.option_type)} ${txt(card.strike)} · ${txt(card.rung)}`}
        />
        <Field
          label="expected option move"
          value={signed(card.expected_option_move_points)}
          sub={
            typeof card.expected_option_move_pct === "number"
              ? `${pct(card.expected_option_move_pct)} · theoretical`
              : "theoretical"
          }
        />
        <Field label="T1" value={n(card.t1)} colour={GREEN} />
        <Field label="T2" value={n(card.t2)} colour={GREEN} />
        <Field label="T3" value={n(card.t3)} colour={GREEN} />
      </div>

      <div className="grid grid-cols-5 gap-3">
        <Field
          label="estimated time to T1"
          value={secs(card.estimated_seconds_to_t1)}
          sub={card.time_to_t1_note || `n=${txt(card.time_to_t1_samples)}`}
        />
        <Field label="stop" value={n(card.stop)} colour={RED}
               sub={typeof card.reward_risk === "number" ? `R:R ${n(card.reward_risk)}` : undefined} />
        <Field
          label="bid / ask"
          value={`${n(card.bid)} / ${n(card.ask)}`}
          sub={`spread ${n(card.spread)} (${pct(card.spread_pct)})`}
        />
        <Field
          label="paper entry"
          value={n(card.preferred_entry)}
          sub={`at the ask · ${txt(card.executability)}`}
        />
        <Field
          label="CAS score"
          value={n(card.cas_score, 1)}
          sub="score, not a probability"
        />
      </div>

      <div className="grid grid-cols-5 gap-3">
        <Field label="T1 likelihood rank" value={txt(card.t1_rank)} sub="rank, not a probability" />
        <Field label="entry quality" value={txt(card.entry_quality)} />
        <Field label="tradability" value={txt(card.tradability)} />
        <Field label="expected hold" value={secs(card.expected_hold_seconds)} />
        <Field label="delta / IV" value={`${n(card.delta)} / ${n(card.iv, 1)}`} />
      </div>

      <div className="text-[11px]" style={{ color: MUTED }}>{txt(card.reason)}</div>
    </div>
  );
}

export default function CasBoard() {
  const [board, setBoard] = useState<CasBoardData | null>(null);
  const [health, setHealth] = useState<CasHealth | null>(null);
  const [recon, setRecon] = useState<CasReconciliation | null>(null);
  const [report, setReport] = useState<CasReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const [b, h, r, rep] = await Promise.all([
          getCasBoard(), getCasHealth(), getCasReconciliation(), getCasReport(),
        ]);
        if (!alive) return;
        setBoard(b);
        setHealth(h);
        setRecon(r);
        setReport(rep);
        setError(null);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "request failed");
      }
    };
    load();
    // 10s inside a 20-minute window: fast enough to watch the auction, slow
    // enough not to add load to a live tick.
    const t = setInterval(load, 10_000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  if (error) {
    return <div className="card p-4 text-sm" style={{ color: RED }}>{error}</div>;
  }
  if (!board || !health || !recon || !report) {
    return <div className="card p-4 text-sm" style={{ color: MUTED }}>Loading…</div>;
  }

  const match = recon.exact_or_near_pct;
  const met = typeof match === "number" && match >= 90;
  const verdict = report.validation.verdict;
  // "Balanced" here means no leg fell out of the funnel unaccounted for. A leg
  // that could not be priced is still counted, with its reason.
  const balanced = Object.values(recon.arithmetic).every((v) => v === 0);
  const resolved = board.resolved.slice(-25).reverse();

  return (
    <div className="flex flex-col gap-3">
      <div className="card p-3">
        <div className="flex items-center gap-2 flex-wrap">
          <div className="text-sm font-semibold">CAS — Closing Auction Session</div>
          <span
            className="text-[10px] px-1.5 py-0.5 rounded"
            style={{ background: "rgba(244,192,37,0.15)", color: AMBER }}
          >
            CAS PAPER ONLY · NO REAL ORDER
          </span>
          <span className="text-[10px]" style={{ color: MUTED }}>
            {board.window} · {txt(health.session)} · the Option Signal never reads this
          </span>
        </div>
        <div className="text-[11px] mt-1" style={{ color: MUTED }}>
          CAS started on 2025-08-03, so the five-year replay contains none of it and
          nothing here is validated yet. Promotion is a human decision — this screen
          cannot make one.
        </div>
      </div>

      <div className="grid grid-cols-6 gap-3">
        <Stat
          label="exact / near-exact"
          value={pct(match)}
          colour={met ? GREEN : RED}
          sub={met ? "at or above the 90% target" : "below target — tables are descriptive only"}
        />
        <Stat label="observations today" value={txt(health.observations)}
              sub={`${txt(recon.candidates)} candidates · ${txt(recon.missing_books)} missing books`} />
        <Stat label="CE / PE captured" value={`${txt(recon.ce_captures)} / ${txt(recon.pe_captures)}`}
              sub={`${txt(recon.both_side_rungs)} rungs with both sides`} />
        <Stat label="paper legs" value={`${txt(recon.paper_entries)} in · ${txt(recon.paper_exits)} out`}
              sub={`${txt(recon.resolved)} resolved · ${txt(recon.unresolved)} open`} />
        <Stat label="reconciliation" value={balanced ? "BALANCED" : "LOSSES"}
              colour={balanced ? GREEN : AMBER} sub={txt(recon.quality_gate)} />
        <Stat label="verdict" value={txt(verdict)}
              colour={verdict === "FAILS" ? RED : verdict === "CAS_PRODUCTION_CANDIDATE" ? GREEN : AMBER}
              sub="manual review only" />
      </div>

      {board.cards.length === 0 ? (
        <div className="card p-4 text-xs" style={{ color: MUTED }}>
          No CAS card. Outside 15:10-15:30 IST this screen is empty by design, and
          inside the window a refusal is a valid result — no trade is forced.
        </div>
      ) : (
        board.cards.map((c) => (
          <Card key={`${c.instrument}-${c.observation_id ?? c.timestamp}`} card={c} />
        ))
      )}

      <div className="card p-3">
        <div className="text-sm font-semibold">Paper legs</div>
        <div className="text-[10px] mt-0.5" style={{ color: MUTED }}>
          simulated fills — entered at the ask, exited at the bid, charged brokerage
          and taxes. NET is what the book would actually have paid.
        </div>
        {board.open.length === 0 && resolved.length === 0 ? (
          <div className="text-xs mt-2" style={{ color: MUTED }}>None yet.</div>
        ) : (
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-xs">
              <thead>
                <tr className="text-[10px] uppercase tracking-wide" style={{ color: MUTED }}>
                  <th className="text-left pb-1 pr-3">session</th>
                  <th className="text-left pb-1 pr-3">leg</th>
                  <th className="text-left pb-1 pr-3">variant</th>
                  <th className="text-left pb-1 pr-3">entry</th>
                  <th className="text-left pb-1 pr-3">exit</th>
                  <th className="text-left pb-1 pr-3">gross</th>
                  <th className="text-left pb-1 pr-3">NET</th>
                  <th className="text-left pb-1 pr-3">R</th>
                  <th className="text-left pb-1 pr-3">MFE / MAE</th>
                  <th className="text-left pb-1 pr-3">hold</th>
                  <th className="text-left pb-1 pr-3">status</th>
                  <th className="text-left pb-1">book</th>
                </tr>
              </thead>
              <tbody>
                {[...board.open, ...resolved].map((r) => (
                  <tr key={r.episode_id} className="border-t border-white/5">
                    <td className="py-1.5 pr-3">{txt(r.session)}</td>
                    <td className="pr-3">
                      <div className="font-semibold">{r.instrument}</div>
                      <div className="text-[10px]" style={{ color: MUTED }}>
                        {txt(r.option_type)} · {txt(r.rung)}
                      </div>
                    </td>
                    <td className="pr-3">{txt(r.strategy_variant)}</td>
                    <td className="pr-3">{n(r.entry_price)}</td>
                    <td className="pr-3">{n(r.exit_price)}</td>
                    <td className="pr-3">{signed(r.gross_rupees, 0)}</td>
                    <td
                      className="pr-3 font-semibold"
                      style={{ color: (r.net_rupees ?? 0) >= 0 ? GREEN : RED }}
                    >
                      {signed(r.net_rupees, 0)}
                    </td>
                    <td className="pr-3">{signed(r.net_r)}</td>
                    <td className="pr-3">
                      {signed(r.mfe_points)} / {signed(r.mae_points)}
                    </td>
                    <td className="pr-3">{secs(r.hold_seconds)}</td>
                    <td className="pr-3">{txt(r.status)}</td>
                    <td style={{ color: qualityColour(r.data_quality) }}>
                      {txt(r.executability)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="card p-3">
        <div className="text-sm font-semibold">Evidence before promotion</div>
        <div className="text-[10px] mt-0.5" style={{ color: MUTED }}>
          {report.validation.reason}
          {report.validation.excluded_pre_cas_rows > 0
            ? ` · ${report.validation.excluded_pre_cas_rows} pre-CAS rows excluded`
            : ""}
        </div>
        <div className="grid grid-cols-3 gap-2 mt-2">
          {Object.entries(report.validation.evidence.checks).map(([k, v]) => (
            <div key={k} className="text-xs flex items-baseline justify-between">
              <span style={{ color: MUTED }}>{k.replace(/_/g, " ")}</span>
              <span style={{ color: v.ok ? GREEN : AMBER }}>
                {txt(v.have)} / {v.need}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="card p-3">
        <div className="text-sm font-semibold">CAS research questions</div>
        <div className="mt-2 flex flex-col gap-1">
          {report.questions.map((q) => (
            <div key={q.question} className="text-xs">
              <span style={{ color: MUTED }}>{q.question}</span>{" "}
              <span
                style={{
                  color: q.answer.startsWith("REQUIRES_MORE_DATA") ? AMBER : "#fff",
                }}
              >
                {q.answer}
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
