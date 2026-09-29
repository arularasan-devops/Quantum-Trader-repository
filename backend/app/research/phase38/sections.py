"""Phase 38 §1-§9 — the nine measured sections of the diagnostic.

Every function here takes the *already resolved* Phase 36 rows and returns a
dict. None of them resolves a leg, chooses a horizon, or decides anything: the
horizon arrives as an argument so that no section can improve its own story by
picking a different hold, which is the failure mode a loss diagnostic is most
prone to.

Where a cohort is too small to speak, the cohort says so. That is not caution
for its own sake — with one session, a three-leg DTE bucket will happily show a
+40% mean, and a reader who quotes it has been misled by this document rather
than by the market.
"""
from __future__ import annotations

from app.research.phase35 import (
    CE,
    FUTURES,
    LONG,
    PE,
    SHORT,
    normalize_direction,
)
from app.research.phase36 import (
    DIRECTIONAL,
    ENTRY_CONFIRMATION,
    PREMIUM_BANDS,
)
from app.research.phase36 import tables as p36tables
from app.research.phase38 import (
    COST_DOMINATED,
    ENTRY_IMPROVEMENT_PCT,
    ENTRY_NOT_THE_PROBLEM,
    FUT_PAID_OPTIONS_LOST,
    GIVEBACK,
    INSUFFICIENT_EVIDENCE,
    MIN_COHORT,
    MOVE_TOO_SMALL,
    NO_EARLIER_ENTRY,
    NO_STOP_RECORDED,
    OPTIONS_MOVED_COST_ERASED,
    OTHER,
    SOME_VEHICLE_PAID,
    TOO_EARLY,
    UNDERLYING_MOVE_INSUFFICIENT,
)
from app.research.phase38 import money as p38money

VEHICLES: tuple[str, ...] = (FUTURES, CE, PE)


def directional(rows: list[dict]) -> list[dict]:
    return [r for r in rows if r.get("role") == DIRECTIONAL]


def _mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def _pf(xs: list[float]) -> float | None:
    gains = sum(x for x in xs if x > 0)
    losses = -sum(x for x in xs if x < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return round(gains / losses, 4)


def _cohort(n: int) -> str | None:
    return INSUFFICIENT_EVIDENCE if n < MIN_COHORT else None


# ---------------------------------------------------------------------------
# §1 — GROSS -> COSTS -> NET per vehicle
# ---------------------------------------------------------------------------
def vehicle_pnl(rows: list[dict], *, horizon: str) -> dict:
    """§1 — one row per vehicle, in rupees, with the cost line itemised.

    ``net_r`` is deliberately absent rather than invented: a Phase 36 triple
    records no stop distance, so there is no R to divide by. The percent-of-entry
    net is reported in its place and the reason is carried in the payload.
    """
    out: dict[str, dict] = {}
    for v in VEHICLES:
        subset = [r for r in directional(rows) if r.get("vehicle") == v]
        ms, skipped = p38money.money_rows(subset, horizon)
        nets = [float(m["net_rupees"]) for m in ms]
        net_pcts = [
            float(m["net_pct"]) for m in ms
            if isinstance(m.get("net_pct"), (int, float))
        ]
        out[v] = {
            "horizon": horizon,
            "trades": len(ms),
            "legs_without_lot_size": skipped,
            "gross_rupees": round(sum(float(m["gross_rupees"]) for m in ms), 2),
            "spread_rupees": _component(ms, "SPREAD"),
            "brokerage_rupees": _component(ms, "BROKERAGE"),
            "statutory_rupees": _component(ms, "STATUTORY"),
            "slippage_rupees": _component(ms, "SLIPPAGE"),
            "cost_rupees": round(sum(float(m["cost_rupees"]) for m in ms), 2),
            "net_rupees": round(sum(nets), 2),
            "net_rupees_per_trade": _mean(nets),
            "net_mean_pct": _mean(net_pcts),
            "net_r": None,
            "net_r_reason": NO_STOP_RECORDED,
            "win_pct": (
                round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 2)
                if nets else None
            ),
            "profit_factor": _pf(nets),
            "insufficient": _cohort(len(ms)),
        }
    return out


def _component(ms: list[dict], label: str) -> float:
    return round(sum(
        float(m["components_rupees"].get(label) or 0.0) for m in ms
    ), 2)


