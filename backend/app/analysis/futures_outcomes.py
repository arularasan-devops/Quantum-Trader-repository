"""Futures shadow outcomes — Phase 12 §12, RESEARCH ONLY.

The futures research card has been published for weeks and never measured: 95
episodes on 25 Aug produced 7 valid plans and zero recorded results, so "is
futures better than options on the same idea" has been unanswerable rather than
answered badly. This follows every ``VALID_FUTURES_PLAN`` from its stated entry
to a stop, a target or the end of the session, and records what happened.

In **index points**, never in premium and never in R borrowed from the option
book: one point is ``lot_size`` rupees and the two vehicles are never summed.

There is **no order path here of any kind** — paper or live. Nothing in this
module places, sizes for, or proposes a trade; it observes the quoted price and
writes a row. The futures paper *tool* is a separate, disabled-by-default module
and this is not it.

Recorded per plan: entry, stop, T1/T2/T3, MFE, MAE, target-before-stop,
time-to-target, time-to-stop, the spread as quoted at entry, gross R and net R
after the modelled round trip.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.models import FuturesSignalCard

VALID = "VALID_FUTURES_PLAN"
LOG_NAME = "futures_shadow_outcomes.jsonl"
REPORT_JSON = "futures_paper_outcomes.json"

LONG = "LONG"
SHORT = "SHORT"

T1, T2, T3, STOP = "T1", "T2", "T3", "STOP"
TIMEOUT = "TIMEOUT"
SESSION_END = "SESSION_END"

TARGET_FIRST = "TARGET_FIRST"
STOP_FIRST = "STOP_FIRST"
NEITHER = "NEITHER"

# Phase 12A §5 lifecycle. A futures result that appears as one RESOLVED row
# cannot say whether the sample is small because plans are rare or because
# following them fails somewhere after the plan, which is exactly the question
# on 26 Aug (1,633 futures rows, 2 valid plans, 1 resolved). Each stage is
# stamped as it happens. PAPER_ENTRY here means "the follow started at the
# plan's stated entry" — no order is placed, sized for or proposed anywhere.
STAGE_PLAN = "VALID_FUTURES_PLAN"
STAGE_ELIGIBLE = "PAPER_ELIGIBLE"
STAGE_ENTRY = "PAPER_ENTRY"
STAGE_EXIT = "PAPER_EXIT"
STAGE_RESOLVED = "RESOLVED"
STAGES = (STAGE_PLAN, STAGE_ELIGIBLE, STAGE_ENTRY, STAGE_EXIT, STAGE_RESOLVED)

MIN_COMPARISON_SAMPLE = 30

_IST = timezone(timedelta(hours=5, minutes=30))
_LOCK = threading.Lock()
# instrument -> open follow. One per instrument: a second valid plan on the same
# contract while one is being followed is a restatement, not a new trade.
_open: dict[str, dict] = {}


def _ist(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d %H:%M:%S")


def _session(ts: float) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%Y-%m-%d")


def log_path() -> str:
    return os.path.join(settings.data_dir, LOG_NAME)


def _append(path: str, row: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")
    except OSError:
        # A research ledger must never break a tick.
        pass


def _signed(direction: str, points: float) -> float:
    """Points in the plan's favour, so a SHORT that falls is a positive move."""
    return -points if direction == SHORT else points


def _plan_key(card: FuturesSignalCard) -> str:
    return "|".join(str(x) for x in (
        card.contract, card.direction, card.entry, card.stop, card.target1))


