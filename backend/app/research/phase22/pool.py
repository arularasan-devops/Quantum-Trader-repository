"""Labelling the candidate pool against the frozen Phase 22 definition.

The pool is every opportunity the replay saw, INCLUDING the ones the production
engine refused (``signal`` WAIT / AVOID, ``taken`` false). Those rows are the
whole point: a definition graded only on trades that were actually taken is
graded through the gate stack that took them, and the result then describes the
gates rather than the setup.

Universe families are kept separate everywhere they are reported. Pooling INDEX
with EQUITY and MCX averages instruments whose point value, noise and cost are
different by an order of magnitude, and an average across them is not a number
anyone can trade.
"""
from __future__ import annotations

from app.research.phase22 import definition as defn

INDEX = "INDEX"
EQUITY = "EQUITY"
MCX = "MCX"
UNKNOWN_FAMILY = "UNKNOWN"

INDEX_NAMES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX",
               "BANKEX", "NIFTYNXT50")
MCX_NAMES = ("CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATURALGASM", "GOLD",
             "GOLDM", "SILVER", "SILVERM", "COPPER", "ZINC", "ALUMINIUM",
             "LEAD", "NICKEL")

ENGINE_BUY = "ENGINE_BUY"
ENGINE_REFUSED = "ENGINE_REFUSED"


def family(instrument: object) -> str:
    name = str(instrument).upper() if isinstance(instrument, str) else ""
    if not name:
        return UNKNOWN_FAMILY
    if name in INDEX_NAMES:
        return INDEX
    if name in MCX_NAMES:
        return MCX
    return EQUITY


def engine_view(row: dict) -> str:
    """What the production engine did with this candidate, for the counterfactual."""
    signal = row.get("signal")
    return ENGINE_BUY if str(signal).upper() == "BUY" else ENGINE_REFUSED


def label_rows(rows: list[dict]) -> list[dict]:
    """Attach the frozen label, its refusals and the family. Additive only.

    The input rows are copied, never mutated: the pool file on disk stays as the
    replay wrote it, so a later re-run of the same study on the same file cannot
    be contaminated by this one.
    """
    out: list[dict] = []
    for row in rows:
        refusals = defn.refusals(row)
        copy = dict(row)
        copy["p22_label"] = defn.LABEL if not refusals else defn.OTHER
        copy["p22_refusals"] = refusals
        copy["p22_family"] = family(row.get("instrument"))
        copy["p22_engine"] = engine_view(row)
        copy["p22_expected_move_points"] = defn.expected_move_points(row)
        out.append(copy)
    return out


def selector(row: dict) -> bool:
    """The selector the walk-forward grades. Reads the frozen label only."""
    return row.get("p22_label") == defn.LABEL


def split(rows: list[dict]) -> dict:
    """PULLBACK / NON_PULLBACK, by the frozen definition."""
    return {
        defn.LABEL: [r for r in rows if selector(r)],
        defn.OTHER: [r for r in rows if not selector(r)],
    }


def refusal_census(rows: list[dict]) -> list[dict]:
    """Which condition refuses how much of the pool.

    Read this before any expectancy number: a condition that refuses 99.9% of
    the pool leaves a sample too small to grade, and a condition that refuses
    nothing is not a condition.
    """
    total = len(rows)
    out = []
    for name in defn.CONDITIONS:
        n = sum(1 for r in rows if name in (r.get("p22_refusals") or []))
        # "Only refusal" = the candidate would be a setup if this one condition
        # were dropped, which is what makes a condition worth arguing about.
        only = sum(1 for r in rows if (r.get("p22_refusals") or []) == [name])
        out.append({
            "condition": name,
            "refused": n,
            "refused_pct": round(100.0 * n / total, 2) if total else None,
            "sole_refusal": only,
        })
    return out


def families(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(str(row.get("p22_family")), []).append(row)
    return out


def sessions(rows: list[dict]) -> list[str]:
    return sorted({str(r["session"]) for r in rows if r.get("session")})


def load(payload: dict | list) -> list[dict]:
    """Every candidate in a saved pool file, in chronological order.

    Accepts the pool shapes the earlier phases already write: a list of rows, or
    a dict carrying ``in_sample`` / ``holdout`` / ``trades`` / ``rows``. The
    saved in-sample and holdout split is deliberately NOT reused as this study's
    split — Phase 22 re-cuts the whole pool chronologically itself, so that its
    holdout is untouched by any earlier phase's fitting.
    """
    if isinstance(payload, list):
        rows = list(payload)
    else:
        rows = []
        for key in ("in_sample", "holdout", "trades", "rows", "candidates"):
            part = payload.get(key)
            if isinstance(part, list):
                rows.extend(part)
    # A pool file that carries both its own split AND a flattened copy would
    # otherwise be counted twice, which doubles every sample and halves nothing.
    seen: set[tuple] = set()
    unique = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("instrument")), str(row.get("session")),
               str(row.get("entry_ts")), str(row.get("side")),
               str(row.get("entry")), str(row.get("stop")))
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return sorted(
        unique,
        key=lambda r: (str(r.get("session") or ""),
                       str(r.get("entry_ts") or "").zfill(20)),
    )
