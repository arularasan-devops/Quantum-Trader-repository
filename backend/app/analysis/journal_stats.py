"""Statistics over the Signal Board Journal — §19 and §20. Read-only.

Every number here is counted from ``signal_journal.jsonl`` and
``signal_outcomes.jsonl``. Nothing is modelled, estimated or carried over from a
replay, and three things are deliberately absent:

* **a rate with no denominator is not reported.** With no resolved signals the
  hit rates are ``None``, not ``0.0`` — a zero rate reads as "it never works"
  when the truth is "nothing has been measured yet".
* **WAIT and NO_TRADE are counted, never scored.** They are recorded calls with no
  followed outcome, so they appear in the counts and in no hit rate. §19's WAIT
  statistics stay empty until an actual WAIT-follow experiment exists.
* **an unresolved signal is its own state.** A signal still being followed, or one
  whose process restarted, is ``UNRESOLVED`` — not a timeout and not a loss.

R is premium-to-premium. The book and the costs are applied by the research
layer, which is why the top-level dictionary says so.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import time
from statistics import median

from app.analysis import r_integrity
from app.analysis import signal_journal as sj
from app.config import settings

_FAST_MIN = 15.0
_SLOW_MIN = 45.0
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _rate(hits: int, total: int) -> float | None:
    """A percentage, or None when there is no denominator to divide by."""
    return None if total <= 0 else round(100.0 * hits / total, 1)


def _mean(values: list[float]) -> float | None:
    return None if not values else round(sum(values) / len(values), 3)


def _median(values: list[float]) -> float | None:
    return None if not values else round(median(values), 3)


def speed_class(minutes: float | None) -> str:
    """§13 — how quickly the call resolved, banded for grouping only.

    These bands are labels for reading a table, not a validated finding: nothing
    in the system treats FAST as better than SLOW.
    """
    if minutes is None:
        return "UNKNOWN"
    if minutes <= _FAST_MIN:
        return "FAST"
    if minutes <= _SLOW_MIN:
        return "NORMAL"
    return "SLOW"


def statistics(session: str | None = None) -> dict:
    """§19 — the whole recorded population, or one session of it."""
    rows = sj._read(sj.journal_path())
    if session:
        rows = [r for r in rows if r.get("session") == session]
    res = sj.resolutions(session)
    by_id = {r["signal_id"]: r for r in res}

    buys = [r for r in rows if r.get("board_action") == "BUY"]
    waits = [r for r in rows if r.get("board_action") == "WAIT"]
    no_trades = [r for r in rows if r.get("board_action") in ("AVOID", "NO_TRADE")]
    followed = [r for r in rows if r.get("followed")]
    unresolved = [r for r in followed if r["signal_id"] not in by_id]

    total = len(res)
    hits = {name: sum(1 for r in res if name in (r.get("targets_reached") or {}))
            for name in (sj.T1, sj.T2, sj.T3)}
    stopped = [r for r in res if r.get("outcome") == sj.STOP]
    target_first = [r for r in res if r.get("order") == sj.TARGET_FIRST]

    def minutes_to(name: str) -> list[float]:
        out = []
        for r in res:
            hit = (r.get("targets_reached") or {}).get(name)
            if hit and hit.get("minutes_from_signal") is not None:
                out.append(float(hit["minutes_from_signal"]))
        return out

    stop_minutes = [float(r["stop_event"]["minutes_from_signal"])
                    for r in res if r.get("stop_event")]
    # Counts stay on every resolution; R averages are taken only over rows whose
    # denominator was a real risk, with the rest counted rather than clamped.
    scored, excluded_r = r_integrity.partition(res)
    mfe_r = [float(r["mfe_r"]) for r in scored if r.get("mfe_r") is not None]
    mae_r = [float(r["mae_r"]) for r in scored if r.get("mae_r") is not None]
    realized = [float(r["realized_r"]) for r in scored
                if r.get("realized_r") is not None]
    capture = [float(r["mfe_capture"]) for r in scored
               if r.get("mfe_capture") is not None]
    give_back = [float(r["give_back_r"]) for r in scored
                 if r.get("give_back_r") is not None]
    wins = [v for v in realized if v > 0]
    losses = [v for v in realized if v < 0]
    gross_loss = abs(sum(losses))

    return {
        "session": session or "ALL_RECORDED",
        "signals_recorded": len(rows),
        "buy_signals": len(buys),
        # Recorded, never scored: a WAIT has no followed outcome, so it appears
        # here and in no hit rate.
        "wait_signals": len(waits),
        "no_trade_signals": len(no_trades),
        "followed_signals": len(followed),
        "resolved_signals": total,
        "r_scored_signals": len(scored),
        "r_excluded_signals": sum(excluded_r.values()),
        "r_excluded_by_reason": dict(sorted(excluded_r.items())),
        "r_exclusion_note": r_integrity.note(),
        "unresolved_signals": len(unresolved),
        "unresolved_note": ("still being followed, or the process restarted while "
                            "they were open; not counted as wins or losses"),
        "t1_hit_rate_pct": _rate(hits[sj.T1], total),
        "t2_hit_rate_pct": _rate(hits[sj.T2], total),
        "t3_hit_rate_pct": _rate(hits[sj.T3], total),
        "stop_hit_rate_pct": _rate(len(stopped), total),
        "target_before_stop_pct": _rate(len(target_first), total),
        "timeout_pct": _rate(sum(1 for r in res if r.get("outcome") == sj.TIMEOUT),
                             total),
        "avg_minutes_to_t1": _mean(minutes_to(sj.T1)),
        "avg_minutes_to_t2": _mean(minutes_to(sj.T2)),
        "avg_minutes_to_t3": _mean(minutes_to(sj.T3)),
        "avg_minutes_to_stop": _mean(stop_minutes),
        "avg_mfe_r": _mean(mfe_r),
        "median_mfe_r": _median(mfe_r),
        "avg_mae_r": _mean(mae_r),
        "median_mae_r": _median(mae_r),
        "avg_mfe_capture": _mean(capture),
        # Only resolutions that kept an MFE have a give-back, so this has its own
        # denominator and is null when none did.
        "avg_give_back_r": _mean(give_back),
        "give_back_sample": len(give_back),
        "avg_r": _mean(realized),
        "net_r": None if not realized else round(sum(realized), 3),
        "profit_factor": (None if not losses or not wins
                          else round(sum(wins) / gross_loss, 3)),
        "speed": {
            k: sum(1 for r in res
                   if speed_class(r.get("minutes_to_resolution")) == k)
            for k in ("FAST", "NORMAL", "SLOW", "UNKNOWN")
        },
        "basis": ("recorded outcomes only, premium-to-premium; the book and the "
                  "costs are applied by the research layer, not here"),
    }


def _best(rows: list[dict], key: str, metric: str) -> dict | None:
    """The group with the highest mean of ``metric``, with its sample size.

    A best-of over two trades is noise, so the count travels with the answer
    instead of being dropped.
    """
    groups: dict[str, list[float]] = {}
    for r in rows:
        name = r.get(key)
        value = r.get(metric)
        if name is None or value is None:
            continue
        groups.setdefault(str(name), []).append(float(value))
    if not groups:
        return None
    name, values = max(groups.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))
    return {"name": name, "n": len(values),
            "avg": round(sum(values) / len(values), 3)}


def daily_report(session: str) -> dict:
    """§20 — one session, from recorded outcomes only."""
    stats = statistics(session)
    rows = [r for r in sj._read(sj.journal_path()) if r.get("session") == session]
    res = sj.resolutions(session)
    setups = {r["signal_id"]: (r.get("signal_info") or {}).get("setup_type")
              for r in rows}
    enriched = [{**r, "setup_type": setups.get(r["signal_id"])} for r in res]

    blockers: dict[str, int] = {}
    for r in rows:
        b = (r.get("signal_info") or {}).get("primary_blocker")
        if b:
            blockers[b] = blockers.get(b, 0) + 1
    outcomes: dict[str, int] = {}
    for r in res:
        o = str(r.get("outcome"))
        outcomes[o] = outcomes.get(o, 0) + 1

    highest_mfe = max(res, key=lambda r: r.get("mfe_r") or -99.0, default=None)
    give_backs = [r for r in res if r.get("give_back_r") is not None]
    biggest_give_back = max(give_backs, key=lambda r: r["give_back_r"],
                            default=None)

    return {
        "date": session,
        "statistics": stats,
        "outcome_counts": outcomes,
        "t1_count": sum(1 for r in res if sj.T1 in (r.get("targets_reached") or {})),
        "t2_count": sum(1 for r in res if sj.T2 in (r.get("targets_reached") or {})),
        "t3_count": sum(1 for r in res if sj.T3 in (r.get("targets_reached") or {})),
        "stop_count": outcomes.get(sj.STOP, 0),
        "timeout_count": outcomes.get(sj.TIMEOUT, 0),
        "expired_count": outcomes.get(sj.EXPIRED, 0),
        "best_setup_by_avg_r": _best(enriched, "setup_type", "realized_r"),
        "best_instrument_by_avg_r": _best(enriched, "instrument", "realized_r"),
        "highest_mfe": (None if highest_mfe is None else
                        {"signal_id": highest_mfe["signal_id"],
                         "instrument": highest_mfe["instrument"],
                         "mfe_r": highest_mfe.get("mfe_r")}),
        "largest_give_back": (None if biggest_give_back is None else
                              {"signal_id": biggest_give_back["signal_id"],
                               "instrument": biggest_give_back["instrument"],
                               "give_back_r": biggest_give_back["give_back_r"],
                               "mfe_r": biggest_give_back.get("mfe_r"),
                               "realized_r": biggest_give_back.get("realized_r")}),
        # The most common named refusal, which is a fact about the gates rather
        # than about the trades that were taken.
        "most_common_blocker": (None if not blockers else
                                max(blockers.items(), key=lambda kv: kv[1])[0]),
        "blocker_counts": dict(sorted(blockers.items(), key=lambda kv: -kv[1])),
    }


def today_session() -> str:
    """The current IST trading date, written exactly as the journal writes it.

    Derived from the same clock the journal stamps rows with, so a live board
    asking for "today" and a row recorded a second ago always agree.
    """
    return sj._session(time.time())


def resolve_session(value: str | None) -> str | None:
    """``TODAY`` resolved server-side; any other value passed through.

    The browser's clock and timezone are not the session's, so the live boards
    ask for the keyword and the server decides which date that is.
    """
    if value and value.strip().upper() == "TODAY":
        return today_session()
    return value


def sessions_recorded() -> list[str]:
    return sorted({str(r.get("session")) for r in sj._read(sj.journal_path())
                   if r.get("session")})


def daily_summary() -> dict:
    """Every recorded session's report, for ``daily_signal_summary.json``."""
    days = sessions_recorded()
    return {
        "sessions": days,
        "overall": statistics(),
        "by_session": {d: daily_report(d) for d in days},
    }


