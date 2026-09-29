"""Chronological validation and the promotion gate — §25, §26, §36.

The default answer here is ``REQUIRES_MORE_DATA``, and on the day this ships it
is the *correct* answer. CAS began on 3 August; there are a handful of expiries
of real evidence in existence, and no amount of analysis creates more of them.

Three rules, all learned the hard way from the five-year study:

* **Splits are chronological, never random.** Development is the earliest
  sessions, validation the middle, holdout the latest. A random split of intraday
  rows leaks tomorrow into today.
* **Whole sessions, never rows.** Legs from one session share a market, so
  splitting inside a session puts the same event on both sides of the line.
* **Nothing before 3 August counts.** Rows earlier than that are labelled
  ``PRE_CAS_REGIME`` and excluded from every statistic — the closing mechanism was
  different, so those sessions are evidence about a market that no longer exists.
"""
from __future__ import annotations

from app.research.phase18 import quality, schema

# §26 minimums before any verdict other than REQUIRES_MORE_DATA is possible.
MIN_RESOLVED = 100
MIN_SESSIONS = 20
MIN_EXPIRY_SESSIONS = 5
MIN_MATCH_PCT = quality.MATCH_TARGET_PCT

DEVELOPMENT = "DEVELOPMENT"
VALIDATION = "VALIDATION"
HOLDOUT = "HOLDOUT"


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def regime_of(session_date: str) -> str:
    """PRE_CAS_REGIME for anything before the auction existed."""
    return (
        schema.CAS_REGIME if session_date >= schema.CAS_START_DATE
        else schema.PRE_CAS_REGIME
    )


def partition(rows: list[dict]) -> dict:
    """Split resolved legs into three chronological folds by whole session."""
    cas_rows, pre = [], []
    for r in rows:
        s = str(r.get("session") or "")
        (cas_rows if s and regime_of(s) == schema.CAS_REGIME else pre).append(r)

    sessions = sorted({str(r.get("session")) for r in cas_rows if r.get("session")})
    n = len(sessions)
    if n < 3:
        return {
            "folds": {DEVELOPMENT: cas_rows, VALIDATION: [], HOLDOUT: []},
            "fold_sessions": {DEVELOPMENT: sessions, VALIDATION: [], HOLDOUT: []},
            "excluded_pre_cas": len(pre),
            "note": (
                f"{n} CAS session(s) — too few to split chronologically; "
                "everything is development until there are more."
            ),
        }
    a, b = int(n * 0.5), int(n * 0.75)
    groups = {
        DEVELOPMENT: set(sessions[:a]),
        VALIDATION: set(sessions[a:b]),
        HOLDOUT: set(sessions[b:]),
    }
    folds = {k: [r for r in cas_rows if str(r.get("session")) in v]
             for k, v in groups.items()}
    return {
        "folds": folds,
        "fold_sessions": {k: sorted(v) for k, v in groups.items()},
        "excluded_pre_cas": len(pre),
        "note": (
            "Chronological, split on whole sessions: the holdout is the latest "
            "sessions and is read once."
        ),
    }


def stats(rows: list[dict]) -> dict:
    """Net expectancy, PF and drawdown over resolved, priced legs only."""
    priced = [r for r in rows if r.get("executability") == schema.EXECUTABLE]
    nets = [_f(r.get("net_rupees")) for r in priced]
    nets = [n for n in nets if n is not None]
    rs = [_f(r.get("net_r")) for r in priced]
    rs = [r for r in rs if r is not None]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]

    equity, peak, dd = 0.0, 0.0, 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        dd = min(dd, equity - peak)

    return {
        "legs": len(rows),
        "priced": len(priced),
        "unpriceable": len(rows) - len(priced),
        "net_expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "net_total_rupees": round(sum(nets), 2) if nets else None,
        "mean_net_rupees": round(sum(nets) / len(nets), 2) if nets else None,
        "gross_profit_rupees": round(sum(wins), 2) if wins else 0.0,
        "gross_loss_rupees": round(abs(sum(losses)), 2) if losses else 0.0,
        "profit_factor": (
            round(sum(wins) / abs(sum(losses)), 3) if wins and losses else None
        ),
        "profit_factor_note": (
            "undefined — no losing leg in this fold"
            if wins and not losses else None
        ),
        "win_pct": (
            round(100.0 * len(wins) / len(nets), 1) if nets else None
        ),
        "max_drawdown_rupees": round(dd, 2) if nets else None,
        "sessions": len({str(r.get("session")) for r in rows if r.get("session")}),
    }


