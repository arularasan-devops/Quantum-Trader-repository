# PHASE 3 — STRIKE / LEG SELECTION AUDIT

**Research only. No production trading logic was changed.** No gate threshold, BUY/WAIT
rule, stop, target, risk parameter, confirmation, chase guard or execution rule was
touched. Early Momentum remains non-executable. Nothing in this report has been
deployed, and the research selector described in §12–§14 is **not** wired into the
engine — the evidence says it should not be.

New research-only tools, all offline:

| file | purpose |
|---|---|
| `backend/strike_audit.py` | modelled-ladder replay, §1–§14 |
| `backend/strike_audit_chain.py` | the same audit against **stored broker chains** |
| `backend/chain_provenance.py` | tells real broker snapshots from simulator ones |

---

## 0. THE DATA PROBLEM, STATED BEFORE ANY NUMBERS

Phase 3 asks a question about *contracts*, and the replay has **no real option chain**.
I checked before building anything.

`chain_provenance.py` classifies every one of the 50,961 `chain_snapshots` in this
machine's `history.db`, using the fact that the Angel provider always writes
`oi_change = 0` (SmartAPI exposes no OI delta) while the simulator draws it from a
Gaussian:

```
total_real_broker_snapshots: 0
CRUDEOIL: 5,512 simulated, 0 real
NIFTY:    5,266 simulated, 0 real
… every instrument the same
```

**Every stored chain on my machine came from the simulator.** So:

* IV, OI, volume and bid/ask **do not exist** in this dataset. Six of the twelve
  features you listed in §4 cannot be measured, and I have not invented them — the
  modelled ladder sets OI and volume to `0` to mean *absent*, never *illiquid*.
* Skew does not exist either. The ladder is priced flat across strikes.

What I built instead, and what it can and cannot prove:

**MODELLED PREMIUM LADDER** — real cached Angel 1-minute candles for the underlying;
each candidate priced with the repository's own Black-Scholes on **trailing realised
volatility from prior bars only**; IV frozen at entry; identical stop/target ratios for
every candidate so they are comparable; a bar spanning both stop and target scored as
STOP. No future information enters candidate construction or scoring.

The honest weakness: with a flat surface, all strikes are smooth functions of one
underlying path, so a modelled ladder **understates** how much strikes can differ. Where
this matters I say so, and §0.1 is the fix.

### 0.1 The real-chain path is now built and waiting for your data

`strike_audit_chain.py` runs this entire audit against `chain_snapshots` — real
premium, real IV, real OI, real volume per strike. It refuses to pretend: its
`chain_source` field reports `REAL_BROKER` or `SIMULATOR`, and on simulator data it
prints a warning that the output means nothing beyond a plumbing test. I have verified
it end-to-end on my simulator data (251 BUYs, pipeline clean).

**What I need from you — two commands on the machine that runs live:**

```bash
cd backend
.venv/bin/python chain_provenance.py --out ~/chain_provenance.json
.venv/bin/python strike_audit_chain.py --instrument CRUDEOIL --out ~/chain_crude.json
```

If your `history.db` has live sessions in it, the first command will report a non-zero
`real_broker_snapshots` and the second becomes the real-premium answer to Phase 3 —
which supersedes everything below. Send me both JSONs. Its own remaining limits: 60-second
snapshot cadence (no intrabar highs/lows) and still no bid/ask, because a SmartAPI quote
carries a premium and not a spread.

**Everything from here on is MODELLED PREMIUM RESULTS.** It is not tick-accurate and it
is not a P&L forecast.

---

## Scope of the run

Frozen engine, replayed bar by bar; every BUY it produced was audited with the full
candidate ladder.

| | CRUDEOIL | NIFTY |
|---|---|---|
| bars replayed | 119,729 | 119,729 |
| BUYs audited | 18,496 | 16,454 |
| horizon | 30 bars (60 also run) | 30 bars |
| assumed DTE | 7 (2 and 20 also run) | 7 |
| ladder widths | ATM±2, ±6, ±12 | ATM±2, ±6, ±12 |

Eight sensitivity runs in total. Every headline below holds across all of them; where a
number moves with DTE, horizon or ladder width, I give the range.

---

## §1–§2 What was available, and what the engine took

Candidates: ATM−n … ATM+n on the traded side, priced at entry-time realised vol. The
production selector (`decision.py:_select_option`) is

```
0.6 · delta_fit(target 0.52)  +  0.4 · min(1, oi / 6000)
```

With no OI data the liquidity term is constant, so **in this replay the engine is a pure
delta-targeting selector**. That is worth stating plainly: 40% of its scoring weight is
inert whenever OI is missing or uniform.