def history(limit: int = 300, filters: dict | None = None) -> list[dict]:
    """§17/§18 — journalled calls joined to their outcome, newest first.

    The outcome is attached, never merged into the original row: the journal row
    stays exactly what the board said at the time.
    """
    f = filters or {}
    res = {r["signal_id"]: r for r in sj.resolutions()}
    missed = _missed_by_signal(f.get("session"))
    out: list[dict] = []
    # Newest first, and stop as soon as the page is full: a board asking for 60
    # rows out of twenty thousand pays for 60. The date and instrument live on
    # the journal row itself, so a row the caller cannot want is dropped by
    # _in_range before its outcome view is assembled at all, and _keep still has
    # the final say on the assembled record.
    for row in reversed(sj._read(sj.journal_path())):
        if len(out) >= limit:
            break
        if not _in_range(row, f):
            continue
        info = row.get("signal_info") or {}
        plan = row.get("entry_plan") or {}
        vol = row.get("volatility") or {}
        outcome = res.get(row["signal_id"])
        reached = (outcome or {}).get("targets_reached") or {}
        entry = (outcome or {}).get("entry")
        final = (outcome or {}).get("final_premium")
        # Points on the premium itself, which is what was paid and what moved.
        # Only a resolved call has a final premium; an open one leaves these
        # empty rather than reporting its entry as a flat result.
        net_points = (None if entry is None or final is None
                      else round(float(final) - float(entry), 2))
        rec = {
            **row,
            "outcome_record": outcome,
            "outcome_state": (outcome.get("outcome") if outcome
                              else (row.get("outcome") or "UNRESOLVED")),
            "setup_type": info.get("setup_type"),
            "signal_score": info.get("signal_score"),
            "volatility_class": vol.get("volatility_class"),
            "t1_hit": sj.T1 in reached,
            "t2_hit": sj.T2 in reached,
            "t3_hit": sj.T3 in reached,
            "stop_hit": bool((outcome or {}).get("stop_event")),
            "realized_r": (outcome or {}).get("realized_r"),
            "minutes_to_resolution": (outcome or {}).get("minutes_to_resolution"),
            "speed": speed_class((outcome or {}).get("minutes_to_resolution")),
            "expiry_day": plan.get("expiry_day"),
            # How convinced the board was, as it displayed it at the time.
            "conviction_pct": info.get("confidence"),
            # Rows written before the relabel carry the same number under the old
            # key, so history stays readable; neither is a probability.
            "conviction_meter": info.get("conviction_meter",
                                        info.get("win_probability_display")),
            "reason_text": info.get("reason_text"),
            "reason_codes": info.get("reason_codes"),
            "entry_premium": entry if entry is not None else plan.get("entry_price"),
            "final_premium": final,
            "peak_points": (outcome or {}).get("mfe_points"),
            "peak_pct": (outcome or {}).get("mfe_pct"),
            "worst_points": (outcome or {}).get("mae_points"),
            "worst_pct": (outcome or {}).get("mae_pct"),
            "minutes_to_peak": (outcome or {}).get("minutes_to_mfe"),
            "net_points": net_points,
            "net_pct": (None if net_points is None or not entry
                        else round(100.0 * net_points / float(entry), 2)),
            # Part 24 — the market read and the vehicle judged apart, taken from
            # the resolution and never recomputed here.
            "market_signal_correct": (outcome or {}).get("market_signal_correct"),
            "vehicle_signal_correct": (outcome or {}).get("vehicle_signal_correct"),
            "failure_kind": (outcome or {}).get("failure_kind"),
            "market_move_points": (outcome or {}).get("market_move_points"),
            "attribution_note": (outcome or {}).get("attribution_note"),
        }
        rec["signal_status"] = _status(rec, missed)
        if _keep(rec, f):
            out.append(rec)
    return out


