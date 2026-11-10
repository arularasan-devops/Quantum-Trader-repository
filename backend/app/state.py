"""Application state and the per-tick analysis loop.

Owns the market-data provider, the current position, the alert stream and the
trade journal, and assembles the full :class:`Snapshot` broadcast to clients
each tick. Deliberately dependency-light (in-memory) so it runs on a laptop;
swap the journal store for PostgreSQL and the broadcast for Redis pub/sub when
scaling out (see DEPLOYMENT.md).
"""
from __future__ import annotations

import logging
import contextlib
import threading
import time
from datetime import datetime, timezone

from app import notify
from app import storage
from app.journal import hooks as journal_hooks
from app.analysis import analyst
from app.analysis import events as events_mod
from app.analysis import exec_funnel
from app.analysis import fill_book
from app.analysis import futures_feed_audit
from app.analysis import futures_outcomes
from app.analysis import intelligence as intel
from app.analysis import news as news_mod
from app.analysis import option_costs
from app.analysis import opportunity
from app.analysis import performance as perf
from app.analysis import rss_news
from app.analysis import (
    futures_signal,
    missed,
    shadow_book,
    signal_journal,
    signal_tracker,
    signal_visibility,
)
from app.analysis import plan_validity
from app.analysis import structure as st
from app.config import settings
from app.engine import account_risk
from app.engine import backtest as bt
from app.engine import decision as eng
from app.engine import learning as learn
from app.engine import paper_books
from app.engine import paper_positions
from app.engine import recovery as rec_eng
from app.engine import risk_v2
from app.engine import weights
from app.engine.risk import RiskManager
from app.execution import (
    credit_spread,
    early_early,
    early_early_tracker,
    early_momentum,
    execution_validator,
    flow,
    flow_tracker,
    futures_paper,
    manual_guard,
    momentum_outcomes,
    scalp,
    scalp_tracker,
    signal_delay,
    zero_to_hero,
)
from app.market import instruments as _instruments
from app.market.instruments import DEFAULT_INSTRUMENT, REGISTRY, get_spec, resolve_key, universe_specs
from app.market import tiers
from app.market.provider import MarketDataProvider, build_provider
from app.execution import live_control
from app.execution import order_ledger
from app.market.tick_quality import feed_quality
from app.models import (
    Alert,
    AutoTradeEntry,
    AutoTradeState,
    BacktestResult,
    Candle,
    Decision,
    EventGuard,
    FeedHealth,
    FuturesSignalCard,
    IndicatorSnapshot,
    Intelligence,
    InstrumentOption,
    MarketStatus,
    OptionQuote,
    OptionType,
    OrderResult,
    Position,
    RecoveryAnalysis,
    Signal,
    Snapshot,
    ZeroToHero,
)
from app.research import capture as research_capture
from app.research.pairs import capture as pair_capture
from app.research.phase17 import service as phase17
from app.research.phase18 import service as phase18
from app.research.phase19 import service as phase19
from app.research.phase22 import service as phase22
from app.research.phase23 import service as phase23


# How many trailing bars are re-offered to the history store on every pass. 60
# one-minute bars covers any realistic gap between two scans of the same
# instrument, including a scan cycle that has fallen well behind its interval.
_CANDLE_TAIL = 60

# Trailing bars re-offered to the frozen pair capture. Same reasoning as
# _CANDLE_TAIL, kept separate so changing one never silently changes the other.
_PAIR_TAIL = 30


def _mark_stage(instrument: str, stage: str) -> None:
    """Stamp one stage of the tick->decision latency chain (observability only).

    Keyed by the provider's scrip root so it lines up with the ticks recorded by
    the feed itself. Never raises into the tick path.
    """
    try:
        feed_quality.mark_stage(get_spec(instrument).symbol, stage)
    except Exception:
        pass


_log = logging.getLogger(__name__)

