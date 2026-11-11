"""Weekly index-option 1-min history downloader (READ-ONLY, research only).

Downloads Angel 1-minute candles for NIFTY (NFO) or SENSEX (BFO) weekly options,
CE and PE, at every strike within +/- --strikes steps of the spot range seen
while each contract was live, plus the spot index itself. Output is CSV under
``<data_dir>/backtest/options/`` for the option-selling backtest.

Limits (printed again at the end):
* Angel candles carry no bid/ask, so fills must be modelled from a measured spread.
* The scrip master lists only unexpired contracts, so only those can be fetched.

Places no orders. Credentials come from the SMARTAPI_* environment variables.

    python option_history_download.py --instrument NIFTY  --days 30
    python option_history_download.py --instrument SENSEX --days 30
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os

import httpx

from app.backtest import angel_history as ah
from app.config import settings

_EXCH = {"NIFTY": ("NFO", "NSE"), "SENSEX": ("BFO", "BSE")}


def _contracts(root: str, exch: str) -> dict[dt.date, list[dict]]:
    scrip = httpx.get(ah.SCRIP_MASTER_URL, timeout=120.0).json()
    out: dict[dt.date, list[dict]] = {}
    for r in scrip:
        if (r.get("name") or "").upper() != root or (r.get("exch_seg") or "").upper() != exch:
            continue
        if (r.get("instrumenttype") or "").upper() != "OPTIDX":
            continue
        exp = ah._parse_expiry(r.get("expiry", ""))
        sym = (r.get("symbol") or "").upper()
        if exp is None or not (sym.endswith("CE") or sym.endswith("PE")):
            continue
        out.setdefault(exp, []).append({
            "token": r.get("token"), "symbol": sym, "type": sym[-2:],
            "strike": float(r.get("strike") or 0) / 100.0, "lot": int(float(r.get("lotsize") or 0)),
        })
    return out


def _write(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["ts"])
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instrument", required=True, choices=sorted(_EXCH))
    ap.add_argument("--days", type=int, default=30, help="how far back to fetch")
    ap.add_argument("--strikes", type=int, default=6, help="strikes each side of the spot range")
    ap.add_argument("--expiries", type=int, default=3, help="nearest N weekly expiries")
    a = ap.parse_args()
    root = a.instrument
    opt_exch, spot_exch = _EXCH[root]
    out_dir = os.path.join(settings.data_dir, "backtest", "options")
    today = dt.date.today()
    start = today - dt.timedelta(days=a.days)

    smart = ah.login_smart()
    spot_tok, spot_sym = ah.index_token(root, spot_exch)
    print(f"spot {spot_sym} ({spot_tok}) {start}..{today}", flush=True)
    spot = ah.download_candles(smart, spot_tok, spot_exch, "ONE_MINUTE", start, today)
    if not spot:
        raise SystemExit("No spot candles returned; stop.")
    _write(os.path.join(out_dir, f"{root}_spot.csv"),
           [{"ts": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close} for c in spot])

    by_exp = _contracts(root, opt_exch)
    expiries = sorted(e for e in by_exp if e >= today)[: a.expiries]
    print("expiries:", ", ".join(map(str, expiries)), flush=True)
    summary = []
    for exp in expiries:
        legs = by_exp[exp]
        strikes = sorted({x["strike"] for x in legs})
        step = min((b - s for s, b in zip(strikes, strikes[1:]) if b > s), default=0)
        if step <= 0:
            continue
        lo = min(c.low for c in spot) - a.strikes * step
        hi = max(c.high for c in spot) + a.strikes * step
        want = [x for x in legs if lo <= x["strike"] <= hi]
        path = os.path.join(out_dir, f"{root}_{exp:%Y%m%d}.csv")
        done = set()
        rows: list[dict] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            done = {r["symbol"] for r in rows}
        print(f"expiry {exp}: step {step:g}, {len(want)} contracts ({len(done)} already saved)", flush=True)
        empty = 0
        for i, x in enumerate(sorted(want, key=lambda z: (z["strike"], z["type"]))):
            if x["symbol"] in done:
                continue
            print(f" [{i + 1}/{len(want)}] {x['symbol']}", flush=True)
            cs = ah.download_candles(smart, x["token"], opt_exch, "ONE_MINUTE", start, min(today, exp),
                                     progress=False)
            if not cs:
                empty += 1
            rows += [{"instrument": root, "expiry": exp.isoformat(), "symbol": x["symbol"],
                      "strike": x["strike"], "option_type": x["type"], "lot_size": x["lot"],
                      "ts": c.time, "open": c.open, "high": c.high, "low": c.low, "close": c.close,
                      "volume": c.volume, "source": "ANGEL_HISTORY"} for c in cs]
            _write(path, rows)  # saved after every contract so a rerun resumes
        summary.append((exp, len(want), empty, len(rows)))
    print("\nDONE (no orders were placed)")
    for exp, n, empty, nrows in summary:
        print(f" {root} {exp}: {n} contracts, {empty} returned no candles, {nrows} rows")
    print(f" files: {out_dir}")
    print(" note: candles have no bid/ask; only unexpired contracts can be fetched.")


if __name__ == "__main__":
    main()