# Lifecycle stages at or before which a stopped signal never reached the user.
_DISPLAY_STAGES = frozenset(("GENERATED", "CLASSIFIED", "PLAN_CREATED",
                            "PLAN_VALIDATED", "DASHBOARD_PUBLISHED",
                            "USER_VISIBLE"))


_missed_cache: dict[tuple[str, str | None],
                    tuple[int, int, dict[str, dict]]] = {}


def _missed_by_signal(session: str | None) -> dict[str, dict]:
    """global_signal_id -> the recorded miss, for the STATUS filter.

    Read from the lifecycle ledger, not inferred: a row's status has to come from
    the stage that actually stopped it. Reconciling the whole ledger is the most
    expensive part of building a board, so the result is kept against the ledger's
    size and modification time — a lifecycle event written by the live tick
    changes both and the next board reconciles again.
    """
    from app.analysis import signal_lifecycle as lc
    from app.analysis import signal_reconciliation as recon

    path = lc.ledger_path()
    try:
        st = os.stat(path)
        stamp = (st.st_size, st.st_mtime_ns)
    except OSError:
        stamp = (0, 0)
    key = (path, session)
    hit = _missed_cache.get(key)
    if hit is not None and (hit[0], hit[1]) == stamp:
        return hit[2]
    try:
        rows = recon.missed_signals(session=session)
    except Exception:
        return {}
    out = {r["global_signal_id"]: r for r in rows if r.get("global_signal_id")}
    _missed_cache[key] = (stamp[0], stamp[1], out)
    return out


