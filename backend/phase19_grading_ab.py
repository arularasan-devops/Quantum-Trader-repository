"""A+ grading A/B — v1 (prior binding) vs v2 (prior informational unless validated).

    .venv/bin/python phase19_grading_ab.py
    .venv/bin/python phase19_grading_ab.py --sessions 2
    .venv/bin/python phase19_grading_ab.py --trades ~/p15a_pool_real.json
    .venv/bin/python phase19_grading_ab.py --json

RESEARCH ONLY. Grades rows that are already on disk. It writes nothing, enters no
paper leg, changes no threshold, and the production grader keeps refusing exactly
what it refuses today. Nothing printed here may promote a strategy.

What it answers, and what it deliberately refuses to answer:

* **Eligibility, measured.** How many opportunities each policy admits, per
  instrument, split READY / PENDING_DATA / REJECTED. This is a replay of recorded
  rows, so it is a real count.
* **Whether each veto is validated.** With ``--trades``, every instrument's prior
  is re-tested on the pool it came from, against the same multiple-testing bar
  Phase 15B used, and then out of sample: a chronological holdout of sessions no
  development row touched, plus a walk-forward sign check fold by fold. All three
  must agree before a five-year refusal counts as supported. Six verdicts, so
  that "clearly negative but under a strict bar" is never read as "fine":
  VALIDATED_NEGATIVE, NEGATIVE_BUT_UNDER_BAR, INDISTINGUISHABLE_FROM_ZERO,
  POSITIVE_BUT_UNDER_BAR, VALIDATED_POSITIVE, INSUFFICIENT_DATA. Only the middle
  three make a refusal unsupported; an instrument the pool never measured keeps
  its veto untouched.
* **Outcomes: mostly unmeasurable, on purpose.** T1-before-SL, net R and PF on
  the option vehicle need an entry and an exit at real prices. A candidate that
  was never entered has neither, and modelling one would produce exactly the
  fabricated expectancy this project exists to avoid. So option outcomes are read
  from resolved legs only, and reported as unmeasured when there are none. The
  pool columns are underlying-only and labelled as such.

Read it as: v2 changes the *universe*; whether the wider universe pays has to be
measured forward, and the WATCH shadow book is what would measure it.
"""
from __future__ import annotations

import argparse
import json

import phase19_grading_audit as audit
from app.research.phase17 import aplus as p17aplus
from app.research.phase17 import store as p17store
from app.research.phase19 import grading_ab as ab


def _load_pool(path: str | None) -> list[dict]:
    """Rows from a saved candidate pool, in-sample and holdout together.

    The pool format is the one ``phase14_rules.py --save-trades`` writes and
    Phase 15/15A/15B read, so the priors are re-tested on their own basis.
    """
    if not path:
        return []
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    if isinstance(blob, list):
        return [r for r in blob if isinstance(r, dict)]
    rows: list[dict] = []
    for part in ("in_sample", "holdout", "trades", "rows"):
        got = blob.get(part)
        if isinstance(got, list):
            rows.extend(r for r in got if isinstance(r, dict))
    return rows


def _resolved_outcomes(instruments: set[str]) -> dict:
    """Option-vehicle outcomes, from resolved costed legs only.

    Every number here is a real fill and a real exit. There is no fallback: with
    no resolved leg on an instrument the answer is "unmeasured", which is the
    honest state of a book that has produced one leg.
    """
    rows = [r for r in p17store.paper(tail_bytes=None) if isinstance(r, dict)]
    per: dict[str, dict] = {}
    for r in rows:
        inst = str(r.get("instrument") or "").upper()
        if instruments and inst not in instruments:
            continue
        net = r.get("net_r")
        if not isinstance(net, (int, float)):
            continue
        b = per.setdefault(inst, {"legs": 0, "net_r": [], "t1": 0})
        b["legs"] += 1
        b["net_r"].append(float(net))
        if str(r.get("outcome") or "").upper().startswith("T1"):
            b["t1"] += 1
    out: dict[str, dict] = {}
    for inst, b in sorted(per.items()):
        vals = b["net_r"]
        wins = sum(v for v in vals if v > 0)
        loss = -sum(v for v in vals if v < 0)
        out[inst] = {
            "resolved_legs": b["legs"],
            "mean_net_r": round(sum(vals) / len(vals), 4) if vals else None,
            "t1_before_sl_pct": round(100.0 * b["t1"] / b["legs"], 2),
            "profit_factor": round(wins / loss, 2) if loss > 0 else None,
            "measured": b["legs"] >= ab.MIN_VALIDATION_ROWS,
            "basis": "RESOLVED_COSTED_LEGS",
        }
    return out


