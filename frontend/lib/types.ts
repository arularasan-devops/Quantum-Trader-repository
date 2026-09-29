export type Signal = "BUY" | "WAIT" | "HOLD" | "EXIT" | "NO_TRADE";

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface Decision {
  signal: Signal;
  confidence: number;
  signal_strength: number;
  trade_quality: string;
  recommended_option: string | null;
  strike: number | null;
  option_type: "CE" | "PE" | null;
  current_premium: number | null;
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
  pcr: number | null;
  max_oi_call_strike: number | null;
  max_oi_put_strike: number | null;
  expected_move: number | null;
}

export interface ScoreBreakdown {
  name: string;
  signal: string;
  weight: number;
  contribution: number;
  detail: string;
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

export interface Alert {
  time: number;
  kind: string;
  message: string;
  severity: string;
}

export interface Snapshot {
  time: number;
  underlying: string;
  futures_price: number;
  futures_change: number;
  futures_change_pct: number;
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
  alerts: Alert[];
  selected_option_candles: Candle[];
  futures_candles: Candle[];
}