def observe(instrument: str, card: FuturesSignalCard, price: float | None,
            now: float | None = None) -> None:
    """Follow this instrument's futures plan, opening one if a valid plan is new.

    Best-effort and side-effect free beyond the ledger. Called once per tick from
    the same place the research card is published.
    """
    if not settings.futures_shadow_outcomes:
        return
    now = time.time() if now is None else now
    try:
        with _LOCK:
            _step(instrument, price, now)
            if (card.status != VALID or card.direction not in (LONG, SHORT)
                    or card.entry is None or card.stop is None):
                return
            risk = abs(float(card.entry) - float(card.stop))
            if risk <= 0:
                # No risk, no R. The option book's 1e8 R rows came from dividing
                # by an epsilon; a futures plan whose stop is its entry is
                # refused here for the same reason.
                return
            open_now = _open.get(instrument)
            if open_now is not None:
                if open_now["plan_key"] != _plan_key(card):
                    open_now["restatements"] += 1
                return
            _open[instrument] = {
                "instrument": instrument,
                "signal_id": card.futures_signal_id or f"{instrument}-FUT-{int(now * 1000)}",
                "episode_id": card.episode_id,
                "global_signal_id": card.global_signal_id,
                "plan_key": _plan_key(card),
                "session": _session(now),
                "ts": int(now),
                "contract": card.contract,
                "expiry": card.expiry,
                "days_to_expiry": card.days_to_expiry,
                "rolled": bool(card.rolled),
                "price_basis": card.price_basis,
                "lot_size": card.lot_size,
                "direction": card.direction,
                "entry": float(card.entry),
                "stop": float(card.stop),
                "risk_points": round(risk, 2),
                "targets": {T1: card.target1, T2: card.target2, T3: card.target3},
                "spread_points": card.spread_points,
                "cost_points": card.cost_points,
                "signal_score": card.signal_score,
                "setup_type": card.setup_type,
                "regime": card.regime,
                "atr": card.atr,
                "lifecycle": [
                    {"stage": STAGE_PLAN, "ts": int(now), "time_ist": _ist(now)},
                    {"stage": STAGE_ELIGIBLE, "ts": int(now), "time_ist": _ist(now)},
                    {"stage": STAGE_ENTRY, "ts": int(now), "time_ist": _ist(now)},
                ],
                "best": 0.0, "worst": 0.0,
                "best_ts": int(now), "worst_ts": int(now),
                "hit": {},
                "sequence": [],
                "first_event": None,
                "restatements": 0,
            }
    except (KeyError, TypeError, ValueError):
        pass


def _step(instrument: str, price: float | None, now: float) -> None:
    st = _open.get(instrument)
    if st is None:
        return
    if price is not None:
        move = _signed(st["direction"], float(price) - st["entry"])
        if move > st["best"]:
            st["best"], st["best_ts"] = round(move, 2), int(now)
        if move < st["worst"]:
            st["worst"], st["worst_ts"] = round(move, 2), int(now)
        for name in (T1, T2, T3):
            level = st["targets"].get(name)
            if level is None or name in st["hit"]:
                continue
            if _signed(st["direction"], float(level) - st["entry"]) <= move:
                _mark(st, name, now)
        stop_move = _signed(st["direction"], st["stop"] - st["entry"])
        if STOP not in st["hit"] and move <= stop_move:
            _mark(st, STOP, now)
            _resolve(instrument, price, now, STOP)
            return
        if T3 in st["hit"]:
            _resolve(instrument, price, now, T3)
            return
    if now - st["ts"] >= max(1, settings.futures_shadow_follow_minutes) * 60:
        _resolve(instrument, price, now, TIMEOUT)
        return
    if _session(now) != st["session"]:
        _resolve(instrument, price, now, SESSION_END)


def _mark(st: dict, name: str, now: float) -> None:
    st["hit"][name] = {"ts": int(now), "time_ist": _ist(now),
                       "minutes_from_signal": round((now - st["ts"]) / 60.0, 1)}
    st["sequence"].append(name)
    if st["first_event"] is None:
        st["first_event"] = name


