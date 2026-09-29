"""Phase 42 — the payload, the ledger and the printed answer.

Every figure carries the fingerprint of the definition that produced it, and the
run records itself in an append-only ledger so a later reader can tell whether
two tables are comparable at all. The pooled numbers are printed because they
describe the book, and labelled descriptive because they are not a test: the
arms were measured on the same legs, so the best of them is optimistic by
construction and only the holdout figure answers anything.
"""
from __future__ import annotations

import json
import os
import sqlite3

from app.research.phase35 import store as p35store
from app.research.phase41 import CHANGED, FIRST_RUN, UNCHANGED
from app.research.phase42 import (
    ARMS_TESTED_NOTE,
    ARTEFACT_DIR,
    BASIS_CHANGED,
    JSON_NAME,
    LEDGER_NAME,
    LIVE_MULTIPLE,
    MD_NAME,
    MIN_SESSION_LEGS,
    MIN_SESSIONS,
    NOT_A_PROMOTION,
    PAPER_ONLY,
    PHASE,
    RANGE_PROXY,
    RECORD_ONLY,
    RESEARCH_ONLY,
    THRESHOLDS,
    VERSION,
)
from app.research.phase42 import arms as p42arms
from app.research.phase42 import freeze as p42freeze
from app.research.phase42 import giveback as p42giveback
from app.research.phase42 import validate as p42validate


def _artefact_dir() -> str:
    return os.path.join(os.path.dirname(p35store.db_path()), "..", ARTEFACT_DIR)


def _dir() -> str:
    return os.path.normpath(_artefact_dir())


def ledger_state(definition: str) -> dict:
    """Whether this definition has been run before, from the append-only ledger."""
    path = os.path.join(_dir(), LEDGER_NAME)
    if not os.path.exists(path):
        return {"state": FIRST_RUN, "previous": None}
    previous = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("definition"):
                previous = str(row["definition"])
    if previous is None:
        return {"state": FIRST_RUN, "previous": None}
    state = UNCHANGED if previous == definition else CHANGED
    return {"state": state, "previous": previous}


