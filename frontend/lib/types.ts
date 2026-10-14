export type Signal = "BUY" | "WAIT" | "HOLD" | "EXIT" | "NO_TRADE" | "AVOID";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface ValidationCheck {
  name: string;
  passed: boolean;
  detail: string;
  available: boolean;
}

export interface TradeValidation {
  enabled: boolean;
  approved: boolean;
  checks: ValidationCheck[];
  rejections: string[];
  refined_stop: number | null;
  refined_stop_source: string | null;
  max_premium_loss_pct: number | null;
  reward_risk: number | null;
  note: string;
}

export interface ExecutionEntry {
  action: string;
  extended: boolean;
  atr_from_vwap: number | null;
  pullback_probability: number | null;
  current_entry: number | null;
  better_entry: number | null;
  wait_candles: number | null;
  reasons: string[];
}

export interface StrikeCandidate {
  symbol: string;
  strike: number;
  moneyness: string;
  score: number;
  delta: number;
  theta: number;
  iv: number;
  oi: number;
  volume: number;
  premium: number;
  reward_risk: number | null;
  survival: number | null;
  reason: string;
}

export interface StrikeSelection {
  current_symbol: string | null;
  recommended_symbol: string | null;
  changed: boolean;
  reason: string;
  candidates: StrikeCandidate[];
}

export interface SurvivalComponent {
  name: string;
  score: number;
  available: boolean;
  detail: string;
}

export interface TradeSurvival {
  overall: number;
  recommendation: string;
  components: SurvivalComponent[];
}

export interface ExecutionIntelligence {
  enabled: boolean;
  grade: string;
  entry_timing: string;
  strike_quality: number | null;
  trade_survival: number | null;
  pullback_probability: number | null;
  recommended_action: string;
  baseline_note: string;
  reasons: string[];
  entry: ExecutionEntry | null;
  strike: StrikeSelection | null;
  survival: TradeSurvival | null;
}

export interface EarlyCheck {
  name: string;
  passed: boolean;
  detail: string;
  available: boolean;
  weight: number;
  points: number;
}

export interface MomentumPlan {
  option_symbol: string | null;
  entry_low: number | null;
  entry_high: number | null;
  stop_loss: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  risk_per_lot: number | null;
  reward_risk: number | null;
  holding_time: string;
  exit_conditions: string[];
  risk_level: string;
  underlying_stop: number | null;
  note: string;
}

export interface EarlyMomentum {
  enabled: boolean;
  active: boolean;
  side: "CE" | "PE" | null;
  stage_num: number;
  stage: string;
  score: number;
  regime: string;
  weight_note: string;
  option_symbol: string | null;
  entry_hint: number | null;
  atr_from_vwap: number | null;
  checks: EarlyCheck[];
  reasons: string[];
  plan: MomentumPlan | null;
  note: string;
}

export interface EarlyEarly {
  enabled: boolean;
  active: boolean;
  side: "CE" | "PE" | null;
  stage_label: string;
  probability: number;
  evidence_count: number;
  evidence_required: number;
  checks: EarlyCheck[];
  risk_level: string;
  hold_time: string;
  recommended_action: string;
  action_reason: string;
  reward_risk: number | null;
  room_atr: number | null;
  spread_pct: number | null;
  liquidity_ok: boolean;
  rr_ok: boolean;
  room_ok: boolean;
  option_symbol: string | null;
  entry_hint: number | null;
  plan: MomentumPlan | null;
  agreement: string;
  confirm_delay_candles: number | null;
  reasons: string[];
  note: string;
}

export interface ScalpSignal {
  enabled: boolean;
  active: boolean;
  side: "CE" | "PE" | null;
  setup: string;
  probability: number;
  condition_count: number;
  condition_required: number;
  checks: EarlyCheck[];
  risk_level: string;
  hold_time: string;
  recommended_action: string;
  action_reason: string;
  target_points: number | null;
  reward_risk: number | null;
  breakeven_at: number | null;
  time_stop_candles: number | null;
  liquidity_ok: boolean;
  rr_ok: boolean;
  option_symbol: string | null;
  entry_hint: number | null;
  plan: MomentumPlan | null;
  agreement: string;
  confirm_delay_candles: number | null;
  in_trade: boolean;
  exit_now: boolean;
  exit_urgency: string;
  exit_reason: string;
  exit_signals: string[];
  live_premium: number | null;
  live_points: number | null;
  reasons: string[];
  note: string;
}

export interface SpreadLeg {
  action: "SELL" | "BUY";
  option_type: "CE" | "PE";
  strike: number;
  premium: number;
  delta: number;
}

