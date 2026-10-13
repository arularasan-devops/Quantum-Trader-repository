# Phase 8 delivery — install notes

This archive is a **patch overlay**, not a runnable application: it is the Phase 7
delivery plus the Phase 8 research package. Copy it over a complete build exactly as
before, and read `INSTALL_PHASE7.md` in this archive for the install, the
one-uvicorn rule and the three files that make the tick fast — all three are
included here unchanged, so a rebuild from this zip cannot lose the
`tick_cost_ms` 37,286 → 37.9 fix.

```bash
cd /path/to/unzipped/delivery
rsync -av --exclude data --exclude .env --exclude .venv backend/  ~/projects/quantum-trader-phase7/backend/
rsync -av frontend/ ~/projects/quantum-trader-phase7/frontend/
```

Never copy `data/`, `.env` or `.venv` from the patch.

## What Phase 8 adds

The research questions Phase 7 could not answer, and nothing else:

| file | question |
| --- | --- |
| `app/research/phase8/calibration.py` | does the displayed confidence rank outcomes? |
| `app/research/phase8/spread.py` | what does the real recorded bid/ask cost, gross R vs net R? |
| `app/research/phase8/causes.py` | which single cause best explains each loss? |
| `app/research/phase8/expiry.py` | expiry from the tradingsymbol; is expiry day different? |
| `app/research/phase8/capture.py` | which exit keeps more of the move, net of the book? |
| `app/research/phase8/continuation.py` | hold or protect, on past-only information? |
| `app/research/phase8/contract.py` | what did the chain say about the leg bought? |
| `app/research/phase8/gates8.py` | the extra gates the expiry study needs |
| `app/research/phase8/findings.py` | the evidence label every block must carry |
| `phase8_report.py` | the CLI that writes `phase8_report.json` + `.md` |

It owns **no** path or policy arithmetic: every MFE, MAE, give-back, chase and fill
comes from `app/research/phase7/paths.py` and `policies.py`, so a Phase 8 table
cannot disagree with the Phase 7 table it came from. `_smoke_phase8.py` asserts that
(the package defines no `walk`, no `simulate`, no exit policy). The extra exit levels
and trail widths (`B_FIXED_4PCT`, `C_TRAIL_25`, `C_TRAIL_40`) are new *names* in the
existing Phase 7 grammar, executed by the Phase 7 parser unchanged.

Contract expiry is parsed from the tradingsymbol because the recorded chain leg has
no `expiry` field: `NIFTY18AUG26...` and the BFO compact form `SENSEX26820...` are
both understood, and anything else becomes `EXPIRY_UNKNOWN` rather than a guess.
Recording `expiry` on the leg would remove the parser; that is a production change
and was deliberately not made.

## Run

```bash
cd ~/projects/quantum-trader-phase7/backend

# stop the backend first, then copy the DB, then analyse the copy — a hot copy
# arrives with a corrupt candle index
.venv/bin/python phase8_report.py \
  --db data/history.db \
  --research-db data/research.db \
  --instruments CRUDEOIL,NIFTY,SILVER,GOLD,COPPER,BANKNIFTY,SENSEX \
  --session $(date +%F) \
  --out ~/phase8_report.json --md ~/phase8_report.md
```

`--research-db` is read only to count the live AI paper book; omit it and that line
reports "not read" instead of inventing paper results.

`--session` takes IST dates (comma separated); omit it for every recorded session.
`--limit N` restricts to the most recent N candles per instrument if a run is slow.

## Guarantees

Read-only research. No change to BUY/WAIT/NO_TRADE, gates, thresholds, stops,
targets, R:R, strike selection, direction rules, the chase guard, Early
Momentum/Early-Early, execution, the auto-buy bot or AI production behaviour. The
displayed confidence is read and never written. No order can be placed from
`phase8_report.py`, and no AI model is built or promoted.

## Smokes

```bash
.venv/bin/python _smoke_phase8.py          # 79 checks — Phase 8
.venv/bin/python _smoke_phase7.py          # 72 checks
.venv/bin/python _smoke_hist_limiter.py    # 14 checks — the performance fix
.venv/bin/python _smoke_history_gaps.py    #  8 checks
.venv/bin/python _smoke_ai_resolver.py     # 12 checks
```

Phase 7 output is unchanged by this delivery because no Phase 7 file is touched by
it: `app/research/phase7/*` and `phase7_report.py` are the Phase 7 delivery's copies,
byte-for-byte, and the 72-check Phase 7 smoke passes against them.

`_smoke_flow.py` still fails as it did before Phase 7: it expects one ordinary red
candle to HOLD while the existing `flow_strength_floor` produces EXIT. Neither the
production behaviour nor the test was changed to hide that.

## How to read the report

Every block carries one label:

- `OBSERVATION` — a measurement of the sample; claims nothing about the next trade.
- `HYPOTHESIS` — a relationship worth testing over 20+ sessions.
- `REQUIRES MORE DATA` — the cell is too small to read at all.

`VALIDATED` exists in the label set and is deliberately unreachable from one
dataset: it requires the gates to pass **and** a chronological holdout the study did
not see to reproduce the effect, so it appears nowhere in a single-session report.
Otherwise Phase 8 never issues a label stronger than `HYPOTHESIS` for a comparison,
even when every gate passes: a passing gate makes a comparison permissible, not
conclusive. Policy rankings are printed as `CANDIDATE_ONLY`, and every early-exit
policy is charged with its premature-exit rate so "captures more of the move" cannot
be won by exiting at the first tick.
The loss-attribution priority order printed with the table **is** the definition of
"primary cause"; it is a research classification rule and is not used anywhere in
production.