def append_ledger(payload: dict) -> str:
    """Record the run. Append-only, and outside the raw store it reads."""
    os.makedirs(_dir(), exist_ok=True)
    path = os.path.join(_dir(), LEDGER_NAME)
    row = {
        "definition": payload["frozen_definition"],
        "components": payload["frozen_components"],
        "version": VERSION,
        "sessions": payload["sessions"],
        "verdict": payload["verdict"]["verdict"],
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def run(
    con: sqlite3.Connection, *, book: str | None = None,
    instrument: str | None = None, with_giveback: bool = True,
) -> dict:
    """One pass: sweep the arms, split chronologically, decompose the giveback."""
    frozen = p42freeze.fingerprint()
    results = p42arms.sweep(con, book=book, instrument=instrument)
    pool = p42arms.pooled(results)
    one_split = p42validate.split(results)
    forward = p42validate.walk_forward(results)
    payload = {
        "phase": PHASE,
        "version": VERSION,
        "mode": [RESEARCH_ONLY, PAPER_ONLY, RECORD_ONLY],
        "frozen_definition": frozen["definition"],
        "frozen_components": frozen["components"],
        "declared": frozen["declared"],
        "definition_state": ledger_state(frozen["definition"])["state"],
        "book": book,
        "instrument": instrument,
        "live_multiple": LIVE_MULTIPLE,
        "sessions": [r.session for r in results],
        "sessions_measured": len(results),
        "sessions_floor": MIN_SESSIONS,
        "pooled_descriptive": pool.payload(),
        "per_session": [r.payload() for r in results],
        "chronological_split": one_split,
        "walk_forward": forward,
        "verdict": p42validate.verdict(one_split, forward),
        "arms_tested_note": ARMS_TESTED_NOTE.format(n=len(THRESHOLDS)),
        "range_proxy": RANGE_PROXY,
        "not_a_promotion": NOT_A_PROMOTION,
    }
    if with_giveback:
        payload["giveback_by_horizon"] = p42giveback.decompose(con)
    return payload


def _row(stats: dict | None) -> str:
    if not stats or stats.get("legs", 0) == 0:
        return "        —"
    mean = stats.get("mean_net_pct")
    win = stats.get("win_pct")
    factor = stats.get("profit_factor")
    return (f"{stats['legs']:>9} {('—' if mean is None else f'{mean:+.2f}%'):>9}"
            f" {('—' if win is None else f'{win:.1f}%'):>8}"
            f" {('—' if factor is None else f'{factor:.3f}'):>8}")


def _basis(tally: object) -> str:
    """A cost-basis tally as text, or a marker when nothing was recorded."""
    if not isinstance(tally, dict) or not tally:
        return "—"
    return ", ".join(f"{label} {count}" for label, count in sorted(
        tally.items(), key=lambda pair: (-int(pair[1]), pair[0])))


def _pool_legs(payload: dict, wanted: list[str], key: str) -> int:
    """Legs of one evidence pool over a named set of sessions."""
    keep = set(wanted)
    return sum(row[key]["legs"] for row in payload["per_session"]
               if row["session"] in keep)


def _split_lines(payload: dict) -> list[str]:
    """The holdout table, and where the measurable legs actually fell.

    A split can be arithmetically valid and still carry no test, when every leg
    whose economics were measurable happens to sit on one side of the cut. That
    is invisible from the arm rows alone — they simply read the same as the
    pooled ones — so the counts are printed beside them.
    """
    split = payload["chronological_split"]
    if split.get("status") != "MEASURED":
        return [f"CHRONOLOGICAL HOLDOUT: {split.get('status')} — "
                f"{split.get('because', '')}"]
    dev, holdout = split["dev_sessions"], split["holdout_sessions"]
    chosen = split["chosen_on_dev"]["multiple"]
    lines: list[str] = []
    if chosen is None:
        lines.append(
            f"CHOSEN ON {len(dev)} SESSION(S): NO ARM MET THE LEG FLOOR THERE "
            f"— {split['chosen_on_dev'].get('reason')}",
        )
    else:
        lines.append(
            f"CHOSEN ON {len(dev)} SESSION(S), TESTED ON {len(holdout)} IT "
            f"NEVER SAW: {chosen:.1f}x",
        )
    basis = split.get("cost_basis_across_the_cut") or {}
    lines.append(
        f"  how the round trip was costed — choosing half "
        f"{_basis(basis.get('dev'))}; tested half "
        f"{_basis(basis.get('holdout'))}",
    )
    if basis.get("status") == BASIS_CHANGED:
        lines.append(f"  {basis.get('because', BASIS_CHANGED)}")
    lines.append(
        f"  measurable legs — choosing half "
        f"{_pool_legs(payload, dev, 'measured')}, tested half "
        f"{_pool_legs(payload, holdout, 'measured')}; unmeasurable "
        f"{_pool_legs(payload, dev, 'unmeasured')} / "
        f"{_pool_legs(payload, holdout, 'unmeasured')}",
    )
    for row in split["holdout_all_arms"]:
        mark = ""
        if chosen is not None and row["multiple"] == chosen:
            mark = " <- chosen"
        elif row["multiple"] == payload["live_multiple"]:
            mark = " <- live"
        lines.append(f"  {row['multiple']:>5.1f}x {_row(row)}{mark}")
    if chosen is None:
        lines.append(
            "  these are the tested half's own arms, and with no arm chosen on "
            "the other half none of them tests anything: they describe the "
            "sessions that happen to carry the evidence",
        )
    return lines


def _session_lines(payload: dict) -> list[str]:
    """Per session: how much of the book could be measured at all."""
    lines = ["", "PER SESSION — WHERE THE EVIDENCE IS, AND HOW IT WAS COSTED",
             "  session         measurable  unmeasurable  cost basis"]
    carrying = 0
    for row in payload["per_session"]:
        if row["measured"]["legs"] >= MIN_SESSION_LEGS:
            carrying += 1
        lines.append(
            f"  {row['session']:<14} {row['measured']['legs']:>11}"
            f" {row['unmeasured']['legs']:>13}  "
            f"{_basis(row.get('cost_bases'))}")
    lines.append(
        f"  {carrying} of {len(payload['per_session'])} session(s) carry at "
        f"least {MIN_SESSION_LEGS} measurable legs, and only those are split "
        f"on: a session captured with one side of the book grades nothing",
    )
    return lines


def _pct(value: object) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{value:.1f}%"


def _giveback_lines(decomposed: dict | None) -> list[str]:
    """The §2 table: what each horizon kept of the peak it had reached."""
    if not decomposed or not decomposed.get("by_channel"):
        return []
    lines = ["", "GIVEBACK BY HORIZON — DESCRIPTIVE, CHANGES NO EXIT RULE"]
    for name, block in decomposed["by_channel"].items():
        peak = block.get("peak", {})
        lines.append(
            f"  {name}: {peak.get('legs', 0)} legs, mean peak "
            f"{_pct(peak.get('mean_peak_pct'))}, time to peak "
            f"{peak.get('mean_time_to_peak_min') or '—'} min",
        )
        lines.append(
            "    horizon      legs      mean      MFE  kept%   back%   neg%"
            "     T1%",
        )
        for row in block["horizons"]:
            mean = row.get("mean_net_pct")
            lines.append(
                f"    {row['horizon']:>8} {row.get('legs', 0):>9}"
                f" {('—' if mean is None else f'{mean:+.2f}%'):>9}"
                f" {_pct(row.get('mean_mfe_pct')):>8}"
                f" {_pct(row.get('mean_retained_share_of_peak_pct')):>6}"
                f" {_pct(row.get('returned_to_entry_or_worse_pct')):>7}"
                f" {_pct(row.get('turned_negative_pct')):>6}"
                f" {_pct(row.get('t1_pct')):>7}",
            )
        lines.append(
            f"    best horizon in hindsight beat the close on "
            f"{block['legs_where_some_horizon_beat_the_close']} of "
            f"{block['legs_with_a_close_figure']} legs",
        )
    lines.append(f"  {decomposed['retained_note']}")
    lines.append(f"  {decomposed['hindsight_note']}")
    return lines


def render(payload: dict) -> str:
    """The printed answer. Rendering only — it computes nothing."""
    lines: list[str] = []
    lines.append(
        f"PHASE 42 — EX-ANTE ENTRY ECONOMICS, {payload['sessions_measured']} "
        f"session(s), definition {payload['frozen_definition']} "
        f"({payload['definition_state']})",
    )
    lines.append(
        "  the gate's own quantity: |delta| x trailing typical minute move, "
        "over the leg's own round-trip cost. Nothing here reads the outcome.",
    )
    lines.append("")
    lines.append("POOLED OVER ALL SESSIONS — DESCRIPTIVE, NOT A TEST")
    lines.append("  multiple      legs      mean     win %      PF")
    for row in payload["pooled_descriptive"]["arms"]:
        marker = " <- live" if row["multiple"] == payload["live_multiple"] else ""
        lines.append(
            f"  {row['multiple']:>5.1f}x {_row(row['measured_only'])}{marker}")
    unmeasured = payload["pooled_descriptive"]["unmeasured"]
    lines.append(
        f"  legs whose economics could not be measured: {unmeasured['legs']} "
        f"(not refused by the live gate, so not refused here)",
    )
    lines.extend(_session_lines(payload))
    lines.append("")
    lines.extend(_split_lines(payload))
    lines.extend(_giveback_lines(payload.get("giveback_by_horizon")))
    lines.append("")
    lines.append(f"VERDICT: {payload['verdict']['verdict']}")
    provisional = payload["verdict"].get("provisional_label")
    if provisional:
        lines.append(
            f"  the holdout arithmetic reads {provisional}, and it is reported "
            "as description because:")
        for reason in payload["verdict"].get("withheld_because", []):
            lines.append(f"    - {reason}")
    else:
        lines.append(f"  {payload['verdict'].get('because', '')}")
    if (payload["chronological_split"].get("status") == "MEASURED"
            and payload["chronological_split"]["chosen_on_dev"]["multiple"]
            is None):
        lines.append(
            "  nothing was carried into the tested half because no arm met the "
            "leg floor on the choosing half, so the label reports missing "
            "evidence there rather than an arm that was tested and failed",
        )
    lines.append(f"  {payload['arms_tested_note']}")
    lines.append(f"  {payload['range_proxy']}")
    lines.append(f"  {payload['not_a_promotion']}")
    return "\n".join(lines)


def write(payload: dict) -> list[str]:
    """The artefacts, plus the ledger row. Returns what was written."""
    os.makedirs(_dir(), exist_ok=True)
    written = []
    json_path = os.path.join(_dir(), JSON_NAME)
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    written.append(json_path)
    md_path = os.path.join(_dir(), MD_NAME)
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write("# " + PHASE + "\n\n```\n" + render(payload) + "\n```\n")
    written.append(md_path)
    written.append(append_ledger(payload))
    return written
