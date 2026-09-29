"""Phase 41 artefacts — written from the payload only, never recomputed.

Every figure here comes from :func:`app.research.phase41.service.run`, which
took all of them from Phase 40. Two rendering rules earn their place:

An unmeasured figure prints as ``UNMEASURED``, never as ``0`` or a blank cell —
a zero in a share column reads as "it never happened" when the truth is "nobody
looked".

The frozen definition hash is printed at the top of the document, not in an
appendix. A reader comparing this table with an older one needs to see, before
the numbers, whether the two were produced by the same measurement.

The non-overlapping frame is printed first and is the only one whose deviate is
called a test. The overlapping frame follows, labelled, because reading it first
is what makes a sampling artefact look like a finding — and order on a page is
part of what a report asserts.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.research.phase40 import UNMEASURED
from app.research.phase41 import (
    ARTEFACT_DIR,
    CHANGED,
    DESCRIPTIVE_FRAME,
    DOMINATED,
    GOVERNING_FRAME,
    JSON_NAME,
    MD_NAME,
    NOT_A_STRATEGY,
    REPORTING_RULE,
    UNSTABLE,
    VERSION,
)

DASH = "—"


def _f(value: object, suffix: str = "") -> str:
    if value is None:
        return UNMEASURED
    if isinstance(value, float):
        return f"{value:g}{suffix}"
    return f"{value}{suffix}"


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|")


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(_cell(h) for h in header) + " |",
           "|" + "|".join(["---"] * len(header)) + "|"]
    if not rows:
        out.append("|" + "|".join([f" {DASH} "] * len(header)) + "|")
    out += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    out.append("")
    return out


def _series_rows(rows: list[dict]) -> list[list[str]]:
    return [
        [
            r["label"], str(r["sessions"]), str(r["legs"]),
            str(r["classified"]), _f(r["favourable_first_pct"], "%"),
            _f(r["adverse_first_pct"], "%"), _f(r["race_z"]),
            "yes" if r["race_material"] else "no", r["verdict"],
        ]
        for r in rows
    ]


SERIES_HEADER = [
    "window", "sessions", "legs", "classified", "fav-first", "adv-first",
    "z", "outside the declared z", "verdict",
]


def _frame(block: dict, *, title: str, caveat: str) -> list[str]:
    pooled = block["pooled"]
    lines = [f"### {title}", "", caveat, ""]
    lines += [
        (f"Pooled: **{pooled['verdict']}**"
         if block.get("governs_verdict")
         else f"Pooled, descriptive only: {pooled['verdict']}")
        + f" — {pooled['because']}", "",
        "**Per session**, on that session's legs alone. A day is a small "
        "sample and most rows will say so.", "",
    ]
    if block.get("z_caveat"):
        lines += [f"`{block['z_caveat']}`", ""]
    lines += _table(SERIES_HEADER, _series_rows(block["per_session"]))
    lines += [
        "**Cumulatively**, in date order — the answer as it would have read "
        "after each session. This is the row to watch: a share drifting "
        "toward resolution and a share sitting at fifty-fifty are "
        "indistinguishable in a single total.", "",
    ]
    lines += _table(SERIES_HEADER, _series_rows(block["cumulative"]))
    if block["leave_one_out"]:
        lines += [
            "**With each session withheld.** A pooled verdict that survives "
            "every removal describes the sample; one that flips describes the "
            "day that was removed.", "",
        ]
        lines += _table(SERIES_HEADER, _series_rows(block["leave_one_out"]))
    stab = block["stability"]
    lines += [f"`{stab['stability']}` — {stab['because']}", ""]
    res = block["resolution"]
    if not block.get("governs_verdict"):
        # No projection for a frame whose deviate is not a test: "how many more
        # legs until it is material" reads as a schedule to a finding, and here
        # the only thing more legs of this kind buy is a larger denominator of
        # the same swings.
        lines += [
            "No resolution figure is projected for this frame. Asking when its "
            "deviate would become material would be asking when the sampling "
            "density becomes large enough to cross a threshold, which is not a "
            "question about the market.", "",
        ]
    elif res.get("classified_needed_at_this_share"):
        lines += [
            f"At the share observed today, the deviate would reach the "
            f"declared threshold at about "
            f"{res['classified_needed_at_this_share']} classified legs — "
            f"{res['multiple_of_current']}x the {res['classified_now']} "
            f"measured so far. {res['note']}", "",
        ]
    else:
        lines += [f"{res.get('note')}", ""]
    return lines


def render(payload: dict) -> str:
    acc = payload["accumulation"]
    frozen = payload["frozen_definition"]
    state = payload["definition_state"]
    lines = [
        "# FROZEN DIAGNOSTIC — SESSION ACCUMULATION",
        "",
        "READ_ONLY. RESEARCH_ONLY. PAPER_ONLY.",
        "NO NEW DATA WAS COLLECTED. NO PHASE 40 DEFINITION WAS CHANGED.",
        "NOTHING HERE PROMOTES, ENABLES OR VALIDATES ANYTHING.",
        "",
        f"PHASE41_VERSION = {VERSION}",
        f"FROZEN_DEFINITION = `{frozen['definition']}`",
        f"DEFINITION_STATE = {state['state']}",
        f"REPORTING_RULE = {REPORTING_RULE}",
        f"VERDICT_FRAME = {GOVERNING_FRAME} — the {DESCRIPTIVE_FRAME} frame is "
        f"printed but is not a test",
        f"SESSIONS = {len(payload['sessions'])}",
        f"LEGS = {payload['legs']}",
        f"REFERENCE_HORIZON = {payload['reference_horizon']} minutes",
        "",
        NOT_A_STRATEGY,
        "",
        "## 1. The definition these numbers were measured under",
        "",
        state["because"],
        "",
    ]
    if state["state"] == CHANGED:
        lines += [
            "**Do not compare any earlier artefact with this one.** The "
            "sessions themselves are unaffected — the raw store is never "
            "overwritten, so every session below was recomputed under the "
            "definition named above — but a table written under the previous "
            "definition is a different measurement of the same days.",
            "",
        ]
    lines += _table(
        ["component", "hash", "what it decides"],
        [
            ["race", frozen["components"]["race"],
             "which threshold was reached first, and from which price"],
            ["cost", frozen["components"]["cost"],
             "the required move: spread, brokerage, statutory, lot"],
            ["money", frozen["components"]["money"],
             "gross, net, peak, giveback and the per-horizon rows"],
            ["test", frozen["components"]["test"],
             "the deviate and the verdict rule"],
            ["declared", frozen["components"]["declared"],
             "horizons, floors, caps, reference hold, materiality z"],
        ],
    )
    lines += [
        f"Hashed: {frozen['hashed']}.", "",
        f"Not hashed: {frozen['not_hashed']}.", "",
    ]
    if payload["definition_history"]:
        lines += ["Earlier runs on record:", ""]
        lines += _table(
            ["when", "definition", "reporting rule", "sessions", "legs",
             "verdict as quoted then"],
            [[str(r.get("at_iso")), str(r.get("definition")),
              str(r.get("reporting_rule") or UNMEASURED),
              str(len(r.get("sessions") or [])), str(r.get("legs")),
              str(r.get("verdict"))]
             for r in payload["definition_history"]],
        )

    rule = payload.get("reporting_rule_state") or {}
    if rule.get("changed"):
        lines += [
            "## 1a. The frame this run quotes as its verdict", "",
            rule.get("because", ""), "",
            "Both frames were computed in every earlier run too, and both are "
            "printed below, so an earlier artefact is not invalidated — its "
            "headline was simply taken from the other table.", "",
        ]

    lines += ["## 2. The answer as the store deepens", ""]
    lines += _frame(
        acc[GOVERNING_FRAME],
        title="Non-overlapping windows only — the verdict",
        caveat=(
            "One window per contract per session per hold, chosen on "
            "timestamps alone so no outcome can influence which legs survive "
            "the cut. Far fewer legs, and the honest denominator: the only "
            "frame here whose deviate is a test."
        ),
    )
    lines += _frame(
        acc[DESCRIPTIVE_FRAME],
        title="Every raced leg — descriptive only, not a test",
        caveat=(
            "Overlapping windows. Sampled instants walked forward from the "
            "same session mostly describe the same few price swings, so these "
            "are not independent draws: the deviate rises with the square root "
            "of how densely the session was sampled and will eventually cross "
            "any threshold at an unchanged share, with no new fact having "
            "arrived. Read it for the shape of the whole opportunity set, "
            "never as evidence."
        ),
    )
    lines += [acc["frames_note"], "", acc["governing_note"], ""]

    lines += ["## 3. Per instrument", "",
              "The sequence view only. What each instrument's answer *is* "
              "belongs to Phase 40's own report, which this does not repeat.",
              ""]
    lines += _table(
        ["instrument", "sessions", "legs", "non-overlapping classified",
         "verdict (non-overlapping)", "all legs (descriptive, not a test)",
         "stability", "classified needed at today's share"],
        [
            [r["instrument"], str(r["sessions"]), str(r["legs"]),
             str(r["classified"]), r["verdict"],
             r["verdict_all_legs_descriptive"], r["stability"],
             _f(r["resolution"].get("classified_needed_at_this_share"))]
            for r in payload["per_instrument"]
        ],
    )

    lines += ["## 4. What this cannot say", "",
              "- **It cannot make an unresolved answer resolve.** More "
              "sessions raise the deviate only if the underlying split is "
              "uneven. An even split is a finding — no usable timing "
              "difference in this mechanism — and no amount of capture turns "
              "it into one.",
              "- **It cannot certify that the definition is right**, only "
              "that it did not change. The corrected race origin in Phase 40 "
              "shows a frozen definition can be frozen and wrong; freezing "
              "protects comparability, not correctness.",
              "- **It does not weight sessions by anything.** A quiet day and "
              "a violent one contribute their legs equally, and the "
              "leave-one-out table exists because of that.",
              "- **It cannot make the overlapping frame into evidence.** "
              "Its deviate is a function of how many instants were sampled per "
              "session, and capturing more densely raises it without "
              "measuring anything new. Only the non-overlapping count is a "
              "count of trials.",
              "- **It proposes nothing.** No exit, no entry, no instrument, "
              "no vehicle, no gate, no order path.",
              ""]
    if acc["stability"]["stability"] == UNSTABLE:
        lines += ["The cumulative verdict changed while the sample grew, so "
                  "no reading of it should be acted on yet.", ""]
    if acc["stability"]["stability"] == DOMINATED:
        lines += ["One session decides the pooled answer. Treat the pooled "
                  "figure as that session's figure until another lands.", ""]
    return "\n".join(lines).rstrip() + "\n"


def write(payload: dict, *, root: Path | str = ".") -> dict:
    out = Path(root) / ARTEFACT_DIR
    out.mkdir(parents=True, exist_ok=True)
    md, js = out / MD_NAME, out / JSON_NAME
    md.write_text(render(payload), encoding="utf-8")
    js.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str),
                  encoding="utf-8")
    return {"md": str(md), "json": str(js)}