It does what it claims. Median chosen delta: **0.519** (Crude), **0.520** (Nifty) — at
its 0.52 target, every configuration.

One finding you cannot see from the code: **which strike that delta target picks depends
entirely on time to expiry.** Same engine, same bars, Crude:

| assumed DTE | chosen ATM+0 | ATM+1 | ATM+2 |
|---|---|---|---|
| 2 days | 12,486 | 4,245 | 723 |
| 7 days | 9,542 | 5,103 | 3,840 |
| 20 days | 3,201 | 6,260 | 9,040 |

A fixed delta target is a *moving* strike distance. On a far expiry it drifts two strikes
OTM; near expiry it pins ATM. Not a bug, but it means "which strike does the engine buy"
has no single answer, and any strike-level rule you add later will interact with expiry.

---

## §3, §5, §6 Does the strike actually change the outcome?

This is the core of Phase 3, so I ran it at three ladder widths. ATM±2 spans only **±1.2%
of spot** on Crude and **±0.4%** on Nifty — narrow enough that I did not trust a null
result from it, so ATM±12 (±7.3% / ±2.4%) tests the question properly.

**Crude, ATM±12, every candidate scored on identical terms (n = 18,498 BUYs × 25 strikes):**

| offset | target-before-stop | expectancy | PF | median delta | median premium |
|---|---|---|---|---|---|
| −12 (deep ITM) | 21.4% | +0.035R | 1.10 | 0.72 | ₹602 |
| −6 | 24.7% | +0.050R | 1.13 | 0.62 | ₹329 |
| −1 | 27.0% | +0.054R | 1.13 | 0.54 | ₹229 |
| **0 (ATM)** | **27.3%** | **+0.054R** | 1.13 | 0.50 | ₹222 |
| **+1** | 27.2% | **+0.054R** | **1.14** | 0.50 | ₹221 |
| +6 | 26.1% | +0.045R | 1.11 | 0.46 | ₹300 |
| +12 (far OTM) | 25.0% | +0.050R | 1.13 | 0.42 | ₹501 |

**Nifty, ATM±12:** the same shape — expectancy peaks at −1/0/+1 (+0.054R) and falls to
+0.025R at ±12; target-before-stop peaks at +1 (35.2%) and falls to 24% at both wings.

Read that table against where the engine actually buys: **ATM and ATM+1 — the peak of
both curves.** Across a 25-strike span the entire expectancy range is 0.035R–0.054R.
The strike knob is worth roughly **0.02R**; the direction call is worth the rest.

**§6 by side and instrument (ATM±2, not combined):**

| | ITM2 | ITM1 | ATM | OTM1 | OTM2 |
|---|---|---|---|---|---|
| Crude CE — tbs / E | 22.9% / +0.042 | 24.7% / +0.045 | 26.3% / +0.047 | 27.9% / +0.051 | 29.2% / +0.051 |
| Crude PE — tbs / E | 24.4% / +0.054 | 26.4% / +0.058 | 28.3% / +0.062 | 29.7% / +0.063 | 30.7% / +0.058 |
| Nifty CE — tbs / E | 26.6% / **+0.016** | 28.8% / +0.019 | 30.7% / +0.019 | 32.5% / +0.020 | 33.9% / +0.019 |
| Nifty PE — tbs / E | 35.9% / +0.086 | 37.7% / +0.086 | 39.1% / +0.087 | 40.2% / +0.088 | 40.5% / +0.076 |

Target-before-stop rises monotonically towards OTM on all four — but expectancy is flat,
because cheaper legs win more often *and* lose more often. Those two metrics disagree
about strikes, which becomes the central problem in §14.

The much larger effect in that table has nothing to do with strikes: **Nifty CE
expectancy is +0.019R (PF 1.04) against Nifty PE +0.087R (PF 1.18)** — a 4× gap between
the two *sides*, dwarfing anything strike selection does. Crude is far more balanced
(CE +0.047 / PE +0.062). Caveat before you act on it: 120k Nifty minutes is a few months
of one directional regime, and a side asymmetry is exactly what a trending sample
manufactures. It needs a longer sample and real premiums before it means anything. I flag
it because it is the biggest number in the audit, not because it is proven.

**§5 delta bands (ATM±12, all candidates):**

