"""Lifecycle funnel — why did N candidates produce only M costed legs?

    .venv/bin/python phase19_funnel.py                  # all sessions on disk
    .venv/bin/python phase19_funnel.py --sessions 2     # the last two only
    .venv/bin/python phase19_funnel.py --json           # machine-readable

Checkpoint 1 says whether the recorder works. This says where the trades went,
which is a different question and became the live one the moment capture cleared
99%: a session that records 8,775 candidates and resolves one costed leg is
either correctly refusing 8,774 or quietly dropping them, and from outside the
two look identical.

Four causes, and the point of this file is that they are distinguishable:

* **A+ is selective by design** — the candidates were graded and refused at a
  named gate. The gate is named and counted, so "selective" is a measurement
  rather than a compliment.
* **Paper admission is too restrictive** — a candidate passed A+ but its book
  was not fillable, so no entry was possible.
* **Positions open and never resolve** — entries exist, resolutions do not.
* **The resolver is losing closures** — resolved rows exist with no exit price,
  or the same episode was written more than once, which would count correlated
  rows as independent evidence.

One number to read before all of them: a recorded candidate is one TICK, not one
opportunity. The same option symbol is re-observed every few seconds for as long
as it stays selected, so thousands of rows can describe a handful of distinct
chances to trade. The funnel therefore counts both, and the distinct count is
the one that bounds how many paper legs could ever have existed.

This file only counts. It replays the admission predicates the engine already
applied, over rows the engine already wrote; it changes no gate and opens no leg.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

from app.config import settings
from app.research.phase17 import aplus as p17aplus
from app.research.phase17 import capture as p17capture
from app.research.phase17 import paper as p17paper
from app.research.phase17 import quality as p17quality
from app.research.phase17 import store as p17store

# Diagnoses. Deliberately not a single "healthy / unhealthy" flag: the fix for
# each of these is a different piece of work, and collapsing them is how a
# selective grader gets mistaken for a broken lifecycle.
NO_DATA = "NO_DATA"
A_PLUS_SELECTIVE = "A_PLUS_SELECTIVE_BY_DESIGN"
ADMISSION_BLOCKED = "PAPER_ADMISSION_BLOCKED"
NOT_ENTERING = "LIFECYCLE_NOT_ENTERING"
NOT_RESOLVING = "POSITIONS_NOT_RESOLVING"
RESOLVER_LOSING = "RESOLVER_LOSING_CLOSURES"
GRADING_MISMATCH = "GRADED_SET_MISMATCH"
DUPLICATE_EPISODES = "DUPLICATE_EPISODE_ROWS"
HEALTHY = "LIFECYCLE_WORKING"


def _sessions_of(rows: list[dict], key: str = "signal_ts") -> list[str]:
    out: list[str] = []
    for r in rows:
        ts = r.get(key) or r.get("ts") or r.get("capture_ts")
        if isinstance(ts, (int, float)):
            day = p17capture.ist_parts(float(ts))[0]
            if day and day not in out:
                out.append(day)
    return sorted(out)


def _within(rows: list[dict], keep: set[str] | None,
            key: str = "signal_ts") -> list[dict]:
    if keep is None:
        return rows
    out = []
    for r in rows:
        ts = r.get(key) or r.get("ts") or r.get("capture_ts")
        if isinstance(ts, (int, float)) and p17capture.ist_parts(float(ts))[0] in keep:
            out.append(r)
    return out


def _tally(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def opportunity_key(row: dict) -> str:
    """One distinct chance to trade, as opposed to one recorded tick.

    Keyed on session, instrument, the selected symbol and the direction: a leg
    re-observed on the next tick is the same chance, while the same symbol on a
    later day, or in the other direction, is a new one.
    """
    sel = row.get("selected") or {}
    return "|".join([
        str(row.get("session") or ""),
        str(row.get("instrument") or ""),
        str(sel.get("symbol") or ""),
        str(row.get("direction") or ""),
    ])


def admission_of(row: dict) -> str:
    """Why paper would or would not have admitted this A+ tick.

    The same predicates as ``paper.consider``, in the same order, read off the
    persisted row. Kept as a replay rather than a second implementation of the
    rule: if the engine's admission changes, this reports the old answer loudly
    instead of agreeing by coincidence.
    """
    sel = row.get("selected") or {}
    if not sel or not sel.get("has_book"):
        return p17paper.REFUSED_NO_BOOK
    if not p17quality.fillable(str(sel.get("data_quality") or p17quality.MISSING)):
        return p17paper.REFUSED_DATA
    ask = sel.get("ask")
    if not isinstance(ask, (int, float)) or float(ask) <= 0:
        return p17paper.REFUSED_NO_BOOK
    return p17paper.ENTERED


def live_paper(timeout: float = 1.5) -> dict | None:
    """Open paper legs and the refusal counters from the running backend.

    Open legs and the by-reason refusal tally live in the engine's memory, so a
    fresh CLI process cannot see them: without this, a session that entered ten
    legs still running would read as a session that entered none. When the app is
    down, say so rather than print a zero that looks like a measurement.
    """
    out: dict = {}
    for name, url in (
        ("paper", f"http://127.0.0.1:{settings.port}/api/phase17/paper"),
        ("health", f"http://127.0.0.1:{settings.port}/api/phase17/health"),
    ):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return None
        if not isinstance(payload, dict):
            return None
        out[name] = payload
    open_rows = out["paper"].get("open")
    health = (out["health"].get("paper") or {}) if isinstance(
        out["health"].get("paper"), dict
    ) else {}
    return {
        "open": len(open_rows) if isinstance(open_rows, list) else 0,
        "entered": health.get("entered"),
        "closed": health.get("closed"),
        "refused_by_reason": health.get("refused_by_reason") or {},
        "paper_enabled": health.get("enabled"),
    }


def diagnose(stages: dict, live: dict | None) -> tuple[str, str]:
    """Name which of the four causes the counts support, or that they don't."""
    if not stages["candidate_ticks"]:
        return NO_DATA, (
            "No candidate was recorded in this window. Nothing about the "
            "lifecycle can be concluded — run a full session first."
        )
    if stages.get("duplicate_episode_rows"):
        return DUPLICATE_EPISODES, (
            f"{stages['duplicate_episode_rows']} paper row(s) repeat an episode "
            "id. Duplicated episodes are correlated rows counted as independent "
            "evidence, which inflates any expectancy computed over them — fix the "
            "book before reading it."
        )
    if stages["resolved_without_exit_price"]:
        return RESOLVER_LOSING, (
            f"{stages['resolved_without_exit_price']} resolved row(s) carry no "
            "exit price. Those legs closed without a price anyone was bidding, "
            "so they must not enter the expectancy — fix the resolver."
        )
    if not stages["a_plus_ticks"] and stages["resolved_legs"]:
        return GRADING_MISMATCH, (
            f"{stages['resolved_legs']} costed leg(s) resolved in this window, "
            "but no observation in it was graded A+ — and a paper leg can only "
            "come from an A+ observation. Either the observation rows for those "
            "legs were not written, or the grade changed after entry. Reconcile "
            "before reading the book."
        )
    if not stages["a_plus_ticks"]:
        top = next(iter(stages["refused_at_grading"].items()), ("NONE", 0))
        return A_PLUS_SELECTIVE, (
            f"No candidate was graded A+, so nothing could enter. The gate doing "
            f"the work is {top[0]} ({top[1]} of {stages['candidate_ticks']} "
            "ticks). This is selectivity, not a broken lifecycle — but it also "
            "means the A+ clock is accruing at zero, and whether that gate is "
            "right is a separate question from whether it is working."
        )
    if not stages["admissible_opportunities"]:
        top = next(iter(stages["refused_at_admission"].items()), ("NONE", 0))
        return ADMISSION_BLOCKED, (
            f"A+ candidates existed ({stages['a_plus_opportunities']} distinct) "
            f"but none had a fillable book: {top[0]} on {top[1]} tick(s). The "
            "grader and the recorder are working; the book was not tradable at "
            "the instant it was graded."
        )
    entered = stages["paper_entries_seen"]
    if not entered:
        return NOT_ENTERING, (
            f"{stages['admissible_opportunities']} admissible opportunity(ies) "
            "and no paper entry recorded. Something between admission and entry "
            "is dropping trades — this is the case that needs code, not patience."
        )
    if not stages["resolved_legs"]:
        return NOT_RESOLVING, (
            f"{entered} entry(ies) and nothing resolved"
            + (f", {live['open']} still open" if live else "")
            + ". Either the follow window has not elapsed yet or resolution is "
            "not firing; re-run after the session closes and it separates."
        )
    return HEALTHY, (
        f"{stages['candidate_ticks']} tick(s) → "
        f"{stages['distinct_opportunities']} distinct opportunity(ies) → "
        f"{stages['a_plus_opportunities']} A+ → "
        f"{stages['admissible_opportunities']} admissible → "
        f"{stages['resolved_legs']} resolved. The lifecycle runs end to end; "
        "what limits the sample is A+ selectivity, which is the intended "
        "behaviour and the reason the A+ clock is measured in months."
    )


