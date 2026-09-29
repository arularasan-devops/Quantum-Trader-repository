"""Phase 43 §6 — render the study as text a reader can argue with.

The ranking table comes first because it is the answer; the answerability map
comes second because it is what makes the table trustworthy, and a reader who
sees "two families UNMEASURED" before the numbers reads the numbers correctly.

Money is printed in rupees per one lot (per leg notional for pairs) so "NET" is
a quantity rather than a score.
"""
from __future__ import annotations

import datetime as dt
import json
import os

from app.research import phase43
from app.research.phase43 import pair, registry
from app.research.phase43.evaluate import HOLD

WIDTH = 100
ARTEFACT_DIR = "data/phase43"
JSON_NAME = "phase43_study.json"
MD_NAME = "phase43_study.md"
REGISTRY_NAME = "phase43_candidate_registry.json"


def _money(v) -> str:
    if v is None:
        return "-"
    return f"{float(v):,.0f}"


def _ts(v: int) -> str:
    if not v:
        return "-"
    return dt.datetime.utcfromtimestamp(int(v) + 19_800).strftime("%Y-%m-%d")


def _rule(ch: str = "-") -> str:
    return ch * WIDTH


def _table(payload: dict) -> list[str]:
    out = [_rule("="), "RANKING — every declared candidate, leads first", _rule("=")]
    head = (f"{'CANDIDATE':<44}{'TRAIN NET':>12}{'VAL NET':>12}{'HOLD NET':>12}"
            f"{'PF':>7}{'TRADES':>8}{'SESS':>6}  {'STRESS':<9}STATUS")
    out.append(head)
    out.append(_rule())
    for r in payload["ranking"]:
        out.append(
            f"{r['candidate'][:44]:<44}"
            f"{_money(r['train_net']):>12}{_money(r['validation_net']):>12}"
            f"{_money(r['holdout_net']):>12}"
            f"{(r['profit_factor'] if r['profit_factor'] is not None else '-'):>7}"
            f"{r['trades']:>8}{r['sessions']:>6}  {r['cost_stress']:<9}"
            f"{r['status']}"
        )
        out.append(f"{'':<44}reason: {r['reason']}")
    return out


def _answerability(payload: dict) -> list[str]:
    a = payload["answerability"]
    out = ["", _rule("="), "WHAT THIS DATASET CAN AND CANNOT ANSWER", _rule("=")]
    for s in a["five_year_series"]:
        out.append(
            f"  {s['instrument']:<12} {s['bars']:>9,} bars  {s['sessions']:>5} "
            f"sessions  {_ts(s['first_ts'])} -> {_ts(s['last_ts'])}  "
            f"{s['granularity']}, no quoted book"
        )
    out.append(
        f"  captured-only instruments (weeks, not years): "
        f"{', '.join(a['captured_only_instruments']) or 'none'}"
    )
    out.append("")
    for f in a["families"]:
        out.append(f"  {f['family']}: "
                   f"{'MEASURABLE' if f['measurable'] else phase43.UNMEASURED}"
                   f" on {', '.join(f['on']) or '-'}")
        for t in f["measurable_terms"]:
            out.append(f"      measured   : {t}")
        for u in f["unmeasured_terms"]:
            out.append(f"      {u['status']:<10}: {u['term']} — {u['reason']}")
    return out


def _leads(payload: dict) -> list[str]:
    rows = {r["candidate"]: r for r in payload["candidates"]}
    leads = [rows[c] for c in payload["leads"]]
    out = ["", _rule("="), "TOP 3 HISTORICAL LEADS", _rule("=")]
    if not leads:
        out.append("  NONE. No declared candidate was positive after costs in "
                   "train, validation and holdout while surviving cost stress, "
                   "the outlier check and the false-discovery correction.")
    ranked = sorted(
        leads, key=lambda r: -(r["splits"][HOLD].get("net_total") or 0.0)
    )[:3]
    for i, r in enumerate(ranked, 1):
        h = r["splits"][HOLD]
        out += [
            f"  {i}. {r['candidate']} ({r['instrument']})",
            f"     {r['mechanism']}",
            f"     holdout: NET {_money(h.get('net_total'))} over "
            f"{h.get('trades', 0)} trades / {h.get('sessions', 0)} sessions, "
            f"PF {h.get('profit_factor')}, expectancy "
            f"{h.get('expectancy')}, win {h.get('win_rate_pct')}%, "
            f"max DD {_money(h.get('max_drawdown'))}",
            f"     descriptive MFE {_money(h.get('avg_mfe'))} / MAE "
            f"{_money(h.get('avg_mae'))} per trade — never used to select",
        ]
    return out


