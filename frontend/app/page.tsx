"use client";
import { useState, useEffect } from "react";
import { useLiveData } from "@/lib/useLiveData";
import { themeFor } from "@/lib/theme";
import {
  buyOption,
  sellPosition,
  selectInstrument,
  getEngineSettings,
  OrderResult,
} from "@/lib/api";
import { playBuySound, playExitSound } from "@/lib/sound";
import Alerts from "@/components/Alerts";
import AlertBell, { AlertToast } from "@/components/AlertBell";
import {
  noteDashboardSignal,
  noteDecisionState,
  noteMomentumSignal,
  setSoundScope,
  startAlerts,
} from "@/lib/alerts";
import BacktestModal from "@/components/BacktestModal";
import PerformanceModal from "@/components/PerformanceModal";
import ProDashboard from "@/components/ProDashboard";
import MomentumBoard from "@/components/MomentumBoard";
import FlowBoard from "@/components/FlowBoard";
import SpreadBoard from "@/components/SpreadBoard";
import FuturesBoard from "@/components/FuturesBoard";
import JournalBoard from "@/components/JournalBoard";
import AplusPaperBoard from "@/components/AplusPaperBoard";
import DailyBestBoard from "@/components/DailyBestBoard";
import VehicleEvidencePanel from "@/components/VehicleEvidencePanel";
import CasBoard from "@/components/CasBoard";
import ProductionReadiness from "@/components/ProductionReadiness";
import ReportsBoard from "@/components/ReportsBoard";
import QuantumSignal from "@/components/QuantumSignal";
import FuturesSignalBoard from "@/components/FuturesSignalBoard";
import CoreAplusPaper from "@/components/CoreAplusPaper";
import ResearchDashboard from "@/components/ResearchDashboard";
import AccountDashboard from "@/components/AccountDashboard";
import PositionSizingModal from "@/components/PositionSizingModal";
import WatchlistModal from "@/components/WatchlistModal";

type View =
  | "signal" | "futsignal" | "decision" | "momentum" | "flow" | "spreads"
  | "futures" | "journal" | "reports" | "oneaday" | "aplus" | "evidence"
  | "cas" | "readiness" | "core" | "research" | "account";

// One row per tab, so the bar is described once instead of sixteen near-identical
// buttons. Order is the order on screen.
const TABS: { key: View; label: string; badge?: string; title?: string }[] = [
  { key: "signal", label: "Option Signal", title: "Option Signal — the single-page CE/PE decision tool (regime/volatility-gated BUY/WAIT/HOLD/EXIT)" },
  { key: "futsignal", label: "Futures Signal", badge: "RESEARCH", title: "Futures Signal — point-based futures research. No order path, paper or live." },
  { key: "decision", label: "Decision" },
  { key: "spreads", label: "Spreads", badge: "RESEARCH" },
  { key: "futures", label: "Futures", badge: "PAPER" },
  { key: "journal", label: "Journal" },
  { key: "reports", label: "Reports" },
  { key: "cas", label: "CAS", title: "Closing auction session, 15:10-15:30 IST. PAPER ONLY — no order path, kept apart from the Option Signal." },
  { key: "account", label: "Account" },
];

type PendingOrder = {
  side: "BUY" | "SELL";
  symbol: string;
  lots: number;
  // Gates this manual order would break. The bot refuses these entries; a hand
  // order used to bypass them silently, which is how a 34%-confidence buy and a
  // ₹7.2 premium buy reached the journal.
  warnings?: string[];
};

