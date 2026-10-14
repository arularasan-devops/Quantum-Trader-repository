import {
  BacktestResult,
  EconomicEvent,
  EventGuard,
  Fundamentals,
  FuturesSignalCard,
  PerformanceReport,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000";
export const WS_BASE =
  process.env.NEXT_PUBLIC_WS_BASE || "ws://localhost:8000";

export interface OrderResult {
  ok: boolean;
  mode: string;
  side: string;
  option_symbol: string;
  lots: number;
  fill_premium: number | null;
  order_id: string | null;
  message: string;
}

export async function buyOption(
  option_symbol: string,
  lots = 1,
  confirm = false,
  instrument?: string
) {
  const res = await fetch(`${API_BASE}/api/buy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ option_symbol, lots, confirm, instrument }),
  });
  return res.json() as Promise<{ result: OrderResult; position: unknown }>;
}

export async function fetchSignal(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/signal${qs}`);
  return res.json() as Promise<import("./types").QuantumSignalView>;
}

// One row per watched instrument, ranked by how far the PREMIUM can travel net
// of costs — the unit that decides whether a move is worth taking.
export interface OpportunityRow {
  instrument: string;
  instrument_name: string;
  score: number;
  tradeable: boolean;
  reason: string;
  blockers: string[];
  atr_points: number | null;
  atr_pct: number | null;
  expected_move_pct: number | null;
  net_move_pct: number | null;
  cost_pct: number | null;
  volume_surge: number | null;
  adx: number | null;
  premium: number | null;
  min_move_pct: number;
  min_score: number;
  action: string;
  actionable: boolean;
  side: string | null;
  withheld_because: string | null;
  confidence: number | null;
  spot: number | null;
  change_pct: number | null;
  feed: import("./types").FeedHealth | null;
}

export interface OpportunityScan {
  rows: OpportunityRow[];
  as_of: number;
  gate_enabled: boolean;
  min_score: number;
  min_move_pct: number;
}

export async function getOpportunities() {
  const res = await fetch(`${API_BASE}/api/opportunities`);
  return res.json() as Promise<OpportunityScan>;
}

export async function fetchSignalScan() {
  const res = await fetch(`${API_BASE}/api/signal/scan`);
  return res.json() as Promise<import("./types").SignalScan>;
}

export async function sellPosition(confirm = false, instrument?: string) {
  const res = await fetch(`${API_BASE}/api/sell`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm, instrument }),
  });
  return res.json() as Promise<{ result: OrderResult; position: unknown }>;
}

export interface AutoTradeConfig {
  enabled: boolean;
  min_confidence: number;
  max_lots: number;
  safe_sizing: boolean;
  ride_trail: boolean;
  trail_pct: number;
  capital: number;
  risk_per_trade_pct: number;
  daily_profit_target: number;
  max_daily_loss: number;
  allow_live: boolean;
  live_max_order_value: number;
  live_max_lots: number;
  zero_to_hero_enabled: boolean;
  zero_to_hero_budget: number;
  max_concurrent: number;
  momentum_entry: boolean;
  no_chase: boolean;
  pre_t1_trail: boolean;
  use_live_balance?: boolean;
  instrument_capital: Record<string, number>;
  instrument_lots: Record<string, number>;
  stop_points?: Record<string, number>;
  target_points?: Record<string, number>;
  // The same early target as a % of premium — the unit the points field should
  // always have been, since 2 points means 0.7% on a ₹262 option and 9% on ₹25.
  target_pct?: Record<string, number>;
  // Floor on that early exit as a multiple of the stop distance, so it can't be
  // configured into a losing reward:risk.
  min_target_r?: number;
  min_premium?: number;
  reentry_cooldown_sec?: number;
  // A stop-out is cooled for longer than a target exit: the view that produced
  // it has just been proven wrong, and nine Crude re-entries in one evening
  // produced five stops at exactly -8% each.
  post_stop_cooldown_sec?: number;
  // Minutes after the bell in which no entry is taken. Opening entries exited at
  // -47.8% / -22.4% / -16.7% against an 8% stop, because a premium gaps through
  // a stop rather than trading down to it.
  no_entry_open_minutes?: number;
  // Upper bound on the confidence that may be traded. Measured over three
  // sessions the top band was the worst: >=95% went 2 wins / 8 for -Rs10,712.
  max_confidence?: number;
  risk_stand_down_sec?: number;
  // Cost model, so P&L is driven by your contract note rather than an estimate.
  brokerage_per_lot?: number;
  option_stt_sell_pct?: number;
  option_txn_pct?: number;
  stale_feed_ticks?: number;
  max_stop_pct?: number;
  // Size every instrument to the same rupee risk instead of a flat lot count,
  // so a cheap SBIN option and an expensive CRUDEOIL one lose the same amount.
  risk_balanced_lots?: boolean;
  balanced_max_lots?: number;
  trade_mode: string;
  mode: string;
}

// Set per-instrument fixed point stops / profit targets for Auto-Buy. Send
// {SYMBOL: points}; a value of 0 clears that instrument (SL falls back to the
// % / engine stop, target falls back to the T1/T2/T3 ride model).
export async function setPointExits(points: {
  stop_points?: Record<string, number>;
  target_points?: Record<string, number>;
  target_pct?: Record<string, number>;
  min_target_r?: number;
  min_premium?: number;
  reentry_cooldown_sec?: number;
  post_stop_cooldown_sec?: number;
  no_entry_open_minutes?: number;
  max_confidence?: number;
  risk_stand_down_sec?: number;
  brokerage_per_lot?: number;
  option_stt_sell_pct?: number;
  option_txn_pct?: number;
  risk_balanced_lots?: boolean;
  balanced_max_lots?: number;
}) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ auto_trade: points }),
  });
  return res.json();
}

export interface AutoBuyStatus {
  on: boolean;
  live_active: boolean;
  trade_mode: string;
  available_cash: number | null;
  use_live_balance: boolean;
  momentum_entry: boolean;
  live_max_order_value: number;
  min_confidence: number;
  note: string;
}

export async function setAutoBuy(on: boolean) {
  const res = await fetch(`${API_BASE}/api/auto-buy`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ on }),
  });
  return res.json() as Promise<AutoBuyStatus>;
}

export async function setAutoTrade(opts: {
  enabled?: boolean;
  min_confidence?: number;
  max_lots?: number;
  safe_sizing?: boolean;
  ride_trail?: boolean;
  trail_pct?: number;
  capital?: number;
  risk_per_trade_pct?: number;
  daily_profit_target?: number;
  max_daily_loss?: number;
  allow_live?: boolean;
  live_max_order_value?: number;
  live_max_lots?: number;
  zero_to_hero_enabled?: boolean;
  zero_to_hero_budget?: number;
  max_concurrent?: number;
  momentum_entry?: boolean;
  no_chase?: boolean;
  pre_t1_trail?: boolean;
  instrument_capital?: Record<string, number>;
  instrument_lots?: Record<string, number>;
}) {
  const res = await fetch(`${API_BASE}/api/auto-trade`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts),
  });
  return res.json() as Promise<AutoTradeConfig>;
}

export async function getEngineSettings(): Promise<{
  entry_mode: string;
  buy_min_confidence: number;
  auto_trade: AutoTradeConfig;
  flow?: FlowSettings;
}> {
  const res = await fetch(`${API_BASE}/api/settings`);
  return res.json();
}

export type EntryMode = "pullback" | "momentum" | "pattern" | "auto";

export async function setEntryMode(
  entry_mode: EntryMode,
): Promise<{ entry_mode: string }> {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ entry_mode }),
  });
  return res.json();
}

export async function selectInstrument(instrument: string) {
  const res = await fetch(`${API_BASE}/api/instrument`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument }),
  });
  return res.json() as Promise<{ ok: boolean; active: string; message?: string }>;
}

export async function runBacktest(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/backtest${qs}`);
  return res.json() as Promise<BacktestResult>;
}

export interface FactorWeight {
  name: string;
  multiplier: number;
  trades: number;
  win_rate: number;
  pnl: number;
}

export async function getLearning(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/learning${qs}`);
  return res.json() as Promise<{ factors: FactorWeight[] }>;
}

