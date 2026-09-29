"""Phase 43 — which historically visible mechanism deserves live validation.

Research only. This package reads five-year one-minute history, resolves paper
outcomes with the existing cost model, and labels each predeclared mechanism. It
imports nothing from the order path, writes nothing to the live store and
changes no gate. Phase 41 and Phase 42 are untouched: no module here imports
them, so their fingerprints cannot move.

Three properties do the work, and each exists because its absence has already
produced a wrong answer in this project:

* **the mechanism is declared before it is measured.** Phase 35's room/cost
  bands were cut after seeing the outcomes, and a monotone four-band table was
  produced that turned out to be a tautology. Here every candidate — including
  its arms and its geometry — is written down in :mod:`mechanisms` and hashed by
  :mod:`freeze` before the study runs, and the search is a fixed list rather
  than a sweep;
* **nothing in the ratio may come from after the decision.** Phase 42 exists
  because ``peak_pct / cost_pct`` was read as an entry filter when the peak is
  realised after entry. Every quantity here is computed from the decision bar's
  close or earlier — including the *cost*, which is estimated at the decision
  close rather than taken from the fill it cannot know;
* **a lead is not a promotion.** The strongest label this phase can emit is
  HISTORICAL_LEAD, which means "worth the expense of real executable capture",
  and nothing more. Options in this dataset have no quoted book at all, so a
  five-year option claim would be a cost model reporting itself.
"""
from __future__ import annotations

VERSION = "43.0"

# --- what a candidate can be labelled -------------------------------------
# A lead has to clear all five conditions in `evaluate.status`. Anything that
# clears fewer is REJECTED when the data was there to reject it, and
# REQUIRES_MORE_DATA when the sample rather than the mechanism is what failed.
HISTORICAL_LEAD = "HISTORICAL_LEAD"
REJECTED = "REJECTED"
REQUIRES_MORE_DATA = "REQUIRES_MORE_DATA"
UNMEASURED = "UNMEASURED"

RESEARCH_ONLY = "RESEARCH_ONLY_NOT_PROMOTED"

# --- frozen geometry ------------------------------------------------------
# Inherited from Phase 24's published base-rate geometry sweep rather than
# tuned here: a 1-ATR stop on 1-minute index futures pays close to a full R in
# charges, so it measures the cost model instead of the mechanism. One geometry
# for every candidate keeps the hypothesis count honest — a geometry grid would
# multiply it, and the FDR denominator would have to grow with it.
STOP_ATR = 2.0
T1_R = 1.5

# --- chronological split --------------------------------------------------
# Whole sessions, three windows, in time order. The holdout is read once, at the
# end, by `evaluate.candidate`, and no branch feeds it back into a choice.
DEV_SHARE = 0.6
VAL_SHARE = 0.2

# --- floors ---------------------------------------------------------------
# Below MIN_TRADES a window is reported as too small rather than as a result.
# MIN_SESSIONS keeps a candidate from being carried by a handful of days, and
# MIN_EXPECTANCY_R is the "economically meaningful" bar: a mechanism whose edge
# is a thousandth of R is indistinguishable from the cost model's own rounding.
MIN_TRADES = 100
MIN_SESSIONS = 60
MIN_EXPECTANCY_R = 0.02
# The same bar for a pair candidate, which has no ATR-based R: two basis points
# of the leg notional per trade. Below that, one tick of unmodelled spread on
# either leg erases the whole result.
MIN_EXPECTANCY_BPS = 2.0

# A lead must not be one session's story. The holdout is re-scored with its best
# session removed and with the top 1% of winners removed, and both have to stay
# positive.
MAX_SINGLE_SESSION_SHARE_PCT = 50.0

# --- cost stress ----------------------------------------------------------
# Every one of these is a worsening. The spread is *unmeasured* in this dataset:
# the five-year feed publishes candles, not depth, so the baseline is optimistic
# by exactly one spread and the multipliers stand in for it.
COST_MULTIPLIERS = (1.0, 1.5, 2.0)

# BH false-discovery rate across every predeclared hypothesis in the run, not
# across the ones that happened to look good.
FDR_ALPHA = 0.05

# --- families -------------------------------------------------------------
FAMILY_COST = "A_COST_AWARE_SELECTION"
FAMILY_RELVAL = "B_RELATIVE_VALUE"
FAMILY_VEHICLE = "C_VEHICLE_SELECTION"
FAMILY_REGIME = "D_REGIME_STATE"

FAMILIES = (FAMILY_COST, FAMILY_RELVAL, FAMILY_VEHICLE, FAMILY_REGIME)

# --- what this dataset cannot answer, stated once ------------------------
# Each of these is reported as UNMEASURED with the count behind it. None is
# reported as a negative result, and none is modelled: an assumed option spread
# would make the vehicle comparison a comparison of assumptions.
NO_QUOTED_DEPTH = (
    "the five-year feed publishes one-minute candles with no bid, ask or depth, "
    "so spread and liquidity conditions cannot be measured on it and are not "
    "modelled"
)
NO_OPTION_HISTORY = (
    "no option contract has five-year history in this store, and the captured "
    "option books cover weeks rather than years, so a five-year CE/PE or "
    "futures-versus-option comparison cannot be run at all"
)
NO_BANKNIFTY_HISTORY = (
    "BANKNIFTY has no five-year one-minute series on disk — only recent "
    "captured candles — so the requested NIFTY-versus-BANKNIFTY relative value "
    "cannot be measured, and no proxy is substituted for it"
)