def _rejected(payload: dict) -> list[str]:
    """The three families that failed hardest, with the count behind each."""
    by_family: dict[str, list[dict]] = {}
    for r in payload["candidates"]:
        by_family.setdefault(r["family"], []).append(r)
    out = ["", _rule("="), "TOP 3 REJECTED / UNMEASURED FAMILIES", _rule("=")]
    scored = []
    for fam, rows in by_family.items():
        rejected = [r for r in rows if r["status"] == phase43.REJECTED]
        more = [r for r in rows if r["status"] == phase43.REQUIRES_MORE_DATA]
        leads = [r for r in rows if r["status"] == phase43.HISTORICAL_LEAD]
        worst = min(
            (r["splits"][HOLD].get("net_total") or 0.0) for r in rows
        ) if rows else 0.0
        scored.append((len(leads), worst, fam, len(rows), len(rejected), len(more)))
    for _, worst, fam, total, rejected, more in sorted(scored)[:3]:
        out.append(f"  {fam}: {rejected}/{total} rejected, {more} require more "
                   f"data, worst holdout NET {_money(worst)}")
        reasons = {r["reason"] for r in by_family[fam]
                   if r["status"] != phase43.HISTORICAL_LEAD}
        for reason in sorted(reasons)[:3]:
            out.append(f"      - {reason}")
    return out


def _vehicle(payload: dict) -> list[str]:
    """Family C: the same mechanism on each vehicle, and what is missing."""
    c = payload["family_c_vehicle_comparison"]
    out = ["", _rule("="), "FAMILY C — VEHICLE COMPARISON", _rule("=")]
    out.append("  The development-selected mechanism, priced on each vehicle "
               "this dataset actually contains:")
    for m in c["measured"]:
        out.append(
            f"      {m['vehicle']:<20} holdout NET {_money(m['holdout_net']):>12}"
            f"  over {m['holdout_trades']:>6} trades   {m['status']}"
        )
    for u in c["unmeasured"]:
        out.append(f"      {u['status']}: {u['comparison']} — {u['reason']}")
    skipped = payload.get("skipped_as_redundant") or []
    if skipped:
        out.append("")
        out.append("  Declared but not measured, because the state was already "
                   "implied by the baseline rule:")
        for s in skipped:
            out.append(f"      {s['candidate']} — {s['reason']}")
    return out


def _live_validation(payload: dict) -> list[str]:
    out = ["", _rule("="), "WHICH LEAD DESERVES REAL EXECUTABLE LIVE VALIDATION",
           _rule("=")]
    if not payload["leads"]:
        out += [
            "  None. Nothing here has earned the cost of a capture programme, "
            "which is a result: the alternative was to promote a candidate that "
            "is positive only before the spread this dataset cannot see.",
            "",
            "  The live capture that is already running remains the only route "
            "to an executable answer, and Phase 42's session floor is unchanged "
            "by this study.",
        ]
        # The fields and the sample size are still worth stating, because the
        # nearest miss is what a later study would have to answer. Naming it is
        # not a lead and it is not pre-registered as one.
        short = [r for r in payload["candidates"]
                 if r["status"] == phase43.REQUIRES_MORE_DATA
                 and (r["splits"][HOLD].get("net_total") or 0.0) > 0]
        if short:
            near = max(short,
                       key=lambda r: r["splits"][HOLD].get("net_total") or 0.0)
            out += ["", "  Nearest miss, for reference only — not a lead and "
                    "not pre-registered as one:"] + _sample_needed(near)
        return out
    rows = {r["candidate"]: r for r in payload["candidates"]}
    top = max(payload["leads"],
              key=lambda c: rows[c]["splits"][HOLD].get("net_total") or 0.0)
    return out + _sample_needed(rows[top])


