"""Direction-based hedged option SELLING lab: backtest + real-time PAPER (research only).

Places no orders and imports nothing from the broker or the live engine. It only
READS option quotes that the live bot already records into the research store
(``mcx_options``: every completed bar, the chain with real bid/ask) or CSVs from
``option_history_download.py``, and writes its own files under
``<data_dir>/option_sell_lab/``.

Registered rules (fixed before any NIFTY/SENSEX result was seen; same as the CRUDEOIL pilot):
* Direction: 30-min % change of the synthetic forward (median of K + C - P over
  strikes quoted on both sides, using past minutes only). > +0.15% UP, < -0.15% DOWN,
  else SIDEWAYS. Mid prices are used for this signal only, never as a fill.
* UP -> bull put spread (sell ATM PE, buy PE ``width`` strikes lower).
  DOWN -> bear call spread (sell ATM CE, buy CE ``width`` strikes higher).
  SIDEWAYS -> iron fly (sell ATM CE+PE, buy both wings).
* Sell leg(s) filled first at bid - slip, then hedge(s) at ask + slip, same minute.
  Close: buy shorts back at ask + slip, sell hedges at bid - slip.
* Exit: profit >= tp x credit, loss >= sl x credit, held >= tmax min, or square-off.
  Quotes missing for > 20 min with a position open -> exit at the last quote seen,
  reason DATA_GAP (reported separately; never silently filled).
* One position per instrument, at most 3 entries a day.
* Costs per fill: Rs20/order, STT/CTT on sell value, exchange txn on both sides,
  SEBI fee, stamp duty on buys, 18% GST on brokerage+txn+SEBI.
* Split: first 60% of trading days in-sample, last 40% out-of-sample. The config is
  chosen on in-sample only; out-of-sample is reported, not optimised.
* Matched control: at the same entry minute, BUY the ATM option in the direction
  (CE when UP, PE when DOWN) at ask + slip and sell it at bid - slip at the spread's exit minute.

Margin is NOT modelled (needs Angel's SPAN calculator); risk per trade is the
defined max loss (width - credit) x lot.

Usage (inside the backend container):
    python option_sell_lab.py backtest --instrument NIFTY                       # recorded bid/ask
    python option_sell_lab.py backtest --instrument NIFTY --csv /app/data/backtest/options/NIFTY_20261117.csv --spread-pct 1.0
    python option_sell_lab.py spread --instrument NIFTY                         # measured spread %
    python option_sell_lab.py paper --instruments NIFTY,SENSEX                  # live paper loop
    python option_sell_lab.py report
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import itertools
import json
import os
import statistics
import time
from collections import defaultdict

from app.config import settings

IST = 19800
EXCH = {"NIFTY": "NFO", "BANKNIFTY": "NFO", "FINNIFTY": "NFO", "MIDCPNIFTY": "NFO",
        "SENSEX": "BFO", "BANKEX": "BFO"}
TXN = {"NFO": 0.03503e-2, "BFO": 0.0325e-2, "MCX": 0.0418e-2}
SELL_TAX = {"NFO": settings.option_stt_sell_pct / 100, "BFO": settings.option_stt_sell_pct / 100,
            "MCX": 0.05e-2}
SEBI, STAMP, GST = 0.0001e-2, 0.003e-2, 0.18
REG = dict(thr_pct=0.15, lookback_sec=1800, max_gap_sec=1200, max_day_entries=3, split=0.6,
           width=[1, 2], tp=[0.3, 0.5, 0.7], sl=[0.5, 1.0, 1.5], tmax=[60, 120, 240])
BASE = dict(width=1, tp=0.5, sl=1.0, tmax=120)
OUT = os.path.join(settings.data_dir, "option_sell_lab")


def exch_of(ins: str) -> str:
    return EXCH.get(ins.upper(), "MCX")


def session(ins: str) -> tuple[int, int, int]:
    """(first entry, last entry, square-off) in IST minutes."""
    if exch_of(ins) == "MCX":
        return 9 * 60 + 5, 22 * 60 + 30, 23 * 60 + 15
    return 9 * 60 + 30, 14 * 60 + 30, 15 * 60 + 15


def ist_min(ts: int) -> int:
    return ((ts + IST) // 60) % 1440


def ist_day(ts: int) -> dt.date:
    return dt.datetime.utcfromtimestamp(ts + IST).date()


def costs(buy_val: float, sell_val: float, orders: int, exch: str) -> float:
    brk = orders * float(settings.brokerage_per_lot)
    txn = (buy_val + sell_val) * TXN[exch]
    sebi = (buy_val + sell_val) * SEBI
    return brk + sell_val * SELL_TAX[exch] + txn + sebi + buy_val * STAMP + (brk + txn + sebi) * GST


# ---------------------------------------------------------------- data
def scrip_rows(ins: str) -> list[dict]:
    path = os.path.join(settings.data_dir, "scrip_master.json")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    return [r for r in data if (r.get("name") or "").upper() == ins.upper()
            and (r.get("instrumenttype") or "").upper() in ("OPTIDX", "OPTFUT")]


def lot_and_expiries(ins: str) -> tuple[int | None, list[dt.date]]:
    rows = scrip_rows(ins)
    lots = [int(float(r.get("lotsize") or 0)) for r in rows if r.get("lotsize")]
    exps = set()
    for r in rows:
        try:
            exps.add(dt.datetime.strptime(r["expiry"].upper(), "%d%b%Y").date())
        except Exception:
            pass
    return (statistics.mode(lots) if lots else None), sorted(exps)


def books_from_db(ins: str, t0: int = 0, t1: int = 2**40) -> dict[int, dict]:
    from app.research.store import SOURCE_REAL, store
    rows = store().db.query(
        "SELECT ts, strike, option_type, bid, ask FROM mcx_options WHERE instrument=? AND ts>? AND ts<=? "
        "AND source=? AND bid>0 AND ask>=bid ORDER BY ts", (ins.upper(), t0, t1, SOURCE_REAL))
    books: dict[int, dict] = defaultdict(dict)
    for r in rows:
        books[int(r["ts"])][(float(r["strike"]), r["option_type"])] = (float(r["bid"]), float(r["ask"]))
    return dict(books)


def books_from_csv(path: str, spread_pct: float) -> tuple[dict[int, dict], int | None, dict]:
    """Front-week expiry only; bid/ask MODELLED as close -/+ half the given spread."""
    by_day_exp: dict[dt.date, set] = defaultdict(set)
    rows = []
    with open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            ts, exp = int(r["ts"]), dt.date.fromisoformat(r["expiry"])
            if ist_day(ts) <= exp:
                by_day_exp[ist_day(ts)].add(exp)
                rows.append((ts, exp, float(r["strike"]), r["option_type"], float(r["close"]), int(r["lot_size"])))
    front = {d: min(e) for d, e in by_day_exp.items()}
    books: dict[int, dict] = defaultdict(dict)
    lot = None
    half = spread_pct / 200.0
    for ts, exp, k, o, c, lt in rows:
        if exp != front[ist_day(ts)] or c <= 0:
            continue
        lot = lt
        h = max(0.05, round(c * half / 0.05) * 0.05)
        books[ts][(k, o)] = (max(0.05, c - h), c + h)
    return dict(books), lot, front


def synthetic(book: dict) -> float | None:
    vals = []
    for (k, o), (b, a) in book.items():
        if o == "CE" and (k, "PE") in book:
            pb, pa = book[(k, "PE")]
            vals.append(k + (b + a) / 2 - (pb + pa) / 2)
    return statistics.median(vals) if vals else None


def strike_step(books: dict) -> float:
    ks = sorted({k for b in list(books.values())[:500] for k, _ in b})
    diffs = [b - a for a, b in zip(ks, ks[1:]) if b > a]
    return min(diffs) if diffs else 0.0


# ---------------------------------------------------------------- engine
class Engine:
    """Minute-by-minute state machine shared by the backtest and the paper loop."""

    def __init__(self, ins, cfg, lot, step, slip=0.05, dte_of=None):
        self.ins, self.cfg, self.lot, self.step, self.slip = ins.upper(), cfg, lot, step, slip
        self.exch = exch_of(ins)
        self.first, self.last_entry, self.eod = session(ins)
        self.dte_of = dte_of or (lambda d: None)
        self.hist: list[tuple[int, float]] = []
        self.pos: dict | None = None
        self.day_n: dict = defaultdict(int)
        self.trades: list[dict] = []

    def direction(self, ts):
        w = [(t, s) for t, s in self.hist if ts - REG["lookback_sec"] < t <= ts]
        if len(w) < 10 or w[0][0] > ts - REG["lookback_sec"] + 300:
            return None
        ch = (w[-1][1] - w[0][1]) / w[0][1] * 100
        return "UP" if ch > REG["thr_pct"] else "DOWN" if ch < -REG["thr_pct"] else "SIDEWAYS"

    def legs(self, dirn, atm):
        w = self.cfg["width"] * self.step
        if dirn == "UP":
            return [(-1, atm, "PE"), (1, atm - w, "PE")]
        if dirn == "DOWN":
            return [(-1, atm, "CE"), (1, atm + w, "CE")]
        return [(-1, atm, "CE"), (-1, atm, "PE"), (1, atm + w, "CE"), (1, atm - w, "PE")]

    def close_cost(self, book, legs):
        """Points paid to close (positive = pay): buy shorts at ask, sell hedges at bid."""
        tot = 0.0
        for s, k, o in legs:
            b, a = book[(k, o)]
            tot += (a + self.slip) if s < 0 else -max(0.0, b - self.slip)
        return tot

    def on_minute(self, ts: int, book: dict) -> dict | None:
        syn = synthetic(book)
        if syn:
            self.hist.append((ts, syn))
            cut = ts - REG["lookback_sec"] - 600
            while self.hist and self.hist[0][0] < cut:
                self.hist.pop(0)
        if self.pos:
            return self._manage(ts, book)
        if syn:
            self._enter(ts, book, syn)
        return None

    def _enter(self, ts, book, syn):
        m, day = ist_min(ts), ist_day(ts)
        if not (self.first <= m <= self.last_entry) or self.day_n[day] >= REG["max_day_entries"]:
            return
        dirn = self.direction(ts)
        if dirn is None:
            return
        atm = round(syn / self.step) * self.step
        legs = self.legs(dirn, atm)
        if not all((k, o) in book for _, k, o in legs):
            return
        shorts = [x for x in legs if x[0] < 0]
        hedges = [x for x in legs if x[0] > 0]
        sell_px = [max(0.0, book[(k, o)][0] - self.slip) for _, k, o in shorts]   # sell leg first
        buy_px = [book[(k, o)][1] + self.slip for _, k, o in hedges]               # then the hedge
        credit = sum(sell_px) - sum(buy_px)
        width = self.cfg["width"] * self.step
        maxloss = width - credit
        if credit <= 0 or maxloss <= 0:
            return
        ctrl = None
        if dirn in ("UP", "DOWN"):
            key = (atm, "CE" if dirn == "UP" else "PE")
            ctrl = (key, book[key][1] + self.slip) if key in book else None
        self.day_n[day] += 1
        self.pos = dict(ts=ts, dir=dirn, legs=legs, credit=credit, maxloss=maxloss, atm=atm,
                        sell0=sum(sell_px) * self.lot, buy0=sum(buy_px) * self.lot,
                        last_ts=ts, last_close=self.close_cost(book, legs), last_book=book, ctrl=ctrl)

    def _manage(self, ts, book):
        p = self.pos
        if ts - p["last_ts"] > REG["max_gap_sec"]:
            return self._exit(p["last_ts"], p["last_book"], p["last_close"], "DATA_GAP")
        if not all((k, o) in book for _, k, o in p["legs"]):
            return None
        close = self.close_cost(book, p["legs"])
        p.update(last_ts=ts, last_close=close, last_book=book)
        pnl = p["credit"] - close
        why = ("TARGET" if pnl >= self.cfg["tp"] * p["credit"] else
               "STOP" if -pnl >= self.cfg["sl"] * p["credit"] else
               "SQUARE_OFF" if ist_min(ts) >= self.eod else
               "TIME" if ts - p["ts"] >= self.cfg["tmax"] * 60 else None)
        return self._exit(ts, book, close, why) if why else None

    def _exit(self, ts, book, close, why):
        p, lot, s = self.pos, self.lot, self.slip
        buy1 = sum(book[(k, o)][1] + s for sg, k, o in p["legs"] if sg < 0) * lot
        sell1 = sum(max(0.0, book[(k, o)][0] - s) for sg, k, o in p["legs"] if sg > 0) * lot
        cost = costs(p["buy0"] + buy1, p["sell0"] + sell1, 2 * len(p["legs"]), self.exch)
        gross = (p["credit"] - close) * lot
        ctrl_net = None
        if p["ctrl"] and p["ctrl"][0] in book:
            key, px0 = p["ctrl"]
            px1 = max(0.0, book[key][0] - s)
            ctrl_net = round((px1 - px0) * lot - costs(px0 * lot, px1 * lot, 2, self.exch))
        short_side = "PE" if p["dir"] == "UP" else "CE" if p["dir"] == "DOWN" else "CE+PE"
        t = dict(instrument=self.ins, entry_ts=p["ts"], exit_ts=ts,
                 entry_ist=dt.datetime.utcfromtimestamp(p["ts"] + IST).strftime("%Y-%m-%d %H:%M"),
                 exit_ist=dt.datetime.utcfromtimestamp(ts + IST).strftime("%Y-%m-%d %H:%M"),
                 direction=p["dir"], short=short_side, atm=p["atm"], legs=len(p["legs"]),
                 credit_pts=round(p["credit"], 2), risk_rs=round(p["maxloss"] * lot), gross=round(gross),
                 cost=round(cost), net=round(gross - cost), reason=why, mins=round((ts - p["ts"]) / 60),
                 dte=self.dte_of(ist_day(p["ts"])), entry_hour=ist_min(p["ts"]) // 60, ctrl_buy_net=ctrl_net)
        self.trades.append(t)
        self.pos = None
        return t


# ---------------------------------------------------------------- stats
def stats(ts: list[dict], key="net") -> dict:
    v = [t[key] for t in ts if t.get(key) is not None]
    if not v:
        return dict(n=0)
    w, lo = sum(x for x in v if x > 0), -sum(x for x in v if x < 0)
    eq = peak = dd = 0.0
    for x in v:
        eq += x
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    risk = [t["risk_rs"] for t in ts if t.get("risk_rs")]
    return dict(n=len(v), net=round(sum(v)), win=round(100 * sum(x > 0 for x in v) / len(v)),
                pf=round(w / lo, 2) if lo else None, exp=round(sum(v) / len(v)), max_dd=round(dd),
                avg_risk=round(sum(risk) / len(risk)) if risk else None,
                cost=round(sum(t.get("cost", 0) for t in ts)))


def run(ins, books, cfg, lot, step, slip, dte_of):
    e = Engine(ins, cfg, lot, step, slip, dte_of)
    for ts in sorted(books):
        e.on_minute(ts, books[ts])
    return e.trades


def dte_fn(ins, expiries, front=None):
    if front:
        return lambda d: (front[d] - d).days if d in front else None
    cap = 7 if exch_of(ins) != "MCX" else 0

    def f(d):
        nxt = [e for e in expiries if e >= d]
        n = (nxt[0] - d).days if nxt else None
        return n if n is not None and n <= cap else None   # older weeks' expiries are gone from the master
    return f


def decay_study(books, step):
    """Change in ATM straddle MID over the next 60 min, at the same strike (measurement only)."""
    out = defaultdict(list)
    tss = sorted(books)
    idx = {t: i for i, t in enumerate(tss)}
    for t in tss:
        if ist_min(t) % 30:
            continue
        s = synthetic(books[t])
        if not s:
            continue
        k = round(s / step) * step
        u = t + 3600
        if u not in idx:
            continue
        b0, b1 = books[t], books[u]
        if not all(x in b0 and x in b1 for x in [(k, "CE"), (k, "PE")]):
            continue
        m0 = sum(sum(b0[(k, o)]) / 2 for o in ("CE", "PE"))
        m1 = sum(sum(b1[(k, o)]) / 2 for o in ("CE", "PE"))
        out[ist_min(t) // 60].append((m1 - m0, m0))
    return {h: dict(n=len(v), pts_per_hr=round(statistics.mean(x for x, _ in v), 2),
                    pct=round(100 * statistics.mean(x / m for x, m in v), 2)) for h, v in sorted(out.items())}


def cmd_backtest(a):
    ins = a.instrument.upper()
    lot, exps = lot_and_expiries(ins)
    front = None
    if a.csv:
        books, csv_lot, front = books_from_csv(a.csv, a.spread_pct)
        lot = csv_lot or lot
        fills = f"MODELLED bid/ask: close -/+ {a.spread_pct / 2:.2f}%"
    else:
        books = books_from_db(ins)
        fills = "RECORDED bid/ask (REAL_BROKER)"
    lot = a.lot or lot
    if not books or not lot:
        raise SystemExit(f"no usable quotes (minutes={len(books)}) or lot size unknown (pass --lot)")
    step = a.step or strike_step(books)
    dte_of = dte_fn(ins, exps, front)
    days = sorted({ist_day(t) for t in books})
    cut = days[int(len(days) * REG["split"])] if len(days) > 2 else days[-1]
    is_trade = lambda t: ist_day(t["entry_ts"]) < cut  # noqa: E731
    grid = []
    base_trades = None
    for w, tp, sl, tm in itertools.product(REG["width"], REG["tp"], REG["sl"], REG["tmax"]):
        cfg = dict(width=w, tp=tp, sl=sl, tmax=tm)
        tr = run(ins, books, cfg, lot, step, a.slip, dte_of)
        if cfg == BASE:
            base_trades = tr
        grid.append(dict(cfg=cfg, IS=stats([t for t in tr if is_trade(t)]),
                         OOS=stats([t for t in tr if not is_trade(t)]), trades=tr))
    ok = [g for g in grid if g["IS"].get("n", 0) >= 10]
    best = max(ok, key=lambda g: g["IS"]["net"]) if ok else None
    os.makedirs(OUT, exist_ok=True)
    tag = f"{ins}_{'csv' if a.csv else 'db'}"
    with open(os.path.join(OUT, f"{tag}_trades_base.csv"), "w", newline="") as fh:
        if base_trades:
            w_ = csv.DictWriter(fh, fieldnames=list(base_trades[0]))
            w_.writeheader()
            w_.writerows(base_trades)
    with open(os.path.join(OUT, f"{tag}_grid.csv"), "w", newline="") as fh:
        w_ = csv.writer(fh)
        w_.writerow(["width", "tp", "sl", "tmax", "part", "n", "net", "win", "pf", "exp", "max_dd", "avg_risk", "cost"])
        for g in grid:
            for part in ("IS", "OOS"):
                s = g[part]
                w_.writerow([*g["cfg"].values(), part] + [s.get(k) for k in
                            ("n", "net", "win", "pf", "exp", "max_dd", "avg_risk", "cost")])
    pr = print
    pr(f"\n=== {ins} | {fills} | lot {lot} | step {step:g} | slip {a.slip}/fill | {len(books)} minutes, "
       f"{len(days)} days {days[0]}..{days[-1]} | OOS from {cut}")
    pr(f"in-sample configs net>0: {sum(1 for g in grid if g['IS'].get('net', 0) > 0)}/{len(grid)};"
       f" out-of-sample configs net>0: {sum(1 for g in grid if g['OOS'].get('net', 0) > 0)}/{len(grid)}")
    if best:
        pr(f"best in-sample config {best['cfg']}: IS {best['IS']}\n  -> OOS {best['OOS']}")
        oos = [t for t in best["trades"] if not is_trade(t)]
        pr(f"  matched ATM BUY control, same OOS minutes: {stats(oos, 'ctrl_buy_net')}")
    else:
        pr("no config had >= 10 in-sample trades")
    if base_trades:
        pr(f"\nbase config {BASE} (all days): {stats(base_trades)}")
        for lab, f in [("short PE (UP)", lambda t: t["direction"] == "UP"),
                       ("short CE (DOWN)", lambda t: t["direction"] == "DOWN"),
                       ("iron fly (SIDEWAYS)", lambda t: t["direction"] == "SIDEWAYS")]:
            pr(f"  {lab:20s} {stats([t for t in base_trades if f(t)])}")
        for lab, grp in [("dte", "dte"), ("entry_hour", "entry_hour"), ("reason", "reason")]:
            by = defaultdict(list)
            for t in base_trades:
                by[t[grp]].append(t)
            pr(f"  by {lab}: " + "; ".join(f"{k}: n={s['n']} net={s['net']}" for k, s in
                                           ((k, stats(v)) for k, v in sorted(by.items(), key=lambda x: str(x[0])))))
        pr(f"  matched ATM BUY control (all base trades): {stats(base_trades, 'ctrl_buy_net')}")
    pr(f"\nATM straddle decay by entry hour (mid, measurement only): {decay_study(books, step)}")
    passed = bool(best and best["OOS"].get("n", 0) >= 30 and best["OOS"]["net"] > 0
                  and (best["OOS"].get("pf") or 0) > 1)
    pr("\nVERDICT: " + ("PASS (positive out-of-sample net expectancy)" if passed else
                       "NOT RECOMMENDED (needs >= 30 OOS trades with net > 0 and PF > 1)"))
    pr("margin not modelled; no orders placed. files: " + OUT)


def cmd_spread(a):
    books = books_from_db(a.instrument.upper())
    v = []
    for b in books.values():
        s = synthetic(b)
        if not s:
            continue
        for (k, o), (bid, ask) in b.items():
            if abs(k - s) <= 2 * (a.step or 100) and bid > 0:
                v.append(100 * (ask - bid) / ((ask + bid) / 2))
    if not v:
        raise SystemExit("no recorded bid/ask for this instrument")
    v.sort()
    print(f"{a.instrument}: {len(v)} near-ATM quotes, spread % of mid: median {v[len(v) // 2]:.2f}, "
          f"p75 {v[3 * len(v) // 4]:.2f}, p90 {v[9 * len(v) // 10]:.2f}")


def cmd_paper(a):
    """Real-time PAPER loop on the quotes the live bot records each minute. No orders."""
    os.makedirs(OUT, exist_ok=True)
    trades_path = os.path.join(OUT, "paper_trades.jsonl")
    state_path = os.path.join(OUT, "paper_state.json")
    engines, seen = {}, {}
    saved = json.load(open(state_path)) if os.path.exists(state_path) else {}
    for ins in [x.strip().upper() for x in a.instruments.split(",") if x.strip()]:
        lot, exps = lot_and_expiries(ins)
        lot = lot or (a.lot if a.lot else None)
        from app.research.store import store
        row = store().db.query_one("SELECT MAX(ts) AS t FROM mcx_options WHERE instrument=?", (ins,))
        newest = int(row["t"]) if row and row["t"] else 0
        hist = books_from_db(ins, newest - REG["lookback_sec"] - 600) if newest else {}
        if not lot or not hist:
            print(f"{ins}: skipped (lot={lot}, recorded minutes={len(hist)})")
            continue
        e = Engine(ins, BASE, lot, a.step or strike_step(hist), a.slip, dte_fn(ins, exps))
        if ins in saved:
            e.pos = saved[ins].get("pos")
            e.day_n.update({dt.date.fromisoformat(k): v for k, v in saved[ins].get("day_n", {}).items()})
            if e.pos:
                e.pos["legs"] = [tuple(x) for x in e.pos["legs"]]
                e.pos["last_book"] = {(float(k.split("|")[0]), k.split("|")[1]): tuple(v)
                                      for k, v in e.pos["last_book"].items()}
                if e.pos.get("ctrl"):
                    e.pos["ctrl"] = (tuple(e.pos["ctrl"][0]), e.pos["ctrl"][1])
        last = max(hist)
        for ts in sorted(hist):
            syn = synthetic(hist[ts])
            if syn:
                e.hist.append((ts, syn))
        engines[ins], seen[ins] = e, max(last, (e.pos or {}).get("last_ts", 0))
        print(f"{ins}: paper engine ready, lot {lot}, step {e.step:g}, open={bool(e.pos)}", flush=True)
    if not engines:
        raise SystemExit("nothing to paper-trade")
    print(f"PAPER ONLY - no broker orders. rules {BASE}. trades -> {trades_path}", flush=True)
    while True:
        for ins, e in engines.items():
            new = books_from_db(ins, seen[ins])
            for ts in sorted(new):
                t = e.on_minute(ts, new[ts])
                seen[ins] = ts
                if t:
                    with open(trades_path, "a") as fh:
                        fh.write(json.dumps(t) + "\n")
                    print(f"PAPER {t['exit_ist']} {ins} {t['direction']} short {t['short']} "
                          f"net Rs{t['net']} ({t['reason']})", flush=True)
                elif e.pos and e.pos["ts"] == ts:
                    print(f"PAPER {ins} OPEN {e.pos['dir']} legs {e.pos['legs']} credit "
                          f"{e.pos['credit']:.2f}", flush=True)
        state = {}
        for ins, e in engines.items():
            pos = None
            if e.pos:
                pos = dict(e.pos, last_book={f"{k}|{o}": v for (k, o), v in e.pos["last_book"].items()})
            state[ins] = dict(pos=pos, day_n={str(k): v for k, v in e.day_n.items()})
        with open(state_path + ".tmp", "w") as fh:
            json.dump(state, fh)
        os.replace(state_path + ".tmp", state_path)
        time.sleep(a.poll)


def cmd_report(a):
    path = os.path.join(OUT, "paper_trades.jsonl")
    if not os.path.exists(path):
        raise SystemExit("no paper trades yet")
    tr = [json.loads(x) for x in open(path) if x.strip()]
    print("ALL", stats(tr), "| matched BUY control", stats(tr, "ctrl_buy_net"))
    for k in sorted({(t["instrument"], t["direction"]) for t in tr}):
        print(k, stats([t for t in tr if (t["instrument"], t["direction"]) == k]))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("backtest", "spread", "paper", "report"):
        p = sub.add_parser(name)
        p.add_argument("--instrument", default="NIFTY")
        p.add_argument("--instruments", default="NIFTY,SENSEX")
        p.add_argument("--csv")
        p.add_argument("--spread-pct", type=float)
        p.add_argument("--slip", type=float, default=0.05, help="extra points per fill beyond bid/ask")
        p.add_argument("--lot", type=int)
        p.add_argument("--step", type=float)
        p.add_argument("--poll", type=float, default=30.0)
    a = ap.parse_args()
    if a.cmd == "backtest" and a.csv and a.spread_pct is None:
        raise SystemExit("--csv needs --spread-pct (measure it with: option_sell_lab.py spread)")
    {"backtest": cmd_backtest, "spread": cmd_spread, "paper": cmd_paper, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    main()