def _resolve(instrument: str, price: float | None, now: float,
             outcome: str) -> None:
    st = _open.pop(instrument, None)
    if st is None:
        return
    risk = st["risk_points"]
    final_move = (_signed(st["direction"], float(price) - st["entry"])
                  if price is not None else st["best"])
    gross_r = round(final_move / risk, 3)
    # The round trip in points as the research card modelled it, plus the quoted
    # spread. No cost model, no net figure — never the gross relabelled.
    cost = st.get("cost_points")
    spread = st.get("spread_points")
    cost_points = None
    if cost is not None:
        cost_points = float(cost) + (float(spread) if spread is not None else 0.0)
    first = st["first_event"]
    order = (STOP_FIRST if first == STOP else
             TARGET_FIRST if first in (T1, T2, T3) else NEITHER)
    lifecycle = list(st["lifecycle"])
    for stage in (STAGE_EXIT, STAGE_RESOLVED):
        lifecycle.append({"stage": stage, "ts": int(now), "time_ist": _ist(now)})
    # §5 capture: how much of the best excursion the plan's own levels kept, and
    # how much it handed back from that best point. The option book's median
    # give-back is 0.9R, so the same measurement has to exist in points before
    # the two vehicles are ever compared.
    best = st["best"]
    capture_pct = (round(100.0 * final_move / best, 1)
                   if best > 0 else (0.0 if best == 0 else None))
    giveback_r = round((best - final_move) / risk, 3) if best > 0 else 0.0
    row = {
        "event": "RESOLVED",
        "vehicle": "FUTURES",
        "unit": "INDEX_POINTS",
        "signal_id": st["signal_id"],
        "episode_id": st["episode_id"],
        "global_signal_id": st["global_signal_id"],
        # §5 — the market idea this plan expressed, so the option outcome on the
        # same idea can be found without matching on instrument and timestamp.
        "market_signal_id": st["global_signal_id"],
        "instrument": instrument,
        "direction_of_idea": ("BULLISH" if st["direction"] == LONG else "BEARISH"),
        "contract": st["contract"],
        "expiry": st["expiry"],
        "days_to_expiry": st["days_to_expiry"],
        "rolled": st["rolled"],
        "price_basis": st["price_basis"],
        "lot_size": st["lot_size"],
        "session": st["session"],
        "signal_ts": st["ts"],
        "signal_time_ist": _ist(st["ts"]),
        "resolved_ts": int(now),
        "resolved_time_ist": _ist(now),
        "minutes_to_resolution": round((now - st["ts"]) / 60.0, 1),
        "direction": st["direction"],
        "entry": st["entry"],
        "stop": st["stop"],
        "risk_points": risk,
        "targets": st["targets"],
        "targets_reached": {k: v for k, v in st["hit"].items() if k != STOP},
        "outcome": outcome,
        "first_event": first,
        "order": order,
        "target_before_stop": order == TARGET_FIRST,
        "final_price": price,
        "final_move_points": round(final_move, 2),
        "mfe_points": st["best"],
        "mae_points": st["worst"],
        "mfe_r": round(st["best"] / risk, 3),
        "mae_r": round(st["worst"] / risk, 3),
        "minutes_to_mfe": round((st["best_ts"] - st["ts"]) / 60.0, 1),
        "minutes_to_mae": round((st["worst_ts"] - st["ts"]) / 60.0, 1),
        "mfe_capture_pct": capture_pct,
        "giveback_points": round(best - final_move, 2) if best > 0 else 0.0,
        "giveback_r": giveback_r,
        "lifecycle": lifecycle,
        "stages_reached": [s["stage"] for s in lifecycle],
        "minutes_to_target": (st["hit"].get(T1) or {}).get("minutes_from_signal"),
        "minutes_to_stop": (st["hit"].get(STOP) or {}).get("minutes_from_signal"),
        "spread_points": spread,
        "cost_points": cost_points,
        "gross_r": gross_r,
        "net_r": (round(gross_r - cost_points / risk, 3)
                  if cost_points is not None else None),
        "signal_score": st["signal_score"],
        "setup_type": st["setup_type"],
        "regime": st["regime"],
        "restatements": st["restatements"],
        "research_only": True,
        "executable": False,
        "note": ("shadow observation of a research plan; no order was placed, "
                 "paper or live, and the result is in index points which are "
                 "never added to option premium results"),
    }
    _append(log_path(), row)


def read_outcomes(session: str | None = None) -> list[dict]:
    """Every recorded futures shadow outcome, or the ones from one session."""
    rows: list[dict] = []
    path = log_path()
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and (
                        not session or row.get("session") == session):
                    rows.append(row)
    except OSError:
        return rows
    return rows


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[mid], 3)
    return round((ordered[mid - 1] + ordered[mid]) / 2.0, 3)