def _status(rec: dict, missed: dict[str, dict]) -> str:
    """ACTIVE / RESOLVED / INVALIDATED / MISSED_DISPLAY / MISSED_EXECUTION.

    Resolution outranks everything: a call that reached an outcome was not
    missed. An invalid plan is reported as INVALIDATED rather than missed,
    because refusing an unenterable plan is the system working.
    """
    if rec.get("outcome_state") not in (None, "UNRESOLVED", "NO_ENTRY"):
        return "RESOLVED"
    if rec.get("plan_invalid_reason") or rec.get("plan_actionable") is False:
        return "INVALIDATED"
    miss = missed.get(rec.get("global_signal_id") or "")
    if miss is not None:
        stage = str(miss.get("stage") or "")
        return "MISSED_DISPLAY" if stage in _DISPLAY_STAGES else "MISSED_EXECUTION"
    return "ACTIVE"


def vehicle_resolved_evidence(instrument: str | None = None,
                              session: str | None = None) -> dict:
    """Resolved net R / MFE / MAE / target-before-stop per vehicle (Part 34).

    Options come from ``signal_outcomes.jsonl``, futures from the paper futures
    ledger, since futures research is published without an order path and so
    never resolves an outcome of its own. Nothing here selects a vehicle: with no
    resolved rows on a side the numbers are ``None``, never zero.
    """
    opt_net: list[float] = []
    opt_mfe: list[float] = []
    opt_mae: list[float] = []
    opt_first = {"TARGET_FIRST": 0, "STOP_FIRST": 0}
    for row in sj._read(sj.outcomes_path()):
        if row.get("event") != "RESOLVED":
            continue
        if session and str(row.get("session")) != session:
            continue
        if instrument and str(row.get("instrument")) != instrument:
            continue
        if str(row.get("market") or "OPTIONS").upper() == "FUTURES":
            continue
        if not r_integrity.usable(row):
            continue
        for key, bucket in (("realized_r", opt_net), ("mfe_r", opt_mfe),
                            ("mae_r", opt_mae)):
            value = row.get(key)
            if isinstance(value, (int, float)):
                bucket.append(float(value))
        order = str(row.get("order") or "")
        if order in opt_first:
            opt_first[order] += 1

    fut_net: list[float] = []
    fut_mfe: list[float] = []
    fut_first = {"TARGET_FIRST": 0, "STOP_FIRST": 0}
    for row in _futures_paper_rows():
        if instrument and str(row.get("instrument")) != instrument:
            continue
        r_multiple = row.get("r_multiple")
        if isinstance(r_multiple, (int, float)):
            fut_net.append(float(r_multiple))
        fav, risk = row.get("max_favourable_points"), row.get("risk_points")
        if (isinstance(fav, (int, float)) and isinstance(risk, (int, float))
                and risk > 0):
            fut_mfe.append(round(float(fav) / float(risk), 3))
        reason = str(row.get("exit_reason") or "").upper()
        if "STOP" in reason:
            fut_first["STOP_FIRST"] += 1
        elif "T1" in reason or "T2" in reason or "T3" in reason or "TARGET" in reason:
            fut_first["TARGET_FIRST"] += 1

    return {
        "option": _side_evidence(opt_net, opt_mfe, opt_mae, opt_first,
                                 "signal_outcomes.jsonl"),
        "futures": _side_evidence(fut_net, fut_mfe, [], fut_first,
                                  "futures_paper.jsonl (paper, not research)"),
        "note": ("resolved evidence only, premium-to-premium for options and "
                 "point-to-point for futures. Futures research publishes no "
                 "order, so its resolved rows come from the separate paper "
                 "futures book; MAE is not recorded there and stays None."),
    }


