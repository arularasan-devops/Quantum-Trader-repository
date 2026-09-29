"""Smoke: a seeded demonstration must never be counted as a real fill.

The defect this guards against reported ₹1,94,034 at an 83.5% win rate from a
journal whose real content was 26 trades at −₹12,841 and a 30.8% win rate: 82
rows were one seeded leg repeated, every one of them a winner. The assertions
below pin both directions — the fiction must be excluded, and a genuine trade
must never be discarded to make a book look tidier.
"""
from __future__ import annotations

from app.analysis import trade_provenance as prov

CHECKS = 0


def ok(cond: bool, msg: str) -> None:
    global CHECKS
    CHECKS += 1
    if not cond:
        raise AssertionError(msg)


def row(instrument: str, option: str | None, pnl: float = 100.0,
        win: bool = True) -> dict:
    return {"instrument": instrument, "option": option, "net_pnl": pnl, "win": win}


def main() -> None:
    # --- the contradiction that identifies a seeded row -------------------
    ok(prov.classify(row("CRUDEOIL", "NIFTY24000CE")) == prov.SEEDED,
       "a NIFTY contract cannot be filled on the CRUDEOIL book — the order path "
       "derives the contract from the instrument")
    ok(prov.classify(row("NIFTY", "CRUDEOIL6100PE")) == prov.SEEDED,
       "the contradiction must be caught in either direction")

    # --- genuine rows, including every shape the real book produces -------
    for inst, opt in (
        ("NIFTY", "NIFTY24500CE"),
        ("CRUDEOIL", "CRUDEOIL6100PE"),
        ("NATURALGAS", "NATURALGAS FUT"),
        ("NIFTY", "NIFTY FUT"),
        ("ITC", "ITC420CE"),
        ("NIFTY", None),
        ("NIFTY", ""),
        ("BANKNIFTY", "BANKNIFTY52000CE"),
    ):
        ok(prov.classify(row(inst, opt)) == prov.REAL,
           f"{inst} / {opt!r} is a shape the real order path produces and must "
           f"not be discarded")

    # --- BANKNIFTY must not be read as NIFTY -----------------------------
    ok(prov.contract_root("BANKNIFTY52000CE") == "BANKNIFTY",
       "prefix matching must try the longest root first, or every BANKNIFTY row "
       "is misfiled as NIFTY and then flagged as seeded")
    ok(prov.classify(row("NIFTY", "BANKNIFTY52000CE")) == prov.SEEDED,
       "a BANKNIFTY contract on the NIFTY book is still a contradiction")

    # --- an unrecognised contract is kept, never assumed fake ------------
    ok(prov.contract_root("ZZZUNKNOWN99CE") is None,
       "a contract the registry cannot place has no root")
    ok(prov.classify(row("NIFTY", "ZZZUNKNOWN99CE")) == prov.REAL,
       "only a contradiction may demote a row; an unknown contract must be kept "
       "so the classifier can only ever be too permissive")

    # --- the exclusion is published, not silent --------------------------
    book = [row("NIFTY", "NIFTY24500CE", 500.0, True),
            row("NIFTY", "NIFTY24500PE", -800.0, False),
            row("CRUDEOIL", "NIFTY24000CE", 2000.0, True),
            row("CRUDEOIL", "NIFTY24000CE", 3000.0, True)]
    real, seeded = prov.split(book)
    ok(len(real) == 2 and len(seeded) == 2, "the partition must be exact")
    ok(round(sum(float(r["net_pnl"]) for r in real), 1) == -300.0,
       "the real book here loses money, and that is the number that must survive")
    p = prov.provenance(book)
    ok(p["excluded_seeded"] == 2 and p["excluded_pnl"] == 5000.0,
       "what was excluded and what it was worth must both be reported, or a "
       "reader cannot tell where a familiar total went")
    ok(p["excluded_win_rate"] == 100.0,
       "a seeded block that never loses is the evidence it was seeded")
    ok(p["real_trades"] == 2, "the real count must be published beside it")

    clean = prov.provenance([row("NIFTY", "NIFTY24500CE")])
    ok(clean["excluded_seeded"] == 0 and clean["excluded_win_rate"] is None,
       "a clean book must not invent an excluded cohort")

    # --- classification reads only instrument and contract ---------------
    # Anything else on the row must be irrelevant, so a seeded row cannot be
    # rescued (or a real one condemned) by its P&L, note or round numbers.
    base = {"instrument": "CRUDEOIL", "option": "NIFTY24000CE", "net_pnl": 2000.0,
            "win": True, "entry": 100.0, "exit": 120.0, "mode": "paper",
            "note": "Manual PAPER", "auto": 0, "lots": 2}
    for field in ("net_pnl", "win", "entry", "exit", "mode", "note", "auto", "lots"):
        stripped = {k: v for k, v in base.items() if k != field}
        ok(prov.classify(stripped) == prov.SEEDED,
           f"removing {field} changed the verdict — the classifier is reading a "
           f"field it has no business reading")

    print(f"checked {CHECKS}")
    print("trade provenance smoke: OK")


if __name__ == "__main__":
    main()
