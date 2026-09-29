"""Phase 22 wiring: one tick hook, one board, one study runner.

The hook is deliberately the thinnest thing that can work. It swallows its own
failures into a counter that the health endpoint reports, because a research
row is never worth a live tick, and it returns nothing the caller reads.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from app.models import Candle, Decision, OptionQuote
from app.research.phase22 import board, definition as defn, pool as pool_mod
from app.research.phase22 import study as study_mod, vehicles, verdict

_LOCK = threading.Lock()
_FAILURES = 0
_LAST_ERROR: str | None = None
_OBSERVED = 0

DEFAULT_CAPTURE = "data/vehicle_comparison.jsonl"


def observe(instrument: str, decision: Decision, chain: list[OptionQuote], *,
            regime: str | None, candles: list[Candle],
            spot: float | None, lot_size: int,
            futures_book: dict | None = None) -> None:
    """Evaluate the frozen setup on this tick. Paper only, returns nothing."""
    global _FAILURES, _LAST_ERROR, _OBSERVED
    try:
        board.observe(instrument, decision, regime, candles, spot, chain,
                      lot_size, futures_book=futures_book)
        with _LOCK:
            _OBSERVED += 1
    except Exception as exc:  # research must never break a live tick
        with _LOCK:
            _FAILURES += 1
            _LAST_ERROR = str(exc)


def health() -> dict:
    with _LOCK:
        return {
            "definition": defn.VERSION,
            "definition_fingerprint": defn.fingerprint(),
            "observed": _OBSERVED,
            "failures": _FAILURES,
            "last_error": _LAST_ERROR,
            "open": board.board()["open"],
            "paper_only": True,
            "no_real_order": True,
        }


def load_pool(path: str | Path) -> list[dict]:
    file = Path(path)
    with file.open("r", encoding="utf-8") as fh:
        return pool_mod.load(json.load(fh))


def run_study(pool_path: str | Path,
              capture_path: str | Path | None = DEFAULT_CAPTURE) -> dict:
    """The whole Phase 22 study: underlying leg, vehicle leg, one verdict."""
    rows = load_pool(pool_path)
    result = study_mod.run(rows)
    labelled = pool_mod.label_rows(rows)
    vehicle = None
    if capture_path is not None:
        captures = vehicles.load(capture_path)
        if captures:
            vehicle = vehicles.run(labelled, captures)
    final = verdict.assess(result, vehicle)
    if vehicle is not None:
        # The per-decision rows are the raw join, useful for a spot check and
        # far too large for a report payload.
        vehicle = {k: v for k, v in vehicle.items() if k != "decisions"}
    return {"study": result, "vehicles": vehicle, "verdict": final}