| delta | Crude tbs / E / PF | Nifty tbs / E / PF |
|---|---|---|
| <0.30 | 38.4% / +0.046 / 1.09 | 41.7% / +0.023 / 1.04 |
| 0.30–0.40 | 30.5% / +0.052 / 1.12 | 35.6% / +0.058 / 1.12 |
| 0.40–0.50 | 26.3% / +0.063 / 1.17 | 31.2% / +0.052 / 1.12 |
| **0.50–0.60** | 23.3% / **+0.067 / 1.19** | 28.3% / +0.052 / 1.13 |
| 0.60–0.70 | 20.1% / +0.043 / 1.13 | 27.0% / +0.042 / 1.10 |
| 0.70–0.80 | 17.8% / +0.039 / 1.12 | 24.6% / +0.053 / 1.13 |
| 0.80+ | 9.9% / +0.025 / 1.10 | 16.0% / +0.037 / 1.11 |

**Is the current 0.52 delta target appropriate? On expectancy, yes — it sits on or beside
the peak** (Crude's best band is 0.50–0.60 at PF 1.19; Nifty's is 0.30–0.40 with
0.40–0.60 within noise). On target-before-stop alone a much lower delta looks better, and
that is a trap: <0.30 delta has the highest hit rate on both instruments and the *worst*
PF on Nifty (1.04). Deep ITM (0.80+) is the one clear loser — lowest hit rate on both,
no expectancy compensation. The engine is not there.

---

## §7–§8 Is it buying options that have already run?

Your hypothesis: the engine picks legs that have already spent their move. Measured per
BUY over the 30 bars before entry, signed in the trade's own direction, and bucketed
*without* any forward information:

**Crude** — median underlying move before entry **+0.58%**, median chosen-leg premium move
**+12.3%**; 13.4% of BUYs enter a leg already ≥25% expanded.
**Nifty** — **+0.19%** underlying, **+19.6%** premium; 37.7% already ≥25% expanded.

So yes, the premium routinely moves 20–100× the underlying's percentage move before
entry. But the outcome by bucket contradicts the conclusion:

| pre-entry expansion of the chosen leg | Crude tbs / E / PF | Nifty tbs / E / PF |
|---|---|---|
| ≤0% (leg flat or down) | 22.6% / +0.058 / 1.17 | 29.7% / +0.015 / 1.03 |
| 0–10% | 23.6% / +0.038 / 1.10 | 26.7% / **−0.011 / 0.98** |
| 10–25% | 27.3% / +0.041 / 1.10 | 37.3% / +0.072 / 1.15 |
| **25–50%** | **32.9% / +0.133 / 1.34** | 35.8% / +0.060 / 1.13 |
| 50–100% | 37.5% / +0.055 / 1.11 | **38.1% / +0.087 / 1.18** |
| >100% | — | 25.0% / **−0.263 / 0.60** (n=40) |

**An already-expanded premium is not the warning sign — it is the best bucket.** Entries
on a leg up 25–50% are the strongest population in the audit (Crude PF 1.34), and the
*worst* Crude/Nifty buckets are the ones where the option had barely moved. Premium
expansion here is reading as momentum confirmation, not as expense.

The exception is the extreme tail: >100% expansion turns sharply negative (Nifty
−0.26R, PF 0.60) — but n = 40, which is an observation and not a rule.