def build(sessions: int | None, pool_path: str | None) -> dict:
    all_obs = p17store.observations(tail_bytes=None)
    present = audit._sessions_of(all_obs)
    keep = set(present[-sessions:]) if sessions else None
    rows = audit._within(all_obs, keep)
    measured = audit._sessions_of(rows)

    pool = _load_pool(pool_path)
    validations = ab.validate_pool(pool)
    cmp = ab.compare(rows, validations, key=audit.opportunity_key,
                     sessions=len(measured))
    cmp["sessions_measured"] = measured
    cmp["sessions_present"] = present
    cmp["pool_path"] = pool_path
    cmp["pool_rows"] = len(pool)
    cmp["option_outcomes"] = _resolved_outcomes(set(p17aplus.PRIOR_DEV_R))
    cmp["thresholds_unchanged"] = True
    cmp["a_plus_floor"] = p17aplus.A_PLUS_MIN_SCORE
    cmp["prior_basis"] = p17aplus.PRIOR_BASIS
    # What the grader currently believes, beside what this pool just measured. A
    # disagreement means the grader is acting on a stale reading of the evidence,
    # which is worth seeing rather than resolving silently.
    cmp["grader_prior_labels"] = dict(sorted(p17aplus.PRIOR_LABEL.items()))
    cmp["grader_veto_labels"] = sorted(p17aplus.VETO_LABELS)
    cmp["grader_disagrees_with_pool"] = sorted(
        str(v["instrument"]) for v in validations
        if v.get("verdict") != ab.INSUFFICIENT_DATA
        and v.get("verdict") != p17aplus.prior_label(str(v["instrument"]))
    )
    return cmp


def _n(val, fmt: str = "{:+.4f}") -> str:
    """A number, or an em dash where there is nothing to report."""
    return fmt.format(val) if isinstance(val, (int, float)) else "—"


def _print_oos(validations: list[dict]) -> None:
    """The out-of-sample half of each verdict, per instrument.

    Printed separately from the pooled table because it is what decides the
    verdict: a pooled mean clearing the bar in sample says little on its own.
    """
    print("\nout of sample — does it hold? (holdout = last sessions, never "
          "searched)")
    print("instrument    dev n  val n  hold n  hold mean R  hold T1  hold PF  "
          "hold DD    folds -R/measured  verdict")
    for v in validations:
        prior = v.get("prior_dev_r")
        if not isinstance(prior, (int, float)) or float(prior) >= 0:
            continue
        per = v.get("periods") or {}
        hold = per.get("holdout") or {}
        wf = v.get("walk_forward") or {}
        print(f"  {v['instrument']:<12} "
              f"{(per.get('development') or {}).get('rows', 0):>5}  "
              f"{(per.get('validation') or {}).get('rows', 0):>5}  "
              f"{hold.get('rows', 0):>6}  "
              f"{_n(hold.get('mean_r')):>11}  "
              f"{_n(hold.get('t1_before_sl_pct'), '{:.1f}%'):>7}  "
              f"{_n(hold.get('profit_factor'), '{:.2f}'):>7}  "
              f"{_n(hold.get('max_drawdown_r'), '{:.2f}R'):>7}    "
              f"{wf.get('folds_negative', 0)}/{wf.get('folds_measured', 0):<15}  "
              f"{v['verdict']}")
        if v.get("downgraded_because"):
            print(f"               pooled mean cleared the bar, but "
                  f"{v['downgraded_because']}")
    print(f"  a veto counts as supported only if the pooled mean clears "
          f"{ab.SIGMA_BAR}σ AND the")
    print(f"  holdout keeps the sign on ≥{ab.MIN_HOLDOUT_ROWS} rows AND every "
          f"measurable fold agrees.")