/** Premium-SELLING engine — defined-risk credit spreads, PAPER ONLY. Every
 *  premium here is MODELLED (Black-Scholes), not a live option quote. */
export interface CreditSpreadSignal {
  enabled: boolean;
  state: "PROPOSE" | "OPEN" | "CLOSE" | "WAIT" | "BLOCKED";
  structure: string | null;
  reason: string | null;
  legs: SpreadLeg[];
  net_credit: number | null;
  max_loss: number | null;
  reward_risk: number | null;
  win_probability: number | null;
  breakeven_low: number | null;
  breakeven_high: number | null;
  stop_at: number | null;
  implied_vol: number | null;
  minutes_to_close: number | null;
  paper_only: boolean;
}

/** Board state of the SEPARATE futures paper tool. Everything is in INDEX
 * POINTS, not premium: one point is `lot_size` rupees, there is no theta and no
 * IV, and a bearish view is a SHORT rather than a long put. PAPER ONLY — there
 * is no live order route into this tool. */
/**
 * Research-only futures plan returned by GET /api/futures-signal.
 * This is a validated research card, not an executable order.
 */
export interface FuturesSignalCard {
  enabled: boolean;
  status: string;
  signal: string;
  direction: string | null;
  instrument: string | null;
  exchange: string | null;
  contract: string | null;
  expiry: string | null;
  days_to_expiry: number | null;
  lot_size: number | null;
  quoted_contract: string | null;
  quoted_days_to_expiry: number | null;
  rolled: boolean;
  reason_for_roll: string | null;
  price_basis: string | null;
  price_basis_note: string;
  price: number | null;
  entry: number | null;
  entry_zone_low: number | null;
  entry_zone_high: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  risk_points: number | null;
  risk_rupees_per_lot: number | null;
  reward_risk_t1: number | null;
  reward_risk_t2: number | null;
  cost_points: number | null;
  cost_to_risk_pct: number | null;
  atr: number | null;
  atr_pct: number | null;
  adx: number | null;
  regime: string | null;
  trend: string | null;
  momentum: string | null;
  setup_type: string | null;
  signal_score: number | null;
  expected_move_points: number | null;
  spread_points: number | null;
  spread_pct: number | null;
  volume: number | null;
  open_interest: number | null;
  liquidity_note: string;
  data_freshness_sec: number | null;
  plan_age_seconds: number;
  plan_version: number;
  plan_state: string;
  invalid_reason: string | null;
  reason: string;
  checks: {
    name: string;
    passed: boolean;
    value: number | string | null;
    threshold: number | string | null;
    detail: string;
  }[];
  global_signal_id: string | null;
  episode_id: string | null;
  futures_signal_id: string | null;
  market: string;
  vehicle: string;
  signal_ts: number | null;
  signal_time_ist: string | null;
  event_type: string | null;
  raw_event_count: number | null;
  meaningful_update_count: number | null;
  last_published_at: number | null;
  feed_state: string | null;
  feed_source: string | null;
  research_only: boolean;
  executable: boolean;
}

export interface FuturesSignal {
  enabled: boolean;
  state: "OFF" | "WAIT" | "BLOCKED" | "ENTERED" | "HOLDING" | "CLOSED" | "ERROR";
  side: "LONG" | "SHORT" | null;
  entry: number | null;
  price: number | null;
  stop: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  locked_floor: number | null;
  lots: number | null;
  lot_size: number | null;
  risk_points: number | null;
  risk_rupees: number | null;
  open_points: number | null;
  open_rupees: number | null;
  net_points: number | null;
  net_rupees: number | null;
  levels_hit: string | null;
  reason: string | null;
  sizing_note: string | null;
  paper_only: boolean;
}

export interface FlowSignal {
  enabled: boolean;
  state: "BUY" | "HOLD" | "EXIT" | "SWITCH" | "WAIT";
  side: "CE" | "PE" | null;
  prev_side: "CE" | "PE" | null;
  switched: boolean;
  in_trade: boolean;
  just_entered: boolean;
  bars_in_trade: number;
  candle_color: string;
  candle_pattern: string;
  pattern_bias: string;
  projection: string;
  green_streak: number;
  strength: number;
  option_symbol: string | null;
  entry_hint: number | null;
  entry_premium: number | null;
  live_premium: number | null;
  peak_premium: number | null;
  points: number | null;
  giveback: number | null;
  exit_now: boolean;
  exit_reason: string;
  exit_signals: string[];
  headline: string;
  reasons: string[];
  note: string;
}

