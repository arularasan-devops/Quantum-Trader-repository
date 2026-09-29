"""The raw store (§4). Append-only, idempotent, and never adjusted.

Layout::

    data/research/phase56/nse/
        raw/2022/raw_2022-07-28.csv      one session's raw prints, write-once
        actions/actions.csv              the published corporate-action table
        manifest.json                    per-session ingest status + digests
        adjusted/<SYMBOL>.csv            derived, rebuildable, deletable

Write-once is enforced, not documented: ``write_session`` refuses to overwrite an
existing session file. If the same session is ingested twice, the second attempt
re-derives the digest and compares — equal means ``IDEMPOTENT``, different means
``CONFLICT`` and the new bytes are *not* written, because a raw print that
changes under a finished study silently invalidates every fingerprint above it.

The one exception is a session file holding no rows: nothing was ever recorded in
it, so it is treated as absent and may be filled. That is what makes a day the
parser once failed on repairable without deleting stored prints by hand.

The adjusted directory is deliberately separate and deliberately disposable. It
can be deleted and rebuilt from raw plus actions at any time, which is the
property that makes the §4 separation checkable: if adjusted data could not be
reconstructed, it would be a second source rather than a derivation.
"""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import CorporateAction, VERSION, classify_purpose
from .bhav import RawRow

RAW_FIELDS = (
    "trade_date", "symbol", "series", "isin",
    "open", "high", "low", "close", "last", "prev_close",
    "volume", "turnover_inr", "trades",
    "delivery_qty", "delivery_pct", "schema",
)

ACTION_FIELDS = (
    "symbol", "series", "ex_date", "action_type",
    "factor", "dividend_inr", "quantified", "purpose", "source_file",
)

WRITTEN = "WRITTEN"
IDEMPOTENT = "IDEMPOTENT"
CONFLICT = "CONFLICT"