def _futures_paper_rows() -> list[dict]:
    """Closed paper futures trades. Read-only; an absent ledger is not an error."""
    path = os.path.join(settings.data_dir, "futures_paper.jsonl")
    rows: list[dict] = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict) and rec.get("exit") is not None:
                    rows.append(rec)
    except OSError:
        return []
    return rows


def _side_evidence(net: list[float], mfe: list[float], mae: list[float],
                   first: dict[str, int], source: str) -> dict:
    decided = first["TARGET_FIRST"] + first["STOP_FIRST"]
    return {
        "resolved": len(net),
        "net_r_total": round(sum(net), 3) if net else None,
        "net_r_avg": _mean(net),
        "net_r_median": _median(net),
        "win_rate_pct": _rate(len([v for v in net if v > 0]), len(net)),
        "mfe_r_avg": _mean(mfe),
        "mae_r_avg": _mean(mae),
        "target_before_stop": first["TARGET_FIRST"],
        "stop_before_target": first["STOP_FIRST"],
        "target_before_stop_pct": _rate(first["TARGET_FIRST"], decided),
        "source": source,
    }


def _in_range(row: dict, f: dict) -> bool:
    """The session/date-range/instrument part of :func:`_keep`, on a raw row.

    Exactly the same comparisons, kept identical deliberately: this only decides
    which rows are worth assembling, and ``_keep`` still has the final say, so
    the two can never disagree about what a filter means.
    """
    day = row.get("session")
    want = f.get("session")
    if want and str(want).upper() != str(day).upper():
        return False
    inst = f.get("instrument")
    if inst and str(inst).upper() != str(row.get("instrument")).upper():
        return False
    lo, hi = f.get("date_from"), f.get("date_to")
    if lo and (not day or str(day) < str(lo)):
        return False
    if hi and (not day or str(day) > str(hi)):
        return False
    return True