export async function getPerformance(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/performance${qs}`);
  return res.json() as Promise<PerformanceReport>;
}

export interface SignalDayStats {
  date: string;
  signals: number;
  target: number;
  stop: number;
  open: number;
  expired: number;
  win_rate: number | null;
}

export interface SignalRecent {
  time: string | null;
  date: string | null;
  option_type: string | null;
  strike: number | null;
  entry: number | null;
  status: string;
  targets_hit: number;
  confidence: number | null;
  first_hit: string | null;
  t1_mins: number | null;
  t2_mins: number | null;
  t3_mins: number | null;
}

export interface SignalTimingLevel {
  level: string;
  reached: number;
  reach_pct: number | null;
  avg_mins: number | null;
}

export interface SignalTiming {
  finalized: number;
  levels: SignalTimingLevel[];
  stop_first: number;
  stop_first_pct: number | null;
  avg_mins_to_stop: number | null;
}

export interface SignalBucket {
  band: string;
  signals: number;
  target: number;
  stop: number;
  open: number;
  win_rate: number | null;
  stop_rate: number | null;
}

export interface SignalStats {
  instrument: string;
  today: SignalDayStats;
  overall: { signals: number; target: number; stop: number; open: number; win_rate: number | null };
  days: SignalDayStats[];
  buckets: SignalBucket[];
  timing: SignalTiming;
  recent: SignalRecent[];
}

export async function getSignalStats(instrument?: string, days = 7) {
  const qs = `?days=${days}${instrument ? `&instrument=${encodeURIComponent(instrument)}` : ""}`;
  const res = await fetch(`${API_BASE}/api/signal-stats${qs}`);
  return res.json() as Promise<SignalStats>;
}

// ---- Live multi-instrument screener ----
export interface ScreenerRow {
  symbol: string;
  display: string;
  signal: string;
  confidence: number;
  option_type: string | null;
  strike: number | null;
  current_premium: number | null;
  entry_low: number | null;
  entry_high: number | null;
  stop_loss: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  htf_trend: string | null;
  entry_trigger: string | null;
  risk_level: string;
  trade_score: number;
  opportunity_score: number;
  opportunity_label: string;
  trade_quality: string;
  expected_move_points: number | null;
  /** Conviction on 0..100, not a probability: it cannot print below 50 and
   *  averaged 78 where the target was actually reached 42.8% of the time. */
  conviction_meter: number | null;
  /** The measured target-before-stop frequency for this trade's reward:risk,
   *  underlying-only — this is the number that may be read as a rate. */
  observed_target_rate: {
    target_hit_rate_pct: number;
    expectancy_r: number;
    sample: number;
    band: string;
    window: string;
  } | null;
  moneyness: string | null;
  expected_holding_minutes: number | null;
  spot_price: number | null;
  futures_price: number | null;
  futures_change_pct: number | null;
  premium_high: number | null;
  premium_low: number | null;
  lot_size: number;
  data_source: string;
  is_buy: boolean;
  no_live_data: boolean;
  chasing: boolean;
  // Early Momentum advisory (parallel engine)
  em_enabled: boolean;
  em_stage: number;
  em_active: boolean;
  em_side: string | null;
  em_score: number;
  em_option: string | null;
  em_entry: number | null;
  em_entry_low: number | null;
  em_entry_high: number | null;
  em_stop: number | null;
  em_target1: number | null;
  em_target2: number | null;
  em_target3: number | null;
}

export async function getScreener() {
  const res = await fetch(`${API_BASE}/api/screener`);
  return res.json() as Promise<{ rows: ScreenerRow[]; as_of: number }>;
}

// ---- AI Command Center (V4) — ranked opportunities (real data only) ----
export interface CommandCenter {
  enabled: boolean;
  opportunities: ScreenerRow[];
  as_of?: number;
}

export async function getCommandCenter() {
  const res = await fetch(`${API_BASE}/api/command-center`);
  return res.json() as Promise<CommandCenter>;
}

// ---- Early-Momentum outcomes (per-instrument, analysis only) ----
export interface MomentumInstrumentStat {
  instrument: string;
  signals: number;
  t1_rate: number;
  t2_rate: number;
  t3_rate: number;
  stop_rate: number;
  avg_max_pct: number;
  best_pct: number | null;
}

export interface MomentumOutcome {
  instrument: string;
  side: string | null;
  option_symbol: string;
  ts_open: number;
  ts_close: number;
  entry_premium: number;
  max_premium: number;
  final_premium: number | null;
  max_gain_points: number;
  max_gain_pct: number | null;
  final_points: number | null;
  t1_hit: boolean;
  t2_hit: boolean;
  t3_hit: boolean;
  stop_hit: boolean;
  close_reason: string;
  duration_sec: number;
  score: number;
  regime: string;
}

export async function getMomentumOutcomes() {
  const res = await fetch(`${API_BASE}/api/momentum-outcomes`);
  return res.json() as Promise<{
    total: number;
    instruments: MomentumInstrumentStat[];
    recent: MomentumOutcome[];
    as_of: number;
  }>;
}

// ---- Flow Engine (Candle-Flow) paper performance ----
export interface FlowInstrumentStat {
  instrument: string;
  signals: number;
  win_rate: number;
  switch_rate: number;
  avg_final_pts: number;
  /** Net of brokerage, statutory charges and spread. Null when uncostable. */
  net_rupees: number | null;
  gross_rupees: number;
  cost_rupees: number | null;
  avg_net_pts: number | null;
  avg_cost_pts: number | null;
  net_win_rate: number | null;
  costed_legs: number;
  avg_hold_min: number;
  best_pts: number | null;
}

export interface FlowRecent {
  instrument: string;
  side: string | null;
  option_symbol: string | null;
  ts_open: number;
  ts_close: number;
  entry_premium: number | null;
  final_premium: number | null;
  peak_premium: number | null;
  final_points: number | null;
  max_gain_points: number | null;
  final_rupees: number | null;
  max_gain_rupees: number | null;
  /** Round-trip cost this leg paid, and what is left after it. */
  cost_points?: number | null;
  cost_rupees?: number | null;
  cost_spread_points?: number | null;
  cost_spread_source?: string | null;
  net_points?: number | null;
  net_rupees?: number | null;
  net_win?: boolean | null;
  lots: number | null;
  lot_size: number | null;
  win: boolean;
  close_reason: string;
  duration_sec: number;
  agreement: string;
  /** IST trading date and clock times of the leg. Derived for older rows. */
  session?: string | null;
  open_time_ist?: string | null;
  close_time_ist?: string | null;
}

export interface FlowSummary {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number | null;
  avg_hold_min: number | null;
  false_signal_rate: number;
  /** Headline P&L, net of costs. Null when no leg could be costed. */
  net_rupees: number | null;
  gross_rupees: number;
  costs: FlowCostBlock;
  instruments: FlowInstrumentStat[];
  recent: FlowRecent[];
  /** The single session these numbers cover, or null for every recorded one. */
  session?: string | null;
  sessions?: string[];
  as_of: number;
}

/** What the round trips cost, beside what the legs earned before paying them. */
export interface FlowCostBlock {
  costed_legs: number;
  coverage_pct: number;
  measured_spread_legs?: number;
  assumed_spread_legs?: number;
  cost_points?: number;
  cost_rupees?: number;
  gross_points?: number;
  net_points?: number;
  cost_per_leg_points?: number;
  gross_per_leg_points?: number;
  /** Cost per leg over gross edge per leg. Above 1 the trade count is the loss. */
  cost_to_gross_edge?: number | null;
  net_win_rate?: number;
  verdict:
    | "NOT_COSTED"
    | "GROSS_NEGATIVE"
    | "COST_EXCEEDS_EDGE"
    | "COST_HALVES_EDGE"
    | "COST_COVERED";
  note: string;
}

/** `session` takes an IST date or `TODAY`, which the server resolves. */
export async function getFlowReport(limit = 200, session?: string) {
  const q = new URLSearchParams({ limit: String(limit) });
  if (session) q.set("session", session);
  const res = await fetch(`${API_BASE}/api/flow-report?${q.toString()}`);
  return res.json() as Promise<{
    enabled: boolean;
    paper_lots: number;
    lot_sizes: Record<string, number>;
    flow: FlowSummary;
  }>;
}

export interface JournalTrade {
  instrument: string;
  ts: number;
  option: string | null;
  option_type: string | null;
  entry: number | null;
  exit: number | null;
  lots: number | null;
  net_pnl: number | null;
  win: boolean;
  // How much money the trade tied up: the premium paid on an option, the margin
  // blocked on a future. Null on rows closed before this was recorded.
  capital_used?: number | null;
  notional?: number | null;
  lot_size?: number | null;
  risk_rupees?: number | null;
  cost_rupees?: number | null;
  return_on_capital_pct?: number | null;
  holding_minutes: number | null;
  mode: string | null;
  auto: boolean;
  exit_reason: string | null;
  confidence: number | null;
  note: string | null;
  /** PULLBACK / REVERSAL / IGNITION / MOMENTUM / ... — which trigger opened it. */
  entry_trigger: string | null;
  /** SIGNAL or FLOW — which book the row belongs to. */
  book?: string | null;
  /** Charges itemised. Brokerage is per ORDER; rows written before that
   *  correction carry cost_model COST_MODEL_OLD and are left as they were. */
  brokerage?: number | null;
  statutory?: number | null;
  brokerage_per_order?: number | null;
  cost_model?: string | null;
  /** MEASURED / PARTIAL_* / UNKNOWN — a spread is never modelled onto a row. */
  cost_status?: string | null;
  entry_bid?: number | null;
  entry_ask?: number | null;
  exit_bid?: number | null;
  exit_ask?: number | null;
  spread_cost_rupees?: number | null;
}

/** Per-trigger performance, so the unproven entries can be judged on real fills. */
export interface TriggerStats {
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number | null;
}

/** What was held out of the journal's figures, and what it was worth.
 *
 * Seeded demonstration rows are excluded rather than deleted: a reader who
 * remembers a ₹1.94L journal needs to see where that total went. */
export interface JournalProvenance {
  real_trades: number;
  excluded_seeded: number;
  excluded_pnl: number;
  excluded_win_rate: number | null;
  note: string;
}

/** Which book to score: everything, the option engine, the futures paper tool,
 *  or the Flow book on its own. Flow is held out of every other view — pooled,
 *  its one-minute legs decide each aggregate by weight of numbers. */
export type JournalBook = "all" | "options" | "futures" | "flow";

export async function getTradeJournal(limit = 500, book: JournalBook = "all") {
  const res = await fetch(`${API_BASE}/api/trade-journal?limit=${limit}&book=${book}`);
  return res.json() as Promise<{
    count: number;
    wins: number;
    losses: number;
    net_pnl: number;
    trades: JournalTrade[];
    by_trigger: Record<string, TriggerStats>;
    provenance: JournalProvenance;
    book?: string;
    flow_excluded?: boolean;
  }>;
}

export function tradeJournalCsvUrl(limit = 5000, book: JournalBook = "all") {
  return `${API_BASE}/api/trade-journal.csv?limit=${limit}&book=${book}`;
}

/** One row of the Signal Board Journal: what the board said, and what followed.
 *
 * The record made at signal time and the outcome are kept apart on purpose —
 * `market_state`, `signal_info` and `entry_plan` are exactly what was computed
 * then, and `outcome_record` is attached beside them, never merged into them, so
 * hindsight cannot edit the original call. A field that was never recorded is
 * `null` or `UNAVAILABLE`; it is never filled in with a plausible value.
 */
export interface SignalBoardRow {
  signal_id: string;
  episode_id: string | null;
  recorded_at: string;
  board_action: string;
  position_signal: string | null;
  followed: boolean;
  market_state: Record<string, unknown>;
  /** Score, confidence, reasons — and the gate trace and setup evidence, which
   *  the journal keeps here rather than in blocks of their own. */
  signal_info: Record<string, unknown>;
  volatility: Record<string, unknown>;
  entry_plan: Record<string, unknown>;
  note: string | null;
  immutable: boolean;
  /** Identity of the call, at the top level of the record. */
  session: string | null;
  time_ist: string | null;
  ts: number | null;
  instrument: string | null;
  exchange: string | null;
  lot_size: number | null;
  vehicle: string | null;
  option_type: string | null;
  strike: number | null;
  expiry: string | null;
  tradingsymbol: string | null;
  moneyness: string | null;
  premium: number | null;
  setup_type: string | null;
  volatility_class: string | null;
  signal_score: number | null;
  outcome_state: string | null;
  outcome_record: Record<string, unknown> | null;
  /** OPTIONS | FUTURES, and the exact leg behind it. */
  market?: string | null;
  signal_vehicle?: string | null;
  /** Lifecycle status: ACTIVE | RESOLVED | INVALIDATED | MISSED_DISPLAY |
   *  MISSED_EXECUTION. Derived from the lifecycle ledger, not from the outcome. */
  signal_status?: string | null;
  t1_hit: boolean;
  t2_hit: boolean;
  t3_hit: boolean;
  stop_hit: boolean;
  realized_r: number | null;
  minutes_to_resolution: number | null;
  speed: string | null;
  expiry_day: boolean | null;
  /** Conviction as the board displayed it, and the reasons it printed. */
  conviction_pct: number | null;
  /** The engine's 0..100 conviction reading. Measured at 78 average on a 42.8%
   *  event, so it is never rendered as a chance of winning. */
  conviction_meter: number | null;
  reason_text: string | null;
  reason_codes: string[] | null;
  /** What the premium did: entry, peak, worst and where it ended. Points are on
   *  the premium itself, so they are what a one-lot position moved by. Blank
   *  while the call is still open — an open trade has no exit. */
  entry_premium: number | null;
  final_premium: number | null;
  peak_points: number | null;
  peak_pct: number | null;
  worst_points: number | null;
  worst_pct: number | null;
  minutes_to_peak: number | null;
  net_points: number | null;
  net_pct: number | null;
  /** The market read and the chosen vehicle judged apart (Part 24). Present only
   *  once the call resolved; a wrong vehicle on a right market read is a
   *  VEHICLE_FAILURE, not a wrong call on direction. */
  market_signal_correct?: boolean | null;
  vehicle_signal_correct?: boolean | null;
  failure_kind?: string | null;
  market_move_points?: number | null;
  attribution_note?: string | null;
}

/** Whether the journal is actually being written to, from the server's own view
 *  of the file. Read by the Reports tab so an empty table states its reason. */
export interface JournalRecording {
  enabled: boolean;
  file: string;
  session: string;
  rows_today: number;
  last_row_at: string | null;
  write_failures: number;
  last_error: string | null;
  last_error_at: string | null;
  last_error_instrument: string | null;
  verdict: string;
}

/** Rates whose denominator is zero come back as `null`, never as 0%. */
export interface SignalBoardStats {
  session: string;
  signals_recorded: number;
  buy_signals: number;
  wait_signals: number;
  no_trade_signals: number;
  followed_signals: number;
  resolved_signals: number;
  unresolved_signals: number;
  unresolved_note: string;
  t1_hit_rate_pct: number | null;
  t2_hit_rate_pct: number | null;
  t3_hit_rate_pct: number | null;
  stop_hit_rate_pct: number | null;
  target_before_stop_pct: number | null;
  timeout_pct: number | null;
  avg_minutes_to_t1: number | null;
  avg_minutes_to_t2: number | null;
  avg_minutes_to_t3: number | null;
  /** Mean realised R over resolutions that produced one. */
  avg_r: number | null;
  net_r: number | null;
  profit_factor: number | null;
  avg_mfe_r: number | null;
  avg_mae_r: number | null;
  avg_mfe_capture: number | null;
  avg_give_back_r: number | null;
  give_back_sample: number;
}

export type SignalBoardFilters = Partial<{
  /** An IST trading date, or `TODAY` — resolved by the server, not the browser. */
  session: string;
  instrument: string;
  /** OPTIONS | FUTURES — which vehicle's book the row belongs to. */
  market: string;
  /** CE | PE | FUTURES — the exact leg, not the coarse vehicle field. */
  signal_vehicle: string;
  /** ACTIVE | RESOLVED | INVALIDATED | MISSED_DISPLAY | MISSED_EXECUTION. */
  status: string;
  /** The board's market bias, which a gate may still have refused. */
  action: string;
  /** The position the board printed — the call itself. */
  position_signal: string;
  vehicle: string;
  option_type: string;
  setup_type: string;
  volatility_class: string;
  outcome: string;
  exchange: string;
  speed: string;
  t1_hit: boolean;
  t2_hit: boolean;
  t3_hit: boolean;
  stop_hit: boolean;
  /** Only calls that carried a contract, an entry, a stop and a first target. */
  followed: boolean;
  expiry_day: boolean;
  min_score: number;
  max_score: number;
  max_minutes_to_resolution: number;
  /** Inclusive IST trading dates, `YYYY-MM-DD`. */
  date_from: string;
  date_to: string;
}>;

/** The CSV of BUY calls for a date range, as a URL the browser downloads itself.
 *  Read-only: the endpoint has no side effect and no order path. */
export function signalBoardReportCsvUrl(
  opts: { date_from?: string; date_to?: string; instrument?: string } = {},
): string {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(opts)) {
    if (v) q.set(k, v);
  }
  const qs = q.toString();
  return `${API_BASE}/api/signal-board-report.csv${qs ? `?${qs}` : ""}`;
}

export async function getSignalBoardHistory(
  limit = 300,
  filters: SignalBoardFilters = {},
) {
  const q = new URLSearchParams({ limit: String(limit) });
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === "" || v === null) continue;
    q.set(k, String(v));
  }
  const res = await fetch(`${API_BASE}/api/signal-board-history?${q.toString()}`);
  return res.json() as Promise<{
    rows: SignalBoardRow[];
    count: number;
    /** BUY calls in the same range that carried no contract, stop or target. */
    buy_calls_without_plan: number;
    sessions: string[];
    statistics: SignalBoardStats;
    /** Why the list is empty when it is empty — a failing journal and a quiet
     *  session both return zero rows and must not look alike. */
    recording: JournalRecording;
  }>;
}

// ---- Futures research vehicle + signal lifecycle (Parts 22-24, 27, 32, 34) ----

/** The research futures card. `executable` is always false: no order route. */
export async function getFuturesSignal(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/futures-signal${qs}`);
  type Payload = {
    instrument: string;
    card: FuturesSignalCard | null;
    research_only: boolean;
    executable: boolean;
    note: string;
  };
  const body = await res.json();
  // Futures research is unavailable for many instruments, and the API says so
  // with a 503 whose body is {detail}. Carry the reason as the note instead of
  // handing the board an error object shaped like a card.
  if (!res.ok) {
    return {
      instrument: instrument || "",
      card: null,
      research_only: true,
      executable: false,
      note: String(body?.detail ?? "futures research not available"),
    } as Payload;
  }
  return body as Payload;
}

export interface VehicleComparison {
  instrument: string;
  label: string;
  option: Record<string, unknown>;
  futures: Record<string, unknown>;
  /** Resolved history behind each vehicle — net R, MFE, MAE, target-before-stop.
   *  Absent, or null on a side, when nothing has resolved yet; that is not zero. */
  resolved?: {
    option: Record<string, unknown>;
    futures: Record<string, unknown>;
    note: string;
  } | null;
  reasons: string[];
  note: string;
}