def _digest(rows: list[RawRow]) -> str:
    blob = "\n".join(
        "|".join(str(row.as_dict()[name]) for name in RAW_FIELDS)
        for row in sorted(rows, key=lambda r: (r.symbol, r.series))
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class SessionWrite:
    status: str
    digest: str
    rows: int
    path: Path


class RawStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.raw_dir = self.root / "raw"
        self.actions_dir = self.root / "actions"
        self.adjusted_dir = self.root / "adjusted"
        for directory in (self.raw_dir, self.actions_dir, self.adjusted_dir):
            directory.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"
        self.manifest: dict = self._load_manifest()

    # -- manifest ----------------------------------------------------------

    def _load_manifest(self) -> dict:
        if self.manifest_path.exists():
            try:
                return json.loads(self.manifest_path.read_text())
            except json.JSONDecodeError:
                pass
        return {"version": VERSION, "sessions": {}}

    def save_manifest(self) -> None:
        self.manifest["version"] = VERSION
        self.manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True))

    def session_status(self, day: date) -> dict | None:
        return self.manifest["sessions"].get(day.isoformat())

    def record_session(self, day: date, **fields) -> None:
        entry = self.manifest["sessions"].setdefault(day.isoformat(), {})
        entry.update(fields)

    # -- raw ---------------------------------------------------------------

    def session_path(self, day: date) -> Path:
        directory = self.raw_dir / f"{day.year:04d}"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"raw_{day.isoformat()}.csv"

    def write_session(self, day: date, rows: list[RawRow]) -> SessionWrite:
        path = self.session_path(day)
        digest = _digest(rows)
        if path.exists() and self.read_session(day):
            existing = self.manifest["sessions"].get(day.isoformat(), {}).get("digest")
            if existing is None:
                existing = _digest(self.read_session(day))
            status = IDEMPOTENT if existing == digest else CONFLICT
            return SessionWrite(status, digest, len(rows), path)
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(RAW_FIELDS))
            writer.writeheader()
            for row in sorted(rows, key=lambda r: (r.symbol, r.series)):
                writer.writerow(row.as_dict())
        return SessionWrite(WRITTEN, digest, len(rows), path)

    def read_session(self, day: date) -> list[RawRow]:
        path = self.session_path(day)
        if not path.exists():
            return []
        out = []
        with path.open(newline="") as handle:
            for raw in csv.DictReader(handle):
                out.append(
                    RawRow(
                        trade_date=raw["trade_date"],
                        symbol=raw["symbol"],
                        series=raw["series"],
                        isin=raw["isin"],
                        open=float(raw["open"]),
                        high=float(raw["high"]),
                        low=float(raw["low"]),
                        close=float(raw["close"]),
                        last=float(raw["last"]),
                        prev_close=float(raw["prev_close"]),
                        volume=int(raw["volume"] or 0),
                        turnover_inr=float(raw["turnover_inr"] or 0.0),
                        trades=int(raw["trades"] or 0),
                        delivery_qty=(int(raw["delivery_qty"]) if raw["delivery_qty"] not in ("", "None") else None),
                        delivery_pct=(float(raw["delivery_pct"]) if raw["delivery_pct"] not in ("", "None") else None),
                        schema=raw["schema"],
                    )
                )
        return out

    def sessions(self) -> list[date]:
        found = []
        for path in self.raw_dir.glob("*/raw_*.csv"):
            try:
                found.append(date.fromisoformat(path.stem.replace("raw_", "")))
            except ValueError:
                continue
        return sorted(found)

    # -- actions -----------------------------------------------------------

    @property
    def actions_path(self) -> Path:
        return self.actions_dir / "actions.csv"

    def write_actions(self, actions: list[CorporateAction]) -> int:
        """Rewrite the action table from the full deduplicated set.

        The table is derived (every row traceable to a published ``Bc`` file), so
        rewriting it is safe in a way that rewriting a price is not.
        """
        ordered = sorted(actions, key=lambda a: (a.ex_date, a.symbol, a.series, a.purpose))
        with self.actions_path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(ACTION_FIELDS))
            writer.writeheader()
            for action in ordered:
                writer.writerow(
                    {
                        "symbol": action.symbol,
                        "series": action.series,
                        "ex_date": action.ex_date.isoformat(),
                        "action_type": action.action_type,
                        "factor": "" if action.factor is None else f"{action.factor:.10g}",
                        "dividend_inr": "" if action.dividend_inr is None else f"{action.dividend_inr:.6g}",
                        "quantified": int(action.quantified),
                        "purpose": action.purpose,
                        "source_file": action.source_file,
                    }
                )
        return len(ordered)

    def read_actions(self) -> list[CorporateAction]:
        if not self.actions_path.exists():
            return []
        out = []
        with self.actions_path.open(newline="") as handle:
            for raw in csv.DictReader(handle):
                factor = float(raw["factor"]) if raw["factor"] else None
                dividend = float(raw["dividend_inr"]) if raw["dividend_inr"] else None
                action_type = raw["action_type"]
                if not action_type:
                    action_type, factor, dividend = classify_purpose(raw["purpose"])
                out.append(
                    CorporateAction(
                        symbol=raw["symbol"],
                        series=raw["series"],
                        ex_date=date.fromisoformat(raw["ex_date"]),
                        purpose=raw["purpose"],
                        action_type=action_type,
                        factor=factor,
                        dividend_inr=dividend,
                        quantified=bool(int(raw["quantified"] or 0)),
                        source_file=raw["source_file"],
                    )
                )
        return out

    # -- symbol view -------------------------------------------------------

    def symbol_series(self, *, series: str = "EQ") -> dict[str, list[RawRow]]:
        """``symbol -> chronological raw rows``. Reads every stored session once."""
        out: dict[str, list[RawRow]] = {}
        for day in self.sessions():
            for row in self.read_session(day):
                if row.series != series:
                    continue
                out.setdefault(row.symbol, []).append(row)
        for rows in out.values():
            rows.sort(key=lambda r: r.trade_date)
        return out