**§8 divergence** (chosen leg already ≥25% up while the underlying still went on to
travel ≥1 ATR in the trade's direction): **10.1% of Crude BUYs, 27.1% of Nifty BUYs.**
Only the *frequency* is a finding. That filter uses forward data in its own definition,
so its outcome stats are conditioned on the future and are **not** an edge — the
entry-time version of the question is the table above, and it points the other way.

---

## §9–§11 Was the chosen leg the right one, and do peak buys come from bad strikes?

Two separate questions, kept separate.

**Future-independent (§9, §4):** the correlation of every entry-time feature with
target-before-stop is weak — the strongest are `premium_pct_of_spot` **−0.17**,
`pre_expansion_ratio` **+0.13**, `vwap_dist_atr` **−0.13** (extended from VWAP is worse,
which supports your Phase 2 intuition), `minute_of_session` **+0.11**, `abs_delta`
**−0.08**. Correlation with realised R is near zero for *every* feature (|r| ≤ 0.03).
**No entry-time feature identifies a materially better contract.** That is the
future-independent verdict, and it is why I did not build a "BEST AVAILABLE" label out of
hindsight rankings alone.

**Counterfactual (§11), COUNTERFACTUAL RESEARCH ONLY** — hindsight rank of the chosen leg
among candidates, with a ±0.02R tie tolerance so that an unavoidable loss where every
strike stopped is not graded a selection failure:

| ladder | BEST | GOOD | ACCEPTABLE | SUBOPTIMAL | all candidates tied |
|---|---|---|---|---|---|
| Crude ATM±2 | **76.2%** | 9.5% | 10.2% | 4.1% | 63.9% |
| Nifty ATM±2 | **81.7%** | 7.6% | 6.4% | 4.3% | 71.3% |
| Crude ATM±6 | 57.5% | 3.7% | 4.3% | 34.4% | 41.5% |
| Nifty ATM±12 | 49.5% | 2.2% | 2.2% | 46.0% | 19.4% |

Note how the grade degrades purely as the ladder widens: with 25 candidates, *something*
beats your pick more often, by arithmetic. That is why the grade is a weak instrument and
the offset curves in §3 are the real evidence. In the window production actually searches,
the chosen leg is the best or joint-best outcome **76–82%** of the time, and in 64–71% of
BUYs **every candidate ended in the same place** — the strike was irrelevant to the result.
Median R left on the table when not best: **0.06R** (Crude) / **0.14R** (Nifty).

**Does a peak buy mean a bad strike? No.** Joining Phase 2's timing class to the strike
grade (ATM±2):

| Phase 2 class | share of BUYs | chose BEST leg | poor strike (ACCEPTABLE+SUBOPTIMAL) |
|---|---|---|---|
| Crude PEAK | 66.7% | 70.7% | **16.3%** |
| Crude LATE | 15.8% | 78.4% | 17.9% |
| Crude EARLY | 2.9% | 69.1% | 20.1% |
| Nifty PEAK | 55.1% | 76.1% | **13.0%** |
| Nifty LATE | 17.6% | 84.5% | 12.6% |

(GOOD is 0% poor by construction — the class requires the trade to have worked — so it is
excluded as tautological.)

**Peak buys are no more associated with poor strike choice than any other class.** Seven
out of ten peak buys took the best available leg: the entry was late *on every strike*.
Phase 2 said timing delay was not the cause of peak buys; Phase 3 says strike choice is
not either. What is left is the direction/regime call and the definition of the entry
trigger itself.

**§10 counterfactual example** (from `counterfactual_examples`, 12 per run, all labelled
`COUNTERFACTUAL_RESEARCH_ONLY`; the engine could not have known any of this):

```
ACTUAL BUY   Crude 5375, CE, regime REVERSAL_MODE, modelled IV 0.29
  strike 5400 (ATM)   entry ₹78.04   delta 0.475   MFE +₹6.79   → STOP

BEST ALTERNATIVE IN HINDSIGHT
  strike 5500 (ATM+2) entry ₹40.98   delta 0.302   MFE +₹4.36   → TARGET in 8 bars
```

Read it carefully, because it is the whole audit in miniature: the winning leg had a
*smaller* favourable move in rupees (+4.4 vs +6.8). It won only because its stop and
target were proportionally closer. Nothing observable at entry made it the better
contract — it was a different risk geometry, not a better forecast.

---

## §12–§14 The research model, and why it must not ship

Plain L2 logistic regression (written locally; `sklearn`/`scipy` are not installed), 15
entry-time features, standardised on training data only. Chronological split, no shuffle:
50% train → 20% validation → 15% out-of-sample → 15% walk-forward, plus expanding-window
folds.

It has genuine, stable ranking skill on the label it was given:

| | train | validation | out-of-sample | walk-forward folds |
|---|---|---|---|---|
| Crude ATM±2 | 0.607 | 0.628 | 0.624 | 0.68 / 0.62 / 0.63 / 0.62 |
| Crude ATM±12 | 0.684 | 0.659 | **0.711** | 0.73 / 0.68 / 0.72 / 0.67 |
| Nifty ATM±2 | 0.605 | 0.612 | 0.658 | 0.64 / 0.68 / 0.71 / 0.73 |

**§14 — and this is the finding that matters most.** Out-of-sample only, the model never
having seen these bars:

**Crude (ATM±2 / ATM±12):**

| selector | target-before-stop | expectancy | PF | max DD | median entry |
|---|---|---|---|---|---|
| current (delta 0.52) | 24.8% | **+0.060R** | **1.16** | −78.7R | ₹198 |
| research model | 29.0% / 36.0% | +0.061R / +0.042R | 1.15 / 1.09 | −93.1R / −95.7R | ₹140 / ₹23 |
| always ATM | 25.7% | **+0.061R** | **1.16** | −80.8R | ₹186 |
| cheapest leg | 29.1% / 36.2% | +0.061R / +0.032R | 1.15 / 1.06 | −93.1R / −90.8R | ₹140 / ₹22 |
| highest delta | 21.3% | +0.054R | 1.15 | −81.2R | ₹240 |

**Nifty:** current +0.040R / PF 1.09 → research **−0.009R / PF 0.98** (ATM±6) and
**−0.021R / PF 0.96** (ATM±12). The model turns a positive book negative.

Three things follow, and I would rather state them against my own work:

1. **The research selector is a rediscovery of "buy the cheapest leg."** Its picks agree
   with the cheapest-leg baseline almost perfectly (agreement with the current selector:
   0.3% on Crude, 0.0% on Nifty). Its AUC is real; its economic value is not.
2. **Maximising P(target before stop) destroys expectancy.** It lifts hit rate by 4–11
   points and pushes drawdown 18–25% deeper, because with proportional stops a cheap leg
   simply has both barriers closer. Target-before-stop cannot be the sole objective — on
   this evidence it is close to an anti-objective when used alone.
3. **A five-line "always ATM" rule matches the current selector exactly** (+0.061R vs
   +0.060R, PF 1.16 both, on Crude). So the delta-fit machinery is not earning its
   complexity — but it is not losing anything either, which is why I am not proposing to
   replace it.

**Recommendation: do not deploy the research selector.** It fails out-of-sample on the
metrics you listed as the objective.

---

## §15 THE ANSWER

> "Is the current Quantum Trader losing opportunity because it chooses the wrong option
> contract even when the market direction is correct?"

**No — not materially, on this evidence.** Four independent lines point the same way:

1. Across a 25-strike span the *entire* expectancy range is 0.035R–0.054R, and the engine
   buys at the peak of that curve (ATM / ATM+1) on both instruments.
2. In the window production searches, the chosen leg is the best or joint-best outcome
   76–82% of the time; in 64–71% of BUYs every candidate finished identically. Median R
   forgone when not best: 0.06R–0.14R.
3. Peak buys are no more associated with poor strike choice (13–16%) than late or early
   buys. Seven in ten peak buys took the best available leg — they were late on every
   strike.
4. No entry-time feature correlates with realised R above |0.03|, and the model built from
   all of them fails to beat the current selector out-of-sample.

**The alternative explanation the evidence supports:** entry quality is dominated by the
direction and regime call, not by contract choice. Phase 2 eliminated confirmation delay;
Phase 3 eliminates strike selection. Both were reasonable hypotheses and both are now
measured out. What remains is the trigger itself — *when* a setup is declared, and on
which side — plus the ~55–67% of BUYs whose target never arrives inside 30 bars, which
is a horizon and target-geometry question rather than a strike question.

**Where a strike effect does exist, precisely:**

* **Delta 0.80+ / ITM beyond ~4 strikes** — lowest target-before-stop on both instruments
  (9.9% / 16.0%), no expectancy compensation. The engine does not go there; it should stay
  out. Deep OTM beyond ~6 strikes is symmetrically weak.
* **Expiry interaction** — a fixed 0.52 delta target lands ATM at 2 DTE and ATM+2 at 20
  DTE. Any future strike rule must be expiry-aware or it will silently mean different
  things on different days.
* **The dead 40%** — the liquidity half of the selector's score does nothing without OI.
  On live data it will start voting, and nobody has ever measured what it does.

**Where the audit cannot answer, and it is exactly where a real effect would hide:**
skew (an OTM put is richer than flat Black-Scholes says, so the OTM tilt in §6 is the
number most likely to be flattered), bid/ask and slippage (they penalise cheap legs
hardest — precisely the legs the research model preferred), thin OI, IV crush after
entry, and expiry-day gamma. All six need real chains. Run the two commands in §0.1.

---

## What I would and would not change

**Would not:** the delta target, the strike ladder, or the selector. There is no evidence
for any of it, and one clear piece of evidence against the alternative.

**Would, when you are ready to discuss changes:** the two provably redundant gates from
Phase 2 (`OVER_EXTENDED` ⊂ `ENTRY_TRIGGER`, `CONFIDENCE` ⊂ `HTF_TREND`), and then work on
the trigger and the CE/PE asymmetry — that is where the remaining money is, if it is
anywhere. Not one line of that is in this build.

**Open items carried forward:**

* **Phase 2F real-premium repeat — still blocked.** Zero closed real-premium windows.
* **Phase 3 real-chain repeat — no longer blocked by code**, only by data: run
  `chain_provenance.py` and `strike_audit_chain.py` on the live machine and send the JSONs.
* Modelled premiums ignore theta beyond the frozen-IV walk, spread and slippage. All three
  push real expectancy **below** these numbers, and PF 1.09–1.16 is thin enough that costs
  could take it under 1.0. **Nothing here is a P&L forecast.**
* The ₹1,200/trade futures cost is still a placeholder — one contract note fixes it.
