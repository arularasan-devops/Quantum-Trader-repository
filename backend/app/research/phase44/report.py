"""Phase 44 §5 — the status page, written to be read by a sceptic.

Three things it must say every time, because each has already been got wrong
once in this project: whether the recorder is on, which definition the rows
were measured under, and that a firing rate is not a result. It reports no
returns, because none are measured.
"""
from __future__ import annotations

import sqlite3

from app.research import phase44
from app.research.phase44 import freeze, store, switch


def progress(tally: dict) -> dict:
    """How far the sample is from the floors declared before any row existed."""
    sessions = int(tally.get("sessions") or 0)
    admitted = int(tally.get("admitted") or 0)
    return {
        "sessions": sessions,
        "admitted": admitted,
        "target_sessions": phase44.TARGET_SESSIONS,
        "target_trades": phase44.TARGET_TRADES,
        "sessions_remaining": max(0, phase44.TARGET_SESSIONS - sessions),
        "trades_remaining": max(0, phase44.TARGET_TRADES - admitted),
        "at_floor": (sessions >= phase44.TARGET_SESSIONS
                     and admitted >= phase44.TARGET_TRADES),
    }


def status(con: sqlite3.Connection) -> dict:
    tally = store.tally(con)
    frozen = freeze.fingerprint()
    return {
        "phase": phase44.PHASE,
        "version": phase44.VERSION,
        "mode": phase44.RECORD_ONLY,
        "switch": switch.state(con),
        "definition": frozen["definition"],
        "components": frozen["components"],
        "candidate": phase44.SOURCE_CANDIDATE,
        "tally": tally,
        "progress": progress(tally),
        "sessions_recorded": store.sessions(con),
        "not_a_promotion": phase44.NOT_A_PROMOTION,
    }


def render(payload: dict) -> str:
    """The status as text."""
    sw = payload["switch"]
    p = payload["progress"]
    t = payload["tally"]
    out = [
        f"PHASE 44 — {payload['candidate']}",
        f"  mode          : {payload['mode']}",
        f"  recorder      : {sw['state']}"
        + ("" if sw["may_record"] else f"  ({sw['refusal']})"),
        f"  definition    : {payload['definition']}",
        f"  armed under   : {sw['armed_under'] or '—'}",
        "",
        "  This phase records what the frozen arm would have seen at each",
        "  captured decision instant. It resolves no outcomes and measures no",
        f"  returns. {payload['not_a_promotion']}.",
        "",
        "  JOURNAL",
        f"    rows                  : {t.get('rows_total') or 0}",
        f"    sessions              : {t.get('sessions') or 0}",
        f"    measurable            : {t.get('measurable') or 0}",
        f"      of which captured lot : {t.get('measured_captured_lot') or 0}",
        f"      of which spec lot     : {t.get('measured_spec_lot') or 0}",
        f"    admitted (measured)   : {t.get('admitted') or 0}",
        f"    admitted (modelled)   : {t.get('admitted_modelled') or 0}",
        "",
        "  COST, MODELLED vs MEASURED",
        "    Phase 43 divided by a cost with no spread in it, because "
        "five-year",
        "    candles have no book. Live, the spread is paid. If the measured",
        "    cost is materially larger, the historical 8x arm fires less often",
        "    live than it did historically — that gap is this phase's first",
        "    finding, and it does not need the full sample to be visible.",
        f"    mean modelled cost    : {_num(t.get('avg_modelled_cost'))} points",
        f"    mean measured cost    : {_num(t.get('avg_measured_cost'))} points",
        f"    mean quoted spread    : {_num(t.get('avg_spread'))} points",
        "",
        "  SAMPLE, against floors declared before any row existed",
        f"    sessions   : {p['sessions']} of {p['target_sessions']}"
        f"  ({p['sessions_remaining']} to go)",
        f"    admissions : {p['admitted']} of {p['target_trades']}"
        f"  ({p['trades_remaining']} to go)",
        f"    at floor   : {'YES' if p['at_floor'] else 'NO'}",
    ]
    if not p["at_floor"]:
        out += [
            "",
            "    Below the floor nothing here may be read as evidence for or",
            "    against the arm. Reaching the floor earns a measurement, not",
            "    a verdict, and the phase that judges it will be "
            "pre-registered",
            "    before it is written.",
        ]
    return "\n".join(out)


def _num(value: object) -> str:
    return "—" if not isinstance(value, (int, float)) else f"{float(value):,.2f}"


def render_preview(payload: dict) -> str:
    """One session's dry run, shown without writing anything."""
    t = payload["tally"]
    reasons = t["unmeasurable_reasons"]
    out = [
        f"PHASE 44 PREVIEW — {payload['session']}  {payload['instrument']}",
        f"  definition          : {payload['definition']}",
        "  nothing was written; this is what the arm would have journalled",
        "",
        f"  captured minutes    : {payload['minutes']}",
        f"  decision instants   : {t['decisions']}",
        f"  measurable          : {t['measurable']}",
        f"    spread + captured lot     : {t['measured_captured_lot']}",
        f"    spread + spec lot         : {t['measured_spec_lot']}",
        f"  base rule admits    : {t['base_admits']}",
        f"  arm admits (measured cost)  : {t['admits']}",
        f"  arm admits (modelled cost)  : {t['admits_modelled']}",
        f"  measured/modelled cost      : {_num(t['measured_over_modelled_cost'])}x"
        f"  over {t['comparable_costs']} instants",
    ]
    out += _ratio_block(t)
    if reasons:
        out += ["", "  UNMEASURABLE, by reason"]
        out += [f"    {k:<48} {v}" for k, v in reasons.items()]
    return "\n".join(out)


def _ratio_block(tally: dict) -> list[str]:
    """How far the ratio actually gets, over the base-admitting instants.

    Descriptive only. "The arm never fired" cannot be read without knowing
    whether the threshold was missed narrowly or by an order of magnitude, and
    the answer decides whether the arm is worth accumulating at all — but it is
    not a licence to move the threshold, which was frozen before measurement.
    """
    out: list[str] = []
    for label, key in (("measured", "ratio_measured_spread"),
                       ("modelled", "ratio_modelled_spread")):
        s = tally.get(key)
        if not isinstance(s, dict) or not s.get("n"):
            continue
        if not out:
            out += ["", f"  RATIO vs THE FROZEN {phase44.THRESHOLD:g}x, over the"
                        " instants the base conditions admitted",
                    "    reported, not applied — the threshold stays where it"
                    " was frozen"]
        rungs = "  ".join(f"{k} {v}" for k, v in s["at_or_above"].items())
        out += [
            f"    {label:<8} n {s['n']}   median {_num(s['median'])}"
            f"   p90 {_num(s['p90'])}   p99 {_num(s['p99'])}"
            f"   max {_num(s['max'])}",
            f"    {'':<8} at or above:  {rungs}",
        ]
    return out