def build(sessions: int | None) -> dict:
    all_obs = p17store.observations()
    present = _sessions_of(all_obs, "signal_ts")
    keep = set(present[-sessions:]) if sessions else None
    observations = _within(all_obs, keep, "signal_ts")
    paper_rows = _within(p17store.paper(), keep, "signal_ts")

    labels = [
        str(((o.get("aplus") or {}).get("a_plus_label")) or "UNGRADED")
        for o in observations
    ]
    a_plus_rows = [
        o for o in observations
        if ((o.get("aplus") or {}).get("a_plus_label")) == p17aplus.A_PLUS
    ]
    admissions = [admission_of(o) for o in a_plus_rows]
    admissible_rows = [
        o for o, verdict in zip(a_plus_rows, admissions, strict=True)
        if verdict == p17paper.ENTERED
    ]

    resolved = [r for r in paper_rows if r.get("exit_ts")]
    resolved_no_price = [
        r for r in resolved if not isinstance(r.get("exit_price"), (int, float))
    ]
    live = live_paper()
    # Entries the engine actually made: resolved rows on disk are the floor, plus
    # whatever is still open in memory. With the app down the open ones are
    # invisible, which is why the mode is printed beside the count.
    identified = {
        str(r["episode_id"]) for r in paper_rows if r.get("episode_id")
    }
    unidentified = sum(1 for r in paper_rows if not r.get("episode_id"))
    entries_seen = len(identified) + unidentified
    # A paper row is one episode, so two rows sharing an id mean the book wrote
    # the same episode twice — the correlated-duplicate failure the futures book
    # already had once. Counted, not averaged away.
    duplicate_ids = len(paper_rows) - unidentified - len(identified)
    if live:
        entries_seen += int(live["open"])

    stages = {
        "candidate_ticks": len(observations),
        "distinct_opportunities": len({opportunity_key(o) for o in observations}),
        "graded": _tally(labels),
        "refused_at_grading": _tally(
            [lb for lb in labels if lb != p17aplus.A_PLUS]
        ),
        "a_plus_ticks": len(a_plus_rows),
        "a_plus_opportunities": len({opportunity_key(o) for o in a_plus_rows}),
        "refused_at_admission": _tally(
            [a for a in admissions if a != p17paper.ENTERED]
        ),
        "admissible_ticks": len(admissible_rows),
        "admissible_opportunities": len(
            {opportunity_key(o) for o in admissible_rows}
        ),
        "paper_entries_seen": entries_seen,
        "duplicate_episode_rows": duplicate_ids,
        "open_now": live["open"] if live else None,
        "resolved_legs": len(resolved),
        "resolved_without_exit_price": len(resolved_no_price),
        "outcomes": _tally([str(r.get("outcome") or "UNKNOWN") for r in resolved]),
        "exit_sides": _tally([str(r.get("exit_side") or "UNKNOWN") for r in resolved]),
    }
    diagnosis, explanation = diagnose(stages, live)
    return {
        "question": "Where did the candidates go?",
        "sessions_requested": sessions,
        "sessions_present": present,
        "sessions_measured": _sessions_of(observations, "signal_ts"),
        "backend_online": live is not None,
        "live_paper": live,
        "stages": stages,
        "diagnosis": diagnosis,
        "explanation": explanation,
        "note": (
            "One recorded candidate is one tick, not one opportunity. The "
            "distinct count is what bounds how many paper legs could exist."
        ),
        "research_only": True,
    }


