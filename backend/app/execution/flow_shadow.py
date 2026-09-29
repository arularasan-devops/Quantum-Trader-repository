"""Shadow exit A/B for Flow paper legs — the 0.5R trailing giveback.

The 5-year path-measured exit study graded eleven exit rules on the same 5,752
holdout candidates and only one was both the best and stable across all five
chronological folds:

    trail_giveback_0.5R   exp +0.0582R  PF 1.17  win 65.0%  5/5 folds+
    as_traded             exp +0.0156R  PF 1.03  win 43.4%  3/5 folds+

It also killed the study's own earlier favourite: breakeven-after-0.5R read
+0.238R when estimated from excursions and +0.027R once the bar order was
actually simulated. That is the reason this module is a *shadow* and not a
replacement. A rule that survives 5 years of underlying-only history has still
never been asked whether the option fill agrees, and the exit spread is the one
cost that differs between the two rules. So the live rule keeps trading and this
one is measured beside it on the same legs, same entries, same order count.

**The risk unit.** The study's R is the plan's stop distance. Flow has no stop —
its only risk allowance is ``flow_giveback_points``, so that is the R used here,
and it is written onto every record as ``r_unit_points`` so no reader has to
guess. The shadow therefore arms once the leg is up half an allowance and exits
on a half-allowance giveback from the peak, against the live rule's full one.

Nothing here can close a leg, place an order or alter the live exit. It observes.
"""
from __future__ import annotations

from app.config import settings

RULE = "trail_giveback_0.5R"
GIVEBACK_R = 0.5
ARM_R = 0.5
OPEN = "OPEN"
EXITED = "EXITED"
UNARMED = "NEVER_ARMED"


def r_unit() -> float:
    """The premium points one R is worth for a Flow leg (its giveback allowance)."""
    return max(0.5, float(settings.flow_giveback_points))


def start(entry_premium: float) -> dict:
    unit = r_unit()
    return {
        "rule": RULE,
        "r_unit_points": round(unit, 2),
        "arm_at_premium": round(entry_premium + ARM_R * unit, 2),
        "giveback_allowance_points": round(GIVEBACK_R * unit, 2),
        "armed": False,
        "state": OPEN,
        "exit_premium": None,
        "exit_ts": None,
        "peak_at_exit": None,
        "reason": "",
    }


def step(sh: dict, entry_premium: float, peak_before: float,
         premium: float, now: int) -> None:
    """Advance the shadow one tick. Exits at most once, then stays put.

    ``peak_before`` is the peak as it stood BEFORE this tick's premium, mirroring
    the study's convention that a trail measures off the peak the bar opened with
    — a tick may not both set a new peak and be judged against it.
    """
    if sh.get("state") != OPEN:
        return
    unit = float(sh["r_unit_points"])
    if not sh["armed"]:
        # The trail never tightens before the leg is up half an allowance, so the
        # shadow can never exit at a worse price than the live rule would allow.
        # Arming ends the tick: the study's convention is that a bar cannot both
        # arm a trail and be judged against the level it just created.
        if peak_before - entry_premium >= ARM_R * unit:
            sh["armed"] = True
        return
    level = peak_before - GIVEBACK_R * unit
    if premium <= level:
        sh["state"] = EXITED
        sh["exit_premium"] = round(float(premium), 2)
        sh["exit_ts"] = int(now)
        sh["peak_at_exit"] = round(float(peak_before), 2)
        sh["reason"] = (
            f"gave back {GIVEBACK_R * unit:.1f} pts from a "
            f"{peak_before:.1f} peak"
        )


def settle(sh: dict, entry_premium: float, final_premium: float | None,
           now: int) -> dict:
    """Close the shadow at the live leg's own exit if it never triggered.

    A shadow that never armed is worth exactly what the leg made — that is what
    "the same leg under this rule" means, and it keeps the A/B against the live
    rule honest rather than quietly dropping the legs the rule did not touch.
    """
    out = dict(sh)
    if out.get("state") == OPEN:
        out["state"] = UNARMED if not out["armed"] else OPEN
        out["exit_premium"] = (round(float(final_premium), 2)
                               if final_premium is not None else None)
        out["exit_ts"] = int(now)
        out["reason"] = ("the trail never fired, so this leg is worth what the "
                         "live rule made on it")
    ex = out.get("exit_premium")
    out["points"] = (round(float(ex) - float(entry_premium), 2)
                     if isinstance(ex, (int, float)) else None)
    return out
