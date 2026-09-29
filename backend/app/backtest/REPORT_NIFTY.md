# Candle-Flow Backtest — NIFTY (1 year, 1-minute)

**Data:** real Angel One `getCandleData` history for the front-month NIFTY
future, **2025-08-04 → 2026-08-04**, **92,320 one-minute candles**.

**Method:** identical to the CRUDEOIL report — replay the real
`app.execution.flow.detect` logic bar-by-bar, measure the outcome on the real
underlying futures move (option premium P&L is modelled only).

## Headline result (default config)

| metric | value |
|---|---|
| trades taken | 11,412 (2,524 SWITCHes) |
| win rate | **31.7%** |
| avg / median | −1.91 / −4.4 pts |
| best / worst | +763 / −476 pts |
| total | **−21,752 pts** |
| profit factor | **0.72** |

## Config sweep

| config | trades | win% | total | PF |
|---|---|---|---|---|
| loose (0.10/1) | 40,839 | 32.8 | −44,274 | 0.76 |
| default (0.30/2) | 11,412 | 31.7 | −21,752 | 0.72 |
| steady (0.35/3) | 3,534 | 31.8 | −6,778 | 0.73 |
| strict (0.45/3) | 2,166 | 31.2 | −5,070 | 0.70 |
| very strict (0.50/4) | 404 | 31.7 | −696 | 0.77 |

## Trend-aligned filter

| config | trades | win% | total | PF |
|---|---|---|---|---|
| raw | 11,412 | 31.7 | −21,752 | 0.72 |
| EMA20/60 | 6,109 | 32.0 | −10,194 | 0.76 |
| EMA30/120 | 5,968 | 32.1 | −10,634 | 0.74 |
| EMA60/240 | 5,889 | 32.3 | −9,898 | 0.75 |

## Conclusion

Same as CRUDEOIL — **no measured edge** for raw 1-minute Candle-Flow on NIFTY in
any configuration or with a higher-timeframe trend filter (PF stays 0.70–0.77,
win rate ~32%). Do not trade it mechanically; use as a discretionary alert only.