export default function Page() {
  // Per-tab instrument selection. Kept in this tab's own React state (not on the
  // server), so duplicating the tab and switching to a different instrument
  // no longer replicates across every tab.
  const [instrument, setInstrument] = useState("CRUDEOIL");
  const { snap, connected } = useLiveData(instrument);
  const [pending, setPending] = useState<PendingOrder | null>(null);
  const [gates, setGates] = useState<{ minConfidence: number; minPremium: number }>({
    minConfidence: 78,
    minPremium: 20,
  });
  const [toast, setToast] = useState<string | null>(null);
  const [showBacktest, setShowBacktest] = useState(false);
  const [showPerformance, setShowPerformance] = useState(false);
  const [showSizing, setShowSizing] = useState(false);
  const [showWatchlist, setShowWatchlist] = useState(false);
  const [view, setView] = useState<View>("signal");

  // Restore this tab's instrument on load/refresh: prefer the ?instrument= URL
  // param (so each tab keeps its own selection and survives a refresh), then the
  // last-used value in localStorage. Runs on mount only — set here (not in the
  // useState initializer) to avoid an SSR/hydration mismatch.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const fromUrl = new URLSearchParams(window.location.search).get("instrument");
    const saved = (fromUrl || window.localStorage.getItem("qt_instrument") || "").toUpperCase();
    if (saved && saved !== instrument) {
      setInstrument(saved);
      selectInstrument(saved).catch(() => {});
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The gates a manual order is checked against, read from the engine so the
  // warning always quotes the values actually in force.
  useEffect(() => {
    getEngineSettings()
      .then((s) =>
        setGates({
          minConfidence: s.buy_min_confidence,
          minPremium: s.auto_trade.min_premium ?? 20,
        }),
      )
      .catch(() => {});
  }, []);

  // BUY/fill alerts run in ONE page-level engine (lib/alerts) rather than inside
  // the Signals screen, so a signal still sounds while you are on Futures or
  // Journal, or while the browser tab is in the background. Started here (not in
  // the toast) so it is running even before the first snapshot arrives.
  useEffect(() => {
    startAlerts();
  }, []);

  // Sound belongs to the Signal dashboard only: it is the screen you watch to
  // trade, and a chime from a research screen tells you nothing you can act on.
  // The alert engine keeps recording events on every tab; only the audio is
  // scoped.
  useEffect(() => {
    setSoundScope(view === "signal");
  }, [view]);

  // ...and the MAIN dashboard's own call card sounds as well, the moment it
  // flips to BUY. The board poll alone was not enough: this is the screen the
  // user actually watches, its card can turn BUY between two 4s polls, and a
  // name below the board's alert gate never appears in that list at all.
  const dashSignal = snap?.decision.signal;
  const dashMarketSignal = snap?.decision.market_signal;
  // The Early Momentum engine has no single "signal" field: its call is
  // active + side, and the stage is what changes as it firms up.
  const em = snap?.decision.early_momentum;
  const momentumSignal = em?.active && em?.side ? `EARLY BUY ${em.side}` : "NONE";
  useEffect(() => {
    if (!snap || !dashSignal) return;
    const conf = snap.decision.confidence;
    const prem = snap.decision.current_premium;
    noteDashboardSignal(
      instrument,
      snap.instrument_name || instrument,
      dashSignal,
      [
        snap.decision.recommended_option || "",
        conf != null ? `${conf.toFixed(0)}% confidence` : "",
        prem != null ? `premium ₹${prem.toFixed(1)}` : "",
      ]
        .filter(Boolean)
        .join(" · "),
    );
    // Keyed on the signal only: a re-render on every tick must not re-announce.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument, dashSignal]);

  // The DECISION tone: the production engine's state changed to something other
  // than BUY (EXIT, HOLD, WAIT, AVOID). A different fact from a new BUY, so a
  // different tone — an EXIT that sounds like an entry is worse than silence.
  useEffect(() => {
    if (!snap) return;
    const state = dashMarketSignal || dashSignal;
    if (!state) return;
    noteDecisionState(
      instrument,
      snap.instrument_name || instrument,
      state,
      snap.decision.recommended_option || "",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument, dashMarketSignal, dashSignal]);

  // The MOMENTUM tone: the separate Early Momentum advisory engine, which has
  // its own plan and is not the production call.
  useEffect(() => {
    if (!snap) return;
    noteMomentumSignal(
      instrument,
      snap.instrument_name || instrument,
      momentumSignal,
      [em?.option_symbol || "", em?.stage || ""].filter(Boolean).join(" · "),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [instrument, momentumSignal]);

  if (!snap) {
    return (
      <div className="h-screen grid place-items-center text-white/50">
        Connecting to Quantum Trader engine…
      </div>
    );
  }

  const t = themeFor(snap.decision.signal);
  const inPosition = !!snap.position.option_symbol;
  const up = snap.futures_change >= 0;
  const live = snap.trade_mode === "live";

  const showResult = (r: OrderResult) => {
    if (r.ok) {
      if (r.side === "BUY") playBuySound();
      else if (r.side === "SELL") playExitSound();
    }
    setToast(`${r.side} ${r.option_symbol || ""} — ${r.message}${r.order_id ? ` (#${r.order_id})` : ""}`);
    setTimeout(() => setToast(null), 5000);
  };

  const onBuy = () => {
    const sym = snap.decision.recommended_option;
    if (!sym || inPosition) return;
    // Hold a hand order to the same gates the bot obeys, and say plainly which
    // one it breaks — paper as well as live, since the paper book is the
    // evidence the strategy is judged on.
    const warnings: string[] = [];
    const conf = snap.decision.confidence;
    const prem = snap.decision.current_premium;
    if (conf != null && conf < gates.minConfidence) {
      warnings.push(
        `Confidence is ${conf.toFixed(0)}% — the engine's gate is ${gates.minConfidence.toFixed(0)}%. The bot would not take this.`,
      );
    }
    if (prem != null && prem < gates.minPremium) {
      warnings.push(
        `Premium is ₹${prem} — below the ₹${gates.minPremium} floor. A contract this cheap moves in double-digit percentages on a tick, so no stop is meaningful.`,
      );
    }
    if (live || warnings.length) {
      setPending({ side: "BUY", symbol: sym, lots: 1, warnings });
      return;
    }
    buyOption(sym, 1, false, instrument).then((d) => showResult(d.result));
  };

  const onSell = () => {
    if (!inPosition) return;
    if (live) { setPending({ side: "SELL", symbol: snap.position.option_symbol || "", lots: snap.position.quantity_lots }); return; }
    sellPosition(false, instrument).then((d) => showResult(d.result));
  };

  const confirmOrder = () => {
    if (!pending) return;
    const p = pending;
    setPending(null);
    const req =
      p.side === "BUY"
        ? buyOption(p.symbol, p.lots, live, instrument)
        : sellPosition(live, instrument);
    req.then((d) => showResult(d.result));
  };

  const onSelectInstrument = (sym: string) => {
    // Switch only THIS tab. Warm the instrument on the server, then point this
    // tab's stream at it — other tabs are untouched.
    setInstrument(sym);
    if (typeof window !== "undefined") {
      // Persist in this tab's URL (survives refresh, stays per-tab) and remember
      // it as the default for the next new tab.
      const url = new URL(window.location.href);
      url.searchParams.set("instrument", sym);
      window.history.replaceState({}, "", url.toString());
      window.localStorage.setItem("qt_instrument", sym);
    }
    selectInstrument(sym).catch(() => {});
  };

  return (
    <div
      className="h-screen w-screen p-3 flex flex-col gap-3 overflow-hidden"
      style={{ ["--accent" as any]: t.accent, ["--accent-dim" as any]: t.accentDim }}
    >
      {view !== "decision" && <Alerts alerts={snap.alerts} />}

      {toast && (
        <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[60] card px-4 py-2 text-sm shadow-2xl">
          {toast}
        </div>
      )}

      {/* Visible twin of the audible alert, on every tab — a sound you missed
          while away from the desk otherwise leaves no trace. */}
      <AlertToast />

      {pending && (
        <div className="fixed inset-0 z-[70] grid place-items-center bg-black/70">
          <div className="card p-6 w-[420px] space-y-4">
            <div className="text-xs uppercase tracking-widest text-[#ff4d4d] font-bold">
              {live ? "Confirm LIVE order" : "This trade breaks the engine's gates"}
            </div>
            <div className="text-lg font-bold">
              {pending.side} {pending.symbol}
              {snap.decision.current_premium != null && (
                <span className="text-white/70 text-base font-semibold">{`  @ ₹${snap.decision.current_premium}`}</span>
              )}
            </div>
            {pending.warnings && pending.warnings.length > 0 && (
              <div className="rounded-lg border border-[#ff4d4d]/40 bg-[#ff4d4d]/10 p-3 space-y-2">
                {pending.warnings.map((w) => (
                  <div key={w} className="text-sm text-[#ff8080] leading-snug">
                    {w}
                  </div>
                ))}
                <div className="text-xs text-white/50">
                  Manual overrides are recorded in the Journal and scored against the
                  bot&apos;s trades.
                </div>
              </div>
            )}
            <div className="text-sm text-white/60">
              {live ? (
                <>
                  {pending.lots} lot(s) · this sends a <b>real {pending.side} order</b> to your
                  Angel One account at market price. This cannot be undone.
                </>
              ) : (
                <>
                  {pending.lots} lot(s) · paper order, no real money — but it still enters the
                  journal your strategy is judged on.
                </>
              )}
            </div>
            <div className="flex gap-3 justify-end pt-2">
              <button
                onClick={() => setPending(null)}
                className="px-4 py-1.5 rounded-lg text-sm font-semibold text-white/70 border border-white/15"
              >
                Cancel
              </button>
              <button
                onClick={confirmOrder}
                className="px-4 py-1.5 rounded-lg text-sm font-bold text-white"
                style={{ background: pending.side === "BUY" ? "#19e57f" : "#ff4d4d" }}
              >
                Confirm {pending.side}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Top bar */}
      <div className="card flex flex-wrap items-center justify-between gap-y-2 px-4 py-2">
        <div className="flex items-center gap-4">
          <div className="text-lg font-black tracking-tight">
            QUANTUM<span style={{ color: t.accent }}>TRADER</span>
          </div>
          <select
            value={instrument}
            onChange={(e) => onSelectInstrument(e.target.value)}
            title="Choose what to analyse (this tab only — other tabs are independent)"
            className="bg-white/5 border border-white/15 rounded-lg text-xs font-semibold px-2 py-1 outline-none disabled:opacity-40"
          >
            {snap.instruments.map((i) => (
              <option key={i.symbol} value={i.symbol} className="bg-[#0e1420]">
                {i.display}
              </option>
            ))}
          </select>
          <span className="text-xs text-white/40">{snap.underlying}</span>
          <span className="text-xl font-bold tabular-nums">{snap.futures_price?.toFixed(1)}</span>
          <span className={up ? "text-[#19e57f] text-sm" : "text-[#ff6b6b] text-sm"}>
            {up ? "+" : ""}{snap.futures_change?.toFixed(1)} ({snap.futures_change_pct?.toFixed(2)}%)
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {snap.session_note && (
            <span
              className="text-[11px] font-medium rounded-full px-3 py-1"
              style={{
                color: snap.market_open ? "#8b97a5" : "#f4c025",
                border: `1px solid ${snap.market_open ? "rgba(255,255,255,0.1)" : "rgba(244,192,37,0.5)"}`,
              }}
            >
              {snap.session_note}
            </span>
          )}
          <span className="pill text-xs font-semibold uppercase tracking-wider rounded-full px-3 py-1">
            {snap.market_status?.replace(/_/g, " ")}
          </span>
          <span
            className="text-[11px] font-bold uppercase tracking-wider rounded-full px-3 py-1"
            style={{
              color: live ? "#ff4d4d" : "#19e57f",
              border: `1px solid ${live ? "rgba(255,77,77,0.6)" : "rgba(25,229,127,0.5)"}`,
            }}
            title={live ? "Live orders go to your broker" : "Paper mode — no real orders"}
          >
            {live ? "● LIVE ORDERS" : "○ PAPER"}
          </span>
          {/* Sound controls, and sound itself, belong to the Signal dashboard —
              the screen you trade from. Hidden elsewhere, so the switches are not
              offered on screens that deliberately stay silent. */}
          {view === "signal" && <AlertBell compact />}
          <button
            onClick={() => setShowWatchlist(true)}
            className="text-xs font-semibold px-3 py-1.5 rounded-lg text-white/80 border border-white/15 hover:bg-white/5"
            title="Enable/disable instruments from the dashboard"
          >
            Watchlist
          </button>
          <button
            disabled={!snap.decision.recommended_option || inPosition}
            onClick={onBuy}
            className="text-sm font-bold px-4 py-1.5 rounded-lg text-black disabled:opacity-30"
            style={{ background: "#19e57f" }}
          >
            BUY
          </button>
          <button
            disabled={!inPosition}
            onClick={onSell}
            className="text-sm font-bold px-4 py-1.5 rounded-lg text-white disabled:opacity-30"
            style={{ background: "#ff4d4d" }}
          >
            SELL
          </button>
          <span className="flex items-center gap-1.5 text-xs text-white/45">
            <span className={`w-2 h-2 rounded-full ${connected ? "bg-[#19e57f]" : "bg-[#ff4d4d]"}`} />
            {connected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
      </div>

      {/* Tab bar, on its own row. It used to sit inside the top bar's right-hand
          cluster, where sixteen tabs could not fit: the group had a fixed content
          width and `overflow-hidden`, so on any screen narrower than the whole
          row the last tabs were clipped away with no way to reach them. Here it
          gets the full width and scrolls horizontally when it still does not fit,
          so every tab stays reachable at any window size. */}
      <div
        className="card card-scroll-x px-2 py-1 flex items-center gap-1 shrink-0 text-xs font-semibold"
        role="tablist"
        onWheel={(e) => {
          // A bar that only scrolls sideways: a mouse without a tilt wheel would
          // otherwise have no way to reach the last tabs, because the browser
          // does not turn a vertical wheel into horizontal scroll by itself.
          const el = e.currentTarget;
          if (el.scrollWidth <= el.clientWidth || e.deltaY === 0) return;
          // No preventDefault: React's wheel listener is passive, and the bar sits
          // outside the page scroller, so nothing else moves anyway.
          el.scrollLeft += e.deltaY;
        }}
      >
        {TABS.map((tb) => (
          <button
            key={tb.key}
            role="tab"
            aria-selected={view === tb.key}
            onClick={() => setView(tb.key)}
            title={tb.title}
            className={`shrink-0 whitespace-nowrap px-3 py-1.5 rounded-lg border ${
              view === tb.key
                ? "bg-white/10 text-white border-white/25"
                : "text-white/60 border-transparent hover:bg-white/5"
            }`}
          >
            {tb.label}
            {tb.badge && (
              <span className="ml-1 text-[9px] text-amber-300/80">{tb.badge}</span>
            )}
          </button>
        ))}
      </div>

      {/* One scroll container for every screen. Each board used to be responsible
          for its own scrolling inside a viewport-height page with
          `overflow-hidden`, and the ones whose root box is content-height (A+
          Paper, Evidence, CAS, Readiness, Account, Reports) therefore ran off the
          bottom of the window with nothing to scroll. */}
      <div className="flex-1 min-h-0 overflow-y-auto pr-1 flex flex-col gap-3">
      {view === "signal" ? (
        /* Quantum Signal — the single-page decision tool. Read-only: the frozen
           engine's call gated by a regime + ADX + expected-move filter so
           signals stop firing in chop. Paper/advisory only. */
        <QuantumSignal instrument={instrument} onInstrument={onSelectInstrument} />
      ) : view === "futsignal" ? (
        /* Futures Signal — the futures research board: risk in points, one point
           worth the lot size in rupees, mandatory stop. Research only: no paper
           or live order route, and it never replaces an option BUY. */
        <FuturesSignalBoard
          instrument={instrument}
          instruments={snap.instruments}
          onInstrument={onSelectInstrument}
        />
      ) : view === "account" ? (
        /* Paper-account overview: balance/equity, trades per day, P&L, and a
           downloadable monthly statement. Paper-only. */
        <AccountDashboard />
      ) : view === "core" ? (
        /* CORE A+ PAPER — the single candidate strategy: selective pullback +
           continuation, then CE vs PE vs FUTURES on whichever of them actually
           had a two-sided book. Paper only; the verdict on it belongs to the
           chronological holdout study, not to this screen. */
        <CoreAplusPaper />
      ) : view === "research" ? (
        /* Research & validation platform (data / replay / analytics / shadow /
           data-quality / Phase 7). Taller than any viewport; the page-level
           scroll container above is what makes the last panels reachable. */
        <ResearchDashboard instrument={instrument} />
      ) : view === "momentum" ? (
        /* Early Momentum board — a SEPARATE advisory engine with its own full
           trade plan (EARLY BUY CE/PE · entry/stop/targets/exit). Never changes
           the frozen Decision engine. Uses the shared instrument selector. */
        <MomentumBoard snap={snap} />
      ) : view === "flow" ? (
        /* Candle-Flow board — a SEPARATE advisory engine that rides the 1-min
           candle (BUY green · HOLD · EXIT red · SWITCH CE↔PE). Never changes the
           frozen Decision engine. Uses the shared instrument selector. */
        <FlowBoard snap={snap} />
      ) : view === "spreads" ? (
        /* Premium-SELLING board — defined-risk credit spreads, PAPER ONLY. A
           SEPARATE advisory engine with no order path; never touches the frozen
           Decision engine. */
        <SpreadBoard snap={snap} />
      ) : view === "futures" ? (
        /* FUTURES paper bot — a SEPARATE self-driving tool with its OWN capital
           pot and point-based risk. Shares no sizing/stop/exit logic with the
           option engine and has NO live order route. */
        <FuturesBoard snap={snap} />
      ) : view === "journal" ? (
        /* Journal — the paper/live trades that were actually opened and closed. */
        <JournalBoard />
      ) : view === "reports" ? (
        /* Reports — the BUY calls the board made, in full, with a date range and a
           CSV download. Read-only over the append-only journal; no order path. */
        <ReportsBoard />
      ) : view === "oneaday" ? (
        /* One a Day — the record of what a one-trade-a-day schedule would have
           produced, graded by whether the day's signal reached target. Read-only
           over the journal and outcome ledgers: it caps nothing, ranks nothing
           and routes nothing, and every R in it is gross of costs. */
        <DailyBestBoard />
      ) : view === "aplus" ? (
        /* A+ Paper — the research ranking and the costed paper book behind it.
           PAPER ONLY: the score is not the production Signal Score, no row on
           this screen has an order path, and an empty board is a valid day. */
        <AplusPaperBoard />
      ) : view === "evidence" ? (
        /* Vehicle Evidence — the measured option book for EVERY eligible
           candidate, both sides at the same timestamp. Read-only research; A+ is
           a label over this feed, not the filter that produced it. */
        <VehicleEvidencePanel />
      ) : view === "cas" ? (
        /* CAS — the closing auction window. Its own screen on purpose: a
           different mechanism, a few weeks of evidence, and paper only. Nothing
           on it can place, size or promote anything. */
        <CasBoard />
      ) : view === "readiness" ? (
        /* Production Readiness — Option A+, Futures A+ and CAS in one shape,
           plus the T1 calibration verdict and the futures paper book behind it.
           Read-only: PRODUCTION_CANDIDATE means a human should review, never that
           anything has been activated, and no order path exists below it. */
        <ProductionReadiness />
      ) : (
        /* Clean, decision-first dashboard: should I trade, which option, entry,
           stop, targets, hold time, risk, opportunity, why. */
        <ProDashboard snap={snap} onCalculate={() => setShowSizing(true)} />
      )}
      </div>

      {showBacktest && (
        <BacktestModal instrument={instrument} onClose={() => setShowBacktest(false)} />
      )}
      {showPerformance && (
        <PerformanceModal instrument={instrument} onClose={() => setShowPerformance(false)} />
      )}
      {showSizing && (
        <PositionSizingModal snap={snap} onClose={() => setShowSizing(false)} />
      )}
      {showWatchlist && (
        <WatchlistModal onClose={() => setShowWatchlist(false)} />
      )}
    </div>
  );
}