/** Option plan vs futures plan on the same event. A label, never a choice. */
export async function getVehicleComparison(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/vehicle-comparison${qs}`);
  if (!res.ok) return null;
  return (await res.json()) as VehicleComparison;
}

export interface MissedSignalRow {
  episode_id: string;
  global_signal_id: string | null;
  instrument: string | null;
  market: string;
  vehicle: string | null;
  time_ist: string | null;
  signal_price: number | null;
  raw_events: number;
  engine_generated: boolean;
  dashboard_published: boolean;
  user_visible: boolean;
  paper_eligible: boolean;
  filled: boolean;
  missed_stage: string;
  missed_reason: string;
  detail: Record<string, unknown> | null;
  /** CORRECTLY_REFUSED | STOPPED_BEFORE_USER — a refusal is not a loss. */
  classification: string;
}

export async function getMissedSignals(session?: string, reason?: string) {
  const q = new URLSearchParams();
  if (session) q.set("session", session);
  if (reason) q.set("reason", reason);
  const qs = q.toString();
  const res = await fetch(`${API_BASE}/api/missed-signals${qs ? `?${qs}` : ""}`);
  return res.json() as Promise<{
    rows: MissedSignalRow[];
    count: number;
    reasons: string[];
    note: string;
  }>;
}

export interface SignalReconciliation {
  session: string | null;
  window: { ledger_events: number; journal_rows: number };
  counts: Record<string, number>;
  missed_reasons: Record<string, number>;
  missing_dashboard_publication: Record<string, unknown>[];
  missing_journal_row: Record<string, unknown>[];
  note: string;
}

export async function getSignalReconciliation(session?: string) {
  const qs = session ? `?session=${encodeURIComponent(session)}` : "";
  const res = await fetch(`${API_BASE}/api/signal-reconciliation${qs}`);
  return res.json() as Promise<SignalReconciliation>;
}

/** A best-of group with the sample size it was computed on. */
export interface GroupBest {
  name: string;
  n: number;
  avg: number;
}

export interface SignalBoardDaily {
  date?: string;
  sessions?: string[];
  overall?: SignalBoardStats;
  by_session?: Record<string, Record<string, unknown>>;
  statistics?: SignalBoardStats;
  outcome_counts?: Record<string, number>;
  t1_count?: number;
  t2_count?: number;
  t3_count?: number;
  stop_count?: number;
  timeout_count?: number;
  expired_count?: number;
  best_setup_by_avg_r?: GroupBest | null;
  best_instrument_by_avg_r?: GroupBest | null;
  highest_mfe?: { signal_id: string; instrument: string | null; mfe_r: number | null } | null;
  largest_give_back?: {
    signal_id: string;
    instrument: string | null;
    give_back_r: number | null;
    mfe_r: number | null;
    realized_r: number | null;
  } | null;
  most_common_blocker?: string | null;
  blocker_counts?: Record<string, number>;
}

/** One recorded day under a one-a-day selection policy, graded by its outcome.
 *
 * Selected on the card as it was written; `signal_score` is what the board
 * printed at signal time, not a rating of the result. `gross_only` is always
 * true — no cost is charged in this table.
 */
export interface DailyBestPick {
  policy: string;
  session: string | null;
  signal_id: string | null;
  instrument: string | null;
  symbol: string | null;
  direction: string | null;
  signal_time_ist: string | null;
  signal_score: number | null;
  conviction: number | null;
  trade_quality: string | null;
  a_plus_shadow: string | null;
  entry: number | null;
  stop: number | null;
  outcome: string | null;
  targets_reached: string[];
  best_target: string | null;
  reached_t1: boolean;
  realized_r: number | null;
  mfe_r: number | null;
  hold_minutes: number | null;
  minutes_to_t1: number | null;
  card_found: boolean;
  gross_only: boolean;
}

export interface DailyBestSummary {
  sessions: number;
  days_with_a_signal: number;
  days_no_card_to_rank: number;
  graded: number;
  reached_t1: number;
  reached_t1_pct: number | null;
  reached_t2_pct: number | null;
  reached_t3_pct: number | null;
  stopped: number;
  expired: number;
  sum_r_gross: number | null;
  mean_r_gross: number | null;
  median_r_gross: number | null;
  evidence: string;
  sessions_required: number;
  sessions_shortfall: number;
  gross_only: boolean;
}

export interface DailyBestReport {
  policies: Record<string, { history: DailyBestPick[]; summary: DailyBestSummary }>;
  every_signal: {
    sessions: number;
    graded: number;
    reached_t1: number;
    reached_t1_pct: number | null;
    signals_per_day: number | null;
    sum_r_gross: number | null;
    mean_r_gross: number | null;
  };
  ranking_validated: boolean;
  ranking_verdict: string;
  notes: string[];
  research_only: boolean;
}

export async function getDailyBest(limitDays = 120) {
  const res = await fetch(`${API_BASE}/api/daily-best?limit_days=${limitDays}`);
  return res.json() as Promise<DailyBestReport>;
}

export async function getSignalBoardDaily(session?: string) {
  const q = session ? `?session=${encodeURIComponent(session)}` : "";
  const res = await fetch(`${API_BASE}/api/signal-board-daily${q}`);
  return res.json() as Promise<SignalBoardDaily>;
}

export interface FlowSettings {
  enabled: boolean;
  paper_lots: number;
  giveback_points: number;
  min_body_frac: number;
  confirm_candles: number;
  sticky_candles: number;
}

/** Entry gates that are evaluated by the decision engine itself (not the bot). */
export interface EntryGateSettings {
  ignition_entry_enabled: boolean;
  ignition_body_factor: number;
  ignition_volume_factor: number;
  reversal_entry_enabled: boolean;
  reversal_rsi_oversold: number;
  reversal_max_atr_from_swing: number;
  supertrend_entry_enabled: boolean;
  supertrend_period: number;
  supertrend_multiplier: number;
  /** Past Target 3 only: giveback step as a % of premium before the exit fires. */
  beyond_t3_ratchet: boolean;
  beyond_t3_step_pct: number;
  beyond_t3_min_step: number;
  /** Multiplies T1/T2/T3 distance. Below 1 brings the targets closer, which
   * lifts the hit rate but cuts reward:risk — `min_reward_risk` is the floor
   * that stops that becoming a losing trade. */
  target_scale: number;
  min_reward_risk: number;
  min_stop_pct_of_premium: number;
  /** Pre-T1 profit trail: arm once the premium is this % above entry, then exit
   * on a give-back, floored at `pre_t1_giveback_min_pct` of the premium. */
  pre_t1_arm_profit_pct: number;
  pre_t1_giveback_min_pct: number;
}

export async function getEntryGates(): Promise<EntryGateSettings> {
  const res = await fetch(`${API_BASE}/api/settings`);
  return res.json();
}

/** Applied at runtime — no restart needed. */
export async function setEntryGates(gates: Partial<EntryGateSettings>) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(gates),
  });
  return res.json() as Promise<EntryGateSettings>;
}

/** Settings of the SEPARATE futures paper tool. It keeps its OWN capital pot so
 * it never competes with the option bot for money, and it sizes from the stop
 * distance because a futures loss is unbounded. `paper_only` is hard-locked on
 * the backend and cannot be switched off from here. */
export interface FuturesToolSettings {
  enabled: boolean;
  capital: number;
  risk_per_trade_pct: number;
  margin_pct: number;
  paper_lots: number;
  // Take every lot the free margin can carry instead of the lots the risk budget
  // allows. Off by default — on Crude that is ~5% of the pot per trade, not 1%.
  size_by_margin: boolean;
  max_concurrent: number;
  // Dead-market filter as a % of price. The absolute points version below is a
  // legacy override (0 = off): 5 points is nothing on Crude and more than a
  // 1-minute ATR on a mid-priced stock future, which made the tool Crude-only.
  min_atr_pct: number;
  min_atr_points: number;
  min_adx: number;
  stop_atr: number;
  t1_r: number;
  t2_r: number;
  t3_r: number;
  t1_lock_r: number;
  // Once T1 is reached the floor trails the peak by this many R until T2.
  post_t1_trail_r: number;
  max_cost_to_risk_pct: number;
  reentry_cooldown_sec: number;
  flip_block_sec: number;
  max_consecutive_losses: number;
  stand_down_sec: number;
  max_daily_loss: number;
  max_trades_per_day: number;
  beyond_t3_step_atr: number;
  slippage_points: number;
  brokerage_per_order: number;
  no_entry_minutes: number;
  paper_only: boolean;
}

/** Live state of the futures risk governor: what it has spent today and which
 * instruments it is refusing to trade right now. */
export interface FuturesGuardStatus {
  day: string;
  day_pnl: number;
  daily_loss_cap: number;
  trades_today: Record<string, number>;
  consecutive_losses: Record<string, number>;
  standing_down: Record<string, number>;
}

/** Account-wide option risk state. One pot for the whole book, persisted, so a
 * cap cannot be multiplied by the number of instruments or reset by a restart. */
export interface AccountRiskStatus {
  day: string;
  day_pnl: number;
  daily_loss_cap: number;
  trades_today: number;
  max_trades_per_day: number;
  consecutive_losses: number;
  stand_down_sec_left: number;
  blocked: string | null;
  account_wide: boolean;
  opening_window_minutes: number;
  reentry_cooldown_sec: number;
  post_stop_cooldown_sec: number;
  cooldowns: Record<
    string,
    { seconds_since_exit: number; stopped_out: boolean; blocked: string | null }
  >;
}

export async function getRiskGuard() {
  const res = await fetch(`${API_BASE}/api/risk-guard`);
  return res.json() as Promise<AccountRiskStatus>;
}

export interface FuturesTradeRow {
  instrument: string;
  side: "LONG" | "SHORT";
  entry: number;
  exit: number;
  lots: number;
  lot_size: number;
  // Capital accounting: the margin actually blocked, the contract's notional
  // value, the rupee risk at entry and the return measured on the margin.
  capital_used?: number;
  notional_rupees?: number;
  risk_rupees?: number;
  cost_rupees?: number;
  return_on_capital_pct?: number | null;
  gross_points: number;
  cost_points: number;
  net_points: number;
  net_rupees: number;
  risk_points: number;
  r_multiple: number | null;
  max_favourable_points: number;
  exit_reason: string;
  entry_reason: string;
  holding_minutes: number;
  closed: number;
  win: boolean;
}

/** A position the bot is holding right now (any instrument, not just the one on
 * screen) — an instrument blocked by the concurrency cap otherwise looks idle. */
export interface FuturesOpenPosition {
  instrument: string;
  side: "LONG" | "SHORT";
  entry: number;
  lots: number;
  lot_size: number;
  stop: number;
  target1: number;
  target2: number;
  target3: number;
  locked_floor: number | null;
  levels_hit: string;
  risk_points: number;
  risk_rupees: number;
  opened: number;
  entry_reason: string;
}

export interface FuturesReport {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  net_rupees: number;
  net_points: number;
  avg_win_points: number | null;
  avg_loss_points: number | null;
  by_exit_reason: Record<string, { trades: number; net_rupees: number }>;
  paper_only: boolean;
  note: string;
  guard: FuturesGuardStatus;
}

export async function getFuturesReport(limit = 500) {
  const res = await fetch(`${API_BASE}/api/futures-report?limit=${limit}`);
  return res.json() as Promise<{
    report: FuturesReport;
    trades: FuturesTradeRow[];
    open_positions: FuturesOpenPosition[];
    capital: { pot: number; deployed_margin: number; free: number };
    settings: FuturesToolSettings;
  }>;
}

/** Applied at runtime — no restart needed. Cannot enable live trading: the
 * futures tool has no live order route at all. */
export async function setFuturesSettings(futures: Partial<FuturesToolSettings>) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ futures }),
  });
  return res.json() as Promise<{ entry_mode: string; futures: FuturesToolSettings }>;
}

export async function setFlowSettings(flow: Partial<FlowSettings>) {
  const res = await fetch(`${API_BASE}/api/settings`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ flow }),
  });
  return res.json() as Promise<{ entry_mode: string; flow: FlowSettings }>;
}

// ---- Daily auto-pick / top movers ----
export interface MoverRow {
  symbol: string;
  display: string;
  type?: string;
  percent_change: number | null;
  direction: string | null;
  ltp: number | null;
  in_universe?: boolean;
}
export interface AutoPick {
  enabled: boolean;
  count?: number;
  core?: { symbol: string; display: string }[];
  movers?: MoverRow[];
  rows?: MoverRow[];
  universe?: string[];
  source?: string | null;
  as_of?: string | null;
}

export async function getAutoPick(refresh = false) {
  const res = await fetch(`${API_BASE}/api/auto-pick${refresh ? "?refresh=true" : ""}`);
  return res.json() as Promise<AutoPick>;
}

// ---- Phase 2: research & validation platform ----
export interface ResearchStatus {
  instrument: string;
  backend: string;
  futures_rows: number;
  signals_recorded: number;
  replay_trades: number;
  shadow_mode: boolean;
  data_provider: string;
  auto_capture_active: boolean;
}

export interface GroupStat {
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
}

export interface SplitStat {
  trades: number;
  win_rate: number | null;
  net_pnl: number | null;
}

export interface ResearchReport {
  instrument: string;
  source: string;
  backend: string;
  is_real_data: boolean;
  data_source: string;
  note: string;
  trades_recorded: number;
  performance: Record<string, number | number[] | null>;
  by_regime: Record<string, GroupStat>;
  by_hour: Record<string, GroupStat>;
  by_weekday: Record<string, GroupStat>;
  by_month: Record<string, GroupStat>;
  rule_impact: Record<string, Record<string, SplitStat>>;
  trade_explanations: Array<{
    entry_ts: number | null;
    option: string | null;
    pnl: number;
    exit_reason: string | null;
    mfe: number;
    mae: number;
    win: boolean;
    why: string;
    factors: string[];
  }>;
  distributions: {
    signal: Record<string, number>;
    confidence: Record<string, number>;
    quantum_score: Record<string, number>;
    trade_score: Record<string, number>;
  };
}

export interface DataQuality {
  instrument: string;
  rows_checked: number;
  issues: number;
  by_kind: Record<string, number>;
  span: { from: string; to: string } | null;
  clean: boolean;
}

export interface ShadowSummary {
  resolved: number;
  accuracy: number | null;
  by_signal: Record<string, { n: number; accuracy: number }>;
}

export async function getResearchStatus(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/research/status${qs}`);
  return res.json() as Promise<ResearchStatus>;
}

