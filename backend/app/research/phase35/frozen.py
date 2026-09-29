"""Phase 35 §15/§17 — the frozen candidates, verified rather than trusted.

Three candidates must survive this phase untouched: the notional-neutral
BANKNIFTY/NIFTY relative value (fingerprint ``be468671c1d8856c``), the ICICIBANK
reversal, and the NIFTY 15m candidate. §17 is the reason the check exists in
code instead of in a promise: the failure mode is not somebody deliberately
rewriting a threshold, it is a small "improvement" made after a bad session,
which turns the whole live record into curve fitting and cannot be detected
afterwards from the results.

Two levels of verification, because the three candidates are not stored the same
way:

* a candidate whose configuration lives in code exposes its own fingerprint
  function, and the expected value is asserted here as a literal. If the config
  drifts by a single threshold the fingerprint changes and :func:`verify` fails
  loudly — this is the strong check, and the relative-value pair has it;
* the other two are research candidates without a fingerprint function of their
  own. For those the guard is a SHA-256 of the source files that define them,
  captured on first run into a registry file and compared on every later run.
  This proves the definition has not been edited, which is exactly what §17
  forbids, without inventing a fingerprint value that was never published.

Nothing here optimizes, re-fits or re-scores anything. The module only answers
"is it still the thing that was frozen?".
"""
from __future__ import annotations

import hashlib
import json
import os

from app.research.pairs import spec as pair_spec
from app.research.phase35 import (
    PAIR_MIN_COST_COVERAGE_PCT,
    PAIR_MIN_SESSIONS,
    PAIR_MIN_TRADES,
    RELATIVE_VALUE,
    REVERSAL,
    UNCLASSIFIED,
)

REGISTRY = "phase35_frozen_registry.json"

# The published fingerprint of the notional-neutral pair, asserted as a literal
# so that a config change cannot silently redefine what "frozen" means.
PAIR_FINGERPRINT = "be468671c1d8856c"

VERIFIED = "VERIFIED_UNCHANGED"
DRIFTED = "DRIFTED_FROM_FROZEN"
BASELINE_RECORDED = "BASELINE_RECORDED"

# Source files that define each candidate. Hashing the file is a blunt guard —
# a comment edit trips it — and that is the right trade: a false alarm costs one
# look, a missed edit costs the entire live record's credibility.
SOURCE_GUARDS: dict[str, tuple[str, ...]] = {
    "BANKNIFTY_NIFTY_RELATIVE_VALUE": ("app/research/pairs/spec.py",),
    "ICICIBANK_REVERSAL": ("app/research/phase17/aplus.py",),
    "NIFTY_15M_CANDIDATE": (
        "app/research/phase27/conditions.py",
        "app/research/phase27/discover.py",
    ),
}

CANDIDATE_TYPES: dict[str, str] = {
    "BANKNIFTY_NIFTY_RELATIVE_VALUE": RELATIVE_VALUE,
    "ICICIBANK_REVERSAL": REVERSAL,
    "NIFTY_15M_CANDIDATE": UNCLASSIFIED,
}


def _repo_root() -> str:
    return os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
    )


def _hash_sources(paths: tuple[str, ...]) -> dict[str, str | None]:
    root = _repo_root()
    out: dict[str, str | None] = {}
    for rel in paths:
        full = os.path.join(root, rel)
        if not os.path.isfile(full):
            out[rel] = None
            continue
        with open(full, "rb") as fh:
            out[rel] = hashlib.sha256(fh.read()).hexdigest()[:16]
    return out


def _registry_path(data_dir: str) -> str:
    return os.path.join(data_dir, REGISTRY)


def _load_registry(data_dir: str) -> dict:
    path = _registry_path(data_dir)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    return loaded if isinstance(loaded, dict) else {}


def _save_registry(data_dir: str, registry: dict) -> None:
    path = _registry_path(data_dir)
    os.makedirs(data_dir, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(registry, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)


def pair_status() -> dict:
    """The strong check: the pair's own fingerprint against the published value."""
    actual = pair_spec.fingerprint()
    ok = actual == PAIR_FINGERPRINT
    return {
        "candidate": "BANKNIFTY_NIFTY_RELATIVE_VALUE",
        "opportunity_type": RELATIVE_VALUE,
        "expected_fingerprint": PAIR_FINGERPRINT,
        "actual_fingerprint": actual,
        "status": VERIFIED if ok else DRIFTED,
        "notional_neutral": bool(pair_spec.FROZEN.get("hedge")),
        "gate": {
            "min_trades": PAIR_MIN_TRADES,
            "min_sessions": PAIR_MIN_SESSIONS,
            "min_measured_cost_coverage_pct": PAIR_MIN_COST_COVERAGE_PCT,
        },
        "note": (
            "thresholds, hedge method, entry, exit and hold time are frozen; a "
            "change to any of them moves this fingerprint"
            if ok else
            "the pair configuration no longer matches the published fingerprint — "
            "stop and restore it before reading any live result"
        ),
    }


def verify(data_dir: str) -> dict:
    """Verify all three candidates. Records a baseline on the first ever run."""
    registry = _load_registry(data_dir)
    out = [pair_status()]
    changed = False
    for name, paths in SOURCE_GUARDS.items():
        actual = _hash_sources(paths)
        baseline = registry.get(name)
        if baseline is None:
            registry[name] = actual
            changed = True
            status, note = BASELINE_RECORDED, (
                "first run: source hashes recorded as the baseline for later "
                "comparison"
            )
        elif baseline == actual:
            status, note = VERIFIED, "defining source files are byte-identical"
        else:
            status, note = DRIFTED, (
                "the source that defines this candidate has been edited since the "
                "baseline; §17 forbids changing it because of recent results"
            )
        if name == "BANKNIFTY_NIFTY_RELATIVE_VALUE":
            out[0]["source_guard"] = {"status": status, "files": actual, "note": note}
            continue
        out.append({
            "candidate": name,
            "opportunity_type": CANDIDATE_TYPES[name],
            "expected_fingerprint": None,
            "actual_fingerprint": None,
            "source_guard": {"status": status, "files": actual, "note": note},
            "status": status,
            "note": (
                "no fingerprint function was ever published for this candidate, so "
                "it is guarded by the hash of its defining source rather than by a "
                "fingerprint value invented here"
            ),
        })
    if changed:
        _save_registry(data_dir, registry)
    statuses = {row["status"] for row in out}
    return {
        "candidates": out,
        "all_verified": DRIFTED not in statuses,
        "optimized_this_phase": False,
        "note": (
            "§15/§17: none of these candidates is re-fitted, re-scored or "
            "re-thresholded by Phase 35; live capture continues against the frozen "
            "definition only"
        ),
    }
