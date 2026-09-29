"""The CAS studies and their payloads — §29, §30, §38.

Same three rules as Phase 17, for the same reasons.

**Capture quality is printed before any economics.** A study over legs whose
books were stale is a study of the feed. The daily and strategy reports lead with
the exact/near-exact rate and the reconciliation arithmetic.

**Net, or nothing.** A leg that could not be priced at a real bid is excluded
from every economic figure and counted in the exclusion line, never quietly
dropped.

**A difference is not a finding.** Every cohort prints its sample size beside its
number, every experiment prints how many cells were compared, and the verdict
stays ``REQUIRES_MORE_DATA`` until the §26 minimums exist. With four to eight
CAS expiries in existence, that is what these reports should say — and saying it
is the point of building them now rather than after the conclusions.
"""
from __future__ import annotations

from app.research.phase18 import (
    experiments,
    journal,
    moves,
    overnight,
    quality,
    safety,
    schema,
    validation,
)

REPORTS: tuple[str, ...] = (
    "cas_daily_report",
    "cas_strategy_report",
    "phase18_cas_capture",
    "phase18_cas_vehicle",
    "phase18_cas_ce_pe",
    "phase18_cas_strike",
    "phase18_cas_expiry",
    "phase18_cas_overnight",
    "phase18_cas_paper",
    "phase18_cas_validation",
)


def _f(v: object) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return None if f != f else f


def _quality_states(observations: list[dict]) -> list[str]:
    out: list[str] = []
    for o in observations:
        for lr in o.get("ladder") or []:
            for key in ("ce", "pe"):
                q = lr.get(key)
                out.append(
                    str(q.get("data_quality")) if isinstance(q, dict)
                    else quality.MISSING
                )
    return out


def build_payloads(
    *,
    observations: list[dict],
    paper_rows: list[dict],
    coverage: list[dict],
    overnight_rows: list[dict] | None = None,
    cards: list[dict] | None = None,
) -> dict[str, dict]:
    """Every report payload, from whatever evidence exists. Empty is valid."""
    states = _quality_states(observations)
    gate = quality.gate(states)
    recon = journal.reconcile(
        observations=observations, cards=cards or [], paper_rows=paper_rows,
    )
    ver = validation.verdict(
        paper_rows, match_pct=_f(gate.get("match_rate_pct")),
    )

    capture_payload = {
        "quality": gate,
        "reconciliation": recon,
        "coverage": _coverage(coverage),
        "sessions": sorted(
            {str(o.get("session")) for o in observations if o.get("session")}
        ),
        "instruments": sorted(
            {str(o.get("instrument")) for o in observations if o.get("instrument")}
        ),
        "note": (
            "Read this page before any other. Every economic table downstream "
            "inherits this sample."
        ),
    }

    paper_payload = _paper(paper_rows)
    ce_pe = experiments.ce_vs_pe(observations)
    strike = experiments.strike_distance(observations)
    timing = experiments.entry_timing(observations)
    expiry = experiments.expiry_cohorts(observations)
    onight = overnight.summarise(overnight_rows or [])

    daily = {
        "quality": gate,
        "reconciliation": recon,
        "underlying": moves.underlying_report(observations),
        "premium": moves.premium_report(observations),
        "paper": paper_payload,
        "verdict": ver["verdict"],
        "verdict_reason": ver["reason"],
        "safety": safety.scan()["statement"],
    }

    strategy = {
        "entry_timing": timing,
        "strike_distance": strike,
        "ce_vs_pe": ce_pe,
        "expiry": expiry,
        "overnight": onight,
        "paper": paper_payload,
        "validation": ver,
        "total_comparisons": (
            int(timing["comparisons"]) + int(strike["comparisons"])
            + int(ce_pe["comparisons"]) + int(expiry["comparisons"])
        ),
        "multiple_testing_note": (
            "Cells compared across all four experiments are counted above. At "
            "the 5% level roughly that many divided by twenty will look "
            "significant with no edge present at all, which is why the best cell "
            "is reported as a candidate rather than a finding."
        ),
    }

    return {
        "cas_daily_report": daily,
        "cas_strategy_report": strategy,
        "phase18_cas_capture": capture_payload,
        "phase18_cas_vehicle": _vehicle(paper_rows),
        "phase18_cas_ce_pe": ce_pe,
        "phase18_cas_strike": strike,
        "phase18_cas_expiry": expiry,
        "phase18_cas_overnight": onight,
        "phase18_cas_paper": {**paper_payload, "entry_timing": timing},
        "phase18_cas_validation": ver,
    }


