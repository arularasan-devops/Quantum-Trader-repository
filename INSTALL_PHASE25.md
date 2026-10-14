# Install — Phase 25: captured-window CE/PE study (cumulative)

Adds a new, isolated study that answers the one question the five-year search
could not: **does buying the ATM option pay, priced at the ask going in and the
bid coming out, on your own stored real-broker books?**

Nothing existing changes. Phase 23's hurdle shadow keeps recording, Phase 24's
five-year study keeps its own artefacts, and the production signal, gates,
direction, strike, premium floor, stop, target, exit, futures logic and order
path are untouched. Phase 25 has no tick hook and no write path into any book.

Install over any build from Phase 23 onward; do not unzip older overlays after
it.

```bash
cd ~/projects/phase8_delivery
unzip -o "/mnt/c/Users/djs admin/Downloads/qt_phase25_fix.zip" -d .
chmod +x start.sh stop.sh

cd backend
.venv/bin/python _smoke_phase25.py     # PHASE 25: 72 passed, 0 failed
.venv/bin/python _smoke_phase24.py     # 93 checks passed
.venv/bin/python _smoke_phase23.py     # 72 checks passed
.venv/bin/python _smoke_phase19.py     # checked 215 · OK
```

If any of those does not pass, stop and send the output — nothing here is
needed to trade, so staying on the previous build costs nothing.

Then run the study. It reads the option-chain store, so **stop the engine
first**: the count walks ~157k payloads and a writing engine turns pages into
retries.

```bash
cd ~/projects/phase8_delivery && ./stop.sh
cd backend
.venv/bin/python -m app.research.phase25.cli coverage   # which instruments qualify
.venv/bin/python -m app.research.phase25.cli run        # the study (long)
.venv/bin/python -m app.research.phase25.cli show       # the saved verdict
cd .. && ./start.sh
```

`run --quick` shortens the search for a first look, and
`run --instrument NIFTY` studies one name. `--db-path` points the study at
another captured store (an archived store, or a merge of several) and
`--out-dir` keeps its artefacts away from the live ones; both default to the
live paths.

Two corrections since the first Phase 25 archive. **Eligibility** now counts the
stored quotes that have a *later* quote of the same contract to resolve
against — that is the sample the study is actually limited by. The first version
required 200 distinct contracts, which refused a captured ladder that is only a
few strikes wide however long it runs; distinct contracts are still reported as
an independence caveat, because heavy overlap makes the effective sample far
smaller than the trade count. **Holding time** is now the exit quote's clock
time minus the entry's, carried per row as `holding_seconds`, instead of a
count of quotes.

Artefacts land in
`backend/data/phase25/`; nothing under `backend/data/` is in this archive, so
every recorded observation, paper leg and journal row survives the install.

Then open **http://localhost:3000** — that port only; the engine's CORS
allowlist contains just that origin. The new **CAPTURED OPTION CE/PE** panel is
at the bottom of the research dashboard, under 5-YEAR DISCOVERY.

## What it measures, and what it refuses to measure

**Only real stored books.** Simulator snapshots are counted and excluded from
every price. A leg is dropped unless both sides are present, positive and
uncrossed. No premium is ever modelled, and the family-median spread assumption
is never used where a real bid/ask exists.

**Executable pricing.** Entry is the **ask** of a later stored quote plus the
configured slippage; exit is the **bid** minus slippage. The mid-price appears
nowhere in a P&L. Brokerage and statutory charges are charged per trade from the
Phase 19 cost model; the spread is *not* charged again there, because it is
already paid in the prices.

**Causal context.** Each snapshot is aligned to the last underlying candle that
had **closed** before it, so no feature can see its own bar. Outcomes are read
forward from later quotes of the *same* contract, inside the same session only;
if a stop and a target are reached at the same observation, the stop wins.
Candidates whose path never resolves are reported as unresolved, not as wins.

**Refusals are kept.** Every refused opportunity and every `NO TRADE` stays in
the candidate rows (`p25_candidates_<INST>.jsonl.gz`), so a rule's selectivity
can be read as well as its returns.

**The window is the ceiling.** The capture is weeks, not years, so the strongest
verdict this study can produce is `RESEARCH_LEAD` — a reason to collect live
paper evidence. There is no `VALIDATED` label in Phase 25 and nothing here
promotes itself. An instrument whose capture cannot support a chronological
development/validation/holdout split is `REQUIRES_MORE_DATA` with its real
counts, never a negative result. An unreadable store reports "unmeasured",
never zero.

## What is reported

Per instrument: candidates, resolved/unresolved, sessions, CE vs PE, base
T1-before-SL, average net R and ₹, median measured spread %, median break-even
hurdle %, and a stop-band geometry sweep with the pre-cost break-even T1 rate
next to the achieved one — so a failed search can be told apart from geometry
that never paid. Then time-of-day cohorts, hurdle bands (0-3 / 3-5 / 5-10 /
>10%), ranked cohorts with validation, holdout, walk-forward folds, drawdown,
top-1% outlier contribution and the exact clause each one failed, plus rejected
cohorts and fingerprints.

Read-only endpoints, artefact-backed, unable to start a study or place an order:

```
GET /api/phase25/coverage
GET /api/phase25/summary
GET /api/phase25/cohorts?limit=25
```