def summarise(rows: list[dict]) -> dict:
    """What the futures plans actually did — and whether that is yet enough."""
    resolved = [r for r in rows if r.get("event") == "RESOLVED"]
    gross = [float(r["gross_r"]) for r in resolved if r.get("gross_r") is not None]
    net = [float(r["net_r"]) for r in resolved if r.get("net_r") is not None]
    tbs = [r for r in resolved if r.get("target_before_stop")]
    to_target = [float(r["minutes_to_target"]) for r in resolved
                 if r.get("minutes_to_target") is not None]
    to_stop = [float(r["minutes_to_stop"]) for r in resolved
               if r.get("minutes_to_stop") is not None]
    spreads = [float(r["spread_points"]) for r in resolved
               if r.get("spread_points") is not None]
    by_instrument: dict[str, dict] = {}
    for row in resolved:
        agg = by_instrument.setdefault(str(row.get("instrument")), {
            "resolved": 0, "target_before_stop": 0, "net_r_total": 0.0,
            "net_r_samples": 0, "rolled": 0})
        agg["resolved"] += 1
        agg["target_before_stop"] += 1 if row.get("target_before_stop") else 0
        agg["rolled"] += 1 if row.get("rolled") else 0
        if row.get("net_r") is not None:
            agg["net_r_total"] = round(agg["net_r_total"] + float(row["net_r"]), 3)
            agg["net_r_samples"] += 1
    # §5 funnel: where the followed plans got to. Every resolved row carries the
    # full stage list, so a small sample can be read as "few plans" rather than
    # guessed at.
    stages = {s: 0 for s in STAGES}
    for row in resolved:
        reached = row.get("stages_reached")
        if not isinstance(reached, list):
            continue
        for stage in reached:
            if stage in stages:
                stages[stage] += 1
    enough = len(net) >= MIN_COMPARISON_SAMPLE
    return {
        "unit": "INDEX_POINTS",
        "resolved": len(resolved),
        "open_follows": len(_open),
        "gross_r_median": _median(gross),
        "net_r_median": _median(net),
        "net_r_total": round(sum(net), 3) if net else None,
        "net_r_samples": len(net),
        "costed_coverage_pct": (round(100.0 * len(net) / len(resolved), 1)
                                if resolved else None),
        "target_before_stop_pct": (round(100.0 * len(tbs) / len(resolved), 1)
                                   if resolved else None),
        "mfe_r_median": _median([float(r["mfe_r"]) for r in resolved
                                 if r.get("mfe_r") is not None]),
        "mae_r_median": _median([float(r["mae_r"]) for r in resolved
                                 if r.get("mae_r") is not None]),
        "minutes_to_target_median": _median(to_target),
        "minutes_to_stop_median": _median(to_stop),
        "spread_points_median": _median(spreads),
        "rolled_plans": sum(1 for r in resolved if r.get("rolled")),
        "lifecycle_funnel": stages,
        "mfe_capture_pct_median": _median([float(r["mfe_capture_pct"]) for r in resolved
                                          if r.get("mfe_capture_pct") is not None]),
        "giveback_r_median": _median([float(r["giveback_r"]) for r in resolved
                                     if r.get("giveback_r") is not None]),
        "by_instrument": by_instrument,
        "min_comparison_sample": MIN_COMPARISON_SAMPLE,
        "comparison_ready": enough,
        "comparison_status": ("READY" if enough else "INSUFFICIENT_SAMPLE"),
        "shortfall": max(0, MIN_COMPARISON_SAMPLE - len(net)),
        "research_only": True,
        "note": ("options are measured in premium and futures in index points, "
                 "so no figure here may be compared with an option figure until "
                 "comparison_ready is true and the comparison is stated per "
                 "market idea, not as two separate books"),
    }


def report(session: str | None = None) -> dict:
    rows = read_outcomes(session)
    out = summarise(rows)
    out["session"] = session
    out["rows_read"] = len(rows)
    return out


def write_report(session: str | None = None) -> str:
    path = os.path.join(settings.data_dir, REPORT_JSON)
    try:
        os.makedirs(settings.data_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report(session), fh, indent=2, default=str)
    except OSError:
        return path
    return path


def reset_for_tests() -> None:
    with _LOCK:
        _open.clear()