def _print_stage(label: str, value: object, detail: str = "") -> None:
    shown = "—" if value is None else str(value)
    print(f"  {label:<34} {shown:>8}   {detail}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=None,
                    help="measure only the last N recorded sessions (default: all)")
    ap.add_argument("--json", action="store_true",
                    help="print JSON instead of a table")
    args = ap.parse_args()

    result = build(args.sessions)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return

    st = result["stages"]
    measured = result["sessions_measured"]
    print("LIFECYCLE FUNNEL — where did the candidates go?")
    print(f"sessions measured: {', '.join(measured) if measured else 'none'}"
          f"   (on disk: {len(result['sessions_present'])})")
    print("backend: " + ("running (open legs and refusal reasons read live)"
                         if result["backend_online"] else
                         "not reachable (open legs invisible; resolved rows only)"))
    print()
    _print_stage("recorded candidate ticks", st["candidate_ticks"],
                 "one tick, not one opportunity")
    _print_stage("distinct opportunities", st["distinct_opportunities"],
                 "session x instrument x symbol x direction")
    _print_stage("graded A+ (ticks)", st["a_plus_ticks"])
    _print_stage("graded A+ (distinct)", st["a_plus_opportunities"])
    _print_stage("fillable at admission (ticks)", st["admissible_ticks"])
    _print_stage("admissible (distinct)", st["admissible_opportunities"],
                 "the ceiling on paper legs")
    _print_stage("paper entries seen", st["paper_entries_seen"],
                 "resolved rows + open legs" if result["backend_online"]
                 else "resolved rows only — backend down")
    _print_stage("open right now", st["open_now"])
    _print_stage("resolved costed legs", st["resolved_legs"])
    _print_stage("resolved without exit price", st["resolved_without_exit_price"],
                 "must be 0")
    _print_stage("duplicate episode rows", st["duplicate_episode_rows"],
                 "same episode written twice — must be 0")

    print("\nrefused at grading (why a candidate is not A+):")
    for reason, count in (st["refused_at_grading"] or {"NONE": 0}).items():
        print(f"  {reason:<34} {count:>8}")
    print("\nrefused at paper admission (A+ but not fillable):")
    for reason, count in (st["refused_at_admission"] or {"NONE": 0}).items():
        print(f"  {reason:<34} {count:>8}")
    if st["outcomes"]:
        print("\noutcomes of the resolved legs:")
        for outcome, count in st["outcomes"].items():
            print(f"  {outcome:<34} {count:>8}")
    live = result["live_paper"]
    if live and live.get("refused_by_reason"):
        print("\nlive engine refusals this process (all sessions since start):")
        for reason, count in live["refused_by_reason"].items():
            print(f"  {reason:<34} {count:>8}")

    print(f"\nDIAGNOSIS: {result['diagnosis']}")
    print(result["explanation"])


if __name__ == "__main__":
    main()
