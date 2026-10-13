# Phase 7 — Real-Chain Entry/Exit Intelligence

**Status: infrastructure delivered, no finding claimed, no production rule changed.**

Phase 6 ended with a mechanism worth chasing — the bot picked the right side and
gave the money back — and three defects that made chasing it impossible. Phase 7
fixes the three defects, builds the machinery that answers the question, and runs
it. What it does *not* do is answer the question, because the data cannot yet: one
recorded session, 12 real Crude BUYs, no bid/ask. The machinery says so itself
(see *Acceptance gates*) rather than leaving it to a footnote.

---

## 1. The three blockers, and what each actually was

| blocker | symptom | real cause | fix |
| --- | --- | --- | --- |
| AI outcome resolver | 13,276 decisions, **0** resolved outcomes | forward bars were read only from the live provider's in-memory window (empty after a restart or for a cold instrument), then skipped silently; the queue is `ORDER BY ts ASC LIMIT n`, so one unlabellable row at the head starved every row behind it forever | falls back to persisted history; a genuinely unlabellable row is written `UNRESOLVABLE_<reason>` and excluded from training so the queue moves; skip reasons published as telemetry |
| 38% missing 1-min bars | Crude 137/357 rows missing; contaminates ATR, VWAP, extension, regime | not the feed — each pass persisted only `candles[-1]`, so every minute between two scans of the same instrument was never stored. Reproduced: CRUDEOIL 25.8%, NIFTY 36.0% missing, contiguous 50+ bar holes | persists the trailing window (`record_candles`, re-writes are no-ops); `candle_gaps()` measures session-aware missing bars |
| regime 85% EXHAUSTED, never FRESH_MOMENTUM | 13,276 live decisions with zero TREND / RANGE / FRESH_MOMENTUM | a calibration bug, **not** the missing bars — on gap-free windows only it was still 87% EXHAUSTED. Extension was gated at ≥2/≥3 ATR from the swing while the measured distribution is p10 ≈ 2.8, p50 ≈ 5.0, so the cuts sat below the 10th percentile and FRESH_MOMENTUM (which also required <2) was unreachable | staging now uses scale-free position inside the swing. After: RANGE 23–32%, EXTENDED 13–23%, FRESH_MOMENTUM 16–21%, TREND 20–35%, EXHAUSTED 5–7% |

This is paper/AI-side code. The production engine's own regime path, gates,
stops, targets, chase guard, strike selector, Early Momentum and the source-level
real-money guard were not touched.

Proof: `_smoke_ai_resolver.py` (12 checks), `_smoke_history_gaps.py` (8),
`_smoke_regime_calibration.py` (23) — all passing.

---

## 2. What was built

`backend/app/research/phase7/` — research only, no import route to an order:

| module | spec parts | what it answers |
| --- | --- | --- |
| `dataset.py` | 1 | loads chains split into **REAL_BROKER / SIMULATOR / UNKNOWN**, per snapshot, never mixed; reads a consistent copy when the live app holds the DB lock |
| `quality.py` | 1, 2 | cadence, strike/CE-PE/expiry coverage, field availability, duplicates, gaps; underlying missing bars / duplicates / ordering; the **age of the data each decision would have used** |
| `paths.py` | 3, 4, 5, 7, 21 | replays the **existing production `decide()`** on recorded bars (no new signal, no lookahead), then walks the chosen leg on its **real quoted premium**: MFE, MAE, time to each, target/stop touch order, entry chase, MFE capture and give-back |
| `policies.py` | 6, 8, 14, 16 | 8 research entry policies × 11 research exit policies, plus the cost model |
| `attribution.py` | 22, 23 | per-loss cause classifier; manual-vs-bot comparison normalised to points / % / R |
| `gates.py` | 24, 25 | acceptance gates that decide what the report is allowed to claim |

`backend/phase7_report.py` runs the whole chain and writes JSON + markdown.
`backend/_smoke_phase7.py` (72 checks) proves the study cannot cheat.

Three invariants are enforced in code, not in prose, and each has a test:

1. **No lookahead** — a policy only reads quotes at or after the timestamp it acts on.
2. **The stop is preserved** — every exit policy honours the production stop at the
   first quote through it and may only add an *earlier* exit; it can never widen or defer one.
3. **Outcome is decided by time, not by size** — a path that is stopped and then
   rallies far past the target is `STOP_FIRST`. A magnitude comparison would score it a winner.

