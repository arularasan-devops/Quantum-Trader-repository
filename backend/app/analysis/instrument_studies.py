"""Per-instrument economics — Phase 12 §8 (stocks), §9 (indices), §10 (MCX).

Read from the recorded ledgers, not from live memory, so a study can be re-run
over any past session. Each row answers one question about one name: could this
instrument have paid for the trade the engine asked for?

Three things are kept deliberately apart:

* **spread** — median, p75 and p90 as a percentage of the premium, because a
  single average hides the tail that does the damage. 25 Aug: 0.80% on index
  options, 1.61% on MCX, 9.52% on single stocks.
* **net R** — the realised R after the spread and the same round-trip cost model
  the board charges. Gross R on a 9.5% spread is fiction.
* **affordability** — the lot cost and the risk per lot against the configured
  paper risk budget (§10). An instrument the pot cannot hold is ``UNAFFORDABLE``;
  that is not a signal failure and is never counted as one.

Nothing here blocks anything. It names which instruments are economically
tradable and which are research-only, which is questions 4, 8 and 9 of §16.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.analysis import instrument_family as fam
from app.analysis import opportunity, r_integrity
from app.config import settings
from app.market.instruments import REGISTRY

# Below this a per-instrument row is reported but never used to call a name
# uneconomic: three bad fills are not a verdict on a contract.
MIN_SAMPLE = 10

ECONOMIC = "ECONOMIC"
MARGINAL = "MARGINAL"
UNECONOMIC = "UNECONOMIC"
UNAFFORDABLE = "UNAFFORDABLE"
INSUFFICIENT = "INSUFFICIENT_SAMPLE"

REPORT_JSON = "instrument_studies.json"


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))
    return round(ordered[idx], 4)


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _read_jsonl(path: Path | str, session: str | None) -> list[dict]:
    """Every row of a ledger, or nothing. A partial study is worse than none."""
    rows: list[dict] = []
    path = Path(path)
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            if session and row.get("session") != session:
                continue
            rows.append(row)
    return rows


def _cost_r(entry: float | None, risk: float | None, spread: float | None,
            lot_size: int) -> float | None:
    """The round trip in R: the recorded spread plus brokerage and taxes.

    No recorded spread means no net number. A gross R relabelled net because the
    book was missing is exactly how a 9.5% spread stayed invisible for three
    sessions.
    """
    if not entry or not risk or risk <= 0 or spread is None:
        return None
    cost_pct = opportunity.round_trip_cost_pct(entry, max(1, lot_size))
    cost_points = entry * cost_pct / 100.0 + float(spread)
    return round(cost_points / risk, 4)


def _affordability(instrument: str, entry: float | None,
                   risk: float | None) -> dict:
    spec = REGISTRY.get(instrument)
    lot = int(spec.lot_size) if spec and spec.lot_size else 0
    budget = settings.shadow_book_capital * settings.shadow_book_risk_per_trade_pct / 100.0
    lot_cost = round(entry * lot, 2) if entry and lot else None
    risk_per_lot = round(risk * lot, 2) if risk and lot else None
    lots = None
    if risk_per_lot is not None and risk_per_lot > 0:
        lots = int(budget // risk_per_lot)
    return {
        "lot_size": lot or None,
        "lot_cost": lot_cost,
        "risk_per_lot": risk_per_lot,
        "risk_budget": round(budget, 2),
        "lots_affordable": lots,
        "affordable": None if lots is None else lots >= 1,
    }


def _verdict(row: dict) -> str:
    """Economic, marginal, uneconomic — or honestly not enough data yet."""
    if row["affordable_share_pct"] == 0.0 and row["samples"] >= MIN_SAMPLE:
        return UNAFFORDABLE
    if row["samples"] < MIN_SAMPLE:
        return INSUFFICIENT
    spread = row["spread_pct_median"]
    net = row["net_r_median"]
    if spread is not None and spread >= settings.tradability_spread_untradable_pct:
        return UNECONOMIC
    if row["resolved"] >= MIN_SAMPLE and net is not None and net < 0:
        return UNECONOMIC
    if spread is not None and spread >= settings.tradability_spread_caution_pct:
        return MARGINAL
    if net is None or row["resolved"] < MIN_SAMPLE:
        return INSUFFICIENT
    return ECONOMIC if net > 0 else MARGINAL


def _row(instrument: str, calls: list[dict], resolved: list[dict]) -> dict:
    spreads = [float(c["spread_pct"]) for c in calls if c.get("spread_pct") is not None]
    over_risk = [float(c["spread_over_risk"]) for c in calls
                 if c.get("spread_over_risk") is not None]
    gross = [float(r["gross_r"]) for r in resolved if r.get("gross_r") is not None]
    net = [float(r["net_r"]) for r in resolved if r.get("net_r") is not None]
    wins = [r for r in resolved if r.get("net_r") is not None and float(r["net_r"]) > 0]
    afford = [c["affordable"] for c in calls if c.get("affordable") is not None]
    row = {
        "instrument": instrument,
        "family": fam.family(instrument),
        "samples": len(calls),
        "quoted_samples": len(spreads),
        "resolved": len(resolved),
        "spread_pct_median": _pct(spreads, 0.5),
        "spread_pct_p75": _pct(spreads, 0.75),
        "spread_pct_p90": _pct(spreads, 0.90),
        "spread_over_risk_median": _pct(over_risk, 0.5),
        "gross_r_median": _pct(gross, 0.5),
        "gross_r_mean": _mean(gross),
        "net_r_median": _pct(net, 0.5),
        "net_r_mean": _mean(net),
        "net_r_total": round(sum(net), 3) if net else None,
        "win_rate_pct": (round(100.0 * len(wins) / len(net), 1) if net else None),
        "affordable_share_pct": (round(100.0 * sum(1 for a in afford if a) / len(afford), 1)
                                 if afford else None),
        "affordability_sample": (calls[-1].get("affordability") if calls else None),
    }
    row["verdict"] = _verdict(row)
    return row


def _calls(journal_rows: list[dict]) -> dict[str, list[dict]]:
    """Flatten each journalled option call into the facts the studies need."""
    out: dict[str, list[dict]] = {}
    for row in journal_rows:
        if str(row.get("signal_vehicle") or "").upper() == "FUTURES":
            continue  # futures are points, options are premium; never pooled
        instrument = str(row.get("instrument") or "")
        if not instrument:
            continue
        state = row.get("market_state") or {}
        trad = row.get("tradability") or {}
        plan = row.get("entry_plan") or {}
        entry = plan.get("entry_price")
        risk = plan.get("risk_points")
        afford = trad.get("affordability") or _affordability(
            instrument,
            float(entry) if entry is not None else None,
            float(risk) if risk is not None else None,
        )
        out.setdefault(instrument, []).append({
            "signal_id": row.get("signal_id"),
            "spread_pct": state.get("spread_pct_of_premium"),
            "spread_points": trad.get("spread"),
            "spread_over_risk": trad.get("spread_over_risk"),
            "tradability_status": trad.get("status"),
            "entry": entry,
            "risk_points": risk,
            "affordability": afford,
            "affordable": afford.get("affordable"),
        })
    return out


def _resolved(outcome_rows: list[dict], calls: dict[str, list[dict]],
              excluded: dict[str, int] | None = None) -> dict[str, list[dict]]:
    """Resolved outcomes with the spread and costs of that call charged to them."""
    by_id: dict[str, dict] = {}
    for rows in calls.values():
        for call in rows:
            sid = call.get("signal_id")
            if sid:
                by_id[str(sid)] = call
    out: dict[str, list[dict]] = {}
    for row in outcome_rows:
        if row.get("event") != "RESOLVED":
            continue
        instrument = str(row.get("instrument") or "")
        if not instrument:
            continue
        why = r_integrity.rejection(row)
        if why is not None:
            if excluded is not None:
                excluded[why] = excluded.get(why, 0) + 1
            continue
        gross = row.get("realized_r")
        if gross is None:
            continue
        call = by_id.get(str(row.get("signal_id") or ""))
        spec = REGISTRY.get(instrument)
        cost = _cost_r(
            row.get("entry"), row.get("risk_points"),
            (call or {}).get("spread_points"),
            int(spec.lot_size) if spec and spec.lot_size else 1,
        )
        out.setdefault(instrument, []).append({
            "signal_id": row.get("signal_id"),
            "outcome": row.get("outcome"),
            "gross_r": float(gross),
            "cost_r": cost,
            # No cost model, no net number — never silently equal to the gross.
            "net_r": (round(float(gross) - cost, 3) if cost is not None else None),
            "mfe_r": row.get("mfe_r"),
            "mae_r": row.get("mae_r"),
            "target_before_stop": row.get("order") == "TARGET_FIRST",
            "failure_kind": (row.get("failure_attribution") or {}).get("failure_kind"),
        })
    return out


def study(journal_rows: list[dict], outcome_rows: list[dict]) -> dict:
    """The §8/§9/§10 studies over the rows given."""
    calls = _calls(journal_rows)
    excluded: dict[str, int] = {}
    resolved = _resolved(outcome_rows, calls, excluded)
    rows = [_row(i, calls.get(i, []), resolved.get(i, []))
            for i in sorted(set(calls) | set(resolved))]
    families: dict[str, dict] = {}
    for family in fam.FAMILIES:
        members = [r for r in rows if r["family"] == family]
        spreads = [r["spread_pct_median"] for r in members
                   if r["spread_pct_median"] is not None]
        nets = [r["net_r_total"] for r in members if r["net_r_total"] is not None]
        families[family] = {
            "instruments": len(members),
            "samples": sum(r["samples"] for r in members),
            "resolved": sum(r["resolved"] for r in members),
            "median_of_median_spread_pct": _pct([float(s) for s in spreads], 0.5),
            "net_r_total": round(sum(float(n) for n in nets), 3) if nets else None,
            "economic": [r["instrument"] for r in members if r["verdict"] == ECONOMIC],
            "uneconomic": [r["instrument"] for r in members if r["verdict"] == UNECONOMIC],
            "unaffordable": [r["instrument"] for r in members
                             if r["verdict"] == UNAFFORDABLE],
        }
    named = {
        "index_study": [r for r in rows if r["instrument"] in fam.INDEX_STUDY],
        "mcx_study": [r for r in rows if r["instrument"] in fam.MCX_STUDY],
        "stock_study": [r for r in rows if r["family"] == fam.STOCK],
    }
    return {
        "min_sample": MIN_SAMPLE,
        "excluded": sum(excluded.values()),
        "excluded_by_reason": dict(sorted(excluded.items())),
        "instruments": rows,
        "families": families,
        "named_studies": named,
        "uneconomic_names": [r["instrument"] for r in rows if r["verdict"] == UNECONOMIC],
        "unaffordable_names": [r["instrument"] for r in rows
                               if r["verdict"] == UNAFFORDABLE],
        "a_plus_candidates": [r["instrument"] for r in rows if r["verdict"] == ECONOMIC],
        "research_only_names": [r["instrument"] for r in rows
                                if r["verdict"] in (MARGINAL, UNECONOMIC,
                                                    UNAFFORDABLE, INSUFFICIENT)],
        "research_only": True,
        "notes": [
            "net R charges the recorded spread of that call plus the same "
            "round-trip cost model the board applies; a call with no quoted "
            "book has no net number rather than a gross one relabelled",
            "UNAFFORDABLE is an affordability fact about the contract and the "
            "configured paper risk budget, never a failed signal",
            "no verdict here blocks, gates or promotes anything",
            r_integrity.note(),
        ],
    }


def report(session: str | None = None) -> dict:
    """Run the studies over the recorded ledgers."""
    from app.analysis.signal_journal import journal_path, outcomes_path

    journal = _read_jsonl(journal_path(), session)
    outcomes = _read_jsonl(outcomes_path(), session)
    out = study(journal, outcomes)
    out["session"] = session
    out["rows_read"] = {"journal": len(journal), "outcomes": len(outcomes)}
    return out


def write_report(session: str | None = None) -> Path:
    from app.analysis.signal_journal import journal_path

    path = Path(journal_path()).parent / REPORT_JSON
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report(session), indent=2, default=str),
                   encoding="utf-8")
    tmp.replace(path)
    return path
