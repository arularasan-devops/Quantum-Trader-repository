"""Phase 40 CLI — read only, research only, paper only.

    python -m app.research.phase40.cli run
    python -m app.research.phase40.cli report
    python -m app.research.phase40.cli run --instrument CRUDEOIL

``--instrument`` is a filter, not a scope: with no filter every instrument in
the captured store is raced, CRUDEOIL first. There is no command here that
collects data, writes to raw, changes a gate, tests an exit or places an order.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store
from app.research.phase40 import (
    ENTRY_DOMINANT,
    EXIT_DOMINANT,
    INSUFFICIENT,
    UNMEASURED,
)
from app.research.phase40 import report as p40report
from app.research.phase40 import service as p40service


def summary(payload: dict) -> dict:
    """The short form — the verdict, its evidence, and what was not covered."""
    return {
        "phase": payload["phase"],
        "version": payload["version"],
        "mode": payload["mode"],
        "sessions": payload["sessions"],
        "legs": payload["legs"],
        "reference_horizon": payload["reference_horizon"],
        "coverage": payload["coverage"],
        "primary": payload["primary"],
        "primary_non_overlapping": {
            "verdict": payload["primary_non_overlapping"]["verdict"],
            "classified": payload["primary_non_overlapping"]["classified"],
            "favourable_first_pct": (
                payload["primary_non_overlapping"]["favourable_first_pct"]
            ),
            "adverse_first_pct": (
                payload["primary_non_overlapping"]["adverse_first_pct"]
            ),
        },
        "cross_instrument": payload["cross_instrument"],
        "not_a_strategy": payload["not_a_strategy"],
    }


def _u(value: object, suffix: str = "") -> str:
    """An unmeasured figure prints as UNMEASURED, never as ``None`` or 0."""
    return UNMEASURED if value is None else f"{value}{suffix}"


def answer(payload: dict) -> str:
    """§16 — the three-part answer, in the order it was asked for."""
    p = payload["primary"]
    alt = payload["primary_non_overlapping"]
    give = _u(p["given_back_fraction_of_mfe_pct_median"], "%")
    return "\n".join([
        "PRIMARY PROBLEM:",
        p["verdict"],
        "",
        "EVIDENCE:",
        f"- {p['because']}",
        f"- favourable-first {_u(p['favourable_first_pct'], '%')}, "
        f"adverse-first {_u(p['adverse_first_pct'], '%')}, neither "
        f"{_u(p['neither_pct'], '%')} of {p['classified']} classified legs "
        f"over {p['sessions']} session(s); {p['uncovered']} window(s) the "
        f"store could not cover",
        f"- median time to favourable "
        f"{_u(p['median_time_to_favourable_min'], ' min')}, to adverse "
        f"{_u(p['median_time_to_adverse_min'], ' min')}",
        f"- of favourable-first legs, {_u(p['given_back_pct'], '%')} ended "
        f"below their own round trip; median {give} of the peak was given back",
        f"- median net at the reference hold {_u(p['net_pct_median'], '%')}",
        f"- on non-overlapping windows only (n={alt['classified']}): "
        f"{alt['verdict']}",
        "",
        "NEXT ACTION:",
        recommendation(payload),
    ])


def recommendation(payload: dict) -> str:
    """One action, and it follows from the verdict rather than preceding it."""
    p = payload["primary"]
    if p["verdict"] == INSUFFICIENT:
        return (
            f"Do not change the mechanism. {p['because']} — keep capturing "
            f"sessions and re-run this diagnostic unchanged; it needs no new "
            f"code to answer once the store is deeper."
        )
    if p["verdict"] == EXIT_DOMINANT:
        return (
            f"The money arrives before the loss, so the next study is an exit "
            f"study — and it must be a study, not a switch: grade candidate "
            f"exits on these same stored paths with a chronological holdout "
            f"before any live exit changes. Median "
            f"{p['given_back_fraction_of_mfe_pct_median']}% of the peak is "
            f"the ceiling on what a perfect exit could recover, not the "
            f"expected gain."
        )
    if p["verdict"] == ENTRY_DOMINANT:
        return (
            "The loss arrives before the money, so an exit study would be "
            "measuring the wrong half. The next study is entry timing and "
            "direction on these same instants — no exit variant should be "
            "built until an entry exists whose adverse move does not come "
            "first."
        )
    return (
        "Neither half dominates at this sample, so changing either mechanism "
        "would be acting on noise. The one thing that resolves it is more "
        "sessions; re-run this diagnostic unchanged as the store deepens."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase40",
        description=(
            "favourable-first vs adverse-first: did the cost-clearing move "
            "arrive before or after an equally large adverse move "
            "(diagnostic only, no exit is tested)"
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("run", "the full diagnostic; writes the md + json artefacts"),
        ("report", "print the whole markdown document"),
        ("answer", "print only the three-part answer"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--instrument", default=None,
            help="optional filter; default is every instrument captured",
        )
        p.add_argument(
            "--no-artefacts", action="store_true",
            help="print only; do not write the md/json artefacts",
        )

    args = ap.parse_args(argv)
    instrument = (args.instrument or "").strip().upper() or None
    con = store.connect()
    try:
        payload = p40service.run(con, instrument=instrument)
        artefacts = None
        if not args.no_artefacts:
            artefacts = p40report.write(payload)
        if args.cmd == "report":
            print(p40report.render(payload))
        elif args.cmd == "answer":
            print(answer(payload))
        else:
            print(json.dumps(summary(payload), indent=2, default=str))
            print()
            print(answer(payload))
        for path in (artefacts or {}).values():
            print(f"  wrote {path}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
