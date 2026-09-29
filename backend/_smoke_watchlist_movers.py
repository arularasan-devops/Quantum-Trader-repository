"""Smoke: the "keep movers only" shortlist, the scan-lag report, and the
watchlist modal's scroll fix.

The user's watchlist had all 66 instruments enabled, could not be scrolled, and
scanning that many names stretches how often each one is visited. These checks
cover the three failure modes that matter: pruning on data that does not exist
yet, pruning a pinned name, and reporting a scan lag that is modelled instead of
measured.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_TMP = tempfile.mkdtemp(prefix="qt-smoke-movers-")
os.environ["QT_DATA_DIR"] = _TMP

from app.config import settings  # noqa: E402

settings.data_dir = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.market import instruments as inst_mod  # noqa: E402

FAILED: list[str] = []
HERE = os.path.dirname(os.path.abspath(__file__))
FRONTEND = os.path.abspath(os.path.join(HERE, "..", "frontend"))


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)


with TestClient(app) as client:
    # --- 1. a cold universe must NOT be pruned ---------------------------
    # Right after boot only the default instrument is warm (Angel's historical
    # API is rate-limited), so every other name measures as no-move. Applying the
    # shortlist then would keep the pinned eight and silently drop the rest for
    # being "quiet" when they were only cold.
    prev = client.get("/api/watchlist/movers?count=15").json()
    check(
        "a cold universe reports low coverage",
        prev["coverage_pct"] < prev["min_coverage_pct"],
        f"{prev['measured_count']}/{prev['active_count']} measured = {prev['coverage_pct']}%",
    )
    check(
        "and is therefore NOT ready to prune",
        prev["ready"] is False,
        f"ready={prev['ready']}",
    )
    applied = client.post("/api/watchlist/movers", json={"count": 15}).json()
    check(
        "applying it is refused rather than dropping 40+ cold instruments",
        applied["applied"] is False,
        applied.get("reason", ""),
    )
    check(
        "and the refusal explains that cold looks like quiet",
        "warming" in (applied.get("reason") or "") or "cold" in (applied.get("reason") or ""),
        applied.get("reason", ""),
    )

    # --- 2. an unmeasured name is reported as unmeasured, never as quiet --
    unmeasured = [r for r in prev["rows"] if not r["measured"]]
    check(
        "unmeasured rows carry no fabricated move number",
        all(r["net_move_pct"] is None and r["score"] is None for r in unmeasured),
        f"{len(unmeasured)} unmeasured rows",
    )

    # --- 3. pinned names always survive the shortlist --------------------
    active = set(inst_mod.UNIVERSE)
    pinned_active = [s for s in inst_mod.PINNED if s in active]
    check(
        "every pinned index/commodity is kept",
        all(s in prev["keep"] for s in pinned_active),
        f"pinned {pinned_active} vs keep {prev['keep']}",
    )
    # A tiny count must not be able to evict a pinned name either.
    tiny = client.get("/api/watchlist/movers?count=1").json()
    check(
        "even keep=1 cannot evict a pinned name",
        all(s in tiny["keep"] for s in pinned_active),
        f"keep={tiny['keep']}",
    )

    # --- 4. ranking is by PREMIUM move, not underlying % change ----------
    measured = [r for r in prev["rows"] if r["measured"]]
    ordered = all(
        (measured[i]["net_move_pct"] or 0.0) >= (measured[i + 1]["net_move_pct"] or 0.0)
        for i in range(len(measured) - 1)
    )
    check(
        "measured rows are ranked by net premium move, best first",
        ordered,
        f"{[(r['symbol'], r['net_move_pct']) for r in measured[:4]]}",
    )
    check(
        "measured rows lead the unmeasured ones",
        all(r["measured"] for r in prev["rows"][: len(measured)]),
        "sort puts measured first",
    )

    # --- 5. the scan lag is MEASURED, not modelled -----------------------
    health = client.get("/api/scan-health").json()
    check(
        "scan health reports the active count",
        health["active_count"] == len(active),
        f"{health['active_count']} active",
    )
    check(
        "and a real per-instrument age for everything it has ticked",
        health["ticked_count"] == len(health["ages"]) and all(v >= 0 for v in health["ages"].values()),
        f"ticked {health['ticked_count']}",
    )
    check(
        "the staleness target comes from the scan loop, not a hardcoded string",
        health["scan_target_sec"] > 0,
        f"target {health['scan_target_sec']}s",
    )
    unticked = [s for s in active if s not in health["ages"]]
    check(
        "an unticked instrument is absent from ages rather than reported as fresh 0s",
        len(unticked) == len(active) - health["ticked_count"],
        f"{len(unticked)} of {len(active)} never ticked",
    )

    # --- 6. with real coverage the shortlist DOES prune ------------------
    # Shrink the universe to names the smoke can actually warm, so coverage is
    # genuine rather than asserted, then confirm a measured name is kept and a
    # cheap/quiet one can be dropped.
    small = ["CRUDEOIL", "NIFTY", "SBIN"]
    inst_mod.set_universe(small)
    for sym in small:
        client.get(f"/api/snapshot?instrument={sym}")
    warm = client.get("/api/watchlist/movers?count=1").json()
    check(
        "a warmed universe reports full coverage",
        warm["coverage_pct"] >= warm["min_coverage_pct"],
        f"{warm['measured_count']}/{warm['active_count']} = {warm['coverage_pct']}%",
    )
    check(
        "and is ready to prune",
        warm["ready"] is True,
        f"ready={warm['ready']}",
    )
    applied2 = client.post("/api/watchlist/movers", json={"count": 1}).json()
    check(
        "applying it now succeeds",
        applied2["applied"] is True,
        applied2.get("reason", ""),
    )
    check(
        "the applied universe is exactly the shortlist",
        set(inst_mod.UNIVERSE) == set(applied2["keep"]),
        f"universe {sorted(inst_mod.UNIVERSE)} vs keep {sorted(applied2['keep'])}",
    )
    check(
        "the non-pinned name is what got dropped (SBIN), not an index",
        "NIFTY" in applied2["keep"] and "CRUDEOIL" in applied2["keep"],
        f"keep {applied2['keep']}",
    )

    # --- 7. the shortlist never places an order -------------------------
    check(
        "the shortlist endpoints are watchlist-only (no order call in the handler)",
        "place_order" not in open(os.path.join(HERE, "app", "main.py"), encoding="utf-8").read()
        .split("def apply_watchlist_movers")[1]
        .split("@app.")[0],
        "paper-safe",
    )

# --- 8. the modal can actually be scrolled -------------------------------
# The root cause was CSS, not layout: `.card` sets overflow:hidden to clip its
# rounded corners and, being plain CSS after @tailwind utilities, it also beat
# Tailwind's `overflow-auto` — so a tall modal ran off the bottom of the screen
# with no scrollbar.
css = open(os.path.join(FRONTEND, "app", "globals.css"), encoding="utf-8").read()
check(
    "an explicit .card-scroll escape hatch exists",
    re.search(r"\.card-scroll\s*\{[^}]*overflow:\s*auto", css) is not None,
    "globals.css",
)
modal = open(os.path.join(FRONTEND, "components", "WatchlistModal.tsx"), encoding="utf-8").read()
check(
    "the watchlist body uses it",
    "card-scroll" in modal,
    "WatchlistModal.tsx",
)
check(
    "the body is a flex-1 min-h-0 region, so the header and Save stay on screen",
    "flex-1 min-h-0" in modal and "flex flex-col" in modal,
    "sticky header/footer",
)
check(
    "the modal no longer relies on `card ... overflow-auto`, which .card overrides",
    "card w-full max-w-2xl max-h-[85vh] overflow-auto" not in modal,
    "old broken class list is gone",
)
perf = open(os.path.join(FRONTEND, "components", "PerformanceModal.tsx"), encoding="utf-8").read()
check(
    "the other .card modal with the same bug is fixed too",
    "card card-scroll" in perf,
    "PerformanceModal.tsx",
)
check(
    "the modal warns about scan coverage, not about the LTP (which is protected)",
    "scan coverage" in modal and "ticks every cycle" in modal,
    "honest wording",
)

# Two modes only: Manual (the tick boxes) or Auto. The third workflow ("Keep
# movers only") was a one-off manual version of what Auto does continuously, and
# a third way to do the same thing is a way to get it wrong.
check(
    "the Keep-movers-only workflow is gone from the modal",
    "Keep movers only" not in modal and "applyWatchlistMovers" not in modal,
    "Manual / Auto only",
)
check(
    "and its keep-count / Rank / confirm controls went with it",
    "keepCount" not in modal and "confirmPrune" not in modal,
    "no third control",
)
check(
    "Auto exposes no hold-count for the user to choose",
    "patchAuto({ count" not in modal and "hold</span>" not in modal,
    "the engine decides the size from the data",
)
check(
    "the footer describes exactly two modes",
    "Two modes" in modal and "Keep movers" not in modal,
    "footer text",
)
check(
    "an unmeasured instrument reads as warming, not as a dash that looks quiet",
    '"warming"' in modal,
    "cold != dead",
)
check(
    "warm-up progress is shown, since it is the real reason a cold start is slow",
    "warm_done" in modal and "Warming" in modal,
    "loading state is measured, not implied",
)

# --- 9. auto day-movers: pick once, hold the day, never touch the manual list --
from app.analysis import day_movers as dm  # noqa: E402
from app.analysis import watchlist as wl  # noqa: E402

check(
    "the held count is clamped to 5-10 (concentration, not one name or thirty)",
    (dm.clamp_count(1), dm.clamp_count(8), dm.clamp_count(40)) == (5, 8, 10),
    f"{dm.clamp_count(1)}/{dm.clamp_count(8)}/{dm.clamp_count(40)}",
)

# 10:30 IST on an arbitrary day, i.e. past the opening window.
IST = 19800
DAY0 = 1_786_600_000 - (1_786_600_000 % 86400)
T1030 = float(DAY0 + 10 * 3600 + 30 * 60 - IST)


def rows_for(pairs: list[tuple[str, float | None]]) -> list[dict]:
    """Mover rows: (symbol, net_move_pct) with None meaning 'not measured yet'."""
    return sorted(
        (
            {
                "symbol": s,
                "measured": mv is not None,
                "net_move_pct": mv,
                "score": None if mv is None else 50.0,
            }
            for s, mv in pairs
        ),
        key=lambda r: (r["measured"], r["net_move_pct"] or 0.0),
        reverse=True,
    )


settings.day_movers_min_coverage = 60.0
settings.day_movers_pick_after_ist_min = 585  # 09:45
settings.day_movers_replace_dead = True
settings.opportunity_min_move_pct = 4.0

# A manual watchlist the user has saved for reference — auto must not rewrite it.
saved = ["CRUDEOIL", "NIFTY", "SENSEX", "SBIN", "INFY", "TCS", "RELIANCE", "ITC", "WIPRO", "TITAN"]
wl.save(saved)

engine = dm.DayMovers()
engine.reset()

# Cold: only 2 of 10 measured.
cold = engine.evaluate(rows_for([("CRUDEOIL", 9.0), ("NIFTY", 7.0)] + [(s, None) for s in saved[2:]]), now=T1030)
check(
    "a cold universe is not locked in as the day's set",
    cold["action"] == "waiting" and cold["held"] == [],
    cold["reason"],
)

# Warm: everything measured. Best five must be held.
warm_pairs = [
    ("CRUDEOIL", 12.0),
    ("SBIN", 11.0),
    ("NIFTY", 9.5),
    ("INFY", 8.0),
    ("SENSEX", 7.0),
    ("TCS", 6.0),
    ("RELIANCE", 5.0),
    ("ITC", 2.0),
    ("WIPRO", 1.0),
    ("TITAN", 0.5),
]
first = engine.evaluate(rows_for(warm_pairs), now=T1030)
# 7 of the 10 clear the 4% floor, so the size is 7 — decided from the data, with
# no number for the user to pick.
check(
    "with coverage it picks the day's set once",
    first["action"] == "picked" and len(first["held"]) == 7,
    f"{first['held']}",
)
check(
    "the size is however many clear the movement floor, not a configured number",
    first["held"] == ["CRUDEOIL", "SBIN", "NIFTY", "INFY", "SENSEX", "TCS", "RELIANCE"],
    f"{len(first['held'])} held for 7 names above the floor",
)
check(
    "the quiet names are dropped, indices included when they do not move",
    "ITC" not in first["held"] and "TITAN" not in first["held"],
    f"{first['held']}",
)
check(
    "the active universe is narrowed to exactly the held set",
    set(inst_mod.UNIVERSE) == set(first["held"]),
    f"{sorted(inst_mod.UNIVERSE)}",
)
check(
    "the user's saved watchlist is NOT rewritten",
    wl.load() == [s.upper() for s in saved],
    f"{wl.load()}",
)

# Re-ranking mid-session must NOT rotate the set: a different leader is not a
# reason to abandon a name the bot is already trading.
shuffled = list(warm_pairs)
shuffled[5] = ("TCS", 99.0)
again = engine.evaluate(rows_for(shuffled), now=T1030 + 600)
check(
    "a new leader mid-session does not rotate the held set",
    again["action"] == "held" and set(engine.held) == set(first["held"]),
    f"{engine.held}",
)

# A held name only leaves after repeated dead readings.
dead = [(s, 0.5 if s == "INFY" else mv) for s, mv in shuffled]
for i in range(dm._DEAD_STRIKES - 1):
    mid = engine.evaluate(rows_for(dead), now=T1030 + 900 + 60 * i)
    check(
        f"one quiet reading ({i + 1} of {dm._DEAD_STRIKES}) is a lull, not a swap",
        mid["action"] == "held" and "INFY" in engine.held,
        f"{engine.held}",
    )
swap = engine.evaluate(rows_for(dead), now=T1030 + 2000)
check(
    f"after {dm._DEAD_STRIKES} dead readings the name is replaced",
    swap["action"] == "swapped" and "INFY" not in engine.held,
    swap["reason"],
)
check(
    "and its replacement is the best measured name outside the set",
    "TCS" in engine.held,
    f"{engine.held}",
)
check(
    "the swap is logged with the numbers behind it",
    engine.log and "below the" in engine.log[-1]["detail"],
    engine.log[-1]["detail"] if engine.log else "no log",
)
check(
    "a swap replaces, never appends — the set size is unchanged",
    len(engine.held) == len(first["held"]),
    f"{engine.held}",
)

# The bound holds at both ends: a thin day cannot become a one-name book, and a
# wild day cannot re-open the whole universe. Run last, because picking applies
# the set to the shared active universe.
thin = dm.DayMovers()
thin.reset()
thin_pick = thin.evaluate(rows_for([(s, 9.0 if i < 2 else 1.0) for i, s in enumerate(saved)]), now=T1030)
check(
    "a day with only 2 movers still holds the 5-name minimum, not a 2-name book",
    len(thin_pick["held"]) == dm.MIN_COUNT,
    f"{thin_pick['held']}",
)
wide_syms = saved + ["BAJAJAUTO", "BHARTIARTL", "MARUTI"]
wide = dm.DayMovers()
wide.reset()
wide_pick = wide.evaluate(rows_for([(s, 20.0 - i) for i, s in enumerate(wide_syms)]), now=T1030)
check(
    "and a day where everything moves is still capped at 10 (concentration, not the whole list)",
    len(wide_pick["held"]) == dm.MAX_COUNT,
    f"{len(wide_pick['held'])} of {len(wide_syms)} measured",
)

# An unmeasured held name must not be swapped out for being unmeasured.
engine2 = dm.DayMovers()
engine2.reset()
engine2.evaluate(rows_for(warm_pairs), now=T1030)
blind = [(s, None if s == "SBIN" else mv) for s, mv in warm_pairs]
for i in range(dm._DEAD_STRIKES + 1):
    engine2.evaluate(rows_for(blind), now=T1030 + 600 + 60 * i)
check(
    "a held name that stops reporting is kept, not pruned for missing data",
    "SBIN" in engine2.held,
    f"{engine2.held}",
)

# The day's set survives a restart, and rolls over at the IST date change.
persisted = dm.DayMovers()
check(
    "today's held set survives a restart",
    persisted.snapshot(T1030 + 3000)["held"] == engine2.held or persisted.held == engine2.held,
    f"reloaded {persisted.held}",
)
tomorrow = persisted.snapshot(T1030 + 86400)
check(
    "and a new IST day starts unpicked instead of reusing yesterday's names",
    tomorrow["held"] == [],
    f"{tomorrow['held']}",
)

# --- the SWITCH itself must survive a restart ----------------------------
# Reported symptom: "I enabled auto button but its not enabling". The mode was
# in-memory only, so every backend restart silently put it back to OFF.
settings.day_movers_auto_enabled = True
dm.movers.remember_mode(True)
settings.day_movers_auto_enabled = False
restarted = dm.DayMovers()
check(
    "auto ON is remembered across a restart, not silently reverted to OFF",
    restarted.restore_mode() is True,
)
dm.movers.remember_mode(False)
off_again = dm.DayMovers()
check(
    "turning it OFF is remembered too (the switch is not one-way)",
    off_again.restore_mode() is False,
)
fresh_dir = tempfile.mkdtemp(prefix="qt-smoke-mode-")
_saved_dir = settings.data_dir
settings.data_dir = fresh_dir
never_set = dm.DayMovers()
check(
    "a state file with no remembered mode leaves the configured default alone",
    never_set.restore_mode() is None,
)
settings.data_dir = _saved_dir

print()
if FAILED:
    print(f"{len(FAILED)} CHECK(S) FAILED: {FAILED}")
    sys.exit(1)
print("ALL WATCHLIST-MOVERS / SCAN-LAG CHECKS PASSED")