export async function getResearchReport(instrument?: string, source = "replay") {
  const qs = `?source=${encodeURIComponent(source)}${
    instrument ? `&instrument=${encodeURIComponent(instrument)}` : ""
  }`;
  const res = await fetch(`${API_BASE}/api/research/report${qs}`);
  return res.json() as Promise<ResearchReport>;
}

export async function getDataQuality(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/research/data-quality${qs}`);
  return res.json() as Promise<DataQuality>;
}

export async function getShadowSummary(instrument?: string) {
  const qs = instrument ? `?instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/research/shadow${qs}`);
  return res.json() as Promise<ShadowSummary>;
}

export async function runCapture(instrument?: string, steps = 400) {
  const res = await fetch(`${API_BASE}/api/research/capture`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument, steps }),
  });
  return res.json();
}

export async function runReplay(instrument?: string, speed = 100) {
  const res = await fetch(`${API_BASE}/api/research/replay`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument, speed }),
  });
  return res.json();
}

export async function runShadow(instrument?: string, horizon_minutes?: number) {
  const res = await fetch(`${API_BASE}/api/research/shadow/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ instrument, horizon_minutes }),
  });
  return res.json();
}

export interface SetupRow {
  id: string | null;
  entry_ts: number | null;
  exit_ts: number | null;
  option_symbol: string | null;
  option_type: string | null;
  entry: number | null;
  exit: number | null;
  pnl: number | null;
  mfe: number | null;
  mae: number | null;
  duration_min: number | null;
  market_regime: string | null;
  trade_score: number | null;
  confidence: number | null;
  exit_reason: string | null;
  win: boolean;
}

export interface SetupLibrary {
  instrument: string;
  source: string;
  is_real_data: boolean;
  data_source: string;
  total: number;
  matched: number;
  setups: SetupRow[];
  note: string | null;
}

export async function getSetupLibrary(
  instrument?: string,
  opts: { outcome?: string; regime?: string; minConfidence?: number; minTradeScore?: number } = {},
): Promise<SetupLibrary> {
  const p = new URLSearchParams();
  if (instrument) p.set("instrument", instrument);
  if (opts.outcome && opts.outcome !== "all") p.set("outcome", opts.outcome);
  if (opts.regime) p.set("regime", opts.regime);
  if (opts.minConfidence != null) p.set("min_confidence", String(opts.minConfidence));
  if (opts.minTradeScore != null) p.set("min_trade_score", String(opts.minTradeScore));
  const res = await fetch(`${API_BASE}/api/research/setups?${p.toString()}`);
  return res.json() as Promise<SetupLibrary>;
}

export interface WatchlistItem {
  symbol: string;
  display: string;
  exchange: string;
  enabled: boolean;
}

export interface WatchlistState {
  instruments: WatchlistItem[];
  manual: boolean;
  env_locked: boolean;
}

export async function getWatchlist(): Promise<WatchlistState> {
  const res = await fetch(`${API_BASE}/api/watchlist`);
  return res.json() as Promise<WatchlistState>;
}

export async function setWatchlist(enabled: string[]): Promise<{
  enabled: string[];
  manual: boolean;
  env_locked: boolean;
}> {
  const res = await fetch(`${API_BASE}/api/watchlist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  return res.json();
}

export interface MoverRow {
  symbol: string;
  display: string;
  exchange: string;
  measured: boolean;
  net_move_pct: number | null;
  score: number | null;
  premium: number | null;
  atr_pct: number | null;
  change_pct: number | null;
  seconds_since_tick: number | null;
  pinned: boolean;
  active: boolean;
  keep?: boolean;
}

export interface MoversPreview {
  rows: MoverRow[];
  keep: string[];
  requested: number;
  measured_count: number;
  unmeasured_count: number;
  active_count: number;
  coverage_pct: number;
  min_coverage_pct: number;
  min_move_pct: number;
  env_locked: boolean;
  ready: boolean;
  applied?: boolean;
  reason?: string;
}

export async function getWatchlistMovers(count = 15): Promise<MoversPreview> {
  const res = await fetch(`${API_BASE}/api/watchlist/movers?count=${count}`);
  return res.json() as Promise<MoversPreview>;
}

export async function applyWatchlistMovers(count = 15): Promise<MoversPreview> {
  const res = await fetch(`${API_BASE}/api/watchlist/movers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ count }),
  });
  return res.json() as Promise<MoversPreview>;
}

export interface DayMoversLogRow {
  ts: number;
  day: string;
  action: string;
  detail: string;
  symbols: string[];
}

export interface DayMoversStatus {
  enabled: boolean;
  // How many names auto settled on today (0 before the first pick). Reported by
  // the engine, not chosen by the user.
  count: number;
  min_count: number;
  max_count: number;
  day: string;
  held: string[];
  held_names: string[];
  picked_at: number | null;
  picked_minutes_ago: number | null;
  pick_note: string;
  waiting_reason: string | null;
  last_check: number | null;
  pick_after_ist_min: number;
  min_coverage_pct: number;
  replace_dead: boolean;
  dead_strikes_needed: number;
  strikes: Record<string, number>;
  log: DayMoversLogRow[];
  candidates: MoverRow[];
  candidate_count: number;
  measured_count: number;
  manual_watchlist: string[];
  manual_watchlist_active: boolean;
  env_locked: boolean;
}

export async function getDayMovers(): Promise<DayMoversStatus> {
  const res = await fetch(`${API_BASE}/api/day-movers`);
  return res.json() as Promise<DayMoversStatus>;
}

