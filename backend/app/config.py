"""Application configuration.

All configuration is sourced from environment variables (never hard-coded).
Copy ``.env.example`` to ``.env`` and adjust values for your environment.
"""
from __future__ import annotations

from dotenv import find_dotenv, load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load the local .env into the process environment so that NON-prefixed broker
# secrets (SMARTAPI_KEY / SMARTAPI_CLIENT_CODE / SMARTAPI_PIN /
# SMARTAPI_TOTP_SECRET) are visible to os.environ.get() in the Angel One
# provider. Pydantic below only maps the QT_-prefixed settings, so without this
# the SmartAPI credentials in .env would never be picked up. find_dotenv walks
# up from the current dir, so it works whether uvicorn is started from the
# project root or the backend/ folder. Real environment variables always win.
load_dotenv(find_dotenv(usecwd=True), override=False)


class Settings(BaseSettings):
    """Runtime settings loaded from the environment / a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="QT_", extra="ignore")

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:3000"

    # --- Market data provider ---
    # Which provider implementation to use. "simulated" ships out of the box.
    # Swap to "kite" / "upstox" / "angelone" / "truedata" once credentials
    # are supplied — the rest of the app is provider-agnostic.
    data_provider: str = "simulated"

    # --- Angel One (SmartAPI) live feed ---
    # Credentials are read from the environment inside the provider
    # (SMARTAPI_KEY / SMARTAPI_CLIENT_CODE / SMARTAPI_PIN / SMARTAPI_TOTP_SECRET).
    angel_exchange: str = "MCX"
    angel_symbol_root: str = "CRUDEOIL"

    # --- Groww live feed (optional, complements Angel) ---
    # When true AND Groww credentials are present, NSE/BSE equity + index option
    # instruments (NFO/BFO) are served by Groww while Angel keeps MCX commodities.
    # This splits the load across two independent accounts so neither is
    # overloaded. Credentials are read in the provider (GROWW_API_KEY /
    # GROWW_TOTP_SECRET, or a ready GROWW_ACCESS_TOKEN).
    enable_groww: bool = False

    # --- Daily auto-pick (universe selection only — never touches the engine) ---
    # When true, each trading day the app asks the broker for the biggest F&O
    # movers (Angel gainersLosers) and focuses the dashboard/screener on the top
    # ``auto_pick_count`` liquid stock options, plus the always-on ``auto_pick_core``
    # (Crude/Nifty/Sensex…). Fewer, high-movement names = faster feed + less
    # research. Static QT_INSTRUMENTS, if set, overrides this.
    auto_pick_enabled: bool = False
    auto_pick_count: int = 5
    auto_pick_core: str = "CRUDEOIL,NIFTY,SENSEX"

    # --- Auto day-movers ---
    # Let the engine choose the day's instruments itself, ranked by how far the
    # PREMIUM can travel net of costs, and then HOLD that set for the session so
    # the bot concentrates instead of rotating. The user's manual watchlist file
    # is never rewritten by this: only the active (in-memory) universe changes,
    # so their saved list stays available for reference and manual override.
    day_movers_auto_enabled: bool = False
    # Share of the universe that must have a measured move before the day's set
    # is locked. Warm-up is rate-limited, so a cold instrument reads as quiet.
    day_movers_min_coverage: float = 60.0
    # Minutes since IST midnight before the first pick (09:45 IST). The opening
    # minutes misprice which names are actually active today.
    day_movers_pick_after_ist_min: int = 585
    # Swap a held instrument out once it reads dead on repeated checks.
    day_movers_replace_dead: bool = True
    # How often the held set is re-examined (seconds).
    day_movers_refresh_sec: float = 300.0

    # --- Two-tier watchlist (feed routing only — never touches the engine) ---
    # Measured over four recorded sessions, a 50-instrument active universe left
    # 46.5% of one-minute bars missing and 38% of signals firing on data already
    # flagged stale, because every active name warms candles over one
    # rate-limited historical API. Splitting the universe fixes the cause:
    #
    #   TIER 1 (deep)  full tick capture, complete one-minute candles, recorded
    #                  option chain with bid/ask, full indicators — the existing
    #                  production engine, unchanged, on fewer names.
    #   TIER 2 (broad) LTP, % move, short-term momentum, activity and feed
    #                  freshness only. No candle warm-up, no chain recording.
    #
    # ``deep_watchlist`` is DELIBERATELY empty by default: empty = the current
    # single-tier behaviour, byte for byte, so this setting can ship without
    # changing what anyone's install does. Which 6-8 names belong in Tier 1 is a
    # trading decision and is not chosen here or anywhere in code.
    # Set e.g. QT_DEEP_WATCHLIST=CRUDEOIL,NIFTY,BANKNIFTY,SENSEX,GOLD,NATURALGAS
    deep_watchlist: str = ""
    # Tier 2. Empty = "everything active that is not Tier 1", which is the
    # sensible default: the broad tier costs one quote per name per cycle.
    broad_watchlist: str = ""
    # Hard ceiling on Tier 1 regardless of how long ``deep_watchlist`` is. The
    # cap exists because deep capture cost is what degraded the data, so a long
    # deep list silently reproduces the problem this split exists to solve.
    max_deep_instruments: int = 8
    # Research-only promotion: a Tier 2 name that becomes strongly interesting is
    # RECORDED as a promotion candidate. Promotion never places an order and
    # never alters a gate; it only asks the feed to start capturing depth.
    tier_promotion_enabled: bool = False
    tier_promote_min_move_pct: float = 1.5      # |% move| from the day's open
    tier_promote_min_momentum_pct: float = 0.4  # short-term momentum, % over the window
    tier_promote_max_feed_age_sec: float = 15.0  # never promote on stale data
    tier_promote_hold_sec: float = 900.0        # keep a promoted name deep this long
    # Promotion must not push Tier 1 past this; the weakest promoted name is
    # demoted first, and a configured deep name is never demoted.
    max_promoted_instruments: int = 2

    # --- Phase 6 real-money kill switch -------------------------------------
    # REPORTING ONLY — this flag cannot enable real-money execution. Real orders
    # are refused unconditionally at the broker adapter by the source constant
    # app.ai.safety.REAL_MONEY_EXECUTION_ENABLED (False), so setting this to false
    # changes nothing: orders are still refused. Turning real execution on is a
    # reviewed code change, deliberately not an environment variable. Paper
    # trading is unaffected either way.
    paper_mode: bool = True

    # --- Order execution ---
    # "paper" tracks a simulated position only (no real orders). "live" places
    # real orders via the broker provider and requires an explicit confirm flag.
    trade_mode: str = "paper"
    angel_product: str = "INTRADAY"  # INTRADAY (MIS) or CARRYFORWARD (NRML)
    angel_order_variety: str = "NORMAL"  # NORMAL / STOPLOSS
    angel_order_type: str = "MARKET"  # MARKET / LIMIT
    # When multiple Angel keys are configured, route ALL live orders through the
    # primary (first) key so real orders never queue behind data rate-limits;
    # quote/history fetching still shards across every key for speed. With a
    # single key this has no effect.
    orders_on_primary_only: bool = True

    # --- Risk management / position sizing ---
    capital: float = 100000.0            # trading capital used for sizing
    risk_per_trade_pct: float = 2.0      # % of capital risked per trade
    # These three are ACCOUNT-WIDE and persisted across restarts (see
    # app/engine/account_risk.py). They used to be per-instrument and in-memory,
    # which made the cap meaningless: measured 13 Aug, the book lost Rs16,834
    # with a Rs5,000 cap enabled because each of seven instruments carried its
    # own Rs5,000 allowance and a mid-session restart zeroed all of them.
    max_daily_loss: float = 5000.0       # halt the whole book once day P&L <= -this
    max_trades_per_day: int = 10         # cap on entries per session, all instruments
    max_consecutive_losses: int = 3      # stand down after N losing trades in a row
    # How long that losing streak stands the book down. A streak in chop says
    # something about the next hour, not about the whole session, so this is a
    # timed stand-down rather than a halt for the day.
    risk_stand_down_sec: float = 3600.0
    auto_squareoff_ist: str = "23:25"    # advise square-off after this IST time

    # --- Paper auto-trading (simulated fills only; NEVER places live orders) ---
    # When enabled, the engine auto-enters a paper position on a BUY at/above the
    # gate and auto-exits at Target 1 or the stop. Hard-guarded to paper mode:
    # it refuses to run whenever trade_mode is 'live'. Safety caps reuse the risk
    # limits above (max_daily_loss, max_trades_per_day, max_consecutive_losses).
    auto_trade_enabled: bool = False
    auto_trade_min_confidence: float = 90.0   # only auto-buy at/above this %
    auto_trade_max_lots: int = 1              # hard upper cap on lots per auto entry
    # Multi-instrument concurrency: hold positions in up to N DIFFERENT instruments
    # at once (each instrument still gets one position). New entries are additionally
    # gated by a GLOBAL capital check so total deployed never exceeds ``capital``.
    auto_trade_max_concurrent: int = 3
    # Entry-timing refinements applied to the auto-entry (advisory engine unchanged):
    #  * momentum-trigger: only enter on a live trigger (PULLBACK / BREAKOUT_RETEST)
    #    with the option premium NOT falling — i.e. as momentum turns up, not mid-run.
    #  * no-chase: skip when the move looks already-extended / trap-like, using the
    #    engine's own buy-trap probability and premium-health read.
    auto_trade_momentum_entry: bool = False
    auto_trade_no_chase: bool = True
    auto_trade_no_chase_trap_prob: float = 60.0   # skip entry when buy_trap_prob >= this
    # Safe (risk-based) position sizing: size each auto entry from capital and
    # risk_per_trade_pct so ONE stop loses only that % — then cap by max_lots.
    # This is the "safest lots" behaviour; turn off to always use max_lots.
    auto_trade_safe_sizing: bool = True
    # Risk-balanced sizing. With max_lots pinned at 1, every trade is one lot
    # whatever the instrument, so the rupee risk is set by contract size rather
    # than by choice: one CRUDEOIL option lot deploys ~Rs27,000 while one SENSEX
    # lot deploys ~Rs2,000. That is why a 4.2% SENSEX win paid Rs42 and a 4.3%
    # CRUDEOIL loss cost Rs1,200 in the same session — the win rate was fine and
    # the rupees were not. Turning this ON lifts the 1-lot cap and lets the
    # risk-based sizer equalise the rupee risk across instruments, still bounded
    # by the capital guard and the ceiling below. Default OFF: it increases
    # position size, so it should be switched on deliberately.
    auto_trade_risk_balanced_lots: bool = False
    auto_trade_balanced_max_lots: int = 10    # hard ceiling when balanced sizing is on
    # Per-instrument overrides for capital budget and lots. Keyed by instrument
    # name (e.g. "NIFTY", "CRUDEOIL"). When an instrument has an entry here it
    # uses THAT capital budget for its own capital guard and THAT lot count as
    # the base size (instead of the global ``capital`` / ``auto_trade_max_lots``).
    # The live max-lots / max-order-value safety caps still apply on top. Set at
    # runtime from the Auto-Bot panel; env override accepts JSON. Empty = use the
    # global values for every instrument (prior behaviour).
    auto_trade_instrument_capital: dict[str, float] = {}
    auto_trade_instrument_lots: dict[str, int] = {}
    # Per-instrument FIXED stop distance in OPTION-PREMIUM POINTS, keyed by the
    # instrument's short name (e.g. {"NIFTY": 15, "CRUDEOIL": 30}). When an
    # instrument has an entry here, Auto-Buy sets its hard stop to
    # ``entry_premium − points`` — a fixed, predictable rupee risk
    # (points × lot_size × lots) — INSTEAD of the ``auto_trade_max_stop_pct``
    # percentage. Instruments left out fall back to the % / engine stop. Set at
    # runtime from the dashboard; env override accepts JSON. Empty = % behaviour.
    auto_trade_stop_points: dict[str, float] = {}
    # Per-instrument FIXED PROFIT target in OPTION-PREMIUM POINTS, keyed by the
    # instrument's short name (e.g. {"NIFTY": 12, "CRUDEOIL": 20}). When set,
    # Auto-Buy exits the moment the premium reaches ``entry + points`` — it does
    # NOT wait for T1/T2/T3. Instruments left out fall back to the T1/T2/T3 ride
    # model. Set at runtime from the dashboard; env override accepts JSON.
    auto_trade_target_points: dict[str, float] = {}
    # The same target expressed as a % OF PREMIUM, which is what the setting
    # should have been: a flat 2 points is 0.7% on a ₹262 Crude option and 9% on
    # a ₹25 SBIN one, so one number cannot mean the same thing on two contracts.
    # Takes precedence over ``auto_trade_target_points`` where both are set.
    auto_trade_target_pct: dict[str, float] = {}
    # Hard floor on that early exit, as a multiple of the STOP distance. Journal
    # evidence: point-targets were banking +0.7% to +2.3% while the stop risked
    # ~8%, i.e. roughly 1:5 against — a 67% win rate still lost money. The early
    # exit may never fire below this multiple of the risk being taken.
    auto_trade_min_target_r: float = 1.0
    # Cheapest option premium the bot may buy. Below this a contract cannot be
    # risk-managed: two ticks on a ₹7 option is ~28%, so no stop is meaningful
    # and a capital-based size buys an absurd number of lots.
    auto_trade_min_premium: float = 20.0
    # Seconds to wait after an exit before re-entering the SAME instrument. Stops
    # the bot laddering one move (14 CRUDEOIL / 6 SENSEX signals in a session),
    # paying entry and exit costs on every rung.
    # 900s, not 300s: nine Crude signals between 20:33 and 21:27 produced five
    # stops at exactly -8%, the engine re-expressing a refused view on a new
    # strike each time. Five minutes was short enough to allow the ladder.
    auto_trade_reentry_cooldown_sec: float = 900.0
    # A STOP earns a longer wait than a target — the view has just been proven
    # wrong, not completed.
    auto_trade_post_stop_cooldown_sec: float = 1800.0
    # No entries in the first N minutes of a session. An option premium gaps
    # straight through its stop in the opening auction volatility: measured
    # 13 Aug, four such entries exited at -47.8% / -22.4% / -16.7% / -15.9%
    # against an 8% stop, costing Rs6,997 of a Rs16,834 losing day. A stop
    # distance cannot fix a gap; not holding the position can.
    auto_trade_no_entry_open_minutes: float = 15.0
    # Refuse an auto-entry ABOVE this confidence. Default 100 = off. Three
    # consecutive sessions measured the score inverted at the top end (13 Aug:
    # >=95% -> 8 trades, 2 wins, -Rs10,712), so the knob exists and is
    # documented, but the score itself is not silently rewritten.
    auto_trade_max_confidence: float = 100.0
    # Consecutive ticks an option premium may sit at exactly the same value
    # before the bot treats the quote as a frozen feed and refuses to enter.
    auto_trade_stale_feed_ticks: int = 12

    # --- Missed-opportunity recorder (measurement only, never a trading input).
    # Follows the leg each refused setup would have bought and logs what it did,
    # tagged with the gate that refused it, so a gate can be judged on outcomes
    # instead of on argument. Writes one JSONL line per closed window.
    missed_opportunity_log: bool = True

    # --- Execution-funnel and feed-profile recorders (measurement only) -------
    # Both write JSONL under ``data_dir`` and are read by nothing that trades.
    # The funnel file answers "why did the book stop at N entries?" after a
    # restart; without it the counters die with the process and the question can
    # only be guessed at. The feed profile snapshots what the feed cost and
    # delivered, so the effect of a smaller deep watchlist is measured on real
    # sessions rather than estimated from arithmetic.
    exec_funnel_log: bool = True
    feed_profile_log: bool = True
    feed_profile_interval_sec: int = 300

    # --- Signal Board Journal (measurement only) ------------------------------
    # An append-only record of every Signal-tab call and of what each call went
    # on to do. Read by the Journal tab and by the research layer; read by
    # nothing that decides, sizes or places a trade.
    signal_journal_log: bool = True

    # --- Phase 17 vehicle evidence + A+ paper (research only) -----------------
    # Captures the option book at the tick that produced the decision, both CE
    # and PE, for every eligible candidate rather than only for A+ ones. The
    # earlier study wrote the chain against the last completed bar and could use
    # 48 of 3,448 legs at a 60s median gap; capturing from the chain the engine
    # has already fetched costs no broker call and makes the timestamps agree.
    # Nothing here can decide, size, delay or place a trade.
    phase17_capture: bool = True
    # Comma-separated instruments eligible for capture. Empty means the Phase 20
    # research universe — indexes, MCX commodities and optionable equities — so a
    # new registry name is studied instead of silently left unmeasured. The old
    # index-only default rested on 0.93 points of median favourable travel on
    # single stocks against a ~2.4-point round trip, but that was measured on the
    # underlying and is evidence about direction, not about the vehicle. Set this
    # explicitly to narrow capture (e.g. to save storage on a slow disk).
    phase17_universe: str = ""
    # Concurrently tracked legs, counting BOTH sides of each candidate. The path
    # data (MFE/MAE/time-to-T1) needs each open leg re-read every tick, and the
    # broker's rate limiter is the binding constraint — the history scan already
    # needed 62 retries on one series. Legs beyond the budget are recorded as
    # TRACKING_DROPPED rather than silently omitted.
    phase17_track_budget: int = 60
    # How long a tracked leg is followed before it resolves as TIMEOUT.
    phase17_follow_minutes: int = 90
    # Strikes either side of ATM recorded per observation (both types).
    phase17_window_steps: int = 2
    # A+ paper simulation. Prices entries at the ask and exits at the bid off
    # recorded quotes and writes JSONL. There is no order path in this module.
    phase17_paper: bool = True
    # Preferred and watch rows on the A+ board. Zero qualifying candidates is a
    # valid day and the board is allowed to be empty.
    phase17_board_preferred: int = 5
    phase17_board_watch: int = 5

    # --- Phase 50 research-recorder tier cadence ------------------------------
    # The sampled tier (the four expensive MCX books, CRUDEOIL excluded) may be
    # re-read every 15 seconds instead of every 60. Research capture only: the
    # production scanner, the signal, the broker and the order path do not read
    # this flag. Measured over 2026-09-17 the 60-second gate left 47 of 86
    # eligible events unable to answer a 15-second observation grid, and the
    # replayed 15-second schedule cost fewer attempts and a lower peak minute
    # than the capture already spends, so the change reduces rather than adds
    # feed work. Set False to restore the 60-second gate without a code change —
    # the documented rollback for tick-latency, stall or resource pressure.
    phase50_shadow_slow_tier: bool = True

    # --- Phase 18 closing-auction (CAS) research + paper ----------------------
    # SEBI's Closing Auction Session discovers the closing price between 15:15
    # and 15:30, and the underlying has been dislocating violently inside it.
    # This block captures that window and paper-trades it. It is a SEPARATE
    # research strategy (strategy = CAS): it has no order path, cannot reach the
    # broker, and never changes the normal Option Signal, its gates, its strikes
    # or its exits. The five-year pool contains no CAS-regime data at all, so the
    # only evidence that counts here is captured live.
    phase18_cas_capture: bool = True
    # Instruments captured in the window. Indices only, same reasoning as
    # Phase 17: single stocks cannot pay for their own round trip.
    phase18_universe: str = "NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,SENSEX"
    # Ladder recorded per observation, both CE and PE at every rung. ATM +/- 3
    # plus two deliberately far rungs, because the whole zero-to-hero claim lives
    # in strikes the normal engine would never buy.
    phase18_ladder_steps: int = 3
    phase18_far_otm_steps: int = 6
    phase18_very_far_otm_steps: int = 10
    # Seconds between samples inside the window. The move being studied happens
    # in five minutes, so a one-minute cadence would miss it entirely; this is
    # bounded by the broker's rate limiter, not by ambition.
    phase18_sample_seconds: int = 5
    # CAS paper book. Buys at the ask, exits at the bid, off recorded quotes.
    # There is no order path in this module and no real-money mode to enable.
    phase18_paper: bool = True
    # Hold a CAS leg overnight and resolve it against the next session's open.
    phase18_overnight: bool = True
    # Slippage scenarios in ticks, charged against the trader on both legs. A
    # research assumption, reported separately from measured costs, never merged
    # into a single "net" that hides which part was assumed.
    phase18_slippage_ticks: str = "0,1,2,3"
    phase18_tick_size: float = 0.05
    # Next-day gap classification thresholds, as a percentage of the previous
    # close. Configurable and printed in the reports, because a gap study whose
    # threshold is buried in code cannot be argued with.
    phase18_gap_flat_pct: float = 0.15
    # Concurrently tracked CAS paper legs. Small on purpose: the window is
    # twenty minutes and every open leg is re-read every sample.
    phase18_track_budget: int = 24

    # --- Phase 19 futures paper book + readiness (measurement only) -----------
    # Futures Signal has always been research-only with no costed execution, so
    # "resolved futures trades" accrued at zero per session forever. This book
    # gives it a lifecycle: valid plan -> paper entry at an executable side ->
    # paper exit -> resolved, with costs decomposed. It has no order path of any
    # kind and does not change the futures plan, its levels or its gates.
    phase19_futures_paper: bool = True

    # --- Signal lifecycle ledger (measurement only) ---------------------------
    # One immutable chain per signal — generation, classification, plan, plan
    # validation, dashboard publication, visibility, paper eligibility,
    # execution, fill, exit, resolution — so a call that reaches the Reports
    # journal but never the dashboard has a recorded stage and reason instead of
    # simply being absent. Written by observation points only; read by the
    # reconciliation API and the reports, never by anything that trades.
    signal_lifecycle_log: bool = True

    # --- Signal churn control (publication only, Phase 12 §1) -----------------
    # One live setup published thousands of raw events on 25 Aug: a single SILVER
    # leg alone accounted for 2,578, and 249 calls were superseded before a
    # client ever rendered them. A repeat is only re-published when something
    # material changed; every raw event is still counted, so the numbers stay
    # exact while the ledger and the dashboard stop restating an unchanged call.
    signal_churn_control: bool = True
    # A premium move this large (% of the published premium) is material.
    churn_premium_pct: float = 1.5
    # A score move this large (points of trade_score) is material.
    churn_score_points: float = 5.0
    # An entry-zone move this large (% of the zone's midpoint) is material.
    churn_zone_pct: float = 1.0
    # An unchanged call is restated at most this often, so a long-lived setup
    # still leaves a periodic trace instead of going quiet.
    churn_heartbeat_seconds: float = 300.0

    # --- Futures contract rollover (research only, Phase 12 §11) --------------
    # The price feed quotes the near month, so the futures RESEARCH engine could
    # only ever score the contract it was already standing on: on 25 Aug 51 of
    # its 76 refusals were EXPIRY_TOO_CLOSE, i.e. the whole expiry week produced
    # no research at all. Inside this many days to expiry the research plan is
    # written on the next contract in the chain instead. Research only — no
    # futures order path exists, and the quoted contract is left untouched.
    futures_rollover_research: bool = True
    futures_roll_min_days: int = 3

    # --- Futures feed freshness (Phase 12A §1-§3) -----------------------------
    # On 26 Aug the futures research engine refused 1,321 times with STALE_FEED
    # while the option feed was healthy: its freshness is measured on the newest
    # 1-minute BAR, and bars come from Angel's historical endpoint, whose budget
    # is shared by every instrument on the key (3.5s spacing), so routine
    # refreshes are deferred and the newest bar can be many minutes old even
    # while the socket pushes every second. With this on, the tail of the futures
    # series is assembled from the ticks already arriving on the WebSocket, which
    # costs no REST budget at all, and the historical endpoint is left to warm-up
    # and backfill. The 90s freshness threshold itself is NOT touched.
    futures_ws_bars: bool = True
    # Feed audit only (never a gate): bar-age bands used to label a contract
    # FRESH / AGING / STALE / DEAD in futures_feed_audit.json.
    futures_feed_audit: bool = True
    futures_feed_aging_sec: float = 90.0
    futures_feed_stale_sec: float = 300.0
    futures_feed_dead_sec: float = 1800.0

    # --- Futures shadow outcomes (research only, Phase 12 §12) ----------------
    # Follow every VALID_FUTURES_PLAN from its stated entry to a stop, a target
    # or the end of the follow window and record what happened, in index points.
    # Observation only: there is no order route, paper or live, from this path.
    futures_shadow_outcomes: bool = True
    futures_shadow_follow_minutes: int = 90

    # --- Tradability research (research only, Phase 12 §4) --------------------
    # Whether a signal was economically takeable at all, measured and reported
    # beside the call — never applied as a production block. The seeds come from
    # the 25 Aug recorded book, where the median spread was 0.80% of premium on
    # index options, 1.61% on MCX and 9.52% on single stocks: at 9.52% a 1R move
    # is handed to the spread, which is why the family studies (§8-§10) are
    # separated rather than pooled.
    tradability_research: bool = True
    # Phase 12A §15 — publish the research labels on the card. DISPLAY ONLY: the
    # signal, score, levels and actionability are unchanged whether this is on or
    # off, and turning it off removes badges rather than restoring a decision.
    signal_badges: bool = True
    tradability_spread_caution_pct: float = 2.0
    tradability_spread_untradable_pct: float = 5.0
    # Spread as a fraction of the plan's own risk. Above the second number the
    # round trip costs more than half of what the plan risks.
    tradability_spread_risk_caution: float = 0.25
    tradability_spread_risk_untradable: float = 0.50
    # A quote older than this is not evidence of a current spread.
    tradability_stale_quote_sec: float = 30.0
    # Expected room to the first target, in R. Below this the plan is paying a
    # spread for a move too small to cover it.
    tradability_min_room_r: float = 1.0

    # --- entry location (research only, Phase 12A §11) ------------------------
    # How far the premium has already expanded from the setup's own premium
    # before the signal printed, in percent, and how many ATRs past its
    # reference the underlying already is. Both are research bands read only by
    # the entry-quality label; the production chase guard is unchanged.
    entry_quality_research: bool = True
    entry_quality_expansion_pct: float = 15.0
    entry_quality_atr_extension: float = 1.5

    # --- entry state + hold window (research only, Phase 13A/13B) -------------
    # BUY_NOW / WAIT_PULLBACK / WAIT_CONFIRMATION / INVALIDATED beside the call,
    # and the measured hold window that replaces the heuristic "Hold: ~N min" on
    # the card as a second, evidence-backed figure. DISPLAY ONLY: the production
    # signal, chase guard, stop, targets, exits and expected_holding_minutes are
    # untouched whether these are on or off.
    entry_location_research: bool = True
    # Distance past the published entry zone, in R, that is still close enough to
    # take now rather than name a cheaper level.
    entry_location_tolerance_r: float = 0.15
    # Pullback the fallback level asks for when the engine published no zone,
    # in R, so the level scales with the trade's own risk.
    entry_location_pullback_r: float = 0.2
    # Past this distance from the zone (in R) a pullback fill would be a
    # different trade from the one planned, so the state is INVALIDATED.
    entry_location_invalidate_r: float = 0.75
    # Fraction of the plan's own distance to T1 that must still be unpaid, once the
    # premium has moved above the planned entry, for the call to be worth taking.
    # A fraction, not an absolute floor in R: this engine places T1 near 0.9R by
    # design, so a 1R floor invalidated 63% of a three-session book at signal time.
    entry_location_min_room_frac: float = 0.5
    # How long a named pullback level stands before the signal is a missed trade.
    entry_location_wait_minutes: int = 5
    # Trap/sweep probability at which a breakout is more likely a liquidity grab,
    # so the state asks for a confirming close instead of a price.
    entry_location_trap_pct: float = 70.0
    # Phase 13B — the hold window is read from the recorded outcome ledger, and
    # a cohort needs at least this many resolved calls before its own quantiles
    # are used instead of the pooled ones. Below it the window says so.
    hold_window_research: bool = True
    hold_window_min_cohort: int = 20

    # --- A+ shadow classifier (research only, Phase 12 §13) -------------------
    # A deterministic composite label over facts already recorded beside a call:
    # data quality, tradability, family, entry quality, vehicle quality, room and
    # spread. There is no fitted probability and no learned weight anywhere in
    # it, and it neither gates nor scores a production trade — §15 requires 20
    # sessions with 5 chronological holdout before any of this is even validated.
    a_plus_shadow: bool = True
    # Board score a call must already carry before A+ will look at it at all.
    a_plus_min_score: float = 65.0
    # Spread ceilings for an A+ label, tighter than the CAUTION seeds above: A+
    # is meant to name the small set of calls whose economics are not in doubt.
    a_plus_max_spread_pct: float = 1.5
    a_plus_max_spread_over_risk: float = 0.15
    # Room to the first target, in R, and the delta band a leg must sit in for
    # the option to track the underlying it is expressing.
    a_plus_min_room_r: float = 1.5
    a_plus_min_delta: float = 0.35

    # --- Shadow paper book (ungated, measurement only) ------------------------
    # A SECOND paper ledger that takes every Signal-board call which carried a
    # leg, a premium, a stop and a target — including the calls the gates
    # refused — and records the blocker it was refused for. It exists to answer
    # "do the gates cost money or save it?" with a measurement instead of an
    # argument, which is the only honest way to decide whether to remove one.
    #
    # It shares nothing with the gated auto-trader: its own capital pot, its own
    # ledger file, its own exit bookkeeping, and NO order path of any kind. It
    # cannot open, size, veto or delay a real or gated paper position.
    shadow_book_enabled: bool = True
    shadow_book_capital: float = 500000.0
    shadow_book_risk_per_trade_pct: float = 1.0
    # Ceiling on lots per shadow position. Sanity only: an ungated book must
    # still not book a thousand lots off a one-rupee stop distance.
    shadow_book_max_lots: int = 10
    # How long a shadow position is followed before it resolves as TIMEOUT.
    shadow_book_follow_minutes: int = 90

    # --- Opportunity gate: is this instrument moving enough to be worth it? ---
    # Scored in PREMIUM PERCENT, never underlying points: the journal's losing
    # pattern was a correct direction on an instrument whose premium could not
    # travel far enough to cover an 8% stop plus costs.
    opportunity_gate_enabled: bool = True
    # Net-of-cost premium move a normal (3-ATR) leg must be worth. Below this the
    # board shows WAIT with the measured number instead of a BUY.
    opportunity_min_move_pct: float = 4.0
    # Composite 0-100 floor (movement 50, ADX 25, volume surge 15, premium 10).
    opportunity_min_score: float = 45.0
    # Round-trip option costs as a share of premium, used by the scorer to judge
    # whether the move actually pays. STT is charged on the sell side only.
    option_stt_sell_pct: float = 0.10
    option_txn_pct: float = 0.05
    # One-click LIVE auto-buy: when True (and the bot is live-armed) the sizing
    # budget is taken from the broker's LIVE available cash instead of the fixed
    # ``capital`` figure, so position size follows your real balance with no
    # manual capital entry. The live max-lots / max-order-value caps still apply.
    auto_trade_use_live_balance: bool = False
    # Ride the winner: instead of dumping the whole position at Target 1, hold and
    # let the ratcheting trailing stop (below) run the trade, exiting on a pullback
    # to the trail or the hard stop. This is the "trail it up" behaviour requested.
    auto_trade_ride_trail: bool = True
    # Beyond Target 3: instead of leaving T3 as the floor (which gives the whole
    # excess back on a reversal), keep ratcheting the locked floor upward as the
    # move extends and exit on the first tick that falls back a step. This lets a
    # runner keep running past T3 while banking the gain in steps.
    #
    # The step is a PERCENTAGE OF THE PREMIUM, not fixed points: a fixed 5 points is
    # under 2% of a ₹300 option (inside the noise — knocked out immediately) and 12%
    # of a ₹40 one (far too loose). A percentage scales with the contract.
    #
    # A trail can never exit AT the peak — one step is always given back — so a
    # smaller percentage keeps more of the peak but is stopped out by smaller dips.
    auto_trade_beyond_t3_ratchet: bool = True
    auto_trade_beyond_t3_step_pct: float = 4.0
    # Floor on the computed step so a very cheap premium still gets a workable gap.
    auto_trade_beyond_t3_min_step: float = 1.0
    # Trailing-stop distance as % below the running peak premium (ratchets up only,
    # never down). ~10% ≈ the "8–10 points" trail idea for typical premiums.
    auto_trade_trail_pct: float = 10.0
    # Pre-T1 peak trail: while a trade is green but has NOT yet reached Target 1,
    # don't sit there hoping for T1 (or slide all the way to the hard stop). Once
    # it's advanced at least ``pre_t1_trail_arm_pct`` of the way from entry toward
    # T1, arm a giveback trail = ``pre_t1_trail_giveback_pct`` of the run from
    # entry to the running high (floored at a couple of points). If price gives
    # that back, exit at the locked peak instead of waiting for T1. The T1/T2/T3
    # target-floor lock takes over once a target is actually reached. Off → prior
    # behaviour (hard stop only until T1).
    auto_trade_pre_t1_trail: bool = True
    auto_trade_pre_t1_trail_arm_pct: float = 40.0     # arm once this % of the way to T1
    auto_trade_pre_t1_trail_giveback_pct: float = 30.0  # exit on giving back this % of the run from the peak
    # Also arm the pre-T1 trail as soon as the premium is at least this % ABOVE
    # entry (independent of how far T1 is). This catches a small green move that
    # rolls over before it ever gets close to T1 — so the trade is cut in profit
    # instead of drifting all the way back to the hard stop.
    auto_trade_pre_t1_trail_arm_profit_pct: float = 6.0
    # Once the pre-T1 trail has armed (we've seen real profit), ratchet the hard
    # stop up to at least break-even (entry). A trade that went green can then
    # never turn into a full stop-loss — worst case is roughly flat.
    auto_trade_breakeven_after_arm: bool = True
    # Cap the initial hard-stop distance below entry premium, as a % of entry
    # (0 = off, use the signal engine's stop as-is). When >0, a stop further than
    # this is tightened to this distance so a single auto trade can't bleed a big
    # premium down to a far stop. The one-click Live Auto-Buy sets this.
    auto_trade_max_stop_pct: float = 0.0
    # Quick-scalp exit for BIG positions on a WEAK signal: when the position is at
    # least ``quick_scalp_lots`` lots and the entry confidence was below
    # ``quick_scalp_conf`` (%), don't hold for T1 — once the premium is at least
    # ``quick_scalp_points`` above entry and ticks down off its peak, bank the
    # small profit. Protects size when conviction is low. 0 lots = feature off.
    auto_trade_quick_scalp_lots: int = 0
    auto_trade_quick_scalp_points: float = 3.0
    auto_trade_quick_scalp_conf: float = 85.0
    # Minimum give-back (premium points) for the pre-T1 profit trail. Small = exit
    # closer to the stall/peak (banks more of the shown profit); larger = more room.
    auto_trade_pre_t1_trail_min_giveback: float = 2.0
    # The same floor as a % of the entry premium, which is the unit it should have
    # been in: 2 points is 0.8% of a ₹255 CRUDEOIL option but 13% of a ₹15 SBIN one
    # — wider than the whole stop, so on a cheap contract the trail could never fire
    # and the trade drifted to the hard stop. Noise scales with the premium, so the
    # floor must too. When > 0 this replaces the points value; 0 = points only.
    auto_trade_pre_t1_trail_min_giveback_pct: float = 0.8
    # Daily profit GOAL (₹). When day P&L >= this, the bot stops opening NEW trades
    # for the day (locks the gain). 0 = disabled. This is a stop-FOR-the-day, NOT a
    # guaranteed minimum — losing days are normal and cannot be ruled out.
    daily_profit_target: float = 0.0
    # --- LIVE auto-execution arm (DANGER: real money) ---
    # The auto-trader places REAL Angel orders ONLY when ALL of these hold:
    # trade_mode == 'live'  AND  auto_trade_enabled  AND  auto_trade_allow_live.
    # If trade_mode is live but this is False, the bot stays hands-off (it will not
    # place any order) — an explicit second switch so live orders are never a
    # surprise. Default False.
    auto_trade_allow_live: bool = False
    # Hard notional cap per live auto entry (premium × lot_size × lots, ₹). A live
    # auto-BUY above this is rejected — a blunt blast-radius limit on any one order.
    auto_trade_live_max_order_value: float = 25000.0
    # Hard cap on lots for a LIVE auto entry, independent of the paper max_lots.
    auto_trade_live_max_lots: int = 1

    # --- Zero-to-Hero expiry sleeve (SPECULATIVE, HIGH RISK, SEPARATE budget) ---
    # A deliberately tiny, isolated punt on EXPIRY DAY only: buy a cheap far-OTM
    # option in the day's bias direction. It can multiply — but usually expires
    # WORTHLESS. It has NO measured edge/backtest and is NOT the safe strategy.
    # It is walled off from the core capital: it can never risk more than
    # ``zero_to_hero_budget`` in a day, and it is advisory-only unless separately
    # armed for live. Off by default.
    zero_to_hero_enabled: bool = False
    # Fixed money you are willing to LOSE ENTIRELY on the sleeve, per day (₹).
    zero_to_hero_budget: float = 5000.0
    # Cheap-premium window for the far-OTM leg (₹ per unit). Keeps it a lottery
    # ticket, not an expensive near-the-money option.
    zero_to_hero_min_premium: float = 2.0
    zero_to_hero_max_premium: float = 40.0
    # How far out of the money to reach (number of strike steps beyond ATM).
    zero_to_hero_otm_steps: int = 4
    # Only fire on the actual expiry day. When the feed can't supply an expiry
    # date (e.g. simulated), fall back to days_to_expiry <= this many days.
    zero_to_hero_expiry_only: bool = True
    zero_to_hero_expiry_days_fallback: int = 1

    # --- Risk Management v2 (controlled, feature-flagged, fully reversible) ---
    # A stop-loss + PRE-TRADE VALIDATION layer that runs AFTER the frozen engine.
    # It does NOT change entry logic, confidence, or strategy rules — it only (a)
    # refines the premium stop with a volatility/Greeks-aware model and (b) REJECTS
    # a BUY (→ AVOID, with a clear reason) when the trade fails validation instead
    # of forcing an unrealistic stop. Turn off to restore exact prior behaviour.
    risk_v2_enabled: bool = False
    # Reject a BUY whose stop implies losing MORE than this % of the premium
    # (the stop is refined, never force-clamped — an out-of-limit trade is rejected).
    risk_v2_max_premium_loss_pct: float = 40.0
    # Reject a BUY whose reward:risk (Target 1 vs stop) is below this.
    risk_v2_min_reward_risk: float = 1.2
    # Liquidity floors — reject a BUY on an option thinner than these.
    risk_v2_min_oi: int = 0                 # 0 = disabled (many feeds lack live OI delta)
    risk_v2_min_volume: int = 0             # 0 = disabled
    # Max acceptable bid/ask spread as % of premium. Only enforced when the feed
    # actually provides bid/ask; otherwise the spread check is skipped (honest).
    risk_v2_max_spread_pct: float = 5.0

    # --- Execution Intelligence Layer (Phase 3.6; feature-flagged, ADVISORY) ---
    # Runs AFTER Risk v2 and BEFORE the final recommendation. It NEVER changes the
    # BUY/WAIT direction, confidence, or strategy — it only advises execution
    # quality: enter now / wait for a better entry / change strike / skip. Off by
    # default → the app behaves exactly as before.
    execution_intelligence_enabled: bool = False
    # Distance from VWAP (in ATR units) beyond which entry is "extended" and a
    # pullback is advised rather than chasing.
    exec_extended_atr: float = 1.5
    # Overall trade-survival score (0..100) below which the layer advises against
    # entering now (WAIT / CHANGE STRIKE / SKIP).
    exec_survival_min: float = 60.0
    # How many strikes either side of the recommended one the Strike Selector
    # evaluates when looking for a more resilient leg.
    exec_strike_search: int = 2

    # --- Early Momentum Advisory Engine (feature-flagged, ADVISORY, parallel) ---
    # A SECOND, independent engine that runs alongside the frozen confirmation
    # engine. It flags an EARLY BUY when a NEW trend is *starting* (first higher-
    # low + VWAP reclaim + rising volume + improving option-chain + underlying
    # confirmation) — i.e. materially earlier than the confirmation BUY, WITHOUT
    # trying to catch the exact bottom. It NEVER changes the frozen engine's
    # signal/confidence/strategy; it only adds an advisory + a Signal-Delay
    # measurement. Off by default → the app behaves exactly as before.
    early_momentum_enabled: bool = False
    # Minimum aligned-evidence score (0..100) before an EARLY BUY is advised.
    early_momentum_min_score: float = 60.0
    # An EARLY entry must still be near the start of the move: reject it once price
    # is already more than this many ATR beyond VWAP (i.e. no longer "early").
    early_momentum_max_atr_from_vwap: float = 1.2
    # A confirmation BUY is flagged "late" once this % of the swing move is gone.
    signal_delay_late_pct: float = 50.0

    # --- Early-Early Mode (Stage 2.5) — a SEPARATE, feature-flagged ADD-ON ---
    # An even-earlier advisory tier that sits BETWEEN Stage 2 (Structure) and
    # Stage 3 (Early Buy). It fires when MULTIPLE evidence pieces align (first
    # higher-low + a bullish VWAP-reclaim candle + above-average volume +
    # positive EMA slope + improving option-chain + adequate room before
    # resistance) WITHOUT waiting for the full Stage-3 score gate — so it is
    # earlier but higher-risk. Completely isolated from the frozen engine and
    # the existing Early Momentum logic; advisory-only, never auto-executed.
    # OFF by default → the tool behaves exactly as before unless enabled.
    early_early_enabled: bool = False
    # Minimum number of the six evidence pieces that must pass to issue it.
    early_early_min_evidence: int = 5
    # Minimum reward:risk (to Target 1) required before an Early-Early is issued.
    early_early_min_rr: float = 2.0
    # Reject if the option's bid/ask spread exceeds this % of its mid (illiquid).
    early_early_max_spread_pct: float = 8.0
    # Require at least this many ATR of room to the next resistance/support.
    early_early_min_room_atr: float = 1.5

    # --- Quick Scalp Engine — a SEPARATE, feature-flagged ADD-ON ---
    # A dedicated short-duration scalp engine that targets small 8–15 point
    # OPTION-premium moves off EXHAUSTION / BOUNCE setups (a selling/buying
    # climax, ATR extension away from VWAP, support/resistance rejection, a
    # volume spike, the first higher-low / lower-high, a micro-structure break
    # and option-premium stabilization). Completely independent of the frozen
    # engine, Early Momentum and Early-Early; advisory-only, NEVER auto-executed.
    # OFF by default → the tool behaves exactly as before unless enabled.
    scalp_enabled: bool = False
    # Minimum number of the (up to) seven scalp conditions that must align.
    scalp_min_conditions: int = 5
    # Fixed scalp objective, in OPTION-PREMIUM points (8–15 is the sweet spot).
    scalp_target_points: float = 10.0
    # Minimum reward:risk required before a scalp is issued (tight stop model).
    scalp_min_rr: float = 1.5
    # ATR of extension away from VWAP that flags exhaustion (bounce setup).
    scalp_min_atr_extension: float = 1.5
    # Move the stop to breakeven once premium travels this fraction of target.
    scalp_breakeven_frac: float = 0.5
    # Hard time-stop: exit after this many candles if the target is not hit.
    scalp_max_hold_candles: int = 6

    # --- Flow Engine (Candle-Flow) — a SEPARATE, feature-flagged ADD-ON ---
    # A deliberately SIMPLE 1-minute candle-flow advisory on its own dashboard
    # tab: BUY the moment the 1-min candle turns/stays GREEN in the direction the
    # market is actually moving, STAY while it keeps printing green, and flash
    # EXIT the moment it rolls RED (or gives back points). It auto-picks the side
    # that is moving with the 1-min candle (CE when up / PE when down) and SWITCHES
    # sides when the flow flips. It uses candlestick patterns to project whether
    # the next candle is more likely green or red. NONE of the trend gates
    # (VWAP-distance, score, room) apply — this is the "ride the green candle"
    # tool the user asked for. Completely independent of the frozen engine, Early
    # Momentum, Early-Early and Quick Scalp; advisory-only, NEVER auto-executed.
    # OFF by default → the tool behaves exactly as before unless enabled.
    flow_enabled: bool = False
    # A candle counts as a decisive GREEN/RED only when its body is at least this
    # fraction of its range (filters indecision wicks/dojis from flipping state).
    flow_min_body_frac: float = 0.30
    # EXIT once the option premium gives back this many points from its peak while
    # the candle is turning against us (the "6–10 points" roll-over the user asked
    # to be warned about). A strong reversal candle exits immediately regardless.
    flow_giveback_points: float = 8.0
    # De-whipsaw confirmation: how many consecutive DECISIVE with-flow 1-min
    # candles must line up before a fresh BUY is issued OR the side is flipped /
    # a colour-turn EXIT fires. 1 = the old twitchy behaviour (flips on a single
    # candle); 2 (default) ignores a lone opposite candle so a choppy/sideways
    # tape sits at WAIT instead of flip-flopping CE↔PE every minute. Giveback and
    # strong reversal PATTERNS still force an immediate EXIT regardless.
    flow_confirm_candles: int = 2
    # "Sticky BUY" window: for this many 1-min candles after a fresh entry (BUY or
    # SWITCH) the board keeps showing the big BUY / JUST ENTERED tag before it
    # settles into HOLD — so a one-candle BUY flash can actually be seen/acted on.
    flow_sticky_candles: int = 3
    # Paper lots used to convert the Flow engine's per-leg POINTS into a ₹ P&L for
    # the Flow history/analysis table. Flow is ADVISORY/PAPER only — this never
    # sizes or places a real order; it only scales the displayed paper P&L so you
    # can judge it in rupees (points × lot_size × flow_paper_lots).
    flow_paper_lots: int = 1
    # Flow strength band. Flow "strength" (0–100) rises with the with-flow candle
    # streak + body size. When > 0, only ENTER a leg while strength >= this floor,
    # and EXIT an open leg the moment strength drops back below it (i.e. the move
    # is weakening) — even before a red candle. 0 = disabled (old behaviour).
    flow_strength_floor: float = 70.0
    # When True, an open leg only SWITCHES to the opposite side on a STRONG
    # confirmed reversal candle (evening/morning star, engulfing, etc.). A plain
    # run of opposite candles still EXITS the leg to cash (per the strength/giveback
    # rules) but does NOT auto-flip — so the day's chosen side is sticky. When
    # False, it flips on `flow_confirm_candles` decisive opposite candles (old).
    flow_switch_strong_only: bool = True
    # When True, a fresh BUY also requires the candlestick projection to support
    # the side (next candle LIKELY_GREEN for the leg). False = streak alone.
    flow_require_pattern: bool = True
    # Minimum entry economics (app/execution/flow_economics.py). A fresh Flow BUY
    # is downgraded to WAIT unless the move the leg can expect — its delta times
    # the recent typical one-minute range of the underlying — is at least
    # `flow_cost_multiple` times its OWN round-trip cost (brokerage + statutory
    # charges + the quoted spread, or the family median when no book was quoted).
    # The audit this comes from: 1,169 recorded legs, +₹26,251 gross against
    # ₹93,512 of brokerage, and 620 legs paying a round trip worth over a tenth
    # of their entry premium of which not one was a net winner. A leg whose
    # economics cannot be measured is NOT refused — it is reported as unmeasured.
    flow_min_economics_enabled: bool = True
    flow_cost_multiple: float = 3.0
    # How many recent 1-min candles the typical range is taken over (median).
    flow_expected_move_candles: int = 10

    # --- AI Command Center (V4) — a NEW read-only dashboard tab that aggregates
    # the REAL signals the engines already compute (regime, VIX, breadth,
    # expected move, ranked opportunities, lifecycle, position health, exit) into
    # one glanceable page. It computes nothing new and fabricates no numbers:
    # any metric without enough real data is shown as "calibrating / N/A".
    # Advisory-only, changes NOTHING when off. OFF by default.
    ai_command_center: bool = False

    # --- Quantum Signal (single-page dashboard) — a READ-ONLY layer that
    # collapses everything into one page and answers "is there a clean tradeable
    # move now, or stay out?". It NEVER changes the frozen engine; it gates the
    # engine's own BUY behind a regime + ADX + expected-move filter so signals
    # stop firing in chop (the 1-yr Angel backtest showed raw signals lose in
    # chop). Minimum ADX for the volatility gate to call the tape "tradeable".
    signal_gate_adx_min: float = 20.0

    # --- WhatsApp alerts (optional; off unless enabled AND provider secrets set) ---
    # meta   -> Meta WhatsApp Cloud API: WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_TO
    # twilio -> Twilio WhatsApp: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM, WHATSAPP_TO
    # All secrets are read from the environment, never hard-coded.
    whatsapp_enabled: bool = False
    whatsapp_provider: str = "meta"
    # Send a detailed WhatsApp BUY alert only when engine conviction is at least
    # this %. Matches the dashboard's high-confidence gate (default 95).
    whatsapp_signal_min_confidence: float = 95.0

    # --- News source ---
    # simulated | rss  (rss fetches public headlines; falls back if unreachable)
    news_source: str = "simulated"
    news_rss_feeds: str = ""  # comma-separated RSS URLs when news_source=rss

    # --- Economic-event calendar / pre-event guard ---
    # Warn (and mildly dampen confidence) around high-impact scheduled events
    # (EIA crude inventory, US NFP, F&O expiry, plus anything in an ICS you add).
    event_guard_enabled: bool = True
    # Minutes BEFORE a high-impact event to start warning / dampening.
    event_guard_before_minutes: int = 20
    # Minutes AFTER a high-impact event to keep warning (initial volatility).
    event_guard_after_minutes: int = 10
    # Optional public ICS calendar URL (e.g. an economic-calendar .ics you trust)
    # merged with the built-in recurring events. Leave empty to use built-ins only.
    events_ics_url: str = ""

    # --- Stock fundamentals (NSE official, best-effort) ---
    # Fetch delivery %, results date and FII/DII from NSE's public endpoints.
    # Best-effort: NSE may rate-limit/block datacenter IPs, so this degrades
    # gracefully to "unavailable" and never blocks the dashboard.
    fundamentals_enabled: bool = True

    # --- Persistence & self-learning ---
    # Directory for the SQLite history DB and the learned-weights file.
    data_dir: str = "data"
    # Notional starting capital for the PAPER account (Account tab equity is
    # this + cumulative realised paper P&L). Paper-only; never a real balance.
    paper_capital: float = 100000.0
    # Record live futures candles + option-chain snapshots so the backtest can
    # replay REAL captured history instead of a fresh synthetic series.
    store_history: bool = True
    # Auto-record each completed live bar (futures + option chain) into the
    # RESEARCH store during the tick loop, so the backtest/analytics can run on
    # REAL captured history with no manual step. Gated to a real broker feed by
    # default so the research DB is never polluted with simulated bars.
    research_autocapture: bool = True
    # Self-learning: re-tune each factor's vote weight from closed-trade outcomes.
    learning_enabled: bool = True
    # HOW the learning is applied. "advisory" (default, SAFE) = the engine only
    # COLLECTS stats and RECOMMENDS weight changes; the live decision rules stay
    # fixed (every multiplier is forced to 1.0) so the engine can't silently
    # drift / overfit. "auto" = actually apply learned multipliers, but ONLY for
    # factors with at least `learning_min_trades` closed trades (guards against
    # tuning on a tiny, statistically meaningless sample).
    learning_mode: str = "advisory"  # advisory | auto
    learning_min_trades: int = 30
    # --- Performance / Validation Mode ---
    # Validation Mode logs every closed trade with its full decision context.
    # The performance report only shows REAL metrics once (a) the feed is a real
    # broker (not the simulated demo) AND (b) at least this many trades have been
    # recorded. Otherwise it honestly reports "insufficient real data" instead of
    # fabricating win-rate / Sharpe / expectancy from synthetic ticks.
    performance_min_trades: int = 20

    # --- Research / validation platform (Phase 2) ---
    # Database URL for the research store (historical candles, signal snapshots,
    # trade history, shadow log). A "postgresql://..." / "postgres://..." URL
    # uses PostgreSQL (requires the optional `psycopg` dependency); anything else
    # (or empty) falls back to a local SQLite file under `data_dir` so the
    # platform runs out of the box with zero external services.
    db_url: str = ""
    # Shadow mode: run the frozen engine live and LOG every recommendation vs.
    # the eventual market outcome — WITHOUT placing any order. Advisory only.
    shadow_mode: bool = False
    # How many minutes ahead the shadow evaluator scores a recommendation against
    # the actual futures move (did BUY go the right way / did WAIT avoid a loss).
    shadow_horizon_minutes: int = 15

    # --- Instrument under analysis ---
    underlying_symbol: str = "MCX:CRUDEOIL"
    tradingview_symbol: str = "MCX:CRUDEOIL"
    lot_size: int = 100  # MCX Crude Oil lot size (barrels)
    tick_interval_seconds: float = 1.0
    candle_interval_seconds: int = 60  # 1-minute candles

    # --- Simulated feed parameters ---
    sim_start_price: float = 6900.0
    sim_annual_vol: float = 0.35
    sim_seed: int = 0  # 0 => random each run

    # --- Options model ---
    risk_free_rate: float = 0.065
    strike_step: float = 50.0
    strikes_each_side: int = 10
    days_to_expiry: float = 7.0

    # --- Brokerage (for net P&L) ---
    # Kept as the editable rate. It is charged PER ORDER, not per lot: a discount
    # broker bills one flat fee for the whole order however many lots it carries.
    # Charging it per lot put 40x the true brokerage on a 39-lot leg, which is why
    # 34 journal rows read as losses while the option price had actually risen.
    brokerage_per_lot: float = 20.0

    # --- Option round-trip cost model (app/analysis/option_costs.py) ----------
    # Spread charged when the feed supplied no book, as a % of premium. These are
    # the 25 Aug recorded medians, the same seeds the tradability study uses, so
    # a leg cannot read as economic in one report and uneconomic in another. A
    # measured spread always wins; these only fill the gap, and the fill is
    # labelled ASSUMED_FAMILY_MEDIAN rather than blended into the total silently.
    cost_spread_index_pct: float = 0.80
    cost_spread_mcx_pct: float = 1.61
    cost_spread_stock_pct: float = 9.52

    # --- Signal stability (avoid flicker: a concrete, actionable recommendation) ---
    # A new signal must persist this many seconds before it replaces the live one.
    signal_confirm_seconds: float = 20.0
    # Once shown, keep a signal at least this long (except urgent EXIT/crash).
    signal_min_hold_seconds: float = 30.0
    # Outcome tracking: how many signals may run concurrently on one instrument
    # before the oldest is retired. A same-direction re-signal no longer kills
    # the earlier one (that was closing signals a median of 7 minutes in, some
    # 96% of the way to T1, leaving only a quarter ever resolved); a direction
    # flip still invalidates it.
    signal_max_open_per_instrument: int = 5

    # --- Decision gate: "fewer, higher-quality trades" ---
    # A BUY is only emitted at/above this confidence — otherwise WAIT/AVOID. The
    # engine floors the effective gate at 75% (decision.py) so no signal fires
    # below the 75-80% quality bar the redesign targets; a strong 5-min trend can
    # relax it slightly (never under 75), a ranging regime tightens it further.
    buy_min_confidence: float = 78.0
    # Higher-timeframe TREND-GATE aggregation factor (in 1-min candles). The
    # frozen engine only allows CE in an UP higher-TF trend / PE in a DOWN one.
    # Default 5 → the classic 5-min gate (safe, later entries). Set to 1 to gate
    # on the 1-min trend instead (earlier BUYs the moment a green run starts, but
    # far more false signals). Fully reversible — this is the ONLY frozen-engine
    # timeframe knob; nothing else in the confirmation logic changes.
    htf_factor: int = 5
    # A BUY also requires genuine LEVEL-1/2/3 (price-action/premium/OI) evidence;
    # lagging indicators (EMA/RSI/MACD/BB) may only confirm, never trigger.
    buy_min_primary_strength: float = 0.30
    # While holding, how strong the OPPOSITE-direction read must be before the
    # engine flags a reversal ("exit CALL, flip to PUT"). Raised so ONLY a
    # genuine, confirmed turn flips the side — small wiggles no longer whipsaw
    # CALL<->PUT (the trailing stop, not a flip, handles minor adverse moves).
    reversal_min_strength: float = 0.30
    # Direction below this |net| is treated as "no clear side" -> WAIT. Stops the
    # engine flip-flopping CE/PE around a flat/neutral read.
    neutral_dead_band: float = 0.16
    # In a RANGING / choppy regime, suppress fresh directional BUYs entirely and
    # WAIT. Ranges are where targets stall and signals whipsaw the most; we only
    # take TRENDING / BREAKOUT setups. Set False to allow range trades.
    ranging_blocks_entry: bool = True
    # Entry style. "pullback" (default, conservative) = only enter on a
    # pullback/retest, never the breakout candle — safest, but misses clean
    # one-directional trends that barely pull back. "momentum" = also enter a
    # CONFIRMED trend continuation (5-min trend + 1-min agree, not over-extended)
    # so strong runs are caught. "pattern" = the confirmation-candle method:
    # enter on a closed candlestick pattern (engulfing / star / hammer / etc.)
    # agreeing with the 5-min trend — fires most often. Toggleable via /api/settings.
    entry_mode: str = "pullback"
    # Momentum entries require at least this directional strength (0..1) so only
    # genuinely strong trends trigger a continuation BUY (not weak drift).
    momentum_min_strength: float = 0.34
    # While HOLDing and the premium is at least this % below entry, offer an
    # (optional, risk-flagged) "average down" suggestion — never automated.
    average_down_trigger_pct: float = 12.0

    # --- Profit-factor upgrades (each kept only after a WALK-FORWARD test) ---
    # SCALE-OUT: book part of the position at T1 and let the rest ride to T2/T3
    # with the stop at break-even, so the runner can never turn the trade red.
    # Measured out-of-sample (hours/rules chosen on the 1st half of the data,
    # scored on the 2nd): NIFTY PF 1.098 -> 1.162, CRUDEOIL PF 1.143 -> 1.172.
    # Needs at least 2 lots — a single lot cannot be split.
    auto_trade_scale_out: bool = True
    auto_trade_scale_out_frac: float = 0.5
    # Hours (IST, 24h) in which NEW entries are blocked, per instrument. Derived
    # from measured per-hour profit factor, then validated walk-forward: NIFTY
    # 10/11/13 held up out-of-sample (PF 1.098 -> 1.324 on unseen data). The same
    # test FAILED for CRUDEOIL (1.143 -> 1.112), so crude is deliberately left
    # unfiltered — its weak hours were noise, not a pattern.
    blocked_entry_hours: dict[str, list[int]] = {"NIFTY": [10, 11, 13]}
    # Instruments the bot may trade at all. Empty = no restriction.
    instrument_allowlist: list[str] = []

    # --- Premium-SELLING engine (defined-risk credit spreads) — PAPER ONLY ---
    # Measured on 1,243 NIFTY sessions (Black-Scholes on realised vol):
    #   frictionless intraday   81.4% win, PF 2.41
    #   + bid-ask on all legs   66.1% win, PF 1.19
    #   held OVERNIGHT          55.0% win, PF 0.65  -> LOSES
    # Hence: intraday only, defined risk only, and default OFF until it has been
    # paper-traded against real option-chain fills. It has NO live order path.
    credit_spread_enabled: bool = False
    credit_spread_short_delta: float = 0.20   # ~80% chance of expiring worthless
    credit_spread_width_steps: int = 2        # hedge distance, in strike steps
    credit_spread_stop_multiple: float = 2.0  # close when loss = 2x the credit
    credit_spread_min_rr: float = 0.12        # credit must be worth the tail risk
    credit_spread_vol_lookback: int = 240     # 1-min bars for the realised-vol proxy
    credit_spread_slippage_per_leg: float = 1.0
    credit_spread_min_minutes_open: int = 60  # too little time left = no new spread
    credit_spread_close_before_minutes: int = 20  # must be flat before the bell

    # --- FUTURES paper tool — a SEPARATE tool, PAPER ONLY, no live route ------
    # Denominated in INDEX POINTS, not premium: one point is lot_size rupees,
    # there is no theta and no IV, and a bearish view is a SHORT rather than a
    # long put. It keeps its OWN capital pot so it never competes with the option
    # bot for money, and it sizes from RISK (stop distance) because a futures loss
    # is unbounded. Default OFF; nothing here is validated against real fills.
    futures_paper_enabled: bool = False
    futures_capital: float = 500000.0          # this tool's own pot, separate
    futures_risk_per_trade_pct: float = 1.0    # % of the pot lost if the stop hits
    futures_margin_pct: float = 12.0           # approx SPAN+exposure, for affordability
    # Hard ceiling on lots per position, 0 = no ceiling. This was 1, which threw
    # away the sizing logic below it: at a 20-point Crude stop the 1% budget of
    # Rs5,000 pays for 2 lots, so every winner was booked at half the size the
    # risk rule allowed. Risk still binds first; this only caps the result.
    futures_paper_lots: int = 5
    # Size to every lot the FREE MARGIN can carry instead of to the risk budget.
    # Deliberately off: on Crude the margin allows ~5 lots against the 2 the 1%
    # rule allows, i.e. ~5% of the pot at risk per trade instead of 1%.
    futures_size_by_margin: bool = False
    futures_max_concurrent: int = 2
    # Entry filters (its own rules — no premium/delta/theta anywhere)
    # Dead-market filter as a % OF PRICE. An absolute point threshold is a unit
    # bug: 5 points is 0.06% of a Crude contract but several times a 1-minute ATR
    # on a Rs150 stock future, so the tool refused every non-commodity as "too
    # quiet" and only ever traded Crude. 0.03% clears Crude (~8 pts vs 2.4),
    # Nifty (~12 vs 7.4) and a Rs800 stock (~0.5 vs 0.24) while still excluding a
    # genuinely dead book.
    futures_min_atr_pct: float = 0.03
    futures_min_atr_points: float = 0.0        # legacy absolute override; 0 = off
    futures_min_adx: float = 18.0              # needs a trend to follow
    futures_body_factor: float = 1.2           # breakout body vs the 20-bar average
    # Risk in points: stop from ATR, targets as R-multiples of that risk.
    # 2.5x rather than 1.5x: a 1.5x stop on CRUDEOIL came out at ~20 points on a
    # ~7,900 contract (0.25%), which is inside the noise — measured 8 stop-outs in
    # one afternoon on 15-27 point moves. A wider stop is not more risk: sizing
    # divides the rupee budget by the stop, so the bot simply takes fewer lots.
    futures_stop_atr: float = 2.5
    futures_t1_r: float = 1.0
    futures_t2_r: float = 2.0
    futures_t3_r: float = 3.0
    # Floor locked once T1 is reached, as a fraction of R. NOT break-even: a trade
    # that has already travelled a full 1R and comes back to the entry price is a
    # LOSS of the round-trip cost, not a scratch (measured: four such exits at
    # -Rs375 each, all at 0 or +1 point). Costs are added on top of this floor.
    futures_t1_lock_r: float = 0.5
    # Once T1 is reached the floor TRAILS the peak by this many R until T2 is
    # touched. Without it the floor sat at the T1 lock and every winner was
    # booked at ~0.5R however far it ran (measured: three of three on 13 Aug).
    futures_post_t1_trail_r: float = 0.5
    # Pre-T1 profit protection. Until this existed the futures floor was only
    # created AFTER T1 was touched, so a trade that ran part of the way and
    # reversed had nothing holding it and walked to the full stop. Measured on
    # the recorded paper book: 12 of the losing futures trades were green first
    # and Rs16,481 of shown profit was handed back, including +Rs2,960 -> -Rs3,600
    # and +Rs1,610 -> -Rs1,990. The ladder below mirrors what the option path has
    # had all along. It reduces the SIZE of losses; it does not create an edge
    # (Phase 26 measured trail variants on 156,949 option books: -0.3061R ->
    # -0.2921R, 0 of 50 instruments positive).
    futures_pre_t1_trail: bool = True
    # Arm once the favourable excursion reaches this fraction of R. Below the T1
    # lock's own trigger, so it can only ever fire before T1.
    futures_pre_t1_arm_r: float = 0.5
    # Once armed, exit on giving back this % of the run from entry to the peak.
    # OFF by default, and deliberately: on the recorded book the ratchet alone can
    # avoid at most Rs13,942 of loss, while Rs22,356 of recorded WINNERS reached
    # the arm threshold and would have been exposed to this exit. Whether it
    # clips more than it saves cannot be answered without tick paths, which are
    # only now being captured — so it stays off until the counterfactual can be
    # graded rather than guessed.
    futures_pre_t1_giveback_exit: bool = False
    futures_pre_t1_giveback_pct: float = 40.0
    # Once armed, ratchet the hard stop up to entry + round-trip cost, so a trade
    # that showed real profit can no longer become a full-stop loss. Entry itself
    # is NOT break-even on futures: closing there loses the whole round trip.
    futures_pre_t1_breakeven: bool = True
    # Refuse a plan whose T1 is not at least this multiple of the round-trip cost.
    # Measured: a NATURALGAS plan carried T1 0.2 pts away against 2.2 pts of risk
    # and ~2.1 pts of cost — the target was inside the cost, so even hitting it
    # booked -Rs2,580. 0 = gate off.
    futures_min_t1_cost_multiple: float = 2.0
    # Maximum round-trip cost as a % of the stop distance. Above this the trade
    # cannot pay for itself: every trade taken between 12% and 25% lost money.
    futures_max_cost_to_risk_pct: float = 12.0
    # Past T3 the floor ratchets in fractions of R so a trend is not capped at T3.
    # It cannot exit at the exact top — one step is always given back.
    futures_beyond_t3_step_atr: float = 0.5    # step = 0.5R
    futures_beyond_t3_min_step: float = 1.0    # never finer than 1 point
    # Costs charged on every paper trade — ignoring these is the easiest way to
    # make a futures tool look profitable when it is not.
    futures_slippage_points: float = 1.0       # per side
    futures_brokerage_per_order: float = 20.0  # flat discount-broker rate, per order
    # Statutory charges dominate a futures round trip and are a % of TURNOVER, not
    # a flat fee: on a NIFTY lot (~Rs16L notional) STT alone is ~Rs326 against Rs40
    # of brokerage. Modelling only brokerage flatters the paper P&L by several
    # points a trade, so both sides are charged explicitly.
    futures_stt_sell_pct: float = 0.02         # STT, sell side only, index futures
    futures_ctt_sell_pct: float = 0.01         # CTT, sell side only, MCX commodities
    futures_txn_pct: float = 0.0035            # exchange + SEBI + stamp + GST, both sides
    futures_no_entry_minutes: float = 45.0     # no new entry inside the last N min

    # --- Frozen pair capture (research only, no order path) --------------------
    # Records BANKNIFTY and NIFTY futures books at the SAME bar timestamp and
    # paper-trades the one frozen relative-value configuration. The first result
    # was REQUIRES_MORE_DATA because the futures spread was never measured and
    # the sample was one six-week window; both are fixed only by capturing.
    pair_capture_enabled: bool = True
    # Also quote the NEXT futures expiry on the same refresh, so the near/next
    # basis (Test B) becomes measurable. One extra token on an existing quote
    # call; it feeds no signal and no order.
    pair_next_expiry_capture: bool = True

    # --- Futures risk governor -------------------------------------------------
    # The option engine has a daily-loss cap, a trade cap and a consecutive-loss
    # halt; the futures tool had none of them and chopped through six stop-outs in
    # a row (SHORT/SHORT/LONG/LONG/SHORT/LONG, 1-17 min apart) before the session
    # ended. On an unbounded-loss instrument these caps matter more, not less.
    futures_reentry_cooldown_sec: float = 600.0    # wait after ANY exit, per instrument
    futures_flip_block_sec: float = 900.0          # after a STOP, no opposite side yet
    futures_max_consecutive_losses: int = 3        # per instrument, then stand down
    futures_stand_down_sec: float = 3600.0         # how long that stand-down lasts
    futures_max_daily_loss: float = 15000.0        # halt the tool for the day at -this
    futures_max_trades_per_day: int = 6            # per instrument, per session

    # --- Profit-protecting entry gates (added to stop chasing peaks) ---
    # IGNITION entry: join the move on the FIRST expansion candle ("the strong
    # green candle") instead of waiting for accumulated momentum to cross a
    # threshold, which only happens several candles later near the top of the
    # run. Unlike the reversal entry this does not guess a bottom — the move has
    # already started — but it is still default-off until the replay proves it.
    ignition_entry_enabled: bool = False
    ignition_body_factor: float = 1.2
    ignition_volume_factor: float = 1.5

    # SUPERTREND FLIP entry — the TradingView-style ATR trailing-band flip. Enters
    # on the candle that closes through the band, i.e. the start of the expansion
    # rather than after momentum has accumulated near the top. OFF until its
    # measured profit factor justifies it.
    supertrend_entry_enabled: bool = False
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    # How many candles a flip stays actionable. 1 = only the just-closed candle,
    # which is the earliest entry and prevents late chasing.
    supertrend_confirm_bars: int = 1
    # Require the higher-timeframe trend to already agree with the flip. Default
    # OFF because at the flip bar the higher timeframe still reflects the OLD
    # direction — demanding agreement is precisely what delayed the entry until
    # the move had run. On makes it a late continuation entry instead.
    supertrend_require_htf: bool = False

    # Minimum stop distance as a % of the OPTION PREMIUM. ``ATR × delta`` can
    # produce a stop that sits INSIDE the option's own noise (measured: a ₹339
    # crude CE given a 4-point / 1.2% stop, knocked out by ordinary wobble after
    # moving in favour first). The stop must sit outside that noise band or the
    # trade loses regardless of direction. Targets are widened by the same
    # factor so the reward:risk of the setup is preserved, and the R:R gate then
    # rejects anything whose target can't justify a survivable stop.
    min_stop_pct_of_premium: float = 8.0
    # Minimum Reward:Risk a FRESH BUY must offer (target distance ÷ stop
    # distance). A setup below this is downgraded to WAIT — no more trades that
    # risk more than they can make (e.g. the 0.64 R:R crude CE). 0 disables.
    min_reward_risk: float = 1.2
    # Scales T1/T2/T3 together WITHOUT moving the stop. Measured over one
    # session's 33 tradeable signals: T1 sat a median 11% above the entry premium
    # while the median signal only ever travelled 2% and only 12% of signals
    # reached 11% at all — so T1 was a level the market rarely visited and the
    # "target hit" statistics were structurally near zero. 1.0 keeps the current
    # levels. Lowering it brings the targets in; because reward:risk falls with
    # it, ``min_reward_risk`` has to come down by the same proportion or the
    # engine will simply downgrade every setup to WAIT.
    decision_target_scale: float = 1.0
    # Don't CHASE a premium that is already exploding: when the option we'd buy
    # is in an EXPLOSION premium-state (a vertical spike), veto the fresh entry
    # so we don't buy the top of the run. Existing positions are unaffected.
    veto_premium_explosion: bool = True
    # Reversal / early-turn entry: allow a FRESH entry at a CONFIRMED turn near a
    # swing low (CE) / swing high (PE) — a stop-run reclaim (liquidity sweep), a
    # V-reversal, or a reversal candle at the extreme with RSI stretched — so the
    # entry lands near the bottom/top instead of deep into the move. Requires
    # confirmation (never a blind knife-catch) and still obeys the R:R gate.
    # DEFAULT OFF: a 5-yr replay showed reversal/bottom entries are a net loser
    # (NIFTY PF ~0.90) that dilute the profitable trend book — kept as an opt-in
    # PAPER toggle, not funded by default.
    reversal_entry_enabled: bool = False
    # RSI must be at/below this for a bullish reversal (at/above 100-this for a
    # bearish one) — i.e. the move was stretched before it turned.
    reversal_rsi_oversold: float = 42.0
    # Price must be within this many ATR of the swing extreme for the reversal
    # candle path (keeps the entry AT the low/high, not mid-range).
    reversal_max_atr_from_swing: float = 1.2

    # --- Trading session (IST). Used to flag MARKET OPEN/CLOSED. ---
    # MCX Crude Oil trades ~09:00–23:30 IST, Monday–Friday.
    market_open_ist: str = "09:00"
    market_close_ist: str = "23:30"
    # NSE/BSE derivatives open later than MCX, so the opening-gap window has to
    # be measured from the right bell per instrument (09:00 for a commodity,
    # 09:15 for an index or stock option).
    equity_open_ist: str = "09:15"
    # …and they close ~8 hours before MCX does. Treating one global 09:00-23:30
    # window as "the market" makes every NSE/BSE name look live until 23:30, so
    # the scan keeps spending its budget and Angel's rate limit on ~40 frozen
    # instruments all evening — the exact cost that slows the names still trading.
    equity_close_ist: str = "15:30"
    market_days: str = "0-4"  # 0=Mon … 6=Sun
    # Simulated demo runs 24/7; set false with a live feed to respect hours.
    ignore_market_hours: bool = True

    def minutes_to_close(self, now_utc: float) -> int:
        """Minutes left in the IST session (0 once it has closed).

        The credit-spread engine is intraday-only, so it needs to know how much
        time is left before it must be flat.
        """
        import datetime as _dt

        ist = _dt.datetime.utcfromtimestamp(now_utc) + _dt.timedelta(hours=5, minutes=30)
        ch, cm = (int(x) for x in self.market_close_ist.split(":"))
        return max(0, (ch * 60 + cm) - (ist.hour * 60 + ist.minute))

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def _parse_days(self) -> set[int]:
        days: set[int] = set()
        for part in self.market_days.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-")
                days.update(range(int(a), int(b) + 1))
            elif part:
                days.add(int(part))
        return days or {0, 1, 2, 3, 4}

    # --- Phase 6 AI engine (LIVE PAPER ONLY) -----------------------------------
    # A second, independent engine that runs beside the existing one and trades
    # only the paper book. The existing engine is untouched and stays the
    # baseline; nothing here can reach a real order (see app/ai/safety.py).
    ai_enabled: bool = False            # compute AI decisions (read-only, no trades)
    ai_paper_enabled: bool = False      # let the AI open/manage PAPER positions
    ai_interval_sec: float = 15.0       # how often the AI re-evaluates the universe
    ai_top_candidates: int = 5          # scanner candidates given the deep AI pass
    # Decision thresholds. Deliberately strict: Phase 5 measured OOS AUC ~0.53,
    # so the model is a weak signal and only its extreme is worth acting on. These
    # are the AI engine's own thresholds and have nothing to do with the
    # production gates, which are unchanged.
    ai_min_probability: float = 0.55    # P(target before stop) required for BUY_NOW
    ai_min_entry_quality: float = 60.0  # 0-100, from the entry-quality engine
    ai_min_regime_confidence: float = 45.0
    ai_min_direction_edge: float = 0.10  # |P(CE) - P(PE)| required to pick a side
    ai_max_open_positions: int = 3
    ai_max_trades_per_day: int = 12
    ai_max_daily_loss: float = 5000.0   # paper rupees; halts the AI book for the day
    ai_risk_per_trade_pct: float = 1.0  # % of the AI paper capital risked per trade
    ai_paper_capital: float = 100000.0  # notional AI paper account
    # Trade levels for the AI book — the SAME geometry every Phase 5 number was
    # measured on, so live paper results are comparable with the research.
    ai_stop_atr: float = 0.8
    ai_t1_r: float = 1.2
    ai_t2_r: float = 2.0
    ai_t3_r: float = 3.0
    ai_max_hold_min: float = 30.0       # TIMEOUT exit, matching the research horizon
    ai_max_data_age_ms: float = 4000.0  # stale feed => no paper entry, ever
    # Paper fill realism. A paper book that fills at the mid is the easiest way to
    # invent an edge, so entries pay the spread and both sides pay slippage.
    ai_slippage_pct: float = 0.15       # % of premium, per side
    ai_cross_spread: bool = True        # buy at ask / sell at bid when a quote exists
    # Model artefact produced by phase6_train.py. Empty = data_dir/ai_model.json.
    # When the file is missing the probability engine reports UNAVAILABLE and the
    # AI cannot emit BUY_NOW — it never falls back to a guessed probability.
    ai_model_path: str = ""

    # --- Phase 45 shadow board + paper journal (research only) ---------------
    # Evaluates the observation Phase 17 has ALREADY captured and persisted, and
    # writes what a research definition would have said into its own database.
    # It cannot change a production signal, arms no gate and has no import path
    # to an order or a broker. On by default because a board that only runs when
    # someone remembers to switch it on accrues no sample; set false to stop the
    # extra write entirely.
    phase45_shadow: bool = True
    # Paper outcomes are resolved this long after the decision instant, so an
    # event is not graded on the two forward quotes that exist a minute later.
    phase45_resolve_after_minutes: int = 15
    # Phase 46 — the research overlay beside the production signal board. It
    # classifies the Phase 45 row that was already journalled for an instant
    # and records one word for where the research evidence stood; it changes no
    # signal, filters nothing and has no import path to an order or a broker.
    # Separate from the Phase 45 switch so switching the view off never
    # switches the evidence off.
    phase46_overlay: bool = True

    def session_window_ist(self, exchange: str | None = None) -> tuple[str, str]:
        """The open/close bells for an exchange, as "HH:MM" strings.

        MCX runs to 23:30; NSE/BSE derivatives stop at 15:30. Passing no exchange
        keeps the widest (MCX) window, which is the historical behaviour.
        """
        exch = (exchange or "").upper()
        if exch in ("NFO", "NSE", "BFO", "BSE", "CDS"):
            return self.equity_open_ist, self.equity_close_ist
        return self.market_open_ist, self.market_close_ist

    def market_session(
        self, now_utc: float, exchange: str | None = None
    ) -> tuple[bool, str]:
        """Return (is_open, human note) evaluated in IST (UTC+5:30).

        The window is per EXCHANGE: an NSE option is shut at 16:00 IST even while
        Crude is still trading, and calling it open costs real scan budget on a
        frozen price.
        """
        import datetime as _dt

        ist = _dt.datetime.utcfromtimestamp(now_utc) + _dt.timedelta(hours=5, minutes=30)
        open_ist, close_ist = self.session_window_ist(exchange)
        oh, om = (int(x) for x in open_ist.split(":"))
        ch, cm = (int(x) for x in close_ist.split(":"))
        open_min, close_min = oh * 60 + om, ch * 60 + cm
        cur = ist.hour * 60 + ist.minute
        is_day = ist.weekday() in self._parse_days()
        is_open = is_day and open_min <= cur <= close_min
        if is_open:
            return True, f"Market OPEN · {ist:%a %H:%M} IST"
        if not is_day:
            return False, f"Market CLOSED · weekend ({ist:%a} IST)"
        return False, f"Market CLOSED · session {open_ist}–{close_ist} IST"


settings = Settings()
