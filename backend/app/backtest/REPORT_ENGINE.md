# Frozen-Engine (Quantum-Signal gated) Backtest — 5 years, real Angel data

**What this tests:** the *actual* pipeline the single-page dashboard uses —
`compute_indicators → classify_market → decide → Quantum-Signal gate`. A trade
opens ONLY when the frozen engine says BUY **and** all gates agree (regime
TRENDING/BREAKOUT, ADX ≥ 20, positive expected move, 5-min trend backs the side,
15-min bias backs the side). Each trade is then managed on the **underlying**
using the engine's own regime ATR stop/target plan and expected holding time.

Underlying points are real/measured. Data: 2021-08-02 → 2026-08-05.

## Headline — the first method with a measured edge (PF > 1.0)

| instrument | trades | win% | avg pt | total pt | profit factor |
|---|---|---|---|---|---|
| NIFTY    | 10,186 | 43.0% | +0.47 | +4,808 | **1.05** |
| CRUDEOIL | 26,402 | 42.6% | +0.29 | +7,587 | **1.06** |

For context, on the same data raw Candle-Flow was PF 0.83–0.84 and candlestick
patterns had no edge. This is the first configuration that is net positive.

## Walk-forward (per calendar year) — is it a fluke? No.

**NIFTY**

| year | trades | win% | total pt | PF |
|---|---|---|---|---|
| 2021* | 892 | 42.9% | −185 | 0.98 |
| 2022 | 2,343 | 43.6% | +2,099 | 1.11 |
| 2023 | 1,839 | 42.5% | +178 | 1.02 |
| 2024 | 1,978 | 42.8% | +996 | 1.05 |
| 2025 | 1,921 | 42.9% | +702 | 1.04 |
| 2026* | 1,213 | 43.0% | +1,018 | 1.08 |

**CRUDEOIL**

| year | trades | win% | total pt | PF |
|---|---|---|---|---|
| 2021* | 2,181 | 40.8% | −623 | 0.93 |
| 2022 | 4,331 | 42.8% | +1,969 | 1.07 |
| 2023 | 5,771 | 42.3% | +330 | 1.01 |
| 2024 | 5,184 | 42.9% | +575 | 1.03 |
| 2025 | 5,148 | 41.8% | +445 | 1.03 |
| 2026* | 3,787 | 44.5% | +4,891 | 1.15 |

*2021 and 2026 are partial years. PF > 1.0 in **5 of 6 years on both
instruments** — the edge is consistent across time, not one lucky period.

## Honest reading — do NOT over-trust this

1. **The edge is THIN.** PF ~1.05 means for every 100 points risked you make
   ~105. Real, but small and high-variance — not a money printer.
2. **Win rate is 43% — you lose more often than you win.** It only nets positive
   because winners are larger than losers. That is psychologically hard to trade.
3. **These are UNDERLYING points, not option P&L.** Real option trading adds
   **theta decay and bid/ask spread**, which can easily eat a 5% edge and push it
   below breakeven. This is the single biggest caveat. It can only be resolved by
   validating on the **live option-chain data** (the recorder we added) — until
   then, do NOT assume this 5% underlying edge survives as option profit.
4. It confirms the design is right: the gate (regime + ADX + 15m/5m alignment) is
   what turns a losing raw signal into a marginally positive one.

## Verdict

The **frozen engine + Quantum-Signal gate** is the only method measured to be net
positive over 5 years, consistently, on both instruments. It should remain the
tool's primary signal. But the edge is thin and unproven at the option level, so
the tool must stay **paper/advisory**, keep win-probability labelled honestly, and
the next real step is validating against recorded live option premiums.

Reproduce:
```
.venv/bin/python engine_backtest_run.py --instrument NIFTY    --adx-min 20
.venv/bin/python engine_backtest_run.py --instrument CRUDEOIL --adx-min 20
```