def evidence(rows: list[dict], *, match_pct: float | None) -> dict:
    """Whether §26's minimum evidence exists at all. Usually it does not."""
    resolved = [
        r for r in rows
        if r.get("status") in schema.CLOSED
        or r.get("executability") == schema.EXECUTABLE
    ]
    sessions = {str(r.get("session")) for r in resolved if r.get("session")}
    expiry_sessions = {
        str(r.get("session")) for r in resolved
        if r.get("expiry_class") == schema.EXPIRY_DAY and r.get("session")
    }
    checks = {
        "resolved_legs": {
            "have": len(resolved), "need": MIN_RESOLVED,
            "ok": len(resolved) >= MIN_RESOLVED,
        },
        "distinct_sessions": {
            "have": len(sessions), "need": MIN_SESSIONS,
            "ok": len(sessions) >= MIN_SESSIONS,
        },
        "expiry_sessions": {
            "have": len(expiry_sessions), "need": MIN_EXPIRY_SESSIONS,
            "ok": len(expiry_sessions) >= MIN_EXPIRY_SESSIONS,
        },
        "capture_quality_pct": {
            "have": match_pct, "need": MIN_MATCH_PCT,
            "ok": match_pct is not None and match_pct >= MIN_MATCH_PCT,
        },
    }
    return {
        "checks": checks,
        "all_met": all(c["ok"] for c in checks.values()),
        "missing": [k for k, c in checks.items() if not c["ok"]],
    }


def _pf_ok(fold: dict) -> bool:
    pf = fold.get("profit_factor")
    if isinstance(pf, (int, float)):
        return float(pf) > 1.0
    gp = fold.get("gross_profit_rupees")
    gl = fold.get("gross_loss_rupees")
    return (
        isinstance(gp, (int, float)) and float(gp) > 0
        and isinstance(gl, (int, float)) and float(gl) == 0.0
    )


def verdict(rows: list[dict], *, match_pct: float | None) -> dict:
    """The §36 promotion gate. Nothing here promotes anything automatically."""
    ev = evidence(rows, match_pct=match_pct)
    part = partition(rows)
    folds = {k: stats(v) for k, v in part["folds"].items()}

    out: dict[str, object] = {
        "strategy": schema.STRATEGY,
        "regime_start": schema.CAS_START_DATE,
        "excluded_pre_cas_rows": part["excluded_pre_cas"],
        "fold_sessions": part["fold_sessions"],
        "folds": folds,
        "evidence": ev,
        "promotion_is_manual": True,
        "note": (
            "A verdict is a research statement. Nothing in this codebase promotes "
            "a CAS setup into production, and no path exists from this module to "
            "an order."
        ),
    }

    if not ev["all_met"]:
        out.update({
            "verdict": schema.REQUIRES_MORE_DATA,
            "reason": (
                "Missing: " + ", ".join(ev["missing"]) + ". CAS began on "
                f"{schema.CAS_START_DATE}, so this is a question of elapsed "
                "sessions, not of analysis."
            ),
        })
        return out

    dev, val, hold = folds[DEVELOPMENT], folds[VALIDATION], folds[HOLDOUT]
    exp = [f.get("net_expectancy_r") for f in (dev, val, hold)]
    positive = all(isinstance(e, (int, float)) and float(e) > 0 for e in exp)
    # PF is undefined in a fold with no losing leg. That is a stronger result
    # than PF > 1, not a missing one, so it passes rather than blocking.
    pf_ok = all(_pf_ok(f) for f in (dev, val, hold))

    if positive and pf_ok:
        out.update({
            "verdict": schema.CAS_PRODUCTION_CANDIDATE,
            "reason": (
                "Positive net expectancy and PF > 1 in all three chronological "
                "folds, on bid-executed, costed legs. This is a candidate for "
                "human review — not an instruction to trade it."
            ),
        })
    elif isinstance(hold.get("net_expectancy_r"), (int, float)) and float(
        hold["net_expectancy_r"]
    ) <= 0:
        out.update({
            "verdict": schema.FAILS,
            "reason": (
                "Net expectancy is not positive on the latest untouched "
                "sessions. Whatever the earlier folds show, the strategy did not "
                "survive out-of-sample."
            ),
        })
    else:
        out.update({
            "verdict": schema.REQUIRES_MORE_DATA,
            "reason": "Folds disagree; more sessions are needed to separate "
                      "signal from sample.",
        })
    return out
