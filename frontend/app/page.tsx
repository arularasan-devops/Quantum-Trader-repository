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
import RiskCard from "@/components/RiskCard";
import LearningCard from "@/components/LearningCard";
import BacktestModal from "@/components/BacktestModal";
import PerformanceModal from "@/components/PerformanceModal";
import AiEngine from "@/components/AiEngine";
import DecisionHero from "@/components/DecisionHero";
import MiniCharts from "@/components/MiniCharts";
import PositionCard from "@/components/PositionCard";
import EntryZoneCard from "@/components/EntryZoneCard";
import TargetsCard from "@/components/TargetsCard";
import StopLossCard from "@/components/StopLossCard";
import AiStatusCard from "@/components/AiStatusCard";
import DecisionStack from "@/components/DecisionStack";
import ReasonsCard from "@/components/ReasonsCard";
import TradeMeterCard from "@/components/TradeMeterCard";
import NewsFeed from "@/components/NewsFeed";
import MarketContext from "@/components/MarketContext";
import ProDashboard from "@/components/ProDashboard";
import FuturesBoard from "@/components/FuturesBoard";
import OptionsBoard from "@/components/OptionsBoard";
import JournalBoard from "@/components/JournalBoard";
import ReportsBoard from "@/components/ReportsBoard";
import PaperBooksBoard from "@/components/PaperBooksBoard";
import CommandCenter from "@/components/CommandCenter";
import QuantumSignal from "@/components/QuantumSignal";
import TopMovers from "@/components/TopMovers";
import ResearchDashboard from "@/components/ResearchDashboard";
import AccountDashboard from "@/components/AccountDashboard";
import PositionSizingModal from "@/components/PositionSizingModal";
import WatchlistModal from "@/components/WatchlistModal";

type PendingOrder = {
  side: "BUY" | "SELL";
  symbol: string;
  lots: number;
  // Gates this manual order would break. The bot refuses these entries; a hand
  // order used to bypass them silently, which is how a 34%-confidence buy and a
  // ₹7.2 premium buy reached the journal.
  warnings?: string[];
};

