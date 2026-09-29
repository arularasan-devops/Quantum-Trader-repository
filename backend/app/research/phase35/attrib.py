"""Phase 35 §10/§18 — why a paper leg lost, where the money went.

Two different questions, answered separately on purpose.

§10 asks for a **primary cause** of a losing engine BUY: one label, from a frozen
list, chosen by a deterministic priority so that the same row always produces the
same answer and no ordering accident can shuffle the histogram. The rule that
matters most is the last one: when the row cannot prove a cause,
:data:`UNATTRIBUTED` is the answer. A taxonomy that always finds a culprit is a
taxonomy that will confidently blame direction for a cost problem, and that is
exactly the mistake this phase exists to stop making.

§18 asks a narrower question — through which **channel** was profitability lost —
and answers it from the arithmetic identity

    GROSS - SPREAD - BROKERAGE - STATUTORY - SLIPPAGE = NET

so the channel is a statement about the decomposition, not an opinion. When
gross was positive and net was negative the channel is COST, and no amount of
direction talk changes that.

Priority order of causes, and the evidence each one needs:

1. ``EXCESSIVE_COST`` — gross was positive and the round trip alone turned it
   negative. Needs a charged cost.
2. ``GIVEBACK`` — the leg reached T1 and handed back at least half of its best
   look. Needs a measured path.
3. ``BAD_EXIT`` — the leg had at least twice its cost in room at the peak and
   still finished negative, without meeting the giveback bar.
4. ``WRONG_VEHICLE`` — another vehicle on the *same* observation, priced on its
   own executable book, netted positive while this one lost.
5. ``WRONG_DIRECTION`` — the opposite option side netted positive, or a futures
   leg's own move was against the recorded view. Needs both sides measured.
6. ``BAD_ENTRY`` — the adverse move came first and the leg's own peak later
   cleared T1: the idea was right and the instant was not.
7. ``INSUFFICIENT_ROOM`` — the best look never reached the round-trip cost. The
   opportunity was too small to pay for itself, whatever was done with it.
8. ``WRONG_STRIKE`` — needs the neighbouring strikes' own executable books,
   which the store does not carry, so it is currently unprovable and is never
   asserted. Recorded here as a known gap rather than approximated from delta.
9. ``OTHER`` — measured, negative, and none of the above discriminates.
"""
from __future__ import annotations

from app.research.phase35 import (
    BAD_ENTRY,
    BAD_EXIT,
    CHANNEL_COST,
    CHANNEL_DIRECTION,
    CHANNEL_ENTRY,
    CHANNEL_EXIT,
    CHANNEL_GIVEBACK,
    CHANNEL_NONE,
    CHANNEL_VEHICLE,
    EXCESSIVE_COST,
    FUTURES,
    GIVEBACK,
    INSUFFICIENT_ROOM,
    MEASURED_EXECUTABLE,
    OTHER,
    UNATTRIBUTED,
    UNMEASURED,
    WRONG_DIRECTION,
    WRONG_VEHICLE,
)

# The giveback bar. Half of the best look is a lot to hand back and it is the
# level Phase 33 found the median cohort crossing, so it is used rather than a
# rounder number.
GIVEBACK_BAR_PCT = 50.0
# "Had room" means the peak was worth at least this many round trips.
ROOM_MULTIPLE = 2.0

# WRONG_STRIKE is in the §10 vocabulary and cannot be proven from what is stored:
# proving it needs the neighbouring strike's own bid/ask at the same instant, and
# the capture keeps the window as context rather than as executable books. Named
# here so the report can state the gap instead of the reader assuming the cause
# never occurs.
UNPROVABLE_CAUSES: tuple[str, ...] = ("WRONG_STRIKE",)


def _pct(points: float | None, entry: float) -> float | None:
    if points is None or entry <= 0:
        return None
    return round(100.0 * float(points) / entry, 4)


