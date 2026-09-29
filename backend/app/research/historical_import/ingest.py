"""§2/§3/§4/§7/§8 — inspect a file, then import it or refuse it.

``inspect`` never writes. It reads the file, says what it found, and says what
it would refuse — so an operator can see the rejection reason before committing
anything to the store.

``import_file`` writes exactly once: one dataset directory, one registry line.
Re-importing the same bytes returns :data:`ALREADY_IMPORTED` with the existing
``dataset_id``; it does not append rows, bump a count, or touch the earlier
dataset.

The refusals are all fail-closed and each names what an operator must supply:
an unmappable header wants ``--map``, an offset-less timestamp wants ``--tz``,
an option file without a strike wants a real option export. None of them is
recoverable by guessing, which is why none of them is guessed.
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import time

from app.research.historical_import import (
    ALREADY_IMPORTED,
    BID_ASK_ABSENT,
    BID_ASK_PRESENT,
    CONTRACT_ABSENT,
    CONTRACT_AMBIGUOUS,
    CONTRACT_PRESENT,
    ELIGIBLE_FULL,
    ELIGIBLE_NONE,
    ELIGIBLE_SHORT,
    EXECUTION_UNMEASURED,
    HISTORICAL_CANDLE_DATA,
    HISTORICAL_LEAD,
    IMPORT_REJECTED,
    IMPORTED,
    MIXED_SYMBOLS,
    NEVER_FROM_IMPORT,
    NO_INSTRUMENT,
    NO_ROWS,
    OPTION_ABSENT,
    OPTION_PRESENT,
    QUALITY_REJECTED,
    SCHEMA_VERSION,
    VERSION,
)
from app.research.historical_import import quality, registry, schema, sessions, store

# The split bar, imported from the study that defines it rather than restated:
# a dataset that cannot be split three ways chronologically must not clear a
# friendlier local threshold and then be compared against one that can.
from app.research.phase24.data import MIN_BARS, MIN_SESSIONS

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

# A file that is mostly unparseable lines is not a candle file with blemishes.
MAX_UNPARSED_SHARE = 0.10


def _num(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "")
    if not s or s.lower() in ("nan", "null", "none", "-", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _side(raw: str) -> str | None:
    s = (raw or "").strip().upper()
    if s in ("CE", "C", "CALL"):
        return "CE"
    if s in ("PE", "P", "PUT"):
        return "PE"
    return None


def read_file(path: str, *, tz_arg: str | None = None,
              column_map: dict[str, str] | None = None,
              declared_timeframe: int | None = None) -> dict:
    """Parse and normalise, or return the reason this file cannot be.

    Nothing is written. On success the result carries the normalised rows, the
    column mapping actually used, the timezone decision and every anomaly
    found.
    """
    if not os.path.exists(path):
        return {"error": f"{IMPORT_REJECTED}:FILE_NOT_FOUND", "rows": []}
    head = schema.sniff(path)
    if not head["header"]:
        return {"error": f"{IMPORT_REJECTED}:{NO_ROWS}", "rows": [],
                "header": []}
    cols = schema.map_columns(head["header"], column_map)
    if cols["error"]:
        return {"error": f"{IMPORT_REJECTED}:{cols['error']}", "rows": [],
                "header": head["header"], "mapping": cols["mapping"]}

    mapping = cols["mapping"]
    ts_samples = [
        r[head["header"].index(mapping["time"])]
        for r in head["sample"]
        if len(r) > head["header"].index(mapping["time"])
    ]
    tzd = schema.timezone_decision(ts_samples, tz_arg)
    if tzd["error"]:
        return {"error": f"{IMPORT_REJECTED}:{tzd['error']}", "rows": [],
                "header": head["header"], "mapping": mapping,
                "timezone": tzd}

    rows: list[dict] = []
    unparsed: list[int] = []
    source_rows = 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh, delimiter=head["delimiter"])
        for lineno, raw in enumerate(reader, start=2):
            source_rows += 1
            t = schema.parse_timestamp(raw.get(mapping["time"], ""), tzd["tz"])
            vals = {
                k: _num(raw.get(mapping[k], ""))
                for k in ("open", "high", "low", "close")
            }
            if t is None or any(v is None for v in vals.values()):
                unparsed.append(lineno)
                continue
            row: dict = {"time": int(t), "_line": lineno, **vals}
            for field in ("volume", "open_interest", "strike",
                          "bid", "ask", "bid_size", "ask_size"):
                col = mapping.get(field)
                if col is not None:
                    v = _num(raw.get(col, ""))
                    if v is not None:
                        row[field] = v
            for field in ("symbol", "exchange", "contract", "expiry"):
                col = mapping.get(field)
                if col is not None:
                    v = (raw.get(col) or "").strip()
                    if v:
                        row[field] = v
            if mapping.get("option_type"):
                side = _side(raw.get(mapping["option_type"], ""))
                if side:
                    row["option_type"] = side
            rows.append(row)

    missing = quality.missing_value_findings(source_rows, len(rows), unparsed)
    if not rows:
        return {"error": f"{IMPORT_REJECTED}:{NO_ROWS}", "rows": [],
                "header": head["header"], "mapping": mapping,
                "timezone": tzd, "missing": missing}
    share = missing["unparsed_rows"] / float(max(1, source_rows))
    if share > MAX_UNPARSED_SHARE:
        return {
            "error": (f"{IMPORT_REJECTED}:UNPARSEABLE_ROWS_"
                      f"{missing['unparsed_rows']}_OF_{source_rows}"),
            "rows": [], "header": head["header"], "mapping": mapping,
            "timezone": tzd, "missing": missing,
        }

    qc = quality.check_rows(rows)
    kept = [r for i, r in enumerate(rows) if i not in set(qc["dropped_rows"])]
    # Identical duplicates collapse to their first occurrence. Conflicting ones
    # are fatal above, so this can never silently pick a price.
    seen: set[int] = set()
    ordered: list[dict] = []
    for r in sorted(kept, key=lambda r: r["time"]):
        if r["time"] in seen:
            continue
        seen.add(r["time"])
        ordered.append(r)

    tf = schema.infer_timeframe([r["time"] for r in ordered], declared_timeframe)
    return {
        "error": None,
        "rows": ordered,
        "header": head["header"],
        "delimiter": head["delimiter"],
        "mapping": mapping,
        "extra_columns": cols["extra"],
        "timezone": tzd,
        "timeframe": tf,
        "quality": qc,
        "missing": missing,
        "bid_ask": (BID_ASK_PRESENT
                    if ("bid" in mapping and "ask" in mapping)
                    else BID_ASK_ABSENT),
    }


def _instrument_of(parsed: dict, override: str | None) -> tuple[str | None, str | None]:
    if override:
        return override.strip().upper(), None
    syms = parsed.get("quality", {}).get("symbols") or []
    if len(syms) == 1 and syms[0]:
        return syms[0].upper(), None
    if len(syms) > 1:
        # Naming the real fault: the file does carry a symbol, it carries
        # several, and asking for --instrument would invite one to be imposed
        # on rows belonging to another.
        return None, (f"{IMPORT_REJECTED}:{MIXED_SYMBOLS}:"
                      f"{','.join(str(s) for s in sorted(syms)[:5])}")
    return None, f"{IMPORT_REJECTED}:{NO_INSTRUMENT}"


def _option_status(parsed: dict) -> tuple[str, str | None]:
    """Option identity is all-or-nothing: strike, expiry and side, or none.

    A file with a strike but no expiry cannot be attributed to a contract, and
    inferring the expiry from the date range is how two different weeklies end
    up in one series.

    An expiry on its own does not make a file an option file — that is what a
    futures export looks like — so only a strike or a side puts the file under
    this rule.
    """
    mapping = parsed.get("mapping", {})
    has = [k for k in ("strike", "expiry", "option_type") if k in mapping]
    if not ({"strike", "option_type"} & set(has)):
        return OPTION_ABSENT, None
    if len(has) < 3:
        return OPTION_ABSENT, (
            f"{IMPORT_REJECTED}:{CONTRACT_AMBIGUOUS}:HAS_{'_'.join(has)}"
        )
    return OPTION_PRESENT, None


def eligibility(rows: int, session_count: int, quality_status: str) -> str:
    if quality_status == QUALITY_REJECTED:
        return ELIGIBLE_NONE
    if session_count >= MIN_SESSIONS and rows >= MIN_BARS:
        return ELIGIBLE_FULL
    return ELIGIBLE_SHORT


def inspect(path: str, *, tz: str | None = None,
            column_map: dict[str, str] | None = None,
            timeframe: int | None = None,
            instrument: str | None = None) -> dict:
    """What this file is, and what would happen on import. Writes nothing."""
    parsed = read_file(path, tz_arg=tz, column_map=column_map,
                       declared_timeframe=timeframe)
    out: dict = {
        "file": os.path.basename(path),
        "path": path,
        "classification": HISTORICAL_CANDLE_DATA,
        "importer_version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "header": parsed.get("header"),
        "mapping": parsed.get("mapping"),
        "extra_columns": parsed.get("extra_columns"),
        "timezone": _tz_summary(parsed.get("timezone")),
        "would_import": False,
        "reject_reason": parsed.get("error"),
    }
    if parsed.get("error"):
        return out
    tf = parsed["timeframe"]
    inst, inst_err = _instrument_of(parsed, instrument)
    opt_status, opt_err = _option_status(parsed)
    sess = sessions.sessions([r["time"] for r in parsed["rows"]],
                             tf.get("timeframe") or 1)
    err = tf.get("error") or inst_err or opt_err or (
        f"{IMPORT_REJECTED}:{';'.join(parsed['quality']['fatal'])}"
        if parsed["quality"]["fatal"] else None
    )
    out.update({
        "instrument": inst,
        "timeframe": tf.get("timeframe"),
        "timeframe_detail": tf,
        "rows": len(parsed["rows"]),
        "quality": parsed["quality"],
        "missing": parsed["missing"],
        "sessions": {k: v for k, v in sess.items() if k != "sessions"},
        "bid_ask_status": parsed["bid_ask"],
        "option_detail_status": opt_status,
        "contract_detail_status": (
            CONTRACT_PRESENT
            if ("contract" in parsed["mapping"] or "expiry" in parsed["mapping"])
            else CONTRACT_ABSENT
        ),
        "execution_note": (
            EXECUTION_UNMEASURED if parsed["bid_ask"] == BID_ASK_ABSENT
            else "SOURCE_CARRIES_TWO_SIDED_QUOTES_EXECUTION_MEASURABLE"
        ),
        "would_import": err is None,
        "reject_reason": err,
        "research_eligibility_if_imported": (
            eligibility(len(parsed["rows"]), sess["session_count"],
                        parsed["quality"]["status"])
            if err is None else ELIGIBLE_NONE
        ),
    })
    return out


def _tz_summary(tzd: dict | None) -> dict | None:
    if not tzd:
        return None
    return {
        "established_by": tzd.get("source"),
        "operator_declared": tzd.get("declared"),
        "error": tzd.get("error"),
    }


def import_file(path: str, *, source: str, tz: str | None = None,
                column_map: dict[str, str] | None = None,
                timeframe: int | None = None,
                instrument: str | None = None,
                exchange: str | None = None) -> dict:
    """Import one file. Idempotent on the source bytes."""
    if not os.path.exists(path):
        # Deliberately not registered. A rejection line is evidence about a
        # file's contents, keyed by the hash of its bytes; a path that does not
        # resolve has neither, so registering it would put a row in the audit
        # that can never be re-examined. A typo is not a dataset.
        return {"status": IMPORT_REJECTED, "recorded": False,
                "reason": f"{IMPORT_REJECTED}:FILE_NOT_FOUND", "file": path}
    src_hash = store.file_hash(path)
    existing = registry.by_hash(src_hash)
    if existing:
        return {
            "status": ALREADY_IMPORTED,
            "dataset_id": existing["dataset_id"],
            "file_hash": src_hash,
            "imported_at": existing.get("import_timestamp"),
            "reason": "THESE_EXACT_BYTES_ARE_ALREADY_A_DATASET_NOTHING_WAS_WRITTEN",
        }

    report = inspect(path, tz=tz, column_map=column_map, timeframe=timeframe,
                     instrument=instrument)
    if not report["would_import"]:
        rec = {
            "dataset_id": None, "source": source, "file_hash": src_hash,
            "instrument": report.get("instrument"), "exchange": exchange,
            "timeframe": report.get("timeframe"),
            "data_quality_status": QUALITY_REJECTED,
            "research_eligibility": ELIGIBLE_NONE,
            "import_timestamp": dt.datetime.now(_IST).isoformat(),
            "rejected_file": os.path.basename(path),
            "reject_reason": report["reject_reason"],
        }
        registry.append(rec)
        return {"status": IMPORT_REJECTED, "recorded": True,
                "reason": report["reject_reason"],
                "file": os.path.basename(path), "inspect": report}

    parsed = read_file(path, tz_arg=tz, column_map=column_map,
                       declared_timeframe=timeframe)
    rows = parsed["rows"]
    inst = report["instrument"]
    tf = int(report["timeframe"])
    sess = sessions.sessions([r["time"] for r in rows], tf)
    did = store.dataset_id(inst, tf, source, src_hash)
    identity = {
        "instrument": inst,
        "timeframe": tf,
        "timezone": report["timezone"]["established_by"],
        "contract": ";".join(parsed["quality"]["contracts"]) or None,
    }
    fp = store.fingerprint(rows, identity)

    manifest = {
        "dataset_id": did,
        "classification": HISTORICAL_CANDLE_DATA,
        "not_a": "LIVE_EXECUTABLE_BOOK",
        "schema_version": SCHEMA_VERSION,
        "importer_version": VERSION,
        "dataset_fingerprint": fp,
        "provenance": {
            "source": source,
            "original_filename": os.path.basename(path),
            "original_path": os.path.abspath(path),
            "file_hash_sha256": src_hash,
            "import_timestamp": dt.datetime.now(_IST).isoformat(),
            "source_columns": parsed["header"],
            "normalized_columns": sorted(
                {k for r in rows for k in r if not k.startswith("_")}
            ),
            "column_mapping": parsed["mapping"],
            "source_timezone": report["timezone"],
            "source_file_is_never_modified": True,
        },
        "identity": {
            **identity,
            "exchange": exchange or _single(parsed, "exchange"),
            "symbol_in_file": (parsed["quality"]["symbols"] or [None])[0],
            "expiry": _single(parsed, "expiry"),
            "strike": _single(parsed, "strike"),
            "option_type": _single(parsed, "option_type"),
            "lot_size": None,
            "lot_size_note": (
                "absent unless the source carried it or an instrument spec "
                "supplied it; never inferred from price"
            ),
        },
        "span": {
            "start": sess["first"], "end": sess["last"], "rows": len(rows),
            "sessions": sess["session_count"],
            "missing_bars": sess["missing_bars"],
            "gap_pct": sess["gap_pct"],
            "incomplete_sessions": len(sess["incomplete_sessions"]),
            "not_graded_weekdays": sess["not_graded_weekday_count"],
        },
        "quality": {k: v for k, v in parsed["quality"].items()
                    if k != "dropped_rows"},
        "dropped_row_count": len(parsed["quality"]["dropped_rows"]),
        "missing_values": parsed["missing"],
        "bid_ask_status": report["bid_ask_status"],
        "option_detail_status": report["option_detail_status"],
        "contract_detail_status": report["contract_detail_status"],
        "execution_note": report["execution_note"],
        "research_eligibility": report["research_eligibility_if_imported"],
        "promotion": {
            "best_possible_from_this_data": HISTORICAL_LEAD,
            "rule": NEVER_FROM_IMPORT,
        },
        "sessions": sess["sessions"],
    }
    paths = store.write_dataset(did, rows, manifest)
    registry.append({
        "dataset_id": did,
        "source": source,
        "file_hash": src_hash,
        "instrument": inst,
        "exchange": manifest["identity"]["exchange"],
        "timeframe": tf,
        "timezone": report["timezone"]["established_by"],
        "start": sess["first"],
        "end": sess["last"],
        "rows": len(rows),
        "sessions": sess["session_count"],
        "coverage": sess["gap_pct"],
        "data_quality_status": parsed["quality"]["status"],
        "contract_detail_status": report["contract_detail_status"],
        "option_detail_status": report["option_detail_status"],
        "bid_ask_status": report["bid_ask_status"],
        "research_eligibility": report["research_eligibility_if_imported"],
        "import_timestamp": manifest["provenance"]["import_timestamp"],
        "dataset_fingerprint": fp,
        "classification": HISTORICAL_CANDLE_DATA,
    })
    return {
        "status": IMPORTED,
        "dataset_id": did,
        "rows": len(rows),
        "sessions": sess["session_count"],
        "fingerprint": fp,
        "paths": paths,
        # Repeated from the manifest deliberately. A caller that only keeps the
        # return value should not be able to hold the row count without also
        # holding what the rows are not: a book, an executable price, or
        # anything promotable.
        "classification": HISTORICAL_CANDLE_DATA,
        "bid_ask_status": report["bid_ask_status"],
        "execution_note": report["execution_note"],
        "contract_detail_status": report["contract_detail_status"],
        "option_detail_status": report["option_detail_status"],
        "research_eligibility": report["research_eligibility_if_imported"],
        "promotion_ceiling": HISTORICAL_LEAD,
        "manifest": manifest,
    }


def _single(parsed: dict, field: str):
    vals = {r.get(field) for r in parsed["rows"] if r.get(field) not in (None, "")}
    return next(iter(vals)) if len(vals) == 1 else None


def validate(dataset_id: str) -> dict:
    """Re-run every check against the stored dataset, and re-hash its source.

    This is what catches the two failures a one-off import cannot: a source
    file that has changed under the recorded hash, and a stored bars file that
    no longer fingerprints to what the manifest claims.
    """
    man = store.read_manifest(dataset_id)
    if not man:
        return {"dataset_id": dataset_id, "status": "UNKNOWN_DATASET"}
    rows = store.read_bars(dataset_id)
    findings: list[str] = []
    prov = man.get("provenance", {})
    src = prov.get("original_path")
    if src and os.path.exists(src):
        now_hash = store.file_hash(src)
        same = now_hash == prov.get("file_hash_sha256")
        source_state = ("SOURCE_FILE_UNCHANGED" if same
                        else "SOURCE_FILE_HAS_CHANGED_SINCE_IMPORT")
    else:
        source_state = "SOURCE_FILE_NO_LONGER_AT_ITS_IMPORT_PATH"

    identity = {
        "instrument": man["identity"]["instrument"],
        "timeframe": man["identity"]["timeframe"],
        "timezone": man["identity"]["timezone"],
        "contract": man["identity"].get("contract"),
    }
    fp = store.fingerprint(rows, identity)
    if fp != man.get("dataset_fingerprint"):
        findings.append("STORED_BARS_DO_NOT_MATCH_THE_MANIFEST_FINGERPRINT")
    qc = quality.check_rows([{**r, "_line": None} for r in rows])
    if qc["fatal"]:
        findings.extend(qc["fatal"])
    sess = sessions.sessions([int(r["time"]) for r in rows],
                             int(man["identity"]["timeframe"]))
    return {
        "dataset_id": dataset_id,
        # The stored dataset and the file it came from are two separate
        # questions. An operator editing the source afterwards does not
        # corrupt bars already written, but it does mean the numbers can no
        # longer be re-derived from those bytes, so it gets its own status
        # rather than being folded into a corruption finding.
        "status": (
            "INVALID" if findings
            else "VALID" if source_state == "SOURCE_FILE_UNCHANGED"
            else f"VALID_BUT_{source_state}"
        ),
        "findings": findings,
        "source_state": source_state,
        "rows": len(rows),
        "fingerprint_recomputed": fp,
        "fingerprint_in_manifest": man.get("dataset_fingerprint"),
        "quality": {k: v for k, v in qc.items() if k != "dropped_rows"},
        "sessions": {k: v for k, v in sess.items() if k != "sessions"},
        "bid_ask_status": man.get("bid_ask_status"),
        "execution_note": man.get("execution_note"),
        "checked_at": dt.datetime.now(_IST).isoformat(),
        "elapsed_note": f"validated_at_{int(time.time())}",
    }