# ---------------------------------------------------------------------------
# §2 — move vs cost, one category per leg
# ---------------------------------------------------------------------------
def categorise(m: dict) -> str:
    """The frozen §2 rule. Mutually exclusive, and it covers every leg.

    Read it as a sequence of questions asked of the leg itself:
    was there any favourable excursion at all; was it bigger than this leg's own
    round trip; did the horizon keep any of it.
    """
    mfe = m.get("mfe_pct")
    cost = m.get("cost_pct")
    net = m.get("net_rupees")
    if isinstance(net, (int, float)) and float(net) >= 0:
        return OTHER
    if not isinstance(mfe, (int, float)) or float(mfe) <= 0:
        return MOVE_TOO_SMALL
    if isinstance(cost, (int, float)) and float(mfe) < float(cost):
        return COST_DOMINATED
    return GIVEBACK


def move_vs_cost(rows: list[dict], *, horizon: str) -> dict:
    """§2 — counts, rupees and the move/cost ratio behind each category."""
    ms, skipped = p38money.money_rows(directional(rows), horizon)
    buckets: dict[str, list[dict]] = {}
    ratios: list[float] = []
    for m in ms:
        m = dict(m)
        mfe, cost = m.get("mfe_pct"), m.get("cost_pct")
        ratio = (
            round(float(mfe) / float(cost), 4)
            if isinstance(mfe, (int, float)) and isinstance(cost, (int, float))
            and float(cost) > 0 else None
        )
        m["move_over_cost"] = ratio
        if ratio is not None:
            ratios.append(ratio)
        buckets.setdefault(categorise(m), []).append(m)
    return {
        "horizon": horizon,
        "legs": len(ms),
        "legs_without_lot_size": skipped,
        "move_over_cost_mean": _mean(ratios),
        "move_over_cost_median": _median(ratios),
        "cleared_own_cost_pct": (
            round(100.0 * sum(1 for x in ratios if x >= 1.0) / len(ratios), 2)
            if ratios else None
        ),
        "by_category": {
            cat: {
                "n": len(group),
                "share_pct": round(100.0 * len(group) / len(ms), 2) if ms else None,
                "net_rupees": round(sum(
                    float(g["net_rupees"]) for g in group
                ), 2),
                "gross_rupees": round(sum(
                    float(g["gross_rupees"]) for g in group
                ), 2),
                "cost_rupees": round(sum(
                    float(g["cost_rupees"]) for g in group
                ), 2),
                "mfe_mean_pct": _mean([
                    float(g["mfe_pct"]) for g in group
                    if isinstance(g.get("mfe_pct"), (int, float))
                ]),
                "by_vehicle": _count_by(group, "vehicle"),
            }
            for cat, group in sorted(buckets.items())
        },
        "definitions": {
            MOVE_TOO_SMALL: "no favourable excursion at all at this horizon",
            COST_DOMINATED: "a favourable excursion smaller than the leg's own "
                            "round-trip cost",
            GIVEBACK: "excursion cleared the round trip and the horizon still "
                      "ended negative",
            OTHER: "net was not negative",
        },
    }


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    mid = len(s) // 2
    return round(s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2.0, 4)