def _coverage(rows: list[dict]) -> dict:
    sessions = sorted({str(r.get("session")) for r in rows if r.get("session")})
    per: dict[str, int] = {}
    for r in rows:
        s = str(r.get("session") or "")
        if s:
            per[s] = per.get(s, 0) + 1
    return {
        "sessions_covered": len(sessions),
        "sessions": sessions,
        "samples_per_session": per,
        "note": (
            "Capture exists only while the app is running. A session missing "
            "from this list was not a quiet session — it was an unobserved one."
        ),
    }


def _paper(rows: list[dict]) -> dict:
    priced = [r for r in rows if r.get("executability") == schema.EXECUTABLE]
    nets = [_f(r.get("net_rupees")) for r in priced]
    nets = [n for n in nets if n is not None]
    theo = [_f(r.get("theoretical_rupees")) for r in priced]
    theo = [t for t in theo if t is not None]
    per_variant: dict[str, dict] = {}
    for v in ("A", "B", "C", "D", "E"):
        sub = [r for r in priced if r.get("strategy_variant") == v]
        vn = [_f(r.get("net_rupees")) for r in sub]
        vn = [n for n in vn if n is not None]
        per_variant[v] = {
            "legs": len(sub),
            "mean_net_rupees": round(sum(vn) / len(vn), 2) if vn else None,
            "win_pct": (
                round(100.0 * sum(1 for n in vn if n > 0) / len(vn), 1)
                if vn else None
            ),
        }
    return {
        "legs": len(rows),
        "priced": len(priced),
        "unpriceable": len(rows) - len(priced),
        "net_total_rupees": round(sum(nets), 2) if nets else None,
        "theoretical_total_rupees": round(sum(theo), 2) if theo else None,
        "vanished_rupees": (
            round(sum(theo) - sum(nets), 2) if nets and theo else None
        ),
        "by_strategy": per_variant,
        "exit_variants": _exit_variants(rows),
        "note": (
            "'Theoretical' marks the leg at its printed price; 'net' is what the "
            "bid would actually have paid after costs. The difference is the "
            "part of a screenshot that cannot be withdrawn."
        ),
    }


def _exit_variants(rows: list[dict]) -> dict:
    out: dict[str, dict] = {}
    for r in rows:
        variants = r.get("exit_variants")
        if not isinstance(variants, dict):
            continue
        for name, res in variants.items():
            if not isinstance(res, dict):
                continue
            bucket = out.setdefault(name, {"legs": 0, "total_points": 0.0,
                                           "wins": 0, "priced": 0})
            bucket["legs"] = int(bucket["legs"]) + 1
            pts = _f(res.get("net_points"))
            if pts is not None:
                bucket["priced"] = int(bucket["priced"]) + 1
                bucket["total_points"] = float(bucket["total_points"]) + pts
                if pts > 0:
                    bucket["wins"] = int(bucket["wins"]) + 1
    for bucket in out.values():
        priced = int(bucket["priced"])
        bucket["mean_points"] = (
            round(float(bucket["total_points"]) / priced, 2) if priced else None
        )
        bucket["win_pct"] = (
            round(100.0 * int(bucket["wins"]) / priced, 1) if priced else None
        )
    return out


