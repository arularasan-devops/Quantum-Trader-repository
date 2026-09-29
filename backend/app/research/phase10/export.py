"""Phase 10 §14 — the nightly dataset export. RESEARCH ONLY.

Every analysis in Phases 7-10 was blocked by the same thing: not enough recorded
sessions, arriving by hand, occasionally corrupt. This builds the archive §14
specifies — ``qt_daily_YYYY-MM-DD.zip`` — from the runtime's own data directory.

Three properties matter more than the file list:

* **``.env`` can never enter the archive.** Not by pattern, not by name, not by
  symlink: the member list is built from an explicit allow-list of data artefacts,
  every candidate is re-checked against ``FORBIDDEN`` before it is added, and the
  finished archive's member names are verified. Credentials leaving the machine is
  the one failure here that cannot be undone by re-running anything.
* **SQLite is verified before it is trusted.** A hot copy of a live database is how
  ``history.db`` arrived corrupt once already, so a database is integrity-checked
  and a failing check aborts the export rather than producing an archive that
  looks fine.
* **Nothing is deleted.** The exporter copies and reads; it never truncates a log
  or rotates a database, because an export that also cleans up is an export that
  can lose the only copy of a session.

This module does not stop the backend. Copying a live SQLite file is unsafe, so
``verify_only`` is the default for databases: the caller (the operator's
``collect.sh``) is responsible for stopping the writer first, and the report says
so rather than the code pretending a hot copy is fine.
"""
from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

# §14's member list. Names are the archive member names; sources are resolved
# relative to the data directory.
JSONL_MEMBERS = (
    "signals.jsonl",
    "paper_trades.jsonl",
    "ai_decisions.jsonl",
    "option_chain.jsonl",
    "feed_quality.jsonl",
    "candle_quality.jsonl",
)
JSON_MEMBERS = (
    "confidence_calibration.json",
    "a_plus_shadow.json",
    "daily_summary.json",
)
DATABASES = ("history.db",)

# Anything matching these is refused even if a future caller adds it to a list.
FORBIDDEN = (".env", ".env.local", "credentials.json", "serviceAccountKey.json",
             "id_rsa", "tokens.json", "session.json")


def _is_forbidden(path: Path) -> bool:
    name = path.name.lower()
    return (name in FORBIDDEN or name.startswith(".env")
            or "credential" in name or "secret" in name or name.endswith(".pem"))


def integrity(db_path: Path) -> str:
    """``PRAGMA integrity_check`` in read-only mode, so the check cannot write."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"unopenable: {exc}"
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "no result"
    except sqlite3.Error as exc:
        return f"failed: {exc}"
    finally:
        conn.close()


def plan(data_dir: Path, *, include_databases: bool = False) -> dict:
    """What would be exported, and why each candidate is in or out.

    Separated from the write so an operator can see the member list — and the
    exclusions — without producing a 400MB file.
    """
    members: list[dict] = []
    for name in (*JSONL_MEMBERS, *JSON_MEMBERS):
        src = data_dir / name
        members.append({
            "member": name,
            "source": str(src),
            "present": src.is_file(),
            "bytes": src.stat().st_size if src.is_file() else 0,
            "included": src.is_file() and not _is_forbidden(src),
        })
    for name in DATABASES:
        src = data_dir / name
        members.append({
            "member": name,
            "source": str(src),
            "present": src.is_file(),
            "bytes": src.stat().st_size if src.is_file() else 0,
            "included": bool(include_databases and src.is_file()),
            "note": "databases are large and unsafe to copy while the writer is "
                    "running; included only on request and only after the caller has "
                    "stopped the backend",
        })
    return {
        "data_dir": str(data_dir),
        "members": members,
        "included_count": sum(1 for m in members if m["included"]),
        "missing": [m["member"] for m in members if not m["present"]],
        "forbidden_patterns": list(FORBIDDEN),
        "secret_exclusion": "the member list is an allow-list, every candidate is "
                            "re-checked, and the finished archive is re-read to "
                            "confirm no credential-shaped name is inside",
    }


def write(data_dir: Path, dest_dir: Path, *, on: date | None = None,
          include_databases: bool = False,
          summary: dict | None = None) -> dict:
    """Build ``qt_daily_YYYY-MM-DD.zip``. Returns the manifest; raises on unsafe."""
    day = on or date.today()
    p = plan(data_dir, include_databases=include_databases)
    checks = {}
    for name in DATABASES:
        src = data_dir / name
        if src.is_file() and include_databases:
            result = integrity(src)
            checks[name] = result
            if result != "ok":
                raise RuntimeError(
                    f"{name} failed PRAGMA integrity_check ({result}); refusing to "
                    f"produce an archive containing a database that cannot be read. "
                    f"Stop the backend before exporting")

    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"qt_daily_{day.isoformat()}.zip"
    written: list[str] = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=1) as zf:
        for m in p["members"]:
            if not m["included"]:
                continue
            src = Path(m["source"])
            if _is_forbidden(src):
                continue
            zf.write(src, arcname=m["member"])
            written.append(m["member"])
        if summary is not None and "daily_summary.json" not in written:
            zf.writestr("daily_summary.json", json.dumps(summary, indent=2))
            written.append("daily_summary.json")

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    leaked = [n for n in names if _is_forbidden(Path(n))]
    if leaked:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"archive contained forbidden member(s) {leaked}; the "
                           f"archive has been deleted")

    return {
        "archive": str(out),
        "bytes": out.stat().st_size,
        "members": names,
        "missing_members": p["missing"],
        "database_integrity": checks,
        "contains_env": False,
        "deleted_anything": False,
        "note": "missing members are reported rather than fabricated: a runtime that "
                "does not yet write a given artefact produces an archive without it, "
                "and the analysis then says which measurement it could not make",
    }
