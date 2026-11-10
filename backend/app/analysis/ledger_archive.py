"""Move past sessions out of the always-growing signal ledgers.

The journal readers keep every parsed row in memory, so a ledger that only ever
grows (938 MB of journal and 853 MB of lifecycle after ~2 months) became several
GB of RAM and starved WSL. At boot, before anything appends, rows from sessions
older than ``QT_LEDGER_KEEP_DAYS`` are appended to a gzip archive under
``data/archive/`` and the live file keeps only the recent sessions. Nothing is
deleted. The Journal/Reports tabs read PostgreSQL and are not affected; nothing
here is read by the strategy.
"""
from __future__ import annotations

import gzip
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from app.config import settings

log = logging.getLogger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))
FILES = ("signal_journal.jsonl", "signal_outcomes.jsonl", "signal_lifecycle.jsonl")


def _cutoff(keep_days: int, now: float | None = None) -> str:
    today = datetime.fromtimestamp(now, _IST) if now else datetime.now(_IST)
    return (today - timedelta(days=keep_days - 1)).strftime("%Y-%m-%d")


def archive_file(name: str, cutoff: str) -> dict:
    src = os.path.join(settings.data_dir, name)
    if not os.path.exists(src):
        return {"file": name, "moved": 0, "kept": 0}
    os.makedirs(os.path.join(settings.data_dir, "archive"), exist_ok=True)
    arc = os.path.join(settings.data_dir, "archive", name + ".gz")
    tmp = src + ".keep.tmp"
    moved = kept = 0
    with open(src, "rb") as fh, open(tmp, "wb") as keep:
        out = None
        try:
            for line in fh:
                if not line.endswith(b"\n"):
                    line += b"\n"
                try:
                    sess = str(json.loads(line).get("session") or "")
                except Exception:
                    sess = ""
                if sess and sess < cutoff:
                    if out is None:
                        out = gzip.open(arc, "ab")
                    out.write(line)
                    moved += 1
                else:
                    keep.write(line)
                    kept += 1
        finally:
            if out is not None:
                out.close()
        keep.flush()
        os.fsync(keep.fileno())
    if moved:
        os.replace(tmp, src)
    else:
        os.remove(tmp)
    return {"file": name, "moved": moved, "kept": kept}


def run(keep_days: int | None = None) -> list[dict]:
    """Archive every ledger. Never fatal: a failure leaves the file as it was."""
    days = settings.ledger_keep_days if keep_days is None else keep_days
    if not days or days <= 0:
        return []
    cut = _cutoff(int(days))
    out = []
    for name in FILES:
        try:
            res = archive_file(name, cut)
        except Exception as exc:
            log.warning("ledger archive skipped for %s: %s", name, exc)
            res = {"file": name, "error": str(exc)[:200]}
        out.append(res)
        if res.get("moved"):
            log.info("ledger archive %s: moved %d rows before %s, kept %d",
                     name, res["moved"], cut, res["kept"])
    return out


if __name__ == "__main__":
    for r in run():
        print(r)