def decompose(leg: dict, cost: dict) -> dict:
    """§18's identity, in percent of the entry price."""
    entry = float(leg.get("entry_price") or 0.0)
    gross = leg.get("gross_pct")
    return {
        "gross_pct": gross,
        "spread_pct": _pct(cost.get("spread_points"), entry),
        "brokerage_pct": _pct(cost.get("brokerage_points"), entry),
        "statutory_pct": _pct(cost.get("statutory_points"), entry),
        "slippage_pct": _pct(cost.get("slippage_points"), entry),
        "net_pct": leg.get("net_pct"),
    }


def channel(leg: dict, cost: dict, giveback: dict, *,
            better_vehicle: bool) -> str | None:
    """Which channel took the money — §18, or None when the leg never resolved.

    An unresolved leg has no channel. Calling it PROFITABLE_NO_LOSS_CHANNEL would
    read as "this one was fine", which is the opposite of "we do not know yet".
    """
    net = leg.get("net_pct")
    gross = leg.get("gross_pct")
    if net is None or gross is None:
        return None if net is None else CHANNEL_COST
    if net >= 0:
        return CHANNEL_NONE
    if gross > 0:
        return CHANNEL_COST
    peak = float(giveback.get("peak_pct") or 0.0)
    cost_pct = _pct(cost.get("cost_points"), float(leg.get("entry_price") or 0.0)) or 0.0
    handed = giveback.get("pct_of_mfe_given_back")
    if peak > 0 and handed is not None and float(handed) >= GIVEBACK_BAR_PCT:
        return CHANNEL_GIVEBACK
    if cost_pct > 0 and peak >= ROOM_MULTIPLE * cost_pct:
        return CHANNEL_EXIT
    if better_vehicle:
        return CHANNEL_VEHICLE
    if giveback.get("adverse_first") and peak > 0:
        return CHANNEL_ENTRY
    return CHANNEL_DIRECTION


def classify(
    leg: dict,
    *,
    cost: dict,
    giveback: dict,
    targets: dict,
    peers: dict[str, float | None],
) -> dict:
    """The primary cause for one losing leg, or UNATTRIBUTED.

    ``peers`` maps the other vehicles measured at the same observation to their
    own net percentages. A peer with ``None`` is unmeasured and is never read as
    "did worse": the comparison is only made where both sides have a book.
    """
    net = leg.get("net_pct")
    gross = leg.get("gross_pct")
    entry = float(leg.get("entry_price") or 0.0)
    evidence = leg.get("evidence") or UNMEASURED
    if net is None or gross is None or entry <= 0:
        return {
            "primary_cause": UNATTRIBUTED,
            "channel": None,
            "evidence": UNMEASURED,
            "note": "no executable net result for this leg",
        }
    if net >= 0:
        return {
            "primary_cause": "NONE_PROFITABLE",
            "channel": CHANNEL_NONE,
            "evidence": evidence,
            "note": "leg was net positive; nothing to attribute",
        }

    cost_pct = _pct(cost.get("cost_points"), entry)
    peak = float(giveback.get("peak_pct") or 0.0)
    handed = giveback.get("pct_of_mfe_given_back")
    t1 = float((targets or {}).get("t1") or 0.0)
    better = {v: p for v, p in peers.items() if p is not None and p > 0}

    cause = OTHER
    note = "measured loss; no single stored field discriminates a cause"
    if gross > 0 and cost_pct is not None:
        cause = EXCESSIVE_COST
        note = (f"gross {gross:+.2f}% was positive; the round trip cost "
                f"{cost_pct:.2f}% of premium")
    elif t1 > 0 and peak >= t1 and handed is not None and float(handed) >= GIVEBACK_BAR_PCT:
        cause = GIVEBACK
        note = (f"reached T1 ({t1:.2f}%), peaked {peak:+.2f}% and handed back "
                f"{float(handed):.0f}% of it")
    elif cost_pct is not None and cost_pct > 0 and peak >= ROOM_MULTIPLE * cost_pct:
        cause = BAD_EXIT
        note = (f"peak {peak:+.2f}% was {peak / cost_pct:.1f}x the round trip and "
                f"the leg still finished at {net:+.2f}%")
    elif better and leg.get("vehicle") == FUTURES:
        cause = WRONG_VEHICLE
        note = f"an option side on the same observation netted {max(better.values()):+.2f}%"
    elif better:
        opposite = {"CE": "PE", "PE": "CE"}.get(str(leg.get("vehicle")))
        if opposite and better.get(opposite) is not None:
            cause = WRONG_DIRECTION
            note = f"the {opposite} side netted {better[opposite]:+.2f}% on the same book"
        else:
            cause = WRONG_VEHICLE
            note = f"another vehicle on the same observation netted {max(better.values()):+.2f}%"
    elif giveback.get("adverse_first") and t1 > 0 and peak >= t1:
        cause = BAD_ENTRY
        note = "the adverse move came first and the leg later cleared T1"
    elif cost_pct is not None and peak < cost_pct:
        cause = INSUFFICIENT_ROOM
        note = (f"best look {peak:+.2f}% never reached the {cost_pct:.2f}% round "
                f"trip; the opportunity could not pay for itself")

    return {
        "primary_cause": cause,
        "channel": channel(leg, cost, giveback,
                           better_vehicle=bool(better)),
        "evidence": evidence if evidence == MEASURED_EXECUTABLE else evidence,
        "note": note,
    }


