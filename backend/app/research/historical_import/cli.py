"""Historical import CLI.

    python -m app.research.historical_import.cli inspect <file> [--tz Asia/Kolkata]
    python -m app.research.historical_import.cli import <file> --source <name>
    python -m app.research.historical_import.cli validate <dataset_id>
    python -m app.research.historical_import.cli list
    python -m app.research.historical_import.cli coverage

`inspect` writes nothing at all. `import` writes one dataset and one registry
line, and re-importing the same bytes writes neither. Nothing in this module
fetches, downloads or schedules anything: a file arrives because an operator
put it on disk and typed its path.

`--map` is how a file this layer's alias table does not recognise gets in:
``--map close=LastPrice --map time=BarDateTime``. It is an operator stating a
fact about their own export. There is no inference behind it and no default.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.research.historical_import import coverage as hcoverage
from app.research.historical_import import ingest, registry, report


def _pairs(values: list[str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in values or []:
        if "=" not in item:
            raise SystemExit(f"--map expects field=column, got {item!r}")
        field, col = item.split("=", 1)
        out[field.strip()] = col.strip()
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="historical_import",
        description="import licensed historical candle CSVs for research",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("inspect", help="what a file contains; writes nothing")
    i.add_argument("file")
    i.add_argument("--tz", default=None,
                   help="timezone of naive timestamps, e.g. Asia/Kolkata")
    i.add_argument("--timeframe", type=int, default=None,
                   help="declared bar minutes; checked against the data")
    i.add_argument("--instrument", default=None,
                   help="required when the file carries no symbol column")
    i.add_argument("--map", action="append", dest="map_",
                   help="field=column override, repeatable")
    i.add_argument("--json", action="store_true")

    m = sub.add_parser("import", help="normalise, register and store one file")
    m.add_argument("file")
    m.add_argument("--source", required=True,
                   help="where this file came from, kept as provenance")
    m.add_argument("--tz", default=None)
    m.add_argument("--timeframe", type=int, default=None)
    m.add_argument("--instrument", default=None)
    m.add_argument("--exchange", default=None)
    m.add_argument("--map", action="append", dest="map_")
    m.add_argument("--json", action="store_true")

    v = sub.add_parser("validate", help="re-check a stored dataset and its source")
    v.add_argument("dataset_id")
    v.add_argument("--json", action="store_true")

    ls = sub.add_parser("list", help="the dataset registry")
    ls.add_argument("--json", action="store_true")

    cv = sub.add_parser("coverage", help="the historical coverage audit")
    cv.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)

    if args.cmd == "inspect":
        rep = ingest.inspect(args.file, tz=args.tz,
                             column_map=_pairs(args.map_),
                             timeframe=args.timeframe,
                             instrument=args.instrument)
        print(json.dumps(rep, indent=2, default=str) if args.json
              else report.inspect_text(rep))
        return 0 if rep.get("would_import") else 2

    if args.cmd == "import":
        res = ingest.import_file(args.file, source=args.source, tz=args.tz,
                                 column_map=_pairs(args.map_),
                                 timeframe=args.timeframe,
                                 instrument=args.instrument,
                                 exchange=args.exchange)
        payload = {k: v for k, v in res.items() if k != "manifest"}
        print(json.dumps(payload, indent=2, default=str) if args.json
              else report.import_text(res))
        return 0 if res["status"] != "IMPORT_REJECTED" else 2

    if args.cmd == "validate":
        res = ingest.validate(args.dataset_id)
        print(json.dumps(res, indent=2, default=str) if args.json
              else report.validate_text(res))
        return 0 if res.get("status") == "VALID" else 2

    if args.cmd == "list":
        rows = hcoverage.rows()
        if args.json:
            print(json.dumps(registry.datasets(), indent=2, default=str))
        else:
            print(report.list_text(rows))
        return 0

    if args.cmd == "coverage":
        rep = hcoverage.report()
        print(json.dumps(rep, indent=2, default=str) if args.json
              else report.coverage_text(rep))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
