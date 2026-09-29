"""Phase 41 CLI — read only, research only, paper only.

    python -m app.research.phase41.cli run
    python -m app.research.phase41.cli report
    python -m app.research.phase41.cli freeze
    python -m app.research.phase41.cli status

``run`` re-runs Phase 40's diagnostic unchanged over every session in the store,
records the definition it ran under, and prints how the answer is moving.
``freeze`` prints the fingerprint alone, which is the cheap way to check whether
two machines are running the same measurement. Nothing here collects data,
changes a definition, tests an exit or places an order.
"""
from __future__ import annotations

import argparse
import json

from app.research.phase35 import store
from app.research.phase40 import UNMEASURED
from app.research.phase41 import (
    ARTEFACT_DIR,
    CHANGED,
    DESCRIPTIVE_FRAME,
    DOMINATED,
    GOVERNING_FRAME,
    ONE_SESSION,
    REPORTING_RULE,
    UNSTABLE,
    freeze,
)
from app.research.phase41 import report as p41report
from app.research.phase41 import service as p41service


def _u(value: object, suffix: str = "") -> str:
    return UNMEASURED if value is None else f"{value}{suffix}"


def summary(payload: dict) -> dict:
    acc = payload["accumulation"]
    return {
        "phase": payload["phase"],
        "version": payload["version"],
        "mode": payload["mode"],
        "frozen_definition": payload["frozen_definition"]["definition"],
        "definition_state": payload["definition_state"]["state"],
        "reporting_rule": REPORTING_RULE,
        "governing_frame": GOVERNING_FRAME,
        "sessions": payload["sessions"],
        "legs": payload["legs"],
        # The verdict is the governing frame's. The overlapping frame is kept
        # beside it under a name that cannot be mistaken for a test.
        "verdict": acc["verdict"],
        "verdict_all_legs_descriptive": (
            acc[DESCRIPTIVE_FRAME]["pooled"]["verdict"]
        ),
        "all_legs_z_valid_for_testing": (
            acc[DESCRIPTIVE_FRAME]["z_valid_for_testing"]
        ),
        "stability": acc["stability"],
        "resolution": acc["resolution"],
        "frames_agree": acc["frames_agree"],
        "not_a_strategy": payload["not_a_strategy"],
    }


def status(payload: dict) -> str:
    """The short read: what it says, how settled it is, what would settle it."""
    acc = payload["accumulation"]
    indep = acc[GOVERNING_FRAME]["pooled"]
    pooled = acc[DESCRIPTIVE_FRAME]["pooled"]
    stab = acc["stability"]
    res = acc["resolution"]
    lines = [
        "FROZEN DEFINITION:",
        f"{payload['frozen_definition']['definition']} "
        f"({payload['definition_state']['state']})",
        "",
        "STANDING ANSWER (non-overlapping windows — the only frame that is a "
        "test):",
        f"{indep['verdict']} over {len(payload['sessions'])} session(s), "
        f"{indep['classified']} classified legs",
        f"- favourable-first {_u(indep['favourable_first_pct'], '%')}, "
        f"adverse-first {_u(indep['adverse_first_pct'], '%')}, "
        f"z={_u(indep['race_test']['z'])}",
        f"- {stab['stability']}: {stab['because']}",
        "",
        "DESCRIPTIVE ONLY — every raced leg, windows overlapping:",
        f"{pooled['verdict']} on {pooled['classified']} classified legs, "
        f"favourable-first {_u(pooled['favourable_first_pct'], '%')}, "
        f"adverse-first {_u(pooled['adverse_first_pct'], '%')}, "
        f"z={_u(pooled['race_test']['z'])} — NOT A TEST: these windows "
        f"re-measure the same price swings, so this z rises with how densely "
        f"the session was sampled and would cross any threshold on sampling "
        f"alone. It is not the verdict and must not be quoted as one.",
        "",
        "WHAT WOULD SETTLE IT:",
    ]
    rule = payload.get("reporting_rule_state") or {}
    if rule.get("changed"):
        lines.append(
            "First, note the verdict on record before this run was quoted from "
            "the overlapping frame. The definition did not change and no "
            "measured number moved — only which frame is quoted — so a "
            "headline that differs from an earlier paste differs for that "
            "reason."
        )
    if payload["definition_state"]["state"] == CHANGED:
        lines.append(
            "First, note the definition changed since the last recorded run "
            f"({', '.join(payload['definition_state']['changed_components'])})"
            ". Earlier artefacts are a different measurement of the same days "
            "and must not be compared with this one."
        )
    if res.get("already_material"):
        lines.append(
            "The split is already outside the declared deviate on "
            "non-overlapping windows; the question now is whether it survives "
            "the next sessions, not whether it exists."
        )
    elif res.get("classified_needed_at_this_share"):
        lines.append(
            f"About {res['classified_needed_at_this_share']} non-overlapping "
            f"classified legs at today's share — {res['multiple_of_current']}x "
            f"the {res['classified_now']} measured so far. This is arithmetic "
            f"at the observed share, not a forecast: an even split never "
            f"resolves, and an even split is itself a finding."
        )
    elif res.get("measurable"):
        lines.append(res["note"])
    else:
        lines.append(
            "Nothing has raced yet, so there is nothing to settle. Capture "
            "first."
        )
    if stab["stability"] in (UNSTABLE, DOMINATED, ONE_SESSION):
        lines.append(
            "Until that changes, no mechanism should be altered on the "
            "strength of this figure."
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="phase41",
        description=(
            "freeze the Phase 40 diagnostic and accumulate sessions under it "
            "(read-only; no definition is changed and no mechanism proposed)"
        ),
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_text in (
        ("run", "re-run the frozen diagnostic; write the md + json artefacts"),
        ("report", "print the whole markdown document"),
        ("status", "print the standing answer and what would settle it"),
        ("freeze", "print the definition fingerprint and exit"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument(
            "--instrument", default=None,
            help="optional filter; default is every instrument captured",
        )
        p.add_argument(
            "--no-artefacts", action="store_true",
            help="print only; do not write artefacts or record the run",
        )

    args = ap.parse_args(argv)
    if args.cmd == "freeze":
        print(json.dumps(freeze.fingerprint(), indent=2, sort_keys=True))
        return 0

    instrument = (args.instrument or "").strip().upper() or None
    con = store.connect()
    try:
        payload = p41service.run(
            con, instrument=instrument,
            directory=None if args.no_artefacts else ARTEFACT_DIR,
        )
        artefacts = None
        if not args.no_artefacts:
            artefacts = p41report.write(payload)
        if args.cmd == "report":
            print(p41report.render(payload))
        elif args.cmd == "status":
            print(status(payload))
        else:
            print(json.dumps(summary(payload), indent=2, default=str))
            print()
            print(status(payload))
        for path in (artefacts or {}).values():
            print(f"  wrote {path}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":  # pragma: no cover - console entry
    raise SystemExit(main())