def rows(legs: list[dict]) -> list[dict]:
    """Attribution rows for storage, one per leg that has enough to be graded."""
    out: list[dict] = []
    for leg in legs:
        cost = leg.get("cost") or {}
        gb = leg.get("giveback") or {}
        verdict = classify(
            leg, cost=cost, giveback=gb, targets=leg.get("targets") or {},
            peers=leg.get("peers") or {},
        )
        parts = decompose(leg, cost)
        out.append({
            "leg_id": leg["leg_id"],
            "primary_cause": verdict["primary_cause"],
            "channel": verdict["channel"],
            **parts,
            "peak_pct": gb.get("peak_pct"),
            "time_to_peak_min": gb.get("time_to_peak_min"),
            # NULL unless the peak was placed less precisely than the sample
            # that made it, in which case the basis is named on the row.
            "time_resolution": gb.get("resolution"),
            "giveback_pct": gb.get("max_giveback_pct"),
            "evidence": verdict["evidence"],
            "note": verdict["note"],
        })
    return out


def histogram_from_store(con) -> dict:
    """The same histogram, counted by SQLite instead of in Python.

    ``leg_attribution`` is one row per paper leg — hundreds of thousands of them
    on a real store — and the histogram is four numbers. Reading the table into a
    list to count it is what made the report unrunnable on an 8 GB machine.
    """
    causes = {
        str(r["primary_cause"]): int(r["n"])
        for r in con.execute(
            "SELECT primary_cause, COUNT(*) AS n FROM leg_attribution"
            " GROUP BY primary_cause"
        )
    }
    channels = {
        str(r["channel"]): int(r["n"])
        for r in con.execute(
            "SELECT channel, COUNT(*) AS n FROM leg_attribution"
            " WHERE channel IS NOT NULL GROUP BY channel"
        )
    }
    return _histogram(sum(causes.values()), sum(channels.values()),
                      causes, channels)


def histogram(attributions: list[dict]) -> dict:
    """Counts by cause and by channel, with the unprovable causes named."""
    causes: dict[str, int] = {}
    channels: dict[str, int] = {}
    graded = 0
    for row in attributions:
        causes[row["primary_cause"]] = causes.get(row["primary_cause"], 0) + 1
        ch = row.get("channel")
        if ch is None:
            continue
        graded += 1
        channels[ch] = channels.get(ch, 0) + 1
    return _histogram(len(attributions), graded, causes, channels)


def _histogram(rows: int, graded: int, causes: dict[str, int],
               channels: dict[str, int]) -> dict:
    return {
        "rows": rows,
        "graded": graded,
        "by_cause": dict(sorted(causes.items(), key=lambda kv: -kv[1])),
        "by_channel": dict(sorted(channels.items(), key=lambda kv: -kv[1])),
        "unprovable_causes": list(UNPROVABLE_CAUSES),
        "unprovable_note": (
            "WRONG_STRIKE needs the neighbouring strike's own bid/ask at the same "
            "instant; the store keeps the strike window as context, not as "
            "executable books, so this cause is never asserted"
        ),
    }