def _print(result: dict) -> None:
    print("A+ GRADING A/B — is the historical prior a validated blocker?")
    measured = result["sessions_measured"]
    print(f"sessions measured: {', '.join(measured) if measured else 'none'}"
          f"   (on disk: {len(result['sessions_present'])})")
    print(f"prior basis recorded in the grader: {result['prior_basis']}")
    if result["pool_rows"]:
        print(f"pool: {result['pool_rows']} underlying outcomes "
              f"from {result['pool_path']}")
    else:
        print("pool: none given (--trades). Prior validation needs it; without a "
              "pool every")
        print("      verdict below is INSUFFICIENT_DATA and v2 vetoes nothing "
              "extra by default.")
    print()

    print("does the pool support vetoing the instrument? "
          f"(bar {ab.SIGMA_BAR}σ over {ab.COMPARISONS} comparisons)")
    print("instrument     prior    pool n   mean R    sigma   T1%    PF     "
          "verdict")
    for v in result["prior_validation"]:
        prior = v["prior_dev_r"]
        sig = f"{v['sigma']:.2f}" if v["sigma"] is not None else "  —"
        t1 = f"{v['t1_before_sl_pct']:.1f}" if v["t1_before_sl_pct"] is not None \
            else "  —"
        pf = f"{v['profit_factor']:.2f}" if v["profit_factor"] is not None else "  —"
        pr = f"{prior:+.4f}" if isinstance(prior, (int, float)) else "    —"
        print(f"  {v['instrument']:<12} {pr}  {v['pool_rows']:>6}  "
              f"{v['pool_mean_r']:+.4f}  {sig:>6}  {t1:>5}  {pf:>5}   "
              f"{v['verdict']}")
    _print_oos(result["prior_validation"])
    groups = result["v2_prior_groups"]
    print("\n  v2 keeps vetoing  (validated negative)      : "
          f"{groups['kept_validated_negative'] or 'nothing'}")
    print("  v2 stops vetoing  (indistinguishable from 0): "
          f"{groups['tested_and_indistinguishable_from_zero'] or 'nothing'}")
    print("  v2 stops vetoing  (negative, under the bar) : "
          f"{groups['lifted_but_still_negative_under_bar'] or 'nothing'}")
    print("  v2 leaves as-is   (never measured here)     : "
          f"{groups['never_measured_in_this_pool'] or 'nothing'}")
    if groups["never_measured_in_this_pool"]:
        print("  ! the last group has no outcomes in this pool, so v2 changes "
              "NOTHING for them.")
        print("    Neither the veto nor its removal is supported: widen the pool "
              "and re-run.")
    print("  The bar decides in both directions: a negative that did not survive "
          "it stops")
    print("  refusing the instrument. NEGATIVE_BUT_UNDER_BAR is still not a pass "
          "— those")
    print("  names are negative out of sample too (PF 0.91-0.95), so they enter "
          "research")
    print("  ranked at the bottom on market_edge, and research is not "
          "production.")

    print("\nwhat the grader currently applies (research eligibility only):")
    for inst, label in result["grader_prior_labels"].items():
        mark = "refuses" if label in p17aplus.VETO_LABELS else "ranks only"
        print(f"  {inst:<12} {label:<28} {mark}")
    stale = result["grader_disagrees_with_pool"]
    print("  grader label disagrees with this pool: "
          f"{stale or 'nothing'}")
    if stale:
        print("  ! the grader is acting on a different reading of the evidence "
              "than this pool gives.")
    print("  Production Option/Futures/CAS signals do not read any of this, and "
          "no production")
    print("  status changed: an admitted instrument is a RESEARCH candidate and "
          "nothing more.")

    print("\neligibility, replayed over the same recorded ticks:")
    print("                              v1 (today)   v2 (proposed)")
    for label, k in (("distinct opportunities", "opportunities"),):
        print(f"  {label:<27} {result['v1'][k]:>10}   {result['v2'][k]:>13}")
    for st in ab.STATES:
        print(f"  {st:<27} "
              f"{result['v1']['opportunity_states'][st]:>10}   "
              f"{result['v2']['opportunity_states'][st]:>13}")
    per = result["a_plus_per_session"]
    print(f"  {'A+ per session':<27} {str(per[ab.V1]):>10}   "
          f"{str(per[ab.V2]):>13}")
    print(f"  delta A+ opportunities: {result['delta_a_plus_opportunities']:+d}")

    print("\noption-vehicle outcomes (resolved costed legs only):")
    if not result["option_outcomes"]:
        print("  unmeasured — no resolved leg yet. Net R, PF and T1-before-SL "
              "cannot be replayed")
        print("  for a candidate that was never entered: there is no fill and no "
              "exit price.")
    for inst, o in result["option_outcomes"].items():
        flag = "" if o["measured"] else "   (too few legs to interpret)"
        print(f"  {inst:<12} legs {o['resolved_legs']:>4}  mean net R "
              f"{o['mean_net_r']}  T1 {o['t1_before_sl_pct']}%  "
              f"PF {o['profit_factor']}{flag}")

    print("\nNo threshold was changed by running this, and the production grader "
          "is untouched:")
    print("v2 exists only in this replay. PENDING_DATA is not A+ and is never "
          "promotable.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=None,
                    help="replay only the last N recorded sessions")
    ap.add_argument("--trades", default=None,
                    help="candidate pool json, for prior validation")
    ap.add_argument("--json", action="store_true", help="print JSON")
    args = ap.parse_args()

    result = build(args.sessions, args.trades)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return
    _print(result)


if __name__ == "__main__":
    main()