export interface SignalDelay {
  available: boolean;
  confirmation_entry_premium: number | null;
  early_entry_premium: number | null;
  premium_diff: number | null;
  premium_diff_pct: number | null;
  candles_between: number | null;
  move_start_price: number | null;
  buy_price: number | null;
  peak_price: number | null;
  missed_move_pct: number | null;
  remaining_move_pct: number | null;
  late: boolean;
  early_would_help: boolean;
  note: string;
}

export interface Decision {
  signal: Signal;
  confidence: number;
  signal_strength: number;
  trade_quality: string;
  market_signal: Signal | null;
  market_confidence: number | null;
  reversal: boolean;
  reversal_option: string | null;
  reversal_option_type: "CE" | "PE" | null;
  average_ok: boolean;
  average_note: string | null;
  suggested_lots: number | null;
  trade_validation: TradeValidation | null;
  execution_intelligence: ExecutionIntelligence | null;
  early_momentum: EarlyMomentum | null;
  signal_delay: SignalDelay | null;
  early_early: EarlyEarly | null;
  scalp: ScalpSignal | null;
  flow: FlowSignal | null;
  credit_spread: CreditSpreadSignal | null;
  futures: FuturesSignal | null;
  htf_trend: string | null;
  htf_strength: number | null;
  entry_trigger: string | null;
  atr_points: number | null;
  underlying_stop: number | null;
  recommended_option: string | null;
  strike: number | null;
  option_type: "CE" | "PE" | null;
  current_premium: number | null;
  spot_price: number | null;
  atm_strike: number | null;
  moneyness: string | null;
  entry_range: [number, number] | null;
  stop_loss: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  expected_holding_minutes: number | null;
  recovery_probability: number | null;
  reasons: string[];
  buy_score: number;
  sell_score: number;
  wait_score: number;
  exit_score: number;
  risk_level: string;
  risk_score: number;
  trade_score: number;
  win_probability: number | null;
  expected_move_points: number | null;
  opportunity_score: number;
  opportunity_label: string;
  risk_meter: string;
  trade_score_breakdown: Record<string, number>;
  premium_quality: number | null;
  premium_health: string | null;
  premium_momentum: number | null;
  premium_velocity: number | null;
  premium_acceleration: number | null;
  buy_trap_prob: number;
  sell_trap_prob: number;
  fake_breakout_prob: number;
  fake_breakdown_prob: number;
  smart_money: string | null;
  smart_money_label: string | null;
  expected_recovery_minutes: number | null;
  do_not_buy_above: number | null;
  emergency_exit: number | null;
  next_review_seconds: number;
  locked: boolean;
  signal_age_seconds: number;
}

export interface PerformanceReport {
  is_real_data: boolean;
  data_ready: boolean;
  reason: string;
  message: string;
  trades_recorded: number;
  trades_required: number;
  metrics: Record<string, number | null>;
  trade_distribution: Record<string, { trades: number; wins: number; net_pnl: number }>;
  monthly_performance: Record<string, { trades: number; net_pnl: number }>;
}

export interface Indicators {
  ema9: number | null;
  ema20: number | null;
  ema50: number | null;
  vwap: number | null;
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  atr: number | null;
  adx: number | null;
  supertrend: number | null;
  supertrend_dir: number | null;
  bb_upper: number | null;
  bb_mid: number | null;
  bb_lower: number | null;
  support: number | null;
  resistance: number | null;
  swing_high: number | null;
  swing_low: number | null;
  trend: string | null;
  market_structure: string | null;
  momentum: number | null;
  volume_spike: boolean;
  breakout: string | null;
  candle_pattern: string | null;
  chart_pattern: string | null;
  chart_pattern_bias: string | null;
  pcr: number | null;
  max_oi_call_strike: number | null;
  max_oi_put_strike: number | null;
  expected_move: number | null;
  institutional: string | null;
  institutional_label: string | null;
  volume_level: string | null;
  fake_signal: string | null;
  liquidity_sweep: string | null;
  pullback: string | null;
  premium_state: string | null;
  premium_bias: string | null;
  premium_call_state: string | null;
  premium_put_state: string | null;
  oi_state: string | null;
  oi_bias: string | null;
  oi_writing: string | null;
  oi_change_pct: number | null;
  smart_money: string | null;
  smart_money_label: string | null;
  smart_money_detail: string | null;
  news_material: boolean;
  news_bull_pct: number | null;
  news_bear_pct: number | null;
  poc: number | null;
  vah: number | null;
  val: number | null;
  value_area_position: string | null;
}

export interface ScoreBreakdown {
  name: string;
  signal: string;
  weight: number;
  contribution: number;
  detail: string;
  level: number;
  confirm_only: boolean;
}