export async function setDayMovers(patch: {
  enabled?: boolean;
  replace_dead?: boolean;
  pick_after_ist_min?: number;
  repick?: boolean;
}): Promise<DayMoversStatus> {
  const res = await fetch(`${API_BASE}/api/day-movers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  return res.json() as Promise<DayMoversStatus>;
}

export interface ScanHealth {
  active_count: number;
  ticked_count: number;
  watched: string[];
  median_age_sec: number | null;
  worst_age_sec: number | null;
  scan_target_sec: number;
  // Warm-up is a distinct, broker-rate-limited phase from scanning.
  warm_total: number;
  warm_done: number;
  tick_cost_ms: number | null;
  scanned_per_cycle: number;
  sweep_estimate_sec: number | null;
  // Instruments skipped because their exchange is shut (NSE/BSE close at 15:30
  // IST while MCX runs to 23:30). They resume by themselves at the next open.
  closed_skipped: number;
  behind: string[];
  behind_count: number;
  armed: boolean;
  ages: Record<string, number>;
}

export async function getScanHealth(): Promise<ScanHealth> {
  const res = await fetch(`${API_BASE}/api/scan-health`);
  return res.json() as Promise<ScanHealth>;
}

export interface AccountTradeRow {
  date: string;
  instrument: string;
  option: string | null;
  option_type: string | null;
  lots: number | null;
  entry: number | null;
  exit: number | null;
  net_pnl: number;
  win: boolean;
  holding_minutes: number | null;
}

export interface AccountDayRow {
  date: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  equity: number;
}

export interface AccountMonthRow {
  month: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number | null;
}

export interface AccountSummary {
  mode: string;
  is_real_feed: boolean;
  note: string;
  starting_capital: number;
  realised_pnl: number;
  equity: number;
  return_pct: number;
  overall: {
    trades: number;
    wins: number;
    losses: number;
    win_rate: number;
    net_pnl: number;
    gross_profit: number;
    gross_loss: number;
    profit_factor: number | null;
    avg_pnl: number;
  };
  by_day: AccountDayRow[];
  by_month: AccountMonthRow[];
  recent_trades: AccountTradeRow[];
  months_available: string[];
}

export async function getAccount(): Promise<AccountSummary> {
  const res = await fetch(`${API_BASE}/api/account`);
  return res.json();
}

export function accountReportUrl(month: string): string {
  return `${API_BASE}/api/account/report.csv?month=${encodeURIComponent(month)}`;
}

export interface LivePosition {
  account: string;
  symbol: string | null;
  product: string | null;
  net_qty: string | number | null;
  ltp: number | null;
  buy_avg: number | null;
  sell_avg: number | null;
  unrealised: number;
  realised: number;
}

export interface LiveAccount {
  enabled: boolean;
  ok?: boolean;
  read_only?: boolean;
  reason?: string;
  accounts?: {
    client_masked: string;
    available_cash?: number | null;
    used_margin?: number | null;
    net_balance?: number | null;
    funds_error?: string;
    positions_error?: string;
  }[];
  totals?: {
    available_cash: number;
    used_margin: number;
    net_balance: number;
    open_pnl: number;
    realised_pnl_today: number;
  };
  positions?: LivePosition[];
}

export async function getLiveAccount(): Promise<LiveAccount> {
  const res = await fetch(`${API_BASE}/api/account/live`);
  return res.json();
}

export async function getEconomicEvents(): Promise<{
  events: EconomicEvent[];
  guard: EventGuard;
}> {
  const res = await fetch(`${API_BASE}/api/events`);
  return res.json();
}

export async function getFundamentals(instrument: string): Promise<Fundamentals> {
  const res = await fetch(
    `${API_BASE}/api/fundamentals?instrument=${encodeURIComponent(instrument)}`,
  );
  return res.json();
}

// ---- Signal diagnostics: which gate refused each setup, and what it then did ----
export interface BlockerStat {
  gate: string;
  refusals: number;
  avg_mfe_pct: number;
  best_mfe_pct: number | null;
  over_5pct: number;
  over_10pct: number;
  over_20pct: number;
}

export interface MissedRecord {
  instrument: string;
  option_symbol: string;
  side: string | null;
  ts_open: number;
  ts_close: number;
  primary_blocker: string | null;
  secondary_blocker: string | null;
  ref_premium: number;
  mfe_points: number;
  mae_points: number;
  mfe_pct: number | null;
  mae_pct: number | null;
  seconds_to_peak: number;
}

export interface LiveBlocker {
  instrument: string;
  signal: string | null;
  primary_blocker: string | null;
  secondary_blocker: string | null;
  blockers: string[];
  confidence: number | null;
  confidence_gate: number | null;
  entry_trigger: string | null;
}

/** Where a BUY died between the signal and the fill. */
export interface ExecutionFunnel {
  stages: string[];
  reached: Record<string, number>;
  blocked: { stage: string; blocker: string; count: number }[];
  recent: {
    ts: number;
    stage: string;
    primary_blocker: string;
    secondary_blocker: string | null;
    instrument: string;
    option: string;
    value: number | string | null;
    threshold: number | string | null;
    where: string;
    reason: string;
  }[];
  executable_pct: number;
  blocked_after_signal: number;
  note: string;
}

export interface SignalDiagnostics {
  enabled: boolean;
  total: number;
  window_minutes: number;
  live: LiveBlocker[];
  gates: BlockerStat[];
  recent: MissedRecord[];
  execution: ExecutionFunnel;
  note: string;
}

export async function getSignalDiagnostics(): Promise<SignalDiagnostics> {
  const res = await fetch(`${API_BASE}/api/signal-diagnostics`);
  return res.json();
}

/** One instrument's fast-scan row. RESEARCH ONLY: the scanner ranks where the
 *  market is moving, it never produces a BUY — the unchanged decision engine
 *  does that. */
export interface OpportunityRow {
  instrument: string;
  display: string;
  exchange: string;
  ltp: number | null;
  direction: string;
  move_1m_pct: number | null;
  move_3m_pct: number | null;
  move_5m_pct: number | null;
  move_15m_pct: number | null;
  session_change_pct: number | null;
  roc_delta: number | null;
  atr: number | null;
  atr_expansion: number | null;
  volatility_state: string;
  vwap_distance_atr: number | null;
  structure: string;
  momentum_state: string;
  relative_volume: number | null;
  volume_state: string;
  oi_state: string;
  liquidity_state: string;
  extension_pct_of_swing: number | null;
  room: string;
  movement_score: number | null;
  acceleration_score: number | null;
  volatility_score: number | null;
  participation_score: number | null;
  momentum_score: number | null;
  liquidity_score: number | null;
  freshness_score: number | null;
  extension_penalty: number | null;
  opportunity_score: number | null;
  data_quality_score: number | null;
  confidence_in_score: number | null;
  data_age_ms: number | null;
  feed_state: string;
  classification: string;
  verdict: string;
  bars_available: number;
  reasons: string[];
}

export interface MarketOpportunities {
  as_of: number;
  view: string;
  market_health: {
    instruments_scanned: number;
    ranked: number;
    opportunities: number;
    watch: number;
    fresh_instruments: number | null;
    aging_instruments: number | null;
    stale_instruments: number | null;
    no_data_instruments: number | null;
  };
  rows: OpportunityRow[];
  excluded: {
    instrument: string;
    feed_state: string;
    classification: string;
    reason: string;
  }[];
  handoff: {
    ts: number;
    instrument: string;
    scanner_rank: number;
    scanner_score: number | null;
    scanner_state: string;
    scanner_verdict: string;
    decision_score: number | null;
    final_signal: string | null;
  }[];
  note: string;
}

export async function getMarketOpportunities(
  top = 10,
  view = "all",
): Promise<MarketOpportunities> {
  const res = await fetch(
    `${API_BASE}/api/market-opportunities?top=${top}&view=${encodeURIComponent(view)}`,
  );
  return res.json();
}

/* ---------------------------------------------------------------- Phase 6 AI
 * AI engine (LIVE PAPER ONLY). Every endpoint below is read-only against
 * production and paper-only against money: there is no real-order call here
 * because the backend exposes none.
 */
export interface AiRegime {
  state: string;
  confidence: number;
  direction_bias: string;
  trend_strength: number;
  volatility_state: string;
  extension_atr: number;
  pullback_depth_atr: number;
  bars_since_extreme: number;
  follow_ok: boolean;
  reasons: string[];
}

export interface AiDirection {
  available: boolean;
  p_ce: number | null;
  p_pe: number | null;
  preferred_side: string | null;
  edge: number;
  agrees_with_move: boolean | null;
  reasons: string[];
}

export interface AiEntryQuality {
  verdict: string;
  score: number;
  pullback_depth_atr: number;
  bars_since_extreme: number;
  extension_atr: number;
  vwap_dist_atr: number;
  suggested_entry: number | null;
  reasons: string[];
}

export interface AiDecisionRow {
  id?: string;
  instrument: string;
  decision: string;
  side: string | null;
  probability: number | null;
  expected_r: number | null;
  suggested_entry: number | null;
  price: number | null;
  atr: number | null;
  feed_state: string;
  data_age_ms: number | null;
  regime: AiRegime | Record<string, never>;
  direction: AiDirection | Record<string, never>;
  entry_quality: AiEntryQuality | Record<string, never>;
  probability_detail: Record<string, unknown>;
  blocked_by: string | null;
  reasons: string[];
  real_money_execution: string;
  baseline?: {
    decision: string | null;
    side: string | null;
    confidence: number | null;
  };
  scan?: { state?: string; verdict?: string; score?: number | null } | null;
  bars?: number;
}

export interface AiSafety {
  paper_mode: boolean;
  real_money_execution: string;
  trade_mode: string;
  ai_paper_enabled: boolean;
  guard: string;
  enforced_at: string[];
  blocked_attempts: number;
  bypass: string;
  note: string;
  refusals?: { ts: number; caller: string; reason: string }[];
}

export interface AiModelStatus {
  available: boolean;
  reason?: string;
  path?: string;
  model_version?: string | null;
  trained_at_ist?: string | null;
  instruments?: string[];
  train_rows?: number | null;
  metrics?: Record<string, unknown>;
  limitations?: string[];
}

export interface AiStatus {
  enabled: boolean;
  paper_enabled: boolean;
  safety: AiSafety;
  model: AiModelStatus;
  interval_sec: number;
  cycles: number;
  last_cycle_ts: number;
  last_cycle_ms: number;
  decisions_recorded: number;
  open_positions: number;
  errors: string[];
  thresholds: Record<string, number>;
}

export interface AiPaperTrade {
  id: string;
  instrument: string;
  symbol: string;
  side: string;
  lots: number;
  lot_size: number;
  entry_ts: number;
  entry_premium: number;
  stop: number | null;
  target1: number | null;
  current_premium: number | null;
  unrealized_pnl: number | null;
  mfe: number | null;
  mae: number | null;
  status: string;
  exit_ts: number | null;
  exit_premium: number | null;
  exit_reason: string | null;
  realized_pnl: number | null;
  r_multiple: number | null;
  hold_sec: number | null;
  probability: number | null;
  regime: string | null;
  entry_quality: string | null;
}

export interface AiPaperPnl {
  paper_mode: boolean;
  capital: number;
  open_positions: number;
  closed_trades: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  trades_today: number;
  realized_today: number;
  costs_charged: number;
}

export interface AiComparison {
  resolved_decisions: number;
  verdict_allowed: boolean;
  min_paired_for_verdict: number;
  basis: string;
  overlap: {
    both_bought: number;
    baseline_only: number;
    ai_only: number;
    neither: number;
  };
  baseline: Record<string, number | null>;
  ai: Record<string, number | null>;
  expectancy_difference_r: number | null;
  ai_missed_baseline_winners: number;
  ai_avoided_baseline_losers: number;
  paper_book: Record<string, unknown>;
  warning: string;
}

export async function getAiStatus(): Promise<AiStatus> {
  const res = await fetch(`${API_BASE}/api/ai/status`);
  return res.json();
}

export async function getAiDecision(instrument: string): Promise<AiDecisionRow> {
  const res = await fetch(
    `${API_BASE}/api/ai/decision?instrument=${encodeURIComponent(instrument)}`,
  );
  return res.json();
}

export async function getAiDecisions(
  instrument?: string,
  limit = 50,
): Promise<{ as_of: number; rows: Record<string, unknown>[] }> {
  const q = instrument ? `&instrument=${encodeURIComponent(instrument)}` : "";
  const res = await fetch(`${API_BASE}/api/ai/decisions?limit=${limit}${q}`);
  return res.json();
}

export async function getAiPaperPositions(): Promise<{
  open: AiPaperTrade[];
  recent: AiPaperTrade[];
  pnl: AiPaperPnl;
}> {
  const res = await fetch(`${API_BASE}/api/ai/paper/positions`);
  return res.json();
}

export async function getAiComparison(): Promise<AiComparison> {
  const res = await fetch(`${API_BASE}/api/ai/comparison`);
  return res.json();
}

export async function getAiSafety(): Promise<AiSafety> {
  const res = await fetch(`${API_BASE}/api/ai/safety`);
  return res.json();
}

/** Close a PAPER position. There is intentionally no real-position equivalent. */
export async function closeAiPaperTrade(
  tradeId: string,
  reason = "AI_EXIT",
): Promise<{ result?: string; message?: string }> {
  const res = await fetch(`${API_BASE}/api/ai/paper/close`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trade_id: tradeId, reason }),
  });
  return res.json();
}

/** Run one AI cycle now (paper only) — used when the background loop is off. */
export async function runAiCycle(): Promise<{
  decisions: AiDecisionRow[];
  events: Record<string, unknown>[];
  resolved: number;
  errors: string[];
  ms: number;
}> {
  const res = await fetch(`${API_BASE}/api/ai/cycle`, { method: "POST" });
  return res.json();
}

/* ------------------------------------------------------------------ Phase 17
 * Vehicle evidence + the A+ paper board. Read-only and paper-only: there is no
 * mutating endpoint in this group, so nothing on these screens can place,
 * modify or promote an order.
 */
export interface P17QualityTally {
  total: number;
  counts: Record<string, number>;
  exact_match_rate_pct: number | null;
  usable_rate_pct: number | null;
  unmatched: number;
  unmatched_pct: number | null;
  target_pct: number;
  meets_target: boolean | null;
  tolerances_ms: { exact: number; good: number; degraded: number };
}

export interface P17Health {
  enabled: boolean;
  observations: number;
  written: number;
  failures: number;
  last_error: string | null;
  last_observation_ts: number | null;
  skipped_ineligible: number;
  universe: string[];
  today_rows: number;
  session: string | null;
  tracker: {
    open_legs: number;
    budget: number;
    opened: number;
    dropped: number;
    resolved: number;
    follow_minutes: number;
  };
  paper: {
    open: number;
    entered: number;
    refused: number;
    closed: number;
    enabled: boolean;
  };
  entry_history: Record<string, number | null>;
  store: Record<string, unknown>;
}

export interface P17BoardRow {
  observation_id: string;
  instrument: string;
  direction: string | null;
  vehicle: string | null;
  symbol: string | null;
  strike: number | null;
  expiry: string | null;
  days_to_expiry: number | null;
  premium: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  spread_pct: number | null;
  entry: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  a_plus_score: number | null;
  a_plus_label: string | null;
  components: Record<string, number | null> | null;
  t1_score: number | null;
  t1_rank: string | null;
  t1_basis: string | null;
  promotable: boolean | null;
  not_a_probability: boolean;
  vehicle_class: string | null;
  cost_points: number | null;
  cost_over_expected_move: number | null;
  entry_quality: string | null;
  expected_hold_minutes: number | null;
  data_quality: string;
  reasons: string[] | null;
  paper_only: boolean;
  no_real_order: boolean;
}

export interface P17Board {
  preferred: P17BoardRow[];
  watch: P17BoardRow[];
  candidates_considered: number;
  a_plus_found: number;
  watch_found: number;
  empty_is_valid: boolean;
  paper_only: boolean;
  no_real_order: boolean;
  note: string;
}

export interface P17Side {
  symbol: string | null;
  vehicle: string | null;
  strike: number | null;
  premium: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  spread_pct: number | null;
  oi: number | null;
  volume: number | null;
  iv: number | null;
  delta: number | null;
  moneyness: string | null;
  vehicle_class: string | null;
  cost_points: number | null;
  data_quality: string;
}

export interface P17EvidenceRow {
  observation_id: string;
  signal_ts: number;
  instrument: string;
  candidate_class: string | null;
  direction: string | null;
  market_signal: string | null;
  regime: string | null;
  htf_alignment: string | null;
  selected_vehicle: string | null;
  ce_pe: { selected: P17Side; opposite: P17Side };
  room: {
    t1_score: number | null;
    t1_rank: string | null;
    not_a_probability: boolean;
    underlying_move_required: number | null;
    room_after_spread: number | null;
  };
  entry_quality: string | null;
  a_plus_score: number | null;
  a_plus_label: string | null;
  both_sides: boolean;
  data_quality: string;
  tracking: string | null;
}

export interface P17Evidence {
  rows: P17EvidenceRow[];
  counts: P17QualityTally;
  note: string;
  research_only: boolean;
}

export interface P17PaperRow {
  episode_id: string;
  instrument: string;
  vehicle: string | null;
  symbol: string | null;
  entry_ts: number | null;
  entry_price: number | null;
  entry_side: string | null;
  exit_ts: number | null;
  exit_price: number | null;
  exit_side: string | null;
  exit_reason: string | null;
  gross_points: number | null;
  cost_points: number | null;
  net_points: number | null;
  net_r: number | null;
  mfe_points: number | null;
  mae_points: number | null;
  hold_minutes: number | null;
  cost_status: string | null;
  paper_only: boolean;
  no_real_order: boolean;
}

export interface P17Paper {
  open: P17PaperRow[];
  resolved: P17PaperRow[];
  report: Record<string, unknown>;
  paper_only: boolean;
  no_real_order: boolean;
}

export interface P17Summary {
  questions: { total: number; requires_more_data: number };
  promotion: { status: string | null; verdict: string | null };
  candidates: number | null;
  exact_match_rate_pct: number | null;
  meets_target: boolean | null;
  both_side_pct: number | null;
  a_plus: number;
  paper_entries: number | null;
  paper_net_points: number | null;
  paper_cost_points: number | null;
  paper_expectancy_r: number | null;
  a_plus_status: string | null;
  vehicle_classes: Record<string, number> | null;
  wrong_market_pct: number | null;
  wrong_vehicle_pct: number | null;
  basis: string;
  paper_only: boolean;
  no_real_order: boolean;
  questions_detail: { question: string; answer: unknown }[];
}

export async function getP17Health(): Promise<P17Health> {
  const res = await fetch(`${API_BASE}/api/phase17/health`);
  return res.json();
}

export async function getP17Board(): Promise<P17Board> {
  const res = await fetch(`${API_BASE}/api/phase17/board`);
  return res.json();
}

export async function getP17Evidence(limit = 60): Promise<P17Evidence> {
  const res = await fetch(`${API_BASE}/api/phase17/evidence?limit=${limit}`);
  return res.json();
}

export async function getP17Paper(): Promise<P17Paper> {
  const res = await fetch(`${API_BASE}/api/phase17/paper`);
  return res.json();
}

export async function getP17Summary(): Promise<P17Summary> {
  const res = await fetch(`${API_BASE}/api/phase17/summary`);
  return res.json();
}

// ---------------------------------------------------------------------------
// Phase 18 — the closing auction session (15:10-15:30 IST). RESEARCH / PAPER.
//
// Everything below is read-only by construction: there is no CAS mutation
// endpoint to call, so no control in this app can send a CAS order.
// ---------------------------------------------------------------------------
export interface CasScoreComponent {
  points: number;
  max: number;
  detail: string;
}

export interface CasCard {
  strategy: string;
  paper_only: boolean;
  no_real_order: boolean;
  banner: string;
  session: string | null;
  instrument: string;
  exchange: string | null;
  timestamp: number | null;
  cas_state: string | null;
  sub_window: string | null;
  remaining_seconds: number | null;
  expiry: string | null;
  days_to_expiry: number | null;
  expiry_class: string | null;
  direction: string;
  direction_components: Record<string, { vote: string; detail: string }> | null;
  data_quality: string | null;
  observation_id: string | null;
  signal: string;
  status: string;
  reason: string;
  option_type: string | null;
  rung?: string | null;
  strike?: number | null;
  symbol?: string | null;
  bid?: number | null;
  ask?: number | null;
  spread?: number | null;
  spread_pct?: number | null;
  preferred_entry?: number | null;
  iv?: number | null;
  delta?: number | null;
  gamma?: number | null;
  theta?: number | null;
  oi?: number | null;
  volume?: number | null;
  cas_score: number | null;
  cas_score_is_probability: boolean;
  cas_score_components?: Record<string, CasScoreComponent> | null;
  t1_rank?: string | null;
  entry_quality?: string | null;
  tradability?: string | null;
  expected_hold_seconds?: number | null;
  executability?: string | null;
  // The explicitly requested card fields.
  current_underlying: number | null;
  cas_expected_move_points: number | null;
  cas_move_range: { low: number | null; high: number | null } | null;
  move_pct: number | null;
  move_atr: number | null;
  expectation_basis: string | null;
  expectation_samples: number | null;
  expectation_warning: string | null;
  current_option_premium: number | null;
  expected_option_move_points: number | null;
  expected_option_move_pct: number | null;
  t1: number | null;
  t2: number | null;
  t3: number | null;
  stop: number | null;
  risk: number | null;
  reward_risk: number | null;
  estimated_seconds_to_t1: number | null;
  time_to_t1_samples: number | null;
  time_to_t1_note: string | null;
}

export interface CasPaperRow {
  episode_id: string;
  session: string | null;
  instrument: string;
  strategy_variant: string | null;
  option_type: string | null;
  rung: string | null;
  symbol: string | null;
  entry_ts: number | null;
  entry_price: number | null;
  exit_ts: number | null;
  exit_price: number | null;
  status: string | null;
  gross_rupees: number | null;
  net_rupees: number | null;
  net_r: number | null;
  theoretical_rupees: number | null;
  mfe_points: number | null;
  mae_points: number | null;
  hold_seconds: number | null;
  executability: string | null;
  data_quality: string | null;
  paper_only: boolean;
}

export interface CasHealth {
  strategy: string;
  paper_only: boolean;
  no_real_order: boolean;
  enabled: boolean;
  paper_enabled: boolean;
  window: string;
  session: string | null;
  observations: number;
  written: number;
  cards: number;
  entries: number;
  resolutions: number;
  failures: number;
  last_error: string | null;
  last_observation_ts: number | null;
  skipped_outside_window: number;
  skipped_ineligible: number;
  open_legs: number;
  resolved_legs: number;
  quality: {
    status: string;
    match_rate_pct: number | null;
    target_pct: number;
    meets_target: boolean;
    counts: Record<string, number>;
    note: string;
  };
}

export interface CasBoard {
  cards: CasCard[];
  open: CasPaperRow[];
  resolved: CasPaperRow[];
  window: string;
  strategy: string;
  paper_only: boolean;
  no_real_order: boolean;
  banner: string;
}

export interface CasReconciliation {
  strategy: string;
  observations: number;
  candidates: number;
  ce_captures: number;
  pe_captures: number;
  both_side_rungs: number;
  exact_timestamp_matches: number;
  near_exact_matches: number;
  exact_or_near_pct: number | null;
  missing_books: number;
  paper_signals: number;
  paper_entries: number;
  paper_exits: number;
  resolved: number;
  unresolved: number;
  unresolved_reasons: Record<string, number>;
  quality_gate: string;
  arithmetic: {
    signals_to_entries_lost: number;
    entries_to_exits_lost: number;
    exits_to_resolved_lost: number;
  };
  note: string;
}

export interface CasQuestion {
  question: string;
  answer: string;
  basis: string;
}

export interface CasReport {
  daily: Record<string, unknown>;
  strategy: Record<string, unknown>;
  capture: Record<string, unknown>;
  paper: Record<string, unknown>;
  validation: {
    strategy: string;
    verdict: string;
    reason: string;
    note: string;
    regime_start: string;
    promotion_is_manual: boolean;
    excluded_pre_cas_rows: number;
    fold_sessions: Record<string, string[]>;
    folds: Record<string, Record<string, number | string | null>>;
    evidence: {
      all_met: boolean;
      missing: string[];
      checks: Record<string, { have: number | null; need: number; ok: boolean }>;
    };
  };
  questions: CasQuestion[];
  paper_only: boolean;
  no_real_order: boolean;
}

export async function getCasHealth(): Promise<CasHealth> {
  const res = await fetch(`${API_BASE}/api/cas-health`);
  return res.json();
}

export async function getCasBoard(): Promise<CasBoard> {
  const res = await fetch(`${API_BASE}/api/cas-board`);
  return res.json();
}

export async function getCasReconciliation(): Promise<CasReconciliation> {
  const res = await fetch(`${API_BASE}/api/cas-reconciliation`);
  return res.json();
}

export async function getCasReport(): Promise<CasReport> {
  const res = await fetch(`${API_BASE}/api/cas-report`);
  return res.json();
}

// --- Phase 19 — futures paper book, T1 calibration, production readiness -----
// Read-only. Nothing in this block can place, size, activate or promote
// anything: PRODUCTION_CANDIDATE is a request for review, not a switch.

export interface ReadinessRow {
  strategy: string;
  vehicle: string;
  status: string;
  sample_size: number;
  holdout_size: number;
  net_expectancy_r: number | null;
  dev_net_expectancy_r: number | null;
  profit_factor: number | null;
  max_drawdown_r: number | null;
  t1_pct: number | null;
  capture_pct: number | null;
  capture_target_pct: number;
  last_validation: string | null;
  blockers: string[];
  paper_only: boolean;
  no_real_order: boolean;
  promotion_is_manual: boolean;
  resolved?: number;
  valid_plans?: number;
  paper_entries?: number;
  upstream_status?: string | null;
  window?: string;
  gate?: string;
  note?: string;
}

export interface ReliabilityBin {
  bin: string;
  predicted: number | null;
  observed: number | null;
  n: number;
  gap: number | null;
}

export interface Calibration {
  verdict: string;
  may_publish_probability: boolean;
  display: string;
  blockers: string[];
  rows: number;
  dev_rows: number;
  holdout_rows: number;
  scored_holdout_rows: number;
  base_rate: number | null;
  brier: number | null;
  brier_base_rate: number | null;
  brier_skill: number | null;
  ece: number | null;
  monotone: boolean | null;
  reliability_curve: ReliabilityBin[];
  gates: Record<string, number>;
  note: string;
}

export interface ReadinessBoard {
  rows: ReadinessRow[];
  statuses: string[];
  best_status: string;
  production_candidates: string[];
  any_production_candidate: boolean;
  calibration: Calibration;
  capture: {
    option_exact_near_pct: number | null;
    option_target_pct: number;
    cas_match_pct: number | null;
    futures_freshness_pct: number | null;
  };
  paper_only: boolean;
  no_real_order: boolean;
  promotion_is_manual: boolean;
  banner: string;
  note: string;
}

export interface FuturesPaperRow {
  instrument: string;
  session: string;
  direction: string;
  contract: string | null;
  expiry: string | null;
  entry_price: number;
  exit_price: number;
  entry_basis: string;
  exit_basis: string;
  outcome: string;
  risk_points: number;
  gross_points: number;
  net_points: number | null;
  net_r: number | null;
  net_rupees: number | null;
  mfe_points: number;
  mae_points: number;
  mfe_capture_pct: number | null;
  giveback_points: number;
  reached_t1: boolean;
  minutes_to_t1: number | null;
  hold_minutes: number;
  hold_bucket: string | null;
  cost_points: number | null;
  cost_rupees: number | null;
  brokerage_rupees: number | null;
  tax_rupees: number | null;
  txn_rupees: number | null;
  spread_cost_points: number | null;
  slippage_points: number;
  cost_status: string;
  entry_time_ist: string;
  exit_time_ist: string;
}

export interface FuturesPaperBoard {
  health: {
    enabled: boolean;
    plans_seen: number;
    entries: number;
    exits: number;
    resolved: number;
    open: number;
    skipped: Record<string, number>;
    last_error: string | null;
    max_hold_minutes: number;
    max_feed_age_sec: number;
    paper_only: boolean;
    no_real_order: boolean;
  };
  freshness_pct: number | null;
  open: Array<Record<string, unknown>>;
  resolved: FuturesPaperRow[];
  evaluation: {
    development: Record<string, number | null>;
    validation: Record<string, number | null>;
    holdout: Record<string, number | null>;
    rows_considered: number;
    rows_excluded_uncosted: number;
    rows_with_measured_spread: number;
    basis: string;
    note: string;
  };
  paper_only: boolean;
  no_real_order: boolean;
  banner: string;
}

export async function getReadiness(): Promise<ReadinessBoard> {
  const res = await fetch(`${API_BASE}/api/phase19/readiness`);
  return res.json();
}

export async function getCalibration(): Promise<Calibration> {
  const res = await fetch(`${API_BASE}/api/phase19/calibration`);
  return res.json();
}

export async function getFuturesPaper(): Promise<FuturesPaperBoard> {
  const res = await fetch(`${API_BASE}/api/phase19/futures-paper`);
  return res.json();
}

/* ---------------------------------------------------------------- Phase 22 */
/* CORE A+ PAPER — the selective pullback + continuation + vehicle-selection
   candidate. Every type here is read-only: there is no Phase 22 mutation in
   this client because there is no Phase 22 order path in the backend. */

export type P22Vehicle = {
  vehicle: string;
  symbol: string | null;
  status: "MEASURED" | "UNMEASURED";
  bid: number | null;
  ask: number | null;
  spread?: number | null;
  spread_pct?: number | null;
  entry?: number | null;
  premium?: number | null;
  stop?: number | null;
  target1?: number | null;
  target2?: number | null;
  target3?: number | null;
  levels_basis?: string | null;
  cost_points?: number | null;
  cost_rupees?: number | null;
  brokerage?: number | null;
  statutory?: number | null;
  slippage_points?: number | null;
  room_points?: number | null;
  room_after_cost?: number | null;
  cost_over_room?: number | null;
  risk_points?: number | null;
  cost_over_risk?: number | null;
  net_rr_after_cost?: number | null;
  reasons: string[];
  pricing: string;
};

export type P22Row = {
  instrument: string;
  direction: string | null;
  setup: string;
  setup_refusals: string[];
  definition: string;
  vehicle: string;
  vehicles: P22Vehicle[];
  vehicle_reasons: string[];
  symbol?: string | null;
  entry: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  levels_basis?: string | null;
  net_rr_after_cost?: number | null;
  current: number | null;
  spread: number | null;
  spread_pct: number | null;
  room_points: number | null;
  room_after_cost: number | null;
  expected_move_points: number | null;
  t1_rank: number | null;
  t1_rank_basis: string;
  expected_hold_minutes: number | null;
  noise_points: number | null;
  status: string;
  paper_pnl: number | null;
  mfe: number | null;
  mae: number | null;
  held_minutes?: number | null;
  paper_only: boolean;
  no_real_order: boolean;
  updated_ts: number;
};

export type P22Board = {
  definition: string;
  definition_fingerprint: string;
  rows: P22Row[];
  no_trade: boolean;
  candidates: number;
  selected: number;
  open: number;
  closed: number;
  net_paper_rupees: number;
  statuses: string[];
  paper_only: boolean;
  no_real_order: boolean;
  promotion: string;
};

export type P22Health = {
  definition: string;
  definition_fingerprint: string;
  observed: number;
  failures: number;
  last_error: string | null;
  open: number;
  paper_only: boolean;
  no_real_order: boolean;
};

export type P22Check = {
  condition: string;
  state: "PASS" | "FAIL" | "UNKNOWN";
  detail: string;
  [key: string]: unknown;
};

export type P22Verdict = {
  verdict: "SURVIVES" | "FAILS" | "REQUIRES_MORE_DATA";
  headline: string;
  checks: P22Check[];
  execution: string;
  promotion: string;
};

export type P22Study = {
  outdir: string;
  available: string[];
  missing: string[];
  verdict?: P22Verdict;
  definition?: { definition?: Record<string, unknown>; fingerprint?: string };
  pullback_vs_non_pullback?: Record<string, unknown>;
  vehicles?: Record<string, unknown>;
  stop_bands?: Record<string, unknown>;
};

export async function getP22Board(): Promise<P22Board> {
  const res = await fetch(`${API_BASE}/api/phase22/board`);
  return res.json();
}

export async function getP22Health(): Promise<P22Health> {
  const res = await fetch(`${API_BASE}/api/phase22/health`);
  return res.json();
}

export async function getP22Study(): Promise<P22Study> {
  const res = await fetch(`${API_BASE}/api/phase22/study`);
  return res.json();
}

/* ---------- Phase 23 — break-even hurdle PAPER SHADOW (read-only) ---------- */

export type P23Metrics = {
  arm?: string;
  trades: number;
  signals?: number;
  accepted?: number;
  rejected?: number;
  t1_before_sl_pct: number | null;
  t1: number;
  sl: number;
  win_pct: number | null;
  profit_factor: number | null;
  net_pnl: number;
  avg_net_r: number | null;
  median_net_r: number | null;
  max_drawdown: number;
  max_losing_streak: number;
  avg_hurdle_pct: number | null;
  median_measured_spread_pct: number | null;
  median_hold_minutes: number | null;
  avg_winner: number | null;
  avg_loser: number | null;
  attribution?: P23Attribution;
};

export type P23Attribution = {
  arm: string;
  threshold_pct: number | null;
  refused_trades: number;
  abstained_unmeasured: number;
  money_saved_by_refusing_high_hurdle_trades: number;
  money_missed_by_refusing_them: number;
  net_effect: number;
  largest_single_refused_abs_pnl: number;
  net_effect_excluding_largest: number;
};

export type P23Coverage = {
  opportunities: number;
  rows_on_file: number;
  simulated_feed_rows_excluded: number;
  measured_book: number;
  unmeasured_book: number;
  measured_pct: number | null;
  engine_would_buy: number;
  engine_refused: number;
  resolved_trades: number;
  open_or_unresolved: number;
};

export type P23SweepRow = {
  threshold_pct: number;
  accepted: number;
  rejected: number;
  abstained_unmeasured: number;
  net_pnl: number;
  vs_existing_net_pnl: number;
  profit_factor: number | null;
  win_pct: number | null;
  t1_before_sl_pct: number | null;
  avg_net_r: number | null;
  median_net_r: number | null;
  max_drawdown: number;
  money_saved: number;
  money_missed: number;
  net_effect: number;
  net_effect_excluding_largest: number;
};

export type P23Verdict = {
  status: string;
  discovered_threshold_pct: number | null;
  promotion_requires_human_review: boolean;
  measured_resolved_trades: number;
  sessions: number;
  candidates: {
    threshold_pct: number;
    net_effect: number;
    vs_existing_net_pnl: number;
    profit_factor: number | null;
    blocking_reasons: string[];
    passes: boolean;
  }[];
  note: string;
};

export type P23Summary = {
  gates: { strict_pct: number; loose_pct: number };
  report: {
    vehicle: string;
    futures_excluded: boolean;
    futures_note: string;
    spread_source: string;
    coverage: P23Coverage;
    cumulative: Record<string, P23Metrics>;
    breakdowns: Record<string, Record<string, Record<string, P23Metrics>>>;
    sweep: P23SweepRow[];
    daily: { date: string; coverage: P23Coverage; arms: Record<string, P23Metrics> }[];
  };
  verdict: P23Verdict;
  health: Record<string, number | string | null>;
};

export type P23Row = {
  ts: number | null;
  date: string | null;
  time_bucket: string;
  instrument: string | null;
  feed: string | null;
  real_feed: boolean | null;
  option: string | null;
  option_type: string | null;
  strike: number | null;
  premium: number | null;
  bid: number | null;
  ask: number | null;
  measured_spread_pct: number | null;
  round_trip_cost_rupees: number | null;
  hurdle_pct: number | null;
  hurdle_status: string | null;
  hurdle_reason: string | null;
  signal: string | null;
  confidence: number | null;
  engine_would_buy: boolean | null;
  gate_3_allows: string | null;
  gate_5_allows: string | null;
  outcome: string | null;
  exit_reason: string | null;
  net_pnl: number | null;
  net_r: number | null;
  hold_minutes: number | null;
  resolved: boolean | null;
};

export async function getP23Summary(): Promise<P23Summary> {
  const res = await fetch(`${API_BASE}/api/phase23/summary`);
  return res.json();
}

export async function getP23Rows(limit = 200): Promise<{ rows: P23Row[]; count: number }> {
  const res = await fetch(`${API_BASE}/api/phase23/rows?limit=${limit}`);
  return res.json();
}

/* ── Phase 24 — five-year discovery study (read-only research) ─────────────
 * Three GETs over artefacts the CLI wrote. Nothing here can start a study,
 * promote a strategy or reach an order path.
 */
export type P24CoverageRow = {
  instrument: string;
  bars: number;
  sessions?: number;
  reason?: string;
};

export type P24Coverage = {
  series: {
    usable: P24CoverageRow[];
    excluded: P24CoverageRow[];
    min_bars_required: number;
    equity_note: string;
  };
  option_books: {
    snapshots: number;
    real_broker_snapshots: number;
    snapshots_with_two_sided_book: number;
    two_sided_by_instrument?: Record<string, number>;
    two_sided_first_ts?: number;
    two_sided_last_ts?: number;
    /** A captured window can support a CE/PE study even when five years cannot. */
    ce_pe_study_possible?: boolean;
    /** True when the store could not be counted — unmeasured, not empty. */
    unmeasured?: boolean;
    note: string;
  };
};

export type P24PoolRow = {
  instrument: string;
  stop_band_atr: number;
  candidates: number;
  resolved: number;
  unresolved: number;
  sessions: number;
  base_t1_before_sl_pct: number;
  base_avg_net_r: number;
  base_timeout_pct: number;
  median_cost_points_per_round_trip: number;
  cost_as_fraction_of_risk: number;
};

export type P24GeometryRow = {
  instrument: string;
  stop_band_atr: number;
  t1_r: number;
  trades: number;
  t1_before_sl_pct: number;
  breakeven_t1_pct_before_costs: number;
  avg_net_r: number;
  cost_as_fraction_of_risk: number;
};

/** The conclusion is keyed exactly as the study is required to report it. */
export type P24Conclusion = {
  BEST_VALIDATED_STRATEGY: string | null;
  EXPECTED_T1_BEFORE_SL: number | null;
  NET_EXPECTANCY_R: number | null;
  PROFIT_FACTOR: number | null;
  FINAL_HOLDOUT: string | number | null;
  TRADES_PER_WEEK: number | null;
  STATUS: string;
  CLOSEST_CANDIDATE?: {
    strategy_id?: string;
    instrument?: string;
    side?: string;
    stop_band_atr?: number;
    conditions?: string[];
    why_it_failed?: string[];
  } | string | null;
};

export type P24Summary = {
  available: boolean;
  note?: string;
  version?: string;
  generated_at?: number;
  windows?: Record<string, unknown>;
  pools?: P24PoolRow[];
  hypotheses_evaluated?: number;
  answers?: Record<string, unknown>;
  conclusion?: P24Conclusion;
  coverage?: P24Coverage["series"];
  option_books?: P24Coverage["option_books"];
  /** OK, or NO_USABLE_HISTORY when the study had nothing to search. */
  data_status?: string;
  vehicles?: Record<string, { status: string; note?: string }>;
  geometry_sweep?: P24GeometryRow[];
  paper_only: boolean;
};

export type P24Strategy = {
  strategy_id: string;
  instrument: string;
  vehicle: string;
  side: string;
  stop_band_atr: number;
  conditions: string[];
  complexity: number;
  score: number | null;
  status: string;
  failed_clauses: string[];
  dev_trades: number | null;
  dev_t1_pct: number | null;
  dev_avg_net_r: number | null;
  val_trades: number | null;
  val_avg_net_r: number | null;
  holdout_trades: number | null;
  holdout_t1_pct: number | null;
  holdout_avg_net_r: number | null;
  holdout_profit_factor: number | null;
  holdout_max_drawdown_r: number | null;
  holdout_longest_losing_streak: number | null;
  outlier_top1_pct: number | null;
  folds_positive: number | null;
  folds_scored: number | null;
  walk_forward_stable: boolean | null;
  trades_per_week: number | null;
  no_trade_pct: number | null;
  fingerprint_published: boolean;
};

export async function getP24Coverage(): Promise<P24Coverage> {
  const res = await fetch(`${API_BASE}/api/phase24/coverage`);
  return res.json();
}

export async function getP24Summary(): Promise<P24Summary> {
  const res = await fetch(`${API_BASE}/api/phase24/summary`);
  return res.json();
}

export async function getP24Strategies(limit = 25): Promise<{
  rows: P24Strategy[];
  count: number;
  research_only: boolean;
  vehicles_measured: string[];
  vehicles_requiring_more_data: string[];
}> {
  const res = await fetch(`${API_BASE}/api/phase24/strategies?limit=${limit}`);
  return res.json();
}

/* ------------------------------------------------------------------ *
 * PHASE 25 — CAPTURED-WINDOW CE/PE STUDY (RESEARCH · PAPER ONLY)
 *
 * Read-only. The strongest verdict this study can produce is RESEARCH_LEAD:
 * the capture is weeks of option books, so nothing here is a validated edge
 * and nothing can be promoted from this panel.
 * ------------------------------------------------------------------ */
export type P25InstrumentCoverage = {
  instrument: string;
  snapshots?: number;
  real_broker_snapshots?: number;
  sessions?: number;
  contract_paths?: number;
  simulator_snapshots_excluded?: number;
  one_sided_legs_excluded?: number;
  eligible?: boolean;
  status: string;
  reasons?: string[];
};

export type P25Coverage = {
  /** True when the store could not be read: unmeasured, never empty. */
  unmeasured: boolean;
  cause?: string;
  note?: string;
  eligible: P25InstrumentCoverage[];
  reported_only: P25InstrumentCoverage[];
  window_claim?: string;
};

export type P25Geometry = {
  stop_pct_of_premium_bands: number[];
  t1_r: number;
  t2_r: number;
  t3_r: number;
  horizon_seconds: number;
  max_forward_quotes: number;
  entry: string;
  exit: string;
  slippage_pct_per_side: number;
  costs_charged: string;
  split: string;
};

export type P25Pool = {
  instrument: string;
  candidates: number;
  resolved: number;
  unresolved: number;
  sessions: number;
  ce: number;
  pe: number;
  base_t1_before_sl_pct: number | null;
  base_avg_net_r: number | null;
  base_avg_net_rupees: number | null;
  base_timeout_pct: number | null;
  median_measured_spread_pct: number | null;
  median_hurdle_pct: number | null;
  median_premium_ask: number | null;
};

export type P25GeometryRow = {
  instrument: string;
  stop_band_pct_of_premium: number;
  t1_r: number;
  trades: number;
  t1_before_sl_pct: number | null;
  breakeven_t1_pct_before_costs: number;
  avg_net_r: number | null;
  cost_as_fraction_of_risk: number | null;
  median_measured_spread_pct: number | null;
  spread_as_fraction_of_risk: number | null;
};

export type P25Cohort = {
  trades: number;
  status?: string;
  t1_before_sl_pct?: number | null;
  avg_net_r?: number | null;
  avg_net_rupees?: number | null;
  /** A string when the cohort had no losing trade, so it is not a ratio. */
  profit_factor?: number | string | null;
};

export type P25HurdleBand = P25Cohort & { band: string };

export type P25Instrument = {
  instrument: string;
  status: string;
  pool: P25Pool | null;
  sides?: Record<string, P25Cohort> | null;
  time_of_day?: Record<string, P25Cohort> | null;
  hurdle_bands?: P25HurdleBand[] | null;
  geometry_sweep?: P25GeometryRow[] | null;
  reasons?: string[] | null;
};

export type P25Conclusion = {
  verdict: string;
  headline: string;
  window: string;
  leads: number;
  closest_candidate?: {
    strategy_id?: string;
    instrument?: string;
    option_type?: string;
    conditions?: string[];
    status?: string;
    failed_clauses?: string[];
  } | null;
};

export type P25Summary = {
  available: boolean;
  note?: string;
  version?: string;
  generated_at?: number;
  quick_mode?: boolean;
  geometry?: P25Geometry;
  window_claim?: string;
  hypotheses_evaluated?: number;
  conclusion?: P25Conclusion;
  answers?: { question: string; answer: string }[];
  instruments?: P25Instrument[];
  paper_only: boolean;
};

export type P25CohortRow = {
  strategy_id: string;
  instrument: string;
  option_type: string;
  underlying_view: string | null;
  stop_band_pct_of_premium: number | null;
  conditions: string[];
  complexity: number | null;
  score: number | null;
  status: string;
  failed_clauses: string[];
  dev_trades: number | null;
  dev_t1_pct: number | null;
  dev_avg_net_r: number | null;
  val_trades: number | null;
  val_avg_net_r: number | null;
  holdout_trades: number | null;
  holdout_t1_pct: number | null;
  holdout_avg_net_r: number | null;
  holdout_profit_factor: number | string | null;
  holdout_max_drawdown_r: number | null;
  outlier_top1_pct: number | null;
  folds_positive: number | null;
  folds_measurable: number | null;
  trades_per_session: number | null;
  no_trade_pct: number | null;
  is_research_lead: boolean;
  paper_only: boolean;
};

export async function getP25Coverage(): Promise<P25Coverage> {
  const res = await fetch(`${API_BASE}/api/phase25/coverage`);
  return res.json();
}

export async function getP25Summary(): Promise<P25Summary> {
  const res = await fetch(`${API_BASE}/api/phase25/summary`);
  return res.json();
}

export async function getP25Cohorts(limit = 25): Promise<{
  rows: P25CohortRow[];
  count: number;
  research_only: boolean;
  paper_only: boolean;
  window_claim: string;
}> {
  const res = await fetch(`${API_BASE}/api/phase25/cohorts?limit=${limit}`);
  return res.json();
}

/* ------------------------------------------------------------------ *
 * PHASE 26 — EXIT MANAGEMENT AND TRADABILITY (RESEARCH / ADVISORY)
 *
 * Read-only. Every one of these endpoints reads an artefact a CLI run
 * wrote: there is no run trigger over HTTP, no threshold to set and no
 * refusal wired to the advisory table.
 * ------------------------------------------------------------------ */
export type P26Geometry = {
  key: string;
  label: string;
  why: string;
  stop_pct_of_premium: number;
  target_r: number | null;
  partial_at_r: number | null;
  partial_fraction: number | null;
  stop_to_breakeven_after_partial: boolean | null;
  trail_giveback_r: number | null;
  trail_arms_at_r: number | null;
  dead_leg_cut_seconds: number | null;
  dead_leg_min_mfe_r: number | null;
  horizon_seconds: number;
  max_forward_quotes: number;
  breakeven_hit_rate_pct_before_costs: number | null;
  exit_orders: number;
  tags: string[];
};

export type P26VariantTotal = {
  variant: string;
  geometry: P26Geometry;
  instruments_scored: number;
  trades: number;
  avg_net_r: number;
  total_net_r: number;
  total_net_rupees: number;
  profit_exit_before_sl_pct: number;
  stop_out_pct: number;
  /** Trades that reached the horizon, in loss — Phase 25's actual finding. */
  flat_timeout_pct: number;
  /** Trades that reached the horizon at all, in profit or not. */
  horizon_exit_pct: number;
  horizon_exit_in_profit_pct: number;
  positive_net_pct: number;
  avg_hold_sec: number;
  instruments_positive: string[];
  instruments_better_than_baseline: string[];
};

export type P26Window = {
  trades: number;
  avg_net_r: number | null;
  profit_factor: number | string | null;
  t1_before_sl_pct?: number | null;
};

export type P26InstrumentVariant = {
  variant: string;
  trades: number;
  avg_net_r: number;
  profit_factor: number | string | null;
  profit_exit_before_sl_pct: number;
  outcome_mix: Record<string, { trades: number; pct: number }>;
  median_hold_sec: number;
  holdout?: P26Window;
};

export type P26Instrument = {
  instrument: string;
  status: string;
  candidates: number | null;
  sessions: number | null;
  best_variant_on_development: string | null;
  uplift: number | null;
  variants: P26InstrumentVariant[];
  reasons?: string[];
};

export type P26Band = {
  band: string;
  trades: number;
  avg_net_r: number | null;
  total_net_rupees: number | null;
  profit_factor: number | string | null;
};

export type P26TimeBand = {
  bucket: string;
  trades: number;
  t1_before_sl_pct: number | null;
  avg_net_r: number | null;
  total_net_rupees: number | null;
};

export type P26AdvisoryRow = {
  instrument: string;
  trades: number;
  median_measured_spread_pct: number | null;
  spread_as_fraction_of_risk: number | null;
  median_break_even_hurdle_pct: number | null;
  all_costs_as_fraction_of_risk: number | null;
  median_premium_ask: number | null;
  measured_t1_before_sl_pct: number | null;
  measured_avg_net_r: number | null;
  measured_total_net_rupees: number | null;
  verdict: string;
  advisory_only: boolean;
  hurdle_bands: P26Band[];
  time_of_day: P26TimeBand[];
  counterfactual_if_refused: Record<string, unknown>;
};

export type P26Advisory = {
  available: boolean;
  note?: string;
  advisory_claim?: string;
  window_claim?: string;
  /** Always false: this table is measured, never wired to a live refusal. */
  wired_to_live_refusal?: boolean;
  rows?: P26AdvisoryRow[];
  counts?: Record<string, number>;
  instruments_by_verdict?: Record<string, string[]>;
  thresholds?: Record<string, number | boolean>;
  refusing_hurdle_gt_5pct_on_this_window?: Record<string, number>;
};

export type P26Conclusion = {
  verdict: string;
  headline: string;
  window: string;
  leads: number;
  best_variant: string | null;
  baseline_variant: string;
  exit_management_improved_the_pool: boolean;
  pool_positive_under_any_variant: boolean;
  closest_cohort: {
    strategy_id: string;
    instrument: string;
    option_type: string;
    variant: string;
    conditions: string[];
    status: string;
    failed_clauses: string[];
  } | null;
};

export type P26Summary = {
  available: boolean;
  note?: string;
  version?: string;
  generated_at?: number;
  quick_mode?: boolean;
  execution?: Record<string, string | number>;
  window_claim?: string;
  advisory_claim?: string;
  event_hypotheses_evaluated?: number;
  variant_totals?: P26VariantTotal[];
  variants_tested?: P26Geometry[];
  conclusion?: P26Conclusion;
  answers?: { question: string; answer: string }[];
  instruments?: P26Instrument[];
  paper_only: boolean;
};

export type P26CohortRow = {
  strategy_id: string;
  instrument: string;
  option_type: string;
  exit_variant: string;
  conditions: string[];
  score: number | null;
  status: string;
  failed_clauses: string[];
  fdr_survivor: boolean | null;
  fdr_denominator: number | null;
  dev_trades: number | null;
  dev_avg_net_r: number | null;
  val_trades: number | null;
  val_avg_net_r: number | null;
  holdout_trades: number | null;
  holdout_avg_net_r: number | null;
  holdout_profit_factor: number | string | null;
  holdout_max_drawdown_r: number | null;
  outlier_top1_pct: number | null;
  folds_positive: number | null;
  folds_measurable: number | null;
  trades_per_session: number | null;
  no_trade_pct: number | null;
  is_research_lead: boolean;
  paper_only: boolean;
};

export async function getP26Summary(): Promise<P26Summary> {
  const res = await fetch(`${API_BASE}/api/phase26/summary`);
  return res.json();
}

export async function getP26Advisory(): Promise<P26Advisory> {
  const res = await fetch(`${API_BASE}/api/phase26/advisory`);
  return res.json();
}

export async function getP26Cohorts(limit = 25): Promise<{
  rows: P26CohortRow[];
  count: number;
  research_only: boolean;
  paper_only: boolean;
  window_claim: string;
}> {
  const res = await fetch(`${API_BASE}/api/phase26/cohorts?limit=${limit}`);
  return res.json();
}

/* ------------------------------------------------------------------ *
 * Phase 27 — higher-timeframe (5m / 15m) futures research, read only.
 * There is no run endpoint on purpose: a dashboard refresh cannot start
 * a study, and nothing here can place, size or refuse an order.
 * ------------------------------------------------------------------ */
export type P27Window = {
  trades: number | null;
  t1_before_sl_pct: number | null;
  avg_net_r: number | null;
  median_net_r: number | null;
  profit_factor: number | null;
  max_drawdown_r: number | null;
};

export type P27Strategy = {
  strategy_id: string;
  timeframe_minutes: number;
  instrument: string;
  side: string;
  stop_band_atr: number | null;
  conditions: string[];
  complexity: number | null;
  score: number | null;
  status: string;
  gate_verdict: string | null;
  fdr_survivor: boolean | null;
  fdr_denominator: number | null;
  failed_clauses: string[] | null;
  parameter_perturbation_tested: boolean | null;
  development: P27Window | null;
  validation: P27Window | null;
  holdout: P27Window | null;
  walk_forward: {
    folds_scored: number | null;
    folds_positive: number | null;
    stable: boolean | null;
  } | null;
  selectivity: Record<string, number | null> | null;
};

export type P27TimeframeBlock = {
  timeframe_minutes: number;
  hypotheses_evaluated: number;
  candidates_surviving_development: number;
  diagnostics: Record<string, number>;
};

export type P27Summary = {
  available: boolean;
  note?: string;
  generated_at?: string;
  runtime_seconds?: number;
  quick_mode?: boolean;
  instruments?: string[];
  timeframes?: number[];
  resample_claim?: string;
  spread_claim?: string;
  stop_rule?: string;
  research_ceiling?: string;
  timeframe_results?: P27TimeframeBlock[];
  answers?: Record<string, unknown>;
  conclusion?: {
    verdict: string;
    headline: string;
    statement?: string;
    stop_rule_triggered: boolean;
    closest_candidate?: unknown;
    what_this_does_not_say?: string[];
  };
};

export type P27ComparisonRow = {
  timeframe_minutes: number;
  instrument: string;
  stop_band_atr: number | null;
  trades: number | null;
  t1_before_sl_pct: number | null;
  breakeven_t1_pct_before_costs: number | null;
  avg_net_r: number | null;
  cost_as_fraction_of_risk: number | null;
  median_risk_points?: number | null;
  source: string;
};

export type P27Coverage = {
  timeframes: number[];
  usable: { instrument: string; bars: number; sessions: number | null }[];
  excluded: {
    instrument: string;
    bars: number;
    status: string;
    reason: string;
  }[];
  resampled: {
    instrument: string;
    timeframe_minutes: number;
    source_minute_bars: number;
    output_bars: number;
    sessions: number;
    full_bars: number;
    short_bars: number;
    short_bar_pct: number;
    median_minutes_per_bar: number;
    spans_a_session: boolean;
  }[];
  one_minute_reference?: string;
};

export async function getP27Summary(): Promise<P27Summary> {
  const res = await fetch(`${API_BASE}/api/phase27/summary`);
  return res.json();
}

export async function getP27Coverage(): Promise<P27Coverage> {
  const res = await fetch(`${API_BASE}/api/phase27/coverage`);
  return res.json();
}

export async function getP27Comparison(): Promise<{
  available?: boolean;
  rows?: P27ComparisonRow[];
  note?: string;
}> {
  const res = await fetch(`${API_BASE}/api/phase27/comparison`);
  return res.json();
}

export async function getP27Strategies(limit = 25): Promise<{
  rows: P27Strategy[];
  count: number;
  research_only: boolean;
  paper_only: boolean;
  stop_rule: string;
}> {
  const res = await fetch(`${API_BASE}/api/phase27/strategies?limit=${limit}`);
  return res.json();
}

/* ------------------------------------------------------------------ *
 * Phase 28 — multi-day futures and cash-equity research, read only.
 * Again no run endpoint: a dashboard refresh cannot start a study, and
 * nothing here can place, size or refuse an order. Two things must stay
 * visible in the types themselves: a cash-equity name with no five-year
 * history is INSUFFICIENT_HISTORY (unanswered) rather than a failure, and
 * a long-only row is only interesting next to buy-and-hold over the same
 * window, so the benchmark travels with the row.
 * ------------------------------------------------------------------ */
export type P28Window = {
  trades: number | null;
  t1_before_sl_pct: number | null;
  avg_net_r: number | null;
  median_net_r: number | null;
  profit_factor: number | null;
  max_drawdown_r: number | null;
};

export type P28Strategy = {
  strategy_id: string;
  vehicle: string;
  instrument: string;
  side: string;
  stop_band_atr: number | null;
  horizon_trading_days: number | null;
  conditions: string[];
  complexity: number | null;
  score: number | null;
  status: string;
  gate_verdict: string | null;
  fdr_survivor: boolean | null;
  fdr_denominator: number | null;
  failed_clauses: string[] | null;
  parameter_perturbation_tested: boolean | null;
  development: P28Window | null;
  validation: P28Window | null;
  holdout: P28Window | null;
  walk_forward: {
    folds_scored: number | null;
    folds_positive: number | null;
    stable: boolean | null;
  } | null;
  benchmark_holdout: {
    benchmark: string | null;
    net_points_per_exposed_day: number | null;
    benchmark_net_points_per_day: number | null;
    beats_benchmark: boolean | null;
  } | null;
  exit_breakdown: Record<string, number | null> | null;
  selectivity: Record<string, number | null> | null;
};

export type P28VehicleBlock = {
  vehicle: string;
  candidates_surviving_development: number | null;
  cohorts_examined_including_near_misses: number | null;
  diagnostics: Record<string, number>;
};

export type P28EquityHistory = {
  status: string;
  tested: string[];
  missing: string[];
  note?: string;
  how_to_answer_it?: string;
  corporate_actions?: string;
};

export type P28Summary = {
  available: boolean;
  note?: string;
  version?: string;
  generated_at?: number;
  runtime_seconds?: number;
  quick_mode?: boolean;
  instruments?: string[];
  data_status?: string;
  answers?: Record<string, unknown>;
  equity_history?: P28EquityHistory;
  option_logic?: string;
  gap_claim?: string;
  rollover_claim?: string;
  delivery_claim?: string;
  buy_and_hold_claim?: string;
  stop_rule?: string;
  research_ceiling?: string;
  conclusion?: {
    verdict: string;
    headline: string;
    statement?: string;
    stop_rule_triggered?: boolean;
    equity_caveat?: P28EquityHistory;
    what_this_does_not_say?: string[];
  };
};

export type P28Funnel = {
  available: boolean;
  note?: string;
  vehicles?: P28VehicleBlock[];
};

export type P28HoldingRow = {
  holding_period: string;
  instrument: string;
  stop_band_atr: number | null;
  median_risk_points: number | null;
  median_cost_points: number | null;
  cost_as_fraction_of_risk: number | null;
  avg_net_r: number | null;
  source: string;
};

export type P28Economics = {
  available: boolean;
  note?: string;
  pools?: {
    instrument: string;
    vehicle: string;
    source: string;
    daily_bars: number | null;
    span_years: number | null;
    candidates: number | null;
    stop_band_atr: number | null;
    horizon_trading_days: number | null;
    base_t1_before_sl_pct: number | null;
    base_avg_net_r: number | null;
    gap_resolved_pct: number | null;
    cost_as_fraction_of_risk: number | null;
    rolls_charged: number | null;
  }[];
  holding_period_comparison?: {
    rows?: P28HoldingRow[];
    median_cost_over_risk_intraday?: number | null;
    median_cost_over_risk_daily?: number | null;
  };
};

export type P28CoverageRow = {
  instrument: string;
  vehicle: string;
  source: string;
  daily_bars: number;
  span_years: number | null;
  thin_sessions: number | null;
  source_bars: number | null;
  status?: string;
  reason?: string;
};

export type P28Coverage = {
  usable: P28CoverageRow[];
  excluded: P28CoverageRow[];
  min_daily_bars?: number;
  min_years?: number;
  note?: string;
  equity_history?: P28EquityHistory;
  daily_bar_rules?: Record<string, unknown>;
};

export async function getP28Summary(): Promise<P28Summary> {
  const res = await fetch(`${API_BASE}/api/phase28/summary`);
  return res.json();
}

export async function getP28Coverage(): Promise<P28Coverage> {
  const res = await fetch(`${API_BASE}/api/phase28/coverage`);
  return res.json();
}

export async function getP28Funnel(): Promise<P28Funnel> {
  const res = await fetch(`${API_BASE}/api/phase28/funnel`);
  return res.json();
}

export async function getP28Economics(): Promise<P28Economics> {
  const res = await fetch(`${API_BASE}/api/phase28/economics`);
  return res.json();
}

export async function getP28Strategies(limit = 25): Promise<{
  rows: P28Strategy[];
  count: number;
  research_only: boolean;
  paper_only: boolean;
  stop_rule: string;
}> {
  const res = await fetch(`${API_BASE}/api/phase28/strategies?limit=${limit}`);
  return res.json();
}
