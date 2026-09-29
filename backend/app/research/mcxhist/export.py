"""Export a collected root to the project's 1-minute file format.

The store is the canonical copy — it keeps token, chunk and provenance rows that
a flat file cannot. The export exists so a study can read GOLD and SILVER the
same way it reads NIFTY and CRUDEOIL, without any phase's data map being edited
and therefore without changing what any existing phase loads.

Two properties are deliberate:

* **one token per file.** Bars from two tokens of the same root are two different
  series; concatenating them is the naive stitch the request forbids. The export
  refuses a root whose bars come from more than one token unless the caller names
  which token to write, and the chosen token is written into the sidecar;
* **a sidecar, not a silent file.** Next to ``<ROOT>_ONE_MINUTE.jsonl`` goes
  ``<ROOT>_ONE_MINUTE.provenance.json`` carrying source, token, contract,
  timezone, data class, series class, roll policy and the audit fingerprint. A
  file that lands without provenance is a file whose provenance will be guessed
  later.
"""
from __future__ import annotations

import json
import os

from app.research.mcxhist import (
    DATA_CLASS,
    ONE_MINUTE,
    SERIES_CLASS,
    fingerprint,
    preregistration,
)
from app.research.mcxhist.store import Store


def paths(root: str, data_dir: str = "data") -> tuple[str, str]:
    base = os.path.join(data_dir, "backtest", f"{root.upper()}_{ONE_MINUTE}")
    return base + ".jsonl", base + ".provenance.json"


def export_root(
    store: Store,
    root: str,
    *,
    token: str | None = None,
    data_dir: str = "data",
) -> dict:
    """Write one root's bars as JSONL plus a provenance sidecar."""
    tokens = store.tokens(root)
    if not tokens:
        return {"root": root, "written": 0, "status": "NO_BARS_STORED"}
    if token is None:
        if len(tokens) > 1:
            return {
                "root": root,
                "written": 0,
                "status": "MULTIPLE_TOKENS_REFUSED",
                "tokens": tokens,
                "reason": (
                    "bars from two tokens are two series; name the token to "
                    "export rather than concatenating them"
                ),
            }
        token = tokens[0]

    rows = store.bars(root, token)
    jsonl_path, sidecar_path = paths(root, data_dir)
    os.makedirs(os.path.dirname(jsonl_path), exist_ok=True)
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for ts, open_, high, low, close, volume in rows:
            fh.write(
                json.dumps(
                    {
                        "time": int(ts),
                        "open": float(open_),
                        "high": float(high),
                        "low": float(low),
                        "close": float(close),
                        "volume": float(volume),
                    }
                )
                + "\n"
            )

    provenance = store.provenance(root)
    chosen = next((p for p in provenance if p["token"] == token), None)
    sidecar = {
        "root": root.upper(),
        "token": token,
        "interval": ONE_MINUTE,
        "bars": len(rows),
        "first_ts": int(rows[0][0]) if rows else None,
        "last_ts": int(rows[-1][0]) if rows else None,
        "data_class": DATA_CLASS,
        "series_class": SERIES_CLASS,
        "roll_policy": preregistration()["roll_policy"],
        "price_adjustment": preregistration()["price_adjustment"],
        "fingerprint": fingerprint(),
        "provenance": chosen,
        "options": preregistration()["options"],
    }
    with open(sidecar_path, "w", encoding="utf-8") as fh:
        json.dump(sidecar, fh, indent=2, sort_keys=True)

    return {
        "root": root,
        "token": token,
        "written": len(rows),
        "status": "OK",
        "jsonl": jsonl_path,
        "provenance": sidecar_path,
    }
