"""Which journal rows are real trades, and which are seeded demonstrations.

Why this exists: the trade journal reported ₹1,94,034 at an 83.5% win rate, and
82 of its 109 rows were a seeded demonstration — the same leg repeated, entry
100.0 → exit 120.0, booked under one instrument while carrying another
instrument's contract. Averaged together they turned a small real book into a
fictional winning one, and every downstream statistic inherited the fiction.

The test is structural, not a guess about round numbers: a row whose contract
belongs to a *different registry instrument* than the row itself could not have
been produced by the order path, which derives the contract from the instrument
it is trading. A NIFTY contract cannot be filled on the CRUDEOIL book. Anything
the registry cannot contradict is treated as real, so the classifier can only
ever be too permissive — never too eager to discard a genuine trade.
"""
from __future__ import annotations

from app.market.instruments import REGISTRY

REAL = "REAL"
SEEDED = "SEEDED_DEMO"

# Longest first, so BANKNIFTY is recognised before NIFTY can claim its prefix.
_ROOTS: tuple[str, ...] = tuple(
    sorted({k.upper() for k in REGISTRY} | {"BANKNIFTY", "MIDCPNIFTY", "FINNIFTY",
                                            "SENSEX", "BANKEX", "NIFTY"},
           key=len, reverse=True)
)


def contract_root(option_symbol: str | None) -> str | None:
    """The registry instrument a contract belongs to, or None if unrecognised."""
    sym = (option_symbol or "").upper().strip()
    if not sym:
        return None
    for root in _ROOTS:
        if sym.startswith(root):
            return root
    return None


def classify(row: dict) -> str:
    """``REAL`` or ``SEEDED_DEMO`` for one journal row.

    Only a contradiction demotes a row: the contract names an instrument the
    registry knows, and it is not the instrument the row was booked on.
    """
    instrument = str(row.get("instrument") or "").upper().strip()
    root = contract_root(row.get("option"))
    if not instrument or root is None:
        return REAL
    return REAL if root == instrument else SEEDED


def split(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Partition into (real, seeded), leaving each row's own fields untouched."""
    real: list[dict] = []
    seeded: list[dict] = []
    for r in rows:
        (seeded if classify(r) == SEEDED else real).append(r)
    return real, seeded


def provenance(rows: list[dict]) -> dict:
    """What was excluded from the headline figures, and what it was worth.

    Published rather than silently dropped: a reader who remembers an ₹1.94L
    journal needs to see where the number went, and the seeded rows' own P&L is
    the evidence that excluding them was right.
    """
    real, seeded = split(rows)
    seeded_pnl = round(sum(float(r.get("net_pnl") or 0.0) for r in seeded), 1)
    seeded_wins = sum(1 for r in seeded if r.get("win"))
    return {
        "real_trades": len(real),
        "excluded_seeded": len(seeded),
        "excluded_pnl": seeded_pnl,
        "excluded_win_rate": (round(seeded_wins / len(seeded) * 100.0, 1)
                              if seeded else None),
        "note": (
            "Rows booked on one instrument while carrying another instrument's "
            "contract cannot have come from the order path, so they are seeded "
            "demonstrations and are excluded from every figure above. They are "
            "counted here rather than deleted."
        ) if seeded else "No seeded rows found; every figure above is real fills.",
    }
