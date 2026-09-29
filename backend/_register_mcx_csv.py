"""Import pre-collected GOLD/SILVER 1-minute CSVs through the historical-import
layer, bypassing the broker entirely.

Use when the collection endpoint is unreachable or rate-limited. It does not
touch the mcxhist store: the CSVs already are what `phase55 register` would have
exported from it. Idempotent on file bytes, so re-running changes nothing.

    cd ~/projects/phase8_delivery/backend
    .venv/bin/python _register_mcx_csv.py
"""
from __future__ import annotations

import json
import os

from app.research.phase55 import collect as p55collect

CSV_DIR = os.path.join("data", "research", "phase55", "csv")
FILES = (("GOLD", "MCX"), ("SILVER", "MCX"))


def main() -> int:
    out = []
    for instrument, exchange in FILES:
        path = os.path.join(CSV_DIR, f"{instrument}_ONE_MINUTE.csv")
        if not os.path.exists(path):
            out.append({"instrument": instrument, "status": "MISSING_CSV", "path": path})
            continue
        out.append(p55collect.register(instrument, path, exchange))
    print(json.dumps(out, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
