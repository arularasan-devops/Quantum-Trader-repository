"""Count the snapshots and say which questions the sample can support.

Nothing here recomputes a metric. It reads the numbers the tabs already wrote,
counts complete sessions, and compares those counts with the floors that were
frozen in Phase 35 before any of this data existed. The output is about
answerability, never about the answer.
"""
from __future__ import annotations

import json
import os

from app.research.phase37 import (
    BELOW_OBSERVATION_CHECKPOINT,
    COLLECTED,
    FLOORS,
    GENERAL_QUESTION_ANSWERABLE,
    MISSING,
    OBSERVATION_CHECKPOINT_MET,
    RELATIVE_VALUE_QUESTION_ANSWERABLE,
    REQUIRED_TABS,
    SESSION_COMPLETE,
    TABS,
    TRADE_COUNT_TAB,
    VERSION,
)
from app.research.phase37 import collect as p37collect

_SPEC = {t["tab"]: t for t in TABS}


def _dig(payload: object, dotted: str) -> object:
    cur = payload
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _number(payload: object, paths: tuple[str, ...]) -> int | None:
    """First dotted path that resolves to a real number, else None.

    Tolerant on purpose: a tab that changes shape must make one count unknown,
    not discard the session. `None` here means UNKNOWN, and UNKNOWN is never
    silently read as zero — a zero would count as a measured empty session.
    """
    for dotted in paths:
        val = _dig(payload, dotted)
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, list):
            return len(val)
    return None


def _attempt_no(stem: str, prefix: str) -> int:
    """1 for the first snapshot, N for ``<tab>.N``.

    Parsed rather than sorted lexically: ``a_plus_paper.2`` sorts *before*
    ``a_plus_paper`` as a string, and ``.10`` before ``.2``, so a filename sort
    would silently read an old snapshot as the current one.
    """
    if stem == prefix:
        return 1
    tail = stem[len(prefix) + 1:]
    return int(tail) if tail.isdigit() else 0


def _attempts(directory: str, tab: str) -> list[str]:
    """Every snapshot written for one tab, oldest attempt first."""
    prefix = tab.lower()
    found: list[tuple[int, str]] = []
    for name in os.listdir(directory):
        stem = name[:-len(".json")] if name.endswith(".json") else name
        if stem != prefix and not stem.startswith(prefix + "."):
            continue
        number = _attempt_no(stem, prefix)
        if number:
            found.append((number, name))
    return [name for _, name in sorted(found)]


def attempts(directory: str, tab: str) -> list[str]:
    """Public view of the snapshot attempts for one tab, oldest first."""
    return _attempts(directory, tab)


def _latest(directory: str, tab: str) -> dict | None:
    files = _attempts(directory, tab)
    if not files:
        return None
    with open(os.path.join(directory, files[-1]), encoding="utf-8") as fh:
        try:
            return json.load(fh)
        except json.JSONDecodeError:
            return None


def sessions(root: str | None = None) -> list[dict]:
    """One row per collected session, latest attempt per tab."""
    base = root or p37collect.root_dir()
    if not os.path.isdir(base):
        return []
    out: list[dict] = []
    for day in sorted(os.listdir(base)):
        directory = os.path.join(base, day)
        if not os.path.isdir(directory):
            continue
        tabs: dict[str, dict] = {}
        for tab, spec in _SPEC.items():
            row = _latest(directory, tab)
            if row is None:
                tabs[tab] = {"status": MISSING, "attempts": 0, "count": None}
                continue
            status = row.get("status")
            tabs[tab] = {
                "status": status,
                "attempts": len(_attempts(directory, tab)),
                "error": row.get("error"),
                "count": (
                    _number(row.get("payload"), spec["count_paths"])
                    if status == COLLECTED and spec["count_paths"] else None
                ),
            }
        absent = [t for t in REQUIRED_TABS if tabs[t]["status"] != COLLECTED]
        out.append({
            "session": day,
            "tabs": tabs,
            "missing_required": absent,
            "complete": not absent,
        })
    return out


def _floor(name: str, *, n_sessions: int, n_trades: int | None) -> dict:
    spec = FLOORS[name]
    need_s, need_t = spec["min_sessions"], spec["min_trades"]
    have_sessions = n_sessions >= need_s
    # UNKNOWN trades cannot satisfy a trade floor. A floor with no trade
    # requirement is satisfied by sessions alone.
    have_trades = (
        need_t == 0 or (n_trades is not None and n_trades >= need_t)
    )
    return {
        "floor": name,
        "permits": spec["permits"],
        "min_sessions": need_s,
        "min_trades": need_t,
        "sessions": n_sessions,
        "trades": n_trades,
        "sessions_short_by": max(0, need_s - n_sessions),
        "trades_short_by": (
            None if n_trades is None else max(0, need_t - n_trades)
        ),
        "met": bool(have_sessions and have_trades),
        "blocked_by": [
            reason for reason, failed in (
                ("SESSIONS", not have_sessions),
                ("TRADES", not have_trades),
                ("TRADE_COUNT_UNKNOWN", need_t > 0 and n_trades is None),
            ) if failed
        ],
    }


def _durable_summary() -> dict:
    """The journal-evidence tally, or why it is unavailable. Never fatal.

    A rollup must still print if the journals are missing: the snapshot count is
    the number that matters here, and losing it to an unreadable side-tally would
    be the wrong failure.
    """
    try:
        from app.research.phase37 import durable as p37durable

        state = p37durable.tally()
    except Exception as exc:  # noqa: BLE001 - side information, not the answer
        return {
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
            "counts_toward_floors": False,
        }
    return {
        "available": True,
        "sessions_with_observations": state["sessions_with_observations"],
        "first_session": state["first_session"],
        "last_session": state["last_session"],
        "total_observations": state["total_observations"],
        "total_paper_legs": state["total_paper_legs"],
        "complete": state["complete"],
        "counts_toward_floors": False,
        "why_not": state["why_not"],
    }


