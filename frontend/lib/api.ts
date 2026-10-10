import { BacktestResult, EconomicEvent, EventGuard, Fundamentals, PerformanceReport } from "./types";

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
  win_probability: number | null;
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
  net_rupees: number;
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
  lots: number | null;
  lot_size: number | null;
  win: boolean;
  close_reason: string;
  duration_sec: number;
  agreement: string;
}

export interface FlowSummary {
  total: number;
  wins: number;
  losses: number;
  win_rate: number;
  profit_factor: number | null;
  avg_hold_min: number | null;
  false_signal_rate: number;
  net_rupees: number;
  instruments: FlowInstrumentStat[];
  recent: FlowRecent[];
  as_of: number;
}

export async function getFlowReport(limit = 200) {
  const res = await fetch(`${API_BASE}/api/flow-report?limit=${limit}`);
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
}

/** Per-trigger performance, so the unproven entries can be judged on real fills. */
export interface TriggerStats {
  trades: number;
  wins: number;
  win_rate: number;
  net_pnl: number;
  profit_factor: number | null;
}

/** Which book to score: everything, the option engine, or the futures paper tool. */
export type JournalBook = "all" | "options" | "futures";

export async function getTradeJournal(limit = 500, book: JournalBook = "all") {
  const res = await fetch(`${API_BASE}/api/trade-journal?limit=${limit}&book=${book}`);
  return res.json() as Promise<{
    count: number;
    wins: number;
    losses: number;
    net_pnl: number;
    trades: JournalTrade[];
    by_trigger: Record<string, TriggerStats>;
  }>;
}

export function tradeJournalCsvUrl(limit = 5000, book: JournalBook = "all") {
  return `${API_BASE}/api/trade-journal.csv?limit=${limit}&book=${book}`;
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