def _keep(rec: dict, f: dict) -> bool:
    """§18 filters. An absent filter never excludes a row."""
    checks = (
        ("session", rec.get("session")),
        ("instrument", rec.get("instrument")),
        ("action", rec.get("board_action")),
        ("position_signal", rec.get("position_signal")),
        ("vehicle", rec.get("vehicle")),
        # OPTIONS / FUTURES, the finer traded leg, and the lifecycle state.
        # Added beside the existing coarse ``vehicle`` rather than changing it,
        # so no archived report changes meaning.
        ("market", rec.get("market")),
        ("signal_vehicle", rec.get("signal_vehicle")),
        ("status", rec.get("signal_status")),
        ("option_type", rec.get("option_type")),
        ("setup_type", rec.get("setup_type")),
        ("volatility_class", rec.get("volatility_class")),
        ("outcome", rec.get("outcome_state")),
        ("exchange", rec.get("exchange")),
        ("speed", rec.get("speed")),
    )
    for name, value in checks:
        want = f.get(name)
        if want and str(want).upper() != str(value).upper():
            return False
    for name, flag in (("t1_hit", rec["t1_hit"]), ("t2_hit", rec["t2_hit"]),
                       ("t3_hit", rec["t3_hit"]), ("stop_hit", rec["stop_hit"]),
                       ("followed", rec.get("followed")),
                       ("expiry_day", rec.get("expiry_day"))):
        want = f.get(name)
        if want is not None and bool(want) != bool(flag):
            return False
    lo, hi = f.get("min_score"), f.get("max_score")
    score = rec.get("signal_score")
    if lo is not None and (score is None or float(score) < float(lo)):
        return False
    if hi is not None and (score is None or float(score) > float(hi)):
        return False
    max_min = f.get("max_minutes_to_resolution")
    if max_min is not None:
        mins = rec.get("minutes_to_resolution")
        if mins is None or float(mins) > float(max_min):
            return False
    # Session is the IST trading date, so an ISO string compares chronologically.
    # A row with no session cannot be placed in time and is excluded from a range
    # rather than assumed to be inside it.
    day = rec.get("session")
    for name, keep in (("date_from", lambda d, b: d >= b),
                       ("date_to", lambda d, b: d <= b)):
        bound = f.get(name)
        if bound and (not day or not keep(str(day), str(bound))):
            return False
    return True