def _sample_needed(r: dict) -> list[str]:
    """What an executable validation of one candidate would have to collect."""
    h = r["splits"][HOLD]
    per_session = h.get("trades", 0) / max(1, h.get("sessions", 1))
    need_trades = max(400, int(phase43.MIN_TRADES * 4))
    need_sessions = int(need_trades / max(0.1, per_session)) + 1
    out = [
        f"      {r['candidate']}",
        f"      {r['mechanism']}",
        "",
        "      Required fields per decision instant (the capture must record "
        "all of them, or the validation measures the model again):",
    ]
    out += [f"          - {f}" for f in registry.REQUIRED_LIVE_FIELDS]
    out += [
        "",
        f"      Minimum live sample: {need_trades} executable trades, which at "
        f"the historical rate of {per_session:.2f} trades per session is about "
        f"{need_sessions} sessions.",
        "      That is a floor from the trade count alone. It assumes every "
        "session captures both sides at the decision instant; a session "
        "captured one-sided contributes nothing, as five of Phase 42's eight "
        "did.",
    ]
    return out


def _registry(payload: dict) -> list[str]:
    out = ["", _rule("="), "CANDIDATE REGISTRY (machine-readable)", _rule("=")]
    if not payload["registry"]:
        out.append("  empty: a registry entry is written for leads only, and "
                   "there are none")
        return out
    for e in payload["registry"]:
        out.append(json.dumps(e, sort_keys=True))
    return out


def render(payload: dict) -> str:
    """The whole report."""
    fp = payload["fingerprint"]
    head = [
        _rule("="),
        f"PHASE 43 — HISTORICAL MECHANISM STUDY  (v{payload['version']})",
        _rule("="),
        f"  status      : {payload['status']}",
        f"  definition  : {fp['definition']}",
        "  components  : " + ", ".join(
            f"{k}={v[:8]}" for k, v in sorted(fp["components"].items())
        ),
        f"  hypotheses  : {payload['hypotheses_evaluated']} evaluated, "
        f"Benjamini-Hochberg at alpha={phase43.FDR_ALPHA}",
        f"  geometry    : stop {phase43.STOP_ATR} ATR, target "
        f"{phase43.T1_R}R, entry at the next bar's open",
        f"  cost basis  : modelled charges from the shared futures cost model, "
        f"no quoted spread in this dataset; stressed at "
        f"{', '.join(f'{m:g}x' for m in phase43.COST_MULTIPLIERS)}",
        f"  split       : {int(phase43.DEV_SHARE * 100)}% / "
        f"{int(phase43.VAL_SHARE * 100)}% / rest of sessions, chronological, "
        f"holdout read once",
        f"  pair sizing : Rs {pair.NOTIONAL_PER_LEG:,.0f} per leg",
        "",
        "  Nothing here is a promotion. A HISTORICAL_LEAD means one thing: the "
        "definition is worth the cost of executable live capture.",
    ]
    parts = (head + _table(payload) + _answerability(payload) + _leads(payload)
             + _rejected(payload) + _vehicle(payload) + _live_validation(payload)
             + _registry(payload))
    return "\n".join(parts) + "\n"


def _dir() -> str:
    """The artefact directory, beside the other phases' output."""
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.normpath(os.path.join(here, "..", "..", ".."))
    return os.path.join(root, ARTEFACT_DIR)


def write(payload: dict) -> list[str]:
    """Write the payload, the rendered report and the registry. Never a store."""
    os.makedirs(_dir(), exist_ok=True)
    written: list[str] = []
    for name, body in (
        (JSON_NAME, json.dumps(payload, indent=2, sort_keys=True)),
        (MD_NAME, "# PHASE 43\n\n```\n" + render(payload) + "```\n"),
        (REGISTRY_NAME, json.dumps(payload["registry"], indent=2,
                                   sort_keys=True)),
    ):
        path = os.path.join(_dir(), name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(body)
        written.append(path)
    return written