export interface Recovery {
  in_position: boolean;
  entry_premium: number | null;
  current_premium: number | null;
  unrealized_pct: number | null;
  recovery_probability: number | null;
  expected_recovery_minutes: number | null;
  recommended_action: string | null;
}

export interface Position {
  option_symbol: string | null;
  side: string | null;
  entry_premium: number | null;
  current_premium: number | null;
  quantity_lots: number;
  pnl: number;
  pnl_pct: number;
  brokerage: number;
  net_pnl: number;
  holding_minutes: number;
  entry_time: number | null;
  trailing_stop: number | null;
}

export interface OptionRecommendation {
  option_symbol: string;
  strike: number;
  option_type: "CE" | "PE";
  premium: number;
  confidence: number;
}

export interface NewsItem {
  time: number;
  source: string;
  headline: string;
  category: string;
  sentiment: "BULLISH" | "BEARISH" | "NEUTRAL";
  impact: number;
}

export interface EventGuard {
  active: boolean;
  minutes_to: number | null;
  event: string | null;
  impact: string | null;
  factor: number;
}

export interface EconomicEvent {
  ts: number;
  name: string;
  category: string;
  impact: string;
  affects: string;
}

export interface Fundamentals {
  stock: {
    available: boolean;
    symbol?: string;
    delivery_pct?: number;
    results_date?: string | null;
    note?: string | null;
  };
  flows: {
    available: boolean;
    date?: string;
    fii_net?: number | null;
    dii_net?: number | null;
    note?: string | null;
  };
}

export interface Alert {
  time: number;
  kind: string;
  message: string;
  severity: string;
}

export interface InstrumentOption {
  symbol: string;
  display: string;
}

export interface QuantumSignalView {
  instrument: string;
  instrument_name: string;
  spot: number;
  change_pct: number;
  market_open: boolean;
  as_of: number;
  action: string; // "BUY CE" | "BUY PE" | "WAIT" | "HOLD" | "EXIT"
  actionable: boolean;
  side: "CE" | "PE" | null;
  reasons: string[];
  regime: { label: string; color: string; raw: string };
  volatility: {
    state: string;
    color: string;
    adx: number | null;
    adx_min: number;
    atr_points: number | null;
    expected_move_points: number | null;
  };
  htf: { trend: string | null; agrees: boolean | null };
  bias15: { trend: string; agrees: boolean | null };
  plan: {
    option_symbol: string | null;
    strike: number | null;
    moneyness: string | null;
    current_premium: number | null;
    entry_range: [number, number] | null;
    stop_loss: number | null;
    target1: number | null;
    target2: number | null;
    target3: number | null;
    underlying_stop: number | null;
    expected_holding_minutes: number | null;
  };
  confidence: number;
  win_probability: number | null;
  win_probability_note: string;
  measured: {
    win_rate_pct: number;
    profit_factor: number;
    avg_points: number;
    trades: number;
    window: string;
  } | null;
  sizing: {
    capital: number;
    risk_per_trade_pct: number;
    risk_amount: number;
    lot_size: number;
    suggested_lots: number | null;
    per_lot_risk: number | null;
    est_risk: number | null;
    max_lots_cap: number;
  };
  zero_to_hero: ZeroToHero | null;
  trade_quality: string | null;
  risk_level: string;
  instruments: InstrumentOption[];
  candles: Candle[];
  position: Position;
  auto_trade: AutoTradeState | null;
}

export interface SignalScanRow {
  instrument: string;
  instrument_name: string;
  action: string;
  actionable: boolean;
  side: "CE" | "PE" | null;
  confidence: number;
  regime: { label: string; color: string; raw: string };
  spot: number;
  change_pct: number;
}

export interface SignalScan {
  rows: SignalScanRow[];
  best_actionable: string | null;
  as_of: number;
}

export interface RiskStatus {
  capital: number;
  risk_per_trade_pct: number;
  day_pnl: number;
  trades_today: number;
  trades_left: number;
  consecutive_losses: number;
  loss_limit: number;
  loss_used_pct: number;
  can_trade: boolean;
  block_reason: string | null;
  suggested_lots: number;
  lot_size: number;
  brokerage_per_lot: number;
  squareoff_note: string;
}

export interface LearningStats {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  expectancy: number;
  best: number;
  worst: number;
  note: string;
}

export interface BacktestResult {
  instrument: string;
  candles_tested: number;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  gross_pnl: number;
  net_pnl: number;
  max_drawdown: number;
  avg_hold_minutes: number;
  profit_factor: number;
  equity_curve: number[];
}