def valid_date(value: str | None) -> bool:
    """An ISO ``YYYY-MM-DD`` day, or nothing at all. Anything else is rejected."""
    return value is None or value == "" or bool(_DATE.match(value))


# The report columns, in the order they are written. Kept explicit so a new
# journal field never silently changes the shape of a file someone archived.
_CSV_COLUMNS: tuple[tuple[str, str], ...] = (
    ("date", "session"),
    ("time_ist", "time_ist"),
    ("instrument", "instrument"),
    ("exchange", "exchange"),
    ("tradingsymbol", "tradingsymbol"),
    ("vehicle", "vehicle"),
    ("option_type", "option_type"),
    ("strike", "strike"),
    ("expiry", "expiry"),
    ("expiry_day", "expiry_day"),
    ("moneyness", "moneyness"),
    ("lot_size", "lot_size"),
    ("premium_at_signal", "premium"),
    ("setup_type", "setup_type"),
    ("volatility_class", "volatility_class"),
    ("signal_score", "signal_score"),
    ("conviction_pct", "conviction_pct"),
    ("conviction_meter", "conviction_meter"),
    ("outcome", "outcome_state"),
    ("t1_hit", "t1_hit"),
    ("t2_hit", "t2_hit"),
    ("t3_hit", "t3_hit"),
    ("stop_hit", "stop_hit"),
    # What the premium actually did, in points and in percent of what was paid.
    ("entry_premium", "entry_premium"),
    ("peak_points", "peak_points"),
    ("peak_pct_of_premium", "peak_pct"),
    ("minutes_to_peak", "minutes_to_peak"),
    ("worst_points", "worst_points"),
    ("worst_pct_of_premium", "worst_pct"),
    ("exit_premium", "final_premium"),
    ("net_points", "net_points"),
    ("net_pct_of_premium", "net_pct"),
    ("realized_r", "realized_r"),
    ("minutes_to_resolution", "minutes_to_resolution"),
    ("signal_reason", "reason_text"),
    ("reason_codes", "reason_codes"),
    ("signal_id", "signal_id"),
)

_PLAN_COLUMNS: tuple[tuple[str, str], ...] = (
    ("entry_price", "entry_price"),
    ("entry_zone_low", "entry_zone_low"),
    ("entry_zone_high", "entry_zone_high"),
    ("do_not_buy_above", "do_not_buy_above"),
    ("stop", "stop"),
    ("stop_pct", "stop_pct"),
    ("risk_points", "risk_points"),
    ("target1", "target1"),
    ("target2", "target2"),
    ("target3", "target3"),
    ("expected_r", "expected_r"),
    ("expected_holding_minutes", "expected_holding_minutes"),
    ("suggested_lots", "suggested_lots"),
    ("minutes_to_expiry", "minutes_to_expiry"),
)

_INFO_COLUMNS: tuple[tuple[str, str], ...] = (
    ("signal_strength", "signal_strength"),
    ("trade_quality", "trade_quality"),
    ("risk_level", "risk_level"),
    ("opportunity_score", "opportunity_score"),
    ("entry_trigger", "entry_trigger"),
    ("chart_pattern", "chart_pattern"),
)

_STATE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("underlying_ltp", "underlying_ltp"),
    ("trend_1m", "trend_1m"),
    ("trend_5m", "trend_5m"),
    ("regime", "regime"),
    ("market_structure", "market_structure"),
    ("adx", "adx"),
    ("rsi", "rsi"),
    ("delta", "delta"),
    ("oi", "oi"),
    ("feed_state", "feed_state"),
    ("data_quality_score", "data_quality_score"),
)