def _count_by(group: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for g in group:
        k = str(g.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


# ---------------------------------------------------------------------------
# §3 — hold time
# ---------------------------------------------------------------------------
def hold_time(rows: list[dict]) -> dict:
    """§3 — every vehicle at every horizon, and which horizon lost least.

    "Lost least" is the phrasing on purpose. With one session and no
    out-of-sample confirmation, the horizon with the highest net is the horizon
    that happened to be best on one day; calling it an exit would be exactly the
    hindsight Phase 36 §22 forbids.
    """
    out: dict[str, dict] = {}
    for v in VEHICLES:
        subset = [r for r in directional(rows) if r.get("vehicle") == v]
        per: dict[str, dict] = {}
        for h in p36tables.HORIZON_KEYS:
            ms, _ = p38money.money_rows(subset, h)
            nets = [float(m["net_rupees"]) for m in ms]
            per[h] = {
                "n": len(ms),
                "gross_rupees": round(sum(
                    float(m["gross_rupees"]) for m in ms
                ), 2),
                "net_rupees": round(sum(nets), 2),
                "net_rupees_per_trade": _mean(nets),
                "profit_factor": _pf(nets),
                "win_pct": (
                    round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 2)
                    if nets else None
                ),
                "mfe_mean_pct": _mean([
                    float(m["mfe_pct"]) for m in ms
                    if isinstance(m.get("mfe_pct"), (int, float))
                ]),
                "mae_mean_pct": _mean([
                    float(m["mae_pct"]) for m in ms
                    if isinstance(m.get("mae_pct"), (int, float))
                ]),
                "insufficient": _cohort(len(ms)),
            }
        eligible = {
            h: s for h, s in per.items()
            if s["net_rupees_per_trade"] is not None and not s["insufficient"]
        }
        best = max(
            eligible, key=lambda h: eligible[h]["net_rupees_per_trade"],
            default=None,
        )
        any_positive = [
            h for h, s in eligible.items()
            if (s["net_rupees_per_trade"] or 0.0) > 0
        ]
        out[v] = {
            "by_horizon": per,
            "least_negative_horizon": best,
            "least_negative_net_per_trade": (
                eligible[best]["net_rupees_per_trade"] if best else None
            ),
            "any_horizon_positive": sorted(any_positive),
            "note": (
                "the best horizon here is descriptive of one session and is "
                "not a validated exit"
            ),
        }
    return out


# ---------------------------------------------------------------------------
# §4 — giveback
# ---------------------------------------------------------------------------
def giveback(rows: list[dict], *, horizon: str) -> dict:
    """§4 — MONEY AVAILABLE vs MONEY CAPTURED, on the losing legs.

    Available is the favourable excursion the leg actually printed; captured is
    what the horizon held. Both are gross of cost in the top two lines and net
    at the bottom, because a reader wants to know first whether the money was
    ever there and only then whether the costs ate it.

    Only losing legs that were **ever in front** contribute to the money lines.
    A leg that went adverse from the first tick offered nothing and therefore
    gave nothing back; counting its loss here would manufacture a giveback
    figure out of an adverse move and point the next fix at the exit rule.
    Those legs are counted separately as ``never_went_green_n``.
    """
    out: dict[str, dict] = {}
    examples: list[dict] = []
    for v in VEHICLES:
        subset = [r for r in directional(rows) if r.get("vehicle") == v]
        ms, _ = p38money.money_rows(subset, horizon)
        by_obs = {r["obs_id"]: r for r in subset}
        all_losers = [m for m in ms if float(m["net_rupees"]) < 0]
        losers = [
            m for m in all_losers
            if isinstance(m.get("mfe_rupees"), (int, float))
            and float(m["mfe_rupees"]) > 0
        ]
        available = round(sum(float(m["mfe_rupees"] or 0.0) for m in losers), 2)
        captured = round(sum(float(m["gross_rupees"]) for m in losers), 2)
        gaves: list[float] = []
        returned = turned = went_green = 0
        for m in losers:
            g = (by_obs.get(m["obs_id"]) or {}).get("giveback") or {}
            if isinstance(g.get("pct_of_mfe_given_back"), (int, float)):
                gaves.append(float(g["pct_of_mfe_given_back"]))
            if (g.get("peak_pct") or 0) > 0:
                went_green += 1
                returned += 1 if g.get("returned_to_entry") else 0
                turned += 1 if g.get(
                    "turned_negative_after_profitable",
                ) else 0
            if len(examples) < 20 and (g.get("peak_pct") or 0) > 0:
                examples.append({
                    "vehicle": v,
                    "obs_id": m["obs_id"],
                    "mfe_pct": g.get("peak_pct"),
                    "time_to_peak_min": g.get("time_to_peak_min"),
                    "profit_at_peak_rupees": m.get("mfe_rupees"),
                    "profit_at_exit_rupees": m.get("gross_rupees"),
                    "pct_of_mfe_returned": g.get("pct_of_mfe_given_back"),
                    "returned_to_entry": g.get("returned_to_entry"),
                    "turned_negative_after_profitable": g.get(
                        "turned_negative_after_profitable",
                    ),
                })
        out[v] = {
            "losing_trades": len(all_losers),
            "losing_trades_that_went_green": len(losers),
            "never_went_green_n": len(all_losers) - len(losers),
            "money_available_rupees": available,
            "money_captured_rupees": captured,
            "money_given_back_rupees": round(available - captured, 2),
            "capture_rate_pct": (
                round(100.0 * captured / available, 2) if available > 0 else None
            ),
            "pct_of_mfe_returned_median": _median(gaves),
            "went_green_n": went_green,
            "returned_to_entry_pct": (
                round(100.0 * returned / went_green, 2) if went_green else None
            ),
            "turned_negative_pct": (
                round(100.0 * turned / went_green, 2) if went_green else None
            ),
            "insufficient": _cohort(len(all_losers)),
        }
    return {
        "horizon": horizon,
        "by_vehicle": out,
        "examples": examples,
        "note": (
            "the money lines cover only losing legs that were in front at some "
            "point; a leg that never went green offered nothing to give back "
            "and is counted under never_went_green_n"
        ),
    }


# ---------------------------------------------------------------------------
# §5 — CE vs PE at the same instant
# ---------------------------------------------------------------------------
def ce_vs_pe(rows: list[dict], *, horizon: str) -> dict:
    """§5 — the same-timestamp CE/PE pair, five ways.

    Both sides of the pair are used regardless of role, because the question is
    about the two option contracts at one instant, not about which one the view
    endorsed. The directional split is reported separately in §6 so a reader
    cannot mistake "the put moved more" for "the put was the right trade".
    """
    by_obs: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r.get("vehicle") in (CE, PE) and r.get("entry_price") is not None:
            by_obs.setdefault(r["obs_id"], {})[r["vehicle"]] = r
    wins = {
        k: {CE: 0, PE: 0, "TIE": 0, "UNMEASURED": 0}
        for k in ("mfe", "mae", "cost", "retention", "net")
    }
    pairs = 0
    for group in by_obs.values():
        ce, pe = group.get(CE), group.get(PE)
        if ce is None or pe is None:
            continue
        mce = p38money.leg_money(ce, horizon)
        mpe = p38money.leg_money(pe, horizon)
        if mce is None or mpe is None:
            continue
        pairs += 1
        _score(wins["mfe"], mce.get("mfe_pct"), mpe.get("mfe_pct"))
        # A better MAE is a *shallower* drawdown; MAE is negative, so the
        # larger number is the better one and no sign flip is needed.
        _score(wins["mae"], mce.get("mae_pct"), mpe.get("mae_pct"))
        _score(wins["cost"], _neg(mce.get("cost_pct")), _neg(mpe.get("cost_pct")))
        _score(wins["retention"], _retention(ce), _retention(pe))
        _score(wins["net"], mce.get("net_rupees"), mpe.get("net_rupees"))
    return {
        "horizon": horizon,
        "pairs": pairs,
        "wins": wins,
        "insufficient": _cohort(pairs),
        "note": (
            "one session; a majority here is a description of this day and "
            "neither side is called superior"
        ),
    }


def _neg(x):
    return -float(x) if isinstance(x, (int, float)) else None


def _retention(row: dict) -> float | None:
    g = row.get("giveback") or {}
    val = g.get("pct_of_mfe_given_back")
    return 100.0 - float(val) if isinstance(val, (int, float)) else None


def _score(bucket: dict, a, b) -> None:
    """Score one comparison, counting an unmeasurable one rather than dropping
    it: a retention row that silently reads 0-0-0 looks like a dead heat when
    it means neither side ever printed a favourable excursion to retain."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        bucket["UNMEASURED"] += 1
        return
    if abs(float(a) - float(b)) < 1e-9:
        bucket["TIE"] += 1
    elif float(a) > float(b):
        bucket[CE] += 1
    else:
        bucket[PE] += 1


# ---------------------------------------------------------------------------
# §6 — futures vs options on the identical opportunity
# ---------------------------------------------------------------------------
def futures_vs_options(rows: list[dict], *, horizon: str) -> dict:
    """§6 — how the three directional expressions of one instant compare."""
    by_obs: dict[str, dict[str, dict]] = {}
    for r in directional(rows):
        if r.get("entry_price") is not None:
            by_obs.setdefault(r["obs_id"], {})[r["vehicle"]] = r
    counts = {p: 0 for p in (
        FUT_PAID_OPTIONS_LOST, OPTIONS_MOVED_COST_ERASED,
        UNDERLYING_MOVE_INSUFFICIENT, SOME_VEHICLE_PAID,
    )}
    complete = 0
    option_moved_more = 0
    for group in by_obs.values():
        fut = group.get(FUTURES)
        opt = next((group[v] for v in (CE, PE) if v in group), None)
        if fut is None or opt is None:
            continue
        mf = p38money.leg_money(fut, horizon)
        mo = p38money.leg_money(opt, horizon)
        if mf is None or mo is None:
            continue
        complete += 1
        fut_net = float(mf["net_rupees"])
        opt_net = float(mo["net_rupees"])
        fut_mfe = float(mf.get("mfe_pct") or 0.0)
        opt_mfe = float(mo.get("mfe_pct") or 0.0)
        if opt_mfe > fut_mfe:
            option_moved_more += 1
        if fut_net > 0 or opt_net > 0:
            counts[SOME_VEHICLE_PAID] += 1
        if fut_net > 0 >= opt_net:
            counts[FUT_PAID_OPTIONS_LOST] += 1
        if opt_net < 0 and opt_mfe > fut_mfe and opt_mfe > 0:
            counts[OPTIONS_MOVED_COST_ERASED] += 1
        if fut_mfe <= float(mf.get("cost_pct") or 0.0) and fut_net <= 0:
            counts[UNDERLYING_MOVE_INSUFFICIENT] += 1
    return {
        "horizon": horizon,
        "complete_triples": complete,
        "patterns": counts,
        "pattern_share_pct": {
            k: (round(100.0 * v / complete, 2) if complete else None)
            for k, v in counts.items()
        },
        "option_excursion_beat_futures_pct": (
            round(100.0 * option_moved_more / complete, 2) if complete else None
        ),
        "insufficient": _cohort(complete),
        "note": (
            "patterns are not exclusive: an instant where the option moved "
            "further and still lost is counted in both its own pattern and, if "
            "the future paid, in FUTURES_PAID_OPTIONS_LOST"
        ),
    }


# ---------------------------------------------------------------------------
# §7 — entry timing
# ---------------------------------------------------------------------------
def entry_timing(
    by_offset: dict[str, list[dict]], *, horizon: str,
) -> dict:
    """§7 — immediate vs 1-minute vs 2-minute delayed, counterfactual only.

    The task asks for bars; the capture is quote-by-quote, so the offsets are
    the minutes Phase 36 already resolves — 1 and 2 — and the label says minutes
    rather than bars so nobody reads a bar rule into it.
    """
    per: dict[str, dict] = {}
    for label, rows in sorted(by_offset.items(), key=lambda kv: str(kv[0])):
        ms, _ = p38money.money_rows(directional(rows), horizon)
        nets = [float(m["net_rupees"]) for m in ms]
        per[str(label)] = {
            "n": len(ms),
            "net_rupees": round(sum(nets), 2),
            "net_rupees_per_trade": _mean(nets),
            "net_mean_pct": _mean([
                float(m["net_pct"]) for m in ms
                if isinstance(m.get("net_pct"), (int, float))
            ]),
            "win_pct": (
                round(100.0 * sum(1 for x in nets if x > 0) / len(nets), 2)
                if nets else None
            ),
            "insufficient": _cohort(len(ms)),
        }
    base = per.get("0.0") or per.get("0")
    base_pct = (base or {}).get("net_mean_pct")
    improved: list[dict] = []
    if isinstance(base_pct, (int, float)):
        for label, s in per.items():
            if label in ("0.0", "0"):
                continue
            got = s.get("net_mean_pct")
            if isinstance(got, (int, float)) and not s["insufficient"] and (
                float(got) - float(base_pct)
            ) > ENTRY_IMPROVEMENT_PCT:
                improved.append({
                    "offset": label,
                    "net_mean_pct": got,
                    "improvement_pct": round(float(got) - float(base_pct), 4),
                })
    improved.sort(key=lambda r: -r["improvement_pct"])
    label = TOO_EARLY if improved else ENTRY_NOT_THE_PROBLEM
    return {
        "horizon": horizon,
        "by_offset": per,
        "baseline_net_mean_pct": base_pct,
        "improved_by_waiting": improved,
        "label": label,
        "too_late_note": NO_EARLIER_ENTRY,
        "confirmation_offset": ENTRY_CONFIRMATION,
        "note": (
            "counterfactual; a delayed entry that could not be filled is a "
            "smaller sample and is reported with its own n rather than averaged "
            "into the immediate entry"
        ),
    }


# ---------------------------------------------------------------------------
# §8 — premium bands, §9 — DTE and moneyness
# ---------------------------------------------------------------------------
def premium_bands(rows: list[dict], *, horizon: str) -> dict:
    """§8 — is the Phase 34 cost wall visible again in this session?

    The wall is defined as it was there: cost consuming most of the measured
    excursion in the cheapest band. It is reported per band with the sample, and
    a band under the floor is not allowed to be the evidence for it.
    """
    cut = p36tables.by_premium_band(rows, horizon=horizon)
    out: dict[str, dict] = {}
    for band, body in cut.items():
        per: dict[str, dict] = {}
        for v in (CE, PE):
            stats = (body.get("by_vehicle") or {}).get(v) or {}
            subset = [
                r for r in directional(rows)
                if r.get("vehicle") == v
                and p36tables.band_of(r.get("premium"), PREMIUM_BANDS) == band
            ]
            ms, _ = p38money.money_rows(subset, horizon)
            nets = [float(m["net_rupees"]) for m in ms]
            per[v] = {
                "trades": stats.get("n"),
                "spread_pct_mean": body.get("spread_pct_mean"),
                "gross_mean_pct": stats.get("gross_mean_pct"),
                "cost_mean_pct": stats.get("cost_mean_pct"),
                "net_mean_pct": stats.get("net_mean_pct"),
                "net_rupees": round(sum(nets), 2),
                "profit_factor": stats.get("profit_factor"),
                "mfe_mean_pct": stats.get("mfe_mean_pct"),
                "mae_mean_pct": stats.get("mae_mean_pct"),
                "cost_over_mfe_pct": _ratio(
                    stats.get("cost_mean_pct"), stats.get("mfe_mean_pct"),
                ),
                "insufficient": _cohort(int(stats.get("n") or 0)),
            }
        out[band] = per
    wall = [
        band for band, per in out.items()
        for v, s in per.items()
        if not s["insufficient"] and isinstance(
            s.get("cost_over_mfe_pct"), (int, float),
        ) and float(s["cost_over_mfe_pct"]) >= 100.0
    ]
    return {
        "horizon": horizon,
        "by_band": out,
        "cost_wall_bands": sorted(set(wall)),
        "cost_wall_definition": (
            "mean round-trip cost at or above the mean favourable excursion in "
            "that band, on a cohort above the evidence floor"
        ),
    }


def _ratio(cost, mfe) -> float | None:
    if not isinstance(cost, (int, float)) or not isinstance(mfe, (int, float)):
        return None
    if float(mfe) <= 0:
        return float("inf")
    return round(100.0 * float(cost) / float(mfe), 2)


def dte_and_moneyness(rows: list[dict], *, horizon: str) -> dict:
    """§9 — the two option cohort tables, each cohort flagged for sample."""
    def flagged(cut: dict) -> dict:
        out: dict[str, dict] = {}
        for label, body in cut.items():
            per = {}
            for v, stats in (body.get("by_vehicle") or {}).items():
                n = int(stats.get("n") or 0)
                per[v] = {
                    "trades": n,
                    "net_mean_pct": stats.get("net_mean_pct"),
                    "gross_mean_pct": stats.get("gross_mean_pct"),
                    "cost_mean_pct": stats.get("cost_mean_pct"),
                    "win_pct": stats.get("win_pct"),
                    "profit_factor": stats.get("profit_factor"),
                    "insufficient": _cohort(n),
                }
            out[label] = {"n": body.get("n"), "by_vehicle": per}
        return out

    return {
        "horizon": horizon,
        "dte": flagged(p36tables.by_dte(rows, horizon=horizon)),
        "moneyness": flagged(p36tables.by_moneyness(rows, horizon=horizon)),
        "note": (
            "one session: most of these cohorts are below the floor and are "
            "printed for completeness, not for reading"
        ),
    }


def direction_split(rows: list[dict], *, horizon: str) -> dict:
    """Context for §6: LONG views used FUTURES/CE, SHORT views FUTURES/PE."""
    out: dict[str, dict] = {}
    for view, side in ((LONG, CE), (SHORT, PE)):
        subset = [
            r for r in directional(rows)
            if normalize_direction(r.get("direction")) == view
        ]
        ms, _ = p38money.money_rows(subset, horizon)
        nets = [float(m["net_rupees"]) for m in ms]
        out[view] = {
            "option_side": side,
            "legs": len(ms),
            "net_rupees": round(sum(nets), 2),
            "net_rupees_per_trade": _mean(nets),
            "insufficient": _cohort(len(ms)),
        }
    return out