// Server registry key: older builds saved the scrip name ("BAJAJ-AUTO", "M&M"),
// which matched no dropdown option, so the select fell back to Crude Oil.
const instrumentKey = (s: string) => s.toUpperCase().replace(/[^A-Z0-9]/g, "");

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
  const [view, setView] = useState<"signal" | "command" | "decision" | "futures" | "options" | "journal" | "books" | "reports" | "movers" | "analysis" | "research" | "account">("signal");

  // Restore this tab's instrument on load/refresh: prefer the ?instrument= URL
  // param (so each tab keeps its own selection and survives a refresh), then the
  // last-used value in localStorage. Runs on mount only — set here (not in the
  // useState initializer) to avoid an SSR/hydration mismatch.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const fromUrl = new URLSearchParams(window.location.search).get("instrument");
    // Older builds saved the scrip name ("BAJAJ-AUTO", "M&M"); the server keys
    // those as BAJAJAUTO / MM.
    const saved = instrumentKey(fromUrl || window.localStorage.getItem("qt_instrument") || "");
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
  const selSymbol = snap.position.option_symbol || snap.decision.recommended_option;
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

  const onSelectInstrument = (picked: string) => {
    const sym = instrumentKey(picked);
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
      className="h-screen w-full p-3 flex flex-col gap-3 overflow-hidden"
      style={{ "--accent": t.accent, "--accent-dim": t.accentDim } as React.CSSProperties}
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
              {(() => {
                // A SELL closes the HELD contract: show its mark, not the
                // engine's current pick (a different strike).
                const px = pending.side === "SELL" ? snap.position.current_premium : snap.decision.current_premium;
                return px != null && (
                  <span className="text-white/70 text-base font-semibold">{`  @ ₹${px}`}</span>
                );
              })()}
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
      <div className="card shrink-0 flex flex-wrap items-center justify-between gap-y-2 px-4 py-2">
        <div className="flex items-center gap-4">
          <div className="text-lg font-black tracking-tight">
            QUANTUM<span style={{ color: t.accent }}>TRADER</span>
          </div>
          <select
            value={instrumentKey(instrument)}
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
            title={inPosition ? "Already in a position on this instrument" : !snap.decision.recommended_option ? "No BUY recommendation on this instrument right now" : "Paper BUY the recommended option"}
            className="text-sm font-bold px-4 py-1.5 rounded-lg text-black disabled:opacity-30"
            style={{ background: "#19e57f" }}
          >
            BUY
          </button>
          <button
            disabled={!inPosition}
            onClick={onSell}
            title={inPosition ? "Paper SELL the open position" : "No open position on this instrument"}
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

  <div className="card shrink-0 flex w-full overflow-x-auto text-xs font-semibold p-0">
        <button
          onClick={() => setView("signal")}
          className={view === "signal" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
          title="Quantum Signal — the single-page decision tool (regime/volatility-gated BUY/WAIT/HOLD/EXIT)"
        >
          Signal
        </button>
        <button
          onClick={() => setView("decision")}
          className={view === "decision" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Decision
        </button>
        <button
          onClick={() => setView("futures")}
          className={view === "futures" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Futures <span className="text-[9px] text-amber-300/80">PAPER</span>
        </button>
        <button
          onClick={() => setView("options")}
          className={view === "options" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Options <span className="text-[9px] text-amber-300/80">PAPER</span>
        </button>
        <button
          onClick={() => setView("journal")}
          className={view === "journal" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Journal
        </button>
        <button
          onClick={() => setView("books")}
          className={view === "books" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Books <span className="text-[9px] text-amber-300/80">PAPER</span>
        </button>
        <button
          onClick={() => setView("reports")}
          className={view === "reports" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Reports
        </button>
        <button
          onClick={() => setView("research")}
          className={view === "research" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Research
        </button>
        <button
          onClick={() => setView("account")}
          className={view === "account" ? "flex-1 whitespace-nowrap px-3 py-2 bg-white/10 text-white" : "flex-1 whitespace-nowrap px-3 py-2 text-white/60 hover:bg-white/5"}
        >
          Account
        </button>
      </div>

      {view === "signal" ? (
        /* Quantum Signal — the single-page decision tool. Read-only: the frozen
           engine's call gated by a regime + ADX + expected-move filter so
           signals stop firing in chop. Paper/advisory only. */
        <div className="flex-1 min-h-0 overflow-y-auto">
          <QuantumSignal instrument={instrument} onInstrument={onSelectInstrument} />
        </div>
      ) : view === "command" ? (
        /* AI Command Center (V4) — a read-only glanceable page that aggregates
           the REAL signals the engines already compute (regime, VIX/breadth,
           ranked opportunities, lifecycle, position health, exit). Feature-
           flagged (QT_AI_COMMAND_CENTER); shows a disabled card when off. Never
           changes the frozen engine and never places an order. */
        <CommandCenter
          snap={snap}
          onPick={(sym) => {
            if (typeof window !== "undefined") {
              window.open(`/?instrument=${encodeURIComponent(sym)}`, "_blank", "noopener");
            }
          }}
        />
      ) : view === "account" ? (
        /* Paper-account overview: balance/equity, trades per day, P&L, and a
           downloadable monthly statement. Paper-only. */
        <AccountDashboard />
      ) : view === "research" ? (
        /* Research & validation platform (data / replay / analytics / shadow /
           data-quality / Phase 7). Given the page-level scroll container here so
           the last panels are reachable: the board is taller than any viewport,
           and its own flex-1 box could only ever show one screenful. */
        <div className="flex-1 min-h-0 overflow-y-auto pr-1">
          <ResearchDashboard instrument={instrument} />
        </div>
      ) : view === "movers" ? (
        /* Today's auto-picked top F&O movers with details. Clicking a row opens
           that instrument in a new Decision tab. */
        <TopMovers
          onPick={(sym) => {
            if (typeof window !== "undefined") {
              window.open(`/?instrument=${encodeURIComponent(sym)}`, "_blank");
            }
          }}
        />
      ) : view === "futures" ? (
        /* FUTURES paper bot — a SEPARATE self-driving tool with its OWN capital
           pot and point-based risk. Shares no sizing/stop/exit logic with the
           option engine and has NO live order route. */
        <FuturesBoard snap={snap} />
      ) : view === "options" ? (
        <OptionsBoard snap={snap} />
      ) : view === "journal" ? (
        /* Complete trade journal — the full persisted record of every closed
           paper/live trade with reason, confidence, P&L and analyst note. */
        <>
          <JournalBoard />
          
        </>
      ) : view === "books" ? (
        /* Six parallel PAPER books (5L/10L/15L x FULL/RISK) on the same signals. */
        <PaperBooksBoard />
      ) : view === "reports" ? (
        /* Every BUY signal persisted in journal v2, executed or skipped, with reason. */
        <ReportsBoard />
      ) : view === "decision" ? (
        /* Clean, decision-first dashboard: should I trade, which option, entry,
           stop, targets, hold time, risk, opportunity, why. */
        <ProDashboard snap={snap} onCalculate={() => setShowSizing(true)} />
      ) : (
        <>
          {/* Row 1 — hero */}
          <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.3" }}>
            <div className="col-span-3 min-h-0">
              <AiEngine ind={snap.indicators} signal={snap.decision.signal} votes={snap.score_breakdown.length} />
            </div>
            <div className="col-span-6 min-h-0">
              <DecisionHero decision={snap.decision} />
            </div>
            <div className="col-span-3 min-h-0">
              <MiniCharts candles={snap.selected_option_candles} decision={snap.decision} symbol={selSymbol} />
            </div>
          </div>

          {/* Row 2 — trade plan */}
          <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.05" }}>
            <div className="col-span-3 min-h-0"><PositionCard position={snap.position} expectedHoldMinutes={snap.decision.expected_holding_minutes} averageNote={snap.decision.average_ok ? snap.decision.average_note : null} /></div>
            <div className="col-span-4 min-h-0"><EntryZoneCard decision={snap.decision} /></div>
            <div className="col-span-2 min-h-0"><TargetsCard decision={snap.decision} /></div>
            <div className="col-span-3 min-h-0"><StopLossCard decision={snap.decision} position={snap.position} /></div>
          </div>

          {/* Row 3 — decision evidence stack + why + scores */}
          <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "1.25" }}>
            <div className="col-span-5 min-h-0"><DecisionStack ind={snap.indicators} breakdown={snap.score_breakdown} /></div>
            <div className="col-span-4 min-h-0"><ReasonsCard decision={snap.decision} /></div>
            <div className="col-span-3 min-h-0 flex flex-col gap-3">
              <div className="flex-1 min-h-0"><TradeMeterCard decision={snap.decision} /></div>
              <div className="flex-1 min-h-0"><AiStatusCard ind={snap.indicators} decision={snap.decision} /></div>
            </div>
          </div>

          {/* Row 4 — risk, learning, news & market context */}
          <div className="grid grid-cols-12 gap-3 min-h-0" style={{ flex: "0.95" }}>
            <div className="col-span-3 min-h-0"><RiskCard risk={snap.risk} /></div>
            <div className="col-span-2 min-h-0"><LearningCard stats={snap.learning} /></div>
            <div className="col-span-4 min-h-0">
              <NewsFeed news={snap.news} sentiment={snap.news_sentiment} score={snap.news_score} />
            </div>
            <div className="col-span-3 min-h-0"><MarketContext instrument={instrument} /></div>
          </div>
        </>
      )}

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