class _UnavailableProvider(MarketDataProvider):
    """Stand-in used when a real provider cannot be constructed for an
    instrument (e.g. the live Angel One feed can't resolve a current NIFTY /
    SENSEX contract, or is being rate-limited).

    It returns empty data — never fabricated prices — so the decision engine
    falls back to its neutral "waiting for market data" WAIT and the dashboard
    can show *why* the instrument is blank instead of silently crashing the
    snapshot. ``AppState.tick`` re-attempts the real build every tick, so the
    instrument self-heals the moment the feed becomes available.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def feed_health(self) -> dict:
        return {"mode": "unavailable", "live": False, "note": self.reason}

    def feed_mode(self) -> str:
        return "unavailable"

    def step(self) -> None:
        return None

    def futures_price(self) -> float:
        return 0.0

    def futures_candles(self, limit: int = 240):
        return []

    def option_chain(self):
        return []

    def option_candles(self, symbol: str, limit: int = 240):
        return []

    def news(self):
        return []


def classify_feed(health: FeedHealth, provider: MarketDataProvider) -> FeedHealth:
    """Stamp provider/source/state onto a provider's raw health reading.

    Only the simulator itself may be labelled SIMULATED; a real provider that
    reports "simulated" (e.g. Groww with no session) is UNAVAILABLE, never a
    silent fallback to synthetic prices.
    """
    from app.market.simulated import SimulatedProvider

    health.provider = (settings.data_provider or "").lower()
    health.checked_at = time.time()
    if isinstance(provider, SimulatedProvider):
        health.source, health.state = "SIMULATED", "SIMULATED"
        return health
    if isinstance(provider, _UnavailableProvider) or health.mode in ("simulated", "unavailable"):
        health.source, health.state = "UNAVAILABLE", "DISCONNECTED"
        health.live = False
        return health
    health.source = "LIVE"
    ages = [a for a in (health.underlying_age_sec, health.option_age_sec)
            if isinstance(a, (int, float))]
    if not ages or max(ages) > float(health.stale_after_sec) * 4:
        health.state = "DISCONNECTED"
    elif health.live and max(ages) <= float(health.stale_after_sec):
        health.state = "CONNECTED"
    else:
        health.state = "DEGRADED"
    if health.state == "DEGRADED" and not health.ws_connected and health.mode != "rest":
        health.state = "RECONNECTING"
    return health


class AppState:
    def __init__(self, instrument: str | None = None) -> None:
        # Each AppState is PINNED to one instrument. The server keeps one per
        # instrument (see StateRegistry) so two browser tabs watching different
        # instruments (e.g. Crude in one, Nifty in another) stay independent
        # instead of overwriting a single shared "active instrument".
        chosen = resolve_key(instrument or settings.angel_symbol_root or DEFAULT_INSTRUMENT)
        self._instrument = chosen if chosen in REGISTRY else DEFAULT_INSTRUMENT
        self._providers: dict[str, MarketDataProvider] = {}
        self.provider = self._provider_for(self._instrument)
        self.position = Position()
        self.alerts: list[Alert] = []
        self.journal: list[dict] = []
        self.risk = RiskManager(self._instrument)
        self.today_profit = 0.0
        self.today_loss = 0.0
        self._last_signal: Signal | None = None
        self._entry_time: int | None = None
        self._last_breakdown: list = []
        self._entry_factors: list[str] = []
        # Validation Mode: the latest decision context, captured at entry and
        # merged into the trade journal at exit for future real-data analysis.
        self._last_decision: Decision | None = None
        self._last_status: MarketStatus | None = None
        self._last_indicators: IndicatorSnapshot | None = None
        self._entry_context: dict = {}
        # Top-of-book at entry and the latest one seen while the leg is open, so
        # the closed row can carry a real spread instead of an assumed one.
        self._entry_book: dict | None = None
        self._exit_book: dict | None = None
        # --- Early Momentum leg tracking (advisory) ---
        # Anchors the current directional leg (swing low/high) and remembers the
        # premium at which the Early Momentum engine FIRST flagged it, so the
        # Signal Delay Analyzer can quantify how much earlier that entry was.
        self._em_anchor: float | None = None
        self._em_early_price: float | None = None
        self._em_early_ctime: int | None = None
        self._em_last_stage: int | None = None
        # Open Early-Momentum outcome record for this instrument's current
        # EARLY-BUY leg (how far the premium reaches). Analysis-only.
        self._em_outcome: dict | None = None
        # Open missed-opportunity record for this instrument (see analysis/missed).
        self._missed: dict | None = None
        # Open Early-Early (Stage 2.5) tracker record — premium travel +
        # frozen-agreement. Analysis-only; SEPARATE from Early Momentum.
        self._ee_outcome: dict | None = None
        # Open Quick Scalp tracker record — fixed-target premium travel +
        # breakeven/time-stop. Analysis-only; SEPARATE from the other engines.
        self._scalp_outcome: dict | None = None
        # Open Flow (Candle-Flow) record — the leg currently being ridden.
        # Analysis-only; SEPARATE from every other engine.
        self._flow_state: dict | None = None
        # --- signal stability state ---
        self._committed: Decision | None = None
        self._committed_at: float = 0.0
        self._pending_signal: Signal | None = None
        self._pending_since: float = 0.0
        # When the committed levels were last computed, and which version of
        # them is on screen. The signal is held across ticks; the plan is not
        # allowed to silently outlive the premium it was built from.
        self._plan_at: float = 0.0
        self._futures_signal: FuturesSignalCard | None = None
        self._plan_version: int = 1
        # dedup key so one suppressed plan writes ONE funnel row, not one a tick
        self._plan_block_key: str | None = None
        self._alert_seen: dict[str, int] = {}
        # --- shadow mode (Phase 2): observe the frozen engine live, place NO
        # orders, and score each recommendation against the actual later move ---
        self._shadow_last_bucket: int = 0
        self._price_hist: list[tuple[int, float]] = []
        # dedup key so a high-confidence BUY fires ONE WhatsApp alert per episode
        self._wa_last_key: str | None = None
        # --- paper auto-trader (simulated fills only; never live) ---
        self._auto_last_key: str | None = None   # one auto-entry per signal episode
        # when the episode key was consumed by a successful auto-buy; a closed
        # trade re-arms the same episode once the re-entry cooldown has passed
        self._auto_key_traded_ts: float | None = None
        self._auto_last_exit_was_stop = False
        self._auto_target1: float | None = None  # T1 captured at auto-entry
        self._auto_target2: float | None = None  # T2 captured at auto-entry
        self._auto_target3: float | None = None  # T3 captured at auto-entry
        self._auto_stop: float | None = None      # stop captured at auto-entry
        # highest target FLOOR reached this trade (ratchets up): 0=none,1=T1,2=T2
        self._auto_tgt_floor: float | None = None
        self._auto_scaled_out: bool = False       # part already booked at T1
        self._auto_pre_t1_halved: bool = False    # half booked at the pre-T1 trail
        # running high premium this trade (for the pre-T1 peak-giveback trail)
        self._auto_peak: float | None = None
        self._auto_entry_prem: float | None = None  # actual entry fill this trade
        self._auto_entry_active: bool = False      # current position was auto-opened
        self._auto_entry_strike: float | None = None
        self._auto_entry_conf: float | None = None  # signal confidence at entry
        self._auto_entry_lots: int = 0             # lots bought this trade
        self._pending_exit_reason: str | None = None  # tag for the next sell()'s journal row
        self._auto_last_action: str | None = None
        self._auto_last_action_ts: int | None = None
        # when this instrument last closed a position (drives the re-entry cooldown)
        self._auto_last_exit_ts: int | None = None
        # stale-feed guard: the last premium seen per option and how many
        # consecutive ticks it has not moved at all
        self._prem_watch: tuple[str, float] | None = None
        self._prem_frozen_ticks: int = 0
        # --- zero-to-hero expiry sleeve (speculative, isolated daily budget) ---
        self._z2h_spent_day: float = 0.0          # sleeve money committed today
        self._z2h_spent_date: str | None = None   # IST date the spend applies to
        self._z2h_last_key: str | None = None      # one punt per (symbol) episode
        self._z2h_is_sleeve = False                # current position is a z2h punt
        # last bar timestamp recorded into the research store (live auto-capture)
        self._last_capture_ts: int | None = None
        self._capture_error: str | None = None
        # Live broker order state (ack != fill), mirrored to the crash-safe order
        # ledger. Restored after a restart only as an UNVERIFIED claim.
        self._pending_order: dict | None = None
        self._pending_exit: dict | None = None
        self._ledger_position: dict | None = None
        self._order_recovery: dict | None = None
        self._persist_error: str | None = None
        self._last_order_poll = 0.0
        self._last_broker_pos_check = 0.0
        self._exit_retry_at = 0.0
        # Resting exchange STOPLOSS_LIMIT SELL at the locked floor (live only).
        self._exch_stop: dict | None = None
        self._exch_stop_retry_at = 0.0
        self._exch_stop_breach_at: float | None = None
        self._broker_flat_seen = 0
        self._restore_live_ledger()
        self._paper_pos_saved_at = 0.0
        self._paper_pos_saved = False
        self._restore_paper_position()

    def _provider_for(self, instrument: str) -> MarketDataProvider:
        cached = self._providers.get(instrument)
        if cached is not None:
            return cached
        try:
            provider = build_provider(settings.data_provider, instrument)
        except Exception as exc:
            # Do NOT cache the failure: return a stand-in that yields empty data
            # (never fabricated prices) and carries the reason, so the dashboard
            # explains why this instrument is blank instead of crashing the
            # snapshot. tick() re-attempts the real build each tick, so the
            # instrument recovers automatically once the feed is available.
            return _UnavailableProvider(f"{instrument} unavailable — {exc}")
        self._providers[instrument] = provider
        return provider

    @staticmethod
    def _atm_pair(chain, price) -> dict:
        """Nearest-strike CE and PE quotes (symbol + premium) for the panel."""
        out = {"atm_ce_symbol": None, "atm_ce_premium": None,
               "atm_pe_symbol": None, "atm_pe_premium": None}
        try:
            strikes = {q.strike for q in chain or []}
            if not strikes or not price:
                return out
            k = min(strikes, key=lambda s: abs(s - float(price)))
            for q in chain:
                if q.strike != k or not q.premium:
                    continue
                side = "ce" if q.option_type == OptionType.CALL else "pe"
                out[f"atm_{side}_symbol"] = q.symbol
                out[f"atm_{side}_premium"] = float(q.premium)
        except Exception:
            pass
        return out

    def _own_bid(self) -> float | None:
        """Best bid of the open option (None without a two-sided quote)."""
        sym = self.position.option_symbol
        if not sym:
            return None
        try:
            q = next((x for x in self.provider.option_chain() if x.symbol == sym), None)
        except Exception:
            return None
        if q is None or not q.bid or not q.ask or not (0 < q.bid <= q.ask):
            return None
        return float(q.bid)

    def _liq_pnl(self) -> float:
        """Open P&L valued at the bid (what closing now pays) in PAPER."""
        p = self.position
        pnl = float(p.net_pnl or 0.0)
        if self.is_live or not settings.auto_trade_exit_on_bid or not p.option_symbol:
            return pnl
        bid, mark = self._own_bid(), p.current_premium
        if bid is not None and mark is not None and bid < mark:
            pnl -= (mark - bid) * get_spec(self._instrument).lot_size * p.quantity_lots
        return pnl

    def _feed_ready(self) -> bool:
        """Whether the prices behind the current plan are current enough to act on.

        Read from the same freshness the board shows. A REAL feed that has never
        reported an age is treated as not ready, because "unknown age" is what a
        warming feed looks like — and the two entries taken in that state had no
        market data behind them at all. A simulated feed has no age to report and
        cannot be stale, since it generates the price it is asked for.
        """
        health = self._feed_health()
        ages = [a for a in (health.underlying_age_sec, health.option_age_sec)
                if isinstance(a, (int, float))]
        if health.source == "SIMULATED":
            return True
        if health.source != "LIVE" or not ages:
            return False
        return max(ages) <= float(health.stale_after_sec)

    def _feed_health(self) -> FeedHealth:
        """Freshness of the prices behind this snapshot.

        Best-effort: a provider that cannot report health must not break the
        snapshot, so any failure degrades to the plain feed mode.
        """
        try:
            health = FeedHealth(**self.provider.feed_health())
        except Exception:
            try:
                health = FeedHealth(mode=self.provider.feed_mode())
            except Exception:
                health = FeedHealth(mode="unavailable", note="provider health unreadable")
        return classify_feed(health, self.provider)

    # ---------- instrument selection (analysis only — never auto-trades) ----------
    @property
    def instrument(self) -> str:
        return self._instrument

    def set_instrument(self, instrument: str) -> str:
        name = resolve_key(instrument)
        if name not in REGISTRY:
            raise ValueError(f"Unknown instrument {instrument}")
        if self.position.option_symbol:
            raise ValueError("Close the open position before switching instrument.")
        if name != self._instrument:
            # Build/resolve the provider FIRST; only switch over once it
            # succeeds, so a failed build (e.g. no current contract) leaves the
            # current instrument intact instead of a half-switched state.
            provider = self._provider_for(name)
            self._instrument = name
            self.provider = provider
            # reset the stabilised signal so the new instrument starts clean
            self._committed = None
            self._pending_signal = None
            self._plan_at = 0.0
            self._plan_version = 1
            self._plan_block_key = None
        return name

    # ---------- trading properties ----------
    @property
    def last_decision(self) -> Decision | None:
        """The most recent decision for this instrument, or None before the first
        tick. Read-only view for diagnostics — never recomputes."""
        return self._last_decision

    @property
    def is_live(self) -> bool:
        return settings.trade_mode.lower() == "live" and self.provider.supports_trading()

    def backtest(self) -> BacktestResult:
        spec = get_spec(self._instrument)
        # prefer REAL captured history; fall back to the provider's series until
        # enough live ticks have been recorded.
        stored = storage.store.candles(self._instrument, 600) if settings.store_history else []
        candles = stored if len(stored) >= 120 else self.provider.futures_candles(600)
        return bt.run(candles, spec)

    def learning_summary(self) -> list[dict]:
        return weights.store.summary()

    def performance_report(self) -> dict:
        # A "real feed" is any configured broker provider (not the simulated
        # demo). Only then are the recorded trades treated as real performance.
        is_real = settings.data_provider.lower() not in ("simulated", "sim", "demo")
        return perf.report(
            self.journal,
            is_real_feed=is_real,
            min_trades=settings.performance_min_trades,
        )

    def _capture_context(self, entry_premium: float) -> dict:
        """Snapshot the full decision context at entry for Validation Mode.

        Everything future real-data performance analysis will need — the trade
        score, opportunity/risk, reasons, regime and the premium/OI/volume/news
        read that justified the entry — is frozen here at fill time.
        """
        d = self._last_decision
        ind = self._last_indicators
        return {
            "entry_time": int(time.time()),
            "entry_premium": entry_premium,
            "instrument": self._instrument,
            "option": d.recommended_option if d else None,
            "option_type": d.option_type.value if d and d.option_type else None,
            "signal": d.signal.value if d else None,
            "trade_score": d.trade_score if d else None,
            "opportunity": d.opportunity_label if d else None,
            "risk": d.risk_meter if d else None,
            "confidence": d.confidence if d else None,
            "reasons": list(d.reasons) if d else [],
            "market_regime": self._last_status.value if self._last_status else None,
            "premium_behaviour": ind.premium_state if ind else None,
            "premium_quality": d.premium_quality if d else None,
            "oi_state": ind.oi_state if ind else None,
            "volume_level": ind.volume_level if ind else None,
            "news_material": ind.news_material if ind else None,
            "htf_trend": d.htf_trend if d else None,
            # extra context used by research rule-impact / coaching analytics
            "entry_trigger": d.entry_trigger if d else None,
            "premium_health": d.premium_health if d else None,
            # Phase 3.6 Execution Intelligence, frozen at entry for later AI
            # training/analytics. Populated only when the layer was enabled;
            # otherwise null (honest — never fabricated).
            "execution": self._capture_execution_context(entry_premium, d),
            # Early Momentum / Signal Delay, frozen at entry for later analysis of
            # whether earlier entries actually improve outcomes. Null when the
            # advisory engine was disabled (honest — never fabricated).
            "early_momentum": self._capture_early_context(d),
        }

    @staticmethod
    def _capture_early_context(d: Decision | None) -> dict | None:
        """Freeze the Early Momentum advisory + Signal Delay at entry so a later
        (data-gated) report can compare whether the EARLY signal would have
        improved the confirmation entry. None when the engine was disabled."""
        em = d.early_momentum if d else None
        sd = d.signal_delay if d else None
        if (em is None or not em.enabled) and sd is None:
            return None
        return {
            "early_active": em.active if em else None,
            "early_stage_num": em.stage_num if em else None,
            "early_stage": em.stage if em else None,
            "early_regime": em.regime if em else None,
            "early_side": em.side.value if em and em.side else None,
            "early_score": em.score if em else None,
            "move_start_price": sd.move_start_price if sd else None,
            "buy_price": sd.buy_price if sd else None,
            "peak_price": sd.peak_price if sd else None,
            "missed_move_pct": sd.missed_move_pct if sd else None,
            "remaining_move_pct": sd.remaining_move_pct if sd else None,
            "late_entry": sd.late if sd else None,
            "confirmation_entry_premium": sd.confirmation_entry_premium if sd else None,
            "early_entry_premium": sd.early_entry_premium if sd else None,
            "premium_diff": sd.premium_diff if sd else None,
            "premium_diff_pct": sd.premium_diff_pct if sd else None,
            "candles_between": sd.candles_between if sd else None,
            "early_would_help": sd.early_would_help if sd else None,
        }

    @staticmethod
    def _capture_execution_context(entry_premium: float, d: Decision | None) -> dict | None:
        """Freeze the Execution Intelligence decision at entry for learning.

        Stores suggested vs actual entry/strike, survival score and grade so a
        later (data-gated) report can compare execution quality to outcome. None
        when the advisory layer was disabled — no synthetic values."""
        ei = d.execution_intelligence if d else None
        if ei is None or not ei.enabled:
            return None
        return {
            "recommended_action": ei.recommended_action,
            "grade": ei.grade,
            "entry_timing": ei.entry_timing,
            "actual_entry": entry_premium,
            "suggested_better_entry": ei.entry.better_entry if ei.entry else None,
            "pullback_probability": ei.pullback_probability,
            "actual_strike": d.recommended_option if d else None,
            "suggested_strike": ei.strike.recommended_symbol if ei.strike else None,
            "strike_changed": ei.strike.changed if ei.strike else None,
            "trade_survival": ei.trade_survival,
            "strike_quality": ei.strike_quality,
        }

    # ---------- trading actions (the user only buys / sells) ----------
    def buy(self, option_symbol: str, lots: int = 1, confirm: bool = False,
            manual: bool = True, _fill: OrderResult | None = None) -> OrderResult:
        # ``_fill`` is internal: a broker-confirmed fill being adopted. It never
        # sends an order.
        adopted = _fill
        if adopted is None and self.is_live:
            blocked = self._live_entry_block()
            if blocked:
                self._add_alert("ORDER_RECOVERY", f"Entry refused: {blocked}", "critical")
                return OrderResult(ok=False, mode="live", side="BUY", option_symbol=option_symbol,
                                   lots=lots, message=blocked)
        # An earlier live entry still unresolved at the broker blocks a second
        # order; its confirmed fills are adopted as the position (no duplicate).
        if adopted is None and self._pending_order:
            adopted = self._poll_pending_entry()
            if adopted is not None and not adopted.ok:
                return adopted
            if adopted is None and self._pending_order:
                pend = self._pending_order
                return OrderResult(ok=False, mode="live", side="BUY",
                                   option_symbol=pend["symbol"], lots=pend["lots"],
                                   order_id=pend.get("order_id"),
                                   status=pend.get("last_status") or "PARTIAL",
                                   message="Earlier order still working at the broker — "
                                           "not sending another.")
        if adopted is not None:
            option_symbol = adopted.option_symbol
            lots = int(adopted.filled_lots or 0)
        chain = self.provider.option_chain()
        q = next((x for x in chain if x.symbol == option_symbol), None)
        if q is None:
            if adopted is not None:
                # A confirmed broker fill is never dropped: park it for the operator.
                self._enter_recovery(f"confirmed fill on {option_symbol} "
                                     f"({adopted.order_id}) is not in the current chain")
                self._apply_to_candidate(1, adopted)
                return OrderResult(ok=False, mode="live", side="BUY",
                                   option_symbol=option_symbol, lots=lots,
                                   order_id=adopted.order_id, status=adopted.status,
                                   message="Broker fill recorded for operator recovery.")
            raise ValueError(f"Unknown option {option_symbol}")

        # A hand-placed entry is checked against the engine's own verdict before
        # anything else. ``manual=False`` is the auto path, which already passed
        # the gates that produced the call it is acting on.
        if manual and adopted is None:
            refused = manual_guard.refusal(self._last_decision, self._feed_ready())
            if refused:
                self._add_alert("MANUAL_REFUSED", f"Entry refused: {refused}",
                                "warning")
                return OrderResult(
                    ok=False, mode="live" if self.is_live else "paper",
                    side="BUY", option_symbol=q.symbol, lots=lots,
                    message=refused,
                )

        if adopted is None and self.is_live and live_control.kill_switch_engaged():
            if manual:
                self._add_alert("KILL_SWITCH", "Entry refused: kill switch engaged", "warning")
            return OrderResult(ok=False, mode="live" if self.is_live else "paper", side="BUY",
                               option_symbol=q.symbol, lots=lots,
                               message="Kill switch engaged — new entries blocked.")
        can, reason = ((True, "") if adopted is not None
                       else self.risk.can_open(time.time()))
        if not can:
            self._add_alert("RISK_BLOCK", f"Trade blocked: {reason}", "warning")
            return OrderResult(ok=False, mode="live" if self.is_live else "paper", side="BUY",
                               option_symbol=q.symbol, lots=lots, message=f"Risk limit: {reason}")

        entry = q.premium
        # Paper fills pay the ask when a two-sided book exists (never LTP/mid).
        if adopted is None and not self.is_live and q.bid and q.ask and 0 < q.bid <= q.ask:
            entry = float(q.ask)
        order_id = None
        if adopted is not None:
            entry = float(adopted.fill_premium)
            order_id = adopted.order_id
        elif self.is_live:
            if not confirm:
                return OrderResult(ok=False, mode="live", side="BUY", option_symbol=q.symbol,
                                   lots=lots, message="Confirmation required for a live order.")
            res = self._submit_live("_pending_order", q.symbol, "BUY", lots)
            if not res.ok:
                return res
            # Broker-confirmed average price and lots only; never the quote LTP.
            entry = float(res.fill_premium)
            order_id = res.order_id
            lots = int(res.filled_lots or lots)

        self.position = Position(
            option_symbol=q.symbol,
            side="LONG",
            entry_premium=entry,
            current_premium=q.premium,
            quantity_lots=lots,
            trailing_stop=round(entry * 0.9, 1),
            entry_time=int(time.time()),
            peak_premium=entry,
            trough_premium=entry,
        )
        if adopted is not None or self.is_live:
            self._ledger_position = {"symbol": q.symbol, "lots": lots, "entry": entry,
                                     "order_ids": [order_id] if order_id else [],
                                     "ts": int(time.time()), "verified": True}
            if self.is_live and adopted is None and self._buy_product():
                self._ledger_position["product"] = self._buy_product()
            self._persist_state()
            self._event("POSITION_OPENED", {"order_id": order_id, "side": "BUY",
                                            "symbol": q.symbol, "lots": lots},
                        avg_price=entry)
        self._entry_time = int(time.time())
        # Top-of-book at the fill, when the feed carried one. Recorded, never
        # modelled: an unrecorded spread is what let a 5-minute round trip look
        # profitable on paper.
        self._entry_book = fill_book.book(q.bid, q.ask, self._entry_time)
        self._exit_book = None
        self.risk.record_entry()
        # capture the factors driving this entry so the learner can score them on exit
        otype = q.option_type
        side = "BULLISH" if otype == OptionType.CALL else "BEARISH"
        self._entry_factors = [b.name for b in self._last_breakdown if b.signal == side]
        self._entry_context = self._capture_context(entry)
        mode = "LIVE" if self.is_live else "paper"
        self._add_alert("BUY_NOW", f"[{mode}] Entered {q.symbol} @ {entry}", "info")
        journal_hooks.on_entry(self, q, lots, entry, manual)
        self._paper_pos_saved_at = 0.0
        return OrderResult(ok=True, mode=mode.lower(), side="BUY", option_symbol=q.symbol,
                           lots=lots, fill_premium=entry, order_id=order_id, message="Entered",
                           status="FILLED", filled_lots=lots)

    # ---------- broker order lifecycle (ack != fill) ----------
    # Live orders only. The intent is persisted BEFORE an order is sent; broker
    # status is read (never inferred from quotes); confirmed fills are applied
    # incrementally. Nothing here retries, cancels or resubmits an order.
    @staticmethod
    def _is_filled(res: OrderResult) -> bool:
        if res.status in ("FILLED", "PARTIAL"):
            return bool(res.fill_premium) and (res.filled_lots is None or res.filled_lots > 0)
        # Legacy providers that report no status but a fill price.
        return not res.status and bool(res.fill_premium)

    def _await_fill(self, res: OrderResult) -> OrderResult:
        """Poll read-only order status until FILLED/REJECTED/CANCELLED or timeout."""
        if not res.order_id or res.status in ("", "FILLED", "REJECTED", "CANCELLED"):
            return res
        deadline = time.time() + max(0.0, float(settings.order_fill_timeout_sec))
        latest = res
        while True:
            try:
                st = self.provider.order_status(res.order_id)
            except Exception:
                st = None
            if st is not None:
                latest = st
                if st.status in ("FILLED", "REJECTED", "CANCELLED"):
                    return st
            if time.time() >= deadline:
                return latest
            time.sleep(0.1)

    def _submit_live(self, attr: str, symbol: str, side: str, lots: int) -> OrderResult:
        """Send ONE live order. ok=True carries the confirmed fill increment
        (``filled_lots`` at the broker's ``fill_premium``); ok=False means
        rejected, refused, or still working at the broker (tracked, no duplicate)."""
        pend = {"order_id": None, "state": "SUBMITTING", "symbol": symbol, "side": side,
                "lots": int(lots), "filled_lots": 0, "avg_price": None, "ts": time.time()}
        setattr(self, attr, pend)
        persisted = self._persist_state() and self._event("SUBMIT_INTENT", pend)
        if not persisted and side == "BUY":
            # No durable record = no new exposure. (An exit still proceeds: it
            # reduces risk, and the failure is already alerted as critical.)
            setattr(self, attr, None)
            self._persist_state()
            return OrderResult(ok=False, mode="live", side=side, option_symbol=symbol, lots=lots,
                               message="Order ledger not writable — live entry refused "
                                       "before anything was sent.")
        # If place_order raises, the SUBMITTING record stays: the outcome is
        # unknown, so new entries remain blocked until it is reconciled.
        from app.ai.safety import RealMoneyBlocked
        try:
            prod = (self._ledger_position or {}).get("product") if side == "SELL" else self._buy_product()
            res = (self.provider.place_order(symbol, side, lots, product=prod) if prod
                   else self.provider.place_order(symbol, side, lots))
        except RealMoneyBlocked as exc:
            # The build's real-money lock raises before any broker call, so the
            # outcome is known (nothing sent): clear the record, refuse cleanly.
            setattr(self, attr, None)
            self._persist_state()
            self._event("SUBMIT_REFUSED_LOCKED", pend)
            self._add_alert("LIVE_ORDERS_LOCKED", f"{side} {symbol} not sent: {exc}", "critical")
            return OrderResult(ok=False, mode="live", side=side, option_symbol=symbol, lots=lots,
                               message="Live order not sent: real-money execution is "
                                       "disabled in this build.")
        if not res.ok and res.status == "UNKNOWN":
            # The request may have reached Angel: keep the SUBMITTING record so no
            # duplicate is sent; it is resolved via /api/order-recovery.
            pend["last_status"] = "UNKNOWN"
            self._persist_state()
            self._event("SUBMIT_UNKNOWN", pend, res)
            self._add_alert("ORDER_UNRESOLVED", f"{side} {symbol}: outcome unknown "
                            f"({res.message}) — check the Angel order book; not sending "
                            "another until resolved via /api/order-recovery", "critical")
            return res
        if not res.ok:
            setattr(self, attr, None)
            self._persist_state()
            self._event("SUBMIT_REJECTED", pend, res)
            self._add_alert("ORDER_REJECTED", f"{side} {symbol}: {res.message}", "critical")
            return res
        pend["order_id"] = res.order_id
        pend["state"] = "ACKNOWLEDGED"
        self._persist_state()
        self._event("ACKNOWLEDGED", pend, res)
        res = self._await_fill(res)
        out = self._consume_status(attr, res)
        if out is None:  # terminal at the broker with no fill (rejected/cancelled)
            return res.model_copy(update={"ok": False})
        if not out.ok:
            self._add_alert("ORDER_PENDING", f"{side} {symbol} acknowledged ({res.order_id}) "
                            "but not filled — no position change until it resolves", "critical")
        return out

    def _consume_status(self, attr: str, st: OrderResult | None) -> OrderResult | None:
        """Apply one broker status reading to the pending order in ``attr``.

        None = nothing pending, or terminal with no new fill (record cleared);
        ok=False = still working / unconfirmable (keep blocking duplicates);
        ok=True = a newly confirmed fill increment (``filled_lots`` new lots at
        their own average ``fill_premium``). The caller applies the increment
        and persists; a terminal record is only cleared once every reported
        fill has been priced and handed over.
        """
        pend = getattr(self, attr, None)
        if not pend:
            return None
        if st is None:
            msg = (f"Broker status for order {pend['order_id']} unavailable — cannot confirm; "
                   "not sending another order." if pend.get("order_id") else
                   "Order submission outcome unknown (no broker order id) — check the broker "
                   "order book and resolve via /api/order-recovery.")
            if pend.get("last_status") != "UNKNOWN":
                pend["last_status"] = "UNKNOWN"
                self._event("STATUS_UNAVAILABLE", pend, note=msg)
                self._persist_state()
            self._add_alert("ORDER_UNRESOLVED", f"{pend['side']} {pend['symbol']}: {msg}",
                            "critical")
            return OrderResult(ok=False, mode="live", side=pend["side"],
                               option_symbol=pend["symbol"], lots=pend["lots"],
                               order_id=pend.get("order_id"), status="UNKNOWN", message=msg)
        legacy_fill = not st.status and bool(st.fill_premium)
        total = st.filled_lots
        if total is None:
            total = pend["lots"] if (st.status == "FILLED" or legacy_fill) else 0
        total = max(0, min(int(total), int(pend["lots"])))
        prev = int(pend.get("filled_lots") or 0)
        inc, inc_avg = total - prev, None
        if inc > 0 and st.fill_premium:
            # Broker averageprice is cumulative over the order; the new lots'
            # own price is what the cumulative average moved by.
            prev_avg = float(pend.get("avg_price") or 0.0)
            inc_avg = (float(st.fill_premium) * total - prev_avg * prev) / inc
            inc_avg = round(inc_avg if inc_avg > 0 else float(st.fill_premium), 4)
            pend["filled_lots"] = total
            pend["avg_price"] = float(st.fill_premium)
        elif inc > 0:
            self._add_alert("ORDER_UNPRICED_FILL", f"{pend['side']} {pend['symbol']}: broker "
                            f"reports {total} lot(s) filled without an average price — "
                            "not applied; still tracking", "critical")
            inc = 0
        status = st.status or ("FILLED" if legacy_fill else "")
        terminal = (status in ("FILLED", "REJECTED", "CANCELLED")
                    and total <= int(pend.get("filled_lots") or 0))
        changed = inc > 0 or status != pend.get("last_status")
        pend["last_status"] = status
        if terminal:
            setattr(self, attr, None)
        elif status:
            pend["state"] = status
        if changed or terminal:
            kind = ("FILL" if terminal else "PARTIAL_FILL") if inc > 0 else f"STATUS_{status or 'UNKNOWN'}"
            self._event(kind, pend, st, inc_lots=inc or None, inc_avg_price=inc_avg)
            if inc <= 0:
                self._persist_state()
        if inc > 0:
            return OrderResult(ok=True, mode="live", side=pend["side"],
                               option_symbol=pend["symbol"], lots=pend["lots"],
                               filled_lots=inc, fill_premium=inc_avg,
                               order_id=pend.get("order_id"), status=status or "FILLED",
                               message=st.message)
        if terminal:
            if status in ("REJECTED", "CANCELLED"):
                self._add_alert("ORDER_REJECTED", f"{pend['side']} {pend['symbol']}: {status} "
                                f"{st.message}", "critical")
            return None
        done = int(pend.get("filled_lots") or 0)
        return OrderResult(
            ok=False, mode="live", side=pend["side"], option_symbol=pend["symbol"],
            lots=pend["lots"], order_id=pend.get("order_id"),
            status=status or "ACKNOWLEDGED",
            message=("Order acknowledged, not filled — awaiting broker fill; no duplicate "
                     "order will be sent." if not done else
                     f"Partially filled ({done}/{pend['lots']} lots); remainder still working "
                     "at the broker — no duplicate order will be sent."),
        )

    def _resolve_pending(self, attr: str) -> OrderResult | None:
        """Read-only broker check of the pending order in ``attr`` (see
        ``_consume_status`` for the return contract)."""
        pend = getattr(self, attr, None)
        if not pend:
            return None
        st = None
        if pend.get("order_id"):
            try:
                st = self.provider.order_status(pend["order_id"])
            except Exception:
                st = None
        return self._consume_status(attr, st)

    def _poll_pending_entry(self) -> OrderResult | None:
        """Reconcile an outstanding live entry. Fills for the position this order
        already opened are added here; an increment with no position yet is
        returned (ok=True) for ``buy`` to adopt."""
        res = self._resolve_pending("_pending_order")
        if res is None or not res.ok:
            return res
        if self.position.option_symbol:
            if self.position.option_symbol == res.option_symbol:
                self._add_confirmed_lots(res)
            else:
                self._enter_recovery(f"fill on {res.option_symbol} ({res.order_id}) while "
                                     f"holding {self.position.option_symbol}")
                self._apply_to_candidate(1, res)
            return None
        return res

    def _add_confirmed_lots(self, inc: OrderResult) -> None:
        """Residual fill of the open position's entry order: weighted-average entry."""
        p = self.position
        n, px = int(inc.filled_lots or 0), float(inc.fill_premium)
        held = p.quantity_lots
        p.entry_premium = round(((p.entry_premium or px) * held + px * n) / (held + n), 4)
        p.quantity_lots = held + n
        if self._auto_entry_active:
            self._auto_entry_lots += n
        if self._ledger_position is not None:
            self._ledger_position.update(lots=p.quantity_lots, entry=p.entry_premium)
            ids = self._ledger_position.setdefault("order_ids", [])
            if inc.order_id and inc.order_id not in ids:
                ids.append(inc.order_id)
        self._persist_state()
        self._add_alert("ORDER_FILL", f"Residual fill {inc.option_symbol}: +{n} lot(s) @ "
                        f"{px:.2f}; now {p.quantity_lots} lot(s) avg {p.entry_premium:.2f}",
                        "warning")

    def _ledger_after_exit(self, remaining: int) -> None:
        if self._ledger_position is None:
            return
        if remaining > 0:
            self._ledger_position["lots"] = remaining
        else:
            self._ledger_position = None
        self._persist_state()

    def _poll_live_orders(self) -> None:
        """Throttled, read-only broker reconciliation from the tick loop. Applies
        confirmed fills only; never places, cancels or resubmits an order."""
        now = time.time()
        if self._exch_stop:
            try:
                self._poll_exch_stop(now)
            except Exception as exc:
                self._add_alert("ORDER_RECONCILE_ERROR", f"Exchange stop check error: {exc}",
                                "critical")
        busy = (self._pending_order or self._pending_exit or self._order_recovery
                or self._persist_error)
        if now - self._last_order_poll < (5.0 if busy else 30.0):
            return
        self._last_order_poll = now
        if not busy:
            if order_ledger.mirror_backlog_bytes() > 0:
                order_ledger.mirror_pending()
            self._check_broker_position(now)
            return
        try:
            if self._persist_error:
                self._retry_persistence()
            if self._order_recovery:
                self._reconcile_recovery()
                return
            if self._pending_order:
                inc = self._poll_pending_entry()
                if inc is not None and inc.ok:
                    res = self.buy(inc.option_symbol, lots=int(inc.filled_lots or 0),
                                   manual=False, _fill=inc)
                    if res.ok:
                        self._add_alert("ORDER_FILL", f"Late broker fill adopted: "
                                        f"{inc.option_symbol} {inc.filled_lots} lot(s) @ "
                                        f"{inc.fill_premium:.2f} — review stop/target",
                                        "critical")
            if self._pending_exit and self.position.option_symbol:
                why = self._pending_exit.get("reason")
                inc = self._resolve_pending("_pending_exit")
                if inc is not None and inc.ok:
                    if why:
                        self._pending_exit_reason = why
                    self._book_exit(int(inc.filled_lots or 0), inc.order_id, inc.fill_premium)
                    if why and not self.position.option_symbol:
                        self._reset_auto_trade_marks()
        except Exception as exc:
            self._add_alert("ORDER_RECONCILE_ERROR", f"Order reconciliation error: {exc}",
                            "critical")

    def _check_broker_position(self, now: float) -> None:
        """Read-only: an open LIVE app position that Angel shows flat (netqty 0
        on two checks in a row) was closed outside the app. Flag recovery so
        the bot neither re-sells it (that would open a short) nor buys again
        until the operator confirms. An unknown answer is never read as flat."""
        sym = self.position.option_symbol
        fetch = getattr(self.provider, "broker_position", None)
        if (not self.is_live or not sym or fetch is None
                or self._pending_order or self._pending_exit):
            self._broker_flat_seen = 0
            return
        if now - self._last_broker_pos_check < settings.live_position_check_sec:
            return
        self._last_broker_pos_check = now
        try:
            row = fetch(sym)
        except Exception:
            row = None
        if not row or row.get("net_qty") != 0:
            self._broker_flat_seen = 0
            return
        self._broker_flat_seen += 1
        if self._broker_flat_seen < 2:
            return
        px = row.get("sell_avg")
        if (settings.live_auto_sync_angel_close and px and px > 0
                and self._order_recovery is None):
            self._sync_angel_close(sym, float(px), "closed in Angel")
            return
        p = self.position
        if self._ledger_position is None:
            self._ledger_position = {"symbol": sym, "lots": p.quantity_lots,
                                     "entry": p.entry_premium, "order_ids": [],
                                     "ts": int(self._entry_time or now), "verified": False}
        reason = (f"Angel shows {sym} flat while the app holds {p.quantity_lots} lot(s) "
                  "— closed outside the app")
        if self._order_recovery is None:
            self._order_recovery = {"reason": reason, "since": now, "manual_only": True}
        self._order_recovery["broker_flat"] = {"symbol": sym, "sell_avg": row.get("sell_avg"),
                                               "buy_avg": row.get("buy_avg")}
        self._enter_recovery(reason)

    def _angel_closed_row(self, sym: str) -> dict | None:
        """Read-only: Angel's row for ``sym`` if it shows the position closed
        (netqty 0 with a sell average). None = open or unknown."""
        fetch = getattr(self.provider, "broker_position", None)
        if fetch is None:
            return None
        try:
            row = fetch(sym)
        except Exception:
            return None
        if row and row.get("net_qty") == 0 and (row.get("sell_avg") or 0) > 0:
            return row
        return None

    def _sync_angel_close(self, sym: str, px: float, why: str) -> OrderResult:
        """The position was closed in the Angel app: book it at Angel's sell
        average so the journal matches Angel. Only a resting exchange stop is
        cancelled (it would otherwise open a short); no SELL is sent."""
        if self._exch_stop:
            held_up = self._release_exch_stop()
            if held_up is not None and held_up.status == "FILLED":
                return held_up  # it was our own exchange stop that closed it
        self._event("ANGEL_CLOSE_SYNCED", note=f"{why}: Angel netqty 0, sell avg {px}",
                    saved_position=self._ledger_position)
        self._pending_exit_reason = "MANUAL EXIT (ANGEL)"
        res = self._book_exit(self.position.quantity_lots, None, px)
        self._ledger_position = None
        self._broker_flat_seen = 0
        self._reset_auto_trade_marks()
        self._persist_state()
        self._add_alert("ANGEL_CLOSE_SYNCED", f"{sym} closed in Angel @ {px:.2f}: booked in "
                        "the journal, no order sent", "info")
        return res.model_copy(update={"message": f"{sym} already closed in Angel @ {px:.2f}: "
                                      "booked from Angel, no SELL sent."})

    # ---------- resting exchange stop at the locked floor (live only) ----------
    def _floor_level(self, floor: float) -> str:
        t1, t2, t3 = self._auto_target1, self._auto_target2, self._auto_target3
        if t3 is not None and floor > t3 + 1e-6:
            return "T3+ RATCHET"
        if t3 is not None and abs(floor - t3) < 1e-6:
            return "TARGET 3"
        if t2 is not None and floor > t2 + 1e-6:
            return "T2 RATCHET"
        if t2 is not None and abs(floor - t2) < 1e-6:
            return "TARGET 2"
        if t1 is not None and floor > t1 + 1e-6:
            return "T1 RATCHET"
        return "TARGET 1"

    def _exch_tick(self, sym: str) -> float:
        fn = getattr(self.provider, "tick_size", None)
        try:
            tick = float(fn(sym)) if fn else 0.05
        except Exception:
            tick = 0.05
        return tick if tick > 0 else 0.05

    def _safe_order_status(self, order_id: str | None) -> OrderResult | None:
        if not order_id:
            return None
        try:
            return self.provider.order_status(order_id)
        except Exception:
            return None

    def _sync_exch_stop(self, bid: float) -> None:
        """Rest (or raise) a STOPLOSS_LIMIT SELL at the locked floor. It only ever
        moves up, is never placed at or below entry (stop losses stay bot market
        exits), and never coexists with another working order."""
        self._exch_stop_breach_at = None
        p = self.position
        floor = self._auto_tgt_floor
        place = getattr(self.provider, "place_stop_order", None)
        if (not settings.live_exchange_trail_stop or not self.is_live or floor is None
                or place is None or not p.option_symbol or not self._auto_entry_active
                or self._pending_order or self._pending_exit or self._order_recovery
                or self._persist_error):
            return
        sym = p.option_symbol
        tick = self._exch_tick(sym)
        entry = self._auto_entry_prem or p.entry_premium or 0.0
        trigger = round(int(floor / tick + 1e-9) * tick, 2)
        if trigger <= entry or bid is None or bid < trigger + tick:
            return  # Angel rejects a SELL trigger at/above the market
        buf = max(0.0, settings.live_exchange_trail_stop_buffer_pct)
        limit = round(max(tick, int(trigger * (1.0 - buf / 100.0) / tick + 1e-9) * tick), 2)
        lots = int(p.quantity_lots)
        reason = f"{self._floor_level(floor)} lock (exchange stop)"
        now = time.time()
        es = self._exch_stop
        if es:
            if es.get("symbol") != sym or not es.get("order_id"):
                return
            step = max(tick, float(es["trigger"]) * settings.live_exchange_trail_stop_min_step_pct
                       / 100.0)
            if int(es["lots"]) == lots and trigger < float(es["trigger"]) + step - 1e-9:
                return
            if now - float(es.get("moved") or 0.0) < 3.0:
                return
            es["moved"] = now
            modify = getattr(self.provider, "modify_stop_order", None)
            prod = (self._ledger_position or {}).get("product")
            try:
                res = (modify(es["order_id"], sym, lots, trigger, limit, product=prod)
                       if modify else None)
            except Exception as exc:
                res = OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                                  lots=lots, order_id=es["order_id"], message=str(exc))
            if res is not None and res.ok:
                es.update(trigger=trigger, limit=limit, lots=lots, reason=reason)
                self._persist_state()
                self._event("EXCH_STOP_MODIFIED", {"order_id": es["order_id"], "side": "SELL",
                            "symbol": sym, "lots": lots}, res, trigger=trigger, limit=limit)
            else:
                # The older, lower order still protects; the bot floor exit backs it up.
                self._event("EXCH_STOP_MODIFY_FAILED", {"order_id": es["order_id"],
                            "side": "SELL", "symbol": sym, "lots": lots}, res,
                            trigger=trigger, limit=limit)
            return
        if now < self._exch_stop_retry_at:
            return
        es = {"order_id": None, "symbol": sym, "lots": lots, "trigger": trigger,
              "limit": limit, "variety": "STOPLOSS", "ts": now, "moved": now,
              "reason": reason}
        self._exch_stop = es
        if not (self._persist_state() and self._event("EXCH_STOP_INTENT", {"side": "SELL",
                "symbol": sym, "lots": lots}, trigger=trigger, limit=limit)):
            self._exch_stop = None
            self._persist_state()
            return
        from app.ai.safety import RealMoneyBlocked
        prod = (self._ledger_position or {}).get("product")
        try:
            res = place(sym, lots, trigger, limit, product=prod)
        except RealMoneyBlocked:
            self._exch_stop = None
            self._exch_stop_retry_at = now + 300.0
            self._persist_state()
            return
        except Exception as exc:
            res = OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym, lots=lots,
                              status="UNKNOWN", message=str(exc))
        if res.ok and res.order_id:
            es["order_id"] = res.order_id
            self._persist_state()
            self._event("EXCH_STOP_PLACED", {"order_id": res.order_id, "side": "SELL",
                        "symbol": sym, "lots": lots}, res, trigger=trigger, limit=limit)
            return
        if res.status == "UNKNOWN":
            # It may be resting at Angel without an id we know: keep the record so
            # no market SELL is sent on top of it, and ask the operator.
            self._event("EXCH_STOP_UNKNOWN", {"side": "SELL", "symbol": sym, "lots": lots},
                        res)
            self._add_alert("EXCH_STOP_UNRESOLVED", f"Stop order for {sym} may be resting at "
                            f"Angel ({res.message}). Check the Angel order book: cancel any "
                            "open SL SELL, then POST /api/order-recovery clear_pending "
                            "which=exch_stop. No bot SELL is sent until then.", "critical")
            return
        self._exch_stop = None
        self._exch_stop_retry_at = now + 30.0
        self._persist_state()
        self._event("EXCH_STOP_REJECTED", {"side": "SELL", "symbol": sym, "lots": lots}, res)
        self._add_alert("EXCH_STOP_REJECTED", f"Exchange stop for {sym} not placed: "
                        f"{res.message} — the bot floor exit still applies", "warning")

    def _exch_stop_to_pending_exit(self, st: OrderResult) -> OrderResult | None:
        """The resting stop has (partly) filled: hand it to the normal pending-exit
        reconciliation and book whatever the broker has confirmed."""
        es = self._exch_stop or {}
        self._pending_exit = {"order_id": es.get("order_id"), "state": "ACKNOWLEDGED",
                              "symbol": es.get("symbol"), "side": "SELL",
                              "lots": int(es.get("lots") or 0), "filled_lots": 0,
                              "avg_price": None, "ts": es.get("ts") or time.time(),
                              "reason": es.get("reason") or "TRAIL FLOOR (exchange stop)",
                              "exchange_stop": True}
        self._exch_stop = None
        self._exch_stop_breach_at = None
        self._persist_state()
        self._event("EXCH_STOP_TRIGGERED", self._pending_exit, st)
        why = self._pending_exit["reason"]
        inc = self._consume_status("_pending_exit", st)
        if inc is None or not inc.ok:
            return inc
        if self.position.option_symbol != inc.option_symbol:
            self._enter_recovery(f"exchange stop {inc.order_id} filled on {inc.option_symbol} "
                                 "while the app does not hold it — check Angel for a short")
            return inc
        self._pending_exit_reason = why
        res = self._book_exit(int(inc.filled_lots or 0), inc.order_id, inc.fill_premium)
        if not self.position.option_symbol:
            self._reset_auto_trade_marks()
        return res

    def _poll_exch_stop(self, now: float, min_gap: float = 5.0) -> bool:
        """Read-only check of the resting stop. True = it filled and was booked
        (or handed to the pending exit)."""
        es = self._exch_stop
        if not es or not es.get("order_id"):
            return False
        if now - float(es.get("checked") or 0.0) < min_gap:
            return False
        es["checked"] = now
        st = self._safe_order_status(es["order_id"])
        if st is None:
            return False
        if st.status in ("FILLED", "PARTIAL") or (st.filled_lots or 0) > 0:
            self._exch_stop_to_pending_exit(st)
            return True
        if st.status in ("REJECTED", "CANCELLED"):
            self._event("EXCH_STOP_DROPPED", {"order_id": es["order_id"], "side": "SELL",
                        "symbol": es.get("symbol"), "lots": es.get("lots")}, st)
            self._exch_stop = None
            self._exch_stop_retry_at = now + 30.0
            self._persist_state()
            self._add_alert("EXCH_STOP_DROPPED", f"Exchange stop {es['order_id']} "
                            f"{st.status.lower()}: {st.message} — the bot floor exit still "
                            "applies", "warning")
            return False
        if not self.position.option_symbol:
            self._release_exch_stop()  # flat but still resting: it would open a short
        return False

    def _release_exch_stop(self) -> OrderResult | None:
        """Before any other SELL: cancel the resting exchange stop and confirm it,
        so two sells can never both fill. None = clear to sell. An OrderResult =
        do NOT send: status FILLED means the stop already sold (booked from the
        broker fill); otherwise the cancel is unconfirmed (retried next tick)."""
        es = self._exch_stop
        if not es:
            return None
        sym = es.get("symbol") or ""
        oid = es.get("order_id")
        if not oid:
            return OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                               lots=int(es.get("lots") or 0), status="NOT_SENT",
                               message="A stop order may be resting at Angel with no known id — "
                               "no SELL sent. Check the Angel order book, then clear_pending "
                               "which=exch_stop.")
        held = OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                           lots=int(es.get("lots") or 0), order_id=oid, status="FILLED",
                           message="The exchange stop at the floor already sold this "
                           "position — booked from the broker fill; no second SELL sent.")

        def settled(st: OrderResult | None) -> OrderResult | None | bool:
            if st is None:
                return False
            if st.status in ("FILLED", "PARTIAL") or (st.filled_lots or 0) > 0:
                self._exch_stop_to_pending_exit(st)
                return held
            if st.status in ("CANCELLED", "REJECTED"):
                self._event("EXCH_STOP_CANCELLED", {"order_id": oid, "side": "SELL",
                            "symbol": sym, "lots": es.get("lots")}, st)
                self._exch_stop = None
                self._exch_stop_breach_at = None
                self._persist_state()
                return None
            return False

        out = settled(self._safe_order_status(oid))
        if out is not False:
            return out
        cancel = getattr(self.provider, "cancel_order", None)
        from app.ai.safety import RealMoneyBlocked
        try:
            res = cancel(oid, es.get("variety") or "STOPLOSS") if cancel else None
        except RealMoneyBlocked:
            res = None
        except Exception as exc:
            res = OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym, lots=0,
                              order_id=oid, status="UNKNOWN", message=str(exc))
        self._event("EXCH_STOP_CANCEL_SENT", {"order_id": oid, "side": "SELL", "symbol": sym,
                    "lots": es.get("lots")}, res)
        deadline = time.time() + max(0.0, float(settings.order_fill_timeout_sec))
        while True:
            out = settled(self._safe_order_status(oid))
            if out is not False:
                return out
            if time.time() >= deadline:
                break
            time.sleep(0.1)
        self._add_alert("EXCH_STOP_UNRESOLVED", f"Cancel of exchange stop {oid} ({sym}) not "
                        "confirmed — no market SELL sent to avoid a double sell; retrying.",
                        "critical")
        return OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                           lots=int(es.get("lots") or 0), order_id=oid, status="NOT_SENT",
                           message="Exchange stop cancel not confirmed yet — no SELL sent; "
                           "retrying.")

    def _broker_closed(self) -> bool:
        rec = self._order_recovery
        return bool(rec and rec.get("broker_flat")
                    and rec["broker_flat"].get("symbol") == self.position.option_symbol)

    def _recovery_cleared(self, note: str) -> None:
        self._order_recovery = None
        self._event("RECOVERY_CLEARED", note=note)
        self.alerts = [a for a in self.alerts if a.kind != "ORDER_RECOVERY"]
        self._add_alert("ORDER_RECOVERY_CLEARED", f"Order recovery cleared: {note}", "info")

    # ---------- crash-safe persistence + restart recovery ----------
    def _persist_state(self) -> bool:
        try:
            order_ledger.save(self._instrument, {
                "pending_entry": self._pending_order,
                "pending_exit": self._pending_exit,
                "exch_stop": self._exch_stop,
                "position": self._ledger_position,
                "recovery": dict(self._order_recovery) if self._order_recovery else None,
            })
            return True
        except order_ledger.LedgerError as exc:
            self._persist_error = f"ledger write failed: {exc}"[:200]
            self._add_alert("LEDGER_WRITE_FAILED", f"Order ledger write failed: {exc}",
                            "critical")
            return False

    def _event(self, kind: str, pend: dict | None = None, st: OrderResult | None = None,
               **extra) -> bool:
        pend = pend or {}
        fields = {
            "order_id": pend.get("order_id") or (st.order_id if st else None),
            "side": pend.get("side") or (st.side if st else None),
            "option_symbol": pend.get("symbol") or (st.option_symbol if st else None),
            "status": st.status if st else None,
            "lots": pend.get("lots"),
            "filled_lots": pend.get("filled_lots"),
            "avg_price": st.fill_premium if st else None,
            "mode": "live",
        }
        if st is not None and st.message:
            fields["broker_message"] = st.message[:200]
        fields.update(extra)
        try:
            order_ledger.record_event(self._instrument, kind, **fields)
            return True
        except order_ledger.LedgerError as exc:
            self._persist_error = f"audit log append failed: {exc}"[:200]
            self._add_alert("LEDGER_WRITE_FAILED", f"Order audit log write failed ({kind}): "
                            f"{exc}", "critical")
            return False

    def _retry_persistence(self) -> None:
        prev = self._persist_error
        if self._persist_state():
            self._persist_error = None
            self._event("PERSISTENCE_RESTORED", note=prev)

    def _live_entry_block(self) -> str | None:
        if self._order_recovery:
            return (f"Order recovery required ({self._order_recovery.get('reason')}) — new live "
                    "entries blocked until reconciled via /api/order-recovery.")
        if self._persist_error:
            return (f"Order persistence failed ({self._persist_error}) — new live entries "
                    "blocked.")
        return None

    _PAPER_SAVE_FIELDS = (
        "_entry_time", "_entry_factors", "_entry_context", "_entry_book",
        "_auto_last_key", "_auto_target1", "_auto_target2", "_auto_target3",
        "_auto_stop", "_auto_tgt_floor", "_auto_scaled_out", "_auto_pre_t1_halved", "_auto_peak",
        "_auto_entry_prem", "_auto_entry_active", "_auto_entry_strike",
        "_auto_entry_conf", "_auto_entry_lots", "_z2h_is_sleeve", "_jr_trade_id",
    )

    # A live bot trade's exit state, saved with the order ledger so an adopted
    # position resumes the same stop / T1-T3 / locked floor / peak after a restart.
    _LIVE_MGMT_FIELDS = (
        "_auto_target1", "_auto_target2", "_auto_target3", "_auto_stop", "_auto_tgt_floor",
        "_auto_scaled_out", "_auto_pre_t1_halved", "_auto_peak", "_auto_entry_prem",
        "_auto_entry_strike", "_auto_entry_conf", "_auto_entry_lots",
    )

    def _save_live_mgmt(self, force: bool = False) -> None:
        c = self._ledger_position
        if not (self.is_live and self._auto_entry_active and c and self.position.option_symbol
                and c.get("symbol") == self.position.option_symbol):
            return
        m = {f: getattr(self, f, None) for f in self._LIVE_MGMT_FIELDS}
        old = c.get("mgmt") or {}
        peak, opeak = m.get("_auto_peak"), old.get("_auto_peak")
        same = all(m[f] == old.get(f) for f in m if f != "_auto_peak")
        peak_moved = peak is not None and (opeak is None or peak > opeak * 1.01)
        if not force and same and not peak_moved:
            return
        c["mgmt"] = m
        c.update(t1=m["_auto_target1"], t2=m["_auto_target2"])
        self._persist_state()

    def _save_paper_position(self, now: float) -> None:
        """Keep the open PAPER position in the database so a restart resumes it
        (exits keep running) instead of silently dropping the trade."""
        if self.is_live or settings.trade_mode.lower() != "paper":
            return
        try:
            if self.position.option_symbol and self.position.quantity_lots > 0:
                if now - self._paper_pos_saved_at < settings.paper_position_save_sec:
                    return
                payload = {"position": self.position.model_dump(),
                           "jr_levels": list(getattr(self, "_jr_levels", (None, None)))}
                for f in self._PAPER_SAVE_FIELDS:
                    payload[f] = getattr(self, f, None)
                paper_positions.save(self._instrument, payload)
                self._paper_pos_saved_at = now
                self._paper_pos_saved = True
            elif self._paper_pos_saved:
                paper_positions.clear(self._instrument)
                self._paper_pos_saved = False
        except Exception:
            _log.warning("could not save the open paper position for %s",
                         self._instrument, exc_info=True)

    def _restore_paper_position(self) -> None:
        if settings.trade_mode.lower() != "paper":
            return
        try:
            saved = paper_positions.load(self._instrument)
            if not saved:
                return
            ts, payload = saved
            pos = payload.get("position") or {}
            if (time.time() - ts > settings.paper_position_restore_max_age_sec
                    or not pos.get("option_symbol") or not pos.get("quantity_lots")):
                paper_positions.clear(self._instrument)
                return
            self.position = Position(**pos)
            for f in self._PAPER_SAVE_FIELDS:
                if f in payload:
                    setattr(self, f, payload[f])
            if payload.get("jr_levels"):
                self._jr_levels = tuple(payload["jr_levels"])
            self._entry_context = dict(self._entry_context or {})
            self._entry_factors = list(self._entry_factors or [])
            self._paper_pos_saved = True
            self._paper_pos_saved_at = time.time()
            self._add_alert(
                "POSITION_RESTORED",
                f"[paper] Restored open {self.position.option_symbol} "
                f"x{self.position.quantity_lots} @ {self.position.entry_premium} after restart",
                "info")
        except Exception:
            _log.warning("could not restore the paper position for %s",
                         self._instrument, exc_info=True)

    def _restore_live_ledger(self) -> None:
        """After a restart, saved live state is a CLAIM, not the broker's truth:
        restore it as unverified, block new live entries, and reconcile."""
        try:
            saved = order_ledger.load(self._instrument)
        except order_ledger.LedgerError as exc:
            self._order_recovery = {"reason": f"order ledger unreadable: {exc}"[:300],
                              "since": time.time(), "manual_only": True}
            self._add_alert("ORDER_RECOVERY", f"Order ledger unreadable: {exc} — new live "
                            "entries blocked", "critical")
            return
        if not saved:
            return
        self._pending_order = saved.get("pending_entry")
        self._pending_exit = saved.get("pending_exit")
        self._exch_stop = saved.get("exch_stop")
        pos = saved.get("position")
        self._ledger_position = dict(pos) if pos else None
        prev = saved.get("recovery")
        if prev:
            self._order_recovery = dict(prev)
        self._enter_recovery("backend restarted with unresolved live order/position state")

    def _enter_recovery(self, reason: str) -> None:
        if self._order_recovery is None:
            self._order_recovery = {"reason": reason[:300], "since": time.time()}
        if self._ledger_position is not None:
            self._ledger_position["verified"] = False
        self._add_alert("ORDER_RECOVERY", f"Live order recovery required: {reason}. New live "
                        "entries blocked; the saved position is NOT assumed to match the "
                        "broker. Check the broker, then POST /api/order-recovery.", "critical")
        self._event("RECOVERY_REQUIRED", note=reason)
        self._persist_state()

    def _apply_to_candidate(self, sign: int, inc: OrderResult) -> None:
        """Confirmed fills seen during recovery update the UNVERIFIED saved
        position (never ``self.position``) and the event log."""
        rec = self._order_recovery if self._order_recovery is not None else {}
        rec["fills_seen"] = True
        c = self._ledger_position
        n, px = int(inc.filled_lots or 0), float(inc.fill_premium)
        if sign > 0 and c is None:
            self._ledger_position = {"symbol": inc.option_symbol, "lots": n, "entry": px,
                                     "order_ids": [inc.order_id] if inc.order_id else [],
                                     "ts": int(time.time()), "verified": False}
        elif c is not None and c.get("symbol") == inc.option_symbol:
            held = int(c.get("lots") or 0)
            if sign > 0:
                c["entry"] = round(((c.get("entry") or px) * held + px * n) / (held + n), 4)
                c["lots"] = held + n
            else:
                c["lots"] = held - n
                if c["lots"] <= 0:
                    self._ledger_position = None
        else:
            rec.setdefault("conflicts", []).append(
                {"order_id": inc.order_id, "symbol": inc.option_symbol, "side": inc.side,
                 "lots": n, "price": px, "ts": time.time()})
            self._add_alert("ORDER_RECOVERY", f"Recovery conflict: fill on {inc.option_symbol} "
                            "does not match the saved position", "critical")
        self._persist_state()

    def _reconcile_recovery(self) -> None:
        """Read-only order-book reconciliation of restored orders. Auto-clears
        only when every restored order ended with no fill and no position was
        saved; anything else waits for the operator."""
        rec = self._order_recovery
        for attr, sign in (("_pending_order", 1), ("_pending_exit", -1)):
            if getattr(self, attr):
                inc = self._resolve_pending(attr)
                if inc is not None and inc.ok:
                    self._apply_to_candidate(sign, inc)
        if (rec and self._ledger_position is None and not self._pending_order
                and not self._pending_exit and not self._exch_stop
                and not rec.get("fills_seen")
                and not rec.get("manual_only") and not rec.get("conflicts")):
            self._recovery_cleared("restored orders ended unfilled; no saved position")
            self._persist_state()

    def recovery_status(self) -> dict:
        rec = self._order_recovery or {}
        p = self.position
        return {
            "instrument": self._instrument,
            "recovery_required": self._order_recovery is not None,
            "reason": rec.get("reason"),
            "since": rec.get("since"),
            "conflicts": rec.get("conflicts") or [],
            "pending_entry": self._pending_order,
            "pending_exit": self._pending_exit,
            "exchange_stop": self._exch_stop,
            "saved_position": self._ledger_position,
            "app_position": ({"symbol": p.option_symbol, "lots": p.quantity_lots,
                              "entry": p.entry_premium} if p.option_symbol else None),
            "persist_error": self._persist_error,
            "live_entries_blocked": self._live_entry_block(),
        }

    def resolve_recovery(self, action: str, note: str, which: str | None = None) -> dict:
        """Operator decision after checking the broker (authorised by the caller).
        Every action is written to the audit log; nothing is sent to the broker."""
        note = (note or "").strip()[:300]
        if len(note) < 3:
            raise ValueError("A note describing the broker-side check is required.")
        if action == "clear_pending":
            attr = {"entry": "_pending_order", "exit": "_pending_exit",
                    "exch_stop": "_exch_stop"}.get((which or "").lower())
            if not attr:
                raise ValueError("which must be 'entry', 'exit' or 'exch_stop'.")
            pend = getattr(self, attr)
            if not pend:
                raise ValueError(f"No pending {which} order.")
            self._event("OPERATOR_CLEARED_PENDING", pend, note=note)
            setattr(self, attr, None)
        elif action == "confirm_flat":
            if self._order_recovery is None:
                raise ValueError("No recovery in progress.")
            if self._pending_exit:
                raise ValueError("Resolve the pending exit order first.")
            # A resting exchange SELL on a flat account would open a short if it
            # triggered: cancel it (risk-reducing) before accepting "flat".
            if self._exch_stop and self._release_exch_stop() is not None:
                raise ValueError("The resting exchange stop order could not be confirmed "
                                 "cancelled. Cancel it in Angel, then clear_pending "
                                 "which=exch_stop.")
            self._event("OPERATOR_CONFIRMED_FLAT", note=note,
                        saved_position=self._ledger_position)
            if self._broker_closed():
                # Closed in Angel: book it at Angel's sell average (nothing is
                # sent) so the journal and the day's loss cap see this trade.
                px = self._order_recovery["broker_flat"].get("sell_avg")
                self._pending_exit_reason = "MANUAL EXIT (ANGEL)"
                self._book_exit(self.position.quantity_lots, None,
                                float(px) if px else None)
                self._reset_auto_trade_marks()
            self._ledger_position = None
            self._order_recovery.pop("conflicts", None)
        elif action == "adopt_position":
            if self._order_recovery is None:
                raise ValueError("No recovery in progress.")
            c = self._ledger_position
            if not c or not c.get("lots") or not c.get("entry"):
                raise ValueError("No saved position with lots and a broker price to adopt.")
            if self.position.option_symbol:
                raise ValueError("The app already holds a position on this instrument.")
            entry = float(c["entry"])
            opened = int(c.get("ts") or time.time())
            self.position = Position(
                option_symbol=c["symbol"], side="LONG", entry_premium=entry,
                current_premium=entry, quantity_lots=int(c["lots"]),
                trailing_stop=round(entry * 0.9, 1), entry_time=opened,
                peak_premium=entry, trough_premium=entry,
            )
            self._entry_time = opened
            self._entry_book = None
            self._exit_book = None
            self._entry_factors = []
            self._entry_context = {"recovered": True, "entry_time": opened,
                                   "order_ids": list(c.get("order_ids") or [])}
            mg = c.get("mgmt") or {}
            if mg:
                for f in self._LIVE_MGMT_FIELDS:
                    if f in mg:
                        setattr(self, f, mg[f])
                self._auto_entry_active = True
            elif c.get("t1") or c.get("t2"):
                self._auto_target1, self._auto_target2 = c.get("t1"), c.get("t2")
                self._auto_entry_active = True
            if self._auto_entry_active:
                # The bot keeps managing the exit: a hard stop is always set.
                self._auto_entry_prem = self._auto_entry_prem or entry
                self._auto_entry_lots = self._auto_entry_lots or int(c["lots"])
                self._auto_peak = max(self._auto_peak or entry, entry)
                if not self._auto_stop:
                    self._auto_stop = round(entry * 0.9, 1)
            c["verified"] = True
            self._order_recovery.pop("conflicts", None)
            self._event("OPERATOR_ADOPTED_POSITION", note=note, saved_position=c)
        else:
            raise ValueError("action must be adopt_position, confirm_flat or clear_pending.")
        if (self._order_recovery is not None
                and (self._ledger_position is None or self._ledger_position.get("verified"))
                and not self._order_recovery.get("conflicts")
                and not any(pd and not pd.get("order_id")
                            for pd in (self._pending_order, self._pending_exit))):
            self._recovery_cleared(note)
        if not self._persist_state():
            raise RuntimeError(f"Order ledger write failed: {self._persist_error}")
        return self.recovery_status()

    def sell(self, confirm: bool = False, lots: int | None = None,
             fill_px: float | None = None, check_broker: bool = False) -> OrderResult:
        """Close the position. ``lots`` sells only part of it (scale-out), leaving
        the remainder open to ride; ``None`` (default) closes everything."""
        if not self.position.option_symbol:
            return OrderResult(ok=False, mode="paper", side="SELL", option_symbol="",
                               lots=0, message="No open position.")
        sym = self.position.option_symbol
        held = self.position.quantity_lots
        partial = lots is not None and 0 < lots < held
        lots = lots if partial else held
        if self._pending_exit:
            exit_fill = self._resolve_pending("_pending_exit")
            if exit_fill is not None and not exit_fill.ok:
                return exit_fill
            if exit_fill is not None:
                return self._book_exit(int(exit_fill.filled_lots or 0), exit_fill.order_id,
                                       exit_fill.fill_premium)
        order_id = None
        exit_px = None
        if self.is_live:
            if not confirm:
                return OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                                   lots=lots, message="Confirmation required for a live order.")
            if self._broker_closed():
                return OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                                   lots=lots, message="Angel shows this position already "
                                   "closed — no SELL sent. Confirm flat via /api/order-recovery.")
            if check_broker and not partial:
                row = self._angel_closed_row(sym)
                if row:
                    return self._sync_angel_close(sym, float(row["sell_avg"]), "Exit now")
            held_up = self._release_exch_stop()
            if held_up is not None:
                return held_up
            now = time.time()
            if now < self._exit_retry_at:
                return OrderResult(ok=False, mode="live", side="SELL", option_symbol=sym,
                                   lots=lots, status="NOT_SENT",
                                   message="Last SELL never reached Angel; retrying shortly.")
            res = self._submit_live("_pending_exit", sym, "SELL", lots)
            if not res.ok:
                if res.status == "NOT_SENT" and not self._pending_exit:
                    self._exit_retry_at = now + settings.live_exit_retry_sec
                return res
            order_id = res.order_id
            lots = int(res.filled_lots or lots)
            exit_px = res.fill_premium
        else:
            # Paper exits receive the bid when a two-sided book exists.
            try:
                q = next((x for x in self.provider.option_chain() if x.symbol == sym), None)
            except Exception:
                q = None
            if q is not None and q.bid and q.ask and 0 < q.bid <= q.ask:
                exit_px = float(q.bid)
            if fill_px is not None and fill_px > 0:
                # PAPER resting limit (a target): filled at the level, or at a
                # better bid.
                exit_px = max(float(fill_px), exit_px or 0.0)
        return self._book_exit(lots, order_id, exit_px)

    def _book_exit(self, lots: int, order_id: str | None,
                   exit_px: float | None) -> OrderResult:
        """Realise ``lots`` of the open position (all of it when ``lots >= held``).
        A live exit is booked at the broker's confirmed average price."""
        sym = self.position.option_symbol or ""
        held = self.position.quantity_lots
        lots = max(0, min(int(lots), held))
        partial = 0 < lots < held
        if exit_px:
            self._mark_position(float(exit_px))
        # On a scale-out only the sold share of the position is realised; the
        # rest keeps running and is booked on the final exit.
        pnl = self.position.net_pnl * (lots / held if held else 1.0)
        if pnl >= 0:
            self.today_profit += pnl
        else:
            self.today_loss += -pnl
        # A stop-out is recorded as such: the account governor cools an
        # instrument down for longer after a stop than after a target, because
        # the view has been refused rather than completed.
        self.risk.record_exit(
            pnl, stopped=(self._pending_exit_reason or "").upper().startswith("STOP")
        )
        # self-learning: reward/penalise the factors that drove this trade
        weights.store.record_trade(self._entry_factors, pnl)
        ctx = self._entry_context
        entry_time = ctx.get("entry_time")
        hold_minutes = int((int(time.time()) - entry_time) / 60) if entry_time else None
        sym_closed = self.position.option_symbol or ""
        otype = "CE" if sym_closed.upper().endswith("CE") else (
            "PE" if sym_closed.upper().endswith("PE") else None
        )
        trade_row = {
            "time": int(time.time()),
            "instrument": self._instrument,
            "option": self.position.option_symbol,
            "option_type": otype,
            "entry": self.position.entry_premium,
            "exit": self.position.current_premium,
            "lots": lots,
            "net_pnl": round(pnl, 1),
            "win": pnl >= 0,
            "holding_minutes": hold_minutes,
            "mode": "live" if self.is_live else "paper",
            # bot-history tags: whether the bot opened it and why it closed
            "auto": self._auto_entry_active,
            # Never leave an exit unattributed: a close with no engine reason was
            # a hand close, and an untagged row can't be scored in the journal.
            "exit_reason": self._pending_exit_reason or (
                "SCALE-OUT (manual)" if partial else "MANUAL CLOSE"
            ),
            "confidence": self._auto_entry_conf,
            "factors": list(self._entry_factors),
            # Validation Mode: full decision context frozen at entry.
            "context": ctx,
        }
        # Capital accounting on every exit row. On a long option the capital used
        # IS the premium paid (there is no margin), and it is also the maximum
        # loss, so the return on it is the honest way to read a rupee figure:
        # +Rs550 on Rs17,070 of premium is +3.2%, not "a small win".
        # A hand-registered position may not know its entry premium; report the
        # field as unknown rather than as zero capital, which would read as an
        # infinite return.
        spec_now = get_spec(self._instrument)
        entry_prem = self.position.entry_premium or 0.0
        premium_paid = entry_prem * spec_now.lot_size * lots
        stop_at_entry = self._auto_stop
        trade_row["capital_used"] = round(premium_paid, 0) if premium_paid > 0 else None
        # Notional is the underlying exposure the contract controls (strike x
        # quantity), which is a different and much larger number than the premium
        # paid — keeping them in separate columns stops one being read as the other.
        strike_now = self._auto_entry_strike
        trade_row["notional"] = (
            round(strike_now * spec_now.lot_size * lots, 0) if strike_now else None
        )
        trade_row["lot_size"] = spec_now.lot_size
        trade_row["risk_rupees"] = (
            round(max(0.0, entry_prem - stop_at_entry) * spec_now.lot_size * lots, 0)
            if stop_at_entry is not None and entry_prem > 0
            else None
        )
        trade_row["return_on_capital_pct"] = (
            round(pnl / premium_paid * 100.0, 2) if premium_paid > 0 else None
        )
        # Charges itemised on the row: brokerage is per ORDER, so the columns can
        # be checked against a contract note instead of being taken on trust.
        exit_prem = self.position.current_premium
        charges_now = option_costs.charges(
            entry_prem, exit_prem, spec_now.lot_size * lots, self._instrument
        )
        trade_row.update(charges_now.as_dict())
        # Top-of-book at both ends, or COST_STATUS UNKNOWN. Never a modelled
        # spread on a trade record.
        books = fill_book.fill_record(
            self._entry_book, self._exit_book, spec_now.lot_size * lots,
            entry_ts=entry_time, exit_ts=trade_row["time"],
        )
        trade_row.update(books)
        if isinstance(ctx, dict):
            ctx.update(
                {
                    "capital_used": trade_row["capital_used"],
                    "notional": trade_row["notional"],
                    "risk_rupees": trade_row["risk_rupees"],
                    "return_on_capital_pct": trade_row["return_on_capital_pct"],
                    "lot_size": spec_now.lot_size,
                }
            )
            ctx.update(charges_now.as_dict())
            ctx.update(books)
        self._pending_exit_reason = None
        self.journal.append(trade_row)
        journal_hooks.on_exit(self, trade_row, partial, lots)
        # Phase 23 — attach this closed trade to the hurdle-shadow row that
        # recorded the opportunity, so the three arms are scored on the engine's
        # own outcome rather than a simulated path. Record only.
        try:
            phase23.resolve(self._instrument, trade_row)
        except Exception:
            pass
        # Durable, cross-instrument record for the Account tab + monthly reports
        # (the in-memory journal is lost on restart).
        try:
            storage.store.record_trade(self._instrument, trade_row)
        except Exception:
            pass
        # Complete "record everything" journal: full context + a plain-English
        # analyst note, so nothing shown on the dashboard is lost on restart.
        try:
            storage.store.record_journal(
                self._instrument, trade_row, analyst.note_for_trade(trade_row)
            )
        except Exception:
            pass
        # Research capture (advisory only): record the completed paper trade into
        # the research store with MFE/MAE and full context so the Setup Library
        # and post-trade analytics have real evidence. Never influences the engine.
        try:
            self._record_research_trade(trade_row, entry_time)
        except Exception:
            pass
        mode = "LIVE" if self.is_live else "paper"
        exit_prem = self.position.current_premium
        if partial:
            # keep the runner open: reduce the size, leave entry/context intact
            self.position.quantity_lots = held - lots
            self._auto_entry_lots = max(0, self._auto_entry_lots - lots)
            self._ledger_after_exit(held - lots)
            self._paper_pos_saved_at = 0.0
            self._add_alert(
                "EXIT_NOW",
                f"[{mode}] Scaled out {lots}/{held} {sym} net {pnl:+.0f}",
                "info",
            )
            return OrderResult(ok=True, mode=mode.lower(), side="SELL", option_symbol=sym,
                               lots=lots, fill_premium=exit_prem, order_id=order_id,
                               message=f"Scaled out {lots} of {held} net {pnl:+.0f}")
        self._entry_factors = []
        self._entry_context = {}
        self._auto_last_exit_ts = int(time.time())
        self._auto_last_exit_was_stop = str(
            trade_row.get("exit_reason") or "").upper().startswith("STOP")
        self._add_alert("EXIT_NOW", f"[{mode}] Exited {sym} net {pnl:+.0f}", "info")
        self.position = Position()
        self._entry_time = None
        self._ledger_after_exit(0)
        self._paper_pos_saved_at = 0.0
        self._save_paper_position(time.time())
        return OrderResult(ok=True, mode=mode.lower(), side="SELL", option_symbol=sym,
                           lots=lots, fill_premium=exit_prem, order_id=order_id,
                           message=f"Exited net {pnl:+.0f}")

    def _add_alert(self, kind: str, message: str, severity: str, cooldown: float = 90.0) -> None:
        now = int(time.time())
        key = f"{kind}|{message}"
        last = self._alert_seen.get(key)
        if last is not None and now - last < cooldown:
            return  # suppress duplicate/repeated alert within cooldown window
        self._alert_seen[key] = now
        self.alerts.insert(0, Alert(time=now, kind=kind, message=message, severity=severity))
        self.alerts = self.alerts[:40]
        if severity in ("critical", "warning"):
            notify.push(f"*Quantum Trader* — {kind}\n{message}")

    # ---------- per-tick assembly ----------
    def tick(self) -> Snapshot:
        # If the provider couldn't be built earlier (e.g. the live feed couldn't
        # resolve this instrument's contract yet, or was rate-limited), retry the
        # real build now so the instrument self-heals once the feed is ready.
        if isinstance(self.provider, _UnavailableProvider):
            self.provider = self._provider_for(self._instrument)
        provider_note = self.provider.reason if isinstance(self.provider, _UnavailableProvider) else None
        self.provider.step()
        self._poll_live_orders()
        self._save_paper_position(time.time())
        _mark_stage(self._instrument, "cache")
        now = int(time.time())
        # v3.5.46: REST history extended with the 1-minute bars built from the
        # live ticks, so the trend is read on current prices even when the
        # key's history budget is behind. Same series the futures plan uses.
        candles = self.provider.futures_candles_live(300) or self.provider.futures_candles(300)
        if not candles and not provider_note:
            # Instrument resolved and may be streaming a live price, but the
            # historical-candle feed returned nothing (rate-limit / no historical
            # entitlement / token mismatch) — surface the real reason instead of
            # a bare "waiting for market data".
            diag = self.provider.diagnostics()
            if diag:
                provider_note = diag
        chain = self.provider.option_chain()
        price = self.provider.futures_price()
        news_items = self.provider.news()
        if settings.news_source.lower() == "rss":
            rss_items = rss_news.fetch()
            if rss_items:
                news_items = rss_items

        # News recency must be measured against the FEED's own clock (latest
        # candle time), not wall-clock — a warm-started/simulated feed can run
        # its clock ahead of real time, which would otherwise make every
        # headline look "just released" and pin the market in NEWS_MODE.
        feed_now = candles[-1].time if candles else now
        news_sent, news_score = news_mod.aggregate(news_items, feed_now)
        high_impact = news_mod.has_high_impact(news_items, feed_now)
        news_session = news_mod.session_impact(news_items, feed_now)

        # Pre-event guard: warn (and mildly dampen confidence) around high-impact
        # scheduled events relevant to THIS instrument. Uses wall-clock (events
        # are real calendar dates), not the feed clock.
        affects = "crude" if get_spec(self._instrument).exchange == "MCX" else "equity"
        eg = events_mod.guard(int(time.time()), affects=affects)
        if eg["active"]:
            self._add_alert(
                "EVENT",
                f"{eg['event']} in ~{eg['minutes_to']}m — high-impact event, prefer WAIT.",
                "warning",
                cooldown=300.0,
            )

        price_change = candles[-1].close - candles[-2].close if len(candles) > 1 else 0.0
        snap_ind = eng.compute_indicators(
            candles, chain, price_change=price_change,
            get_opt_candles=self.provider.option_candles,
        )
        snap_ind.news_material = news_session["material"]
        snap_ind.news_bull_pct = news_session["bull_pct"]
        snap_ind.news_bear_pct = news_session["bear_pct"]
        market_status = eng.classify_market(snap_ind, high_impact is not None)
        self._last_market_status = str(getattr(market_status, "value", market_status) or "")

        # update open position marks
        in_pos = bool(self.position.option_symbol)
        pos_type = None
        if in_pos:
            q = next((x for x in chain if x.symbol == self.position.option_symbol), None)
            if q is not None:
                pos_type = q.option_type
                self._mark_position(q.premium, q)

        _mark_stage(self._instrument, "process")
        raw_decision, breakdown = eng.decide(
            candles, chain, snap_ind, news_score, high_impact is not None,
            market_status, in_pos, pos_type,
            news_factor=news_session["factor"] * eg["factor"],
            spot=price,
            option_atr=self._option_atr,
            exchange=get_spec(self._instrument).exchange,
            lot_size=int(get_spec(self._instrument).lot_size or 0),
        )
        self._last_breakdown = breakdown
        _mark_stage(self._instrument, "decision")

        # Which tier this instrument is in decides whether the EXPENSIVE capture
        # runs — chain snapshots and research snapshots, the two costs that
        # starved the feed. Candles are always stored. An open position is always
        # captured regardless of tier: a held trade must never lose its history
        # because of a feed-routing setting. With no deep list configured every
        # instrument is DEEP, so this is a no-op on an unconfigured install.
        deep_capture = tiers.is_deep(self._instrument) or bool(in_pos)

        # persist real captured history for the backtest / self-learning
        if settings.store_history and candles:
            # The recent TAIL, not just the newest bar: an instrument is only
            # re-processed when the scan reaches it, so storing one bar per pass
            # lost every minute in between (38% of Crude's one-minute history in
            # the recorded session). Re-writing an already-stored bar is a no-op.
            storage.store.record_candles(self._instrument, candles[-_CANDLE_TAIL:])
            if deep_capture:
                storage.store.record_chain(self._instrument, now, chain)

        # Auto-capture each COMPLETED bar (futures + option chain) into the
        # research store so the backtest/analytics run on REAL history with no
        # manual step. Gated to a REAL broker feed (not the simulator) so the
        # research DB is never polluted with synthetic bars, and deduped so we
        # record a bar once (when it finalises). Paper mode is fine — this only
        # records data, it never places an order.
        real_feed = settings.data_provider.lower() not in ("simulated", "sim", "demo")
        if settings.research_autocapture and real_feed and deep_capture and len(candles) >= 2:
            completed = candles[-2]
            ts = int(completed.time)
            if ts != self._last_capture_ts:
                try:
                    research_capture.record_live_snapshot(
                        self._instrument, completed, chain
                    )
                    self._last_capture_ts = ts
                except Exception as exc:  # never let capture break a live tick
                    self._capture_error = str(exc)

        # Stabilise the recommendation so it is concrete/actionable, not flickering.
        decision = self._stabilize(raw_decision, in_pos, now)
        self._annotate_averaging(decision, in_pos)
        if not in_pos:
            self._block_closed_market(decision, now)

        # Risk Management v2 (feature-flagged, reversible): refine the premium stop
        # with a Greeks-aware model and run the Pre-Trade Validator. Runs AFTER the
        # frozen engine and only touches the stop + validation layer — a rejected
        # BUY is downgraded to AVOID with reasons; entry/confidence stay frozen.
        if settings.risk_v2_enabled and not in_pos:
            self._apply_risk_v2(decision, chain)

        # Phase 3.6 Execution Intelligence (feature-flagged, ADVISORY): assess
        # entry timing, strike resilience and trade survival AFTER Risk v2. It
        # only attaches an advisory recommendation — it never changes the signal,
        # confidence, or strategy. Skipped unless there is still a fresh BUY.
        if (
            settings.execution_intelligence_enabled
            and not in_pos
            and decision.signal == Signal.BUY
            and decision.recommended_option
        ):
            self._apply_execution_intelligence(decision, chain, snap_ind)

        # Early Momentum Advisory Engine (feature-flagged, ADVISORY, PARALLEL):
        # a SECOND, independent engine that flags the early stage of a new trend
        # (earlier than the confirmation BUY) and measures how much of the move
        # the confirmation BUY missed. It NEVER changes the frozen engine's
        # signal, confidence, targets, or strategy — it only attaches advice.
        # Runs on EVERY tick while the flag is on (even while a position is open)
        # so the Momentum board/screener are always populated — otherwise the
        # board intermittently fell back to "engine is off" whenever a paper
        # position was open. Advisory + independent, so it is safe in-position.
        if settings.early_momentum_enabled:
            self._apply_early_momentum(decision, candles, chain, snap_ind, price, now)

        # Early-Early Mode (Stage 2.5) — a SEPARATE feature-flagged add-on. Fully
        # isolated: it reads market data, produces its own advisory, and touches
        # neither the frozen engine nor the Early Momentum result above.
        if settings.early_early_enabled:
            self._apply_early_early(decision, candles, chain, snap_ind, price, now)

        # Quick Scalp Engine — a SEPARATE feature-flagged add-on. Fully isolated:
        # it reads market data, produces its own short-duration scalp advisory,
        # and touches none of the engines above.
        if settings.scalp_enabled:
            self._apply_scalp(decision, candles, chain, snap_ind, price, now)

        # Flow Engine (Candle-Flow) — a SEPARATE feature-flagged add-on with its
        # own dashboard tab. Fully isolated: it reads the 1-min candle flow,
        # produces its own BUY/HOLD/EXIT/SWITCH advisory, and touches none of the
        # engines above.
        if settings.flow_enabled:
            self._apply_flow(decision, candles, chain, snap_ind, price, now)

        # Premium-SELLING engine (defined-risk credit spreads) — PAPER ONLY, own
        # tab, no order path of any kind. Isolated like the engines above: it
        # reads market data and produces its own proposal, nothing more.
        if settings.credit_spread_enabled:
            spec = REGISTRY.get(self._instrument)
            decision.credit_spread = credit_spread.detect(
                candles,
                price,
                spec.strike_step if spec else 50.0,
                market_status,
                decision.htf_trend,
                settings.minutes_to_close(now),
            )

        # FUTURES paper tool — a SEPARATE tool with its own board, own capital pot
        # and point-based risk. Fully isolated: it shares no sizing, stop or exit
        # logic with the option engine above and has NO order path of any kind.
        if settings.futures_paper_enabled:
            decision.futures = futures_paper.tick(
                self._instrument,
                candles,
                price,
                snap_ind.atr,
                market_status,
                snap_ind.support,
                snap_ind.resistance,
                settings.minutes_to_close(now),
                int(now),
            )

        # FUTURES research signal (Phase 11A ext., Part 23) — its own vehicle,
        # its own validation, its own dashboard card. RESEARCH ONLY: it is not an
        # option signal with the strike removed, it never replaces the option
        # call, and it has no order route of any kind. Published for comparison
        # against the option plan on the same market event.
        # Phase 12A §1-§3: the futures plan reads the FRESHEST series — REST
        # history extended by the ticks already on the socket — and its age comes
        # from that series. The production option engine keeps reading ``candles``
        # above, unchanged, so no BUY is computed from anything new. The 90s
        # freshness refusal inside futures_signal is untouched.
        fut_candles = self.provider.futures_candles_live(300) or candles
        fut_trace = self.provider.futures_feed_trace()
        futures_feed_audit.observe(self._instrument, fut_trace, now)
        self._futures_signal = futures_signal.evaluate(
            self._instrument,
            fut_candles,
            price,
            snap_ind,
            market_status,
            contract=self.provider.futures_contract(),
            chain=self.provider.futures_contract_chain(),
            feed_age_sec=(now - fut_candles[-1].time) if fut_candles else None,
            feed_source=str(fut_trace.get("source") or "") or None,
            minutes_to_close=settings.minutes_to_close(now),
            now=now,
        )
        signal_visibility.observe_futures(self._instrument, self._futures_signal, now)
        signal_journal.observe_futures(self._instrument, self._futures_signal, now)
        # Phase 12 §12 — measure what those research plans actually did, in index
        # points. Observation only: this call cannot place, size for or propose an
        # order, and the futures paper tool above is a separate module.
        futures_outcomes.observe(self._instrument, self._futures_signal, price, now)
        # Phase 19 §6 — the same plan, given a costed paper lifecycle: entry at an
        # executable side, exit at the opposite one, resolved to T1/T2/T3, stop,
        # timeout or the close. A second observer of a plan that already exists;
        # it does not evaluate, change, size or gate the futures signal, has no
        # order route of any kind, and swallows its own failures.
        self._observe_phase19_futures(
            fut_candles[-1].close if fut_candles else price,
            (now - fut_candles[-1].time) if fut_candles else None,
            market_status,
            now,
        )

        # Validation Mode: remember the current decision context so buy() can
        # freeze it at entry (and the performance module can analyse it later).
        self._last_decision = decision
        self._last_status = market_status
        self._last_indicators = snap_ind

        if settings.shadow_mode:
            self._shadow_step(now, price, decision)

        # Signal identity + visibility (Phase 11A ext.). Runs after every layer
        # that can still change the decision, so the ids and the recorded stages
        # describe the call that is actually published. It stamps
        # global_signal_id/episode_id onto the decision and records generation,
        # classification, the plan, plan validation and publication — so a call
        # that never reaches the dashboard has a stage and a reason instead of
        # simply being absent. Measurement only.
        signal_visibility.observe_option(
            self._instrument, decision, signal_journal.board_key(decision), now
        )

        # Observe + log the frozen engine's BUY signals and resolve them against
        # real option prices (Target / Stop). Pure observability — no orders.
        signal_tracker.observe(self._instrument, decision, chain, now)

        # Signal Board Journal — write down what the board actually said on this
        # tick (BUY, WAIT or NO_TRADE), with the market state and gate trace it
        # was computed on, then follow each recorded BUY to a resolution.
        # Measurement only: it runs after the decision exists, it writes files
        # nothing in the trading path reads, and it places no order.
        exp_iso, mins_to_exp = self._expiry_context(now)
        # §15 — the same labels, on the card. Display only: it runs after the
        # decision is final and nothing downstream reads what it writes.
        signal_journal.attach_badges(
            self._instrument, decision, chain, snap_ind, price, now
        )
        signal_journal.observe(
            self._instrument, decision, chain, snap_ind, price, market_status,
            candles, now, expiry=exp_iso, minutes_to_expiry=mins_to_exp,
        )

        # Phase 17 vehicle evidence. Runs on the SAME chain object the decision
        # was computed on, at the decision instant, which is the whole point: the
        # previous attempt polled separately and matched 48 of 3,448 legs. It
        # returns an id nothing here reads, it places no order, and it swallows
        # its own failures (counted in its health endpoint) so a research bug
        # cannot break a tick.
        self._observe_phase17(
            decision, chain, price, snap_ind, market_status, candles,
            now, exp_iso, mins_to_exp,
        )

        # Phase 18 — the closing auction window, 15:10-15:30 IST. A second
        # observer of the same tick, and nothing more: it is paper-only by
        # construction, it reads no production field it could change, and the
        # value it returns is an id kept for logging. Outside the window it does
        # nothing at all.
        self._observe_phase18(
            chain, price, snap_ind, market_status, candles,
            now, exp_iso, mins_to_exp,
        )

        # Frozen pair capture. A fourth observer of the same tick, and the only
        # one whose configuration cannot change: it records this instrument's
        # futures book at THIS bar, pairs BANKNIFTY with NIFTY only when both
        # reported the same bar timestamp, and paper-trades the one frozen
        # relative-value rule. Capture and paper only — no order, no gate, and
        # its failures stay inside its own health counter.
        self._observe_pair_capture(candles, price, now)

        # Phase 22 — the CORE A+ PAPER board. A third observer of the same
        # tick: it grades this decision against the frozen pullback definition,
        # picks a vehicle only where a two-sided book exists, and keeps a paper
        # position. It places no order, returns nothing this tick reads and
        # swallows its own failures into its health endpoint.
        self._observe_phase22(decision, chain, price, market_status, candles)

        # Hold every WAIT to account: follow the leg the engine refused and
        # record what it did, tagged with the gate that refused it. Measurement
        # only — it cannot influence this or any later decision.
        self._missed = missed.update(
            self._instrument, decision, chain, now, self._missed
        )

        # Optional: WhatsApp alert on a high-confidence BUY (display/advisory only,
        # gated behind config + provider secrets; never places an order).
        self._notify_high_conf_signal(decision)

        # Optional: paper auto-trader (simulated fills only; hard-guarded off in
        # live mode). Auto-enters on a BUY >= gate, auto-exits at Target 1 / stop.
        was_in_pos = in_pos
        # Inside this block every refusal reported to the execution funnel is
        # attributed to THIS signal in the lifecycle ledger, so each gate keeps
        # one definition and one report instead of two that can disagree.
        with signal_visibility.execution_context(self._instrument, decision):
            self._auto_trade(decision, in_pos, now)
        self._save_live_mgmt()
        in_pos = bool(self.position.option_symbol)
        if in_pos and not was_in_pos:
            signal_visibility.mark_filled(
                self._instrument, decision,
                float(self.position.entry_premium or 0.0),
                int(self.position.quantity_lots or 0), now=now,
            )

        # Journal v2 (record only): session, signal outcome, stop/target changes.
        journal_hooks.after_tick(self, decision, chain, was_in_pos, now)
        # Parallel PAPER comparison books (ledger only; never the broker).
        paper_books.on_tick(self, decision, chain, now)

        # Phase 23 — the break-even hurdle shadow. It runs AFTER the auto-trader
        # has decided, records this BUY opportunity with the measured book, and
        # keeps three parallel arms (live engine, <=5%, <=3%). Record only: it
        # cannot refuse an order, it changes no gate, and the live auto-buy path
        # above is untouched.
        self._observe_phase23(
            decision, chain, now,
            engine_bought=bool(in_pos and not was_in_pos),
            in_position_before=was_in_pos,
        )

        # Shadow book — the SAME call, taken unconditionally in a separate
        # ungated paper ledger, with the gate that refused the gated book
        # recorded beside it. It runs after the gated path has had its turn, so
        # what it records is what actually happened rather than what would have.
        # Measurement only: its own capital pot, its own file, no order path, and
        # nothing in the trading path reads it back.
        shadow_book.observe(
            self._instrument, decision, chain, now,
            gated_taken=bool(
                in_pos and not was_in_pos
                and self.position.option_symbol == decision.recommended_option
            ),
            gated_in_position=was_in_pos,
            tick_started=now,
        )
        shadow_book.follow(self._instrument, chain, now)

        # Optional: zero-to-hero expiry sleeve (speculative, isolated budget).
        # Advisory always; auto-enters a tiny punt only when armed + slot free.
        z2h = self._zero_to_hero(decision, chain, price, in_pos, now)
        in_pos = bool(self.position.option_symbol)
        if not in_pos:
            self._z2h_is_sleeve = False

        recovery = self._recovery(decision, chain, pos_type, snap_ind, in_pos)
        self._raise_alerts(decision, snap_ind, candles, high_impact, in_pos)

        # watchlist follows the current directional bias
        want_call = decision.option_type == OptionType.CALL if decision.option_type else True
        wl = eng.watchlist(chain, price, want_call, snap_ind.atr or 0.004 * price)

        # selected option chart series
        sel_symbol = self.position.option_symbol or decision.recommended_option
        # Nothing recommended (WAIT/AVOID): follow the ATM option on the bias
        # side so the option price, its day H/L and chart are never blank.
        atm = self._atm_pair(chain, price)
        disp_symbol, disp_premium = None, None
        if not sel_symbol:
            bear = (decision.htf_trend or "").upper() in ("DOWN", "BEARISH")
            side = "pe" if bear else "ce"
            disp_symbol, disp_premium = atm[f"atm_{side}_symbol"], atm[f"atm_{side}_premium"]
        chart_symbol = sel_symbol or disp_symbol
        sel_candles = self.provider.option_candles(chart_symbol) if chart_symbol else []

        # Official exchange day OHLC (matches the broker app). Falls back to
        # None on feeds that can't supply it; the UI then derives from candles.
        fut_ohlc = self.provider.day_ohlc()
        opt_ohlc = self.provider.day_ohlc(chart_symbol) if chart_symbol else None
        # Day change is measured against the PREVIOUS SESSION CLOSE (like the
        # broker) when available, not the previous 1-min bar.
        prev = candles[-2].close if len(candles) > 1 else price
        if fut_ohlc and fut_ohlc.get("prev_close"):
            prev = fut_ohlc["prev_close"]
        is_open, session_note = settings.market_session(
            now, get_spec(self._instrument).exchange
        )
        # Feed label must reflect the ACTUAL data provider, not whether market
        # hours are ignored. Angel One only constructs successfully after a real
        # login (build_provider has no silent fallback), so provider == angelone
        # means a live feed is connected.
        feed_label = "LIVE Angel One feed" if settings.data_provider.lower() == "angelone" else "SIMULATED feed"
        if settings.hours_ignored():
            session_note = "Live session" if is_open else session_note
            is_open = True
        session_note = f"{session_note} · {feed_label}"
        if provider_note:
            # Feed can't serve this instrument yet — show the real reason instead
            # of a silently-blank dashboard, and mark the market not-open so the
            # UI reflects "no live data" rather than a stale/zero price.
            is_open = False
            session_note = f"{provider_note} · {feed_label}"
            self._add_alert("FEED", provider_note, "warning", cooldown=300.0)

        spec = get_spec(self._instrument)
        entry_hint = decision.current_premium or 0.0
        stop_hint = decision.stop_loss if decision.stop_loss is not None else entry_hint * 0.9
        risk_status = self.risk.status(now, entry_hint, stop_hint, spec.lot_size)
        # Advisory position-sizing hint on the signal itself (display-only; the
        # frozen engine's decision is already final at this point).
        if decision.signal in (Signal.BUY, Signal.HOLD) and entry_hint > 0:
            decision.suggested_lots = risk_status.suggested_lots
        learning = learn.summarize(self.journal)
        # Show the ACTIVE universe (post auto-pick / QT_INSTRUMENTS) in the
        # dropdown, not the full master, so the dashboard focuses on today's set.
        # Registry KEY, not the scrip name: "BAJAJ-AUTO" is keyed BAJAJAUTO, and
        # sending the scrip name back used to open the default (Crude) instead.
        instruments = [InstrumentOption(symbol=k, display=REGISTRY[k].display)
                       for k in _instruments.UNIVERSE if k in REGISTRY]

        # ---- Phase 3.1 Intelligence Layer (ADVISORY, read-only) ------------
        # Computed alongside the frozen engine from data it already produced;
        # never feeds back into decision-making. VIX/breadth come from a
        # background-refreshed cache (non-blocking here).
        vix_ctx, breadth_ctx = intel.cached_context()
        is_mcx = spec.exchange == "MCX"
        intelligence = Intelligence(
            india_vix=vix_ctx,
            breadth=breadth_ctx,
            max_pain=intel.max_pain(chain, price),
            pcr=intel.pcr_reading(chain),
            trade_quality=intel.trade_quality(
                signal=decision.signal.value,
                option_type=decision.option_type,
                htf_trend=decision.htf_trend,
                htf_strength=decision.htf_strength,
                adx=snap_ind.adx,
                volume_level=snap_ind.volume_level,
                volume_spike=snap_ind.volume_spike,
                oi_bias=snap_ind.oi_bias,
                institutional=snap_ind.institutional,
                smart_money=decision.smart_money,
                entry_range=decision.entry_range,
                stop_loss=decision.stop_loss,
                target1=decision.target1,
                atr_points=decision.atr_points,
                spot=price,
                market_status=market_status,
                now=now,
                is_mcx=is_mcx,
                vix=vix_ctx,
            ),
        )

        return Snapshot(
            time=now,
            underlying=spec.tv_symbol,
            instrument=spec.symbol,
            instrument_name=spec.display,
            instruments=instruments,
            tradingview_symbol=spec.tv_symbol,
            risk=risk_status,
            auto_trade=self._auto_trade_state(in_pos),
            zero_to_hero=z2h,
            learning=learning,
            futures_price=price,
            futures_change=round(price - prev, 1),
            futures_change_pct=round((price - prev) / prev * 100, 3) if prev else 0.0,
            futures_day_high=fut_ohlc.get("high") if fut_ohlc else None,
            futures_day_low=fut_ohlc.get("low") if fut_ohlc else None,
            futures_day_open=fut_ohlc.get("open") if fut_ohlc else None,
            futures_prev_close=fut_ohlc.get("prev_close") if fut_ohlc else None,
            option_day_high=opt_ohlc.get("high") if opt_ohlc else None,
            option_day_low=opt_ohlc.get("low") if opt_ohlc else None,
            **atm,
            display_option_symbol=disp_symbol,
            display_option_premium=disp_premium,
            market_open=is_open,
            session_note=session_note,
            data_source=settings.data_provider,
            feed_mode=self.provider.feed_mode(),
            feed_health=self._feed_health(),
            trade_mode="live" if self.is_live else "paper",
            can_trade_live=self.provider.supports_trading(),
            market_status=market_status,
            news_sentiment=news_sent,
            news_score=news_score,
            indicators=snap_ind,
            decision=decision,
            recovery=recovery,
            position=self.position,
            watchlist=wl,
            score_breakdown=breakdown,
            news=news_items,
            event_guard=EventGuard(**eg),
            alerts=self.alerts,
            intelligence=intelligence,
            selected_option_candles=sel_candles,
            futures_candles=candles[-240:],
            futures_signal=self._futures_signal,
        )

    # ---------- signal stability ----------
    def _stabilize(self, raw: Decision, in_pos: bool, now: float) -> Decision:
        """Keep the recommendation concrete: hold the committed signal until a
        different one is *confirmed* over a window, so it does not flicker every
        tick. Urgent EXIT/crash is allowed through immediately for safety."""
        urgent = raw.signal == Signal.EXIT and in_pos

        if self._committed is None:
            return self._commit(raw, now)

        if raw.signal == self._committed.signal:
            self._pending_signal = None
            return self._refresh(raw, now)

        if urgent:
            return self._commit(raw, now)

        # enforce a minimum on-screen time for the current signal
        if now - self._committed_at < settings.signal_min_hold_seconds:
            return self._refresh(raw, now)

        # require the new signal to persist for the confirmation window
        if self._pending_signal != raw.signal:
            self._pending_signal = raw.signal
            self._pending_since = now
        if now - self._pending_since >= settings.signal_confirm_seconds:
            return self._commit(raw, now)
        return self._refresh(raw, now)

    def _commit(self, raw: Decision, now: float) -> Decision:
        self._committed = raw.model_copy(deep=True)
        self._committed_at = now
        self._plan_at = now
        self._plan_version = 1
        return self._decorate(now)

    def _refresh(self, raw: Decision, now: float) -> Decision:
        """Signal unchanged: keep the frozen plan (entry/stop/targets/option) but
        refresh the live premium and lightweight, non-jittery fields.

        Holding the plan is what stops the levels flickering every tick, but the
        premium underneath it keeps moving: a target committed at 9.7 is not a
        target once the premium is 13.7. When the held plan can no longer be
        entered, this adopts the freshly-computed levels for the SAME leg, and if
        those are unusable too the call is marked stale rather than left on
        screen as an actionable BUY. Levels are never invented here — they are
        either the committed ones or this tick's engine output.
        """
        c = self._committed
        assert c is not None
        if c.recommended_option:
            q = next((x for x in self.provider.option_chain() if x.symbol == c.recommended_option), None)
            if q is not None:
                c.current_premium = q.premium
        if plan_validity.unusable(c.current_premium, c.stop_loss, c.target1) and (
            c.recommended_option
            and raw.recommended_option == c.recommended_option
            and not plan_validity.unusable(raw.current_premium, raw.stop_loss, raw.target1)
        ):
            # This tick produced a usable plan for the leg already on screen.
            c.entry_range = raw.entry_range
            c.stop_loss = raw.stop_loss
            c.target1, c.target2, c.target3 = raw.target1, raw.target2, raw.target3
            c.underlying_stop = raw.underlying_stop
            c.do_not_buy_above = raw.do_not_buy_above
            c.emergency_exit = raw.emergency_exit
            c.expected_holding_minutes = raw.expected_holding_minutes
            self._plan_at = now
            self._plan_version += 1
        c.recovery_probability = raw.recovery_probability
        # The position-independent market scan and the reversal/flip hints are
        # re-evaluated every tick, so keep them live even while the main signal
        # is held frozen (otherwise the BUY/WAIT chip and the flip alert stale).
        c.market_signal = raw.market_signal
        c.market_confidence = raw.market_confidence
        c.reversal = raw.reversal
        c.reversal_option = raw.reversal_option
        c.reversal_option_type = raw.reversal_option_type
        c.htf_trend = raw.htf_trend
        c.htf_strength = raw.htf_strength
        c.entry_trigger = raw.entry_trigger
        c.entry_triggers = list(raw.entry_triggers)
        if c.signal not in (Signal.BUY, Signal.HOLD, Signal.EXIT):
            # No plan is held on a stand-aside call, so its strike is display
            # only: follow this tick's pick, else one committed before the chain
            # loaded stays blank ("—") until the signal changes. The contract and
            # its premium follow too, so the card never pairs one strike's label
            # with another strike's price.
            c.strike, c.option_type = raw.strike, raw.option_type
            c.recommended_option, c.current_premium = raw.recommended_option, raw.current_premium
        return self._decorate(now)

    def _decorate(self, now: float) -> Decision:
        c = self._committed
        assert c is not None
        age = int(now - self._committed_at)
        c.signal_age_seconds = age
        c.locked = True
        c.next_review_seconds = max(0, int(settings.signal_min_hold_seconds - age))
        self._mark_plan(c, now)
        return c

    def _block_closed_market(self, c: Decision, now: float) -> None:
        """A real feed keeps streaming frozen quotes after the bell; never turn
        them into a fresh BUY (or an auto-entry) outside exchange hours."""
        if settings.hours_ignored():
            return
        is_open, note = settings.market_session(now, get_spec(self._instrument).exchange)
        if is_open or not (c.signal == Signal.BUY or c.market_signal == Signal.BUY):
            return
        if c.signal == Signal.BUY:
            c.signal = Signal.WAIT
        if c.market_signal == Signal.BUY:
            c.market_signal = Signal.WAIT
        c.reasons = [f"WAIT — {note}; no new entry outside market hours", *c.reasons][:6]

    def _mark_plan(self, c: Decision, now: float) -> None:
        """Stamp plan freshness on the decision and suppress an unenterable BUY.

        A held plan whose target is no longer above the live premium (or whose
        stop is no longer below it) is not a trade. It stays visible with its
        levels and its reason, but it stops being printed as BUY, and the
        suppression is recorded in the funnel so the call cannot disappear
        without an explanation.
        """
        c.plan_version = self._plan_version
        c.plan_age_seconds = max(0, int(now - self._plan_at)) if self._plan_at else 0
        c.plan_premium = c.current_premium
        had_levels = c.stop_loss is not None and c.target1 is not None
        called_buy = c.signal in (Signal.BUY, Signal.HOLD) or c.market_signal == Signal.BUY
        if not called_buy and not had_levels:
            # WAIT / AVOID / NO_TRADE: the engine is not offering a trade, so
            # there is no plan to judge. Say that rather than call it invalid.
            c.plan_state = "NO_PLAN"
            c.plan_invalid_reason = None
            c.plan_actionable = False
            return
        why = plan_validity.unusable(c.current_premium, c.stop_loss, c.target1)
        if why is None:
            c.plan_state = "REFRESHED" if self._plan_version > 1 else "FRESH"
            c.plan_invalid_reason = None
            c.plan_actionable = True
            return
        c.plan_invalid_reason = why
        c.plan_actionable = False
        # A plan that was usable when committed and is not any more is STALE; one
        # that never described an enterable trade is INVALID.
        c.plan_state = "STALE" if self._plan_version > 1 or had_levels else "INVALID"
        # Only a FRESH entry is suppressed. An open position keeps its own signal
        # (HOLD/EXIT) and its own managed stop — a premium that has run past the
        # printed target is normal there and is the exit layer's business.
        if self.position.option_symbol or not (
            c.signal == Signal.BUY or c.market_signal == Signal.BUY
        ):
            return
        blocker = "PLAN_STALE" if c.plan_state == "STALE" else "PLAN_INVALID"
        if c.signal == Signal.BUY:
            c.signal = Signal.WAIT
        if c.market_signal == Signal.BUY:
            c.market_signal = Signal.WAIT
        c.reasons = [
            f"WAIT — the printed plan cannot be entered at ₹{c.current_premium} "
            f"({plan_validity.describe(why)}); not showing this as a BUY",
            *c.reasons,
        ][:6]
        key = f"{c.recommended_option}|{blocker}|{self._plan_version}"
        if self._plan_block_key != key:
            self._plan_block_key = key
            exec_funnel.blocked(
                stage="VALIDATION", blocker=blocker,
                instrument=self._instrument, option=c.recommended_option,
                value=c.current_premium,
                threshold=(c.target1 if why == plan_validity.NO_UPSIDE
                           else c.stop_loss),
                where="state.py:_mark_plan",
                reason=plan_validity.describe(why),
            )

    def _shadow_step(self, now: int, price: float, decision: Decision) -> None:
        """Log the frozen engine's recommendation (NO order) once per candle and
        resolve any recommendation whose evaluation horizon has elapsed against
        the actual futures move. Never allowed to break the live tick loop."""
        try:
            from app.research import shadow as shadow_mod

            self._price_hist.append((now, price))
            if len(self._price_hist) > 20000:
                self._price_hist = self._price_hist[-20000:]

            bucket = now - (now % settings.candle_interval_seconds)
            actionable = {Signal.BUY, Signal.WAIT, Signal.AVOID, Signal.NO_TRADE}
            if bucket != self._shadow_last_bucket and decision.signal in actionable:
                self._shadow_last_bucket = bucket
                otype = decision.option_type.value if decision.option_type else None
                shadow_mod.log_recommendation(
                    self._instrument, bucket, decision.signal.value,
                    decision.confidence, otype, price,
                )

            hist = self._price_hist

            def price_at(ts: int):
                for t, p in hist:
                    if t >= ts:
                        return p
                return None

            shadow_mod.resolve_due(self._instrument, now, price_at)
        except Exception:
            # shadow logging is best-effort observability; never disrupt trading
            return

    def _notify_high_conf_signal(self, decision: Decision) -> None:
        """Fire a single, detailed WhatsApp alert when the frozen engine issues a
        BUY at/above the configured confidence gate. Best-effort and fully gated:
        does nothing unless WhatsApp is enabled AND provider secrets are present.
        Advisory only — it never places or influences an order."""
        if not notify.enabled():
            return
        if decision.signal != Signal.BUY or not decision.recommended_option:
            return
        if decision.confidence < settings.whatsapp_signal_min_confidence:
            return
        key = f"{decision.recommended_option}|{decision.option_type.value if decision.option_type else ''}"
        if self._wa_last_key == key:
            return
        self._wa_last_key = key
        spec = get_spec(self._instrument)
        side = decision.option_type.value if decision.option_type else "?"
        if decision.entry_range:
            entry = f"₹{decision.entry_range[0]:.1f} – ₹{decision.entry_range[1]:.1f}"
        elif decision.current_premium is not None:
            entry = f"₹{decision.current_premium:.1f}"
        else:
            entry = "—"
        tgts = " / ".join(
            f"₹{t:.1f}" for t in (decision.target1, decision.target2, decision.target3) if t is not None
        ) or "—"
        stop = f"₹{decision.stop_loss:.1f}" if decision.stop_loss is not None else "—"
        lines = [
            f"*Quantum Trader — {spec.display} BUY {side}*",
            f"Conviction: {decision.confidence:.0f}%",
            f"Strike: {decision.strike} {side}" if decision.strike is not None else f"Side: {side}",
            f"Entry zone: {entry}",
            f"Stop loss: {stop}",
            f"Targets: {tgts}",
        ]
        if decision.expected_holding_minutes is not None:
            lines.append(f"Hold: ~{decision.expected_holding_minutes} min")
        lines.append("Paper/advisory alert — place the order manually per your risk plan.")
        notify.push("\n".join(lines))

    def _live_squareoff_due(self, now: float) -> bool:
        """LIVE only: past the intraday square-off time of this exchange."""
        if settings.hours_ignored():
            return False
        exch = (get_spec(self._instrument).exchange or "").upper()
        raw = (settings.auto_trade_live_squareoff_mcx_ist if exch == "MCX"
               else settings.auto_trade_live_squareoff_equity_ist)
        try:
            h, m = (int(x) for x in str(raw).split(":"))
        except ValueError:
            return False
        return account_risk.ist_minutes(now) >= h * 60 + m

    def _live_entry_ask(self, opt: str | None) -> float:
        """Current ask of ``opt`` from the feed (0 when unknown). Read-only."""
        try:
            q = next((x for x in self.provider.option_chain() if x.symbol == opt), None)
        except Exception:
            q = None
        return float(getattr(q, "ask", 0) or 0)

    def _auto_live_armed(self) -> bool:
        """True only when the bot is fully armed to place REAL orders: live feed +
        auto-trader on + the explicit second live switch. Any one missing → paper."""
        return bool(self.is_live and settings.auto_trade_enabled and settings.auto_trade_allow_live)

    def _pullback_spread_and_risk_ok(self, decision: Decision, opt: str, now: float) -> bool:
        """SPREAD OK? and RISK ACCEPTABLE? of the PAPER pullback engine."""
        self._auto_entry_ask = 0.0
        try:
            q = next((x for x in self.provider.option_chain() if x.symbol == opt), None)
        except Exception:
            q = None
        bid = float(getattr(q, "bid", 0) or 0)
        ask = float(getattr(q, "ask", 0) or 0)
        spread = None
        if bid > 0 and ask >= bid:
            spread = (ask - bid) / ((ask + bid) / 2.0) * 100.0
            if spread > settings.auto_trade_max_spread_pct:
                self._auto_last_action = f"AUTO SKIP {opt}: spread {spread:.1f}% too wide"
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "VALIDATION", "SPREAD_TOO_WIDE", instrument=self._instrument, option=opt,
                    value=round(spread, 2), threshold=settings.auto_trade_max_spread_pct,
                    where="state.py:_pullback_spread_and_risk_ok", reason="bid/ask spread too wide",
                )
                return False
        entry = ask if ask > 0 else float(decision.current_premium or 0.0)
        stop = decision.stop_loss
        if entry <= 0 or stop is None or stop >= entry:
            exec_funnel.blocked(
                "RISK", "INVALID_STOP", instrument=self._instrument, option=opt,
                value=stop, where="state.py:_pullback_spread_and_risk_ok",
                reason="stop is missing or not below the entry price",
            )
            return False
        self._auto_entry_ask = ask
        sig = float(decision.current_premium or 0.0)
        chase_cap = settings.auto_trade_max_chase_pct
        if chase_cap > 0 and ask > 0 and 0 < stop < sig < ask:
            chase = (ask - sig) / (sig - stop) * 100.0
            if chase > chase_cap:
                self._auto_last_action = (
                    f"AUTO SKIP {opt}: ask {ask:.2f} is {chase:.0f}% of the stop above "
                    f"the signal {sig:.2f}")
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "RISK", "ENTRY_CHASE", instrument=self._instrument, option=opt,
                    value=round(chase, 1), threshold=chase_cap,
                    where="state.py:_pullback_spread_and_risk_ok",
                    reason="ask too far above the signal price for its stop",
                )
                return False
        risk_pct = (entry - stop) / entry * 100.0
        metals = {m.upper() for m in settings.auto_trade_metal_roots}
        if self._instrument.upper() in metals and settings.auto_trade_metal_risk_caps:
            caps = settings.auto_trade_metal_risk_caps
            cap = float(caps[int(now // 60) % len(caps)])
            label = f"metal cap {cap:g}%"
        else:
            cap = float(settings.auto_trade_max_structural_risk_pct)
            label = f"risk cap {cap:g}%"
        if cap > 0 and risk_pct > cap:
            self._auto_last_action = (
                f"AUTO SKIP {opt}: structural risk {risk_pct:.1f}% > {label}")
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "RISK", "STRUCTURAL_RISK", instrument=self._instrument, option=opt,
                value=round(risk_pct, 2), threshold=cap,
                where="state.py:_pullback_spread_and_risk_ok",
                reason=f"natural stop {risk_pct:.1f}% away exceeds the {label}",
            )
            return False
        self._auto_entry_note = (
            f"pullback engine: spread {spread:.1f}%" if spread is not None else "pullback engine: spread n/a"
        ) + f", structural risk {risk_pct:.1f}% within {label}"
        return True

    def _auto_trade(self, decision: Decision, in_pos: bool, now: int) -> None:
        """Auto-trader: auto-enter on a BUY at/above the gate and auto-exit on the
        ratcheting trailing stop / target / hard stop. Reuses buy()/sell() so every
        entry/exit flows through the same risk checks, journal and performance
        tracking as a manual order.

        Execution mode is decided by :meth:`_auto_live_armed`:
          * paper  → simulated fills, nothing hits the broker (default).
          * LIVE   → real Angel orders, but ONLY when trade_mode is live AND
                     auto_trade_allow_live is set (a deliberate second switch), and
                     each live entry is additionally bounded by a hard lots cap and
                     a per-order notional cap. If the live feed is up but the bot is
                     not armed for live, it stays completely hands-off."""
        # An open LIVE auto position keeps its exits running when the bot is
        # switched off or the kill switch disarms it; only new entries stop.
        manage_only = False
        if not settings.auto_trade_enabled or (self.is_live and not settings.auto_trade_allow_live):
            if not (self.is_live and in_pos and self._auto_entry_active):
                return
            manage_only = True
        live = self._auto_live_armed() or manage_only
        if in_pos and self._broker_closed():
            return  # closed in Angel: no exits on a position that no longer exists

        # --- manage an open auto-position ---
        # Two exit styles:
        #   ride_trail ON  → ratcheting TARGET LOCK: as each target is reached the
        #                    exit FLOOR steps up (T1 hit → floor T1, T2 hit → floor
        #                    T2, T3 hit → floor T3). Above T3 it keeps riding for
        #                    higher highs but can never give back below T3 — if it
        #                    stalls and falls back to a locked target it exits there.
        #                    A winner can never turn red once T1 is hit; below T1 the
        #                    hard stop still applies.
        #   ride_trail OFF → book fully at Target 1 or exit on the stop (prior logic).
        if in_pos:
            p = self.position
            prem = p.current_premium
            if prem is None:
                return
            ltp_now = prem
            if live and self._live_squareoff_due(now) and not self._carry_keep(p.option_symbol, now):
                self._pending_exit_reason = "SQUARE-OFF"
                self.sell(confirm=True)
                self._auto_last_action = f"AUTO EXIT {p.option_symbol} · LIVE SQUARE-OFF"
                self._auto_last_action_ts = now
                self._reset_auto_trade_marks()
                return
            if (settings.auto_trade_exit_on_bid
                    and (not live or settings.auto_trade_live_exit_on_bid)):
                bid = self._own_bid()
                if bid is not None:
                    prem = bid  # judge every exit on the price a sell fills at
            # Targets behave like a resting sell at the level: reached when the
            # last trade OR the bid gets there. Stops and trails stay on the bid.
            tgt_px = max(prem, ltp_now)
            if ((not live or settings.auto_trade_live_loss_budget)
                    and self._auto_entry_prem is not None):
                with contextlib.suppress(Exception):
                    acct = account_risk.account
                    equity = acct.day_pnl + sum(
                        o._liq_pnl() for o in registry.active() if o.position.option_symbol
                    )
                    if acct.must_flatten(equity):
                        self._pending_exit_reason = "PROFIT LOCK"
                        self.sell(confirm=live)
                        self._auto_last_action = (
                            f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · PROFIT LOCK "
                            f"(day ₹{equity:,.0f} fell to the lock ₹{acct.lock_level() or 0:,.0f})"
                        )
                        self._auto_last_action_ts = now
                        self._reset_auto_trade_marks()
                        return
            if self._stock_hold_active(live):
                tgt = self._auto_target2 or self._auto_target1
                entry = self._auto_entry_prem or p.entry_premium or 0.0
                cap = settings.auto_trade_stock_hold_max_loss_rupees
                qty = get_spec(self._instrument).lot_size * p.quantity_lots
                if tgt is not None and tgt_px >= tgt:
                    why = "STOCK HOLD TARGET"
                elif cap > 0 and entry > 0 and (entry - prem) * qty >= cap:
                    why = "STOCK HOLD MAX LOSS"
                else:
                    return
                self._pending_exit_reason = why
                self.sell(confirm=live)
                self._auto_last_action = f"AUTO EXIT {p.option_symbol} @ {tgt_px if why.endswith('TARGET') else prem:.1f} · {why}"
                self._auto_last_action_ts = now
                self._reset_auto_trade_marks()
                return
            if not settings.auto_trade_ride_trail:
                stop = max(self._auto_stop or 0.0, p.trailing_stop or 0.0)
                if self._auto_target1 is not None and prem >= self._auto_target1:
                    self._pending_exit_reason = "TARGET 1"
                    self.sell(confirm=live)
                    self._auto_last_action = f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · TARGET 1"
                    self._auto_last_action_ts = now
                    self._reset_auto_trade_marks()
                elif stop > 0 and prem <= stop and not (
                        stop == (self._auto_stop or 0.0) and self._no_stop_loss_exit()
                        and stop < (self._auto_entry_prem or p.entry_premium or 0.0)):
                    reason = "TRAIL STOP" if (p.trailing_stop and stop == p.trailing_stop) else "STOP LOSS"
                    self._pending_exit_reason = reason
                    self.sell(confirm=live)
                    self._auto_last_action = f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · {reason}"
                    self._auto_last_action_ts = now
                    self._reset_auto_trade_marks()
                return

            # --- ride_trail ON: ratcheting target lock ---
            t1, t2, t3 = self._auto_target1, self._auto_target2, self._auto_target3
            # A target locked on an EARLIER tick becomes the floor. Exit only when a
            # LATER tick falls back to that floor — reaching a target does NOT exit on
            # the same tick, so the trade can keep riding up toward the next target.
            prev_floor = self._auto_tgt_floor
            if prev_floor is not None and prem <= prev_floor:
                lvl = self._floor_level(prev_floor)
                if live and self._exch_stop and self._exch_stop.get("order_id"):
                    if self._poll_exch_stop(time.time(), min_gap=1.0):
                        self._auto_last_action = (f"AUTO EXIT {p.option_symbol} · {lvl} lock "
                                                  "filled at the exchange stop")
                        self._auto_last_action_ts = now
                        self._reset_auto_trade_marks()
                        return
                    if self._exch_stop:
                        since = self._exch_stop_breach_at or time.time()
                        self._exch_stop_breach_at = since
                        if time.time() - since < settings.live_exchange_trail_stop_fallback_sec:
                            return  # the resting order is working at the exchange
                self._pending_exit_reason = f"{lvl} lock"
                # A floor is hit on the way DOWN: it is a stop, filled at the bid.
                self.sell(confirm=live)
                self._auto_last_action = f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · {lvl} lock"
                self._auto_last_action_ts = now
                self._reset_auto_trade_marks()
                return
            # Step the locked floor UP as each target is reached (never steps down).
            # PAST T3 the floor keeps ratcheting in fixed premium steps so a big
            # run is not capped at T3 and cannot give the whole excess back: every
            # completed step above T3 becomes the new floor, and the exit fires on
            # the first tick that falls back to it (i.e. once the move rolls over).
            # Note this cannot exit AT the top — a trail only knows the peak has
            # passed after price comes off it, so one step is always given back.
            if t3 is not None and tgt_px >= t3:
                floor = t3
                if settings.auto_trade_beyond_t3_ratchet:
                    # Step sized as a % of the CURRENT premium so the giveback scales
                    # with the contract instead of being noise on a dear option and a
                    # huge gap on a cheap one.
                    step = max(
                        settings.auto_trade_beyond_t3_min_step,
                        prem * settings.auto_trade_beyond_t3_step_pct / 100.0,
                    )
                    if step > 0:
                        floor = t3 + int((prem - t3) // step) * step
                if settings.auto_trade_tiered_exit:
                    self._auto_peak = max(self._auto_peak or prem, prem)
                    runner = self._auto_peak * (1.0 - settings.tier_t3_runner_trail_pct / 100.0)
                    floor = max(floor, t3, runner)
                if self._auto_tgt_floor is None or floor > self._auto_tgt_floor:
                    self._auto_tgt_floor = floor
            elif t2 is not None and tgt_px >= t2:
                floor = t2
                if settings.auto_trade_tiered_exit and t1 is not None:
                    self._auto_peak = max(self._auto_peak or prem, prem)
                    floor = max(t2, self._auto_peak - (self._auto_peak - t1)
                                * settings.tier_t2_giveback_pct / 100.0)
                if self._auto_tgt_floor is None or floor > self._auto_tgt_floor:
                    self._auto_tgt_floor = floor
            elif t1 is not None and tgt_px >= t1:
                # Stage C (T1 -> T2): the runner's floor starts a buffer BELOW T1
                # (tier_t1_runner_buffer_pct of entry->T1), so a bid flicker at T1
                # no longer closes it seconds after the scale-out; then it trails
                # the peak and only ever moves up.
                entry_ref = self._auto_entry_prem or p.entry_premium or 0.0
                t1_floor = t1 - max(0.0, t1 - entry_ref) * settings.tier_t1_runner_buffer_pct / 100.0
                if (settings.auto_trade_tiered_exit and self._auto_tgt_floor is not None
                        and self._auto_tgt_floor >= t1_floor - 1e-9):
                    self._auto_peak = max(self._auto_peak or prem, prem)
                    f_c = self._auto_peak - (self._auto_peak - t1_floor) * settings.tier_t1_giveback_pct / 100.0
                    if f_c > self._auto_tgt_floor:
                        self._auto_tgt_floor = f_c
                if self._auto_tgt_floor is None or t1_floor > self._auto_tgt_floor + 1e-9:
                    self._auto_tgt_floor = t1_floor
                    # SCALE-OUT: bank part of the position at T1 and let the rest
                    # ride toward T2/T3. Walk-forward measured: NIFTY PF
                    # 1.098 -> 1.162, CRUDEOIL 1.143 -> 1.172.
                    if (
                        settings.auto_trade_scale_out
                        and not self._auto_scaled_out
                        and p.quantity_lots >= 2
                    ):
                        book = max(
                            1,
                            min(
                                p.quantity_lots - 1,
                                int(p.quantity_lots * settings.auto_trade_scale_out_frac),
                            ),
                        )
                        self._pending_exit_reason = "SCALE-OUT T1"
                        res = self.sell(confirm=live, lots=book,
                                        fill_px=(max(prem, t1) if not live else None))
                        if res.ok:
                            self._auto_scaled_out = True
                            self._auto_stop = max(
                                self._auto_stop or 0.0,
                                self._auto_entry_prem or p.entry_premium or 0.0,
                            )
                            self._auto_last_action = (
                                f"AUTO SCALE-OUT {book} lot(s) {p.option_symbol} @ "
                                f"{prem:.1f} · T1 — runner rides with stop at break-even"
                            )
                            self._auto_last_action_ts = now
                        return
            if live:
                self._sync_exch_stop(prem)
            # --- per-instrument TARGET POINTS profit-lock (dashboard) ---
            # Below T1, if the premium has been up by your configured points and
            # then ticks down (struggling to reach T1), exit there and bank the
            # profit — don't drift back to the stop. If it instead keeps climbing
            # to T1 the ratcheting ride model above takes over toward T2/T3.
            entry_prem = self._auto_entry_prem or p.entry_premium
            tgt_pts = self._early_target_points(entry_prem)
            if self._auto_tgt_floor is None and tgt_pts and tgt_pts > 0:
                entry = entry_prem
                if self._auto_peak is None or prem > self._auto_peak:
                    self._auto_peak = prem
                if (
                    entry is not None
                    and entry > 0
                    and self._auto_peak is not None
                    and self._auto_peak >= entry + float(tgt_pts)
                    and prem < self._auto_peak
                    and prem > entry
                ):
                    self._pending_exit_reason = "TARGET POINTS"
                    self.sell(confirm=live)
                    self._auto_last_action = (
                        f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · +{tgt_pts:g}pt "
                        f"target (high {self._auto_peak:.1f})"
                    )
                    self._auto_last_action_ts = now
                    self._reset_auto_trade_marks()
                    return
            # --- pre-T1 peak-giveback trail ---
            # While the trade is green but T1 not yet locked, don't just wait for
            # T1: track the running high and, once we're meaningfully advanced
            # toward T1, exit if price gives back a share of the run from that peak
            # (locks the high instead of drifting back to the hard stop).
            if (self._auto_tgt_floor is None and settings.auto_trade_pre_t1_trail
                    and p.quantity_lots >= max(1, settings.auto_trade_pre_t1_trail_min_lots)):
                entry = self._auto_entry_prem or p.entry_premium
                if self._auto_peak is None or prem > self._auto_peak:
                    self._auto_peak = prem
                # Quick-scalp: for a BIG position taken on a WEAK signal, don't hold
                # for T1 — once it's a few points green and rolls off the peak, bank
                # the small profit (protects size when conviction was low).
                if (
                    entry is not None
                    and entry > 0
                    and settings.auto_trade_quick_scalp_lots > 0
                    and self._auto_entry_lots >= settings.auto_trade_quick_scalp_lots
                    and (self._auto_entry_conf or 100.0) < settings.auto_trade_quick_scalp_conf
                    and self._auto_peak is not None
                    and self._auto_peak >= entry + settings.auto_trade_quick_scalp_points
                    and prem >= entry + settings.auto_trade_quick_scalp_points
                    and prem < self._auto_peak
                ):
                    self._pending_exit_reason = "QUICK SCALP"
                    self.sell(confirm=live)
                    self._auto_last_action = (
                        f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · quick scalp "
                        f"(+{prem - entry:.1f} pts, weak signal x{self._auto_entry_lots})"
                    )
                    self._auto_last_action_ts = now
                    self._reset_auto_trade_marks()
                    return
                if entry is not None and entry > 0:
                    peak = self._auto_peak
                    # Arm on EITHER a share of the way to T1 OR a small absolute
                    # profit above entry — the latter catches a modest green move
                    # that reverses before it ever approaches T1.
                    arm_by_t1 = (
                        t1 is not None
                        and t1 > entry
                        and peak is not None
                        and peak >= entry + (t1 - entry) * (settings.auto_trade_pre_t1_trail_arm_pct / 100.0)
                    )
                    arm_by_profit = (
                        peak is not None
                        and peak >= entry * (1.0 + settings.auto_trade_pre_t1_trail_arm_profit_pct / 100.0)
                    )
                    arm_rs = settings.auto_trade_pre_t1_trail_arm_rupees
                    qty = int(get_spec(self._instrument).lot_size or 0) * int(
                        self._auto_entry_lots or p.quantity_lots or 0
                    )
                    arm_by_rupees = (
                        arm_rs > 0 and peak is not None and qty > 0
                        and (peak - entry) * qty >= arm_rs
                    )
                    lock_pts = self._pre_t1_lock_points()
                    be = entry + self._exit_cost_per_unit(entry)
                    lock_px = be + lock_pts if lock_pts > 0 else None
                    locked = lock_px is not None and peak is not None and peak >= lock_px
                    mcx_hold = self._mcx_hold()
                    if mcx_hold:
                        # MCX: let small profits run; nothing arms before +arm pts.
                        lock_px = be + settings.mcx_hold_floor_points
                        locked = peak is not None and peak >= be + settings.mcx_hold_arm_points
                        arm_by_t1 = arm_by_profit = arm_by_rupees = False
                    if peak is not None and (locked or arm_by_t1 or arm_by_profit or arm_by_rupees):
                        # Once we've seen real profit, ratchet the hard stop up to
                        # break-even (after charges) so a green trade can't become a loss.
                        if settings.auto_trade_breakeven_after_arm:
                            self._auto_stop = max(self._auto_stop or 0.0, be)
                        if locked:
                            self._auto_stop = max(self._auto_stop or 0.0, lock_px)
                        base_floor = lock_px if locked else be
                        run = peak - entry
                        pct_floor = settings.auto_trade_pre_t1_trail_min_giveback_pct
                        floor = (
                            entry * pct_floor / 100.0
                            if pct_floor > 0
                            else settings.auto_trade_pre_t1_trail_min_giveback
                        )
                        giveback = max(
                            floor,
                            run * (settings.auto_trade_pre_t1_trail_giveback_pct / 100.0),
                        )
                        if mcx_hold:
                            giveback = max(giveback, settings.mcx_hold_min_giveback_points)
                        trail = max(peak - giveback, base_floor)
                        hit = prem <= trail and (locked or prem > entry)
                        halved = getattr(self, "_auto_pre_t1_halved", False)
                        if (hit and not halved
                                and settings.auto_trade_pre_t1_half_out
                                and p.quantity_lots >= 2):
                            # Bank half; the rest holds with the locked / break-even
                            # stop until T1 (price-path replay: early trails left
                            # T1 runs on the table).
                            book = p.quantity_lots // 2
                            self._pending_exit_reason = "PRE-T1 HALF OUT"
                            res = self.sell(confirm=live, lots=book)
                            if res.ok:
                                self._auto_pre_t1_halved = True
                                self._auto_stop = max(self._auto_stop or 0.0, base_floor)
                                self._auto_last_action = (
                                    f"AUTO HALF OUT {book} lot(s) {p.option_symbol} @ "
                                    f"{prem:.1f} · peak trail — rest holds to T1, stop at "
                                    f"{base_floor:.1f}")
                                self._auto_last_action_ts = now
                            return
                        if hit and not halved:
                            why = ("PRE-T1 PROFIT LOCK" if locked and trail <= base_floor
                                   else "PRE-T1 PEAK TRAIL")
                            self._pending_exit_reason = why
                            self.sell(confirm=live)
                            self._auto_last_action = (
                                f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · {why.lower()} "
                                f"(high {peak:.1f}, floor {trail:.1f})"
                            )
                            self._auto_last_action_ts = now
                            self._reset_auto_trade_marks()
                            return
            np_min = settings.auto_trade_no_progress_exit_min
            if (np_min > 0 and self._auto_tgt_floor is None and self._entry_time
                    and entry_prem and (self._auto_stop or 0.0) < entry_prem
                    and not getattr(self, "_auto_pre_t1_halved", False)
                    and now - self._entry_time >= np_min * 60):
                self._pending_exit_reason = "NO-PROGRESS EXIT"
                self.sell(confirm=live)
                self._auto_last_action = (
                    f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · no progress in "
                    f"{np_min:g} min (trail never armed)")
                self._auto_last_action_ts = now
                self._reset_auto_trade_marks()
                return
            # before any target is reached the hard stop still protects the trade
            if self._auto_tgt_floor is None and self._auto_stop and prem <= self._auto_stop:
                ent = self._auto_entry_prem or p.entry_premium or 0.0
                why = ("BREAK-EVEN STOP" if getattr(self, "_auto_pre_t1_halved", False)
                       or (ent > 0 and self._auto_stop >= ent) else "STOP LOSS")
                if why == "STOP LOSS" and self._no_stop_loss_exit():
                    return
                self._pending_exit_reason = why
                self.sell(confirm=live)
                self._auto_last_action = f"AUTO EXIT {p.option_symbol} @ {prem:.1f} · {why}"
                self._auto_last_action_ts = now
                self._reset_auto_trade_marks()
            return
        if manage_only:
            return

        # --- consider a fresh auto-entry ---
        actionable = (
            decision.signal == Signal.BUY
            and decision.recommended_option
            and decision.current_premium
            and decision.stop_loss is not None
            and decision.target1 is not None
        )
        key = f"{decision.recommended_option}|{decision.option_type.value if decision.option_type else ''}"
        if not actionable:
            # A BUY with no tradeable plan attached is a real refusal and is
            # recorded; anything that simply isn't a BUY is not a refusal at all.
            if decision.signal == Signal.BUY:
                exec_funnel.blocked(
                    "SIGNAL", "INCOMPLETE_PLAN", instrument=self._instrument,
                    option=decision.recommended_option or "",
                    where="state.py:_auto_trade",
                    reason="BUY without an option, premium, stop or target",
                )
            self._auto_last_key = None if not decision.recommended_option else self._auto_last_key
            return
        opt = decision.recommended_option or ""
        exec_funnel.reached("SIGNAL")
        # PAPER pullback engine: TRENDING -> PULLBACK -> quote -> spread -> risk -> BUY.
        eng = settings.auto_trade_pullback_engine and (
            not live or settings.auto_trade_live_pullback_engine)
        if live and self._live_squareoff_due(now):
            exec_funnel.blocked(
                "RISK", "LIVE_SQUAREOFF_TIME", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade", reason="past the live intraday square-off time",
            )
            return
        _only = {s.strip().upper() for s in (settings.auto_trade_instruments or "").split(",") if s.strip()}
        if _only and (self._instrument or "").upper() not in _only:
            self._auto_last_action = (
                f"AUTO SKIP {opt}: {self._instrument} not in auto-trade instruments "
                f"({','.join(sorted(_only))})")
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "SIGNAL", "INSTRUMENT_NOT_ALLOWED", instrument=self._instrument, option=opt,
                value=self._instrument, threshold=",".join(sorted(_only)),
                where="state.py:_auto_trade", reason="instrument not in QT_AUTO_TRADE_INSTRUMENTS",
            )
            return
        _mini = dict(p.split(":", 1) for p in (settings.auto_trade_metal_mini or "").upper()
                     .replace(" ", "").split(",") if ":" in p)
        if (self._instrument or "").upper() in _mini:
            m = _mini[(self._instrument or "").upper()]
            self._auto_last_action = f"AUTO SKIP {opt}: {self._instrument} is bought as {m} (mini)"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "SIGNAL", "TRADED_AS_MINI", instrument=self._instrument, option=opt, value=m,
                where="state.py:_auto_trade",
                reason=f"{self._instrument} auto-buys go to {m} (QT_AUTO_TRADE_METAL_MINI)",
            )
            return
        self._auto_entry_note = ""
        if settings.auto_trade_strategy_gate:
            trig = (decision.entry_trigger or "").upper()
            regime = (getattr(self, "_last_market_status", "") or "").upper()
            allowed_t = {t.upper() for t in settings.auto_trade_allowed_triggers}
            if live:  # v3.5.49: a blocked setup alone never buys live; with another allowed one it does
                allowed_t -= {t.upper() for t in settings.auto_trade_live_blocked_triggers}
            allowed_r = {r.upper() for r in settings.auto_trade_allowed_regimes}
            # A tick can satisfy several triggers; entry_trigger names only the
            # highest-priority one (PULLBACK outranks MOMENTUM/RETEST), so check all.
            trig_ok = trig in allowed_t or any(
                t.upper() in allowed_t for t in (decision.entry_triggers or []))
            if not trig_ok or regime not in allowed_r:
                blocker = "STRATEGY_TRIGGER" if not trig_ok else "STRATEGY_REGIME"
                self._auto_last_action = (
                    f"AUTO SKIP {opt}: strategy needs {'/'.join(sorted(allowed_t))} in "
                    f"{'/'.join(sorted(allowed_r))} (got {trig or 'none'} in {regime or 'unknown'})"
                )
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "SIGNAL", blocker, instrument=self._instrument, option=opt,
                    value=f"{trig or 'none'}/{regime or 'unknown'}",
                    threshold=f"{sorted(allowed_t)}/{sorted(allowed_r)}",
                    where="state.py:_auto_trade",
                    reason="not a pullback entry in a trending market",
                )
                return
        if decision.confidence < settings.auto_trade_min_confidence:
            exec_funnel.blocked(
                "RISK", "CONFIDENCE_FLOOR", instrument=self._instrument, option=opt,
                value=round(decision.confidence, 1),
                threshold=settings.auto_trade_min_confidence,
                where="state.py:_auto_trade",
                reason="confidence below the auto-entry floor",
            )
            return
        # Confidence CEILING (default 100 = off). Measured three sessions running,
        # the score inverts at the top end: 13 Aug, >=95% produced 8 trades, 2
        # wins, -Rs10,712, while <80% lost a tenth of that. The knob exists so
        # that can be acted on without silently rewriting the score.
        if not eng and decision.confidence > settings.auto_trade_max_confidence:
            self._auto_last_action = (
                f"AUTO SKIP {decision.recommended_option}: confidence "
                f"{decision.confidence:.0f}% is above the "
                f"{settings.auto_trade_max_confidence:g}% ceiling"
            )
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "RISK", "CONFIDENCE_CEILING", instrument=self._instrument, option=opt,
                value=round(decision.confidence, 1),
                threshold=settings.auto_trade_max_confidence,
                where="state.py:_auto_trade", reason="confidence above the ceiling",
            )
            return
        if self._auto_last_key == key:
            left = self._episode_rearm_wait(now)
            if left is not None and left <= 0:
                self._auto_last_key = None
                self._auto_key_traded_ts = None
        if self._auto_last_key == key:
            left = self._episode_rearm_wait(now)
            exec_funnel.blocked(
                "SIGNAL", "EPISODE_ALREADY_TRADED", instrument=self._instrument,
                option=opt, where="state.py:_auto_trade",
                reason=("this signal episode has already been auto-traded" if left is None
                        else f"same setup was traded and closed; re-arms in {int(left)}s"),
            )
            return  # already auto-traded this signal episode; wait for a new one
        retry_at = getattr(self, "_auto_unsent_retry_at", 0.0)
        if now < retry_at:
            exec_funnel.blocked(
                "SIGNAL", "ORDER_NOT_SENT_RETRY", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade",
                reason=f"last order never reached Angel; retrying in {int(retry_at - now)}s",
            )
            return
        exec_funnel.reached("RISK")

        # --- opening-window block: an option premium gaps THROUGH its stop in
        # the opening volatility, so the stop distance is not what is risked.
        # Measured 13 Aug: four entries in the first minutes exited at -47.8%,
        # -22.4%, -16.7% and -15.9% against an 8% stop — Rs6,997 of that day.
        open_block = account_risk.account.opening_window_block(
            now, account_risk.session_open_min(get_spec(self._instrument).exchange)
        )
        if open_block and not eng:
            self._auto_last_action = f"AUTO WAIT {self._instrument}: {open_block}"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "RISK", "OPENING_WINDOW", instrument=self._instrument, option=opt,
                threshold=settings.auto_trade_no_entry_open_minutes,
                where="state.py:_auto_trade", reason=open_block,
            )
            return

        # --- minimum premium: a very cheap contract cannot be risk-managed. Two
        # ticks on a ₹7 option is a ~28% move, so no stop is meaningful there and
        # a capital-based size buys an absurd number of lots. Journal evidence:
        # 10 lots of a ₹7.2 option lost 28% in 18 minutes.
        # --- execution eligibility: data, quote and contract metadata ---
        if not self._feed_ready():
            self._auto_last_action = f"AUTO SKIP {opt}: feed not ready (stale or disconnected)"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "VALIDATION", "FEED_NOT_READY", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade", reason="market data is stale or disconnected",
            )
            return
        if self._feed_health().source != "SIMULATED":
            try:
                q_now = next((x for x in self.provider.option_chain() if x.symbol == opt), None)
            except Exception:
                q_now = None
            bid = getattr(q_now, "bid", None)
            ask = getattr(q_now, "ask", None)
            if not bid or not ask or bid <= 0 or ask < bid:
                self._auto_last_action = f"AUTO SKIP {opt}: no executable two-sided quote"
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "VALIDATION", "NO_TWO_SIDED_QUOTE", instrument=self._instrument,
                    option=opt, value=f"bid={bid} ask={ask}",
                    where="state.py:_auto_trade", reason="no executable two-sided quote",
                )
                return
        try:
            lot_ok = int(get_spec(self._instrument).lot_size or 0) > 0
        except Exception:
            lot_ok = False
        if not lot_ok:
            self._auto_last_action = f"AUTO SKIP {opt}: contract lot size unknown"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "VALIDATION", "MISSING_METADATA", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade", reason="contract lot size unknown",
            )
            return
        if eng and not self._pullback_spread_and_risk_ok(decision, opt, now):
            return
        prem_now = float(decision.current_premium or 0.0)

        # --- stale feed guard: a premium that has not moved a single tick is not
        # a quote, it is a frozen feed. SILVER produced 9 signals in one session
        # with peak == entry == trough, one unchanged for 149 minutes.
        sym_now = decision.recommended_option or ""
        watch = self._prem_watch
        if watch is not None and watch[0] == sym_now and watch[1] == prem_now:
            self._prem_frozen_ticks += 1
        else:
            self._prem_frozen_ticks = 0
        self._prem_watch = (sym_now, prem_now)
        if self._prem_frozen_ticks >= settings.auto_trade_stale_feed_ticks:
            self._auto_last_action = (
                f"AUTO SKIP {sym_now}: premium frozen at {prem_now:.1f} for "
                f"{self._prem_frozen_ticks} ticks — stale feed"
            )
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "VALIDATION", "STALE_FEED", instrument=self._instrument, option=opt,
                value=self._prem_frozen_ticks,
                threshold=settings.auto_trade_stale_feed_ticks,
                where="state.py:_auto_trade", reason="premium frozen — not a live quote",
            )
            return

        if not eng and prem_now < settings.auto_trade_min_premium:
            self._auto_last_action = (
                f"AUTO SKIP {decision.recommended_option}: premium {prem_now:.1f} is "
                f"below the ₹{settings.auto_trade_min_premium:g} floor — too cheap to "
                f"stop out sanely"
            )
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "VALIDATION", "MIN_PREMIUM", instrument=self._instrument, option=opt,
                value=round(prem_now, 2), threshold=settings.auto_trade_min_premium,
                where="state.py:_auto_trade", reason="contract too cheap to stop out",
            )
            return

        # --- opportunity gate: the SAME test the signal board shows, so the bot
        # never buys a contract the board is calling too quiet to pay for.
        if not eng and settings.opportunity_gate_enabled:
            ind_now = self._last_indicators
            opp = opportunity.score(
                self._instrument,
                spot=float(decision.spot_price or 0.0),
                atr=float((ind_now.atr if ind_now else None) or 0.0),
                premium=prem_now,
                adx=(ind_now.adx if ind_now else None),
            )
            if not opp["tradeable"]:
                self._auto_last_action = (
                    f"AUTO SKIP {decision.recommended_option}: {opp['reason']}"
                )
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "VALIDATION", "OPPORTUNITY_GATE", instrument=self._instrument,
                    option=opt, where="state.py:_auto_trade", reason=str(opp["reason"]),
                )
                return

        # --- re-entry cooldown: after an exit, wait before taking the same
        # instrument again. Without it the bot laddered the same move (CRUDEOIL
        # 14 signals, SENSEX 6 in one session), paying entry+exit costs a rung.
        # The clock lives in the shared account governor, so it survives a
        # restart — an in-memory cooldown handed the bot a fresh ladder every
        # time the backend came back up mid-session.
        cool_block = account_risk.account.instrument_blocked(self._instrument, now)
        if cool_block and not eng:
            self._auto_last_action = f"AUTO WAIT {self._instrument}: {cool_block}"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "RISK", "INSTRUMENT_COOLDOWN", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade", reason=cool_block,
            )
            return

        # --- instrument allowlist: only trade what has a measured positive edge
        if (
            not eng
            and settings.instrument_allowlist
            and self._instrument not in settings.instrument_allowlist
        ):
            self._auto_last_action = f"AUTO SKIP {self._instrument}: not in the allowlist"
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "RISK", "ALLOWLIST", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade", reason="instrument not in the allowlist",
            )
            return
        # --- time-of-day filter: block the hours whose measured profit factor is
        # below 1 for this instrument (walk-forward validated; see config).
        blocked = settings.blocked_entry_hours.get(self._instrument) or []
        if blocked and not eng:
            hour_ist = time.gmtime(now + 19800).tm_hour
            if hour_ist in blocked:
                self._auto_last_action = (
                    f"AUTO SKIP {self._instrument}: {hour_ist:02d}:00 IST is a "
                    f"measured negative-edge hour"
                )
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "RISK", "BLOCKED_HOUR", instrument=self._instrument, option=opt,
                    value=f"{hour_ist:02d}:00 IST", where="state.py:_auto_trade",
                    reason="measured negative-edge hour for this instrument",
                )
                return

        # --- A) momentum-trigger entry: only fire on a live trigger with premium not
        # falling, so we enter as momentum turns up rather than mid-run. If the engine
        # says "wait for a pullback" we do NOT consume the episode — we can still enter
        # once the trigger actually fires.
        exec_funnel.reached("VALIDATION")
        if not eng and settings.auto_trade_momentum_entry:
            trig = (decision.entry_trigger or "").upper()
            if trig in ("WAIT_PULLBACK", "NONE", ""):
                self._auto_last_action = (
                    f"AUTO WAIT {decision.recommended_option}: waiting for entry trigger "
                    f"({trig or 'none'})"
                )
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "EXECUTION", "MOMENTUM_TRIGGER", instrument=self._instrument,
                    option=opt, value=trig or "none", threshold="a live trigger",
                    where="state.py:_auto_trade", reason="no live entry trigger",
                )
                return
            if decision.premium_momentum is not None and decision.premium_momentum < 0:
                self._auto_last_action = (
                    f"AUTO WAIT {decision.recommended_option}: premium momentum falling"
                )
                self._auto_last_action_ts = now
                exec_funnel.blocked(
                    "EXECUTION", "PREMIUM_MOMENTUM", instrument=self._instrument,
                    option=opt, value=round(decision.premium_momentum, 3), threshold=0,
                    where="state.py:_auto_trade", reason="premium momentum falling",
                )
                return

        # --- B) no-chase filter: skip an already-extended / trap-like move so we don't
        # buy the top. Uses the engine's own buy-trap probability + premium health.
        if not eng and settings.auto_trade_no_chase:
            if (
                decision.buy_trap_prob >= settings.auto_trade_no_chase_trap_prob
                or (decision.premium_health or "").upper() == "DANGEROUS"
            ):
                self._auto_last_action = (
                    f"AUTO SKIP {decision.recommended_option}: no-chase "
                    f"(trap {decision.buy_trap_prob:.0f}%/{decision.premium_health or '-'})"
                )
                self._auto_last_action_ts = now
                self._auto_last_key = key
                exec_funnel.blocked(
                    "EXECUTION", "NO_CHASE", instrument=self._instrument, option=opt,
                    value=round(decision.buy_trap_prob, 1),
                    threshold=settings.auto_trade_no_chase_trap_prob,
                    where="state.py:_auto_trade",
                    reason=f"trap probability / premium health {decision.premium_health or '-'}",
                )
                return

        # RISK_GATE: the pullback engine is gated like every other entry —
        # daily loss, daily profit target, giveback from the day's equity peak,
        # trade cap, loss streak, max concurrent and the manual kill switch.
        with contextlib.suppress(Exception):
            account_risk.account.note_equity(
                now, sum(o._liq_pnl() for o in registry.active()
                         if o.position.option_symbol))
        can, _reason = self.risk.can_open(time.time())
        if can and live and live_control.kill_switch_engaged():
            can, _reason = False, "kill switch engaged (live)"
        if not can:
            exec_funnel.blocked(
                "RISK", "RISK_GOVERNOR", instrument=self._instrument, option=opt,
                where="state.py:_auto_trade -> risk.can_open", reason=_reason,
            )
            return  # daily-loss / trade-count / consecutive-loss / profit-goal caps hit

        # --- multi-instrument concurrency guard ---
        # Each instrument keeps its own single slot; across instruments we allow up to
        # auto_trade_max_concurrent OPEN positions at once (the GLOBAL capital ceiling
        # is enforced after sizing, below).
        open_positions = 0
        deployed = 0.0
        deployed_self = 0.0
        for other in registry.active():
            op = other.position
            if not op.option_symbol:
                continue
            open_positions += 1
            try:
                ls = get_spec(other.instrument).lot_size
            except Exception:
                ls = 0
            val = (op.entry_premium or 0.0) * ls * op.quantity_lots
            deployed += val
            if other.instrument == self._instrument:
                deployed_self += val
        if open_positions >= max(1, settings.auto_trade_max_concurrent):
            self._auto_last_action = (
                f"AUTO SKIP {decision.recommended_option}: max concurrent positions "
                f"({open_positions}/{settings.auto_trade_max_concurrent})"
            )
            self._auto_last_action_ts = now
            exec_funnel.blocked(
                "EXECUTION", "MAX_CONCURRENT", instrument=self._instrument, option=opt,
                value=open_positions, threshold=settings.auto_trade_max_concurrent,
                where="state.py:_auto_trade", reason="all position slots are in use",
            )
            return

        # Safe (risk-based) sizing: size so one stop loses only risk_per_trade_pct
        # of capital, then never exceed the hard max_lots cap. Falls back to
        # max_lots when safe sizing is off or the stop distance is unknown.
        lot_size = get_spec(self._instrument).lot_size
        # Per-instrument overrides (from the Auto-Bot panel) take priority over the
        # global values: a fixed lot count and its own capital budget.
        inst_lots = settings.auto_trade_instrument_lots.get(self._instrument)
        inst_capital = settings.auto_trade_instrument_capital.get(self._instrument)
        use_balance = bool(live and settings.auto_trade_use_live_balance)
        if inst_lots is not None and inst_lots >= 1:
            # Hard fixed lots for this instrument (still clamped by live caps + capital below).
            lots = int(inst_lots)
        elif use_balance:
            # One-click LIVE auto-buy: don't ask the user for lots. Let the
            # confidence-scaled balance budget (computed below) drive the size —
            # start from the live cap so the capital guard trims it to what the
            # budget affords (higher confidence → larger budget → more lots).
            lots = max(1, settings.auto_trade_live_max_lots)
        else:
            # A 1-lot cap makes contract size, not choice, decide the rupee risk:
            # one CRUDEOIL option lot deploys ~₹27,000 against ~₹2,000 for a
            # SENSEX lot, so a 4.2% SENSEX win paid ₹42 in the same session that
            # a 4.3% CRUDEOIL loss cost ₹1,200. Balanced sizing lifts the cap and
            # lets the risk sizer equalise the rupees; the capital guard below
            # still trims it.
            cap = max(1, settings.auto_trade_max_lots)
            if settings.auto_trade_risk_balanced_lots and settings.auto_trade_safe_sizing:
                cap = max(cap, settings.auto_trade_balanced_max_lots)
            if settings.auto_trade_safe_sizing and decision.current_premium and decision.stop_loss is not None:
                # Size against the stop that will actually be applied after fill: a
                # per-instrument fixed-point stop overrides the decision stop there,
                # so use whichever is wider to keep a stop-out within the risk budget.
                sizing_stop = decision.stop_loss
                fixed_pts = settings.auto_trade_stop_points.get(self._instrument)
                if fixed_pts and fixed_pts > 0:
                    sizing_stop = min(sizing_stop, max(0.05, decision.current_premium - float(fixed_pts)))
                safe = self.risk.suggested_lots(decision.current_premium, sizing_stop, lot_size)
                lots = max(1, min(cap, safe))
            else:
                lots = cap

        # LIVE safeguards: a hard lots cap + a per-order notional ceiling so a single
        # real order can never exceed the configured blast radius.
        live_cap_sized = bool(live and eng and settings.auto_trade_live_capital_sizing)
        if live and not live_cap_sized:
            lots = max(1, min(lots, settings.auto_trade_live_max_lots))
            order_value = (decision.current_premium or 0.0) * lot_size * lots
            if order_value > settings.auto_trade_live_max_order_value:
                self._auto_last_action = (
                    f"AUTO SKIP {decision.recommended_option}: order ₹{order_value:,.0f} "
                    f"> live cap ₹{settings.auto_trade_live_max_order_value:,.0f}"
                )
                self._auto_last_action_ts = now
                self._auto_last_key = key
                exec_funnel.blocked(
                    "EXECUTION", "LIVE_ORDER_VALUE_CAP", instrument=self._instrument,
                    option=opt, value=round(order_value, 0),
                    threshold=settings.auto_trade_live_max_order_value,
                    where="state.py:_auto_trade", reason="order above the live blast-radius cap",
                )
                return

        # GLOBAL capital guard: this new order's cost + capital already deployed in
        # other open positions must not exceed settings.capital. Trim lots to fit;
        # skip only if even one lot can't fit the remaining capital.
        # When this instrument has its own capital budget, gate it against THAT
        # budget vs. only its own deployed capital (one slot per instrument, so 0
        # when flat). Otherwise use the global capital vs. all-instrument deployed.
        cost_per_lot = (decision.current_premium or 0.0) * lot_size
        if inst_capital is not None and inst_capital > 0:
            budget = min(inst_capital, settings.capital)
            used = deployed_self
        else:
            budget = settings.capital
            used = deployed
            # One-click LIVE auto-buy: size from the broker's real available cash
            # instead of the fixed capital figure (cached, read-only), scaled by
            # the signal's CONFIDENCE — higher confidence deploys a larger share
            # of the balance (more size for stronger signals). Falls back to the
            # configured capital if the balance can't be read.
            if use_balance:
                try:
                    from app.market import angelone

                    live_cash = angelone.available_cash()
                    if live_cash and live_cash > 0:
                        minc = settings.auto_trade_min_confidence
                        span = max(1.0, 100.0 - minc)
                        conf_norm = min(1.0, max(0.0, (decision.confidence - minc) / span))
                        # Deploy 50% of balance at the confidence gate, ramping to
                        # 100% at 100% confidence.
                        frac = 0.5 + 0.5 * conf_norm
                        # Balance may only shrink the budget, never exceed capital.
                        budget = min(settings.capital, live_cash * frac)
                except Exception:
                    pass
        if cost_per_lot > 0 and budget > 0:
            remaining = max(0.0, budget - used)
            affordable = int(remaining // cost_per_lot)
            if affordable < 1:
                self._auto_last_action = (
                    f"AUTO SKIP {decision.recommended_option}: capital used "
                    f"₹{used:,.0f}/₹{budget:,.0f} — no room for a lot"
                )
                self._auto_last_action_ts = now
                self._auto_last_key = key
                exec_funnel.blocked(
                    "EXECUTION", "CAPITAL", instrument=self._instrument, option=opt,
                    value=round(cost_per_lot, 0), threshold=round(remaining, 0),
                    where="state.py:_auto_trade",
                    reason="not enough free capital for a single lot",
                )
                return
            if eng and (settings.auto_trade_capital_sizing if not live else live_cap_sized):
                # As many lots as the free capital pays for (LIVE: QT_CAPITAL).
                lots = affordable
            elif affordable < lots:
                lots = affordable

        # LOSS BUDGET (paper): the stop-out of this trade may not exceed the
        # per-trade rupee cap, nor (once the day lock is armed) its share of the
        # room above the lock — a single trade can't give back the locked day.
        live_ask = 0.0
        if live and settings.auto_trade_live_loss_budget and lots >= 1 and not eng:
            live_ask = self._live_entry_ask(opt)
            sig = float(decision.current_premium or 0.0)
            stop = float(decision.stop_loss) if decision.stop_loss is not None else 0.0
            chase_cap = settings.auto_trade_max_chase_pct
            if chase_cap > 0 and live_ask > 0 and 0 < stop < sig < live_ask:
                chase = (live_ask - sig) / (sig - stop) * 100.0
                if chase > chase_cap:
                    self._auto_last_action = (
                        f"AUTO SKIP {opt}: ask {live_ask:.2f} is {chase:.0f}% of the stop "
                        f"above the signal {sig:.2f}")
                    self._auto_last_action_ts = now
                    self._auto_last_key = key
                    exec_funnel.blocked(
                        "RISK", "ENTRY_CHASE", instrument=self._instrument, option=opt,
                        value=round(chase, 1), threshold=chase_cap,
                        where="state.py:_auto_trade", reason="live ask too far above the signal",
                    )
                    return
        if ((not live or settings.auto_trade_live_loss_budget)
                and lots >= 1 and decision.stop_loss is not None):
            acct = account_risk.account
            equity = acct.day_pnl + sum(
                o._liq_pnl() for o in registry.active() if o.position.option_symbol)
            budget = account_risk.entry_loss_budget(
                acct.day_peak_equity, equity,
                per_trade_cap=settings.auto_trade_max_loss_rupees,
                lock=acct.lock_level())
            # Size on the price a buy fills at (the ask), not the signal premium.
            entry_est = max(float(decision.current_premium or 0.0),
                            float(getattr(self, "_auto_entry_ask", 0.0) or 0.0) if eng else 0.0,
                            live_ask)
            stop_est = float(decision.stop_loss)
            stop_pts = settings.auto_trade_stop_points.get(self._instrument)
            if stop_pts and stop_pts > 0:
                stop_est = entry_est - float(stop_pts)
            elif settings.auto_trade_max_stop_pct > 0:
                stop_est = max(stop_est, entry_est * (1.0 - settings.auto_trade_max_stop_pct / 100.0))
            if budget is not None and entry_est > 0:
                fit = account_risk.lots_for_budget(budget, entry_est, stop_est, lot_size)
                if fit < 1:
                    self._auto_last_action = (
                        f"AUTO SKIP {decision.recommended_option}: 1 lot risks more than "
                        f"the loss budget ₹{budget:,.0f}"
                    )
                    self._auto_last_action_ts = now
                    self._auto_last_key = key
                    exec_funnel.blocked(
                        "RISK", "LOSS_BUDGET", instrument=self._instrument, option=opt,
                        value=round((entry_est - stop_est) * lot_size, 0),
                        threshold=round(budget, 0), where="state.py:_auto_trade",
                        reason="one lot's stop-out exceeds the per-trade cap / daily room / lock room",
                    )
                    return
                if fit < lots:
                    lots = fit
                    note = f"lots cut to {fit} by loss budget ₹{budget:,.0f}"
                    prev = getattr(self, "_auto_entry_note", "") or ""
                    self._auto_entry_note = f"{prev}; {note}" if prev else note

        if live_cap_sized and lots >= 1:
            px = max(float(decision.current_premium or 0.0),
                     float(getattr(self, "_auto_entry_ask", 0.0) or 0.0))
            per_lot = px * lot_size
            fit = int(settings.auto_trade_live_max_order_value // per_lot) if per_lot > 0 else 0
            if fit < 1:
                self._auto_last_action = (
                    f"AUTO SKIP {opt}: one lot ₹{per_lot:,.0f} > live order cap "
                    f"₹{settings.auto_trade_live_max_order_value:,.0f}")
                self._auto_last_action_ts = now
                self._auto_last_key = key
                exec_funnel.blocked(
                    "EXECUTION", "LIVE_ORDER_VALUE_CAP", instrument=self._instrument,
                    option=opt, value=round(per_lot, 0),
                    threshold=settings.auto_trade_live_max_order_value,
                    where="state.py:_auto_trade", reason="one lot above the live order cap",
                )
                return
            lots = min(lots, fit)

        if self._stock_hold_active(live):
            lots = 1
        exec_funnel.reached("EXECUTION")
        exec_funnel.reached("BROKER")
        res = self.buy(decision.recommended_option, lots=lots, confirm=live,
                       manual=False)
        self._auto_last_key = key
        self._auto_key_traded_ts = float(now) if res.ok else None
        prev_key, tries = getattr(self, "_auto_unsent", (None, 0))
        tries = tries if prev_key == key else 0
        if (not res.ok and res.status == "NOT_SENT" and not res.order_id
                and not self._pending_order and tries < settings.auto_trade_unsent_max_retries):
            # Nothing reached Angel: the still-valid signal may buy again shortly.
            self._auto_unsent = (key, tries + 1)
            self._auto_last_key = None
            self._auto_unsent_retry_at = float(now) + settings.auto_trade_unsent_retry_sec
        elif res.ok:
            self._auto_unsent = (None, 0)
        if not res.ok:
            exec_funnel.blocked(
                "ACCEPTED", "ORDER_REJECTED", instrument=self._instrument, option=opt,
                value=lots, where="state.py:buy", reason=res.message,
            )
        else:
            exec_funnel.reached("ACCEPTED")
            exec_funnel.reached("FILLED")
        if res.ok:
            self._auto_target1 = decision.target1
            self._auto_target2 = decision.target2
            self._auto_target3 = decision.target3
            self._auto_stop = decision.stop_loss
            if live and self._ledger_position is not None:
                self._ledger_position.update(t1=decision.target1, t2=decision.target2)
                self._persist_state()
            # Cap how far the hard stop may sit below entry (one-click Live
            # Auto-Buy sets this) so a single trade can't bleed to a distant stop.
            entry_fill = res.fill_premium
            # A per-instrument FIXED point stop (set in the dashboard) takes
            # precedence: hard stop = entry − points, for an exact, predictable
            # rupee risk. Falls back to the % cap when no points are configured.
            stop_pts = settings.auto_trade_stop_points.get(self._instrument)
            if stop_pts and stop_pts > 0 and entry_fill and entry_fill > 0:
                self._auto_stop = max(0.05, entry_fill - float(stop_pts))
            elif (
                settings.auto_trade_max_stop_pct > 0
                and entry_fill
                and entry_fill > 0
                and self._auto_stop is not None
            ):
                floor = entry_fill * (1.0 - settings.auto_trade_max_stop_pct / 100.0)
                if self._auto_stop < floor:
                    self._auto_stop = floor
            self._auto_tgt_floor = None
            self._auto_peak = res.fill_premium
            self._auto_entry_prem = res.fill_premium
            self._auto_entry_active = True
            self._auto_entry_strike = decision.strike
            self._auto_entry_conf = decision.confidence
            self._auto_entry_lots = lots
            tag = "LIVE BUY" if live else "AUTO BUY"
            self._auto_last_action = (
                f"{tag} {decision.recommended_option} x{lots} @ {res.fill_premium:.1f} "
                f"· conf {decision.confidence:.0f}%"
            )
            self._auto_last_action_ts = now

    def _no_stop_loss_exit(self) -> bool:
        """v3.5.47: this instrument's exchange has no stop-loss exit."""
        try:
            exch = get_spec(self._instrument).exchange.upper()
        except Exception:
            return False
        return exch in {e.upper() for e in settings.auto_trade_no_stop_loss_exchanges}

    def _stock_hold_active(self, live: bool) -> bool:
        """v3.5.32: LIVE single-stock option under the hold-to-T2 rule."""
        if not (live and settings.auto_trade_stock_hold_to_target):
            return False
        from app.analysis.instrument_family import INDEX_ROOTS
        try:
            spec = get_spec(self._instrument)
        except Exception:
            return False
        return spec.exchange in ("NFO", "BFO") and self._instrument.upper() not in INDEX_ROOTS

    def _buy_product(self) -> str | None:
        """v3.5.33: broker product for a new live buy (None = the default angel_product)."""
        if settings.auto_trade_stock_hold_carry_forward and self._stock_hold_active(True):
            return settings.angel_product_carry
        return None

    def _carry_keep(self, sym: str | None, now: float) -> bool:
        """v3.5.33: keep a CARRYFORWARD stock hold past the square-off, unless expiry is near
        (or unknown)."""
        led = self._ledger_position or {}
        if not (settings.auto_trade_stock_hold_carry_forward and sym and led.get("symbol") == sym
                and led.get("product") == settings.angel_product_carry):
            return False
        import datetime as _dt
        import re as _re
        m = _re.search(r"(\d{2})([A-Z]{3})(\d{2})\d+(?:\.\d+)?(?:CE|PE)$", sym.upper())
        try:
            exp = _dt.datetime.strptime("".join(m.groups()), "%d%b%y").date()
        except (AttributeError, ValueError):
            return False
        today = _dt.datetime.fromtimestamp(now, _dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()
        return (exp - today).days > settings.auto_trade_stock_hold_exit_days_before_expiry

    def _early_target_points(self, entry: float | None) -> float | None:
        """Premium points for the early profit-lock, floored at a minimum R.

        The dashboard field was in absolute premium points, which means a very
        different thing on a ₹262 contract than on a ₹25 one: journal evidence
        showed it banking +0.7% while the stop risked ~8%, so a 67% win rate
        still lost money. The percentage form is preferred where set, and either
        way the exit may not fire closer than ``auto_trade_min_target_r`` times
        the stop distance — it can no longer be configured into a losing ratio.
        """
        if entry is None or entry <= 0:
            return None
        pct = settings.auto_trade_target_pct.get(self._instrument)
        if pct and pct > 0:
            target = entry * float(pct) / 100.0
        else:
            pts = settings.auto_trade_target_points.get(self._instrument)
            if not pts or pts <= 0:
                return None
            target = float(pts)
        stop = self._auto_stop
        if stop is not None and 0 < stop < entry:
            target = max(target, (entry - stop) * settings.auto_trade_min_target_r)
        return target

    def _pre_t1_lock_points(self) -> float:
        by = settings.auto_trade_pre_t1_lock_points_by_instrument or {}
        if self._instrument in by:
            return max(0.0, float(by[self._instrument] or 0.0))
        base = settings.auto_trade_pre_t1_lock_points
        if base <= 0:
            return 0.0
        hq = settings.auto_trade_pre_t1_lock_high_qty
        if hq > 0 and int(get_spec(self._instrument).lot_size or 0) >= hq:
            return max(0.0, settings.auto_trade_pre_t1_lock_points_high_qty)
        return base

    def _option_atr(self, symbol: str | None) -> float | None:
        """The option's own 1-min ATR (premium points); None without enough candles."""
        n = max(2, int(settings.vol_atr_period or 14))
        if not symbol:
            return None
        try:
            cs = self.provider.option_candles(symbol, limit=n + 30) or []
        except Exception:
            return None
        cs = [c for c in cs if c.high and c.low and c.close and c.high >= c.low]
        if len(cs) < n + 1:
            return None
        tr = [max(cs[i].high - cs[i].low, abs(cs[i].high - cs[i - 1].close),
                  abs(cs[i].low - cs[i - 1].close)) for i in range(len(cs) - n, len(cs))]
        v = sum(tr) / n
        return float(v) if v > 0 else None

    def _mcx_hold(self) -> bool:
        spec = get_spec(self._instrument)
        hq = settings.auto_trade_pre_t1_lock_high_qty
        return (str(spec.exchange).upper() == "MCX" and settings.mcx_hold_arm_points > 0
                and not (hq > 0 and int(spec.lot_size or 0) >= hq))

    def relevel_auto(self, use_vol: bool = False, stop: float | None = None,
                     t1: float | None = None, t2: float | None = None,
                     t3: float | None = None) -> dict:
        """Re-set the OPEN bot trade's stop and targets (pre-T1 only). Never sends
        an order; refuses a stop at/above the current price (that would sell now)."""
        p = self.position
        if not p.option_symbol or not self._auto_entry_active:
            return {"ok": False, "message": "No open bot trade on this instrument."}
        if self._auto_tgt_floor is not None:
            return {"ok": False, "message": "T1 already locked; levels are managed by the ratchet."}
        entry = float(self._auto_entry_prem or p.entry_premium or 0.0)
        if entry <= 0:
            return {"ok": False, "message": "Entry premium unknown."}
        atr_v = None
        if use_vol:
            atr_v = self._option_atr(p.option_symbol)
            spec = get_spec(self._instrument)
            lv = eng.vol_levels(entry, atr_v, spec.exchange, int(spec.lot_size or 0)) if atr_v else None
            if lv is None:
                return {"ok": False, "message": "No option 1-min candles yet; try again in a minute."}
            stop, t1, t2, t3 = (round(entry - lv[0], 1), round(entry + lv[1], 1),
                                round(entry + lv[2], 1), round(entry + lv[3], 1))
        stop = self._auto_stop if stop is None else float(stop)
        t1 = self._auto_target1 if t1 is None else float(t1)
        t2 = self._auto_target2 if t2 is None else float(t2)
        t3 = self._auto_target3 if t3 is None else float(t3)
        now_px = self._own_bid() or p.current_premium
        if stop is None or stop <= 0:
            return {"ok": False, "message": "Stop must be above 0."}
        if now_px is not None and stop >= float(now_px):
            return {"ok": False, "message": (
                f"Stop {stop:.1f} is at/above the current price {float(now_px):.1f}; that would sell "
                "immediately. Use Exit instead.")}
        if not (t1 and t2 and t3 and entry < t1 < t2 < t3):
            return {"ok": False, "message": "Targets must be above entry and T1 < T2 < T3."}
        old = (self._auto_stop, self._auto_target1, self._auto_target2, self._auto_target3)
        # A profit lock already above entry is never loosened.
        if old[0] is not None and old[0] >= entry:
            stop = max(stop, old[0])
        self._auto_stop = round(stop, 2)
        self._auto_target1, self._auto_target2, self._auto_target3 = t1, t2, t3
        if self.is_live and self._ledger_position is not None:
            self._save_live_mgmt(force=True)
        else:
            with contextlib.suppress(Exception):
                self._save_paper_position(time.time())
        msg = (f"Levels re-set {p.option_symbol}: stop {self._auto_stop:.1f}, T1 {t1:.1f}, "
               f"T2 {t2:.1f}, T3 {t3:.1f}" + (f" (option ATR {atr_v:.1f})" if atr_v else ""))
        self._add_alert("LEVELS", msg, "info")
        self._auto_last_action = msg
        self._auto_last_action_ts = int(time.time())
        return {"ok": True, "message": msg, "old": old,
                "new": (self._auto_stop, t1, t2, t3), "option_atr": atr_v}

    def _exit_cost_per_unit(self, entry: float) -> float:
        """Round-trip charges per unit of premium, so a floor at entry + this
        books zero net, not a loss."""
        if not settings.auto_trade_lock_covers_charges or entry <= 0:
            return 0.0
        qty = int(get_spec(self._instrument).lot_size or 0) * int(
            self._auto_entry_lots or self.position.quantity_lots or 1)
        if qty <= 0:
            return 0.0
        try:
            return float(option_costs.charges(entry, entry, qty, self._instrument).total) / qty
        except Exception:
            return 0.0

    def _episode_rearm_wait(self, now: float) -> float | None:
        """Seconds until a traded-and-closed episode may be bought again; None
        when the episode is still held (open, or an order whose fate is unknown)."""
        traded, ex = self._auto_key_traded_ts, self._auto_last_exit_ts
        if (traded is None or ex is None or ex < traded or self.position.option_symbol
                or self._pending_order or self._pending_exit):
            return None
        wait = (settings.auto_trade_post_stop_cooldown_sec if self._auto_last_exit_was_stop
                else settings.auto_trade_reentry_cooldown_sec)
        return max(0.0, wait - (now - ex))

    def _reset_auto_trade_marks(self, force: bool = False) -> None:
        """Clear the per-trade exit marks after an auto-exit so the next entry
        starts clean (targets, stop and the ratcheting target floor)."""
        if not force and self.is_live and self.position.option_symbol:
            # The live SELL did not complete: keep the stop/targets so the next
            # tick tries the exit again instead of leaving the trade unmanaged.
            return
        self._auto_target1 = None
        self._auto_target2 = None
        self._auto_target3 = None
        self._auto_stop = None
        self._auto_tgt_floor = None
        self._auto_scaled_out = False
        self._auto_pre_t1_halved = False
        self._auto_peak = None
        self._auto_entry_prem = None
        self._auto_entry_active = False
        self._auto_entry_strike = None
        self._auto_entry_conf = None
        self._auto_entry_lots = 0

    def _auto_trade_state(self, in_pos: bool) -> AutoTradeState:
        can, reason = self.risk.can_open(time.time())
        # Live feed up but the second live switch off → surface it so the user knows
        # the bot is intentionally standing down rather than silently broken.
        if not reason and self.is_live and not settings.auto_trade_allow_live and settings.auto_trade_enabled:
            reason = "Live feed — bot NOT armed for live orders (set auto_trade_allow_live)"
        mode_error = live_control.trade_mode_error()
        if mode_error:
            reason = mode_error

        # Persistent entry snapshot so the UI can always show what the bot took, even
        # after the BUY signal card has cleared once a position is open.
        entry = None
        if in_pos and self._auto_entry_active:
            p = self.position
            entry = AutoTradeEntry(
                option_symbol=p.option_symbol,
                side=("CE" if (p.option_symbol or "").upper().endswith("CE")
                      else "PE" if (p.option_symbol or "").upper().endswith("PE") else None),
                strike=self._auto_entry_strike,
                entry_premium=p.entry_premium,
                lots=p.quantity_lots,
                entry_time=p.entry_time,
                target1=self._auto_target1,
                target2=self._auto_target2,
                target3=self._auto_target3,
                stop_loss=self._auto_stop,
                locked_floor=self._auto_tgt_floor,
            )

        # Recent completed AUTO trades (most-recent first) for the bot history panel.
        history = [
            {
                "time": t.get("time"),
                "instrument": t.get("instrument"),
                "option": t.get("option"),
                "option_type": t.get("option_type"),
                "entry": t.get("entry"),
                "exit": t.get("exit"),
                "lots": t.get("lots"),
                "net_pnl": t.get("net_pnl"),
                "win": t.get("win"),
                "holding_minutes": t.get("holding_minutes"),
                "mode": t.get("mode"),
                "exit_reason": t.get("exit_reason"),
            }
            for t in self.journal
            if t.get("auto")
        ][-30:][::-1]

        return AutoTradeState(
            enabled=settings.auto_trade_enabled,
            mode="live" if self._auto_live_armed() else "paper",
            kill_switch=live_control.kill_switch_engaged(),
            min_confidence=settings.auto_trade_min_confidence,
            max_lots=(settings.auto_trade_live_max_lots if self._auto_live_armed() else max(1, settings.auto_trade_max_lots)),
            in_position=in_pos,
            last_action=self._auto_last_action,
            last_action_time=self._auto_last_action_ts,
            entries_today=self.risk.trades_today,
            day_pnl=round(self.today_profit - self.today_loss, 1),
            blocked_reason=None if can else reason,
            entry=entry,
            history=history,
        )

    # ----------------------------------------------- zero-to-hero expiry sleeve
    def _is_expiry_day(self, now: int) -> bool:
        """True when today (IST) is the nearest option expiry. Uses the feed's
        real expiry date when available; otherwise falls back to the configured
        days-to-expiry (simulated feed can't supply a real calendar date)."""
        import datetime as _dt

        exp = None
        try:
            exp = self.provider.nearest_expiry()
        except Exception:
            exp = None
        if exp is not None:
            ist = _dt.datetime.utcfromtimestamp(now) + _dt.timedelta(hours=5, minutes=30)
            return ist.date() == exp
        return settings.days_to_expiry <= settings.zero_to_hero_expiry_days_fallback

    def _expiry_context(self, now: int) -> tuple[str | None, int | None]:
        """The nearest expiry date and minutes remaining to it, for the journal.

        The real calendar date when the feed quotes one; otherwise the configured
        days-to-expiry, with the date left unstated rather than fabricated.
        """
        import datetime as _dt

        try:
            exp = self.provider.nearest_expiry()
        except Exception:
            exp = None
        if exp is None:
            return None, int(settings.days_to_expiry * 24 * 60)
        ist = _dt.datetime.utcfromtimestamp(now) + _dt.timedelta(hours=5, minutes=30)
        # 15:30 IST on the expiry date is the last minute the contract trades.
        close = _dt.datetime.combine(exp, _dt.time(15, 30))
        return exp.isoformat(), int((close - ist).total_seconds() // 60)

    # -------------------------------------------------- Phase 17 (research only)
    def _observe_phase17(
        self,
        decision: Decision,
        chain: list[OptionQuote],
        price: float | None,
        snap_ind: IndicatorSnapshot,
        market_status: str,
        candles: list[Candle],
        now: int,
        expiry_iso: str | None,
        minutes_to_expiry: int | None,
    ) -> None:
        """Hand the decision-instant chain to the Phase 17 evidence layer.

        Deliberately returns nothing. The option age reported by the feed becomes
        the quote age, so a REST-poll session is recorded as DEGRADED rather than
        silently presented as an exact match.

        The futures book is read HERE, on the same tick and from the same quote
        refresh as ``chain``, and handed down with the option legs. That is the
        whole of Phase 21 §1: the futures leg is only a comparison if it was
        quoted at the same signal, so it cannot be fetched later by the report.
        """
        try:
            health = self._feed_health()
            age_ms = (
                None if health.option_age_sec is None
                else float(health.option_age_sec) * 1000.0
            )
            dte = (
                None if minutes_to_expiry is None
                else max(0, int((minutes_to_expiry + 1439) // 1440))
            )
            try:
                futures_book = self.provider.futures_book()
            except Exception:
                # A provider that cannot quote its contract leaves the leg
                # MISSING with a reason; it does not cost the option row.
                futures_book = None
            phase17.observe(
                self._instrument, decision, chain,
                spot=price, ind=snap_ind, status=market_status,
                candles=candles, signal_ts=float(now),
                expiry=expiry_iso, days_to_expiry=dte,
                source=health.mode, quote_age_ms=age_ms,
                futures_book=futures_book,
            )
        except Exception as exc:  # research must never break a live tick
            self._capture_error = str(exc)

    # ------------------------------------------- Phase 22 (core setup, paper)
    def _observe_phase22(
        self,
        decision: Decision,
        chain: list[OptionQuote],
        price: float | None,
        market_status: str,
        candles: list[Candle],
    ) -> None:
        """Grade this decision against the frozen Phase 22 setup. Paper only.

        The futures book is read on this same tick, as the Phase 17 hook does,
        so a futures leg is a comparison at the signal rather than a price
        fetched later by a report.
        """
        try:
            futures_book = self.provider.futures_book()
        except Exception:
            futures_book = None
        phase22.observe(
            self._instrument, decision, chain,
            regime=str(market_status), candles=candles, spot=price,
            lot_size=get_spec(self._instrument).lot_size,
            futures_book=futures_book,
        )

    # ------------------------------------------- frozen pair capture (research)
    def _observe_pair_capture(self, candles: list, price: float | None,
                              now: int) -> None:
        """Record this instrument's futures book for the frozen pair study.

        The book and the near/next contract chain are read HERE, at the instant
        the series is read, for the same reason Phase 21 reads the futures book
        on the tick: a price fetched later by a report is not a synchronized
        observation and cannot answer whether the relationship was executable at
        the instant.

        The observation is stamped with the LAST CLOSED CANDLE's time, not with
        wall clock. The two instruments are polled seconds apart, so a wall-clock
        stamp would never repeat across legs and nothing would ever pair; the bar
        time is the feed's own clock and is identical on both legs for the same
        minute. When no candle exists there is no bar to stamp, so nothing is
        recorded rather than being recorded against a made-up instant.

        The series used is the RESEARCH one (``futures_candles_live``), which is
        REST history extended by the WebSocket-built tail. The production
        indicator series is deliberately left alone. This matters for measured
        cost: when REST history is rate-limited (AB1021) a leg's newest REST bar
        can be many minutes old, and stamping a book quoted now onto that old
        bar would be a false synchronization -- while the socket knows the
        current minute and quotes the book for it, which is a real one.
        """
        try:
            series = self.provider.futures_candles_live(limit=_PAIR_TAIL)
        except Exception:
            series = candles
        bar_ts = int(series[-1].time) if series else None
        if bar_ts is None:
            # Passed on rather than dropped: an empty series is itself the
            # finding (no REST history and no socket tick), and it is the one
            # cause of thin coverage that leaves no trace in the stored rows.
            pair_capture.observe_leg(self._instrument, None, price,
                                     now=time.time())
            return
        try:
            book = self.provider.futures_book()
        except Exception:
            book = None
        try:
            chain = self.provider.futures_book_chain()
        except Exception:
            chain = []
        # The recent TAIL as well, for the same reason the history store keeps
        # one: an instrument is re-read only when the scan reaches it and the
        # REST history is rate-limited, so each leg misses minutes -- and the
        # two legs miss different ones, which leaves them sharing almost no
        # timestamps. The tail carries the feed's own closes for those bars;
        # they are stored without a book, because a spread cannot be recovered
        # after the instant it was quoted.
        history = [(int(c.time), float(c.close)) for c in series[-_PAIR_TAIL:-1]]
        pair_capture.observe_leg(
            self._instrument, bar_ts, price,
            futures_book=book, contract_books=chain,
            history=history, now=time.time(),
        )

    def capture_pair_leg(self) -> None:
        """Record this leg for the frozen pair study WITHOUT running the engine.

        The tick is the wrong and only clock the capture had: an instrument is
        ticked when the scan reaches it, so a leg competing with a large universe
        for the historical budget is read a handful of times an hour, and a pair
        needs BOTH legs on the same minute. This entry point reads only what the
        socket has already pushed (cached quote book, WS-extended candle tail),
        makes no broker request, and evaluates no signal -- so the two legs can
        be sampled on a steady cadence without changing when a production
        decision is computed or which instruments the scanner prioritises.
        """
        try:
            price = self.provider.futures_price()
        except Exception:
            price = None
        self._observe_pair_capture([], price, int(time.time()))

    # ------------------------------------------- Phase 23 (hurdle paper shadow)
    def _observe_phase23(
        self,
        decision: Decision,
        chain: list[OptionQuote],
        now: int,
        *,
        engine_bought: bool,
        in_position_before: bool,
    ) -> None:
        """Record this auto BUY opportunity with the measured book. Record only.

        Options only — the futures book is deliberately outside this experiment,
        because its measured hurdle was already negligible and its losses were
        directional. Failures are swallowed into the Phase 23 health counter: a
        research row is never worth a live tick.
        """
        phase23.observe(
            self._instrument, decision, chain,
            lot_size=get_spec(self._instrument).lot_size,
            lots=int(self.position.quantity_lots or 0) or 1,
            engine_bought=engine_bought,
            engine_fill=(float(self.position.entry_premium)
                         if engine_bought and self.position.entry_premium
                         else None),
            in_position_before=in_position_before,
            now=now,
        )

    # -------------------------------------------------- Phase 18 (CAS research)
    def _observe_phase18(
        self,
        chain: list[OptionQuote],
        price: float | None,
        snap_ind: IndicatorSnapshot,
        market_status: str,
        candles: list[Candle],
        now: int,
        expiry_iso: str | None,
        minutes_to_expiry: int | None,
    ) -> None:
        """Hand the same decision-instant chain to the CAS evidence layer.

        Separate from :meth:`_observe_phase17` on purpose: CAS is a different
        regime with its own window, its own scoring and its own paper book, and
        keeping the two hooks apart means either can be switched off without
        touching the other. Returns nothing, as the Phase 17 hook does.
        """
        try:
            health = self._feed_health()
            age_ms = (
                None if health.option_age_sec is None
                else float(health.option_age_sec) * 1000.0
            )
            dte = (
                None if minutes_to_expiry is None
                else max(0, int((minutes_to_expiry + 1439) // 1440))
            )
            phase18.observe(
                self._instrument, chain,
                spot=price, ind=snap_ind, candles=candles,
                htf_trend=snap_ind.trend, tick_ts=float(now),
                expiry=expiry_iso, days_to_expiry=dte,
                source=health.mode, quote_age_ms=age_ms,
                market_open=str(market_status).upper() == "OPEN",
            )
        except Exception as exc:  # research must never break a live tick
            self._capture_error = str(exc)

    # ------------------------------------------- Phase 19 (futures paper book)
    def _observe_phase19_futures(
        self,
        fut_price: float | None,
        feed_age_sec: float | None,
        market_status: str,
        now: int,
    ) -> None:
        """Step the futures paper book, and flatten it at the close.

        The close matters: without it an open trade would be carried to the next
        session and resolved against a price it could never have traded at, which
        is the most flattering error a paper book can make.
        """
        try:
            if str(market_status).upper() != "OPEN":
                phase19.close_futures_session(
                    self._instrument, fut_price, now=float(now)
                )
                return
            phase19.observe_futures(
                self._instrument,
                self._futures_signal,
                fut_price,
                now=float(now),
                feed_age_sec=feed_age_sec,
            )
        except Exception as exc:  # research must never break a live tick
            self._capture_error = str(exc)

    def _z2h_reset_if_new_day(self, now: int) -> None:
        import datetime as _dt

        ist = _dt.datetime.utcfromtimestamp(now) + _dt.timedelta(hours=5, minutes=30)
        day = ist.strftime("%Y-%m-%d")
        if self._z2h_spent_date != day:
            self._z2h_spent_date = day
            self._z2h_spent_day = 0.0

    def _zero_to_hero(
        self,
        decision: Decision,
        chain: list[OptionQuote],
        price: float,
        in_pos: bool,
        now: int,
    ) -> ZeroToHero:
        """Compute the expiry-day hero suggestion and, when enabled and there is a
        free position slot, auto-enter ONE budget-capped punt. Isolated from the
        core bot by a fixed daily budget; never exceeds it. Paper unless the same
        live-arm switches as the core bot are set."""
        self._z2h_reset_if_new_day(now)
        is_expiry = self._is_expiry_day(now)
        budget_left = max(0.0, settings.zero_to_hero_budget - self._z2h_spent_day)
        z = zero_to_hero.evaluate(self._instrument, chain, price, decision, is_expiry, budget_left)

        # Auto-enter only when: sleeve armed, an actionable punt exists, the core
        # bot has no open position (single slot), and we haven't already punted
        # this leg today. Reuses buy() so risk checks / journal still apply.
        if (
            settings.zero_to_hero_enabled
            and settings.auto_trade_enabled
            and z.active
            and z.option_symbol
            and z.lots
            and not in_pos
            and self._z2h_last_key != z.option_symbol
        ):
            live = self._auto_live_armed()
            if not (self.is_live and not settings.auto_trade_allow_live):
                res = self.buy(z.option_symbol, lots=z.lots, confirm=live,
                               manual=False)
                self._z2h_last_key = z.option_symbol
                if res.ok:
                    self._z2h_spent_day += (z.cost or 0.0)
                    self._z2h_is_sleeve = True
                    # ride it: no targets / no hard stop (lottery ticket), let it run
                    # to a spike or day-end. Budget already caps the downside.
                    self._reset_auto_trade_marks(force=True)
                    tag = "LIVE HERO" if live else "HERO PUNT"
                    self._auto_last_action = (
                        f"{tag} {z.option_symbol} x{z.lots} @ "
                        f"{res.fill_premium:.1f} · expiry punt (₹{z.cost:,.0f})"
                    )
                    self._auto_last_action_ts = now
        return z

    def _apply_risk_v2(self, decision: Decision, chain: list[OptionQuote]) -> None:
        """Risk Management v2: refine the premium stop (Greeks-aware) and run the
        Pre-Trade Validator on a fresh BUY. Mutates only ``stop_loss``,
        ``trade_validation`` and — on rejection — the ``signal`` (BUY→AVOID) and
        ``reasons``. Never changes confidence, targets, entry range, or the
        strategy rules. Fully skipped when the flag is off or there is no BUY."""
        premium = decision.current_premium
        if (
            decision.signal != Signal.BUY
            or not decision.recommended_option
            or premium is None
            or premium <= 0
        ):
            return

        quote = next((x for x in chain if x.symbol == decision.recommended_option), None)

        # Underlying stop distance (points) the frozen engine already derived.
        u_dist = 0.0
        if decision.spot_price is not None and decision.underlying_stop is not None:
            u_dist = abs(decision.spot_price - decision.underlying_stop)

        if u_dist > 0:
            refined, source = risk_v2.refined_premium_stop(
                premium=premium,
                underlying_stop_distance=u_dist,
                quote=quote,
            )
        else:
            # No underlying distance available — keep the engine's stop as the
            # candidate so the validator can still judge it.
            refined = decision.stop_loss if decision.stop_loss is not None else premium
            source = "engine"

        validation = risk_v2.validate_buy(
            premium=premium,
            proposed_stop=refined,
            target1=decision.target1,
            quote=quote,
        )
        validation.refined_stop = refined
        validation.refined_stop_source = source

        if validation.approved:
            # Replace the premium stop with the refined, risk-aware level.
            decision.stop_loss = refined
        else:
            # Reject the BUY rather than force an unrealistic stop.
            decision.signal = Signal.AVOID
            decision.recommended_option = None
            decision.option_type = None
            decision.entry_range = None
            for r in validation.rejections:
                if r not in decision.reasons:
                    decision.reasons.insert(0, r)
            decision.reasons.insert(0, "AVOID — Risk v2 pre-trade validation rejected this BUY:")

        decision.trade_validation = validation

    def _apply_execution_intelligence(
        self, decision: Decision, chain: list[OptionQuote], snap_ind: IndicatorSnapshot
    ) -> None:
        """Phase 3.6: attach the advisory Execution Intelligence result. Reads the
        engine's own decision + indicators + best-effort India VIX; NEVER changes
        the signal, confidence, targets, or strategy."""
        vix_ctx, _ = intel.cached_context()
        vix = vix_ctx.last if vix_ctx.available else None
        decision.execution_intelligence = execution_validator.evaluate(
            decision, chain, snap_ind, vix
        )

    def _apply_early_momentum(
        self,
        decision: Decision,
        candles: list[Candle],
        chain: list[OptionQuote],
        snap_ind: IndicatorSnapshot,
        spot: float,
        now: int,
    ) -> None:
        """Early Momentum Advisory Engine (parallel, advisory). Maps the market
        onto the 5-stage accumulation→trend ladder with adaptive, regime/VIX/
        expiry/time-weighted evidence, logs stage transitions, and measures how
        much of the move the confirmation BUY missed. NEVER mutates the frozen
        engine's signal/confidence/targets/strategy."""
        vix_ctx, _ = intel.cached_context()
        vix = vix_ctx.last if vix_ctx.available else None
        is_mcx = get_spec(self._instrument).exchange == "MCX"
        base_side = decision.option_type if decision.signal == Signal.BUY else None
        em = early_momentum.detect(
            candles, chain, snap_ind, spot,
            vix=vix, now=now, is_mcx=is_mcx,
            baseline_buy=decision.signal == Signal.BUY, baseline_side=base_side,
        )

        # Log every stage transition (accumulation → trend) for future AI training.
        if em.stage_num != self._em_last_stage:
            early_momentum.log_transition(self._instrument, em, now)
            self._em_last_stage = em.stage_num

        # Anchor the current directional leg so we can remember the FIRST price
        # at which the early engine flagged it. A probe (no early price) gives us
        # the swing anchor; a new anchor means a new leg → reset the memory.
        probe = signal_delay.analyze(candles, snap_ind, spot, em.side)
        anchor = probe.move_start_price
        if anchor is not None and (
            self._em_anchor is None or abs(anchor - self._em_anchor) > 1e-6
        ):
            self._em_anchor = anchor
            self._em_early_price = None
            self._em_early_ctime = None
        if em.active and em.entry_hint and self._em_early_price is None:
            self._em_early_price = em.entry_hint
            self._em_early_ctime = candles[-1].time if candles else None

        # Candles elapsed between the EARLY flag and now (the confirmation view).
        cbetween: int | None = None
        if self._em_early_ctime is not None:
            cbetween = sum(1 for c in candles if c.time > self._em_early_ctime)

        decision.early_momentum = em
        decision.signal_delay = signal_delay.analyze(
            candles, snap_ind, spot, em.side,
            early_entry_premium=self._em_early_price,
            confirmation_premium=decision.current_premium,
            candles_between=cbetween,
        )

        # Record how far each EARLY BUY actually reaches, per instrument, for
        # later analysis. Analysis-only — no order, engine untouched.
        self._em_outcome = momentum_outcomes.update(
            self._instrument, em, chain, now, self._em_outcome
        )

    def _apply_early_early(
        self,
        decision: Decision,
        candles: list[Candle],
        chain: list[OptionQuote],
        snap_ind: IndicatorSnapshot,
        spot: float,
        now: int,
    ) -> None:
        """Early-Early Mode (Stage 2.5) — a SEPARATE, feature-flagged advisory
        add-on. Produces its own multi-evidence early advisory (with R:R and
        liquidity validation), tracks whether the frozen engine later confirms
        the same side (agreement + delay in candles), and logs everything to its
        own file. NEVER mutates the frozen engine or the Early Momentum result."""
        ee = early_early.detect(candles, chain, snap_ind, spot)

        # Agreement tracker: is the FROZEN engine currently BUYing the same side?
        frozen_same_side = bool(
            decision.signal == Signal.BUY
            and ee.side is not None
            and decision.option_type == ee.side
        )
        self._ee_outcome = early_early_tracker.update(
            self._instrument, ee, chain, candles, frozen_same_side, now, self._ee_outcome
        )
        # Reflect the live agreement status back onto the advisory for display.
        if self._ee_outcome is not None:
            ee.agreement = self._ee_outcome.get("agreement", "PENDING")
            ee.confirm_delay_candles = self._ee_outcome.get("confirm_delay_candles")
        elif not ee.active:
            ee.agreement = "—"

        decision.early_early = ee

    def _apply_scalp(
        self,
        decision: Decision,
        candles: list[Candle],
        chain: list[OptionQuote],
        snap_ind: IndicatorSnapshot,
        spot: float,
        now: int,
    ) -> None:
        """Quick Scalp Engine — a SEPARATE, feature-flagged advisory add-on.
        Produces its own short-duration scalp advisory (fixed target + tight
        stop + breakeven + time-stop), tracks whether the frozen engine later
        confirms the same side (for comparison), and logs everything to its own
        file. NEVER mutates the frozen engine or any other advisory result."""
        sc = scalp.detect(candles, chain, snap_ind, spot)

        frozen_same_side = bool(
            decision.signal == Signal.BUY
            and sc.side is not None
            and decision.option_type == sc.side
        )
        # LIVE EXIT alert: evaluate BEFORE advancing the tracker so the running
        # leg's own entry/stop/target (not this tick's fresh advisory) drive it.
        rec = self._scalp_outcome
        if rec is not None:
            leg_prem: float | None = None
            for q in chain:
                if q.symbol == rec.get("option_symbol") and q.premium and q.premium > 0:
                    leg_prem = float(q.premium)
                    break
            up = rec.get("side") == OptionType.CALL.value
            exit_now, urgency, reason, sigs = scalp.evaluate_exit(
                up, candles, snap_ind, spot,
                rec.get("entry_premium"), leg_prem,
                rec.get("stop_loss"), rec.get("target1"),
            )
            sc.in_trade = True
            sc.exit_now = exit_now
            sc.exit_urgency = urgency
            sc.exit_reason = reason
            sc.exit_signals = sigs
            sc.live_premium = leg_prem
            entry = rec.get("entry_premium")
            if leg_prem is not None and entry:
                sc.live_points = round(leg_prem - float(entry), 1)

        self._scalp_outcome = scalp_tracker.update(
            self._instrument, sc, chain, candles, frozen_same_side, now, self._scalp_outcome
        )
        if self._scalp_outcome is not None:
            sc.agreement = self._scalp_outcome.get("agreement", "PENDING")
            sc.confirm_delay_candles = self._scalp_outcome.get("confirm_delay_candles")
        elif not sc.active:
            sc.agreement = "—"

        decision.scalp = sc

    def _apply_flow(
        self,
        decision: Decision,
        candles: list[Candle],
        chain: list[OptionQuote],
        snap_ind: IndicatorSnapshot,
        spot: float,
        now: int,
    ) -> None:
        """Flow Engine (Candle-Flow) — a SEPARATE, feature-flagged advisory add-on.
        Rides the 1-min candle flow (BUY green / HOLD / EXIT red / SWITCH side),
        maintains its own leg record for the live P&L + EXIT alert, logs finished
        legs to its own file, and records whether the frozen engine later agreed
        on the same side (for comparison). NEVER mutates the frozen engine or any
        other advisory result."""
        fl = flow.detect(candles, chain, snap_ind, spot, self._flow_state,
                         instrument=self._instrument)

        frozen_same_side = bool(
            decision.signal == Signal.BUY
            and fl.side is not None
            and decision.option_type == fl.side
        )
        # Only open NEW paper legs for instruments the user has enabled in the
        # active universe (Watchlist). A disabled instrument still shows the live
        # advisory, but the bot must not paper-trade it. An already-open leg is
        # still managed to its EXIT so it is never left orphaned.
        from app.market.instruments import UNIVERSE

        allow_open = self._instrument in UNIVERSE
        self._flow_state = flow_tracker.update(
            self._instrument, fl, chain, candles, spot, frozen_same_side, now,
            self._flow_state, allow_open=allow_open,
        )

        decision.flow = fl

    def _annotate_averaging(self, decision: Decision, in_pos: bool) -> None:
        """Conservatively flag an optional 'average down' opportunity.

        Only when HOLDing (thesis still intact), the position is meaningfully
        underwater, the premium is still comfortably above the trailing stop,
        and the directional edge is still decent. If the direction has flipped
        (EXIT / reversal) we never suggest adding — that would be throwing good
        money after bad. This is a *suggestion*, not automation, and it never
        places an order.
        """
        decision.average_ok = False
        decision.average_note = None
        p = self.position
        if not in_pos or decision.signal != Signal.HOLD or not p.entry_premium:
            return
        underwater = p.pnl_pct <= -settings.average_down_trigger_pct
        above_stop = (
            p.current_premium is not None
            and p.trailing_stop is not None
            and p.current_premium > p.trailing_stop * 1.03
        )
        edge_ok = decision.signal_strength >= 100 * settings.buy_min_primary_strength
        if underwater and above_stop and edge_ok:
            decision.average_ok = True
            decision.average_note = (
                f"Underwater {p.pnl_pct:.0f}% but the trend still supports you. "
                f"OPTIONAL: add 1 lot near ₹{p.current_premium} to lower your average — "
                "only within your risk limit; averaging doubles exposure and is not risk-free."
            )

    def _mark_position(self, premium: float,
                       quote: OptionQuote | None = None) -> None:
        p = self.position
        p.current_premium = premium
        if quote is not None:
            # Latest valid top-of-book behind this mark. The exit row is written
            # from the last mark, so this is the book at the exit premium.
            latest = fill_book.book(quote.bid, quote.ask, int(time.time()))
            if latest is not None:
                self._exit_book = latest
        # track MFE/MAE excursions (research capture only — never a trade trigger)
        if p.peak_premium is None or premium > p.peak_premium:
            p.peak_premium = premium
        if p.trough_premium is None or premium < p.trough_premium:
            p.trough_premium = premium
        lot_size = get_spec(self._instrument).lot_size
        qty = lot_size * p.quantity_lots
        gross = (premium - (p.entry_premium or premium)) * qty
        ch = option_costs.charges(p.entry_premium, premium, qty, self._instrument)
        p.brokerage = ch.brokerage
        p.statutory_charges = ch.statutory
        p.total_costs = ch.total
        p.cost_model = ch.model
        p.pnl = round(gross, 1)
        p.net_pnl = round(gross - ch.total, 1)
        if p.entry_premium:
            p.pnl_pct = round((premium - p.entry_premium) / p.entry_premium * 100, 2)
        if self._entry_time:
            p.holding_minutes = int((int(time.time()) - self._entry_time) / 60)
        # trail the stop up as premium rises (ratchets up only, never down)
        trail_frac = max(0.0, 1.0 - settings.auto_trade_trail_pct / 100.0)
        new_trail = round(premium * trail_frac, 1)
        if p.trailing_stop is None or new_trail > p.trailing_stop:
            p.trailing_stop = new_trail

    def _record_research_trade(self, trade_row: dict, entry_time: int | None) -> None:
        """Persist a completed paper trade into the research store with MFE/MAE
        and full decision context (Setup Library + post-trade analytics).

        Advisory / observational only — nothing here feeds back into the frozen
        signal engine. Best-effort: the caller wraps this in try/except so a
        research-store hiccup never affects the trade itself.
        """
        from app.research.store import store as research_store

        p = self.position
        entry_px = p.entry_premium
        if entry_px is None:
            return
        lot_size = get_spec(self._instrument).lot_size
        peak = p.peak_premium if p.peak_premium is not None else entry_px
        trough = p.trough_premium if p.trough_premium is not None else entry_px
        # MFE >= 0 (best unrealised gain), MAE <= 0 (worst drawdown), in ₹.
        mfe = round((peak - entry_px) * lot_size, 1)
        mae = round((trough - entry_px) * lot_size, 1)
        exit_ts = int(trade_row.get("time") or time.time())
        hour = datetime.fromtimestamp(exit_ts, tz=timezone.utc).hour
        ctx = trade_row.get("context") or {}
        research_store().insert_trade({
            "instrument": self._instrument,
            "source": "paper_live",
            "option_symbol": trade_row.get("option"),
            "option_type": trade_row.get("option_type"),
            "entry_ts": entry_time,
            "exit_ts": exit_ts,
            "entry": entry_px,
            "exit": trade_row.get("exit"),
            "pnl": trade_row.get("net_pnl"),
            "duration_min": trade_row.get("holding_minutes"),
            "exit_reason": "MANUAL_EXIT",
            "mfe": mfe,
            "mae": mae,
            "slippage": 0.0,
            "brokerage": p.brokerage,
            "trade_score": ctx.get("trade_score"),
            "market_regime": ctx.get("market_regime"),
            "entry_hour": hour,
            "win": bool(trade_row.get("win")),
            "context": ctx,
        })

    def _recovery(self, decision: Decision, chain, pos_type, snap_ind, in_pos) -> RecoveryAnalysis:
        if not in_pos or self.position.entry_premium is None:
            return RecoveryAnalysis(in_position=False)
        q = next((x for x in chain if x.symbol == self.position.option_symbol), None)
        if q is None:
            return RecoveryAnalysis(in_position=False)
        # directional edge relative to the position's option type
        edge = (decision.signal_strength / 100.0)
        if (pos_type == OptionType.CALL) != (decision.option_type == OptionType.CALL):
            edge = -edge
        minutes_to_expiry = int(settings.days_to_expiry * 24 * 60)
        return rec_eng.analyse(
            entry_premium=self.position.entry_premium,
            current_premium=q.premium,
            directional_edge=edge,
            theta_per_day=q.theta,
            minutes_held=self.position.holding_minutes,
            minutes_to_expiry=minutes_to_expiry,
        )

    def _raise_alerts(self, decision, snap_ind, candles, high_impact, in_pos) -> None:
        # signal transitions
        if decision.signal != self._last_signal:
            if decision.signal == Signal.BUY:
                self._add_alert("BUY_NOW", f"BUY {decision.recommended_option} ({decision.confidence:.0f}% conf)", "critical")
            elif decision.signal == Signal.EXIT and in_pos:
                if decision.reversal and decision.reversal_option:
                    flip = "PUT" if decision.reversal_option_type == OptionType.PUT else "CALL"
                    self._add_alert(
                        "REVERSAL",
                        f"Direction flipped — exit and consider {flip} {decision.reversal_option}",
                        "critical",
                    )
                else:
                    self._add_alert("EXIT_NOW", "Exit signal on open position", "critical")
            self._last_signal = decision.signal

        # crash / big player
        import numpy as np

        c = np.array([x.close for x in candles], dtype=float)
        v = np.array([x.volume for x in candles], dtype=float)
        crash, detail = st.crash_signal(c, v, snap_ind.atr)
        if crash:
            self._add_alert("CRASH", f"Sudden move detected: {detail}", "critical")
        if snap_ind.volume_spike:
            self._add_alert("VOLUME_SPIKE", "Volume spike vs 20-bar average", "warning")
        if high_impact is not None:
            self._add_alert("NEWS", f"{high_impact.source}: {high_impact.headline}", "warning")

        # position target / stop
        if in_pos and self.position.current_premium is not None:
            if decision.target1 and self.position.current_premium >= decision.target1:
                self._add_alert("TARGET_HIT", "Target 1 reached", "info")
            if self.position.trailing_stop and self.position.current_premium <= self.position.trailing_stop:
                self._add_alert("STOP_LOSS", "Trailing stop hit", "critical")


class StateRegistry:
    """One :class:`AppState` per instrument, created lazily on first use.

    Keeping state per-instrument (instead of a single global "active" one) is
    what lets two browser tabs watch *different* instruments simultaneously
    without stepping on each other — each tab addresses its own instrument by
    name, so switching or trading in one tab never mutates another.
    """

    def __init__(self) -> None:
        self._states: dict[str, AppState] = {}
        self._guard = threading.Lock()
        self._build_locks: dict[str, threading.Lock] = {}

    def get(self, instrument: str | None) -> AppState:
        name = resolve_key(instrument or DEFAULT_INSTRUMENT)
        if name not in REGISTRY:
            name = DEFAULT_INSTRUMENT
        st = self._states.get(name)
        if st is not None:
            return st
        # Construction is slow (broker login, candles) and reads the live order
        # ledger. Concurrent callers must share ONE instance per instrument: a
        # second copy would restore an in-flight order as "unresolved" and could
        # trade independently of the first.
        with self._guard:
            lock = self._build_locks.setdefault(name, threading.Lock())
        with lock:
            st = self._states.get(name)
            if st is None:
                st = AppState(name)
                self._states[name] = st
        return st

    def active(self) -> list[AppState]:
        """States that have been requested at least once (worth ticking)."""
        return list(self._states.values())


registry = StateRegistry()

# Backwards-compatible default (the previously global single instrument). Some
# call sites / tests import ``state`` directly; it maps to the default
# instrument's AppState.
state = registry.get(DEFAULT_INSTRUMENT)