One measurement fell out of that third invariant and is worth stating on its own:
on a 60-second snapshot series a stop can only be acted on at the *next* quote, so
**a stopped trade loses measurably more than the 1R it was risked at** (median
overshoot 0.47R in the local run). Part of Phase 6's alarming −4.31R median MAE is
this effect, not the market.

---

## 3. What today's data can and cannot support

The gates run on every report and derive the verdict:

| gate | need | this box | pass |
| --- | --- | --- | --- |
| REAL_SESSIONS | ≥ 20 REAL_BROKER sessions | 0 (all local snapshots are SIMULATOR) | FAIL |
| SAMPLE_SIZE | ≥ 100 reconstructed BUYs | 8 | FAIL |
| CELL_SIZE | ≥ 30 BUYs per cell | 8 | FAIL |
| DATA_COMPLETENESS | ≤ 5% missing 1-min bars | 0.0% (after the fix) | PASS |
| SPREAD_AVAILABLE | bid + ask on the legs | no | FAIL |
| MANUAL_COMPARISON | ≥ 20 legs traded by both | 0 | FAIL |

Status: **EXPLORATORY_ONLY** — and the report prints, per failing gate, the exact
class of conclusion it forbids. While `SPREAD_AVAILABLE` fails, no absolute
expectancy claim is permitted at all; while `REAL_SESSIONS` fails, "policy X is
better" is not a sentence this data can produce.

Your machine is the one with real chains (2,874 REAL_BROKER snapshots, 47
instruments, one session). Run it there:

```bash
cd backend
.venv/bin/python phase7_report.py \
    --instruments CRUDEOIL,NIFTY \
    --manual ~/my_fills.json \
    --out ~/phase7_report.json --md ~/phase7_report.md
```

`--manual` is optional and takes your own fills — the only real execution
benchmark in the whole phase:

```json
{"fills": [{"symbol": "CRUDEOIL7850PE", "entry": 98.35, "exit": 103.00,
            "lots": 20, "lot_size": 100}]}
```

Nothing is inferred for that file. A made-up manual fill would corrupt the only
honest comparison available, so an absent file simply skips Part 23.

---

## 4. Limits carried into every number the tool prints

- option premiums are ~60s snapshot closes: an intrabar stop or target touch inside a minute is invisible;
- no bid/ask is recorded, so every entry and exit price is **better than a real fill would have been**;
- costs are an assumption (₹120/round trip options, ₹1,200 futures) until you send one contract note;
- the replay does not apply live cooldowns, concurrency limits or the risk governor, so it produces **more** BUYs than the live funnel did — it is not a P&L simulation of your day.

---

## 5. UI changes shipped with this phase

- **Signals tab removed** — components deleted, not merely unrouted; one signal screen remains.
- **Four distinct tones** — Signal (new BUY), Bot-bought (fill), Decision (state change), Momentum — replacing one tone for everything, each de-duplicated per transition, first observation silent.
- **Sound only on the Signal dashboard** — audio is gated by the active view; the alert log still records everything on other tabs.
- **Research tab scrolling fixed** — one page-level scroller instead of nested containers, so the lower panels are reachable.

Verified: `npx tsc --noEmit` clean, `next build` succeeds, `_smoke_alerts.py` passing.

---

## 6. Known issues I did not touch

- **`_smoke_flow.py` fails, pre-existing and unrelated.** It asserts a single ordinary red candle must HOLD an open CE leg (de-whipsaw), but `flow_strength_floor=70` — added in a later phase — now EXITs when strength fades. Both behaviours are defensible; one of the two is wrong. Changing the test would hide it and changing the production exit is not authorised, so it is reported instead.
- **`_smoke_buypath.py` is slow** (it ticks every registered instrument), not broken; it needs an isolated `QT_DATA_DIR` when the app is running, as do `_smoke_scaleout.py` and any test that writes history.
- **No git repository on this box** (`qt_work` is not under version control), so there is no PR. Delivery is a zip; point me at the repo and I will open one properly.

---

## 7. Recommendation

Nothing here justifies a production change, and that is the finding. The order of
work that would:

1. record **20+ real sessions** with the recorder verified as writing `REAL_BROKER`;
2. record **bid and ask** on the legs — without a spread no exit policy can be priced;
3. send **one options contract note** so cost stops being an assumption;
4. keep the AI paper book running now that the resolver labels outcomes, for the paired record;
5. then re-run `phase7_report.py` and let the gates decide whether anything may be proposed.

Until the gates pass, the exit-give-back mechanism stays what Phase 6 made it:
the most plausible explanation available, and still not a measurement.
