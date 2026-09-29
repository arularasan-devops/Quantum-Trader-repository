"""The frozen definition of the Phase 22 setup. Written before any result.

Read this first. Every threshold below was chosen from the production engine's
own published rules and from geometry arguments, NOT from a scan of outcomes,
and it is frozen: if a later result would be better with a different number,
the number does not move, a second version is created and the first stays in
the report beside it. That is the only defence against re-fitting the same
evidence, and an earlier study of this pool needed it — 231 condition
combinations were searched there and the best of them was still noise.

The setup, in the order the conditions are read:

    1 TREND        the higher timeframe is trending and agrees with the side
    2 STRUCTURE    the regime is trending, not ranging or choppy
    3 PULLBACK     the engine's own PULLBACK / BREAKOUT_RETEST trigger fired
    4 CONTINUATION no trap or fake running against the side, conviction present
    5 MOVEMENT     the plan's own expected move clears the noise it must pay
    6 ROOM         reward:risk is worth a spread at all

A candidate failing any condition is NON_PULLBACK. It is kept, not dropped:
the comparison the study exists to make is against the opportunities this
definition refuses, including the ones the production engine refused too.

Where each condition comes from:

* 1, 2 and 3 are the production engine's own fields (``htf_trend``,
  ``htf_strength``, ``regime``, ``entry_trigger``). Condition 3 is deliberately
  the engine's frozen trigger label rather than a re-derived pullback: the 94
  journal rows that started this study are labelled by that same trigger, so a
  re-derived one would be measuring a different setup and quoting the journal's
  win rate for it.
* 4 uses ``trap_prob`` / ``fake_prob`` / ``conviction_meter``, which is the
  engine's reclaim evidence: a pullback into a running trap is not a pullback
  into continuation.
* 5 is the condition the evidence says everything else failed on. The 1 Sep A+
  cohort risked 1.35 points on a 15.75 premium against a round-trip cost of
  0.72R, and no win rate survives that. So the plan's own expected move must
  clear the instrument's noise by a margin, measured on the underlying where
  the pool lives, and again on the option's real spread in the vehicle layer.
* 6 is the reward:risk floor. The measured median across the plans on file was
  0.25 — risking 1 to make 0.25. A setup cannot be graded through that.

Nothing here mentions a win rate, and nothing here targets a trade count. The
definition is allowed to produce zero candidates on a given day.
"""
from __future__ import annotations

import hashlib
import json

VERSION = "SELECTIVE_PULLBACK_CONTINUATION_V1"

LABEL = "PULLBACK"
OTHER = "NON_PULLBACK"

LONG = "LONG"
SHORT = "SHORT"

# 1 TREND
TRENDING_HTF = ("UP", "DOWN")
MIN_HTF_STRENGTH = 50.0

# 2 STRUCTURE
TRENDING_REGIMES = ("TRENDING", "TREND", "TRENDING_UP", "TRENDING_DOWN",
                    "STRONG_TREND")

# 3 PULLBACK — the engine's own trigger labels, unchanged.
PULLBACK_TRIGGERS = ("PULLBACK", "BREAKOUT_RETEST")

# 4 CONTINUATION
MAX_TRAP_PROB = 25.0
MAX_FAKE_PROB = 25.0
MIN_CONVICTION = 50.0

# 5 MOVEMENT — the plan's target distance must clear the instrument's own noise
# by this multiple, and the stop must sit outside that noise rather than inside
# it. Both are measured in the pool's ``noise_points``.
MIN_MOVE_OVER_NOISE = 1.5
MIN_RISK_OVER_NOISE = 1.0

# 6 ROOM
MIN_REWARD_RISK = 1.5

# Conditions in evaluation order. The name of each is what a refusal reports,
# so a NON_PULLBACK candidate can always say which condition refused it.
CONDITIONS = ("TREND", "STRUCTURE", "PULLBACK", "CONTINUATION", "MOVEMENT",
              "ROOM")


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _text(value: object) -> str:
    return str(value).upper() if isinstance(value, str) else ""


def trend_ok(row: dict) -> bool:
    htf = _text(row.get("htf_trend"))
    strength = _num(row.get("htf_strength"))
    if htf not in TRENDING_HTF or strength is None:
        return False
    side = _text(row.get("side"))
    agrees = (side == LONG and htf == "UP") or (side == SHORT and htf == "DOWN")
    return agrees and strength >= MIN_HTF_STRENGTH


def structure_ok(row: dict) -> bool:
    return _text(row.get("regime")) in TRENDING_REGIMES


def pullback_ok(row: dict) -> bool:
    return _text(row.get("entry_trigger")) in PULLBACK_TRIGGERS


def continuation_ok(row: dict) -> bool:
    trap = _num(row.get("trap_prob"))
    fake = _num(row.get("fake_prob"))
    conviction = _num(row.get("conviction_meter"))
    if trap is not None and trap > MAX_TRAP_PROB:
        return False
    if fake is not None and fake > MAX_FAKE_PROB:
        return False
    return conviction is None or conviction >= MIN_CONVICTION


def expected_move_points(row: dict) -> float | None:
    """The plan's own target distance, in the underlying's points."""
    risk = _num(row.get("risk"))
    rr = _num(row.get("reward_risk"))
    if risk is None or rr is None or risk <= 0:
        return None
    return risk * rr


def movement_ok(row: dict) -> bool:
    noise = _num(row.get("noise_points"))
    move = expected_move_points(row)
    if noise is None or noise <= 0 or move is None:
        return False
    if move < MIN_MOVE_OVER_NOISE * noise:
        return False
    risk = _num(row.get("risk"))
    return risk is not None and risk >= MIN_RISK_OVER_NOISE * noise


def room_ok(row: dict) -> bool:
    rr = _num(row.get("reward_risk"))
    return rr is not None and rr >= MIN_REWARD_RISK


CHECKS = {
    "TREND": trend_ok,
    "STRUCTURE": structure_ok,
    "PULLBACK": pullback_ok,
    "CONTINUATION": continuation_ok,
    "MOVEMENT": movement_ok,
    "ROOM": room_ok,
}


def refusals(row: dict) -> list[str]:
    """Every condition this candidate fails, in definition order."""
    return [name for name in CONDITIONS if not CHECKS[name](row)]


def is_setup(row: dict) -> bool:
    """True when the candidate is a PULLBACK by the frozen definition."""
    return not refusals(row)


def label(row: dict) -> str:
    return LABEL if is_setup(row) else OTHER


def spec() -> dict:
    """The definition as data, for the artefacts and the fingerprint."""
    return {
        "version": VERSION,
        "conditions": list(CONDITIONS),
        "trend": {"htf_trend_in": list(TRENDING_HTF),
                  "min_htf_strength": MIN_HTF_STRENGTH,
                  "must_agree_with_side": True},
        "structure": {"regime_in": list(TRENDING_REGIMES)},
        "pullback": {"entry_trigger_in": list(PULLBACK_TRIGGERS)},
        "continuation": {"max_trap_prob": MAX_TRAP_PROB,
                         "max_fake_prob": MAX_FAKE_PROB,
                         "min_conviction_meter": MIN_CONVICTION},
        "movement": {"min_expected_move_over_noise": MIN_MOVE_OVER_NOISE,
                     "min_risk_over_noise": MIN_RISK_OVER_NOISE},
        "room": {"min_reward_risk": MIN_REWARD_RISK},
    }


def fingerprint() -> str:
    """A hash of the frozen definition, printed on every artefact.

    If this string changes between two reports, the two reports are not
    measuring the same setup and their numbers may not be compared.
    """
    blob = json.dumps(spec(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]
