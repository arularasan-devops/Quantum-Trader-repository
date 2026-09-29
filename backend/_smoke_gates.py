"""Smoke test: Journal Flow-exclusion + config gate defaults. READ-ONLY-ish
(writes to a throwaway temp data dir only)."""
from __future__ import annotations

import os
import tempfile

from app.config import settings


def main() -> None:
    settings.data_dir = tempfile.mkdtemp(prefix="qt_smoke_")
    from app import storage
    store = storage.HistoryStore(os.path.join(settings.data_dir, "history.db"))

    def mk(note: str, pnl: float):
        return {
            "time": 1_700_000_000,
            "instrument": "NIFTY",
            "option": "NIFTY24500CE",
            "option_type": "CE",
            "entry": 100.0,
            "exit": 100.0 + pnl,
            "lots": 1,
            "net_pnl": pnl,
            "win": pnl > 0,
            "holding_minutes": 5,
            "mode": "paper",
            "auto": True,
            "exit_reason": "TEST",
            "confidence": 80.0,
        }

    store.record_journal("NIFTY", mk("x", 10.0), "Auto-Buy PAPER · real trade")
    store.record_journal("NIFTY", mk("x", -5.0), "Candle-Flow PAPER · flow leg")
    store.record_journal("NIFTY", mk("x", 7.0), "Manual · signal-board buy")

    all_rows = store.journal(limit=100)
    board_rows = store.journal(limit=100, exclude_flow=True)
    flow_in_all = [r for r in all_rows if str(r["note"]).startswith("Candle-Flow")]
    flow_in_board = [r for r in board_rows if str(r["note"]).startswith("Candle-Flow")]

    print(f"all rows        : {len(all_rows)} (flow={len(flow_in_all)})")
    print(f"board rows      : {len(board_rows)} (flow={len(flow_in_board)})")
    assert len(all_rows) == 3, "all rows should keep Flow"
    assert len(board_rows) == 2, "board should drop the Flow leg"
    assert flow_in_board == [], "no Flow may appear on the board"
    print("OK journal exclude_flow")

    assert settings.min_reward_risk >= 1.0
    assert settings.reversal_entry_enabled in (True, False)
    print(f"OK gate defaults: min_rr={settings.min_reward_risk} "
          f"veto_explosion={settings.veto_premium_explosion} "
          f"reversal_enabled={settings.reversal_entry_enabled}")


if __name__ == "__main__":
    main()
