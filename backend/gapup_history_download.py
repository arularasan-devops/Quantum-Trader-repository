"""Gap-up history downloader (READ-ONLY, research only; places no orders).

Pulls Angel historical candles for every NSE/BSE instrument in the registry:

* ``<SYM>_daily.csv``  - ONE_DAY candles (session open / previous close -> gap %)
* ``<SYM>_1m.csv.gz``  - ONE_MINUTE underlying candles, 09:15-10:45 IST only
* ``<SYM>_opt.csv.gz`` - ONE_MINUTE CE candles, 09:15-10:45 IST, gap-up days only,
  ATM (nearest the day's open) and the next strike up, for the unexpired expiries
  listed in the scrip master (expired contracts cannot be fetched from Angel).

Candles carry no bid/ask; the backtest must apply a measured spread. Files are
written per symbol and skipped on rerun, so an interrupted run resumes.

    python gapup_history_download.py --days 180
    python gapup_history_download.py --days 180 --symbols SBIN,DLF --no-options
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import os
import re
import time

import httpx

from app.backtest import angel_history as ah
from app.config import settings
from app.market.instruments import REGISTRY

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
WIN_START, WIN_END = 9 * 60 + 15, 10 * 60 + 45
INDEX = {  # root -> (spot exchange, spot token, option exchange)
    "NIFTY": ("NSE", "99926000", "NFO"), "BANKNIFTY": ("NSE", "99926009", "NFO"),
    "FINNIFTY": ("NSE", "99926037", "NFO"), "MIDCPNIFTY": ("NSE", "99926074", "NFO"),
    "SENSEX": ("BSE", "99919000", "BFO"), "BANKEX": ("BSE", "99919012", "BFO"),
}
CHUNK_1M = 20  # calendar days per ONE_MINUTE call (~15 sessions x 375 bars < 8000 cap)


def _key(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


class Pacer:
    def __init__(self, pace: float):
        self.pace, self.last, self.calls = pace, 0.0, 0

    def fetch(self, smart, token, exch, interval, d0: dt.date, d1: dt.date, t0="09:00", t1="23:59"):
        wait = self.pace - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        rows = ah._fetch_chunk(smart, {"exchange": exch, "symboltoken": str(token), "interval": interval,
                                       "fromdate": f"{d0:%Y-%m-%d} {t0}", "todate": f"{d1:%Y-%m-%d} {t1}"})
        self.last = time.time()
        self.calls += 1
        out = []
        for r in rows:
            t = dt.datetime.fromisoformat(r[0])
            out.append((int(t.timestamp()), t.astimezone(IST), float(r[1]), float(r[2]), float(r[3]),
                        float(r[4]), float(r[5])))
        return out


def _in_window(t: dt.datetime) -> bool:
    m = t.hour * 60 + t.minute
    return WIN_START <= m <= WIN_END


def _write(path: str, header: list[str], rows: list[list]) -> None:
    tmp = path + ".tmp"
    op = gzip.open if path.endswith(".gz") else open
    with op(tmp, "wt", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def _scrip(roots: set[str]):
    """Reduce the scrip master to the equity tokens and CE contracts we need."""
    cached = os.path.join(settings.data_dir, "scrip_master.json")  # backend's same-day cache
    if os.path.exists(cached):
        import json
        with open(cached, encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = httpx.get(ah.SCRIP_MASTER_URL, timeout=300.0).json()
    eq, opts = {}, {}
    want = {_key(r): r for r in roots}
    for r in data:
        k = _key(r.get("name"))
        if k not in want:
            continue
        root = want[k]
        seg = (r.get("exch_seg") or "").upper()
        sym = (r.get("symbol") or "").upper()
        if seg == "NSE" and sym.endswith("-EQ"):
            eq[root] = r["token"]
        elif seg in ("NFO", "BFO") and (r.get("instrumenttype") or "").upper() in ("OPTSTK", "OPTIDX") \
                and sym.endswith("CE"):
            exp = ah._parse_expiry(r.get("expiry", ""))
            if exp:
                opts.setdefault(root, []).append({"token": r["token"], "symbol": sym, "seg": seg, "expiry": exp,
                                                  "strike": float(r.get("strike") or 0) / 100.0,
                                                  "lot": int(float(r.get("lotsize") or 0))})
    del data
    return eq, opts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--symbols", default="", help="comma list; default = all NSE/BSE registry names")
    ap.add_argument("--no-options", action="store_true")
    ap.add_argument("--expiries", type=int, default=3, help="nearest N unexpired expiries per name")
    ap.add_argument("--pace", type=float, default=ah._MIN_INTERVAL, help="seconds between Angel calls")
    a = ap.parse_args()
    roots = [s.strip().upper() for s in a.symbols.split(",") if s.strip()] or \
        [k for k, s in REGISTRY.items() if s.exchange in ("NFO", "BFO")]
    out = os.path.join(settings.data_dir, "backtest", "gapup")
    os.makedirs(out, exist_ok=True)
    today = dt.date.today()
    start = today - dt.timedelta(days=a.days)
    print(f"{len(roots)} names, {start}..{today}, out={out}", flush=True)

    eq, opts = _scrip(set(roots))
    smart = ah.login_smart()
    pacer = Pacer(a.pace)
    summary = []
    for n, root in enumerate(roots, 1):
        if root in INDEX:
            sx, stok, _ = INDEX[root]
        elif root in eq:
            sx, stok = "NSE", eq[root]
        else:
            print(f"[{n}/{len(roots)}] {root}: no NSE equity token, skipped", flush=True)
            summary.append((root, "NO_TOKEN", 0, 0, 0))
            continue
        print(f"[{n}/{len(roots)}] {root} ({sx}:{stok})", flush=True)
        p_day = os.path.join(out, f"{root}_daily.csv")
        if os.path.exists(p_day):
            with open(p_day, encoding="utf-8") as fh:
                daily = [(dt.date.fromisoformat(r["date"]), float(r["open"]), float(r["close"]))
                         for r in csv.DictReader(fh)]
        else:
            rows = pacer.fetch(smart, stok, sx, "ONE_DAY", start - dt.timedelta(days=10), today, "09:00", "15:30")
            daily = [(t.date(), o, c) for _, t, o, h, l, c, v in rows]
            _write(p_day, ["date", "open", "high", "low", "close", "volume"],
                   [[t.date().isoformat(), o, h, l, c, v] for _, t, o, h, l, c, v in rows])
        gapup = {}  # date -> session open
        for (d0, _, c0), (d1, o1, _) in zip(daily, daily[1:]):
            if d1 >= start and c0 > 0 and o1 > c0:
                gapup[d1] = o1

        p_1m = os.path.join(out, f"{root}_1m.csv.gz")
        n1 = 0
        if not os.path.exists(p_1m):
            rows, cur = [], start
            while cur <= today:
                end = min(cur + dt.timedelta(days=CHUNK_1M - 1), today)
                rows += [[ts, o, h, l, c, v] for ts, t, o, h, l, c, v in
                         pacer.fetch(smart, stok, sx, "ONE_MINUTE", cur, end, "09:15", "10:45") if _in_window(t)]
                cur = end + dt.timedelta(days=1)
            _write(p_1m, ["ts", "open", "high", "low", "close", "volume"], rows)
            n1 = len(rows)

        p_opt = os.path.join(out, f"{root}_opt.csv.gz")
        nopt = 0
        if not a.no_options and gapup and not os.path.exists(p_opt):
            legs = [x for x in opts.get(root, []) if x["expiry"] >= today]
            exps = sorted({x["expiry"] for x in legs})[: a.expiries]
            rows = []
            for exp in exps:
                chain = sorted((x for x in legs if x["expiry"] == exp), key=lambda x: x["strike"])
                strikes = [x["strike"] for x in chain]
                need: dict[str, list[dt.date]] = {}
                for d, o in gapup.items():
                    if d > exp or not strikes:
                        continue
                    i = min(range(len(strikes)), key=lambda j: abs(strikes[j] - o))
                    for x in chain[i:i + 2]:
                        need.setdefault(x["symbol"], []).append(d)
                for x in chain:
                    days = sorted(need.get(x["symbol"], []))
                    if not days:
                        continue
                    dayset, i = set(days), 0
                    while i < len(days):
                        d0 = days[i]
                        d1 = max(d for d in days if d <= d0 + dt.timedelta(days=CHUNK_1M - 1))
                        for ts, t, o, h, l, c, v in pacer.fetch(smart, x["token"], x["seg"], "ONE_MINUTE",
                                                                d0, d1, "09:15", "10:45"):
                            if t.date() in dayset and _in_window(t):
                                rows.append([root, exp.isoformat(), x["symbol"], x["strike"], "CE", x["lot"],
                                             ts, o, h, l, c, v, "ANGEL_HISTORY"])
                        i = days.index(d1) + 1
            _write(p_opt, ["instrument", "expiry", "symbol", "strike", "option_type", "lot_size", "ts",
                           "open", "high", "low", "close", "volume", "source"], rows)
            nopt = len(rows)
        summary.append((root, "OK", len(daily), len(gapup), n1, nopt))
        print(f"   daily {len(daily)}  gap-up days {len(gapup)}  1m rows {n1}  option rows {nopt}  "
              f"(Angel calls so far {pacer.calls})", flush=True)

    print("\nDONE (no orders were placed)")
    for s in summary:
        print(" ", *s)
    print(f" files: {out}")
    print(" note: candles have no bid/ask; only unexpired option contracts can be fetched.")


if __name__ == "__main__":
    main()