_OUTCOME_COLUMNS: tuple[tuple[str, str], ...] = (
    ("mfe_r", "mfe_r"),
    ("mae_r", "mae_r"),
    ("give_back_r", "give_back_r"),
    ("mfe_capture", "mfe_capture"),
)


def buy_rows(date_from: str | None = None, date_to: str | None = None,
             instrument: str | None = None, limit: int = 5000) -> list[dict]:
    """The BUY calls the board actually printed, newest first, inclusive range.

    The filter is ``position_signal``, not ``board_action``: the board's market
    bias can read BUY on a call the gates then refused, and such a row is a view
    on the market rather than a call to buy something. Only a call whose printed
    position was BUY belongs in a report of BUY signals.

    ``followed`` is required as well, because it is the condition under which an
    outcome is tracked at all: the call carried a leg, an entry premium, a stop
    and a first target. A BUY with none of those has no premium move, no target
    and no stop to report, so it is counted by ``buy_calls_without_plan`` rather
    than listed with empty cells that read as a flat trade.
    """
    return history(limit=limit, filters={
        "position_signal": "BUY", "followed": True, "instrument": instrument,
        "date_from": date_from or None, "date_to": date_to or None,
    })


def buy_calls_without_plan(date_from: str | None = None,
                           date_to: str | None = None,
                           instrument: str | None = None,
                           limit: int = 5000,
                           session: str | None = None) -> int:
    """How many BUY calls the range holds that carried no actionable plan.

    Reported beside the table so the excluded rows are visible as a count rather
    than silently dropped. ``session`` narrows it to a single trading date, so a
    today-only board reports today's excluded calls instead of a count taken from
    every session ever recorded.
    """
    return len(history(limit=limit, filters={
        "position_signal": "BUY", "followed": False, "instrument": instrument,
        "session": session or None,
        "date_from": date_from or None, "date_to": date_to or None,
    }))


def buy_report_csv(date_from: str | None = None, date_to: str | None = None,
                   instrument: str | None = None, limit: int = 5000) -> str:
    """§18 download — the BUY calls of a date range as CSV, oldest first.

    One row per call, exactly as journalled, with its outcome beside it. An
    unresolved call is written as ``UNRESOLVED`` with empty outcome cells rather
    than as a zero, because a call still open is not a flat result.
    """
    rows = buy_rows(date_from, date_to, instrument, limit)[::-1]
    header = ([c for c, _ in _CSV_COLUMNS] + [c for c, _ in _INFO_COLUMNS]
              + [c for c, _ in _PLAN_COLUMNS] + [c for c, _ in _STATE_COLUMNS]
              + [c for c, _ in _OUTCOME_COLUMNS])
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    for r in rows:
        info = r.get("signal_info") or {}
        plan = r.get("entry_plan") or {}
        out = r.get("outcome_record") or {}
        line: list[object] = [_cell(r.get(k)) for _, k in _CSV_COLUMNS]
        line += [_cell(info.get(k)) for _, k in _INFO_COLUMNS]
        line += [_cell(plan.get(k)) for _, k in _PLAN_COLUMNS]
        line += [_cell((r.get("market_state") or {}).get(k))
                 for _, k in _STATE_COLUMNS]
        line += [_cell(out.get(k)) for _, k in _OUTCOME_COLUMNS]
        w.writerow(line)
    return buf.getvalue()


def _cell(value: object) -> str:
    """A missing value stays empty — never 0, never False, never 'N/A'."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict):
        return ""
    if isinstance(value, list):
        # A list of reasons reads as one cell; anything nested is dropped rather
        # than written as a Python repr.
        value = " | ".join(str(v) for v in value
                           if not isinstance(v, (dict, list)))
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # A spreadsheet treats a leading =, +, - or @ as a formula. Numbers are
    # returned above, so anything reaching here that starts with one is text and
    # is neutralised rather than handed to Excel as an expression.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text