def _vehicle(rows: list[dict]) -> dict:
    """What the option actually did, as distinct from what the index did."""
    priced = [r for r in rows if r.get("executability") == schema.EXECUTABLE]
    spreads = [_f(r.get("entry_spread_pct")) for r in priced]
    spreads = [s for s in spreads if s is not None]
    worthless = [r for r in priced if r.get("worthless")]
    return {
        "legs": len(rows),
        "priced": len(priced),
        "median_entry_spread_pct": (
            round(sorted(spreads)[len(spreads) // 2], 2) if spreads else None
        ),
        "expired_worthless_pct": (
            round(100.0 * len(worthless) / len(priced), 1) if priced else None
        ),
        "note": (
            "The 44x screenshots are the same distribution as the legs that go "
            "to zero. Both are counted here; only one of them gets posted."
        ),
    }


# --------------------------------------------------------------- questions
QUESTIONS: tuple[str, ...] = (
    "Does the underlying actually move in the CAS window?",
    "How large is the move, in points, percent and ATR?",
    "Is the direction of the move predictable before 15:15?",
    "Does the move persist to 15:30 or reverse?",
    "Which sub-window carries the move?",
    "Do options respond to the move as delta implies?",
    "What is the option spread during CAS?",
    "Is the printed premium executable at the bid?",
    "How much of the theoretical gain survives the book?",
    "Which strike distance pays best after costs?",
    "Is CE or PE the better vehicle in this window?",
    "Does expiry day behave differently from other days?",
    "Which entry clock is best?",
    "Which exit rule keeps the most of MFE?",
    "How often does a CAS leg expire worthless?",
    "Does holding overnight help or hurt?",
    "How does the gap distribute, and does it decide the result?",
    "What is the net expectancy after brokerage, taxes and slippage?",
    "Does the result survive chronological validation?",
    "Is CAS ready for production?",
)


def _median_points(per_session: list[dict]) -> float | None:
    vals = [_f(p.get("range_points")) for p in per_session]
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals) // 2] if vals else None


def _busiest(per_session: list[dict]) -> str | None:
    """Which five-minute sub-window carries the largest median absolute move."""
    totals: dict[str, list[float]] = {}
    for p in per_session:
        legs = p.get("leg_moves")
        if not isinstance(legs, dict):
            continue
        for label, val in legs.items():
            v = _f(val)
            if v is not None:
                totals.setdefault(str(label), []).append(abs(v))
    if not totals:
        return None
    return max(totals, key=lambda k: sorted(totals[k])[len(totals[k]) // 2])


def _response(prem: dict) -> float | None:
    paths = prem.get("per_path")
    if not isinstance(paths, list):
        return None
    vals = [_f(p.get("premium_per_underlying_pct")) for p in paths
            if isinstance(p, dict)]
    vals = sorted(v for v in vals if v is not None)
    return vals[len(vals) // 2] if vals else None


def answers(payloads: dict[str, dict]) -> list[dict]:
    """One answer per question, each carrying the sample it rests on."""
    daily = payloads.get("cas_daily_report", {})
    strategy = payloads.get("cas_strategy_report", {})
    under = daily.get("underlying") if isinstance(daily.get("underlying"), dict) else {}
    prem = daily.get("premium") if isinstance(daily.get("premium"), dict) else {}
    paper_p = daily.get("paper") if isinstance(daily.get("paper"), dict) else {}
    ver = payloads.get("phase18_cas_validation", {})
    gate = daily.get("quality") if isinstance(daily.get("quality"), dict) else {}

    sessions = int(under.get("measured") or 0) if isinstance(
        under.get("measured"), (int, float)) else 0
    priced = int(paper_p.get("priced") or 0)
    per_sessions = under.get("per_session")
    per_sessions = per_sessions if isinstance(per_sessions, list) else []
    by_rung = prem.get("by_rung") if isinstance(prem.get("by_rung"), dict) else {}
    atm = by_rung.get(schema.ATM) if isinstance(
        by_rung.get(schema.ATM), dict) else {}

    def thin(value: object, n: int, need: int, text: str) -> dict:
        if value is None or n < need:
            return {
                "answer": schema.REQUIRES_MORE_DATA,
                "basis": f"{n} of the {need} observations needed",
            }
        return {"answer": text, "basis": f"{n} observations"}

    out: list[dict] = []
    out.append(thin(_median_points(per_sessions), sessions, 5,
                    f"median {_median_points(per_sessions)} points of range"))
    out.append(thin(under.get("median_abs_move_pct"), sessions, 5,
                    f"{under.get('median_abs_move_pct')}% median absolute move, "
                    f"max {under.get('max_abs_move_pct')}%"))
    out.append({"answer": str(under.get("answer") or schema.REQUIRES_MORE_DATA),
                "basis": f"{sessions} measured instrument-session(s)"})
    out.append(thin(under.get("reversal_pct"), sessions, 5,
                    f"reversed in {under.get('reversal_pct')}% of sessions"))
    out.append(thin(_busiest(per_sessions), sessions, 5,
                    str(_busiest(per_sessions))))
    out.append(thin(_response(prem), sessions, 5,
                    f"median {_response(prem)}% of executable option return per "
                    "1% of underlying move"))
    out.append(thin(atm.get("median_spread_pct"), int(atm.get("n") or 0), 3,
                    f"ATM median {atm.get('median_spread_pct')}% of premium"))
    out.append(thin(paper_p.get("priced"), priced, 20,
                    f"{paper_p.get('priced')} of {paper_p.get('legs')} legs were "
                    "priceable at a real bid"))
    out.append(thin(paper_p.get("vanished_rupees"), priced, 20,
                    f"₹{paper_p.get('vanished_rupees')} of the marked value did "
                    "not survive the book"))
    best_strike = (
        strategy.get("strike_distance", {}).get("by_side", {})
        .get(schema.CE, {}).get("best", {})
    )
    out.append({"answer": str(best_strike.get("cell") or schema.REQUIRES_MORE_DATA),
                "basis": str(best_strike.get("reason") or "")})
    ce_pe_best = strategy.get("ce_vs_pe", {}).get("best", {})
    out.append({"answer": str(ce_pe_best.get("cell") or schema.REQUIRES_MORE_DATA),
                "basis": str(ce_pe_best.get("reason") or "")})
    exp_best = strategy.get("expiry", {}).get("best", {})
    out.append({"answer": str(exp_best.get("cell") or schema.REQUIRES_MORE_DATA),
                "basis": str(exp_best.get("reason") or "")})
    t_best = strategy.get("entry_timing", {}).get("best", {})
    out.append({"answer": str(t_best.get("cell") or schema.REQUIRES_MORE_DATA),
                "basis": str(t_best.get("reason") or "")})
    ev = paper_p.get("exit_variants") if isinstance(
        paper_p.get("exit_variants"), dict) else {}
    best_exit = max(
        (k for k, v in ev.items() if isinstance(v.get("mean_points"), (int, float))),
        key=lambda k: float(ev[k]["mean_points"]), default=None,
    )
    out.append({"answer": best_exit or schema.REQUIRES_MORE_DATA,
                "basis": f"{len(ev)} variants graded on the same legs"})
    vehicle = payloads.get("phase18_cas_vehicle", {})
    out.append(thin(vehicle.get("expired_worthless_pct"), priced, 20,
                    f"{vehicle.get('expired_worthless_pct')}% expired worthless"))
    on = payloads.get("phase18_cas_overnight", {})
    out.append({"answer": str(on.get("verdict") or schema.REQUIRES_MORE_DATA),
                "basis": f"{on.get('resolved', 0)} carried legs resolved"})
    out.append({"answer": str(on.get("verdict") or schema.REQUIRES_MORE_DATA),
                "basis": f"gap band ±{on.get('gap_threshold_pct')}%"})
    out.append(thin(paper_p.get("net_total_rupees"), priced, 20,
                    f"₹{paper_p.get('net_total_rupees')} net over "
                    f"{paper_p.get('priced')} priced legs"))
    out.append({"answer": str(ver.get("verdict") or schema.REQUIRES_MORE_DATA),
                "basis": str(ver.get("reason") or "")})
    out.append({
        "answer": "NO",
        "basis": (
            f"{ver.get('verdict')}. Capture quality {gate.get('match_rate_pct')}%. "
            "Promotion is manual and no order path exists from this phase."
        ),
    })
    return [
        {"question": q, **a} for q, a in zip(QUESTIONS, out, strict=False)
    ]