def rollup(root: str | None = None, *, with_durable: bool = False) -> dict:
    """Sessions collected, outcomes counted, and what that permits.

    ``with_durable`` tallies the days that hold journal evidence but no snapshot,
    which means scanning every byte of every journal. Off by default and
    deliberately not on the API path: the observation series is tens of
    gigabytes across its rolled files, and putting a full scan behind a dashboard
    poll is how the option premiums stopped looking live once already.
    """
    rows = sessions(root)
    durable_summary = _durable_summary() if with_durable else {
        "available": False,
        "not_read": "ASK_FOR_IT_EXPLICITLY_A_FULL_JOURNAL_READ_IS_NOT_FREE",
        "counts_toward_floors": False,
    }
    complete = [r for r in rows if r["complete"]]

    # Every one of these endpoints reports over the whole store, so the number a
    # tab gives on the 30th night already contains the first 29. `latest` is
    # therefore the count, and there is deliberately no sum: adding running
    # totals together would inflate the sample the floors are measured against,
    # which is worse than having no bookkeeping at all.
    per_tab: dict[str, dict] = {}
    for tab, spec in _SPEC.items():
        seen = [
            (r["session"], r["tabs"][tab]["count"]) for r in complete
            if r["tabs"][tab]["count"] is not None
        ]
        per_tab[tab] = {
            "counts": spec.get("counts"),
            "sessions_with_count": len(seen),
            "latest": seen[-1][1] if seen else None,
            "latest_from_session": seen[-1][0] if seen else None,
            "first": seen[0][1] if seen else None,
            "collected_sessions": sum(
                1 for r in rows if r["tabs"][tab]["status"] == COLLECTED
            ),
            "cumulative": True,
        }

    n_sessions = len(complete)
    # Only the paper book's count may be read as trades.
    trades = per_tab.get(TRADE_COUNT_TAB, {}).get("latest")
    floors = {
        name: _floor(name, n_sessions=n_sessions, n_trades=trades)
        for name in FLOORS
    }

    if floors["relative_value_question"]["met"]:
        status = RELATIVE_VALUE_QUESTION_ANSWERABLE
    elif floors["general_question"]["met"]:
        status = GENERAL_QUESTION_ANSWERABLE
    elif floors["observation_checkpoint"]["met"]:
        status = OBSERVATION_CHECKPOINT_MET
    else:
        status = BELOW_OBSERVATION_CHECKPOINT

    return {
        "phase": "PHASE37_SESSION_EVIDENCE",
        "version": VERSION,
        "sessions_collected": len(rows),
        "sessions_complete": n_sessions,
        "sessions_partial": len(rows) - n_sessions,
        "first_session": rows[0]["session"] if rows else None,
        "last_session": rows[-1]["session"] if rows else None,
        "resolved_paper_trades": trades,
        "resolved_paper_trades_source": TRADE_COUNT_TAB,
        # Beside the snapshot count, never inside it. See phase37.durable for
        # why a day with journal rows but no snapshot is not a countable
        # session, and why re-defining that now would void the floors.
        "durable_evidence": durable_summary,
        "per_tab": per_tab,
        "floors": floors,
        "status": status,
        "rows": [
            {
                "session": r["session"],
                "complete": r["complete"],
                "missing_required": r["missing_required"],
                "counts": {
                    tab: r["tabs"][tab]["count"] for tab in _SPEC
                    if r["tabs"][tab]["count"] is not None
                },
            }
            for r in rows
        ],
        "research_only": True,
        "paper_only": True,
        "note": (
            "Counts of collected evidence and the floors they must clear. This "
            "says which questions the sample can support, never what the answer "
            "is, and no status here promotes anything or enables a vehicle."
        ),
    }


def headline(state: dict) -> str:
    floors = state["floors"]
    obs = floors["observation_checkpoint"]
    gen = floors["general_question"]
    rel = floors["relative_value_question"]
    trades = state["resolved_paper_trades"]
    trades_txt = "UNKNOWN" if trades is None else str(trades)
    dur = state.get("durable_evidence") or {}
    dur_line = ""
    if dur.get("available") and dur.get("sessions_with_observations"):
        dur_line = (
            f"  on disk besides: {dur['sessions_with_observations']} sessions"
            f" hold measured observations"
            f" ({dur['first_session']} to {dur['last_session']}) —"
            f" journal evidence, NOT countable sessions\n"
        )
    return (
        f"PHASE 37 SESSION EVIDENCE — {state['status']}\n"
        f"  sessions: {state['sessions_complete']} complete"
        f" ({state['sessions_partial']} partial)"
        f"  resolved paper trades: {trades_txt}\n"
        f"  observation checkpoint {obs['sessions']}/{obs['min_sessions']}"
        f"  general {gen['sessions']}/{gen['min_sessions']} sessions and"
        f" {trades_txt}/{gen['min_trades']} trades\n"
        f"  relative value {rel['sessions']}/{rel['min_sessions']} sessions and"
        f" {trades_txt}/{rel['min_trades']} trades\n"
        f"{dur_line}"
        f"  a complete session needs: {', '.join(REQUIRED_TABS)}\n"
        f"  {SESSION_COMPLETE} counts toward a floor; a partial session does not."
    )