export interface AutoTradeEntry {
  option_symbol: string | null;
  side: string | null;
  strike: number | null;
  entry_premium: number | null;
  lots: number | null;
  entry_time: number | null;
  target1: number | null;
  target2: number | null;
  target3: number | null;
  stop_loss: number | null;
  locked_floor: number | null;
}

export interface AutoTradeHistoryRow {
  time: number | null;
  instrument: string | null;
  option: string | null;
  option_type: string | null;
  entry: number | null;
  exit: number | null;
  lots: number | null;
  net_pnl: number | null;
  win: boolean | null;
  holding_minutes: number | null;
  mode: string | null;
  exit_reason: string | null;
}

export interface AutoTradeState {
  enabled: boolean;
  mode: string;
  min_confidence: number;
  max_lots: number;
  in_position: boolean;
  last_action: string | null;
  last_action_time: number | null;
  entries_today: number;
  day_pnl: number;
  blocked_reason: string | null;
  entry: AutoTradeEntry | null;
  history: AutoTradeHistoryRow[];
}

export interface ZeroToHero {
  active: boolean;
  reason: string | null;
  is_expiry_day: boolean;
  side: string | null;
  option_symbol: string | null;
  strike: number | null;
  premium: number | null;
  lot_size: number | null;
  lots: number | null;
  budget: number;
  cost: number | null;
  max_loss: number | null;
  note: string | null;
}

export interface IndiaVix {
  available: boolean;
  last: number | null;
  change_pct: number | null;
  level: string | null;
  note: string | null;
}

export interface MarketBreadth {
  available: boolean;
  advances: number | null;
  declines: number | null;
  unchanged: number | null;
  ratio: number | null;
  bias: string | null;
  note: string | null;
}

export interface MaxPain {
  available: boolean;
  strike: number | null;
  spot: number | null;
  distance_pct: number | null;
  bias: string | null;
  note: string | null;
}

export interface PcrReading {
  available: boolean;
  pcr: number | null;
  bias: string | null;
  note: string | null;
}

export interface QualityComponent {
  name: string;
  score: number;
  weight: number;
  detail: string;
  available: boolean;
}

export interface TradeQuality {
  score: number;
  grade: string; // A+ / EXCELLENT / GOOD / AVERAGE / SKIP / NO SETUP
  components: QualityComponent[];
  note: string;
}

export interface Intelligence {
  india_vix: IndiaVix;
  breadth: MarketBreadth;
  max_pain: MaxPain;
  pcr: PcrReading;
  trade_quality: TradeQuality;
}

// How fresh the prices on screen are. A silent drop from the push socket to the
// ~3s REST poll is invisible on price alone, so the tick age is reported.
export interface FeedHealth {
  mode: string; // push | rest | simulated
  live: boolean;
  underlying_age_sec: number | null;
  option_age_sec: number | null;
  stale_after_sec: number;
  ws_connected: boolean;
  subscribed_tokens: number;
  push_pct_today: number | null;
  fallbacks_today: number;
  note: string;
  provider?: string;
  source?: string; // LIVE | SIMULATED | UNAVAILABLE | UNKNOWN
  state?: string; // CONNECTED | DEGRADED | DISCONNECTED | SIMULATED
  checked_at?: number | null;
}

export interface Snapshot {
  time: number;
  underlying: string;
  instrument: string;
  instrument_name: string;
  instruments: InstrumentOption[];
  tradingview_symbol: string;
  futures_price: number;
  futures_change: number;
  futures_change_pct: number;
  futures_day_high: number | null;
  futures_day_low: number | null;
  futures_day_open: number | null;
  futures_prev_close: number | null;
  option_day_high: number | null;
  option_day_low: number | null;
  market_open: boolean;
  session_note: string | null;
  data_source: string;
  feed_mode: string; // push | rest | simulated
  feed_health?: FeedHealth | null;
  trade_mode: string;
  can_trade_live: boolean;
  risk: RiskStatus | null;
  auto_trade: AutoTradeState | null;
  zero_to_hero: ZeroToHero | null;
  learning: LearningStats | null;
  market_status: string;
  news_sentiment: string;
  news_score: number;
  indicators: Indicators;
  decision: Decision;
  recovery: Recovery;
  position: Position;
  watchlist: OptionRecommendation[];
  score_breakdown: ScoreBreakdown[];
  news: NewsItem[];
  event_guard?: EventGuard | null;
  alerts: Alert[];
  intelligence?: Intelligence | null;
  selected_option_candles: Candle[];
  futures_candles: Candle[];
}
